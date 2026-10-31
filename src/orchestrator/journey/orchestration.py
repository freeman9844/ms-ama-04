"""신청 여정 오케스트레이션(Durable 비의존 생성기).

function_app의 오케스트레이터가 `yield from journey(context, RETRY)`로 위임한다.
결정적이어야 한다: 시각은 ctx.current_utc_datetime만 쓰고, 입출력은 활동으로만 한다.
단계마다 신청 문서 스냅샷(deepcopy)을 save_document 활동으로 Cosmos에 남긴다.
"""

import json
from collections.abc import Generator
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

from agents.underwriting import FLAG_ASSESSMENT_UNAVAILABLE
from journey.models import KST, StartRequest, Status
from journey.validation import validate_officer_decision

ORCHESTRATOR = "application_journey"
EVENT_OFFICER_DECISION = "officer_decision"
FLAG_DATA_INCOMPLETE = "data_incomplete"
FLAG_POLICY_UNAVAILABLE = "policy_unavailable"
_FINAL = {
    "approve": Status.APPROVED,
    "conditional": Status.CONDITIONALLY_APPROVED,
    "decline": Status.DECLINED,
}

Steps = Generator[Any, Any, dict[str, Any]]


def _now(ctx: Any) -> datetime:
    now = ctx.current_utc_datetime
    return now if now.tzinfo else now.replace(tzinfo=UTC)


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _result(doc: dict[str, Any]) -> dict[str, Any]:
    return {"application_id": doc["application_id"], "status": doc["status"]}


def _save(ctx: Any, retry: Any, doc: dict[str, Any]) -> Any:
    return ctx.call_activity_with_retry(
        "save_document", retry, {"container": "applications", "document": deepcopy(doc)}
    )


def _advance(ctx: Any, retry: Any, doc: dict[str, Any], status: Status) -> Generator[Any, Any, None]:
    at = _now(ctx).isoformat()
    doc["status"] = status.value
    doc["updated_at"] = at
    doc["timeline"].append({"status": status.value, "at": at})
    ctx.set_custom_status(status.value)
    yield _save(ctx, retry, doc)


def _fail(ctx: Any, retry: Any, doc: dict[str, Any], stage: str, error: Exception) -> Steps:
    doc["error"] = {"stage": stage, "message": str(error)[:300]}
    yield from _advance(ctx, retry, doc, Status.FAILED)
    return _result(doc)


def _new_document(req: StartRequest, received: datetime) -> dict[str, Any]:
    return {
        "id": req.application_id,
        "application_id": req.application_id,
        "applicant_id": req.applicant_id,
        "requested_amount_krw": req.requested_amount_krw,
        "requested_tenor_months": req.requested_tenor_months,
        "received_at": received.isoformat(),
        "as_of": received.astimezone(KST).date().isoformat(),
        "status": None,
        "updated_at": None,
        "timeline": [],
        "consent": None,
        "assembly": None,
        "underwriting": None,
        "policy": None,
        "assessment": None,
        "assessment_run": None,
        "flags": [],
        "sla": None,
        "decision_attempts": [],
        "officer_decision": None,
        "error": None,
    }


