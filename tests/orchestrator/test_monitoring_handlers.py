import json
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from agents.collections import COLLECTIONS_AGENT, CollectionsContext, RepaymentCase
from agents.data_assembly import SourceError
from agents.early_warning import _TRIGGER_TEXT, EW_AGENT
from agents.escalation import plan_link, stage_message
from agents.features import monthly_installment
from agents.incident import new_watch
from agents.monitoring import WEEKS
from agents.repayment import item_key
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
    TOPIC_REPAYMENT,
    handle_flow_update,
    handle_replay,
    handle_restructure_decision,
    handle_repayment_updated,
    process_week,
)
from mockapis.app import create_app as create_mock_app
from mockapis.demo import FlowStore, build_demo_cases
from shared.repayment import LENDER_DEBIT, due_dates
from shared.usd_source import UsdMarketFlow

AS_OF = date(2026, 11, 2)
SECRET = b"s" * 32
KEY = b"k" * 32
NOW = datetime(2026, 11, 2, 9, 0, tzinfo=KST)
LOAN = "app-1"
FIRST_DUE = due_dates(AS_OF, WEEKS * 7)[0]  # 2026-12-02, 5주차
CAUSE_SIGNAL = {
    "sales_slump": "inflow_drop",
    "settlement_hold": "settlement_hold",
    "return_spike": "return_spike",
    "cost_surge": "cost_surge",
}


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

    def replace_batch(self, container, partition_key, documents):
        assert all(d["loan_id"] == partition_key for d in documents)
        for doc in documents:
            current = self.read(container, doc["id"], partition_key)
            if (current or {}).get("_etag") != doc.get("_etag"):
                return False
        for doc in documents:
            self.upsert(container, doc)
        return True


class MemoryQueue:
    def __init__(self):
        self.sent = []

    def send(self, queue, message):
        self.sent.append((queue, message))


class MemoryRaw:
    def open_container(self, container):
        pass

    def __init__(self):
        self.blobs = {}

    def put_json(self, container, name, value):
        self.blobs[(container, name)] = json.loads(json.dumps(value))

    def get_json(self, container, name):
        return self.blobs.get((container, name))


class Outbox:
    def __init__(self, fail_topics=()):
        self.sent, self.fail_topics, self.delivered = [], set(fail_topics), 0

    def publish(self, topic, message, message_id, session_id=None):
        if topic in self.fail_topics:
            raise RuntimeError("sb down")
        self.sent.append((topic, message, message_id, session_id))

    def bodies(self, topic):
        return [json.dumps(m) for t, m, _, _ in self.sent if t == topic]


class ScriptedAgents:
    """EW: new_debt 트리거가 있으면 원인 후보를 붙인 경보를 올린다. Collections: 상담 안내 + 재조정 상담 제안."""

    def __init__(self):
        self.calls = []

    def run(self, spec, input_text, handlers):
        self.calls.append(spec.name)
        if spec.name == EW_AGENT:
            base = handlers["get_baseline"]({})
            week = base["current_week"]
            decision = "no_alert"
            if "new_debt" in base["triggers"]:
                candidates = base["cause_candidates"]
                primary = candidates[0] if candidates else None
                signals = [
                    {
                        "kind": "loan_stacking",
                        "text": "고금리 상환이 새로 나타났다.",
                        "metric_ids": [f"W:{week}:high_cost_lender"],
                        "txn_ids": [],
                    }
                ]
                if primary is not None and primary["cause"] != "high_cost_borrowing":
                    signals.append(
                        {
                            "kind": CAUSE_SIGNAL[primary["cause"]],
                            "text": "주원인 지표가 먼저 움직였다.",
                            "metric_ids": list(primary["evidence_ids"]),
                            "txn_ids": [],
                        }
                    )
                out = handlers["raise_alert"](
                    {
                        "severity": "high",
                        "summary": "실행 직후 고금리 대부 상환이 시작됐다.",
                        "recommended_action": "contact_borrower",
                        "signals": signals,
                        "primary_cause": primary["cause"] if primary else "",
                        "secondary_causes": [c["cause"] for c in candidates[1:3]],
                    }
                )
                assert out["accepted"], out
                decision = "alert"
            output = {"decision": decision, "reason": "조사 결과다."}
        else:
            out = handlers["propose"](
                {"script_id": "hardship_offer", "hardship_category": "debt_overload", "hardship_note": "대출 중첩",
                 "reason": "누적 미납"}
            )
            assert out == {"accepted": True}, out
            output = {"decision": "proposed", "reason": "상담 안내와 재조정 상담을 제안했다."}
        return AgentRun(spec.name, "1", f"conv-{len(self.calls)}", spec.output_model.model_validate(output), [], 1, 1)

    def delete_container(self, container_id):
        return True


class HighAgents(ScriptedAgents):
    """EW: 트리거가 새로 생기는 주마다 경보를 올린다. 심각도는 severities를 차례로 쓰고, 다 쓰면 high다."""

    def __init__(self, severities=()):
        super().__init__()
        self.severities = list(severities)

    def run(self, spec, input_text, handlers):
        if spec.name != EW_AGENT:
            return super().run(spec, input_text, handlers)
        self.calls.append(spec.name)
        base = handlers["get_baseline"]({})
        week, candidates = base["current_week"], base["cause_candidates"]
        primary = candidates[0] if candidates else None
        signals = []
        if primary is not None:
            signals.append({"kind": CAUSE_SIGNAL.get(primary["cause"], "loan_stacking"), "text": "주원인 지표가 먼저 움직였다.",
                            "metric_ids": list(primary["evidence_ids"]), "txn_ids": []})
        for trigger in base["triggers"]:
            kind, metric, text = _TRIGGER_TEXT[trigger]
            signals.append({"kind": kind, "text": text, "metric_ids": [metric.format(week=week)], "txn_ids": []})
        severity = self.severities.pop(0) if self.severities else "high"
        out = handlers["raise_alert"]({
            "severity": severity,
            "summary": "자금 흐름이 나빠졌다.",
            "check_action": "다음 상환일 전 계좌 잔고 확인이 필요합니다.",
            "recommended_action": "contact_borrower",
            "signals": signals[:6],
            "primary_cause": primary["cause"] if primary else "",
            "secondary_causes": [c["cause"] for c in candidates[1:3]],
        })
        assert out["accepted"], out
        output = {"decision": "alert", "reason": "조사 결과다."}
        return AgentRun(spec.name, "1", f"conv-{len(self.calls)}", spec.output_model.model_validate(output), [], 1, 1)


@pytest.fixture(scope="module")
def mock_app():
    cases = build_demo_cases(AS_OF)
    return create_mock_app(cases, SECRET, lambda: NOW, flows=FlowStore(cases, AS_OF))


class RegistrationStore(MemoryStore):
    def pending_raw_repairs(self):
        return [i for (c, i), d in self.docs.items() if c == "consents" and (d.get("deletion_notice_pending") or d.get("raw_repair_pending")
                or d.get("raw_write_count") and d.get("status") in ("revoked", "expired"))]

    def prior_contact_slots(self, borrower, lo, hi):
        ids = {i for (c, i), d in self.docs.items() if c == LOANS and d.get("applicant_id") == borrower}
        return [dict(loan_id=d["loan_id"], **{k: s[k] for k in ("ref", "alert_id", "by", "on") if k in s})
                for (c, _), d in self.docs.items() if c == ALERTS and d.get("loan_id") in ids
                for s in d.get("contacts", []) if s["status"] == "sent" and lo <= s["on"] <= hi]

    def create_if_absent(self, container, document):
        key = (container, document["id"])
        self.docs.setdefault(key, deepcopy(document))
        self.etags.setdefault(key, 1)


