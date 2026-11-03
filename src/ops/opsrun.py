"""운영 지표 흐름: job-ops 실행(load·submit·collect), 결과 저장, 지표·보고서 작성."""
from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.transport import TransportError
from ops import costs, kql
from ops.azcli import OpsError, Runner, subscription_runner
from ops.azdenv import get_env
from ops.jobs import collect_with_retry, start_job, wait_job
from ops.opsreport import render
from ops.smoke_runner import log_hint
from opsmetrics.fairness import gap_pp_from_env
from opsmetrics.prices import load_prices
from opsmetrics.summary import summarize
from shared.segments import LOAD_GRID

REPORTS = Path(__file__).resolve().parents[2] / "ops" / "reports"
REQUIRED = ("docs.json", "summary.json")
MAX_COUNT = len(LOAD_GRID)
JOB_TIMEOUT_S = 5400  # job-ops replicaTimeout
JOB_WAIT_S = 1500  # opsjob OPS_WAIT_S 기본값(예산 가드 표본 대기 + 마지막 대기, 각각 최대)


@dataclass(frozen=True)
class JobRun:
    run_id: str
    execution: str
    out_dir: Path
    summary: dict[str, Any]


def new_run_id(kind: str, now: datetime) -> str:
    return f"{kind}-{now:%Y%m%d%H%M%S}"


def _now() -> datetime:
    return datetime.now(UTC)


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def update_meta(out_dir: Path, extra: dict[str, Any]) -> None:
    path = out_dir / "run.json"
    _write(path, {**(_read(path) if path.exists() else {}), **extra})


def run_job(runner: Runner, run_id: str, overrides: dict[str, str], *, out_root: Path | None = None,
            sleep: Callable[[float], None] | None = None, poll_wait: float = 15, poll_limit: int = 250,
            log_wait: float = 30, log_limit: int = 20, progress: Callable[[str], None] | None = None) -> JobRun:
    sleep = sleep or time.sleep
    env = get_env(runner)
    group, job = env["AZURE_RESOURCE_GROUP"], env.get("OPS_JOB_NAME")
    if not job:
        raise OpsError("azd 환경에 OPS_JOB_NAME이 없습니다. 인프라 갱신(azd provision) 후 다시 실행하세요.")
    az = subscription_runner(runner, env)
    execution = start_job(az, env, group, job, {"OPS_RUN_ID": run_id, **overrides})
    if progress:
        progress(f"job-ops 시작: {execution} (run {run_id}). 끝날 때까지 기다립니다.")
    wait_job(az, env, group, job, execution, sleep=sleep, poll_wait=poll_wait, poll_limit=poll_limit,
             label="운영 Job")
    try:
        files = collect_with_retry(az, env.get("LOG_ANALYTICS_CUSTOMER_ID", ""), execution, REQUIRED,
                                   run_id=run_id, sleep=sleep, wait=log_wait, limit=log_limit)
    except (TransportError, OpsError) as err:
        raise OpsError(f"결과 복원 실패: {err}\n{log_hint(env, execution)}") from err
    out_dir = (out_root or REPORTS) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (out_dir / name).write_bytes(data)
    update_meta(out_dir, {"run_id": run_id, "execution": execution, "mode": overrides.get("OPS_MODE")})
    return JobRun(run_id, execution, out_dir, json.loads(files["summary.json"]))


def load(runner: Runner, *, count: int = MAX_COUNT, interval_s: float = 15, now: datetime | None = None,
         **job_kw: Any) -> JobRun:
    if not 1 <= count <= MAX_COUNT:
        raise OpsError(f"--count는 1~{MAX_COUNT}입니다(균형 격자 크기)")
    budget = (count - 1) * interval_s + 2 * JOB_WAIT_S
    if budget > JOB_TIMEOUT_S:
        raise OpsError(f"제출 일정이 Job 제한 시간을 넘습니다: (건수-1)×간격 + 대기 2×{JOB_WAIT_S}초 = {budget:g}초 "
                       f"> {JOB_TIMEOUT_S}초. --count나 --interval을 줄이세요.")
    return run_job(runner, new_run_id("load", now or _now()),
                   {"OPS_MODE": "load", "OPS_COUNT": str(count), "OPS_INTERVAL_S": f"{interval_s:g}",
                    "OPS_SOURCE": "load"}, **job_kw)


