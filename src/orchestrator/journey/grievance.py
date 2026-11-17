"""이의제기(grievance) 접수와 심사역 처리.

접수 시점의 판단 기록(정책 사유, 분석 요약·counterfactual, 심사역 결정)을 스냅숏으로 남기고,
처리 결과(원결정 유지 또는 재심사)와 회신 초안을 grievances 문서에 기록한다. 신청 문서는 바꾸지 않는다
(재심사 결과의 실행은 별도 절차). 회신 에이전트에는 판단 기록만 주고 차주가 쓴 내용은 주지 않는다.
심사역 사유는 자유 문장이라 그 신청의 가명처리기로 가려 보내고, 원천을 읽지 못하면 보내지 않는다.
재심사로는 정책 밖 예외 승인을 할 수 없다(`rereview_outside_policy`).
"""

import logging
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from agents.data_assembly import build_pseudonymizer
from agents.notices import grievance_facts, write_grievance_reply
from agents.pretranslate import load_case_raw
from journey.activities import Deps, update_doc
from journey.models import GrievanceDecision, GrievanceSubmission
from journey.validation import validate_rereview_decision

log = logging.getLogger("journey.grievance")
GRIEVANCES_CONTAINER = "grievances"
TOPIC_GRIEVANCE = "grievance"
EVENT_GRIEVANCE_SUBMITTED = "grievance.submitted"
PENDING = "pending_officer"
RESOLVED = {"uphold": "resolved_upheld", "rereview": "resolved_rereviewed"}


def _snapshot(application: dict[str, Any]) -> dict[str, Any]:
    policy, assessment = application.get("policy"), application.get("assessment")
    return {
        "status": application.get("status"),
        "policy": (
            {k: policy.get(k) for k in ("decision", "max_amount_krw", "rate_band", "reason_codes", "rules_version")}
            if policy
            else None
        ),
        "assessment": (
            {"summary": assessment.get("summary"), "counterfactuals": assessment.get("counterfactuals", [])}
            if assessment
            else None
        ),
        "officer_decision": application.get("officer_decision"),
    }


def _reason_mask(deps: Deps, grievance: dict[str, Any]) -> Callable[[str], str] | None:
    """심사역 사유를 가릴 그 신청의 가명처리기. 원천이 없으면 패턴만, 읽다 예외가 나면 None(사유를 보내지 않는다)."""
    try:
        case = load_case_raw(
            GRIEVANCES_CONTAINER, grievance, deps.raw, lambda container, ident: deps.store.read(container, ident, ident)
        )
    except Exception as e:  # noqa: BLE001 - 엔티티 없이 가린 사유가 상호·이름을 그대로 보낼 수 있다
        # 검증 오류 메시지에 원천 값이 들어가므로 종류만 남긴다.
        log.warning("이의 회신 원천 읽기 실패로 심사역 사유를 빼고 보냄: %s %s", grievance.get("grievance_id"), type(e).__name__)
        return None
    profile, transactions = case if case else (None, ())
    return build_pseudonymizer(deps.pseudonym_key, profile, transactions).mask


def handle_grievance(deps: Deps, body: str) -> str:
    """created | duplicate | unknown(신청·차주 불일치) | not_decided(결정 전) | invalid."""
    try:
        msg = GrievanceSubmission.model_validate_json(body)
    except ValidationError as e:
        log.warning("잘못된 이의제기 메시지 폐기: %s", e.errors(include_input=False)[:3])
        return "invalid"
    if deps.store.read(GRIEVANCES_CONTAINER, msg.grievance_id, msg.application_id) is not None:
        return "duplicate"
    application = deps.store.read("applications", msg.application_id, msg.application_id)
    if application is None or application.get("applicant_id") != msg.applicant_id:
        log.warning("알 수 없는 신청의 이의제기 폐기: %s", msg.application_id)
        return "unknown"
    if not application.get("officer_decision"):
        return "not_decided"
    doc: dict[str, Any] = {
        "id": msg.grievance_id,
        "grievance_id": msg.grievance_id,
        "application_id": msg.application_id,
        "applicant_id": msg.applicant_id,
        "text": msg.text,
        "submitted_at": deps.clock().isoformat(),
        "status": PENDING,
        "record": _snapshot(application),
        "decision_attempts": [],
        "decision": None,
        "reply": None,
        "resolved_at": None,
        "event_published": False,
        "event_error": None,
    }
    if deps.events is None:
        doc["event_error"] = "servicebus_disabled"
    else:
        # 이의 내용은 개인정보가 섞일 수 있어 이벤트에 넣지 않는다.
        message = {"type": EVENT_GRIEVANCE_SUBMITTED, "grievance_id": msg.grievance_id, "application_id": msg.application_id}
        try:
            deps.events.publish(TOPIC_GRIEVANCE, message, f"{EVENT_GRIEVANCE_SUBMITTED}:{msg.grievance_id}")
            doc["event_published"] = True
        except Exception as e:  # noqa: BLE001 - 알림 실패가 접수를 막지 않는다
            doc["event_error"] = f"{type(e).__name__}: {e}"[:300]
    deps.store.upsert(GRIEVANCES_CONTAINER, doc)
    return "created"


def handle_grievance_decision(deps: Deps, body: str) -> str:
    """resolved | rejected(정책 밖 재심사) | ignored(이미 처리됨) | unknown | invalid."""
    try:
        msg = GrievanceDecision.model_validate_json(body)
    except ValidationError as e:
        log.warning("잘못된 이의 처리 메시지 폐기: %s", e.errors(include_input=False)[:3])
        return "invalid"
    current = deps.store.read(GRIEVANCES_CONTAINER, msg.grievance_id, msg.application_id)
    if current is None:
        return "unknown"
    if current["status"] != PENDING:
        return "ignored"
    at = deps.clock().isoformat()
    record = current["record"]
    if msg.outcome == "rereview":
        proposed = {
            "decision": msg.new_decision,
            "officer_id": msg.officer_id,
            "amount_krw": msg.amount_krw,
            "conditions": msg.conditions,
            "reason": msg.reason,
        }
        _, error = validate_rereview_decision(proposed, record["policy"])
        if error:
            attempt = {"at": at, "error": error, "payload": msg.model_dump(mode="json")}

            def reject(doc: dict[str, Any]) -> bool | None:
                if doc["status"] != PENDING:
                    return False
                doc["decision_attempts"].append(attempt)
                return None

            update_doc(deps.store, GRIEVANCES_CONTAINER, msg.grievance_id, msg.application_id, reject)
            return "rejected"
    decision = {**msg.model_dump(mode="json", exclude={"grievance_id", "application_id"}), "at": at}
    mask = _reason_mask(deps, current) if deps.agents is not None else None
    reply = write_grievance_reply(grievance_facts(record, decision), runner=deps.agents, model=deps.grievance_model, mask=mask)
    resolved = False

    def resolve(doc: dict[str, Any]) -> bool | None:
        nonlocal resolved
        resolved = doc["status"] == PENDING  # 재시도마다 다시 판단한다(그사이 다른 처리가 끝났을 수 있다)
        if not resolved:
            return False
        doc.update(status=RESOLVED[msg.outcome], decision=decision, reply={**reply.as_dict(), "at": at}, resolved_at=at)
        return None

    update_doc(deps.store, GRIEVANCES_CONTAINER, msg.grievance_id, msg.application_id, resolve)
    return "resolved" if resolved else "ignored"
