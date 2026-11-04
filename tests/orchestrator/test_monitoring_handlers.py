import json
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from agents.collections import COLLECTIONS_AGENT
from agents.early_warning import EW_AGENT
from agents.monitoring import WEEKS
from agents.runtime import AgentRun
from journey.activities import Deps, assemble_data, register_monitoring, request_consent, underwrite
from journey.adapters import HttpMockApi
from journey import monitoring as monitoring_module
from journey.models import KST, MONITORING_PURPOSE, REPLAY_GUARD
from journey.monitoring import (
    ALERTS,
    LOANS,
    OutOfOrder,
    TOPIC_ALERT,
    TOPIC_FLOW,
    handle_alert_raised,
    handle_flow_update,
    handle_replay,
    handle_restructure_decision,
    process_week,
)
from mockapis.app import create_app as create_mock_app
from mockapis.demo import build_demo_cases, build_postloan_flows

AS_OF = date(2026, 11, 2)
SECRET = b"s" * 32
KEY = b"k" * 32
NOW = datetime(2026, 11, 2, 9, 0, tzinfo=KST)
LOAN = "app-1"


class MemoryStore:
    def __init__(self):
        self.docs, self.etags = {}, {}

    def upsert(self, container, document):
        key = (container, document["id"])
        self.docs[key] = {k: v for k, v in deepcopy(document).items() if k != "_etag"}
        self.etags[key] = self.etags.get(key, 0) + 1

    def read(self, container, item_id, partition_key):
        doc = self.docs.get((container, item_id))
        return {**deepcopy(doc), "_etag": str(self.etags[(container, item_id)])} if doc else None

    def replace(self, container, document, etag):
        key = (container, document["id"])
        if key not in self.docs or str(self.etags[key]) != etag:
            return False
        self.upsert(container, document)
        return True


class MemoryQueue:
    def __init__(self):
        self.sent = []

    def send(self, queue, message):
        self.sent.append((queue, message))


class MemoryRaw:
    def __init__(self):
        self.blobs = {}

    def put_json(self, container, name, value):
        self.blobs[(container, name)] = json.loads(json.dumps(value))

    def get_json(self, container, name):
        return self.blobs.get((container, name))


class Outbox:
    def __init__(self, fail_topics=()):
        self.sent, self.fail_topics = [], set(fail_topics)

    def publish(self, topic, message, message_id, session_id=None):
        if topic in self.fail_topics:
            raise RuntimeError("sb down")
        self.sent.append((topic, message, message_id, session_id))

    def bodies(self, topic):
        return [json.dumps(m) for t, m, _, _ in self.sent if t == topic]


class ScriptedAgents:
    """EW: new_debt 트리거가 있으면 근거 있는 경보를 올린다. Collections: 상담 안내 + 재조정 요청."""

    def __init__(self):
        self.calls = []

    def run(self, spec, input_text, handlers):
        self.calls.append(spec.name)
        if spec.name == EW_AGENT:
            base = handlers["get_baseline"]({})
            week = base["current_week"]
            decision = "no_alert"
            if "new_debt" in base["triggers"]:
                out = handlers["raise_alert"](
                    {
                        "severity": "high",
                        "summary": "실행 직후 고금리 대부 상환이 시작됐다.",
                        "recommended_action": "contact_borrower",
                        "signals": [
                            {
                                "kind": "loan_stacking",
                                "text": "고금리 상환이 새로 나타났다.",
                                "metric_ids": [f"W:{week}:high_cost_lender"],
                                "txn_ids": [],
                            }
                        ],
                    }
                )
                assert out["accepted"], out
                decision = "alert"
            output = {"decision": decision, "reason": "조사 결과다."}
        else:
            handlers["send_script"]({"script_id": "hardship_offer"})
            handlers["route_hardship"]({"category": "debt_overload", "note": "대출 중첩"})
            output = {"decision": "routed", "reason": "상담으로 넘겼다."}
        return AgentRun(spec.name, "1", f"conv-{len(self.calls)}", spec.output_model.model_validate(output), [], 1, 1)

    def delete_container(self, container_id):
        return True


@pytest.fixture(scope="module")
def mock_app():
    cases = build_demo_cases(AS_OF)
    return create_mock_app(cases, SECRET, lambda: NOW, flows=build_postloan_flows(cases, AS_OF))


def make_deps(mock_app, events=None, agents=None):
    return Deps(
        store=MemoryStore(),
        raw=MemoryRaw(),
        mock=HttpMockApi(client=TestClient(mock_app)),
        policy=None,
        events=events,
        pseudonym_key=KEY,
        clock=lambda: NOW,
        agents=agents,
        queue=MemoryQueue(),
    )


