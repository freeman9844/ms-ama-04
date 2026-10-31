"""smoke Container Apps Job을 시작하고 끝날 때까지 기다립니다."""
from __future__ import annotations

import time
from typing import Callable

from ops.azcli import OpsError, Runner, subscription_runner
from ops.azdenv import get_env

TERMINAL = {"Succeeded", "Failed", "Stopped", "Degraded"}


def log_hint(env: dict[str, str], execution: str) -> str:
    query = (f"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '{execution}' "
             "| project TimeGenerated, Log_s | order by TimeGenerated asc")
    workspace = env.get("LOG_ANALYTICS_CUSTOMER_ID", "<workspace-id>")
    return f'로그 조회: az monitor log-analytics query -w {workspace} --analytics-query "{query}" -o table'


def run_smoke(runner: Runner, *, sleep: Callable[[float], None] = time.sleep, poll_wait: float = 10,
              poll_limit: int = 90) -> str:
    env = get_env(runner)
    group, job = env["AZURE_RESOURCE_GROUP"], env["SMOKE_JOB_NAME"]
    az = subscription_runner(runner, env)
    execution = az(["az", "containerapp", "job", "start", "-g", group, "-n", job,
                    "--query", "name", "-o", "tsv"]).strip()
    for _ in range(poll_limit):
        status = az(["az", "containerapp", "job", "execution", "show", "-g", group, "-n", job,
                     "--job-execution-name", execution, "--query", "properties.status", "-o", "tsv"]).strip()
        if status in TERMINAL:
            if status != "Succeeded":
                raise OpsError(f"smoke 실패: {execution} 상태 {status}\n{log_hint(env, execution)}")
            return execution
        sleep(poll_wait)
    raise OpsError(f"smoke 대기 시간 초과: {execution}\n{log_hint(env, execution)}")
