"""P5b 콘솔: 고객 콘솔 내 대출 관리(동의 철회·이의제기), 동의 데모 버튼, 삭제 증명, 이의제기 처리."""
import base64
import json
import re
from datetime import datetime

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

    def list_recent(self, limit, *, legacy: bool = False):
        return list(self.docs.values())[:limit]

    def list_status_rows(self, *, legacy: bool = False):
        return list(self.docs.values())

    def for_applicant(self, applicant_id, *, legacy=False):
        from shared.portfolio import in_portfolio
        return [d for d in self.docs.values() if d["applicant_id"] == applicant_id and in_portfolio(d, legacy)]

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

    def consents_for_applicant(self, applicant_id, *, legacy: bool = False):
        return [c for c in self.consents.values() if c["applicant_id"] == applicant_id]

    def consents_for_application(self, application_id):
        return [c for c in self.consents.values() if c["application_id"] == application_id]

    def get_consent(self, consent_id):
        return self.consents.get(consent_id)

    def get_proof(self, consent_id):
        return self.proofs.get(consent_id)

    def list_grievances(self, limit, *, legacy: bool = False):
        return list(self.grievances.values())[:limit]

    def grievances_for_applicant(self, applicant_id, *, legacy: bool = False):
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
    return TestClient(create_app(deps), follow_redirects=False, cookies={"ama_locale_v2": "ko"}), queue, privacy


def csrf(client, headers, path):
    page = client.get(path, headers=headers).text
    return re.search(r'name="csrf" value="([0-9a-f]{64})"', page).group(1)


def post(client, headers, path, token_path, **form):
    return client.post(path, headers=headers, data={"csrf": csrf(client, headers, token_path)} | form)


# ── 고객 콘솔(내 대출 관리) ──

CUSTOMER = principal(oid="oid-seller", name="김셀러")
HOME = "/legacy/borrower/seller-dailylook-01"


def test_officer_sidebar_links_customer_console_and_grievances(env):
    client, _, _ = env
    page = client.get("/", headers=OFFICER).text
    assert 'class="customer-link" href="/borrower"' in page and 'href="/grievances"' in page
    assert '<li class="n-borrower' not in page


def test_borrower_index_lists_demo_applicants(env):
    client, _, _ = env
    page = client.get("/borrower", headers=CUSTOMER).text
    for seller in ("seller-dailylook-01", "seller-candle-05"):
        url = f"/apply?applicant_id={seller}"
        assert f'href="{url}"' in page
        form = client.get(url, headers=CUSTOMER)
        assert form.status_code == 200 and f'name="applicant_id" value="{seller}"' in form.text
    assert '<li class="n-status active"><a href="/borrower">사업자 선택</a>' in page
    english = client.get("/borrower", headers={**CUSTOMER, "Cookie": "ama_locale_v2=en"}).text
    assert "<h1>Select business</h1>" in english and "Customer choice" not in english
    assert 'class="n-apply' not in page


def test_customer_menu_links_every_page_of_the_chosen_seller(env):
    client, _, _ = env
    page = client.get(HOME, headers=CUSTOMER).text
    for sub in ("", "/plans", "/notices", "/consents", "/grievances"):
        assert f'href="{HOME}{sub}"' in page
    assert 'href="/apply?applicant_id=seller-dailylook-01"' in page and 'href="/legacy/borrower">' in page
    assert '<li class="n-status active">' in page


@pytest.mark.parametrize("path", ["/borrower", HOME, f"{HOME}/plans", f"{HOME}/consents", f"{HOME}/consents/c-0/proof"])
def test_customer_pages_need_login_only(env, path):
    client, _, _ = env
    assert client.get(path).status_code == 401
    assert client.get(path, headers=CUSTOMER).status_code == 200


def test_unknown_customer_menu_is_404(env):
    client, _, _ = env
    assert client.get(f"{HOME}/status", headers=CUSTOMER).status_code == 404
    assert client.get(f"{HOME}/nope", headers=CUSTOMER).status_code == 404


