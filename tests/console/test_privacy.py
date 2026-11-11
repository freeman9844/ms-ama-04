"""P5b 콘솔: 차주 시뮬레이터(동의 철회·이의제기), 동의 데모 버튼, 삭제 증명, 이의제기 처리."""
import base64
import json
import re

import pytest
from fastapi.testclient import TestClient

from console.app import ConsoleDeps, create_app
from console.auth import AUDITOR, CREDIT_MANAGER, CREDIT_OFFICER
from tests.console.test_monitoring import NOW, FakeMonitoring, plan_loan

OID = "http://schemas.microsoft.com/identity/claims/objectidentifier"


def principal(*roles, oid="oid-officer", name="박심사"):
    claims = [{"typ": OID, "val": oid}, {"typ": "name", "val": name}] + [{"typ": "roles", "val": r} for r in roles]
    body = {"auth_typ": "aad", "name_typ": "name", "role_typ": "roles", "claims": claims}
    return {"X-MS-CLIENT-PRINCIPAL": base64.b64encode(json.dumps(body).encode()).decode()}


OFFICER = principal(CREDIT_OFFICER)
MANAGER = principal(CREDIT_MANAGER, oid="oid-manager")
AUDITOR_H = principal(AUDITOR, oid="oid-auditor")


class FakeStore:
    def __init__(self, docs):
        self.docs = {d["id"]: d for d in docs}

    def list_recent(self, limit):
        return list(self.docs.values())[:limit]

    def get(self, application_id):
        return self.docs.get(application_id)


class FakeQueue:
    def __init__(self):
        self.sent = []

    def send(self, queue, message):
        self.sent.append((queue, message))


class FakePrivacy:
    def __init__(self, consents, proofs, grievances):
        self.consents = {c["id"]: c for c in consents}
        self.proofs = {p["id"]: p for p in proofs}
        self.grievances = {(g["application_id"], g["id"]): g for g in grievances}

    def consents_for_applicant(self, applicant_id):
        return [c for c in self.consents.values() if c["applicant_id"] == applicant_id]

    def consents_for_application(self, application_id):
        return [c for c in self.consents.values() if c["application_id"] == application_id]

    def get_consent(self, consent_id):
        return self.consents.get(consent_id)

    def get_proof(self, consent_id):
        return self.proofs.get(consent_id)

    def list_grievances(self, limit):
        return list(self.grievances.values())[:limit]

    def grievances_for_applicant(self, applicant_id):
        return [g for g in self.grievances.values() if g["applicant_id"] == applicant_id]

    def get_grievance(self, application_id, grievance_id):
        return self.grievances.get((application_id, grievance_id))


def consent(consent_id, application_id, status="granted", **extra):
    return {
        "id": consent_id, "consent_id": consent_id, "application_id": application_id,
        "applicant_id": "seller-dailylook-01", "scopes": ["account", "tax", "pg"], "purpose": "운전자금 대출 심사",
        "granted_at": "2026-11-01T09:00:00+09:00", "expires_at": "2027-01-30T09:00:00+09:00",
        "status": status, "notices": [], **extra,
    }


PROOF = {
    "id": "c-0", "consent_id": "c-0", "application_id": "app-2", "applicant_id": "seller-dailylook-01",
    "reason": "expired", "trigger": "expire_now", "requested_by": "oid-officer",
    "deleted_at": "2026-11-02T10:00:00+09:00",
    "items": [
        {"kind": "raw_container", "target": "consent-c-0", "outcome": "deleted", "verified": True, "detail": None},
        {"kind": "consent_registry", "target": "c-0", "outcome": "absent", "verified": True, "detail": None},
        {"kind": "conversation", "target": "conv_abc", "outcome": "deleted", "verified": True, "detail": None},
    ],
    "manifest": [{"name": "profile.json", "size": 321, "sha256": "a" * 64},
                 {"name": "transactions.json", "size": 4567, "sha256": "b" * 64}],
    "manifest_sha256": "f" * 64,
    "retained": [{"kind": "application_record", "target": "applications/app-2",
                  "note": "심사 판단 기록(파생 지표와 근거 ID)"}],
    "complete": True, "attempts": 1, "event_published": False, "event_error": "servicebus_disabled",
}


