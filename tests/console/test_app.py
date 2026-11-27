import base64
import json
import re
import secrets
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from console.app import ConsoleDeps, create_app
from console.auth import AUDITOR, CREDIT_MANAGER, CREDIT_OFFICER
from journey.models import KST

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


@pytest.fixture
def env(make_doc, raw, key):
    docs = [make_doc(), make_doc(id="app-2", application_id="app-2", status="approved")]
    queue = FakeQueue()
    deps = ConsoleDeps(store=FakeStore(docs), raw=raw, queue=queue, pseudonym_key=key,
                       usd_origination_enabled=True, new_application_id=lambda: "app-20261101-abc123")
    return TestClient(create_app(deps), follow_redirects=False, cookies={"ama_locale_v2": "ko"}), queue


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


def test_home_lists_latest_20_but_counts_every_application(env, make_doc):
    client, _ = env
    store = client.app.state.deps.store
    for i in range(3, 33):
        store.docs[f"app-{i}"] = make_doc(id=f"app-{i}", application_id=f"app-{i}", status="approved")
    page = client.get("/", headers=AUDITOR_H).text
    assert page.count('<a href="/applications/app-') == 20
    assert "32건" in page and "승인 31 · 거절 0" in page
    assert client.get("/applications/rows", headers=AUDITOR_H).text.count('<a href="/applications/app-') == 20


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
    assert "data-tab-reset" in resp.text
    assert "data-tab-reset" not in client.get("/applications/app-1", headers=OFFICER).text


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


def test_post_handlers_read_the_store_off_the_event_loop(env):
    import asyncio

    client, queue = env
    store = client.app.state.deps.store

    class OffLoopStore:
        def get(self, application_id):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return store.get(application_id)
            raise AssertionError("blocking store read on the event loop")

    token = csrf(client, OFFICER)
    client.app.state.deps.store = OffLoopStore()
    assert decide(client, OFFICER, token).status_code == 202 and len(queue.sent) == 1


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
    assert "seller-dailylook-01" in form_page and "seller-candle-05" not in form_page
    token = csrf(client, OFFICER, "/applications/new")
    resp = client.post("/applications", headers=OFFICER, data={
        "csrf": token, "applicant_id": "seller-dailylook-01", "requested_amount_usd": "10,000",
        "requested_tenor_months": "6"})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/applications/app-20261101-abc123"
    (queue_name, sent), = queue.sent
    submitted_at = sent.pop("submitted_at")
    assert datetime.fromisoformat(submitted_at).tzinfo is not None
    assert (queue_name, sent) == ("applications-start", {
        "application_id": "app-20261101-abc123", "applicant_id": "seller-dailylook-01",
        "currency": "USD", "product_version": "usd-v1", "language": "en",
        "requested_amount_cents": 1_000_000, "requested_tenor_months": 6, "sla_hours": 24.0, "close_hours": 168.0,
        "source": "console"})


@pytest.mark.parametrize("fields", [{"requested_amount_usd": "0"}, {"requested_amount_usd": "10000001"},
                                    {"requested_tenor_months": "2"}, {"requested_tenor_months": "7"},
                                    {"requested_tenor_months": "12"}, {"applicant_id": ""}, {"requested_amount_usd": "lots"},
                                    {"requested_amount_usd": "99.99"}, {"applicant_id": "seller-unknown"}])
def test_new_application_validation(env, fields):
    client, queue = env
    token = csrf(client, OFFICER, "/applications/new")
    form = {"csrf": token, "applicant_id": "seller-dailylook-01",     "requested_amount_usd": "10000",
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
                       usd_origination_enabled=True, new_application_id=lambda: "app-20261101-demo01", demo_principal=demo)
    return TestClient(create_app(deps), follow_redirects=False, cookies={"ama_locale_v2": "ko"}), queue