def test_status_page_lists_own_applications_with_customer_status(env):
    client, _, _ = env
    page = client.get(HOME, headers=CUSTOMER).text
    assert "app-1" in page and "app-2" in page and "app-9" not in page
    assert "승인" in page and "15,000,000원" in page and "심사" in page


def test_status_page_shows_latest_application_as_hero(env):
    client, _, _ = env
    page = client.get(HOME, headers=CUSTOMER).text
    hero = page.split('class="card hero-card"', 1)[1].split("</section>", 1)[0]
    first_row = re.search(r'<tbody>.*?<tr><td>(?:<a [^>]*>)?([\w-]+)', page, re.S).group(1)
    assert first_row in hero and 'aria-current="step"' in hero


def test_status_page_without_applications_has_no_hero(env):
    client, _, _ = env
    page = client.get("/borrower/seller-unknown-99", headers=CUSTOMER).text
    assert "hero-card" not in page and "신청 기록이 없습니다." in page


def test_borrower_index_shows_seller_cards_with_initial_avatar(env):
    client, _, _ = env
    page = client.get("/borrower", headers=CUSTOMER).text
    assert '<ul class="seller-grid">' in page and 'class="seller-card" href="/apply?applicant_id=seller-dailylook-01"' in page
    assert re.search(r'<span class="avatar av-\d" aria-hidden="true">성</span>', page)


def test_consents_page_shows_own_consents_notices_proof_and_revoke(env):
    client, _, _ = env
    page = client.get(f"{HOME}/consents", headers=CUSTOMER).text
    assert '<li class="n-consents active">' in page
    assert "c-1" in page and "c-0" in page and "c-9" not in page
    assert "계좌 거래내역" in page and "운전자금 대출 심사" in page
    assert "유효" in page and "만료" in page
    assert "동의 만료 예정 안내" in page and "2027년 1월 30일에 만료됩니다." in page
    assert f'href="{HOME}/consents/c-0/proof"' in page
    # 철회는 유효한 동의에만 있다.
    assert f'action="{HOME}/consents/c-1/revoke"' in page
    assert "/consents/c-0/revoke" not in page


def test_grievances_page_offers_only_own_decided_applications(env):
    client, _, _ = env
    page = client.get(f"{HOME}/grievances", headers=CUSTOMER).text
    assert '<li class="n-grievances active">' in page
    assert re.search(r'<option value="app-2"', page) and not re.search(r'<option value="app-1"', page)
    assert "원결정을 유지합니다." in page and "처리 대기" in page
    assert "&lt;b&gt;한도&lt;/b&gt;" in page


def test_customer_proof_page_uses_customer_layout_and_checks_owner(env):
    client, _, _ = env
    page = client.get(f"{HOME}/consents/c-0/proof", headers=CUSTOMER).text
    assert "transactions.json" in page and "customer-shell" in page
    assert f'href="{HOME}/consents"' in page and 'href="/applications/app-2"' not in page
    other = "/borrower/seller-knitroom-04/consents/c-0/proof"
    assert client.get(other, headers=CUSTOMER).status_code == 404
    assert client.get(f"{HOME}/consents/c-1/proof", headers=CUSTOMER).status_code == 404


def test_revoke_sends_consent_command_as_borrower(env):
    client, queue, _ = env
    resp = post(client, CUSTOMER, f"{HOME}/consents/c-1/revoke", f"{HOME}/consents")
    assert resp.status_code == 202
    assert f'href="{HOME}/consents"' in resp.text and "customer-shell" in resp.text
    assert queue.sent == [("consent-commands", {"consent_id": "c-1", "action": "revoke",
                                                "requested_by": "borrower:seller-dailylook-01", "application_id": "app-1"})]


