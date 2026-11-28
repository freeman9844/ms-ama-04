"""평가 데이터셋: 조기경보 원인 경로(원인 4개 × 아키타입 2개 + STABLE 2개)."""

from evals.datasets import (
    ARCHETYPES,
    EW_CAUSE_SCENARIOS,
    INJECTION_VARIANT_ARCHETYPES,
    INJECTION_VARIANTS,
    counterfactual_cases,
    ew_paths,
    summary,
    underwriting_cases,
)
from mockapis.postloan import FlowScenario
from mockapis.synth import INJECTION_TEXT
from shared.repayment import LENDER_CREDIT


def test_ew_paths_cover_causes():
    paths = ew_paths()
    assert len(paths) == 10 and len({p.path_id for p in paths}) == 10
    assert sorted(p.flow.scenario.value for p in paths if p.positive) == sorted(
        [s.value for s in EW_CAUSE_SCENARIOS] * 2
    )
    assert all(p.flow.cause is not None and p.onset_week for p in paths if p.positive)
    stable = [p for p in paths if p.flow.scenario is FlowScenario.STABLE]
    assert len(stable) == 2 and not any(p.positive or p.onset_week for p in stable)


def test_ew_paths_carry_loan_terms_and_ledger():
    p = ew_paths()[0]
    assert any(t.description == LENDER_CREDIT for t in p.flow.transactions)  # 자체 대출금 입금
    pg = p.flow.pg_flow()
    assert pg.orders and pg.settlements and min(o.order_on for o in pg.orders) < p.flow.start


def test_summary_records_cause_and_onset():
    rows = {r["path_id"]: r for r in summary()["early_warning"]["paths"]}
    assert len(rows) == 10
    assert rows["ev-eheals"]["cause"] == "sales_slump" and rows["ev-eheals"]["onset_week"] >= 1
    assert rows["ev-ehealb"]["cause"] is None and rows["ev-ehealb"]["onset_week"] is None

def test_injection_variants_cover_informative_archetypes():
    cases = underwriting_cases()
    for archetype in INJECTION_VARIANT_ARCHETYPES:
        injected = [c for c in cases if c.archetype is archetype and c.injected]
        texts = {t.description for c in injected for t in c.case.transactions} - {
            t.description for c in cases if c.archetype is archetype and not c.injected for t in c.case.transactions}
        assert len(injected) == 1 + len(INJECTION_VARIANTS)
        assert texts == {INJECTION_TEXT, *INJECTION_VARIANTS.values()}
    assert len({c.case_id for c in cases}) == len(cases)
    assert summary()["underwriting"]["injected"] == len(ARCHETYPES) + len(INJECTION_VARIANT_ARCHETYPES) * len(INJECTION_VARIANTS)

def test_counterfactual_pairs_cover_every_archetype_with_a_different_region():
    cases = underwriting_cases()
    pairs = counterfactual_cases(cases)
    base = {c.case_id: c for c in cases}
    assert {base[p.pair_of].archetype for p in pairs} == set(ARCHETYPES)
    assert all(p.case.profile.region != base[p.pair_of].case.profile.region for p in pairs)
    assert all(p.case.transactions[i].amount_krw == t.amount_krw
               for p in pairs for i, t in enumerate(base[p.pair_of].case.transactions))
