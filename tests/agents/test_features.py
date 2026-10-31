from datetime import date

import pytest

from agents.features import FLAG_HIGH_COST_LENDER, compute_underwriting, monthly_installment
from mockapis.demo import build_demo_cases
from policy.engine import evaluate
from policy.rules import DEFAULT_RULES_PATH, load_rules

AS_OF = date(2026, 11, 2)
AMOUNT = 20_000_000
TENOR = 12


@pytest.fixture(scope="module")
def cases():
    return build_demo_cases(AS_OF)


def _underwrite(cases, applicant_id, transactions=None):
    case = cases[applicant_id]
    return compute_underwriting(
        f"app-{applicant_id}",
        case.profile,
        case.transactions if transactions is None else transactions,
        case.tax_filings,
        as_of=AS_OF,
        requested_amount_krw=AMOUNT,
        requested_tenor_months=TENOR,
    )


@pytest.mark.parametrize(
    ("applicant_id", "months", "inflow", "dscr", "buffer", "vol", "match"),
    [
        ("demo-cafe-001", 12, 32_265_353, 3.802, 124.0, 0.193, 0.983),
        ("demo-mismatch-002", 12, 32_360_867, 3.781, 122.7, 0.206, 0.6),
        ("demo-stress-003", 12, 30_451_861, 3.007, 128.1, 0.265, 0.979),
        ("demo-thin-004", 4, 37_407_437, 4.735, 55.9, 0.184, 0.0),
        ("demo-injection-005", 12, 32_295_059, 3.766, 122.2, 0.193, 0.989),
    ],
)
def test_features_match_calibration(cases, applicant_id, months, inflow, dscr, buffer, vol, match):
    f = _underwrite(cases, applicant_id).features
    assert f.application_id == f"app-{applicant_id}"
    assert (f.months_of_history, f.avg_monthly_inflow_krw) == (months, inflow)
    assert (f.dscr, f.cash_buffer_days, f.revenue_volatility, f.tax_bank_match_ratio) == (dscr, buffer, vol, match)
    assert (f.requested_amount_krw, f.requested_tenor_months) == (AMOUNT, TENOR)
    assert f.industry == cases[applicant_id].profile.industry


@pytest.mark.parametrize(
    ("applicant_id", "decision", "codes"),
    [
        ("demo-cafe-001", "eligible", []),
        ("demo-mismatch-002", "refer", ["R101"]),
        ("demo-stress-003", "eligible", []),
        ("demo-thin-004", "ineligible", ["R001", "R101", "R104"]),
        ("demo-injection-005", "eligible", []),
    ],
)
def test_policy_outcomes_for_demo_applicants(cases, applicant_id, decision, codes):
    d = evaluate(_underwrite(cases, applicant_id).features, load_rules(DEFAULT_RULES_PATH))
    assert d.decision.value == decision
    assert [r.code for r in d.reason_codes] == codes
    if decision != "ineligible":
        assert (d.max_amount_krw, d.rate_band) == (AMOUNT, "A")


def test_signals_explain_dscr(cases):
    s = _underwrite(cases, "demo-cafe-001").signals
    assert s["avg_monthly_noi_krw"] == 9_086_020
    assert s["avg_monthly_debt_service_krw"] == 650_000
    assert s["new_installment_krw"] == 1_739_768
    assert s["avg_monthly_owner_draw_krw"] == 1_500_000
    assert s["classification_rules"] == "classify-v1"


def test_high_cost_lender_flag_only_for_stressed_case(cases):
    assert FLAG_HIGH_COST_LENDER in _underwrite(cases, "demo-stress-003").flags
    assert _underwrite(cases, "demo-cafe-001").flags == []


def test_empty_history_yields_zero_features_and_knockout(cases):
    uw = _underwrite(cases, "demo-cafe-001", transactions=())
    f = uw.features
    assert (f.months_of_history, f.avg_monthly_inflow_krw) == (0, 0)
    assert (f.dscr, f.cash_buffer_days, f.revenue_volatility, f.tax_bank_match_ratio) == (0.0, 0.0, 0.0, 0.0)
    d = evaluate(f, load_rules(DEFAULT_RULES_PATH))
    assert d.decision.value == "ineligible" and "R001" in [r.code for r in d.reason_codes]


def test_monthly_installment_annuity():
    assert int(monthly_installment(AMOUNT, TENOR)) == 1_739_768
