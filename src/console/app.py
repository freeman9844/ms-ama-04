"""심사역 콘솔(FastAPI + Jinja2 + htmx).

인증은 Container Apps 기본 인증(Easy Auth)이 맡고, 앱은 X-MS-CLIENT-PRINCIPAL의 앱 역할로 권한을 정한다.
결정·신규 신청·사후 재생·재조정 결정·동의 명령·이의제기는 큐 메시지로만 오케스트레이터에 전달한다
(콘솔은 Cosmos 읽기 전용). /apply·/borrower는 콘솔 역할 없이 셀러가 직접 신청하고 신청 현황·상환 플랜·
안내·동의·이의제기를 보는 고객 콘솔이다(데모라 로그인한 사용자가 고객을 고른다).
"""

import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
import time
from functools import partial
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, quote, urlencode

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from agents.collections import RepaymentCase, message_issues, proposal_text_issues
from agents.repayment import current_quoted_installment
from console import i18n
from console.i18n import _
from agents.monitoring import WEEKS
from agents.pretranslate import RawScope, active_scope, raw_container, translation_scope
from agents.runtime import AgentError, GatewayBlocked, GatewayThrottled
from console.assessment import assessment_panels
from console.auth import (
    DEMO_LOGIN_ID,
    DEMO_SESSION_COOKIE,
    DEMO_SESSION_TTL_S,
    PRINCIPAL_HEADER,
    Principal,
    csrf_token,
    demo_session_token,
    parse_principal,
    safe_next,
    verify_csrf,
    verify_demo_session,
)
from console.customer import APPLY_ERRORS, CONSENT_ITEMS, customer_status, parse_requested_usd_cents, validate_apply, validate_request
from shared.portfolio import in_portfolio
from console.dashboard import (
    display_usd,
    usd_tick,
    POLICY_DECISIONS,
    RECOMMENDATION_LABELS,
    detail_summary,
    home_summary,
    loan_summary,
    loans_summary,
    ops_summary,
)
from console.monitoring import (
    ACTION_LABELS,
    MONITORING_FILTERS,
    MONITORING_LABELS,
    PLAN_OPTION_LABELS,
    REPAYMENT_LABELS,
    SEVERITY_LABELS,
    TRIGGER_LABELS,
    MonitoringStore,
    borrower_plan,
    installment_label,
    load_monitoring_raw,
    loan_view,
    stage_label,
)
from console.officer_chat import CHAT_ERRORS, CHAT_SUGGESTIONS, build_snapshot, clean_history, validate_question
from console.opinion import opinion_panel, policy_cents_ids, review_history
from console.ops import (
    AXIS_LABELS,
    RECENT_RUN,
    STEP_LABELS,
    OpsStore,
    default_run,
    ops_view,
    run_options,
    scope_docs,
    sorted_runs,
)
from console.privacy import DEMO_ACTIONS, LABEL_FILTERS, PrivacyStore
from console.statuses import STATUS_LABELS, TERMINAL
from console import translate
from console.translate import MachineTranslation, PageTexts, catalog, data_label, mt_filters, page_scope, validate_texts
from console.views import DECIDABLE, RawStore, case_masker, decision_for_display, detail_view, load_raw, reason_text
from journey.models import (
    CONSENT_PURPOSE,
    CONSENT_TTL_DAYS,
    KST,
    CollectionsDecision,
    ConsentCommand,
    GrievanceDecision,
    GrievanceSubmission,
    UsdGrievanceDecision,
    UsdGrievanceSubmission,
    OfferExpire,
    OfficerMessage,
    PlanChoice,
    PlanDecision,
    ReplayRequest,
    RestructureDecision,
    UsdStartRequest,
    replay_held,
    replay_in_progress,
)
from journey.validation import REREVIEW_ERRORS, usd_rereview_error, validate_officer_decision, validate_rereview_decision
from shared.opinions import archived_reply, opinion_state, published_opinion
from shared.pseudonymize import Pseudonymizer
from shared.product_context import product_of
from shared.money import format_usd, parse_usd_cents
from shared.sellers import DEMO_SELLERS, USD_CHANNEL_NAMES, USD_ONLY_SELLER_IDS, USD_SELLERS, seller_label, seller_name
from shared.contracts import USD_V1

HERE = Path(__file__).parent
START_QUEUE = "applications-start"
DECISION_QUEUE = "officer-decisions"
REPLAY_QUEUE = "monitoring-replay"
RESTRUCTURE_QUEUE = "restructure-decisions"
CONSENT_COMMAND_QUEUE = "consent-commands"
GRIEVANCE_QUEUE = "grievances"
GRIEVANCE_DECISION_QUEUE = "grievance-decisions"
MAX_FORM_BYTES = 16 * 1024
MAX_CHAT_BYTES = 128 * 1024
CSRF_HEADER = "X-CSRF-Token"
chat_log = logging.getLogger("console.chat")
mt_log = logging.getLogger("console.translate")
LIST_LIMIT = 50
# 홈 신청 목록 건수(테스트 신청 제외). 홈 통계는 건수 제한 없이 모든 신청을 집계한다.
HOME_LIST_LIMIT = 20
DEMO_APPLICANTS = (*(s.applicant_id for s in DEMO_SELLERS), *USD_ONLY_SELLER_IDS)
# 큐로 보낸 작업이 반영될 때까지 결과 화면을 짧게 다시 읽는다(최대 WATCH_LIMIT회).
# 신청 상세(/applications/{id})의 모든 자동 새로 고침 주기.
DETAIL_REFRESH_SECONDS = 10
# 사후 모니터링(/loans/{id})의 모든 자동 새로 고침 주기(재생 진행·요청 반영 대기).
MONITORING_REFRESH_SECONDS = 7
WATCH_LIMIT = 12
# 고객 신청 현황(/apply/{id})이 문서 생성을 기다리는 최대 새로 고침 횟수(3초 × 40 = 2분).
APPLY_WAIT_LIMIT = 40
# 고객 콘솔 '내 대출 관리' 메뉴(경로 끝 이름 → 템플릿). status는 /borrower/{id} 자체다.
BORROWER_PAGES = {
    "status": "borrower.html", "plans": "borrower_plans.html", "notices": "borrower_notices.html",
    "consents": "borrower_consents.html", "grievances": "borrower_grievances.html",
}
# 접수한 심사역 결정 표시의 최대 유지 시간. 큐 처리가 멈춰도 이 시간이 지나면 다시 결정할 수 있다.
DECISION_MARK_SECONDS = 300
HTMX_SRC = "https://unpkg.com/htmx.org@2.0.11/dist/htmx.min.js"
HTMX_SRI = "sha384-2OatzQy1H+Zd/IIrjr1TcuDGqLXeHhbooAyJY1KdQMKnr4LZ22k31GBLdYKHmVjg"
# form-action은 넣지 않는다: Easy Auth 세션 만료 시 로그인 리디렉션(login.microsoftonline.com)을 막는다.
CSP = (
    "default-src 'self'; script-src 'self' https://unpkg.com; style-src 'self'; "
    "img-src 'self' data:; frame-ancestors 'none'"
)
DECISION_ERRORS = {
    "invalid_payload": "입력 형식이 올바르지 않습니다.",
    "reason_required": "사유를 입력해야 합니다.",
    "override_required": "정책 결과상 거절입니다. 승인하려면 예외 승인 확인이 필요합니다.",
    "amount_required": "승인 금액을 입력해야 합니다.",
    "amount_exceeds_policy": "승인 금액이 정책 한도를 넘습니다.",
    "amount_exceeds_product": "예외 승인 금액이 상품 최대 한도(1,000만 원)를 넘습니다.",
    "conditional_retired": "조건부 승인은 더 이상 쓰지 않습니다. 승인 또는 거절을 고르세요.",
    **REREVIEW_ERRORS,
}


class ApplicationStore(Protocol):
    def for_applicant(self, applicant_id: str, *, legacy: bool = False) -> list[dict[str, Any]]: ...

    def list_recent(self, limit: int, *, legacy: bool = False) -> list[dict[str, Any]]: ...

    def list_status_rows(self, *, legacy: bool = False) -> list[dict[str, Any]]: ...

    def get(self, application_id: str) -> dict[str, Any] | None: ...


class QueueSender(Protocol):
    def send(self, queue: str, message: dict[str, Any]) -> None: ...


class TranslationReader(Protocol):
    def for_owner(self, owner: str) -> dict[str, str]: ...


def _new_application_id() -> str:
    return f"app-{datetime.now(KST):%Y%m%d}-{secrets.token_hex(3)}"


def _new_grievance_id() -> str:
    return f"grv-{secrets.token_hex(6)}"


