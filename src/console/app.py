"""심사역 콘솔(FastAPI + Jinja2 + htmx).

인증은 Container Apps 기본 인증(Easy Auth)이 맡고, 앱은 X-MS-CLIENT-PRINCIPAL의 앱 역할로 권한을 정한다.
결정·신규 신청·사후 재생·재조정 결정은 큐 메시지로만 오케스트레이터에 전달한다(콘솔은 Cosmos 읽기 전용).
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
from console.monitoring import (
    ACTION_LABELS,
    MONITORING_LABELS,
    SEVERITY_LABELS,
    TRIGGER_LABELS,
    MonitoringStore,
    load_monitoring_raw,
    loan_view,
)
from console.views import DECIDABLE, RawStore, detail_view, load_raw
from journey.models import KST, OfficerMessage, ReplayRequest, RestructureDecision, StartRequest, replay_in_progress
from journey.validation import validate_officer_decision

HERE = Path(__file__).parent
START_QUEUE = "applications-start"
DECISION_QUEUE = "officer-decisions"
REPLAY_QUEUE = "monitoring-replay"
RESTRUCTURE_QUEUE = "restructure-decisions"
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
    # 데모 모드(Easy Auth 끔): 운영자 IP 허용 목록만 남고 모든 요청을 이 주체로 처리한다.
    demo_principal: Principal | None = None
    monitoring: MonitoringStore | None = None
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(KST))


def _krw(value: Any) -> str:
    return f"{value:,}원" if isinstance(value, int) and not isinstance(value, bool) else "-"


def _templates() -> Environment:
    env = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=True)
    env.filters["krw"] = _krw
    env.filters["status_label"] = lambda s: STATUS_LABELS.get(s or "", s or "-")
    env.filters["path"] = lambda s: quote(str(s), safe="")
    env.filters["monitoring_label"] = lambda s: MONITORING_LABELS.get(s or "", "재생 전")
    env.filters["severity_label"] = lambda s: SEVERITY_LABELS.get(s or "", s or "-")
    env.filters["action_label"] = lambda s: ACTION_LABELS.get(s or "", s or "-")
    env.filters["trigger_label"] = lambda s: TRIGGER_LABELS.get(s or "", s or "-")
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
        html = templates.get_template(name).render(user=user, csrf=csrf, demo=deps.demo_principal is not None, **ctx)
        return HTMLResponse(html, status_code=status_code)

    def viewer(request: Request) -> Principal:
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

    def monitoring_store() -> MonitoringStore:
        if deps.monitoring is None:
            raise HTTPException(404, "monitoring not configured")
        return deps.monitoring

    def loan_or_404(loan_id: str) -> dict[str, Any]:
        loan = monitoring_store().get_loan(loan_id)
        if loan is None:
            raise HTTPException(404, "loan not found")
        return loan

    def loan_page(user: Principal, loan: dict[str, Any], status_code: int = 200, **ctx: Any) -> HTMLResponse:
        mon = loan.get("monitoring") or {}
        alerts = monitoring_store().list_alerts(loan["id"], mon["replay_id"]) if mon.get("replay_id") else []
        raw = load_monitoring_raw(deps.raw, loan)
        view = loan_view(loan, alerts, raw, deps.pseudonym_key, restore=user.can_restore)
        blocked = replay_in_progress(mon, deps.clock())
        return render("loan.html", user, status_code, v=view, replay_blocked=blocked, **ctx)

    @app.get("/loans", response_class=HTMLResponse)
    def loans(request: Request) -> HTMLResponse:
        user = viewer(request)
        return render("loans.html", user, rows=monitoring_store().list_loans(LIST_LIMIT))

    @app.get("/loans/{loan_id}", response_class=HTMLResponse)
    def loan_detail(loan_id: str, request: Request) -> HTMLResponse:
        user = viewer(request)
        return loan_page(user, loan_or_404(loan_id))

    @app.post("/loans/{loan_id}/replay", response_class=HTMLResponse)
    async def replay(loan_id: str, request: Request) -> HTMLResponse:
        user = decider(request)
        await checked_form(request, user)
        loan = loan_or_404(loan_id)
        if replay_in_progress(loan.get("monitoring"), deps.clock()):
            return loan_page(user, loan, 409, error="이미 재생이 진행 중입니다.")
        deps.queue.send(REPLAY_QUEUE, ReplayRequest(loan_id=loan["id"]).model_dump(mode="json"))
        return render(
            "loan_sent.html", user, 202, loan_id=loan["id"],
            message="사후 13주 재생 요청을 큐에 보냈습니다. 주차별 처리가 끝나면 경보가 표시됩니다.",
        )

    @app.post("/loans/{loan_id}/alerts/{alert_id}/restructure", response_class=HTMLResponse)
    async def restructure(loan_id: str, alert_id: str, request: Request) -> HTMLResponse:
        user = decider(request)
        form = await checked_form(request, user)
        loan = loan_or_404(loan_id)
        replay_id = (loan.get("monitoring") or {}).get("replay_id")
        alerts = monitoring_store().list_alerts(loan["id"], replay_id) if replay_id else []
        alert = next((a for a in alerts if a.get("alert_id") == alert_id), None)
        if alert is None:
            raise HTTPException(404, "alert not found")
        if (alert.get("restructure") or {}).get("status") != "pending_officer":
            raise HTTPException(409, "restructure request is not pending")
        reason = form.get("reason", "").strip()
        try:
            # 심사역 ID는 폼이 아니라 로그인 주체에서만 가져온다.
            message = RestructureDecision(
                loan_id=loan["id"], alert_id=alert_id, decision=form.get("decision", ""),
                officer_id=user.user_id, reason=reason,
            )
        except ValidationError:
            return loan_page(user, loan, 422, error="재조정 결정 형식이 올바르지 않습니다.")
        if not reason:
            return loan_page(user, loan, 422, error="사유를 입력해야 합니다.")
        deps.queue.send(RESTRUCTURE_QUEUE, message.model_dump(mode="json"))
        return render(
            "loan_sent.html", user, 202, loan_id=loan["id"],
            message=f"재조정 결정({message.decision})을 큐에 보냈습니다.",
        )

    return app