def approved_loan(deps, applicant_id="seller-dailylook-01"):
    consent = request_consent(deps, {"application_id": LOAN, "applicant_id": applicant_id})
    assemble_data(
        deps,
        {"application_id": LOAN, "consent_id": consent["consent_id"], "token": consent["token"], "scopes": consent["scopes"]},
    )
    uw = underwrite(
        deps,
        {
            "application_id": LOAN,
            "consent_id": consent["consent_id"],
            "as_of": AS_OF.isoformat(),
            "requested_amount_krw": 3_300_000,
            "requested_tenor_months": 12,
        },
    )
    register_monitoring(
        deps,
        {
            "application_id": LOAN,
            "applicant_id": applicant_id,
            "consent_id": consent["consent_id"],
            "decision": "approve",
            "amount_krw": 3_300_000,
            "tenor_months": 12,
            "decided_at": NOW.isoformat(),
            "underwriting": {"features": uw["features"], "monthly": uw["monthly"]},
        },
    )
    return consent


def replay(deps):
    return handle_replay(deps, json.dumps({"loan_id": LOAN}))


def loan(deps):
    return deps.store.read(LOANS, LOAN, LOAN)


def alerts(deps):
    return [d for (c, i), d in deps.store.docs.items() if c == ALERTS and i.startswith("ew-")]


def test_register_monitoring_builds_baseline_and_keeps_replay_state(mock_app):
    deps = make_deps(mock_app)
    approved_loan(deps)
    doc = loan(deps)
    assert doc["industry"] == "패션의류" and doc["monitoring"] is None
    assert doc["baseline"]["avg_weekly_revenue"] > 0 and set(doc["baseline"]["seasonal_index"]) == {f"{m:02d}" for m in range(1, 13)}
    replay(deps)
    approved_loan(deps)
    assert loan(deps)["monitoring"]["status"] == "completed"


def test_inline_replay_without_agents_raises_detector_alerts_for_officer(mock_app):
    deps = make_deps(mock_app)
    approved_loan(deps)
    started = replay(deps)
    mon = loan(deps)["monitoring"]
    assert started["mode"] == mon["mode"] == "inline"
    assert mon["processed_week"] == WEEKS and mon["status"] == "completed" and len(mon["weeks"]) == WEEKS
    consent = deps.store.read("consents", mon["consent_id"], mon["consent_id"])
    assert consent["purpose"] == MONITORING_PURPOSE and consent["scopes"] == ["account"]
    assert deps.raw.get_json(f"consent-{mon['consent_id']}", "flow.json")
    week2 = mon["weeks"][1]
    assert "new_debt" in week2["new_triggers"] and week2["alert_id"]
    found = alerts(deps)
    assert found and all(a["models"] == {"investigator": "detector_only"} for a in found)
    # officer_review 경보는 자동 연락하지 않는다.
    assert all(a["collections"]["decision"] == "none" and a["collections"]["contacts"] == [] for a in found)
    # 트리거 종류마다 한 번만 조사한다.
    new = [t for w in mon["weeks"] for t in w["new_triggers"]]
    assert len(new) == len(set(new))
    assert all(bool(w["investigation"]) == bool(w["new_triggers"]) for w in mon["weeks"])


def test_servicebus_replay_publishes_ordered_session_messages(mock_app):
    outbox = Outbox()
    deps = make_deps(mock_app, events=outbox, agents=ScriptedAgents())
    approved_loan(deps)
    started = replay(deps)
    flow_msgs = [m for m in outbox.sent if m[0] == TOPIC_FLOW]
    assert [m[1]["week"] for m in flow_msgs] == list(range(1, WEEKS + 1))
    assert all(m[3] == LOAN for m in flow_msgs)
    assert flow_msgs[0][2] == f"flow.updated:{LOAN}:{started['replay_id']}:1"
    assert loan(deps)["monitoring"]["processed_week"] == 0

    bodies = outbox.bodies(TOPIC_FLOW)
    assert handle_flow_update(deps, bodies[0])["week"] == 1
    assert handle_flow_update(deps, bodies[0]) is None  # 중복 전달
    with pytest.raises(OutOfOrder):
        handle_flow_update(deps, bodies[2])
    assert handle_flow_update(deps, json.dumps({"type": "smoke"})) is None
    for body in bodies[1:]:
        handle_flow_update(deps, body)
    mon = loan(deps)["monitoring"]
    assert mon["status"] == "completed"
    raised = [m for m in outbox.sent if m[0] == TOPIC_ALERT]
    assert [m[1]["alert_id"] for m in raised] == mon["alert_ids"] and all(m[3] == LOAN for m in raised)
    alert = deps.store.read(ALERTS, mon["alert_ids"][0], LOAN)
    assert alert["severity"] == "high" and alert["models"] == {"investigator": "gpt-5.4"}
    assert alert["replay_id"] == started["replay_id"]


