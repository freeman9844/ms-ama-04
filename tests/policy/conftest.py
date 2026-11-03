import pytest

from policy.rules import load_rules
from shared.contracts import CashFlowFeatures


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
            requested_tenor_months=24,
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
