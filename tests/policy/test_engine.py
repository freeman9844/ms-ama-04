from policy.engine import evaluate
from shared.contracts import Decision


def _codes(decision):
    return [r.code for r in decision.reason_codes]


def test_healthy_is_eligible(make_features, rules):
    d = evaluate(make_features(), rules)
    assert d.decision is Decision.ELIGIBLE
    assert d.rate_band == "B"
    assert d.max_amount_krw == 3_000_000
    assert d.reason_codes == ()
    assert d.rules_version == "2026.11-v2"


def test_boundary_values_pass_knockouts(make_features, rules):
    d = evaluate(make_features(months_of_history=6, dscr=1.0), rules)
    assert d.decision is Decision.ELIGIBLE
    assert d.rate_band == "D"


def test_just_below_boundaries_knock_out(make_features, rules):
    d = evaluate(make_features(months_of_history=5), rules)
    assert d.decision is Decision.INELIGIBLE
    assert _codes(d) == ["R001"]
    d = evaluate(make_features(dscr=0.99), rules)
    assert d.decision is Decision.INELIGIBLE
    assert _codes(d) == ["R002", "R901"]
    assert d.max_amount_krw == 0 and d.rate_band is None


def test_refer_boundaries_do_not_trigger(make_features, rules):
    d = evaluate(
        make_features(tax_bank_match_ratio=0.7, revenue_volatility=0.5, cash_buffer_days=10, business_age_months=12),
        rules,
    )
    assert d.decision is Decision.ELIGIBLE


def test_tax_mismatch_refers(make_features, rules):
    d = evaluate(make_features(tax_bank_match_ratio=0.6), rules)
    assert d.decision is Decision.REFER
    assert _codes(d) == ["R101"]
    assert d.max_amount_krw > 0 and d.rate_band == "B"


def test_ineligible_includes_refer_reasons(make_features, rules):
    d = evaluate(make_features(months_of_history=3, tax_bank_match_ratio=0.5), rules)
    assert d.decision is Decision.INELIGIBLE
    assert _codes(d) == ["R001", "R101"]


def test_limit_capped_and_rounded(make_features, rules):
    d = evaluate(make_features(avg_monthly_inflow_krw=2_345_678, requested_amount_krw=4_000_000), rules)
    assert d.max_amount_krw == 3_500_000
    d = evaluate(make_features(avg_monthly_inflow_krw=10_000_000, requested_amount_krw=8_000_000), rules)
    assert d.max_amount_krw == 8_000_000
    d = evaluate(make_features(avg_monthly_inflow_krw=10_000_000, requested_amount_krw=12_000_000), rules)
    assert d.max_amount_krw == 10_000_000


def test_limit_below_product_min_is_r900(make_features, rules):
    d = evaluate(make_features(avg_monthly_inflow_krw=300_000), rules)
    assert d.decision is Decision.INELIGIBLE
    assert _codes(d) == ["R900"]


def test_deterministic(make_features, rules):
    f = make_features(tax_bank_match_ratio=0.6, cash_buffer_days=5)
    assert evaluate(f, rules) == evaluate(f, rules)


def test_reason_carries_observed_and_threshold(make_features, rules):
    d = evaluate(make_features(dscr=0.8), rules)
    r002 = d.reason_codes[0]
    assert (r002.code, r002.field, r002.observed, r002.threshold) == ("R002", "dscr", 0.8, 1.0)
