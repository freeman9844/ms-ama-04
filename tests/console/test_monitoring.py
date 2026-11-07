import base64
import json
import re
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from console.app import REPLAY_QUEUE, RESTRUCTURE_QUEUE, ConsoleDeps, create_app
from console.auth import AUDITOR, CREDIT_OFFICER
from journey.models import KST

OID = "http://schemas.microsoft.com/identity/claims/objectidentifier"
NOW = datetime(2026, 11, 2, 9, 0, tzinfo=KST)


def principal(*roles, oid):
    claims = [{"typ": OID, "val": oid}, {"typ": "name", "val": oid}] + [{"typ": "roles", "val": r} for r in roles]
    body = {"auth_typ": "aad", "name_typ": "name", "role_typ": "roles", "claims": claims}
    return {"X-MS-CLIENT-PRINCIPAL": base64.b64encode(json.dumps(body).encode()).decode()}


OFFICER = principal(CREDIT_OFFICER, oid="oid-officer")
AUDITOR_H = principal(AUDITOR, oid="oid-auditor")
BASELINE = {
    "avg_weekly_revenue": 9_000_000, "revenue_volatility": 0.12, "cash_buffer_days": 30.0,
    "operating_daily_krw": 900_000, "tax_bank_match_ratio": 0.97,
    "seasonal_index": {f"{m:02d}": 1.0 for m in range(1, 13)},
}
ROW = {
    "week": 1, "start": "2026-09-01", "end": "2026-09-07", "revenue": 5_000_000, "revenue_ratio": 0.556,
    "operating_out": 2_500_000, "debt_service": 1_000_000, "high_cost_lender": 1_000_000, "high_cost_count": 1,
    "loan_proceeds": 0, "unclassified_in": 0, "end_balance": 14_000_000, "buffer_days": 15.6,
}


def make_loan(status="completed", started_at=NOW - timedelta(hours=1), replay_id="rp-1"):
    monitoring = None if status is None else {
        "replay_id": replay_id, "consent_id": "m-1", "flow_start": "2026-09-01", "started_at": started_at.isoformat(),
        "mode": "inline", "status": status, "processed_week": 1, "seen_triggers": ["new_debt"], "overrides": {},
        "weeks": [{"week": 1, "row": ROW, "triggers": ["new_debt"], "new_triggers": ["new_debt"],
                   "classification": None, "investigation": {"decision": "alert", "reason": "r", "record": {}},
                   "alert_id": "ew-rp-1-w01"}],
        "alert_ids": ["ew-rp-1-w01"], "error": None,
    }
    return {
        "id": "app-1", "loan_id": "app-1", "application_id": "app-1", "applicant_id": "seller-dailylook-01",
        "industry": "카페", "underwriting_consent_id": "c-1", "decision": "approve", "amount_krw": 20_000_000,
        "tenor_months": 12, "approved_at": "2026-10-31T09:00:00+09:00", "baseline": BASELINE,
        "monitoring": monitoring, "updated_at": NOW.isoformat(),
    }


def make_alert(mask, restructure_status="pending_officer"):
    return {
        "id": "ew-rp-1-w01", "schema_version": "1", "alert_id": "ew-rp-1-w01", "loan_id": "app-1", "week": 1,
        "as_of": "2026-09-07", "severity": "high",
        "summary": mask("김대표 개인이체와 고금리 상환이 겹쳤다"),
        "signals": [{"kind": "new_debt", "text": mask("김대표 개인이체 뒤 고금리 상환 1건"),
                     "metric_ids": ["W:1:high_cost_count", "W:9:revenue"], "txn_ids": ["t2"]}],
        "recommended_action": "hardship_review", "triggers": ["new_debt"], "models": {"investigator": "gpt-5.4"},
        "replay_id": "rp-1", "status": "restructure_pending", "created_at": NOW.isoformat(), "event_error": None,
        "collections": {"decision": "routed", "reason": "상담 연결", "at": NOW.isoformat(),
                        "contacts": [{"alert_id": "ew-rp-1-w01", "on": "2026-11-02", "at": NOW.isoformat(),
                                      "script_id": "hardship_offer", "status": "sent", "reason": None,
                                      "by": "collections-agent"}], "record": {}},
        "restructure": {"request_id": "rs-ew-rp-1-w01", "category": "debt_overload",
                        "note": mask("김대표 대출 중첩"), "status": restructure_status,
                        "created_at": NOW.isoformat()},
    }


