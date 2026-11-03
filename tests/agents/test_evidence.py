from datetime import date

import pytest

from agents.evidence import build_catalog
from agents.features import compute_underwriting
from mockapis.demo import build_demo_cases
from policy.engine import evaluate
from policy.rules import DEFAULT_RULES_PATH, load_rules
from shared.contracts import EVIDENCE_ID_PATTERN

AS_OF = date(2026, 11, 2)


@pytest.fixture(scope="module")
def catalogs():
    cases = build_demo_cases(AS_OF)
    out = {}
    for applicant_id in ("demo-healthy-001", "demo-thin-004"):
        case = cases[applicant_id]
        uw = compute_underwriting(
            f"app-{applicant_id}", case.profile, case.transactions, case.tax_filings,
            as_of=AS_OF, requested_amount_krw=20_000_000, requested_tenor_months=12,
        )
        policy = evaluate(uw.features, load_rules(DEFAULT_RULES_PATH))
        out[applicant_id] = build_catalog(
            uw.features.model_dump(mode="json"), uw.signals, uw.monthly, policy.model_dump(mode="json")
        )
    return out


def test_catalog_exposes_features_signals_and_policy(catalogs):
    c = catalogs["demo-healthy-001"]
    assert c["M:dscr"] == 3.802
    assert c["M:cash_buffer_days"] == 124.0
    assert c["M:requested_amount_krw"] == 20_000_000
    assert c["M:avg_monthly_noi_krw"] == 9_086_020
    assert c["M:new_installment_krw"] == 1_739_768
    assert c["P:max_amount_krw"] == 20_000_000
    assert "M:classification_rules" not in c


def test_catalog_monthly_series_and_seasonality_index(catalogs):
    c = catalogs["demo-healthy-001"]
    revenues = [v for k, v in c.items() if k.startswith("S:") and k.endswith(":revenue")]
    assert len(revenues) == 12
    assert c["S:2025-11:revenue"] > 0 and "S:2026-10:debt_service" in c
    indexes = [v for k, v in c.items() if k.endswith(":index")]
    assert abs(sum(indexes) / len(indexes) - 1) < 0.01


def test_catalog_includes_reason_code_observations(catalogs):
    c = catalogs["demo-thin-004"]
    assert c["P:R001:observed"] == 4
    assert c["P:R001:threshold"] > 4
    assert c["P:max_amount_krw"] == 0


def test_all_catalog_ids_are_valid_evidence_ids(catalogs):
    import re

    for c in catalogs.values():
        assert all(re.fullmatch(EVIDENCE_ID_PATTERN, k) for k in c)
        assert all(isinstance(v, int | float) and not isinstance(v, bool) for v in c.values())