def test_stale_replay_messages_are_ignored(mock_app):
    outbox = Outbox()
    deps = make_deps(mock_app, events=outbox)
    approved_loan(deps)
    replay(deps)
    old = outbox.bodies(TOPIC_FLOW)[0]
    expire_replay(deps)
    replay(deps)
    assert handle_flow_update(deps, old) is None
    assert loan(deps)["monitoring"]["processed_week"] == 0


def expire_replay(deps):
    doc = loan(deps)
    doc["monitoring"]["started_at"] = (NOW - REPLAY_GUARD).isoformat()
    deps.store.upsert(LOANS, doc)


def test_replay_in_progress_blocks_a_second_replay(mock_app):
    outbox = Outbox()
    deps = make_deps(mock_app, events=outbox)
    approved_loan(deps)
    first = replay(deps)
    assert replay(deps) is None  # 더블 클릭·중복 큐 메시지: 동시 재생은 대출 문서를 서로 덮어쓴다
    assert loan(deps)["monitoring"]["replay_id"] == first["replay_id"]
    assert len(outbox.bodies(TOPIC_FLOW)) == WEEKS
    expire_replay(deps)  # 멈춘 재생(메시지 dead-letter 등)은 가드 시간이 지나면 다시 시작할 수 있다
    assert replay(deps)["replay_id"] != first["replay_id"]


def test_inline_replay_failure_is_recorded_without_retry(mock_app, monkeypatch):
    deps = make_deps(mock_app)
    approved_loan(deps)

    def boom(*args, **kwargs):
        raise RuntimeError("foundry down")

    monkeypatch.setattr(monitoring_module, "process_week", boom)
    started = replay(deps)
    mon = loan(deps)["monitoring"]
    assert started["replay_id"] == mon["replay_id"]
    assert mon["status"] == "failed" and "foundry down" in mon["error"]
    assert replay(deps)["replay_id"] != started["replay_id"]  # 실패한 재생은 바로 다시 시작할 수 있다


def test_lost_claim_race_starts_nothing(mock_app):
    outbox = Outbox()
    deps = make_deps(mock_app, events=outbox)
    approved_loan(deps)
    store = deps.store
    store.replace = lambda container, document, etag: False  # 다른 재생이 먼저 대출 문서를 선점했다
    sent_before, published_before = len(deps.queue.sent), len(outbox.sent)
    consents_before = {k for k in store.docs if k[0] == "consents"}
    assert replay(deps) is None
    assert loan(deps)["monitoring"] is None
    assert {k for k in store.docs if k[0] == "consents"} == consents_before  # 모니터링 동의를 만들지 않는다
    assert len(deps.queue.sent) == sent_before and len(outbox.sent) == published_before


def test_concurrent_claim_lets_only_one_replay_through(mock_app):
    deps = make_deps(mock_app, events=Outbox())
    approved_loan(deps)
    store, first_read = deps.store, deps.store.read
    stale = first_read(LOANS, LOAN, LOAN)
    reads = []

    def racing_read(container, item_id, partition_key):
        # 두 재생이 모두 같은 옛 문서를 읽은 뒤 서로 선점하려 한다.
        if container == LOANS and len(reads) < 2:
            reads.append(1)
            return deepcopy(stale)
        return first_read(container, item_id, partition_key)

    store.read = racing_read
    results = [replay(deps), replay(deps)]
    assert [r is None for r in results] == [False, True]
    assert len([k for k in store.docs if k[0] == "consents"]) == 2  # 심사 동의 + 모니터링 동의 하나
    assert len(deps.events.bodies(TOPIC_FLOW)) == WEEKS


def test_failure_after_claim_is_recorded_and_not_blocking(mock_app):
    deps = make_deps(mock_app)
    approved_loan(deps)

    class FlowDown:
        def __init__(self, inner):
            self.inner = inner

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def fetch(self, kind, token):
            raise RuntimeError("mock flow down")

    broken = replace(deps, mock=FlowDown(deps.mock))
    with pytest.raises(RuntimeError, match="mock flow down"):
        replay(broken)
    mon = loan(deps)["monitoring"]
    assert mon["status"] == "failed" and "mock flow down" in mon["error"]
    assert replay(deps)["replay_id"] != mon["replay_id"]  # failed는 가드에 걸리지 않는다


