"""대출 실행 이후 91일 계좌 흐름 합성(조기경보 재생용, seed 결정론적). 심사 때와 같은 셀러 엔진·페르소나를 쓴다.

- STABLE: 정상 영업. 오탐 미끼는 엔진이 그대로 만든다: 11월 행사·연말 매출, 1월 부가세 확정 납부,
  공휴일이 낀 주의 정산 공백(쿠팡 주정산·11번가 월 2회가 영업일로 밀린다).
- STACKING(주인공): 시즌 재고 대량 선매입 → 판매 부진으로 정산 감소 → 광고비·카드대금 증가 →
  대부·P2P 신규 차입(2주 간격, 매주 금요일 상환) → 급여 지연·4대보험 미납(출금 없음) → 잔고 마이너스.
  일부 대부·캐피탈 적요는 규칙 분류기가 못 잡도록 만들어 의미 분류의 몫을 남긴다.
  선매입은 잔고를 CAPEX_FLOOR_RATIO까지 끌어내리고, 외상 잔금(매주 수요일)은 첫 부도일이 DEFAULT_WINDOW
  안에 오도록 고른다. 그래서 계절·셀러 규모와 무관하게 첫 고금리 차입부터 60일 넘게 사전 감지 여유가 있다.
- default_on: 처음으로 일말 잔고가 0 미만이 된 날(부도 대리 지표, P6 평가의 사전 감지 일수 기준).
labels(정답 분류)·default_on은 평가 전용이며 API로 노출하지 않는다.
"""

import random
from dataclasses import dataclass, replace
from datetime import date, timedelta
from enum import StrEnum

from mockapis.sellerdata.costs import Event
from mockapis.sellerdata.engine import simulate
from mockapis.synth import SyntheticCase, TxnCategory
from shared.contracts import Transaction, TxnTypeCode
from shared.krcalendar import next_business_day

POSTLOAN_DAYS = 91
STACKING_ONSET_DAY = 7
CAPEX_DAY = 2
CAPEX_UNIT_KRW = 10_000
CAPEX_FLOOR_RATIO = 0.08  # 월 주문액 대비, 선매입 뒤 2주 안에 남는 잔고(그 뒤 목표 범위 전까지는 절반 이상)
BALANCE_DUE_FROM_DAY = 14  # 선매입 외상 잔금을 3주차 수요일부터 매주 낸다
DEFAULT_WINDOW = (70, 80)  # 첫 부도일 목표 범위(일). 앞선 저점 때문에 잔금을 깎으면 며칠 늦어질 수 있다
INSTALLMENT_KRW = 287_000  # 330만 원·12개월·연 8% 원리금균등
INSTALLMENT_DAY = 15
PROCEEDS_KRW = 1_500_000
REPAYMENT_PER_LOAN_KRW = 70_000
PAYROLL_DELAY_FROM_DAY = 28
INSURANCE_UNPAID_FROM_DAY = 35
REVENUE_FLOOR = 0.4
DECAY_END_DAY = 56
AD_SURGE = 1.8  # 매출을 살리려 광고 충전·카드 결제가 는다
LENDER = "에이엠에이렌딩"
# (상환 적요, 상환 유형, 대출금 적요, 대출금 유형). 두 번째는 규칙(대부·P2P·대출금 키워드)에 걸리지 않는다.
_STACK_LENDERS: tuple[tuple[str, TxnTypeCode, str, TxnTypeCode], ...] = (
    ("한빛대부 상환", "AUTO_DEBIT", "한빛대부 대출금", "TRANSFER_IN"),
    ("머니무브캐피탈", "AUTO_DEBIT", "머니무브 빠른자금", "TRANSFER_IN"),
    ("펀딩리P2P 상환", "AUTO_DEBIT", "펀딩리 투자금 입금", "TRANSFER_IN"),
)


class FlowScenario(StrEnum):
    STABLE = "stable"
    STACKING = "stacking"


@dataclass(frozen=True)
class PostLoanFlow:
    applicant_id: str
    scenario: FlowScenario
    start: date
    end: date
    transactions: tuple[Transaction, ...]
    labels: dict[str, TxnCategory]
    stacking_onset: date | None
    default_on: date | None


def _revenue_factor(day: int) -> float:
    if day < STACKING_ONSET_DAY:
        return 1.0
    progress = min((day - STACKING_ONSET_DAY) / (DECAY_END_DAY - STACKING_ONSET_DAY), 1.0)
    return 1.0 - (1.0 - REVENUE_FLOOR) * progress


def _installments(start: date, end: date) -> list[Event]:
    out = []
    y, m = start.year, start.month
    while date(y, m, 1) < end:
        d = next_business_day(date(y, m, INSTALLMENT_DAY))
        if start <= d < end:
            out.append(Event(d, -INSTALLMENT_KRW, f"{LENDER} 대출상환", "AUTO_DEBIT", TxnCategory.LOAN_REPAYMENT))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _stacking_loans(onset: date, end: date) -> list[Event]:
    out: list[Event] = []
    loans = 0
    d = onset
    while d < end:
        if (d - onset).days % 14 == 0:
            _, _, desc, code = _STACK_LENDERS[loans % len(_STACK_LENDERS)]
            out.append(Event(d, PROCEEDS_KRW, desc, code, TxnCategory.LOAN_PROCEEDS))
            loans += 1
        if d.weekday() == 4:
            for i in range(loans):
                desc, code, _, _ = _STACK_LENDERS[i % len(_STACK_LENDERS)]
                out.append(Event(d, -REPAYMENT_PER_LOAN_KRW, desc, code, TxnCategory.HIGH_COST_LENDER))
        d += timedelta(days=1)
    return out


