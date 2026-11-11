"""대출 실행 이후 91일 계좌 흐름·PG 정산 원장 합성(조기경보 재생용, seed 결정론적).

심사 때와 같은 셀러 엔진·페르소나를 쓰고, 원인별 시나리오 계획은 `postloan_scenarios.py`에 있다.

- STABLE: 정상 영업. 오탐 미끼는 엔진이 그대로 만든다: 11월 행사·연말 매출, 1월 부가세 확정 납부,
  공휴일이 낀 주의 정산 공백(쿠팡 주정산·11번가 월 2회가 영업일로 밀린다).
- SALES_SLUMP(주인공): 주문 하락 → 광고·카드 증가 → 대부·P2P 신규 차입(2주 간격, 매주 금요일 상환) →
  급여 지연·4대보험 미납(출금 없음) → 잔고 마이너스. 일부 대부·캐피탈 적요는 규칙 분류기가 못 잡는다.
  선매입은 잔고를 CAPEX_FLOOR_RATIO까지 끌어내리고, 외상 잔금(매주 수요일)은 첫 부도일이 DEFAULT_WINDOW
  안에 오도록 고른다. 그래서 계절·셀러 규모와 무관하게 부도 시점이 같은 범위에 온다.
- SETTLEMENT_HOLD·RETURN_SPIKE·COST_SURGE: 정산 보류·반품 급증·비용 증가가 원인이고 차입은 결과다.
- 상환: 30일 주기 상환일(`shared.repayment.due_dates`)에 자동이체. 기준일은 흐름의 첫 거래일이다(오케스트레이터가
  같은 기준으로 원장을 만든다). 잔고가 모자라면 출금 없이 missed_installments에
  남고, 시나리오가 정한 회차(`force_missed`)도 미납으로 둔다.
- 대출 조건(`LoanTerms`)을 주면 1일차에 대출금이 입금되고(LENDER_CREDIT) 닷새 동안 90%를 매입·운영비로 쓰며
  상환액도 그 조건을 따른다. 주지 않으면 옛 동작대로 287,000원 상환·대출금 입금 없음이다.
- 원장: 사후 기간도 심사와 같은 반영기(`receipts.bank_receipts`)를 거쳐 정산 원장(지급일·상계·보류)과 계좌 입금을
  나눈다. 원장·주문은 대출 전 8주(PRE_LOAN_DAYS)부터 남겨 기준선을 만들 수 있게 한다.
- default_on: 처음으로 일말 잔고가 0 미만이 된 날(부도 대리 지표, P6 평가의 사전 감지 일수 기준).
labels(정답 분류)·cause·default_on은 평가 전용이며 API로 노출하지 않는다.
"""

import random
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from mockapis.postloan_scenarios import end_of_day_balances, scenario_plan
from mockapis.sellerdata.costs import Event
from mockapis.sellerdata.engine import WARMUP_DAYS, simulate
from mockapis.synth import RECEIPT_SEED_OFFSET, Archetype, SyntheticCase, TxnCategory
from shared.contracts import AlertCause, PgFlow, PgOrderDay, PgSettlement, Transaction
from shared.krcalendar import next_business_day
from shared.repayment import LENDER_CREDIT, LENDER_DEBIT, due_dates

POSTLOAN_DAYS = 91
PRE_LOAN_DAYS = 56  # 원장 기준선용 대출 전 8주(사후 시뮬레이션 워밍업에서 만든다)
CAPEX_DAY = 2
CAPEX_UNIT_KRW = 10_000
CAPEX_FLOOR_RATIO = 0.08  # 월 주문액 대비, 선매입 뒤 2주 안에 남는 잔고(그 뒤 목표 범위 전까지는 절반 이상)
BALANCE_DUE_FROM_DAY = 14  # 선매입 외상 잔금을 3주차 수요일부터 매주 낸다
DEFAULT_WINDOW = (70, 80)  # 첫 부도일 목표 범위(일). 앞선 저점 때문에 잔금을 깎으면 며칠 늦어질 수 있다
INSTALLMENT_KRW = 287_000  # 330만 원·12개월·연 8% 원리금균등(대출 조건이 없는 옛 재생의 기본값)
DISBURSE_SHARE = 0.9  # 대출금 중 1~5일차에 쓰는 비율
DISBURSE_WEIGHTS = (0.35, 0.25, 0.15, 0.1, 0.05)
DISBURSE_SUPPLIER_DAYS = 3  # 앞 사흘은 재고 매입, 나머지는 운영비
LENDER = "에이엠에이렌딩"


