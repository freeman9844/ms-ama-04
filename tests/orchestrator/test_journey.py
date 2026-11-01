import json
from datetime import UTC, datetime

import pytest

from journey.orchestration import (
    FLAG_ASSESSMENT_UNAVAILABLE,
    FLAG_DATA_INCOMPLETE,
    FLAG_MONITORING_UNAVAILABLE,
    FLAG_POLICY_UNAVAILABLE,
)

START = {
    "application_id": "app-1",
    "applicant_id": "demo-cafe-001",
    "requested_amount_krw": 20_000_000,
    "requested_tenor_months": 12,
}
CONSENT = {
    "consent_id": "c-1",
    "token": "secret-token",
    "scopes": ["account", "tax", "pg"],
    "expires_at": "2027-01-30T15:00:00+00:00",
}
COMPLETE = {"status": "complete", "missing_sources": [], "flags": [], "conversation_id": "conv_1"}
UNDERWRITING = {
    "features": {"application_id": "app-1", "dscr": 3.8},
    "monthly": [{"month": "2026-10", "revenue": 1}],
    "signals": {},
    "flags": ["high_cost_lender_detected"],
}
ELIGIBLE = {"application_id": "app-1", "decision": "eligible", "max_amount_krw": 20_000_000, "rate_band": "A"}
INELIGIBLE = {"application_id": "app-1", "decision": "ineligible", "max_amount_krw": 0, "rate_band": None}
APPROVE = {"decision": "approve", "officer_id": "officer-1", "amount_krw": 20_000_000}
DECLINE = {"decision": "decline", "officer_id": "officer-1", "reason": "정책 부적격"}
ASSESSMENT = {"application_id": "app-1", "recommendation": "approve", "review": {"passed": True, "rounds": 1}}
ASSESSED = {"assessment": ASSESSMENT, "flags": [], "record": {"rounds": [{"round": 1}]}}


def acts(**overrides):
    base = {
        "request_consent": lambda _: CONSENT,
        "assemble_data": lambda _: COMPLETE,
        "underwrite": lambda _: UNDERWRITING,
        "evaluate_policy": lambda _: ELIGIBLE,
        "assess_credit": lambda _: ASSESSED,
        "register_monitoring": lambda p: {"loan_id": p["application_id"]},
    }
    return base | overrides


def boom(_):
    raise RuntimeError("down")


def statuses(ctx):
    return [s["status"] for s in ctx.saved[-1]["timeline"]]


def test_happy_path_reaches_approved(run_journey):
    result, ctx = run_journey(START, acts(), [("event", APPROVE)])
    assert result == {"application_id": "app-1", "status": "approved"}
    assert statuses(ctx) == [
        "received", "consent_granted", "data_assembled", "underwritten", "assessed", "awaiting_officer", "approved"
    ]
    doc = ctx.saved[-1]
    assert (doc["id"], doc["application_id"], doc["as_of"]) == ("app-1", "app-1", "2026-11-02")
    assert doc["officer_decision"]["amount_krw"] == 20_000_000
    assert doc["policy"] == ELIGIBLE and doc["flags"] == ["high_cost_lender_detected"]
    assert doc["consent"] == {k: CONSENT[k] for k in ("consent_id", "scopes", "expires_at")}
    assert all("secret-token" not in json.dumps(s, ensure_ascii=False) for s in ctx.saved)
    assert ctx.called("assemble_data")[0].input["token"] == "secret-token"
    uw = ctx.called("underwrite")[0].input
    assert (uw["as_of"], uw["consent_id"], uw["requested_amount_krw"]) == ("2026-11-02", "c-1", 20_000_000)
    assert ctx.called("evaluate_policy")[0].retry == "RETRY"
    assert ctx.timers[0].cancelled and ctx.custom_status == "approved"


def test_each_stage_is_saved_as_snapshot(run_journey):
    _, ctx = run_journey(START, acts(), [("event", APPROVE)])
    # 마지막 저장은 같은 상태에서 모니터링 등록 결과를 남긴다.
    assert [s["status"] for s in ctx.saved] == [*statuses(ctx), "approved"]
    assert ctx.saved[0]["consent"] is None  # 이후 변경이 이전 저장본에 섞이지 않는다
    assert all(c.input["container"] == "applications" for c in ctx.called("save_document"))