def grievance(grievance_id="grv-abc123", status="pending_officer", **extra):
    return {
        "id": grievance_id, "grievance_id": grievance_id, "application_id": "app-2",
        "applicant_id": "seller-dailylook-01", "text": "<b>한도</b>가 너무 낮습니다", "submitted_at": "2026-11-02T11:00:00+09:00",
        "status": status,
        "record": {
            "status": "approved",
            "policy": {"decision": "eligible", "max_amount_krw": 20_000_000, "rate_band": "B",
                       "reason_codes": [{"code": "R101", "message": "DSCR 충족", "field": "dscr", "observed": 1.8,
                                         "threshold": 1.25}], "rules_version": "2026.11-v1"},
            "assessment": {"summary": "계절성이 있다", "counterfactuals": []},
            "officer_decision": {"decision": "approve", "officer_id": "oid-officer", "amount_krw": 15_000_000,
                                 "conditions": [], "reason": ""},
        },
        "decision_attempts": [], "decision": None, "reply": None, "resolved_at": None, **extra,
    }


@pytest.fixture
def env(make_doc, raw, key):
    decided = make_doc(id="app-2", application_id="app-2", status="approved",
                       officer_decision={"decision": "approve", "officer_id": "oid-officer",
                                         "amount_krw": 15_000_000, "conditions": [], "reason": ""})
    other = make_doc(id="app-9", application_id="app-9", applicant_id="seller-knitroom-04", status="declined",
                     officer_decision={"decision": "decline", "officer_id": "oid-officer", "amount_krw": None,
                                       "conditions": [], "reason": "부족"})
    privacy = FakePrivacy(
        [
            consent("c-1", "app-1", notices=[{"kind": "renewal", "at": "2026-11-02T09:00:00+09:00",
                                              "title": "동의 만료 예정 안내", "body": "2027년 1월 30일에 만료됩니다.",
                                              "source": "agent", "issues": []}]),
            consent("c-0", "app-2", status="expired", ended_at="2026-11-02T10:00:00+09:00",
                    deletion_proof_id="c-0", deletion_complete=True,
                    notices=[{"kind": "deletion", "at": "2026-11-02T10:00:01+09:00", "title": "원천 데이터 삭제 완료",
                              "body": "삭제 증명 번호 c-0", "source": "template", "issues": ["x"]}]),
            consent("c-9", "app-9") | {"applicant_id": "seller-knitroom-04"},
        ],
        [PROOF],
        [grievance(), grievance("grv-done01", status="resolved_upheld",
                                decision={"outcome": "uphold", "officer_id": "oid-manager", "reason": "정책 한도",
                                          "at": "2026-11-02T12:00:00+09:00"},
                                reply={"title": "이의제기 처리 결과", "body": "원결정을 유지합니다.", "source": "agent",
                                       "issues": [], "at": "2026-11-02T12:00:00+09:00"},
                                resolved_at="2026-11-02T12:00:00+09:00")],
    )
    queue = FakeQueue()
    deps = ConsoleDeps(store=FakeStore([make_doc(), decided, other]), raw=raw, queue=queue, pseudonym_key=key,
                       privacy=privacy, new_grievance_id=lambda: "grv-new001")
    return TestClient(create_app(deps), follow_redirects=False), queue, privacy


def csrf(client, headers, path):
    page = client.get(path, headers=headers).text
    return re.search(r'name="csrf" value="([0-9a-f]{64})"', page).group(1)


def post(client, headers, path, token_path, **form):
    return client.post(path, headers=headers, data={"csrf": csrf(client, headers, token_path)} | form)


