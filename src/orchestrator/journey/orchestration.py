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
from journey.metering import METER_KEY, TAGS_KEY, failed_record, step_records
from journey.models import KST, WITHDRAWN_EVENT, StartRequest, Status
from journey.validation import validate_officer_decision
from shared.segments import segment_of

ORCHESTRATOR = "application_journey"
EVENT_OFFICER_DECISION = "officer_decision"
FLAG_DATA_INCOMPLETE = "data_incomplete"
FLAG_POLICY_UNAVAILABLE = "policy_unavailable"
FLAG_MONITORING_UNAVAILABLE = "monitoring_unavailable"
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


def _tags(req: StartRequest) -> dict[str, str]:
    return {k: v for k, v in (("source", req.source), ("run_id", req.run_id)) if v}


def _tagged(payload: dict[str, Any], tags: dict[str, str]) -> dict[str, Any]:
    return {**payload, TAGS_KEY: tags} if tags else payload


def _metered(doc: dict[str, Any], step: str, result: Any, started: datetime, ended: datetime) -> Any:
    """`_meter`를 떼어 steps[]에 옮긴다. 결과 객체는 고치지 않는다."""
    meter = result.get(METER_KEY) if isinstance(result, dict) else None
    clean = {k: v for k, v in result.items() if k != METER_KEY} if isinstance(result, dict) else result
    doc["steps"].extend(step_records(step, meter, clean, started, ended))
    return clean


def _new_document(req: StartRequest, received: datetime) -> dict[str, Any]:
    return {
        "id": req.application_id,
        "application_id": req.application_id,
        "applicant_id": req.applicant_id,
        "requested_amount_krw": req.requested_amount_krw,
        "requested_tenor_months": req.requested_tenor_months,
        "received_at": received.isoformat(),
        "submitted_at": req.submitted_at.isoformat() if req.submitted_at else None,
        "source": req.source,
        "run_id": req.run_id,
        "segment": None,
        "steps": [],
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
        "monitoring": None,
        "withdrawal": None,
        "error": None,
    }


def journey(ctx: Any, retry: Any) -> Steps:
    req = StartRequest.model_validate(_json(ctx.get_input()))
    doc = _new_document(req, _now(ctx))
    yield from _advance(ctx, retry, doc, Status.RECEIVED)

    tags = _tags(req)
    started = _now(ctx)
    try:
        consent = yield ctx.call_activity_with_retry(
            "request_consent", retry,
            _tagged({"application_id": req.application_id, "applicant_id": req.applicant_id}, tags),
        )
    except Exception as e:  # noqa: BLE001 - 활동 실패는 문서에 기록하고 종료한다
        doc["steps"].append(failed_record("consent", started, _now(ctx), e))
        return (yield from _fail(ctx, retry, doc, "request_consent", e))
    consent = _metered(doc, "consent", consent, started, _now(ctx))
    # 동의 토큰은 문서에 남기지 않는다(활동 입력으로만 전달).
    doc["consent"] = {k: consent[k] for k in ("consent_id", "scopes", "expires_at")}
    yield from _advance(ctx, retry, doc, Status.CONSENT_GRANTED)

    started = _now(ctx)
    try:
        # 에이전트 실행은 비용이 들고 안전망이 있으므로 활동 재시도를 걸지 않는다.
        assembly = yield ctx.call_activity(
            "assemble_data",
            _tagged(
                {
                    "application_id": req.application_id,
                    "consent_id": consent["consent_id"],
                    "token": consent["token"],
                    "scopes": consent["scopes"],
                },
                tags,
            ),
        )
    except Exception as e:  # noqa: BLE001
        doc["steps"].append(failed_record("assemble", started, _now(ctx), e))
        return (yield from _fail(ctx, retry, doc, "assemble_data", e))
    assembly = _metered(doc, "assemble", assembly, started, _now(ctx))
    doc["assembly"] = assembly
    doc["flags"].extend(assembly.get("flags", []))

    if assembly["status"] != "complete":
        doc["flags"].append(FLAG_DATA_INCOMPLETE)
        yield from _advance(ctx, retry, doc, Status.DATA_INCOMPLETE)
    else:
        yield from _advance(ctx, retry, doc, Status.DATA_ASSEMBLED)
        started = _now(ctx)
        try:
            underwriting = yield ctx.call_activity_with_retry(
                "underwrite",
                retry,
                _tagged(
                    {
                        "application_id": req.application_id,
                        "consent_id": consent["consent_id"],
                        "as_of": doc["as_of"],
                        "requested_amount_krw": req.requested_amount_krw,
                        "requested_tenor_months": req.requested_tenor_months,
                    },
                    tags,
                ),
            )
        except Exception as e:  # noqa: BLE001
            doc["steps"].append(failed_record("underwrite", started, _now(ctx), e))
            return (yield from _fail(ctx, retry, doc, "underwrite", e))
        underwriting = _metered(doc, "underwrite", underwriting, started, _now(ctx))
        doc["segment"] = segment_of(underwriting.get("features"))
        doc["underwriting"] = underwriting
        doc["flags"].extend(underwriting.get("flags", []))
        yield from _advance(ctx, retry, doc, Status.UNDERWRITTEN)
        started = _now(ctx)
        try:
            result = yield ctx.call_activity_with_retry(
                "evaluate_policy", retry, _tagged(underwriting["features"], tags)
            )
        except Exception as e:  # noqa: BLE001 - 정책 결과 없음 = 자동 승인 없음, 심사역 예외 승인만 가능(fail-closed)
            doc["steps"].append(failed_record("policy", started, _now(ctx), e))
            doc["flags"].append(FLAG_POLICY_UNAVAILABLE)
            doc["flags"].append(FLAG_ASSESSMENT_UNAVAILABLE)
        else:
            doc["policy"] = _metered(doc, "policy", result, started, _now(ctx))
            yield from _assess(ctx, retry, doc, consent["consent_id"], underwriting, tags)

    return (yield from _await_officer(ctx, retry, doc, req.sla_hours))


