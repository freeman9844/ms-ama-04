from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from shared.contracts import (
    ApplicantProfile,
    CashFlowFeatures,
    ConsentGrant,
    ConsentScope,
    Decision,
    PgSettlement,
    PolicyDecision,
    ReasonCode,
    TaxFiling,
    Transaction,
)

KST = timezone(timedelta(hours=9))
T0 = datetime(2026, 10, 1, 9, 0, tzinfo=KST)


def _grant(**kw):
    base = dict(
        consent_id="c1",
        applicant_id="a1",
        scopes=frozenset({ConsentScope.ACCOUNT}),
        purpose="loan-application",
        granted_at=T0,
        expires_at=T0 + timedelta(days=30),
    )
    base.update(kw)
    return ConsentGrant(**base)


def _features(**kw):
    base = dict(
        application_id="app-1",
        months_of_history=12,
        avg_monthly_inflow_krw=20_000_000,
        dscr=1.8,
        cash_buffer_days=30.0,
        revenue_volatility=0.2,
        tax_bank_match_ratio=0.95,
        requested_amount_krw=20_000_000,
        requested_tenor_months=24,
        industry="카페",
        region="서울 성동구",
        business_age_months=38,
    )
    base.update(kw)
    return CashFlowFeatures(**base)


def test_contracts_are_frozen():
    g = _grant()
    with pytest.raises(ValidationError):
        g.purpose = "other"


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        _grant(unexpected="x")


def test_schema_version_must_be_1():
    with pytest.raises(ValidationError):
        _grant(schema_version="2")


def test_consent_requires_at_least_one_scope():
    with pytest.raises(ValidationError):
        _grant(scopes=frozenset())


def test_consent_requires_aware_datetimes():
    with pytest.raises(ValidationError):
        _grant(granted_at=datetime(2026, 10, 1, 9, 0))


def test_consent_expiry_must_follow_grant():
    with pytest.raises(ValidationError):
        _grant(expires_at=T0)


def test_business_reg_no_format():
    ok = ApplicantProfile(
        applicant_id="a1",
        business_name="성수커피랩",
        business_reg_no="123-45-67890",
        representative_name="김민수",
        industry="카페",
        region="서울 성동구",
        business_age_months=38,
    )
    assert ok.business_reg_no == "123-45-67890"
    with pytest.raises(ValidationError):
        ApplicantProfile(**{**ok.model_dump(), "business_reg_no": "1234567890"})


def test_transaction_amount_nonzero_and_type_code_literal():
    t = Transaction(
        txn_id="t1",
        booked_on=date(2026, 9, 1),
        amount_krw=-50_000,
        balance_after_krw=1_000_000,
        description="임대료",
        txn_type_code="AUTO_DEBIT",
    )
    assert t.amount_krw == -50_000
    with pytest.raises(ValidationError):
        Transaction(**{**t.model_dump(), "amount_krw": 0})
    with pytest.raises(ValidationError):
        Transaction(**{**t.model_dump(), "txn_type_code": "CASH"})


def test_tax_filing_period_order():
    with pytest.raises(ValidationError):
        TaxFiling(
            period_start=date(2026, 6, 30),
            period_end=date(2026, 1, 1),
            declared_revenue_krw=1,
        )


def test_pg_settlement_net_must_equal_gross_minus_fee():
    PgSettlement(settled_on=date(2026, 9, 7), platform="배민", gross_krw=1000, fee_krw=68, net_krw=932)
    with pytest.raises(ValidationError):
        PgSettlement(settled_on=date(2026, 9, 7), platform="배민", gross_krw=1000, fee_krw=68, net_krw=1000)


def test_features_reject_nan_inf_and_ranges():
    with pytest.raises(ValidationError):
        _features(dscr=float("nan"))
    with pytest.raises(ValidationError):
        _features(cash_buffer_days=float("inf"))
    with pytest.raises(ValidationError):
        _features(tax_bank_match_ratio=1.2)
    with pytest.raises(ValidationError):
        _features(requested_amount_krw=0)
    with pytest.raises(ValidationError):
        _features(requested_tenor_months=61)


def test_policy_decision_invariants():
    reason = ReasonCode(code="R002", message="DSCR 미달", field="dscr", observed=0.8, threshold=1.0)
    PolicyDecision(
        application_id="app-1",
        decision=Decision.INELIGIBLE,
        max_amount_krw=0,
        rate_band=None,
        reason_codes=(reason,),
        rules_version="v",
    )
    # INELIGIBLE인데 한도가 있으면 거부
    with pytest.raises(ValidationError):
        PolicyDecision(
            application_id="app-1",
            decision=Decision.INELIGIBLE,
            max_amount_krw=1_000_000,
            rate_band=None,
            reason_codes=(reason,),
            rules_version="v",
        )
    # ELIGIBLE인데 밴드가 없으면 거부
    with pytest.raises(ValidationError):
        PolicyDecision(
            application_id="app-1",
            decision=Decision.ELIGIBLE,
            max_amount_krw=10_000_000,
            rate_band=None,
            reason_codes=(),
            rules_version="v",
        )
    # REFER인데 사유가 없으면 거부
    with pytest.raises(ValidationError):
        PolicyDecision(
            application_id="app-1",
            decision=Decision.REFER,
            max_amount_krw=10_000_000,
            rate_band="B",
            reason_codes=(),
            rules_version="v",
        )


def test_reason_code_pattern():
    with pytest.raises(ValidationError):
        ReasonCode(code="X1", message="m")
