import base64
import json
import re
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from agents.collections import RepaymentCase, build_proposal
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
        "alert_ids": ["ew-rp-1-w01"], "case_ids": ["rc-rp-1-i1"], "error": None,
    }
    repayment = None if status is None else {"installment_krw": 1_779_000, "schedule": [
        {"no": 1, "due_on": "2026-10-01", "status": "missed", "reminded_on": "2026-09-28", "reminder_status": "sent",
         "txn_id": None, "case_id": "rc-rp-1-i1"},
        {"no": 2, "due_on": "2026-10-31", "status": "paid", "reminded_on": "2026-10-28", "reminder_status": "deferred",
         "txn_id": "t-pay-2", "case_id": None},
        {"no": 3, "due_on": "2026-11-30", "status": "paid", "reminded_on": "2026-11-27", "reminder_status": "sent",
         "txn_id": "t-pay-3", "case_id": None},
    ]}
    return {
        "id": "app-1", "loan_id": "app-1", "application_id": "app-1", "applicant_id": "seller-dailylook-01",
        "industry": "카페", "underwriting_consent_id": "c-1", "decision": "approve", "amount_krw": 20_000_000,
        "tenor_months": 12, "approved_at": "2026-10-31T09:00:00+09:00", "baseline": BASELINE,
        "monitoring": monitoring, "repayment": repayment, "updated_at": NOW.isoformat(),
    }


def make_alert(mask):
    return {
        "id": "ew-rp-1-w01", "schema_version": "1", "alert_id": "ew-rp-1-w01", "loan_id": "app-1", "week": 1,
        "as_of": "2026-09-07", "severity": "high",
        "summary": mask("김대표 개인이체와 고금리 상환이 겹쳤다"),
        "signals": [{"kind": "new_debt", "text": mask("김대표 개인이체 뒤 고금리 상환 1건"),
                     "metric_ids": ["W:1:high_cost_count", "W:9:revenue"], "txn_ids": ["t2"]}],
        "recommended_action": "hardship_review", "triggers": ["new_debt"], "models": {"investigator": "gpt-5.4"},
        "replay_id": "rp-1", "status": "open", "created_at": NOW.isoformat(), "event_error": None,
        "collections": None, "restructure": None,
    }


def make_case(mask, restructure_status="pending_officer"):
    return {
        "id": "rc-rp-1-i1", "kind": "repayment_case", "case_id": "rc-rp-1-i1", "loan_id": "app-1", "replay_id": "rp-1",
        "week": 5, "as_of": "2026-10-01", "installment": {"no": 1, "due_on": "2026-10-01", "amount_krw": 1_779_000},
        "missed_count": 1,
        "recent_alert": {"alert_id": "ew-rp-1-w01", "week": 1, "severity": "high",
                         "summary": mask("김대표 개인이체와 고금리 상환이 겹쳤다")},
        "status": "restructure_pending", "created_at": NOW.isoformat(), "event_error": None,
        "collections": {"decision": "routed", "reason": "상담 연결", "at": NOW.isoformat(),
                        "contacts": [{"ref": "rc-rp-1-i1", "on": "2026-10-01", "at": NOW.isoformat(),
                                      "script_id": "hardship_offer", "status": "sent", "reason": None,
                                      "by": "agent"}], "record": {}},
        "restructure": {"request_id": "rs-rc-rp-1-i1", "category": "debt_overload",
                        "note": mask("김대표 대출 중첩"), "status": restructure_status,
                        "created_at": NOW.isoformat()},
    }