@pytest.mark.parametrize("consent_id, status", [("c-9", 404), ("c-x", 404), ("c-0", 409)])
def test_revoke_only_own_granted_consent(env, consent_id, status):
    client, queue, _ = env
    path = f"{HOME}/consents"
    assert post(client, CUSTOMER, f"{HOME}/consents/{consent_id}/revoke", path).status_code == status
    assert queue.sent == []


def test_borrower_posts_need_login_and_csrf(env):
    client, queue, _ = env
    token = csrf(client, CUSTOMER, f"{HOME}/consents")
    assert client.post(f"{HOME}/consents/c-1/revoke", headers=CUSTOMER, data={"csrf": ""}).status_code == 403
    assert client.post(f"{HOME}/consents/c-1/revoke", data={"csrf": token}).status_code == 401
    assert client.post(f"{HOME}/grievances", data={"csrf": token, "application_id": "app-2", "text": "x"}).status_code == 401
    assert queue.sent == []


def test_grievance_submission_is_enqueued(env):
    client, queue, _ = env
    path = f"{HOME}/grievances"
    resp = post(client, CUSTOMER, path, path, application_id="app-2", text="  한도가 낮습니다  ")
    assert resp.status_code == 202
    assert f'href="{path}"' in resp.text
    assert queue.sent == [("grievances", {"grievance_id": "grv-new001", "application_id": "app-2",
                                          "applicant_id": "seller-dailylook-01", "text": "한도가 낮습니다"})]


def test_usd_grievance_forms_deliver_both_commands_to_workers(raw, key):
    from journey.activities import Deps
    from journey.grievance import handle_grievance, handle_grievance_decision
    from shared.contracts import USD_V1
    from tests.orchestrator.test_grievance import MemoryStore
    meta = USD_V1.model_dump()
    applicant, gid = "seller-dailylook-01", "grv-new001"
    app = dict(meta, id="app-2", application_id="app-2", applicant_id=applicant,
               status="approved", requested_amount_cents=90000,
               policy=dict(meta, decision="eligible", max_amount_cents=80000, rate_band="A", reason_codes=[]),
               officer_decision=dict(decision="approve", amount_cents=80000, officer_id="old", reason="original"))
    queue, privacy, store = FakeQueue(), FakePrivacy([consent("c-2", "app-2")], [], []), MemoryStore()
    store.upsert("applications", app)
    worker = Deps(store=store, raw=raw, mock=None, policy=None, events=None, clock=lambda: NOW, pseudonym_key=key)
    client = TestClient(create_app(ConsoleDeps(store=FakeStore([app]), raw=raw, queue=queue, pseudonym_key=key,
        privacy=privacy, new_grievance_id=lambda: gid)))
    path = f"/borrower/{applicant}/grievances"
    assert post(client, CUSTOMER, path, path, application_id="app-2", text="Please review.", currency="KRW").status_code == 202
    topic, submission = queue.sent.pop()
    assert topic == "grievances" and handle_grievance(worker, json.dumps(submission)) == "created"
    privacy.grievances[("app-2", gid)] = store.read("grievances", gid, "app-2")
    path = f"/grievances/app-2/{gid}"
    page = client.get(path, headers=OFFICER).text
    assert 'name="amount_usd"' in page and "$800" in page and 'name="amount_krw"' not in page
    assert "Seongsu Daily Look" in page and "Seongsu Daily Look" in client.get("/grievances", headers=OFFICER).text
    fields = dict(outcome="rereview", new_decision="approve", reason="Recorded review.")
    for invalid in ("700.011", "800.01"):
        assert post(client, OFFICER, path+"/decision", path, **fields, amount_usd=invalid).status_code == 422
        assert not queue.sent
    assert post(client, OFFICER, path+"/decision", path, **fields, amount_usd="700.01").status_code == 202
    topic, decision = queue.sent.pop()
    assert topic == "grievance-decisions" and decision["amount_cents"] == 70001 and "amount_krw" not in decision
    assert handle_grievance_decision(worker, json.dumps(decision)) == "resolved"
    assert "$700.01" in store.read("grievances", gid, "app-2")["reply"]["body"]