def make_deps(mock_app, events=None, agents=None):
    return Deps(
        store=RegistrationStore(),
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
    registration = register_monitoring(
        deps,
        {
            "application_id": LOAN,
            "applicant_id": applicant_id,
            "consent_id": consent["consent_id"],
            "decision": "approve",
            "amount_krw": 3_300_000,
            "tenor_months": 12,
            "decided_at": NOW.isoformat(),
            "underwriting": {k: uw[k] for k in ("features", "monthly", "settlement_profile")},
        },
    )
    assert registration == {"loan_id": LOAN}
    return consent


def replay(deps, **fields):
    return handle_replay(deps, json.dumps({"loan_id": LOAN} | fields))


def pump(deps, outbox, topics=(TOPIC_FLOW, TOPIC_REPAYMENT)):
    """Service Bus 흉내: 아직 전달하지 않은 메시지를 발행 순서대로 전달한다(처리 중 새로 발행된 메시지 포함)."""
    while outbox.delivered < len(outbox.sent):
        topic, message, _, _ = outbox.sent[outbox.delivered]
        outbox.delivered += 1
        if topic == TOPIC_FLOW and topic in topics:
            handle_flow_update(deps, json.dumps(message))
        elif topic == TOPIC_REPAYMENT and topic in topics:
            handle_repayment_updated(deps, json.dumps(message))


def flows(outbox):
    return [m for m in outbox.sent if m[0] == TOPIC_FLOW]


def resume_until_done(deps, replay_id, deliver=lambda: None):
    """미납이 다시 나오면 또 멈춘다. 대기 항목 처리·재개를 반복해 끝까지 돌리고 추가로 멈춘 횟수를 돌려준다."""
    pauses = 0
    while (mon := loan(deps)["monitoring"])["status"] in ("paused", "awaiting_customer"):
        pauses += 1
        settle_holds(deps)
        assert replay(deps, action="resume", replay_id=replay_id)["from_week"] == mon["paused_week"] + 1
        deliver()
    return pauses


def settle_holds(deps):
    """멈춘 재생의 대기 항목을 순서대로 푼다(추심 승인 → 재조정 결정 → 고객 플랜 응답은 유지). 끝나면 재개 대기다."""
    while (mon := loan(deps)["monitoring"])["status"] in ("paused", "awaiting_customer"):
        hold, case_id = mon["holds"][0], mon.get("pause_case_id") or mon.get("pause_alert_id")
        if hold == "collections_proposal":
            assert decide_collections(deps, case_id) is True
        elif hold == "customer_response":
            offer_id = loan(deps)["repayment"]["offer"]["offer_id"]
            assert handle_restructure_decision(deps, json.dumps(
                {"kind": "plan_choice", "loan_id": LOAN, "offer_id": offer_id, "option": "keep"})) is True
        else:
            assert hold == "restructure"
            assert decide_restructure(deps, case_id) is True
    assert loan(deps)["monitoring"]["status"] == "awaiting_resume"


def decide_restructure(deps, alert_id, decision="approve"):
    body = {"loan_id": LOAN, "alert_id": alert_id, "decision": decision, "officer_id": "officer-1", "reason": "3개월 유예"}
    return handle_restructure_decision(deps, json.dumps(body))


def decide_collections(deps, case_id, decision="approve", **fields):
    body = {"kind": "collections", "loan_id": LOAN, "case_id": case_id, "decision": decision,
            "officer_id": "officer-1"} | fields
    return handle_restructure_decision(deps, json.dumps(body))


def officer_contacts(deps, replay_id):
    return [c for c in contacts(deps, replay_id) if c["by"] in ("agent", "fallback", "officer")]


def loan(deps):
    return deps.store.read(LOANS, LOAN, LOAN)


def test_contact_cap_across_currencies(mock_app):
    deps = make_deps(mock_app)
    from shared.contracts import USD_V1
    borrower = "same-borrower"
    deps.store.upsert(LOANS, dict(id="old", applicant_id=borrower))
    deps.store.upsert(ALERTS, dict(id="contacts-old", loan_id="old", contacts=[
        dict(ref=f"old-{i}", by="officer", on="2026-11-02", status="sent") for i in range(7)]))
    deps.store.upsert(LOANS, dict(USD_V1.model_dump(), id=LOAN, applicant_id=borrower))
    contact = dict(ref="new", by="reminder", on="2026-11-03", status="sent")
    assert hasattr(monitoring_module, "_reserve_contact")
    monitoring_module._reserve_contact(deps, LOAN, contact)
    assert (contact["status"], contact["reason"]) == ("deferred", "weekly_limit")


def test_cross_loan_slot_race_and_recovery_expose_no_case_or_amount(mock_app, monkeypatch):
    from shared.contracts import USD_V1
    deps = make_deps(mock_app)
    deps.store.upsert(LOANS, dict(id="legacy", applicant_id="same"))
    deps.store.upsert(LOANS, dict(USD_V1.model_dump(), id=LOAN, applicant_id="same"))
    def send(lid, ref):
        entry = dict(ref=ref, by="officer", on="2026-11-02", status="sent")
        monitoring_module._reserve_contact(deps, lid, entry)
        return entry["status"]
    for i in range(6):
        assert send("legacy", str(i)) == "sent"
    real = deps.store.replace_batch
    def contend(*args):
        monkeypatch.setattr(deps.store, "replace_batch", real)
        assert send(LOAN, "rc-usd-i1-b49999") == "sent"
        return real(*args)
    monkeypatch.setattr(deps.store, "replace_batch", contend)
    assert send("legacy", "last-slot") == "deferred"
    assert send(LOAN, "rc-usd-i1-b49999") == "sent"  # retry of the winning slot
    records = [doc for (c, k), doc in deps.store.docs.items() if k.startswith("cap-")]
    assert records and "b49999" not in str(records)


def test_pre_ref_receipts_count_once_across_currencies(mock_app):
    from shared.contracts import USD_V1

    deps = make_deps(mock_app)
    borrower = "seller-bakery-20"
    deps.store.upsert(LOANS, dict(id="old", applicant_id=borrower))
    deps.store.upsert(LOANS, dict(USD_V1.model_dump(), id=LOAN, applicant_id=borrower))
    old = dict(alert_id="ew-one", by="agent", on="2026-11-02", at=NOW.isoformat(),
               status="sent", script_id="payment_reminder", reason=None)
    # One repeated historical receipt, plus five distinct modern receipts; the identical
    # string in the modern ref namespace denotes a different contact.
    current = [{k: v for k, v in old.items() if k != "alert_id"} | {"ref": ref}
               for ref in ("ew-one", "r2", "r3", "r4", "r5")]
    deps.store.upsert(ALERTS, dict(id="contacts-history", loan_id="old", contacts=[old, old, *current]))
    def attempt(ref):
        contact = dict(ref=ref, by="officer", on=old["on"], status="sent")
        monitoring_module._reserve_contact(deps, LOAN, contact)
        return contact
    assert attempt("seventh")["status"] == "sent"
    assert attempt("seventh")["status"] == "sent"  # Redelivery consumes no additional slot.
    assert attempt("eighth")["reason"] == "weekly_limit"


@pytest.mark.parametrize("filename", ["flow.json", "pg_flow.json"])
@pytest.mark.parametrize("end_before_put", [True, False])
def test_replay_stops_if_raw_write_follows_deletion_proof(mock_app, monkeypatch, filename, end_before_put):
    from journey.consent import PROOFS_CONTAINER, delete_consent_data
    from tests.orchestrator.test_consent_lifecycle import MemoryRaw as DeletableRaw

    deps = replace(make_deps(mock_app, events=Outbox()), raw=DeletableRaw())
    approved_loan(deps)
    put = deps.raw.put_json
    ended = []
    def end_during_write(container, name, value):
        if not end_before_put:
            put(container, name, value)
        if name == filename:
            cid = container.removeprefix("consent-")
            reason, trigger = ("revoked", "revoke") if end_before_put else ("expired", "timer")
            result = delete_consent_data(deps, dict(consent_id=cid, reason=reason, trigger=trigger,
                                                    requested_by="test"))
            assert result["complete"] and deps.raw.manifest(container) is None
            ended.append(cid)
        if end_before_put:
            put(container, name, value)
    monkeypatch.setattr(deps.raw, "put_json", end_during_write)
    assert replay(deps) is None
    cid, = ended
    assert deps.store.read(PROOFS_CONTAINER, cid, cid)["complete"]
    assert deps.raw.manifest(f"consent-{cid}") is None
    assert deps.store.read("consents", cid, cid)["late_cleanup"][-1]["outcome"] == (
        "deleted" if end_before_put else "absent")
    assert loan(deps)["monitoring"]["status"] == "stopped" and not flows(deps.events)


@pytest.mark.parametrize("filename", ["flow.json", "pg_flow.json"])
def test_late_raw_cleanup_recovered_by_timer(mock_app, monkeypatch, filename):
    import function_app
    from journey.consent import PROOFS_CONTAINER, delete_consent_data, send_consent_notice
    from tests.orchestrator.test_consent_lifecycle import MemoryRaw as DeletableRaw

    deps = replace(make_deps(mock_app, events=Outbox()), raw=DeletableRaw())
    approved_loan(deps)
    put, delete = deps.raw.put_json, deps.raw.delete_container
    failures, original = [], {}

    def revoke_then_write(container, name, value):
        if name == filename:
            cid = container.removeprefix("consent-")
            delete_consent_data(deps, dict(consent_id=cid, reason="revoked", trigger="revoke", requested_by="test"))
            send_consent_notice(deps, dict(consent_id=cid, kind="deletion"))
            original.update(deps.store.read(PROOFS_CONTAINER, cid, cid))
            failures.extend(["unavailable", "not_deleted"])
            monkeypatch.setattr(deps.mock, "revoke", lambda *_: pytest.fail("repeated registry deletion"))
        put(container, name, value)

    def intermittent(container):
        if failures:
            if failures.pop(0) == "unavailable":
                raise OSError("storage unavailable")
            return True  # Accepted but the blob is still visible.
        return delete(container)

    monkeypatch.setattr(deps.raw, "put_json", revoke_then_write)
    monkeypatch.setattr(deps.raw, "delete_container", intermittent)
    assert replay(deps) is None
    cid = original["id"]
    def proof():
        return deps.store.read(PROOFS_CONTAINER, cid, cid)
    def consent():
        return deps.store.read("consents", cid, cid)
    assert loan(deps)["monitoring"]["status"] == "stopped" and not flows(deps.events)
    assert not proof()["complete"] and not consent()["deletion_complete"]
    assert not next(i for i in proof()["items"] if i["kind"] == "raw_container")["verified"]
    events = list(deps.events.sent)
    monkeypatch.setattr(function_app, "deps", lambda: deps)
    function_app.repair_consent_deletions(None)
    assert not proof()["complete"] and not consent()["deletion_complete"]
    assert deps.raw.manifest(f"consent-{cid}")
    replace_doc = deps.store.replace
    def interrupt_checkpoint(container, doc, etag):
        if container == "consents" and doc.get("deletion_complete"):
            raise OSError("crash after proof checkpoint")
        return replace_doc(container, doc, etag)
    with monkeypatch.context() as m:
        m.setattr(deps.store, "replace", interrupt_checkpoint)
        function_app.repair_consent_deletions(None)
    assert proof()["complete"] and not consent()["deletion_complete"]
    monkeypatch.setattr(deps.raw, "delete_container", lambda *_: pytest.fail("repeated verified deletion"))
    function_app.repair_consent_deletions(None)
    assert proof()["complete"] and consent()["deletion_complete"]
    assert deps.raw.manifest(f"consent-{cid}") is None
    repaired = proof()
    function_app.repair_consent_deletions(None)
    assert proof() == repaired and repaired["manifest"] == original["manifest"]
    assert [n["kind"] for n in consent()["notices"]] == ["deletion"]
    assert deps.events.sent == events and loan(deps)["monitoring"]["status"] == "stopped"


def test_repair_marker_stops_replay_if_proof_checkpoint_is_unavailable(mock_app, monkeypatch):
    import function_app
    from types import SimpleNamespace
    from console.adapters import CosmosPrivacy
    from journey.consent import PROOFS_CONTAINER, delete_consent_data, send_consent_notice
    from tests.orchestrator.test_consent_lifecycle import MemoryRaw as DeletableRaw

    deps = replace(make_deps(mock_app, events=Outbox()), raw=DeletableRaw())
    approved_loan(deps)
    put, replace_doc = deps.raw.put_json, deps.store.replace
    def fail_proof(container, doc, etag):
        if container == PROOFS_CONTAINER:
            raise OSError("checkpoint unavailable")
        return replace_doc(container, doc, etag)
    def revoke_then_write(container, name, value):
        cid = container.removeprefix("consent-")
        delete_consent_data(deps, dict(consent_id=cid, reason="revoked", trigger="revoke", requested_by="test"))
        monkeypatch.setattr(deps.store, "replace", fail_proof)
        put(container, name, value)
    monkeypatch.setattr(deps.raw, "put_json", revoke_then_write)
    assert replay(deps) is None
    cid = loan(deps)["monitoring"]["consent_id"]
    assert loan(deps)["monitoring"]["status"] == "stopped" and not flows(deps.events)
    assert not deps.store.read("consents", cid, cid)["deletion_complete"]
    assert deps.raw.manifest(f"consent-{cid}")
    def reader(container):
        return SimpleNamespace(read_item=lambda item, partition_key: deps.store.read(container, item, partition_key))
    assert deps.store.read(PROOFS_CONTAINER, cid, cid)["complete"]
    assert not CosmosPrivacy(reader("consents"), reader(PROOFS_CONTAINER), None).get_proof(cid)["complete"]
    with pytest.raises(LookupError):
        send_consent_notice(deps, {"consent_id": cid, "kind": "deletion"})
    monkeypatch.setattr(deps.store, "replace", replace_doc)
    monkeypatch.setattr(function_app, "deps", lambda: deps)
    function_app.repair_consent_deletions(None)
    assert deps.store.read(PROOFS_CONTAINER, cid, cid)["complete"]
    assert deps.store.read("consents", cid, cid)["deletion_complete"]
    assert not deps.raw.manifest(f"consent-{cid}")


@pytest.mark.parametrize("filename", ["flow.json", "pg_flow.json"])
def test_failed_write_intent_never_uploads(mock_app, monkeypatch, filename):
    deps = make_deps(mock_app, events=Outbox())
    approved_loan(deps)
    fetch, replace_doc = deps.mock.fetch, deps.store.replace
    def fail_after_fetch(source, token, terms):
        value = fetch(source, token, terms)
        if source + ".json" == filename:
            def fail_marker(container, doc, etag):
                if container == "consents":
                    raise OSError("intent unavailable")
                return replace_doc(container, doc, etag)
            monkeypatch.setattr(deps.store, "replace", fail_marker)
        return value
    monkeypatch.setattr(deps.mock, "fetch", fail_after_fetch)
    with pytest.raises(OSError, match="intent unavailable"):
        replay(deps)
    assert not any(name == filename for _, name in deps.raw.blobs)
    assert not flows(deps.events)


def test_abandoned_upload_keeps_intent_until_ended_cleanup(mock_app, monkeypatch):
    import function_app
    from journey.consent import delete_consent_data
    from tests.orchestrator.test_consent_lifecycle import MemoryRaw as DeletableRaw

    deps = replace(make_deps(mock_app, events=Outbox()), raw=DeletableRaw())
    approved_loan(deps)
    put = deps.raw.put_json
    class WorkerLost(BaseException):
        pass
    def die_after_upload(container, name, value):
        put(container, name, value)
        raise WorkerLost()
    monkeypatch.setattr(deps.raw, "put_json", die_after_upload)
    with pytest.raises(WorkerLost):
        replay(deps)
    container, = [c for c, n in deps.raw.blobs if n == "flow.json"]
    cid = container.removeprefix("consent-")
    def consent():
        return deps.store.read("consents", cid, cid)
    assert consent().get("raw_write_count") == 1
    monkeypatch.setattr(function_app, "deps", lambda: deps)
    function_app.repair_consent_deletions(None)
    assert deps.raw.manifest(container) and consent()["raw_write_count"] == 1
    assert delete_consent_data(deps, dict(consent_id=cid, reason="revoked", trigger="revoke",
                                         requested_by="test"))["complete"]
    assert not deps.raw.manifest(container) and not consent().get("raw_write_count")


@pytest.mark.parametrize("source_name,code", [("pg_flow", "invalid_usd_pg_flow"), ("flow", "invalid_account_flow"),
                                              ("rejected", "account_flow_unavailable")])
def test_invalid_usd_flow_never_exposes_source_values(mock_app, monkeypatch, caplog, source_name, code):
    from shared.contracts import USD_V1

    deps = make_deps(mock_app, events=Outbox())
    deps.store.upsert(LOANS, dict(USD_V1.model_dump(), id=LOAN, application_id=LOAN, applicant_id="seller-bakery-20",
                                  baseline={"avg_weekly_revenue_cents": 120000, "cash_buffer_days": 12},
                                  industry="Food", decision="approve",
                                  underwriting_consent_id="prior", amount_cents=500000, tenor_months=6))
    fetch = deps.mock.fetch
    sentinel = "SYNTHETIC-PRIVATE-PG-VALUE"
    def corrupt(source, token, terms):
        if source == "flow" and source_name == "rejected":
            from agents.data_assembly import SourceError
            raise SourceError(422, sentinel)
        if source == source_name:
            return {**USD_V1.model_dump(), "orders": sentinel, "settlements": []} if source == "pg_flow" else [sentinel]
        return fetch(source, token, terms)
    monkeypatch.setattr(deps.mock, "fetch", corrupt)
    with pytest.raises(ValueError) as exc:
        replay(deps, **USD_V1.model_dump())
    mon = loan(deps)["monitoring"]
    assert sentinel not in caplog.text and sentinel not in json.dumps(mon)
    assert str(exc.value) == code and exc.value.__context__ is None
    assert mon["status"] == "failed" and code in mon["error"] and not flows(deps.events)
    from console.app import create_app
    from consolepreview.fixtures import build_deps
    ui = build_deps()
    ui.monitoring.loans[LOAN] = loan(deps)
    page = TestClient(create_app(ui)).get(f"/loans/{LOAN}")
    assert page.status_code == 200 and code in page.text and sentinel not in page.text


def test_usd_worker_partial_redelivery_and_late_payoff(mock_app):
    from agents.repayment import usd_schedule
    from shared.contracts import USD_V1, UsdTransaction
    from shared.repayment import USD_DEBIT
    deps, meta = make_deps(mock_app), USD_V1.model_dump()
    led = usd_schedule(AS_OF, 150001, 3)
    doc = dict(meta, id=LOAN, applicant_id="borrower", monitoring={"alert_ids": [], "replay_id": "rp-usd"},
               repayment=led)
    deps.store.upsert(LOANS, doc)
    item = led["schedule"][0]
    due = date.fromisoformat(item["due_on"])
    penny = UsdTransaction(**meta, txn_id="one", loan_id=LOAN, scheduled_item_key="i1", amount_cents=-1,
                           balance_after_cents=50000, booked_on=due, description=USD_DEBIT, txn_type_code="AUTO_DEBIT")
    def post(txns, day):
        monitoring_module._repayment_week(deps, LOAN, "rp-usd", doc, txns, day, day, 5, {})
    post([penny], due)
    cid = item["case_id"]
    assert (item["paid_cents"], item["balance_cents"], item["status"]) == (1, 49999, "partial")
    snapshot = deepcopy(doc)
    post([penny], due)
    assert doc == snapshot
    later = due + timedelta(days=1)
    post([penny.model_copy(update=dict(txn_id="rest", booked_on=later, amount_cents=-49999,
                                       scheduled_item_key=None))], later)
    assert (item["paid_cents"], item["balance_cents"], item["status"]) == (50000, 0, "paid")
    assert deps.store.read(ALERTS, cid, LOAN)["status"] == "paid"
    assert monitoring_module.run_collections(deps, LOAN, cid) is None


def test_usd_final_week_resolved_before_hold_does_not_publish_week_fourteen(mock_app):
    from shared.contracts import USD_V1
    deps = make_deps(mock_app, events=Outbox())
    deps.store.upsert(LOANS, dict(USD_V1.model_dump(), id=LOAN, monitoring=dict(status="replaying",
        processed_week=13, replay_id="rp-final", weeks=[{"case_id": "rc-final"}])))
    deps.store.upsert(ALERTS, dict(id="rc-final", loan_id=LOAN, status="rejected", proposal={"status": "rejected"}))
    monitoring_module._continue(deps, LOAN, "rp-final", 13, "rc-final")
    assert not deps.events.sent and loan(deps)["monitoring"]["status"] == "completed"


class StageAgents:
    """USD EW: get_baseline의 기본 단계를 그대로 쓰고, medium·high면 raise_alert(영어, 첫 원인 후보·신호 근거)를 먼저 부른다.
    호출 수를 센다. Collections는 오프라인 폴백이다."""

    def __init__(self):
        self.calls = 0

    def stage(self, base):
        return base["base_stage"]

    def adjust(self, base, stage):
        return ""

    def run(self, spec, input_text, handlers):
        if spec.name != EW_AGENT:
            raise RuntimeError("Offline collections fallback")
        self.calls += 1
        base = handlers["get_baseline"]({})
        stage = self.stage(base)
        if stage in ("medium", "high"):
            candidates = base["cause_candidates"]
            cause = candidates[0] if candidates else None
            signals = [dict(kind=CAUSE_SIGNAL.get(cause["cause"], "other"), text="The main cause metric moved first.",
                            metric_ids=list(cause["evidence_ids"]), txn_ids=[])] if cause else []
            signals += [dict(kind="other", text="The detector flagged this signal.", metric_ids=list(s["metric_ids"]),
                             txn_ids=[]) for s in base["signals"] if s.get("metric_ids")]
            out = handlers["raise_alert"](dict(
                severity=stage, summary="Cash flow is under pressure.", check_action="Check available funds.",
                recommended_action="officer_review", primary_cause=cause["cause"] if cause else "",
                secondary_causes=[c["cause"] for c in candidates[1:3]], signals=signals[:6]))
            assert out["accepted"], out
        return AgentRun(spec.name, "1", f"conv-{self.calls}", spec.output_model.model_validate(dict(
            stage=stage, why_now="Settlement data moved first and bank receipts followed.",
            outlook="An officer should review the evidence before the next update.",
            adjust_reason=self.adjust(base, stage),
            reason="The stage follows the detector signals.")), [], 1, 1)


class EnglishHigh(StageAgents):
    """check_stage가 받아 주는 가장 높은 단계: 은행 강한 신호가 있으면 high, 아니면 기본 단계."""

    def stage(self, base):
        bank_strong = any(s["source"] == "bank" and s["strength"] == "strong" for s in base["signals"])
        return "high" if bank_strong else base["base_stage"]


class HoldMedium(StageAgents):
    """높음 기본 단계를 마켓 근거(W:n:mkt_*)를 인용해 중간으로 한 단계 낮춘다(check_stage 통과). 그 밖에는 기본 단계."""

    def stage(self, base):
        return "medium" if base["base_stage"] == "high" else base["base_stage"]

    def adjust(self, base, stage):
        if stage == base["base_stage"]:
            return ""
        market = next(m for s in base["signals"] if s["source"] == "market" for m in s["metric_ids"])
        return f"The marketplace grade in {market} still lets the seller sell, so the shortfall is checked first."


# 스트레스(높음 아님) 변형은 HoldMedium으로 사건을 중간에 묶어 추심 제안·7일 7회 경로를 본다. 에이전트 없이도
# 스펙 §5.4 폴백이 스스로 에스컬레이션하며, 그 경로는 test_usd_agents_disabled_still_escalates가 본다.
@pytest.mark.parametrize("seller,stress,high", [("seller-bakery-20", False, False),
    ("seller-dailylook-01", True, False), ("seller-dailylook-01", True, True)])
def test_usd_officer_to_servicing(run_journey, seller, stress, high):
    import asyncio
    from functools import partial
    from unittest.mock import AsyncMock
    from journey.activities import ACTIVITIES
    from journey.adapters import HttpPolicyApi
    from journey.entry import handle_decision, handle_start
    from policy.app import create_app
    from policy.rules import load_rules
    from shared.contracts import USD_V1
    from shared.product_context import validate_usd_evidence

    day = date(2026, 11, 24)
    sellers = build_demo_cases(day)
    outbox = Outbox()
    now = NOW.replace(day=day.day)
    deps = make_deps(create_mock_app(sellers, SECRET, lambda: now, flows=FlowStore(sellers, day)), outbox)
    deps = replace(deps, policy=HttpPolicyApi(client=TestClient(create_app(load_rules()))), clock=lambda: now)
    meta = USD_V1.model_dump()
    acts = {k: partial(f, deps) for k, f in ACTIVITIES.items()}
    acts["assess_credit"] = lambda _: {"assessment": {"review": {"passed": True}}}
    decision = dict(meta, decision="approve", amount_cents=500000, officer_id="test-officer")
    durable = AsyncMock()
    durable.get_status.return_value = None
    durable.start_new.return_value = LOAN
    request = dict(meta, application_id=LOAN, applicant_id=seller, requested_amount_cents=500000,
                   requested_tenor_months=6)
    assert asyncio.run(handle_start(durable, json.dumps(request))) == LOAN
    durable.get_status.return_value = type("Running", (), {"runtime_status": "Running"})()
    assert asyncio.run(handle_decision(durable, json.dumps(dict(application_id=LOAN, decision=decision))))
    result, ctx = run_journey(durable.start_new.call_args.args[2], acts, [("event", {**decision, "currency": "KRW"}),
                              ("event", durable.raise_event.call_args.args[2])], start=now)
    assert result["status"] == "approved" and len(ctx.saved[-1]["decision_attempts"]) == 1
    assert loan(deps)["amount_cents"] == 500000
    registration = ctx.called("register_monitoring")[0].input
    assert register_monitoring(deps, registration) == {"loan_id": LOAN}
    with pytest.raises(ValueError, match="registration"):
        register_monitoring(deps, registration | {"amount_cents": 499999})
    saved = deps.store.read("applications", LOAN, LOAN)
    deps.store.upsert("applications", {**saved, "officer_decision": None, "status": "awaiting_officer"})
    with pytest.raises(ValueError, match="approval"):
        register_monitoring(deps, registration)
    deps.store.upsert("applications", saved)
    assert replay(deps) is None and loan(deps)["monitoring"] is None
    if stress and not high:
        deps.store.upsert(LOANS, dict(id="legacy-usd-peer", applicant_id=seller))
        deps.store.upsert(ALERTS, dict(id="contacts-old", loan_id="legacy-usd-peer", contacts=[
            dict(ref=f"old-{i}", by="officer", on="2027-02-18", status="sent") for i in range(7)]))
    started = replay(deps, **meta)
    rid = started["replay_id"]
    if high:
        deps = replace(deps, agents=EnglishHigh())
    elif stress:
        deps = replace(deps, agents=HoldMedium())
    pump(deps, outbox)
    if high:
        mon = loan(deps)["monitoring"]
        assert mon["status"] == "awaiting_customer" and mon["paused_week"] < 13
        offer = loan(deps)["repayment"]["offer"]
        assert handle_restructure_decision(deps, json.dumps(dict(meta, loan_id=LOAN, kind="plan_choice",
                                                                  offer_id=offer["offer_id"], option="split")))
        change = loan(deps)["repayment"]["plan_change"]
        assert loan(deps)["monitoring"]["holds"] == ["plan_change"]
        assert sum(i["amount_cents"] for i in change["items"]) == 83333
        assert handle_restructure_decision(deps, json.dumps(dict(meta, loan_id=LOAN, kind="plan_change",
                                        change_id=change["change_id"], decision="approve", officer_id="officer")))
        assert replay(deps, action="resume", replay_id=rid, **meta)
        pump(deps, outbox)
    if stress:
        mon = loan(deps)["monitoring"]
        assert (mon["status"], mon["holds"]) == ("paused", ["collections_proposal"])
        assert mon["alert_ids"]
        assert not officer_contacts(deps, rid)
        if not high:
            assert mon["paused_week"] == 13
            assert {w["watch"]["stage"] for w in mon["weeks"]} <= {"normal", "watch", "medium"}
            assert all(w["watch"]["agent"] == "agent" for w in mon["weeks"] if w["watch"]["agent"])
            assert cases(deps)[0]["proposal"]["evidence"]["amount_cents"] == 83333
            assert cases(deps)[0]["proposal"]["evidence"]["contacts_7d"] == 7
        while (mon := loan(deps)["monitoring"])["status"] == "paused":
            assert decide_collections(deps, mon["pause_case_id"], **meta)
            assert replay(deps, action="resume", replay_id=rid, **meta)
            pump(deps, outbox)
        assert officer_contacts(deps, rid)
        if not high:
            assert officer_contacts(deps, rid)[0]["reason"] == "weekly_limit"
    doc = loan(deps)
    assert doc["monitoring"]["status"] == "completed"
    assert [w["week"] for w in doc["monitoring"]["weeks"]] == list(range(1, 14))
    assert len(doc["monitoring"]["weeks"][0]["projection"]["path"]) == 28
    if not high:
        assert sum(i.get("paid_cents", 0) for i in doc["repayment"]["schedule"]) == 83333 * (2 if stress else 3)
    else:
        assert len(doc["repayment"]["plan_history"]) == 1
        history = doc["repayment"]["plan_history"][0]
        assert sum(i["amount_cents"] for i in history["after"]) == sum(i["amount_cents"] for i in history["before"])
        assert all(c.get("text", "").isascii() for c in contacts(deps, rid))
    assert all(m["currency"] == "USD" for t, m, *_ in outbox.sent if t in (TOPIC_FLOW, TOPIC_REPAYMENT, TOPIC_ALERT))
    validate_usd_evidence([doc, *cases(deps), *alerts(deps)])
    assert handle_flow_update(deps, outbox.bodies(TOPIC_FLOW)[0]) is None
    assert doc["repayment"] == loan(deps)["repayment"]
    if not stress:
        class WrongLoanFeed:
            def __getattr__(self, key):
                return getattr(deps.mock, key)
            def fetch(self, source, token, params):
                rows = deps.mock.fetch(source, token, params)
                if source == "flow":
                    return [dict(r, loan_id="another") if r.get("loan_id") else r for r in rows]
                return rows
        with pytest.raises(ValueError, match="loan"):
            monitoring_module.start_replay(replace(deps, mock=WrongLoanFeed()), LOAN)


def usd_registration(run_journey, substitutions=None, ratio=None, seller="seller-dailylook-01"):
    """USD 승인 후 이미 등록된 대출을 지우고, 심사 근거(정책 대체 규칙·대사 비율)를 바꿔 다시 등록할 입력을 만든다."""
    import asyncio
    from functools import partial
    from unittest.mock import AsyncMock
    from journey.activities import ACTIVITIES
    from journey.adapters import HttpPolicyApi
    from journey.entry import handle_decision, handle_start
    from policy.app import create_app
    from policy.rules import load_rules
    from shared.contracts import USD_V1

    day = date(2026, 11, 24)
    sellers = build_demo_cases(day)
    outbox = Outbox()
    now = NOW.replace(day=day.day)
    deps = make_deps(create_mock_app(sellers, SECRET, lambda: now, flows=FlowStore(sellers, day)), outbox)
    deps = replace(deps, policy=HttpPolicyApi(client=TestClient(create_app(load_rules()))), clock=lambda: now)
    meta = USD_V1.model_dump()
    acts = {k: partial(f, deps) for k, f in ACTIVITIES.items()}
    acts["assess_credit"] = lambda _: {"assessment": {"review": {"passed": True}}}
    decision = dict(meta, decision="approve", amount_cents=500000, officer_id="test-officer")
    durable = AsyncMock()
    durable.get_status.return_value = None
    durable.start_new.return_value = LOAN
    request = dict(meta, application_id=LOAN, applicant_id=seller, requested_amount_cents=500000,
                   requested_tenor_months=6)
    assert asyncio.run(handle_start(durable, json.dumps(request))) == LOAN
    durable.get_status.return_value = type("Running", (), {"runtime_status": "Running"})()
    assert asyncio.run(handle_decision(durable, json.dumps(dict(application_id=LOAN, decision=decision))))
    _, ctx = run_journey(durable.start_new.call_args.args[2], acts, [("event", {**decision, "currency": "KRW"}),
                         ("event", durable.raise_event.call_args.args[2])], start=now)
    registration = deepcopy(ctx.called("register_monitoring")[0].input)
    deps.store.docs.pop((LOANS, LOAN))
    deps.store.etags.pop((LOANS, LOAN), None)
    saved = deps.store.read("applications", LOAN, LOAN)
    if substitutions is not None:
        deps.store.upsert("applications", {**saved, "policy": {**(saved.get("policy") or {}),
                                                               "applied_substitutions": substitutions}})
    if ratio is not None:
        registration["underwriting"]["features"]["pg_reconciliation_ratio"] = ratio
    return deps, outbox, registration, meta


def test_register_usd_loan_stores_approval_basis(run_journey):
    deps, _, registration, _ = usd_registration(run_journey, substitutions=["S003"], ratio=0.99)
    assert register_monitoring(deps, registration) == {"loan_id": LOAN}
    doc = loan(deps)
    assert doc["approval_basis"] == [{"code": "S003", "metric": "recon_ratio", "approved": 0.99, "floor": 0.95}]
    assert "approval_basis" not in doc["baseline"] and doc["monitoring"] is None


def test_register_v1_usd_loan_has_empty_basis(run_journey):
    deps, _, registration, _ = usd_registration(run_journey, substitutions=[])
    register_monitoring(deps, registration)
    assert loan(deps)["approval_basis"] == []


def test_register_krw_loan_has_no_approval_basis(mock_app):
    deps = make_deps(mock_app)
    approved_loan(deps)
    assert "approval_basis" not in loan(deps)


def test_usd_monitoring_consent_includes_marketplace(run_journey):
    deps, _, registration, meta = usd_registration(run_journey, seller="seller-bakery-20")
    register_monitoring(deps, registration)
    assert replay(deps, **meta)["replay_id"]
    mon = loan(deps)["monitoring"]
    consent = deps.store.read("consents", mon["consent_id"], mon["consent_id"])
    assert consent["scopes"] == ["account", "pg", "marketplace"]
    assert deps.raw.get_json(f"consent-{mon['consent_id']}", "market_flow.json")
    assert mon["market"] == "ok"


def test_krw_monitoring_consent_scopes_unchanged(mock_app):
    deps = make_deps(mock_app)
    approved_loan(deps)
    replay(deps)
    mon = loan(deps)["monitoring"]
    consent = deps.store.read("consents", mon["consent_id"], mon["consent_id"])
    assert consent["scopes"] == ["account", "pg"]
    assert "market" not in mon
    assert deps.raw.get_json(f"consent-{mon['consent_id']}", "market_flow.json") is None


def test_market_flow_failure_keeps_replaying(run_journey, monkeypatch, caplog):
    deps, outbox, registration, meta = usd_registration(run_journey, seller="seller-bakery-20")
    register_monitoring(deps, registration)
    fetch = deps.mock.fetch
    sentinel = "SYNTHETIC-PRIVATE-MARKET-VALUE"
    def corrupt(source, token, params=None):
        if source == "market_flow":
            return {"weeks": [{"week_end": sentinel}]}
        return fetch(source, token, params)
    monkeypatch.setattr(deps.mock, "fetch", corrupt)
    assert replay(deps, **meta)["replay_id"]
    pump(deps, outbox)
    mon = loan(deps)["monitoring"]
    assert mon["market"] == "missing"
    assert mon["status"] == "completed" and mon["processed_week"] == WEEKS
    assert sentinel not in caplog.text
    assert caplog.text.count("저장된 마켓 흐름 없음") == 1


def test_usd_week_entry_records_both_tracks(run_journey):

    deps, outbox, registration, meta = usd_registration(run_journey)
    register_monitoring(deps, registration)
    assert replay(deps, **meta)["replay_id"]
    pump(deps, outbox)
    week = loan(deps)["monitoring"]["weeks"][0]
    assert {"bank_only_triggers", "explained", "basis"} <= week.keys()
    assert isinstance(week["bank_only_triggers"], list) and isinstance(week["explained"], list)
    assert week["basis"] == loan(deps)["approval_basis"]


def test_replay_restart_keeps_approval_basis(run_journey):
    deps, _, registration, meta = usd_registration(run_journey, substitutions=["S003"], ratio=0.99)
    register_monitoring(deps, registration)
    basis = loan(deps)["approval_basis"]
    assert basis
    replay(deps, **meta)
    replay(deps, **meta)
    assert loan(deps)["approval_basis"] == basis


def finish_usd(deps, outbox, meta, rid):
    """Service Bus 흉내로 USD 재생을 끝까지 돌린다. 대기 항목은 승인·유지로 풀고 재개한다."""
    pump(deps, outbox)
    while (mon := loan(deps)["monitoring"])["status"] in ("paused", "awaiting_customer", "awaiting_resume"):
        if mon["status"] == "awaiting_resume":
            assert replay(deps, action="resume", replay_id=rid, **meta)
            pump(deps, outbox)
        elif mon["holds"][0] == "collections_proposal":
            assert decide_collections(deps, mon["pause_case_id"], **meta)
        elif mon["holds"][0] == "customer_response":
            offer_id = loan(deps)["repayment"]["offer"]["offer_id"]
            assert handle_restructure_decision(deps, json.dumps(dict(meta, kind="plan_choice", loan_id=LOAN,
                                                                      offer_id=offer_id, option="keep")))
        else:
            raise AssertionError(mon["holds"])
    assert loan(deps)["monitoring"]["status"] == "completed"


def usd_watch_replay(run_journey, seller, agents):
    deps, outbox, registration, meta = usd_registration(run_journey, seller=seller)
    register_monitoring(deps, registration)
    deps = replace(deps, agents=agents)
    rid = replay(deps, **meta)["replay_id"]
    finish_usd(deps, outbox, meta, rid)
    return deps, outbox, rid, loan(deps)["monitoring"]


def ladder(mon):
    """일지 단계가 바뀐 (단계, 첫 주). 정상 줄은 뺀다."""
    out = []
    for w in mon["weeks"]:
        stage = w["watch"]["stage"]
        if stage != "normal" and (not out or out[-1][0] != stage):
            out.append((stage, w["week"]))
    return out


def ew_contacts_in(deps, rid, week):
    row = week["row"]
    return [c for c in contacts(deps, rid) if c["by"] == "early_warning" and row["start"] <= c["on"] <= row["end"]]


def test_usd_watch_journal_and_alerts(run_journey):
    agents = StageAgents()
    deps, outbox, rid, mon = usd_watch_replay(run_journey, "seller-dailylook-01", agents)
    weeks = mon["weeks"]
    assert [w["week"] for w in weeks] == list(range(1, WEEKS + 1)) and all(w["watch"] for w in weeks)
    stages = [s for s, _ in ladder(mon)]
    assert stages[:3] == ["watch", "medium", "high"]
    ups = [w for w in weeks if w["watch"]["stage_up"]]
    assert len(alerts(deps)) == len(ups) and len(outbox.bodies(TOPIC_ALERT)) == len(alerts(deps))
    assert all(w["alert_id"] and w["watch"]["alert_action"] == "created" for w in ups)
    for w in weeks:
        if w["watch"]["stage"] == "watch":
            assert w["alert_id"] is None and w["watch"]["alert_action"] is None and not ew_contacts_in(deps, rid, w)
    first_high = next(w for w in weeks if w["watch"]["stage"] == "high")
    s1 = next(c for c in contacts(deps, rid) if c["ref"] == f"{rid}-s1")
    assert s1["on"] == first_high["row"]["end"]
    assert mon["escalation"]["stage1"]["alert_id"] == first_high["watch"]["alert_id"]
    assert all(w["investigation"] is None for w in weeks if w["watch"]["agent"] is None)
    assert 0 < agents.calls <= 8
    assert mon["watch"]["stages"] == [w["watch"]["stage"] for w in weeks]


def test_usd_gearstore_resolves_without_escalation(run_journey):
    agents = StageAgents()
    deps, _, rid, mon = usd_watch_replay(run_journey, "seller-gearstore-06", agents)
    assert [s for s, _ in ladder(mon)] == ["watch", "medium", "resolved"]
    assert alerts(deps) and any(w["watch"]["kind"] == "resolved_quiet" for w in mon["weeks"])
    assert not [c for c in contacts(deps, rid) if c["ref"] in (f"{rid}-s1", f"{rid}-s2")]
    assert mon["escalation"] == {"stage1": None, "stage2": None}
    assert agents.calls <= 8


def test_usd_cause_update_keeps_alert_id_and_does_not_republish(run_journey, monkeypatch):
    # 장면마다 원인 후보가 하나뿐이라(dailylook은 sales_slump) 첫 경보가 다른 원인으로 저장된 것처럼 꾸민다.
    deps, outbox, registration, meta = usd_registration(run_journey, seller="seller-dailylook-01")
    register_monitoring(deps, registration)
    agents = StageAgents()
    deps = replace(deps, agents=agents)
    real, seeded = deps.store.upsert, []

    def stale_first_alert(container, document):
        if container == ALERTS and document["id"].startswith("ew-") and not seeded:
            seeded.append(document["id"])
            document = dict(document, primary_cause="settlement_hold", secondary_causes=[])
        return real(container, document)

    monkeypatch.setattr(deps.store, "upsert", stale_first_alert)
    rid = replay(deps, **meta)["replay_id"]
    finish_usd(deps, outbox, meta, rid)
    mon = loan(deps)["monitoring"]
    created = [w["watch"]["alert_id"] for w in mon["weeks"] if w["watch"]["alert_action"] == "created"]
    updated = [w for w in mon["weeks"] if w["watch"]["alert_action"] == "updated"]
    assert len(updated) == 1 and updated[0]["alert_id"] is None and updated[0]["watch"]["stage_up"] is False
    alert_id = updated[0]["watch"]["alert_id"]
    assert alert_id == seeded[0] == created[0]
    doc = deps.store.read(ALERTS, alert_id, LOAN)
    assert doc["primary_cause"] == "sales_slump" and doc["id"] == alert_id and doc["why_now"]
    assert len(alerts(deps)) == len(created)
    published = [json.loads(b)["alert_id"] for b in outbox.bodies(TOPIC_ALERT)]
    assert published == created and published.count(alert_id) == 1
    assert agents.calls <= 8


def test_usd_agents_disabled_still_escalates(run_journey):
    deps, _, rid, mon = usd_watch_replay(run_journey, "seller-dailylook-01", None)
    called = [w["watch"] for w in mon["weeks"] if w["watch"]["agent"] is not None]
    assert called and all(line["agent"] == "fallback" for line in called)
    assert [s for s, _ in ladder(mon)][:3] == ["watch", "medium", "high"]
    first_high = next(w for w in mon["weeks"] if w["watch"]["stage"] == "high")
    s1 = next(c for c in contacts(deps, rid) if c["ref"] == f"{rid}-s1")
    assert (s1["on"], s1["status"]) == (first_high["row"]["end"], "sent")


def test_usd_watch_retry_does_not_duplicate_alert(run_journey, monkeypatch):
    deps, _, registration, meta = usd_registration(run_journey, seller="seller-dailylook-01")
    register_monitoring(deps, registration)
    agents = StageAgents()
    deps = replace(deps, agents=agents)
    rid = replay(deps, **meta)["replay_id"]
    real, failed = deps.store.upsert, []

    def flaky(container, document):
        weeks = (document.get("monitoring") or {}).get("weeks") or [{}]
        if container == LOANS and weeks[-1].get("alert_id") and not failed:
            failed.append(weeks[-1]["week"])  # 경보를 쓴 뒤 대출 문서를 쓰다 실패한다
            raise RuntimeError("Cosmos 503")
        return real(container, document)

    monkeypatch.setattr(deps.store, "upsert", flaky)
    for week in range(1, WEEKS + 1):
        try:
            process_week(deps, LOAN, rid, week)
        except RuntimeError:
            break
    (week,) = failed
    alert_id = f"ew-{rid}-w{week:02d}"
    assert deps.store.read(ALERTS, alert_id, LOAN) and loan(deps)["monitoring"]["processed_week"] == week - 1
    calls = agents.calls
    entry = process_week(deps, LOAN, rid, week)
    assert agents.calls == calls
    assert entry["watch"]["agent"] == "stored" and entry["watch"]["stage_up"] and entry["alert_id"] == alert_id
    assert entry["watch"]["alert_action"] == "created"
    mon = loan(deps)["monitoring"]
    assert len(alerts(deps)) == 1 and mon["alert_ids"] == [alert_id]
    assert len(mon["watch"]["stages"]) == week and mon["watch"]["open"]["alert_id"] == alert_id


@pytest.mark.parametrize("stage_sent", ["stage1", "stage2"])
def test_usd_high_week_retry_does_not_duplicate_alert_or_outreach(run_journey, monkeypatch, stage_sent):
    deps, _, registration, meta = usd_registration(run_journey, seller="seller-dailylook-01")
    register_monitoring(deps, registration)
    deps = replace(deps, agents=StageAgents())
    rid = replay(deps, **meta)["replay_id"]
    real, failed = deps.store.upsert, []

    def flaky(container, document):
        mon = document.get("monitoring") or {}
        sent = (mon.get("escalation") or {}).get(stage_sent)
        if container == LOANS and sent and mon["weeks"][-1]["watch"]["stage"] == "high" and not failed:
            failed.append(mon["weeks"][-1]["week"])  # 경보·연락을 쓴 뒤 대출 문서를 쓰다 실패한다
            raise RuntimeError("Cosmos 503")
        return real(container, document)

    monkeypatch.setattr(deps.store, "upsert", flaky)
    for week in range(1, WEEKS + 1):
        try:
            process_week(deps, LOAN, rid, week)
        except RuntimeError:
            break
    (week,) = failed
    ref = f"{rid}-s{stage_sent[-1]}"
    before_alerts, before_contacts = len(alerts(deps)), [c["ref"] for c in contacts(deps, rid)]
    assert ref in before_contacts and loan(deps)["monitoring"]["processed_week"] == week - 1
    entry = process_week(deps, LOAN, rid, week)
    assert entry["watch"]["stage"] == "high"
    mon = loan(deps)["monitoring"]
    assert mon["processed_week"] == week and mon["escalation"][stage_sent]["status"] == "sent"
    assert len(alerts(deps)) == before_alerts and [c["ref"] for c in contacts(deps, rid)] == before_contacts
    assert len(set(mon["alert_ids"])) == len(mon["alert_ids"])


def test_usd_replay_without_watch_key_resumes_from_resume_week(run_journey):
    # 이 브랜치 이전에 시작한 USD 재생 문서에는 monitoring.watch가 없다. 이어 처리하면 그 주부터 사건 상태를 새로 시작한다.
    deps, _, registration, meta = usd_registration(run_journey, seller="seller-dailylook-01")
    register_monitoring(deps, registration)
    deps = replace(deps, agents=StageAgents())
    rid = replay(deps, **meta)["replay_id"]
    doc = loan(deps)
    del doc["monitoring"]["watch"]
    deps.store.upsert(LOANS, doc)
    entry = process_week(deps, LOAN, rid, 1)
    mon = loan(deps)["monitoring"]
    assert entry["watch"]["stage"] and mon["weeks"][0]["watch"] == entry["watch"]
    assert mon["watch"]["stages"] == [entry["watch"]["stage"]] and mon["processed_week"] == 1
    process_week(deps, LOAN, rid, 2)
    assert len(loan(deps)["monitoring"]["watch"]["stages"]) == 2


def test_usd_recent_alert_is_the_open_incidents_own(mock_app):
    from shared.contracts import USD_V1
    deps, meta = make_deps(mock_app), USD_V1.model_dump()
    for alert_id, week in (("ew-old", 3), ("ew-open", 5), ("ew-latest-other", 7)):
        deps.store.upsert(ALERTS, dict(id=alert_id, loan_id=LOAN, week=week, severity="high", summary=alert_id,
                                       signals=[], **meta))

    def doc(watch):
        return dict(meta, id=LOAN, monitoring={"alert_ids": ["ew-old", "ew-open", "ew-latest-other"], "watch": watch})

    recent = monitoring_module._recent_alert
    assert recent(deps, doc({**new_watch(), "open": {"id": "inc-1", "alert_id": "ew-open"}}), LOAN)["alert_id"] == "ew-open"
    assert recent(deps, doc(new_watch()), LOAN) is None
    assert recent(deps, doc({**new_watch(), "open": {"id": "inc-2", "alert_id": None}}), LOAN) is None
    assert recent(deps, doc(None), LOAN) is None


def test_replay_restart_resets_watch(run_journey, mock_app):
    deps, _, registration, meta = usd_registration(run_journey, seller="seller-bakery-20")
    register_monitoring(deps, registration)
    first = replay(deps, **meta)["replay_id"]
    doc = loan(deps)
    assert doc["monitoring"]["watch"] == new_watch()
    doc["monitoring"].update(status="completed", watch={**new_watch(), "stages": ["watch", "medium"]})
    deps.store.upsert(LOANS, doc)
    assert replay(deps, **meta)["replay_id"] != first
    assert loan(deps)["monitoring"]["watch"] == new_watch()
    krw = make_deps(mock_app)
    approved_loan(krw)
    replay(krw)
    assert loan(krw)["monitoring"]["watch"] is None


def test_market_reader_returns_stored_flow_or_none(run_journey):
    deps, _, registration, meta = usd_registration(run_journey, seller="seller-bakery-20")
    register_monitoring(deps, registration)
    replay(deps, **meta)
    mon = loan(deps)["monitoring"]
    assert isinstance(monitoring_module._market(deps, mon), UsdMarketFlow)
    assert monitoring_module._market(deps, {**mon, "consent_id": "absent"}) is None
    deps.raw.put_json(f"consent-{mon['consent_id']}", "market_flow.json", {"weeks": "bad"})
    assert monitoring_module._market(deps, mon) is None


def alerts(deps):
    return [d for (c, i), d in deps.store.docs.items() if c == ALERTS and i.startswith("ew-")]


def cases(deps):
    return sorted((d for (c, i), d in deps.store.docs.items() if c == ALERTS and i.startswith("rc-")),
                  key=lambda d: d["week"])


def contacts(deps, replay_id):
    return (deps.store.read(ALERTS, f"contacts-{replay_id}", LOAN) or {"contacts": []})["contacts"]


class DropDebits:
    """상환일 출금을 흐름에서 빼 미납을 만든다(데모 셀러는 마지막 회차만 미납이라 앞 회차 미납을 흉내 낸다)."""

    def __init__(self, inner, dates):
        self.inner, self.dates = inner, {d.isoformat() for d in dates}

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def fetch(self, kind, token, params=None):
        out = self.inner.fetch(kind, token, params)
        if kind != "flow":
            return out
        return [t for t in out if not (t["description"] == LENDER_DEBIT and t["booked_on"] in self.dates)]


def missing_first_due(deps):
    return replace(deps, mock=DropDebits(deps.mock, [FIRST_DUE]))


class RecordingMock:
    """fetch 호출(원천·params)과 동의 범위를 기록한다. fail_on에 든 원천은 SourceError를 낸다."""

    def __init__(self, inner, fail_on=()):
        self.inner, self.fail_on = inner, set(fail_on)
        self.calls, self.consent_scopes = [], []

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def create_consent(self, applicant_id, scopes, purpose, ttl_days):
        self.consent_scopes.append(list(scopes))
        return self.inner.create_consent(applicant_id, scopes, purpose, ttl_days)

    def fetch(self, kind, token, params=None):
        self.calls.append((kind, params))
        if kind in self.fail_on:
            raise SourceError(503, "pg down")
        return self.inner.fetch(kind, token, params)

    def params_by_source(self):
        return dict(self.calls)


def test_register_monitoring_builds_baseline_and_keeps_replay_state(mock_app):
    deps = make_deps(mock_app)
    approved_loan(deps)
    doc = loan(deps)
    assert doc["industry"] == "패션의류" and doc["monitoring"] is None
    assert doc["baseline"]["avg_weekly_revenue"] > 0 and set(doc["baseline"]["seasonal_index"]) == {f"{m:02d}" for m in range(1, 13)}
    assert doc["baseline"]["installment_krw"] == round(monthly_installment(3_300_000, 12))
    assert doc["baseline"]["settlement"]["outflows"]["items"]
    replay(deps)
    before_retry = loan(deps)
    assert before_retry["monitoring"]["status"] == "completed" and before_retry["repayment"]["schedule"]
    approved_loan(deps)
    assert loan(deps) == before_retry


def test_inline_replay_paces_each_week_so_the_console_sees_it_advance(mock_app):
    slept, seen = [], []
    deps = make_deps(mock_app)
    deps = replace(deps, week_pace_seconds=3.0,
                   sleep=lambda s: (slept.append(s), seen.append(loan(deps)["monitoring"]["processed_week"])))
    approved_loan(deps)
    replay(deps)
    # 주차마다(마지막 주 제외) 남은 시간만큼 쉰다. 쉬는 동안 콘솔은 방금 저장된 주차까지 본다.
    assert seen == list(range(1, WEEKS)) and all(0 < s <= 3.0 for s in slept)
    assert loan(deps)["monitoring"]["status"] == "completed"


def test_inline_replay_without_agents_raises_detector_alerts_for_officer(mock_app):
    deps = make_deps(mock_app)
    approved_loan(deps)
    started = replay(deps)
    mon = loan(deps)["monitoring"]
    assert started["mode"] == mon["mode"] == "inline"
    assert mon["processed_week"] == WEEKS and mon["status"] == "completed" and len(mon["weeks"]) == WEEKS
    consent = deps.store.read("consents", mon["consent_id"], mon["consent_id"])
    assert consent["purpose"] == MONITORING_PURPOSE and consent["scopes"] == ["account", "pg"]
    assert deps.raw.get_json(f"consent-{mon['consent_id']}", "flow.json")
    # 매출 부진 셀러의 차입은 6주차 이후 결과로 나온다(원인 → 차입 순서).
    debt_week = next(w for w in mon["weeks"] if "new_debt" in w["new_triggers"])
    assert debt_week["week"] >= 6 and debt_week["alert_id"]
    found = alerts(deps)
    assert found and all(a["models"] == {"investigator": "detector_only"} for a in found)
    # 조기경보는 심사역 검토용이다. 추심은 미납(데모 셀러는 13주차 3회차)에서만 돈다.
    assert all(a["collections"] is None for a in found)
    schedule = loan(deps)["repayment"]["schedule"]
    assert [s["due_on"] for s in schedule] == [d.isoformat() for d in due_dates(AS_OF, WEEKS * 7)]
    assert schedule[-1]["status"] == "missed" and schedule[-1]["case_id"] == f"rc-{mon['replay_id']}-i3"
    assert all(s["status"] == "paid" and s["txn_id"] for s in schedule[:-1])
    (case,) = cases(deps)
    assert case["week"] == WEEKS and case["installment"]["no"] == 3 and case["missed_count"] == 1
    assert case["recent_alert"]["alert_id"] == mon["alert_ids"][-1]
    # 폴백 경보는 요약에 근거가 없어 추심 에이전트가 신호 종류로 판단한다.
    last = deps.store.read(ALERTS, mon["alert_ids"][-1], LOAN)
    assert case["recent_alert"]["signals"] == [s["kind"] for s in last["signals"]] and case["recent_alert"]["signals"]
    assert case["recent_alert"]["check_action"] == last["check_action"] and last["check_action"]
    assert case["proposal"]["evidence"]["recent_alert"]["check_action"] == last["check_action"]
    # 추심 에이전트는 제안만 한다. 13주차 미납은 재생이 끝난 뒤라 제안이 심사역 결정을 기다리고 아무것도 보내지 않는다.
    assert case["collections"]["decision"] == "proposed" and case["collections"]["contacts"] == []
    assert case["status"] == "proposal_pending" and case["proposal"]["status"] == "pending_officer"
    assert case["proposal"]["by"] == "fallback"
    assert case["proposal"]["script_id"] == "missed_payment"  # 탐지기 경보는 medium
    sent = contacts(deps, mon["replay_id"])
    assert [c["by"] for c in sent] == ["reminder"] * 3
    assert [c["on"] for c in sent[:3]] == ["2026-11-29", "2027-01-01", "2027-01-26"]
    assert mon["weeks"][WEEKS - 1]["case_id"] == case["case_id"]
    assert mon["weeks"][3]["repayment"] == {"reminders": [1], "paid": [], "missed": []}
    assert mon["weeks"][4]["repayment"]["paid"] == [1]
    # 전망은 원장 상환일로 이번 대출 원리금을 뺀다: 4주차 창(11/30~12/27)엔 12/2, 5주차 창(12/7~1/3)엔 없다.
    assert mon["weeks"][3]["projection"]["totals"]["installment"] == -loan(deps)["repayment"]["installment_krw"]
    assert mon["weeks"][4]["projection"]["totals"].get("installment", 0) == 0
    # 트리거 종류마다 한 번만 조사한다.
    new = [t for w in mon["weeks"] for t in w["new_triggers"]]
    assert len(new) == len(set(new))
    assert all(bool(w["investigation"]) == bool(w["new_triggers"]) for w in mon["weeks"])
    # 주차마다 4주 잔고 전망을 남긴다. 주인공은 부도(잔고 0 미만) 전에 전망 부족이 먼저 걸린다.
    assert all(len(w["projection"]["path"]) == 28 for w in mon["weeks"])
    short = next(w["week"] for w in mon["weeks"] if "projected_shortfall" in w["new_triggers"])
    broke = next(w["week"] for w in mon["weeks"] if w["row"]["end_balance"] < 0)
    assert short < broke


@pytest.mark.parametrize("change", ["withdrawal", "replacement", "unchanged"])
def test_manual_resume_rechecks_after_acceptance_before_publishing(mock_app, monkeypatch, change):
    from journey.consent import delete_consent_data
    from tests.orchestrator.test_consent_lifecycle import MemoryRaw as DeletableRaw, end

    outbox = Outbox()
    deps = missing_first_due(replace(make_deps(mock_app, events=outbox), raw=DeletableRaw()))
    approved_loan(deps)
    rid = replay(deps)["replay_id"]
    pump(deps, outbox)
    settle_holds(deps)
    published = len(flows(outbox))
    resume = monitoring_module.resume_replay

    def interleave(*args):
        accepted = resume(*args)
        current = loan(deps)
        mon = current["monitoring"]
        assert accepted["from_week"] == 6 and mon["status"] == "replaying"
        if change == "withdrawal":
            delete_consent_data(deps, end(mon["consent_id"]))
        elif change == "replacement":
            mon.update(replay_id="replacement", processed_week=0)
            deps.store.upsert(LOANS, current)
        return accepted

    monkeypatch.setattr(monitoring_module, "resume_replay", interleave)
    assert replay(deps, action="resume", replay_id=rid)["from_week"] == 6
    new_weeks = [m[1]["week"] for m in flows(outbox)[published:]]
    if change == "unchanged":
        assert new_weeks == [6]
        assert handle_flow_update(deps, outbox.bodies(TOPIC_FLOW)[-1])["week"] == 6
    else:
        assert not new_weeks
        mon = loan(deps)["monitoring"]
        assert (mon["replay_id"], mon["status"]) == (
            (rid, "stopped") if change == "withdrawal" else ("replacement", "replaying"))


@pytest.mark.parametrize("fault", ["guard", "stop", "send_accepted", "processed_before_retry"])
def test_resume_queue_redelivery_recovers_only_its_pending_week(mock_app, monkeypatch, fault):
    import azure.functions as func
    import function_app
    from journey.consent import delete_consent_data
    from tests.orchestrator.test_consent_lifecycle import MemoryRaw as DeletableRaw, end

    outbox = Outbox()
    deps = missing_first_due(replace(make_deps(mock_app, events=outbox), raw=DeletableRaw()))
    approved_loan(deps)
    rid = replay(deps)["replay_id"]
    pump(deps, outbox)
    settle_holds(deps)
    monkeypatch.setattr(function_app, "deps", lambda: deps)
    def deliver(mid="original", replay_id=rid, **fields):
        body = json.dumps(dict(loan_id=LOAN, action="resume", replay_id=replay_id, **fields))
        function_app.monitoring_replay(func.QueueMessage(id=mid, body=body))

    resume = monitoring_module.resume_replay
    before = len(flows(outbox))
    with monkeypatch.context() as patch:
        def accepted(*args, **kwargs):
            result = resume(*args, **kwargs)
            assert result["from_week"] == 6
            if fault == "stop":
                delete_consent_data(deps, end(loan(deps)["monitoring"]["consent_id"]))
                patch.setattr(deps.store, "replace", lambda *_: False)
            elif fault == "guard":
                read = deps.store.read
                def unavailable(container, *args):
                    if container == "consents":
                        raise TimeoutError("guard unavailable")
                    return read(container, *args)
                patch.setattr(deps.store, "read", unavailable)
            else:
                publish = outbox.publish
                def lost_ack(*args, **kwargs):
                    publish(*args, **kwargs)
                    raise TimeoutError("send acknowledgement lost")
                patch.setattr(outbox, "publish", lost_ack)
            return result
        patch.setattr(monitoring_module, "resume_replay", accepted)
        with pytest.raises((RuntimeError, TimeoutError)):
            deliver()

    ambiguous = fault in ("send_accepted", "processed_before_retry")
    assert len(flows(outbox)) == before + ambiguous
    deliver("fresh-click")
    deliver(replay_id="rp-stale")
    deliver(paused_week=4)
    assert len(flows(outbox)) == before + ambiguous
    assert loan(deps)["monitoring"]["status"] == "replaying"
    if fault == "processed_before_retry":
        assert handle_flow_update(deps, outbox.bodies(TOPIC_FLOW)[-1])["week"] == 6
        published = len(flows(outbox))
        deliver()
        assert len(flows(outbox)) == published and loan(deps)["monitoring"]["processed_week"] == 6
        return
    deliver()
    if fault == "stop":
        assert loan(deps)["monitoring"]["status"] == "stopped"
        assert len(flows(outbox)) == before
        return
    events = flows(outbox)[before:]
    assert len(events) == 1 + ambiguous
    assert all(m[2:] == (f"flow.updated:{LOAN}:{rid}:6", LOAN) for m in events)
    for i, event in enumerate(events):
        result = handle_flow_update(deps, json.dumps(event[1]))
        if i == 0:
            assert result["week"] == 6
        else:
            assert result is None
    mon = loan(deps)["monitoring"]
    assert [w["week"] for w in mon["weeks"]].count(6) == len(mon["resumed_at"]) == 1
    # A late dequeue of the accepted command cannot accept a different pause.
    current = loan(deps)
    current["monitoring"].update(status="awaiting_resume", paused_week=6)
    deps.store.upsert(LOANS, current)
    count = len(flows(outbox))
    deliver()
    deliver("late-click", paused_week=5)
    assert len(flows(outbox)) == count and loan(deps)["monitoring"]["status"] == "awaiting_resume"


def test_servicebus_replay_publishes_ordered_session_messages(mock_app):
    outbox = Outbox()
    deps = missing_first_due(make_deps(mock_app, events=outbox, agents=ScriptedAgents()))
    approved_loan(deps)
    started = replay(deps)
    replay_id = started["replay_id"]
    # 1주차만 발행하고, 다음 주차는 앞 주차 처리가 끝난 뒤 발행한다(재조정 요청이 나오면 멈출 수 있게).
    assert [m[1]["week"] for m in flows(outbox)] == [1]
    assert flows(outbox)[0][2] == f"flow.updated:{LOAN}:{replay_id}:1" and flows(outbox)[0][3] == LOAN
    assert loan(deps)["monitoring"]["processed_week"] == 0

    week1 = outbox.bodies(TOPIC_FLOW)[0]
    assert handle_flow_update(deps, week1)["week"] == 1
    assert handle_flow_update(deps, week1) is None  # 중복 전달: 다시 처리하지 않고 같은 ID로 다음 주차만 다시 낸다
    assert {m[2] for m in flows(outbox)} == {f"flow.updated:{LOAN}:{replay_id}:{w}" for w in (1, 2)}
    week3 = json.dumps({"type": "flow.updated", "loan_id": LOAN, "replay_id": replay_id, "week": 3})
    with pytest.raises(OutOfOrder):
        handle_flow_update(deps, week3)
    assert handle_flow_update(deps, json.dumps({"type": "smoke"})) is None
    pump(deps, outbox)
    mon = loan(deps)["monitoring"]
    # 2주차 경보는 추심을 부르지 않는다. 5주차 1회차 미납 → 추심이 제안 → 재생이 멈춘다. 결정 후 이어서 재생한다.
    case_id = f"rc-{replay_id}-i1"
    assert (mon["status"], mon["paused_week"], mon["processed_week"], mon["pause_case_id"]) == ("paused", 5, 5, case_id)
    assert mon["holds"] == ["collections_proposal"]
    assert [json.loads(b)["case_id"] for b in outbox.bodies(TOPIC_REPAYMENT)] == [case_id]
    assert [m for m in outbox.sent if m[0] == TOPIC_REPAYMENT][0][2:] == (f"repayment.updated:{case_id}", LOAN)
    published, sent_before = len(flows(outbox)), contacts(deps, replay_id)
    assert handle_repayment_updated(deps, outbox.bodies(TOPIC_REPAYMENT)[0]) is None  # 재전달은 다시 제안하지 않는다
    assert len(flows(outbox)) == published and loan(deps)["monitoring"]["status"] == "paused"
    assert contacts(deps, replay_id) == sent_before
    settle_holds(deps)
    assert replay(deps, action="resume", replay_id=replay_id)["from_week"] == 6
    assert flows(outbox)[-1][1]["week"] == 6
    pump(deps, outbox)
    pauses = resume_until_done(deps, replay_id, lambda: pump(deps, outbox))
    assert sorted({m[1]["week"] for m in flows(outbox)}) == list(range(1, WEEKS + 1))
    assert all(m[3] == LOAN for m in flows(outbox))
    mon = loan(deps)["monitoring"]
    assert mon["status"] == "completed" and len(mon["resumed_at"]) == 1 + pauses
    raised = [m for m in outbox.sent if m[0] == TOPIC_ALERT]
    assert [m[1]["alert_id"] for m in raised] == mon["alert_ids"] and all(m[3] == LOAN for m in raised)
    alert = deps.store.read(ALERTS, mon["alert_ids"][0], LOAN)
    assert alert["severity"] == "high" and alert["models"] == {"investigator": "gpt-5.6-luna"}
    assert alert["replay_id"] == started["replay_id"] and alert["collections"] is None
    # 13주차 미납은 재생을 멈추지 않는다. 재생은 끝나고 추심 제안은 심사역 결정을 기다린다.
    last = cases(deps)[-1]
    assert last["week"] == WEEKS and last["proposal"]["status"] == "pending_officer" and last["restructure"] is None
    assert mon["case_ids"] == [c["case_id"] for c in cases(deps)]


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
    assert len(outbox.bodies(TOPIC_FLOW)) == 1
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
    assert len(deps.events.bodies(TOPIC_FLOW)) == 1


def test_failure_after_claim_is_recorded_and_not_blocking(mock_app):
    deps = make_deps(mock_app)
    approved_loan(deps)

    class FlowDown:
        def __init__(self, inner):
            self.inner = inner

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def fetch(self, kind, token, params=None):
            raise RuntimeError("mock flow down")

    broken = replace(deps, mock=FlowDown(deps.mock))
    with pytest.raises(ValueError, match="account_flow_unavailable"):
        replay(broken)
    mon = loan(deps)["monitoring"]
    assert mon["status"] == "failed" and "account_flow_unavailable" in mon["error"]
    assert replay(deps)["replay_id"] != mon["replay_id"]  # failed는 가드에 걸리지 않는다


def test_redelivery_recovers_week_saved_before_repayment_publish(mock_app, monkeypatch):
    outbox = Outbox()
    deps = missing_first_due(make_deps(mock_app, events=outbox, agents=ScriptedAgents()))
    approved_loan(deps)
    replay(deps)
    fail_once(monkeypatch, "_publish_case")
    with pytest.raises(RuntimeError, match="503"):
        pump(deps, outbox)
    mon = loan(deps)["monitoring"]
    assert mon["processed_week"] == 5 and not outbox.bodies(TOPIC_REPAYMENT)
    body = outbox.bodies(TOPIC_FLOW)[-1]
    handle_flow_update(deps, body)
    assert json.loads(outbox.bodies(TOPIC_REPAYMENT)[0])["case_id"] == mon["weeks"][-1]["case_id"]
    pump(deps, outbox)
    assert loan(deps)["monitoring"]["holds"] == ["collections_proposal"]
    assert deps.agents.calls.count(COLLECTIONS_AGENT) == 1


@pytest.mark.parametrize("kind", ["collections", "restructure", "plan"])
def test_unstored_decision_retries_after_write_conflicts(mock_app, monkeypatch, kind):
    if kind == "plan":
        deps, _ = high_replay(mock_app)
        assert choose(deps, "move")
        def send():
            return decide_plan(deps)
    else:
        deps, _ = paused_inline(mock_app)
        case_id = loan(deps)["monitoring"]["pause_case_id"]
        if kind == "restructure":
            assert decide_collections(deps, case_id, include_hardship=True)
        def send():
            return (decide_restructure if kind == "restructure" else decide_collections)(deps, case_id)
    with monkeypatch.context() as patch:
        patch.setattr(deps.store, "replace", lambda *args: False)
        with pytest.raises(RuntimeError):
            send()
    assert send() is True
    assert send() is False


def test_repayment_updated_runs_collections_once(mock_app):
    outbox = Outbox()
    agents = ScriptedAgents()
    deps = missing_first_due(make_deps(mock_app, events=outbox, agents=agents))
    approved_loan(deps)
    replay(deps)
    pump(deps, outbox, topics=(TOPIC_FLOW,))
    assert loan(deps)["monitoring"]["processed_week"] == 5  # 추심이 끝나야 다음 주차를 낸다
    body = outbox.bodies(TOPIC_REPAYMENT)[0]
    result = handle_repayment_updated(deps, body)
    assert result["decision"] == "proposed" and result["contacts"] == []
    assert handle_repayment_updated(deps, body) is None
    assert agents.calls.count(COLLECTIONS_AGENT) == 1
    case_id = json.loads(body)["case_id"]
    doc = deps.store.read(ALERTS, case_id, LOAN)
    # 제안 1회: 심사역 승인 전에는 아무것도 보내지 않고 재조정 요청도 만들지 않는다.
    assert doc["status"] == "proposal_pending" and doc["restructure"] is None
    assert doc["proposal"]["script_id"] == "hardship_offer" and doc["proposal"]["by"] == "agent"
    assert doc["proposal"]["hardship"] == {"category": "debt_overload", "note": "대출 중첩"}
    replay_id = loan(deps)["monitoring"]["replay_id"]
    assert [(c["by"], c["status"]) for c in contacts(deps, replay_id)] == [("reminder", "sent")]
    consent_id = loan(deps)["monitoring"]["consent_id"]
    monitoring_runs = [c for c in agents.calls if c in (EW_AGENT, COLLECTIONS_AGENT)]
    assert len(deps.store.read("consents", consent_id, consent_id)["foundry_conversations"]) == len(monitoring_runs)


def test_restructure_decision_requires_pending_request(mock_app):
    outbox = Outbox()
    deps = missing_first_due(make_deps(mock_app, events=outbox, agents=ScriptedAgents()))
    approved_loan(deps)
    replay(deps)
    pump(deps, outbox, topics=(TOPIC_FLOW,))
    case_id = json.loads(outbox.bodies(TOPIC_REPAYMENT)[0])["case_id"]
    decision = {"loan_id": LOAN, "alert_id": case_id, "decision": "approve", "officer_id": "officer-1", "reason": "3개월 유예"}
    assert handle_restructure_decision(deps, json.dumps(decision)) is False  # 아직 Collections 전
    assert loan(deps)["monitoring"]["status"] == "replaying"  # 추심 전에는 멈추지도 다음 주차를 내지도 않는다
    handle_repayment_updated(deps, outbox.bodies(TOPIC_REPAYMENT)[0])
    assert loan(deps)["monitoring"]["status"] == "paused"
    assert handle_restructure_decision(deps, json.dumps(decision)) is False  # 추심 승인 전에는 재조정 요청이 없다
    assert decide_collections(deps, case_id, include_hardship=True) is True
    mon = loan(deps)["monitoring"]
    assert (mon["status"], mon["holds"]) == ("paused", ["restructure"])
    assert handle_restructure_decision(deps, json.dumps(decision)) is True
    assert loan(deps)["monitoring"]["status"] == "awaiting_resume"
    assert loan(deps)["monitoring"]["holds"] == []
    assert handle_restructure_decision(deps, json.dumps(decision)) is False
    assert handle_restructure_decision(deps, json.dumps({**decision, "decision": "maybe"})) is False
    doc = deps.store.read(ALERTS, case_id, LOAN)
    assert doc["status"] == "restructure_approved"
    assert doc["restructure"]["officer_id"] == "officer-1" and doc["restructure"]["reason"] == "3개월 유예"


@pytest.mark.parametrize("topic", [TOPIC_REPAYMENT, TOPIC_ALERT])
def test_publish_failure_is_recorded_and_replay_continues(mock_app, topic):
    outbox = Outbox(fail_topics={topic})
    deps = missing_first_due(make_deps(mock_app, events=outbox, agents=ScriptedAgents()))
    approved_loan(deps)
    replay_id = replay(deps)["replay_id"]
    pump(deps, outbox)
    # 미납 발행이 실패하면 주차 핸들러가 직접 추심을 돌리고 잇는다(재조정 요청이면 멈춘다).
    resume_until_done(deps, replay_id, lambda: pump(deps, outbox))
    assert loan(deps)["monitoring"]["status"] == "completed"
    docs = cases(deps) if topic == TOPIC_REPAYMENT else alerts(deps)
    assert docs and all("sb down" in d["event_error"] for d in docs)
    assert all(c["collections"] is not None for c in cases(deps))


def test_week13_case_is_decidable_after_completion(mock_app):
    deps = make_deps(mock_app, agents=ScriptedAgents())
    approved_loan(deps)
    replay_id = replay(deps)["replay_id"]
    resume_until_done(deps, replay_id)  # 연속 높음 경보로 열린 셀프 플랜 제안은 유지로 답한다
    mon = loan(deps)["monitoring"]
    last = cases(deps)[-1]
    assert mon["status"] == "completed" and mon["processed_week"] == WEEKS
    assert last["week"] == WEEKS and last["proposal"]["status"] == "pending_officer"
    assert decide_collections(deps, last["case_id"], include_hardship=True) is True
    assert deps.store.read(ALERTS, last["case_id"], LOAN)["restructure"]["status"] == "pending_officer"
    assert decide_restructure(deps, last["case_id"]) is True
    assert deps.store.read(ALERTS, last["case_id"], LOAN)["restructure"]["status"] == "approved"
    assert loan(deps)["monitoring"]["status"] == "completed"


def test_legacy_pause_alert_id_still_promotes(mock_app):
    # 배포 전에 멈춘 대출 문서는 옛 키(pause_alert_id)를 쓰고 holds가 없다. 결정 뒤 재개 대기로 올라가야 한다.
    deps, replay_id = paused_inline(mock_app)
    doc = loan(deps)
    case_id = doc["monitoring"].pop("pause_case_id")
    doc["monitoring"].pop("holds")
    doc["monitoring"]["pause_alert_id"] = case_id
    deps.store.upsert(LOANS, doc)
    assert decide_collections(deps, case_id, include_hardship=True) is True
    assert loan(deps)["monitoring"]["status"] == "paused"  # 재조정 상담이 들어 있어 재조정 결정까지 멈춘다
    assert decide_restructure(deps, case_id, "decline") is True
    assert loan(deps)["monitoring"]["status"] == "awaiting_resume"


def test_new_replay_rebuilds_the_ledger(mock_app):
    deps = make_deps(mock_app)
    approved_loan(deps)
    first = replay(deps)["replay_id"]
    second = replay(deps)["replay_id"]
    assert second != first
    schedule = loan(deps)["repayment"]["schedule"]
    assert schedule[-1]["case_id"] == f"rc-{second}-i3" and all(s["status"] != "scheduled" for s in schedule)
    assert [c["by"] for c in contacts(deps, second)] == ["reminder"] * 3


def test_reminder_is_not_resent_when_a_week_is_retried(mock_app):
    # 연락 기록을 쓴 뒤 대출 문서를 쓰기 전에 실패하면 같은 주차가 다시 온다. 안내를 두 번 보내지 않는다.
    deps = make_deps(mock_app, events=Outbox())
    approved_loan(deps)
    replay_id = replay(deps)["replay_id"]
    for week in (1, 2, 3, 4):
        process_week(deps, LOAN, replay_id, week)
    doc = loan(deps)
    doc["monitoring"]["processed_week"] = 3
    doc["monitoring"]["weeks"].pop()
    doc["repayment"]["schedule"][0].update(reminded_on=None, reminder_status=None)
    deps.store.upsert(LOANS, doc)
    assert process_week(deps, LOAN, replay_id, 4)["repayment"]["reminders"] == [1]
    assert [c["ref"] for c in contacts(deps, replay_id)] == [f"{replay_id}-i1"]
    assert loan(deps)["repayment"]["schedule"][0]["reminded_on"] == "2026-11-29"


def test_reminder_uses_simulated_10am(mock_app):
    # 자동 안내는 시뮬레이션 날짜 10:00 KST에 보낸 것으로 본다. 밤에 재생해도 시간대 규칙에 걸리지 않는다.
    deps = make_deps(mock_app)
    deps = replace(deps, clock=lambda: NOW.replace(hour=23))
    approved_loan(deps)
    replay_id = replay(deps)["replay_id"]
    first = contacts(deps, replay_id)[0]
    assert (first["by"], first["status"], first["reason"]) == ("reminder", "sent", None)
    assert first["at"] == datetime(2026, 11, 29, 10, 0, tzinfo=KST).isoformat()
    assert loan(deps)["repayment"]["schedule"][0]["reminder_status"] == "sent"


def test_unknown_loan_and_bad_messages(mock_app):
    deps = make_deps(mock_app)
    assert handle_replay(deps, json.dumps({"loan_id": "nope-1"})) is None
    assert handle_replay(deps, "not json") is None
    assert handle_repayment_updated(deps, json.dumps({"type": "repayment.updated", "loan_id": LOAN, "case_id": "x"})) is None
    assert handle_repayment_updated(deps, json.dumps({"type": "alert.raised", "loan_id": LOAN, "alert_id": "x"})) is None
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


def paused_inline(mock_app):
    deps = missing_first_due(make_deps(mock_app, agents=ScriptedAgents()))
    approved_loan(deps)
    started = replay(deps)
    assert loan(deps)["monitoring"]["flow_start"] == AS_OF.isoformat()  # 상환일은 흐름 시작일(=심사 기준일) 기준
    return deps, started["replay_id"]


def test_inline_replay_pauses_on_restructure_request_and_resumes(mock_app):
    deps, replay_id = paused_inline(mock_app)
    mon = loan(deps)["monitoring"]
    assert mon["status"] == "paused" and mon["paused_week"] == mon["processed_week"] == 5
    assert mon["pause_case_id"] == f"rc-{replay_id}-i1" and len(mon["weeks"]) == 5
    assert mon["holds"] == ["collections_proposal"]
    assert replay(deps) is None  # 멈춘 재생을 새 재생이 덮지 않는다
    assert replay(deps, action="resume", replay_id=replay_id) is None  # 결정 전에는 재개할 수 없다
    assert process_week(deps, LOAN, replay_id, 6) is None  # 멈춘 동안 들어온 주차 메시지는 무시
    assert decide_collections(deps, mon["pause_case_id"], include_hardship=True) is True
    assert replay(deps, action="resume", replay_id=replay_id) is None  # 재조정 결정 전에도 재개할 수 없다
    assert decide_restructure(deps, mon["pause_case_id"], "decline") is True
    assert loan(deps)["monitoring"]["status"] == "awaiting_resume"
    assert replay(deps, action="resume", replay_id="rp-other") is None
    resumed = replay(deps, action="resume", replay_id=replay_id)
    assert resumed["from_week"] == 6 and resumed["replay_id"] == replay_id
    pauses = resume_until_done(deps, replay_id)
    mon = loan(deps)["monitoring"]
    assert mon["status"] == "completed" and mon["processed_week"] == WEEKS and len(mon["weeks"]) == WEEKS
    assert mon["resumed_at"] == [NOW.isoformat()] * (1 + pauses) and mon["started_at"] == NOW.isoformat()
    assert [w["week"] for w in mon["weeks"]] == list(range(1, WEEKS + 1))
    assert replay(deps, action="resume", replay_id=replay_id) is None  # 두 번째 재개는 무시


def test_stop_ends_paused_replay_and_allows_a_new_one(mock_app):
    deps, replay_id = paused_inline(mock_app)
    assert replay(deps, action="stop", replay_id=replay_id) is None  # 결정 전에는 끝낼 수 없다
    settle_holds(deps)
    assert replay(deps, action="stop", replay_id=replay_id) == {"loan_id": LOAN, "replay_id": replay_id, "status": "stopped"}
    mon = loan(deps)["monitoring"]
    assert mon["status"] == "stopped" and mon["stopped_at"] == NOW.isoformat() and mon["processed_week"] == 5
    assert replay(deps, action="resume", replay_id=replay_id) is None
    assert replay(deps)["replay_id"] != replay_id  # 종료한 뒤에는 새 재생을 시작할 수 있다


@pytest.mark.parametrize("ended", ["expired", "revoked"])
def test_resume_after_underwriting_consent_ended_stops_with_reason(mock_app, ended):
    # 재조정을 기다리는 사이 심사 동의가 끝나면 원천 프로필이 지워져 남은 주차를 처리할 수 없다.
    # Service Bus 모드에서 재개를 받아 주면 주차 메시지가 계속 실패해 '재생 중'에 멈추므로 재개 전에 끝낸다.
    deps, replay_id = paused_inline(mock_app)
    settle_holds(deps)
    cid = loan(deps)["underwriting_consent_id"]
    consent = deps.store.read("consents", cid, cid)
    consent["status"] = ended
    deps.store.upsert("consents", consent)
    week = loan(deps)["monitoring"]["processed_week"]
    assert replay(deps, action="resume", replay_id=replay_id) is None
    mon = loan(deps)["monitoring"]
    assert (mon["status"], mon["stopped_at"], mon["processed_week"]) == ("stopped", NOW.isoformat(), week)
    assert "심사 동의가 끝나" in mon["error"]
    assert replay(deps) is None  # 새 재생도 같은 이유로 시작하지 않는다


@pytest.mark.parametrize("ended", ["expired", "revoked"])
def test_resume_after_monitoring_consent_ended_stops_with_reason(mock_app, ended):
    # 사후 동의가 끝나면 flow.json이 지워져 남은 주차를 읽을 수 없다. 재개 전에 이유를 남기고 끝낸다.
    deps, replay_id = paused_inline(mock_app)
    settle_holds(deps)
    cid = loan(deps)["monitoring"]["consent_id"]
    consent = deps.store.read("consents", cid, cid)
    consent["status"] = ended
    deps.store.upsert("consents", consent)
    assert replay(deps, action="resume", replay_id=replay_id) is None
    mon = loan(deps)["monitoring"]
    assert (mon["status"], mon["stopped_at"]) == ("stopped", NOW.isoformat())
    assert "사후 동의가 끝나" in mon["error"]


def test_decision_landing_between_pause_check_and_pause_write_still_awaits_resume(mock_app):
    # 주차 처리가 '대기 중' 요청을 읽은 직후 심사역 결정이 도착하면, 결정 쪽은 아직 '재생 중'인 대출을 보고
    # 재개 대기로 올리지 않는다. 뒤이어 멈춤을 쓰면 결정이 끝났는데도 '멈춤'에 영영 남는다.
    deps, replay_id = paused_inline(mock_app)
    doc = loan(deps)
    case_id = doc["monitoring"]["pause_case_id"]
    assert decide_collections(deps, case_id, include_hardship=True) is True  # 재조정 상담이 들어 있어 재조정 요청이 대기 중이다
    doc = loan(deps)
    doc["monitoring"].update(status="replaying", paused_week=None, pause_case_id=None, holds=[])
    deps.store.upsert(LOANS, doc)
    read = deps.store.read

    def racing_read(container, item_id, partition_key):
        got = read(container, item_id, partition_key)
        if container == ALERTS and item_id == case_id and deps.store.read is racing_read:
            deps.store.read = read
            assert decide_restructure(deps, case_id) is True
        return got

    deps.store.read = racing_read
    week = doc["monitoring"]["processed_week"]
    assert monitoring_module._after_week(deps, LOAN, replay_id, week, case_id) == "paused"
    mon = loan(deps)["monitoring"]
    assert (mon["status"], mon["pause_case_id"], mon["holds"]) == ("awaiting_resume", case_id, [])
    assert replay(deps, action="resume", replay_id=replay_id)["from_week"] == week + 1


def test_restructure_decision_for_other_alert_does_not_unpause(mock_app):
    deps, _ = paused_inline(mock_app)
    case_id = cases(deps)[0]["id"]
    other = {k: v for k, v in deepcopy(cases(deps)[0]).items() if k != "_etag"} | {"id": "rc-elsewhere"}
    deps.store.upsert(ALERTS, other)  # 아직 제안이 대기 중인 다른 추심 건
    doc = loan(deps)
    doc["monitoring"]["pause_case_id"] = "rc-elsewhere"
    deps.store.upsert(LOANS, doc)
    assert decide_collections(deps, case_id, include_hardship=True) is True
    assert decide_restructure(deps, case_id) is True
    mon = loan(deps)["monitoring"]
    assert mon["status"] == "paused" and mon["holds"][0] == "collections_proposal"


def test_refresh_hold_with_missing_pause_case_still_sees_loan_holds(mock_app):
    deps, _ = paused_inline(mock_app)
    doc = loan(deps)
    doc["monitoring"]["pause_case_id"] = "rc-missing"
    doc["repayment"]["offer"] = {"status": "open"}
    deps.store.upsert(LOANS, doc)
    monitoring_module._refresh_hold(deps, LOAN)
    mon = loan(deps)["monitoring"]
    assert (mon["status"], mon["holds"]) == ("awaiting_customer", ["customer_response"])


def fail_once(monkeypatch, name):
    real, calls = getattr(monitoring_module, name), []

    def flaky(*a, **k):
        if not calls:
            calls.append(1)
            raise RuntimeError("Cosmos 503")
        return real(*a, **k)

    monkeypatch.setattr(monitoring_module, name, flaky)


@pytest.mark.parametrize("name", ["_append_contact", "_refresh_hold"])
def test_redelivered_collections_decision_repairs_state_after_failure(mock_app, monkeypatch, name):
    deps, replay_id, case = missed_without_agents(mock_app)
    fail_once(monkeypatch, name)
    with pytest.raises(RuntimeError):
        decide_collections(deps, case["case_id"])
    assert deps.store.read(ALERTS, case["case_id"], LOAN)["proposal"]["status"] == "approved"
    assert loan(deps)["monitoring"]["status"] == "paused"
    assert decide_collections(deps, case["case_id"]) is False  # 큐 재전달: 다시 결정하지 않고 상태만 고친다
    assert decide_collections(deps, case["case_id"]) is False
    (sent,) = officer_contacts(deps, replay_id)
    assert sent["text"] == case["proposal"]["text"]
    mon = loan(deps)["monitoring"]
    assert (mon["status"], mon["holds"]) == ("awaiting_resume", [])


def test_contact_write_conflicts_raise_so_redelivery_repairs(mock_app, monkeypatch):
    deps, replay_id, case = missed_without_agents(mock_app)
    real = deps.store.replace_batch
    monkeypatch.setattr(deps.store, "replace_batch", lambda *args: False)
    with pytest.raises(RuntimeError):
        decide_collections(deps, case["case_id"])
    monkeypatch.setattr(deps.store, "replace_batch", real)
    assert officer_contacts(deps, replay_id) == []
    assert decide_collections(deps, case["case_id"]) is False
    assert len(officer_contacts(deps, replay_id)) == 1
    assert loan(deps)["monitoring"]["status"] == "awaiting_resume"


def test_payoff_wins_between_validation_and_contact_commit(mock_app, monkeypatch):
    deps, rid, case = missed_without_agents(mock_app)
    store = deps.store
    races = []

    def pay_before_contact(docs):
        if races or not any(d["id"] == f"contacts-{rid}" for d in docs):
            return
        races.append(True)
        monitoring_module._adjust_case(deps, LOAN, case["id"], 0)
        doc = loan(deps)
        doc["repayment"]["schedule"][0].update(status="paid", balance_krw=0)
        store.upsert(LOANS, doc)

    replace_one, replace_batch = store.replace, store.replace_batch

    def single(c, d, tag):
        pay_before_contact([d])
        return replace_one(c, d, tag)

    def batch(c, pk, docs):
        pay_before_contact(docs)
        return replace_batch(c, pk, docs)

    monkeypatch.setattr(store, "replace", single)
    monkeypatch.setattr(store, "replace_batch", batch)
    assert decide_collections(deps, case["id"])
    assert races and not officer_contacts(deps, rid)
    assert not decide_collections(deps, case["id"]) and not officer_contacts(deps, rid)


def test_reminder_retry_preserves_concurrent_officer_contact(mock_app, monkeypatch):
    deps = make_deps(mock_app, events=Outbox())
    approved_loan(deps)
    rid = replay(deps)["replay_id"]
    for week in range(1, 4):
        process_week(deps, LOAN, rid, week)
    real = monitoring_module.remind
    officer = {"ref": "rc-earlier", "by": "officer", "on": "2026-11-28", "status": "sent"}

    def interleaved(*args, **kwargs):
        monkeypatch.setattr(monitoring_module, "remind", real)
        history = monitoring_module._contacts_doc(deps, LOAN, rid)
        history["contacts"].append(officer)
        deps.store.upsert(ALERTS, history)
        return real(*args, **kwargs)

    monkeypatch.setattr(monitoring_module, "remind", interleaved)
    with pytest.raises(RuntimeError, match="연락 기록 충돌"):
        process_week(deps, LOAN, rid, 4)
    process_week(deps, LOAN, rid, 4)
    assert officer_contacts(deps, rid) == [officer]
    assert len(contacts(deps, rid)) == 2


def test_delivery_wins_before_payoff_cas_and_preserves_delivery_receipt(mock_app, monkeypatch):
    deps, rid, case = missed_without_agents(mock_app)
    fail_once(monkeypatch, "_append_contact")
    with pytest.raises(RuntimeError, match="503"):
        decide_collections(deps, case["id"])
    store = deps.store
    replace_one = store.replace

    def deliver_before_invalidation(container, doc, etag):
        if doc["id"] == case["id"] and doc.get("balance_krw") == 0:
            monkeypatch.setattr(store, "replace", replace_one)
            assert not decide_collections(deps, case["id"])
        return replace_one(container, doc, etag)

    monkeypatch.setattr(store, "replace", deliver_before_invalidation)
    assert not monitoring_module._adjust_case(deps, LOAN, case["id"], 0)
    contact, = officer_contacts(deps, rid)
    stored = store.read(ALERTS, case["id"], LOAN)
    assert stored["balance_krw"] == 0 and stored["proposal"]["contact"] == contact
    assert not decide_collections(deps, case["id"]) and officer_contacts(deps, rid) == [contact]


@pytest.mark.parametrize("recover", [False, True])
def test_approval_counts_contacts_through_processed_end_of_origin_week(mock_app, monkeypatch, recover):
    deps, rid, case = missed_without_agents(mock_app)
    if recover:
        fail_once(monkeypatch, "_append_contact")
        with pytest.raises(RuntimeError, match="503"):
            decide_collections(deps, case["id"])
    log = deps.store.read(ALERTS, f"contacts-{rid}", LOAN)
    log["contacts"].extend({"on": "2026-12-05", "status": "sent", "by": "reminder", "ref": str(i)}
                           for i in range(7))
    deps.store.upsert(ALERTS, log)
    decide_collections(deps, case["id"])
    sent, = officer_contacts(deps, rid)
    assert (sent["on"], sent["status"], sent["reason"]) == ("2026-12-06", "deferred", "weekly_limit")


def test_undelivered_approval_rechecks_contact_limit_on_retry(mock_app, monkeypatch):
    deps, rid, case = missed_without_agents(mock_app)
    fail_once(monkeypatch, "_append_contact")
    with pytest.raises(RuntimeError, match="503"):
        decide_collections(deps, case["id"])
    history = deps.store.read(ALERTS, f"contacts-{rid}", LOAN)
    history["contacts"].extend({"on": str(FIRST_DUE), "status": "sent", "by": "reminder", "ref": str(i)}
                               for i in range(7))
    deps.store.upsert(ALERTS, history)
    assert not decide_collections(deps, case["id"])
    contact, = officer_contacts(deps, rid)
    assert (contact["status"], contact["reason"]) == ("deferred", "weekly_limit")
    assert deps.store.read(ALERTS, case["id"], LOAN)["proposal"]["contact"] == contact


def test_undelivered_approval_uses_current_replay_day_if_replay_advanced(mock_app, monkeypatch):
    deps, rid, case = missed_without_agents(mock_app)
    fail_once(monkeypatch, "_append_contact")
    with pytest.raises(RuntimeError, match="503"):
        decide_collections(deps, case["id"])
    doc = loan(deps)
    doc["monitoring"]["processed_week"] += 1
    deps.store.upsert(LOANS, doc)
    history = deps.store.read(ALERTS, f"contacts-{rid}", LOAN)
    history["contacts"].extend({"on": "2026-12-10", "status": "sent", "by": "reminder", "ref": str(i)}
                               for i in range(7))
    deps.store.upsert(ALERTS, history)
    assert not decide_collections(deps, case["id"])
    contact, = officer_contacts(deps, rid)
    assert (contact["status"], contact["reason"], contact["on"]) == ("deferred", "weekly_limit", "2026-12-13")
    assert deps.store.read(ALERTS, case["id"], LOAN)["installment"]["due_on"] == str(FIRST_DUE)


@pytest.mark.parametrize("balance", [0, 17])
@pytest.mark.parametrize("delivered", [False, True])
def test_recovery_after_payment_preserves_only_already_delivered_contact(mock_app, monkeypatch, balance, delivered):
    deps, rid, case = missed_without_agents(mock_app)
    if delivered:
        assert decide_collections(deps, case["id"])
    else:
        fail_once(monkeypatch, "_append_contact")
        with pytest.raises(RuntimeError, match="503"):
            decide_collections(deps, case["id"])
    history = officer_contacts(deps, rid)
    doc = loan(deps)
    doc["repayment"]["schedule"][0].update(status="partial" if balance else "paid", balance_krw=balance)
    deps.store.upsert(LOANS, doc)
    assert not decide_collections(deps, case["id"])
    assert officer_contacts(deps, rid) == history
    proposal = deps.store.read(ALERTS, case["id"], LOAN)["proposal"]
    if not delivered:
        assert not proposal.get("contact") and proposal["status"] == "approved"


def test_redelivered_restructure_decision_repairs_hold(mock_app, monkeypatch):
    deps, _ = paused_inline(mock_app)
    case_id = loan(deps)["monitoring"]["pause_case_id"]
    assert decide_collections(deps, case_id, include_hardship=True) is True
    fail_once(monkeypatch, "_refresh_hold")
    with pytest.raises(RuntimeError):
        decide_restructure(deps, case_id)
    assert loan(deps)["monitoring"]["status"] == "paused"
    assert decide_restructure(deps, case_id) is False
    mon = loan(deps)["monitoring"]
    assert (mon["status"], mon["holds"]) == ("awaiting_resume", [])


def test_restructure_decision_keeps_concurrent_alert_update(mock_app, monkeypatch):
    deps, _ = paused_inline(mock_app)
    case_id = loan(deps)["monitoring"]["pause_case_id"]
    assert decide_collections(deps, case_id, include_hardship=True) is True
    real_read, raced = deps.store.read, []

    def racing_read(container, item_id, partition_key):
        doc = real_read(container, item_id, partition_key)
        if item_id == case_id and not raced:
            raced.append(1)  # 읽은 직후 다른 처리가 같은 문서를 고친다
            concurrent = real_read(container, item_id, partition_key)
            concurrent["event_error"] = "동시 수정"
            deps.store.upsert(container, concurrent)
        return doc

    monkeypatch.setattr(deps.store, "read", racing_read)
    assert decide_restructure(deps, case_id) is True
    doc = real_read(ALERTS, case_id, LOAN)
    assert doc["event_error"] == "동시 수정" and doc["restructure"]["status"] == "approved"
    assert doc["status"] == "restructure_approved"


def test_retried_week_reuses_stored_alert_without_reinvestigating(mock_app, monkeypatch):
    agents = HighAgents()
    deps = make_deps(mock_app, events=Outbox(), agents=agents)
    approved_loan(deps)
    replay_id = replay(deps)["replay_id"]
    real, failed = monitoring_module._repayment_week, []

    def flaky(*args):
        entry = args[-1]
        if entry["alert_id"] and not failed:
            failed.append(entry["week"])  # 경보를 쓴 뒤 대출 문서를 쓰기 전에 실패한다
            raise RuntimeError("Cosmos 503")
        return real(*args)

    monkeypatch.setattr(monitoring_module, "_repayment_week", flaky)
    for week in range(1, WEEKS + 1):
        try:
            process_week(deps, LOAN, replay_id, week)
        except RuntimeError:
            break
    (week,) = failed
    alert_id = f"ew-{replay_id}-w{week:02d}"
    doc = deps.store.read(ALERTS, alert_id, LOAN)
    doc.update(status="acknowledged", collections={"case_id": "rc-x"})
    deps.store.upsert(ALERTS, doc)
    investigations = agents.calls.count(EW_AGENT)
    entry = process_week(deps, LOAN, replay_id, week)
    assert agents.calls.count(EW_AGENT) == investigations
    assert entry["alert_id"] == alert_id and entry["investigation"]["decision"] == "alert"
    stored = deps.store.read(ALERTS, alert_id, LOAN)
    assert (stored["status"], stored["collections"], stored["created_at"]) == ("acknowledged", {"case_id": "rc-x"}, doc["created_at"])
    assert loan(deps)["monitoring"]["alert_ids"].count(alert_id) == 1


def missed_without_agents(mock_app, **fields):
    deps = missing_first_due(make_deps(mock_app))
    if fields:
        deps = replace(deps, **fields)
    approved_loan(deps)
    replay_id = replay(deps)["replay_id"]
    return deps, replay_id, cases(deps)[0]


@pytest.mark.parametrize("producer", [EW_AGENT, COLLECTIONS_AGENT])
@pytest.mark.parametrize("confirmed", [False, True])
def test_monitoring_withdrawal_during_remote_call(mock_app, producer, confirmed, caplog):
    from agents.runtime import AgentError
    from journey.consent import IncompleteDeletion, delete_consent_data, send_consent_notice
    from tests.orchestrator.test_consent_lifecycle import FoundryRunner, MemoryRaw as DeletableRaw, end

    deps = missing_first_due(replace(make_deps(mock_app), raw=DeletableRaw()))
    approved_loan(deps)

    class Withdrawing(FoundryRunner):
        def run(self, spec, text, handlers):
            if spec.name != producer:
                return ScriptedAgents().run(spec, text, handlers)
            cid = loan(deps)["monitoring"]["consent_id"]
            with pytest.raises(IncompleteDeletion):
                delete_consent_data(worker, end(cid))
            with pytest.raises(LookupError):
                send_consent_notice(worker, {"consent_id": cid, "kind": "deletion"})
            if not confirmed:
                raise AgentError("synthetic-private-error", "conv_partial", ("known_partial",))
            return ScriptedAgents().run(spec, text, handlers)

    worker = replace(deps, agents=Withdrawing())
    replay(worker)
    assert loan(deps)["monitoring"]["status"] == "stopped"
    cid = loan(deps)["monitoring"]["consent_id"]
    proof = deps.store.read("deletion_proofs", cid, cid)
    assert proof["complete"] is confirmed and proof["event_published"] is False
    notice = {"consent_id": cid, "kind": "deletion"}
    if confirmed:
        assert send_consent_notice(deps, notice)["kind"] == "deletion"
    else:
        assert {"conv_partial", "known_partial"} <= {i["target"] for i in proof["items"] if i["verified"]}
        with pytest.raises(LookupError):
            send_consent_notice(deps, notice)
    assert "synthetic-private-error" not in caplog.text


@pytest.mark.parametrize("producer", [EW_AGENT, COLLECTIONS_AGENT])
@pytest.mark.parametrize("timing", ["during", "before", "replacement", "conflict"])
def test_service_bus_withdrawal_stops_only_matching_delivery(mock_app, monkeypatch, producer, timing):
    from agents.runtime import AgentError
    from journey.activities import Conflict
    from journey.consent import IncompleteDeletion, delete_consent_data
    from tests.orchestrator.test_consent_lifecycle import FoundryRunner, MemoryRaw as DeletableRaw, end

    outbox = Outbox()
    deps = missing_first_due(replace(make_deps(mock_app, events=outbox), raw=DeletableRaw()))
    approved_loan(deps)
    replay_id = replay(deps)["replay_id"]
    cid = loan(deps)["monitoring"]["consent_id"]
    if producer == COLLECTIONS_AGENT:
        pump(deps, outbox, topics=(TOPIC_FLOW,))
    topic, deliver = ((TOPIC_FLOW, handle_flow_update) if producer == EW_AGENT
                      else (TOPIC_REPAYMENT, handle_repayment_updated))
    calls = []

    class Withdrawing(FoundryRunner):
        def run(self, spec, text, handlers):
            assert spec.name == producer
            calls.append(spec.name)
            with pytest.raises(IncompleteDeletion):
                delete_consent_data(worker, end(cid))
            raise AgentError("synthetic-private-error", "conv_partial")

    worker = replace(deps, agents=Withdrawing())
    def forbid_raw(*args):
        pytest.fail("A terminal/stale delivery must not access deleted raw data")

    replace_doc = deps.store.replace
    def race(container, doc, etag):
        if container == LOANS and doc["monitoring"].get("status") == "stopped":
            if timing == "conflict":
                return False
            if timing == "replacement":
                newer = loan(deps)
                newer["monitoring"].update(replay_id="replacement", consent_id="fresh-grant")
                deps.store.upsert(LOANS, newer)
                return False
        return replace_doc(container, doc, etag)

    if timing == "before":
        delete_consent_data(deps, end(cid))
        monkeypatch.setattr(deps.raw, "get_json", forbid_raw)
    monkeypatch.setattr(deps.store, "replace", race)
    conflicts = 0
    for _ in range(WEEKS):
        body = outbox.bodies(topic)[-1]
        published = len(flows(outbox))
        if timing == "conflict":
            try:
                result = deliver(worker, body)
            except Conflict:
                conflicts += 1
                assert calls and loan(deps)["monitoring"]["status"] == "replaying"
                monkeypatch.setattr(deps.store, "replace", replace_doc)
                monkeypatch.setattr(deps.raw, "get_json", forbid_raw)
                result = deliver(worker, body)
        else:
            result = deliver(worker, body)
        if calls or timing == "before":
            break
    assert result is None and len(flows(outbox)) == published
    monkeypatch.setattr(deps.raw, "get_json", forbid_raw)
    assert deliver(worker, body) is None and len(flows(outbox)) == published
    mon = loan(deps)["monitoring"]
    assert calls == ([] if timing == "before" else [producer])
    assert conflicts == int(timing == "conflict")
    if timing == "replacement":
        assert (mon["replay_id"], mon["status"]) == ("replacement", "replaying")
    else:
        assert (mon["replay_id"], mon["status"], mon["error"]) == (replay_id, "stopped", "monitoring_consent_ended")


@pytest.mark.parametrize("change", ["underwriting", "monitoring", "replacement", "read_failure", "missing"])
def test_next_week_checks_fresh_replay_and_both_grants_after_agentless_work(mock_app, monkeypatch, change):
    from journey.consent import delete_consent_data
    from tests.orchestrator.test_consent_lifecycle import MemoryRaw as DeletableRaw, end

    outbox = Outbox()
    deps = replace(make_deps(mock_app, events=outbox), raw=DeletableRaw())
    approved_loan(deps)
    replay(deps)
    body, = outbox.bodies(TOPIC_FLOW)
    after_week = monitoring_module._after_week
    def interleave(*args):
        decision = after_week(*args)
        assert decision == "next"
        current = loan(deps)
        mon = current["monitoring"]
        assert mon["processed_week"] == 1
        if change == "replacement":
            mon.update(replay_id="replacement", processed_week=0)
            deps.store.upsert(LOANS, current)
        elif change in ("read_failure", "missing"):
            read = deps.store.read
            def fail(container, key, partition):
                if container == "consents":
                    if change == "missing":
                        return None
                    raise TimeoutError("synthetic checkpoint outage")
                return read(container, key, partition)
            monkeypatch.setattr(deps.store, "read", fail)
        else:
            cid = current["underwriting_consent_id"] if change == "underwriting" else mon["consent_id"]
            delete_consent_data(deps, end(cid))
        return decision
    monkeypatch.setattr(monitoring_module, "_after_week", interleave)
    if change in ("read_failure", "missing"):
        with pytest.raises(TimeoutError if change == "read_failure" else RuntimeError):
            handle_flow_update(deps, body)
    else:
        handle_flow_update(deps, body)
    assert outbox.bodies(TOPIC_FLOW) == [body]
    assert loan(deps)["monitoring"]["status"] == ("stopped" if change in ("underwriting", "monitoring") else "replaying")


@pytest.mark.parametrize("status", ["failed", "completed"])
@pytest.mark.parametrize("race", [False, True])
def test_withdrawal_cannot_overwrite_terminal_repayment_delivery(mock_app, monkeypatch, status, race):
    from journey.consent import delete_consent_data
    from tests.orchestrator.test_consent_lifecycle import MemoryRaw as DeletableRaw, end

    outbox = Outbox()
    deps = missing_first_due(replace(make_deps(mock_app, events=outbox), raw=DeletableRaw()))
    approved_loan(deps)
    replay(deps)
    pump(deps, outbox, topics=(TOPIC_FLOW,))
    body, = outbox.bodies(TOPIC_REPAYMENT)
    delete_consent_data(deps, end(loan(deps)["monitoring"]["consent_id"]))
    def terminal():
        current = loan(deps)
        current["monitoring"].update(status=status, error="original_result")
        deps.store.upsert(LOANS, current)
    if race:
        replace_doc = deps.store.replace
        def raced(container, doc, etag):
            if container == LOANS:
                monkeypatch.setattr(deps.store, "replace", replace_doc)
                terminal()
            return replace_doc(container, doc, etag)
        monkeypatch.setattr(deps.store, "replace", raced)
    else:
        terminal()
    monkeypatch.setattr(deps.raw, "get_json", lambda *_: pytest.fail("Read deleted raw data"))
    before = len(outbox.sent)
    for _ in range(2):
        assert handle_repayment_updated(deps, body) is None
        mon = loan(deps)["monitoring"]
        assert (mon["status"], mon["error"]) == (status, "original_result")
    assert len(outbox.sent) == before


def test_collections_crash_and_redelivery_cannot_certify_first_attempt(mock_app, monkeypatch):
    from journey.activities import Conflict
    from journey.consent import IncompleteDeletion, delete_consent_data
    from tests.orchestrator.test_consent_lifecycle import FoundryRunner, MemoryRaw as DeletableRaw, end

    deps, _, case = missed_without_agents(mock_app, raw=DeletableRaw())
    # Deliver the case as it stood before a proposal was saved.
    deps.store.upsert(ALERTS, dict(case, collections=None, status="open"))
    cid = loan(deps)["monitoring"]["consent_id"]
    calls = []

    class LostWorker(BaseException):
        pass

    class Interrupted(FoundryRunner):
        def run(self, *args):
            calls.append(True)
            if len(calls) == 1:
                raise LostWorker
            return ScriptedAgents().run(*args)

    worker = replace(deps, agents=Interrupted())
    def deliver():
        return monitoring_module.run_collections(worker, LOAN, case["id"])

    with monkeypatch.context() as m:
        m.setattr(deps.store, "replace", lambda *args: False)
        with pytest.raises(Conflict):
            deliver()
    assert not calls
    with pytest.raises(LostWorker):
        deliver()
    assert deliver() and deliver() is None
    with pytest.raises(IncompleteDeletion):
        delete_consent_data(worker, end(cid))


def test_missed_payment_creates_proposal_and_pauses_without_contact(mock_app):
    deps = missing_first_due(make_deps(mock_app)); approved_loan(deps); replay(deps)
    case, mon = cases(deps)[0], loan(deps)["monitoring"]
    assert case["status"] == "proposal_pending" and case["proposal"]["text"]
    assert mon["status"] == "paused" and mon["holds"] == ["collections_proposal"]
    assert not [c for c in contacts(deps, mon["replay_id"]) if c["by"] in ("agent", "fallback", "officer")]


@pytest.mark.parametrize("live,accepted", [(70000, False), (70001, True)])
def test_usd_collection_worker_rechecks_live_cent_balance(mock_app, live, accepted):
    from shared.contracts import USD_V1
    deps, meta = make_deps(mock_app), USD_V1.model_dump()
    cid, rid = "rc-rp-1-i1", "rp-1"
    deps.store.upsert(LOANS, dict(meta, id=LOAN, applicant_id="same", monitoring=dict(replay_id=rid, processed_week=0),
        repayment={"schedule": [dict(case_id=cid, status="missed", balance_cents=live, due_on="2026-11-02")]}))
    deps.store.upsert(ALERTS, dict(meta, id=cid, case_id=cid, loan_id=LOAN, replay_id=rid,
        kind="repayment_case", week=1, missed_count=1, status="proposal_pending",
        installment={"no": 1, "due_on": "2026-11-02", "amount_cents": 70001},
        proposal=dict(status="pending_officer", script_id="missed_payment", evidence=dict(meta, amount_cents=70001))))
    text = "Your balance is $700.01."
    assert decide_collections(deps, cid, "modify", text=text, **meta) is accepted
    assert [c["text"] for c in officer_contacts(deps, rid)] == ([text] if accepted else [])


def test_approve_sends_exact_text_and_awaits_resume(mock_app):
    deps, replay_id, case = missed_without_agents(mock_app)
    assert decide_collections(deps, case["case_id"], "approve") is True
    (sent,) = officer_contacts(deps, replay_id)
    assert (sent["by"], sent["status"], sent["officer_id"]) == ("officer", "sent", "officer-1")
    assert sent["text"] == case["proposal"]["text"] and sent["ref"] == case["case_id"]
    doc = deps.store.read(ALERTS, case["case_id"], LOAN)
    assert doc["status"] == "contacted" and doc["proposal"]["status"] == "approved" and doc["restructure"] is None
    mon = loan(deps)["monitoring"]
    assert (mon["status"], mon["holds"]) == ("awaiting_resume", [])


@pytest.mark.parametrize("balance", [0, 17])
@pytest.mark.parametrize("approval_interrupted", [False, True])
def test_next_week_payment_invalidates_prior_unsent_proposal(mock_app, monkeypatch, balance, approval_interrupted):
    deps, rid, original = missed_without_agents(mock_app)
    if approval_interrupted:
        fail_once(monkeypatch, "_append_contact")
        with pytest.raises(RuntimeError, match="503"):
            decide_collections(deps, original["id"])
    doc = loan(deps)
    mon, led = doc["monitoring"], doc["repayment"]
    # Older/imported replay with an outstanding proposal from a previous week.
    mon.update(status="replaying", holds=[])
    deps.store.upsert(LOANS, doc)
    container = f"consent-{mon['consent_id']}"
    flow = deps.raw.get_json(container, "flow.json")
    flow.append({**flow[-1], "txn_id": "late", "booked_on": "2026-12-09", "description": LENDER_DEBIT,
                 "txn_type_code": "AUTO_DEBIT", "amount_krw": balance - led["installment_krw"]})
    deps.raw.put_json(container, "flow.json", flow)
    entry = process_week(deps, LOAN, rid, 6)
    row = loan(deps)["repayment"]["schedule"][0]
    assert row["balance_krw"] == balance and row["paid_krw"] == led["installment_krw"] - balance
    assert not decide_collections(deps, original["id"]) and not officer_contacts(deps, rid)
    assert deps.store.read(ALERTS, original["id"], LOAN)["proposal"]["status"] != "pending_officer"
    if approval_interrupted:
        previous = deps.store.read(ALERTS, original["id"], LOAN)["proposal"]
        assert previous["status"] == "approved" and previous["contact"] is None
    if balance:
        assert row["status"] == "partial" and entry["case_ids"] == [row["case_id"]]
        history = deps.store.read(ALERTS, f"contacts-{rid}", LOAN)
        history["contacts"].extend({"on": "2026-12-10", "status": "sent", "by": "reminder", "ref": str(i)}
                                   for i in range(6))
        deps.store.upsert(ALERTS, history)
        monitoring_module.run_collections(deps, LOAN, row["case_id"])
        case = deps.store.read(ALERTS, row["case_id"], LOAN)
        evidence = case["proposal"]["evidence"]
        assert evidence["amount_krw"] == 17 and evidence["missed_count"] == 1
        assert evidence["due_on"] == str(FIRST_DUE) and evidence["contacts_7d"] == 6
        assert evidence["contact_on"] == "2026-12-13"
        history["contacts"].append({"on": "2026-12-12", "status": "sent", "by": "reminder", "ref": "7"})
        deps.store.upsert(ALERTS, history)
        assert decide_collections(deps, case["id"])
        contact, = officer_contacts(deps, rid)
        assert (contact["status"], contact["reason"], contact["on"]) == ("deferred", "weekly_limit", "2026-12-13")
    else:
        assert row["status"] == "paid" and row["case_id"] == original["id"]


def test_all_same_week_cases_publish_and_block_resume_until_both_decided(mock_app):
    outbox = Outbox()
    deps = missing_first_due(make_deps(mock_app, events=outbox))
    approved_loan(deps)
    rid = replay(deps)["replay_id"]
    doc = loan(deps)
    doc["repayment"]["schedule"][1]["due_on"] = str(FIRST_DUE)
    doc["repayment"]["installment_krw"] = 500_000
    container = f"consent-{doc['monitoring']['consent_id']}"
    flow = deps.raw.get_json(container, "flow.json")
    flow.append({**flow[-1], "txn_id": "one-won", "booked_on": str(FIRST_DUE), "loan_id": LOAN,
                 "description": LENDER_DEBIT, "txn_type_code": "AUTO_DEBIT", "amount_krw": -1})
    deps.raw.put_json(container, "flow.json", flow)
    deps.store.upsert(LOANS, doc)
    pump(deps, outbox)
    ids = [json.loads(body)["case_id"] for body in outbox.bodies(TOPIC_REPAYMENT)]
    assert ids == [f"rc-{rid}-i1", f"rc-{rid}-i2"]
    assert [deps.store.read(ALERTS, cid, LOAN)["missed_count"] for cid in ids] == [2, 2]
    assert [json.loads(body)["balance_krw"] for body in outbox.bodies(TOPIC_REPAYMENT)] == [499_999, 500_000]
    assert loan(deps)["monitoring"]["weeks"][-1]["case_ids"] == ids
    assert decide_collections(deps, ids[0])
    assert loan(deps)["monitoring"]["status"] == "paused"
    assert decide_collections(deps, ids[1])
    assert loan(deps)["monitoring"]["status"] == "awaiting_resume"


def test_cured_installment_still_counts_when_next_installment_is_missed(mock_app):
    outbox = Outbox()
    deps = missing_first_due(make_deps(mock_app, events=outbox))
    approved_loan(deps)
    rid = replay(deps)["replay_id"]
    pump(deps, outbox)
    assert decide_collections(deps, f"rc-{rid}-i1")
    replay(deps, action="resume", replay_id=rid)
    pump(deps, outbox)
    rows = loan(deps)["repayment"]["schedule"]
    assert rows[0]["status"] == "paid" and rows[1]["status"] == "missed"
    assert deps.store.read(ALERTS, rows[1]["case_id"], LOAN)["missed_count"] == 2


@pytest.mark.parametrize("balance", [0, 7])
def test_approval_rechecks_ledger_if_pending_quote_lags_payment(mock_app, balance):
    deps, rid, case = missed_without_agents(mock_app)
    doc = loan(deps)
    doc["repayment"]["schedule"][0].update(status="partial" if balance else "paid", balance_krw=balance)
    deps.store.upsert(LOANS, doc)
    assert not decide_collections(deps, case["id"])
    assert not officer_contacts(deps, rid)


def test_duplicate_collections_decision_sends_once(mock_app):
    deps, replay_id, case = missed_without_agents(mock_app)
    assert decide_collections(deps, case["case_id"]) is True
    before = deps.store.read(ALERTS, case["case_id"], LOAN)
    assert decide_collections(deps, case["case_id"]) is False  # 큐 재전달
    assert decide_collections(deps, case["case_id"], "reject", reason="중복") is False
    assert len(officer_contacts(deps, replay_id)) == 1
    after = deps.store.read(ALERTS, case["case_id"], LOAN)
    assert {k: v for k, v in after.items() if k != "_etag"} == {k: v for k, v in before.items() if k != "_etag"}
    assert loan(deps)["monitoring"]["status"] == "awaiting_resume"


def test_modify_with_banned_text_is_dropped(mock_app):
    deps, replay_id, case = missed_without_agents(mock_app)
    text = "고객님, 상환하지 않으면 법적 조치를 진행합니다."
    assert decide_collections(deps, case["case_id"], "modify", text=text) is False
    assert officer_contacts(deps, replay_id) == []
    doc = deps.store.read(ALERTS, case["case_id"], LOAN)
    assert doc["proposal"]["status"] == "pending_officer" and doc["status"] == "proposal_pending"
    assert loan(deps)["monitoring"]["status"] == "paused"
    # 고친 문구가 정책을 통과하면 그 문구를 보낸다.
    ok = "고객님, 에이엠에이렌딩입니다. 1회차 상환금 자동이체가 처리되지 않아 안내드립니다."
    assert decide_collections(deps, case["case_id"], "modify", text=ok) is True
    (sent,) = officer_contacts(deps, replay_id)
    assert sent["text"] == ok
    assert deps.store.read(ALERTS, case["case_id"], LOAN)["proposal"]["sent_text"] == ok


def test_reject_sends_nothing_and_awaits_resume(mock_app):
    deps, replay_id, case = missed_without_agents(mock_app)
    assert decide_collections(deps, case["case_id"], "reject") is False  # 반려 사유 필수
    assert loan(deps)["monitoring"]["status"] == "paused"
    assert decide_collections(deps, case["case_id"], "reject", reason="고객과 통화로 해결") is True
    assert officer_contacts(deps, replay_id) == []
    doc = deps.store.read(ALERTS, case["case_id"], LOAN)
    assert doc["status"] == "rejected" and doc["proposal"]["decision_reason"] == "고객과 통화로 해결"
    assert loan(deps)["monitoring"]["status"] == "awaiting_resume"


def test_approve_sends_text_only_and_skips_restructure_by_default(mock_app):
    deps, replay_id = paused_inline(mock_app)
    case_id = loan(deps)["monitoring"]["pause_case_id"]
    assert deps.store.read(ALERTS, case_id, LOAN)["proposal"]["hardship"]
    assert decide_collections(deps, case_id) is True
    doc = deps.store.read(ALERTS, case_id, LOAN)
    assert doc["restructure"] is None and doc["status"] == "contacted"
    assert [c["script_id"] for c in officer_contacts(deps, replay_id)] == ["hardship_offer"]
    assert loan(deps)["monitoring"]["status"] == "awaiting_resume"


def test_approve_with_hardship_waits_for_restructure(mock_app):
    deps, replay_id = paused_inline(mock_app)
    case_id = loan(deps)["monitoring"]["pause_case_id"]
    assert decide_collections(deps, case_id, include_hardship=True) is True
    doc = deps.store.read(ALERTS, case_id, LOAN)
    assert doc["status"] == "restructure_pending" and doc["restructure"]["request_id"] == f"rs-{case_id}"
    assert doc["restructure"]["status"] == "pending_officer"
    assert [c["script_id"] for c in officer_contacts(deps, replay_id)] == ["hardship_offer"]
    mon = loan(deps)["monitoring"]
    assert (mon["status"], mon["holds"]) == ("paused", ["restructure"])
    assert decide_restructure(deps, case_id) is True
    mon = loan(deps)["monitoring"]
    assert (mon["status"], mon["holds"]) == ("awaiting_resume", [])


def test_modify_can_drop_hardship_and_skip_restructure(mock_app):
    deps, replay_id = paused_inline(mock_app)
    case_id = loan(deps)["monitoring"]["pause_case_id"]
    text = deps.store.read(ALERTS, case_id, LOAN)["proposal"]["text"]
    assert decide_collections(deps, case_id, "modify", text=text, include_hardship=False) is True
    doc = deps.store.read(ALERTS, case_id, LOAN)
    assert doc["restructure"] is None and doc["status"] == "contacted"
    assert loan(deps)["monitoring"]["status"] == "awaiting_resume"


def test_officer_approval_at_night_is_deferred_but_decided(mock_app):
    deps, replay_id, case = missed_without_agents(mock_app, clock=lambda: NOW.replace(hour=23))
    assert decide_collections(deps, case["case_id"]) is True
    (sent,) = officer_contacts(deps, replay_id)
    assert (sent["status"], sent["reason"]) == ("deferred", "contact_hours")
    doc = deps.store.read(ALERTS, case["case_id"], LOAN)
    assert doc["status"] == "deferred" and doc["proposal"]["status"] == "approved"
    assert loan(deps)["monitoring"]["status"] == "awaiting_resume"


def test_legacy_restructure_message_without_kind_still_decides(mock_app):
    deps, _ = paused_inline(mock_app)
    case_id = loan(deps)["monitoring"]["pause_case_id"]
    assert decide_collections(deps, case_id, include_hardship=True) is True
    body = {"loan_id": LOAN, "alert_id": case_id, "decision": "approve", "officer_id": "officer-1", "reason": "유예"}
    assert "kind" not in body
    assert handle_restructure_decision(deps, json.dumps(body)) is True
    assert deps.store.read(ALERTS, case_id, LOAN)["status"] == "restructure_approved"
    assert loan(deps)["monitoring"]["status"] == "awaiting_resume"


def test_collections_decision_for_case_without_proposal_is_dropped(mock_app):
    # 옛 추심 건(proposal 없음)과 없는 건에 온 추심 결정은 버린다.
    deps, replay_id, case = missed_without_agents(mock_app)
    doc = deps.store.read(ALERTS, case["case_id"], LOAN)
    doc.pop("proposal")
    deps.store.upsert(ALERTS, doc)
    assert decide_collections(deps, case["case_id"]) is False
    assert decide_collections(deps, "rc-missing") is False
    assert officer_contacts(deps, replay_id) == []


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "plan_choice", "loan_id": LOAN, "offer_id": "of-1", "option": "move"},
        {"kind": "plan_change", "loan_id": LOAN, "change_id": "pc-1", "decision": "approve", "officer_id": "o-1"},
        {"kind": "offer_expire", "loan_id": LOAN, "offer_id": "of-1", "officer_id": "o-1"},
        {"kind": "bogus", "loan_id": LOAN},
    ],
)
def test_plan_messages_without_matching_offer_are_dropped(mock_app, body):
    deps, _, _ = missed_without_agents(mock_app)
    before = loan(deps)["monitoring"]
    assert handle_restructure_decision(deps, json.dumps(body)) is False
    assert loan(deps)["monitoring"] == before


