import base64
import json
import re
from datetime import datetime

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
NO_ROLE = principal("Reader", oid="oid-reader")


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


@pytest.fixture
def env(make_doc, raw, key):
    docs = [make_doc(), make_doc(id="app-2", application_id="app-2", status="approved")]
    queue = FakeQueue()
    deps = ConsoleDeps(store=FakeStore(docs), raw=raw, queue=queue, pseudonym_key=key,
                       new_application_id=lambda: "app-20261101-abc123")
    return TestClient(create_app(deps), follow_redirects=False), queue


def csrf(client, headers, path="/applications/app-1"):
    page = client.get(path, headers=headers).text
    return re.search(r'name="csrf" value="([0-9a-f]{64})"', page).group(1)


def decide(client, headers, token, path="/applications/app-1/decision", **fields):
    form = {"csrf": token, "decision": "approve", "amount_krw": "15000000", "conditions": "", "reason": ""} | fields
    return client.post(path, data=form, headers=headers)


def test_healthz_needs_no_login(env):
    client, _ = env
    assert client.get("/healthz").json() == {"status": "ok"}


@pytest.mark.parametrize("path", ["/", "/applications/rows", "/applications/app-1", "/applications/new"])
def test_pages_require_principal_and_console_role(env, path):
    client, _ = env
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"X-MS-CLIENT-PRINCIPAL": "garbage"}).status_code == 401
    assert client.get(path, headers=NO_ROLE).status_code == 403


def test_list_shows_recent_applications_with_polling(env):
    client, _ = env
    page = client.get("/", headers=AUDITOR_H)
    assert page.status_code == 200
    assert "app-1" in page.text and "app-2" in page.text
    assert 'hx-get="/applications/rows"' in page.text
    assert "새 신청" not in page.text
    assert "새 신청" in client.get("/", headers=OFFICER).text
    rows = client.get("/applications/rows", headers=AUDITOR_H)
    assert "app-1" in rows.text and "<html" not in rows.text


def test_officer_sees_restored_names_and_decision_form(env):
    client, _ = env
    page = client.get("/applications/app-1", headers=OFFICER).text
    assert "빈스카페(대표 김대표)는 계절성이 있다" in page
    assert "김대표 개인이체" in page
    assert "[[PERSON_" not in page
    assert 'action="/applications/app-1/decision"' in page
    assert "M:dscr" in page and "1.8" in page


def test_auditor_sees_pseudonyms_and_no_decision_form(env):
    client, _ = env
    page = client.get("/applications/app-1", headers=AUDITOR_H).text
    assert "김대표" not in page and "빈스카페" not in page
    assert "[[PERSON_" in page
    assert "/decision" not in page and 'name="csrf"' not in page
    assert 'data-default-tab="tab-summary"' in page


def test_decision_tab_opens_for_officer_and_on_error_rerender(env):
    client, _ = env
    assert 'data-default-tab="tab-decision"' in client.get("/applications/app-1", headers=OFFICER).text
    assert 'data-default-tab="tab-summary"' in client.get("/applications/app-2", headers=OFFICER).text
    resp = decide(client, OFFICER, csrf(client, OFFICER), amount_krw="25000000")
    assert resp.status_code == 422 and 'data-default-tab="tab-decision"' in resp.text


def test_model_text_is_html_escaped(env, make_doc):
    client, _ = env
    doc = make_doc()
    doc["assessment"]["summary"] = '<script>alert(1)</script><img src=x onerror=alert(2)>'
    client.app.state.deps.store.docs["app-1"] = doc
    page = client.get("/applications/app-1", headers=OFFICER).text
    assert "<script>alert(1)" not in page and "<img src=x" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_unknown_application_shows_pending_page(env):
    client, _ = env
    page = client.get("/applications/app-20261101-abc123", headers=OFFICER)
    assert page.status_code == 404
    assert 'http-equiv="refresh"' in page.text


def test_officer_decision_is_sent_with_principal_as_officer(env):
    client, queue = env
    resp = decide(client, OFFICER, csrf(client, OFFICER), officer_id="spoofed", amount_krw="15,000,000")
    assert resp.status_code == 202
    assert queue.sent == [(
        "officer-decisions",
        {"application_id": "app-1", "decision": {"decision": "approve", "officer_id": "oid-officer",
                                                  "amount_krw": 15_000_000, "conditions": [], "reason": "",
                                                  "policy_override": False}},
    )]