def make_proposal_case(mask, status="pending_officer", hardship=True, **decided):
    """추심 제안(proposal)이 있는 미납 추심 건(2026-11-11 이후). 제안서는 실제 조립 함수로 만든다."""
    case = make_case(mask)
    case.update(
        status="proposal_pending", missed_count=2, restructure=None,
        collections={"decision": "proposed", "reason": "누적 미납", "contacts": [], "record": {}, "at": NOW.isoformat()},
    )
    case["recent_alert"] = {**case["recent_alert"], "primary_cause": "settlement_hold",
                            "check_action": "정산 상태와 다른 계좌 가용자금 확인이 필요합니다."}
    draft = {"script_id": "hardship_offer", "reason": mask("김대표 정산 보류가 이어져 상담이 필요하다"), "by": "agent",
             "hardship": {"category": "revenue_shock", "note": mask("김대표 정산 보류")} if hardship else None}
    escalation = [{"script_id": "balance_warning", "on": "2026-09-10", "status": "sent"}]
    proposal = build_proposal(RepaymentCase.from_doc(case), draft, [], NOW, escalation, lambda s: s)
    case["proposal"] = {**proposal, "status": status, **decided}
    return case


class FakeMonitoring:
    def __init__(self, loans, alerts, contacts=None):
        self.loans = {d["id"]: d for d in loans}
        self.alerts = alerts
        self.contacts = contacts or {}
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

    def get_contacts(self, loan_id, replay_id):
        return self.contacts.get((loan_id, replay_id))


class FakeQueue:
    def __init__(self):
        self.sent = []

    def send(self, queue, message):
        self.sent.append((queue, message))


@pytest.fixture
def setup(fake_raw, raw_profile, raw_txns, key, mask):
    def build(loan=None, alerts=None, monitoring=True):
        raw = fake_raw({("consent-c-1", "profile.json"): raw_profile, ("consent-m-1", "flow.json"): raw_txns})
        store = FakeMonitoring([loan or make_loan()], [make_alert(mask), make_case(mask)] if alerts is None else alerts)
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
    assert "hardship_offer" in page and 'action="/loans/app-1/alerts/rc-rp-1-i1/restructure"' in page
    assert "김대표 대출 중첩" in page and "/alerts/ew-rp-1-w01/restructure" not in page
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
    # 재생 중에는 자동 새로 고침이 입력 중인 사유를 지우므로 결정 폼을 숨긴다.
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
    r = client.post("/loans/app-1/alerts/rc-rp-1-i1/restructure", data=form, headers=OFFICER)
    assert r.status_code == 202
    # kind(기본값 restructure)는 빼서 보낸다: 이전 오케스트레이터(extra=forbid)도 받는다.
    assert queue.sent == [(RESTRUCTURE_QUEUE, {"loan_id": "app-1", "alert_id": "rc-rp-1-i1", "decision": "approve",
                                               "officer_id": "oid-officer", "reason": "3개월 유예"})]


@pytest.mark.parametrize("fields, status", [
    ({"decision": "approve", "reason": " "}, 422),
    ({"decision": "maybe", "reason": "x"}, 422),
    ({"decision": "approve", "reason": "x" * 501}, 422),
])
def test_restructure_decision_validation(setup, fields, status):
    client, queue, *_ = setup()
    r = client.post("/loans/app-1/alerts/rc-rp-1-i1/restructure", data={"csrf": csrf(client)} | fields, headers=OFFICER)
    assert r.status_code == status and queue.sent == []


def test_restructure_decision_needs_pending_request_of_current_replay(setup, mask):
    client, queue, *_ = setup(alerts=[make_case(mask, restructure_status="approved")])
    token = csrf(client)
    path = "/loans/app-1/alerts/rc-rp-1-i1/restructure"
    assert client.post(path, data={"csrf": token, "decision": "approve", "reason": "x"}, headers=OFFICER).status_code == 409
    other, queue2, *_ = setup(loan=make_loan(replay_id="rp-2"))
    assert other.post(path, data={"csrf": csrf(other), "decision": "approve", "reason": "x"},
                      headers=OFFICER).status_code == 404
    assert queue.sent == queue2.sent == []


def held_loan(status, paused_week=2):
    loan = make_loan(status=status)
    loan["monitoring"].update(processed_week=paused_week, paused_week=paused_week, pause_case_id="rc-rp-1-i1",
                              resumed_at=[], stopped_at=None)
    return loan


