"""대출 사후 모니터링 처리(스펙 §3.3). 큐·Service Bus 트리거가 부르며 외부 I/O는 Deps 포트로만 한다.

흐름: monitoring-replay 큐 → handle_replay(모니터링 동의·흐름 수집, 주차 메시지 13건 발행)
      → flow.updated(세션=loan_id) → process_week(탐지기 → 새 트리거면 EW 에이전트 → 경보 저장·alert.raised)
      → alert.raised(세션=loan_id) → run_collections(공정 추심 규칙 안에서 연락·재조정 요청)
      → restructure-decisions 큐 → handle_restructure_decision(심사역 결정 기록).
Service Bus가 꺼져 있으면(deps.events is None) handle_replay가 같은 처리를 순서대로 직접 한다.
"""

import logging
from datetime import date
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from agents.collections import CollectionsContext, act
from agents.data_assembly import build_pseudonymizer
from agents.early_warning import EwContext, investigate
from agents.monitoring import WEEKS, Baseline, categorize, detect, week_window, weekly_series
from journey.activities import (
    _TRANSACTIONS,
    ENDED_CONSENT,
    Deps,
    _remember_foundry,
    _semantic,
    raw_container,
    schedule_lifecycle,
)
from journey.models import (
    CONSENT_TTL_DAYS,
    MONITORING_PURPOSE,
    MONITORING_SCOPES,
    AlertRaised,
    FlowUpdated,
    ReplayRequest,
    RestructureDecision,
    replay_in_progress,
)
from shared.contracts import ApplicantProfile, EarlyWarningAlert, Transaction

log = logging.getLogger("journey.monitoring")
LOANS, ALERTS = "loans", "alerts"
TOPIC_FLOW, TOPIC_ALERT = "flow.updated", "alert.raised"
_ALERT_FIELDS = tuple(EarlyWarningAlert.model_fields)


class OutOfOrder(RuntimeError):
    """앞 주차가 처리되지 않았다. 메시지를 되돌려 재시도하게 한다."""


def _mask_and_flow(deps: Deps, loan: dict[str, Any]) -> tuple[ApplicantProfile, list[Transaction], Any]:
    mon = loan["monitoring"]
    profile = ApplicantProfile.model_validate(
        deps.raw.get_json(raw_container(loan["underwriting_consent_id"]), "profile.json")
    )
    flow = deps.raw.get_json(raw_container(mon["consent_id"]), "flow.json")
    if flow is None:
        raise RuntimeError(f"사후 흐름 없음: {mon['consent_id']}")
    txns = _TRANSACTIONS.validate_python(flow)
    return profile, txns, build_pseudonymizer(deps.pseudonym_key, profile, txns).mask


def start_replay(deps: Deps, loan_id: str) -> dict[str, Any] | None:
    loan = deps.store.read(LOANS, loan_id, loan_id)
    if loan is None:
        log.warning("모니터링 대상 대출 없음: %s", loan_id)
        return None
    if replay_in_progress(loan.get("monitoring"), deps.clock()):
        log.warning("재생 진행 중이라 새 재생 요청 무시: %s", loan_id)
        return None
    underwriting_consent = loan["underwriting_consent_id"]
    consent = deps.store.read("consents", underwriting_consent, underwriting_consent)
    if consent is not None and consent.get("status") in ENDED_CONSENT:
        # 사후 탐지는 심사 동의로 받은 프로필(가명화 키)이 필요하다. 동의가 끝나면 원천 데이터가 없다.
        log.warning("심사 동의가 끝나 재생할 수 없음: %s (%s)", loan_id, consent.get("status"))
        return None
    scopes = list(MONITORING_SCOPES)
    grant = deps.mock.create_consent(loan["applicant_id"], scopes, MONITORING_PURPOSE, CONSENT_TTL_DAYS)
    consent_id, now = grant["consent_id"], deps.clock().isoformat()
    schedule_lifecycle(
        deps,
        {
            "id": consent_id,
            "consent_id": consent_id,
            "application_id": loan_id,
            "applicant_id": loan["applicant_id"],
            "scopes": scopes,
            "purpose": MONITORING_PURPOSE,
            "granted_at": now,
            "expires_at": grant["expires_at"],
            "status": "granted",
            "raw_container": raw_container(consent_id),
            "foundry_conversations": [],
        },
    )
    flow = deps.mock.fetch("flow", grant["token"])
    txns = _TRANSACTIONS.validate_python(flow)
    if not txns:
        raise RuntimeError(f"사후 흐름이 비어 있다: {loan_id}")
    deps.raw.put_json(raw_container(consent_id), "flow.json", flow)
    replay_id = f"rp-{uuid4().hex[:10]}"
    loan["monitoring"] = {
        "replay_id": replay_id,
        "consent_id": consent_id,
        "flow_start": min(t.booked_on for t in txns).isoformat(),
        "started_at": now,
        "mode": "servicebus" if deps.events is not None else "inline",
        "status": "replaying",
        "processed_week": 0,
        "seen_triggers": [],
        "overrides": {},
        "weeks": [],
        "alert_ids": [],
        "error": None,
    }
    loan["updated_at"] = now
    deps.store.upsert(LOANS, loan)
    return {"loan_id": loan_id, "replay_id": replay_id, "mode": loan["monitoring"]["mode"]}