def _stack(events: list[Event], start: date) -> list[Event]:
    """광고·카드 급증, 4대보험 미납(출금 없음)."""
    out = []
    for e in events:
        day = (e.booked_on - start).days
        if day >= STACKING_ONSET_DAY and e.category in (TxnCategory.ADVERTISING, TxnCategory.CARD_PAYMENT):
            e = replace(e, amount_krw=round(e.amount_krw * AD_SURGE))
        if day >= INSURANCE_UNPAID_FROM_DAY and e.category is TxnCategory.SOCIAL_INSURANCE:
            continue
        out.append(e)
    return out


def _end_of_day_balances(opening: int, events: list[Event], start: date, days: int) -> list[int]:
    by_day = [0] * days
    for e in events:
        by_day[(e.booked_on - start).days] += e.amount_krw
    balances, balance = [], opening
    for delta in by_day:
        balance += delta
        balances.append(balance)
    return balances


def _due_days(start: date, days: int) -> list[int]:
    """외상 잔금 납부일(대출 시작 기준 일수): BALANCE_DUE_FROM_DAY 이후 매주 수요일, 공휴일이면 다음 영업일."""
    out = []
    for offset in range(BALANCE_DUE_FROM_DAY, days):
        d = start + timedelta(days=offset)
        if d.weekday() == 2:
            paid = (next_business_day(d) - start).days
            if paid < days:
                out.append(paid)
    return out


def _capex_plan(opening: int, events: list[Event], start: date, days: int, floor: int) -> tuple[int, int, list[int]]:
    """(선매입액, 주간 외상 잔금, 납부일). 잔금 W는 DEFAULT_WINDOW 안 어느 날 잔고를 0 아래로 내리는 최소 금액이고,
    그 전 날들은 0 이상으로 남도록 W를 상한으로 깎는다."""
    balances = _end_of_day_balances(opening, events, start, days)
    lo_day, hi_day = DEFAULT_WINDOW
    keep = min(min(balances[CAPEX_DAY:BALANCE_DUE_FROM_DAY]) - floor, min(balances[CAPEX_DAY:lo_day]) - floor // 2)
    upfront = max(keep, 0) // CAPEX_UNIT_KRW * CAPEX_UNIT_KRW
    base = [b - (upfront if t >= CAPEX_DAY else 0) for t, b in enumerate(balances)]
    due = _due_days(start, days)

    def paid_by(t: int) -> int:
        return sum(1 for d in due if d <= t)

    need = min(base[t] / paid_by(t) for t in range(lo_day, min(hi_day + 1, days)))
    weekly = (int(need) // CAPEX_UNIT_KRW + 1) * CAPEX_UNIT_KRW
    allowed = min((base[t] / paid_by(t) for t in range(lo_day) if paid_by(t)), default=float("inf"))
    if weekly > allowed:
        weekly = int(allowed) // CAPEX_UNIT_KRW * CAPEX_UNIT_KRW
    return upfront, max(weekly, 0), due


def generate_postloan(
    case: SyntheticCase, scenario: FlowScenario, start: date, seed: int, days: int = POSTLOAN_DAYS
) -> PostLoanFlow:
    rng = random.Random(seed)
    persona = case.persona
    stacking = scenario is FlowScenario.STACKING
    onset = start + timedelta(days=STACKING_ONSET_DAY) if stacking else None
    end = start + timedelta(days=days)
    opening = case.transactions[-1].balance_after_krw if case.transactions else 0

    def late(d: date) -> bool:
        return stacking and (d - start).days >= PAYROLL_DELAY_FROM_DAY

    ledger = simulate(
        persona, start, end, rng,
        sales_factor=lambda d: _revenue_factor((d - start).days) if stacking else 1.0,
        payroll_day=lambda y, m: 20 if late(date(y, m, 20)) else 10,
        extra=[*_installments(start, end), *(_stacking_loans(onset, end) if onset else ())],
    )
    events = list(ledger.events)
    if stacking:
        events = _stack(events, start)
        floor = round(persona.monthly_sales_krw * CAPEX_FLOOR_RATIO)
        upfront, weekly, due = _capex_plan(opening, events, start, days, floor)
        if upfront:
            events.append(Event(start + timedelta(days=CAPEX_DAY), -upfront, "시즌 재고 대량 선매입", "TRANSFER_OUT",
                                TxnCategory.SUPPLIER))
        if weekly:
            events += [Event(start + timedelta(days=d), -weekly, "시즌 재고 외상 잔금", "TRANSFER_OUT",
                             TxnCategory.SUPPLIER) for d in due]
        events.sort(key=lambda e: (e.booked_on, e.amount_krw < 0))

    balance = opening
    transactions: list[Transaction] = []
    labels: dict[str, TxnCategory] = {}
    default_on: date | None = None
    prefix = f"{case.profile.applicant_id}-P"
    for i, e in enumerate(events):
        balance += e.amount_krw
        txn_id = f"{prefix}{i:05d}"
        transactions.append(
            Transaction(txn_id=txn_id, booked_on=e.booked_on, amount_krw=e.amount_krw, balance_after_krw=balance,
                        description=e.description, txn_type_code=e.code)
        )
        labels[txn_id] = e.category
    for t, nxt in zip(transactions, [*transactions[1:], None]):
        end_of_day = nxt is None or nxt.booked_on != t.booked_on
        if end_of_day and t.balance_after_krw < 0:
            default_on = t.booked_on
            break
    return PostLoanFlow(
        applicant_id=case.profile.applicant_id,
        scenario=scenario,
        start=start,
        end=end - timedelta(days=1),
        transactions=tuple(transactions),
        labels=labels,
        stacking_onset=onset,
        default_on=default_on,
    )