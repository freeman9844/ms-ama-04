import asyncio
import json
import sys
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from types import ModuleType, SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agents.notices import Draft
from agents.runtime import AgentRun
from journey.activities import ConsentEnded, Deps, _remember_foundry, assemble_data, request_consent
from journey.adapters import BlobRawStore, HttpMockApi, HttpPolicyApi
from journey.models import KST
from journey.consent import (
    CONSENT_ACTIVITIES,
    LIFECYCLE_ORCHESTRATOR,
    PROOFS_CONTAINER,
    IncompleteDeletion,
    consent_lifecycle,
    delete_consent_data,
    handle_consent_command,
    handle_lifecycle_start,
    send_consent_notice,
)
from mockapis.app import create_app as create_mock_app
from mockapis.demo import build_demo_cases
from policy.app import create_app as create_policy_app
from policy.rules import load_rules


AS_OF = date(2026, 11, 2)
SECRET = b"s" * 32
KEY = b"k" * 32
NOW = datetime(2026, 11, 2, 9, 0, tzinfo=KST)
START = datetime(2026, 11, 1, 15, 0, tzinfo=UTC)  # conftest의 오케스트레이션 시작 시각
RETRY = "RETRY"  # conftest가 오케스트레이터에 넘기는 재시도 정책 자리표시


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



class FoundryRunner:
    """안내문 에이전트는 검사에 걸리는 초안을 내 템플릿을 쓰게 하고, 삭제·확인 호출을 기록한다."""

    def __init__(self, exists=False):
        self.deleted_conversations, self.deleted_containers, self.exists = [], [], exists

    def run(self, spec, input_text, handlers):
        return AgentRun(spec.name, "1", "conv_notice", Draft(title="안내", body="필수 사실이 빠진 초안"), [], 1, 0)

    def delete_conversation(self, conversation_id):
        self.deleted_conversations.append(conversation_id)
        return "deleted"

    def conversation_exists(self, conversation_id):
        return self.exists

    def delete_container(self, container_id):
        self.deleted_containers.append(container_id)
        return True


@pytest.fixture
def mock_client():
    return TestClient(create_mock_app(build_demo_cases(AS_OF), SECRET, lambda: NOW))


def make_deps(mock_client, agents=None):
    return Deps(
        store=MemoryStore(),
        raw=MemoryRaw(),
        mock=HttpMockApi(client=mock_client),
        policy=HttpPolicyApi(client=TestClient(create_policy_app(load_rules()))),
        events=Outbox(),
        pseudonym_key=KEY,
        clock=lambda: NOW,
        agents=agents,
        queue=MemoryQueue(),
    )


def granted(deps, conversations=("conv_a",)):
    consent, _ = assembled(replace(deps, agents=None))  # 수집 에이전트는 이 테스트의 관심이 아니다
    cid = consent["consent_id"]
    containers = [{"id": "cntr_1", "deleted": False}, {"id": "cntr_2", "deleted": True}]
    _remember_foundry(deps, cid, list(conversations), containers if conversations else [])
    return consent


def consent_for(deps, applicant_id="demo-cafe-001", application_id="app-1"):
    return request_consent(deps, {"application_id": application_id, "applicant_id": applicant_id})


def assembled(deps, applicant_id="demo-cafe-001"):
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


def end(cid, reason="revoked", trigger="revoke", by="borrower:demo-cafe-001"):
    return {"consent_id": cid, "reason": reason, "trigger": trigger, "requested_by": by}