@pytest.mark.parametrize("fields, status", [
    ({"application_id": "app-2", "text": " "}, 422),
    ({"application_id": "app-2", "text": "가" * 1001}, 422),
    ({"application_id": "app-1", "text": "결정 전"}, 409),
    ({"application_id": "app-9", "text": "남의 신청"}, 404),
    ({"application_id": "nope", "text": "없음"}, 404),
])
def test_grievance_submission_validation(env, fields, status):
    client, queue, _ = env
    path = f"{HOME}/grievances"
    resp = post(client, CUSTOMER, path, path, **fields)
    assert resp.status_code == status
    if status == 422:
        assert '<li class="n-grievances active">' in resp.text
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


def test_proof_explains_pending_inventory_even_when_known_items_are_verified(env):
    client, _, privacy = env
    privacy.proofs["c-0"] = dict(PROOF, complete=False, foundry_inventory_pending=True)
    client.cookies.set("ama_locale_v2", "en")
    page = client.get("/consents/c-0/proof", headers=AUDITOR_H)
    assert page.status_code == 200 and "Foundry target inventory is not yet confirmed" in page.text


def test_missing_proof_is_404(env):
    client, _, _ = env
    assert client.get("/consents/c-1/proof", headers=OFFICER).status_code == 404


# ── 이의제기 처리 ──

def test_grievance_list_and_detail(env):
    client, _, _ = env
    page = client.get("/legacy/grievances", headers=AUDITOR_H).text
    assert 'href="/grievances/app-2/grv-abc123"' in page and "처리 대기" in page and "원결정 유지" in page
    detail = client.get("/grievances/app-2/grv-abc123", headers=OFFICER).text
    assert "&lt;b&gt;한도&lt;/b&gt;" in detail and "R101" in detail and "15,000,000원" in detail
    assert 'action="/grievances/app-2/grv-abc123/decision"' in detail
    assert "/decision" not in client.get("/grievances/app-2/grv-abc123", headers=AUDITOR_H).text
    done = client.get("/grievances/app-2/grv-done01", headers=OFFICER).text
    assert "원결정을 유지합니다." in done and "/decision" not in done
    assert client.get("/grievances/app-2/grv-nope00", headers=OFFICER).status_code == 404


def _english(client, path):
    return client.get(path, headers={**OFFICER, "Cookie": "ama_locale_v2=en"})


def _pending(page):
    import html

    return [html.unescape(t) for t in re.findall(r'data-mt="([^"]*)"', page.text)]


def _translating(env):
    """번역 에이전트와 저장 번역 읽기를 단 앱. 원천 오류를 바꿔 끼울 수 있게 deps를 돌려준다."""
    from console.app import create_app
    from consolepreview.fixtures import InMemoryTranslations
    from tests.console.test_translate import FakeTranslator

    client, _, _ = env
    deps = client.app.state.deps
    deps.translator, deps.translations = FakeTranslator(), InMemoryTranslations()
    return TestClient(create_app(deps), follow_redirects=False, cookies={"ama_locale_v2": "ko"}), deps


class BrokenRaw:
    def get_json(self, container, name):
        raise RuntimeError("blob down")


def test_english_grievance_page_reads_stored_translations_of_its_application(env):
    client, deps = _translating(env)
    deps.privacy.grievances[("app-2", "grv-abc123")]["record"]["officer_decision"]["reason"] = "거절"
    assert client.get("/grievances/app-2/grv-abc123", headers=OFFICER).status_code == 200
    assert deps.translations.calls == []
    en = _english(client, "/grievances/app-2/grv-abc123")
    assert en.status_code == 200 and deps.translations.calls == ["app-2"]
    assert "<b>한도</b>가 너무 낮습니다" in _pending(en)
    assert '거절<span class="mt-ref">' in en.text and 'href="/legacy/grievances"' in en.text


