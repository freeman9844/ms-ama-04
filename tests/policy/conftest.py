import pytest

from pathlib import Path

import policy.rules
from policy.rules import load_rules
from shared.contracts import USD_V1, CashFlowFeatures, UsdCashFlowFeatures


def usd_features(**overrides) -> UsdCashFlowFeatures:
    """usd-v1과 usd-v2 모두에서 적격인 기본 신청. 나머지 pg_*·mkt_*는 None이다."""
    base = dict(
        application_id="app-usd-1",
        months_of_history=12,
        dscr=2.5,
        cash_buffer_days=25,
        revenue_volatility=0.1,
        tax_bank_match_ratio=1.0,
        revenue_trend_ratio=1.0,
        business_age_months=38,
        high_cost_lender_count=0,
        avg_monthly_inflow_cents=2_000_000,
        requested_amount_cents=500_000,
        requested_tenor_months=6,
        industry="Food",
        region="Seoul",
    )
    return UsdCashFlowFeatures(**USD_V1.model_dump(), **{**base, **overrides})


def usd_features_payload(**overrides) -> dict:
    return usd_features(**overrides).model_dump(mode="json")


@pytest.fixture
def make_features():
    def _make(**kw):
        base = dict(
            application_id="app-1",
            months_of_history=12,
            avg_monthly_inflow_krw=3_000_000,
            dscr=1.8,
            cash_buffer_days=30.0,
            revenue_volatility=0.2,
            tax_bank_match_ratio=0.95,
            requested_amount_krw=3_000_000,
            requested_tenor_months=12,
            industry="패션의류",
            region="서울 성동구",
            business_age_months=38,
        )
        base.update(kw)
        return CashFlowFeatures(**base)

    return _make


@pytest.fixture
def rules():
    return load_rules()


@pytest.fixture
def rules_v3():
    return load_rules(Path(policy.rules.__file__).parent / "rulesets" / "v3.yaml")
