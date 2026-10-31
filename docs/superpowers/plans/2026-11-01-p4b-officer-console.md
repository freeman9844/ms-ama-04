# P4b 심사역 콘솔(Officer Console)·Entra 인증 구현 계획

> **For agentic workers:** 이 계획은 `superpowers:subagent-driven-development`(태스크별 구현자+리뷰어) 또는 `superpowers:executing-plans`(현 세션에서 순차 실행)로 실행한다. 각 태스크는 TDD 순서(실패 테스트 → 실패 확인 → 구현 → 통과 확인 → 커밋)를 지킨다. 체크박스는 진행에 따라 갱신한다.

**Goal:** 스펙 §1.2·§2.2의 심사역 콘솔을 만든다. 심사역은 신청 목록과 상세(타임라인, 정책 결과, 심사 에이전트의 claim·근거 값·참조 거래)를 보고 승인·조건부 승인·거절을 내리며, 데모 사업자로 새 신청을 넣는다. 콘솔은 external Container Apps 환경(`cae-web`)에 하나만 두고 운영자 IP 허용 + Entra 로그인(Easy Auth)으로 막는다. Entra 앱 등록과 앱 역할(`CreditOfficer`, `CreditManager`, `Auditor`)은 Bicep Graph 확장으로 만든다. `CreditOfficer`·`CreditManager`는 가명 토큰을 실명으로 복원해 보고 결정할 수 있고, `Auditor`는 가명 그대로 읽기만 한다. 실제 Azure에서 콘솔을 배포하고 smoke로 로그인 관문을, 브라우저로 결정 흐름을 확인한다.

**Architecture:** `src/console`은 FastAPI + Jinja2 서버 렌더링(목록만 htmx 15초 폴링) 앱이다. 순수 로직은 `auth.py`(Easy Auth 헤더 → `Principal`, CSRF), `views.py`(신청 문서 + 동의 원천 데이터 → 화면 모델, 가명 복원)에 두고, `app.py`는 `ConsoleDeps`(저장소·원천 데이터·큐·가명 키) 프로토콜에만 의존해 가짜로 테스트한다. `adapters.py`가 Cosmos(`lending/applications` 읽기), 데이터 Blob(`consent-<id>` 읽기), Function Storage Queue(`applications-start`, `officer-decisions` 보내기), Key Vault(`pseudonym-key`)를 관리 ID로 연결한다. 결정은 P3 `OfficerMessage` 그대로 큐로 보내며, 콘솔은 같은 `validate_officer_decision`으로 먼저 걸러 내고 최종 검증은 오케스트레이터가 다시 한다. 인증은 Container Apps 기본 인증(ID 토큰, 비밀 없음)이 맡고 앱은 `X-MS-CLIENT-PRINCIPAL`의 역할만 믿는다. `officer_id`는 폼이 아니라 로그인 사용자에서 온다.

**Tech Stack:** P4a와 같음(Python 3.12, pydantic 2, Bicep) + FastAPI 0.162 / Starlette 1.9 / Jinja2 3.1.6 / uvicorn, htmx 2.0.11(unpkg, SRI 고정), Azure SDK(azure-identity, azure-cosmos, azure-storage-blob, azure-storage-queue, azure-keyvault-secrets — 콘솔 이미지에만, 지연 import), Bicep Graph 확장 `microsoftGraphV1` 1.0.0.

**Spec:** `docs/superpowers/specs/2026-10-31-agentic-cashflow-lending-design.md` (§1.2 UI·외부 진입, §2.2 네트워크 토폴로지, §4.1 결정 권한, §4.2 위협 모델 — 권한 오남용·앱 역할, §4.3 PII, §6.1 데모 대본, §7 콘솔 외부 ingress 위험)

**범위 밖(후속 계획):** 조기경보 알림·Service Bus 실시간 타임라인 표시, 동의 만료 시 원천 데이터·Foundry 대화 삭제(P5). 평가 대시보드(P6). 콘솔 결정 이력 감사 화면, 페이지 나누기·검색.

## 사용자 결정(브레인스토밍)

| 항목 | 결정 |
|------|------|
| 범위 | 최소 콘솔: 목록, 상세, 실명 복원, 결정, 새 신청 |
| 인증 흐름 | Easy Auth ID 토큰(암시적 흐름, 클라이언트 비밀 없음). 운영 설계는 README에 기록 |
| PII 표시 | `CreditOfficer`·`CreditManager`는 실명 복원, `Auditor`는 가명 그대로 |

## Global Constraints

- P2~P4a Global Constraints를 그대로 따른다(리전, 태그, 무키, 한국어 문서, Windows PowerShell, 커밋 trailer 두 줄, fail-closed, 멱등).
- **결정 권한(스펙 §4.1):** 콘솔은 결정을 큐(`officer-decisions`)로만 보낸다. Cosmos에 쓰지 않는다(관리 ID에 쓰기 역할 없음). 결정 가능한 상태는 `awaiting_officer`, `escalated`뿐이며, P3 `validate_officer_decision`(정책 한도, 조건부 조건 필수 등)을 그대로 먼저 적용한다.
- **인증·권한:** 모든 화면은 Easy Auth 헤더의 `Principal`과 앱 역할이 있어야 한다(`/healthz`·`/static` 제외). 결정·새 신청은 `CreditOfficer`·`CreditManager`만. `officer_id`는 로그인 사용자 ID.
- **진입 통제 fail-closed:** `CONSOLE_IP`가 없으면 ingress가 TEST-NET 주소(`192.0.2.0/32`)만 허용한다. Graph 앱 서비스 주체는 역할 할당 필수(`appRoleAssignmentRequired`).
- **PII(스펙 §4.3):** 복원은 화면 렌더링 순간에만 메모리에서 한다(가명 키는 Key Vault, 원천 데이터는 동의 컨테이너). 복원 결과를 저장하거나 로그에 남기지 않는다. `Auditor`와 원천 데이터가 없는 신청은 가명 그대로.
- **웹 보안:** Jinja2 autoescape, CSRF(사용자·키 HMAC), `application/x-www-form-urlencoded` + 16KB 상한, CSP·`nosniff`·`no-referrer`·`no-store`, htmx는 버전 고정 + SRI.
- 테스트: `.\.venv\Scripts\python -m pytest -q`(빠른 전체), 인프라는 `$env:RUN_INFRA_TESTS='1'`일 때만.

## Review Focus

| # | 위험 지점 | 기대 동작 | 고정 테스트 |
|---|-----------|-----------|-------------|
| 1 | 로그인·역할 없이 화면 접근 | 헤더 없음 401, 역할 없음 403, 깨진 헤더·oid 없음은 미인증 | T1 `test_missing_or_garbled_header_has_no_principal`, `test_principal_without_object_id_is_rejected`, T3 `test_pages_require_principal_and_console_role` |
| 2 | 감사자가 결정하거나 실명을 봄 | 결정·새 신청 403, 상세는 가명 토큰 그대로 | T3 `test_auditor_cannot_decide_or_submit`, `test_auditor_sees_pseudonyms_and_no_decision_form`, T2 `test_without_restore_everything_stays_pseudonymized` |
| 3 | 폼으로 `officer_id` 위조 | 큐 메시지의 `officer_id`는 로그인 사용자 | T3 `test_officer_decision_is_sent_with_principal_as_officer` |
| 4 | CSRF(Easy Auth 쿠키 세션) | 사용자에 묶인 토큰 없으면 403, 다른 사용자 토큰도 403 | T1 `test_csrf_token_is_bound_to_user_and_key`, T3 `test_decision_requires_csrf_token_of_same_user` |
| 5 | 모델·원천 텍스트로 XSS | autoescape, CSP | T3 `test_model_text_is_html_escaped`, `test_security_headers_on_every_response` |
| 6 | 결정 대기가 아닌 신청에 결정·한도 초과 금액 | 409, 한도 초과는 큐에 보내기 전 422 | T3 `test_decision_only_while_awaiting_officer`, `test_decision_over_policy_limit_is_rejected_before_queue`, `test_invalid_decisions_are_rejected` |
| 7 | 콘솔이 IP 제한 없이 열림 | `CONSOLE_IP` 없으면 TEST-NET만 허용, 있으면 그 /32만. 내부 앱은 IP 규칙·인증 없음 | T5 `test_console_ip_allow_list_fails_closed`, `test_vnet_only_apps_have_no_ip_rules_or_auth` |
| 8 | 로그인 없이 통과(Easy Auth 꺼짐·잘못된 앱) | `RedirectToLoginPage`, 발급자 v2.0, audience = 앱 ID. 실제 배포는 smoke가 Entra 리디렉션·`client_id` 확인 | T5 `test_console_easy_auth_requires_entra_login`, T6 `test_gate_failures` |
| 9 | 콘솔 관리 ID 권한 과다 | Cosmos `lending` 읽기, Blob 읽기, 큐 보내기, KV 비밀 읽기, ACR pull만 | T5 `test_console_identity_grants_are_least_privilege` |
| 10 | 콘솔 이미지에 필요한 코드 누락(`.dockerignore`) | Dockerfile COPY + `.dockerignore`만으로 `/app` 재현해 import | T4 `test_dockerfile_copies_everything_the_console_imports`, `test_dockerignore_keeps_other_images_lean` |
| 11 | 큐 메시지 인코딩 불일치 | base64 없이 UTF-8 JSON(host.json `messageEncoding: none`) | T4 `test_queue_sender_sends_plain_utf8_json` |

## 사전 검증(probe) 결과

- **Bicep Graph 확장:** Bicep CLI 0.48.1과 azd 1.37.2가 `infra/bicepconfig.json`(`microsoftGraphV1` 1.0.0)로 컴파일한다. Graph 템플릿(앱 역할 3개, redirect URI, 서비스 주체 `appRoleAssignmentRequired`, 운영자 `appRoleAssignedTo`)을 `az deployment group create`로 `rg-amalend`에 실제 배포해 성공했고, probe 앱과 배포 기록은 지웠다.
- **PSRule 1.47:** Graph 리소스를 조용히 빼고 실패하지 않는다(기존 인프라 테스트 영향 없음). 그래서 Graph 단언은 `console-auth.bicep`을 직접 컴파일한 JSON으로, Easy Auth 단언은 콘솔용 `container-app.bicep`만 펼친 결과(`infra/tests/console-app.bicepparam`)로 한다. PSRule에서 관리 ID `principalId`는 모두 같은 값이라, 콘솔 역할은 컴파일된 `resources.bicep`의 모듈 파라미터 식에서 확인한다.
- **Easy Auth 흐름:** 관리 ID 페더레이션(FIC) 방식은 Container Apps에서 아직 비공식이다. 클라이언트 비밀 없이 `clientId` + `openIdIssuer`만 주면 ID 토큰(암시적 흐름)으로 로그인한다(앱 등록에 `enableIdTokenIssuance`).

## 알려진 한계(README에 기록)

- 암시적 흐름(ID 토큰만)은 데모 범위다. 운영 설계는 Key Vault 비밀 또는 FIC로 인증 코드 흐름, 콘솔은 internal 환경 + App Gateway WAF 뒤.
- Easy Auth 헤더의 역할 클레임 이름은 문서 기준 `roles`로 가정한다(긴 role URI·`role_typ`도 읽음). 실제 배포 후 브라우저로 확인한다(T7).
- 실제 로그인 → 결정 흐름은 자동 테스트가 없다. smoke는 로그인 없는 요청이 Entra로 리디렉션되는지만 본다.
- 목록은 Cosmos 교차 파티션 `ORDER BY updated_at` 최근 50건(페이지 없음).

## 배포 위험(T7 실제 배포에서 확인)

| # | 위험 | 대응 |
|---|------|------|
| 1 | `azd provision`이 Graph 확장 리소스를 배포하지 못함 | probe는 az CLI 배포였다. 실패하면 오류를 기록하고 멈춘 뒤 사용자와 대안(사전 등록 + 파라미터)을 정한다 |
| 2 | redirect URI 도메인 불일치(`AADSTS50011`) | `ca-console.<cae-web 기본 도메인>`으로 계산한다. 다르면 앱 FQDN과 비교하고 Bicep을 고친다(TDD) |
| 3 | 콘솔 → Cosmos·Blob·Queue·KV Private Endpoint 이름 해석(`snet-apps-web`) | 같은 VNet, Private DNS zone이 VNet에 연결되어 있다. 상세 화면 오류면 콘솔 로그(`ContainerAppConsoleLogs_CL`)를 본다 |
| 4 | 교차 파티션 `ORDER BY` 쿼리 거부 | serverless Cosmos에서 단일 필드 정렬은 기본 인덱스로 된다. 실패하면 목록 오류를 보고 쿼리를 고친다(TDD) |
| 5 | 역할 클레임 이름 다름 → 로그인 후 403 | `/.auth/me`로 실제 클레임을 확인하고 `auth.py` 파서를 고친다(TDD) |
| 6 | P4a 리뷰 수정(8ca913b)이 아직 Function에 없음 | T7에서 `deploy orchestrator`로 함께 배포한다 |

## 파일 구조

```
src/console/__init__.py                    빈 파일
src/console/auth.py                        Easy Auth 헤더 → Principal(역할 권한), CSRF 토큰
src/console/views.py                       원천 데이터 로드, 가명 복원, 상세 화면 모델(claim 근거 값·참조 거래)
src/console/app.py                         FastAPI 앱: 목록·상세·결정·새 신청, 보안 헤더
src/console/templates/*.html               base, list, _rows, detail, new, pending, sent
src/console/static/console.css
src/console/adapters.py                    Cosmos·Blob·Queue·Key Vault 어댑터, build_deps
src/console/main.py                        uvicorn 진입점
src/console/Dockerfile                     콘솔 이미지(shared, agents, journey 모델·검증, console)
requirements-console.txt                   콘솔 이미지 의존성(버전 고정)
.dockerignore                              (수정) src/agents 포함, journey 모델·검증만 예외
pyproject.toml                             (수정) jinja2, src/console 패키지
infra/bicepconfig.json                     Graph 확장
infra/modules/console-auth.bicep           Entra 앱 등록·앱 역할·서비스 주체·운영자 역할 할당
infra/modules/container-app.bicep          (수정) ipAllowList, authClientId(Easy Auth)
infra/modules/identities.bicep             (수정) id-console
infra/resources.bicep                      (수정) 콘솔 역할, console-auth, ca-console, 출력
infra/main.bicep, infra/main.bicepparam    (수정) consoleImage, consoleIp, 출력
infra/tests/{open,variant}.bicepparam      (수정) consoleIp, consoleImage
infra/tests/console-app.bicepparam         Easy Auth 확인용 모듈 파라미터
src/ops/console_gate.py                    운영자 PC에서 콘솔 로그인 관문 확인
src/ops/{deploy,window,cli}.py             (수정) console 대상, CONSOLE_IP, smoke 뒤 관문 확인
tests/console/*                            conftest, test_auth, test_views, test_app, test_adapters, test_dockerfile
tests/infra/test_console.py, conftest.py, test_platform.py   (새 파일/수정)
tests/ops/test_console_gate.py, test_deploy.py, test_window.py (새 파일/수정)
README.md                                  (수정) 운영 명령, P4b 절
```

태스크 의존: T1 → T2 → T3 → T4 → T5 → T6 → T7(순서대로). 각 태스크 끝의 전체 테스트 수는 P4a merge(`6e35081`) 위에서 순서대로 재현해 확인한 값이다.

---

### Task 1: 콘솔 인증: Easy Auth 주체·앱 역할·CSRF

콘솔 패키지를 만들고 Easy Auth 헤더 파서·역할 권한·CSRF 토큰을 둔다(스펙 §4.2 앱 역할).

**설계 메모**
- `parse_principal(header) -> Principal | None`: `X-MS-CLIENT-PRINCIPAL`(base64 JSON)의 `claims`에서 객체 ID(`.../objectidentifier` 또는 `oid`)를 `user_id`로 쓴다. 헤더가 없거나 깨졌거나 객체 ID가 없으면 None(미인증).
- 역할은 `roles`, 긴 role URI, 헤더의 `role_typ` 클레임에서 모은다. 이름은 `name_typ` → `name` → `preferred_username` → `user_id` 순서.
- `Principal.can_view`(세 역할 중 하나), `can_decide`·`can_restore`(`CreditOfficer`·`CreditManager`만). 감사자는 읽기만 하고 가명 그대로 본다.
- `csrf_token(key, user_id)`는 HMAC-SHA256, `verify_csrf`는 `compare_digest`. Easy Auth는 쿠키 세션이라 상태 변경 폼마다 토큰을 함께 보낸다.
- `pyproject.toml`: `jinja2` 의존성, `src/console` 패키지 등록.

**Files:**
- Create: `src/console/__init__.py`
- Create: `src/console/auth.py`
- Modify: `pyproject.toml`
- Create: `tests/console/test_auth.py`

- [ ] **Step 1: 실패하는 테스트 작성**

새 파일 `tests/console/test_auth.py`:

```python
import base64
import json

from console.auth import AUDITOR, CREDIT_MANAGER, CREDIT_OFFICER, Principal, csrf_token, parse_principal, verify_csrf

OID = "http://schemas.microsoft.com/identity/claims/objectidentifier"
ROLE = "http://schemas.microsoft.com/ws/2008/06/identity/claims/role"


def header(claims, **extra):
    body = {"auth_typ": "aad", "name_typ": "name", "role_typ": ROLE, "claims": claims, **extra}
    return base64.b64encode(json.dumps(body).encode()).decode()


def claims(*roles, oid="0f0e-officer", name="김심사"):
    out = [{"typ": OID, "val": oid}, {"typ": "name", "val": name}]
    return out + [{"typ": "roles", "val": r} for r in roles]


def test_parses_user_and_roles_from_easy_auth_header():
    p = parse_principal(header(claims(CREDIT_OFFICER)))
    assert p == Principal(user_id="0f0e-officer", name="김심사", roles=frozenset({CREDIT_OFFICER}))


def test_role_claims_under_role_typ_are_also_read():
    p = parse_principal(header([{"typ": OID, "val": "u1"}, {"typ": ROLE, "val": AUDITOR}]))
    assert p.roles == frozenset({AUDITOR})
    assert p.name == "u1"


def test_short_oid_claim_is_accepted():
    p = parse_principal(header([{"typ": "oid", "val": "u2"}, {"typ": "roles", "val": CREDIT_MANAGER}]))
    assert p.user_id == "u2"


def test_missing_or_garbled_header_has_no_principal():
    assert parse_principal(None) is None
    assert parse_principal("") is None
    assert parse_principal("not-base64!!") is None
    assert parse_principal(base64.b64encode(b"[1, 2]").decode()) is None
    assert parse_principal(base64.b64encode(b"\xff\xfe").decode()) is None


def test_principal_without_object_id_is_rejected():
    assert parse_principal(header([{"typ": "roles", "val": CREDIT_OFFICER}])) is None


def test_permissions_by_role():
    officer = Principal("u", "u", frozenset({CREDIT_OFFICER}))
    manager = Principal("u", "u", frozenset({CREDIT_MANAGER}))
    auditor = Principal("u", "u", frozenset({AUDITOR}))
    nobody = Principal("u", "u", frozenset({"Reader"}))
    assert [officer.can_view, officer.can_decide, officer.can_restore] == [True, True, True]
    assert [manager.can_view, manager.can_decide, manager.can_restore] == [True, True, True]
    assert [auditor.can_view, auditor.can_decide, auditor.can_restore] == [True, False, False]
    assert [nobody.can_view, nobody.can_decide, nobody.can_restore] == [False, False, False]


def test_csrf_token_is_bound_to_user_and_key():
    token = csrf_token(b"k" * 32, "u1")
    assert verify_csrf(b"k" * 32, "u1", token)
    assert not verify_csrf(b"k" * 32, "u2", token)
    assert not verify_csrf(b"x" * 32, "u1", token)
    assert not verify_csrf(b"k" * 32, "u1", "")
    assert not verify_csrf(b"k" * 32, "u1", None)
```

