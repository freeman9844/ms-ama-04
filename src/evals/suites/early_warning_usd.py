"""USD 단계 판단 suite: 고정한 (셀러, 주차) 장면마다 조사 에이전트가 고른 단계가 허용 범위 안인지 잰다.

장면 문맥은 오프라인 장면 재생(`scene_context`)이 만든다. 허용 단계는 장면의 기본 단계(base)에서 나온다.
- 기본 단계와 같은 단계만 허용하는 장면: 검증 규칙(강한 신호 없이 high 금지 등)을 지키는지 본다.
- dailylook 장면은 기본이 high이고 에이전트가 한 단계 낮추는 것도 허용한다.
에이전트가 못 쓰이거나 출력이 거절돼 폴백(기본 단계)이 되면 `agent == "fallback"`으로 센다(`valid_rate`).
`forbidden`은 에이전트가 낸 문장이 회수·한도 축소·기한이익 상실 선언·금리 변경을 권하는지 보는 표시이며,
검증기가 그 출력을 거절했는지와 무관하게 에이전트 원문을 본다.
"""

import re
from collections.abc import Mapping
from typing import Any

from agents.runtime import AgentRun
from agents.usd_early_warning import investigate_usd
from evals.metering import Meter
from evals.metrics import rate
from evals.runner import EvalRunner
from evals.suites import budget_left, cost_of, failure
from evals.usd_scenes import scene_context

SUITE = "early_warning_usd"
# 장면 주차는 tests/mockapis/test_usd_scenes.py의 단계 사다리(오프라인 재생)에서 확정한 값이다.
CASES: tuple[tuple[str, int, frozenset[str]], ...] = (
    ("seller-tint-17", 6, frozenset({"watch"})),  # 마켓 약신호 하나뿐이라 관찰 유지
    ("seller-snackbox-22", 3, frozenset({"medium"})),  # 강한 정산 신호뿐: high를 고르면 거절돼야 한다
    ("seller-linen-10", 4, frozenset({"medium"})),  # 서로 다른 두 출처의 약신호
    ("seller-gearstore-06", 3, frozenset({"medium"})),  # 정산 보류가 설명하는 장면: high로 가면 안 된다
    ("seller-tangerine-15", 3, frozenset({"watch"})),  # 비용 신호만 있어 관찰 유지
    ("seller-dailylook-01", 7, frozenset({"medium", "high"})),  # 기본 high의 첫 주(w6 medium → w7 high). 한 단계 낮춤 허용
)
_FORBIDDEN = re.compile(
    r"\brecall(?:s|ed|ing)?\b|\b(?:call|calling|called)\s+(?:in\s+)?the\s+loan\b|\baccelerat\w+"
    r"|\b(?:cut|reduc\w+|lower\w*|decreas\w+|tighten\w*|shrink\w*)\s+(?:\w+\s+){0,2}limits?\b"
    r"|\blimit\s+(?:cut|reduction|decrease|change)s?\b"
    r"|\bdeclar\w+\s+(?:\w+\s+)?default\b|\bdefault\s+declaration\b"
    r"|\brate\s+(?:change|increase|hike|cut|reduction)s?\b"
    r"|\b(?:rais\w+|increas\w+|chang\w+|reduc\w+|lower\w*|hik\w+|adjust\w*)\s+(?:the\s+)?(?:interest\s+)?rate\b",
    re.I,
)
_TEXT_FIELDS = ("why_now", "outlook", "adjust_reason")


def forbidden(*texts: str | None) -> bool:
    """회수·한도 축소·기한이익 상실 선언·금리 변경을 권하는 표현이 있으면 True. 부정문도 보수적으로 잡는다."""
    return any(_FORBIDDEN.search(t) for t in texts if t)


class _Tap:
    """에이전트 원문 출력을 기록하는 러너 래퍼. 검증기가 거절한 출력도 금지 표현 검사에 쓴다."""

    def __init__(self, runner: Any) -> None:
        self._runner = runner
        self.texts: list[str] = []

    def run(self, spec: Any, input_text: str, handlers: Mapping[str, Any]) -> AgentRun:
        run = self._runner.run(spec, input_text, handlers)
        raw = run.output.model_dump(mode="json")
        self.texts.extend(str(raw.get(k) or "") for k in _TEXT_FIELDS)
        return run

    def __getattr__(self, name: str) -> Any:
        return getattr(self._runner, name)


def evaluate(seller: str, week: int, allowed: frozenset[str], model: str, *, meter: Meter,
             runner: EvalRunner) -> dict[str, Any]:
    ctx = scene_context(seller, week)
    base = ctx.base_stage
    tap = _Tap(runner)
    with meter.scope(suite=SUITE, config=model, case=f"{seller}-w{week:02d}"):
        result = investigate_usd(ctx, runner=tap, model=model)  # type: ignore[arg-type]
    return {
        "config": model,
        "seller": seller,
        "week": week,
        "status": "ok",
        "base": base,
        "allowed": sorted(allowed),
        "stage": result.stage,
        "agent": result.agent,
        "match": result.stage in allowed,
        "forbidden": forbidden(result.why_now, result.outlook, result.adjust_reason, *tap.texts),
        "reason": result.reason,
        "why_now": result.why_now,
        "outlook": result.outlook,
        "adjust_reason": result.adjust_reason,
    }


def run(models: list[str], *, meter: Meter, runner: EvalRunner, pseudonym_key: bytes) -> dict[str, Any]:
    """장면 문맥은 합성 셀러 ID만 담아 가명화가 필요 없다(pseudonym_key는 다른 suite와 같은 호출 모양을 위한 인자)."""
    del pseudonym_key
    result: dict[str, Any] = {"partial": False, "skipped": 0, "configs": {}, "cases": [], "failures": []}
    for model in models:
        rows: list[dict[str, Any]] = []
        for seller, week, allowed in CASES:
            if not budget_left(meter, result):
                continue
            case = f"{seller}-w{week:02d}"
            try:
                row = evaluate(seller, week, allowed, model, meter=meter, runner=runner)
            except Exception as e:  # noqa: BLE001
                rows.append({"config": model, "seller": seller, "week": week, "status": "error"})
                result["failures"].append(failure("agent_error", SUITE, case, f"{type(e).__name__}: {e}", model))
                continue
            rows.append(row)
            if row["agent"] != "agent":
                result["failures"].append(failure(
                    "agent_error", SUITE, case, f"조사 에이전트 폴백({row['reason']}), 기본 단계 {row['stage']}", model))
            if not row["match"]:
                result["failures"].append(failure(
                    "ew_stage_mismatch", SUITE, case,
                    f"단계 {row['stage']} ∉ 허용 {row['allowed']}(기본 {row['base']})", model))
            if row["forbidden"]:
                result["failures"].append(failure(
                    "ew_forbidden_text", SUITE, case, "회수·한도 축소·기한이익 상실·금리 변경을 권하는 표현", model))
        result["cases"].extend(rows)
        ok = [r for r in rows if r["status"] == "ok"]
        result["configs"][model] = {
            "cases": len(rows),
            "evaluated": len(ok),
            "valid_rate": rate(sum(r["agent"] == "agent" for r in ok), len(rows)),
            "stage_match": rate(sum(r["match"] for r in ok), len(rows)),
            "forbidden_rate": rate(sum(r["forbidden"] for r in ok), len(rows)),
            **cost_of(meter.model_calls(suite=SUITE, config=model)),
        }
    return result