# ── 차주 시뮬레이터 ──

def test_nav_links_borrower_and_grievances(env):
    client, _, _ = env
    page = client.get("/", headers=OFFICER).text
    assert 'href="/borrower"' in page and 'href="/grievances"' in page


def test_borrower_index_lists_demo_applicants(env):
    client, _, _ = env
    page = client.get("/borrower", headers=OFFICER).text
    assert 'href="/borrower/seller-dailylook-01"' in page and 'href="/borrower/seller-candle-05"' in page


def test_borrower_page_shows_own_consents_notices_and_grievances(env):
    client, _, _ = env
    page = client.get("/borrower/seller-dailylook-01", headers=OFFICER).text
    assert "c-1" in page and "c-0" in page and "c-9" not in page
    assert "계좌 거래내역" in page and "운전자금 대출 심사" in page
    assert "유효" in page and "만료" in page
    assert "동의 만료 예정 안내" in page and "2027년 1월 30일에 만료됩니다." in page
    assert 'href="/consents/c-0/proof"' in page
    # 철회는 유효한 동의에만 있다.
    assert 'action="/borrower/seller-dailylook-01/consents/c-1/revoke"' in page
    assert "/consents/c-0/revoke" not in page
    # 이의제기는 결정이 난 자기 신청에만 낼 수 있다.
    assert re.search(r'<option value="app-2"', page) and not re.search(r'<option value="app-1"', page)
    assert "원결정을 유지합니다." in page and "처리 대기" in page
    assert "&lt;b&gt;한도&lt;/b&gt;" in page


def test_auditor_sees_borrower_page_without_forms(env):
    client, _, _ = env
    page = client.get("/borrower/seller-dailylook-01", headers=AUDITOR_H).text
    assert "c-1" in page and "/revoke" not in page and 'name="csrf"' not in page


def test_revoke_sends_consent_command_as_borrower(env):
    client, queue, _ = env
    path = "/borrower/seller-dailylook-01"
    resp = post(client, OFFICER, f"{path}/consents/c-1/revoke", path)
    assert resp.status_code == 202
    assert queue.sent == [("consent-commands", {"consent_id": "c-1", "action": "revoke",
                                                "requested_by": "borrower:seller-dailylook-01", "application_id": "app-1"})]


@pytest.mark.parametrize("consent_id, status", [("c-9", 404), ("c-x", 404), ("c-0", 409)])
def test_revoke_only_own_granted_consent(env, consent_id, status):
    client, queue, _ = env
    path = "/borrower/seller-dailylook-01"
    assert post(client, OFFICER, f"{path}/consents/{consent_id}/revoke", path).status_code == status
    assert queue.sent == []


def test_borrower_posts_need_csrf_and_decider_role(env):
    client, queue, _ = env
    path = "/borrower/seller-dailylook-01"
    token = csrf(client, OFFICER, path)
    assert client.post(f"{path}/consents/c-1/revoke", headers=OFFICER, data={"csrf": ""}).status_code == 403
    assert client.post(f"{path}/consents/c-1/revoke", headers=AUDITOR_H, data={"csrf": token}).status_code == 403
    assert client.post(f"{path}/grievances", headers=AUDITOR_H,
                       data={"csrf": token, "application_id": "app-2", "text": "x"}).status_code == 403
    assert queue.sent == []


def test_grievance_submission_is_enqueued(env):
    client, queue, _ = env
    path = "/borrower/seller-dailylook-01"
    resp = post(client, OFFICER, f"{path}/grievances", path, application_id="app-2", text="  한도가 낮습니다  ")
    assert resp.status_code == 202
    assert queue.sent == [("grievances", {"grievance_id": "grv-new001", "application_id": "app-2",
                                          "applicant_id": "seller-dailylook-01", "text": "한도가 낮습니다"})]