@pytest.mark.parametrize("fields", [{"action": "resume"}, {"action": "stop"}, {"action": "pause", "replay_id": "rp-1"},
                                    {"action": "resume", "replay_id": ""}])
def test_invalid_resume_stop_requests_are_dropped(mock_app, fields):
    deps, _ = paused_inline(mock_app)
    before = loan(deps)["monitoring"]
    assert replay(deps, **fields) is None
    assert loan(deps)["monitoring"] == before


def recording_deps(mock_app, fail_on=(), **fields):
    deps = make_deps(mock_app, **fields)
    return replace(deps, mock=RecordingMock(deps.mock, fail_on))


def test_replay_passes_terms_and_pg_scope(mock_app):
    deps = recording_deps(mock_app)
    approved_loan(deps)
    replay(deps)
    expected = round(monthly_installment(3_300_000, 12))
    assert deps.mock.consent_scopes[-1] == ["account", "pg"]
    params = deps.mock.params_by_source()
    terms = {"amount_krw": 3_300_000, "installment_krw": expected}
    assert params["flow"] == params["pg_flow"] == terms
    assert params["transactions"] is None  # 심사 수집은 그대로 params 없이 부른다
    doc = loan(deps)
    mon = doc["monitoring"]
    assert mon["pg"] == "ok" and mon["ledger_baseline"]["weekly_orders"] > 0
    assert mon["ledger_baseline"]["deduction_rate"] >= 0 and mon["ledger_baseline"]["platform_weekly_net"]
    assert deps.raw.get_json(f"consent-{mon['consent_id']}", "pg_flow.json")["settlements"]
    # 원장 상환액과 Mock에 넘긴 상환액은 같은 값이다(같아야 상환일 출금이 원장과 맞는다).
    assert doc["repayment"]["installment_krw"] == expected
    assert all(w["row"].get("order_gross", 0) >= 0 for w in mon["weeks"])