def test_english_grievance_page_translates_counterfactual_explanation(env):
    client, deps = _translating(env)
    deps.privacy.grievances[("app-2", "grv-abc123")]["record"]["assessment"]["counterfactuals"] = [
        {"metric_id": "M:dscr", "current": 1.1, "target": 1.25, "explanation": "DSCR가 기준을 넘으면 승인 가능"}]
    assert "DSCR가 기준을 넘으면 승인 가능" not in _pending(_english(client, "/grievances/app-2/grv-abc123"))
    deps.privacy.grievances[("app-2", "grv-abc123")]["record"]["assessment"].update(
        schema_version="2", review={"passed": True}, recommendation="refer")
    assert "DSCR가 기준을 넘으면 승인 가능" in _pending(_english(client, "/grievances/app-2/grv-abc123"))


def test_archived_counteroffer_reply_is_not_replayed_or_translated(env):
    from agents.pretranslate import extract

    client, _, privacy = env
    g = privacy.grievances[("app-2", "grv-done01")]
    g["record"]["assessment"]["counterfactuals"] = [{"explanation": "311만 원 제안"}]
    g["reply"]["body"] = "311만 원 감액은 권고하지 않습니다."
    for path in ("/grievances/app-2/grv-done01", "/legacy/borrower/seller-dailylook-01/grievances"):
        for lang in ("ko", "en"):
            response = client.get(f"{path}?lang={lang}", headers=OFFICER)
            assert response.status_code == 200 and "311만 원" not in response.text
    assert g["reply"]["body"] not in extract("grievances", g)
    assert g["reply"]["body"] == "311만 원 감액은 권고하지 않습니다."


def test_english_grievance_page_with_unreadable_source_sends_nothing(env, caplog):
    client, deps = _translating(env)
    deps.raw = BrokenRaw()
    with caplog.at_level("WARNING", logger="console.translate"):
        en = _english(client, "/grievances/app-2/grv-abc123")
    assert en.status_code == 200 and "data-mt=" not in en.text and "한도" in en.text
    assert "app-2" in caplog.text and "RuntimeError" in caplog.text and "blob down" not in caplog.text


def test_english_grievance_page_with_failing_application_read_sends_nothing(env):
    client, deps = _translating(env)

    class BrokenStore:
        def get(self, application_id):
            raise RuntimeError("cosmos down")

    deps.store = BrokenStore()
    en = _english(client, "/grievances/app-2/grv-abc123")
    assert en.status_code == 200 and "data-mt=" not in en.text


def _borrower_env(env, raw):
    from tests.console.conftest import PROFILE

    client, deps = _translating(env)
    other = PROFILE.model_copy(update={"business_name": "다른상점", "representative_name": "박다른",
                                       "business_reg_no": "987-65-43210"})
    raw.blobs[("consent-c-2", "profile.json")] = other.model_dump(mode="json")
    raw.blobs[("consent-c-2", "transactions.json")] = []
    deps.store.docs["app-2"]["consent"]["consent_id"] = "c-2"
    deps.privacy.consents["c-2"] = consent("c-2", "app-2")
    grievance = deps.privacy.grievances[("app-2", "grv-abc123")]
    grievance["text"] = "다른상점 박다른 그리고 김대표 문의"
    return client, deps


def test_english_borrower_page_merges_entities_and_stored_translations_of_each_application(env, raw):
    from agents.pretranslate import translation_id
    from console.app import create_app

    client, deps = _borrower_env(env, raw)
    path = "/legacy/borrower/seller-dailylook-01/grievances"
    first = _english(client, path)
    assert first.status_code == 200 and deps.translations.calls == ["app-1", "app-2"]
    pending = [p for p in _pending(first) if "[[" in p and "문의" in p]
    assert len(pending) == 1 and len(re.findall(r"\[\[", pending[0])) == 3
    assert all(n not in pending[0] for n in ("다른상점", "박다른", "김대표"))
    deps.translations.items["app-2"] = {translation_id(p): p.replace("문의", "inquiry") for p in pending}
    again = _english(TestClient(create_app(deps), cookies={"ama_locale_v2": "ko"}), path)
    assert "다른상점" in again.text and "inquiry" in again.text and not [p for p in _pending(again) if "문의" in p]