def test_conditional_decision_is_retired_and_decline_drops_amount(env):
    client, queue = env
    token = csrf(client, MANAGER)
    resp = decide(client, MANAGER, token, decision="conditional", conditions="월별 매출 보고")
    assert resp.status_code == 422 and "조건부 승인은 더 이상 쓰지 않습니다" in resp.text
    assert queue.sent == []
    assert decide(client, MANAGER, token, decision="decline", reason="상환 여력 부족").status_code == 202
    assert queue.sent[-1][1]["decision"]["officer_id"] == "oid-manager"
    assert queue.sent[-1][1]["decision"]["amount_krw"] is None


def test_auditor_cannot_decide_or_submit(env):
    client, queue = env
    token = csrf(client, OFFICER)
    assert decide(client, AUDITOR_H, token).status_code == 403
    assert client.get("/applications/new", headers=AUDITOR_H).status_code == 403
    assert queue.sent == []


@pytest.mark.parametrize("token", ["", "0" * 64, None])
def test_decision_requires_csrf_token_of_same_user(env, token):
    client, queue = env
    if token is None:
        token = csrf(client, MANAGER)
    assert decide(client, OFFICER, token).status_code == 403
    assert queue.sent == []


def test_decision_over_policy_limit_is_rejected_before_queue(env):
    client, queue = env
    resp = decide(client, OFFICER, csrf(client, OFFICER), amount_krw="25000000")
    assert resp.status_code == 422
    assert "amount_exceeds_policy" in resp.text or "한도" in resp.text
    assert queue.sent == []


@pytest.mark.parametrize("fields", [{"decision": "conditional"}, {"decision": "decline"}, {"amount_krw": "abc"},
                                    {"decision": "maybe"}])
def test_invalid_decisions_are_rejected(env, fields):
    client, queue = env
    assert decide(client, OFFICER, csrf(client, OFFICER), **fields).status_code == 422
    assert queue.sent == []


def test_decision_form_defaults_amount_to_requested(env):
    client, _ = env
    page = client.get("/applications/app-1", headers=OFFICER).text
    assert 'name="amount_krw" inputmode="numeric" value="30000000"' in page
    assert "data-policy-blocked" not in page
    assert 'value="conditional"' not in page and 'name="conditions"' not in page


@pytest.fixture
def blocked(env, make_doc):
    client, queue = env
    doc = make_doc()
    doc["policy"] = {**doc["policy"], "decision": "ineligible", "max_amount_krw": 0}
    client.app.state.deps.store.docs["app-1"] = doc
    return client, queue


def test_policy_blocked_form_carries_confirm_hook(blocked):
    client, _ = blocked
    page = client.get("/applications/app-1", headers=OFFICER).text
    assert "data-policy-blocked" in page and 'name="policy_override"' in page
    assert '<script src="/static/decision.js" defer></script>' in page
    assert client.get("/static/decision.js").status_code == 200


def test_policy_blocked_approval_needs_override_confirmation(blocked):
    client, queue = blocked
    token = csrf(client, OFFICER)
    resp = decide(client, OFFICER, token, amount_krw="5000000", reason="매출 소명")
    assert resp.status_code == 422 and "override_required" in resp.text
    assert queue.sent == []
    resp = decide(client, OFFICER, token, amount_krw="5000000", reason="매출 소명", policy_override="1")
    assert resp.status_code == 202 and "정책 예외 승인" in resp.text
    assert queue.sent[-1][1]["decision"]["policy_override"] is True


def test_override_decision_is_labelled_on_detail(env, make_doc):
    client, _ = env
    doc = make_doc(status="approved")
    doc["officer_decision"] = {"decision": "approve", "officer_id": "oid-officer", "amount_krw": 5_000_000,
                               "conditions": [], "reason": "매출 소명", "policy_override": True, "at": "x"}
    client.app.state.deps.store.docs["app-1"] = doc
    assert "정책 예외 승인" in client.get("/applications/app-1", headers=OFFICER).text


def test_past_conditional_decision_still_renders(env, make_doc):
    client, _ = env
    doc = make_doc(status="conditionally_approved")
    doc["officer_decision"] = {"decision": "conditional", "officer_id": "oid-officer", "amount_krw": 5_000_000,
                               "conditions": ["월별 매출 보고"], "reason": "", "policy_override": False, "at": "x"}
    client.app.state.deps.store.docs["app-1"] = doc
    page = client.get("/applications/app-1", headers=OFFICER).text
    assert "조건부 승인" in page and "월별 매출 보고" in page