def test_replay_continues_without_pg_flow(mock_app):
    deps = recording_deps(mock_app, fail_on={"pg_flow"})
    approved_loan(deps)
    started = replay(deps)
    mon = loan(deps)["monitoring"]
    assert mon["pg"] == "missing" and mon["ledger_baseline"] is None
    assert mon["status"] == "completed" and mon["processed_week"] == WEEKS
    assert [w["week"] for w in mon["weeks"]] == list(range(1, WEEKS + 1))
    assert all("order_gross" not in w["row"] for w in mon["weeks"])
    assert all(not w["candidates"] or all(c["cause"] for c in w["candidates"]) for w in mon["weeks"])
    assert deps.raw.get_json(f"consent-{mon['consent_id']}", "pg_flow.json") is None
    assert started["replay_id"] == mon["replay_id"]


def test_week_entry_records_candidates_and_alert_cause(mock_app):
    # 정산 보류 셀러. runner가 없어 조사 에이전트는 폴백 경보를 올리고 주원인은 첫 후보에서 온다.
    deps = recording_deps(mock_app)
    approved_loan(deps, "seller-gearstore-06")
    replay(deps)
    mon = loan(deps)["monitoring"]
    first = next(w for w in mon["weeks"] if w["alert_id"])
    assert first["candidates"][0]["cause"] == "settlement_hold"
    assert first["candidates"][0]["evidence_ids"]
    alert = deps.store.read(ALERTS, first["alert_id"], LOAN)
    assert alert["primary_cause"] == "settlement_hold"
    assert "settlement_hold" in {c["cause"] for c in first["candidates"]}
    assert first["row"]["held"] > 0