@pytest.mark.parametrize("fields, status", [
    ({"application_id": "app-2", "text": " "}, 422),
    ({"application_id": "app-2", "text": "가" * 1001}, 422),
    ({"application_id": "app-1", "text": "결정 전"}, 409),
    ({"application_id": "app-9", "text": "남의 신청"}, 404),
    ({"application_id": "nope", "text": "없음"}, 404),
])
def test_grievance_submission_validation(env, fields, status):
    client, queue, _ = env
    path = "/borrower/seller-dailylook-01"
    assert post(client, OFFICER, f"{path}/grievances", path, **fields).status_code == status
    assert queue.sent == []


# ── 신청 상세의 동의 영역과 데모 버튼 ──

def test_application_detail_shows_consents_with_demo_buttons(env):
    client, _, _ = env
    page = client.get("/applications/app-1", headers=OFFICER).text
    assert "c-1" in page and "c-0" not in page
    assert 'action="/applications/app-1/consents/c-1/expire_now"' in page
    assert 'action="/applications/app-1/consents/c-1/remind_now"' in page
    ended = client.get("/applications/app-2", headers=OFFICER).text
    assert 'href="/consents/c-0/proof"' in ended and "/consents/c-0/expire_now" not in ended
    assert "/expire_now" not in client.get("/applications/app-1", headers=AUDITOR_H).text


@pytest.mark.parametrize("action", ["expire_now", "remind_now"])
def test_demo_consent_command_uses_principal(env, action):
    client, queue, _ = env
    resp = post(client, MANAGER, f"/applications/app-1/consents/c-1/{action}", "/applications/app-1")
    assert resp.status_code == 202
    assert queue.sent == [("consent-commands", {"consent_id": "c-1", "action": action,
                                                "requested_by": "oid-manager", "application_id": "app-1"})]


@pytest.mark.parametrize("path, status", [
    ("/applications/app-1/consents/c-1/revoke", 404),
    ("/applications/app-1/consents/c-0/expire_now", 404),
    ("/applications/app-2/consents/c-0/expire_now", 409),
])
def test_demo_consent_command_guards(env, path, status):
    client, queue, _ = env
    assert post(client, OFFICER, path, "/applications/app-1").status_code == status
    assert queue.sent == []


# ── 삭제 증명 ──

def test_proof_page_lists_verified_items_manifest_and_retained_record(env):
    client, _, _ = env
    page = client.get("/consents/c-0/proof", headers=AUDITOR_H)
    assert page.status_code == 200
    text = page.text
    assert "완료" in text and "동의 기간 만료" in text and "expire_now" in text
    assert "원천 데이터(Blob)" in text and "Foundry 대화" in text and "conv_abc" in text
    assert "transactions.json" in text and "4567" in text and "f" * 64 in text
    assert "applications/app-2" in text and "심사 판단 기록" in text
    assert 'href="/applications/app-2"' in text


def test_missing_proof_is_404(env):
    client, _, _ = env
    assert client.get("/consents/c-1/proof", headers=OFFICER).status_code == 404


# ── 이의제기 처리 ──

def test_grievance_list_and_detail(env):
    client, _, _ = env
    page = client.get("/grievances", headers=AUDITOR_H).text
    assert 'href="/grievances/app-2/grv-abc123"' in page and "처리 대기" in page and "원결정 유지" in page
    detail = client.get("/grievances/app-2/grv-abc123", headers=OFFICER).text
    assert "&lt;b&gt;한도&lt;/b&gt;" in detail and "R101" in detail and "15,000,000원" in detail
    assert 'action="/grievances/app-2/grv-abc123/decision"' in detail
    assert "/decision" not in client.get("/grievances/app-2/grv-abc123", headers=AUDITOR_H).text
    done = client.get("/grievances/app-2/grv-done01", headers=OFFICER).text
    assert "원결정을 유지합니다." in done and "/decision" not in done
    assert client.get("/grievances/app-2/grv-nope00", headers=OFFICER).status_code == 404


