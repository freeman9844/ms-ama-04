"""smoke Container Apps Job을 시작하고 끝날 때까지 기다립니다."""
from __future__ import annotations

import time
from typing import Callable

from ops.azcli import Runner, subscription_runner
from ops.azdenv import get_env
from ops.jobs import start_job, wait_job


def run_smoke(runner: Runner, *, sleep: Callable[[float], None] = time.sleep, poll_wait: float = 10,
              poll_limit: int = 185) -> str:
    env = get_env(runner)
    group, job = env["AZURE_RESOURCE_GROUP"], env["SMOKE_JOB_NAME"]
    az = subscription_runner(runner, env)
    execution = start_job(az, env, group, job)
    wait_job(az, env, group, job, execution, sleep=sleep, poll_wait=poll_wait, poll_limit=poll_limit, label="smoke")
    return execution
