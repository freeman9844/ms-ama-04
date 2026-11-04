import json
from copy import deepcopy
from datetime import date, datetime
from functools import partial

import httpx
import pytest
from fastapi.testclient import TestClient

from agents.data_assembly import FLAG_AGENT_FALLBACK, SourceError
from agents.runtime import AgentError, AgentRun
from agents.underwriting import ANALYST_AGENT, FLAG_ASSESSMENT_UNAVAILABLE, REVIEWER_AGENT
from journey.activities import (
    ACTIVITIES,
    Deps,
    StoreCache,
    assemble_data,
    assess_credit,
    evaluate_policy,
    Conflict,
    _remember_foundry,
    request_consent,
    save_document,
    underwrite,
    update_doc,
)
from agents.txn_classify import DecisionClient
from journey.adapters import HttpMockApi, HttpPolicyApi, PolicyUnavailable, agent_settings
from journey.models import KST
from mockapis.app import create_app as create_mock_app
from mockapis.demo import build_demo_cases
from policy.app import create_app as create_policy_app
from policy.rules import load_rules

AS_OF = date(2026, 11, 2)
SECRET = b"s" * 32
KEY = b"k" * 32
NOW = datetime(2026, 11, 2, 9, 0, tzinfo=KST)


class MemoryStore:
    def __init__(self):
        self.docs, self.etags, self.conflicts = {}, {}, 0

    def upsert(self, container, document):
        self.docs[(container, document["id"])] = deepcopy(document)
        self.etags[(container, document["id"])] = self.etags.get((container, document["id"]), 0) + 1

    def read(self, container, item_id, partition_key):
        doc = self.docs.get((container, item_id))
        return {**deepcopy(doc), "_etag": str(self.etags[(container, item_id)])} if doc else None

    def replace(self, container, document, etag):
        key = (container, document["id"])
        if self.conflicts:
            self.conflicts -= 1
            self.etags[key] += 1
        if key not in self.docs or str(self.etags[key]) != etag:
            return False
        self.upsert(container, {k: v for k, v in document.items() if k != "_etag"})
        return True


class MemoryRaw:
    def __init__(self):
        self.blobs = {}

    def put_json(self, container, name, value):
        self.blobs[(container, name)] = json.loads(json.dumps(value))

    def get_json(self, container, name):
        return self.blobs.get((container, name))

    def manifest(self, container):
        names = sorted(n for c, n in self.blobs if c == container)
        if not names:
            return None
        return [{"name": n, "size": len(json.dumps(self.blobs[(container, n)])), "sha256": "h"} for n in names]

    def delete_container(self, container):
        keys = [k for k in self.blobs if k[0] == container]
        for k in keys:
            del self.blobs[k]
        return bool(keys)


class MemoryQueue:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send(self, queue, message):
        if self.fail:
            raise RuntimeError("queue down")
        self.sent.append((queue, message))


class Outbox:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def publish(self, topic, message, message_id):
        if self.fail:
            raise RuntimeError("sb down")
        self.sent.append((topic, message, message_id))


class FailingRunner:
    def run(self, spec, input_text, handlers):
        raise AgentError("model down", "conv_x")


@pytest.fixture
def mock_client():
    app = create_mock_app(build_demo_cases(AS_OF), SECRET, lambda: NOW)
    return TestClient(app)


@pytest.fixture
def deps(mock_client):
    return Deps(
        store=MemoryStore(),
        raw=MemoryRaw(),
        mock=HttpMockApi(client=mock_client),
        policy=HttpPolicyApi(client=TestClient(create_policy_app(load_rules()))),
        events=Outbox(),
        pseudonym_key=KEY,
        clock=lambda: NOW,
        queue=MemoryQueue(),
    )


def consent_for(deps, applicant_id="seller-dailylook-01", application_id="app-1"):
    return request_consent(deps, {"application_id": application_id, "applicant_id": applicant_id})


def assembled(deps, applicant_id="seller-dailylook-01"):
    consent = consent_for(deps, applicant_id)
    report = assemble_data(
        deps,
        {
            "application_id": "app-1",
            "consent_id": consent["consent_id"],
            "token": consent["token"],
            "scopes": consent["scopes"],
        },
    )
    return consent, report


def underwrite_input(consent_id):
    return {
        "application_id": "app-1",
        "consent_id": consent_id,
        "as_of": AS_OF.isoformat(),
        "requested_amount_krw": 3_300_000,
        "requested_tenor_months": 12,
    }