- [ ] **Step 2: 실패 확인**

```powershell
.\.venv\Scripts\python -m pytest tests/console/test_auth.py -q
```

Expected: 수집 단계 `ModuleNotFoundError: No module named 'console'`.

- [ ] **Step 3: 구현**

새 파일 `src/console/__init__.py`: 빈 파일.

새 파일 `src/console/auth.py`:

```python
"""Container Apps 기본 인증(Easy Auth)이 넘기는 사용자 정보와 콘솔 권한.

Easy Auth는 로그인한 사용자의 ID 토큰 클레임을 `X-MS-CLIENT-PRINCIPAL`(base64 JSON)로 넘기고,
외부에서 들어온 같은 이름의 헤더는 지운다. 콘솔은 이 헤더만 믿고 역할(Entra 앱 역할)로 권한을 정한다.
"""

import base64
import binascii
import hashlib
import hmac
import json
from dataclasses import dataclass

CREDIT_OFFICER = "CreditOfficer"
CREDIT_MANAGER = "CreditManager"
AUDITOR = "Auditor"
PRINCIPAL_HEADER = "x-ms-client-principal"

_ROLE_CLAIMS = {"roles", "http://schemas.microsoft.com/ws/2008/06/identity/claims/role"}
_OID_CLAIMS = ("http://schemas.microsoft.com/identity/claims/objectidentifier", "oid")
_DECIDERS = frozenset({CREDIT_OFFICER, CREDIT_MANAGER})


@dataclass(frozen=True)
class Principal:
    user_id: str
    name: str
    roles: frozenset[str]

    @property
    def can_view(self) -> bool:
        return bool(self.roles & (_DECIDERS | {AUDITOR}))

    @property
    def can_decide(self) -> bool:
        """승인·조건부·거절 결정과 신규 신청 제출."""
        return bool(self.roles & _DECIDERS)

    @property
    def can_restore(self) -> bool:
        """가명 토큰을 실명으로 복원해 보기. 감사자는 가명 그대로 본다."""
        return bool(self.roles & _DECIDERS)


def parse_principal(header: str | None) -> Principal | None:
    if not header:
        return None
    try:
        data = json.loads(base64.b64decode(header, validate=True))
    except (binascii.Error, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    claims = [c for c in data.get("claims") or [] if isinstance(c, dict)]

    def first(*types: str) -> str:
        return next((str(c.get("val")) for t in types for c in claims if c.get("typ") == t and c.get("val")), "")

    user_id = first(*_OID_CLAIMS)
    if not user_id:
        return None
    role_types = _ROLE_CLAIMS | {data.get("role_typ")}
    roles = frozenset(str(c["val"]) for c in claims if c.get("typ") in role_types and c.get("val"))
    name = first(data.get("name_typ") or "name", "name", "preferred_username") or user_id
    return Principal(user_id=user_id, name=name, roles=roles)


def csrf_token(key: bytes, user_id: str) -> str:
    """Easy Auth 세션 쿠키로 인증되므로 상태 변경 폼에는 사용자에 묶인 토큰을 함께 보낸다."""
    return hmac.new(key, f"csrf:{user_id}".encode(), hashlib.sha256).hexdigest()


def verify_csrf(key: bytes, user_id: str, token: str | None) -> bool:
    return bool(token) and hmac.compare_digest(csrf_token(key, user_id), token)
```

`pyproject.toml` 수정(`git apply`로 적용 가능한 diff):

```diff
diff --git a/pyproject.toml b/pyproject.toml
index a3b7c41..348978b 100644
--- a/pyproject.toml
+++ b/pyproject.toml
@@ -13,6 +13,7 @@ dependencies = [
   "uvicorn[standard]>=0.30",
   "pyyaml>=6.0",
   "httpx>=0.27",
+  "jinja2>=3.1",
 ]
 
 [project.optional-dependencies]
@@ -23,7 +24,7 @@ dev = [
 ]
 
 [tool.hatch.build.targets.wheel]
-packages = ["src/shared", "src/policy", "src/mockapis", "src/ops", "src/agents"]
+packages = ["src/shared", "src/policy", "src/mockapis", "src/ops", "src/agents", "src/console"]
 
 [tool.pytest.ini_options]
 pythonpath = ["src", "src/orchestrator"]
```

- [ ] **Step 4: 통과 확인**

```powershell
.\.venv\Scripts\python -m pytest tests/console/test_auth.py -q; .\.venv\Scripts\python -m pytest -q
```

Expected: `7 passed`, 전체 `375 passed, 74 skipped`(경고 1건은 Starlette TestClient의 httpx 사용 안내로 무해).

- [ ] **Step 5: 커밋**

```powershell
git add src/console/__init__.py src/console/auth.py pyproject.toml tests/console/test_auth.py
git commit -m "console: Easy Auth principal, app roles and CSRF tokens" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>" -m "Copilot-Session: 05f1ca1b-6073-417d-bfb1-6b546f82cb58"
```

---

### Task 2: 상세 화면 모델: 원천 데이터·실명 복원·근거 값

상세 화면 모델: 신청 문서(Cosmos)와 동의 원천 데이터(Blob)를 합쳐 claim·근거 값·참조 거래를 보여 주고, 권한이 있으면 가명을 실명으로 되돌린다(스펙 §4.3, §6.1).

**설계 메모**
- `raw_container(consent_id)`는 `consent-<id>`로, 오케스트레이터의 같은 이름 함수와 테스트로 묶는다(콘솔은 오케스트레이터 런타임 의존성을 끌어오지 않는다).
- `load_raw(store, doc)`: 동의 ID가 없거나 `profile.json`·`transactions.json` 중 하나라도 없으면 None.
- 복원: 가명 토큰은 HMAC으로 결정적이라 같은 키로 원문(상호·대표자·사업자번호·거래 적요)을 다시 가명처리하면 저장된 토큰과 같은 매핑이 생긴다. `restore=True`면 모델 출력 문자열 전체에 `restore`를 적용하고, 아니면 원천 적요를 `mask`해서 보여 준다. 상태는 `pii`(`restored`·`masked`·`unavailable`)로 화면에 표시한다.
- claim 근거 값은 P4a `build_catalog`로 다시 계산해 `(ID, 값)`으로 보여 준다(문서에 카탈로그를 따로 저장하지 않는다). 참조 거래는 claim이 인용한 `txn_ids`만.
- `DECIDABLE = {awaiting_officer, escalated}`.
- `tests/console/conftest.py`: 가명 키, 데모 프로필·거래, 심사 완료 문서(`assessed_doc`), 가짜 원천 저장소 fixture.

**Files:**
- Create: `src/console/views.py`
- Create: `tests/console/conftest.py`
- Create: `tests/console/test_views.py`

- [ ] **Step 1: 실패하는 테스트 작성**

새 파일 `tests/console/conftest.py`:

```python
from datetime import date

import pytest

from agents.data_assembly import build_pseudonymizer
from shared.contracts import ApplicantProfile, Transaction

KEY = b"console-test-pseudonym-key-0123456789"
PROFILE = ApplicantProfile(
    applicant_id="demo-cafe-001", business_name="빈스카페", business_reg_no="123-45-67890",
    representative_name="김대표", industry="cafe", region="서울", business_age_months=48,
)
TXNS = [
    Transaction(txn_id="t1", booked_on=date(2026, 9, 1), amount_krw=-2_500_000, balance_after_krw=10_000_000,
                description="급여 이직원", txn_type_code="TRANSFER_OUT"),
    Transaction(txn_id="t2", booked_on=date(2026, 9, 2), amount_krw=-1_000_000, balance_after_krw=9_000_000,
                description="김대표 개인이체", txn_type_code="TRANSFER_OUT"),
    Transaction(txn_id="t3", booked_on=date(2026, 9, 3), amount_krw=5_000_000, balance_after_krw=14_000_000,
                description="카드매출 정산", txn_type_code="CARD_SETTLE"),
]


def mask(text: str) -> str:
    return build_pseudonymizer(KEY, PROFILE, TXNS).mask(text)


class FakeRaw:
    def __init__(self, blobs: dict[tuple[str, str], object] | None = None):
        self.blobs = blobs if blobs is not None else {
            ("consent-c-1", "profile.json"): PROFILE.model_dump(mode="json"),
            ("consent-c-1", "transactions.json"): [t.model_dump(mode="json") for t in TXNS],
        }
        self.reads: list[tuple[str, str]] = []

    def get_json(self, container: str, name: str):
        self.reads.append((container, name))
        return self.blobs.get((container, name))


def make_doc(status: str = "awaiting_officer", **overrides) -> dict:
    doc = {
        "id": "app-1",
        "application_id": "app-1",
        "applicant_id": "demo-cafe-001",
        "requested_amount_krw": 30_000_000,
        "requested_tenor_months": 12,
        "status": status,
        "received_at": "2026-10-31T09:00:00+09:00",
        "updated_at": "2026-10-31T09:02:00+09:00",
        "timeline": [
            {"status": "received", "at": "2026-10-31T09:00:00+09:00"},
            {"status": "awaiting_officer", "at": "2026-10-31T09:02:00+09:00"},
        ],
        "consent": {"consent_id": "c-1", "scopes": ["account", "tax", "pg"], "expires_at": "2027-01-29T09:00:00+09:00"},
        "underwriting": {
            "features": {"dscr": 1.8, "avg_monthly_inflow_krw": 42_000_000, "industry": "cafe"},
            "signals": {"owner_draw_ratio": 0.1},
            "flags": [],
            "monthly": [{"month": "2026-09", "revenue": 40_000_000, "operating_out": 30_000_000,
                         "debt_service": 1_000_000, "owner_draw": 1_000_000}],
            "classification": None,
            "category_overrides": {},
        },
        "policy": {
            "application_id": "app-1", "decision": "eligible", "max_amount_krw": 20_000_000, "rate_band": "B",
            "reason_codes": [{"code": "R101", "message": "DSCR 충족", "field": "dscr", "observed": 1.8,
                              "threshold": 1.25}],
            "rules_version": "2026.11-v1",
        },
        "assessment": {
            "application_id": "app-1",
            "recommendation": "approve",
            "summary": mask("빈스카페(대표 김대표)는 계절성이 있다"),
            "claims": [
                {"text": mask("김대표 개인이체가 월 100만원이다"), "metric_ids": ["M:dscr", "M:unknown"],
                 "txn_ids": ["t2"]},
                {"text": "DSCR 1.8로 정책 기준을 넘는다", "metric_ids": ["S:2026-09:index", "P:R101:threshold"],
                 "txn_ids": []},
            ],
            "proposed_structure": {"amount_krw": 20_000_000, "tenor_months": 12, "schedule": "level",
                                   "reduced_payment_months": [], "rationale": "한도 내"},
            "counterfactuals": [],
            "review": {"passed": True, "rounds": 1, "issues": []},
            "models": {"analyst": "gpt-5.4", "reviewer": "gpt-5.4-mini"},
        },
        "flags": [],
        "sla": {"hours": 24, "deadline": "2026-11-01T09:00:00+09:00", "escalated": False},
        "decision_attempts": [],
        "officer_decision": None,
        "error": None,
    }
    doc.update(overrides)
    return doc


@pytest.fixture
def raw():
    return FakeRaw()


@pytest.fixture
def key():
    return KEY


@pytest.fixture(name="make_doc")
def make_doc_fixture():
    return make_doc


@pytest.fixture(name="fake_raw")
def fake_raw_fixture():
    return FakeRaw
```

새 파일 `tests/console/test_views.py`:

```python
from console.views import DECIDABLE, detail_view, load_raw, raw_container
from journey.activities import raw_container as journey_raw_container


def test_raw_container_matches_orchestrator():
    assert raw_container("c-1") == journey_raw_container("c-1") == "consent-c-1"


def test_load_raw_reads_profile_and_transactions(raw, make_doc):
    data = load_raw(raw, make_doc())
    assert data.profile.business_name == "빈스카페"
    assert [t.txn_id for t in data.transactions] == ["t1", "t2", "t3"]
    assert ("consent-c-1", "profile.json") in raw.reads


def test_load_raw_is_none_without_consent_or_blobs(make_doc, fake_raw):
    assert load_raw(fake_raw(), make_doc(consent=None)) is None
    assert load_raw(fake_raw({}), make_doc()) is None


def test_restore_shows_real_names_in_assessment_and_transactions(raw, make_doc, key):
    view = detail_view(make_doc(), load_raw(raw, make_doc()), key, restore=True)
    assert view["pii"] == "restored"
    assert view["assessment"]["summary"] == "빈스카페(대표 김대표)는 계절성이 있다"
    assert view["claims"][0]["text"] == "김대표 개인이체가 월 100만원이다"
    assert view["transactions"] == [
        {"txn_id": "t2", "booked_on": "2026-09-02", "amount_krw": -1_000_000, "description": "김대표 개인이체"}
    ]


def test_without_restore_everything_stays_pseudonymized(raw, make_doc, key):
    view = detail_view(make_doc(), load_raw(raw, make_doc()), key, restore=False)
    assert view["pii"] == "masked"
    assert "김대표" not in view["assessment"]["summary"] and "[[PERSON_" in view["assessment"]["summary"]
    assert "빈스카페" not in view["assessment"]["summary"]
    assert view["transactions"][0]["description"].startswith("[[PERSON_")
    assert "김대표" not in str(view)


def test_restore_without_raw_data_keeps_tokens(make_doc, key):
    view = detail_view(make_doc(), None, key, restore=True)
    assert view["pii"] == "unavailable"
    assert "[[PERSON_" in view["assessment"]["summary"]
    assert view["transactions"] == []


def test_claim_evidence_values_come_from_catalog(raw, make_doc, key):
    view = detail_view(make_doc(), load_raw(raw, make_doc()), key, restore=False)
    assert view["claims"][0]["evidence"] == [("M:dscr", 1.8), ("M:unknown", None)]
    assert view["claims"][1]["evidence"] == [("S:2026-09:index", 1.0), ("P:R101:threshold", 1.25)]
    assert view["claims"][0]["txn_ids"] == ["t2"]


def test_early_stage_document_has_empty_sections(make_doc, key):
    doc = make_doc(status="received", underwriting=None, policy=None, assessment=None, consent=None)
    view = detail_view(doc, None, key, restore=True)
    assert view["assessment"] is None
    assert view["claims"] == [] and view["transactions"] == []
    assert view["decidable"] is False


def test_decidable_only_while_waiting_for_officer(make_doc, key):
    assert DECIDABLE == {"awaiting_officer", "escalated"}
    for status, expected in (("awaiting_officer", True), ("escalated", True), ("approved", False)):
        assert detail_view(make_doc(status=status), None, key, restore=False)["decidable"] is expected
    assert detail_view(make_doc(), None, key, restore=False)["max_amount_krw"] == 20_000_000
```

- [ ] **Step 2: 실패 확인**

```powershell
.\.venv\Scripts\python -m pytest tests/console/test_views.py -q
```

Expected: 수집 단계 `ModuleNotFoundError: No module named 'console.views'`.

- [ ] **Step 3: 구현**

새 파일 `src/console/views.py`:

```python
"""심사역 콘솔 화면 데이터. 가명 토큰 복원과 근거 값 조회를 여기서 한다.

에이전트 결과(assessment)는 가명 토큰([[KIND_hex]])이 들어간 채 저장된다. 복원 권한이 있으면
원천 데이터(Blob)로 같은 키의 가명처리기를 다시 만들어 토큰→원문 매핑을 채운 뒤 되돌린다.
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from agents.data_assembly import build_pseudonymizer
from agents.evidence import build_catalog
from shared.contracts import ApplicantProfile, Transaction

DECIDABLE = frozenset({"awaiting_officer", "escalated"})


class RawStore(Protocol):
    def get_json(self, container: str, name: str) -> Any: ...


@dataclass(frozen=True)
class RawData:
    profile: ApplicantProfile
    transactions: list[Transaction]


def raw_container(consent_id: str) -> str:
    # journey.activities.raw_container와 같아야 한다(테스트로 고정). 콘솔은 오케스트레이터 의존성을 끌어오지 않는다.
    return f"consent-{consent_id}"


def load_raw(store: RawStore, doc: Mapping[str, Any]) -> RawData | None:
    consent = doc.get("consent") or {}
    if not consent.get("consent_id"):
        return None
    container = raw_container(consent["consent_id"])
    profile = store.get_json(container, "profile.json")
    txns = store.get_json(container, "transactions.json")
    if profile is None or txns is None:
        return None
    return RawData(ApplicantProfile.model_validate(profile), [Transaction.model_validate(t) for t in txns])


def _map_strings(value: Any, fn: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, list):
        return [_map_strings(v, fn) for v in value]
    if isinstance(value, dict):
        return {k: _map_strings(v, fn) for k, v in value.items()}
    return value


def _text_functions(raw: RawData | None, key: bytes, restore: bool) -> tuple[str, Callable[[str], str], Callable[[str], str]]:
    """(pii 상태, 모델 출력 변환, 원천 텍스트 변환)."""
    if raw is None:
        return ("unavailable" if restore else "masked"), (lambda s: s), (lambda s: s)
    p = build_pseudonymizer(key, raw.profile, raw.transactions)
    if not restore:
        return "masked", (lambda s: s), p.mask
    # 토큰은 결정적(HMAC)이라 같은 원문을 다시 가명처리하면 저장된 토큰과 같은 매핑이 생긴다.
    p.mask(" ".join((raw.profile.business_name, raw.profile.representative_name, raw.profile.business_reg_no)))
    for t in raw.transactions:
        p.mask(t.description)
    return "restored", p.restore, (lambda s: s)


def _referenced(claims: Iterable[Mapping[str, Any]]) -> list[str]:
    seen: dict[str, None] = {}
    for c in claims:
        for txn_id in c.get("txn_ids", ()):
            seen.setdefault(txn_id, None)
    return list(seen)


def detail_view(doc: Mapping[str, Any], raw: RawData | None, key: bytes, *, restore: bool) -> dict[str, Any]:
    pii, model_text, raw_text = _text_functions(raw, key, restore)
    uw = doc.get("underwriting") or {}
    policy = doc.get("policy")
    catalog = (
        build_catalog(uw.get("features") or {}, uw.get("signals") or {}, uw.get("monthly") or [], policy)
        if uw and policy
        else {}
    )
    assessment = _map_strings(doc["assessment"], model_text) if doc.get("assessment") else None
    claims = [
        {
            "text": c["text"],
            "evidence": [(m, catalog.get(m)) for m in c.get("metric_ids", ())],
            "txn_ids": list(c.get("txn_ids", ())),
        }
        for c in (assessment or {}).get("claims", ())
    ]
    wanted = set(_referenced(claims))
    transactions = [
        {
            "txn_id": t.txn_id,
            "booked_on": t.booked_on.isoformat(),
            "amount_krw": t.amount_krw,
            "description": raw_text(t.description),
        }
        for t in (raw.transactions if raw else ())
        if t.txn_id in wanted
    ]
    return {
        "doc": doc,
        "pii": pii,
        "assessment": assessment,
        "claims": claims,
        "transactions": transactions,
        "features": uw.get("features") or {},
        "policy": policy,
        "decidable": doc.get("status") in DECIDABLE,
        "max_amount_krw": (policy or {}).get("max_amount_krw"),
    }
```

