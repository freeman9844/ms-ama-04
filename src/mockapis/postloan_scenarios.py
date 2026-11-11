"""사후 재생 시나리오 계획(스펙 §4, 2026-11-11). 원인마다 주문·정산·비용을 어떻게 바꿀지 한 곳에 모은다.

`postloan.generate_postloan`이 계획을 받아 셀러 엔진에 넘기고(매출 배율·반품률·정산 훅·추가 사건),
시뮬레이션이 끝난 사건 목록을 `adjust`로 한 번 더 손본다(광고·택배 배율, 급여 지연, 주문액에 비례한 사건).
시나리오는 셀러를 하드코딩하지 않는다: 쿠팡이 없으면 비중이 가장 큰 오픈마켓에 보류를 건다.

| 시나리오 | 원인 변화 | 결과 | 정답 |
|---|---|---|---|
| SALES_SLUMP | 주문액 7일 1.0 → 31일 0.50 | 광고·카드 1.8배, 대부 2주 간격, 급여 지연·4대보험 미납 | 매출 부진 |
| SETTLEMENT_HOLD | 지급일 14~41일 보류, 44일 해제 | 선정산 양도(해제까지), 브릿지 대부 1회·일시 상환, 첫 상환 미납 | 정산 보류 |
| RETURN_SPIKE | 반품률 14~55일 22.5%, 이후 8% | 반품회수 택배비, 차입 없음 | 반품 급증 |
| COST_SURGE | 상계율 +8%p, 광고·카드 2배·택배 1.3배 | 소액 대부 1회 | 비용 증가 |
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta

from mockapis.sellerdata.categories import TxnCategory
from mockapis.sellerdata.channels import OrderDay, _same_rate
from mockapis.sellerdata.costs import Event
from mockapis.sellerdata.personas import Persona
from mockapis.sellerdata.receipts import OFFSET_PLATFORMS, ReceiptHooks
from mockapis.synth import Archetype, SyntheticCase
from shared.contracts import AlertCause, TxnTypeCode
from shared.krcalendar import next_business_day, previous_business_day
from shared.repayment import LENDER_DEBIT, due_dates

PROCEEDS_KRW = 1_500_000
REPAYMENT_PER_LOAN_KRW = 70_000
LOAN_INTERVAL_DAYS = 14
PAYROLL_DELAYED_DAY = 20
# (상환 적요, 상환 유형, 대출금 적요, 대출금 유형). 두 번째는 규칙(대부·P2P·대출금 키워드)에 걸리지 않는다.
LENDERS: tuple[tuple[str, TxnTypeCode, str, TxnTypeCode], ...] = (
    ("한빛대부 상환", "AUTO_DEBIT", "한빛대부 대출금", "TRANSFER_IN"),
    ("머니무브캐피탈", "AUTO_DEBIT", "머니무브 빠른자금", "TRANSFER_IN"),
    ("펀딩리P2P 상환", "AUTO_DEBIT", "펀딩리 투자금 입금", "TRANSFER_IN"),
)

SLUMP_ONSET_DAY = 7
SLUMP_FLOOR_DAY = 31  # 주문액이 바닥(SLUMP_FLOOR)에 닿는 날
SLUMP_FLOOR = 0.50  # 스펙 표는 34일 0.60이지만 3~5주차 주문액이 기준의 0.70 아래로 내려가지 않아 더 깊게 잡았다
SLUMP_COST_FROM_DAY = 35  # 주문 하락이 먼저 보이도록 광고·상계 증가는 하락이 끝난 뒤에 시작한다
SLUMP_AD_SURGE = 1.8
SLUMP_EXTRA_OFFSET = 0.05  # 광고 1.8배 중 플랫폼 광고분은 정산 상계로 나타난다
SLUMP_LOAN_FROM_DAY = 35
SLUMP_PAYROLL_DELAY_FROM_DAY = 56

HOLD_FROM_DAY = 14
HOLD_TO_DAY = 41
HOLD_RELEASE_DAY = 44
HOLD_ADVANCE_FROM_DAY = 28  # 선지급은 지급일 5~7일 전에 들어온다(입금은 4주차부터)
HOLD_LOAN_DAY = 28
HOLD_LOAN_UNIT_KRW = 1_000_000
HOLD_LOAN_MIN_KRW = 1_500_000
HOLD_LOAN_INTEREST = 0.03  # 브릿지 대부 일시 상환 이자

RETURN_FROM_DAY = 14
RETURN_TO_DAY = 55
RETURN_SPIKE_RATE = 0.225
RETURN_CALM_RATE = 0.08
RETURN_PICKUP_RATIO = 0.06  # 그 주 반품액 대비 반품회수 택배비

COST_FROM_DAY = 14
COST_SALES_FACTOR = 1.05
COST_EXTRA_OFFSET = 0.08
COST_AD_SURGE = 2.0
COST_SHIPPING_SURGE = 1.3
COST_LOAN_DAY = 45
COST_LOAN_KRW = 1_000_000


def _one(d: date) -> float:
    return 1.0


def _keep(events: list[Event], orders: Sequence[OrderDay]) -> list[Event]:
    return events


def _nothing(dues: Sequence[date]) -> set[date]:
    return set()


@dataclass(frozen=True)
class ScenarioPlan:
    """시나리오 한 개의 계획. 기본값은 STABLE(아무것도 바꾸지 않는다)."""

    sales_factor: Callable[[date], float] = _one
    return_rate: Callable[[date, float], float] = _same_rate
    hooks: ReceiptHooks = ReceiptHooks()
    assigned_from: date | None = None
    extra: list[Event] = field(default_factory=list)
    adjust: Callable[[list[Event], Sequence[OrderDay]], list[Event]] = _keep
    cause: AlertCause | None = None
    secondary: tuple[AlertCause, ...] = ()
    onset: date | None = None
    force_missed: Callable[[Sequence[date]], set[date]] = _nothing
    calibrate_default: bool = False


def end_of_day_balances(opening: int, events: Sequence[Event], start: date, days: int) -> list[int]:
    by_day = [0] * days
    for e in events:
        offset = (e.booked_on - start).days
        if 0 <= offset < days:
            by_day[offset] += e.amount_krw
    balances, balance = [], opening
    for delta in by_day:
        balance += delta
        balances.append(balance)
    return balances


def hold_platform(persona: Persona) -> str:
    """정산을 보류할 플랫폼: 쿠팡이 있으면 쿠팡, 없으면 비중이 가장 큰 오픈마켓."""
    weights = dict(persona.channels)
    if "쿠팡" in weights:
        return "쿠팡"
    markets = {c: w for c, w in weights.items() if c in OFFSET_PLATFORMS} or weights
    return max(sorted(markets), key=markets.__getitem__)


def _advance_platform(persona: Persona, exclude: str) -> str | None:
    markets = {c: w for c, w in persona.channels if c in OFFSET_PLATFORMS and c != exclude}
    return max(sorted(markets), key=markets.__getitem__) if markets else None


def _borrowings(onset: date, end: date, proceeds: int, repayment: int, loans: int | None = None) -> list[Event]:
    """onset부터 2주 간격 대부 차입, 차입 건마다 매주 금요일 상환(대부는 최대 loans건)."""
    out: list[Event] = []
    taken = 0
    d = onset
    while d < end:
        if (d - onset).days % LOAN_INTERVAL_DAYS == 0 and (loans is None or taken < loans):
            _, _, desc, code = LENDERS[taken % len(LENDERS)]
            out.append(Event(d, proceeds, desc, code, TxnCategory.LOAN_PROCEEDS))
            taken += 1
        if d.weekday() == 4:
            for i in range(taken):
                desc, code, _, _ = LENDERS[i % len(LENDERS)]
                out.append(Event(d, -repayment, desc, code, TxnCategory.HIGH_COST_LENDER))
        d += timedelta(days=1)
    return out


def _scaled(events: list[Event], start: date, from_day: int, factors: dict[TxnCategory, float]) -> list[Event]:
    out = []
    for e in events:
        factor = factors.get(e.category)
        if factor is not None and (e.booked_on - start).days >= from_day:
            e = replace(e, amount_krw=round(e.amount_krw * factor))
        out.append(e)
    return out


def _sales_slump(case: SyntheticCase, start: date, days: int) -> ScenarioPlan:
    end = start + timedelta(days=days)

    def sales_factor(d: date) -> float:
        day = (d - start).days
        if day < SLUMP_ONSET_DAY:
            return 1.0
        progress = min((day - SLUMP_ONSET_DAY) / (SLUMP_FLOOR_DAY - SLUMP_ONSET_DAY), 1.0)
        return 1.0 - (1.0 - SLUMP_FLOOR) * progress

    def adjust(events: list[Event], orders: Sequence[OrderDay]) -> list[Event]:
        """광고·카드 급증, 4대보험 미납(출금 없음), 급여 지연(10일 → 20일)."""
        out = []
        for e in _scaled(events, start, SLUMP_COST_FROM_DAY,
                         {TxnCategory.ADVERTISING: SLUMP_AD_SURGE, TxnCategory.CARD_PAYMENT: SLUMP_AD_SURGE}):
            day = (e.booked_on - start).days
            if day >= SLUMP_PAYROLL_DELAY_FROM_DAY and e.category is TxnCategory.SOCIAL_INSURANCE:
                continue
            if e.category is TxnCategory.PAYROLL:
                late = date(e.booked_on.year, e.booked_on.month, PAYROLL_DELAYED_DAY)
                payday = previous_business_day(late)
                if (late - start).days >= SLUMP_PAYROLL_DELAY_FROM_DAY and payday < end:
                    e = replace(e, booked_on=payday)
            out.append(e)
        return out

    return ScenarioPlan(
        sales_factor=sales_factor,
        hooks=ReceiptHooks(
            extra_offset=lambda d: SLUMP_EXTRA_OFFSET if (d - start).days >= SLUMP_COST_FROM_DAY else 0.0
        ),
        extra=_borrowings(start + timedelta(days=SLUMP_LOAN_FROM_DAY), end, PROCEEDS_KRW, REPAYMENT_PER_LOAN_KRW),
        adjust=adjust,
        cause=AlertCause.SALES_SLUMP,
        secondary=(AlertCause.COST_SURGE, AlertCause.HIGH_COST_BORROWING),
        onset=start + timedelta(days=SLUMP_ONSET_DAY),
        force_missed=lambda dues: {dues[-1]},
        calibrate_default=True,
    )


def _settlement_hold(case: SyntheticCase, start: date, days: int) -> ScenarioPlan:
    end = start + timedelta(days=days)
    platform = hold_platform(case.persona)
    advance = _advance_platform(case.persona, platform)
    release_on = next_business_day(start + timedelta(days=HOLD_RELEASE_DAY))
    opening = case.transactions[-1].balance_after_krw if case.transactions else 0
    # 요청 start 기준 첫 상환일이다. LoanTerms가 있으면 첫 거래(실행)가 start에 잡혀 앵커 == start라
    # 운영·평가 경로는 generate_postloan의 앵커 기준 상환일과 같다(loan=None 옛 경로에서만 어긋날 수 있다).
    first_due = due_dates(start, days)[0]
    repay_on = next_business_day(release_on + timedelta(days=1))

    def held(payout) -> bool:
        day = (payout.paid_on - start).days
        return payout.channel == platform and HOLD_FROM_DAY <= day <= HOLD_TO_DAY

    def adjust(events: list[Event], orders: Sequence[OrderDay]) -> list[Event]:
        """보류 기간 잔고가 0 아래로 내려가지 않을 만큼만 빌리고(100만 원 단위), 해제 다음 영업일에 일시 상환한다.

        빌린 돈은 상환일 전까지만 잔고를 떠받친다. 해제 뒤 구간까지 메우려 들면 일시 상환액(원금의 1.03배)이
        함께 커져 꼬리가 더 나빠지므로, 부족분은 보류 기간(차입일~상환 전날)만 보고 한 번에 정한다.
        """
        paid = [e for e in events if not (e.description == LENDER_DEBIT and e.booked_on == first_due)]
        hold_window = end_of_day_balances(opening, paid, start, days)[HOLD_LOAN_DAY:(repay_on - start).days]
        short = max(0, -min(hold_window, default=0))
        rounded = (short + HOLD_LOAN_UNIT_KRW - 1) // HOLD_LOAN_UNIT_KRW * HOLD_LOAN_UNIT_KRW
        return [*events, *_bridge(max(HOLD_LOAN_MIN_KRW, rounded))]

    def _bridge(amount: int) -> list[Event]:
        desc, code, proceeds_desc, proceeds_code = LENDERS[0]
        return [
            Event(start + timedelta(days=HOLD_LOAN_DAY), amount, proceeds_desc, proceeds_code,
                  TxnCategory.LOAN_PROCEEDS),
            Event(repay_on, -round(amount * (1 + HOLD_LOAN_INTEREST)), desc, code, TxnCategory.HIGH_COST_LENDER),
        ]

    return ScenarioPlan(
        # 양도할 다른 오픈마켓이 없으면 선정산을 새로 트지 않는다(보류 플랫폼에 선정산이 붙으면 원인이 흐려진다).
        # 새로 튼 양도는 보류가 풀리면 끊는다(스펙 §4.2 8~13주 정상 회복). 원래 양도 셀러는 그대로 쓴다.
        hooks=ReceiptHooks(hold=held, release_on=release_on, advance_platform=advance,
                           advance_until=None if case.archetype is Archetype.ASSIGNED else release_on),
        assigned_from=start + timedelta(days=HOLD_ADVANCE_FROM_DAY) if advance else None,
        adjust=adjust,
        cause=AlertCause.SETTLEMENT_HOLD,
        secondary=(AlertCause.HIGH_COST_BORROWING,),
        onset=start + timedelta(days=HOLD_FROM_DAY),
        force_missed=lambda dues: {dues[0]},
    )


def _return_spike(case: SyntheticCase, start: date, days: int) -> ScenarioPlan:
    courier = case.persona.courier

    def return_rate(day: date, base: float) -> float:
        offset = (day - start).days
        if RETURN_FROM_DAY <= offset <= RETURN_TO_DAY:
            return RETURN_SPIKE_RATE
        return RETURN_CALM_RATE if offset > RETURN_TO_DAY else base

    def adjust(events: list[Event], orders: Sequence[OrderDay]) -> list[Event]:
        """반품 급증 기간에는 매주 월요일 지난 7일 반품액에 비례한 반품회수 택배비를 낸다."""
        returns: dict[date, int] = {}
        for o in orders:
            returns[o.day] = returns.get(o.day, 0) + o.returns_krw
        out = list(events)
        for offset in range(RETURN_FROM_DAY, RETURN_TO_DAY + 1):
            d = start + timedelta(days=offset)
            if d.weekday() != 0:
                continue
            week = sum(returns.get(d - timedelta(days=k), 0) for k in range(1, 8))
            fee = round(week * RETURN_PICKUP_RATIO)
            if fee:
                out.append(Event(d, -fee, f"{courier} 반품회수", "AUTO_DEBIT", TxnCategory.SHIPPING))
        return out

    return ScenarioPlan(
        return_rate=return_rate,
        adjust=adjust,
        cause=AlertCause.RETURN_SPIKE,
        onset=start + timedelta(days=RETURN_FROM_DAY),
    )


def _cost_surge(case: SyntheticCase, start: date, days: int) -> ScenarioPlan:
    end = start + timedelta(days=days)

    def adjust(events: list[Event], orders: Sequence[OrderDay]) -> list[Event]:
        return _scaled(events, start, COST_FROM_DAY, {
            TxnCategory.ADVERTISING: COST_AD_SURGE,
            TxnCategory.CARD_PAYMENT: COST_AD_SURGE,
            TxnCategory.SHIPPING: COST_SHIPPING_SURGE,
        })

    return ScenarioPlan(
        sales_factor=lambda d: COST_SALES_FACTOR if (d - start).days >= COST_FROM_DAY else 1.0,
        hooks=ReceiptHooks(extra_offset=lambda d: COST_EXTRA_OFFSET if (d - start).days >= COST_FROM_DAY else 0.0),
        extra=_borrowings(start + timedelta(days=COST_LOAN_DAY), end, COST_LOAN_KRW, REPAYMENT_PER_LOAN_KRW, loans=1),
        adjust=adjust,
        cause=AlertCause.COST_SURGE,
        secondary=(AlertCause.HIGH_COST_BORROWING,),
        onset=start + timedelta(days=COST_FROM_DAY),
    )


_PLANS = {
    AlertCause.SALES_SLUMP.value: _sales_slump,
    AlertCause.SETTLEMENT_HOLD.value: _settlement_hold,
    AlertCause.RETURN_SPIKE.value: _return_spike,
    AlertCause.COST_SURGE.value: _cost_surge,
}


def scenario_plan(scenario: str, case: SyntheticCase, start: date, days: int) -> ScenarioPlan:
    """시나리오(FlowScenario)별 계획. STABLE은 아무것도 바꾸지 않는다."""
    build = _PLANS.get(str(scenario))
    return build(case, start, days) if build else ScenarioPlan()