def test_repayment_case_carries_alert_cause_to_collections(mock_app):
    """경보 원인이 추심 건 recent_alert와 추심 에이전트 입력(get_case)까지 간다(스펙 §7.4)."""
    deps = make_deps(mock_app)
    approved_loan(deps)
    replay(deps)
    mon = loan(deps)["monitoring"]
    last = deps.store.read(ALERTS, mon["alert_ids"][-1], LOAN)
    (case,) = cases(deps)
    assert last["primary_cause"]
    assert case["recent_alert"]["primary_cause"] == last["primary_cause"]
    assert case["recent_alert"]["secondary_causes"] == list(last["secondary_causes"])
    ctx = CollectionsContext(case=RepaymentCase.from_doc(case), contacts=[], now=NOW, mask=lambda s: s)
    got = ctx.get_case({})["recent_alert"]
    assert got["primary_cause"] == last["primary_cause"]
    assert got["secondary_causes"] == list(last["secondary_causes"])


def test_due_shortfall_trigger_fires_when_due_balance_below_installment(mock_app, monkeypatch):
    monkeypatch.setattr(
        monitoring_module, "due_check", lambda *a: {"next_due_on": FIRST_DUE.isoformat(), "due_balance": 1}
    )
    deps = make_deps(mock_app)
    approved_loan(deps)
    replay(deps)
    entry = loan(deps)["monitoring"]["weeks"][0]
    assert "due_shortfall" in entry["triggers"] and entry["row"]["due_balance"] == 1
    assert "due_shortfall" in entry["new_triggers"] and entry["investigation"]


