from datetime import date

import pytest

from agents.classify import Category, classify
from agents.features import FLAG_HIGH_COST_LENDER, compute_underwriting, monthly_installment
from mockapis.demo import build_demo_cases
from policy.engine import evaluate
from policy.rules import DEFAULT_RULES_PATH, load_rules

AS_OF = date(2026, 11, 2)
AMOUNT = 3_300_000
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
        ("seller-dailylook-01", 12, 27_925_932, 4.106, 22.7, 0.121, 0.972),
        ("seller-mangwon-02", 12, 24_736_976, 4.22, 31.9, 0.066, 0.6),
        ("seller-glowlab-03", 12, 25_062_551, 2.983, 37.6, 0.181, 0.972),
        ("seller-knitroom-04", 4, 10_070_066, 3.267, 27.4, 0.068, 0.0),
        ("seller-candle-05", 12, 16_400_357, 4.704, 47.9, 0.116, 0.976),
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
        ("seller-dailylook-01", "eligible", []),
        ("seller-mangwon-02", "refer", ["R101"]),
        ("seller-glowlab-03", "eligible", []),
        ("seller-knitroom-04", "ineligible", ["R001", "R101", "R104"]),
        ("seller-candle-05", "eligible", []),
    ],
)
def test_policy_outcomes_for_demo_applicants(cases, applicant_id, decision, codes):
    d = evaluate(_underwrite(cases, applicant_id).features, load_rules(DEFAULT_RULES_PATH))
    assert d.decision.value == decision
    assert [r.code for r in d.reason_codes] == codes
    if decision != "ineligible":
        assert (d.max_amount_krw, d.rate_band) == (AMOUNT, "A")


def test_signals_explain_dscr(cases):
    s = _underwrite(cases, "seller-dailylook-01").signals
    assert s["avg_monthly_noi_krw"] == 2_674_318
    assert s["avg_monthly_debt_service_krw"] == 364_204
    assert s["new_installment_krw"] == 287_061
    assert s["avg_monthly_owner_draw_krw"] == 1_900_000
    assert s["classification_rules"] == "classify-v1"


def test_high_cost_lender_flag_only_for_stressed_case(cases):
    assert FLAG_HIGH_COST_LENDER in _underwrite(cases, "seller-glowlab-03").flags
    assert _underwrite(cases, "seller-dailylook-01").flags == []


def test_empty_history_yields_zero_features_and_knockout(cases):
    uw = _underwrite(cases, "seller-dailylook-01", transactions=())
    f = uw.features
    assert (f.months_of_history, f.avg_monthly_inflow_krw) == (0, 0)
    assert (f.dscr, f.cash_buffer_days, f.revenue_volatility, f.tax_bank_match_ratio) == (0.0, 0.0, 0.0, 0.0)
    d = evaluate(f, load_rules(DEFAULT_RULES_PATH))
    assert d.decision.value == "ineligible" and "R001" in [r.code for r in d.reason_codes]


def test_monthly_installment_annuity():
    assert int(monthly_installment(AMOUNT, TENOR)) == 287_061


def _unclassified_out(cases, applicant_id):
    return [t.txn_id for t in cases[applicant_id].transactions if classify(t) is Category.UNCLASSIFIED_OUT]


def _with_overrides(cases, applicant_id, overrides):
    case = cases[applicant_id]
    return compute_underwriting(
        f"app-{applicant_id}",
        case.profile,
        case.transactions,
        case.tax_filings,
        as_of=AS_OF,
        requested_amount_krw=AMOUNT,
        requested_tenor_months=TENOR,
        overrides=overrides,
    )


def test_no_overrides_keeps_calibration_and_reports_zero(cases):
    uw = _with_overrides(cases, "seller-dailylook-01", {})
    assert uw.features == _underwrite(cases, "seller-dailylook-01").features
    assert uw.signals["semantic_overrides"] == 0
    assert uw.signals["dscr_rules_only"] == uw.features.dscr


def test_monthly_series_matches_features(cases):
    uw = _underwrite(cases, "seller-dailylook-01")
    assert len(uw.monthly) == 12
    assert [r["month"] for r in uw.monthly] == sorted(r["month"] for r in uw.monthly)
    assert uw.monthly[0]["month"] == "2025-11" and uw.monthly[-1]["month"] == "2026-10"
    assert int(sum(r["revenue"] for r in uw.monthly) / 12) == uw.features.avg_monthly_inflow_krw
    assert int(sum(r["debt_service"] for r in uw.monthly) / 12) == uw.signals["avg_monthly_debt_service_krw"]
    assert all(r["operating_out"] > 0 and r["owner_draw"] >= 0 for r in uw.monthly)


def test_debt_overrides_never_improve_dscr_or_buffer(cases):
    base = _underwrite(cases, "seller-dailylook-01")
    ids = _unclassified_out(cases, "seller-dailylook-01")
    uw = _with_overrides(cases, "seller-dailylook-01", dict.fromkeys(ids, Category.LOAN_REPAYMENT))
    assert uw.signals["semantic_overrides"] == len(ids) > 0
    assert uw.signals["avg_monthly_debt_service_krw"] > base.signals["avg_monthly_debt_service_krw"]
    assert uw.features.dscr <= base.features.dscr
    assert uw.features.cash_buffer_days <= base.features.cash_buffer_days
    assert uw.signals["dscr_rules_only"] == base.features.dscr
    assert uw.features.dscr == min(base.features.dscr, uw.signals["dscr_semantic"])
    assert uw.features.model_dump(exclude={"dscr", "cash_buffer_days"}) == base.features.model_dump(
        exclude={"dscr", "cash_buffer_days"}
    )


def test_high_cost_override_raises_flag(cases):
    ids = _unclassified_out(cases, "seller-dailylook-01")[:1]
    uw = _with_overrides(cases, "seller-dailylook-01", dict.fromkeys(ids, Category.HIGH_COST_LENDER))
    assert FLAG_HIGH_COST_LENDER in uw.flags
    assert uw.signals["high_cost_lender_count"] == 1


def test_overrides_only_apply_to_unclassified_transactions(cases):
    revenue_id = next(
        t.txn_id for t in cases["seller-dailylook-01"].transactions if classify(t) is Category.REVENUE
    )
    uw = _with_overrides(cases, "seller-dailylook-01", {revenue_id: Category.LOAN_REPAYMENT, "unknown": Category.LOAN_REPAYMENT})
    assert uw.signals["semantic_overrides"] == 0
    assert uw.features == _underwrite(cases, "seller-dailylook-01").features
