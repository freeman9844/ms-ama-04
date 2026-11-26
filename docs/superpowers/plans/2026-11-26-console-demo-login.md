# 심사 콘솔 데모 로그인 화면 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 데모 모드에서 심사역 화면 앞에 로그인 화면(`/login`)을 두고, 서명 쿠키로 로그인·로그아웃 장면이 실제처럼 동작하게 한다.

**Architecture:** `console/auth.py`에 세션 토큰 서명·검증과 `next` 정리 함수를 둔다. `console/app.py`의 `viewer()`가 `demo_login`이 켜져 있으면 쿠키를 요구하고, `/login`·`/logout` 라우트와 단독 템플릿 `login.html`을 더한다. Bicep이 데모 모드에서 `CONSOLE_DEMO_LOGIN=true`를 넣고, 배포 후 점검(`ops/console_gate.py`)이 `/` → `/login` 리디렉션을 확인한다.

**Tech Stack:** Python 3.12, FastAPI + Jinja2 + HTMX, pytest(`fastapi.testclient`), Bicep

**Spec:** `docs/superpowers/specs/2026-11-26-console-demo-login-design.md`

## Global Constraints

- 데모 아이디: `demo.officer`. 저장된 비밀번호는 없다. 비밀번호는 비어 있지만 않으면 통과한다(비밀 값 커밋 금지).
- 쿠키: 이름 `ama_demo_session`, 값 `<발급 epoch 초>.<HMAC-SHA256 hex>`, 유효 8시간(`28800`초), `HttpOnly`·`Secure`·`SameSite=Lax`·`Path=/`.
- 서명 키: `hmac.new(deps.pseudonym_key, b"console-demo-login", hashlib.sha256).digest()`.
- 켜는 조건: `deps.demo_principal is not None and deps.demo_login`. 환경 변수 `CONSOLE_DEMO_LOGIN=true`. 기본은 꺼짐이며, Easy Auth 모드·고객 화면·미리보기(`consolepreview`)·기존 테스트의 동작은 바뀌지 않는다.
- 결정 기록 주체는 그대로 `demo-operator`(`deps.demo_principal`)다.
- 화면 문구는 한국어 원문을 키로 쓰고 영문을 `src/console/locales/en/base.json`에 더한다(`src/console/locales/README.md`). CSP 때문에 인라인 스크립트·스타일은 쓰지 않는다.
- 테스트 실행: 저장소 가상환경의 python으로 `python -m pytest ...`(이 작업 트리에 `.venv`가 없으면 `main` 작업 트리의 `.venv\Scripts\python.exe`).
- `Secure` 쿠키를 주고받는 테스트는 `TestClient(..., base_url="https://testserver")`를 쓴다(http면 httpx가 쿠키를 보내지 않는다).

## Review Focus

1. 열린 리디렉션: `next=//evil.example`, `https://evil.example`, `/\evil.example`, `/login`은 모두 `/`로 간다 → Task 1 `test_safe_next_*`.
2. 위조·변조·만료 쿠키: 서명 변조, 숫자가 아닌 발급 시각, 8시간 경과, 미래 발급 시각은 로그인 화면으로 보낸다 → Task 1·Task 2.
3. 세션이 없을 때의 HTMX 조각 요청(목록 자동 갱신 등)은 깨진 조각 대신 `HX-Redirect: /login`을 받는다 → Task 2 `test_demo_login_htmx_gets_hx_redirect`.
4. 영문 화면: `/login`이 `ama_locale_v2=en`에서 영문으로 보이고, 모든 새 문구에 영문이 있다 → Task 2 `test_demo_login_page_in_english` + 기존 `test_every_msgid_has_english`.
5. 꺼진 모드: Easy Auth 모드와 `demo_login=False` 데모 모드에서는 `/login`·`/logout`이 404이고 사용자 영역 링크가 지금과 같다 → Task 2 `test_login_routes_absent_when_demo_login_off`.

---

### Task 1: 세션 토큰과 `next` 정리 함수

**Files:**
- Modify: `src/console/auth.py`
- Test: `tests/console/test_auth.py`

