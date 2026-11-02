"""장애 시나리오 증거: 신청별 의존성 호출 수(App Insights → Log Analytics)."""
from __future__ import annotations

import json
from collections.abc import Callable

from ops.azcli import OpsError, Runner

LOOKBACK = "3d"


def _query(application_ids: list[str]) -> str:
    # az.cmd는 큰따옴표·줄바꿈이 든 인자를 깨뜨리므로 작은따옴표 한 줄로 만든다. ID는 opsjob 형식([a-z0-9.-_])만 온다.
    ids = ", ".join(f"'{a}'" for a in application_ids)
    return (f"let ids = dynamic([{ids}]); "
            f"let ops = union AppRequests, AppDependencies | where TimeGenerated > ago({LOOKBACK}) "
            "| extend app = tostring(Properties.application_id) | where app in (ids) | distinct OperationId, app; "
            f"AppDependencies | where TimeGenerated > ago({LOOKBACK}) and not(Name startswith 'journey.') "
            "| join kind=inner ops on OperationId "
            "| summarize total = count(), failed = countif(Success == false) by app, Target, AppRoleName")


def dependency_counts(az: Runner, workspace: str, application_ids: list[str], *,
                      sleep: Callable[[float], None], retries: int = 3, wait: float = 30.0,
                      ) -> dict[str, list[dict]]:
    for attempt in range(1, retries + 1):
        try:
            rows = json.loads(az(["az", "monitor", "log-analytics", "query", "-w", workspace,
                                  "--analytics-query", _query(application_ids), "-o", "json"]) or "[]")
            break
        except OpsError:
            if attempt == retries:
                raise
            sleep(wait)
    out: dict[str, list[dict]] = {a: [] for a in application_ids}
    for r in rows:
        out.setdefault(r["app"], []).append({"target": r["Target"], "role": r["AppRoleName"],
                                             "total": int(r["total"]), "failed": int(r["failed"])})
    return out