@dataclass
class ConsoleDeps:
    store: ApplicationStore
    raw: RawStore
    queue: QueueSender
    pseudonym_key: bytes
    new_application_id: Callable[[], str] = field(default=_new_application_id)
    # 데모 모드(Easy Auth 끔): 운영자 IP 허용 목록만 남고 모든 요청을 이 주체로 처리한다.
    demo_principal: Principal | None = None
    # 데모 모드의 장면용 로그인 화면(/login). 인증이 아니며 결정 기록 주체는 그대로 demo_principal이다(ADR-007).
    demo_login: bool = False
    monitoring: MonitoringStore | None = None
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(KST))
    privacy: PrivacyStore | None = None
    new_grievance_id: Callable[[], str] = field(default=_new_grievance_id)
    ops: OpsStore | None = None
    # 심사역 결정 채팅(agents.officer_chat.OfficerChat). 없으면 결정 탭에 "채팅을 쓸 수 없습니다"만 보인다.
    chat: Any | None = None
    # 영문 화면의 데이터 기계 번역(agents.translator.Translator). 없으면 카탈로그에 있는 데이터만 영문으로 바뀐다.
    translator: Any | None = None
    # 오케스트레이터가 미리 번역해 둔 문구(translations 컨테이너). 없으면 렌더 때 저장 번역을 쓰지 않는다.
    translations: TranslationReader | None = None
    usd_origination_enabled: bool = False


def state_tag(*docs: Any) -> str:
    """문서 상태의 짧은 지문. 큐 처리 전후로 값이 바뀌었는지만 본다."""
    body = json.dumps(docs, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha1(body.encode()).hexdigest()[:12]


def watch_url(request: Request, path: str, tag: str) -> str | None:
    """?since=<보낼 때 지문>이 지금 지문과 같으면 아직 반영 전이다. 다음 확인 URL을 돌려준다."""
    since = request.query_params.get("since")
    try:
        n = int(request.query_params.get("n", "0"))
    except ValueError:
        n = WATCH_LIMIT
    if not since or since != tag or n >= WATCH_LIMIT:
        return None
    return f"{path}?since={quote(since, safe='')}&n={n + 1}"


def retry_url(request: Request, path: str, limit: int) -> str | None:
    """문서가 아직 없는 화면의 다음 새로 고침 URL. ?n=으로 횟수를 세어 limit회 뒤에는 멈춘다(없는 ID를 끝없이 읽지 않게)."""
    try:
        n = int(request.query_params.get("n", "0"))
    except ValueError:
        n = limit
    return None if n >= limit else f"{path}?n={n + 1}"


def _krw(value: Any) -> str:
    if not isinstance(value, int) or isinstance(value, bool):
        return "-"
    return f"₩{value:,}" if i18n.current() == "en" else f"{value:,}원"


def _kst(value: Any) -> str:
    """ISO 시각을 KST 'YYYY-MM-DD HH:MM'으로 보인다. 읽을 수 없으면 원문 그대로."""
    if not isinstance(value, str):
        return "-" if value is None else str(value)
    try:
        d = datetime.fromisoformat(value)
    except ValueError:
        return value
    return (d.astimezone(KST) if d.tzinfo else d).strftime("%Y-%m-%d %H:%M")


def _templates() -> Environment:
    env = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=True, extensions=["jinja2.ext.i18n"])
    env.install_gettext_callables(gettext=i18n.gettext, ngettext=lambda s, p, n: i18n.gettext(s if n == 1 else p),
                                  pgettext=i18n.pgettext, newstyle=True)
    env.filters["krw"] = _krw
    env.filters["usd_cents"] = display_usd
    env.filters["usd_tick"] = usd_tick
    env.globals["reason_text"] = reason_text
    env.filters["policy_cents_ids"] = policy_cents_ids
    env.filters["usd_input"] = lambda cents: format_usd(cents)[1:].replace(",", "").removesuffix(".00") if type(cents) is int else ""
    env.filters["usd_seller"] = lambda s: USD_SELLERS[s].business_name if s in USD_SELLERS else s
    env.filters["channel"] = lambda s: USD_CHANNEL_NAMES.get(s, s)
    env.filters["kst"] = _kst
    env.filters["status_label"] = lambda s: i18n.label(STATUS_LABELS, s)
    env.filters["path"] = lambda s: quote(str(s), safe="")
    env.filters["monitoring_label"] = lambda s: i18n.label(MONITORING_LABELS, s, "재생 전")
    env.filters["severity_label"] = lambda s: i18n.label(SEVERITY_LABELS, s)
    env.filters["action_label"] = lambda s: i18n.label(ACTION_LABELS, s)
    env.filters["trigger_label"] = lambda s: i18n.label(TRIGGER_LABELS, s)
    env.filters["stage_label"] = stage_label
    env.filters["repayment_label"] = lambda s: i18n.label(REPAYMENT_LABELS, s)
    env.filters["seller_label"] = lambda s: data_label(seller_label(s))
    env.filters["seller_name"] = lambda s: translate.data(seller_name(s))
    env.filters["seller_initial"] = lambda s: str(translate.data(seller_name(s) or (
        USD_SELLERS[s].business_name if s in USD_SELLERS else None)) or s)[:1]
    env.filters["recommendation_label"] = lambda s: i18n.label(RECOMMENDATION_LABELS, s)
    env.filters["opinion_state"] = opinion_state
    env.filters["archived_reply"] = archived_reply
    env.filters["published_opinion"] = published_opinion
    env.filters["policy_label"] = lambda s: i18n.gettext(POLICY_DECISIONS[s][0]) if s in POLICY_DECISIONS else s or "-"
    for name, labels in (LABEL_FILTERS | MONITORING_FILTERS).items():
        env.filters[name] = lambda s, labels=labels: i18n.label(labels, s)
    env.filters["installment_label"] = installment_label
    env.filters["pct"] = lambda v: f"{v * 100:.1f}%" if isinstance(v, int | float) else "-"
    env.filters["usd"] = lambda v: f"${v:,.4f}" if isinstance(v, int | float) else "-"
    env.filters["num"] = lambda v, d=1: f"{v:.{d}f}" if isinstance(v, int | float) else "-"
    env.globals.update(
        htmx_src=HTMX_SRC, htmx_sri=HTMX_SRI, demo_applicants=DEMO_APPLICANTS, demo_actions=DEMO_ACTIONS,
        monitoring_refresh_seconds=MONITORING_REFRESH_SECONDS, detail_refresh_seconds=DETAIL_REFRESH_SECONDS,
        locale=i18n.current, lang_url=i18n.lang_url,
    )
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


class _BadBody(Exception):
    pass


@dataclass(frozen=True)
class Posted:
    """CSRF를 확인한 폼 POST: 로그인 주체와 폼 값."""

    user: Principal
    form: dict[str, str]


async def _json_body(request: Request) -> dict[str, Any]:
    if not request.headers.get("content-type", "").startswith("application/json"):
        raise HTTPException(415, "json required")
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_CHAT_BYTES:
            raise HTTPException(413, "body too large")
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise _BadBody from e
    if not isinstance(parsed, dict):
        raise _BadBody
    return parsed


def _chat_error(code: str, usd: bool = False) -> JSONResponse:
    status, message = CHAT_ERRORS[code]
    return JSONResponse({"error": i18n.CATALOGS["en"][message] if usd else i18n.gettext(message)}, status_code=status)


_TOO_LONG = re.compile(r"문구가 (\d+)자를 넘습니다\((\d+)자\)\.")
_BANNED = re.compile(r"금지 표현 '(.+)'이\(가\) 들어 있습니다\.")


def _issue_text(issue: str) -> str:
    """agents.collections.message_issues의 한국어 사유를 화면 언어로. 금지 표현 자체는 고객 문구라 그대로 둔다."""
    if m := _TOO_LONG.fullmatch(issue):
        return _("문구가 %(max)s자를 넘습니다(%(n)s자).", max=m.group(1), n=m.group(2))
    if m := _BANNED.fullmatch(issue):
        return _("금지 표현 '%(phrase)s'이(가) 들어 있습니다.", phrase=m.group(1))
    return _(issue)


