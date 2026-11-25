"""운영 지표 화면(/ops): 측정 실행(run_id)별 또는 최근 신청(전체 채널) 투영 → opsmetrics 요약. 고정비는 계산하지 않는다."""

from collections import Counter
from collections.abc import Iterable
from typing import Any, Protocol

from console.i18n import _
from opsmetrics.fairness import gap_pp_from_env
from opsmetrics.labels import AXIS_LABELS, STEP_LABELS
from opsmetrics.prices import load_prices
from opsmetrics.projection import archetype_of
from opsmetrics.summary import summarize
from shared.opinions import CURRENT, opinion_state


RECENT_RUN = "recent"
RECENT_LIMIT = 100
RECENT_LABEL = "최근 신청 100건(전체 채널)"


class OpsStore(Protocol):
    def runs(self, *, legacy: bool = False) -> list[str]: ...
    def by_run(self, run_id: str, *, legacy: bool = False) -> list[dict[str, Any]]: ...
    def recent(self, limit: int, *, legacy: bool = False) -> list[dict[str, Any]]: ...


def _stamp(run_id: str) -> str:
    return run_id.rsplit("-", 1)[-1]


def sorted_runs(runs: Iterable[str]) -> list[str]:
    return sorted(set(runs), key=lambda r: (_stamp(r), r), reverse=True)


def default_run(runs: Iterable[str]) -> str | None:
    ordered = sorted_runs(runs)
    return next((r for r in ordered if r.startswith("load-")), ordered[0] if ordered else None)


def run_options(runs: Iterable[str]) -> list[tuple[str, str]]:
    """선택 목록: 맨 앞이 최근 신청, 그 뒤가 측정 실행(최신순)."""
    return [(RECENT_RUN, _(RECENT_LABEL))] + [(r, r) for r in sorted_runs(runs)]


def scope_docs(store: OpsStore, selected: str, *, legacy: bool = False) -> list[dict[str, Any]]:
    return store.recent(RECENT_LIMIT, legacy=legacy) if selected == RECENT_RUN else store.by_run(selected, legacy=legacy)


def ops_view(docs: list[dict[str, Any]]) -> dict[str, Any]:
    summary = summarize(docs, load_prices(), gap_pp=gap_pp_from_env())
    summary["basis"] = _(summary["basis"])
    cost = summary["ai_cost"]
    if not any(m["calls"] and m["cost_usd"] is not None for m in cost["by_model"].values()):
        cost["per_application_usd"] = None
    for step in cost["by_step"].values():
        if step["calls"] == step["unpriced_calls"]:
            step["cost_usd"] = None
    for axis, a in summary["fairness"]["axes"].items():
        groups = [v for v in a["values"].values() if not v["insufficient"]]
        if sum(v["approve_rate"] is not None for v in groups) < 2:
            a["gap_pp"] = None
        if sum(v["eligible_rate"] is not None for v in groups) < 2:
            a["eligible_gap_pp"] = None
        # opsmetrics uses zero even without a comparison. A controlled pair requires
        # two reviewed recommendations per segment within the same load archetype.
        counts = Counter((arch, value) for doc in docs if (arch := archetype_of(doc.get("applicant_id")))
                         and (value := (doc.get("segment") or {}).get(axis)) is not None
                         and opinion_state(doc.get("assessment")) in CURRENT)
        comparable = Counter(arch for (arch, _), n in counts.items() if n >= 2)
        if not any(n >= 2 for n in comparable.values()):
            a["controlled"]["max_pp"] = None
    return summary
