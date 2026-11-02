"""운영 지표 화면(/ops): 측정 실행(run_id)별 신청 투영 → opsmetrics 요약. 고정비는 계산하지 않는다."""

from collections.abc import Iterable
from typing import Any, Protocol

from opsmetrics.fairness import gap_pp_from_env
from opsmetrics.labels import AXIS_LABELS, STEP_LABELS
from opsmetrics.prices import load_prices
from opsmetrics.summary import summarize


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