def test_english_customer_page_with_pending_text_loads_mt_script_with_csrf(env, raw):
    client, _ = _borrower_env(env, raw)
    en = _english(client, "/legacy/borrower/seller-dailylook-01/grievances")
    assert _pending(en) and "/static/mt.js" in en.text and 'data-mt-csrf="' in en.text


def test_english_borrower_page_with_one_unreadable_source_sends_nothing(env, raw):
    client, deps = _borrower_env(env, raw)
    real = deps.raw

    class OneBroken:
        def get_json(self, container, name):
            if container == "consent-c-2":
                raise RuntimeError("blob down")
            return real.get_json(container, name)

    deps.raw = OneBroken()
    en = _english(client, "/legacy/borrower/seller-dailylook-01/grievances")
    assert en.status_code == 200 and "data-mt=" not in en.text and "문의" in en.text
    deps.raw = real
    assert _pending(_english(client, "/legacy/borrower/seller-dailylook-01/grievances"))


def test_deleted_mixed_page_uses_only_the_grievances_own_stored_match(env, raw):
    from agents.pretranslate import translation_id

    client, deps = _borrower_env(env, raw)
    deps.raw.blobs.pop(("consent-c-2", "profile.json"))
    g = deps.privacy.grievances[("app-2", "grv-abc123")]
    g["text"] = "다시 확인해 주세요"
    key = translation_id(g["text"])
    deps.translations.items["app-1"] = {key: "Other owner's translation"}
    deps.translations.items["app-2"] = {key: "Please check again"}
    en = _english(client, "/legacy/borrower/seller-dailylook-01/grievances")
    assert "Please check again" in en.text and "Other owner's translation" not in en.text
    assert not _pending(en)


def test_uphold_decision_is_sent_with_principal(env):
    client, queue, _ = env
    path = "/grievances/app-2/grv-abc123"
    resp = post(client, MANAGER, f"{path}/decision", path, outcome="uphold", reason=" 정책 한도 ",
                officer_id="spoofed", new_decision="approve", amount_krw="1")
    assert resp.status_code == 202
    assert queue.sent == [("grievance-decisions", {
        "grievance_id": "grv-abc123", "application_id": "app-2", "outcome": "uphold", "officer_id": "oid-manager",
        "reason": "정책 한도", "new_decision": None, "amount_krw": None, "conditions": []})]


def test_usd_grievance_shows_snapshot_units_and_limit_beside_input(env):
    from shared.contracts import USD_V1
    client, _, privacy = env
    g = privacy.grievances[("app-2", "grv-abc123")]
    g.update(USD_V1.model_dump())
    g["record"] = {"policy": dict(USD_V1.model_dump(), decision="ineligible", max_amount_cents=0, reason_codes=[
        {"code": "R900", "field": "max_amount_cents", "observed": 0.0, "threshold": 10000.0},
        {"code": "R108", "field": "pg_reconciliation_ratio", "observed": 0.68, "threshold": 0.9}]),
        "assessment": {"schema_version": "2", "recommendation": "decline", "review": {"passed": True},
                       "counterfactuals": [{"metric_id": "M:avg_monthly_inflow_cents", "current": 4500,
                                            "target": 10000, "explanation": "More settled receipts."},
                                           {"metric_id": "P:R900:observed", "current": 0.0, "target": 10000.0,
                                            "explanation": "The limit is below the product minimum."}]}}
    html = client.get("/grievances/app-2/grv-abc123?lang=en", headers=OFFICER).text
    assert re.search(r"<label>[^<]*\$0[^<]*<input name=\"amount_usd\"", html)
    assert "<td>$0</td><td>$100</td>" in html and "<td>0.68</td><td>0.9</td>" in html
    assert "M:avg_monthly_inflow_cents: $45 → $100" in html and "P:R900:observed: $0 → $100" in html


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