class FakeMonitoring:
    def __init__(self, loans, alerts):
        self.loans = {d["id"]: d for d in loans}
        self.alerts = alerts
        self.alert_queries = []

    def list_loans(self, limit):
        return [{"id": d["id"], "applicant_id": d["applicant_id"], "industry": d["industry"],
                 "amount_krw": d["amount_krw"], "tenor_months": d["tenor_months"],
                 "monitoring_status": (d["monitoring"] or {}).get("status"),
                 "alerts": len((d["monitoring"] or {}).get("alert_ids", [])) if d["monitoring"] else None,
                 "updated_at": d["updated_at"]} for d in self.loans.values()][:limit]

    def get_loan(self, loan_id):
        return self.loans.get(loan_id)

    def list_alerts(self, loan_id, replay_id):
        self.alert_queries.append((loan_id, replay_id))
        return [a for a in self.alerts if a["loan_id"] == loan_id and a["replay_id"] == replay_id]


class FakeQueue:
    def __init__(self):
        self.sent = []

    def send(self, queue, message):
        self.sent.append((queue, message))


@pytest.fixture
def setup(fake_raw, raw_profile, raw_txns, key, mask):
    def build(loan=None, alerts=None, monitoring=True):
        raw = fake_raw({("consent-c-1", "profile.json"): raw_profile, ("consent-m-1", "flow.json"): raw_txns})
        store = FakeMonitoring([loan or make_loan()], [make_alert(mask)] if alerts is None else alerts)
        queue = FakeQueue()
        deps = ConsoleDeps(store=None, raw=raw, queue=queue, pseudonym_key=key, clock=lambda: NOW,
                           monitoring=store if monitoring else None)
        return TestClient(create_app(deps), follow_redirects=False), queue, store, raw

    return build


def csrf(client, headers=OFFICER):
    page = client.get("/loans/app-1", headers=headers).text
    return re.search(r'name="csrf" value="([0-9a-f]{64})"', page).group(1)


def test_loans_pages_require_console_role_and_configured_store(setup):
    client, *_ = setup()
    assert client.get("/loans").status_code == 401
    assert client.get("/loans/app-1").status_code == 401
    page = client.get("/loans", headers=AUDITOR_H)
    assert page.status_code == 200 and "/loans/app-1" in page.text and "재생 완료" in page.text
    assert client.get("/loans/nope", headers=AUDITOR_H).status_code == 404
    off, *_ = setup(monitoring=False)
    assert off.get("/loans", headers=AUDITOR_H).status_code == 404


def test_loan_page_restores_alert_text_and_shows_evidence_for_officer(setup, mask):
    client, _, store, raw = setup()
    page = client.get("/loans/app-1", headers=OFFICER).text
    assert store.alert_queries == [("app-1", "rp-1")]
    assert ("consent-m-1", "flow.json") in raw.reads
    assert "김대표 개인이체와 고금리 상환이 겹쳤다" in page and "[[" not in page
    assert "W:1:high_cost_count = 1" in page
    assert "W:9:revenue = 없음" in page  # 없는 근거 ID는 경고 칩으로 드러낸다
    assert "김대표 개인이체" in page and "t2" in page  # 근거 거래(원문 적요)
    assert "hardship_offer" in page and 'action="/loans/app-1/alerts/ew-rp-1-w01/restructure"' in page
    assert 'action="/loans/app-1/replay"' in page and "disabled" not in page


def test_auditor_sees_pseudonyms_and_no_forms(setup, mask):
    client, *_ = setup()
    page = client.get("/loans/app-1", headers=AUDITOR_H).text
    assert mask("김대표") in page and "김대표" not in page
    assert "/replay" not in page and "/restructure" not in page


def test_loan_without_replay_skips_alert_lookup(setup):
    client, _, store, _ = setup(loan=make_loan(status=None))
    page = client.get("/loans/app-1", headers=OFFICER).text
    assert "재생 전" in page and store.alert_queries == []


def test_replay_sends_queue_message(setup):
    client, queue, *_ = setup()
    r = client.post("/loans/app-1/replay", data={"csrf": csrf(client)}, headers=OFFICER)
    assert r.status_code == 202
    assert queue.sent == [(REPLAY_QUEUE, {"loan_id": "app-1"})]


def test_replay_is_blocked_while_one_is_running(setup):
    client, queue, *_ = setup(loan=make_loan(status="replaying", started_at=NOW - timedelta(minutes=5)))
    token = csrf(client)
    assert "disabled" in client.get("/loans/app-1", headers=OFFICER).text
    r = client.post("/loans/app-1/replay", data={"csrf": token}, headers=OFFICER)
    assert r.status_code == 409 and "이미 재생이 진행 중" in r.text and queue.sent == []
    stuck, queue2, *_ = setup(loan=make_loan(status="replaying", started_at=NOW - timedelta(minutes=31)))
    assert stuck.post("/loans/app-1/replay", data={"csrf": csrf(stuck)}, headers=OFFICER).status_code == 202
    assert queue2.sent == [(REPLAY_QUEUE, {"loan_id": "app-1"})]