def test_delete_consent_data_removes_everything_and_writes_proof(mock_client):
    runner = FoundryRunner()
    deps = make_deps(mock_client, runner)
    consent = granted(deps)
    cid = consent["consent_id"]
    raw = deps.raw.manifest(f"consent-{cid}")

    result = delete_consent_data(deps, end(cid))

    assert result == {"proof_id": cid, "complete": True, "reason": "revoked"}
    proof = deps.store.read(PROOFS_CONTAINER, cid, cid)
    assert (proof["consent_id"], proof["application_id"], proof["applicant_id"]) == (cid, "app-1", "demo-cafe-001")
    assert (proof["reason"], proof["trigger"], proof["requested_by"]) == ("revoked", "revoke", "borrower:demo-cafe-001")
    assert proof["deleted_at"] == NOW.isoformat() and proof["complete"] is True and proof["attempts"] == 1
    assert [(i["kind"], i["target"], i["outcome"]) for i in proof["items"]] == [
        ("raw_container", f"consent-{cid}", "deleted"),
        ("consent_registry", cid, "revoked"),
        ("conversation", "conv_a", "deleted"),
        ("container", "cntr_1", "deleted"),
        ("container", "cntr_2", "deleted"),
    ]
    assert all(i["verified"] for i in proof["items"])
    assert proof["manifest"] == raw and len(proof["manifest_sha256"]) == 64
    assert {r["kind"] for r in proof["retained"]} == {"application_record"}
    assert deps.raw.manifest(f"consent-{cid}") is None
    assert runner.deleted_conversations == ["conv_a"] and runner.deleted_containers == ["cntr_1"]
    doc = deps.store.read("consents", cid, cid)
    assert (doc["status"], doc["ended_at"], doc["deletion_proof_id"]) == ("revoked", NOW.isoformat(), cid)
    assert doc["deletion_complete"] is True
    # 철회 뒤에는 토큰으로 원천 데이터를 다시 가져올 수 없다.
    response = mock_client.get("/mydata/profile", headers={"Authorization": f"Bearer {consent['token']}"})
    assert response.status_code == 401
    topic, message, message_id = deps.events.sent[-1]
    assert (topic, message["type"], message_id) == ("consent", "consent.revoked", f"consent.revoked:{cid}")
    assert message["proof_id"] == cid and proof["event_published"] is True


def test_delete_consent_data_is_idempotent_once_complete(mock_client):
    runner = FoundryRunner()
    deps = make_deps(mock_client, runner)
    cid = granted(deps)["consent_id"]
    delete_consent_data(deps, end(cid))
    again = delete_consent_data(deps, end(cid, "expired", "timer", "system"))
    assert again == {"proof_id": cid, "complete": True, "reason": "revoked"}
    assert deps.store.read(PROOFS_CONTAINER, cid, cid)["attempts"] == 1
    assert runner.deleted_conversations == ["conv_a"] and len(deps.events.sent) == 2  # granted + revoked


def test_unverified_foundry_deletion_is_recorded_and_raises(mock_client):
    deps = make_deps(mock_client, FoundryRunner(exists=True))
    cid = granted(deps)["consent_id"]
    with pytest.raises(IncompleteDeletion):
        delete_consent_data(deps, end(cid))
    proof = deps.store.read(PROOFS_CONTAINER, cid, cid)
    conv = next(i for i in proof["items"] if i["kind"] == "conversation")
    assert conv["outcome"] == "deleted" and conv["verified"] is False and proof["complete"] is False
    assert deps.store.read("consents", cid, cid)["status"] == "revoked"
    with pytest.raises(IncompleteDeletion):
        delete_consent_data(deps, end(cid, "expired"))
    proof = deps.store.read(PROOFS_CONTAINER, cid, cid)
    assert proof["attempts"] == 2 and proof["reason"] == "revoked"  # 처음 끝난 사유를 유지한다


def test_deletion_without_agents_is_incomplete_only_when_foundry_items_exist(mock_client):
    deps = make_deps(mock_client)
    cid = granted(deps)["consent_id"]
    with pytest.raises(IncompleteDeletion):
        delete_consent_data(deps, end(cid))
    conv = next(i for i in deps.store.read(PROOFS_CONTAINER, cid, cid)["items"] if i["kind"] == "conversation")
    assert (conv["outcome"], conv["detail"]) == ("failed", "agents_disabled")

    clean = granted(deps, conversations=())["consent_id"]
    assert delete_consent_data(deps, end(clean, "expired", "timer", "system"))["complete"] is True


def test_registry_absent_after_mock_restart_still_counts_as_deleted(mock_client):
    deps = make_deps(mock_client, FoundryRunner())
    cid = granted(deps)["consent_id"]
    restarted = TestClient(create_mock_app(build_demo_cases(AS_OF), SECRET, lambda: NOW))
    fresh = replace(deps, mock=HttpMockApi(client=restarted))
    delete_consent_data(fresh, end(cid))
    item = next(i for i in deps.store.read(PROOFS_CONTAINER, cid, cid)["items"] if i["kind"] == "consent_registry")
    assert (item["outcome"], item["verified"]) == ("absent", True)


