"""정책 v3 임계값이 모의 데이터 분포에서 정상 셀러와 위험 원형을 가르는지 확인한다(스펙 §5)."""

from datetime import date

import pytest

from agents.features import compute_underwriting
from evals.datasets import AS_OF as EVAL_AS_OF
from evals.datasets import underwriting_cases
from mockapis.demo import build_demo_cases
from mockapis.synth import Archetype

AS_OFS = (date(2026, 10, 1), date(2026, 11, 2), date(2026, 11, 9))
HEALTHY = {Archetype.HEALTHY_SEASONAL, Archetype.TAX_MISMATCH}


def _cases():
    for as_of in AS_OFS:
        for applicant_id, case in build_demo_cases(as_of).items():
            yield applicant_id, case, as_of
    for c in underwriting_cases():
        yield c.case_id, c.case, EVAL_AS_OF


@pytest.fixture(scope="module")
def dist():
    out: dict[Archetype, list] = {}
    for applicant_id, case, as_of in _cases():
        uw = compute_underwriting(f"app-{applicant_id}", case.profile, case.transactions, case.tax_filings,
                                  as_of=as_of, requested_amount_krw=3_300_000, requested_tenor_months=6,
                                  pg_settlements=case.pg_settlements)
        out.setdefault(case.archetype, []).append(uw.features)
    for a, fs in out.items():
        print(a.value, "recon", sorted(f.pg_reconciliation_ratio for f in fs if f.pg_reconciliation_ratio is not None),
              "trend", sorted(f.revenue_trend_ratio for f in fs if f.revenue_trend_ratio is not None))
    return out


def test_healthy_reconciliation_above_threshold(dist):
    vals = [f.pg_reconciliation_ratio for a in HEALTHY for f in dist.get(a, []) if f.pg_reconciliation_ratio is not None]
    assert vals and min(vals) > 0.90


def test_assigned_reconciliation_below_threshold(dist):
    vals = [f.pg_reconciliation_ratio for f in dist[Archetype.ASSIGNED]]
    assert vals and max(vals) < 0.90


def test_stressed_revenue_trend_below_threshold(dist):
    vals = [f.revenue_trend_ratio for f in dist[Archetype.STRESSED] if f.revenue_trend_ratio is not None]
    assert vals and max(vals) < 0.80