def test_restructure_form_waits_for_replay_to_finish(setup):
    # 재생 중에는 3초 자동 새로 고침이 입력 중인 사유를 지우므로 결정 폼을 숨긴다.
    client, *_ = setup(loan=make_loan(status="replaying", started_at=NOW - timedelta(minutes=5)))
    page = client.get("/loans/app-1", headers=OFFICER).text
    assert 'http-equiv="refresh"' in page and "/restructure" not in page
    assert "재생이 끝나면 재조정 결정을 내릴 수 있습니다." in page
    stuck, *_ = setup(loan=make_loan(status="replaying", started_at=NOW - timedelta(minutes=31)))
    page = stuck.get("/loans/app-1", headers=OFFICER).text
    assert 'http-equiv="refresh"' not in page and "/restructure" in page


def test_replay_requires_decider_and_csrf(setup):
    client, queue, *_ = setup()
    assert client.post("/loans/app-1/replay", data={"csrf": "0" * 64}, headers=OFFICER).status_code == 403
    assert client.post("/loans/app-1/replay", data={"csrf": "0" * 64}, headers=AUDITOR_H).status_code == 403
    assert client.post("/loans/nope/replay", data={"csrf": csrf(client)}, headers=OFFICER).status_code == 404
    assert queue.sent == []


def test_restructure_decision_uses_login_identity(setup):
    client, queue, *_ = setup()
    form = {"csrf": csrf(client), "decision": "approve", "reason": "3개월 유예", "officer_id": "someone-else"}
    r = client.post("/loans/app-1/alerts/ew-rp-1-w01/restructure", data=form, headers=OFFICER)
    assert r.status_code == 202
    assert queue.sent == [(RESTRUCTURE_QUEUE, {"loan_id": "app-1", "alert_id": "ew-rp-1-w01", "decision": "approve",
                                               "officer_id": "oid-officer", "reason": "3개월 유예"})]


@pytest.mark.parametrize("fields, status", [
    ({"decision": "approve", "reason": " "}, 422),
    ({"decision": "maybe", "reason": "x"}, 422),
    ({"decision": "approve", "reason": "x" * 501}, 422),
])
def test_restructure_decision_validation(setup, fields, status):
    client, queue, *_ = setup()
    r = client.post("/loans/app-1/alerts/ew-rp-1-w01/restructure", data={"csrf": csrf(client)} | fields, headers=OFFICER)
    assert r.status_code == status and queue.sent == []


def test_restructure_decision_needs_pending_request_of_current_replay(setup, mask):
    client, queue, *_ = setup(alerts=[make_alert(mask, restructure_status="approved")])
    token = csrf(client)
    path = "/loans/app-1/alerts/ew-rp-1-w01/restructure"
    assert client.post(path, data={"csrf": token, "decision": "approve", "reason": "x"}, headers=OFFICER).status_code == 409
    other, queue2, *_ = setup(loan=make_loan(replay_id="rp-2"))
    assert other.post(path, data={"csrf": csrf(other), "decision": "approve", "reason": "x"},
                      headers=OFFICER).status_code == 404
    assert queue.sent == queue2.sent == []


def held_loan(status, paused_week=2):
    loan = make_loan(status=status)
    loan["monitoring"].update(processed_week=paused_week, paused_week=paused_week, pause_alert_id="ew-rp-1-w01",
                              resumed_at=[], stopped_at=None)
    return loan


def test_paused_replay_blocks_new_replay_and_opens_restructure_form(setup):
    client, queue, *_ = setup(loan=held_loan("paused"))
    page = client.get("/loans/app-1", headers=OFFICER).text
    assert "재조정 대기" in page and "재조정 요청으로 재생을 멈췄습니다" in page and "남은 11주" in page
    assert 'http-equiv="refresh"' not in page  # 사유 입력이 지워지지 않게 새로 고치지 않는다
    assert "/restructure" in page and "/replay/resume" not in page
    assert re.search(r'<button type="submit" disabled>사후 13주 재생\(다시\)</button>', page)
    r = client.post("/loans/app-1/replay", data={"csrf": csrf(client)}, headers=OFFICER)
    assert r.status_code == 409 and "재개 확인이 끝나야" in r.text
    form = {"csrf": csrf(client), "decision": "decline", "reason": "상환 여력 없음"}
    sent = client.post("/loans/app-1/alerts/ew-rp-1-w01/restructure", data=form, headers=OFFICER)
    assert sent.status_code == 202 and "재생을 이어갈지 선택하세요" in sent.text
    assert [q for q, _ in queue.sent] == [RESTRUCTURE_QUEUE]


