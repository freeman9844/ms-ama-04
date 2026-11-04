import pytest
from pydantic import ValidationError

from journey.models import OfficerDecision, StartRequest
from journey.validation import OVERRIDE_MAX_AMOUNT_KRW, validate_officer_decision
from policy.rules import DEFAULT_RULES_PATH, load_rules

ELIGIBLE = {"decision": "eligible", "max_amount_krw": 20_000_000}
REFER = {"decision": "refer", "max_amount_krw": 20_000_000}
INELIGIBLE = {"decision": "ineligible", "max_amount_krw": 0}


def d(**kw):
    return {"officer_id": "officer-1", **kw}


@pytest.mark.parametrize(
    ("raw", "policy", "error"),
    [
        ("approve", ELIGIBLE, "invalid_payload"),
        (d(decision="approve", amount_krw=1, extra=1), ELIGIBLE, "invalid_payload"),
        (d(decision="approve", amount_krw=5_000_000), INELIGIBLE, "override_required"),
        (d(decision="approve", amount_krw=5_000_000), None, "override_required"),
        (d(decision="approve", amount_krw=5_000_000, policy_override=True), INELIGIBLE, "reason_required"),
        (d(decision="approve", policy_override=True, reason="소명"), None, "amount_required"),
        (d(decision="approve", amount_krw=5_000_001, policy_override=True, reason="소명"), INELIGIBLE,
         "amount_exceeds_product"),
        (d(decision="conditional", amount_krw=3_000_000, policy_override=True, reason="소명",
           conditions=["월별 매출 보고"]), INELIGIBLE, "conditional_retired"),
        (d(decision="approve"), ELIGIBLE, "amount_required"),
        (d(decision="approve", amount_krw=20_000_001), ELIGIBLE, "amount_exceeds_policy"),
        (d(decision="conditional", amount_krw=10_000_000, conditions=["월별 매출 보고"]), ELIGIBLE,
         "conditional_retired"),
        (d(decision="approve", amount_krw=10_000_000), REFER, "reason_required"),
        (d(decision="decline"), INELIGIBLE, "reason_required"),
    ],
)
def test_rejects_invalid_decisions(raw, policy, error):
    assert validate_officer_decision(raw, policy) == (None, error)


@pytest.mark.parametrize(
    ("raw", "policy"),
    [
        (d(decision="approve", amount_krw=20_000_000), ELIGIBLE),
        (d(decision="approve", amount_krw=10_000_000, reason="세금 신고 소명 확인"), REFER),
        (d(decision="decline", reason="정책 부적격"), INELIGIBLE),
        (d(decision="decline", reason="데이터 불충분"), None),
    ],
)
def test_accepts_valid_decisions(raw, policy):
    decision, error = validate_officer_decision(raw, policy)
    assert error is None and decision == OfficerDecision.model_validate(raw)


@pytest.mark.parametrize("policy", [INELIGIBLE, None])
def test_officer_can_override_policy_decline_with_reason(policy):
    raw = d(decision="approve", amount_krw=5_000_000, policy_override=True, reason="주거래처 계약서로 매출 소명")
    decision, error = validate_officer_decision(raw, policy)
    assert error is None and decision.policy_override and decision.amount_krw == 5_000_000


def test_override_flag_is_cleared_when_policy_allows_approval():
    raw = d(decision="approve", amount_krw=20_000_000, policy_override=True)
    decision, error = validate_officer_decision(raw, ELIGIBLE)
    assert error is None and decision.policy_override is False


def test_override_cap_matches_product_max():
    assert OVERRIDE_MAX_AMOUNT_KRW == load_rules(DEFAULT_RULES_PATH).product.max_amount_krw


@pytest.mark.parametrize(
    "payload",
    [
        {"application_id": "A1", "applicant_id": "x", "requested_amount_krw": 1, "requested_tenor_months": 12},
        {"application_id": "app-1", "applicant_id": "x", "requested_amount_krw": 0, "requested_tenor_months": 12},
        {"application_id": "app-1", "applicant_id": "x", "requested_amount_krw": 1, "requested_tenor_months": 13},
        {"application_id": "app-1", "applicant_id": "x", "requested_amount_krw": 1, "requested_tenor_months": 12, "x": 1},
        {"application_id": "app-1", "applicant_id": "x", "requested_amount_krw": 1, "requested_tenor_months": 12, "sla_hours": 0},
    ],
)
def test_start_request_rejects_bad_input(payload):
    with pytest.raises(ValidationError):
        StartRequest.model_validate(payload)