def test_save_document_rejects_unknown_container(deps):
    with pytest.raises(ValueError):
        save_document(deps, {"container": "secrets", "document": {"id": "x"}})
    assert save_document(deps, {"container": "applications", "document": {"id": "a"}}) == {"id": "a"}
    assert ("applications", "a") in deps.store.docs


def test_request_consent_stores_doc_without_token_and_publishes(deps):
    consent = consent_for(deps)
    assert consent["token"] and consent["scopes"] == ["account", "tax", "pg"]
    doc = deps.store.read("consents", consent["consent_id"], consent["consent_id"])
    assert doc["application_id"] == "app-1"
    assert doc["raw_container"] == f"consent-{consent['consent_id']}"
    assert doc["event_published"] is True
    assert consent["token"] not in json.dumps(doc)
    topic, message, message_id = deps.events.sent[0]
    assert topic == "consent"
    assert message["type"] == "consent.granted"
    assert message_id == f"consent.granted:{consent['consent_id']}"


@pytest.mark.parametrize("events, error", [(None, "servicebus_disabled"), (Outbox(fail=True), "RuntimeError: sb down")])
def test_consent_event_failure_is_recorded_not_fatal(deps, events, error):
    d = Deps(**{**deps.__dict__, "events": events})
    consent = consent_for(d)
    doc = d.store.read("consents", consent["consent_id"], consent["consent_id"])
    assert doc["event_published"] is False
    assert doc["event_error"] == error


def test_request_consent_unknown_applicant_raises(deps):
    with pytest.raises(httpx.HTTPStatusError):
        consent_for(deps, applicant_id="nobody")


def test_assemble_data_writes_raw_blobs_per_consent(deps):
    consent, report = assembled(deps)
    assert report["status"] == "complete"
    assert FLAG_AGENT_FALLBACK in report["flags"]
    container = f"consent-{consent['consent_id']}"
    names = {name for (c, name) in deps.raw.blobs if c == container}
    assert names == {"profile.json", "transactions.json", "tax.json", "pg.json"}
    assert "token" not in json.dumps(report)


def test_assemble_data_records_conversation_on_consent(deps):
    d = Deps(**{**deps.__dict__, "agents": FailingRunner()})
    consent, report = assembled(d)
    assert report["conversation_id"] == "conv_x"
    doc = d.store.read("consents", consent["consent_id"], consent["consent_id"])
    assert doc["foundry_conversations"] == ["conv_x"]


def test_underwrite_and_policy_on_real_apps(deps):
    consent, _ = assembled(deps)
    result = underwrite(deps, underwrite_input(consent["consent_id"]))
    assert result["features"]["application_id"] == "app-1"
    assert result["features"]["months_of_history"] == 12
    assert result["signals"]["classification_rules"]
    assert {c["payer"] for c in result["settlement_profile"]["channels"]} >= {"네이버파이낸셜", "토스페이먼츠"}
    assert [e["group"] for e in result["expense_breakdown"]][0] == "supplier" and len(result["expense_breakdown"]) == 8
    decision = evaluate_policy(deps, result["features"])
    assert decision["decision"] == "eligible"
    assert decision["max_amount_krw"] == 3_300_000


def test_underwrite_flags_high_cost_lender(deps):
    consent, _ = assembled(deps, "seller-glowlab-03")
    assert "high_cost_lender_detected" in underwrite(deps, underwrite_input(consent["consent_id"]))["flags"]


def test_underwrite_requires_raw_data(deps):
    with pytest.raises(RuntimeError, match="원천 데이터 없음"):
        underwrite(deps, underwrite_input("missing"))


def test_mock_fetch_maps_errors(mock_client):
    api = HttpMockApi(client=mock_client)
    with pytest.raises(SourceError) as bad:
        api.fetch("profile", "not-a-token")
    assert bad.value.status == 401
    mock_client.post("/admin/faults", json={"mode": "unavailable"})
    with pytest.raises(SourceError) as down:
        api.fetch("profile", "not-a-token")
    assert down.value.status == 503


def test_policy_rejects_invalid_features(deps):
    with pytest.raises(PolicyUnavailable):
        deps.policy.decide({"application_id": "app-1"})


def journey_activities(deps):
    return {name: partial(fn, deps) for name, fn in ACTIVITIES.items()}