def test_due_shortfall_ignores_due_date_without_scheduled_items(mock_app, monkeypatch):
    monkeypatch.setattr(monitoring_module, "due_check", lambda *a: {"next_due_on": "2026-12-25", "due_balance": 1})
    deps = make_deps(mock_app)
    approved_loan(deps)
    replay(deps)
    entry = loan(deps)["monitoring"]["weeks"][0]
    assert entry["row"]["due_balance"] == 1 and "due_shortfall" not in entry["triggers"]


def test_week_row_records_next_due_and_pre_debit_balance(mock_app):
    deps = make_deps(mock_app)
    approved_loan(deps)
    replay(deps)
    mon = loan(deps)["monitoring"]
    installment = loan(deps)["repayment"]["installment_krw"]
    week4 = mon["weeks"][3]  # 11/23~11/29: 12/2 상환일이 전망 안에 든다
    assert week4["row"]["next_due_on"] == FIRST_DUE.isoformat()
    path = {p["date"]: p["balance"] for p in week4["projection"]["path"]}
    assert week4["row"]["due_balance"] == path[FIRST_DUE.isoformat()] + installment
    assert ("due_shortfall" in week4["triggers"]) == (week4["row"]["due_balance"] < installment)


def test_week_without_repayment_ledger_skips_due_check(mock_app):
    outbox = Outbox()
    deps = make_deps(mock_app, events=outbox)
    approved_loan(deps)
    started = replay(deps)
    doc = loan(deps)
    doc.pop("repayment")
    deps.store.upsert(LOANS, doc)
    entry = process_week(deps, LOAN, started["replay_id"], 1)
    assert "next_due_on" not in entry["row"] and "due_shortfall" not in entry["triggers"]


