"""USD 사건 상태 기계 — 주별 신호를 사건과 단계로 바꾸고 에이전트 호출 여부를 정한다.

순수 모듈이다. 입출력, 시계, 로깅이 없고 상태는 JSON 직렬화 가능한 dict만 쓴다.
"""
from dataclasses import dataclass
from typing import Any, Callable, Literal

STAGES = ("watch", "medium", "high")
QUIET_TO_RESOLVE = 2

_RANK = {stage: i for i, stage in enumerate(STAGES)}

Action = Literal["normal", "suppressed", "quiet", "resolve", "call", "hold"]


@dataclass(frozen=True)
class WeekPlan:
    action: Action
    base: str | None


def new_watch() -> dict:
    return {"open": None, "closed": [], "suppressed": [], "suppress_quiet": 0, "stages": []}


def incident_id(replay_id: str, week: int) -> str:
    return f"in-{replay_id}-w{week:02d}"


def base_stage(signals: list[dict]) -> str | None:
    if not signals:
        return None
    if any(s["source"] == "bank" and s["strength"] == "strong" for s in signals):
        return "high"
    weak_sources = {s["source"] for s in signals if s["strength"] == "weak"}
    strong_other = any(s["strength"] == "strong" for s in signals)
    if len(weak_sources) >= 2 or strong_other:
        return "medium"
    return "watch"


def signal_key(signals: list[dict]) -> list[str]:
    return sorted(f"{s['source']}:{s['code']}" for s in signals)


def plan_week(watch: dict, signals: list[dict], *, high_recheck: bool) -> WeekPlan:
    base = base_stage(signals)
    incident = watch["open"]
    if incident is None:
        suppressed = set(watch["suppressed"])
        if not signals:
            return WeekPlan("normal", None)
        if suppressed and all(s["code"] in suppressed for s in signals):
            return WeekPlan("suppressed", base)
        return WeekPlan("call", base)
    if not signals:
        quiet = incident["quiet"] + 1
        return WeekPlan("resolve" if quiet >= QUIET_TO_RESOLVE else "quiet", None)
    if signal_key(signals) != incident["last_key"]:
        return WeekPlan("call", base)
    if incident["stage"] == "high" and high_recheck:
        return WeekPlan("call", base)
    return WeekPlan("hold", base)


def _close(watch: dict, incident: dict, week: int, reason: str) -> None:
    watch["closed"].append({**incident, "closed_week": week, "close_reason": reason})
    watch["open"] = None


def _line(stage: str, kind: str, base: str | None, incident_id: str | None, quiet: int, stage_up: bool) -> dict:
    return {
        "stage": stage, "kind": kind, "incident_id": incident_id, "base_stage": base,
        "quiet_weeks": quiet, "stage_up": stage_up, "signals": [], "market": False,
        "agent": None, "why_now": None, "outlook": None, "adjust_reason": None,
        "alert_id": None, "alert_action": None,
    }


def _suppress(watch: dict, signals: list[dict]) -> None:
    watch["suppressed"] = sorted(set(watch["suppressed"]) | {s["code"] for s in signals})
    watch["suppress_quiet"] = 0


def apply_week(
    watch: dict, plan: WeekPlan, signals: list[dict], *, week: int, replay_id: str, stage: str | None = None,
) -> dict:
    incident = watch["open"]
    action = plan.action

    if action == "normal":
        if watch["suppressed"]:
            watch["suppress_quiet"] += 1
            if watch["suppress_quiet"] >= QUIET_TO_RESOLVE:
                watch["suppressed"] = []
                watch["suppress_quiet"] = 0
        line = _line("normal", "normal", None, None, 0, False)
    elif action == "suppressed":
        watch["suppress_quiet"] = 0
        line = _line("normal", "suppressed", plan.base, None, 0, False)
    elif action == "quiet":
        incident["quiet"] += 1
        line = _line(incident["stage"], "quiet", None, incident["id"], incident["quiet"], False)
    elif action == "resolve":
        incident["quiet"] += 1
        line = _line("resolved", "resolved_quiet", None, incident["id"], incident["quiet"], False)
        _close(watch, incident, week, "quiet")
    elif action == "hold":
        incident["quiet"] = 0
        line = _line(incident["stage"], "hold", plan.base, incident["id"], 0, False)
    else:
        line = _apply_call(watch, plan, signals, week, replay_id, stage)

    watch["stages"].append(line["stage"])
    return line


def _apply_call(watch: dict, plan: WeekPlan, signals: list[dict], week: int, replay_id: str, stage: str | None) -> dict:
    decided = stage or plan.base
    incident = watch["open"]
    previous = incident["stage"] if incident else None
    if incident is None:
        watch["suppressed"] = []
        watch["suppress_quiet"] = 0
        incident = {
            "id": incident_id(replay_id, week), "opened_week": week, "stage": plan.base,
            "last_key": [], "quiet": 0, "alert_id": None,
        }
        watch["open"] = incident
    incident["last_key"] = signal_key(signals)
    incident["quiet"] = 0

    if decided == "resolve":
        line = _line("resolved", "resolved_agent", plan.base, incident["id"], 0, False)
        _close(watch, incident, week, "agent")
        _suppress(watch, signals)
        return line

    incident["stage"] = decided
    rises = decided in ("medium", "high") and (previous is None or _RANK[decided] > _RANK[previous])
    return _line(decided, "call", plan.base, incident["id"], 0, rises)


def watch_week(
    watch: dict,
    signals: list[dict],
    *,
    week: int,
    replay_id: str,
    high_recheck: bool,
    explained: bool,
    market: bool,
    investigate: Callable[[str], Any],
) -> tuple[dict, Any]:
    plan = plan_week(watch, signals, high_recheck=high_recheck)
    result = investigate(plan.base) if plan.action == "call" else None
    line = apply_week(watch, plan, signals, week=week, replay_id=replay_id, stage=result.stage if result else None)
    if line["kind"] == "normal" and explained:
        line["kind"] = "explained"
    line["signals"] = signals
    line["market"] = market
    if result is not None:
        line["agent"] = result.agent
        line["why_now"] = result.why_now
        line["outlook"] = result.outlook
        line["adjust_reason"] = result.adjust_reason
    return line, result
