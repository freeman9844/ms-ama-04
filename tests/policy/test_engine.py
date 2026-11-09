from pathlib import Path

import pytest

import policy.rules
from policy.engine import evaluate, explain
from policy.rules import load_rules
from shared.contracts import Decision

RULESETS = Path(policy.rules.__file__).parent / "rulesets"


def _codes(decision):
    return [r.code for r in decision.reason_codes]


@pytest.mark.parametrize("name", ["v1.yaml", "v2.yaml", "v3.yaml"])
@pytest.mark.parametrize("kw", [{}, {"business_age_months": 11}, {"months_of_history": 5}, {"dscr": 0.99}])
def test_explain_matches_evaluate_across_versions(make_features, name, kw):
    rules = load_rules(RULESETS / name)
    f = make_features(**kw)
    e = explain(f, rules)
    assert e.decision == evaluate(f, rules)
    assert e.rules_version == rules.version
    hits = [c.code for c in e.checks if c.result == "hit"]
    assert sorted(hits) == sorted(r.code for r in e.decision.reason_codes)


def test_explain_checks_cover_every_rule_plus_engine_codes(make_features, rules):
    e = explain(make_features(), rules)
    assert [c.code for c in e.checks] == [c.code for c in (*rules.knockouts, *rules.refers)] + ["R900", "R901"]
    assert {c.kind for c in e.checks if c.code in ("R900", "R901")} == {"engine"}
    assert {c.kind for c in e.checks if c.code in ("R001", "R002")} == {"knockout"}
    assert next(c for c in e.checks if c.code == "R104").kind == "refer"


def test_explain_missing_optional_metric_is_not_applied(make_features, rules):
    e = explain(make_features(revenue_trend_ratio=None), rules)
    r106 = next(c for c in e.checks if c.field == "revenue_trend_ratio")
    assert r106.result == "not_applied" and r106.observed is None
    r104 = next(c for c in e.checks if c.code == "R104")
    assert r104.result == "pass" and r104.observed == 38.0 and r104.threshold == 12


def test_explain_limit_and_band_trace(make_features, rules):
    e = explain(make_features(requested_amount_krw=9_000_000, avg_monthly_inflow_krw=3_456_789), rules)
    lim = e.limit
    assert lim.inflow_cap_krw == int(3_456_789 * rules.product.inflow_multiplier)
    assert lim.before_rounding_krw == min(9_000_000, lim.inflow_cap_krw, rules.product.max_amount_krw)
    assert lim.unit_krw == 100_000
    assert lim.limit_krw == lim.before_rounding_krw - lim.before_rounding_krw % 100_000 == e.decision.max_amount_krw
    assert e.band.bands == tuple((b.band, b.min_dscr) for b in rules.rate_bands) and e.band.band == "B"


def test_explain_r900_r901_hit(make_features, rules):
    e = explain(make_features(avg_monthly_inflow_krw=0, dscr=0.5), rules)
    by = {c.code: c for c in e.checks}
    assert by["R900"].result == "hit" and by["R900"].threshold == rules.product.min_amount_krw
    assert by["R901"].result == "hit" and by["R901"].threshold == rules.rate_bands[-1].min_dscr
    assert e.band.band is None and e.decision.max_amount_krw == 0


def test_healthy_is_eligible(make_features, rules):
    d = evaluate(make_features(), rules)
    assert d.decision is Decision.ELIGIBLE
    assert d.rate_band == "B"
    assert d.max_amount_krw == 3_000_000
    assert d.reason_codes == ()
    assert d.rules_version == "2026.11-v3"


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


def test_young_business_refers(make_features, rules):
    d = evaluate(make_features(business_age_months=11), rules)
    assert d.decision is Decision.REFER
    assert _codes(d) == ["R104"]
    assert d.max_amount_krw > 0 and d.rate_band == "B"


def test_ineligible_includes_refer_reasons(make_features, rules):
    d = evaluate(make_features(months_of_history=3, business_age_months=3), rules)
    assert d.decision is Decision.INELIGIBLE
    assert _codes(d) == ["R001", "R104"]


def test_v3_skips_rules_with_missing_values(make_features, rules):
    d = evaluate(make_features(), rules)  # 새 선택 필드 모두 None
    assert rules.version == "2026.11-v3" and d.decision is Decision.ELIGIBLE and d.reason_codes == ()


@pytest.mark.parametrize(("field", "value", "code"), [
    ("high_cost_lender_count", 1, "R105"),
    ("revenue_trend_ratio", 0.79, "R106"),
    ("pg_top_platform_share", 0.81, "R107"),
    ("pg_reconciliation_ratio", 0.89, "R108"),
])
def test_v3_refer_rules(make_features, rules, field, value, code):
    d = evaluate(make_features(**{field: value}), rules)
    assert d.decision is Decision.REFER and _codes(d) == [code]


def test_v3_has_no_tax_mismatch_rule(make_features, rules):
    assert "R101" not in _codes(evaluate(make_features(tax_bank_match_ratio=0.0), rules))


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