def test_delete_unknown_consent_raises(mock_client):
    with pytest.raises(LookupError):
        delete_consent_data(make_deps(mock_client), end("nope"))


def test_renewal_notice_is_appended_to_consent(mock_client):
    deps = make_deps(mock_client)
    cid = granted(deps, conversations=())["consent_id"]
    result = send_consent_notice(deps, {"consent_id": cid, "kind": "renewal"})
    assert result == {"kind": "renewal", "source": "template", "title": result["title"]}
    (notice,) = deps.store.read("consents", cid, cid)["notices"]
    assert (notice["kind"], notice["source"], notice["at"]) == ("renewal", "template", NOW.isoformat())
    assert "동의 철회" in notice["body"] and notice["record"] == {"error": "agents_disabled"}


def test_deletion_notice_needs_complete_proof_and_is_sent_once(mock_client):
    deps = make_deps(mock_client, FoundryRunner())
    cid = granted(deps)["consent_id"]
    with pytest.raises(LookupError):
        send_consent_notice(deps, {"consent_id": cid, "kind": "deletion"})
    delete_consent_data(deps, end(cid))
    first = send_consent_notice(deps, {"consent_id": cid, "kind": "deletion"})
    second = send_consent_notice(deps, {"consent_id": cid, "kind": "deletion"})
    assert first == second
    notices = deps.store.read("consents", cid, cid)["notices"]
    assert [n["kind"] for n in notices] == ["deletion"] and cid in notices[0]["body"]
    assert notices[0]["source"] == "template" and notices[0]["issues"]
    assert notices[0]["record"]["conversations"] == {"conv_notice": "deleted"}


def test_consent_activities_are_registered_separately():
    assert set(CONSENT_ACTIVITIES) == {"delete_consent_data", "send_consent_notice"}


# --- 수명주기 오케스트레이션 ---

EXPIRES = START + timedelta(days=20)


def lifecycle_acts(**overrides):
    base = {
        "send_consent_notice": lambda p: {"kind": p["kind"], "source": "template", "title": "t"},
        "delete_consent_data": lambda p: {"proof_id": p["consent_id"], "complete": True, "reason": p["reason"]},
    }
    return base | overrides


@pytest.fixture
def run_lifecycle(run_orchestrator):
    def run(outcomes=(), acts=None, **start):
        payload = {"consent_id": "c-1", "expires_at": EXPIRES.isoformat(), **start}
        return run_orchestrator(consent_lifecycle, json.dumps(payload), acts or lifecycle_acts(), outcomes)

    return run


def names(ctx):
    return [(c.name, c.input.get("kind") or c.input.get("trigger")) for c in ctx.calls]


def boom(_):
    raise RuntimeError("down")


def test_lifecycle_waits_in_chunks_reminds_then_expires(run_lifecycle):
    result, ctx = run_lifecycle([("timer",)] * 5)
    assert result == {"consent_id": "c-1", "status": "deleted", "reason": "expired", "notice": "sent"}
    days = [(t.input - START).days for t in ctx.timers]
    assert days == [6, 12, 13, 19, 20]
    assert names(ctx) == [
        ("send_consent_notice", "renewal"),
        ("delete_consent_data", "timer"),
        ("send_consent_notice", "deletion"),
    ]
    delete = ctx.called("delete_consent_data")[0]
    assert delete.retry == RETRY and delete.input == {
        "consent_id": "c-1", "reason": "expired", "trigger": "timer", "requested_by": "system"
    }
    assert ctx.called("send_consent_notice")[0].retry is None
    assert ctx.custom_status == "deleted"


def test_revoke_during_wait_deletes_without_renewal(run_lifecycle):
    result, ctx = run_lifecycle([("timer",), ("revoke", {"requested_by": "borrower:demo-cafe-001"})])
    assert result["reason"] == "revoked" and result["status"] == "deleted"
    assert names(ctx) == [("delete_consent_data", "revoke"), ("send_consent_notice", "deletion")]
    assert ctx.called("delete_consent_data")[0].input["requested_by"] == "borrower:demo-cafe-001"
    assert ctx.timers[-1].cancelled


