import pytest
from pydantic import ValidationError

from journey.models import OfficerDecision, StartRequest
from journey.validation import validate_officer_decision

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
        (d(decision="approve", amount_krw=20_000_000), INELIGIBLE, "decline_only"),
        (d(decision="approve", amount_krw=20_000_000), None, "decline_only"),
        (d(decision="approve"), ELIGIBLE, "amount_required"),
        (d(decision="approve", amount_krw=20_000_001), ELIGIBLE, "amount_exceeds_policy"),
        (d(decision="conditional", amount_krw=10_000_000), ELIGIBLE, "conditions_required"),
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
        (d(decision="conditional", amount_krw=15_000_000, conditions=["월별 매출 보고"]), ELIGIBLE),
        (d(decision="approve", amount_krw=10_000_000, reason="세금 신고 소명 확인"), REFER),
        (d(decision="decline", reason="정책 부적격"), INELIGIBLE),
        (d(decision="decline", reason="데이터 불충분"), None),
    ],
)
def test_accepts_valid_decisions(raw, policy):
    decision, error = validate_officer_decision(raw, policy)
    assert error is None and decision == OfficerDecision.model_validate(raw)


@pytest.mark.parametrize(
    "payload",
    [
        {"application_id": "A1", "applicant_id": "x", "requested_amount_krw": 1, "requested_tenor_months": 12},
        {"application_id": "app-1", "applicant_id": "x", "requested_amount_krw": 0, "requested_tenor_months": 12},
        {"application_id": "app-1", "applicant_id": "x", "requested_amount_krw": 1, "requested_tenor_months": 61},
        {"application_id": "app-1", "applicant_id": "x", "requested_amount_krw": 1, "requested_tenor_months": 12, "x": 1},
        {"application_id": "app-1", "applicant_id": "x", "requested_amount_krw": 1, "requested_tenor_months": 12, "sla_hours": 0},
    ],
)
def test_start_request_rejects_bad_input(payload):
    with pytest.raises(ValidationError):
        StartRequest.model_validate(payload)