def test_event_payload_as_json_string_is_parsed(run_journey):
    result, _ = run_journey(START, acts(), [("event", json.dumps(APPROVE))])
    assert result["status"] == "approved"


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({**APPROVE, "decision": "conditional", "conditions": ["월별 매출 보고"]}, "conditionally_approved"),
        (DECLINE, "declined"),
    ],
)
def test_final_status_follows_officer_decision(run_journey, payload, status):
    result, _ = run_journey(START, acts(), [("event", payload)])
    assert result["status"] == status


def test_invalid_decision_is_recorded_and_wait_continues(run_journey):
    over = {**APPROVE, "amount_krw": 30_000_000}
    result, ctx = run_journey(START, acts(), [("event", over), ("event", APPROVE)])
    assert result["status"] == "approved"
    doc = ctx.saved[-1]
    assert [a["error"] for a in doc["decision_attempts"]] == ["amount_exceeds_policy"]
    assert doc["decision_attempts"][0]["payload"]["amount_krw"] == 30_000_000
    assert "awaiting_officer" in statuses(ctx) and statuses(ctx).count("approved") == 1
    assert len(ctx.timers) == 2 and ctx.timers[0].input == ctx.timers[1].input
    assert ctx.timers[0].cancelled


def test_ineligible_allows_decline_only(run_journey):
    result, ctx = run_journey(
        START, acts(evaluate_policy=lambda _: INELIGIBLE), [("event", APPROVE), ("event", DECLINE)]
    )
    assert result["status"] == "declined"
    assert [a["error"] for a in ctx.saved[-1]["decision_attempts"]] == ["decline_only"]


def test_policy_unavailable_fails_closed(run_journey):
    result, ctx = run_journey(START, acts(evaluate_policy=boom), [("event", APPROVE), ("event", DECLINE)])
    doc = ctx.saved[-1]
    assert result["status"] == "declined"
    assert doc["policy"] is None and FLAG_POLICY_UNAVAILABLE in doc["flags"]
    assert [a["error"] for a in doc["decision_attempts"]] == ["decline_only"]
    assert not ctx.called("assess_credit") and FLAG_ASSESSMENT_UNAVAILABLE in doc["flags"]


def test_assessment_runs_after_policy_without_retry(run_journey):
    _, ctx = run_journey(START, acts(), [("event", APPROVE)])
    (call,) = ctx.called("assess_credit")
    assert call.retry is None
    assert call.input == {
        "application_id": "app-1", "consent_id": "c-1", "underwriting": UNDERWRITING, "policy": ELIGIBLE
    }
    names = [c.name for c in ctx.calls if c.name != "save_document"]
    assert names.index("evaluate_policy") < names.index("assess_credit")
    doc = ctx.saved[-1]
    assert doc["assessment"] == ASSESSMENT and doc["assessment_run"] == ASSESSED["record"]


def test_assessment_flags_are_recorded(run_journey):
    not_passed = {**ASSESSED, "flags": ["review_not_passed"]}
    _, ctx = run_journey(START, acts(assess_credit=lambda _: not_passed), [("event", APPROVE)])
    assert "review_not_passed" in ctx.saved[-1]["flags"] and "assessed" in statuses(ctx)


@pytest.mark.parametrize(
    "assess",
    [boom, lambda _: {"assessment": None, "flags": [FLAG_ASSESSMENT_UNAVAILABLE], "record": {"rounds": []}}],
)
def test_assessment_failure_does_not_block_officer(run_journey, assess):
    result, ctx = run_journey(START, acts(assess_credit=assess), [("event", APPROVE)])
    doc = ctx.saved[-1]
    assert result["status"] == "approved"
    assert "assessed" not in statuses(ctx)
    assert doc["assessment"] is None
    assert doc["flags"].count(FLAG_ASSESSMENT_UNAVAILABLE) == 1