class FlowScenario(StrEnum):
    STABLE = "stable"
    SALES_SLUMP = "sales_slump"
    SETTLEMENT_HOLD = "settlement_hold"
    RETURN_SPIKE = "return_spike"
    COST_SURGE = "cost_surge"

    @classmethod
    def _missing_(cls, value: object) -> "FlowScenario | None":
        return cls.SALES_SLUMP if value == "stacking" else None  # 옛 재생 문서 호환(2026-11-11)


@dataclass(frozen=True)
class LoanTerms:
    amount_krw: int
    installment_krw: int


@dataclass(frozen=True)
class PostLoanFlow:
    applicant_id: str
    scenario: FlowScenario
    start: date
    end: date
    transactions: tuple[Transaction, ...]
    labels: dict[str, TxnCategory]
    stacking_onset: date | None  # 첫 외부 고금리 차입일(없으면 None)
    default_on: date | None
    missed_installments: tuple[date, ...] = ()
    cause: AlertCause | None = None
    secondary_causes: tuple[AlertCause, ...] = ()
    onset: date | None = None  # 원인이 시작된 날
    pg_settlements: tuple[PgSettlement, ...] = ()  # start - PRE_LOAN_DAYS ~ end
    orders: tuple[PgOrderDay, ...] = ()

    def pg_flow(self) -> PgFlow:
        return PgFlow(settlements=self.pg_settlements, orders=self.orders)


def _installments(dues: list[date], installment_krw: int) -> list[Event]:
    return [Event(d, -installment_krw, LENDER_DEBIT, "AUTO_DEBIT", TxnCategory.LOAN_REPAYMENT) for d in dues]


def _disbursement(case: SyntheticCase, start: date, loan: "LoanTerms | None") -> list[Event]:
    """1일차 대출금 입금과 1~5일차 사용처(재고 매입·운영비)."""
    if loan is None:
        return []
    out = [Event(start, loan.amount_krw, LENDER_CREDIT, "TRANSFER_IN", TxnCategory.LOAN_PROCEEDS)]
    budget = round(loan.amount_krw * DISBURSE_SHARE)
    spent = 0
    for i, weight in enumerate(DISBURSE_WEIGHTS):
        amount = budget - spent if i == len(DISBURSE_WEIGHTS) - 1 else round(budget * weight)
        desc = f"{case.persona.suppliers[0]} 재고 매입" if i < DISBURSE_SUPPLIER_DAYS else "운영비 이체"
        if amount > 0:
            out.append(Event(start + timedelta(days=i), -amount, desc, "TRANSFER_OUT", TxnCategory.SUPPLIER))
        spent += amount
    return out


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


