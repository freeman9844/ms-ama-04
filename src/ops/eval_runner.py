"""평가 Container Apps Job을 실행하고, 콘솔 로그(EVALCHUNK)에서 리포트를 복원해 저장합니다."""
from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from evals.transport import TAG, TransportError, reassemble
from ops.azcli import OpsError, Runner, subscription_runner
from ops.azdenv import get_env
from ops.smoke_runner import TERMINAL, log_hint

REPORTS = Path(__file__).resolve().parents[2] / "eval" / "reports"
REQUIRED = ("summary.json", "report.md", "calls.jsonl")
START_API = "2024-03-01"


@dataclass(frozen=True)
class EvalResult:
    execution: str
    out_dir: Path
    summary: dict


def _query(execution: str) -> str:
    return (f"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '{execution}' "
            f"| where Log_s has '{TAG}' | project Log_s")


def _run_id(lines: list[str], execution: str) -> str:
    # Job에 실행 이름이 주입되지 않으면 local-<시각>을 쓴다. 로그는 이미 실행 이름으로 걸렀으므로 그 하나를 고른다.
    ids = {line[line.find(TAG):].split(" ")[1] for line in lines if TAG + " " in line}
    if execution in ids or len(ids) != 1:
        return execution
    return ids.pop()


def collect(az: Runner, workspace: str, execution: str) -> dict[str, bytes]:
    rows = json.loads(az(["az", "monitor", "log-analytics", "query", "-w", workspace,
                          "--analytics-query", _query(execution), "-o", "json"]) or "[]")
    lines = [row.get("Log_s", "") for row in rows]
    files = reassemble(lines, _run_id(lines, execution))
    missing = [name for name in REQUIRED if name not in files]
    if missing:
        raise TransportError(f"파일 누락: {', '.join(missing)}")
    return files


def _start(az: Runner, env: dict[str, str], group: str, job: str, suite: str) -> str:
    if suite == "all":
        return az(["az", "containerapp", "job", "start", "-g", group, "-n", job,
                   "--query", "name", "-o", "tsv"]).strip()
    # start 템플릿 덮어쓰기는 컨테이너 정의를 통째로 바꾼다. 기존 정의를 복사하고 EVAL_SUITE만 바꿔 ARM으로 시작한다
    # (az.cmd는 따옴표가 든 인자를 깨뜨리므로 본문은 파일로 넘긴다).
    template = json.loads(az(["az", "containerapp", "job", "show", "-g", group, "-n", job,
                              "--query", "properties.template", "-o", "json"]))
    containers = template["containers"]
    containers[0]["env"] = ([e for e in containers[0].get("env", []) if e.get("name") != "EVAL_SUITE"]
                            + [{"name": "EVAL_SUITE", "value": suite}])
    body = {"containers": containers}
    if template.get("initContainers"):
        body["initContainers"] = template["initContainers"]
    url = (f"https://management.azure.com/subscriptions/{env['AZURE_SUBSCRIPTION_ID']}/resourceGroups/{group}"
           f"/providers/Microsoft.App/jobs/{job}/start?api-version={START_API}")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(body, f)
    try:
        return json.loads(az(["az", "rest", "--method", "post", "--url", url, "--body", f"@{f.name}"]))["name"]
    finally:
        os.unlink(f.name)


def run_eval(runner: Runner, *, suite: str = "all", out_root: Path | None = None,
             sleep: Callable[[float], None] | None = None, poll_wait: float = 15, poll_limit: int = 250,
             log_wait: float = 30, log_limit: int = 20) -> EvalResult:
    sleep = sleep or time.sleep
    env = get_env(runner)
    group, job = env["AZURE_RESOURCE_GROUP"], env["EVAL_JOB_NAME"]
    az = subscription_runner(runner, env)
    execution = _start(az, env, group, job, suite)
    for _ in range(poll_limit):
        status = az(["az", "containerapp", "job", "execution", "show", "-g", group, "-n", job,
                     "--job-execution-name", execution, "--query", "properties.status", "-o", "tsv"]).strip()
        if status in TERMINAL:
            break
        sleep(poll_wait)
    else:
        raise OpsError(f"평가 대기 시간 초과: {execution}\n{log_hint(env, execution)}")
    if status != "Succeeded":
        raise OpsError(f"평가 실패: {execution} 상태 {status}\n{log_hint(env, execution)}")

    # Log Analytics 수집은 수 분 늦을 수 있어 조각이 모두 모일 때까지 다시 조회한다(기본 최대 10분).
    workspace = env.get("LOG_ANALYTICS_CUSTOMER_ID", "")
    for attempt in range(1, log_limit + 1):
        try:
            files = collect(az, workspace, execution)
            break
        except TransportError as err:
            if attempt == log_limit:
                raise OpsError(f"리포트 복원 실패: {err}\n{log_hint(env, execution)}") from err
            sleep(log_wait)
    out_dir = (out_root or REPORTS) / execution
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (out_dir / name).write_bytes(data)
    return EvalResult(execution, out_dir, json.loads(files["summary.json"]))