def test_demo_mode_acts_as_fixed_principal_without_easy_auth_header(demo_env):
    client, queue = demo_env
    page = client.get("/")
    assert page.status_code == 200
    assert "데모 모드" not in page.text and "demo-banner" not in page.text
    assert "/.auth/logout" not in page.text
    assert "빈스카페(대표 김대표)는 계절성이 있다" in client.get("/applications/app-1").text
    token = csrf(client, {}, "/applications/new")
    resp = client.post("/applications", data={"csrf": token, "applicant_id": "seller-dailylook-01",
                                              "requested_amount_usd": "5,000", "requested_tenor_months": "6"})
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


# ── 데모 로그인 화면(CONSOLE_DEMO_LOGIN): 장면용 로그인. 인증이 아니며 주체는 그대로 데모 주체 ──

@pytest.fixture
def login_env(make_doc, raw, key):
    from console.auth import Principal
    now = {"t": datetime(2026, 11, 26, 9, 0, tzinfo=KST)}
    demo = Principal(user_id="demo-operator", name="데모 사용자", roles=frozenset({CREDIT_OFFICER}))
    deps = ConsoleDeps(store=FakeStore([make_doc()]), raw=raw, queue=FakeQueue(), pseudonym_key=key,
                       demo_principal=demo, demo_login=True, clock=lambda: now["t"])
    client = TestClient(create_app(deps), follow_redirects=False, base_url="https://testserver",
                        cookies={"ama_locale_v2": "ko"})
    return client, now


def login(client, **fields):
    return client.post("/login", data={"username": "demo.officer", "password": "x", "next": "/"} | fields)


def test_demo_login_redirects_pages_to_login_with_next(login_env):
    client, _ = login_env
    resp = client.get("/applications/app-1?since=abc")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login?next=/applications/app-1%3Fsince%3Dabc"


def test_demo_login_page_prefills_demo_account(login_env):
    client, _ = login_env
    page = client.get("/login?next=/loans")
    assert page.status_code == 200
    assert 'value="demo.officer"' in page.text and 'name="password"' in page.text
    assert 'name="next" value="/loans"' in page.text and "sidebar" not in page.text


def test_demo_login_sets_cookie_and_returns_to_next(login_env):
    client, _ = login_env
    resp = login(client, next="/loans")
    assert resp.status_code == 303 and resp.headers["location"] == "/loans"
    cookie = resp.headers["set-cookie"]
    assert cookie.startswith("ama_demo_session=")
    for attr in ("HttpOnly", "Secure", "SameSite=lax", "Path=/", "Max-Age=28800"):
        assert attr.lower() in cookie.lower()
    page = client.get("/")
    assert page.status_code == 200 and 'href="/logout"' in page.text and "/.auth/logout" not in page.text


@pytest.mark.parametrize("fields", [{"username": "someone"}, {"password": ""}])
def test_demo_login_rejects_wrong_input(login_env, fields):
    client, _ = login_env
    resp = login(client, **fields)
    assert resp.status_code == 422 and "아이디 또는 비밀번호가 올바르지 않습니다." in resp.text
    assert "set-cookie" not in resp.headers


def test_demo_login_ignores_external_next(login_env):
    client, _ = login_env
    assert login(client, next="//evil.example").headers["location"] == "/"


def test_demo_login_expires_after_eight_hours(login_env):
    client, now = login_env
    login(client)
    now["t"] += timedelta(hours=8)
    assert client.get("/").status_code == 303


def test_demo_login_rejects_tampered_cookie(login_env):
    client, _ = login_env
    login(client)
    issued, sig = client.cookies["ama_demo_session"].split(".")
    client.cookies.delete("ama_demo_session")
    client.cookies.set("ama_demo_session", f"{int(issued) + 1}.{sig}")
    assert client.get("/").status_code == 303


def test_demo_login_htmx_gets_hx_redirect(login_env):
    client, _ = login_env
    resp = client.get("/applications/rows", headers={"HX-Request": "true"})
    assert resp.status_code == 401 and resp.headers["hx-redirect"] == "/login"


def test_demo_login_post_without_session_is_401(login_env):
    client, _ = login_env
    assert client.post("/applications", data={"csrf": "x"}).status_code == 401


def test_demo_logout_clears_cookie(login_env):
    client, _ = login_env
    login(client)
    resp = client.get("/logout")
    assert resp.status_code == 303 and resp.headers["location"] == "/login"
    assert client.get("/").status_code == 303