def test_remind_now_sends_renewal_once_and_expire_now_ends(run_lifecycle):
    outcomes = [("remind_now", {"requested_by": "officer"}), ("timer",), ("expire_now", json.dumps({"requested_by": "officer"}))]
    result, ctx = run_lifecycle(outcomes)
    assert result["reason"] == "expired"
    assert names(ctx) == [
        ("send_consent_notice", "renewal"),
        ("delete_consent_data", "expire_now"),
        ("send_consent_notice", "deletion"),
    ]
    assert [e.name for e in ctx.events].count("remind_now") == 2
    # 안내를 보냈으니 다음 대기는 갱신 시점이 아니라 만료 시점을 향한다.
    assert (ctx.timers[1].input - START).days == 6 and (ctx.timers[2].input - START).days == 12


def test_initial_action_runs_without_waiting(run_lifecycle):
    result, ctx = run_lifecycle(initial="revoke", requested_by="borrower:x")
    assert result["reason"] == "revoked" and not ctx.timers
    assert ctx.called("delete_consent_data")[0].input["requested_by"] == "borrower:x"


def test_initial_remind_now_then_waits_for_expiry(run_lifecycle):
    result, ctx = run_lifecycle([("timer",)] * 4, initial="remind_now")
    assert names(ctx)[0] == ("send_consent_notice", "renewal")
    assert [(t.input - START).days for t in ctx.timers] == [6, 12, 18, 20]
    assert result["status"] == "deleted"


def test_already_expired_consent_is_deleted_without_renewal(run_lifecycle):
    result, ctx = run_lifecycle(expires_at=(START - timedelta(days=1)).isoformat())
    assert names(ctx) == [("delete_consent_data", "timer"), ("send_consent_notice", "deletion")]
    assert result["reason"] == "expired" and not ctx.timers


def test_renewal_notice_failure_does_not_stop_expiry(run_lifecycle):
    acts = lifecycle_acts(send_consent_notice=boom)
    result, ctx = run_lifecycle([("timer",)] * 5, acts)
    assert result == {"consent_id": "c-1", "status": "deleted", "reason": "expired", "notice": "failed"}


def test_deletion_failure_skips_deletion_notice(run_lifecycle):
    result, ctx = run_lifecycle(initial="expire_now", acts=lifecycle_acts(delete_consent_data=boom))
    assert result == {"consent_id": "c-1", "status": "deletion_failed", "reason": "expired", "error": "down"}
    assert not ctx.called("send_consent_notice") and ctx.custom_status == "deletion_failed"


# --- 큐 처리 ---


class FakeClient:
    def __init__(self, statuses=None):
        self.statuses = statuses or {}
        self.started, self.raised = [], []

    async def get_status(self, instance_id):
        return SimpleNamespace(runtime_status=self.statuses.get(instance_id))

    async def start_new(self, name, instance_id, payload):
        self.started.append((name, instance_id, payload))
        return instance_id

    async def raise_event(self, instance_id, name, payload):
        self.raised.append((instance_id, name, payload))


def command(**fields):
    return json.dumps({"consent_id": "c-1", "action": "revoke", "requested_by": "borrower:a", **fields})


def test_lifecycle_start_starts_once():
    body = json.dumps({"consent_id": "c-1", "expires_at": EXPIRES.isoformat()})
    client = FakeClient()
    assert asyncio.run(handle_lifecycle_start(client, body)) == "consent-c-1"
    (name, instance, payload) = client.started[0]
    assert (name, instance, payload["consent_id"]) == (LIFECYCLE_ORCHESTRATOR, "consent-c-1", "c-1")
    running = FakeClient({"consent-c-1": "Running"})
    assert asyncio.run(handle_lifecycle_start(running, body)) is None and not running.started
    assert asyncio.run(handle_lifecycle_start(client, "{}")) is None


