"""Suite 공통: 실패 유형 기록과 예산 가드."""

from typing import Any

from evals.metering import CallRecord, Meter


def failure(kind: str, suite: str, case: str, detail: str, config: str = "") -> dict[str, Any]:
    return {"type": kind, "suite": suite, "config": config, "case": case, "detail": detail[:300]}


def budget_left(meter: Meter, result: dict[str, Any]) -> bool:
    """예산을 넘었으면 부분 결과로 표시하고 False."""
    if meter.over_budget():
        result["partial"] = True
        result["skipped"] = result.get("skipped", 0) + 1
        return False
    return True


def cost_of(calls: list[CallRecord]) -> dict[str, Any]:
    """단가를 아는 호출의 합계와 단가 미확인 호출 수(Decision-1 등)."""
    return {
        "model_calls": len(calls),
        "cost_usd": round(sum(c.cost_usd for c in calls if c.cost_usd is not None), 6),
        "unpriced_calls": sum(c.cost_usd is None for c in calls),
    }