def test_demo_login_leaves_customer_and_health_open(login_env):
    client, _ = login_env
    assert client.get("/healthz").status_code == 200
    assert client.get("/apply").headers["location"] == "/borrower"  # customer()를 통과해 사업자 선택으로


def test_demo_login_page_in_english(login_env):
    client, _ = login_env
    client.cookies.set("ama_locale_v2", "en")
    page = client.get("/login").text
    assert "Sign in" in page and "아이디" not in page


def test_demo_password_required_when_configured(make_doc, raw, key):
    # WAF IP 규칙을 끈 테스트 기간: 설정된 비밀번호와 같아야 들어오고, 화면에 미리 채우지 않습니다.
    from console.auth import Principal
    demo = Principal(user_id="demo-operator", name="데모 사용자", roles=frozenset({CREDIT_OFFICER}))
    pw = secrets.token_urlsafe(16)  # 코드에 비밀번호 문자열을 남기지 않습니다(비밀 탐지 오탐 방지).
    deps = ConsoleDeps(store=FakeStore([make_doc()]), raw=raw, queue=FakeQueue(), pseudonym_key=key,
                       demo_principal=demo, demo_login=True, demo_password=pw)
    client = TestClient(create_app(deps), follow_redirects=False, base_url="https://testserver",
                        cookies={"ama_locale_v2": "ko"})
    page = client.get("/login").text
    assert 'value="demo"' not in page and "데모 비밀번호는 운영자에게 받으세요." in page
    for wrong in ("x", "demo", pw[:-1], pw + "x", ""):
        resp = login(client, password=wrong)
        assert resp.status_code == 422 and "set-cookie" not in resp.headers
    assert client.get("/").status_code == 303
    for path in ("/apply", "/borrower"):  # 고객 화면도 비밀번호 없이 열리면 안 됩니다.
        assert client.get(path).headers["location"].startswith("/login?next=")
    assert client.post("/apply", data={}).status_code == 401
    resp = login(client, password=pw)
    assert resp.status_code == 303 and resp.headers["set-cookie"].startswith("ama_demo_session=")
    assert client.get("/").status_code == 200
    assert client.get("/apply").headers["location"] == "/borrower"


def test_login_routes_absent_when_demo_login_off(env, demo_env):
    for client, headers in ((env[0], OFFICER), (demo_env[0], {})):
        assert client.get("/login").status_code == 404
        assert client.get("/logout").status_code == 404
        assert 'href="/logout"' not in client.get("/", headers=headers).text


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


def test_second_decision_on_the_same_document_is_rejected(env, make_doc):
    client, queue = env
    assert decide(client, OFFICER, csrf(client, OFFICER)).status_code == 202
    second = decide(client, MANAGER, csrf(client, MANAGER), decision="decline", reason="상환 여력 부족")
    assert second.status_code == 409 and second.headers["content-type"].startswith("text/html")
    assert "이미 다른 결정이 접수되었습니다" in second.text
    assert '<button type="submit" disabled>결정 반영 대기 중</button>' in second.text
    assert '<button type="submit" disabled>결정 반영 대기 중</button>' in client.get(
        "/applications/app-1", headers=MANAGER).text
    assert len(queue.sent) == 1
    # 오케스트레이터가 첫 결정을 거부해 문서가 바뀌면 다시 결정할 수 있다.
    doc = make_doc()
    doc["decision_attempts"] = [{"at": "2026-11-01T09:00:00+09:00", "error": "amount_exceeds_policy"}]
    client.app.state.deps.store.docs["app-1"] = doc
    assert decide(client, MANAGER, csrf(client, MANAGER), decision="decline", reason="x").status_code == 202
    assert len(queue.sent) == 2