@pytest.mark.parametrize("status", ["Running", "Pending"])
def test_command_raises_event_and_withdraws_running_journey(status):
    client = FakeClient({"consent-c-1": status, "app-1": "Running"})
    store = MemoryStore()
    assert asyncio.run(handle_consent_command(client, command(application_id="app-1"), store)) == "raised"
    assert client.raised == [
        ("consent-c-1", "revoke", {"requested_by": "borrower:a"}),
        ("app-1", "consent_withdrawn", {"consent_id": "c-1", "requested_by": "borrower:a"}),
    ]


def test_command_does_not_withdraw_finished_journey_or_on_other_actions():
    client = FakeClient({"consent-c-1": "Running", "app-1": "Completed"})
    asyncio.run(handle_consent_command(client, command(application_id="app-1"), MemoryStore()))
    asyncio.run(handle_consent_command(client, command(action="remind_now", application_id="app-1"), MemoryStore()))
    assert [r[1] for r in client.raised] == ["revoke", "remind_now"]


def test_command_without_lifecycle_backfills_from_consent_doc():
    store = MemoryStore()
    store.upsert("consents", {"id": "c-1", "consent_id": "c-1", "expires_at": EXPIRES.isoformat(), "status": "granted"})
    client = FakeClient({"consent-c-1": "Failed"})
    assert asyncio.run(handle_consent_command(client, command(action="expire_now", requested_by="officer"), store)) == "started"
    (name, instance, payload) = client.started[0]
    assert (name, instance) == (LIFECYCLE_ORCHESTRATOR, "consent-c-1")
    assert (payload["initial"], payload["requested_by"]) == ("expire_now", "officer")
    assert datetime.fromisoformat(payload["expires_at"]) == EXPIRES


@pytest.mark.parametrize(("ended", "initial"), [("revoked", "revoke"), ("expired", "expire_now")])
def test_command_after_failed_deletion_restarts_lifecycle_to_retry(ended, initial):
    store = MemoryStore()
    store.upsert("consents", {"id": "c-1", "consent_id": "c-1", "expires_at": EXPIRES.isoformat(), "status": ended,
                              "deletion_complete": False})
    client = FakeClient({"consent-c-1": "Completed", "app-1": "Running"})
    cmd = command(action="remind_now", requested_by="officer", application_id="app-1")
    assert asyncio.run(handle_consent_command(client, cmd, store)) == "retried"
    (name, instance, payload) = client.started[0]
    assert (name, instance, payload["initial"], payload["requested_by"]) == (
        LIFECYCLE_ORCHESTRATOR, "consent-c-1", initial, "officer")
    assert not client.raised
    store.upsert("consents", {**store.read("consents", "c-1", "c-1"), "deletion_complete": True})
    again = FakeClient({"consent-c-1": "Completed"})
    assert asyncio.run(handle_consent_command(again, cmd, store)) == "ignored" and not again.started


def test_command_for_unknown_completed_or_invalid_is_dropped():
    assert asyncio.run(handle_consent_command(FakeClient(), command(), MemoryStore())) == "unknown"
    done = FakeClient({"consent-c-1": "Completed", "app-1": "Running"})
    assert asyncio.run(handle_consent_command(done, command(application_id="app-1"), MemoryStore())) == "ignored"
    assert not done.raised and not done.started
    assert asyncio.run(handle_consent_command(FakeClient(), command(action="extend"), MemoryStore())) == "invalid"


class PendingDeleteRaw(MemoryRaw):
    """Azure는 컨테이너 삭제를 202로 받고 나중에 지워서, 직후에는 빈 컨테이너가 남아 보인다."""

    def __init__(self):
        super().__init__()
        self.pending = set()

    def manifest(self, container):
        found = super().manifest(container)
        return [] if found is None and container in self.pending else found

    def delete_container(self, container):
        self.pending.add(container)
        return super().delete_container(container)


def test_raw_container_is_verified_while_empty_container_deletion_is_pending(mock_client):
    deps = replace(make_deps(mock_client, FoundryRunner()), raw=PendingDeleteRaw())
    cid = granted(deps)["consent_id"]

    assert delete_consent_data(deps, end(cid))["complete"] is True
    item = deps.store.read(PROOFS_CONTAINER, cid, cid)["items"][0]
    assert (item["kind"], item["outcome"], item["verified"]) == ("raw_container", "deleted", True)