# --- 조기경보 단계 자동 안내와 셀프 상환 플랜(Task 7) ---


def high_replay(mock_app, severities=(), applicant_id="seller-dailylook-01", **fields):
    deps = make_deps(mock_app, agents=HighAgents(severities))
    if fields:
        deps = replace(deps, **fields)
    approved_loan(deps, applicant_id)
    return deps, replay(deps)["replay_id"]


def by_kind(deps, replay_id, by):
    return [c for c in contacts(deps, replay_id) if c["by"] == by]


def week_end(week):
    return AS_OF + timedelta(days=7 * week - 1)


def plan_message(deps, kind, **fields):
    return handle_restructure_decision(deps, json.dumps({"kind": kind, "loan_id": LOAN} | fields))


def choose(deps, option, offer_id=None):
    offer_id = offer_id or loan(deps)["repayment"]["offer"]["offer_id"]
    return plan_message(deps, "plan_choice", offer_id=offer_id, option=option)


def decide_plan(deps, decision="approve", reason="", change_id=None):
    change_id = change_id or loan(deps)["repayment"]["plan_change"]["change_id"]
    return plan_message(deps, "plan_change", change_id=change_id, decision=decision, officer_id="officer-1",
                        reason=reason)


def expire_offer(deps):
    offer_id = loan(deps)["repayment"]["offer"]["offer_id"]
    return plan_message(deps, "offer_expire", offer_id=offer_id, officer_id="officer-1")


def test_first_high_alert_sends_balance_warning_and_early_reminders(mock_app):
    deps, replay_id = high_replay(mock_app, ["high", "medium"] * 5)  # 높음이 연속하지 않아 2단계는 없다
    doc = loan(deps)
    mon, schedule, installment = doc["monitoring"], doc["repayment"]["schedule"], doc["repayment"]["installment_krw"]
    assert mon["status"] == "completed" and mon["escalation"]["stage2"] is None
    first = next(w for w in mon["weeks"] if w["alert_id"])
    (s1,) = by_kind(deps, replay_id, "early_warning")
    on = week_end(first["week"])
    assert (s1["ref"], s1["script_id"], s1["status"], s1["on"]) == (f"{replay_id}-s1", "balance_warning", "sent", on.isoformat())
    assert mon["escalation"]["stage1"] == {"alert_id": first["alert_id"], "on": on.isoformat(), "status": "sent", "reason": None}
    upcoming = [s for s in schedule if date.fromisoformat(s["due_on"]) > on]
    # 5주차 행에는 다음 상환일 예상 잔고가 없다(전망 밖) → 금액 없는 문구.
    assert "due_balance" not in first["row"]
    assert s1["text"] == stage_message(1, due_on=date.fromisoformat(upcoming[0]["due_on"]), amount=installment,
                                       balance=None, link="")[1]
    early = by_kind(deps, replay_id, "early_reminder")
    assert [c["ref"] for c in early] == [f"{replay_id}-i{s['no']}-e" for s in upcoming] and early
    for c, s in zip(early, upcoming):
        due = date.fromisoformat(s["due_on"])
        assert (c["script_id"], c["status"], c["on"]) == ("early_reminder", "sent", (due - timedelta(days=5)).isoformat())
        assert c["at"] == datetime.combine(due - timedelta(days=5), datetime.min.time(), KST).replace(hour=10).isoformat()
        assert s["due_on"] in c["text"] and f"{installment:,}원" in c["text"]
        assert (s["early_reminded_on"], s["early_reminder_status"]) == (c["on"], "sent")
    assert all(s.get("early_reminded_on") is None for s in schedule if s not in upcoming)  # 1단계 전 회차
    # 기본 3일 전 안내는 그대로다.
    assert [c["ref"] for c in by_kind(deps, replay_id, "reminder")] == [f"{replay_id}-i{s['no']}" for s in schedule]