- [ ] **Step 4: 통과 확인**

```powershell
.\.venv\Scripts\python -m pytest tests/console/test_views.py -q; .\.venv\Scripts\python -m pytest -q
```

Expected: `9 passed`, 전체 `384 passed, 74 skipped`(경고 1건은 Starlette TestClient의 httpx 사용 안내로 무해).

- [ ] **Step 5: 커밋**

```powershell
git add src/console/views.py tests/console/conftest.py tests/console/test_views.py
git commit -m "console: detail view model with pseudonym restore and evidence values" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>" -m "Copilot-Session: 05f1ca1b-6073-417d-bfb1-6b546f82cb58"
```

---

### Task 3: 콘솔 웹 앱: 목록·상세·결정·새 신청

FastAPI 앱: 목록·상세·결정·새 신청 화면과 보안 헤더(스펙 §1.2, §4.1, §4.2).

**설계 메모**
- `create_app(ConsoleDeps)`: `ConsoleDeps(store, raw, queue, pseudonym_key)` 프로토콜만 쓴다. CSRF 키는 가명 키에서 파생해 레플리카가 같은 토큰을 쓴다.
- 경로: `GET /healthz`(인증 없음), `GET /`(최근 50건, `/applications/rows`를 htmx 15초 폴링), `GET /applications/new`, `POST /applications`(데모 사업자 선택 + 금액·기간을 P3 `StartRequest`로 검증, 실패 422 → `applications-start` 큐, 상세로 303), `GET /applications/{id}`(없으면 접수 대기 화면 + 자동 새로고침), `POST /applications/{id}/decision`.
- 결정: 결정 가능한 상태가 아니면 409. 폼을 P3 `OfficerDecision` 모양으로 만들어 `validate_officer_decision(decision, doc.policy)`로 먼저 검사하고 실패는 422(오류 문구를 화면에 표시). 통과하면 `OfficerMessage(application_id, officer_id=로그인 사용자, decision)`를 `officer-decisions` 큐로 보낸다. 조건부는 줄 단위 조건, 거절은 금액을 버린다.
- 폼은 `application/x-www-form-urlencoded`만(아니면 415), 16KB 넘으면 413, 필드 20개까지. 응답마다 CSP(`script-src 'self' https://unpkg.com`, `frame-ancestors 'none'`; `form-action`은 Easy Auth 리디렉션을 깨서 넣지 않음), `nosniff`, `no-referrer`, 정적 파일 외 `no-store`.
- 템플릿은 Jinja2 autoescape. htmx 2.0.11은 unpkg 고정 URL + SRI.

**Files:**
- Create: `src/console/app.py`
- Create: `src/console/templates/base.html`
- Create: `src/console/templates/list.html`
- Create: `src/console/templates/_rows.html`
- Create: `src/console/templates/detail.html`
- Create: `src/console/templates/new.html`
- Create: `src/console/templates/pending.html`
- Create: `src/console/templates/sent.html`
- Create: `src/console/static/console.css`
- Create: `tests/console/test_app.py`

- [ ] **Step 1: 실패하는 테스트 작성**

새 파일 `tests/console/test_app.py`:

```python
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
                                                  "amount_krw": 15_000_000, "conditions": [], "reason": ""}},
    )]


def test_conditional_decision_splits_conditions_and_decline_drops_amount(env):
    client, queue = env
    token = csrf(client, MANAGER)
    assert decide(client, MANAGER, token, decision="conditional", conditions="월별 매출 보고\r\n\r\n 담보 설정 ").status_code == 202
    assert queue.sent[-1][1]["decision"]["conditions"] == ["월별 매출 보고", "담보 설정"]
    assert queue.sent[-1][1]["decision"]["officer_id"] == "oid-manager"
    assert decide(client, MANAGER, token, decision="decline", reason="상환 여력 부족").status_code == 202
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
    assert "demo-cafe-001" in form_page and "demo-injection-005" in form_page
    token = csrf(client, OFFICER, "/applications/new")
    resp = client.post("/applications", headers=OFFICER, data={
        "csrf": token, "applicant_id": "demo-cafe-001", "requested_amount_krw": "30,000,000",
        "requested_tenor_months": "12"})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/applications/app-20261101-abc123"
    assert queue.sent == [("applications-start", {
        "application_id": "app-20261101-abc123", "applicant_id": "demo-cafe-001",
        "requested_amount_krw": 30_000_000, "requested_tenor_months": 12, "sla_hours": 24.0})]


@pytest.mark.parametrize("fields", [{"requested_amount_krw": "0"}, {"requested_tenor_months": "61"},
                                    {"applicant_id": ""}, {"requested_amount_krw": "lots"}])
def test_new_application_validation(env, fields):
    client, queue = env
    token = csrf(client, OFFICER, "/applications/new")
    form = {"csrf": token, "applicant_id": "demo-cafe-001", "requested_amount_krw": "30000000",
            "requested_tenor_months": "12"} | fields
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
```

- [ ] **Step 2: 실패 확인**

```powershell
.\.venv\Scripts\python -m pytest tests/console/test_app.py -q
```

Expected: 수집 단계 `ModuleNotFoundError: No module named 'console.app'`.

- [ ] **Step 3: 구현**

새 파일 `src/console/app.py`:

```python
"""심사역 콘솔(FastAPI + Jinja2 + htmx).

인증은 Container Apps 기본 인증(Easy Auth)이 맡고, 앱은 X-MS-CLIENT-PRINCIPAL의 앱 역할로 권한을 정한다.
결정과 신규 신청은 큐 메시지로만 오케스트레이터에 전달한다(콘솔은 Cosmos 읽기 전용).
"""

import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader
from pydantic import ValidationError

from console.auth import PRINCIPAL_HEADER, Principal, csrf_token, parse_principal, verify_csrf
from console.views import DECIDABLE, RawStore, detail_view, load_raw
from journey.models import KST, OfficerMessage, StartRequest
from journey.validation import validate_officer_decision

HERE = Path(__file__).parent
START_QUEUE = "applications-start"
DECISION_QUEUE = "officer-decisions"
MAX_FORM_BYTES = 16 * 1024
LIST_LIMIT = 50
DEMO_APPLICANTS = ("demo-cafe-001", "demo-mismatch-002", "demo-stress-003", "demo-thin-004", "demo-injection-005")
HTMX_SRC = "https://unpkg.com/htmx.org@2.0.11/dist/htmx.min.js"
HTMX_SRI = "sha384-2OatzQy1H+Zd/IIrjr1TcuDGqLXeHhbooAyJY1KdQMKnr4LZ22k31GBLdYKHmVjg"
# form-action은 넣지 않는다: Easy Auth 세션 만료 시 로그인 리디렉션(login.microsoftonline.com)을 막는다.
CSP = (
    "default-src 'self'; script-src 'self' https://unpkg.com; style-src 'self'; "
    "img-src 'self' data:; frame-ancestors 'none'"
)
TERMINAL = frozenset({"approved", "conditionally_approved", "declined", "closed_no_decision", "failed"})
STATUS_LABELS = {
    "received": "접수",
    "consent_granted": "동의 완료",
    "data_assembled": "데이터 수집",
    "data_incomplete": "데이터 부족",
    "underwritten": "심사 지표 산출",
    "assessed": "에이전트 평가",
    "awaiting_officer": "심사역 대기",
    "escalated": "SLA 초과",
    "approved": "승인",
    "conditionally_approved": "조건부 승인",
    "declined": "거절",
    "closed_no_decision": "미결 종료",
    "failed": "실패",
}
DECISION_ERRORS = {
    "invalid_payload": "입력 형식이 올바르지 않습니다.",
    "reason_required": "사유를 입력해야 합니다.",
    "decline_only": "정책 결과상 거절만 할 수 있습니다.",
    "amount_required": "승인 금액을 입력해야 합니다.",
    "amount_exceeds_policy": "승인 금액이 정책 한도를 넘습니다.",
    "conditions_required": "조건부 승인에는 조건이 한 줄 이상 필요합니다.",
}


class ApplicationStore(Protocol):
    def list_recent(self, limit: int) -> list[dict[str, Any]]: ...

    def get(self, application_id: str) -> dict[str, Any] | None: ...


class QueueSender(Protocol):
    def send(self, queue: str, message: dict[str, Any]) -> None: ...


def _new_application_id() -> str:
    return f"app-{datetime.now(KST):%Y%m%d}-{secrets.token_hex(3)}"


@dataclass
class ConsoleDeps:
    store: ApplicationStore
    raw: RawStore
    queue: QueueSender
    pseudonym_key: bytes
    new_application_id: Callable[[], str] = field(default=_new_application_id)


def _krw(value: Any) -> str:
    return f"{value:,}원" if isinstance(value, int) and not isinstance(value, bool) else "-"


def _templates() -> Environment:
    env = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=True)
    env.filters["krw"] = _krw
    env.filters["status_label"] = lambda s: STATUS_LABELS.get(s or "", s or "-")
    env.filters["path"] = lambda s: quote(str(s), safe="")
    env.globals.update(htmx_src=HTMX_SRC, htmx_sri=HTMX_SRI, demo_applicants=DEMO_APPLICANTS)
    return env


def _int(text: str) -> int | None:
    cleaned = text.replace(",", "").strip()
    return int(cleaned) if cleaned.isascii() and cleaned.isdigit() else None


async def _form(request: Request) -> dict[str, str]:
    if not request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded"):
        raise HTTPException(415, "form required")
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_FORM_BYTES:
            raise HTTPException(413, "form too large")
    try:
        parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True, max_num_fields=20, strict_parsing=False)
    except (UnicodeDecodeError, ValueError) as e:
        raise HTTPException(400, "bad form") from e
    return {k: v[0] for k, v in parsed.items()}


def create_app(deps: ConsoleDeps) -> FastAPI:
    app = FastAPI(title="AMA Lending officer console", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.deps = deps
    templates = _templates()
    # 레플리카가 여러 개여도 같은 토큰을 쓰도록 가명처리 키에서 파생한다.
    csrf_key = hmac.new(deps.pseudonym_key, b"console-csrf", hashlib.sha256).digest()
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if not request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def render(name: str, user: Principal | None, status_code: int = 200, **ctx: Any) -> HTMLResponse:
        csrf = csrf_token(csrf_key, user.user_id) if user else ""
        html = templates.get_template(name).render(user=user, csrf=csrf, **ctx)
        return HTMLResponse(html, status_code=status_code)

    def viewer(request: Request) -> Principal:
        user = parse_principal(request.headers.get(PRINCIPAL_HEADER))
        if user is None:
            raise HTTPException(401, "login required")
        if not user.can_view:
            raise HTTPException(403, "console role required")
        return user

    def decider(request: Request) -> Principal:
        user = viewer(request)
        if not user.can_decide:
            raise HTTPException(403, "CreditOfficer or CreditManager role required")
        return user

    async def checked_form(request: Request, user: Principal) -> dict[str, str]:
        form = await _form(request)
        if not verify_csrf(csrf_key, user.user_id, form.get("csrf")):
            raise HTTPException(403, "invalid csrf token")
        return form

    def detail(user: Principal, doc: dict[str, Any], status_code: int = 200, **ctx: Any) -> HTMLResponse:
        raw = load_raw(deps.raw, doc) if doc.get("consent") else None
        view = detail_view(doc, raw, deps.pseudonym_key, restore=user.can_restore)
        in_progress = doc.get("status") not in DECIDABLE | TERMINAL
        return render("detail.html", user, status_code, v=view, in_progress=in_progress, **ctx)

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        user = viewer(request)
        return render("list.html", user, rows=deps.store.list_recent(LIST_LIMIT))

    @app.get("/applications/rows", response_class=HTMLResponse)
    def rows(request: Request) -> HTMLResponse:
        user = viewer(request)
        return render("_rows.html", user, rows=deps.store.list_recent(LIST_LIMIT))

    @app.get("/applications/new", response_class=HTMLResponse)
    def new_form(request: Request) -> HTMLResponse:
        return render("new.html", decider(request))

    @app.post("/applications")
    async def submit(request: Request):
        user = decider(request)
        form = await checked_form(request, user)
        try:
            req = StartRequest(
                application_id=deps.new_application_id(),
                applicant_id=form.get("applicant_id", "").strip(),
                requested_amount_krw=_int(form.get("requested_amount_krw", "")),
                requested_tenor_months=_int(form.get("requested_tenor_months", "")),
            )
        except ValidationError:
            return render("new.html", user, 422, error="신청 정보가 올바르지 않습니다.", form=form)
        deps.queue.send(START_QUEUE, req.model_dump(mode="json"))
        return RedirectResponse(f"/applications/{quote(req.application_id, safe='')}", status_code=303)

    @app.get("/applications/{application_id}", response_class=HTMLResponse)
    def show(application_id: str, request: Request) -> HTMLResponse:
        user = viewer(request)
        doc = deps.store.get(application_id)
        if doc is None:
            return render("pending.html", user, 404, application_id=application_id)
        return detail(user, doc)

    @app.post("/applications/{application_id}/decision", response_class=HTMLResponse)
    async def decide(application_id: str, request: Request) -> HTMLResponse:
        user = decider(request)
        form = await checked_form(request, user)
        doc = deps.store.get(application_id)
        if doc is None:
            raise HTTPException(404, "application not found")
        if doc.get("status") not in DECIDABLE:
            raise HTTPException(409, "application is not awaiting an officer decision")
        kind = form.get("decision", "")
        amount_text = form.get("amount_krw", "").strip()
        amount = None if kind == "decline" or not amount_text else _int(amount_text)
        # 심사역 ID는 폼이 아니라 로그인 주체에서만 가져온다.
        raw_decision = {
            "decision": kind,
            "officer_id": user.user_id,
            "amount_krw": amount,
            "conditions": [c.strip() for c in form.get("conditions", "").splitlines() if c.strip()],
            "reason": form.get("reason", "").strip(),
        }
        if amount is None and amount_text and kind != "decline":
            decision, error = None, "invalid_payload"
        else:
            decision, error = validate_officer_decision(raw_decision, doc.get("policy"))
        if error:
            return detail(user, doc, 422, error=DECISION_ERRORS.get(error, error), error_code=error, form=form)
        message = OfficerMessage(application_id=application_id, decision=decision.model_dump(mode="json"))
        deps.queue.send(DECISION_QUEUE, message.model_dump(mode="json"))
        return render("sent.html", user, 202, application_id=application_id, decision=decision)

    return app
```

새 파일 `src/console/templates/base.html`:

```html
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="htmx-config" content='{"includeIndicatorStyles":false}'>
{% block head %}{% endblock %}
<title>{% block title %}AMA Lending 심사 콘솔{% endblock %}</title>
<link rel="stylesheet" href="/static/console.css">
<script src="{{ htmx_src }}" integrity="{{ htmx_sri }}" crossorigin="anonymous"></script>
</head>
<body>
<header>
  <a class="brand" href="/">AMA Lending 심사 콘솔</a>
  <span class="who">{{ user.name }} · {{ user.roles|sort|join(", ") }}</span>
  <a href="/.auth/logout">로그아웃</a>
</header>
<main>
{% block main %}{% endblock %}
</main>
</body>
</html>
```

새 파일 `src/console/templates/list.html`:

```html
{% extends "base.html" %}
{% block main %}
<h1>신청 목록</h1>
{% if user.can_decide %}<p><a class="button" href="/applications/new">새 신청</a></p>{% endif %}
<table>
  <thead><tr><th>신청 ID</th><th>신청인</th><th>상태</th><th>신청 금액</th><th>에이전트 권고</th><th>갱신 시각</th></tr></thead>
  <tbody hx-get="/applications/rows" hx-trigger="every 15s" hx-swap="innerHTML">
{% include "_rows.html" %}
  </tbody>
</table>
{% endblock %}
```

새 파일 `src/console/templates/_rows.html`:

```html
{% for r in rows %}
<tr>
  <td><a href="/applications/{{ r.id|path }}">{{ r.id }}</a></td>
  <td>{{ r.applicant_id }}</td>
  <td><span class="status s-{{ r.status }}">{{ r.status|status_label }}</span></td>
  <td>{{ r.requested_amount_krw|krw }}</td>
  <td>{{ r.recommendation or "-" }}</td>
  <td>{{ r.updated_at or "" }}</td>
</tr>
{% else %}
<tr><td colspan="6">신청이 없습니다.</td></tr>
{% endfor %}
```

새 파일 `src/console/templates/detail.html`:

