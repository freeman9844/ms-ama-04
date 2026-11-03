import json
from copy import deepcopy
from dataclasses import replace
from datetime import datetime

import pytest

from agents.notices import Draft
from agents.runtime import AgentRun
from journey.activities import Deps
from journey.grievance import (
    GRIEVANCES_CONTAINER,
    handle_grievance,
    handle_grievance_decision,
)
from journey.models import KST

NOW = datetime(2026, 11, 3, 10, 0, tzinfo=KST)
GID = "grv-0a1b2c3d"
TEXT = "매출이 늘었는데 거절되었습니다. 제 연락처는 010-1234-5678입니다."
APPLICATION = {
    "id": "app-1",
    "application_id": "app-1",
    "applicant_id": "demo-healthy-001",
    "status": "declined",
    "policy": {
        "application_id": "app-1",
        "decision": "refer",
        "max_amount_krw": 20_000_000,
        "rate_band": "B",
        "reason_codes": [{"code": "R101", "message": "DSCR 기준 미달", "field": "dscr", "observed": 1.1, "threshold": 1.25}],
        "rules_version": "2026.10",
    },
    "assessment": {
        "recommendation": "decline",
        "summary": "현금흐름이 상환액 대비 부족하다.",
        "claims": [{"text": "DSCR 1.1", "metric_ids": ["M:dscr"]}],
        "counterfactuals": [
            {"metric_id": "M:dscr", "current": 1.1, "target": 1.25, "explanation": "DSCR이 1.25 이상이면 결과가 달라질 수 있다."}
        ],
    },
    "officer_decision": {"decision": "decline", "officer_id": "officer-1", "reason": "DSCR 미달", "at": "2026-11-02T10:00:00+09:00"},
    "flags": [],
}


class MemoryStore:
    def __init__(self):
        self.docs, self.etags = {}, {}

    def upsert(self, container, document):
        self.docs[(container, document["id"])] = deepcopy(document)
        self.etags[(container, document["id"])] = self.etags.get((container, document["id"]), 0) + 1

    def read(self, container, item_id, partition_key):
        doc = self.docs.get((container, item_id))
        if doc is None or doc.get("application_id", partition_key) != partition_key:
            return None
        return {**deepcopy(doc), "_etag": str(self.etags[(container, item_id)])}

    def replace(self, container, document, etag):
        key = (container, document["id"])
        if key not in self.docs or str(self.etags[key]) != etag:
            return False
        self.upsert(container, {k: v for k, v in document.items() if k != "_etag"})
        return True


class Outbox:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def publish(self, topic, message, message_id, session_id=None):
        if self.fail:
            raise RuntimeError("sb down")
        self.sent.append((topic, message, message_id))


class ReplyRunner:
    def __init__(self, draft):
        self.draft, self.inputs, self.deleted = draft, [], []

    def run(self, spec, input_text, handlers):
        self.inputs.append(input_text)
        return AgentRun(spec.name, "1", "conv_g", self.draft, [], 10, 0)

    def delete_conversation(self, conversation_id):
        self.deleted.append(conversation_id)
        return "deleted"

    def delete_container(self, container_id):
        return True


@pytest.fixture
def deps():
    store = MemoryStore()
    store.upsert("applications", APPLICATION)
    return Deps(
        store=store, raw=None, mock=None, policy=None, events=Outbox(), pseudonym_key=b"k" * 32, clock=lambda: NOW
    )


def submit(deps, **fields):
    body = {"grievance_id": GID, "application_id": "app-1", "applicant_id": "demo-healthy-001", "text": TEXT, **fields}
    return handle_grievance(deps, json.dumps(body, ensure_ascii=False))


def decide(deps, **fields):
    body = {"grievance_id": GID, "application_id": "app-1", "outcome": "uphold", "officer_id": "officer-2",
            "reason": "DSCR 기준을 다시 확인했으나 미달입니다.", **fields}
    return handle_grievance_decision(deps, json.dumps(body, ensure_ascii=False))


def grievance(deps):
    return deps.store.read(GRIEVANCES_CONTAINER, GID, "app-1")


def test_submission_records_snapshot_and_publishes_without_text(deps):
    assert submit(deps) == "created"
    doc = grievance(deps)
    assert (doc["id"], doc["application_id"], doc["applicant_id"]) == (GID, "app-1", "demo-healthy-001")
    assert (doc["status"], doc["text"], doc["submitted_at"]) == ("pending_officer", TEXT, NOW.isoformat())
    record = doc["record"]
    assert record["status"] == "declined" and record["officer_decision"] == APPLICATION["officer_decision"]
    assert record["policy"]["reason_codes"] == APPLICATION["policy"]["reason_codes"]
    assert record["policy"]["max_amount_krw"] == 20_000_000
    assert record["assessment"] == {
        "summary": APPLICATION["assessment"]["summary"],
        "counterfactuals": APPLICATION["assessment"]["counterfactuals"],
    }
    assert doc["decision"] is None and doc["reply"] is None and doc["decision_attempts"] == []
    topic, message, message_id = deps.events.sent[0]
    assert (topic, message_id) == ("grievance", f"grievance.submitted:{GID}")
    assert message == {"type": "grievance.submitted", "grievance_id": GID, "application_id": "app-1"}
    assert doc["event_published"] is True
    assert deps.store.read("applications", "app-1", "app-1") == {**APPLICATION, "_etag": "1"}