def _grant_script(grant: dict[str, Any]) -> str:
    body = json.dumps(grant, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return f'<script type="application/json" class="mt-grant">{body}</script>'


def create_app(deps: ConsoleDeps) -> FastAPI:
    app = FastAPI(title="AMA Lending officer console", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.deps = deps
    templates = _templates()
    machine = MachineTranslation(deps.translator) if deps.translator is not None else None
    templates.filters.update(mt_filters(machine, deps.pseudonym_key))
    templates.globals["mt_enabled"] = machine is not None
    # 배포 뒤 브라우저가 옛 mt.js를 쓰지 않도록 내용 해시를 쿼리로 붙인다.
    templates.globals["mt_js_version"] = hashlib.sha256((HERE / "static" / "mt.js").read_bytes()).hexdigest()[:10]
    # 레플리카가 여러 개여도 같은 토큰을 쓰도록 가명처리 키에서 파생한다.
    csrf_key = hmac.new(deps.pseudonym_key, b"console-csrf", hashlib.sha256).digest()
    login_key = hmac.new(deps.pseudonym_key, b"console-demo-login", hashlib.sha256).digest()
    login_on = deps.demo_principal is not None and deps.demo_login
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    app.add_middleware(i18n.LocaleMiddleware)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if not request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def render(name: str, user: Principal | None, status_code: int = 200, texts: PageTexts | None = None,
               **ctx: Any) -> HTMLResponse:
        csrf = csrf_token(csrf_key, user.user_id) if user else ""

        def draw() -> str:
            return templates.get_template(name).render(user=user, csrf=csrf, demo=deps.demo_principal is not None,
                                                       demo_login=login_on,
                                                       origination_open=deps.usd_origination_enabled, **ctx)

        if i18n.current() == i18n.SOURCE:
            return HTMLResponse(draw(), status_code=status_code)
        page = texts or PageTexts(Pseudonymizer(deps.pseudonym_key), allow_pending=False)
        with page_scope(page):
            html = draw()
        if page.requested and user and page.scope:
            tickets = {t: translate.mt_ticket(deps.pseudonym_key, user.user_id, page.scope, t) for t in page.requested}
            script = _grant_script({"scope": page.scope, "tickets": tickets})
            at = html.rfind("</body>")
            html = html + script if at < 0 else html[:at] + script + html[at:]
        return HTMLResponse(html, status_code=status_code)

    def stored_translations(owner: str) -> dict[str, str]:
        try:
            return deps.translations.for_owner(owner) if deps.translations else {}
        except Exception:  # noqa: BLE001
            mt_log.warning("stored translations unavailable owner=%s", owner)
            return {}

    def mt_active(scope: RawScope, *, check_raw: bool = False) -> bool:
        try:
            if check_raw and not all(deps.raw.get_json(raw_container(cid), blob) is not None for cid, blob in scope):
                return False
            return bool(deps.privacy and active_scope(scope, deps.privacy.get_consent))
        except Exception as e:  # noqa: BLE001
            mt_log.warning("translation scope unavailable error=%s", type(e).__name__)
            return False

    def page_texts(raw: Any, owner: str, scope: RawScope) -> PageTexts | None:
        if i18n.current() == i18n.SOURCE:
            return None
        stored = stored_translations(owner)
        if raw is None:
            return PageTexts(Pseudonymizer(deps.pseudonym_key), stored, allow_pending=False)
        page = PageTexts.for_case(deps.pseudonym_key, raw.profile, raw.transactions, stored)
        page.scope, page.allow_pending = scope, mt_active(scope)
        return page

    def read_raw(load: Callable[[], Any], owner: str) -> tuple[Any, bool]:
        """원천 읽기 오류는 (None, False)로 돌려준다. 예외 종류만 기록한다."""
        try:
            return load(), True
        except Exception as e:  # noqa: BLE001
            mt_log.warning("raw unavailable for page texts owner=%s error=%s", owner, type(e).__name__)
            return None, False

    def require_demo_session(request: Request) -> None:
        if verify_demo_session(login_key, request.cookies.get(DEMO_SESSION_COOKIE), int(deps.clock().timestamp())):
            return
        if request.headers.get("HX-Request"):
            raise HTTPException(401, "login required", headers={"HX-Redirect": "/login"})
        if request.method == "GET":
            target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
            raise HTTPException(303, "login required", headers={"Location": "/login?next=" + quote(target, safe="/")})
        raise HTTPException(401, "login required")

    def viewer(request: Request) -> Principal:
        if login_on:
            require_demo_session(request)
        user = deps.demo_principal or parse_principal(request.headers.get(PRINCIPAL_HEADER))
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

    # 폼 POST는 이벤트 루프에서 본문만 읽고(아래 의존성), 저장소·큐·화면 렌더는 동기 엔드포인트로 둬서
    # FastAPI가 스레드 풀에서 돌린다. 느린 Cosmos·Blob·Service Bus 호출이 워커의 다른 요청을 막지 않게 한다.
    async def decider_form(request: Request) -> Posted:
        user = decider(request)
        return Posted(user, await checked_form(request, user))

    # 접수한 심사역 결정 표시(application_id → (보낼 때 문서 지문, 만료 시각)). 오케스트레이터는 먼저 온 결정만
    # 반영하므로, 같은 문서 상태에 온 두 번째 결정은 202로 받아 조용히 버려지지 않게 여기서 409로 돌려보낸다.
    # 문서가 바뀌면(반영·거부 기록) 지문이 달라져 표시는 저절로 풀린다. 프로세스 메모리라 레플리카 하나(maxReplicas 1)를 전제한다.
    decision_marks: dict[str, tuple[str, float]] = {}
    decision_lock = threading.Lock()

    def decision_pending(application_id: str, state: str) -> bool:
        with decision_lock:
            mark = decision_marks.get(application_id)
            return mark is not None and mark[0] == state and mark[1] > time.monotonic()

    def reserve_decision(application_id: str, state: str) -> bool:
        now = time.monotonic()
        with decision_lock:
            for key in [k for k, (_tag, expires) in decision_marks.items() if expires <= now]:
                del decision_marks[key]
            mark = decision_marks.get(application_id)
            if mark is not None and mark[0] == state:
                return False
            decision_marks[application_id] = (state, now + DECISION_MARK_SECONDS)
            return True

    def release_decision(application_id: str, state: str) -> None:
        with decision_lock:
            if (decision_marks.get(application_id) or ("",))[0] == state:
                del decision_marks[application_id]

    def detail(user: Principal, doc: dict[str, Any], status_code: int = 200, **ctx: Any) -> HTMLResponse:
        state = state_tag(doc)
        ctx.setdefault("decision_pending", decision_pending(doc["application_id"], state))
        raw, _ = read_raw(lambda: load_raw(deps.raw, doc), doc["application_id"])
        view = detail_view(doc, raw, deps.pseudonym_key, restore=user.can_restore)
        in_progress = doc.get("status") not in DECIDABLE | TERMINAL
        consents = deps.privacy.consents_for_application(doc["application_id"]) if deps.privacy else None
        usd = bool(product_of(doc))
        texts = None if usd else page_texts(raw, doc["application_id"], translation_scope("applications", doc))
        return render("detail.html", user, status_code, texts=texts, usd=usd,
                      v=view, ui=detail_summary(view), panels=assessment_panels(view),
                      op=opinion_panel(view), history=review_history(doc, published=bool(view["assessment"])),
                      in_progress=in_progress, state=state,
                      consents=consents, chat_enabled=deps.chat is not None, chat_suggestions=CHAT_SUGGESTIONS,
                      **ctx)

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    def login_page(next_url: str, status_code: int = 200, error: str | None = None) -> HTMLResponse:
        return render("login.html", None, status_code, next_url=next_url, login_id=DEMO_LOGIN_ID, error=error)

    @app.get("/login", response_class=HTMLResponse)
    def login_form(next: str | None = None) -> HTMLResponse:
        if not login_on:
            raise HTTPException(404)
        return login_page(safe_next(next))

    @app.post("/login")
    async def login_submit(request: Request):
        if not login_on:
            raise HTTPException(404)
        form = await _form(request)
        target = safe_next(form.get("next"))
        if form.get("username", "").strip() != DEMO_LOGIN_ID or not form.get("password"):
            return login_page(target, 422, error=_("아이디 또는 비밀번호가 올바르지 않습니다."))
        response = RedirectResponse(target, status_code=303)
        response.set_cookie(DEMO_SESSION_COOKIE, demo_session_token(login_key, int(deps.clock().timestamp())),
                            max_age=DEMO_SESSION_TTL_S, path="/", secure=True, httponly=True, samesite="lax")
        return response

    @app.get("/logout")
    def logout() -> RedirectResponse:
        if not login_on:
            raise HTTPException(404)
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(DEMO_SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
        return response

    @app.get("/legacy", response_class=HTMLResponse)
    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        user = viewer(request)
        legacy = request.url.path == "/legacy"
        limit = 100 if legacy else HOME_LIST_LIMIT
        rows = deps.store.list_recent(limit, legacy=legacy)
        ui = home_summary(deps.store.list_status_rows(legacy=legacy))
        return render("list.html", user, rows=rows, ui=ui, limit=limit, prefix="/legacy" if legacy else "")

    @app.get("/legacy/applications/rows", response_class=HTMLResponse)
    @app.get("/applications/rows", response_class=HTMLResponse)
    def rows(request: Request) -> HTMLResponse:
        user = viewer(request)
        legacy = request.url.path.startswith("/legacy/")
        return render("_rows.html", user, rows=deps.store.list_recent(100 if legacy else HOME_LIST_LIMIT, legacy=legacy))

    @app.get("/applications/new", response_class=HTMLResponse)
    def new_form(request: Request) -> HTMLResponse:
        return render("new.html", decider(request))

    def start(form: dict[str, str], source: str) -> UsdStartRequest:
        req = UsdStartRequest(**USD_V1.model_dump(), application_id=deps.new_application_id(),
                              applicant_id=form["applicant_id"].strip(), source=source,
                              requested_amount_cents=parse_requested_usd_cents(form["requested_amount_usd"]),
                              requested_tenor_months=_int(form["requested_tenor_months"]), submitted_at=deps.clock())
        deps.queue.send(START_QUEUE, req.model_dump(mode="json", exclude_none=True))
        return req

    @app.post("/applications")
    def submit(posted: Posted = Depends(decider_form)):
        user, form = posted.user, posted.form
        if code := validate_request(form, DEMO_APPLICANTS):
            return render("new.html", user, 422, error=_(APPLY_ERRORS[code]), form=form)
        if not deps.usd_origination_enabled:
            return render("new.html", user, 503, error=_("신규 USD 신청 접수가 아직 열리지 않았습니다."), form=form)
        req = start(form, "console")
        return RedirectResponse(f"/applications/{quote(req.application_id, safe='')}", status_code=303)

    def customer(request: Request) -> Principal:
        """고객 페이지는 콘솔 역할을 묻지 않는다. 로그인(또는 데모 주체)만 있으면 된다."""
        user = deps.demo_principal or parse_principal(request.headers.get(PRINCIPAL_HEADER))
        if user is None:
            raise HTTPException(401, "login required")
        return user

    async def customer_form(request: Request) -> Posted:
        user = customer(request)
        return Posted(user, await checked_form(request, user))

    def apply_form(user: Principal, status_code: int = 200, **ctx: Any) -> HTMLResponse:
        return render("apply.html", user, status_code, consent_items=CONSENT_ITEMS, purpose=CONSENT_PURPOSE,
                      ttl_days=CONSENT_TTL_DAYS, **ctx)

    @app.get("/apply", response_class=HTMLResponse)
    def apply_page(request: Request, applicant_id: str | None = None):
        user = customer(request)
        if applicant_id not in DEMO_APPLICANTS:
            return RedirectResponse("/borrower", status_code=303)
        return apply_form(user, applicant_id=applicant_id)

    @app.post("/apply")
    def apply_submit(posted: Posted = Depends(customer_form)):
        user, form = posted.user, posted.form
        chosen = form.get("applicant_id", "").strip()
        if chosen not in DEMO_APPLICANTS:
            return RedirectResponse("/borrower", status_code=303)
        code = validate_apply(form, DEMO_APPLICANTS)
        if code:
            return apply_form(user, 422, error=_(APPLY_ERRORS[code]), error_code=code, form=form,
                              applicant_id=chosen)
        if not deps.usd_origination_enabled:
            return apply_form(user, 503, error=_("신규 USD 신청 접수가 아직 열리지 않았습니다."), applicant_id=chosen, form=form)
        req = start(form, "customer")
        return RedirectResponse(f"/apply/{quote(req.application_id, safe='')}?applicant_id={quote(req.applicant_id, safe='')}",
                                status_code=303)

    @app.get("/apply/{application_id}", response_class=HTMLResponse)
    def apply_status(application_id: str, request: Request, applicant_id: str | None = None) -> HTMLResponse:
        user = customer(request)
        doc = deps.store.get(application_id)
        if doc is not None and doc.get("source") != "customer":
            raise HTTPException(404, "not a customer application")
        # 문서가 생기기 전(접수 직후 대기)에는 신청할 때 고른 사업자로 메뉴를 유지한다.
        chosen = doc.get("applicant_id") if doc else (applicant_id if applicant_id in DEMO_APPLICANTS else None)
        wait_url = None if doc else retry_url(request, f"/apply/{quote(application_id, safe='')}", APPLY_WAIT_LIMIT)
        if wait_url and chosen:
            wait_url += f"&applicant_id={quote(chosen, safe='')}"
        return render("apply_status.html", user, application_id=application_id, s=customer_status(doc),
                      seller=(USD_SELLERS[chosen].business_name if doc and product_of(doc) and chosen in USD_SELLERS
                              else data_label(seller_label(chosen)) if chosen else None), waiting=doc is None,
                      wait_url=wait_url, applicant_id=chosen,
                      requested=doc.get("requested_amount_cents", doc.get("requested_amount_krw")) if doc else None,
                      usd=bool(doc and product_of(doc)), prefix="/legacy" if doc and not product_of(doc) else "",
                      tenor=doc.get("requested_tenor_months") if doc else None)

    @app.get("/applications/{application_id}", response_class=HTMLResponse)
    def show(application_id: str, request: Request) -> HTMLResponse:
        user = viewer(request)
        doc = deps.store.get(application_id)
        path = f"/applications/{quote(application_id, safe='')}"
        if doc is None:
            return render("pending.html", user, 404, application_id=application_id,
                          retry_url=retry_url(request, path, WATCH_LIMIT))
        return detail(user, doc, watch_url=watch_url(request, path, state_tag(doc)))

    @app.post("/applications/{application_id}/decision", response_class=HTMLResponse)
    def decide(application_id: str, posted: Posted = Depends(decider_form)) -> HTMLResponse:
        user, form = posted.user, posted.form
        doc = deps.store.get(application_id)
        if doc is None:
            raise HTTPException(404, "application not found")
        if doc.get("status") not in DECIDABLE:
            raise HTTPException(409, "application is not awaiting an officer decision")
        state = state_tag(doc)
        if form.get("state", state) != state:
            return detail(user, doc, 409, error=_("신청 상태가 바뀌었습니다. 바뀐 내용을 확인한 뒤 다시 결정해 주세요."),
                          form=form)
        taken = _("이미 다른 결정이 접수되었습니다. 반영될 때까지 기다려 주세요.")
        if decision_pending(application_id, state):
            return detail(user, doc, 409, error=taken)
        kind = form.get("decision", "")
        product = product_of(doc)
        amount_text = form.get("amount_usd" if product else "amount_krw", "").strip()
        try:
            amount = None if kind == "decline" or not amount_text else (parse_usd_cents if product else _int)(amount_text)
        except ValueError:
            amount = None
        # 심사역 ID는 폼이 아니라 로그인 주체에서만 가져온다.
        raw_decision = {
            "decision": kind,
            "officer_id": user.user_id,
            **({**product.model_dump(), "amount_cents": amount} if product else {"amount_krw": amount}),
            "reason": form.get("reason", "").strip(),
            "policy_override": form.get("policy_override", "") == "1",
        }
        unexpected = {"currency", "product_version", "language", "amount_cents", "amount_krw" if product else "amount_usd"}
        if unexpected.intersection(form) or (amount is None and amount_text and kind != "decline"):
            decision, error = None, "invalid_payload"
        else:
            decision, error = validate_officer_decision(raw_decision, doc.get("policy"), usd=bool(product))
        if error:
            return detail(user, doc, 422, error=_(DECISION_ERRORS.get(error, error)), error_code=error, form=form)
        if not reserve_decision(application_id, state):
            return detail(user, doc, 409, error=taken)
        message = OfficerMessage(application_id=application_id, decision=decision.model_dump(mode="json"))
        try:
            deps.queue.send(DECISION_QUEUE, message.model_dump(mode="json"))
        except Exception:
            release_decision(application_id, state)
            raise
        return render("sent.html", user, 202, application_id=application_id, decision=decision, since=state)

    @app.post("/applications/{application_id}/chat")
    async def officer_chat(application_id: str, request: Request) -> JSONResponse:
        user = viewer(request)
        if not verify_csrf(csrf_key, user.user_id, request.headers.get(CSRF_HEADER)):
            raise HTTPException(403, "invalid csrf token")
        try:
            body = await _json_body(request)
        except _BadBody:
            return _chat_error("bad_request")
        doc = await run_in_threadpool(deps.store.get, application_id)
        usd = bool(product_of(doc or {}))
        if deps.chat is None:
            return _chat_error("disabled", usd)
        if doc is None:
            return _chat_error("not_found")
        question = validate_question(body.get("question"))
        if question is None:
            return _chat_error("invalid_question", usd)
        # 스냅샷은 원천 없이(raw=None) 저장된 가명 텍스트로만 만든다. 원천은 심사역이 쓴 질문·대화를 그 건의
        # 가명처리기로 가리는 데만 쓴다. 원천을 읽다 오류가 나면 엔티티 없이 가린 문장을 보내지 않고 실패로 답한다.
        raw, readable = await run_in_threadpool(read_raw, lambda: load_raw(deps.raw, doc), application_id)
        if not readable or (usd and raw is None):
            return _chat_error("failed", usd)
        display_name = None if usd else seller_name(doc.get("applicant_id", ""))
        masker = case_masker(raw, deps.pseudonym_key, display_name=display_name)
        translate = None
        if not usd and i18n.current() == "en":
            stored = await run_in_threadpool(stored_translations, application_id)
            page = PageTexts(masker, stored, allow_pending=False)

            def translate(text: str) -> str | None:
                translated = catalog(text) or page.stored_en(text)
                return masker.mask(translated) if translated else None

        try:
            snapshot = build_snapshot(detail_view(doc, None, deps.pseudonym_key, restore=False), translate=translate)
        except ValueError:
            return _chat_error("failed", usd)
        started = time.monotonic()
        result, answer = "ok", None
        try:
            answer = await run_in_threadpool(partial(deps.chat.ask, mask=masker.mask), snapshot,
                                             clean_history(body.get("history")), question, "en" if usd else i18n.current())
        except GatewayThrottled:
            result = "throttled"
        except GatewayBlocked:
            result = "blocked"
        except AgentError:
            result = "failed"
        except Exception:  # noqa: BLE001 - 토큰 발급 실패 등. 본문 없이 결과 코드만 남긴다
            result = "failed"
        chat_log.info(
            "officer_chat application_id=%s result=%s latency_ms=%d input_tokens=%d output_tokens=%d",
            application_id, result, int((time.monotonic() - started) * 1000),
            answer.input_tokens if answer else 0, answer.output_tokens if answer else 0,
        )
        if answer is None:
            return _chat_error(result, usd)
        return JSONResponse({"answer": answer.answer})

    @app.post("/i18n/translate")
    async def machine_translate(request: Request) -> JSONResponse:
        """영문 화면에서 카탈로그에 없는 데이터 문구를 기계 번역한다(static/mt.js). 실패하면 원문을 그대로 둔다."""
        user = viewer(request)
        if not verify_csrf(csrf_key, user.user_id, request.headers.get(CSRF_HEADER)):
            raise HTTPException(403, "invalid csrf token")
        if machine is None:
            return JSONResponse({"error": "disabled"}, status_code=503)
        try:
            body = await _json_body(request)
            texts = validate_texts(body)
        except _BadBody:
            texts = None
        if texts is None:
            return JSONResponse({"error": "bad_request"}, status_code=400)
        scope = translate.verified_scope(deps.pseudonym_key, user.user_id, body, texts)
        if not scope:
            raise HTTPException(403, "invalid translation grant")
        # 정상 클라이언트는 이미 마스킹한 문구를 보낸다(재마스킹은 변화 없음). 원문이 오면 모델에 가기 전에 가린다.
        mask = Pseudonymizer(deps.pseudonym_key).mask
        texts = [mask(t) for t in texts]
        started = time.monotonic()
        result, out, tokens = "ok", None, (0, 0)
        try:
            with i18n.use("en"):
                out, *tokens = await run_in_threadpool(machine.translate_authorized, texts, partial(mt_active, scope, check_raw=True))
        except GatewayThrottled:
            result = "throttled"
        except GatewayBlocked:
            result = "blocked"
        except Exception:  # noqa: BLE001 - AgentError·토큰 발급 실패 등. 본문 없이 결과 코드만 남긴다
            result = "failed"
        mt_log.info(
            "machine_translate result=%s items=%d chars=%d latency_ms=%d input_tokens=%d output_tokens=%d",
            result, len(texts), sum(len(t) for t in texts), int((time.monotonic() - started) * 1000), *tokens,
        )
        if out is None:
            return JSONResponse({"error": result}, status_code={"throttled": 429, "blocked": 422}.get(result, 502))
        return JSONResponse({"translations": out})

    def monitoring_store() -> MonitoringStore:
        if deps.monitoring is None:
            raise HTTPException(404, "monitoring not configured")
        return deps.monitoring

    def loan_or_404(loan_id: str) -> dict[str, Any]:
        loan = monitoring_store().get_loan(loan_id)
        if loan is None:
            raise HTTPException(404, "loan not found")
        return loan

    def loan_alerts(loan: dict[str, Any]) -> list[dict[str, Any]]:
        replay_id = (loan.get("monitoring") or {}).get("replay_id")
        return monitoring_store().list_alerts(loan["id"], replay_id) if replay_id else []

    def loan_sent(user: Principal, loan: dict[str, Any], message: str, note: str | None = None,
                  alerts: list[dict[str, Any]] | None = None) -> HTMLResponse:
        """alerts는 라우트가 이미 읽었으면 넘겨 받아 경보 조회를 한 번만 한다."""
        return render("loan_sent.html", user, 202, loan_id=loan["id"], message=message, note=note,
                      since=state_tag(loan, loan_alerts(loan) if alerts is None else alerts))

    def loan_page(user: Principal, loan: dict[str, Any], status_code: int = 200,
                  request: Request | None = None, alerts: list[dict[str, Any]] | None = None,
                  **ctx: Any) -> HTMLResponse:
        mon = loan.get("monitoring") or {}
        alerts = loan_alerts(loan) if alerts is None else alerts
        path = f"/loans/{quote(loan['id'], safe='')}"
        # 접힌 원장의 열림 상태는 ?ledger=open으로 들고 다녀 새로 고침에도 남게 한다.
        query = [(k, val) for k, val in (request.query_params.multi_items() if request else ()) if k != "ledger"]
        ledger_open = request is not None and request.query_params.get("ledger") == "open"
        if request is not None:
            url = watch_url(request, path, state_tag(loan, alerts))
            ctx["watch_url"] = f"{url}&ledger=open" if url and ledger_open else url
        toggle = query if ledger_open else [*query, ("ledger", "open")]
        ctx.update(ledger_open=ledger_open, ledger_toggle_url=f"{path}?{urlencode(toggle)}" if toggle else path)
        raw = (read_raw(lambda: load_monitoring_raw(deps.raw, loan), loan["id"])[0] if i18n.current() != i18n.SOURCE
               else load_monitoring_raw(deps.raw, loan))
        contacts = monitoring_store().get_contacts(loan["id"], mon["replay_id"]) if mon.get("replay_id") else None
        view = loan_view(loan, alerts, raw, deps.pseudonym_key, restore=user.can_restore, contacts=contacts)
        blocked = replay_in_progress(mon, deps.clock())
        return render("loan.html", user, status_code, texts=page_texts(raw, loan["id"], translation_scope("loans", loan)), v=view, ui=loan_summary(view),
                      usd=bool(product_of(loan)), replay_blocked=blocked,
                      replay_held=replay_held(mon), weeks_left=WEEKS - (mon.get("paused_week") or 0), **ctx)

    @app.get("/legacy/loans", response_class=HTMLResponse)
    @app.get("/loans", response_class=HTMLResponse)
    def loans(request: Request) -> HTMLResponse:
        user = viewer(request)
        legacy = request.url.path.startswith("/legacy/")
        rows = monitoring_store().list_loans(LIST_LIMIT, legacy=legacy)
        return render("loans.html", user, rows=rows, ui=loans_summary(rows), limit=LIST_LIMIT, prefix="/legacy" if legacy else "")

    @app.get("/loans/{loan_id}", response_class=HTMLResponse)
    def loan_detail(loan_id: str, request: Request) -> HTMLResponse:
        user = viewer(request)
        return loan_page(user, loan_or_404(loan_id), request=request)

    def queue_servicing(queue, loan, message, **options):
        data = message.model_dump(mode="json", **options)
        if product := product_of(loan):
            data.update(product.model_dump())
        deps.queue.send(queue, data)

    @app.post("/loans/{loan_id}/replay", response_class=HTMLResponse)
    def replay(loan_id: str, posted: Posted = Depends(decider_form)) -> HTMLResponse:
        user = posted.user
        loan = loan_or_404(loan_id)
        if replay_in_progress(loan.get("monitoring"), deps.clock()):
            return loan_page(user, loan, 409, error=_("이미 재생이 진행 중입니다."))
        if replay_held(loan.get("monitoring")):
            return loan_page(user, loan, 409, error=_("대기 중인 결정과 재개 확인이 끝나야 새 재생을 시작할 수 있습니다."))
        # 기본값(action=start)은 빼서 보낸다: 배포 순서와 관계없이 이전 오케스트레이터(extra=forbid)도 받는다.
        queue_servicing(REPLAY_QUEUE, loan, ReplayRequest(loan_id=loan["id"]), exclude_defaults=True)
        return loan_sent(user, loan, _("사후 13주 재생 요청을 큐에 보냈습니다. 주차별 처리가 끝나면 경보가 표시됩니다."))

    @app.post("/loans/{loan_id}/replay/{action}", response_class=HTMLResponse)
    def replay_resume_or_stop(loan_id: str, action: str, posted: Posted = Depends(decider_form)) -> HTMLResponse:
        user = posted.user
        if action not in ("resume", "stop"):
            raise HTTPException(404, "unknown replay action")
        loan = loan_or_404(loan_id)
        mon = loan.get("monitoring") or {}
        if mon.get("status") != "awaiting_resume":
            return loan_page(user, loan, 409, error=_("재개 확인을 기다리는 재생이 없습니다."))
        message = ReplayRequest(loan_id=loan["id"], action=action, replay_id=mon["replay_id"],
                                paused_week=mon["paused_week"] if action == "resume" else None)
        queue_servicing(REPLAY_QUEUE, loan, message, exclude_none=True)
        weeks_left = WEEKS - (mon.get("paused_week") or 0)
        text = (_("남은 %(n)s주 재생을 이어서 요청했습니다.", n=weeks_left) if action == "resume"
                else _("남은 재생을 종료하도록 요청했습니다."))
        return loan_sent(user, loan, text)

    @app.post("/loans/{loan_id}/alerts/{alert_id}/restructure", response_class=HTMLResponse)
    def restructure(loan_id: str, alert_id: str, posted: Posted = Depends(decider_form)) -> HTMLResponse:
        user, form = posted.user, posted.form
        loan = loan_or_404(loan_id)
        alerts = loan_alerts(loan)
        # 대상은 미납 추심 건(rc-) 또는 재조정 요청이 붙은 옛 경보 문서(ew-). 둘 다 문서 id로 찾는다.
        alert = next((a for a in alerts if a.get("id") == alert_id), None)
        if alert is None:
            raise HTTPException(404, "alert not found")
        if (alert.get("restructure") or {}).get("status") != "pending_officer":
            return loan_page(user, loan, 409, alerts=alerts, error=_("심사역 결정을 기다리는 재조정 요청이 없습니다."))
        reason = form.get("reason", "").strip()
        try:
            # 심사역 ID는 폼이 아니라 로그인 주체에서만 가져온다.
            message = RestructureDecision(
                loan_id=loan["id"], alert_id=alert_id, decision=form.get("decision", ""),
                officer_id=user.user_id, reason=reason,
            )
        except ValidationError:
            return loan_page(user, loan, 422, alerts=alerts, error=_("재조정 결정 형식이 올바르지 않습니다."))
        if not reason:
            return loan_page(user, loan, 422, alerts=alerts, error=_("사유를 입력해야 합니다."))
        # kind(기본값 restructure)는 빼서 보낸다: 배포 순서와 관계없이 이전 오케스트레이터(extra=forbid)도 받는다.
        queue_servicing(RESTRUCTURE_QUEUE, loan, message, exclude_defaults=True)
        return loan_sent(
            user, loan, _("재조정 결정(%(decision)s)을 큐에 보냈습니다.", decision=message.decision),
            note=_("결정이 반영되면 대출 화면에서 재생을 이어갈지 선택하세요.") if replay_held(loan.get("monitoring")) else None,
            alerts=alerts,
        )

    def held_note(loan: dict[str, Any]) -> str | None:
        if replay_held(loan.get("monitoring")):
            return _("다른 대기 항목이 없으면 대출 화면에서 재생을 이어갈지 선택하세요.")
        return None

    @app.post("/loans/{loan_id}/cases/{case_id}/proposal", response_class=HTMLResponse)
    def collections_proposal(loan_id: str, case_id: str, posted: Posted = Depends(decider_form)) -> HTMLResponse:
        """추심 제안 승인·수정·반려. 고객 문구는 보내기 전에 여기서도 같은 규칙으로 검사한다."""
        user, form = posted.user, posted.form
        loan = loan_or_404(loan_id)
        alerts = loan_alerts(loan)
        case = next((a for a in alerts if a.get("id") == case_id and a.get("kind") == "repayment_case"),
                    None)
        if case is None:
            raise HTTPException(404, "case not found")
        proposal = case.get("proposal") or {}
        if proposal.get("status") != "pending_officer":
            return loan_page(user, loan, 409, alerts=alerts, error=_("심사역 승인을 기다리는 추심 제안이 없습니다."))
        if (product_of(case) or product_of(loan)) and current_quoted_installment(loan, case) is None:
            return loan_page(user, loan, 409, alerts=alerts,
                             error="This quote is no longer current. Refresh the loan and review the outstanding balance.")
        decision = form.get("decision", "")
        reason = form.get("reason", "").strip()
        text = form.get("text", "").strip() if decision == "modify" else None
        if decision in ("approve", "modify"):
            send_text = str(proposal.get("text") or "") if text is None else text
            issues = (proposal_text_issues(send_text, str(proposal.get("script_id")), decision, RepaymentCase.from_doc(case))
                      if product_of(case) else message_issues(send_text))
            if issues:
                return loan_page(user, loan, 422, alerts=alerts, error=_("고객 문구를 보낼 수 없습니다: ") + " ".join(_issue_text(i) for i in issues))
        if decision == "reject" and not reason:
            return loan_page(user, loan, 422, alerts=alerts, error=_("반려 사유를 입력해야 합니다."))
        include = form.get("include_hardship") == "1" if decision in ("approve", "modify") and proposal.get("hardship") else None
        try:
            message = CollectionsDecision(
                kind="collections", loan_id=loan["id"], case_id=case_id, decision=decision,
                officer_id=user.user_id, text=text, include_hardship=include, reason=reason,
            )
        except ValidationError:
            return loan_page(user, loan, 422, alerts=alerts, error=_("추심 결정 형식이 올바르지 않습니다."))
        queue_servicing(RESTRUCTURE_QUEUE, loan, message)
        return loan_sent(user, loan, _("추심 제안 결정(%(decision)s)을 큐에 보냈습니다.", decision=message.decision),
                         note=held_note(loan), alerts=alerts)

    @app.post("/loans/{loan_id}/plan/{change_id}", response_class=HTMLResponse)
    def plan_change(loan_id: str, change_id: str, posted: Posted = Depends(decider_form)) -> HTMLResponse:
        user, form = posted.user, posted.form
        loan = loan_or_404(loan_id)
        change = (loan.get("repayment") or {}).get("plan_change")
        if not change or change.get("change_id") != change_id:
            raise HTTPException(404, "plan change not found")
        if change.get("status") != "pending_officer":
            return loan_page(user, loan, 409, error=_("심사역 승인을 기다리는 플랜 변경 요청이 없습니다."))
        reason = form.get("reason", "").strip()
        try:
            message = PlanDecision(
                kind="plan_change", loan_id=loan["id"], change_id=change_id, decision=form.get("decision", ""),
                officer_id=user.user_id, reason=reason,
            )
        except ValidationError:
            return loan_page(user, loan, 422, error=_("플랜 변경 결정 형식이 올바르지 않습니다."))
        if message.decision == "decline" and not reason:
            return loan_page(user, loan, 422, error=_("반려 사유를 입력해야 합니다."))
        queue_servicing(RESTRUCTURE_QUEUE, loan, message)
        return loan_sent(user, loan, _("플랜 변경 결정(%(decision)s)을 큐에 보냈습니다.", decision=message.decision), note=held_note(loan))

    @app.post("/loans/{loan_id}/offer/expire", response_class=HTMLResponse)
    def offer_expire(loan_id: str, posted: Posted = Depends(decider_form)) -> HTMLResponse:
        """고객 응답 없이 계속: 열린 플랜 제안을 닫는다(그 뒤 고객 선택은 오케스트레이터가 버린다)."""
        user = posted.user
        loan = loan_or_404(loan_id)
        offer = (loan.get("repayment") or {}).get("offer")
        if not offer or offer.get("status") != "open":
            return loan_page(user, loan, 409, error=_("고객 응답을 기다리는 플랜 제안이 없습니다."))
        message = OfferExpire(kind="offer_expire", loan_id=loan["id"], offer_id=offer["offer_id"],
                              officer_id=user.user_id)
        queue_servicing(RESTRUCTURE_QUEUE, loan, message)
        return loan_sent(user, loan, _("고객 응답 없이 계속하도록 플랜 제안 만료를 큐에 보냈습니다."), note=held_note(loan))

    # ── 동의 수명주기·이의제기(P5b) ──

    def privacy() -> PrivacyStore:
        if deps.privacy is None:
            raise HTTPException(404, "privacy views not configured")
        return deps.privacy

    def sent(user: Principal, message: str, back_url: str, back_label: str, **ctx: Any) -> HTMLResponse:
        return render("privacy_sent.html", user, 202, message=message, back_url=back_url, back_label=back_label, **ctx)

    def customer_sent(user: Principal, applicant_id: str, page: str, message: str, *, legacy: bool = False) -> HTMLResponse:
        back = {"plans": _("상환 플랜으로"), "consents": _("동의 관리로"), "grievances": _("이의제기로")}[page]
        prefix = "/legacy" if legacy else ""
        return sent(user, message, prefix + borrower_url(applicant_id, page), back,
                    layout="customer_base.html", applicant_id=applicant_id, nav=page, prefix=prefix)

    def borrower_url(applicant_id: str, page: str = "status") -> str:
        base = f"/borrower/{quote(applicant_id, safe='')}"
        return base if page == "status" else f"{base}/{page}"

    def borrower_page(user: Principal, applicant_id: str, page: str = "status", status_code: int = 200, legacy: bool = False,
                      **ctx: Any) -> HTMLResponse:
        store = privacy()
        consents = store.consents_for_applicant(applicant_id, legacy=legacy)
        applications = deps.store.for_applicant(applicant_id, legacy=legacy)
        loans = borrower_loans(applicant_id, applications) if page in {"plans", "notices"} or (
            page == "consents" and legacy and i18n.current() != i18n.SOURCE) else []
        grievances = store.grievances_for_applicant(applicant_id, legacy=legacy)
        rows = [
            {"application_id": doc["application_id"], "received_on": str(doc.get("submitted_at") or "")[:10],
             "amount": doc.get("requested_amount_krw" if legacy else "requested_amount_cents"), "tenor": doc.get("requested_tenor_months"),
             "s": customer_status(doc),
             "link": doc.get("source") == "customer"}
            for doc in sorted(applications, key=lambda d: str(d.get("submitted_at") or ""), reverse=True)
        ]
        return render(
            BORROWER_PAGES[page], user, status_code,
            texts=borrower_texts(applications, loans, consents, grievances, page) if legacy else None,
            applicant_id=applicant_id, consents=consents, rows=rows,
            usd=not legacy, prefix="/legacy" if legacy else "",
            applications=applications, grievances=grievances,
            plans=borrower_plans(loans) if page in {"plans", "notices"} else [], **ctx,
        )

    def borrower_loans(applicant_id: str, applications: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """신청별 대출(대출 id = 신청 id). 모니터링이 없으면 빈 목록."""
        if deps.monitoring is None:
            return []
        loans = (deps.monitoring.get_loan(doc["application_id"]) for doc in applications)
        return [loan for doc, loan in zip(applications, loans, strict=True)
                if loan is not None and loan.get("applicant_id") == applicant_id and product_of(doc) == product_of(loan)]

    def borrower_plans(loans: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """대출별 셀프 플랜 제안과 받은 안내."""
        plans = []
        today = deps.clock().astimezone(KST).date().isoformat()
        for loan in loans:
            replay_id = (loan.get("monitoring") or {}).get("replay_id")
            contacts = deps.monitoring.get_contacts(loan["id"], replay_id) if replay_id else None
            plans.append(borrower_plan(loan, contacts, today))
        return plans

    def borrower_texts(applications, loans, consents, grievances, menu) -> PageTexts | None:
        """고객 콘솔 화면은 여러 신청이 섞이므로 신청·대출 원천의 엔티티를 합쳐 가명처리한다."""
        if i18n.current() == i18n.SOURCE:
            return None
        cases, refs, readable = [], [], True
        for doc in applications:
            raw, _ = read_raw(lambda: load_raw(deps.raw, doc), doc["application_id"])
            readable &= raw is not None
            refs.extend(translation_scope("applications", doc))
            if raw is not None:
                cases.append((raw.profile, raw.transactions))
        for loan in loans:
            raw, _ = read_raw(lambda: load_monitoring_raw(deps.raw, loan), loan["id"])
            readable &= raw is not None
            refs.extend(translation_scope("loans", loan))
            if raw is not None:
                cases.append((raw.profile, raw.transactions))
        owners = {d["application_id"] for d in applications}
        if menu == "grievances":
            readable &= all(g.get("application_id") in owners for g in grievances)
        if menu == "consents":
            known = {cid for cid, _ in refs}
            readable &= all(c.get("consent_id") in known for c in consents if c.get("notices"))
        scope = tuple(dict.fromkeys(refs))
        allowed = readable and mt_active(scope)
        page = PageTexts.merged(deps.pseudonym_key, cases, allow_pending=allowed)
        page.owner_translations = {owner: stored_translations(owner) for owner in sorted(owners)}
        page.scope = scope
        return page

    @app.get("/legacy/borrower", response_class=HTMLResponse)
    @app.get("/borrower", response_class=HTMLResponse)
    def borrower_index(request: Request) -> HTMLResponse:
        user = customer(request)
        privacy()
        legacy = request.url.path.startswith("/legacy/")
        applicants = tuple(a for a in DEMO_APPLICANTS if a not in USD_ONLY_SELLER_IDS) if legacy else DEMO_APPLICANTS
        return render("customer_select.html", user, prefix="/legacy" if legacy else "", applicants=applicants)

    @app.get("/legacy/borrower/{applicant_id}", response_class=HTMLResponse)
    @app.get("/borrower/{applicant_id}", response_class=HTMLResponse)
    def borrower(applicant_id: str, request: Request) -> HTMLResponse:
        return borrower_page(customer(request), applicant_id, legacy=request.url.path.startswith("/legacy/"))

    @app.get("/legacy/borrower/{applicant_id}/{page}", response_class=HTMLResponse)
    @app.get("/borrower/{applicant_id}/{page}", response_class=HTMLResponse)
    def borrower_menu(applicant_id: str, page: str, request: Request) -> HTMLResponse:
        user = customer(request)
        if page not in BORROWER_PAGES or page == "status":
            raise HTTPException(404, "unknown page")
        return borrower_page(user, applicant_id, page, legacy=request.url.path.startswith("/legacy/"))

    @app.get("/legacy/borrower/{applicant_id}/consents/{consent_id}/proof", response_class=HTMLResponse)
    @app.get("/borrower/{applicant_id}/consents/{consent_id}/proof", response_class=HTMLResponse)
    def borrower_proof(applicant_id: str, consent_id: str, request: Request) -> HTMLResponse:
        user = customer(request)
        store = privacy()
        consent = store.get_consent(consent_id)
        doc = store.get_proof(consent_id) if consent and consent.get("applicant_id") == applicant_id else None
        if doc is None:
            raise HTTPException(404, "deletion proof not found")
        prefix = "" if product_of(consent) else "/legacy"
        return render("proof.html", user, p=doc, layout="customer_base.html", applicant_id=applicant_id, prefix=prefix,
                      nav="consents", back_url=prefix + borrower_url(applicant_id, "consents"), back_label=_("동의 관리로"))

    @app.post("/legacy/borrower/{applicant_id}/consents/{consent_id}/revoke", response_class=HTMLResponse)
    @app.post("/borrower/{applicant_id}/consents/{consent_id}/revoke", response_class=HTMLResponse)
    def borrower_revoke(applicant_id: str, consent_id: str, posted: Posted = Depends(customer_form)) -> HTMLResponse:
        user = posted.user
        doc = privacy().get_consent(consent_id)
        if doc is None or doc.get("applicant_id") != applicant_id:
            raise HTTPException(404, "consent not found")
        if doc.get("status") != "granted":
            raise HTTPException(409, "consent is not active")
        command = ConsentCommand(
            consent_id=consent_id, action="revoke", requested_by=f"borrower:{applicant_id}"[:64],
            application_id=doc.get("application_id"),
        )
        queue_servicing(CONSENT_COMMAND_QUEUE, doc, command)
        return customer_sent(user, applicant_id, "consents", _(
            "동의 %(id)s 철회를 요청했습니다. 원천 데이터 삭제가 끝나면 삭제 증명이 표시됩니다.", id=consent_id), legacy=not product_of(doc))

    @app.post("/legacy/borrower/{applicant_id}/grievances", response_class=HTMLResponse)
    @app.post("/borrower/{applicant_id}/grievances", response_class=HTMLResponse)
    def borrower_grievance(applicant_id: str, request: Request, posted: Posted = Depends(customer_form)) -> HTMLResponse:
        user, form = posted.user, posted.form
        privacy()
        application_id = form.get("application_id", "")
        doc = deps.store.get(application_id)
        legacy = request.url.path.startswith("/legacy/")
        if doc is None or doc.get("applicant_id") != applicant_id or not in_portfolio(doc, legacy):
            raise HTTPException(404, "application not found")
        if not doc.get("officer_decision"):
            raise HTTPException(409, "application has no decision yet")
        try:
            product = product_of(doc)
            message = (UsdGrievanceSubmission if product else GrievanceSubmission)(
                **(product.model_dump() if product else {}),
                grievance_id=deps.new_grievance_id(), application_id=application_id, applicant_id=applicant_id,
                text=form.get("text", ""),
            )
        except ValidationError:
            return borrower_page(user, applicant_id, "grievances", 422, legacy=legacy,
                                 error=_("이의 내용은 1~1000자로 입력해야 합니다."), form=form)
        deps.queue.send(GRIEVANCE_QUEUE, message.model_dump(mode="json"))
        return customer_sent(user, applicant_id, "grievances", _(
            "이의제기 %(id)s를 접수했습니다. 심사역이 처리하면 회신이 표시됩니다.", id=message.grievance_id), legacy=legacy)

    @app.post("/legacy/borrower/{applicant_id}/plan", response_class=HTMLResponse)
    @app.post("/borrower/{applicant_id}/plan", response_class=HTMLResponse)
    def borrower_plan_choice(applicant_id: str, posted: Posted = Depends(customer_form)) -> HTMLResponse:
        """셀프 상환 플랜 선택. 만료(고객 응답 없이 계속)·응답이 끝난 제안에는 고를 수 없다(409)."""
        user, form = posted.user, posted.form
        loan = monitoring_store().get_loan(form.get("loan_id", ""))
        if loan is None or loan.get("applicant_id") != applicant_id:
            raise HTTPException(404, "loan not found")
        offer = (loan.get("repayment") or {}).get("offer")
        if not offer or offer.get("status") != "open" or offer.get("offer_id") != form.get("offer_id"):
            return borrower_page(user, applicant_id, "plans", 409, legacy=not product_of(loan), error=_("이미 마감된 상환 플랜 제안입니다."))
        option = form.get("option", "")
        if option not in {o.get("option") for o in offer.get("options") or ()}:
            return borrower_page(user, applicant_id, "plans", 422, legacy=not product_of(loan), error=_("제안에 없는 플랜입니다."))
        try:
            message = PlanChoice(kind="plan_choice", loan_id=loan["id"], offer_id=offer["offer_id"], option=option)
        except ValidationError:
            return borrower_page(user, applicant_id, "plans", 422, error=_("제안에 없는 플랜입니다."))
        queue_servicing(RESTRUCTURE_QUEUE, loan, message)
        label = i18n.label(PLAN_OPTION_LABELS, message.option)
        text = (_("플랜(%(label)s)을 선택했습니다. 지금 일정대로 상환합니다.", label=label) if message.option == "keep"
                else _("플랜(%(label)s)을 선택했습니다. 심사역이 확인하면 결과가 상환 플랜 메뉴에 표시됩니다.", label=label))
        return customer_sent(user, applicant_id, "plans", text, legacy=not product_of(loan))

    @app.post("/applications/{application_id}/consents/{consent_id}/{action}", response_class=HTMLResponse)
    def consent_demo(application_id: str, consent_id: str, action: str, posted: Posted = Depends(decider_form)) -> HTMLResponse:
        user = posted.user
        if action not in DEMO_ACTIONS:
            raise HTTPException(404, "unknown action")
        doc = privacy().get_consent(consent_id)
        if doc is None or doc.get("application_id") != application_id:
            raise HTTPException(404, "consent not found")
        if doc.get("status") != "granted":
            raise HTTPException(409, "consent is not active")
        # 요청자는 폼이 아니라 로그인 주체에서만 가져온다.
        command = ConsentCommand(
            consent_id=consent_id, action=action, requested_by=user.user_id, application_id=application_id
        )
        queue_servicing(CONSENT_COMMAND_QUEUE, doc, command)
        return sent(user, _("동의 %(id)s에 '%(action)s' 명령을 보냈습니다.", id=consent_id, action=i18n.label(DEMO_ACTIONS, action)),
                    f"/applications/{quote(application_id, safe='')}", _("신청 상세로"))

    @app.get("/consents/{consent_id}/proof", response_class=HTMLResponse)
    def proof(consent_id: str, request: Request) -> HTMLResponse:
        user = viewer(request)
        doc = privacy().get_proof(consent_id)
        if doc is None:
            raise HTTPException(404, "deletion proof not found")
        return render("proof.html", user, p=doc)

    @app.get("/legacy/grievances", response_class=HTMLResponse)
    @app.get("/grievances", response_class=HTMLResponse)
    def grievances(request: Request) -> HTMLResponse:
        user = viewer(request)
        legacy = request.url.path.startswith("/legacy/")
        return render("grievances.html", user, rows=privacy().list_grievances(LIST_LIMIT, legacy=legacy), prefix="/legacy" if legacy else "")

    def ops_store() -> OpsStore:
        if deps.ops is None:
            raise HTTPException(404, "ops metrics not configured")
        return deps.ops

    @app.get("/legacy/ops", response_class=HTMLResponse)
    @app.get("/ops", response_class=HTMLResponse)
    def ops_page(request: Request, run: str | None = None) -> HTMLResponse:
        user = viewer(request)
        store = ops_store()
        legacy = request.url.path.startswith("/legacy/")
        runs = sorted_runs(store.runs(legacy=legacy))
        if run is not None and run != RECENT_RUN and run not in runs:
            raise HTTPException(404, "run not found")
        selected = run or default_run(runs) or RECENT_RUN
        docs = scope_docs(store, selected, legacy=legacy)
        summary = ops_view(docs) if docs else None
        return render("ops.html", user, runs=runs, options=run_options(runs), selected=selected, prefix="/legacy" if legacy else "",
                      recent=selected == RECENT_RUN, s=summary,
                      ui=ops_summary(summary, STEP_LABELS) if summary else None,
                      AXIS_LABELS={k: i18n.label(AXIS_LABELS, k) for k in AXIS_LABELS},
                      STEP_LABELS={k: i18n.label(STEP_LABELS, k) for k in STEP_LABELS})

    def grievance_or_404(application_id: str, grievance_id: str) -> dict[str, Any]:
        doc = privacy().get_grievance(application_id, grievance_id)
        if doc is None:
            raise HTTPException(404, "grievance not found")
        return doc

    def grievance_page(user: Principal, doc: dict[str, Any], status_code: int = 200, **ctx: Any) -> HTMLResponse:
        owner = doc["application_id"]
        record = doc.get("record") or {}
        decision = record.get("officer_decision")
        mask_decision = decision and not user.can_restore
        texts = None
        if mask_decision or i18n.current() != i18n.SOURCE:
            application, _ = read_raw(lambda: deps.store.get(owner), owner)
            raw = read_raw(lambda: load_raw(deps.raw, application), owner)[0] if application else None
            texts = page_texts(raw, owner, translation_scope("applications", application or {}))
            if mask_decision:
                shown = decision_for_display(decision, case_masker(raw, deps.pseudonym_key).mask if raw else None)
                doc = {**doc, "record": {**record, "officer_decision": shown}}
        return render("grievance.html", user, status_code, texts=texts, g=doc, **ctx)

    @app.get("/grievances/{application_id}/{grievance_id}", response_class=HTMLResponse)
    def grievance(application_id: str, grievance_id: str, request: Request) -> HTMLResponse:
        user = viewer(request)
        doc = grievance_or_404(application_id, grievance_id)
        return grievance_page(user, doc)

    @app.post("/grievances/{application_id}/{grievance_id}/decision", response_class=HTMLResponse)
    def grievance_decision(application_id: str, grievance_id: str, posted: Posted = Depends(decider_form)) -> HTMLResponse:
        user, form = posted.user, posted.form
        doc = grievance_or_404(application_id, grievance_id)
        if doc.get("status") != "pending_officer":
            raise HTTPException(409, "grievance is not pending")
        outcome = form.get("outcome", "")
        rereview = outcome == "rereview"
        new_decision = form.get("new_decision", "") if rereview else ""
        product = product_of(doc)

        def invalid(message: str, code: str | None = None) -> HTMLResponse:
            return grievance_page(user, doc, 422, error=message, error_code=code, form=form)

        record = doc.get("record") or {}
        if product != product_of(record):
            return invalid("Grievance record product mismatch.")
        if product and ("amount_krw" in form or "amount_cents" in form):
            return invalid("Enter the amount in USD.")
        amount_text = form.get("amount_usd" if product else "amount_krw", "").strip()
        if not product and (not rereview or new_decision == "decline"):
            amount_text = ""
        try:
            amount = (parse_usd_cents if product else _int)(amount_text) if amount_text else None
        except ValueError:
            return invalid("Enter a dollar amount with at most two decimal places.")
        if amount_text and amount is None:
            return invalid(_(DECISION_ERRORS["invalid_payload"]))
        try:
            # 심사역 ID는 폼이 아니라 로그인 주체에서만 가져온다.
            message = (UsdGrievanceDecision if product else GrievanceDecision)(
                grievance_id=grievance_id, application_id=application_id, outcome=outcome,
                officer_id=user.user_id, reason=form.get("reason", ""), new_decision=new_decision or None,
                **({**product.model_dump(), "amount_cents": amount} if product else {"amount_krw": amount}),
            )
        except ValidationError:
            return invalid(_("처리 결과와 사유를 확인하세요(재심사에는 새 결정이 필요합니다)."))
        if rereview and isinstance(message, UsdGrievanceDecision):
            if error := usd_rereview_error(message, record.get("policy") or {}):
                return invalid(_(DECISION_ERRORS.get(error, error)), error)
        elif rereview:
            proposed = message.model_dump(include={"officer_id", "amount_krw", "conditions", "reason"})
            _proposed, error = validate_rereview_decision(
                {**proposed, "decision": message.new_decision}, (doc.get("record") or {}).get("policy")
            )
            if error:
                return invalid(_(DECISION_ERRORS.get(error, error)), error)
        deps.queue.send(GRIEVANCE_DECISION_QUEUE, message.model_dump(mode="json"))
        return sent(user, _("이의제기 %(id)s 처리(%(outcome)s)를 보냈습니다. 회신 초안이 곧 기록됩니다.", id=grievance_id, outcome=outcome),
                    f"/grievances/{quote(application_id, safe='')}/{quote(grievance_id, safe='')}", _("이의제기로"))

    return app
