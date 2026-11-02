"""활동 계량. 활동 결과에 `_meter`(시각·모델별 토큰·판정)를 붙여 오케스트레이터가 steps[]로 옮기게 한다.

결과 객체는 고치지 않는다(새 dict). 계량 실패는 여정을 막지 않는다.
"""

import dataclasses
from collections.abc import Callable
from datetime import datetime
from typing import Any

from agents.data_assembly import FLAG_AGENT_FALLBACK
from journey import telemetry

METER_KEY = "_meter"
TAGS_KEY = "_tags"
DETAIL_MAX = 200
DECISION_MODEL = "decision-1"
AGENT_STEPS = frozenset({"assemble", "classify", "assess"})
STEPS = {
    "request_consent": "consent",
    "assemble_data": "assemble",
    "underwrite": "underwrite",
    "evaluate_policy": "policy",
    "assess_credit": "assess",
}


class MeteredRunner:
    """에이전트 러너를 감싸 호출마다 모델·토큰을 모은다. 그 밖의 메서드(대화·컨테이너 삭제)는 위임한다."""

    def __init__(self, inner: Any):
        self._inner = inner
        self._usage: list[dict[str, Any]] = []

    def run(self, spec: Any, input_text: str, handlers: Any, **kwargs: Any) -> Any:
        name = getattr(spec, "name", "")
        attrs = {"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": name, "gen_ai.request.model": spec.model}
        with telemetry.span(f"invoke_agent {name}", attrs) as sp:
            try:
                run = self._inner.run(spec, input_text, handlers, **kwargs)
            except Exception:
                self._usage.append({"model": spec.model, "input_tokens": 0, "output_tokens": 0, "calls": 1})
                raise
            entry = {
                "model": spec.model,
                "input_tokens": int(getattr(run, "input_tokens", 0) or 0),
                "output_tokens": int(getattr(run, "output_tokens", 0) or 0),
                "calls": int(getattr(run, "rounds", 0) or 0) + 1,
            }
            telemetry.set_attrs(sp, {"gen_ai.usage.input_tokens": entry["input_tokens"],
                                     "gen_ai.usage.output_tokens": entry["output_tokens"]})
        self._usage.append(entry)
        return run

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def entries(self) -> list[dict[str, Any]]:
        return list(self._usage)


def outcome_of(step: str, result: Any) -> tuple[str, str | None]:
    if not isinstance(result, dict):
        return "ok", None
    if step == "assemble":
        if result.get("status") != "complete":
            return "degraded", f"status={result.get('status')}"
        if FLAG_AGENT_FALLBACK in (result.get("flags") or []):
            return "degraded", FLAG_AGENT_FALLBACK
    if step == "assess" and result.get("assessment") is None:
        error = (result.get("record") or {}).get("error")
        return "failed", str(error)[:DETAIL_MAX] if error else None
    return "ok", None


def _classify_substep(result: Any, usage: list[dict[str, Any]], started: str, ended: str) -> list[dict[str, Any]]:
    classification = result.get("classification") if isinstance(result, dict) else None
    if not isinstance(classification, dict):
        return []
    if "error" in classification:
        outcome, detail = "failed", str(classification["error"])[:DETAIL_MAX]
    else:
        outcome, detail = "ok", None
    calls = int(((classification.get("metrics") or {}).get("decision_calls")) or 0)
    sub_usage = list(usage)
    if calls:
        sub_usage.append({"model": DECISION_MODEL, "input_tokens": 0, "output_tokens": 0, "calls": calls})
    sub = {"step": "classify", "started_at": started, "ended_at": ended, "outcome": outcome, "usage": sub_usage}
    if detail:
        sub["detail"] = detail
    return [sub]


def _record(step: str, outcome: str, started: datetime, ended: datetime, usage: list, detail: str | None):
    rec = {
        "step": step,
        "outcome": outcome,
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
        "duration_ms": max(0, int((ended - started).total_seconds() * 1000)),
        "agent": step in AGENT_STEPS,
        "usage": usage,
    }
    if detail:
        rec["detail"] = detail[:DETAIL_MAX]
    return rec


def failed_record(step: str, started: datetime, ended: datetime, error: BaseException) -> dict[str, Any]:
    return _record(step, "failed", started, ended, [], f"{type(error).__name__}: {error}")


def _parse(value: Any) -> datetime:
    return datetime.fromisoformat(value)


def step_records(step: str, meter: Any, result: Any, started: datetime, ended: datetime) -> list[dict[str, Any]]:
    """`_meter`를 단계 기록으로 바꾼다. 없거나 형식이 틀리면 결과로 판정하고 시각은 오케스트레이터 값을 쓴다."""
    try:
        s, e = _parse(meter["started_at"]), _parse(meter["ended_at"])
        records = [_record(step, str(meter["outcome"]), s, e, list(meter["usage"]), meter.get("detail"))]
        for sub in meter.get("substeps") or []:
            records.append(
                _record(sub["step"], str(sub["outcome"]), _parse(sub["started_at"]), _parse(sub["ended_at"]),
                        list(sub["usage"]), sub.get("detail"))
            )
        return records
    except (KeyError, TypeError, ValueError):
        outcome, detail = outcome_of(step, result)
        return [_record(step, outcome, started, ended, [], detail)]


def run_metered(activity: str, fn: Callable[[Any, dict], Any], deps: Any, payload: Any) -> Any:
    step = STEPS[activity]
    tags = {}
    if isinstance(payload, dict) and TAGS_KEY in payload:
        payload = dict(payload)
        tags = payload.pop(TAGS_KEY) or {}
    app_id = payload.get("application_id") if isinstance(payload, dict) else None
    runner = MeteredRunner(deps.agents) if getattr(deps, "agents", None) is not None else None
    inner = dataclasses.replace(deps, agents=runner) if runner is not None else deps
    started = deps.clock()
    with telemetry.span(f"journey.{activity}", {"application_id": app_id, "step": step, **tags}):
        try:
            result = fn(inner, payload)
        except Exception as e:
            telemetry.record_step(failed_record(step, started, deps.clock(), e), application_id=app_id, tags=tags)
            raise
    ended = deps.clock()
    usage = runner.entries() if runner is not None else []
    s, e = started.isoformat(), ended.isoformat()
    if step == "underwrite":
        substeps, usage = _classify_substep(result, usage, s, e), []
    else:
        substeps = []
    outcome, detail = outcome_of(step, result)
    meter = {"started_at": s, "ended_at": e, "outcome": outcome, "usage": usage, "substeps": substeps}
    if detail:
        meter["detail"] = detail
    for rec in step_records(step, meter, result, started, ended):
        telemetry.record_step(rec, application_id=app_id, tags=tags)
    if not isinstance(result, dict):
        return result
    return {**result, METER_KEY: meter}