START = {
    "application_id": "app-1",
    "applicant_id": "seller-dailylook-01",
    "requested_amount_krw": 3_300_000,
    "requested_tenor_months": 12,
}


def test_full_journey_approved_end_to_end(deps, run_journey):
    approve = {"decision": "approve", "officer_id": "officer-1", "amount_krw": 3_300_000}
    result, ctx = run_journey(START, journey_activities(deps), [("event", approve)])
    assert result == {"application_id": "app-1", "status": "approved"}
    called = {c.name for c in ctx.calls}
    assert called == set(ACTIVITIES)
    final = ctx.saved[-1]
    assert final["policy"]["decision"] == "eligible"
    assert final["assembly"]["status"] == "complete"
    assert final["consent"]["consent_id"] in {doc["consent_id"] for (c, _), doc in deps.store.docs.items() if c == "consents"}
    assert "token" not in json.dumps(ctx.saved)


def test_full_journey_sources_down_goes_incomplete(deps, mock_client, run_journey):
    mock_client.post("/admin/faults", json={"mode": "unavailable"})
    decline = {"decision": "decline", "officer_id": "officer-1", "reason": "자료 미비"}
    result, ctx = run_journey(START, journey_activities(deps), [("event", decline)])
    assert result["status"] == "declined"
    final = ctx.saved[-1]
    assert final["assembly"]["status"] == "incomplete"
    assert "data_incomplete" in final["flags"]
    assert not ctx.called("underwrite")


class FakeDecider:
    def __init__(self, answer=("supplier", 0.95), error=None):
        self.answer, self.error, self.states = answer, error, []

    def choose(self, state):
        self.states.append(state)
        if self.error:
            raise self.error
        return self.answer


ANALYST_DRAFT = {
    "recommendation": "approve",
    "summary": "상환 여력이 충분하다.",
    "claims": [{"text": "DSCR은 4.106다.", "metric_ids": ["M:dscr"], "txn_ids": []}],
    "proposed_structure": {
        "amount_krw": 3_300_000, "tenor_months": 12, "schedule": "level", "reduced_payment_months": [], "rationale": "한도 안",
    },
    "counterfactuals": [],
}
REVIEW = {"verdicts": [{"claim_index": 0, "supported": True, "reason": "일치"}], "feedback": ""}


class UnderwritingRunner:
    def __init__(self):
        self.names, self.deleted = [], []

    def run(self, spec, input_text, handlers):
        self.names.append(spec.name)
        n = len(self.names)
        out = ANALYST_DRAFT if spec.name == ANALYST_AGENT else REVIEW
        containers = (f"cntr_{n}",) if spec.name == ANALYST_AGENT else ()
        return AgentRun(spec.name, "1", f"conv_{n}", spec.output_model.model_validate(out), [], 10, 1, containers)

    def delete_container(self, container_id):
        self.deleted.append(container_id)
        return True


def with_deps(deps, **changes):
    return Deps(**{**deps.__dict__, **changes})


def test_underwrite_without_decider_skips_semantic_classification(deps):
    consent, _ = assembled(deps)
    result = underwrite(deps, underwrite_input(consent["consent_id"]))
    assert result["classification"] is None and result["category_overrides"] == {}
    assert len(result["monthly"]) == 12 and result["signals"]["semantic_overrides"] == 0


def test_underwrite_classifies_unresolved_without_caching_transfers(deps):
    consent, _ = assembled(deps)
    decider = FakeDecider()
    d = with_deps(deps, decider=decider, cache=StoreCache(deps.store))
    first = underwrite(d, underwrite_input(consent["consent_id"]))
    m = first["classification"]["metrics"]
    assert m["decision_calls"] == m["unique_descriptions"] > 0 and m["cache_hits"] == 0
    assert first["category_overrides"] == {}
    expenses = {e["group"]: e["monthly_avg_krw"] for e in first["expense_breakdown"]}
    assert expenses["unclassified"] == 0 and expenses["supplier"] > 0  # 의미 라벨이 지출 구성으로 간다
    assert {s["business_industry"] for s in decider.states} == {"패션의류"}
    # 자동이체 적요(연금·솔루션·카드대금)만 공유 캐시에 남고, 이체(공급처 송금·광고 충전)는 남지 않는다.
    cached = [k for c, k in deps.store.docs if c == "classifications"]
    assert 0 < len(cached) < m["unique_descriptions"]
    second = underwrite(d, underwrite_input(consent["consent_id"]))["classification"]["metrics"]
    assert (second["decision_calls"], second["cache_hits"]) == (m["unique_descriptions"] - len(cached), len(cached))


