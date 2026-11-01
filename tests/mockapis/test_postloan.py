from datetime import date, timedelta

import pytest

from agents.classify import Category, classify
from mockapis.demo import build_demo_cases
from mockapis.postloan import STACKING_ONSET_DAY, FlowScenario, generate_postloan
from mockapis.synth import Archetype, TxnCategory, generate_case

AS_OF = date(2026, 11, 2)


@pytest.fixture(scope="module")
def cafe():
    return build_demo_cases(AS_OF)["demo-cafe-001"]


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
def test_stacking_defaults_late_enough_for_a_60_day_warning(as_of, seed):
    case = generate_case(f"x{seed}", Archetype.HEALTHY_SEASONAL, as_of, seed)
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