def _assess(
    ctx: Any, retry: Any, doc: dict[str, Any], consent_id: str, underwriting: dict[str, Any], tags: dict[str, str]
) -> Steps:
    """정책 결과를 근거로 분석·검토 에이전트를 돌린다. 실패해도 심사역 단계로 넘어간다(결정 권한 없음)."""
    started = _now(ctx)
    try:
        # 에이전트 실행은 비용이 들고 반성 루프가 안에 있으므로 활동 재시도를 걸지 않는다.
        result = yield ctx.call_activity(
            "assess_credit",
            _tagged(
                {
                    "application_id": doc["application_id"],
                    "consent_id": consent_id,
                    "underwriting": underwriting,
                    "policy": doc["policy"],
                },
                tags,
            ),
        )
    except Exception as e:  # noqa: BLE001
        doc["steps"].append(failed_record("assess", started, _now(ctx), e))
        doc["flags"].append(FLAG_ASSESSMENT_UNAVAILABLE)
        return None
    result = _metered(doc, "assess", result, started, _now(ctx))
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
    withdrawn = ctx.wait_for_external_event(WITHDRAWN_EVENT)
    while True:
        timer = ctx.create_timer(deadline)
        winner = yield ctx.task_any([event, withdrawn, timer])
        if winner is withdrawn:
            timer.cancel()
            payload = _json(withdrawn.result)
            if isinstance(payload, dict) and payload.get("consent_id") == doc["consent"]["consent_id"]:
                doc["withdrawal"] = {
                    "consent_id": payload["consent_id"],
                    "requested_by": str(payload.get("requested_by", ""))[:64],
                    "at": _now(ctx).isoformat(),
                }
                yield from _advance(ctx, retry, doc, Status.WITHDRAWN)
                return _result(doc)
            # 다른 동의(사후 모니터링 동의 등)의 철회는 신청 여정을 끝내지 않는다.
            withdrawn = ctx.wait_for_external_event(WITHDRAWN_EVENT)
            continue
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
            if decision.decision != "decline":
                yield from _register_monitoring(ctx, retry, doc)
            return _result(doc)
        if doc["sla"]["escalated"]:
            yield from _advance(ctx, retry, doc, Status.CLOSED_NO_DECISION)
            return _result(doc)
        deadline = _now(ctx) + sla
        doc["sla"].update(escalated=True, deadline=deadline.isoformat())
        yield from _advance(ctx, retry, doc, Status.ESCALATED)


def _register_monitoring(ctx: Any, retry: Any, doc: dict[str, Any]) -> Steps:
    """승인된 대출을 사후 모니터링에 등록한다. 실패해도 승인은 유지하고 플래그만 남긴다."""
    decision = doc["officer_decision"]
    try:
        if doc["underwriting"] is None or doc["consent"] is None:
            raise ValueError("심사 결과 없음")
        result = yield ctx.call_activity_with_retry(
            "register_monitoring",
            retry,
            {
                "application_id": doc["application_id"],
                "applicant_id": doc["applicant_id"],
                "consent_id": doc["consent"]["consent_id"],
                "decision": decision["decision"],
                "amount_krw": decision.get("amount_krw") or doc["requested_amount_krw"],
                "tenor_months": doc["requested_tenor_months"],
                "decided_at": decision["at"],
                "underwriting": {k: doc["underwriting"][k] for k in ("features", "monthly")},
            },
        )
        doc["monitoring"] = {"loan_id": result["loan_id"], "registered_at": _now(ctx).isoformat()}
    except Exception:  # noqa: BLE001
        doc["flags"].append(FLAG_MONITORING_UNAVAILABLE)
    yield _save(ctx, retry, doc)
    return None