def test_underwrite_records_fallback_conversation_on_consent(deps):
    consent, _ = assembled(deps)
    d = with_deps(deps, decider=FakeDecider(("cannot_tell", 0.5)), agents=FailingRunner())
    result = underwrite(d, underwrite_input(consent["consent_id"]))
    assert result["classification"]["metrics"]["fallback_errors"] == 1
    doc = deps.store.read("consents", consent["consent_id"], consent["consent_id"])
    assert doc["foundry_conversations"] == ["conv_x"]


def test_semantic_debt_label_is_conservative(deps):
    consent, _ = assembled(deps)
    base = underwrite(deps, underwrite_input(consent["consent_id"]))
    d = with_deps(deps, decider=FakeDecider(("high_cost_lender", 0.95)))
    result = underwrite(d, underwrite_input(consent["consent_id"]))
    assert result["category_overrides"] and set(result["category_overrides"].values()) == {"high_cost_lender"}
    assert "high_cost_lender_detected" in result["flags"]
    assert result["features"]["dscr"] <= base["features"]["dscr"]
    assert result["features"]["cash_buffer_days"] <= base["features"]["cash_buffer_days"]


def test_decider_outage_falls_back_to_rules(deps):
    consent, _ = assembled(deps)
    base = underwrite(deps, underwrite_input(consent["consent_id"]))
    d = with_deps(deps, decider=FakeDecider(error=RuntimeError("503")))
    result = underwrite(d, underwrite_input(consent["consent_id"]))
    assert result["classification"]["metrics"]["decision_errors"] > 0
    assert result["features"] == base["features"]


def assess_input(deps, applicant_id="seller-dailylook-01"):
    consent, _ = assembled(deps, applicant_id)
    uw = underwrite(deps, underwrite_input(consent["consent_id"]))
    policy = evaluate_policy(deps, uw["features"])
    payload = {"application_id": "app-1", "consent_id": consent["consent_id"], "underwriting": uw, "policy": policy}
    return consent, json.loads(json.dumps(payload))


def test_assess_credit_returns_assessment_and_records_foundry_artifacts(deps):
    consent, payload = assess_input(deps)
    runner = UnderwritingRunner()
    result = assess_credit(with_deps(deps, agents=runner), payload)
    a = result["assessment"]
    assert (a["recommendation"], a["review"]["passed"], a["models"]["analyst"]) == ("approve", True, "gpt-5.4")
    assert result["flags"] == [] and result["record"]["rounds"][0]["grounding_issues"] == []
    assert runner.names == [ANALYST_AGENT, REVIEWER_AGENT] and runner.deleted == ["cntr_1"]
    doc = deps.store.read("consents", consent["consent_id"], consent["consent_id"])
    assert doc["foundry_conversations"] == ["conv_1", "conv_2"]
    assert doc["foundry_containers"] == [{"id": "cntr_1", "deleted": True}]


def test_assess_credit_without_agents_is_unavailable(deps):
    _, payload = assess_input(deps)
    result = assess_credit(deps, payload)
    assert (result["assessment"], result["flags"]) == (None, [FLAG_ASSESSMENT_UNAVAILABLE])


def test_store_cache_round_trip(deps):
    cache = StoreCache(deps.store)
    assert cache.get("k1") is None
    cache.put("k1", {"label": "supplier", "confidence": 0.9})
    assert cache.get("k1")["label"] == "supplier"
    assert deps.store.read("classifications", "k1", "k1")["id"] == "k1"


def test_agent_settings_disabled_without_endpoints():
    assert agent_settings({}, lambda scope: "t") == {}


def test_agent_settings_builds_agents_decider_and_models():
    scopes = []
    env = {
        "AI_PROJECT_ENDPOINT": "https://ais.services.ai.azure.com/api/projects/p",
        "AI_ACCOUNT_ENDPOINT": "https://ais.services.ai.azure.com",
        "DECISION_DEPLOYMENT": "decision-1",
        "ANALYST_MODEL": "gpt-x",
    }
    settings = agent_settings(env, lambda scope: scopes.append(scope) or "t")
    assert isinstance(settings["decider"], DecisionClient)
    assert settings["agents"] is not None
    assert (settings["analyst_model"], settings["reviewer_model"]) == ("gpt-x", "gpt-5.4-mini")
    assert settings["classifier_model"] == "gpt-5.4-mini"
    assert (settings["notice_model"], settings["grievance_model"]) == ("gpt-5.4-mini", "gpt-5.4-mini")