def test_decision_only_while_awaiting_officer(env):
    client, queue = env
    token = csrf(client, OFFICER)
    assert decide(client, OFFICER, token, path="/applications/app-2/decision").status_code == 409
    assert decide(client, OFFICER, token, path="/applications/nope/decision").status_code == 404
    assert queue.sent == []


def test_form_must_be_urlencoded_and_small(env):
    client, queue = env
    token = csrf(client, OFFICER)
    resp = client.post("/applications/app-1/decision", content=json.dumps({"csrf": token}), headers=OFFICER
                       | {"content-type": "application/json"})
    assert resp.status_code == 415
    big = decide(client, OFFICER, token, reason="가" * 20_000)
    assert big.status_code == 413
    assert queue.sent == []


def test_new_application_is_enqueued_and_redirects(env):
    client, queue = env
    form_page = client.get("/applications/new", headers=OFFICER).text
    assert "seller-dailylook-01" in form_page and "seller-candle-05" in form_page
    token = csrf(client, OFFICER, "/applications/new")
    resp = client.post("/applications", headers=OFFICER, data={
        "csrf": token, "applicant_id": "seller-dailylook-01", "requested_amount_krw": "10,000,000",
        "requested_tenor_months": "6"})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/applications/app-20261101-abc123"
    (queue_name, sent), = queue.sent
    submitted_at = sent.pop("submitted_at")
    assert datetime.fromisoformat(submitted_at).tzinfo is not None
    assert (queue_name, sent) == ("applications-start", {
        "application_id": "app-20261101-abc123", "applicant_id": "seller-dailylook-01",
        "requested_amount_krw": 10_000_000, "requested_tenor_months": 6, "sla_hours": 24.0,
        "source": "console"})


@pytest.mark.parametrize("fields", [{"requested_amount_krw": "0"}, {"requested_amount_krw": "10000001"},
                                    {"requested_tenor_months": "2"}, {"requested_tenor_months": "7"},
                                    {"requested_tenor_months": "12"}, {"applicant_id": ""}, {"requested_amount_krw": "lots"}])
def test_new_application_validation(env, fields):
    client, queue = env
    token = csrf(client, OFFICER, "/applications/new")
    form = {"csrf": token, "applicant_id": "seller-dailylook-01",     "requested_amount_krw": "10000000",
            "requested_tenor_months": "6"} | fields
    assert client.post("/applications", headers=OFFICER, data=form).status_code == 422
    assert client.post("/applications", headers=OFFICER, data=form | {"csrf": ""}).status_code == 403
    assert queue.sent == []


def test_security_headers_on_every_response(env):
    client, _ = env
    for resp in (client.get("/healthz"), client.get("/", headers=OFFICER), client.get("/")):
        csp = resp.headers["content-security-policy"]
        assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp
        assert "script-src 'self' https://unpkg.com" in csp
        assert "form-action" not in csp
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["referrer-policy"] == "no-referrer"
    assert client.get("/applications/app-1", headers=OFFICER).headers["cache-control"] == "no-store"


def test_htmx_is_pinned_with_sri(env):
    client, _ = env
    page = client.get("/", headers=OFFICER).text
    assert 'src="https://unpkg.com/htmx.org@2.0.11/dist/htmx.min.js"' in page
    assert 'integrity="sha384-2OatzQy1H+Zd/IIrjr1TcuDGqLXeHhbooAyJY1KdQMKnr4LZ22k31GBLdYKHmVjg"' in page
    assert 'href="/.auth/logout"' in page
    assert client.get("/static/console.css").status_code == 200


# ── 데모 모드(Easy Auth 끔): 운영자 IP만 허용하고 고정 데모 주체로 동작 ──

@pytest.fixture
def demo_env(make_doc, raw, key):
    from console.auth import Principal
    queue = FakeQueue()
    demo = Principal(user_id="demo-officer", name="데모 심사역", roles=frozenset({CREDIT_OFFICER}))
    deps = ConsoleDeps(store=FakeStore([make_doc()]), raw=raw, queue=queue, pseudonym_key=key,
                       new_application_id=lambda: "app-20261101-demo01", demo_principal=demo)
    return TestClient(create_app(deps), follow_redirects=False), queue


def test_demo_mode_acts_as_fixed_principal_without_easy_auth_header(demo_env):
    client, queue = demo_env
    page = client.get("/")
    assert page.status_code == 200
    assert "데모 모드" in page.text and "/.auth/logout" not in page.text
    assert "빈스카페(대표 김대표)는 계절성이 있다" in client.get("/applications/app-1").text
    token = csrf(client, {}, "/applications/new")
    resp = client.post("/applications", data={"csrf": token, "applicant_id": "seller-dailylook-01",
                                              "requested_amount_krw": "5,000,000", "requested_tenor_months": "6"})
    assert resp.status_code == 303
    assert queue.sent[0][1]["application_id"] == "app-20261101-demo01"