def journey(ctx: Any, retry: Any) -> Steps:
    req = StartRequest.model_validate(_json(ctx.get_input()))
    doc = _new_document(req, _now(ctx))
    yield from _advance(ctx, retry, doc, Status.RECEIVED)

    try:
        consent = yield ctx.call_activity_with_retry(
            "request_consent", retry, {"application_id": req.application_id, "applicant_id": req.applicant_id}
        )
    except Exception as e:  # noqa: BLE001 - 활동 실패는 문서에 기록하고 종료한다
        return (yield from _fail(ctx, retry, doc, "request_consent", e))
    # 동의 토큰은 문서에 남기지 않는다(활동 입력으로만 전달).
    doc["consent"] = {k: consent[k] for k in ("consent_id", "scopes", "expires_at")}
    yield from _advance(ctx, retry, doc, Status.CONSENT_GRANTED)

    try:
        # 에이전트 실행은 비용이 들고 안전망이 있으므로 활동 재시도를 걸지 않는다.
        assembly = yield ctx.call_activity(
            "assemble_data",
            {
                "application_id": req.application_id,
                "consent_id": consent["consent_id"],
                "token": consent["token"],
                "scopes": consent["scopes"],
            },
        )
    except Exception as e:  # noqa: BLE001
        return (yield from _fail(ctx, retry, doc, "assemble_data", e))
    doc["assembly"] = assembly
    doc["flags"].extend(assembly.get("flags", []))

    if assembly["status"] != "complete":
        doc["flags"].append(FLAG_DATA_INCOMPLETE)
        yield from _advance(ctx, retry, doc, Status.DATA_INCOMPLETE)
    else:
        yield from _advance(ctx, retry, doc, Status.DATA_ASSEMBLED)
        try:
            underwriting = yield ctx.call_activity_with_retry(
                "underwrite",
                retry,
                {
                    "application_id": req.application_id,
                    "consent_id": consent["consent_id"],
                    "as_of": doc["as_of"],
                    "requested_amount_krw": req.requested_amount_krw,
                    "requested_tenor_months": req.requested_tenor_months,
                },
            )
        except Exception as e:  # noqa: BLE001
            return (yield from _fail(ctx, retry, doc, "underwrite", e))
        doc["underwriting"] = underwriting
        doc["flags"].extend(underwriting.get("flags", []))
        yield from _advance(ctx, retry, doc, Status.UNDERWRITTEN)
        try:
            doc["policy"] = yield ctx.call_activity_with_retry("evaluate_policy", retry, underwriting["features"])
        except Exception:  # noqa: BLE001 - 정책 결과 없음 = 거절만 허용(fail-closed)
            doc["flags"].append(FLAG_POLICY_UNAVAILABLE)
            doc["flags"].append(FLAG_ASSESSMENT_UNAVAILABLE)
        else:
            yield from _assess(ctx, retry, doc, consent["consent_id"], underwriting)

    return (yield from _await_officer(ctx, retry, doc, req.sla_hours))


def _assess(ctx: Any, retry: Any, doc: dict[str, Any], consent_id: str, underwriting: dict[str, Any]) -> Steps:
    """정책 결과를 근거로 분석·검토 에이전트를 돌린다. 실패해도 심사역 단계로 넘어간다(결정 권한 없음)."""
    try:
        # 에이전트 실행은 비용이 들고 반성 루프가 안에 있으므로 활동 재시도를 걸지 않는다.
        result = yield ctx.call_activity(
            "assess_credit",
            {
                "application_id": doc["application_id"],
                "consent_id": consent_id,
                "underwriting": underwriting,
                "policy": doc["policy"],
            },
        )
    except Exception:  # noqa: BLE001
        doc["flags"].append(FLAG_ASSESSMENT_UNAVAILABLE)
        return None
    doc["assessment"] = result.get("assessment")
    doc["assessment_run"] = result.get("record")
    doc["flags"].extend(result.get("flags", []))
    if doc["assessment"] is not None:
        yield from _advance(ctx, retry, doc, Status.ASSESSED)
    return None


def _await_officer(ctx: Any, retry: Any, doc: dict[str, Any], sla_hours: float) -> Steps:
    sla = timedelta(hours=sla_hours)
    deadline = _now(ctx) + sla
    doc["sla"] = {"hours": sla_hours, "deadline": deadline.isoformat(), "escalated": False}
    yield from _advance(ctx, retry, doc, Status.AWAITING_OFFICER)

    # 이벤트 태스크는 완료될 때까지 재사용한다. 타이머가 이겼다고 새로 만들면
    # 버려진 태스크가 다음 이벤트를 소비해 결정이 유실될 수 있다.
    event = ctx.wait_for_external_event(EVENT_OFFICER_DECISION)
    while True:
        timer = ctx.create_timer(deadline)
        winner = yield ctx.task_any([event, timer])
        if winner is event:
            timer.cancel()
            payload = _json(event.result)
            decision, error = validate_officer_decision(payload, doc["policy"])
            at = _now(ctx).isoformat()
            if error:
                recorded = payload if isinstance(payload, dict) else {"raw": str(payload)[:200]}
                doc["decision_attempts"].append({"at": at, "error": error, "payload": recorded})
                yield _save(ctx, retry, doc)
                event = ctx.wait_for_external_event(EVENT_OFFICER_DECISION)
                continue
            doc["officer_decision"] = {**decision.model_dump(mode="json"), "at": at}
            yield from _advance(ctx, retry, doc, _FINAL[decision.decision])
            return _result(doc)
        if doc["sla"]["escalated"]:
            yield from _advance(ctx, retry, doc, Status.CLOSED_NO_DECISION)
            return _result(doc)
        deadline = _now(ctx) + sla
        doc["sla"].update(escalated=True, deadline=deadline.isoformat())
        yield from _advance(ctx, retry, doc, Status.ESCALATED)