def test_rereview_approval_outside_policy_is_rejected_with_specific_error(env):
    client, queue, privacy = env
    privacy.grievances[("app-2", "grv-abc123")]["record"]["policy"]["decision"] = "ineligible"
    path = "/grievances/app-2/grv-abc123"
    r = post(client, OFFICER, f"{path}/decision", path, outcome="rereview", new_decision="approve",
             amount_krw="5,000,000", reason="재심사")
    assert r.status_code == 422 and "rereview_outside_policy" in r.text and "이의 재심사로 승인할 수 없습니다" in r.text
    assert queue.sent == []

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
    client = TestClient(create_app(deps), cookies={"ama_locale_v2": "ko"})
    for path in ("/borrower/seller-dailylook-01", "/grievances", "/consents/c-1/proof"):
        assert client.get(path, headers=OFFICER).status_code == 404
    assert client.get("/applications/app-1", headers=OFFICER).status_code == 200

# ── 셀프 상환 플랜(2026-11-11) ──

PLAN_PATH = "/legacy/borrower/seller-dailylook-01/plan"
BORROWER = "/legacy/borrower/seller-dailylook-01"
PLANS = f"{BORROWER}/plans"


@pytest.fixture
def plan_env(make_doc, raw, key):
    def build(loan, contacts=None):
        queue = FakeQueue()
        deps = ConsoleDeps(store=FakeStore([make_doc()]), raw=raw, queue=queue, pseudonym_key=key,
                           privacy=FakePrivacy([consent("c-1", "app-1")], [], []),
                           monitoring=FakeMonitoring([loan], [], contacts),
                           clock=lambda: datetime.fromisoformat("2026-09-30T15:00:00+00:00"))
        return TestClient(create_app(deps), follow_redirects=False, cookies={"ama_locale_v2": "ko"}), queue

    return build


def self_plan(page):
    start = page.index('id="self-plan"')
    return page[start:page.index("</section>", start)]


def choose(client, **form):
    return post(client, CUSTOMER, PLAN_PATH, f"{BORROWER}/consents", **({"loan_id": "app-1", "offer_id": "of-rp-1"} | form))


def test_borrower_sees_open_offer_with_option_schedules(plan_env):
    client, _ = plan_env(plan_loan(offer_status="open", change_status=None, holds=("customer_response",),
                                   status="awaiting_customer"))
    section = self_plan(client.get(PLANS, headers=CUSTOMER).text)
    assert "상환 플랜 제안" in section
    for s in ("상환일 이동", "분할납부", "지금 일정 유지", "3회차", "2026-11-30 → 2026-12-04", "1,779,000원",
              "3-1회차", "3-2회차", "889,500원"):
        assert s in section, s
    assert f'action="{PLAN_PATH}"' in section
    assert 'name="loan_id" value="app-1"' in section and 'name="offer_id" value="of-rp-1"' in section
    for option in ("move", "split", "keep"):
        assert f'name="option" value="{option}"' in section


def test_borrower_choice_queues_plan_choice(plan_env):
    client, queue = plan_env(plan_loan(offer_status="open", change_status=None, holds=("customer_response",),
                                       status="awaiting_customer"))
    resp = choose(client, option="move")
    assert resp.status_code == 202, resp.text
    assert f'href="{PLANS}"' in resp.text and "상환 플랜 메뉴" in resp.text
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
    # 다른 차주의 대출·없는 대출은 404, 로그인이 없으면 401.
    other = plan_loan(offer_status="open", change_status=None) | {"applicant_id": "seller-knitroom-04"}
    client2, queue2 = plan_env(other)
    assert choose(client2, option="keep").status_code == 404
    assert choose(client2, option="keep", loan_id="app-x").status_code == 404
    token = csrf(client, CUSTOMER, f"{BORROWER}/consents")
    assert client.post(PLAN_PATH, data={"csrf": token, "loan_id": "app-1", "offer_id": "of-rp-1",
                                         "option": "keep"}).status_code == 401
    assert queue2.sent == []