def handle_replay(deps: Deps, body: str) -> dict[str, Any] | None:
    try:
        req = ReplayRequest.model_validate_json(body)
    except ValidationError as e:
        log.warning("잘못된 재생 요청 폐기: %s", e.errors(include_input=False)[:3])
        return None
    started = start_replay(deps, req.loan_id)
    if started is None:
        return None
    replay_id = started["replay_id"]
    if deps.events is not None:
        for week in range(1, WEEKS + 1):
            message = FlowUpdated(type=TOPIC_FLOW, loan_id=req.loan_id, replay_id=replay_id, week=week)
            deps.events.publish(
                TOPIC_FLOW,
                message.model_dump(mode="json"),
                f"{TOPIC_FLOW}:{req.loan_id}:{replay_id}:{week}",
                session_id=req.loan_id,
            )
        return started
    # Service Bus가 꺼진 동안: 같은 처리를 주차 순서대로 직접 한다(데모 비용 절감 모드).
    try:
        for week in range(1, WEEKS + 1):
            entry = process_week(deps, req.loan_id, replay_id, week)
            if entry and entry.get("alert_id"):
                run_collections(deps, req.loan_id, entry["alert_id"])
    except Exception as e:  # noqa: BLE001 - 큐 재시도는 13주를 처음부터 다시 돌린다(LLM 비용). 실패를 기록하고 콘솔에서 다시 시작한다
        log.exception("인라인 재생 실패: %s %s", req.loan_id, replay_id)
        loan = deps.store.read(LOANS, req.loan_id, req.loan_id)
        if loan and (loan.get("monitoring") or {}).get("replay_id") == replay_id:
            loan["monitoring"].update(status="failed", error=f"{type(e).__name__}: {e}"[:300])
            loan["updated_at"] = deps.clock().isoformat()
            deps.store.upsert(LOANS, loan)
    return started


def process_week(deps: Deps, loan_id: str, replay_id: str, week: int) -> dict[str, Any] | None:
    loan = deps.store.read(LOANS, loan_id, loan_id)
    mon = (loan or {}).get("monitoring")
    if not mon or mon["replay_id"] != replay_id:
        log.info("지난 재생의 주차 메시지 무시: %s %s", loan_id, replay_id)
        return None
    if week <= mon["processed_week"]:
        return None
    if week != mon["processed_week"] + 1:
        raise OutOfOrder(f"{loan_id} {week}주차 앞 주차 미처리({mon['processed_week']})")

    profile, txns, mask = _mask_and_flow(deps, loan)
    start = date.fromisoformat(mon["flow_start"])
    lo, hi = week_window(start, week)
    visible = [t for t in txns if t.booked_on <= hi]
    classification, overrides = _semantic(
        deps, profile, [t for t in visible if t.booked_on >= lo], mon["consent_id"]
    )
    mon["overrides"].update({k: v.value for k, v in overrides.items()})
    baseline = Baseline.from_dict(loan["baseline"])
    cats = categorize(visible, mon["overrides"])
    rows = weekly_series(visible, cats, start, week, baseline)
    triggers = detect(rows, baseline)
    new = [t for t in triggers if t not in mon["seen_triggers"]]
    entry: dict[str, Any] = {
        "week": week,
        "row": rows[-1],
        "triggers": triggers,
        "new_triggers": new,
        "classification": (classification or {}).get("metrics") or (classification or {}).get("error"),
        "investigation": None,
        "alert_id": None,
    }
    if new:
        alert_id = f"ew-{replay_id}-w{week:02d}"
        ctx = EwContext(
            alert_id=alert_id,
            loan_id=loan_id,
            week=week,
            start=start,
            industry=loan["industry"],
            triggers=triggers,
            rows=rows,
            baseline=baseline,
            transactions=visible,
            categories=cats,
            mask=mask,
            terms={"amount_krw": loan["amount_krw"], "tenor_months": loan["tenor_months"]},
        )
        result = investigate(ctx, runner=deps.agents, model=deps.ew_model)
        _remember_foundry(deps, mon["consent_id"], result.conversations, [])
        mon["seen_triggers"].extend(new)
        entry["investigation"] = {"decision": result.decision, "reason": result.reason, "record": result.record}
        if result.alert is not None:
            deps.store.upsert(
                ALERTS,
                {
                    "id": alert_id,
                    **result.alert.model_dump(mode="json"),
                    "replay_id": replay_id,
                    "status": "open",
                    "created_at": deps.clock().isoformat(),
                    "collections": None,
                    "restructure": None,
                    "event_error": None,
                },
            )
            entry["alert_id"] = alert_id
            mon["alert_ids"].append(alert_id)
    mon["processed_week"] = week
    mon["weeks"].append(entry)
    if week == WEEKS:
        mon["status"] = "completed"
    loan["updated_at"] = deps.clock().isoformat()
    deps.store.upsert(LOANS, loan)
    if entry["alert_id"] and deps.events is not None:
        _publish_alert(deps, loan_id, entry["alert_id"])
    return entry


