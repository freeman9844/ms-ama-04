import json
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from agents.collections import COLLECTIONS_AGENT, CollectionsContext, RepaymentCase
from agents.data_assembly import SourceError
from agents.early_warning import EW_AGENT
from agents.features import monthly_installment
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


@pytest.fixture(scope="module")
def mock_app():
    cases = build_demo_cases(AS_OF)
    return create_mock_app(cases, SECRET, lambda: NOW, flows=FlowStore(cases, AS_OF))


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
            "underwriting": {k: uw[k] for k in ("features", "monthly", "settlement_profile")},
        },
    )
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
    while (mon := loan(deps)["monitoring"])["status"] == "paused":
        pauses += 1
        settle_holds(deps)
        assert replay(deps, action="resume", replay_id=replay_id)["from_week"] == mon["paused_week"] + 1
        deliver()
    return pauses


def settle_holds(deps):
    """멈춘 재생의 대기 항목을 순서대로 푼다(추심 승인 → 재조정 결정). 끝나면 재개 대기다."""
    while (mon := loan(deps)["monitoring"])["status"] == "paused":
        hold, case_id = mon["holds"][0], mon.get("pause_case_id") or mon.get("pause_alert_id")
        if hold == "collections_proposal":
            assert decide_collections(deps, case_id) is True
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
    with pytest.raises(RuntimeError, match="mock flow down"):
        replay(broken)
    mon = loan(deps)["monitoring"]
    assert mon["status"] == "failed" and "mock flow down" in mon["error"]
    assert replay(deps)["replay_id"] != mon["replay_id"]  # failed는 가드에 걸리지 않는다


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
    assert decide_collections(deps, case_id) is True
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
    replay(deps)
    mon = loan(deps)["monitoring"]
    last = cases(deps)[-1]
    assert mon["status"] == "completed" and mon["paused_week"] is None
    assert last["week"] == WEEKS and last["proposal"]["status"] == "pending_officer"
    assert decide_collections(deps, last["case_id"]) is True
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
    assert decide_collections(deps, case_id) is True
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
    assert decide_collections(deps, mon["pause_case_id"]) is True
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
    assert decide_collections(deps, case_id) is True  # 재조정 상담이 들어 있어 재조정 요청이 대기 중이다
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
    assert decide_collections(deps, case_id) is True
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
    real = deps.store.replace
    monkeypatch.setattr(deps.store, "replace",
                        lambda c, d, etag: False if d["id"].startswith("contacts-") else real(c, d, etag))
    with pytest.raises(RuntimeError):
        decide_collections(deps, case["case_id"])
    monkeypatch.setattr(deps.store, "replace", real)
    assert officer_contacts(deps, replay_id) == []
    assert decide_collections(deps, case["case_id"]) is False
    assert len(officer_contacts(deps, replay_id)) == 1
    assert loan(deps)["monitoring"]["status"] == "awaiting_resume"


def test_redelivered_restructure_decision_repairs_hold(mock_app, monkeypatch):
    deps, _ = paused_inline(mock_app)
    case_id = loan(deps)["monitoring"]["pause_case_id"]
    assert decide_collections(deps, case_id) is True
    fail_once(monkeypatch, "_refresh_hold")
    with pytest.raises(RuntimeError):
        decide_restructure(deps, case_id)
    assert loan(deps)["monitoring"]["status"] == "paused"
    assert decide_restructure(deps, case_id) is False
    mon = loan(deps)["monitoring"]
    assert (mon["status"], mon["holds"]) == ("awaiting_resume", [])


def missed_without_agents(mock_app, **fields):
    deps = missing_first_due(make_deps(mock_app))
    if fields:
        deps = replace(deps, **fields)
    approved_loan(deps)
    replay_id = replay(deps)["replay_id"]
    return deps, replay_id, cases(deps)[0]


def test_missed_payment_creates_proposal_and_pauses_without_contact(mock_app):
    deps = missing_first_due(make_deps(mock_app)); approved_loan(deps); replay(deps)
    case, mon = cases(deps)[0], loan(deps)["monitoring"]
    assert case["status"] == "proposal_pending" and case["proposal"]["text"]
    assert mon["status"] == "paused" and mon["holds"] == ["collections_proposal"]
    assert not [c for c in contacts(deps, mon["replay_id"]) if c["by"] in ("agent", "fallback", "officer")]


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


def test_approve_with_hardship_waits_for_restructure(mock_app):
    deps, replay_id = paused_inline(mock_app)
    case_id = loan(deps)["monitoring"]["pause_case_id"]
    assert decide_collections(deps, case_id) is True
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
    assert decide_collections(deps, case_id) is True
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
def test_plan_messages_are_not_handled_yet(mock_app, body):
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