**Interfaces:**
- Produces:
  - 상수 `DEMO_LOGIN_ID = "demo.officer"`, `DEMO_SESSION_COOKIE = "ama_demo_session"`, `DEMO_SESSION_TTL_S = 28800`
  - `demo_session_token(key: bytes, issued_at: int) -> str` — `f"{issued_at}.{hmac_hex}"`, 서명 대상 `f"demo-login:{issued_at}"`
  - `verify_demo_session(key: bytes, token: str | None, now: int) -> bool` — 형식·서명(`hmac.compare_digest`)이 맞고 `0 <= now - issued_at < DEMO_SESSION_TTL_S`일 때만 `True`
  - `safe_next(target: str | None) -> str` — `/`로 시작하고 `//`·`/\`로 시작하지 않으며 CR/LF가 없고 경로가 `/login`이 아닐 때만 그대로, 아니면 `"/"`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
from console.auth import DEMO_SESSION_TTL_S, demo_session_token, safe_next, verify_demo_session

K = b"k" * 32


def test_demo_session_roundtrip_and_expiry():
    token = demo_session_token(K, 1_000)
    assert verify_demo_session(K, token, 1_000)
    assert verify_demo_session(K, token, 1_000 + DEMO_SESSION_TTL_S - 1)
    assert not verify_demo_session(K, token, 1_000 + DEMO_SESSION_TTL_S)
    assert not verify_demo_session(K, token, 999)          # 미래 발급


@pytest.mark.parametrize("token", [None, "", "abc", "x.y", "1000.", "1000." + "0" * 64])
def test_demo_session_rejects_malformed(token):
    assert not verify_demo_session(K, token, 1_000)


def test_demo_session_rejects_other_key_and_tampering():
    token = demo_session_token(K, 1_000)
    assert not verify_demo_session(b"z" * 32, token, 1_000)
    assert not verify_demo_session(K, "1001." + token.split(".")[1], 1_001)


@pytest.mark.parametrize("target, expected", [
    ("/applications/app-1?since=abc", "/applications/app-1?since=abc"), ("/", "/"),
    (None, "/"), ("", "/"), ("//evil.example", "/"), ("https://evil.example", "/"),
    ("/\\evil.example", "/"), ("/login", "/"), ("/login?next=/", "/"), ("/a\r\nSet-Cookie: x", "/"),
])
def test_safe_next(target, expected):
    assert safe_next(target) == expected
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/console/test_auth.py -q`
Expected: FAIL (`ImportError: cannot import name 'demo_session_token'`)

- [ ] **Step 3: `src/console/auth.py`에 위 상수와 세 함수 구현** (기존 `csrf_token`과 같은 `hmac`·`hashlib` 사용)

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/console/test_auth.py -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add src/console/auth.py tests/console/test_auth.py
git commit -m "feat(console): sign demo login sessions and sanitize next targets"
```

---

### Task 2: 로그인 화면·관문·로그아웃과 환경 설정

**Files:**
- Modify: `src/console/app.py` (`ConsoleDeps`, `render`, `viewer`, 새 라우트 `/login`·`/logout`)
- Create: `src/console/templates/login.html`
- Modify: `src/console/templates/base.html:27` (로그아웃 링크)
- Modify: `src/console/static/console.css` (`.login-*` 클래스)
- Modify: `src/console/locales/en/base.json`
- Modify: `src/console/adapters.py` (`build_deps`)
- Test: `tests/console/test_app.py`, `tests/console/test_adapters.py`

**Interfaces:**
- Consumes: Task 1의 상수·`demo_session_token`·`verify_demo_session`·`safe_next`
- Produces: `ConsoleDeps.demo_login: bool = False`(`demo_principal` 바로 아래). `build_deps`가 `demo_login=env.get("CONSOLE_DEMO_LOGIN") == "true"`로 채운다. 템플릿 변수 `demo_login`(= `deps.demo_principal is not None and deps.demo_login`)을 `render`가 넘긴다.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/console/test_app.py`의 데모 모드 절 아래에 픽스처와 테스트를 더한다(import 추가: `from datetime import timedelta`, `from journey.models import KST`).

```python
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
    assert client.get("/apply").headers["location"] == "/borrower"   # customer()를 통과해 사업자 선택으로


def test_demo_login_page_in_english(login_env):
    client, _ = login_env
    page = client.get("/login", cookies={"ama_locale_v2": "en"}).text
    assert "Sign in" in page and "아이디" not in page


def test_login_routes_absent_when_demo_login_off(env, demo_env):
    for client, headers in ((env[0], OFFICER), (demo_env[0], {})):
        assert client.get("/login").status_code == 404
        assert client.get("/logout").status_code == 404
        assert 'href="/logout"' not in client.get("/", headers=headers).text
```