def _publish_alert(deps: Deps, loan_id: str, alert_id: str) -> None:
    message = AlertRaised(type=TOPIC_ALERT, loan_id=loan_id, alert_id=alert_id).model_dump(mode="json")
    try:
        deps.events.publish(TOPIC_ALERT, message, f"{TOPIC_ALERT}:{alert_id}", session_id=loan_id)  # type: ignore[union-attr]
    except Exception as e:  # noqa: BLE001 - 주차 처리는 끝났다. 경보는 콘솔에 남고 발행 실패를 기록한다
        doc = deps.store.read(ALERTS, alert_id, loan_id)
        if doc is not None:
            doc["event_error"] = f"{type(e).__name__}: {e}"[:300]
            deps.store.upsert(ALERTS, doc)


def handle_flow_update(deps: Deps, body: str) -> dict[str, Any] | None:
    try:
        msg = FlowUpdated.model_validate_json(body)
    except ValidationError:
        log.info("flow.updated 형식이 아닌 메시지 무시(스모크 등)")
        return None
    return process_week(deps, msg.loan_id, msg.replay_id, msg.week)


def run_collections(deps: Deps, loan_id: str, alert_id: str) -> dict[str, Any] | None:
    doc = deps.store.read(ALERTS, alert_id, loan_id)
    loan = deps.store.read(LOANS, loan_id, loan_id)
    if doc is None or loan is None or doc.get("collections") is not None:
        return None
    alert = EarlyWarningAlert.model_validate({k: doc[k] for k in _ALERT_FIELDS})
    contacts_id = f"contacts-{doc['replay_id']}"
    contacts = deps.store.read(ALERTS, contacts_id, loan_id) or {
        "id": contacts_id,
        "loan_id": loan_id,
        "replay_id": doc["replay_id"],
        "contacts": [],
    }
    _, _, mask = _mask_and_flow(deps, loan)
    ctx = CollectionsContext(alert=alert, contacts=contacts["contacts"], now=deps.clock(), mask=mask)
    result = act(ctx, runner=deps.agents, model=deps.collections_model)
    _remember_foundry(deps, loan["monitoring"]["consent_id"], result.conversations, [])
    doc["collections"] = {
        "decision": result.decision,
        "reason": result.reason,
        "contacts": result.contacts,
        "record": result.record,
        "at": deps.clock().isoformat(),
    }
    doc["restructure"] = result.restructure
    if result.restructure is not None:
        doc["status"] = "restructure_pending"
    # 경보 문서를 먼저 쓴다: 재시도 시 collections가 있으면 다시 연락하지 않는다(중복 발송 방지).
    deps.store.upsert(ALERTS, doc)
    contacts["contacts"].extend(result.contacts)
    deps.store.upsert(ALERTS, contacts)
    return doc["collections"]


def handle_alert_raised(deps: Deps, body: str) -> dict[str, Any] | None:
    try:
        msg = AlertRaised.model_validate_json(body)
    except ValidationError:
        log.info("alert.raised 형식이 아닌 메시지 무시")
        return None
    return run_collections(deps, msg.loan_id, msg.alert_id)


def handle_restructure_decision(deps: Deps, body: str) -> bool:
    try:
        msg = RestructureDecision.model_validate_json(body)
    except ValidationError as e:
        log.warning("잘못된 재조정 결정 폐기: %s", e.errors(include_input=False)[:3])
        return False
    doc = deps.store.read(ALERTS, msg.alert_id, msg.loan_id)
    request = (doc or {}).get("restructure")
    if not request or request.get("status") != "pending_officer":
        log.warning("대기 중인 재조정 요청 없음: %s", msg.alert_id)
        return False
    status = "approved" if msg.decision == "approve" else "declined"
    request.update(
        status=status, officer_id=msg.officer_id, reason=msg.reason, decided_at=deps.clock().isoformat()
    )
    doc["status"] = f"restructure_{status}"  # type: ignore[index]
    deps.store.upsert(ALERTS, doc)  # type: ignore[arg-type]
    return True