def test_paused_replay_blocks_new_replay_and_opens_restructure_form(setup):
    client, queue, *_ = setup(loan=held_loan("paused"))
    page = client.get("/loans/app-1", headers=OFFICER).text
    assert "심사역 결정 대기" in page and "재조정 요청으로 재생을 멈췄습니다" in page and "남은 11주" in page
    assert 'http-equiv="refresh"' not in page  # 사유 입력이 지워지지 않게 새로 고치지 않는다
    assert "/restructure" in page and "/replay/resume" not in page
    assert re.search(r'<button type="submit" disabled>사후 13주 재생\(다시\)</button>', page)
    r = client.post("/loans/app-1/replay", data={"csrf": csrf(client)}, headers=OFFICER)
    assert r.status_code == 409 and "재개 확인이 끝나야" in r.text
    form = {"csrf": csrf(client), "decision": "decline", "reason": "상환 여력 없음"}
    sent = client.post("/loans/app-1/alerts/rc-rp-1-i1/restructure", data=form, headers=OFFICER)
    assert sent.status_code == 202 and "재생을 이어갈지 선택하세요" in sent.text
    assert [q for q, _ in queue.sent] == [RESTRUCTURE_QUEUE]


@pytest.mark.parametrize(("action", "text"), [("resume", "남은 11주 재생을 이어서"), ("stop", "종료하도록")])
def test_awaiting_resume_offers_resume_and_stop(setup, mask, action, text):
    client, queue, *_ = setup(loan=held_loan("awaiting_resume"), alerts=[make_case(mask, "approved")])
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
    assert re.search(r'content="7;url=/loans/app-1\?since=[0-9a-f]{12}&amp;n=1"', waiting)
    store.loans["app-1"] = make_loan(status="replaying", started_at=NOW - timedelta(minutes=1), replay_id="rp-2")
    running = client.get(m.group(1), headers=OFFICER).text
    assert '<meta http-equiv="refresh" content="7">' in running and "7초마다" in running


def test_restructure_sent_page_watches_alert_changes(setup, mask):
    client, _, store, _ = setup(loan=held_loan("paused"))
    form = {"csrf": csrf(client), "decision": "approve", "reason": "3개월 유예"}
    sent = client.post("/loans/app-1/alerts/rc-rp-1-i1/restructure", data=form, headers=OFFICER)
    url = re.search(r'content="1;url=(/loans/app-1\?since=[0-9a-f]{12})"', sent.text).group(1)
    assert "반영을 기다리는 중" in client.get(url, headers=OFFICER).text
    store.alerts = [make_alert(mask), make_case(mask, "approved")]
    assert "반영을 기다리는 중" not in client.get(url, headers=OFFICER).text


def test_restructure_form_is_locked_while_the_decision_is_pending(setup):
    client, *_ = setup(loan=held_loan("paused"))
    page = client.get("/loans/app-1", headers=OFFICER).text
    assert '<button type="submit">결정 보내기</button>' in page and "data-decision-form" in page
    assert '<script src="/static/decision.js" defer></script>' in page
    form = {"csrf": csrf(client), "decision": "approve", "reason": "3개월 유예"}
    sent = client.post("/loans/app-1/alerts/rc-rp-1-i1/restructure", data=form, headers=OFFICER)
    url = re.search(r'content="1;url=(/loans/app-1\?since=[0-9a-f]{12})"', sent.text).group(1)
    waiting = client.get(url, headers=OFFICER).text
    assert '<button type="submit" disabled>결정 반영 대기 중</button>' in waiting
    assert '<textarea name="reason" rows="2" disabled>' in waiting


def test_loans_list_shows_business_name_to_deciders(setup):
    client, *_ = setup()
    assert "성수데일리룩" in client.get("/loans", headers=OFFICER).text
    assert "성수데일리룩" not in client.get("/loans", headers=AUDITOR_H).text