def test_decision_from_a_stale_page_is_rejected(env, make_doc):
    client, queue = env
    page = client.get("/applications/app-1", headers=OFFICER).text
    state = re.search(r'name="state" value="([0-9a-f]{12})"', page).group(1)
    token = csrf(client, OFFICER)
    doc = make_doc()
    doc["decision_attempts"] = [{"at": "2026-11-01T09:00:00+09:00", "error": "amount_exceeds_policy"}]
    client.app.state.deps.store.docs["app-1"] = doc
    resp = decide(client, OFFICER, token, state=state, reason="재검토")
    assert resp.status_code == 409 and "신청 상태가 바뀌었습니다" in resp.text and "재검토" in resp.text
    assert queue.sent == []
    fresh = re.search(r'name="state" value="([0-9a-f]{12})"', resp.text).group(1)
    assert fresh != state and decide(client, OFFICER, token, state=fresh).status_code == 202


def test_application_detail_pages_refresh_every_ten_seconds(env, make_doc):
    client, _ = env
    client.app.state.deps.store.docs["app-1"] = make_doc(status="received")
    assert '<meta http-equiv="refresh" content="10">' in client.get("/applications/app-1", headers=OFFICER).text
    pending = client.get("/applications/app-9", headers=OFFICER).text
    assert '<meta http-equiv="refresh" content="10;url=/applications/app-9?n=1">' in pending and "10초마다" in pending
    assert 'hx-trigger="every 3s"' in client.get("/", headers=OFFICER).text


def test_unknown_application_stops_refreshing_after_watch_limit(env):
    client, _ = env
    assert 'content="10;url=/applications/app-9?n=12"' in client.get("/applications/app-9?n=11", headers=OFFICER).text
    stopped = client.get("/applications/app-9?n=12", headers=OFFICER)
    assert stopped.status_code == 404 and 'http-equiv="refresh"' not in stopped.text
    assert "자동 새로 고침을 멈췄습니다" in stopped.text and "10초마다" not in stopped.text


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


def test_summary_tab_shows_officer_decision_once_decided(env, make_doc):
    client, _ = env
    assert "summary-officer" not in client.get("/applications/app-1", headers=OFFICER).text
    doc = make_doc(status="approved")
    doc["officer_decision"] = {"decision": "approve", "amount_krw": 5_000_000, "officer_id": "o1", "conditions": [],
                               "reason": "", "policy_override": False, "at": "2026-11-09T05:00:00+00:00"}
    client.app.state.deps.store.docs["app-1"] = doc
    page = client.get("/applications/app-1", headers=OFFICER).text
    card = page[page.index('id="tab-summary"'):page.index('id="tab-cashflow"')]
    assert 'class="card summary-officer"' in card and "심사역 결정" in card and ">승인<" in card
    assert "신청 금액" in card and "승인 금액" in card and "5,000,000원" in card


def test_detail_shows_kst_times_and_decision_label(env, make_doc):
    client, _ = env
    doc = make_doc(status="approved", received_at="2026-10-31T00:00:00+00:00")
    doc["sla"] = {**doc["sla"], "deadline": "2026-11-01T00:00:00Z"}
    doc["officer_decision"] = {"decision": "approve", "amount_krw": 5_000_000, "officer_id": "o1", "conditions": [],
                               "reason": "", "policy_override": False, "at": "2026-11-09T05:00:00+00:00"}
    client.app.state.deps.store.docs["app-1"] = doc
    page = client.get("/applications/app-1", headers=OFFICER).text
    assert "<dd>2026-10-31 09:00</dd>" in page and "<dd>2026-11-01 09:00</dd>" in page
    assert "마감 2026-11-01 09:00" in page and "T00:00:00" not in page
    tab = page[page.index('id="tab-decision"'):]
    assert "승인 · 5,000,000원" in tab and "approve ·" not in tab


def test_escalated_detail_shows_undecided_close_time(env, make_doc):
    client, _ = env
    doc = make_doc(status="escalated")
    doc["sla"] = {"hours": 24, "close_hours": 168, "deadline": "2026-11-08T00:00:00Z", "escalated": True}
    client.app.state.deps.store.docs["app-1"] = doc
    page = client.get("/applications/app-1", headers=OFFICER).text
    assert "<dt>미결 종료 예정</dt><dd>2026-11-08 09:00 (SLA 초과)</dd>" in page