def test_duplicate_submission_keeps_first(deps):
    submit(deps)
    assert submit(deps, text="다른 내용") == "duplicate"
    assert grievance(deps)["text"] == TEXT and len(deps.events.sent) == 1


@pytest.mark.parametrize(
    ("fields", "outcome"),
    [
        ({"application_id": "app-9"}, "unknown"),
        ({"applicant_id": "someone-else"}, "unknown"),
        ({"text": "   "}, "invalid"),
        ({"grievance_id": "bad id"}, "invalid"),
    ],
)
def test_submission_rejects_unknown_or_invalid(deps, fields, outcome):
    assert submit(deps, **fields) == outcome
    assert grievance(deps) is None


def test_submission_requires_officer_decision(deps):
    deps.store.upsert("applications", {**APPLICATION, "status": "awaiting_officer", "officer_decision": None})
    assert submit(deps) == "not_decided"


@pytest.mark.parametrize(("events", "error"), [(None, "servicebus_disabled"), (Outbox(fail=True), "RuntimeError: sb down")])
def test_event_failure_does_not_block_submission(deps, events, error):
    assert submit(replace(deps, events=events)) == "created"
    doc = grievance(deps)
    assert doc["event_published"] is False and doc["event_error"] == error


def test_uphold_resolves_with_template_reply_and_keeps_application(deps):
    submit(deps)
    assert decide(deps) == "resolved"
    doc = grievance(deps)
    assert (doc["status"], doc["resolved_at"]) == ("resolved_upheld", NOW.isoformat())
    assert doc["decision"] == {
        "outcome": "uphold", "officer_id": "officer-2", "reason": "DSCR 기준을 다시 확인했으나 미달입니다.",
        "new_decision": None, "amount_krw": None, "conditions": [], "at": NOW.isoformat(),
    }
    reply = doc["reply"]
    assert reply["source"] == "template" and "유지" in reply["body"] and "DSCR 기준 미달" in reply["body"]
    assert deps.store.read("applications", "app-1", "app-1")["status"] == "declined"


def test_rereview_within_policy_resolves(deps):
    submit(deps)
    result = decide(deps, outcome="rereview", new_decision="approve", amount_krw=15_000_000, reason="추가 매출 자료 확인")
    assert result == "resolved"
    doc = grievance(deps)
    assert doc["status"] == "resolved_rereviewed" and doc["decision"]["new_decision"] == "approve"
    assert "승인" in doc["reply"]["body"] and "15,000,000원" in doc["reply"]["body"]


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        ({"new_decision": "approve", "amount_krw": 30_000_000}, "amount_exceeds_policy"),
        ({"new_decision": "conditional", "amount_krw": 10_000_000}, "conditions_required"),
    ],
)
def test_rereview_outside_policy_is_rejected_and_stays_pending(deps, fields, error):
    submit(deps)
    assert decide(deps, outcome="rereview", **fields) == "rejected"
    doc = grievance(deps)
    assert doc["status"] == "pending_officer" and doc["decision"] is None
    assert [a["error"] for a in doc["decision_attempts"]] == [error]
    assert doc["decision_attempts"][0]["at"] == NOW.isoformat()


def test_decision_for_unknown_resolved_or_invalid_is_dropped(deps):
    assert decide(deps) == "unknown"
    submit(deps)
    assert decide(deps, outcome="rereview") == "invalid"  # 재심사에는 새 결정이 필요하다
    assert decide(deps) == "resolved"
    assert decide(deps, reason="두 번째") == "ignored"
    assert grievance(deps)["decision"]["reason"] != "두 번째"


def test_agent_reply_is_grounded_and_never_sees_borrower_text(deps):
    draft = Draft(title="이의제기 처리 결과", body="검토 결과 원결정(거절)을 유지합니다. 주요 사유는 DSCR 기준 미달입니다.")
    runner = ReplyRunner(draft)
    submit(deps)
    assert decide(replace(deps, agents=runner)) == "resolved"
    reply = grievance(deps)["reply"]
    assert reply["source"] == "agent" and reply["body"] == draft.body
    assert reply["record"]["conversations"] == {"conv_g": "deleted"}
    assert "010-1234-5678" not in runner.inputs[0] and "매출이 늘었는데" not in runner.inputs[0]
