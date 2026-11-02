"""Container Apps Job 공통: 시작(컨테이너 환경 변수 덮어쓰기)·종료 대기·콘솔 로그 조각(EVALCHUNK) 회수."""
from __future__ import annotations

import json
from collections.abc import Callable, Iterable

from evals.transport import TAG, TransportError, reassemble
from ops.azcli import OpsError, Runner, rest_post
from ops.smoke_runner import TERMINAL, log_hint

START_API = "2024-03-01"


def start_job(az: Runner, env: dict[str, str], group: str, job: str,
              overrides: dict[str, str] | None = None) -> str:
    if not overrides:
        return az(["az", "containerapp", "job", "start", "-g", group, "-n", job,
                   "--query", "name", "-o", "tsv"]).strip()
    # start 템플릿 덮어쓰기는 컨테이너 정의를 통째로 바꾼다. 기존 정의를 복사하고 지정한 변수만 바꿔 ARM으로 시작한다.
    template = json.loads(az(["az", "containerapp", "job", "show", "-g", group, "-n", job,
                              "--query", "properties.template", "-o", "json"]))
    containers = template["containers"]
    containers[0]["env"] = ([e for e in containers[0].get("env", []) if e.get("name") not in overrides]
                            + [{"name": k, "value": v} for k, v in overrides.items()])
    body = {"containers": containers}
    if template.get("initContainers"):
        body["initContainers"] = template["initContainers"]
    url = (f"https://management.azure.com/subscriptions/{env['AZURE_SUBSCRIPTION_ID']}/resourceGroups/{group}"
           f"/providers/Microsoft.App/jobs/{job}/start?api-version={START_API}")
    return rest_post(az, url, body)["name"]


def wait_job(az: Runner, env: dict[str, str], group: str, job: str, execution: str, *,
             sleep: Callable[[float], None], poll_wait: float, poll_limit: int, label: str) -> None:
    for _ in range(poll_limit):
        status = az(["az", "containerapp", "job", "execution", "show", "-g", group, "-n", job,
                     "--job-execution-name", execution, "--query", "properties.status", "-o", "tsv"]).strip()
        if status in TERMINAL:
            break
        sleep(poll_wait)
    else:
        raise OpsError(f"{label} 대기 시간 초과: {execution}\n{log_hint(env, execution)}")
    if status != "Succeeded":
        raise OpsError(f"{label} 실패: {execution} 상태 {status}\n{log_hint(env, execution)}")


def _query(execution: str) -> str:
    return (f"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '{execution}' "
            f"| where Log_s has '{TAG}' | project Log_s")


def _run_id(lines: list[str], execution: str) -> str:
    # Job에 실행 이름이 주입되지 않으면 local-<시각>을 쓴다. 로그는 이미 실행 이름으로 걸렀으므로 그 하나를 고른다.
    ids = {line[line.find(TAG):].split(" ")[1] for line in lines if TAG + " " in line}
    if execution in ids or len(ids) != 1:
        return execution
    return ids.pop()


def collect_files(az: Runner, workspace: str, execution: str, required: Iterable[str],
                  run_id: str | None = None) -> dict[str, bytes]:
    rows = json.loads(az(["az", "monitor", "log-analytics", "query", "-w", workspace,
                          "--analytics-query", _query(execution), "-o", "json"]) or "[]")
    lines = [row.get("Log_s", "") for row in rows]
    files = reassemble(lines, run_id or _run_id(lines, execution))
    missing = [name for name in required if name not in files]
    if missing:
        raise TransportError(f"파일 누락: {', '.join(missing)}")
    return files


def collect_with_retry(az: Runner, workspace: str, execution: str, required: Iterable[str], *,
                       sleep: Callable[[float], None], wait: float, limit: int,
                       run_id: str | None = None) -> dict[str, bytes]:
    """Log Analytics 수집은 수 분 늦을 수 있어 조각이 모두 모일 때까지 다시 조회한다. 마지막 오류는 그대로 올린다."""
    for attempt in range(1, limit + 1):
        try:
            return collect_files(az, workspace, execution, required, run_id)
        except (TransportError, OpsError):
            if attempt == limit:
                raise
            sleep(wait)
    raise OpsError("limit은 1 이상이어야 합니다")
