from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from journey.models import (
    KST,
    MAX_TIMER,
    ConsentCommand,
    GrievanceDecision,
    GrievanceSubmission,
    LifecycleStart,
    Status,
    lifecycle_instance,
    next_wake,
    renewal_at,
)

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=KST)


def test_lifecycle_start_requires_aware_expiry_and_known_initial_action():
    start = LifecycleStart(consent_id="c" * 32, expires_at=NOW.isoformat())
    assert start.initial is None and start.requested_by == "system"
    for action in ("revoke", "expire_now", "remind_now"):
        assert LifecycleStart(consent_id="abc", expires_at=NOW, initial=action).initial == action
    with pytest.raises(ValidationError):
        LifecycleStart(consent_id="abc", expires_at="2026-11-02T09:00:00")
    with pytest.raises(ValidationError):
        LifecycleStart(consent_id="abc", expires_at=NOW, initial="extend")
    with pytest.raises(ValidationError):
        LifecycleStart(consent_id="../x", expires_at=NOW)


def test_consent_command_actions():
    cmd = ConsentCommand(consent_id="abc", action="revoke", requested_by="borrower:demo-cafe-001", application_id="app-1")
    assert cmd.application_id == "app-1"
    for action in ("expire_now", "remind_now"):
        assert ConsentCommand(consent_id="abc", action=action, requested_by="officer").application_id is None
    with pytest.raises(ValidationError):
        ConsentCommand(consent_id="abc", action="extend", requested_by="officer")


def test_grievance_submission_limits_text():
    ok = GrievanceSubmission(grievance_id="grv-0a1b2c3d", application_id="app-1", applicant_id="a1", text="  다시 봐주세요 ")
    assert ok.text == "다시 봐주세요"
    for text in ("", "   ", "가" * 1001):
        with pytest.raises(ValidationError):
            GrievanceSubmission(grievance_id="grv-0a1b2c3d", application_id="app-1", applicant_id="a1", text=text)
    with pytest.raises(ValidationError):
        GrievanceSubmission(grievance_id="bad id", application_id="app-1", applicant_id="a1", text="x")


def _decision(**kw):
    base = dict(grievance_id="grv-0a1b2c3d", application_id="app-1", officer_id="o1", reason="기록 재검토")
    base.update(kw)
    return GrievanceDecision(**base)


def test_grievance_decision_rereview_needs_new_decision_and_uphold_forbids_it():
    assert _decision(outcome="uphold").new_decision is None
    re = _decision(outcome="rereview", new_decision="conditional", amount_krw=10_000_000, conditions=["월 1회 보고"])
    assert re.new_decision == "conditional"
    with pytest.raises(ValidationError):
        _decision(outcome="rereview")
    with pytest.raises(ValidationError):
        _decision(outcome="uphold", new_decision="approve")
    with pytest.raises(ValidationError):
        _decision(outcome="uphold", reason="")


def test_withdrawn_status_and_instance_id():
    assert Status.WITHDRAWN == "withdrawn"
    assert lifecycle_instance("abc") == "consent-abc"


def test_timer_helpers_chunk_long_waits():
    expires = NOW + timedelta(days=90)
    assert renewal_at(expires) == expires - timedelta(days=7)
    assert next_wake(NOW, expires) == NOW + MAX_TIMER
    assert next_wake(NOW, NOW + timedelta(hours=1)) == NOW + timedelta(hours=1)
    assert next_wake(NOW, NOW - timedelta(days=1)) == NOW