def test_loan_page_shows_ledger_and_case(setup):
    client, *_ = setup()
    page = client.get("/loans/app-1", headers=OFFICER).text
    assert "상환 원장" in page and 'class="r-missed"' in page and "t-pay-2" in page and "(보류)" in page
    assert 'id="rc-rp-1-i1"' in page and "1회차 미납" in page and 'href="#ew-rp-1-w01"' in page
    assert "상환 2/3회" in page and "미납 1" in page
    # 경보를 근거로 미납 추심이 연락했으면 경보 카드에도 그 연락을 보인다.
    card = page[page.index('id="ew-rp-1-w01"'):page.index('id="rc-rp-1-i1"')]
    assert "이 경보를 근거로 한 차주 연락" in card and 'href="#rc-rp-1-i1"' in card
    assert "1회차 미납 추심" in card and "hardship_offer" in card and "sent" in card
    assert "재조정 상담 요청 pending_officer" in card and "차주 연락 없음" not in page


def test_alert_card_without_followup_says_alert_alone_does_not_contact(setup, mask):
    client, *_ = setup(alerts=[make_alert(mask)])
    page = client.get("/loans/app-1", headers=OFFICER).text
    assert "심사역 검토 대상 · 이 경보로 나간 차주 연락 없음" in page
    assert "높음 경보가 처음 나면 잔고 부족 예상 알림" in page and "심사역이 승인해야 보냅니다" in page
    assert "경보만으로는 연락하지 않고" not in page


def test_legacy_alert_with_restructure_still_decidable(setup, mask):
    case = make_case(mask)
    legacy = {**make_alert(mask), "status": "restructure_pending", "collections": case["collections"],
              "restructure": {**case["restructure"], "request_id": "rs-ew-rp-1-w01"}}
    client, queue, *_ = setup(alerts=[legacy])
    page = client.get("/loans/app-1", headers=OFFICER).text
    assert 'action="/loans/app-1/alerts/ew-rp-1-w01/restructure"' in page and "차주 연락 없음" not in page
    form = {"csrf": csrf(client), "decision": "approve", "reason": "옛 경보"}
    assert client.post("/loans/app-1/alerts/ew-rp-1-w01/restructure", data=form, headers=OFFICER).status_code == 202
    assert queue.sent[0][1]["alert_id"] == "ew-rp-1-w01"


# ── 추심 제안 승인·수정·반려, 플랜 변경 승인, 응답 없이 계속(2026-11-11) ──

PROPOSAL_PATH = "/loans/app-1/cases/rc-rp-1-i1/proposal"
SPLIT = [{"no": 3, "part": 1, "due_on": "2026-11-30", "amount_krw": 889_500, "from_due_on": "2026-11-30"},
         {"no": 3, "part": 2, "due_on": "2026-12-04", "amount_krw": 889_500, "from_due_on": "2026-11-30"}]
MOVE = [{"no": 3, "part": None, "due_on": "2026-12-04", "amount_krw": 1_779_000, "from_due_on": "2026-11-30"}]


def plan_loan(offer_status="chosen", change_status="pending_officer", holds=("plan_change",), status="paused"):
    loan = held_loan(status)
    loan["monitoring"].update(holds=list(holds), escalation={"stage1": None, "stage2": None})
    loan["repayment"]["schedule"][2].update(status="scheduled", txn_id=None)
    loan["repayment"]["offer"] = {
        "offer_id": "of-rp-1", "week": 2, "sent_on": "2026-09-10", "status": offer_status,
        "options": [{"option": "move", "items": MOVE, "replaces": ["i3"]},
                    {"option": "split", "items": SPLIT, "replaces": ["i3"]},
                    {"option": "keep", "items": [], "replaces": []}],
        "choice": None if offer_status == "open" else "split", "responded_at": None,
    }
    loan["repayment"]["plan_change"] = None if change_status is None else {
        "change_id": "pc-rp-1", "option": "split", "items": SPLIT, "replaces": ["i3"], "status": change_status,
        "requested_at": NOW.isoformat(), "officer_id": None, "decided_at": None, "reason": None,
    }
    return loan


