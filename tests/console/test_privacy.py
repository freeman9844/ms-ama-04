"""P5b 콘솔: 차주 시뮬레이터(동의 철회·이의제기), 동의 데모 버튼, 삭제 증명, 이의제기 처리."""
import base64
import json
import re

import pytest
from fastapi.testclient import TestClient

from console.app import ConsoleDeps, create_app
from console.auth import AUDITOR, CREDIT_MANAGER, CREDIT_OFFICER

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
        "applicant_id": "demo-cafe-001", "scopes": ["account", "tax", "pg"], "purpose": "운전자금 대출 심사",
        "granted_at": "2026-11-01T09:00:00+09:00", "expires_at": "2027-01-30T09:00:00+09:00",
        "status": status, "notices": [], **extra,
    }


PROOF = {
    "id": "c-0", "consent_id": "c-0", "application_id": "app-2", "applicant_id": "demo-cafe-001",
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
        "applicant_id": "demo-cafe-001", "text": "<b>한도</b>가 너무 낮습니다", "submitted_at": "2026-11-02T11:00:00+09:00",
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
    other = make_doc(id="app-9", application_id="app-9", applicant_id="demo-thin-004", status="declined",
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
            consent("c-9", "app-9") | {"applicant_id": "demo-thin-004"},
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
    assert 'href="/borrower/demo-cafe-001"' in page and 'href="/borrower/demo-injection-005"' in page


def test_borrower_page_shows_own_consents_notices_and_grievances(env):
    client, _, _ = env
    page = client.get("/borrower/demo-cafe-001", headers=OFFICER).text
    assert "c-1" in page and "c-0" in page and "c-9" not in page
    assert "계좌 거래내역" in page and "운전자금 대출 심사" in page
    assert "유효" in page and "만료" in page
    assert "동의 만료 예정 안내" in page and "2027년 1월 30일에 만료됩니다." in page
    assert 'href="/consents/c-0/proof"' in page
    # 철회는 유효한 동의에만 있다.
    assert 'action="/borrower/demo-cafe-001/consents/c-1/revoke"' in page
    assert "/consents/c-0/revoke" not in page
    # 이의제기는 결정이 난 자기 신청에만 낼 수 있다.
    assert re.search(r'<option value="app-2"', page) and not re.search(r'<option value="app-1"', page)
    assert "원결정을 유지합니다." in page and "처리 대기" in page
    assert "&lt;b&gt;한도&lt;/b&gt;" in page


def test_auditor_sees_borrower_page_without_forms(env):
    client, _, _ = env
    page = client.get("/borrower/demo-cafe-001", headers=AUDITOR_H).text
    assert "c-1" in page and "/revoke" not in page and 'name="csrf"' not in page


def test_revoke_sends_consent_command_as_borrower(env):
    client, queue, _ = env
    path = "/borrower/demo-cafe-001"
    resp = post(client, OFFICER, f"{path}/consents/c-1/revoke", path)
    assert resp.status_code == 202
    assert queue.sent == [("consent-commands", {"consent_id": "c-1", "action": "revoke",
                                                "requested_by": "borrower:demo-cafe-001", "application_id": "app-1"})]


@pytest.mark.parametrize("consent_id, status", [("c-9", 404), ("c-x", 404), ("c-0", 409)])
def test_revoke_only_own_granted_consent(env, consent_id, status):
    client, queue, _ = env
    path = "/borrower/demo-cafe-001"
    assert post(client, OFFICER, f"{path}/consents/{consent_id}/revoke", path).status_code == status
    assert queue.sent == []


def test_borrower_posts_need_csrf_and_decider_role(env):
    client, queue, _ = env
    path = "/borrower/demo-cafe-001"
    token = csrf(client, OFFICER, path)
    assert client.post(f"{path}/consents/c-1/revoke", headers=OFFICER, data={"csrf": ""}).status_code == 403
    assert client.post(f"{path}/consents/c-1/revoke", headers=AUDITOR_H, data={"csrf": token}).status_code == 403
    assert client.post(f"{path}/grievances", headers=AUDITOR_H,
                       data={"csrf": token, "application_id": "app-2", "text": "x"}).status_code == 403
    assert queue.sent == []


def test_grievance_submission_is_enqueued(env):
    client, queue, _ = env
    path = "/borrower/demo-cafe-001"
    resp = post(client, OFFICER, f"{path}/grievances", path, application_id="app-2", text="  한도가 낮습니다  ")
    assert resp.status_code == 202
    assert queue.sent == [("grievances", {"grievance_id": "grv-new001", "application_id": "app-2",
                                          "applicant_id": "demo-cafe-001", "text": "한도가 낮습니다"})]


@pytest.mark.parametrize("fields, status", [
    ({"application_id": "app-2", "text": " "}, 422),
    ({"application_id": "app-2", "text": "가" * 1001}, 422),
    ({"application_id": "app-1", "text": "결정 전"}, 409),
    ({"application_id": "app-9", "text": "남의 신청"}, 404),
    ({"application_id": "nope", "text": "없음"}, 404),
])
def test_grievance_submission_validation(env, fields, status):
    client, queue, _ = env
    path = "/borrower/demo-cafe-001"
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
    ok = post(client, OFFICER, f"{path}/decision", path, outcome="rereview", new_decision="conditional",
              amount_krw="18,000,000", conditions="월별 매출 보고\r\n", reason="재심사")
    assert ok.status_code == 202
    assert queue.sent[-1][1] == {
        "grievance_id": "grv-abc123", "application_id": "app-2", "outcome": "rereview", "officer_id": "oid-officer",
        "reason": "재심사", "new_decision": "conditional", "amount_krw": 18_000_000, "conditions": ["월별 매출 보고"]}


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
        status="withdrawn", withdrawal={"consent_id": "c-1", "requested_by": "borrower:demo-cafe-001",
                                        "at": "2026-11-02T09:30:00+09:00"})
    page = client.get("/applications/app-1", headers=OFFICER).text
    assert "철회 종료" in page and 'http-equiv="refresh"' not in page
    assert "borrower:demo-cafe-001" in page


def test_privacy_pages_404_when_not_configured(make_doc, raw, key):
    deps = ConsoleDeps(store=FakeStore([make_doc()]), raw=raw, queue=FakeQueue(), pseudonym_key=key)
    client = TestClient(create_app(deps))
    for path in ("/borrower/demo-cafe-001", "/grievances", "/consents/c-1/proof"):
        assert client.get(path, headers=OFFICER).status_code == 404
    assert client.get("/applications/app-1", headers=OFFICER).status_code == 200
