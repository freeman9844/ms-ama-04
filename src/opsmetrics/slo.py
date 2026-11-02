"""SLO1(접수 → 심사역 대기열 지연)과 SLO2(에이전트 단계 성공률)."""

import math
from datetime import datetime
from typing import Any

OUTCOMES = ("ok", "degraded", "failed")


def nearest_rank(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(1, math.ceil(p / 100 * len(ordered))) - 1]


def _reached_at(p: dict[str, Any]) -> str | None:
    return next((t["at"] for t in p.get("timeline") or [] if t.get("status") == "awaiting_officer"), None)


def latency(docs: list[dict[str, Any]], target_min: float = 10.0, min_samples: int = 20) -> dict[str, Any]:
    minutes, unreached, received_only = [], 0, 0
    for p in docs:
        end = _reached_at(p)
        if end is None:
            unreached += 1
            continue
        start = p.get("submitted_at")
        if not start:
            start = p.get("received_at")
            if not start:
                continue
            received_only += 1
        minutes.append((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() / 60)
    p95 = nearest_rank(minutes, 95)
    return {
        "n": len(minutes),
        "p50_min": nearest_rank(minutes, 50),
        "p95_min": p95,
        "max_min": max(minutes) if minutes else None,
        "target_min": target_min,
        "met": p95 is not None and p95 <= target_min,
        "insufficient": len(minutes) < min_samples,
        "unreached": unreached,
        "received_only": received_only,
    }


def agent_success(docs: list[dict[str, Any]], target: float = 0.99) -> dict[str, Any]:
    counts = dict.fromkeys(OUTCOMES, 0)
    by_step: dict[str, dict[str, int]] = {}
    for p in docs:
        for s in p.get("steps") or []:
            if not s.get("agent"):
                continue
            outcome = s.get("outcome") if s.get("outcome") in OUTCOMES else "failed"
            counts[outcome] += 1
            by_step.setdefault(s["step"], dict.fromkeys(OUTCOMES, 0))[outcome] += 1
    total = sum(counts.values())
    rate = counts["ok"] / total if total else None
    return {
        "total": total,
        **counts,
        "rate": rate,
        "degraded_rate": counts["degraded"] / total if total else None,
        "target": target,
        "met": rate is not None and rate >= target,
        "by_step": by_step,
    }