```html
{% extends "base.html" %}
{% set d = v.doc %}
{% block head %}{% if in_progress %}<meta http-equiv="refresh" content="10">{% endif %}{% endblock %}
{% block title %}{{ d.application_id }} · AMA Lending 심사 콘솔{% endblock %}
{% block main %}
<p><a href="/">← 목록</a></p>
<h1>{{ d.application_id }} <span class="status s-{{ d.status }}">{{ d.status|status_label }}</span></h1>
<dl class="facts">
  <dt>신청인</dt><dd>{{ d.applicant_id }}</dd>
  <dt>신청 금액</dt><dd>{{ d.requested_amount_krw|krw }} / {{ d.requested_tenor_months }}개월</dd>
  <dt>접수</dt><dd>{{ d.received_at }}</dd>
  {% if d.sla %}<dt>SLA 마감</dt><dd>{{ d.sla.deadline }}{% if d.sla.escalated %} (초과){% endif %}</dd>{% endif %}
  {% if d.flags %}<dt>플래그</dt><dd>{% for f in d.flags %}<span class="chip warn">{{ f }}</span>{% endfor %}</dd>{% endif %}
  {% if d.error %}<dt>오류</dt><dd class="error">{{ d.error.stage }}: {{ d.error.message }}</dd>{% endif %}
</dl>
{% if v.pii == "restored" %}<p class="pii restored">실명 복원 표시 중(CreditOfficer·CreditManager 권한).</p>
{% elif v.pii == "unavailable" %}<p class="pii">원천 데이터가 없어 가명 토큰을 복원하지 못했습니다.</p>
{% else %}<p class="pii">가명 표시 중(감사자 권한은 실명을 볼 수 없습니다).</p>{% endif %}

<section>
<h2>타임라인</h2>
<ol class="timeline">{% for t in d.timeline %}<li><span class="status s-{{ t.status }}">{{ t.status|status_label }}</span> {{ t.at }}</li>{% endfor %}</ol>
</section>

{% if v.features %}
<section>
<h2>현금흐름 지표</h2>
<table class="kv">{% for k, val in v.features|dictsort %}<tr><th>M:{{ k }}</th><td>{{ val }}</td></tr>{% endfor %}</table>
</section>
{% endif %}

{% if v.policy %}
<section>
<h2>정책 판정 <small>{{ v.policy.rules_version }}</small></h2>
<p>판정 <strong>{{ v.policy.decision }}</strong> · 한도 {{ v.policy.max_amount_krw|krw }} · 금리 구간 {{ v.policy.rate_band or "-" }}</p>
<table>
  <thead><tr><th>코드</th><th>내용</th><th>관측값</th><th>기준</th></tr></thead>
  <tbody>{% for r in v.policy.reason_codes %}<tr><td>{{ r.code }}</td><td>{{ r.message }}</td><td>{{ r.observed }}</td><td>{{ r.threshold }}</td></tr>{% endfor %}</tbody>
</table>
</section>
{% endif %}

{% if v.assessment %}
{% set a = v.assessment %}
<section>
<h2>에이전트 평가 <small>권고: {{ a.recommendation }}</small></h2>
<p class="summary">{{ a.summary }}</p>
<h3>주장과 근거</h3>
<ul class="claims">
{% for c in v.claims %}
  <li>{{ c.text }}
    <div>{% for id, val in c.evidence %}<span class="chip{% if val is none %} warn{% endif %}">{{ id }} = {{ val if val is not none else "없음" }}</span>{% endfor %}
    {% for t in c.txn_ids %}<span class="chip txn">{{ t }}</span>{% endfor %}</div>
  </li>
{% endfor %}
</ul>
{% if a.proposed_structure %}{% set s = a.proposed_structure %}
<h3>제안 구조</h3>
<p>{{ s.amount_krw|krw }} · {{ s.tenor_months }}개월 · {{ s.schedule }}{% if s.reduced_payment_months %} · 감액 월 {{ s.reduced_payment_months|join(", ") }}{% endif %}</p>
<p>{{ s.rationale }}</p>
{% endif %}
{% if a.counterfactuals %}
<h3>개선 조건(반사실)</h3>
<ul>{% for cf in a.counterfactuals %}<li>{{ cf.metric_id }}: {{ cf.current }} → {{ cf.target }} — {{ cf.explanation }}</li>{% endfor %}</ul>
{% endif %}
{% if a.review %}<p class="review">검토 에이전트: {{ "통과" if a.review.passed else "미통과" }} ({{ a.review.rounds }}회){% for i in a.review.issues %} · {{ i }}{% endfor %}</p>{% endif %}
</section>
{% endif %}

{% if v.transactions %}
<section>
<h2>근거 거래</h2>
<table>
  <thead><tr><th>거래 ID</th><th>일자</th><th>금액</th><th>적요</th></tr></thead>
  <tbody>{% for t in v.transactions %}<tr><td>{{ t.txn_id }}</td><td>{{ t.booked_on }}</td><td>{{ t.amount_krw|krw }}</td><td>{{ t.description }}</td></tr>{% endfor %}</tbody>
</table>
</section>
{% endif %}

<section>
<h2>심사역 결정</h2>
{% if d.officer_decision %}
<p>{{ d.officer_decision.decision }} · {{ d.officer_decision.amount_krw|krw }} · {{ d.officer_decision.officer_id }}</p>
{% if d.officer_decision.conditions %}<ul>{% for c in d.officer_decision.conditions %}<li>{{ c }}</li>{% endfor %}</ul>{% endif %}
{% if d.officer_decision.reason %}<p>{{ d.officer_decision.reason }}</p>{% endif %}
{% endif %}
{% if d.decision_attempts %}
<details><summary>거부된 결정 시도 {{ d.decision_attempts|length }}건</summary>
<ul>{% for at in d.decision_attempts %}<li>{{ at.at }} {{ at.error }}</li>{% endfor %}</ul></details>
{% endif %}
{% if v.decidable and user.can_decide %}
{% if error %}<p class="error" role="alert">{{ error }}{% if error_code %} ({{ error_code }}){% endif %}</p>{% endif %}
<form method="post" action="/applications/{{ d.application_id|path }}/decision" class="decision">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <fieldset>
    <legend>결정</legend>
    {% set chosen = form.decision if form else "approve" %}
    {% for value, label in (("approve", "승인"), ("conditional", "조건부 승인"), ("decline", "거절")) %}
    <label><input type="radio" name="decision" value="{{ value }}"{% if chosen == value %} checked{% endif %}> {{ label }}</label>
    {% endfor %}
  </fieldset>
  <label>승인 금액(원, 한도 {{ v.max_amount_krw|krw }})
    <input name="amount_krw" inputmode="numeric" value="{{ form.amount_krw if form else ((v.assessment.proposed_structure.amount_krw if v.assessment and v.assessment.proposed_structure) or v.max_amount_krw or '') }}"></label>
  <label>조건(한 줄에 하나) <textarea name="conditions" rows="3">{{ form.conditions if form else "" }}</textarea></label>
  <label>사유 <textarea name="reason" rows="3">{{ form.reason if form else "" }}</textarea></label>
  <button type="submit">결정 보내기</button>
</form>
{% elif not d.officer_decision %}
<p>{% if v.decidable %}감사자 권한은 결정할 수 없습니다.{% else %}심사역 결정 단계가 아닙니다.{% endif %}</p>
{% endif %}
</section>
{% endblock %}
```

새 파일 `src/console/templates/new.html`:

```html
{% extends "base.html" %}
{% block main %}
<h1>새 신청</h1>
{% if error %}<p class="error" role="alert">{{ error }}</p>{% endif %}
<form method="post" action="/applications">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <label>신청인(데모)
    <select name="applicant_id">
    {% for a in demo_applicants %}<option value="{{ a }}"{% if form and form.applicant_id == a %} selected{% endif %}>{{ a }}</option>{% endfor %}
    </select>
  </label>
  <label>신청 금액(원) <input name="requested_amount_krw" inputmode="numeric" required value="{{ form.requested_amount_krw if form else '30000000' }}"></label>
  <label>기간(개월, 1~60) <input name="requested_tenor_months" inputmode="numeric" required value="{{ form.requested_tenor_months if form else '12' }}"></label>
  <button type="submit">제출</button>
</form>
<p><a href="/">목록으로</a></p>
{% endblock %}
```

새 파일 `src/console/templates/pending.html`:

```html
{% extends "base.html" %}
{% block head %}<meta http-equiv="refresh" content="5">{% endblock %}
{% block main %}
<h1>{{ application_id }}</h1>
<p>신청 문서가 아직 없습니다. 오케스트레이터가 큐 메시지를 처리하면 자동으로 표시됩니다(5초마다 새로 고침).</p>
<p><a href="/">목록으로</a></p>
{% endblock %}
```

새 파일 `src/console/templates/sent.html`:

```html
{% extends "base.html" %}
{% block main %}
<h1>{{ application_id }}</h1>
<p class="ok">결정({{ decision.decision }})을 큐에 보냈습니다. 오케스트레이터가 정책 한도를 다시 검증한 뒤 상태를 바꿉니다.</p>
<p><a href="/applications/{{ application_id|path }}">신청 상세로</a> · <a href="/">목록으로</a></p>
{% endblock %}
```

새 파일 `src/console/static/console.css`:

```css
:root { --fg: #1b1f24; --muted: #57606a; --line: #d0d7de; --accent: #0969da; --warn: #9a6700; --bad: #cf222e; --good: #1a7f37; }
* { box-sizing: border-box; }
body { margin: 0; font: 15px/1.5 system-ui, "Malgun Gothic", sans-serif; color: var(--fg); }
header { display: flex; gap: 1rem; align-items: center; padding: .6rem 1.2rem; border-bottom: 1px solid var(--line); background: #f6f8fa; }
header .brand { font-weight: 600; color: var(--fg); text-decoration: none; margin-right: auto; }
header .who { color: var(--muted); }
main { max-width: 1100px; margin: 0 auto; padding: 1rem 1.2rem 3rem; }
a { color: var(--accent); }
table { border-collapse: collapse; width: 100%; margin: .5rem 0 1rem; }
th, td { border-bottom: 1px solid var(--line); padding: .35rem .5rem; text-align: left; vertical-align: top; }
table.kv th { width: 40%; font-weight: 500; color: var(--muted); }
section { margin-top: 1.5rem; }
h1 small, h2 small { color: var(--muted); font-weight: 400; font-size: .8em; }
dl.facts { display: grid; grid-template-columns: max-content 1fr; gap: .2rem 1rem; }
dl.facts dt { color: var(--muted); }
dl.facts dd { margin: 0; }
.status { display: inline-block; padding: 0 .5rem; border-radius: 1rem; background: #eaeef2; font-size: .85em; }
.s-awaiting_officer, .s-escalated { background: #fff8c5; color: var(--warn); }
.s-approved, .s-conditionally_approved { background: #dafbe1; color: var(--good); }
.s-declined, .s-failed, .s-closed_no_decision { background: #ffebe9; color: var(--bad); }
.chip { display: inline-block; margin: .15rem .25rem 0 0; padding: 0 .4rem; border: 1px solid var(--line); border-radius: .3rem; font: 12px ui-monospace, monospace; }
.chip.warn { border-color: var(--warn); color: var(--warn); }
.chip.txn { background: #f6f8fa; }
.claims li { margin-bottom: .5rem; }
.pii { padding: .4rem .6rem; border-left: 3px solid var(--line); color: var(--muted); }
.pii.restored { border-color: var(--warn); }
.error { color: var(--bad); font-weight: 500; }
.ok { color: var(--good); font-weight: 500; }
.timeline { padding-left: 1.2rem; }
form label { display: block; margin: .5rem 0; }
form input[name], form select, form textarea { display: block; width: 100%; max-width: 32rem; padding: .3rem; font: inherit; }
form input[type=radio] { display: inline; width: auto; }
fieldset { border: 1px solid var(--line); max-width: 32rem; }
button, .button { display: inline-block; padding: .4rem 1rem; border: 0; border-radius: .3rem; background: var(--accent); color: #fff; font: inherit; text-decoration: none; cursor: pointer; }
```

- [ ] **Step 4: 통과 확인**

```powershell
.\.venv\Scripts\python -m pytest tests/console/test_app.py -q; .\.venv\Scripts\python -m pytest -q
```

Expected: `30 passed`, 전체 `414 passed, 74 skipped`(경고 1건은 Starlette TestClient의 httpx 사용 안내로 무해).

- [ ] **Step 5: 커밋**

```powershell
git add src/console/app.py src/console/templates/base.html src/console/templates/list.html src/console/templates/_rows.html src/console/templates/detail.html src/console/templates/new.html src/console/templates/pending.html src/console/templates/sent.html src/console/static/console.css tests/console/test_app.py
git commit -m "console: officer web app with decisions via queue and security headers" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>" -m "Copilot-Session: 05f1ca1b-6073-417d-bfb1-6b546f82cb58"
```

---

### Task 4: Azure 어댑터와 콘솔 이미지

Azure 어댑터와 콘솔 컨테이너 이미지.

**설계 메모**
- `CosmosApplications`: 목록은 교차 파티션 `SELECT TOP @limit ... ORDER BY c.updated_at DESC` 투영, 상세는 `read_item`(파티션 = 신청 ID), 404만 None.
- `BlobRawStore.get_json`: 없으면 None. `StorageQueueSender.send`: base64 없이 UTF-8 JSON 원문(Functions host.json `messageEncoding: none`과 맞춤).
- `build_deps(env)`: `AZURE_CLIENT_ID`·`COSMOS_ENDPOINT`·`COSMOS_DATABASE`·`DATA_BLOB_ENDPOINT`·`QUEUE_ENDPOINT` 필수, 관리 ID 자격 증명 하나로 네 서비스에 연결한다. 가명 키는 Key Vault `pseudonym-key`(`PSEUDONYM_KEY` 환경 변수가 있으면 그것, 로컬용).
- Azure SDK는 지연 import한다(빠른 테스트 venv에 SDK 없음). `main.py`가 `create_app(build_deps())`.
- Dockerfile은 `shared`, `agents`, 오케스트레이터 `journey/{__init__,models,validation}.py`, `console`만 복사한다. `.dockerignore`가 `src/agents`·`src/orchestrator`를 빼고 있어 `az acr build`가 실패할 상태였으므로 고친다(`src/agents` 줄 삭제, journey 세 파일 `!` 예외). 테스트는 `.dockerignore`를 적용한 빌드 컨텍스트로 `/app`을 재현해 import한다.

**Files:**
- Create: `src/console/adapters.py`
- Create: `src/console/main.py`
- Create: `src/console/Dockerfile`
- Create: `requirements-console.txt`
- Modify: `.dockerignore`
- Create: `tests/console/test_adapters.py`
- Create: `tests/console/test_dockerfile.py`

- [ ] **Step 1: 실패하는 테스트 작성**

새 파일 `tests/console/test_adapters.py`:

```python
import json

import pytest

from console.adapters import LIST_QUERY, CosmosApplications, StorageQueueSender, build_deps


class FakeContainer:
    def __init__(self, items=None, missing=False):
        self.items = items or []
        self.missing = missing
        self.calls = []

    def query_items(self, query, parameters, enable_cross_partition_query):
        self.calls.append(("query", query, parameters, enable_cross_partition_query))
        return iter(self.items)

    def read_item(self, item, partition_key):
        self.calls.append(("read", item, partition_key))
        if self.missing:
            raise NotFound()
        return {"id": item}


class NotFound(Exception):
    status_code = 404


def test_list_recent_queries_projection_across_partitions():
    container = FakeContainer([{"id": "app-1"}])
    assert CosmosApplications(container).list_recent(50) == [{"id": "app-1"}]
    kind, query, params, cross = container.calls[0]
    assert (kind, query, cross) == ("query", LIST_QUERY, True)
    assert params == [{"name": "@limit", "value": 50}]
    assert "ORDER BY c.updated_at DESC" in query and "c.assessment.recommendation AS recommendation" in query
    assert "c.assessment " not in query.replace("c.assessment.recommendation", "")


def test_get_reads_by_application_partition_and_maps_missing_to_none():
    container = FakeContainer()
    assert CosmosApplications(container).get("app-1") == {"id": "app-1"}
    assert container.calls == [("read", "app-1", "app-1")]
    assert CosmosApplications(FakeContainer(missing=True)).get("app-x") is None


def test_queue_sender_sends_plain_utf8_json():
    sent = {}

    class FakeQueueClient:
        def __init__(self, name):
            self.name = name

        def send_message(self, content):
            sent[self.name] = content

    StorageQueueSender(FakeQueueClient).send("officer-decisions", {"reason": "상환 여력 <부족>"})
    assert json.loads(sent["officer-decisions"]) == {"reason": "상환 여력 <부족>"}
    assert "상환" in sent["officer-decisions"]


def test_build_deps_wires_env(monkeypatch):
    made = {}

    class Recorder:
        def __init__(self, *args, **kwargs):
            made[type(self).__name__] = (args, kwargs)

    for name in ("ManagedIdentityCredential", "CosmosApplications", "BlobRawStore", "StorageQueueSender"):
        monkeypatch.setattr(f"console.adapters.{name}", type(name, (Recorder,), {}), raising=True)
    monkeypatch.setattr("console.adapters._cosmos_container", lambda endpoint, db, cred: ("container", endpoint, db))
    monkeypatch.setattr("console.adapters._queue_factory", lambda endpoint, cred: ("queues", endpoint))
    env = {
        "AZURE_CLIENT_ID": "cid", "COSMOS_ENDPOINT": "https://cos", "COSMOS_DATABASE": "lending",
        "DATA_BLOB_ENDPOINT": "https://blob", "QUEUE_ENDPOINT": "https://queue",
        "PSEUDONYM_KEY": "k" * 32,
    }
    deps = build_deps(env)
    assert made["ManagedIdentityCredential"][1] == {"client_id": "cid"}
    assert made["CosmosApplications"][0] == (("container", "https://cos", "lending"),)
    assert made["BlobRawStore"][0][0] == "https://blob"
    assert made["StorageQueueSender"][0] == (("queues", "https://queue"),)
    assert deps.pseudonym_key == b"k" * 32


def test_build_deps_requires_endpoints():
    with pytest.raises(KeyError):
        build_deps({"AZURE_CLIENT_ID": "cid", "PSEUDONYM_KEY": "k" * 32})
```

새 파일 `tests/console/test_dockerfile.py`:

```python
import fnmatch
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "src" / "console" / "Dockerfile"


def _dockerignored(rel: str) -> bool:
    """.dockerignore 규칙(마지막 일치 우선, `!` 예외, 상위 디렉터리 일치 포함)으로 제외 여부를 판단한다."""
    parts = rel.split("/")
    candidates = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
    ignored = False
    for raw in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines():
        pattern = raw.strip()
        if not pattern or pattern.startswith("#"):
            continue
        negate = pattern.startswith("!")
        pattern = pattern.lstrip("!").strip("/")
        if pattern.startswith("**/"):
            tail = pattern.removeprefix("**/")
            hit = any(fnmatch.fnmatchcase(c.rsplit("/", 1)[-1], tail) for c in candidates)
        else:
            hit = any(fnmatch.fnmatchcase(c, pattern) for c in candidates)
        if hit:
            ignored = not negate
    return ignored


def _copy(src: Path, dest: Path) -> None:
    """빌드 컨텍스트(.dockerignore 적용)에 남는 파일만 복사한다."""
    files = [src] if src.is_file() else [f for f in src.rglob("*") if f.is_file()]
    for f in files:
        if _dockerignored(f.relative_to(ROOT).as_posix()):
            continue
        target = dest / f.name if src.is_file() else dest / f.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(f, target)


def test_dockerfile_copies_everything_the_console_imports(tmp_path):
    """Dockerfile의 COPY 목록과 .dockerignore만으로 /app을 재현해 콘솔 모듈을 import한다(Azure SDK는 지연 import)."""
    app = tmp_path / "app"
    for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        m = re.match(r"COPY (src/\S.*) (/app/\S*)$", line)
        if not m:
            continue
        *sources, target = m.group(1).split() + [m.group(2)]
        dest = app / target.removeprefix("/app/").rstrip("/")
        for src in sources:
            _copy(ROOT / src, dest)
    # 편집 설치(.pth)가 저장소 src를 sys.path에 넣으므로, /app 재현본 말고 다른 소스 경로는 지운다.
    code = (
        "import sys, pathlib; "
        f"sys.path[:] = [p for p in sys.path if p == {str(app)!r} or not (pathlib.Path(p) / 'shared').is_dir()]; "
        "import console.app, console.adapters, console.views; "
        "assert not [m for m in sys.modules if m.startswith('azure')], 'azure imported eagerly'"
    )
    env = {**os.environ, "PYTHONPATH": str(app)}
    result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (app / "console" / "templates" / "detail.html").exists()
    assert (app / "console" / "static" / "console.css").exists()
    assert not (app / "journey" / "activities.py").exists()


def test_dockerignore_keeps_other_images_lean():
    # 콘솔에 필요한 journey 파일만 예외로 두고, 오케스트레이터·ops·테스트는 계속 빌드 컨텍스트에서 뺀다.
    assert _dockerignored("src/orchestrator/function_app.py")
    assert _dockerignored("src/orchestrator/journey/activities.py")
    assert _dockerignored("src/ops/cli.py")
    assert _dockerignored("tests/console/test_app.py")
    assert _dockerignored("src/shared/__pycache__/x.pyc")
    assert not _dockerignored("src/orchestrator/journey/models.py")
    assert not _dockerignored("src/agents/features.py")
```