def test_uphold_decision_is_sent_with_principal(env):
    client, queue, _ = env
    path = "/grievances/app-2/grv-abc123"
    resp = post(client, MANAGER, f"{path}/decision", path, outcome="uphold", reason=" 정책 한도 ",
                officer_id="spoofed", new_decision="approve", amount_krw="1")
    assert resp.status_code == 202
    assert queue.sent == [("grievance-decisions", {
        "grievance_id": "grv-abc123", "application_id": "app-2", "outcome": "uphold", "officer_id": "oid-manager",
        "reason": "정책 한도", "new_decision": None, "amount_krw": None, "conditions": []})]


def test_rereview_is_validated_against_recorded_policy(env):
    client, queue, _ = env
    path = "/grievances/app-2/grv-abc123"
    over = post(client, OFFICER, f"{path}/decision", path, outcome="rereview", new_decision="approve",
                amount_krw="25,000,000", reason="재심사")
    assert over.status_code == 422 and "한도" in over.text
    assert queue.sent == []
    retired = post(client, OFFICER, f"{path}/decision", path, outcome="rereview", new_decision="conditional",
                   amount_krw="18,000,000", conditions="월별 매출 보고\r\n", reason="재심사")
    assert retired.status_code == 422 and queue.sent == []
    ok = post(client, OFFICER, f"{path}/decision", path, outcome="rereview", new_decision="approve",
              amount_krw="18,000,000", reason="재심사")
    assert ok.status_code == 202
    assert queue.sent[-1][1] == {
        "grievance_id": "grv-abc123", "application_id": "app-2", "outcome": "rereview", "officer_id": "oid-officer",
        "reason": "재심사", "new_decision": "approve", "amount_krw": 18_000_000, "conditions": []}


@pytest.mark.parametrize("fields", [{"outcome": "uphold", "reason": ""}, {"outcome": "maybe", "reason": "x"},
                                    {"outcome": "rereview", "reason": "x", "new_decision": ""}])
def test_invalid_grievance_decisions_are_rejected(env, fields):
    client, queue, _ = env
    path = "/grievances/app-2/grv-abc123"
    assert post(client, OFFICER, f"{path}/decision", path, **fields).status_code == 422
    assert queue.sent == []


def test_grievance_decision_guards(env):
    client, queue, _ = env
    path = "/grievances/app-2/grv-abc123"
    token = csrf(client, OFFICER, path)
    form = {"csrf": token, "outcome": "uphold", "reason": "x"}
    assert client.post(f"{path}/decision", headers=AUDITOR_H, data=form).status_code == 403
    assert client.post("/grievances/app-2/grv-done01/decision", headers=OFFICER, data=form).status_code == 409
    assert client.post("/grievances/app-2/grv-nope00/decision", headers=OFFICER, data=form).status_code == 404
    assert queue.sent == []


# ── 철회 종료 상태와 비활성 구성 ──

def test_withdrawn_is_terminal_with_label(env, make_doc):
    client, _, _ = env
    client.app.state.deps.store.docs["app-1"] = make_doc(
        status="withdrawn", withdrawal={"consent_id": "c-1", "requested_by": "borrower:seller-dailylook-01",
                                        "at": "2026-11-02T09:30:00+09:00"})
    page = client.get("/applications/app-1", headers=OFFICER).text
    assert "철회 종료" in page and 'http-equiv="refresh"' not in page
    assert "borrower:seller-dailylook-01" in page


def test_privacy_pages_404_when_not_configured(make_doc, raw, key):
    deps = ConsoleDeps(store=FakeStore([make_doc()]), raw=raw, queue=FakeQueue(), pseudonym_key=key)
    client = TestClient(create_app(deps))
    for path in ("/borrower/seller-dailylook-01", "/grievances", "/consents/c-1/proof"):
        assert client.get(path, headers=OFFICER).status_code == 404
    assert client.get("/applications/app-1", headers=OFFICER).status_code == 200

