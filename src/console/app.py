"""심사역 콘솔(FastAPI + Jinja2 + htmx).

인증은 Container Apps 기본 인증(Easy Auth)이 맡고, 앱은 X-MS-CLIENT-PRINCIPAL의 앱 역할로 권한을 정한다.
결정·신규 신청·사후 재생·재조정 결정·동의 명령·이의제기는 큐 메시지로만 오케스트레이터에 전달한다
(콘솔은 Cosmos 읽기 전용). /borrower는 차주 화면을 흉내 낸 데모 시뮬레이터이고,
/apply는 콘솔 역할 없이 셀러가 직접 신청하는 고객 페이지다.
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

from agents.monitoring import WEEKS
from console.assessment import assessment_panels
from console.auth import PRINCIPAL_HEADER, Principal, csrf_token, parse_principal, verify_csrf
from console.customer import APPLY_ERRORS, CONSENT_ITEMS, customer_status, validate_apply
from console.dashboard import (
    RECOMMENDATION_LABELS,
    detail_summary,
    home_summary,
    loan_summary,
    loans_summary,
    ops_summary,
)
from console.monitoring import (
    ACTION_LABELS,
    MONITORING_LABELS,
    SEVERITY_LABELS,
    TRIGGER_LABELS,
    MonitoringStore,
    load_monitoring_raw,
    loan_view,
)
from console.ops import AXIS_LABELS, STEP_LABELS, OpsStore, default_run, ops_view, sorted_runs
from console.privacy import DEMO_ACTIONS, LABEL_FILTERS, PrivacyStore
from console.statuses import STATUS_LABELS, TERMINAL
from console.views import DECIDABLE, RawStore, detail_view, load_raw
from journey.models import (
    CONSENT_PURPOSE,
    CONSENT_TTL_DAYS,
    KST,
    ConsentCommand,
    GrievanceDecision,
    GrievanceSubmission,
    OfficerMessage,
    ReplayRequest,
    RestructureDecision,
    StartRequest,
    replay_held,
    replay_in_progress,
)
from journey.validation import validate_officer_decision
from shared.sellers import DEMO_SELLERS, seller_label

HERE = Path(__file__).parent
START_QUEUE = "applications-start"
DECISION_QUEUE = "officer-decisions"
REPLAY_QUEUE = "monitoring-replay"
RESTRUCTURE_QUEUE = "restructure-decisions"
CONSENT_COMMAND_QUEUE = "consent-commands"
GRIEVANCE_QUEUE = "grievances"
GRIEVANCE_DECISION_QUEUE = "grievance-decisions"
MAX_FORM_BYTES = 16 * 1024
LIST_LIMIT = 50
DEMO_APPLICANTS = tuple(s.applicant_id for s in DEMO_SELLERS)
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
}


class ApplicationStore(Protocol):
    def list_recent(self, limit: int) -> list[dict[str, Any]]: ...

    def get(self, application_id: str) -> dict[str, Any] | None: ...


class QueueSender(Protocol):
    def send(self, queue: str, message: dict[str, Any]) -> None: ...


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
    monitoring: MonitoringStore | None = None
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(KST))
    privacy: PrivacyStore | None = None
    new_grievance_id: Callable[[], str] = field(default=_new_grievance_id)
    ops: OpsStore | None = None


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
    env.filters["seller_label"] = seller_label
    env.filters["recommendation_label"] = lambda s: RECOMMENDATION_LABELS.get(s or "", s or "-")
    for name, labels in LABEL_FILTERS.items():
        env.filters[name] = lambda s, labels=labels: labels.get(s or "", s or "-")
    env.filters["pct"] = lambda v: f"{v * 100:.1f}%" if isinstance(v, int | float) else "-"
    env.filters["usd"] = lambda v: f"${v:,.4f}" if isinstance(v, int | float) else "-"
    env.filters["num"] = lambda v, d=1: f"{v:.{d}f}" if isinstance(v, int | float) else "-"
    env.globals.update(
        htmx_src=HTMX_SRC, htmx_sri=HTMX_SRI, demo_applicants=DEMO_APPLICANTS, demo_actions=DEMO_ACTIONS
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
        consents = deps.privacy.consents_for_application(doc["application_id"]) if deps.privacy else None
        return render("detail.html", user, status_code, v=view, ui=detail_summary(view), panels=assessment_panels(view),
                      in_progress=in_progress,
                      consents=consents, **ctx)

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        user = viewer(request)
        rows = deps.store.list_recent(LIST_LIMIT)
        return render("list.html", user, rows=rows, ui=home_summary(rows), limit=LIST_LIMIT)

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
                submitted_at=deps.clock(),
                source="console",
            )
        except ValidationError:
            return render("new.html", user, 422, error="신청 정보가 올바르지 않습니다.", form=form)
        deps.queue.send(START_QUEUE, req.model_dump(mode="json", exclude_none=True))
        return RedirectResponse(f"/applications/{quote(req.application_id, safe='')}", status_code=303)

    def customer(request: Request) -> Principal:
        """고객 페이지는 콘솔 역할을 묻지 않는다. 로그인(또는 데모 주체)만 있으면 된다."""
        user = deps.demo_principal or parse_principal(request.headers.get(PRINCIPAL_HEADER))
        if user is None:
            raise HTTPException(401, "login required")
        return user

    def apply_form(user: Principal, status_code: int = 200, **ctx: Any) -> HTMLResponse:
        return render("apply.html", user, status_code, consent_items=CONSENT_ITEMS, purpose=CONSENT_PURPOSE,
                      ttl_days=CONSENT_TTL_DAYS, **ctx)

    @app.get("/apply", response_class=HTMLResponse)
    def apply_page(request: Request) -> HTMLResponse:
        return apply_form(customer(request))

    @app.post("/apply")
    async def apply_submit(request: Request):
        user = customer(request)
        form = await checked_form(request, user)
        code = validate_apply(form, DEMO_APPLICANTS)
        if code:
            return apply_form(user, 422, error=APPLY_ERRORS[code], error_code=code, form=form)
        req = StartRequest(
            application_id=deps.new_application_id(),
            applicant_id=form["applicant_id"].strip(),
            requested_amount_krw=_int(form["requested_amount_krw"]),
            requested_tenor_months=_int(form["requested_tenor_months"]),
            submitted_at=deps.clock(),
            source="customer",
        )
        deps.queue.send(START_QUEUE, req.model_dump(mode="json", exclude_none=True))
        return RedirectResponse(f"/apply/{quote(req.application_id, safe='')}", status_code=303)

    @app.get("/apply/{application_id}", response_class=HTMLResponse)
    def apply_status(application_id: str, request: Request) -> HTMLResponse:
        user = customer(request)
        doc = deps.store.get(application_id)
        if doc is not None and doc.get("source") != "customer":
            raise HTTPException(404, "not a customer application")
        return render("apply_status.html", user, application_id=application_id, s=customer_status(doc),
                      seller=seller_label(doc["applicant_id"]) if doc else None)

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
            "policy_override": form.get("policy_override", "") == "1",
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
        return render("loan.html", user, status_code, v=view, ui=loan_summary(view), replay_blocked=blocked,
                      replay_held=replay_held(mon), weeks_left=WEEKS - (mon.get("paused_week") or 0), **ctx)

    @app.get("/loans", response_class=HTMLResponse)
    def loans(request: Request) -> HTMLResponse:
        user = viewer(request)
        rows = monitoring_store().list_loans(LIST_LIMIT)
        return render("loans.html", user, rows=rows, ui=loans_summary(rows), limit=LIST_LIMIT)

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
        if replay_held(loan.get("monitoring")):
            return loan_page(user, loan, 409, error="재조정 결정과 재개 확인이 끝나야 새 재생을 시작할 수 있습니다.")
        # 기본값(action=start)은 빼서 보낸다: 배포 순서와 관계없이 이전 오케스트레이터(extra=forbid)도 받는다.
        deps.queue.send(REPLAY_QUEUE, ReplayRequest(loan_id=loan["id"]).model_dump(mode="json", exclude_defaults=True))
        return render(
            "loan_sent.html", user, 202, loan_id=loan["id"],
            message="사후 13주 재생 요청을 큐에 보냈습니다. 주차별 처리가 끝나면 경보가 표시됩니다.",
        )

    @app.post("/loans/{loan_id}/replay/{action}", response_class=HTMLResponse)
    async def replay_resume_or_stop(loan_id: str, action: str, request: Request) -> HTMLResponse:
        user = decider(request)
        await checked_form(request, user)
        if action not in ("resume", "stop"):
            raise HTTPException(404, "unknown replay action")
        loan = loan_or_404(loan_id)
        mon = loan.get("monitoring") or {}
        if mon.get("status") != "awaiting_resume":
            return loan_page(user, loan, 409, error="재개 확인을 기다리는 재생이 없습니다.")
        message = ReplayRequest(loan_id=loan["id"], action=action, replay_id=mon["replay_id"])
        deps.queue.send(REPLAY_QUEUE, message.model_dump(mode="json"))
        weeks_left = WEEKS - (mon.get("paused_week") or 0)
        text = f"남은 {weeks_left}주 재생을 이어서 요청했습니다." if action == "resume" else "남은 재생을 종료하도록 요청했습니다."
        return render("loan_sent.html", user, 202, loan_id=loan["id"], message=text)

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
            note="결정이 반영되면 대출 화면에서 재생을 이어갈지 선택하세요." if replay_held(loan.get("monitoring")) else None,
        )

    # ── 동의 수명주기·이의제기(P5b) ──

    def privacy() -> PrivacyStore:
        if deps.privacy is None:
            raise HTTPException(404, "privacy views not configured")
        return deps.privacy

    def sent(user: Principal, message: str, back_url: str, back_label: str) -> HTMLResponse:
        return render("privacy_sent.html", user, 202, message=message, back_url=back_url, back_label=back_label)

    def borrower_page(user: Principal, applicant_id: str, status_code: int = 200, **ctx: Any) -> HTMLResponse:
        store = privacy()
        consents = store.consents_for_applicant(applicant_id)
        applications = []
        for application_id in dict.fromkeys(c["application_id"] for c in consents if c.get("application_id")):
            doc = deps.store.get(application_id)
            if doc is not None and doc.get("applicant_id") == applicant_id:
                applications.append(doc)
        return render(
            "borrower.html", user, status_code, applicant_id=applicant_id, consents=consents,
            applications=applications, grievances=store.grievances_for_applicant(applicant_id), **ctx,
        )

    @app.get("/borrower", response_class=HTMLResponse)
    def borrower_index(request: Request) -> HTMLResponse:
        user = viewer(request)
        privacy()
        return render("borrower_index.html", user)

    @app.get("/borrower/{applicant_id}", response_class=HTMLResponse)
    def borrower(applicant_id: str, request: Request) -> HTMLResponse:
        return borrower_page(viewer(request), applicant_id)

    @app.post("/borrower/{applicant_id}/consents/{consent_id}/revoke", response_class=HTMLResponse)
    async def borrower_revoke(applicant_id: str, consent_id: str, request: Request) -> HTMLResponse:
        user = decider(request)
        await checked_form(request, user)
        doc = privacy().get_consent(consent_id)
        if doc is None or doc.get("applicant_id") != applicant_id:
            raise HTTPException(404, "consent not found")
        if doc.get("status") != "granted":
            raise HTTPException(409, "consent is not active")
        command = ConsentCommand(
            consent_id=consent_id, action="revoke", requested_by=f"borrower:{applicant_id}"[:64],
            application_id=doc.get("application_id"),
        )
        deps.queue.send(CONSENT_COMMAND_QUEUE, command.model_dump(mode="json"))
        back = f"/borrower/{quote(applicant_id, safe='')}"
        return sent(user, f"동의 {consent_id} 철회를 요청했습니다. 원천 데이터 삭제가 끝나면 삭제 증명이 표시됩니다.",
                    back, "차주 화면으로")

    @app.post("/borrower/{applicant_id}/grievances", response_class=HTMLResponse)
    async def borrower_grievance(applicant_id: str, request: Request) -> HTMLResponse:
        user = decider(request)
        form = await checked_form(request, user)
        privacy()
        application_id = form.get("application_id", "")
        doc = deps.store.get(application_id)
        if doc is None or doc.get("applicant_id") != applicant_id:
            raise HTTPException(404, "application not found")
        if not doc.get("officer_decision"):
            raise HTTPException(409, "application has no decision yet")
        try:
            message = GrievanceSubmission(
                grievance_id=deps.new_grievance_id(), application_id=application_id, applicant_id=applicant_id,
                text=form.get("text", ""),
            )
        except ValidationError:
            return borrower_page(user, applicant_id, 422, error="이의 내용은 1~1000자로 입력해야 합니다.", form=form)
        deps.queue.send(GRIEVANCE_QUEUE, message.model_dump(mode="json"))
        back = f"/borrower/{quote(applicant_id, safe='')}"
        return sent(user, f"이의제기 {message.grievance_id}를 접수했습니다. 심사역이 처리하면 회신이 표시됩니다.",
                    back, "차주 화면으로")

    @app.post("/applications/{application_id}/consents/{consent_id}/{action}", response_class=HTMLResponse)
    async def consent_demo(application_id: str, consent_id: str, action: str, request: Request) -> HTMLResponse:
        user = decider(request)
        await checked_form(request, user)
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
        deps.queue.send(CONSENT_COMMAND_QUEUE, command.model_dump(mode="json"))
        return sent(user, f"동의 {consent_id}에 '{DEMO_ACTIONS[action]}' 명령을 보냈습니다.",
                    f"/applications/{quote(application_id, safe='')}", "신청 상세로")

    @app.get("/consents/{consent_id}/proof", response_class=HTMLResponse)
    def proof(consent_id: str, request: Request) -> HTMLResponse:
        user = viewer(request)
        doc = privacy().get_proof(consent_id)
        if doc is None:
            raise HTTPException(404, "deletion proof not found")
        return render("proof.html", user, p=doc)

    @app.get("/grievances", response_class=HTMLResponse)
    def grievances(request: Request) -> HTMLResponse:
        user = viewer(request)
        return render("grievances.html", user, rows=privacy().list_grievances(LIST_LIMIT))

    def ops_store() -> OpsStore:
        if deps.ops is None:
            raise HTTPException(404, "ops metrics not configured")
        return deps.ops

    @app.get("/ops", response_class=HTMLResponse)
    def ops_page(request: Request, run: str | None = None) -> HTMLResponse:
        user = viewer(request)
        store = ops_store()
        runs = sorted_runs(store.runs())
        if run is not None and run not in runs:
            raise HTTPException(404, "run not found")
        selected = run or default_run(runs)
        summary = ops_view(store.by_run(selected)) if selected else None
        return render("ops.html", user, runs=runs, selected=selected, s=summary,
                      ui=ops_summary(summary, STEP_LABELS) if summary else None,
                      AXIS_LABELS=AXIS_LABELS, STEP_LABELS=STEP_LABELS)

    def grievance_or_404(application_id: str, grievance_id: str) -> dict[str, Any]:
        doc = privacy().get_grievance(application_id, grievance_id)
        if doc is None:
            raise HTTPException(404, "grievance not found")
        return doc

    @app.get("/grievances/{application_id}/{grievance_id}", response_class=HTMLResponse)
    def grievance(application_id: str, grievance_id: str, request: Request) -> HTMLResponse:
        user = viewer(request)
        return render("grievance.html", user, g=grievance_or_404(application_id, grievance_id))

    @app.post("/grievances/{application_id}/{grievance_id}/decision", response_class=HTMLResponse)
    async def grievance_decision(application_id: str, grievance_id: str, request: Request) -> HTMLResponse:
        user = decider(request)
        form = await checked_form(request, user)
        doc = grievance_or_404(application_id, grievance_id)
        if doc.get("status") != "pending_officer":
            raise HTTPException(409, "grievance is not pending")
        outcome = form.get("outcome", "")
        rereview = outcome == "rereview"
        new_decision = form.get("new_decision", "") if rereview else ""
        amount_text = form.get("amount_krw", "").strip() if rereview and new_decision != "decline" else ""
        amount = _int(amount_text) if amount_text else None

        def invalid(message: str, code: str | None = None) -> HTMLResponse:
            return render("grievance.html", user, 422, g=doc, error=message, error_code=code, form=form)

        if amount_text and amount is None:
            return invalid(DECISION_ERRORS["invalid_payload"])
        try:
            # 심사역 ID는 폼이 아니라 로그인 주체에서만 가져온다.
            message = GrievanceDecision(
                grievance_id=grievance_id, application_id=application_id, outcome=outcome,
                officer_id=user.user_id, reason=form.get("reason", ""), new_decision=new_decision or None,
                amount_krw=amount,
                conditions=[c.strip() for c in form.get("conditions", "").splitlines() if c.strip()] if rereview else [],
            )
        except ValidationError:
            return invalid("처리 결과와 사유를 확인하세요(재심사에는 새 결정이 필요합니다).")
        if rereview:
            proposed = message.model_dump(include={"officer_id", "amount_krw", "conditions", "reason"})
            _, error = validate_officer_decision(
                {**proposed, "decision": message.new_decision}, (doc.get("record") or {}).get("policy")
            )
            if error:
                return invalid(DECISION_ERRORS.get(error, error), error)
        deps.queue.send(GRIEVANCE_DECISION_QUEUE, message.model_dump(mode="json"))
        return sent(user, f"이의제기 {grievance_id} 처리({outcome})를 보냈습니다. 회신 초안이 곧 기록됩니다.",
                    f"/grievances/{quote(application_id, safe='')}/{quote(grievance_id, safe='')}", "이의제기로")

    return app
