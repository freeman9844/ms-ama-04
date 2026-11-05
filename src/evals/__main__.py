"""평가 실행 진입점: python -m evals [--suite ...] [--fake] [--out DIR].

리포트를 쓰면 종료 코드 0이다(suite 오류·예산 도달은 리포트의 partial·errors로 남긴다). 대화 정리는 항상 한다.
"""

import argparse
import json
import os
import secrets
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from agents.gateway import gateway_enabled
from evals import datasets, gateway_probe, report, transport
from evals.backend import Backend, Models, azure_backend, fake_backend
from evals.judge import Judge
from evals.metering import Meter
from evals.runner import EvalRunner
from evals.suites import classify, early_warning, safety, underwriting

SUITES = ("classify", "underwriting", "early_warning", "safety")
DEFAULT_BUDGET_USD = 5.0


def load_prices() -> dict[str, Any]:
    return json.loads(resources.files("evals").joinpath("prices.json").read_text(encoding="utf-8"))


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def parse(argv: list[str] | None, env: Mapping[str, str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m evals")
    p.add_argument("--suite", choices=("all", *SUITES), default=env.get("EVAL_SUITE", "all"))
    p.add_argument("--fake", action="store_true", help="Azure 대신 가짜 HTTP 에뮬레이터로 실행")
    p.add_argument("--out", type=Path, help="결과 디렉터리(기본 eval/reports/<run_id>)")
    p.add_argument("--budget", type=float, default=float(env.get("EVAL_BUDGET_USD", DEFAULT_BUDGET_USD)))
    p.add_argument("--emit", action="store_true", default=env.get("EVAL_EMIT_LOGS") == "1",
                   help="결과 파일을 EVALCHUNK 로그 조각으로 stdout에 남긴다")
    return p.parse_args(argv)


def selected(suite: str) -> list[str]:
    if suite == "all":
        return list(SUITES)
    # 안전성 suite는 심사 suite 결과(주입 케이스·반사실 기준선)를 쓴다.
    return ["underwriting", "safety"] if suite == "safety" else [suite]


def run_suites(names: list[str], *, backend: Backend, meter: Meter, runner: EvalRunner, models: Models,
               key: bytes, log: Callable[[str], None]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    results: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    uw_cases = datasets.underwriting_cases()
    steps: dict[str, Callable[[], dict[str, Any]]] = {
        "classify": lambda: classify.run(
            datasets.classify_apps(), classify.configs(models.decision, models.mini, models.full),
            meter=meter, runner=runner, decider=backend.decider, pseudonym_key=key),
        "underwriting": lambda: underwriting.run(
            uw_cases, [models.mini, models.full], reviewer_model=models.reviewer, meter=meter, runner=runner,
            pseudonym_key=key, judge=Judge(runner, models.judge)),
        "early_warning": lambda: early_warning.run(
            datasets.ew_paths(), [models.mini, models.full], meter=meter, runner=runner, pseudonym_key=key),
        "safety": lambda: safety.run(
            datasets.counterfactual_cases(uw_cases), results.get("underwriting", {}).get("cases", []), models.full,
            reviewer_model=models.reviewer, meter=meter, runner=runner, pseudonym_key=key),
    }
    for name in names:
        log(f"[eval] {name} 시작 (사용 ${meter.spent_usd:.4f})")
        try:
            results[name] = steps[name]()
        except Exception as e:  # noqa: BLE001 - suite 하나가 실패해도 나머지와 리포트는 남긴다
            errors.append(f"{name}: {type(e).__name__}: {str(e)[:300]}")
        log(f"[eval] {name} 끝 (사용 ${meter.spent_usd:.4f})")
    return results, errors


def run_gateway_probe(env: Mapping[str, str], *, runner: EvalRunner, meter: Meter, backend: Backend, models: Models,
                      run_id: str, out_dir: Path, emit: bool, log: Callable[[str], None]) -> int:
    """EVAL_TASK=gateway-probe: 프로브만 돌리고 probe.json을 남긴다(평가 리포트는 만들지 않음)."""
    mode = env.get("PROBE_MODE", "all")
    cleanup: dict[str, int] | None = None
    try:
        result = gateway_probe.run_probe(runner, meter, model=models.mini, mode=mode)
    finally:
        try:
            cleanup = runner.cleanup()
        except Exception as e:  # noqa: BLE001
            log(f"[probe] 대화 정리 실패: {type(e).__name__}")
        backend.close()
    result = {**result, "run_id": run_id, "gateway": runner.gateway_summary(), "cleanup": cleanup}
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / gateway_probe.OUTPUT
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"[probe] {path} " + ", ".join(f"{k}={v['reproduced']}" for k, v in result["checks"].items()))
    if emit:
        log(f"[probe] 로그 조각 {transport.emit(run_id, [path], log)}줄")
    return 0


def main(argv: list[str] | None = None, env: Mapping[str, str] = os.environ,
         log: Callable[[str], None] = print) -> int:
    args = parse(argv, env)
    started = _now()
    run_id = env.get("CONTAINER_APP_JOB_EXECUTION_NAME") or f"local-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    out_dir = args.out or Path("eval") / "reports" / run_id
    prices = load_prices()
    meter = Meter(prices, args.budget)
    models = Models.from_env(env)
    probe = env.get("EVAL_TASK") == gateway_probe.TASK
    # 프로브는 첫 429를 그대로 본다. 클라이언트 재시도가 기다리면 1분 토큰 창이 풀려 한도가 재현되지 않는다.
    backend = (fake_backend(meter, models, retry_throttle=not probe) if args.fake
               else azure_backend(meter, models, env, retry_throttle=not probe))
    runner = EvalRunner(backend.agents, meter, gateway=gateway_enabled(env) and not args.fake)
    if probe:
        return run_gateway_probe(env, runner=runner, meter=meter, backend=backend, models=models, run_id=run_id,
                                 out_dir=out_dir, emit=args.emit, log=log)
    # 가명 키는 실행마다 새로 만든다(평가 결과는 가명값을 저장하지 않는다).
    key = secrets.token_bytes(32)
    errors: list[str] = []
    cleanup: dict[str, int] | None = None
    try:
        results, errors = run_suites(selected(args.suite), backend=backend, meter=meter, runner=runner,
                                     models=models, key=key, log=log)
    finally:
        try:
            cleanup = runner.cleanup()
        except Exception as e:  # noqa: BLE001
            errors.append(f"cleanup: {type(e).__name__}: {str(e)[:300]}")
        backend.close()
    summary = report.build_summary(
        run_id=run_id, mode=backend.info.get("mode", "?"), started_at=started, finished_at=_now(),
        budget_usd=args.budget, spent_usd=meter.spent_usd, dataset=datasets.summary(),
        models={"full": models.full, "mini": models.mini, "decision": models.decision, "reviewer": models.reviewer,
                "judge": models.judge},
        prices_source=prices.get("_source", ""), suites=results, by_model=meter.by_model(), cleanup=cleanup, errors=errors,
        gateway=runner.gateway_summary(),
    )
    paths = report.write(out_dir, summary, meter.records)
    log(f"[eval] 리포트 {out_dir} (partial={summary['partial']}, 실패 {sum(summary['failure_counts'].values())}건)")
    if args.emit:
        log(f"[eval] 로그 조각 {transport.emit(run_id, paths, log)}줄")
    if env.get("EVAL_BLOB_ACCOUNT_URL") and not args.fake:
        try:
            from azure.identity import ManagedIdentityCredential

            cred = ManagedIdentityCredential(client_id=env.get("AZURE_CLIENT_ID"))
            names = transport.upload(paths, account_url=env["EVAL_BLOB_ACCOUNT_URL"],
                                     container=env.get("EVAL_BLOB_CONTAINER", "eval-reports"), run_id=run_id,
                                     token=lambda: cred.get_token(transport.STORAGE_SCOPE).token)
            log(f"[eval] Blob 업로드 {len(names)}개")
        except Exception as e:  # noqa: BLE001 - 보관 사본 실패는 로그 조각으로 대신한다
            log(f"[eval] Blob 업로드 실패: {type(e).__name__}: {str(e)[:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