# ── 셀프 상환 플랜(2026-11-11) ──

PLAN_PATH = "/borrower/seller-dailylook-01/plan"
BORROWER = "/borrower/seller-dailylook-01"


@pytest.fixture
def plan_env(make_doc, raw, key):
    def build(loan, contacts=None):
        queue = FakeQueue()
        deps = ConsoleDeps(store=FakeStore([make_doc()]), raw=raw, queue=queue, pseudonym_key=key,
                           privacy=FakePrivacy([consent("c-1", "app-1")], [], []),
                           monitoring=FakeMonitoring([loan], [], contacts))
        return TestClient(create_app(deps), follow_redirects=False), queue

    return build


def self_plan(page):
    start = page.index('id="self-plan"')
    return page[start:page.index("</section>", start)]


def choose(client, **form):
    return post(client, OFFICER, PLAN_PATH, BORROWER, **({"loan_id": "app-1", "offer_id": "of-rp-1"} | form))


def test_borrower_sees_open_offer_with_option_schedules(plan_env):
    client, _ = plan_env(plan_loan(offer_status="open", change_status=None, holds=("customer_response",),
                                   status="awaiting_customer"))
    section = self_plan(client.get(BORROWER, headers=OFFICER).text)
    assert "상환 플랜 제안" in section
    for s in ("상환일 이동", "분할납부", "지금 일정 유지", "3회차", "2026-11-30 → 2026-12-04", "1,779,000원",
              "3-1회차", "3-2회차", "889,500원"):
        assert s in section, s
    assert f'action="{PLAN_PATH}"' in section
    assert 'name="loan_id" value="app-1"' in section and 'name="offer_id" value="of-rp-1"' in section
    for option in ("move", "split", "keep"):
        assert f'name="option" value="{option}"' in section
    # 감사자는 보기만 한다.
    auditor = self_plan(client.get(BORROWER, headers=AUDITOR_H).text)
    assert "분할납부" in auditor and f'action="{PLAN_PATH}"' not in auditor


def test_borrower_choice_queues_plan_choice(plan_env):
    client, queue = plan_env(plan_loan(offer_status="open", change_status=None, holds=("customer_response",),
                                       status="awaiting_customer"))
    resp = choose(client, option="move")
    assert resp.status_code == 202, resp.text
    assert queue.sent == [("restructure-decisions", {"kind": "plan_choice", "loan_id": "app-1",
                                                     "offer_id": "of-rp-1", "option": "move"})]


@pytest.mark.parametrize("offer_status, offer_id", [("expired", "of-rp-1"), ("chosen", "of-rp-1"),
                                                    ("open", "of-rp-old")])
def test_borrower_choice_on_expired_offer_is_409(plan_env, offer_status, offer_id):
    change = None if offer_status != "chosen" else "pending_officer"
    client, queue = plan_env(plan_loan(offer_status=offer_status, change_status=change))
    resp = choose(client, option="keep", offer_id=offer_id)
    assert resp.status_code == 409 and "상환 플랜 제안" in resp.text
    assert queue.sent == []


def test_borrower_choice_unknown_option_is_422(plan_env):
    loan = plan_loan(offer_status="open", change_status=None, holds=("customer_response",), status="awaiting_customer")
    loan["repayment"]["offer"]["options"] = [o for o in loan["repayment"]["offer"]["options"] if o["option"] != "move"]
    client, queue = plan_env(loan)
    assert choose(client, option="move").status_code == 422
    assert choose(client, option="later").status_code == 422
    assert queue.sent == []
    # 다른 차주의 대출·없는 대출은 404, 감사자는 403.
    other = plan_loan(offer_status="open", change_status=None) | {"applicant_id": "seller-knitroom-04"}
    client2, queue2 = plan_env(other)
    assert choose(client2, option="keep").status_code == 404
    assert choose(client2, option="keep", loan_id="app-x").status_code == 404
    token = csrf(client, OFFICER, BORROWER)
    assert client.post(PLAN_PATH, headers=AUDITOR_H, data={"csrf": token, "loan_id": "app-1", "offer_id": "of-rp-1",
                                                           "option": "keep"}).status_code == 403
    assert queue2.sent == []