def _capex_plan(opening: int, events: list[Event], start: date, days: int, floor: int) -> tuple[int, dict[int, int]]:
    """(선매입액, 납부일별 외상 잔금). 주간 잔금 W는 DEFAULT_WINDOW 안 어느 날 잔고를 0 아래로 내리는 최소 금액이고,
    그 전 날들은 0 이상으로 남도록 W를 상한으로 깎는다. 깎느라 범위 안에서 0 아래로 못 내려가면
    (계절 매출이 받쳐 주는 셀러) 범위 안 납부일 한 번에 남은 잔금을 몰아 낸다."""
    balances = end_of_day_balances(opening, events, start, days)
    lo_day, hi_day = DEFAULT_WINDOW
    keep = min(min(balances[CAPEX_DAY:BALANCE_DUE_FROM_DAY]) - floor, min(balances[CAPEX_DAY:lo_day]) - floor // 2)
    upfront = max(keep, 0) // CAPEX_UNIT_KRW * CAPEX_UNIT_KRW
    base = [b - (upfront if t >= CAPEX_DAY else 0) for t, b in enumerate(balances)]
    due = _due_days(start, days)
    hi_day = min(hi_day, days - 1)

    def paid_by(t: int) -> int:
        return sum(1 for d in due if d <= t)

    def over_unit(amount: float) -> int:
        return (int(amount) // CAPEX_UNIT_KRW + 1) * CAPEX_UNIT_KRW

    need = min(base[t] / paid_by(t) for t in range(lo_day, hi_day + 1))
    allowed = min((base[t] / paid_by(t) for t in range(lo_day) if paid_by(t)), default=float("inf"))
    weekly = max(min(over_unit(need), int(allowed) // CAPEX_UNIT_KRW * CAPEX_UNIT_KRW), 0)
    plan = {d: weekly for d in due} if weekly else {}
    rest = [base[t] - weekly * paid_by(t) for t in range(lo_day, hi_day + 1)]
    if min(rest) >= 0:
        target = next((d for d in due if lo_day <= d <= hi_day), hi_day)
        plan[target] = plan.get(target, 0) + over_unit(rest[target - lo_day])
    return upfront, plan


def _capex_events(case: SyntheticCase, events: list[Event], start: date, days: int) -> list[Event]:
    """시즌 재고 선매입과 외상 잔금(첫 부도일을 DEFAULT_WINDOW 안에 맞춘다)."""
    opening = case.transactions[-1].balance_after_krw if case.transactions else 0
    floor = round(case.persona.monthly_sales_krw * CAPEX_FLOOR_RATIO)
    upfront, plan = _capex_plan(opening, events, start, days, floor)
    out: list[Event] = []
    if upfront:
        out.append(Event(start + timedelta(days=CAPEX_DAY), -upfront, "시즌 재고 대량 선매입", "TRANSFER_OUT",
                         TxnCategory.SUPPLIER))
    out += [Event(start + timedelta(days=d), -amount, "시즌 재고 외상 잔금", "TRANSFER_OUT", TxnCategory.SUPPLIER)
            for d, amount in sorted(plan.items())]
    return out


def _external_debt_on(transactions: list[Transaction], labels: dict[str, TxnCategory]) -> date | None:
    """첫 외부 고금리 차입일. 자체 대출금 입금·선정산 선지급은 차입으로 세지 않는다."""
    days = [
        t.booked_on for t in transactions
        if labels[t.txn_id] in (TxnCategory.HIGH_COST_LENDER, TxnCategory.LOAN_PROCEEDS)
        and t.description != LENDER_CREDIT and "선정산" not in t.description
    ]
    return min(days) if days else None


def generate_postloan(
    case: SyntheticCase, scenario: FlowScenario, start: date, seed: int, days: int = POSTLOAN_DAYS,
    *, loan: LoanTerms | None = None,
) -> PostLoanFlow:
    rng = random.Random(seed)
    end = start + timedelta(days=days)
    ledger_from = start - timedelta(days=PRE_LOAN_DAYS)
    plan = scenario_plan(scenario, case, start, days)
    assigned = [d for d in (plan.assigned_from, start if case.archetype is Archetype.ASSIGNED else None) if d]

    ledger = simulate(
        case.persona, start, end, rng,
        sales_factor=plan.sales_factor,
        extra=[*_disbursement(case, start, loan), *plan.extra],
        receipt_rng=random.Random(seed + RECEIPT_SEED_OFFSET),
        assigned_from=min(assigned) if assigned else None,
        return_rate=plan.return_rate,
        receipt_hooks=plan.hooks,
        warmup_days=WARMUP_DAYS + PRE_LOAN_DAYS,
        ledger_from=ledger_from,
    )
    # 오케스트레이터는 흐름의 첫 거래일로 상환 원장을 만든다. 정산 입금이 밀려 1일차에 거래가 없는 셀러도 있어
    # 상환일과 흐름 창을 같은 기준(첫 거래일)으로 잡아야 납부 건이 미납으로 읽히지 않는다.
    anchor = min((e.booked_on for e in ledger.events), default=start)
    last = anchor + timedelta(days=days - 1)
    dues = due_dates(anchor, days)
    installments = _installments(dues, loan.installment_krw if loan else INSTALLMENT_KRW)
    events = plan.adjust(sorted([*ledger.events, *installments], key=lambda e: e.booked_on), ledger.orders)
    if plan.calibrate_default:
        events += _capex_events(case, events, start, days)
    events.sort(key=lambda e: (e.booked_on, e.amount_krw < 0))

    balance = case.transactions[-1].balance_after_krw if case.transactions else 0
    transactions: list[Transaction] = []
    labels: dict[str, TxnCategory] = {}
    default_on: date | None = None
    prefix = f"{case.profile.applicant_id}-P"
    forced = plan.force_missed(dues)
    missed: list[date] = []
    for e in events:
        # 잔고가 모자라면 자동이체가 처리되지 않는다. 시나리오가 정한 회차도 미납으로 둔다(추심 재생용).
        if e.description == LENDER_DEBIT and e.amount_krw < 0 and (balance < -e.amount_krw or e.booked_on in forced):
            missed.append(e.booked_on)
            continue
        balance += e.amount_krw
        txn_id = f"{prefix}{len(transactions):05d}"
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
        end=last,
        transactions=tuple(transactions),
        labels=labels,
        stacking_onset=_external_debt_on(transactions, labels),
        default_on=default_on,
        missed_installments=tuple(missed),
        cause=plan.cause,
        secondary_causes=plan.secondary,
        onset=plan.onset,
        pg_settlements=tuple(s for s in ledger.pg_settlements if s.settled_on <= last),
        orders=tuple(
            PgOrderDay(order_on=o.day, platform=o.channel, gross_krw=o.gross_krw, returns_krw=o.returns_krw)
            for o in ledger.orders if ledger_from <= o.day <= last
        ),
    )