- [ ] **Step 2: 실패 확인**

```powershell
.\.venv\Scripts\python -m pytest tests/console/test_adapters.py tests/console/test_dockerfile.py -q
```

Expected: 수집 단계 `ModuleNotFoundError: No module named 'console.adapters'`.

- [ ] **Step 3: 구현**

새 파일 `src/console/adapters.py`:

```python
"""콘솔의 Azure 어댑터(관리 ID). Azure SDK는 지연 import한다.

- Cosmos `applications`: 읽기 전용(Built-in Data Reader)
- Blob 원천 데이터: 읽기 전용(실명 복원용)
- Storage Queue: applications-start·officer-decisions에 보내기만 한다(Functions host.json messageEncoding=none)
"""

import json
import os
from collections.abc import Callable, Mapping
from typing import Any

from console.app import ConsoleDeps

APPLICATIONS = "applications"
PSEUDONYM_SECRET = "pseudonym-key"
LIST_QUERY = (
    "SELECT TOP @limit c.id, c.applicant_id, c.status, c.requested_amount_krw, c.updated_at, "
    "c.assessment.recommendation AS recommendation FROM c ORDER BY c.updated_at DESC"
)


class CosmosApplications:
    def __init__(self, container: Any) -> None:
        self._container = container

    def list_recent(self, limit: int) -> list[dict[str, Any]]:
        items = self._container.query_items(
            query=LIST_QUERY, parameters=[{"name": "@limit", "value": limit}], enable_cross_partition_query=True
        )
        return list(items)

    def get(self, application_id: str) -> dict[str, Any] | None:
        try:
            return self._container.read_item(item=application_id, partition_key=application_id)
        except Exception as e:  # noqa: BLE001 - SDK를 import하지 않고 404만 None으로 바꾼다
            if getattr(e, "status_code", None) == 404:
                return None
            raise


class BlobRawStore:
    def __init__(self, account_url: str, credential: Any) -> None:
        from azure.storage.blob import BlobServiceClient

        self._svc = BlobServiceClient(account_url, credential=credential)

    def get_json(self, container: str, name: str) -> Any | None:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            data = self._svc.get_blob_client(container, name).download_blob().readall()
        except ResourceNotFoundError:
            return None
        return json.loads(data)


class StorageQueueSender:
    def __init__(self, queue_client: Callable[[str], Any]) -> None:
        self._queue_client = queue_client

    def send(self, queue: str, message: dict[str, Any]) -> None:
        # base64 인코딩 없이 JSON 원문을 보낸다. SDK가 XML 본문에 넣을 때 이스케이프한다.
        self._queue_client(queue).send_message(json.dumps(message, ensure_ascii=False))


def _cosmos_container(endpoint: str, database: str, credential: Any) -> Any:
    from azure.cosmos import CosmosClient

    return CosmosClient(endpoint, credential=credential).get_database_client(database).get_container_client(
        APPLICATIONS
    )


def _queue_factory(endpoint: str, credential: Any) -> Callable[[str], Any]:
    from azure.storage.queue import QueueServiceClient

    service = QueueServiceClient(endpoint, credential=credential)
    return service.get_queue_client


def load_pseudonym_key(env: Mapping[str, str], credential: Any) -> bytes:
    if value := env.get("PSEUDONYM_KEY"):
        return value.encode()
    from azure.keyvault.secrets import SecretClient

    return SecretClient(env["KEYVAULT_URI"], credential).get_secret(PSEUDONYM_SECRET).value.encode()


def ManagedIdentityCredential(**kwargs: Any) -> Any:  # noqa: N802 - 테스트에서 바꿔 끼우는 지연 import 래퍼
    from azure.identity import ManagedIdentityCredential as Credential

    return Credential(**kwargs)


REQUIRED_ENV = ("AZURE_CLIENT_ID", "COSMOS_ENDPOINT", "COSMOS_DATABASE", "DATA_BLOB_ENDPOINT", "QUEUE_ENDPOINT")


def build_deps(env: Mapping[str, str] = os.environ) -> ConsoleDeps:
    cfg = {name: env[name] for name in REQUIRED_ENV}
    credential = ManagedIdentityCredential(client_id=cfg["AZURE_CLIENT_ID"])
    return ConsoleDeps(
        store=CosmosApplications(_cosmos_container(cfg["COSMOS_ENDPOINT"], cfg["COSMOS_DATABASE"], credential)),
        raw=BlobRawStore(cfg["DATA_BLOB_ENDPOINT"], credential),
        queue=StorageQueueSender(_queue_factory(cfg["QUEUE_ENDPOINT"], credential)),
        pseudonym_key=load_pseudonym_key(env, credential),
    )
```

새 파일 `src/console/main.py`:

```python
"""uvicorn 진입점: uvicorn console.main:app"""

from console.adapters import build_deps
from console.app import create_app

app = create_app(build_deps())
```

새 파일 `src/console/Dockerfile`:

```dockerfile
# 심사역 콘솔. 빌드 컨텍스트는 저장소 루트입니다: az acr build -f src/console/Dockerfile .
# journey는 models·validation만 복사합니다(둘 다 pydantic만 쓰고, 오케스트레이터 런타임은 필요 없음).
FROM mcr.microsoft.com/azurelinux/base/python:3.12
RUN python3 -m venv /opt/venv
COPY requirements-console.txt /tmp/requirements.txt
RUN /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt
COPY src/shared /app/shared
COPY src/agents /app/agents
COPY src/orchestrator/journey/__init__.py src/orchestrator/journey/models.py src/orchestrator/journey/validation.py /app/journey/
COPY src/console /app/console
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1
WORKDIR /app
USER 65532
EXPOSE 8000
CMD ["/opt/venv/bin/uvicorn", "console.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

새 파일 `requirements-console.txt`:

```text
# 심사역 콘솔 컨테이너 의존성(고정). 로컬 개발은 pyproject.toml을 씁니다.
fastapi==0.162.0
starlette==1.9.0
uvicorn==0.54.0
pydantic==2.14.1
jinja2==3.1.6
httpx==0.28.1
azure-identity==1.27.0
azure-cosmos==4.17.1
azure-storage-blob==12.31.1
azure-storage-queue==12.18.1
azure-keyvault-secrets==4.12.0
```

`.dockerignore` 수정(`git apply`로 적용 가능한 diff):

```diff
diff --git a/.dockerignore b/.dockerignore
index e777d28..ac7c925 100644
--- a/.dockerignore
+++ b/.dockerignore
@@ -8,5 +8,8 @@ docs
 infra
 tests
 src/orchestrator
-src/agents
+# 심사역 콘솔 이미지는 journey 모델·검증만 씁니다(src/console/Dockerfile).
+!src/orchestrator/journey/__init__.py
+!src/orchestrator/journey/models.py
+!src/orchestrator/journey/validation.py
 src/ops
```

- [ ] **Step 4: 통과 확인**

```powershell
.\.venv\Scripts\python -m pytest tests/console/test_adapters.py tests/console/test_dockerfile.py -q; .\.venv\Scripts\python -m pytest -q
```

Expected: `7 passed`, 전체 `421 passed, 74 skipped`(경고 1건은 Starlette TestClient의 httpx 사용 안내로 무해).

- [ ] **Step 5: 커밋**

```powershell
git add src/console/adapters.py src/console/main.py src/console/Dockerfile requirements-console.txt .dockerignore tests/console/test_adapters.py tests/console/test_dockerfile.py
git commit -m "console: Azure adapters, container image and build context" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>" -m "Copilot-Session: 05f1ca1b-6073-417d-bfb1-6b546f82cb58"
```

---

### Task 5: 인프라: 콘솔 ID·Entra 앱 등록·IP 허용·Easy Auth

인프라: 콘솔 관리 ID와 최소 권한, Entra 앱 등록(Graph 확장), external 환경의 콘솔 앱(IP 허용 + Easy Auth)(스펙 §2.2, §4.2, §7).

**설계 메모**
- `identities.bicep`: `id-console` 추가. 권한은 KV 비밀 읽기, 데이터 Blob 읽기, Function Storage 큐 보내기, Cosmos Built-in Data Reader(데이터베이스 범위), ACR pull만.
- `console-auth.bicep`(`extension microsoftGraphV1`): 앱 등록(`uniqueName`으로 멱등, 앱 역할 3개, web redirect URI, `enableIdTokenIssuance`), 서비스 주체 `appRoleAssignmentRequired: true`, 운영자(`operatorPrincipalId`)에게 `CreditOfficer` 할당. `infra/bicepconfig.json`이 확장을 등록한다.
- `container-app.bicep`: `ipAllowList`(ingress `ipSecurityRestrictions`), `authClientId`가 있으면 `authConfigs/current`(로그인 강제, v2.0 발급자, audience = 앱 ID). 내부 앱은 기본값(빈 값)이라 변화 없음. 쓰지 않던 `fqdn` 출력 제거.
- `resources.bicep`: 콘솔 FQDN은 `ca-console.<cae-web 기본 도메인>`으로 계산해 redirect URI에 쓴다(앱 출력에 의존하면 auth ↔ app 순환). 허용 IP는 `CONSOLE_IP`/32, 비면 `192.0.2.0/32`(fail-closed). 출력 `CONSOLE_APP_NAME`, `CONSOLE_URL`, `CONSOLE_CLIENT_ID`.
- 테스트: PSRule 펼침은 Graph 리소스를 빼고 관리 ID `principalId`가 모두 같으므로, Graph는 `console-auth.bicep` 컴파일 JSON, Easy Auth는 `container-app.bicep`을 `console-app.bicepparam`으로 펼친 결과, 콘솔 역할은 컴파일된 `resources.bicep`의 모듈 파라미터 식에서 확인한다. 인프라 테스트는 importlib 모드라 도우미는 fixture(`console_app`)로 둔다.

**Files:**
- Create: `infra/bicepconfig.json`
- Create: `infra/modules/console-auth.bicep`
- Modify: `infra/modules/container-app.bicep`
- Modify: `infra/modules/identities.bicep`
- Modify: `infra/resources.bicep`
- Modify: `infra/main.bicep`
- Modify: `infra/main.bicepparam`
- Modify: `infra/tests/open.bicepparam`
- Modify: `infra/tests/variant.bicepparam`
- Modify: `tests/infra/conftest.py`
- Create: `infra/tests/console-app.bicepparam`
- Create: `tests/infra/test_console.py`
- Modify: `tests/infra/test_platform.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/infra/conftest.py` 수정(`git apply`로 적용 가능한 diff):

```diff
diff --git a/tests/infra/conftest.py b/tests/infra/conftest.py
index 9298ab0..dbc20c6 100644
--- a/tests/infra/conftest.py
+++ b/tests/infra/conftest.py
@@ -25,7 +25,8 @@ def _require_opt_in():
         pytest.skip("RUN_INFRA_TESTS=1 일 때만 실행합니다")
 
 
-def _expand(param_file: Path, extra_env: dict[str, str] | None = None) -> list[dict]:
+def _expand(param_file: Path, extra_env: dict[str, str] | None = None,
+            template: Path = INFRA / "main.bicep") -> list[dict]:
     pwsh = shutil.which("pwsh")
     if pwsh is None:
         pytest.fail("PowerShell 7(pwsh)이 필요합니다")