@pytest.mark.parametrize(("action", "text"), [("resume", "남은 11주 재생을 이어서"), ("stop", "종료하도록")])
def test_awaiting_resume_offers_resume_and_stop(setup, mask, action, text):
    client, queue, *_ = setup(loan=held_loan("awaiting_resume"), alerts=[make_alert(mask, "approved")])
    page = client.get("/loans/app-1", headers=OFFICER).text
    assert "재개 확인 대기" in page and "남은 11주 재생을 이어갈까요?" in page
    assert 'action="/loans/app-1/replay/resume"' in page and 'action="/loans/app-1/replay/stop"' in page
    r = client.post(f"/loans/app-1/replay/{action}", data={"csrf": csrf(client)}, headers=OFFICER)
    assert r.status_code == 202 and text in r.text
    assert queue.sent == [(REPLAY_QUEUE, {"loan_id": "app-1", "action": action, "replay_id": "rp-1"})]


@pytest.mark.parametrize("status", ["replaying", "paused", "completed", "stopped"])
def test_resume_and_stop_need_awaiting_resume(setup, status):
    client, queue, *_ = setup(loan=held_loan(status))
    for action in ("resume", "stop"):
        r = client.post(f"/loans/app-1/replay/{action}", data={"csrf": csrf(client)}, headers=OFFICER)
        assert r.status_code == 409 and "재개 확인을 기다리는 재생이 없습니다" in r.text
    assert queue.sent == []


def test_resume_stop_require_decider_csrf_and_known_action(setup):
    client, queue, *_ = setup(loan=held_loan("awaiting_resume"))
    page = client.get("/loans/app-1", headers=AUDITOR_H).text
    assert "남은 11주 재생을 이어갈까요?" in page and "/replay/resume" not in page
    assert client.post("/loans/app-1/replay/resume", data={"csrf": "0" * 64}, headers=OFFICER).status_code == 403
    assert client.post("/loans/app-1/replay/resume", data={"csrf": "0" * 64}, headers=AUDITOR_H).status_code == 403
    assert client.post("/loans/app-1/replay/pause", data={"csrf": csrf(client)}, headers=OFFICER).status_code == 404
    assert queue.sent == []


def test_stopped_replay_can_start_again(setup):
    client, queue, *_ = setup(loan=held_loan("stopped"))
    page = client.get("/loans/app-1", headers=OFFICER).text
    assert "재생 종료" in page
    assert client.post("/loans/app-1/replay", data={"csrf": csrf(client)}, headers=OFFICER).status_code == 202
    assert queue.sent == [(REPLAY_QUEUE, {"loan_id": "app-1"})]


def test_replay_sent_page_forwards_to_loan_until_the_replay_starts(setup):
    client, _, store, _ = setup()
    r = client.post("/loans/app-1/replay", data={"csrf": csrf(client)}, headers=OFFICER)
    m = re.search(r'http-equiv="refresh" content="1;url=(/loans/app-1\?since=[0-9a-f]{12})"', r.text)
    assert m, r.text
    waiting = client.get(m.group(1), headers=OFFICER).text
    assert re.search(r'content="5;url=/loans/app-1\?since=[0-9a-f]{12}&amp;n=1"', waiting)
    store.loans["app-1"] = make_loan(status="replaying", started_at=NOW - timedelta(minutes=1), replay_id="rp-2")
    running = client.get(m.group(1), headers=OFFICER).text
    assert '<meta http-equiv="refresh" content="3">' in running and "3초마다" in running


def test_restructure_sent_page_watches_alert_changes(setup, mask):
    client, _, store, _ = setup(loan=held_loan("paused"))
    form = {"csrf": csrf(client), "decision": "approve", "reason": "3개월 유예"}
    sent = client.post("/loans/app-1/alerts/ew-rp-1-w01/restructure", data=form, headers=OFFICER)
    url = re.search(r'content="1;url=(/loans/app-1\?since=[0-9a-f]{12})"', sent.text).group(1)
    assert "반영을 기다리는 중" in client.get(url, headers=OFFICER).text
    store.alerts = [make_alert(mask, "approved")]
    assert "반영을 기다리는 중" not in client.get(url, headers=OFFICER).text


def test_loans_list_shows_business_name_to_deciders(setup):
    client, *_ = setup()
    assert "성수데일리룩" in client.get("/loans", headers=OFFICER).text
    assert "성수데일리룩" not in client.get("/loans", headers=AUDITOR_H).text
