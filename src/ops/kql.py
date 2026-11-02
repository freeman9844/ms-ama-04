"""장애 시나리오 증거: 신청별 의존성 호출 수(App Insights → Log Analytics)."""
from __future__ import annotations

import json
import re
from collections.abc import Callable

from ops.azcli import OpsError, Runner

LOOKBACK = "3d"

_SUFFIX_LABELS = (
    (".queue.core.windows.net", "storage-queue"),
    (".blob.core.windows.net", "storage-blob"),
    (".table.core.windows.net", "storage-table"),
    (".documents.azure.com", "cosmos"),
    (".services.ai.azure.com", "foundry"),
    (".openai.azure.com", "foundry"),
    (".cognitiveservices.azure.com", "foundry"),
    (".vault.azure.net", "key-vault"),
    (".search.windows.net", "ai-search"),
    (".servicebus.windows.net", "service-bus"),
)
_IPV4 = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")
_HOSTNAME = re.compile(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+")


def target_label(target: str) -> str:
    """의존성 대상의 리소스 호스트명을 역할 라벨로 바꾼다(보고서에 리소스 이름을 남기지 않음). 호스트가 아니면 그대로."""
    host = target.split(":", 1)[0].lower()
    for suffix, label in _SUFFIX_LABELS:
        if host.endswith(suffix):
            return label
    if _IPV4.fullmatch(host):
        return "link-local" if host.startswith("169.254.") else "ip-address"
    if host.startswith("ca-") and (host.endswith(".azurecontainerapps.io") or "." not in host):
        return host.split(".", 1)[0][3:]
    if _HOSTNAME.fullmatch(host):
        return "external-host"
    return target


def relabel(deps: dict[str, list[dict]]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for app, rows in deps.items():
        merged: dict[tuple[str, str], dict] = {}
        for r in rows:
            key = (target_label(r["target"]), r["role"])
            m = merged.setdefault(key, {"target": key[0], "role": r["role"], "total": 0, "failed": 0})
            m["total"] += r["total"]
            m["failed"] += r["failed"]
        out[app] = list(merged.values())
    return out


def _query(application_ids: list[str]) -> str:
    # az.cmd는 큰따옴표·줄바꿈이 든 인자를 깨뜨리므로 작은따옴표 한 줄로 만든다. ID는 opsjob 형식([a-z0-9.-_])만 온다.
    ids = ", ".join(f"'{a}'" for a in application_ids)
    return (f"let ids = dynamic([{ids}]); "
            f"let ops = union AppRequests, AppDependencies | where TimeGenerated > ago({LOOKBACK}) "
            "| extend app = tostring(Properties.application_id) | where app in (ids) | distinct OperationId, app; "
            f"AppDependencies | where TimeGenerated > ago({LOOKBACK}) and not(Name startswith 'journey.') "
            # Policy ASGI 내부 send/receive 스팬(계측 수정 전 데이터)은 호출 수가 아니므로 뺀다.
            "| where not(Name endswith ' http send' or Name endswith ' http receive') "
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
    return relabel(out)