def test_proposal_card_shows_actions_evidence_checks_and_exact_text(setup, mask):
    case = make_proposal_case(mask)
    client, *_ = setup(loan=held_loan("paused"), alerts=[make_alert(mask), case])
    page = client.get("/loans/app-1", headers=OFFICER).text
    card = page[page.index('id="rc-rp-1-i1"'):]
    for s in ("제안 조치와 이유", "판단 근거", "정책 확인", "고객에게 보낼 문구", "상환 곤란 상담 안내",
              "승인 스크립트", "7일 7회", "금지 표현 없음", "재조정 근거", "잔고 부족 예상 알림",
              "정산 상태와 다른 계좌 가용자금 확인이 필요합니다.", "김대표 정산 보류가 이어져 상담이 필요하다"):
        assert s in card, s
    assert case["proposal"]["text"] in card and "✔" in card
    assert f'action="{PROPOSAL_PATH}"' in card and 'value="modify"' in card and 'value="reject"' in card
    assert 'name="include_hardship" value="1" checked' in card and 'maxlength="300"' in card
    # 고객 문구에는 실명 토큰이 없고, 이유는 심사역에게만 복원된다.
    assert "[[PERSON_" not in card
    auditor = client.get("/loans/app-1", headers=AUDITOR_H).text
    assert f'action="{PROPOSAL_PATH}"' not in auditor and case["proposal"]["text"] in auditor


def test_proposal_without_hardship_has_no_hardship_checkbox(setup, mask):
    client, *_ = setup(loan=held_loan("paused"), alerts=[make_proposal_case(mask, hardship=False)])
    page = client.get("/loans/app-1", headers=OFFICER).text
    assert 'name="include_hardship"' not in page and "재조정 근거" not in page


def test_proposal_approve_queues_collections_decision(setup, mask):
    client, queue, *_ = setup(loan=held_loan("paused"), alerts=[make_proposal_case(mask)])
    form = {"csrf": csrf(client), "decision": "approve", "text": "무시", "include_hardship": "1", "reason": "규칙대로",
            "officer_id": "someone-else"}
    r = client.post(PROPOSAL_PATH, data=form, headers=OFFICER)
    assert r.status_code == 202, r.text
    assert queue.sent == [(RESTRUCTURE_QUEUE, {
        "kind": "collections", "loan_id": "app-1", "case_id": "rc-rp-1-i1", "decision": "approve",
        "officer_id": "oid-officer", "text": None, "include_hardship": None, "reason": "규칙대로"})]


def test_proposal_modify_sends_text_and_hardship_choice(setup, mask):
    client, queue, *_ = setup(loan=held_loan("paused"), alerts=[make_proposal_case(mask)])
    form = {"csrf": csrf(client), "decision": "modify", "text": "  상환일 안내드립니다. 문의는 담당자에게 주세요.  "}
    assert client.post(PROPOSAL_PATH, data=form, headers=OFFICER).status_code == 202
    msg = queue.sent[0][1]
    assert msg["kind"] == "collections" and msg["decision"] == "modify"
    assert msg["text"] == "상환일 안내드립니다. 문의는 담당자에게 주세요." and msg["include_hardship"] is False


@pytest.mark.parametrize("text", ["법적 조치를 진행하겠습니다.", "", "가" * 301, "[[PERSON_1]]님 안내"],
                         ids=["banned", "empty", "too_long", "pii_token"])
def test_proposal_modify_with_banned_text_is_422_and_not_queued(setup, mask, text):
    client, queue, *_ = setup(loan=held_loan("paused"), alerts=[make_proposal_case(mask)])
    r = client.post(PROPOSAL_PATH, data={"csrf": csrf(client), "decision": "modify", "text": text}, headers=OFFICER)
    assert r.status_code == 422 and "고객 문구" in r.text and queue.sent == []


