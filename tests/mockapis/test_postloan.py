from datetime import date, timedelta

import pytest

from agents.classify import Category, classify
from mockapis.demo import build_demo_cases
from mockapis.demo import build_load_cases
from mockapis.postloan import INSURANCE_UNPAID_FROM_DAY, STACKING_ONSET_DAY, FlowScenario, generate_postloan
from mockapis.sellerdata.personas import DEMO_PERSONAS
from mockapis.synth import Archetype, TxnCategory, generate_case

AS_OF = date(2026, 11, 2)


@pytest.fixture(scope="module")
def cafe():
    return build_demo_cases(AS_OF)["seller-dailylook-01"]


def test_same_seed_same_flow(cafe):
    a = generate_postloan(cafe, FlowScenario.STACKING, AS_OF, 7)
    b = generate_postloan(cafe, FlowScenario.STACKING, AS_OF, 7)
    assert a == b


def test_flow_continues_the_underwriting_account(cafe):
    flow = generate_postloan(cafe, FlowScenario.STABLE, AS_OF, 7)
    first = flow.transactions[0]
    assert first.booked_on == AS_OF
    assert first.balance_after_krw == cafe.transactions[-1].balance_after_krw + first.amount_krw
    assert flow.end == AS_OF + timedelta(days=90)
    ids = {t.txn_id for t in flow.transactions}
    assert len(ids) == len(flow.transactions)
    assert not ids & {t.txn_id for t in cafe.transactions}


def test_stable_never_defaults_and_has_no_new_debt(cafe):
    flow = generate_postloan(cafe, FlowScenario.STABLE, AS_OF, 7)
    assert flow.default_on is None and flow.stacking_onset is None
    assert not {TxnCategory.HIGH_COST_LENDER, TxnCategory.LOAN_PROCEEDS} & set(flow.labels.values())


@pytest.mark.parametrize("as_of", [date(2026, 11, 2), date(2026, 5, 4), date(2027, 2, 1), date(2026, 8, 3)])
@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("seller", [None, *DEMO_PERSONAS])
def test_stacking_defaults_late_enough_for_a_60_day_warning(as_of, seed, seller):
    # 11월 시작은 행사·연말 매출이 잔고를 받쳐 준다. 규모·계절과 무관하게 부도 시점이 같은 범위에 와야 한다.
    persona = DEMO_PERSONAS[seller] if seller else None
    case = generate_case(f"x{seed}", Archetype.HEALTHY_SEASONAL, as_of, seed, persona=persona)
    flow = generate_postloan(case, FlowScenario.STACKING, as_of, seed)
    assert flow.default_on is not None
    first_debt = min(
        t.booked_on for t in flow.transactions if flow.labels[t.txn_id] is TxnCategory.HIGH_COST_LENDER
    )
    assert flow.stacking_onset == as_of + timedelta(days=STACKING_ONSET_DAY)
    assert 60 <= (flow.default_on - first_debt).days
    assert 70 <= (flow.default_on - as_of).days <= 90


def test_stacking_hides_some_lenders_from_the_rules(cafe):
    flow = generate_postloan(cafe, FlowScenario.STACKING, AS_OF, 7)
    hidden = {
        t.description
        for t in flow.transactions
        if flow.labels[t.txn_id] in (TxnCategory.HIGH_COST_LENDER, TxnCategory.LOAN_PROCEEDS)
        and classify(t) in (Category.UNCLASSIFIED_IN, Category.UNCLASSIFIED_OUT)
    }
    assert {"머니무브캐피탈", "머니무브 빠른자금"} <= hidden


def test_stacking_delays_payroll(cafe):
    flow = generate_postloan(cafe, FlowScenario.STACKING, AS_OF, 7)
    payroll_days = sorted({t.booked_on.day for t in flow.transactions if flow.labels[t.txn_id] is TxnCategory.PAYROLL})
    assert 20 in payroll_days


def test_stacking_story_on_the_seller_engine(cafe):
    flow = generate_postloan(cafe, FlowScenario.STACKING, AS_OF, 7)
    by_desc = lambda d: [t for t in flow.transactions if t.description == d]  # noqa: E731
    (capex,) = by_desc("시즌 재고 대량 선매입")
    assert capex.booked_on == AS_OF + timedelta(days=2) and capex.amount_krw < -cafe.persona.monthly_sales_krw / 4
    dues = by_desc("시즌 재고 외상 잔금")
    assert len(dues) >= 8 and len({t.amount_krw for t in dues}) == 1
    # 3주차부터 매주 수요일(공휴일이면 다음 영업일)
    assert dues[0].booked_on >= AS_OF + timedelta(days=14) and all(t.booked_on.weekday() in (2, 3, 4) for t in dues)
    unpaid_from = AS_OF + timedelta(days=INSURANCE_UNPAID_FROM_DAY)
    insurance = [t for t in flow.transactions if flow.labels[t.txn_id] is TxnCategory.SOCIAL_INSURANCE]
    assert insurance and all(t.booked_on < unpaid_from for t in insurance)


def test_load_sellers_keep_their_own_persona_after_the_loan():
    cases = build_load_cases(AS_OF)
    small = min(cases.values(), key=lambda c: c.persona.monthly_sales_krw)
    big = max(cases.values(), key=lambda c: c.persona.monthly_sales_krw)

    def weekly(case):
        flow = generate_postloan(case, FlowScenario.STABLE, AS_OF, 1)
        return sum(t.amount_krw for t in flow.transactions if t.amount_krw > 0) / 13
    sales_ratio = big.persona.monthly_sales_krw / small.persona.monthly_sales_krw
    assert sales_ratio > 1.3
    assert weekly(big) / weekly(small) == pytest.approx(sales_ratio, rel=0.25)