`tests/console/test_adapters.py`의 `test_build_deps_demo_mode_principal` 옆에 더한다:

```python
@pytest.mark.parametrize("value, expected", [(None, False), ("false", False), ("true", True)])
def test_build_deps_demo_login_flag(monkeypatch, value, expected):
    # 위 테스트와 같은 monkeypatch 4개와 기본 env 사용
    env = BASE_ENV | {"CONSOLE_AUTH_MODE": "demo"} | ({"CONSOLE_DEMO_LOGIN": value} if value else {})
    assert build_deps(env).demo_login is expected
```

(`BASE_ENV`가 없으면 기존 테스트의 env 사전을 모듈 상수로 올려 두 테스트가 함께 쓴다.)

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/console/test_app.py -k "demo_login or login_routes or demo_logout" tests/console/test_adapters.py -k "demo_login" -q`
Expected: FAIL (`TypeError: ConsoleDeps.__init__() got an unexpected keyword argument 'demo_login'`)

- [ ] **Step 3: 구현**
  - `ConsoleDeps.demo_login: bool = False`, `build_deps`에서 환경 변수로 채운다.
  - `create_app`: `login_key`(Global Constraints의 키). `login_on = deps.demo_principal is not None and deps.demo_login`. `render`가 템플릿에 `demo_login=login_on`을 넘긴다.
  - `viewer()`: `login_on`이고 쿠키 `DEMO_SESSION_COOKIE`가 `verify_demo_session(login_key, …, int(deps.clock().timestamp()))`를 통과하지 못하면 `HX-Request` 헤더가 있을 때 `HTTPException(401, headers={"HX-Redirect": "/login"})`, GET이면 `HTTPException(303, headers={"Location": "/login?next=" + quote(경로+쿼리, safe="/")})`, 그 밖은 `HTTPException(401, "login required")`. 통과하면 지금처럼 `deps.demo_principal`을 쓴다. `customer()`는 바꾸지 않는다.
  - `GET /login`: `login_on`이 아니면 404. `login.html`을 `user=None`으로 그리고 `next=safe_next(쿼리 next)`를 넘긴다.
  - `POST /login`: `login_on`이 아니면 404. `_form(request)`로 읽어 `username.strip() == DEMO_LOGIN_ID`이고 `password`가 비어 있지 않으면 `RedirectResponse(safe_next(next), 303)`에 Global Constraints의 쿠키를 `set_cookie`한다. 아니면 422로 같은 화면에 `_("아이디 또는 비밀번호가 올바르지 않습니다.")`.
  - `GET /logout`: `login_on`이 아니면 404. `/login`으로 303 하고 `delete_cookie(DEMO_SESSION_COOKIE, path="/")`.
  - `login.html`: `base.html`을 상속하지 않는 단독 페이지. 기존 로고 SVG와 `AMA Lending {{ _("심사 콘솔") }}`, `{% include "_lang.html" %}`, 폼(`username` 값 `demo.officer`, `password` 값 미리 채움, hidden `next`), 버튼 `{{ _("로그인") }}`, 안내 `{{ _("데모 계정이 미리 입력돼 있습니다.") }}`, 오류가 있으면 오류 문구. 스펙의 텍스트 스케치 배치를 따른다.
  - `base.html:27`: `{% if demo_login %}<a href="/logout">…{% elif not demo %}<a href="/.auth/logout">…{% endif %}`(문구는 기존 `_("로그아웃")`).
  - `base.json` 영문: `아이디` → `User ID`, `비밀번호` → `Password`, `로그인` → `Sign in`, `데모 계정이 미리 입력돼 있습니다.` → `A demo account is pre-filled.`, `아이디 또는 비밀번호가 올바르지 않습니다.` → `Incorrect user ID or password.` (이미 있는 키는 다시 넣지 않는다).

- [ ] **Step 4: 통과 확인(새 테스트 + 콘솔 전체 회귀)**

Run: `python -m pytest tests/console -q`
Expected: PASS (기존 `test_demo_mode_*`, `test_every_msgid_has_english`, `test_template_korean_goes_through_gettext` 포함)

- [ ] **Step 5: 커밋**

```bash
git add src/console tests/console
git commit -m "feat(console): demo login page gating the officer console"
```

---

### Task 3: 배포 설정·배포 후 점검·문서

**Files:**
- Modify: `infra/resources.bicep:650-653` (데모 분기 환경 변수)
- Modify: `src/ops/console_gate.py` (`_check_demo_console`, 모듈 docstring)
- Modify: `README.md:64`, `docs/adr/ADR-007-demo-auth-mode.md`, `docs/architecture.md`(콘솔 행 인증 칸), `docs/runbook/setup.md:95`
- Test: `tests/infra/test_console.py`, `tests/ops/test_console_gate.py`

**Interfaces:**
- Consumes: Task 2의 `CONSOLE_DEMO_LOGIN` 환경 변수, `/` → `/login?next=/` 303, `GET /login` 200

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/infra/test_console.py::test_console_easy_auth_can_be_switched_off_for_demo`에 `assert env["CONSOLE_DEMO_LOGIN"] == "true"`를 더하고, 기본 배포(`default`) 컨테이너 env에 `CONSOLE_DEMO_LOGIN`이 없음을 단언한다.