def test_expired_offer_is_shown_as_expired(plan_env):
    loan = plan_loan(offer_status="expired", change_status=None, holds=(), status="awaiting_resume")
    loan["repayment"]["offer"].update(choice=None, expired_at=NOW.isoformat(), expired_by="oid-officer")
    client, _ = plan_env(loan)
    section = self_plan(client.get(BORROWER, headers=OFFICER).text)
    assert "만료" in section and f'action="{PLAN_PATH}"' not in section and "oid-officer" not in section


@pytest.mark.parametrize("offer_status, change_status, text", [
    ("chosen", "pending_officer", "심사역 확인 중"),
    ("chosen", "approved", "승인"),
    ("chosen", "declined", "반려"),
    ("kept", None, "지금 일정 유지"),
])
def test_responded_offer_shows_result_without_buttons(plan_env, offer_status, change_status, text):
    loan = plan_loan(offer_status=offer_status, change_status=change_status)
    if change_status in ("approved", "declined"):
        loan["repayment"]["plan_change"].update(officer_id="oid-officer", decided_at=NOW.isoformat(), reason="내부 메모")
    client, _ = plan_env(loan)
    section = self_plan(client.get(BORROWER, headers=OFFICER).text)
    assert text in section and f'action="{PLAN_PATH}"' not in section
    assert "oid-officer" not in section and "내부 메모" not in section


def test_borrower_sees_received_notices(plan_env):
    contacts = {("app-1", "rp-1"): {"id": "contacts-rp-1", "loan_id": "app-1", "contacts": [
        {"ref": "rp-1-i1", "on": "2026-09-28", "at": NOW.isoformat(), "script_id": "payment_reminder",
         "status": "sent", "reason": None, "by": "reminder"},
        {"ref": "rp-1-s1", "on": "2026-09-10", "at": NOW.isoformat(), "script_id": "balance_warning",
         "status": "sent", "reason": None, "by": "early_warning", "text": "잔고 부족 예상 안내 문구입니다."},
        {"ref": "rp-1-i3", "on": "2026-11-25", "at": NOW.isoformat(), "script_id": "early_reminder",
         "status": "deferred", "reason": "weekly_limit", "by": "early_reminder", "text": "5일 전 추가 안내 문구입니다."},
        {"ref": "rc-rp-1-i1", "on": "2026-10-01", "at": NOW.isoformat(), "script_id": "missed_payment",
         "status": "sent", "reason": None, "by": "officer", "text": "심사역이 승인한 안내 문구입니다.",
         "officer_id": "oid-officer"},
    ]}}
    client, _ = plan_env(plan_loan(offer_status="open", change_status=None), contacts)
    page = client.get(BORROWER, headers=OFFICER).text
    start = page.index('id="notices"')
    section = page[start:page.index("</section>", start)]
    for s in ("받은 안내", "2026-09-10", "잔고 부족 예상 안내 문구입니다.", "2026-11-25", "5일 전 추가 안내 문구입니다.",
              "보류", "2026-10-01", "심사역이 승인한 안내 문구입니다.", "발송"):
        assert s in section, s
    assert section.index("2026-09-10") < section.index("2026-10-01") < section.index("2026-11-25")
    assert "2026-09-28" not in section and "oid-officer" not in section


def test_borrower_page_without_loan_has_no_plan_section(plan_env):
    loan = plan_loan(offer_status="open", change_status=None) | {"applicant_id": "seller-knitroom-04"}
    client, _ = plan_env(loan)
    page = client.get(BORROWER, headers=OFFICER).text
    assert 'id="self-plan"' not in page and "c-1" in page