def test_alert_raised_runs_collections_once(mock_app):
    outbox = Outbox()
    agents = ScriptedAgents()
    deps = make_deps(mock_app, events=outbox, agents=agents)
    approved_loan(deps)
    replay(deps)
    for body in outbox.bodies(TOPIC_FLOW):
        handle_flow_update(deps, body)
    body = outbox.bodies(TOPIC_ALERT)[0]
    result = handle_alert_raised(deps, body)
    assert result["decision"] == "routed" and result["contacts"][0]["script_id"] == "hardship_offer"
    assert handle_alert_raised(deps, body) is None
    assert agents.calls.count(COLLECTIONS_AGENT) == 1
    alert_id = json.loads(body)["alert_id"]
    doc = deps.store.read(ALERTS, alert_id, LOAN)
    assert doc["status"] == "restructure_pending" and doc["restructure"]["status"] == "pending_officer"
    replay_id = loan(deps)["monitoring"]["replay_id"]
    contacts = deps.store.read(ALERTS, f"contacts-{replay_id}", LOAN)
    assert [c["status"] for c in contacts["contacts"]] == ["sent"]
    consent_id = loan(deps)["monitoring"]["consent_id"]
    monitoring_runs = [c for c in agents.calls if c in (EW_AGENT, COLLECTIONS_AGENT)]
    assert len(deps.store.read("consents", consent_id, consent_id)["foundry_conversations"]) == len(monitoring_runs)


def test_restructure_decision_requires_pending_request(mock_app):
    outbox = Outbox()
    deps = make_deps(mock_app, events=outbox, agents=ScriptedAgents())
    approved_loan(deps)
    replay(deps)
    for body in outbox.bodies(TOPIC_FLOW):
        handle_flow_update(deps, body)
    alert_id = json.loads(outbox.bodies(TOPIC_ALERT)[0])["alert_id"]
    decision = {"loan_id": LOAN, "alert_id": alert_id, "decision": "approve", "officer_id": "officer-1", "reason": "3개월 유예"}
    assert handle_restructure_decision(deps, json.dumps(decision)) is False  # 아직 Collections 전
    handle_alert_raised(deps, outbox.bodies(TOPIC_ALERT)[0])
    assert handle_restructure_decision(deps, json.dumps(decision)) is True
    assert handle_restructure_decision(deps, json.dumps(decision)) is False
    assert handle_restructure_decision(deps, json.dumps({**decision, "decision": "maybe"})) is False
    doc = deps.store.read(ALERTS, alert_id, LOAN)
    assert doc["status"] == "restructure_approved"
    assert doc["restructure"]["officer_id"] == "officer-1" and doc["restructure"]["reason"] == "3개월 유예"


def test_alert_publish_failure_is_recorded(mock_app):
    outbox = Outbox(fail_topics={TOPIC_ALERT})
    deps = make_deps(mock_app, events=outbox, agents=ScriptedAgents())
    approved_loan(deps)
    replay(deps)
    for body in outbox.bodies(TOPIC_FLOW):
        handle_flow_update(deps, body)
    mon = loan(deps)["monitoring"]
    assert mon["status"] == "completed"
    assert all("sb down" in deps.store.read(ALERTS, a, LOAN)["event_error"] for a in mon["alert_ids"])


def test_unknown_loan_and_bad_messages(mock_app):
    deps = make_deps(mock_app)
    assert handle_replay(deps, json.dumps({"loan_id": "nope-1"})) is None
    assert handle_replay(deps, "not json") is None
    assert handle_alert_raised(deps, json.dumps({"type": "alert.raised", "loan_id": LOAN, "alert_id": "x"})) is None
    assert process_week(deps, LOAN, "rp-x", 1) is None


def test_replay_schedules_monitoring_consent_lifecycle(mock_app):
    deps = make_deps(mock_app)
    underwriting = approved_loan(deps)
    replay(deps)
    mon_consent = loan(deps)["monitoring"]["consent_id"]
    scheduled = [m["consent_id"] for q, m in deps.queue.sent if q == "consent-lifecycle"]
    assert scheduled == [underwriting["consent_id"], mon_consent]


def test_replay_refused_after_underwriting_consent_ended(mock_app):
    deps = make_deps(mock_app)
    underwriting = approved_loan(deps)
    doc = deps.store.read("consents", underwriting["consent_id"], underwriting["consent_id"])
    deps.store.upsert("consents", {**doc, "status": "expired"})
    assert replay(deps) is None
    assert loan(deps)["monitoring"] is None
    assert len(deps.queue.sent) == 1
