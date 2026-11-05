"""평가 Container Apps Job을 실행하고, 콘솔 로그(EVALCHUNK)에서 리포트를 복원해 저장합니다."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from evals.gateway_probe import OUTPUT as PROBE_OUTPUT, TASK as PROBE_TASK
from evals.transport import TransportError
from ops.azcli import OpsError, Runner, subscription_runner
from ops.azdenv import get_env
from ops.jobs import collect_with_retry, start_job, wait_job
from ops.smoke_runner import log_hint

REPORTS = Path(__file__).resolve().parents[2] / "eval" / "reports"
REQUIRED = ("summary.json", "report.md", "calls.jsonl")
PROBE_REQUIRED = (PROBE_OUTPUT,)


@dataclass(frozen=True)
class EvalResult:
    execution: str
    out_dir: Path
    summary: dict


def run_eval(runner: Runner, *, suite: str = "all", collect: str | None = None, out_root: Path | None = None,
             sleep: Callable[[float], None] | None = None, poll_wait: float = 15, poll_limit: int = 250,
             log_wait: float = 30, log_limit: int = 20, overrides: dict[str, str] | None = None,
             required: tuple[str, ...] = REQUIRED, rerun: str = "python -m ops eval --collect") -> EvalResult:
    """Job을 시작해 끝날 때까지 기다린 뒤 리포트를 회수한다. collect에 실행 이름을 주면 회수만 다시 한다."""
    sleep = sleep or time.sleep
    env = get_env(runner)
    group, job = env["AZURE_RESOURCE_GROUP"], env["EVAL_JOB_NAME"]
    az = subscription_runner(runner, env)
    if collect:
        execution = collect
    else:
        if overrides is None:
            overrides = None if suite == "all" else {"EVAL_SUITE": suite}
        execution = start_job(az, env, group, job, overrides)
        wait_job(az, env, group, job, execution, sleep=sleep, poll_wait=poll_wait, poll_limit=poll_limit,
                 label="평가")

    # Log Analytics 수집은 수 분 늦을 수 있어 조각이 모두 모일 때까지 다시 조회한다(기본 최대 10분).
    workspace = env.get("LOG_ANALYTICS_CUSTOMER_ID", "")
    try:
        files = collect_with_retry(az, workspace, execution, required, sleep=sleep, wait=log_wait, limit=log_limit)
    except (TransportError, OpsError) as err:  # 조각 누락·수집 지연·일시적 조회 오류
        container = env.get("EVAL_REPORTS_CONTAINER", "eval-reports")
        raise OpsError(
            f"리포트 복원 실패: {err}\n잠시 뒤 회수만 다시: {rerun} {execution}\n"
            f"원본 사본: 데이터 스토리지 Blob {container}/{execution}/ (사설 엔드포인트 전용)\n"
            f"{log_hint(env, execution)}") from err
    out_dir = (out_root or REPORTS) / execution
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (out_dir / name).write_bytes(data)
    return EvalResult(execution, out_dir, json.loads(files[required[0]]))


def run_gateway_probe(runner: Runner, *, mode: str = "all", collect: str | None = None, **kwargs) -> EvalResult:
    """eval Job을 프로브 모드(EVAL_TASK)로 돌려 probe.json을 회수한다. EvalResult.summary가 프로브 결과다."""
    return run_eval(runner, collect=collect, overrides={"EVAL_TASK": PROBE_TASK, "PROBE_MODE": mode},
                    required=PROBE_REQUIRED, rerun=f"python -m ops gateway probe --mode {mode} --collect", **kwargs)