def test_incomplete_data_skips_underwriting_and_allows_decline_only(run_journey):
    incomplete = {**COMPLETE, "status": "incomplete", "missing_sources": ["transactions"], "flags": ["agent_fallback"]}
    result, ctx = run_journey(
        START, acts(assemble_data=lambda _: incomplete), [("event", APPROVE), ("event", DECLINE)]
    )
    assert result["status"] == "declined"
    assert statuses(ctx)[:4] == ["received", "consent_granted", "data_incomplete", "awaiting_officer"]
    assert not ctx.called("underwrite") and not ctx.called("evaluate_policy") and not ctx.called("assess_credit")
    assert ctx.saved[-1]["flags"] == ["agent_fallback", FLAG_DATA_INCOMPLETE]


@pytest.mark.parametrize(
    ("override", "stage"),
    [
        ({"request_consent": boom}, "request_consent"),
        ({"assemble_data": boom}, "assemble_data"),
        ({"underwrite": boom}, "underwrite"),
    ],
)
def test_stage_failure_marks_application_failed(run_journey, override, stage):
    result, ctx = run_journey(START, acts(**override))
    assert result["status"] == "failed"
    assert ctx.saved[-1]["error"] == {"stage": stage, "message": "down"}
    assert not ctx.events


def test_escalation_reuses_pending_event_task(run_journey):
    result, ctx = run_journey(START, acts(), [("timer",), ("event", APPROVE)])
    assert result["status"] == "approved"
    assert statuses(ctx)[-3:] == ["awaiting_officer", "escalated", "approved"]
    assert len(ctx.events) == 1  # 타이머가 이겨도 대기 중인 이벤트 태스크를 버리지 않는다
    assert ctx.timers[0].input == datetime(2026, 11, 2, 15, 0, tzinfo=UTC)
    assert ctx.timers[1].input == datetime(2026, 11, 3, 15, 0, tzinfo=UTC)
    sla = ctx.saved[-1]["sla"]
    assert sla["escalated"] is True and sla["deadline"] == "2026-11-03T15:00:00+00:00"


def test_second_timeout_closes_without_decision(run_journey):
    result, ctx = run_journey({**START, "sla_hours": 0.5}, acts(), [("timer",), ("timer",)])
    assert result["status"] == "closed_no_decision"
    assert statuses(ctx)[-2:] == ["escalated", "closed_no_decision"]
    assert ctx.timers[0].input == datetime(2026, 11, 1, 15, 30, tzinfo=UTC)


def test_input_as_json_string_is_accepted(run_journey):
    result, _ = run_journey(json.dumps(START), acts(), [("event", DECLINE)])
    assert result["status"] == "declined"


def test_approval_registers_monitoring(run_journey):
    _, ctx = run_journey(START, acts(), [("event", {**APPROVE, "amount_krw": 15_000_000})])
    call = ctx.called("register_monitoring")[0]
    assert call.retry == "RETRY"
    assert call.input == {
        "application_id": "app-1",
        "applicant_id": "demo-cafe-001",
        "consent_id": "c-1",
        "decision": "approve",
        "amount_krw": 15_000_000,
        "tenor_months": 12,
        "decided_at": ctx.saved[-1]["officer_decision"]["at"],
        "underwriting": {"features": UNDERWRITING["features"], "monthly": UNDERWRITING["monthly"]},
    }
    assert ctx.saved[-1]["monitoring"]["loan_id"] == "app-1"


def test_monitoring_failure_keeps_approval(run_journey):
    result, ctx = run_journey(START, acts(register_monitoring=boom), [("event", APPROVE)])
    assert result["status"] == "approved"
    assert FLAG_MONITORING_UNAVAILABLE in ctx.saved[-1]["flags"] and ctx.saved[-1]["monitoring"] is None


def test_decline_does_not_register_monitoring(run_journey):
    result, ctx = run_journey(START, acts(), [("event", DECLINE)])
    assert result["status"] == "declined" and ctx.called("register_monitoring") == []
