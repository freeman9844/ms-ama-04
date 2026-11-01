"""대출 실행 이후 91일 계좌 흐름 합성(조기경보 재생용, seed 결정론적).

- STABLE: 심사 시점과 같은 영업 패턴. 계절 지수(12월 1.2, 1~2월 0.8)만 움직인다(오탐 미끼).
- STACKING: 확장 공사대금으로 잔고가 줄어든 뒤 매출이 꺾이고, 2주마다 고금리 대출을 새로 받아
  매주 상환이 쌓인다(loan stacking). 급여가 늦어지고 결국 잔고가 0 아래로 내려간다.
  일부 대부·캐피탈 적요는 규칙 분류기가 못 잡도록 만들어 의미 분류의 몫을 남긴다.
- default_on: 처음으로 일말 잔고가 0 미만이 된 날(부도 대리 지표, P6 평가의 사전 감지 일수 기준).
labels(정답 분류)·default_on은 평가 전용이며 API로 노출하지 않는다.
"""

import random
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from mockapis.synth import _BASE_DAILY, _CARD_ISSUERS, _PERSONAS, _PG_FEE_RATE, _SEASONAL, SyntheticCase, TxnCategory
from shared.contracts import Transaction, TxnTypeCode

POSTLOAN_DAYS = 91
STACKING_ONSET_DAY = 7
CAPEX_DAY = 2
CAPEX_FLOOR_KRW = 24_000_000
INSTALLMENT_KRW = 1_600_000
INSTALLMENT_DAY = 15
PROCEEDS_KRW = 2_000_000
REPAYMENT_PER_LOAN_KRW = 600_000
PAYROLL_DELAY_FROM_DAY = 28
REVENUE_FLOOR = 0.4
DECAY_END_DAY = 56
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


def _revenue_factor(day: int, stacking: bool) -> float:
    if not stacking or day < STACKING_ONSET_DAY:
        return 1.0
    progress = min((day - STACKING_ONSET_DAY) / (DECAY_END_DAY - STACKING_ONSET_DAY), 1.0)
    return 1.0 - (1.0 - REVENUE_FLOOR) * progress


def generate_postloan(
    case: SyntheticCase, scenario: FlowScenario, start: date, seed: int, days: int = POSTLOAN_DAYS
) -> PostLoanFlow:
    rng = random.Random(seed)
    persona = _PERSONAS[case.archetype]
    stacking = scenario is FlowScenario.STACKING
    onset = start + timedelta(days=STACKING_ONSET_DAY) if stacking else None
    opening = case.transactions[-1].balance_after_krw if case.transactions else 0
    events: list[tuple[date, int, str, TxnTypeCode, TxnCategory]] = []

    def add(d: date, amount: int, desc: str, code: TxnTypeCode, cat: TxnCategory) -> None:
        events.append((d, amount, desc, code, cat))

    loans_taken = 0
    for day in range(days):
        d = start + timedelta(days=day)
        season = _SEASONAL.get(d.month, 1.0)
        nominal = _BASE_DAILY * season
        base = nominal * _revenue_factor(day, stacking)

        add(d, int(base * rng.uniform(0.85, 1.15)), f"{rng.choice(_CARD_ISSUERS)} 매출", "CARD_SETTLE", TxnCategory.REVENUE)
        if d.weekday() == 0:
            platform = rng.choice(("배민", "쿠팡이츠"))
            gross = int(base * rng.uniform(1.5, 2.5))
            add(d, gross - round(gross * _PG_FEE_RATE), f"{platform} 정산", "PG_SETTLE", TxnCategory.REVENUE)
        if d.day == 5:
            add(d, -2_500_000, f"임대료 {persona.region}", "AUTO_DEBIT", TxnCategory.RENT)
        payroll_day = 20 if stacking and day >= PAYROLL_DELAY_FROM_DAY else 10
        if d.day == payroll_day:
            for name, pay in zip(persona.employees, (2_800_000, 2_600_000)):
                add(d, -pay, f"급여 {name}", "TRANSFER_OUT", TxnCategory.PAYROLL)
        if d.day == 10:
            add(d, -480_000, "국민건강보험공단", "AUTO_DEBIT", TxnCategory.SOCIAL_INSURANCE)
        if d.weekday() == 2:
            # 거래처 대금은 매출보다 늦게 줄어든다(재고·계약). 계절만 반영한 명목 기준으로 쓴다.
            add(d, -int(nominal * 3.0 * rng.uniform(0.9, 1.1)), persona.suppliers[0], "TRANSFER_OUT", TxnCategory.SUPPLIER)
            add(d, -int(nominal * 1.0 * rng.uniform(0.9, 1.1)), persona.suppliers[1], "TRANSFER_OUT", TxnCategory.SUPPLIER)
        if d.day == INSTALLMENT_DAY:
            add(d, -INSTALLMENT_KRW, f"{LENDER} 대출상환", "AUTO_DEBIT", TxnCategory.LOAN_REPAYMENT)
        if d.day == 25:
            add(d, -650_000, "기업은행 대출상환", "AUTO_DEBIT", TxnCategory.LOAN_REPAYMENT)
            if d.month in (1, 7):
                add(d, -1_800_000, "부가세 국세청", "TAX", TxnCategory.TAX)
        if d.day == 28:
            add(d, -1_500_000, f"{persona.representative} 개인이체", "TRANSFER_OUT", TxnCategory.OWNER_TRANSFER)
        if stacking:
            if day == CAPEX_DAY:
                capex = max(opening - CAPEX_FLOOR_KRW, 0) // 100_000 * 100_000
                if capex:
                    add(d, -capex, "2호점 인테리어 공사대금", "TRANSFER_OUT", TxnCategory.SUPPLIER)
            if day >= STACKING_ONSET_DAY and (day - STACKING_ONSET_DAY) % 14 == 0:
                _, _, desc, code = _STACK_LENDERS[loans_taken % len(_STACK_LENDERS)]
                add(d, PROCEEDS_KRW, desc, code, TxnCategory.LOAN_PROCEEDS)
                loans_taken += 1
            if d.weekday() == 4:
                for i in range(loans_taken):
                    desc, code, _, _ = _STACK_LENDERS[i % len(_STACK_LENDERS)]
                    add(d, -REPAYMENT_PER_LOAN_KRW, desc, code, TxnCategory.HIGH_COST_LENDER)

    balance = opening
    transactions: list[Transaction] = []
    labels: dict[str, TxnCategory] = {}
    default_on: date | None = None
    prefix = f"{case.profile.applicant_id}-P"
    for i, (booked_on, amount, desc, code, cat) in enumerate(events):
        balance += amount
        txn_id = f"{prefix}{i:05d}"
        transactions.append(
            Transaction(
                txn_id=txn_id,
                booked_on=booked_on,
                amount_krw=amount,
                balance_after_krw=balance,
                description=desc,
                txn_type_code=code,
            )
        )
        labels[txn_id] = cat
    for t, nxt in zip(transactions, [*transactions[1:], None]):
        end_of_day = nxt is None or nxt.booked_on != t.booked_on
        if end_of_day and t.balance_after_krw < 0:
            default_on = t.booked_on
            break
    return PostLoanFlow(
        applicant_id=case.profile.applicant_id,
        scenario=scenario,
        start=start,
        end=start + timedelta(days=days - 1),
        transactions=tuple(transactions),
        labels=labels,
        stacking_onset=onset,
        default_on=default_on,
    )
