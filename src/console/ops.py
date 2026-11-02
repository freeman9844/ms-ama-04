"""운영 지표 화면(/ops): 측정 실행(run_id)별 신청 투영 → opsmetrics 요약. 고정비는 계산하지 않는다."""

from collections.abc import Iterable
from typing import Any, Protocol

from opsmetrics.fairness import gap_pp_from_env
from opsmetrics.prices import load_prices
from opsmetrics.summary import summarize

AXIS_LABELS = {"industry": "업종", "region": "지역", "business_age_band": "업력"}
STEP_LABELS = {"consent": "동의", "assemble": "자료 조립", "classify": "거래 분류", "underwrite": "심사 분석",
               "policy": "정책 판정", "assess": "신용 평가"}


class OpsStore(Protocol):
    def runs(self) -> list[str]: ...
    def by_run(self, run_id: str) -> list[dict[str, Any]]: ...


def _stamp(run_id: str) -> str:
    return run_id.rsplit("-", 1)[-1]


def sorted_runs(runs: Iterable[str]) -> list[str]:
    return sorted(set(runs), key=lambda r: (_stamp(r), r), reverse=True)


def default_run(runs: Iterable[str]) -> str | None:
    ordered = sorted_runs(runs)
    return next((r for r in ordered if r.startswith("load-")), ordered[0] if ordered else None)


def ops_view(docs: list[dict[str, Any]]) -> dict[str, Any]:
    return summarize(docs, load_prices(), gap_pp=gap_pp_from_env())