class AsyncDeleteContainer:
    """컨테이너 삭제 요청은 받아 두기만 하고 blob 목록은 그대로 보이는 Azure 동작을 흉내 낸다."""

    def __init__(self, names):
        self.blobs = {n: b"{}" for n in names}
        self.delete_requested = False

    def list_blobs(self):
        return [SimpleNamespace(name=n) for n in self.blobs]

    def download_blob(self, name):
        return SimpleNamespace(readall=lambda: self.blobs[name])

    def delete_blob(self, name):
        del self.blobs[name]

    def delete_container(self):
        self.delete_requested = True


@pytest.fixture
def azure_errors(monkeypatch):
    """개발 venv에는 azure-core가 없어 함수 앱 의존성의 예외 모듈만 대신 넣는다."""
    try:
        import azure.core.exceptions  # noqa: F401
    except ModuleNotFoundError:
        errors = ModuleType("azure.core.exceptions")
        errors.ResourceNotFoundError = type("ResourceNotFoundError", (Exception,), {})
        monkeypatch.setitem(sys.modules, "azure.core", ModuleType("azure.core"))
        monkeypatch.setitem(sys.modules, "azure.core.exceptions", errors)


def test_blob_store_deletes_blobs_so_pending_container_is_empty_at_once(azure_errors):
    container = AsyncDeleteContainer(["pg.json", "tax.json"])
    store = BlobRawStore.__new__(BlobRawStore)
    store._svc = SimpleNamespace(
        get_container_client=lambda name: container,
        delete_container=lambda name: container.delete_container(),
    )

    assert store.delete_container("consent-x") is True
    assert container.delete_requested and store.manifest("consent-x") == []


class FoundryLike(FoundryRunner):
    """실제 Foundry처럼 이미 지운 컨테이너를 다시 지우면 404라 False, 첫 대화 확인은 일시 실패(None)."""

    def __init__(self):
        super().__init__()
        self.checks = 0

    def conversation_exists(self, conversation_id):
        self.checks += 1
        return None if self.checks == 1 else False

    def delete_container(self, container_id):
        if container_id in self.deleted_containers:
            return False
        return super().delete_container(container_id)


def test_retry_keeps_items_verified_on_an_earlier_attempt(mock_client):
    runner = FoundryLike()
    deps = make_deps(mock_client, runner)
    cid = granted(deps)["consent_id"]
    with pytest.raises(IncompleteDeletion, match="conversation:conv_a"):
        delete_consent_data(deps, end(cid))

    assert delete_consent_data(deps, end(cid))["complete"] is True
    assert runner.deleted_containers == ["cntr_1"]
    proof = deps.store.read(PROOFS_CONTAINER, cid, cid)
    assert proof["attempts"] == 2 and all(i["verified"] for i in proof["items"])


class RevokeOnWriteRaw(MemoryRaw):
    """데이터 조립 에이전트가 끝나 쓰기 직전에 철회 삭제가 먼저 끝난 경우를 흉내 낸다."""

    def __init__(self):
        super().__init__()
        self.before_first_write = None

    def put_json(self, container, name, value):
        hook, self.before_first_write = self.before_first_write, None
        if hook:
            hook()
        super().put_json(container, name, value)


def test_raw_data_written_after_deletion_is_removed_and_assembly_stops(mock_client):
    deps = replace(make_deps(mock_client), raw=RevokeOnWriteRaw())
    consent = consent_for(deps)
    cid = consent["consent_id"]
    deps.raw.before_first_write = lambda: delete_consent_data(deps, end(cid))

    with pytest.raises(ConsentEnded):
        assemble_data(
            deps,
            {"application_id": "app-1", "consent_id": cid, "token": consent["token"], "scopes": consent["scopes"]},
        )
    assert deps.raw.manifest(f"consent-{cid}") is None
    late = deps.store.read("consents", cid, cid)["late_cleanup"]
    assert [(i["kind"], i["target"], i["outcome"]) for i in late] == [("raw_container", f"consent-{cid}", "deleted")]