def test_proposal_reject_requires_reason(setup, mask):
    client, queue, *_ = setup(loan=held_loan("paused"), alerts=[make_proposal_case(mask)])
    r = client.post(PROPOSAL_PATH, data={"csrf": csrf(client), "decision": "reject", "reason": " "}, headers=OFFICER)
    assert r.status_code == 422 and "사유" in r.text
    bad = client.post(PROPOSAL_PATH, data={"csrf": csrf(client), "decision": "maybe"}, headers=OFFICER)
    assert bad.status_code == 422 and queue.sent == []
    ok = client.post(PROPOSAL_PATH, data={"csrf": csrf(client), "decision": "reject", "reason": "이미 통화함"},
                     headers=OFFICER)
    assert ok.status_code == 202 and queue.sent[0][1]["decision"] == "reject"


def test_proposal_not_pending_is_409(setup, mask):
    contact = {"ref": "rc-rp-1-i1", "on": "2026-11-02", "at": NOW.isoformat(), "script_id": "hardship_offer",
               "status": "deferred", "reason": "contact_hours", "by": "officer", "text": "x", "officer_id": "oid-officer"}
    done = make_proposal_case(mask, status="modified", officer_id="oid-officer", decided_at=NOW.isoformat(),
                              decision_reason="문구 순화", sent_text="수정한 안내 문구입니다.", contact=contact)
    client, queue, *_ = setup(loan=held_loan("awaiting_resume"), alerts=[done])
    page = client.get("/loans/app-1", headers=OFFICER).text
    card = page[page.index('id="rc-rp-1-i1"'):]
    assert "수정 후 승인" in card and "수정한 안내 문구입니다." in card and "보류" in card and "연락 시간 밖" in card
    assert f'action="{PROPOSAL_PATH}"' not in card
    r = client.post(PROPOSAL_PATH, data={"csrf": csrf(client), "decision": "approve"}, headers=OFFICER)
    assert r.status_code == 409
    nocase = client.post("/loans/app-1/cases/rc-nope/proposal", data={"csrf": csrf(client), "decision": "approve"},
                         headers=OFFICER)
    assert nocase.status_code == 404 and queue.sent == []


def test_proposal_form_waits_for_replay_and_needs_decider(setup, mask):
    client, queue, *_ = setup(loan=make_loan(status="replaying", started_at=NOW - timedelta(minutes=5)),
                              alerts=[make_proposal_case(mask)])
    page = client.get("/loans/app-1", headers=OFFICER).text
    assert "재생이 멈추면 결정할 수 있습니다" in page and f'action="{PROPOSAL_PATH}"' not in page
    assert client.post(PROPOSAL_PATH, data={"csrf": "0" * 64, "decision": "approve"}, headers=AUDITOR_H).status_code == 403
    assert queue.sent == []


def test_case_without_proposal_renders_as_before(setup, mask):
    client, *_ = setup(alerts=[make_case(mask)])
    page = client.get("/loans/app-1", headers=OFFICER).text
    card = page[page.index('id="rc-rp-1-i1"'):]
    assert "추심 에이전트 <small>routed</small>" in card and "제안 조치와 이유" not in card
    assert 'action="/loans/app-1/alerts/rc-rp-1-i1/restructure"' in card


def test_plan_change_card_and_decline_requires_reason(setup):
    client, queue, *_ = setup(loan=plan_loan(), alerts=[])
    page = client.get("/loans/app-1", headers=OFFICER).text
    assert 'id="plan-change"' in page and 'id="plan-offer"' in page and "분할납부" in page
    assert "3-1회차" in page and "3-2회차" in page and "2026-12-04" in page and "889,500원" in page
    assert 'action="/loans/app-1/plan/pc-rp-1"' in page
    path = "/loans/app-1/plan/pc-rp-1"
    r = client.post(path, data={"csrf": csrf(client), "decision": "decline", "reason": ""}, headers=OFFICER)
    assert r.status_code == 422 and queue.sent == []
    assert client.post("/loans/app-1/plan/pc-other", data={"csrf": csrf(client), "decision": "approve"},
                       headers=OFFICER).status_code == 404
    ok = client.post(path, data={"csrf": csrf(client), "decision": "approve", "reason": "확인"}, headers=OFFICER)
    assert ok.status_code == 202
    assert queue.sent == [(RESTRUCTURE_QUEUE, {"kind": "plan_change", "loan_id": "app-1", "change_id": "pc-rp-1",
                                               "decision": "approve", "officer_id": "oid-officer", "reason": "확인"})]