`tests/ops/test_console_gate.py`의 데모 테스트를 바꾼다:

```python
def test_gate_in_demo_mode_expects_redirect_to_demo_login():
    base = ENV["CONSOLE_URL"]
    fetch, calls = fake_fetch({f"{base}/": (303, "/login?next=/"), f"{base}/login": (200, None)})
    result = check_console_gate(DEMO_ENV, fetch)
    assert "데모 로그인" in result and "App Gateway" in result
    assert calls == [f"{base}/", f"{base}/login"]


@pytest.mark.parametrize("responses, message", [
    ({"/": (403, None)}, "window open"),
    ({"/": (302, LOGIN)}, "Easy Auth"),
    ({"/": (200, None)}, "CONSOLE_DEMO_LOGIN"),
    ({"/": (303, "/login?next=/"), "/login": (500, None)}, "HTTP 500"),
    ({"/": (500, None)}, "HTTP 500"),
])
def test_gate_in_demo_mode_failures(responses, message):
    fetch, _ = fake_fetch({ENV["CONSOLE_URL"] + path: r for path, r in responses.items()})
    with pytest.raises(OpsError, match=message):
        check_console_gate(DEMO_ENV, fetch)
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/infra/test_console.py tests/ops/test_console_gate.py -q`
Expected: FAIL (`KeyError: 'CONSOLE_DEMO_LOGIN'`, 데모 점검 단언 실패). Bicep CLI가 없어 infra 테스트가 건너뛰어지면 ops 테스트만 실패를 확인한다.

- [ ] **Step 3: 구현**
  - `resources.bicep` 데모 분기에 `{ name: 'CONSOLE_DEMO_LOGIN', value: 'true' }`.
  - `_check_demo_console(base: str, fetch: Fetch) -> str`(인자는 끝 `/` 없는 기본 URL): `/`가 403이면 `_IP_BLOCKED`, 200이면 `"…데모 로그인 화면이 꺼져 있습니다(CONSOLE_DEMO_LOGIN). `python -m ops window open`으로 다시 프로비저닝하세요."`, 리디렉션이 같은 호스트의 경로 `/login`이 아니면 지금의 Easy Auth 오류, 그 밖의 상태는 `예상하지 못한 응답 HTTP {status}`. 그다음 `/login`이 200이 아니면 같은 HTTP 오류. 성공 문구는 `"데모 모드: Entra 로그인 꺼짐, 운영자 IP에서 App Gateway(WAF)를 거쳐 데모 로그인 화면(/login)으로 들어갑니다(자체 서명 인증서라 TLS 검증 생략)"`. 호출부는 `_check_demo_console(base.rstrip("/"), fetch)`.
  - 문서: ADR-007 결정·결과에 "데모 모드는 `CONSOLE_DEMO_LOGIN=true`로 장면용 로그인 화면(`/login`, 아이디 `demo.officer`, 비밀번호 검사 없음, 서명 쿠키 8시간)을 둔다. 인증이 아니며 결정 기록 주체는 그대로 `demo-operator`, 접근 차단은 WAF 운영자 IP 규칙"과 스펙 링크를 더한다. `README.md:64`, `docs/runbook/setup.md:95`, `docs/architecture.md` 콘솔 행 인증 칸에 같은 사실을 한 구절로 맞춘다.

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/infra/test_console.py tests/ops/test_console_gate.py tests/console -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add infra/resources.bicep src/ops/console_gate.py tests/infra/test_console.py tests/ops/test_console_gate.py README.md docs
git commit -m "feat(ops): enable demo login in demo mode and check it after deploy"
```