def test_demo_mode_ignores_forged_principal_header(demo_env):
    client, _ = demo_env
    page = client.get("/", headers=principal(CREDIT_MANAGER, oid="oid-forged", name="위조")).text
    assert "데모 심사역" in page and "위조" not in page


def test_easy_auth_mode_has_no_demo_banner(env):
    client, _ = env
    page = client.get("/", headers=OFFICER).text
    assert "데모 모드" not in page and "/.auth/logout" in page


def test_decision_sent_page_forwards_to_detail_until_the_decision_lands(env, make_doc):
    client, _ = env
    resp = decide(client, OFFICER, csrf(client, OFFICER))
    m = re.search(r'http-equiv="refresh" content="1;url=(/applications/app-1\?since=[0-9a-f]{12})"', resp.text)
    assert m, resp.text
    waiting = client.get(m.group(1), headers=OFFICER).text
    assert re.search(r'content="10;url=/applications/app-1\?since=[0-9a-f]{12}&amp;n=1"', waiting)
    assert "반영을 기다리는 중" in waiting and "10초마다" in waiting
    assert 'http-equiv="refresh"' in client.get(m.group(1) + "&n=11", headers=OFFICER).text
    assert 'http-equiv="refresh"' not in client.get(m.group(1) + "&n=12", headers=OFFICER).text
    client.app.state.deps.store.docs["app-1"] = make_doc(status="approved")
    landed = client.get(m.group(1), headers=OFFICER).text
    assert 'http-equiv="refresh"' not in landed and "반영을 기다리는 중" not in landed


def test_decision_form_is_locked_while_the_sent_decision_is_pending(env):
    client, _ = env
    fresh = client.get("/applications/app-1", headers=OFFICER).text
    assert '<button type="submit">결정 보내기</button>' in fresh and "data-decision-form" in fresh
    assert '<script src="/static/decision.js" defer></script>' in fresh
    resp = decide(client, OFFICER, csrf(client, OFFICER))
    url = re.search(r'content="1;url=(/applications/app-1\?since=[0-9a-f]{12})"', resp.text).group(1)
    waiting = client.get(url, headers=OFFICER).text
    assert '<button type="submit" disabled>결정 반영 대기 중</button>' in waiting
    assert '<textarea name="reason" rows="3" disabled>' in waiting and 'value="decline" disabled>' in waiting


def test_application_detail_pages_refresh_every_ten_seconds(env, make_doc):
    client, _ = env
    client.app.state.deps.store.docs["app-1"] = make_doc(status="received")
    assert '<meta http-equiv="refresh" content="10">' in client.get("/applications/app-1", headers=OFFICER).text
    pending = client.get("/applications/app-9", headers=OFFICER).text
    assert '<meta http-equiv="refresh" content="10">' in pending and "10초마다" in pending
    assert 'hx-trigger="every 3s"' in client.get("/", headers=OFFICER).text


def test_list_shows_business_name_to_deciders_and_id_to_auditors(env):
    client, _ = env
    rows = client.get("/applications/rows", headers=OFFICER).text
    assert "성수데일리룩" in rows and "seller-dailylook-01" in rows
    assert "성수데일리룩" not in client.get("/applications/rows", headers=AUDITOR_H).text
    assert "성수데일리룩" in client.get("/applications/app-1", headers=OFFICER).text


def test_detail_shows_gateway_block_badge(env, make_doc):
    client, _ = env
    assert "AI 게이트웨이" not in client.get("/applications/app-1", headers=OFFICER).text
    doc = make_doc()
    doc["steps"] = [*(doc.get("steps") or []), {
        "step": "assemble", "outcome": "degraded", "started_at": "2026-11-05T01:00:00+00:00",
        "ended_at": "2026-11-05T01:00:05+00:00", "duration_ms": 5000, "agent": True,
        "usage": [{"model": "gpt-5.4-mini", "input_tokens": 0, "output_tokens": 0, "calls": 1,
                   "gateway": "content_blocked"}],
    }]
    client.app.state.deps.store.docs["app-1"] = doc
    page = client.get("/applications/app-1", headers=OFFICER).text
    assert "<dt>AI 게이트웨이</dt>" in page and "콘텐츠 차단 · 데이터 수집" in page
