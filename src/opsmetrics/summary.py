"""보고서·console이 공유하는 요약."""

from typing import Any

from opsmetrics.cost import ai_cost, unit_economics
from opsmetrics.fairness import fairness
from opsmetrics.slo import agent_success, latency

BASIS = "합성 데이터 기준"


def summarize(
    docs: list[dict[str, Any]],
    prices: dict[str, dict[str, Any]],
    fixed_monthly_usd: float | None = None,
    fixed_source: str | None = None,
    gap_pp: float = 20.0,
) -> dict[str, Any]:
    cost = ai_cost(docs, prices)
    return {
        "basis": BASIS,
        "applications": len(docs),
        "latency": latency(docs),
        "agent_success": agent_success(docs),
        "ai_cost": cost,
        "fixed": {"monthly_usd": fixed_monthly_usd, "source": fixed_source},
        "unit_economics": unit_economics(cost["per_application_usd"], fixed_monthly_usd),
        "fairness": fairness(docs, gap_pp=gap_pp),
    }