def test_request_consent_schedules_lifecycle(deps):
    consent = consent_for(deps)
    ((queue, message),) = deps.queue.sent
    assert queue == "consent-lifecycle"
    assert message == {"consent_id": consent["consent_id"], "expires_at": consent["expires_at"]}
    doc = deps.store.read("consents", consent["consent_id"], consent["consent_id"])
    assert doc["lifecycle_error"] is None and doc["status"] == "granted"


def test_request_consent_without_queue_records_error(deps):
    d = with_deps(deps, queue=None)
    consent = consent_for(d)
    assert d.store.read("consents", consent["consent_id"], consent["consent_id"])["lifecycle_error"] == "queue_disabled"


def test_request_consent_fails_closed_when_lifecycle_cannot_be_scheduled(deps):
    d = with_deps(deps, queue=MemoryQueue(fail=True))
    with pytest.raises(RuntimeError, match="queue down"):
        consent_for(d)
    ((_, doc),) = [(k, v) for k, v in d.store.docs.items() if k[0] == "consents"]
    assert doc["status"] == "void" and "queue down" in doc["lifecycle_error"]


def test_update_doc_retries_on_etag_conflict(deps):
    deps.store.upsert("consents", {"id": "c1", "n": 0})
    deps.store.conflicts = 2
    doc = update_doc(deps.store, "consents", "c1", "c1", lambda d: d.update(n=d["n"] + 1))
    assert doc["n"] == 1 and deps.store.read("consents", "c1", "c1")["n"] == 1
    assert update_doc(deps.store, "consents", "zz", "zz", lambda d: None) is None
    deps.store.conflicts = 10
    with pytest.raises(Conflict):
        update_doc(deps.store, "consents", "c1", "c1", lambda d: d.update(n=5), attempts=3)


def test_update_doc_skips_write_when_mutate_returns_false(deps):
    deps.store.upsert("consents", {"id": "c1", "n": 0})
    before = deps.store.etags[("consents", "c1")]
    update_doc(deps.store, "consents", "c1", "c1", lambda d: False)
    assert deps.store.etags[("consents", "c1")] == before


class CleanupRunner:
    def __init__(self):
        self.deleted_conversations, self.deleted_containers = [], []

    def delete_conversation(self, conversation_id):
        self.deleted_conversations.append(conversation_id)
        return "deleted"

    def delete_container(self, container_id):
        self.deleted_containers.append(container_id)
        return True


def test_remember_foundry_on_ended_consent_deletes_immediately(deps):
    runner = CleanupRunner()
    d = with_deps(deps, agents=runner)
    d.store.upsert("consents", {"id": "c1", "consent_id": "c1", "status": "revoked", "foundry_conversations": []})
    _remember_foundry(d, "c1", ["conv_late"], [{"id": "cntr_late", "deleted": False}])
    doc = d.store.read("consents", "c1", "c1")
    assert runner.deleted_conversations == ["conv_late"] and runner.deleted_containers == ["cntr_late"]
    assert doc["foundry_conversations"] == []
    assert doc["late_cleanup"] == [
        {"kind": "conversation", "target": "conv_late", "outcome": "deleted", "at": NOW.isoformat()},
        {"kind": "container", "target": "cntr_late", "outcome": "deleted", "at": NOW.isoformat()},
    ]


def test_remember_foundry_on_live_consent_appends(deps):
    deps.store.upsert("consents", {"id": "c1", "consent_id": "c1", "status": "granted", "foundry_conversations": ["a"]})
    _remember_foundry(deps, "c1", ["a", "b"], [{"id": "k", "deleted": True}])
    doc = deps.store.read("consents", "c1", "c1")
    assert doc["foundry_conversations"] == ["a", "b"] and doc["foundry_containers"] == [{"id": "k", "deleted": True}]


def test_mock_revoke(mock_client, deps):
    consent = consent_for(deps)
    api = HttpMockApi(client=mock_client)
    assert api.revoke(consent["consent_id"]) == "revoked"
    with pytest.raises(SourceError):
        api.fetch("profile", consent["token"])
    assert api.revoke("unknown") == "absent"