@@ -33,7 +34,7 @@ def _expand(param_file: Path, extra_env: dict[str, str] | None = None) -> list[d
         out = Path(tmp) / "expanded.json"
         proc = subprocess.run(
             [pwsh, "-NoProfile", "-NonInteractive", "-File", str(EXPAND),
-             "-TemplateFile", str(INFRA / "main.bicep"),
+             "-TemplateFile", str(template),
              "-ParamFile", str(param_file), "-OutFile", str(out)],
             capture_output=True, text=True, encoding="utf-8",
             env={**os.environ, **(extra_env or {})},
@@ -126,6 +127,13 @@ def variant() -> Expanded:
     return _cached("variant")
 
 
+@pytest.fixture(scope="session")
+def console_app() -> Expanded:
+    """콘솔용 container-app 모듈만 펼친 결과(Graph appId를 아는 상태의 authConfigs 확인용)."""
+    return Expanded(_expand(INFRA / "tests" / "console-app.bicepparam",
+                            template=INFRA / "modules" / "container-app.bicep"))
+
+
 @pytest.fixture(scope="session")
 def expand_azd():
     """azd용 infra/main.bicepparam을 주어진 환경 변수로 펼칩니다."""
```

새 파일 `infra/tests/console-app.bicepparam`:

```bicep
// tests/infra/test_console.py 전용: 콘솔용 container-app 모듈만 펼쳐 Easy Auth 설정을 확인합니다.
// (main.bicep을 펼치면 Graph 앱의 appId를 알 수 없어 authConfigs가 빠집니다.)
using '../modules/container-app.bicep'

param name = 'ca-console'
param location = 'koreacentral'
param tags = {}
param serviceName = 'console'
param environmentId = '/subscriptions/ffffffff-ffff-ffff-ffff-ffffffffffff/resourceGroups/rg-amatest/providers/Microsoft.App/managedEnvironments/cae-web-amatest'
param identityId = '/subscriptions/ffffffff-ffff-ffff-ffff-ffffffffffff/resourceGroups/rg-amatest/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-console-amatest'
param identityClientId = 'ffffffff-ffff-ffff-ffff-ffffffffffff'
param registryServer = 'acramatest.azurecr.io'
param ipAllowList = ['20.30.40.50/32']
param authClientId = '22222222-2222-2222-2222-222222222222'
```

새 파일 `tests/infra/test_console.py`:

```python
"""심사역 콘솔(P4b) IaC: 외부 환경 Container App, IP 허용 목록, Easy Auth, Graph 앱 등록, 최소 권한."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

INFRA = Path(__file__).resolve().parents[2] / "infra"
CA = "Microsoft.App/containerApps"
COSMOS_READER = "00000000-0000-0000-0000-000000000001"
BICEP = shutil.which("bicep") or str(Path.home() / ".azure" / "bin" / "bicep.exe")


def _deployment(exp, name: str) -> dict:
    [d] = [r for r in exp.raw if r["type"] == "Microsoft.Resources/deployments" and r["name"] == name]
    return {k: v["value"] for k, v in d["properties"]["parameters"].items()}


def _compile(path: Path) -> dict:
    proc = subprocess.run([BICEP, "build", str(path), "--stdout"], capture_output=True, text=True,
                          encoding="utf-8", check=True)
    return json.loads(proc.stdout)


def _resources(template: dict) -> list[dict]:
    res = template["resources"]
    return list(res.values()) if isinstance(res, dict) else res


def test_console_runs_in_web_environment_with_own_identity(closed):
    app = closed.one(CA, "ca-console")
    assert app["properties"]["environmentId"].endswith("/managedEnvironments/cae-web-amatest")
    assert list(app["identity"]["userAssignedIdentities"]) == [
        next(i["id"] for i in closed.of_type("Microsoft.ManagedIdentity/userAssignedIdentities")
             if i["name"] == "id-console-amatest")]
    ingress = app["properties"]["configuration"]["ingress"]
    assert ingress["external"] is True and ingress["targetPort"] == 8000
    assert ingress["allowInsecure"] is False
    env = {e["name"] for e in app["properties"]["template"]["containers"][0]["env"]}
    assert env == {"AZURE_CLIENT_ID", "COSMOS_ENDPOINT", "COSMOS_DATABASE", "DATA_BLOB_ENDPOINT",
                   "QUEUE_ENDPOINT", "KEYVAULT_URI"}  # 가명 키는 Key Vault에서만 읽습니다.
    queue = next(e["value"] for e in app["properties"]["template"]["containers"][0]["env"]
                 if e["name"] == "QUEUE_ENDPOINT")
    assert re.fullmatch(r"https://stfn[0-9a-z]+\.queue\.core\.windows\.net/", queue)


def test_console_ip_allow_list_fails_closed(closed, opened):
    # 운영자 IP가 없으면 TEST-NET 주소만 허용해 아무도 들어오지 못하게 합니다.
    rules = closed.one(CA, "ca-console")["properties"]["configuration"]["ingress"]["ipSecurityRestrictions"]
    assert rules == [{"name": "allow-0", "ipAddressRange": "192.0.2.0/32", "action": "Allow"}]
    rules = opened.one(CA, "ca-console")["properties"]["configuration"]["ingress"]["ipSecurityRestrictions"]
    assert rules == [{"name": "allow-0", "ipAddressRange": "20.30.40.50/32", "action": "Allow"}]


def test_vnet_only_apps_have_no_ip_rules_or_auth(closed):
    for name in ("ca-policy", "ca-mockapi"):
        ingress = closed.one(CA, name)["properties"]["configuration"]["ingress"]
        assert ingress.get("ipSecurityRestrictions") in (None, []), name
    assert not [r for r in closed.of_type(f"{CA}/authConfigs") if not r["name"].startswith("ca-console")]


def test_console_image_param(closed, variant):
    assert _deployment(closed, "ca-console")["image"] == ""
    assert _deployment(variant, "ca-console")["image"] == "acramatest.azurecr.io/console:v1"


def test_console_auth_registration_wiring(closed):
    params = _deployment(closed, "console-auth")
    # PSRule은 환경 기본 도메인을 모르므로 앞뒤만 확인합니다.
    assert params["redirectUri"].startswith("https://ca-console.")
    assert params["redirectUri"].endswith("/.auth/login/aad/callback")
    assert params["operatorPrincipalId"] == "11111111-1111-1111-1111-111111111111"


def test_console_easy_auth_requires_entra_login(console_app):
    [auth] = console_app.of_type(f"{CA}/authConfigs")
    props = auth["properties"]
    assert props["platform"]["enabled"] is True
    assert props["globalValidation"] == {"unauthenticatedClientAction": "RedirectToLoginPage",
                                         "redirectToProvider": "azureactivedirectory"}
    aad = props["identityProviders"]["azureActiveDirectory"]
    assert aad["registration"]["clientId"] == "22222222-2222-2222-2222-222222222222"
    assert aad["registration"]["openIdIssuer"].startswith("https://login.microsoftonline.com/")
    assert aad["registration"]["openIdIssuer"].endswith("/v2.0")
    assert "clientSecretSettingName" not in aad["registration"]  # ID 토큰만(비밀 없음)
    assert aad["validation"]["allowedAudiences"] == ["22222222-2222-2222-2222-222222222222"]


def test_console_app_registration_defines_roles_and_requires_assignment():
    template = _compile(INFRA / "modules" / "console-auth.bicep")
    by_type = {r["type"]: r for r in _resources(template)}
    app = by_type["Microsoft.Graph/applications@v1.0"]
    assert app["properties"]["signInAudience"] == "AzureADMyOrg"
    [roles] = [c for c in app["properties"]["copy"] if c["name"] == "appRoles"]
    assert roles["input"]["allowedMemberTypes"] == ["User"]
    assert {r["value"] for r in template["variables"]["roles"]} == {"CreditOfficer", "CreditManager", "Auditor"}
    web = app["properties"]["web"]
    assert web["implicitGrantSettings"] == {"enableIdTokenIssuance": True, "enableAccessTokenIssuance": False}
    assert web["redirectUris"] == ["[parameters('redirectUri')]"]
    sp = by_type["Microsoft.Graph/servicePrincipals@v1.0"]
    assert sp["properties"]["appRoleAssignmentRequired"] is True
    assert "Microsoft.Graph/appRoleAssignedTo@v1.0" in by_type


# 콘솔 ID는 읽기 + 결정 큐 보내기만 합니다(쓰기·삭제 없음).
EXPECTED_CONSOLE_GRANTS = {
    "keyvault": {"kvValueReader"},
    "storage-data": {"blobReader"},
    "storage-functions": {"queueSender"},
    "acr": {"acrPull"},
    "cosmos": {COSMOS_READER},
}


def test_console_identity_grants_are_least_privilege(closed):
    grants: dict[str, set[str]] = {}
    for module in _resources(_compile(INFRA / "resources.bicep")):
        text = json.dumps(module.get("properties", {}).get("parameters", {}))
        found = re.findall(r"consolePrincipalId\.value.{0,80}?(?:role'\)\.(\w+)|roleDefinitionGuid', '([0-9a-f-]+)')",
                           text)
        if found:
            grants[module["name"]] = {a or b for a, b in found}
    assert grants == EXPECTED_CONSOLE_GRANTS
    # 계정 범위가 아니라 lending DB 범위의 읽기 전용입니다(smoke는 계정 범위).
    assert any(a["properties"]["roleDefinitionId"].endswith(COSMOS_READER)
               and a["properties"]["scope"].endswith("/dbs/lending")
               for a in closed.of_type("Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments"))


@pytest.mark.parametrize("output", ["CONSOLE_URL", "CONSOLE_CLIENT_ID", "CONSOLE_APP_NAME"])
def test_console_outputs_exposed_to_azd(output):
    assert output in _compile(INFRA / "main.bicep")["outputs"]
```

`tests/infra/test_platform.py` 수정(`git apply`로 적용 가능한 diff):

```diff
diff --git a/tests/infra/test_platform.py b/tests/infra/test_platform.py
index 8bddf50..6830074 100644
--- a/tests/infra/test_platform.py
+++ b/tests/infra/test_platform.py
@@ -30,6 +30,6 @@ def test_app_insights_is_workspace_based_and_entra_only(closed):
     assert closed.roles_on("appi-") == {ROLE_METRICS_PUBLISHER}
 
 
-def test_three_user_assigned_identities(closed):
+def test_workload_user_assigned_identities(closed):
     names = {r["name"] for r in closed.of_type("Microsoft.ManagedIdentity/userAssignedIdentities")}
-    assert names == {"id-orch-amatest", "id-apps-amatest", "id-smoke-amatest"}
+    assert names == {"id-orch-amatest", "id-apps-amatest", "id-smoke-amatest", "id-console-amatest"}
```

- [ ] **Step 2: 실패 확인**

```powershell
$env:RUN_INFRA_TESTS='1'; .\.venv\Scripts\python -m pytest tests/infra/test_console.py tests/infra/test_platform.py -q
```

Expected: `10 failed, 5 passed, 1 error`. 실패·오류: `test_console_runs_in_web_environment_with_own_identity`, `test_console_ip_allow_list_fails_closed`, `test_console_image_param`, `test_console_auth_registration_wiring`, `test_console_app_registration_defines_roles_and_requires_assignment`, `test_console_identity_grants_are_least_privilege`, `test_console_outputs_exposed_to_azd[CONSOLE_URL]`, `test_console_outputs_exposed_to_azd[CONSOLE_CLIENT_ID]`, `test_console_outputs_exposed_to_azd[CONSOLE_APP_NAME]`, `test_workload_user_assigned_identities`, `test_console_easy_auth_requires_entra_login`. 통과하는 5건은 기존 플랫폼 테스트 4건과 `test_vnet_only_apps_have_no_ip_rules_or_auth`(내부 앱 무변화 고정)다.

- [ ] **Step 3: 구현**

새 파일 `infra/bicepconfig.json`:

```json
{
  "extensions": {
    "microsoftGraphV1": "br:mcr.microsoft.com/bicep/extensions/microsoftgraph/v1.0:1.0.0"
  }
}
```

새 파일 `infra/modules/console-auth.bicep`:

```bicep
// 심사역 콘솔 Entra 앱 등록(Microsoft Graph Bicep 확장). Container Apps 기본 인증(Easy Auth)이 씁니다.
// 비밀 없이 ID 토큰만 받는 암시적 흐름입니다(데모 범위). 운영 전환 시 Key Vault 비밀 또는
// 관리 ID 페더레이션(FIC)을 쓰는 인증 코드 흐름으로 바꿉니다(README P4b).
extension microsoftGraphV1

param uniqueName string
param displayName string
@description('Easy Auth 콜백 URI (https://<앱 FQDN>/.auth/login/aad/callback)')
param redirectUri string
@description('CreditOfficer 역할을 줄 운영자 Entra 객체 ID. 비면 할당하지 않습니다.')
param operatorPrincipalId string = ''

var roles = [
  { value: 'CreditOfficer', name: '심사역', description: '신청 조회·실명 복원·승인/조건부/거절 결정·신규 신청' }
  { value: 'CreditManager', name: '심사 관리자', description: '심사역 권한과 같음(에스컬레이션 처리)' }
  { value: 'Auditor', name: '감사자', description: '가명 상태로 조회만' }
]

resource app 'Microsoft.Graph/applications@v1.0' = {
  uniqueName: uniqueName
  displayName: displayName
  signInAudience: 'AzureADMyOrg'
  web: {
    redirectUris: [ redirectUri ]
    implicitGrantSettings: { enableIdTokenIssuance: true, enableAccessTokenIssuance: false }
  }
  appRoles: [for r in roles: {
    id: guid(uniqueName, r.value)
    value: r.value
    displayName: r.name
    description: r.description
    allowedMemberTypes: [ 'User' ]
    isEnabled: true
  }]
}

// 역할이 할당된 사용자만 로그인할 수 있습니다.
resource sp 'Microsoft.Graph/servicePrincipals@v1.0' = {
  appId: app.appId
  appRoleAssignmentRequired: true
}

resource operatorRole 'Microsoft.Graph/appRoleAssignedTo@v1.0' = if (!empty(operatorPrincipalId)) {
  principalId: operatorPrincipalId
  resourceId: sp.id
  appRoleId: guid(uniqueName, 'CreditOfficer')
}

output clientId string = app.appId
```

`infra/modules/container-app.bicep` 수정(`git apply`로 적용 가능한 diff):

```diff
diff --git a/infra/modules/container-app.bicep b/infra/modules/container-app.bicep
index 439e251..3620fdb 100644
--- a/infra/modules/container-app.bicep
+++ b/infra/modules/container-app.bicep
@@ -1,5 +1,6 @@
-// internal Container Apps 환경의 앱(VNet 전용 진입점). image가 비면 MCR Python 자리표시 이미지로 만들고,
-// ops deploy가 ACR 이미지로 갱신합니다(azd 환경 SERVICE_<NAME>_IMAGE_NAME으로 재프로비저닝에도 유지).
+// Container Apps 앱. internal 환경에서는 VNet 전용, 외부 환경(콘솔)에서는 ipAllowList와 Entra 기본 인증으로 막습니다.
+// image가 비면 MCR Python 자리표시 이미지로 만들고, ops deploy가 ACR 이미지로 갱신합니다
+// (azd 환경 SERVICE_<NAME>_IMAGE_NAME으로 재프로비저닝에도 유지).
 param name string
 param location string
 param tags object
@@ -14,6 +15,10 @@ param envVars array = []
 @secure()
 @description('{ 환경 변수 이름: 비밀 값 }. 값이 빈 항목은 만들지 않습니다.')
 param secretEnv object = {}
+@description('허용할 IPv4 CIDR 목록. 비면 IP 제한이 없습니다(internal 환경 전용).')
+param ipAllowList array = []
+@description('Entra 앱(클라이언트) ID. 있으면 Easy Auth로 로그인을 강제합니다(ID 토큰, 비밀 없음).')
+param authClientId string = ''
 
 var placeholder = empty(image)
 var secretItems = filter(items(secretEnv), s => !empty(s.value))
@@ -36,6 +41,11 @@ resource app 'Microsoft.App/containerApps@2024-03-01' = {
         targetPort: 8000
         transport: 'auto'
         allowInsecure: false
+        ipSecurityRestrictions: [for (cidr, i) in ipAllowList: {
+          name: 'allow-${i}'
+          ipAddressRange: cidr
+          action: 'Allow'
+        }]
       }
       registries: placeholder ? [] : [ { server: registryServer, identity: identityId } ]
       // 값은 @secure() secretEnv에서 오지만 filter() 때문에 린터가 추적하지 못합니다.
@@ -64,4 +74,28 @@ resource app 'Microsoft.App/containerApps@2024-03-01' = {
   }
 }
 
+// 로그인하지 않은 요청은 Entra 로그인으로 보냅니다. 앱은 X-MS-CLIENT-PRINCIPAL 헤더의 역할만 믿습니다.
+resource auth 'Microsoft.App/containerApps/authConfigs@2024-03-01' = if (!empty(authClientId)) {
+  parent: app
+  name: 'current'
+  properties: {
+    platform: { enabled: true }
+    globalValidation: {
+      unauthenticatedClientAction: 'RedirectToLoginPage'
+      redirectToProvider: 'azureactivedirectory'
+    }
+    identityProviders: {
+      azureActiveDirectory: {
+        enabled: true
+        registration: {
+          clientId: authClientId
+          openIdIssuer: '${environment().authentication.loginEndpoint}${tenant().tenantId}/v2.0'
+        }
+        validation: { allowedAudiences: [ authClientId ] }
+      }
+    }
+    login: { preserveUrlFragmentsForLogins: false }
+  }
+}
+
 output name string = app.name
```

`infra/modules/identities.bicep` 수정(`git apply`로 적용 가능한 diff):

```diff
diff --git a/infra/modules/identities.bicep b/infra/modules/identities.bicep
index e63e1bf..128b187 100644
--- a/infra/modules/identities.bicep
+++ b/infra/modules/identities.bicep
@@ -1,4 +1,4 @@
-// 워크로드별 사용자 할당 관리 ID. 오케스트레이터(Functions), 앱(Container Apps), smoke Job.
+// 워크로드별 사용자 할당 관리 ID. 오케스트레이터(Functions), 앱(Container Apps), smoke Job, 심사역 콘솔.
 param names object
 param location string
 param tags object
@@ -21,6 +21,13 @@ resource smoke 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
   tags: tags
 }
 
+// 콘솔은 인터넷에 노출되므로 앱 ID와 분리해 읽기·큐 보내기 권한만 줍니다.
+resource console 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
+  name: names.console
+  location: location
+  tags: tags
+}
+
 output orchestratorId string = orchestrator.id
 output orchestratorPrincipalId string = orchestrator.properties.principalId
 output orchestratorClientId string = orchestrator.properties.clientId
@@ -30,3 +37,6 @@ output appsClientId string = apps.properties.clientId
 output smokeId string = smoke.id
 output smokePrincipalId string = smoke.properties.principalId
 output smokeClientId string = smoke.properties.clientId
+output consoleId string = console.id
+output consolePrincipalId string = console.properties.principalId
+output consoleClientId string = console.properties.clientId
```

`infra/resources.bicep` 수정(`git apply`로 적용 가능한 diff):

```diff
diff --git a/infra/resources.bicep b/infra/resources.bicep
index 6a366fc..beec60f 100644
--- a/infra/resources.bicep
+++ b/infra/resources.bicep
@@ -16,6 +16,9 @@ param mockConsentSecret string
 param pseudonymKey string
 param policyImage string
 param mockapiImage string
+param consoleImage string
+@description('콘솔에 접속할 운영자 공인 IPv4. 비면 TEST-NET 주소만 허용해 사실상 막습니다(fail-closed).')
+param consoleIp string
 
 var sfx = uniqueString(subscription().id, resourceGroup().id, environmentName)
 
@@ -41,9 +44,12 @@ var names = {
   idOrchestrator: 'id-orch-${environmentName}'
   idApps: 'id-apps-${environmentName}'
   idSmoke: 'id-smoke-${environmentName}'
+  idConsole: 'id-console-${environmentName}'
   smokeJob: 'job-smoke'
   caPolicy: 'ca-policy'
   caMockapi: 'ca-mockapi'
+  caConsole: 'ca-console'
+  consoleApp: 'amalend-console-${environmentName}-${sfx}'
 }
 
 var storageSuffix = environment().suffixes.storage
@@ -118,6 +124,7 @@ module identities 'modules/identities.bicep' = {
       orchestrator: names.idOrchestrator
       apps: names.idApps
       smoke: names.idSmoke
+      console: names.idConsole
     }
     location: location
     tags: tags
@@ -151,6 +158,7 @@ module keyVault 'modules/keyvault.bicep' = {
       { principalId: identities.outputs.orchestratorPrincipalId, roleId: role.kvValueReader, principalType: sp }
       { principalId: identities.outputs.appsPrincipalId, roleId: role.kvValueReader, principalType: sp }
       { principalId: identities.outputs.smokePrincipalId, roleId: role.kvValueReader, principalType: sp }
+      { principalId: identities.outputs.consolePrincipalId, roleId: role.kvValueReader, principalType: sp }
     ], hasOperator ? [
       { principalId: operatorPrincipalId, roleId: role.kvValueAdmin, principalType: 'User' }
     ] : [])
@@ -192,6 +200,7 @@ module stData 'modules/storage.bicep' = {
       { principalId: identities.outputs.orchestratorPrincipalId, roleId: role.blobContributor, principalType: sp }
       { principalId: identities.outputs.appsPrincipalId, roleId: role.blobContributor, principalType: sp }
       { principalId: identities.outputs.smokePrincipalId, roleId: role.blobReader, principalType: sp }
+      { principalId: identities.outputs.consolePrincipalId, roleId: role.blobReader, principalType: sp }
     ], hasOperator ? [
       { principalId: operatorPrincipalId, roleId: role.blobContributor, principalType: 'User' }
     ] : [])
@@ -217,6 +226,7 @@ module stFunc 'modules/storage.bicep' = {
       { principalId: identities.outputs.orchestratorPrincipalId, roleId: role.queueContributor, principalType: sp }
       { principalId: identities.outputs.orchestratorPrincipalId, roleId: role.tableContributor, principalType: sp }
       { principalId: identities.outputs.smokePrincipalId, roleId: role.queueSender, principalType: sp }
+      { principalId: identities.outputs.consolePrincipalId, roleId: role.queueSender, principalType: sp }
     ]
   }
 }
@@ -238,6 +248,7 @@ module cosmos 'modules/cosmos.bicep' = {
       { principalId: identities.outputs.orchestratorPrincipalId, roleDefinitionGuid: '00000000-0000-0000-0000-000000000002', scope: 'database' }
       { principalId: identities.outputs.appsPrincipalId, roleDefinitionGuid: '00000000-0000-0000-0000-000000000002', scope: 'database' }
       { principalId: identities.outputs.smokePrincipalId, roleDefinitionGuid: '00000000-0000-0000-0000-000000000001', scope: 'account' }
+      { principalId: identities.outputs.consolePrincipalId, roleDefinitionGuid: '00000000-0000-0000-0000-000000000001', scope: 'database' }
     ], hasOperator ? [
       { principalId: operatorPrincipalId, roleDefinitionGuid: '00000000-0000-0000-0000-000000000002', scope: 'account' }
     ] : [])
@@ -378,6 +389,7 @@ module acr 'modules/acr.bicep' = {
     zoneId: zoneId['privatelink.azurecr.io']
     roleAssignments: concat([
       { principalId: identities.outputs.appsPrincipalId, roleId: role.acrPull, principalType: sp }
+      { principalId: identities.outputs.consolePrincipalId, roleId: role.acrPull, principalType: sp }
     ], hasOperator ? [
       { principalId: operatorPrincipalId, roleId: role.acrPush, principalType: 'User' }
     ] : [])
@@ -505,6 +517,49 @@ module functions 'modules/functions.bicep' = {
 
 output FUNCTION_APP_NAME string = functions.outputs.name
 
+// ── 심사역 콘솔 (P4b) ──────────────────────────────────────────────────
+// 외부 Container Apps 환경(snet-apps-web, 같은 VNet이라 Private Endpoint DNS가 풀립니다).
+// 진입은 운영자 IP 허용 목록 + Entra 로그인(앱 역할)으로 막습니다.
+var consoleFqdn = '${names.caConsole}.${acaWeb.outputs.defaultDomain}'
+
+module consoleAuth 'modules/console-auth.bicep' = {
+  name: 'console-auth'
+  params: {
+    uniqueName: names.consoleApp
+    displayName: 'AMA Lending 심사 콘솔 (${environmentName})'
+    redirectUri: 'https://${consoleFqdn}/.auth/login/aad/callback'
+    operatorPrincipalId: operatorPrincipalId
+  }
+}
+
+module caConsole 'modules/container-app.bicep' = {
+  name: 'ca-console'
+  params: {
+    name: names.caConsole
+    location: location
+    tags: tags
+    serviceName: 'console'
+    environmentId: acaWeb.outputs.id
+    identityId: identities.outputs.consoleId
+    identityClientId: identities.outputs.consoleClientId
+    registryServer: acr.outputs.loginServer
+    image: consoleImage
+    ipAllowList: [ empty(consoleIp) ? '192.0.2.0/32' : '${consoleIp}/32' ]
+    authClientId: consoleAuth.outputs.clientId
+    envVars: [
+      { name: 'COSMOS_ENDPOINT', value: cosmos.outputs.endpoint }
+      { name: 'COSMOS_DATABASE', value: cosmos.outputs.databaseName }
+      { name: 'DATA_BLOB_ENDPOINT', value: stData.outputs.blobEndpoint }
+      { name: 'QUEUE_ENDPOINT', value: 'https://${names.stFunc}.queue.${storageSuffix}/' }
+      { name: 'KEYVAULT_URI', value: keyVault.outputs.uri }
+    ]
+  }
+}
+
+output CONSOLE_APP_NAME string = caConsole.outputs.name
+output CONSOLE_URL string = 'https://${consoleFqdn}'
+output CONSOLE_CLIENT_ID string = consoleAuth.outputs.clientId
+
 // ── VNet 안 smoke Job (T8) ─────────────────────────────────────────────
 // Private Endpoint가 있는 모든 호스트. VNet 안에서 사설 IP로 해석되어야 합니다.
 var smokePrivateHosts = concat([
```

`infra/main.bicep` 수정(`git apply`로 적용 가능한 diff):

```diff
diff --git a/infra/main.bicep b/infra/main.bicep
index 59d224c..244092c 100644
--- a/infra/main.bicep
+++ b/infra/main.bicep
@@ -47,6 +47,12 @@ param policyImage string = ''
 @description('Mock API 이미지. 비면 자리표시 이미지(ops deploy 전).')
 param mockapiImage string = ''
 
+@description('심사역 콘솔 이미지. 비면 자리표시 이미지(ops deploy 전).')
+param consoleImage string = ''
+
+@description('심사역 콘솔 접속 허용 IPv4(ops window open이 설정, close 후에도 유지). 비면 콘솔 진입을 막습니다.')
+param consoleIp string = ''
+
 var tags = {
   'azd-env-name': environmentName
   project: 'ama-cashflow-lending'
@@ -84,6 +90,8 @@ module resources 'resources.bicep' = {
     pseudonymKey: pseudonymKey
     policyImage: policyImage
     mockapiImage: mockapiImage
+    consoleImage: consoleImage
+    consoleIp: consoleIp
   }
 }
 
@@ -128,3 +136,7 @@ output MOCKAPI_URL string = resources.outputs.MOCKAPI_URL
 output FUNCTION_APP_NAME string = resources.outputs.FUNCTION_APP_NAME
 
 output SMOKE_JOB_NAME string = resources.outputs.SMOKE_JOB_NAME
+
+output CONSOLE_APP_NAME string = resources.outputs.CONSOLE_APP_NAME
+output CONSOLE_URL string = resources.outputs.CONSOLE_URL
+output CONSOLE_CLIENT_ID string = resources.outputs.CONSOLE_CLIENT_ID
```

`infra/main.bicepparam` 수정(`git apply`로 적용 가능한 diff):

```diff
diff --git a/infra/main.bicepparam b/infra/main.bicepparam
index af8bc16..a23b5d6 100644
--- a/infra/main.bicepparam
+++ b/infra/main.bicepparam
@@ -17,3 +17,5 @@ param mockConsentSecret = readEnvironmentVariable('MOCK_CONSENT_SECRET', '')
 param pseudonymKey = readEnvironmentVariable('PSEUDONYM_KEY', '')
 param policyImage = readEnvironmentVariable('SERVICE_POLICY_IMAGE_NAME', '')
 param mockapiImage = readEnvironmentVariable('SERVICE_MOCKAPI_IMAGE_NAME', '')
+param consoleImage = readEnvironmentVariable('SERVICE_CONSOLE_IMAGE_NAME', '')
+param consoleIp = readEnvironmentVariable('CONSOLE_IP', '')
```

`infra/tests/open.bicepparam` 수정(`git apply`로 적용 가능한 diff):

```diff
diff --git a/infra/tests/open.bicepparam b/infra/tests/open.bicepparam
index d7aaa74..ab03928 100644
--- a/infra/tests/open.bicepparam
+++ b/infra/tests/open.bicepparam
@@ -7,3 +7,4 @@ param acrPublic = false
 param operatorPrincipalId = '11111111-1111-1111-1111-111111111111'
 param serviceBusEnabled = true
 param deployCapabilityHost = true
+param consoleIp = '20.30.40.50'
```

`infra/tests/variant.bicepparam` 수정(`git apply`로 적용 가능한 diff):

```diff
diff --git a/infra/tests/variant.bicepparam b/infra/tests/variant.bicepparam
index 2f8dcdd..453bde5 100644
--- a/infra/tests/variant.bicepparam
+++ b/infra/tests/variant.bicepparam
@@ -11,3 +11,4 @@ param chatModelSku = 'DataZoneStandard'
 param decisionModelSku = 'DataZoneStandard'
 param policyImage = 'acramatest.azurecr.io/policy:v1'
 param mockapiImage = 'acramatest.azurecr.io/mockapi:v1'
+param consoleImage = 'acramatest.azurecr.io/console:v1'
```

- [ ] **Step 4: 통과 확인**

```powershell
$env:RUN_INFRA_TESTS='1'; .\.venv\Scripts\python -m pytest tests/infra/test_console.py tests/infra/test_platform.py -q; .\.venv\Scripts\python -m pytest tests/infra -q
```

Expected: `16 passed`, `tests/infra` 전체 `85 passed`.

- [ ] **Step 5: 커밋**

```powershell
git add infra/bicepconfig.json infra/modules/console-auth.bicep infra/modules/container-app.bicep infra/modules/identities.bicep infra/resources.bicep infra/main.bicep infra/main.bicepparam infra/tests/open.bicepparam infra/tests/variant.bicepparam tests/infra/conftest.py infra/tests/console-app.bicepparam tests/infra/test_console.py tests/infra/test_platform.py
git commit -m "infra: officer console with Entra app roles, IP allow list and Easy Auth" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>" -m "Copilot-Session: 05f1ca1b-6073-417d-bfb1-6b546f82cb58"
```

---

### Task 6: 운영: 콘솔 배포·허용 IP·로그인 관문 확인

운영 명령: 콘솔 이미지 배포, 콘솔 허용 IP, smoke 뒤 로그인 관문 확인.

**설계 메모**
- `ops deploy console`: 기존 `ContainerService` 목록에 `console`(`src/console/Dockerfile`, `SERVICE_CONSOLE_IMAGE_NAME`)을 더한다. `all`에도 포함.
- `ops window open`이 `CONSOLE_IP`도 같은 IP로 설정한다. `close`는 `CONSOLE_IP`를 지우지 않는다(창을 닫은 뒤에도 시연 중 콘솔 접속).
- `check_console_gate(env, fetch)`: 운영자 PC에서 `CONSOLE_URL`을 리디렉션 없이 요청한다. 콘솔 호스트 안 리디렉션(`/.auth/login/aad`)은 최대 4번 따라가고, `login.microsoftonline.com`이고 `client_id`가 `CONSOLE_CLIENT_ID`면 통과. 200(로그인 없이 열림), 403(IP 차단 → `window open` 안내), 다른 호스트, `client_id` 불일치, 반복 리디렉션은 `OpsError`. 출력이 없으면 `azd provision` 안내.
- `ops smoke`는 VNet 안 smoke Job 성공 뒤 `콘솔 관문 확인: ...`을 출력한다.

**Files:**
- Create: `src/ops/console_gate.py`
- Modify: `src/ops/deploy.py`
- Modify: `src/ops/window.py`
- Modify: `src/ops/cli.py`
- Create: `tests/ops/test_console_gate.py`
- Modify: `tests/ops/test_deploy.py`
- Modify: `tests/ops/test_window.py`

- [ ] **Step 1: 실패하는 테스트 작성**

새 파일 `tests/ops/test_console_gate.py`:

```python
import io

import pytest

from ops.azcli import OpsError
from ops.cli import main
from ops.console_gate import check_console_gate

ENV = {"CONSOLE_URL": "https://ca-console.example.koreacentral.azurecontainerapps.io",
       "CONSOLE_CLIENT_ID": "22222222-2222-2222-2222-222222222222"}
LOGIN = ("https://login.microsoftonline.com/tenant-1/oauth2/v2.0/authorize?response_type=id_token"
         "&client_id=22222222-2222-2222-2222-222222222222&redirect_uri=x")


def fake_fetch(responses):
    calls = []

    def fetch(url):
        calls.append(url)
        return responses[url]
    return fetch, calls


def test_gate_follows_easy_auth_redirect_to_entra_login():
    base = ENV["CONSOLE_URL"]
    fetch, calls = fake_fetch({
        f"{base}/": (302, f"{base}/.auth/login/aad?post_login_redirect_uri=/"),
        f"{base}/.auth/login/aad?post_login_redirect_uri=/": (302, LOGIN),
    })
    assert "login.microsoftonline.com" in check_console_gate(ENV, fetch)
    assert calls == [f"{base}/", f"{base}/.auth/login/aad?post_login_redirect_uri=/"]  # Entra는 호출하지 않습니다.


def test_gate_accepts_relative_redirect_and_direct_login():
    base = ENV["CONSOLE_URL"]
    fetch, _ = fake_fetch({f"{base}/": (302, LOGIN)})
    check_console_gate(ENV, fetch)
    fetch, _ = fake_fetch({f"{base}/": (302, "/.auth/login/aad"), f"{base}/.auth/login/aad": (302, LOGIN)})
    check_console_gate(ENV, fetch)


@pytest.mark.parametrize("status, location, message", [
    (200, None, "로그인 없이"),
    (403, None, "window open"),
    (302, "https://login.microsoftonline.com/t/oauth2/v2.0/authorize?client_id=other", "client_id"),
    (302, "https://evil.example/login?client_id=22222222-2222-2222-2222-222222222222", "evil.example"),
    (404, None, "HTTP 404"),
])
def test_gate_failures(status, location, message):
    fetch, _ = fake_fetch({f"{ENV['CONSOLE_URL']}/": (status, location)})
    with pytest.raises(OpsError, match=message):
        check_console_gate(ENV, fetch)


def test_gate_stops_redirect_loops():
    base = ENV["CONSOLE_URL"]
    fetch, calls = fake_fetch({f"{base}/": (302, f"{base}/")})
    with pytest.raises(OpsError, match="리디렉션"):
        check_console_gate(ENV, fetch)
    assert len(calls) <= 5


def test_gate_requires_console_outputs():
    with pytest.raises(OpsError, match="azd provision"):
        check_console_gate({}, lambda url: (302, LOGIN))


def test_cli_smoke_checks_console_gate_after_job(fake_runner, monkeypatch):
    order = []
    monkeypatch.setattr("ops.cli.run_smoke", lambda runner: order.append("job") or "job-smoke-1")
    monkeypatch.setattr("ops.cli.check_console_gate",
                        lambda env: order.append(("gate", env["CONSOLE_URL"])) or "Entra 로그인으로 리디렉션")
    out = io.StringIO()
    assert main(["smoke"], runner=fake_runner(ENV), out=out) == 0
    assert order == ["job", ("gate", ENV["CONSOLE_URL"])]
    assert "콘솔" in out.getvalue()
```

`tests/ops/test_deploy.py` 수정(`git apply`로 적용 가능한 diff):

```diff
diff --git a/tests/ops/test_deploy.py b/tests/ops/test_deploy.py
index 133b235..646ec84 100644
--- a/tests/ops/test_deploy.py
+++ b/tests/ops/test_deploy.py
@@ -105,12 +105,13 @@ def test_function_package_layout_and_linux_wheels(fake_runner, repo):
     assert runner.commands("azd", "provision") == []
 
 
-def test_all_builds_both_images_provisions_once_then_deploys_function(fake_runner, repo):
+def test_all_builds_every_image_provisions_once_then_deploys_function(fake_runner, repo):
     runner = fake_runner(ENV)
     deploy(runner, "all", root=repo, tag="t2")
     tags = [c[c.index("-t") + 1] for c in runner.commands("az", "acr", "build")]
-    assert tags == ["policy:t2", "mockapi:t2"]
+    assert tags == ["policy:t2", "mockapi:t2", "console:t2"]
     assert runner.env["SERVICE_MOCKAPI_IMAGE_NAME"] == "acrx.azurecr.io/mockapi:t2"
+    assert runner.env["SERVICE_CONSOLE_IMAGE_NAME"] == "acrx.azurecr.io/console:t2"
     assert len(runner.commands("azd", "provision")) == 1
     assert runner.index("azd", "provision") < runner.index(*ZIP_DEPLOY)
 
@@ -122,3 +123,14 @@ def test_cli_deploy(fake_runner, repo, monkeypatch):
     assert main(["deploy", "mockapi"], runner=runner, out=out) == 0
     assert runner.env["SERVICE_MOCKAPI_IMAGE_NAME"].startswith("acrx.azurecr.io/mockapi:")
     assert "mockapi" in out.getvalue()
+
+
+def test_console_image_built_from_repo_root(fake_runner, repo):
+    # 콘솔 이미지는 src/agents·src/shared·journey 모델을 함께 복사하므로 저장소 루트가 빌드 컨텍스트입니다.
+    runner = fake_runner(ENV)
+    assert deploy(runner, "console", root=repo, tag="t3") == ["acrx.azurecr.io/console:t3"]
+    [build] = runner.commands("az", "acr", "build")
+    assert build[build.index("-f") + 1] == "src/console/Dockerfile"
+    assert str(repo) in build
+    assert runner.env["SERVICE_CONSOLE_IMAGE_NAME"] == "acrx.azurecr.io/console:t3"
+    assert runner.commands(*ZIP_DEPLOY) == []
```

`tests/ops/test_window.py` 수정(`git apply`로 적용 가능한 diff):

```diff
diff --git a/tests/ops/test_window.py b/tests/ops/test_window.py
index 8d8fe32..3a850fe 100644
--- a/tests/ops/test_window.py
+++ b/tests/ops/test_window.py
@@ -26,6 +26,7 @@ def test_open_window_sets_ip_and_provisions(fake_runner):
     runner = fake_runner(PROVISIONED)
     assert open_window(runner, "20.30.40.50") == "20.30.40.50"
     assert runner.env["OPERATOR_IP"] == "20.30.40.50"
+    assert runner.env["CONSOLE_IP"] == "20.30.40.50"
     assert runner.env["ACR_PUBLIC"] == "false"
     assert runner.commands("azd", "provision") == [["azd", "provision", "--no-prompt"]]
 
@@ -51,9 +52,11 @@ def test_open_window_before_first_provision_keeps_caphost(fake_runner):
 
 
 def test_close_window_sets_closed_sentinel(fake_runner):
-    runner = fake_runner({**PROVISIONED, "OPERATOR_IP": "20.30.40.50", "ACR_PUBLIC": "true"})
+    runner = fake_runner({**PROVISIONED, "OPERATOR_IP": "20.30.40.50", "ACR_PUBLIC": "true",
+                          "CONSOLE_IP": "20.30.40.50"})
     close_window(runner)
     assert runner.env["OPERATOR_IP"] == "closed"
+    assert runner.env["CONSOLE_IP"] == "20.30.40.50"  # 창을 닫아도 콘솔 시연은 계속됩니다.
     assert runner.env["ACR_PUBLIC"] == "false"
     assert len(runner.commands("azd", "provision")) == 1
 
```

- [ ] **Step 2: 실패 확인**

```powershell
.\.venv\Scripts\python -m pytest tests/ops/test_console_gate.py tests/ops/test_deploy.py tests/ops/test_window.py -q
```

Expected: 수집 단계 `ModuleNotFoundError: No module named 'ops.console_gate'`.

- [ ] **Step 3: 구현**

새 파일 `src/ops/console_gate.py`:

```python
"""운영자 PC에서 콘솔 진입 관문을 확인합니다: 로그인 없이 들어가면 Entra 로그인으로 보내야 합니다.

콘솔은 외부 ingress라 VNet 안 smoke Job이 아니라 운영자 IP(CONSOLE_IP)에서 확인합니다.
Entra 로그인 화면 자체는 호출하지 않고, 리디렉션 대상(호스트·client_id)만 봅니다.
"""
from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from ops.azcli import OpsError

LOGIN_HOST = "login.microsoftonline.com"
MAX_HOPS = 4
Fetch = Callable[[str], "tuple[int, str | None]"]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def urllib_fetch(url: str) -> tuple[int, str | None]:
    """리디렉션을 따라가지 않고 (상태 코드, Location)을 돌려줍니다."""
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(urllib.request.Request(url, method="GET"), timeout=30) as resp:
            return resp.status, resp.headers.get("Location")
    except urllib.error.HTTPError as err:
        return err.code, err.headers.get("Location")


def check_console_gate(env: dict[str, str], fetch: Fetch = urllib_fetch) -> str:
    base, client_id = env.get("CONSOLE_URL"), env.get("CONSOLE_CLIENT_ID")
    if not base or not client_id:
        raise OpsError("CONSOLE_URL/CONSOLE_CLIENT_ID가 azd 환경에 없습니다. `azd provision` 후 다시 실행하세요.")
    console_host = urllib.parse.urlsplit(base).hostname
    url = base.rstrip("/") + "/"
    for _ in range(MAX_HOPS):
        status, location = fetch(url)
        if status == 403:
            raise OpsError("콘솔이 현재 IP를 막았습니다(403). 콘솔 허용 IP는 CONSOLE_IP입니다: "
                           "`python -m ops window open`으로 갱신한 뒤 `window close` 하세요.")
        if status == 200:
            raise OpsError(f"{url}: 로그인 없이 200을 돌려줍니다. Easy Auth(authConfigs)를 확인하세요.")
        if status not in (301, 302, 303, 307, 308) or not location:
            raise OpsError(f"{url}: 예상하지 못한 응답 HTTP {status}")
        target = urllib.parse.urljoin(url, location)
        parts = urllib.parse.urlsplit(target)
        if parts.hostname == LOGIN_HOST:
            if urllib.parse.parse_qs(parts.query).get("client_id") != [client_id]:
                raise OpsError(f"Entra 로그인 리디렉션의 client_id가 콘솔 앱({client_id})이 아닙니다: {target}")
            return f"로그인 없는 요청은 Entra 로그인으로 리디렉션({LOGIN_HOST}, client_id {client_id})"
        if parts.hostname != console_host:
            raise OpsError(f"콘솔이 예상하지 못한 호스트로 리디렉션합니다: {parts.hostname}")
        url = target
    raise OpsError(f"콘솔 리디렉션이 {MAX_HOPS}번 안에 Entra 로그인에 도달하지 않습니다: {url}")
```

`src/ops/deploy.py` 수정(`git apply`로 적용 가능한 diff):

```diff
diff --git a/src/ops/deploy.py b/src/ops/deploy.py
index dc49a85..1625c83 100644
--- a/src/ops/deploy.py
+++ b/src/ops/deploy.py
@@ -13,7 +13,7 @@ from ops.azdenv import get_env, provision, set_env
 from ops.window import CLOSED
 
 ROOT = Path(__file__).resolve().parents[2]
-TARGETS = ("policy", "mockapi", "orchestrator", "all")
+TARGETS = ("policy", "mockapi", "console", "orchestrator", "all")
 # pip는 manylinux_2_28만 주면 manylinux_2_17/2014 wheel(pydantic-core 등)을 고르지 않습니다.
 PIP_PLATFORMS = ("manylinux_2_28_x86_64", "manylinux_2_17_x86_64", "manylinux2014_x86_64")
 SITE_PACKAGES = Path(".python_packages") / "lib" / "site-packages"
@@ -30,6 +30,7 @@ class ContainerService:
 CONTAINERS = (
     ContainerService("policy", "src/policy/Dockerfile", "SERVICE_POLICY_IMAGE_NAME"),
     ContainerService("mockapi", "src/mockapis/Dockerfile", "SERVICE_MOCKAPI_IMAGE_NAME"),
+    ContainerService("console", "src/console/Dockerfile", "SERVICE_CONSOLE_IMAGE_NAME"),
 )
 
 
@@ -80,7 +81,7 @@ def deploy_function(runner: Runner, root: Path) -> str:
 
 
 def deploy(runner: Runner, target: str, *, root: Path | None = None, tag: str | None = None) -> list[str]:
-    """target: policy | mockapi | orchestrator | all. 배포한 이미지·앱 이름 목록을 돌려줍니다."""
+    """target: policy | mockapi | console | orchestrator | all. 배포한 이미지·앱 이름 목록을 돌려줍니다."""
     if target not in TARGETS:
         raise OpsError(f"알 수 없는 배포 대상: {target}")
     root = root or ROOT
```

`src/ops/window.py` 수정(`git apply`로 적용 가능한 diff):

```diff
diff --git a/src/ops/window.py b/src/ops/window.py
index 7a87aa1..099888e 100644
--- a/src/ops/window.py
+++ b/src/ops/window.py
@@ -40,6 +40,8 @@ def open_window(runner: Runner, ip: str, *, acr: bool = False) -> str:
     ip = validate_operator_ip(ip)
     ensure_caphost_skip(runner, get_env(runner))
     set_env(runner, "OPERATOR_IP", ip)
+    # 콘솔 허용 IP는 창을 닫아도 유지합니다(시연 중 콘솔 접속). close_window는 건드리지 않습니다.
+    set_env(runner, "CONSOLE_IP", ip)
     set_env(runner, "ACR_PUBLIC", "true" if acr else "false")
     provision(runner)
     return ip
```

`src/ops/cli.py` 수정(`git apply`로 적용 가능한 diff):

```diff
diff --git a/src/ops/cli.py b/src/ops/cli.py
index 321d7e2..676ad80 100644
--- a/src/ops/cli.py
+++ b/src/ops/cli.py
@@ -7,6 +7,8 @@ from typing import Callable
 
 from ops import deploy as deploy_module
 from ops.azcli import OpsError, Runner, subprocess_runner
+from ops.azdenv import get_env
+from ops.console_gate import check_console_gate
 from ops.lifecycle import pause, resume, teardown
 from ops.smoke_runner import run_smoke
 from ops.window import close_window, fetch_public_ip, open_window, window_status
@@ -26,8 +28,8 @@ def build_parser() -> argparse.ArgumentParser:
 
     commands.add_parser("pause", help="Service Bus Premium 삭제(비용 절감)")
     commands.add_parser("resume", help="Service Bus 다시 배포")
-    commands.add_parser("smoke", help="VNet 안 smoke Job 실행")
-    deployer = commands.add_parser("deploy", help="정책·Mock 이미지(ACR Build)와 Function 코드 배포")
+    commands.add_parser("smoke", help="VNet 안 smoke Job 실행 + 콘솔 로그인 관문 확인")
+    deployer = commands.add_parser("deploy", help="정책·Mock·콘솔 이미지(ACR Build)와 Function 코드 배포")
     deployer.add_argument("target", nargs="?", default="all", choices=deploy_module.TARGETS)
     destroy = commands.add_parser("teardown", help="환경 전체 삭제")
     destroy.add_argument("--confirm", required=True, help="삭제할 리소스 그룹 이름")
@@ -68,6 +70,7 @@ def main(argv: list[str] | None = None, *, runner: Runner = subprocess_runner,
             print("배포했습니다: " + ", ".join(deployed), file=out)
         elif args.command == "smoke":
             print(f"smoke 성공: {run_smoke(runner)}", file=out)
+            print(f"콘솔 관문 확인: {check_console_gate(get_env(runner))}", file=out)
         elif args.command == "teardown":
             teardown(runner, args.confirm)
             print(f"{args.confirm} 삭제를 마쳤습니다.", file=out)
```

- [ ] **Step 4: 통과 확인**

```powershell
.\.venv\Scripts\python -m pytest tests/ops/test_console_gate.py tests/ops/test_deploy.py tests/ops/test_window.py -q; .\.venv\Scripts\python -m pytest -q
```

Expected: `32 passed`, 전체 `432 passed, 85 skipped`(경고 1건은 Starlette TestClient의 httpx 사용 안내로 무해).

- [ ] **Step 5: 커밋**

```powershell
git add src/ops/console_gate.py src/ops/deploy.py src/ops/window.py src/ops/cli.py tests/ops/test_console_gate.py tests/ops/test_deploy.py tests/ops/test_window.py
git commit -m "ops: deploy console, console IP and login gate check in smoke" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>" -m "Copilot-Session: 05f1ca1b-6073-417d-bfb1-6b546f82cb58"
```

---

### Task 7: README P4b 절과 실제 배포·로그인 확인

**Files:**
- Modify: `README.md`

**설계 메모**
- 운영 명령 표(`deploy` 대상, `smoke`)를 고치고 README 끝(“알려진 제약(P4a)” 뒤)에 P4b 절(구성, 역할별 권한, 배포 순서, 문제 대응, 알려진 제약)을 붙인다.
- 그다음 실제 Azure에 배포한다. 첫 프로비저닝(`window open --acr`)이 Graph 앱 등록, `id-console`과 역할, `ca-console`(자리표시 이미지)을 만든다. `deploy console`이 이미지를 빌드하고 다시 프로비저닝하며, `deploy orchestrator`가 P4a 리뷰 수정분(8ca913b)을 Function에 싣는다.
- smoke는 자동으로 로그인 관문까지 보고, 실제 로그인 → 실명 복원 → 결정 흐름은 브라우저로 확인한다(Step 6 체크리스트).
- 실패하면 README "문제 대응(P4b)"과 위 "배포 위험" 표를 따른다. 배포 위험 1(azd Graph 확장 실패)은 멈추고 사용자와 대안을 정한다. 코드 결함(위험 2·4·5)은 실패 테스트를 먼저 쓰고 고친다.

- [ ] **Step 1: README 수정**

`README.md` 수정(`git apply`로 적용 가능한 diff):

```diff
diff --git a/README.md b/README.md
index 18621b8..2737954 100644
--- a/README.md
+++ b/README.md
@@ -69,8 +69,8 @@ Bicep `loadTextContent`로 Job에 들어가므로 수정 후에는 `azd provisio
 | `python -m ops window open [--ip <IP>] [--acr]` | 배포 창 열기: 운영자 공인 IP만 허용(`--acr`은 ACR 빌드 창). 실측 11.0분(Cosmos 갱신이 약 8분) |
 | `python -m ops window status` | 창 상태 확인. 공인 IP가 바뀌었으면 경고(종료 코드 2) |
 | `python -m ops window close` | 배포 창 닫기(작업 후 반드시 실행). 실측 10.8분 |
-| `python -m ops deploy [policy\|mockapi\|orchestrator\|all]` | 앱 배포(P3): 정책·Mock 이미지 ACR Build → 프로비저닝, Function zip. `window open --acr` 필요 |
-| `python -m ops smoke` | VNet 안 smoke Job 실행 |
+| `python -m ops deploy [policy\|mockapi\|console\|orchestrator\|all]` | 앱 배포(P3·P4b): 정책·Mock·콘솔 이미지 ACR Build → 프로비저닝, Function zip. `window open --acr` 필요 |
+| `python -m ops smoke` | VNet 안 smoke Job 실행 + 운영자 PC에서 콘솔 로그인 관문 확인(P4b) |
 | `python -m ops pause` / `resume` | Service Bus Premium 삭제/복구(비용 절감). 실측 pause 5.8분, resume 4.7분 |
 | `python -m ops teardown --confirm rg-amalend` | capability host → Foundry 계정 purge → 리소스 그룹 삭제 → Key Vault purge |
 
@@ -196,3 +196,62 @@ python -m ops smoke                # P2 점검 + health:policy, health:mockapi,
 - 데모 데이터에서 규칙 미분류 적요는 공급업체 송금뿐이라 의미 분류가 특성을 바꾸지 않습니다. 보수적 반영은 단위 테스트로 검증합니다.
 - 수치 근거 검사는 숫자 일치만 봅니다. 해석의 타당성은 검토 에이전트 판정에 의존합니다.
 - `demo-stress-003`은 고금리 대출이 감지되어도 정책상 eligible입니다(P3 규칙). 분석가는 이를 claim·counterfactual로 드러냅니다.
+
+## 심사역 콘솔(P4b)
+
+심사역이 신청 목록·상세(심사 에이전트 근거)를 보고 승인·조건부 승인·거절을 내리는 웹 콘솔입니다(`src/console`, FastAPI +
+서버 렌더링 HTML, 목록만 htmx 폴링). 결정은 P3와 같은 `officer-decisions` 큐로 보내고, 최종 판단은 오케스트레이터가
+정책 한도와 함께 다시 검증합니다.
+
+| 구성 | 위치 | 비고 |
+|------|------|------|
+| 콘솔 | `ca-console`(external 환경 `cae-web`, `snet-apps-web`) | 관리 ID `id-console`: Cosmos `lending` 읽기, 데이터 Blob 읽기, 큐 보내기, Key Vault 비밀 읽기, ACR pull |
+| 진입 통제 | ingress IP 허용(`CONSOLE_IP`/32) + Easy Auth(Entra 로그인 강제) | `CONSOLE_IP`가 비면 TEST-NET 주소만 허용(fail-closed) |
+| 앱 등록 | Bicep Graph 확장(`infra/modules/console-auth.bicep`) | 앱 역할 `CreditOfficer`·`CreditManager`·`Auditor`, 역할 할당 필수, 운영자에게 `CreditOfficer` 부여 |
+
+| 역할 | 목록·상세 | 실명 복원 | 결정·새 신청 |
+|------|-----------|-----------|--------------|
+| `CreditOfficer`, `CreditManager` | 가능 | 가능(동의된 원천 데이터로 가명 토큰 복원) | 가능 |
+| `Auditor` | 가능 | 불가(가명 토큰 그대로) | 불가(403) |
+| 역할 없음 | 불가(Entra가 로그인 거부: 역할 할당 필수) | | |
+
+- 심사역 ID(`officer_id`)는 폼 값이 아니라 Easy Auth 헤더(`X-MS-CLIENT-PRINCIPAL`)의 로그인 사용자에서 가져옵니다.
+- 결정·새 신청은 CSRF 토큰(가명 키 HMAC, 복제본 간 동일)과 `application/x-www-form-urlencoded`만 받습니다.
+  응답에는 CSP(`script-src 'self'` + htmx SRI), `no-store`, `nosniff`, `no-referrer`를 붙입니다.
+- 결정 가능한 상태는 `awaiting_officer`, `escalated`뿐이고, 금액은 정책 한도(`max_amount_krw`) 이하만 보냅니다.
+
+### 배포 순서(P4b)
+
+```powershell
+python -m ops resume                       # 선택: Service Bus 이벤트까지 보려면
+python -m ops window open --acr            # CONSOLE_IP도 운영자 IP로 설정(창을 닫아도 유지)
+python -m ops deploy console               # 콘솔 이미지 + 프로비저닝(Graph 앱 등록·Easy Auth 포함)
+python -m ops deploy orchestrator          # P4a 리뷰 수정분 포함 Function 재배포
+python -m ops window close
+python -m ops smoke                        # 25개 점검 + 콘솔 관문(로그인 없이 접근 → Entra 로그인 리디렉션)
+python -m ops pause
+```
+
+브라우저로 `azd env get-value CONSOLE_URL`을 열어 로그인합니다(허용 IP에서만). `새 신청`으로 데모 사업자를 넣고
+`awaiting_officer`가 되면 상세 화면에서 결정합니다. 공인 IP가 바뀌면 `python -m ops window open` 후 `window close`로
+`CONSOLE_IP`를 갱신합니다.
+
+다른 사용자에게 역할을 줄 때는 Entra 관리 센터 → 엔터프라이즈 애플리케이션 → `AMA Lending 심사 콘솔 (<환경>)` →
+사용자 및 그룹에서 할당합니다. 운영자 자신을 `Auditor`로 바꿔 보려면 같은 화면에서 역할을 바꾸고 다시 로그인합니다.
+
+### 문제 대응(P4b)
+
+- 콘솔이 403(빈 화면): 현재 공인 IP가 `CONSOLE_IP`와 다릅니다. `python -m ops window open` → `window close`.
+- 로그인 후 `AADSTS50105`(역할 미할당): 엔터프라이즈 애플리케이션에서 사용자에게 앱 역할을 할당합니다.
+- 로그인 후 `AADSTS50011`(redirect URI 불일치): `azd provision`이 Container Apps 기본 도메인으로 redirect URI를 다시 씁니다.
+  `ops window open`/`close` 중 하나를 다시 실행하세요.
+- 로그인은 되지만 403 "역할 없음": 토큰에 `roles` 클레임이 없습니다. 역할을 할당한 뒤 로그아웃(`/.auth/logout`)하고 다시 로그인합니다.
+- 상세 화면의 실명이 "원천 데이터 없음": 동의 컨테이너(`consent-<동의 ID>`)가 없거나 만료 처리되었습니다(가명 그대로 표시).
+
+### 알려진 제약(P4b)
+
+- Easy Auth는 클라이언트 비밀 없이 ID 토큰(암시적 흐름)만 씁니다. 운영 설계는 Key Vault 비밀 또는 페더레이션 자격 증명으로
+  권한 부여 코드 흐름을 쓰고, 콘솔은 internal 환경 + Application Gateway WAF 뒤에 둡니다.
+- 콘솔 인증 흐름(실제 Entra 로그인 → 결정)은 자동 테스트가 없습니다. smoke는 로그인 없는 접근이 Entra로 리디렉션되는지만 봅니다.
+- 목록은 Cosmos 교차 파티션 `ORDER BY updated_at` 최근 50건입니다(페이지 없음).
+- 콘솔이 보낸 결정의 결과는 상세 화면 새로 고침(진행 중 10초 자동)으로 확인합니다. 큐 처리 실패는 오케스트레이터 로그에 남습니다.
```

- [ ] **Step 2: 커밋**

```powershell
git add README.md
git commit -m "docs: README P4b section - officer console, roles, deployment, troubleshooting" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>" -m "Copilot-Session: 05f1ca1b-6073-417d-bfb1-6b546f82cb58"
```

- [ ] **Step 3: 전체 테스트**

```powershell
$env:RUN_INFRA_TESTS = '1'; .\.venv\Scripts\python -m pytest -q
```

Expected: `517 passed`(인프라 테스트 포함, skip 0).

- [ ] **Step 4: 실제 배포**

각 명령의 소요 시간을 기록하며 실행한다(시작·종료 시각).

```powershell
python -m ops resume                  # 선택: Service Bus 이벤트까지 보려면
python -m ops window open --acr       # 프로비저닝: Graph 앱 등록, id-console·역할, ca-console(자리표시), CONSOLE_IP
python -m ops deploy console          # 콘솔 이미지 ACR Build → 프로비저닝(SERVICE_CONSOLE_IMAGE_NAME)
python -m ops deploy orchestrator     # Function zip(P4a 리뷰 수정분 포함)
python -m ops window close
azd env get-values | Select-String CONSOLE_
```

Expected:
- `window open`이 Graph 확장 리소스까지 성공한다. `azd`가 확장을 처리하지 못하면(배포 위험 1) 오류를 기록하고 멈춘다.
- `deploy console`: `배포했습니다: <acr>/console:<태그>, …` 형태.
- `CONSOLE_APP_NAME`, `CONSOLE_URL`(`https://ca-console.<기본 도메인>`), `CONSOLE_CLIENT_ID`, `CONSOLE_IP`(운영자 IP)가 있다.

- [ ] **Step 5: smoke**

```powershell
python -m ops smoke
```

Expected: `smoke 성공: …`(P4a와 같은 25개 점검) 다음 줄에 `콘솔 관문 확인: 로그인 없는 요청은 Entra 로그인으로 리디렉션(login.microsoftonline.com, client_id <CONSOLE_CLIENT_ID>)`. 403이면 IP(README 문제 대응), 200이면 Easy Auth가 꺼진 것이므로 멈추고 `authConfigs`를 확인한다.

- [ ] **Step 6: 브라우저 확인(수동 체크리스트)**

허용 IP의 브라우저에서 `CONSOLE_URL`을 연다.

1. Entra 로그인 후 신청 목록이 보인다(역할 `CreditOfficer`). 로그인 화면에서 `AADSTS50011`이면 배포 위험 2.
2. `<CONSOLE_URL>/.auth/me`에서 `user_claims`에 `roles`(값 `CreditOfficer`)가 있다. 클레임 이름이 다르면 배포 위험 5(`auth.py`를 TDD로 고친다).
3. `새 신청` → `demo-cafe-001`, 금액 50,000,000, 기간 12 → 접수 대기 화면이 자동 새로고침되다 상세로 바뀐다. 목록이 오류면 배포 위험 4, 상세가 오류면 배포 위험 3(콘솔 로그).
4. 상태가 `awaiting_officer`가 되면 상세에 실명(상호·대표자)과 claim 근거 값·참조 거래가 보인다(`pii` 표시 “실명 복원”).
5. 금액을 정책 한도보다 크게 넣어 결정하면 422 오류 문구가 보이고 큐에 가지 않는다(상태 변화 없음).
6. `조건부 승인`(조건 한 줄 이상) 또는 `승인`으로 결정 → “결정을 보냈습니다” → 상세 새로 고침 시 최종 상태(`conditionally_approved`·`approved`)와 타임라인에 심사역 결정이 보인다.
7. Entra 관리 센터 → 엔터프라이즈 애플리케이션 → `AMA Lending 심사 콘솔 (<환경>)`에서 운영자 역할을 `Auditor`로 바꾸고 `/.auth/logout` 후 다시 로그인 → 같은 신청이 가명 토큰으로 보이고 결정 폼·새 신청 버튼이 없다. 확인 후 `CreditOfficer`로 되돌린다.

각 항목의 결과(성공·실패와 화면 요지)를 기록한다. 실패한 항목은 고치거나 README 알려진 제약에 적는다.

- [ ] **Step 7: 정리**

```powershell
python -m ops pause                   # 비용 절감(데모 전까지)
```

- [ ] **Step 8: 실측 기록 커밋**

README P4b "배포 순서" 아래에 실제 배포 실측(명령별 시간, smoke 결과, 브라우저 체크리스트 결과)을 적는다.

```powershell
git add README.md
git commit -m "docs: record P4b deployment and console login check" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>" -m "Copilot-Session: 05f1ca1b-6073-417d-bfb1-6b546f82cb58"
```