def submit(runner: Runner, applicants: Sequence[str], *, interval_s: float, now: datetime | None = None,
           **job_kw: Any) -> JobRun:
    return run_job(runner, new_run_id("fault", now or _now()),
                   {"OPS_MODE": "submit", "OPS_APPLICANTS": ",".join(applicants),
                    "OPS_INTERVAL_S": f"{interval_s:g}", "OPS_SOURCE": "fault"}, **job_kw)


def _fault_section(az: Runner, env: dict[str, str], run_id: str, docs: list[dict[str, Any]], root: Path,
                   kql_fn: Callable[..., dict], sleep: Callable[[float], None]) -> dict[str, Any]:
    meta = (_read(root / run_id / "run.json") if (root / run_id / "run.json").exists() else {}).get("fault") or {}
    ids = [d["application_id"] for d in docs]
    deps, error = None, None
    try:
        deps = kql_fn(az, env.get("LOG_ANALYTICS_CUSTOMER_ID", ""), ids, sleep=sleep) if ids else {}
    except OpsError as err:
        error = str(err).splitlines()[0]
    return {
        "run_id": run_id,
        "scenario": meta.get("scenario"),
        "reverted": meta.get("reverted"),
        "applications": [{"application_id": d["application_id"], "applicant_id": d.get("applicant_id"),
                          "status": d.get("status"), "flags": d.get("flags") or [],
                          "steps": [{"step": s.get("step"), "outcome": s.get("outcome")} for s in d.get("steps") or []]}
                         for d in docs],
        "dependencies": deps,
        "dependencies_error": error,
    }


def metrics(runner: Runner, run_id: str, *, fault_runs: Sequence[str] = (), fixed_monthly_usd: float | None = None,
            fx_krw_per_usd: float | None = None, recollect: bool = False, out_root: Path | None = None,
            now: datetime | None = None, sleep: Callable[[float], None] | None = None,
            cost_fn: Callable[..., tuple[float, str]] = costs.monthly_fixed_usd,
            kql_fn: Callable[..., dict] = kql.dependency_counts, **job_kw: Any) -> Path:
    root, now, sleep = out_root or REPORTS, now or _now(), sleep or time.sleep
    runs = [run_id, *fault_runs]
    if recollect or any(not (root / r / "docs.json").exists() for r in runs):
        job = run_job(runner, new_run_id("collect", now), {"OPS_MODE": "collect", "OPS_RUN_IDS": ",".join(runs)},
                      out_root=root, sleep=sleep, **job_kw)
        collected = _read(job.out_dir / "docs.json")["runs"]
        for r in runs:
            _write(root / r / "docs.json", {"runs": {r: collected.get(r, [])}})
    docs = {r: _read(root / r / "docs.json")["runs"].get(r, []) for r in runs}

    env = get_env(runner)
    az = subscription_runner(runner, env)
    if fixed_monthly_usd is not None:
        fixed, source = fixed_monthly_usd, "수동 입력"
    else:
        try:
            fixed, source = cost_fn(az, env, now.date(), fx_krw_per_usd=fx_krw_per_usd, sleep=sleep)
        except OpsError as err:
            fixed, source = None, f"측정 불가({str(err).splitlines()[0]})"

    out = root / run_id
    load_summary = out / "summary.json"
    data = {
        "run_id": run_id,
        "generated_at": now.isoformat(),
        "load": _read(load_summary) if load_summary.exists() else None,
        "summary": summarize(docs[run_id], load_prices(), fixed, source, gap_pp=gap_pp_from_env()),
        "faults": [_fault_section(az, env, r, docs[r], root, kql_fn, sleep) for r in fault_runs],
    }
    _write(out / "metrics.json", data)
    (out / "report.md").write_text(render(data), encoding="utf-8")
    return out
