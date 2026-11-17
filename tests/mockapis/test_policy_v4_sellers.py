"""데모 셀러 21곳의 정책 v4 판정: 마켓 자료가 바꾼 셀러와 그대로인 셀러(스펙 4.3·8.3)."""

from datetime import date

import pytest

from agents.features import compute_underwriting
from mockapis.demo import build_demo_cases
from policy.engine import explain
from policy.rules import load_rules

AS_OF = date(2026, 11, 17)
AMOUNT_KRW = 5_000_000
TENOR_MONTHS = 6
V4_EXPECTED = {  # (decision, reason code 집합, cash_only decision)
    "seller-knitroom-04": ("refer", {"S001", "R107"}, "ineligible"),
    "seller-leather-14": ("refer", {"S001", "R107"}, "ineligible"),
    "seller-petfood-07": ("ineligible", {"R003"}, "eligible"),
    "seller-ceramic-08": ("refer", {"R107", "R109"}, "refer"),
}
STILL_INELIGIBLE = ("seller-tint-17", "seller-streetfit-19")


@pytest.fixture(scope="module")
def explanations():
    rules = load_rules()
    out = {}
    for applicant_id, case in build_demo_cases(AS_OF).items():
        uw = compute_underwriting(case.profile.applicant_id, case.profile, case.transactions, case.tax_filings,
                                  as_of=AS_OF, requested_amount_krw=AMOUNT_KRW, requested_tenor_months=TENOR_MONTHS,
                                  pg_settlements=case.pg_settlements, marketplace=case.marketplace)
        out[applicant_id] = explain(uw.features, rules)
    return out


def _codes(decision):
    return {r.code for r in decision.reason_codes}


@pytest.mark.parametrize("applicant_id", list(V4_EXPECTED))
def test_v4_demo_outcomes(explanations, applicant_id):
    d = explanations[applicant_id].decision
    assert (d.decision.value, _codes(d), d.cash_only.decision.value) == V4_EXPECTED[applicant_id]
    if "S001" in _codes(d):
        assert 0 < d.max_amount_krw <= 5_000_000


def test_tint_streetfit_stay_ineligible(explanations):
    tint = explanations["seller-tint-17"]
    assert tint.decision.decision.value == "ineligible" and {"R001", "R002"} <= _codes(tint.decision)
    s001 = next(t for t in tint.substitutions if t.code == "S001")
    assert s001.conditions_met and not s001.applied and "R002" in s001.blocked_by
    streetfit = explanations["seller-streetfit-19"].decision
    assert streetfit.decision.value == "ineligible" and {"R001", "R002", "R110"} <= _codes(streetfit)


def test_other_sellers_match_cash_only(explanations):
    others = set(explanations) - set(V4_EXPECTED) - set(STILL_INELIGIBLE)
    assert len(others) == 15
    for applicant_id in sorted(others):
        d = explanations[applicant_id].decision
        assert (d.decision, _codes(d)) == (d.cash_only.decision, _codes(d.cash_only)), applicant_id