@pytest.mark.parametrize("balance, shown", [(100_000, True), (-5, False), (5_000_000, False)])
def test_stage_one_shows_balance_only_when_short_and_not_negative(mock_app, monkeypatch, balance, shown):
    monkeypatch.setattr(monitoring_module, "due_check", lambda *a: {"next_due_on": FIRST_DUE.isoformat(), "due_balance": balance})
    deps, replay_id = high_replay(mock_app)
    s1 = next(c for c in by_kind(deps, replay_id, "early_warning") if c["ref"].endswith("-s1"))
    assert ("100,000원" in s1["text"]) is shown
    assert "-5원" not in s1["text"] and "5,000,000원" not in s1["text"]


def test_consecutive_high_alerts_open_offer_and_wait_for_customer(mock_app, monkeypatch):
    uptos = []
    real = monitoring_module.payout_days
    monkeypatch.setattr(monitoring_module, "payout_days", lambda pg, platform, upto: uptos.append(upto) or real(pg, platform, upto))
    deps, replay_id = high_replay(mock_app)
    doc = loan(deps)
    mon, offer = doc["monitoring"], doc["repayment"]["offer"]
    s1, s2 = by_kind(deps, replay_id, "early_warning")
    assert s1["ref"] == f"{replay_id}-s1" and s2["ref"] == f"{replay_id}-s2"
    assert (s2["script_id"], s2["status"]) == ("self_plan_offer", "sent") and plan_link(doc["applicant_id"]) in s2["text"]
    week = mon["processed_week"]
    assert s2["on"] == week_end(week).isoformat() and mon["escalation"]["stage2"]["status"] == "sent"
    assert uptos == [week_end(week)]  # 지급일은 이번 주 끝까지 공개된 것만 쓴다
    assert (offer["offer_id"], offer["week"], offer["sent_on"], offer["status"]) == (f"of-{replay_id}", week, s2["on"], "open")
    assert offer["choice"] is None and offer["responded_at"] is None
    assert [o["option"] for o in offer["options"]] == ["move", "split", "keep"]
    assert (mon["status"], mon["holds"], mon["paused_week"]) == ("awaiting_customer", ["customer_response"], week)
    assert replay(deps, action="resume", replay_id=replay_id) is None  # 고객 응답 전에는 재개할 수 없다


def test_stage_messages_are_sent_once_per_replay(mock_app):
    deps, replay_id = high_replay(mock_app)
    assert choose(deps, "keep") is True
    assert replay(deps, action="resume", replay_id=replay_id) is not None
    mon = loan(deps)["monitoring"]
    assert mon["status"] == "completed" and len(mon["alert_severities"]) == len(mon["alert_ids"]) > 2
    assert set(mon["alert_severities"]) == {"high"}
    assert [c["ref"] for c in by_kind(deps, replay_id, "early_warning")] == [f"{replay_id}-s1", f"{replay_id}-s2"]


def test_auto_sends_ignore_real_night_clock(mock_app):
    deps, replay_id = high_replay(mock_app, clock=lambda: NOW.replace(hour=23))
    s1, s2 = by_kind(deps, replay_id, "early_warning")
    assert (s1["status"], s2["status"]) == ("sent", "sent")
    assert s1["at"] == datetime.combine(date.fromisoformat(s1["on"]), datetime.min.time(), KST).replace(hour=10).isoformat()
    assert loan(deps)["monitoring"]["status"] == "awaiting_customer"


def test_deferred_stage_two_opens_no_offer(mock_app):
    outbox = Outbox()
    deps = make_deps(mock_app, events=outbox, agents=HighAgents())
    approved_loan(deps)
    replay_id = replay(deps)["replay_id"]
    s2_on = week_end(6).isoformat()  # 두 번째 높음 경보 주차(6주차) 끝
    full = [{"ref": f"x-{i}", "on": s2_on, "at": NOW.isoformat(), "script_id": "missed_payment", "status": "sent",
             "reason": None, "by": "officer"} for i in range(7)]
    deps.store.upsert(ALERTS, {"id": f"contacts-{replay_id}", "loan_id": LOAN, "replay_id": replay_id, "contacts": full})
    pump(deps, outbox)
    doc = loan(deps)
    mon = doc["monitoring"]
    s2 = next(c for c in by_kind(deps, replay_id, "early_warning") if c["ref"] == f"{replay_id}-s2")
    assert (s2["on"], s2["status"], s2["reason"]) == (s2_on, "deferred", "weekly_limit")
    assert mon["escalation"]["stage2"]["status"] == "deferred" and mon["escalation"]["stage2"]["reason"] == "weekly_limit"
    assert doc["repayment"].get("offer") is None
    assert mon["status"] == "completed" and mon["processed_week"] == WEEKS
    assert len(by_kind(deps, replay_id, "early_warning")) == 2  # deferred도 쓴 것으로 보고 다시 보내지 않는다


def test_approval_blocks_ambiguous_legacy_flow_without_changing_schedule(mock_app):
    deps, _ = high_replay(mock_app)
    assert choose(deps, "move")
    before = loan(deps)
    container = f"consent-{before['monitoring']['consent_id']}"
    flow = deps.raw.get_json(container, "flow.json")
    for t in flow:
        t.pop("scheduled_item_key", None)
    deps.raw.put_json(container, "flow.json", flow)
    assert not decide_plan(deps)
    led = loan(deps)["repayment"]
    assert led["plan_change"]["validation_error"] == "ambiguous_scheduled_debit"
    assert led["plan_change"]["status"] == "pending_officer" and not led.get("plan_history")
    assert led["schedule"] == before["repayment"]["schedule"]


def test_stored_legacy_plan_fails_replay_before_publishing_a_doubled_cashflow(mock_app, monkeypatch):
    outbox = Outbox()
    deps = make_deps(mock_app, events=outbox)
    approved_loan(deps)
    rid = replay(deps)["replay_id"]
    before = loan(deps)
    led = before["repayment"]
    row = led["schedule"][0]
    original = {"key": "i1", "due_on": row["due_on"], "amount_krw": led["installment_krw"]}
    row["due_on"] = "2026-12-09"
    led["plan_history"] = [{"change_id": "legacy", "applied_from": str(AS_OF), "before": [original],
                            "after": [{**original, "due_on": row["due_on"]}]}]
    deps.store.upsert(LOANS, before)
    container = f"consent-{before['monitoring']['consent_id']}"
    flow = deps.raw.get_json(container, "flow.json")
    for t in flow:
        t.pop("scheduled_item_key", None)
    deps.raw.put_json(container, "flow.json", flow)
    pump(deps, outbox)
    mon = loan(deps)["monitoring"]
    assert mon["status"] == "failed" and mon["error"] == "ambiguous_scheduled_debit"
    assert mon["weeks"] == [] and loan(deps)["repayment"] == led and not contacts(deps, rid)
    assert len(outbox.bodies(TOPIC_FLOW)) == 1

    old_id = LOAN
    stopped = loan(deps)
    monkeypatch.setitem(globals(), "LOAN", "recovery-synthetic-loan")
    approved_loan(deps)
    new_rid = replay(deps)["replay_id"]
    assert new_rid != rid
    pump(deps, outbox)
    resume_until_done(deps, new_rid, lambda: pump(deps, outbox))
    recovered = loan(deps)
    mon = recovered["monitoring"]
    assert mon["status"] == "completed" and mon["processed_week"] == WEEKS
    regenerated = deps.raw.get_json(f"consent-{mon['consent_id']}", "flow.json")
    debits = {t["txn_id"]: -t["amount_krw"] for t in regenerated if t["description"] == LENDER_DEBIT}
    assert debits and all(t.get("scheduled_item_key") for t in regenerated if t["txn_id"] in debits)
    posted = {}
    for row in recovered["repayment"]["schedule"]:
        for allocation in row.get("allocations", ()):
            txn = allocation["txn_id"]
            posted[txn] = posted.get(txn, 0) + allocation["amount_krw"]
    assert posted == debits
    notices = contacts(deps, new_rid)
    assert len({(n["by"], n["ref"]) for n in notices}) == len(notices)
    assert deps.store.read(LOANS, old_id, old_id) == stopped
    assert deps.raw.get_json(container, "flow.json") == flow


def test_plan_choice_then_approval_changes_schedule_and_replay(mock_app):
    deps, replay_id = high_replay(mock_app)
    before = loan(deps)
    week = before["monitoring"]["processed_week"]
    original_debit = next(t for t in deps.raw.get_json(f"consent-{before['monitoring']['consent_id']}", "flow.json")
                          if t["description"] == LENDER_DEBIT and t["booked_on"] == before["repayment"]["schedule"][1]["due_on"])
    move = next(o for o in before["repayment"]["offer"]["options"] if o["option"] == "move")
    assert move["replaces"] == ["i2"]
    assert choose(deps, "move") is True
    doc = loan(deps)
    change = doc["repayment"]["plan_change"]
    assert change == {"change_id": f"pc-{replay_id}", "option": "move", "items": move["items"], "replaces": ["i2"],
                      "status": "pending_officer", "requested_at": NOW.isoformat(), "officer_id": None,
                      "decided_at": None, "reason": None}
    assert (doc["repayment"]["offer"]["status"], doc["repayment"]["offer"]["choice"]) == ("chosen", "move")
    assert doc["repayment"]["offer"]["responded_at"] == NOW.isoformat()
    assert doc["repayment"]["schedule"] == before["repayment"]["schedule"]  # 승인 전에는 일정 그대로
    assert (doc["monitoring"]["status"], doc["monitoring"]["holds"]) == ("paused", ["plan_change"])

    assert decide_plan(deps) is True
    doc = loan(deps)
    led, mon = doc["repayment"], doc["monitoring"]
    old = before["repayment"]["schedule"][1]
    (hist,) = led["plan_history"]
    assert hist == {
        "change_id": f"pc-{replay_id}", "option": "move", "applied_from": (AS_OF + timedelta(days=7 * week)).isoformat(),
        "before": [{"key": "i2", "due_on": old["due_on"], "amount_krw": led["installment_krw"]}],
        "after": [{"key": item_key(i), "due_on": i["due_on"], "amount_krw": i["amount_krw"]} for i in move["items"]],
        "officer_id": "officer-1", "decided_at": NOW.isoformat(),
    }
    new_due = move["items"][0]["due_on"]
    assert [s["due_on"] for s in led["schedule"] if s["no"] == 2] == [new_due]
    assert (led["plan_change"]["status"], led["plan_change"]["officer_id"]) == ("approved", "officer-1")
    assert (mon["status"], mon["holds"]) == ("awaiting_resume", [])

    assert replay(deps, action="resume", replay_id=replay_id)["from_week"] == week + 1
    resume_until_done(deps, replay_id)
    doc = loan(deps)
    mon, schedule = doc["monitoring"], doc["repayment"]["schedule"]
    assert mon["status"] == "completed"
    moved = next(s for s in schedule if s["no"] == 2)
    # 옮긴 날짜에 새 출금으로 납부했다. 원래 날짜의 출금은 흐름에서 빠져 판정에 쓰이지 않는다.
    assert (moved["due_on"], moved["status"], moved["txn_id"]) == (new_due, "paid", f"pc-{replay_id}-i2")
    assert all(s["due_on"] != old["due_on"] for s in schedule)
    assert original_debit["txn_id"] not in {s["txn_id"] for s in schedule}
    # 공개된 주차의 행은 승인 전과 같다.
    assert [w["row"] for w in mon["weeks"][:week]] == [w["row"] for w in before["monitoring"]["weeks"]]


def test_keep_choice_needs_no_officer(mock_app):
    deps, replay_id = high_replay(mock_app)
    schedule = loan(deps)["repayment"]["schedule"]
    assert choose(deps, "keep") is True
    led, mon = loan(deps)["repayment"], loan(deps)["monitoring"]
    assert (led["offer"]["status"], led["offer"]["choice"]) == ("kept", "keep")
    assert led.get("plan_change") is None and led["schedule"] == schedule
    assert (mon["status"], mon["holds"]) == ("awaiting_resume", [])


def test_plan_decline_keeps_schedule(mock_app):
    deps, replay_id = high_replay(mock_app)
    schedule = loan(deps)["repayment"]["schedule"]
    assert choose(deps, "split") is True
    assert decide_plan(deps, "decline") is False  # 반려 사유 필수
    assert decide_plan(deps, "decline", reason="   ") is False
    assert loan(deps)["repayment"]["plan_change"]["status"] == "pending_officer"
    assert loan(deps)["monitoring"]["status"] == "paused"
    assert decide_plan(deps, "decline", reason="분할 대신 통화 상담") is True
    led, mon = loan(deps)["repayment"], loan(deps)["monitoring"]
    assert (led["plan_change"]["status"], led["plan_change"]["reason"]) == ("declined", "분할 대신 통화 상담")
    assert led["schedule"] == schedule and not led.get("plan_history")
    assert (mon["status"], mon["holds"]) == ("awaiting_resume", [])


def test_offer_expire_then_late_choice_is_dropped(mock_app):
    deps, replay_id = high_replay(mock_app)
    assert expire_offer(deps) is True
    led, mon = loan(deps)["repayment"], loan(deps)["monitoring"]
    assert (led["offer"]["status"], led["offer"]["expired_by"], led["offer"]["choice"]) == ("expired", "officer-1", None)
    assert (mon["status"], mon["holds"]) == ("awaiting_resume", [])
    assert choose(deps, "move") is False  # 심사역이 "고객 응답 없이 계속"을 누른 뒤 온 선택
    assert expire_offer(deps) is False
    assert loan(deps)["repayment"] == led
    assert loan(deps)["monitoring"]["status"] == "awaiting_resume"


def test_duplicate_plan_decision_applies_once(mock_app):
    deps, replay_id = high_replay(mock_app)
    assert choose(deps, "move") is True
    assert choose(deps, "split") is False  # 이미 고른 제안
    assert decide_plan(deps) is True
    led = loan(deps)["repayment"]
    assert decide_plan(deps) is False  # 큐 재전달
    assert decide_plan(deps, "decline", reason="중복") is False
    assert loan(deps)["repayment"] == led and len(led["plan_history"]) == 1
    assert loan(deps)["monitoring"]["status"] == "awaiting_resume"


@pytest.mark.parametrize("step", ["keep", "approve", "expire"])
def test_redelivered_plan_message_repairs_hold_after_failure(mock_app, monkeypatch, step):
    deps, _ = high_replay(mock_app)
    if step == "approve":
        assert choose(deps, "move") is True
    send = {"keep": lambda: choose(deps, "keep"), "approve": lambda: decide_plan(deps),
            "expire": lambda: expire_offer(deps)}[step]
    fail_once(monkeypatch, "_refresh_hold")
    with pytest.raises(RuntimeError):
        send()
    held = loan(deps)
    assert held["monitoring"]["status"] in ("paused", "awaiting_customer")
    assert send() is False  # 다시 결정하지 않고 대기 항목만 고친다
    assert loan(deps)["repayment"] == held["repayment"]
    mon = loan(deps)["monitoring"]
    assert (mon["status"], mon["holds"]) == ("awaiting_resume", [])


def test_stage_two_and_miss_same_week_need_both_cleared(mock_app):
    # 정산 보류 셀러: 3·4·5주차 경보, 5주차 1회차 미납. 4·5주차가 연속 높음이라 5주차에 2단계와 미납이 함께 난다.
    deps, replay_id = high_replay(mock_app, ["medium", "high", "high"], applicant_id="seller-gearstore-06")
    mon = loan(deps)["monitoring"]
    assert mon["escalation"]["stage2"]["on"] == week_end(5).isoformat()
    assert (mon["status"], mon["paused_week"]) == ("paused", 5)
    assert mon["holds"] == ["collections_proposal", "customer_response"]
    case_id = mon["pause_case_id"]
    text = deps.store.read(ALERTS, case_id, LOAN)["proposal"]["text"]
    assert decide_collections(deps, case_id, "modify", text=text, include_hardship=False) is True
    mon = loan(deps)["monitoring"]
    assert (mon["status"], mon["holds"]) == ("awaiting_customer", ["customer_response"])
    assert replay(deps, action="resume", replay_id=replay_id) is None
    assert choose(deps, "keep") is True
    mon = loan(deps)["monitoring"]
    assert (mon["status"], mon["holds"]) == ("awaiting_resume", [])


@pytest.mark.parametrize("new_due, early_on, remind_on", [
    ("2027-01-12", "2027-01-07", "2027-01-09"),  # 새 안내일이 재개 뒤 주차에 온다
    ("2027-01-06", None, "2027-01-04"),  # 새 안내일이 공개된 주차로 지나갔다 → 재개 주 첫날에 늦게라도
])
def test_moved_installment_gets_reminders_for_new_due_date(mock_app, monkeypatch, new_due, early_on, remind_on):
    real = monitoring_module.plan_options

    def options(schedule, installment, **kw):
        out = real(schedule, installment, **kw)
        return [dict(o, items=[dict(o["items"][0], due_on=new_due)]) if o["option"] == "move" else o for o in out]

    monkeypatch.setattr(monitoring_module, "plan_options", options)
    # 7주차 1단계, 9주차(~1/3) 2단계. 2회차 상환일(1/4)이 2단계 주 끝 사흘 안이라 옛 날짜 안내가 이미 나갔다.
    deps, replay_id = high_replay(mock_app, ["medium", "medium", "high", "high"])
    assert loan(deps)["monitoring"]["escalation"]["stage2"]["on"] == "2027-01-03"
    old = {c["ref"]: c for c in contacts(deps, replay_id)}
    assert old[f"{replay_id}-i2"]["on"] == "2027-01-01" and old[f"{replay_id}-i2-e"]["on"] == "2026-12-30"
    assert choose(deps, "move") is True and decide_plan(deps) is True
    assert replay(deps, action="resume", replay_id=replay_id)["from_week"] == 10
    resume_until_done(deps, replay_id)
    item = next(s for s in loan(deps)["repayment"]["schedule"] if s["no"] == 2)
    assert (item["due_on"], item["status"], item["txn_id"]) == (new_due, "paid", f"pc-{replay_id}-i2")
    sent = {c["ref"]: c for c in contacts(deps, replay_id)}
    assert {r: sent[r] for r in old} == old  # 옛 날짜 안내는 그대로 두고 새 날짜 안내를 따로 보낸다
    moved = sent[f"{replay_id}-i2-m"]
    assert (moved["on"], moved["status"], moved["by"]) == (remind_on, "sent", "reminder")
    assert (item["reminded_on"], item["reminder_status"]) == (remind_on, "sent")
    if early_on:
        early = sent[f"{replay_id}-i2-m-e"]
        assert (early["on"], early["status"]) == (early_on, "sent") and new_due in early["text"]
        assert (item["early_reminded_on"], item["early_reminder_status"]) == (early_on, "sent")
    else:  # 3일 전 안내일까지 지나 5일 전 안내는 늦게 보내지 않는다
        assert f"{replay_id}-i2-m-e" not in sent and item["early_reminded_on"] is None


def test_stage_two_without_plan_options_is_skipped(mock_app):
    # 9주차 1단계, 10주차 2단계: 다음 회차(3회차 1/29)는 옮기거나 나눌 지급일이 흐름 안에 없다.
    deps, replay_id = high_replay(mock_app, ["medium"] * 3 + ["high", "high"])
    doc = loan(deps)
    mon = doc["monitoring"]
    assert mon["status"] == "completed" and doc["repayment"].get("offer") is None
    assert mon["escalation"]["stage2"] == {"alert_id": mon["weeks"][9]["alert_id"], "on": week_end(10).isoformat(),
                                           "status": "skipped", "reason": "no_options"}
    assert mon["escalation"]["stage1"]["on"] == week_end(9).isoformat()
    assert [c["ref"] for c in by_kind(deps, replay_id, "early_warning")] == [f"{replay_id}-s1"]