def test_decided_plan_change_is_409_and_shows_result(setup):
    loan = plan_loan(change_status="declined", holds=(), status="awaiting_resume")
    loan["repayment"]["plan_change"].update(officer_id="oid-officer", decided_at=NOW.isoformat(), reason="여력 부족")
    client, queue, *_ = setup(loan=loan, alerts=[])
    page = client.get("/loans/app-1", headers=OFFICER).text
    assert "반려" in page and "여력 부족" in page and 'action="/loans/app-1/plan/pc-rp-1"' not in page
    r = client.post("/loans/app-1/plan/pc-rp-1", data={"csrf": csrf(client), "decision": "approve"}, headers=OFFICER)
    assert r.status_code == 409 and queue.sent == []


def test_offer_expire_queues_message_only_when_open(setup):
    client, queue, *_ = setup(loan=plan_loan(offer_status="open", change_status=None, holds=("customer_response",),
                                             status="awaiting_customer"), alerts=[])
    page = client.get("/loans/app-1", headers=OFFICER).text
    assert "고객 응답 대기" in page and "고객 응답 없이 계속" in page and 'action="/loans/app-1/offer/expire"' in page
    r = client.post("/loans/app-1/offer/expire", data={"csrf": csrf(client)}, headers=OFFICER)
    assert r.status_code == 202
    assert queue.sent == [(RESTRUCTURE_QUEUE, {"kind": "offer_expire", "loan_id": "app-1", "offer_id": "of-rp-1",
                                               "officer_id": "oid-officer"})]
    closed, queue2, *_ = setup(loan=plan_loan(), alerts=[])
    assert 'action="/loans/app-1/offer/expire"' not in closed.get("/loans/app-1", headers=OFFICER).text
    assert closed.post("/loans/app-1/offer/expire", data={"csrf": csrf(closed)}, headers=OFFICER).status_code == 409
    assert queue2.sent == []


def test_pause_banner_lists_holds_in_order(setup, mask):
    loan = plan_loan(offer_status="open", change_status=None, holds=("customer_response", "collections_proposal"))
    client, *_ = setup(loan=loan, alerts=[make_proposal_case(mask)])
    page = client.get("/loans/app-1", headers=OFFICER).text
    banner = page[page.index('aria-label="재생 일시정지"'):page.index("</section>", page.index('aria-label="재생 일시정지"'))]
    assert banner.index("추심 승인 대기") < banner.index("고객 응답 대기")
    assert 'href="#rc-rp-1-i1"' in banner and 'href="#plan-offer"' in banner
    assert "재조정 요청으로 재생을 멈췄습니다" not in page


def test_ledger_shows_item_amount_parts_and_early_reminder(setup):
    loan = make_loan()
    sched = loan["repayment"]["schedule"]
    sched[2].update(early_reminded_on="2026-11-25", early_reminder_status="deferred")
    sched.append({**sched[2], "no": 4, "part": 1, "amount_krw": 889_500, "from_due_on": "2026-12-30",
                  "due_on": "2026-12-30", "status": "scheduled", "txn_id": None, "early_reminded_on": None})
    client, *_ = setup(loan=loan)
    page = client.get("/loans/app-1", headers=OFFICER).text
    ledger = page[page.index("상환 원장"):]
    assert "5일 전 안내" in ledger and "2026-11-25 (보류)" in ledger and "4-1회차" in ledger and "889,500원" in ledger
    assert "시뮬레이션 날짜 10시" in ledger and "심사역 승인" in ledger