def test_expired_offer_is_shown_as_expired(plan_env):
    loan = plan_loan(offer_status="expired", change_status=None, holds=(), status="awaiting_resume")
    loan["repayment"]["offer"].update(choice=None, expired_at=NOW.isoformat(), expired_by="oid-officer")
    client, _ = plan_env(loan)
    section = self_plan(client.get(PLANS, headers=CUSTOMER).text)
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
    section = self_plan(client.get(PLANS, headers=CUSTOMER).text)
    assert text in section and f'action="{PLAN_PATH}"' not in section
    assert "oid-officer" not in section and "내부 메모" not in section
    if offer_status == "kept":
        assert "지금 일정대로 유지됩니다." in section


def test_borrower_sees_received_notices(plan_env):
    contacts = {("app-1", "rp-1"): {"id": "contacts-rp-1", "loan_id": "app-1", "contacts": [
        {"ref": "rp-1-i1", "on": "2026-09-28", "at": NOW.isoformat(), "script_id": "payment_reminder",
         "status": "sent", "reason": None, "by": "reminder"},
        {"ref": "rp-1-s1", "on": "2026-09-10", "at": NOW.isoformat(), "script_id": "balance_warning",
         "status": "sent", "reason": None, "by": "early_warning", "text": "잔고 부족 예상 안내 문구입니다."},
        {"on": "2026-10-02", "status": "sent", "by": "reminder", "script_id": "payment_reminder"},
        {"on": "2026-09-29", "status": "skipped", "by": "reminder", "script_id": "payment_reminder"},
        {"ref": "rp-1-i3", "on": "2026-09-30", "at": NOW.isoformat(), "script_id": "early_reminder",
         "status": "deferred", "reason": "weekly_limit", "by": "early_reminder", "text": "5일 전 추가 안내 문구입니다."},
        {"ref": "rc-rp-1-i1", "on": "2026-10-01", "at": NOW.isoformat(), "script_id": "missed_payment",
         "status": "sent", "reason": None, "by": "officer", "text": "심사역이 승인한 안내 문구입니다.",
         "officer_id": "oid-officer"},
    ]}}
    client, _ = plan_env(plan_loan(offer_status="open", change_status=None), contacts)
    page = client.get(f"{BORROWER}/notices", headers=CUSTOMER).text
    start = page.index('id="notices"')
    section = page[start:page.index("</section>", start)]
    assert '<li class="n-notices active">' in page
    for s in ("2026-09-10", "잔고 부족 예상 안내 문구입니다.", "2026-10-01", "심사역이 승인한 안내 문구입니다.", "발송"):
        assert s in section, s
    for hidden in ("2026-10-02", "2026-09-29", "2026-09-30"):
        assert hidden not in section
    # 3일 전 기본 안내는 문구가 기록되지 않아 스크립트 라벨로 보인다.
    assert "2026-09-28" in section and "상환일 안내" in section
    assert section.index("2026-09-10") < section.index("2026-09-28") < section.index("2026-10-01")
    assert "oid-officer" not in section


def test_plans_page_without_own_loan_has_no_offer(plan_env):
    loan = plan_loan(offer_status="open", change_status=None) | {"applicant_id": "seller-knitroom-04"}
    client, _ = plan_env(loan)
    section = self_plan(client.get(PLANS, headers=CUSTOMER).text)
    assert "실행된 대출이 없습니다." in section and "<form" not in section
