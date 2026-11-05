"""운영 CLI: python -m ops <명령>."""
from __future__ import annotations

import argparse
import json
import sys
from typing import Callable

from ops import deploy as deploy_module
from ops import faults, opsrun
from ops.azcli import OpsError, Runner, subprocess_runner
from ops.azdenv import get_env
from ops.console_gate import check_console_gate
from ops.eval_runner import run_eval, run_gateway_probe
from ops.lifecycle import pause, resume, teardown
from ops.smoke_runner import run_smoke
from ops.window import close_window, fetch_public_ip, open_window, window_status

EVAL_SUITES = ("all", "classify", "underwriting", "early_warning", "safety")
GATEWAY_PROBE_MODES = ("all", "throttle", "unsafe")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ops", description="데모 환경 운영 도구")
    commands = parser.add_subparsers(dest="command", required=True)

    window = commands.add_parser("window", help="배포 창(운영자 IP 한정 public access)")
    actions = window.add_subparsers(dest="action", required=True)
    opener = actions.add_parser("open", help="배포 창 열기")
    opener.add_argument("--ip", help="허용할 공인 IPv4(기본: 자동 감지)")
    opener.add_argument("--acr", action="store_true", help="az acr build를 위해 ACR도 전체 공개")
    actions.add_parser("close", help="배포 창 닫기")
    actions.add_parser("status", help="배포 창 상태와 IP 변경 확인")

    commands.add_parser("pause", help="Service Bus Premium 삭제(비용 절감)")
    commands.add_parser("resume", help="Service Bus 다시 배포")
    commands.add_parser("smoke", help="VNet 안 smoke Job 실행 + 콘솔 로그인 관문 확인")
    evaluator = commands.add_parser("eval", help="평가 Job 실행 후 리포트를 eval/reports/<실행>/에 저장")
    evaluator.add_argument("--suite", default="all", choices=EVAL_SUITES)
    evaluator.add_argument("--collect", metavar="EXECUTION", help="Job을 다시 돌리지 않고 끝난 실행의 리포트만 회수")
    gateway = commands.add_parser("gateway", help="AI 게이트웨이(APIM) 확인")
    gateway_actions = gateway.add_subparsers(dest="action", required=True)
    probe = gateway_actions.add_parser("probe", help="eval Job으로 토큰 한도(429)·콘텐츠 차단(403) 재현")
    probe.add_argument("--mode", default="all", choices=GATEWAY_PROBE_MODES)
    probe.add_argument("--collect", metavar="EXECUTION", help="Job을 다시 돌리지 않고 끝난 프로브 결과만 회수")
    loader = commands.add_parser("load", help="job-ops로 균형 격자 신청을 제출하고 결과를 ops/reports/<run>/에 저장")
    loader.add_argument("--count", type=int, default=opsrun.MAX_COUNT, help=f"제출 건수(1~{opsrun.MAX_COUNT})")
    loader.add_argument("--interval", type=float, default=15.0, help="제출 간격 초")
    meter = commands.add_parser("metrics", help="SLO·비용·공정성 보고서(ops/reports/<run>/report.md)")
    meter.add_argument("--run", required=True, help="ops load가 출력한 run ID")
    meter.add_argument("--fault-runs", nargs="*", default=[], help="ops fault run이 출력한 run ID")
    meter.add_argument("--fixed-monthly-usd", type=float, help="고정비(월, USD) 수동 입력")
    meter.add_argument("--fx-krw-per-usd", type=float, help="비용 통화가 KRW일 때 환율")
    meter.add_argument("--recollect", action="store_true", help="job-ops로 신청 상태를 다시 수집")
    fault = commands.add_parser("fault", help="장애 주입(리비전 갱신)")
    fault_actions = fault.add_subparsers(dest="action", required=True)
    fault_actions.add_parser("source", help="Mock 데이터 원천 일시 오류").add_argument("mode", choices=("flaky", "off"))
    fault_actions.add_parser("policy", help="Policy 서비스 503").add_argument("mode", choices=("on", "off"))
    fault_actions.add_parser("status", help="현재 장애 모드")
    fault_actions.add_parser("run", help="토글 → 제출 → 대기 → 원복").add_argument(
        "scenario", choices=tuple(faults.SCENARIOS))
    deployer = commands.add_parser("deploy", help="정책·Mock·콘솔·평가 이미지(ACR Build)와 Function 코드 배포")
    deployer.add_argument("target", nargs="?", default="all", choices=deploy_module.TARGETS)
    destroy = commands.add_parser("teardown", help="환경 전체 삭제")
    destroy.add_argument("--confirm", required=True, help="삭제할 리소스 그룹 이름")
    return parser


def main(argv: list[str] | None = None, *, runner: Runner = subprocess_runner,
         public_ip: Callable[[], str] = fetch_public_ip, out=None) -> int:
    out = out or sys.stdout
    args = build_parser().parse_args(argv)
    try:
        if args.command == "window" and args.action == "open":
            ip = open_window(runner, args.ip or public_ip(), acr=args.acr)
            suffix = " (ACR 전체 공개)" if args.acr else ""
            print(f"배포 창을 열었습니다: {ip}{suffix}. 작업 후 `python -m ops window close`로 닫으세요.", file=out)
        elif args.command == "window" and args.action == "close":
            close_window(runner)
            print("배포 창을 닫았습니다.", file=out)
        elif args.command == "window":
            try:
                current = public_ip()
            except (OpsError, OSError):
                current = None
            status = window_status(runner, current)
            state = f"열림({status.allowed_ip})" if status.open else "닫힘"
            print(f"배포 창: {state}, ACR 공개: {status.acr_public}, 현재 공인 IP: {current or '확인 불가'}", file=out)
            if status.warning:
                print(f"경고: {status.warning}", file=out)
                return 2
        elif args.command == "pause":
            deleted = pause(runner)
            print("Service Bus를 삭제했습니다." if deleted else "Service Bus가 이미 없습니다.", file=out)
        elif args.command == "resume":
            resume(runner)
            print("Service Bus를 다시 배포했습니다.", file=out)
        elif args.command == "deploy":
            deployed = deploy_module.deploy(runner, args.target, root=deploy_module.ROOT)
            print("배포했습니다: " + ", ".join(deployed), file=out)
        elif args.command == "smoke":
            print(f"smoke 성공: {run_smoke(runner)}", file=out)
            print(f"콘솔 관문 확인: {check_console_gate(get_env(runner))}", file=out)
        elif args.command == "eval":
            result = run_eval(runner, suite=args.suite, collect=args.collect)
            summary = result.summary
            failures = sum(summary.get("failure_counts", {}).values())
            print(f"평가 완료: {result.execution} (partial={summary.get('partial')}, "
                  f"사용 ${summary.get('spent_usd')}, 실패 {failures}건)", file=out)
            print(f"리포트: {result.out_dir}", file=out)
        elif args.command == "gateway":
            result = run_gateway_probe(runner, mode=args.mode, collect=args.collect)
            print(json.dumps(result.summary, ensure_ascii=False, indent=2), file=out)
            print(f"프로브 결과: {result.out_dir}", file=out)
            if not all(c.get("reproduced") for c in result.summary.get("checks", {}).values()):
                return 2
        elif args.command == "load":
            result = opsrun.load(runner, count=args.count, interval_s=args.interval,
                                 progress=lambda m: print(m, file=out))
            s = result.summary
            print(f"제출 {s['submitted']}건, 도달 {s['reached']}건, 미도달 {len(s['unreached'])}건 "
                  f"(run {result.run_id})", file=out)
            if s.get("aborted"):
                print(f"예산 가드로 제출 중단: 예상 ${s['guard']['projected_usd']:.2f}", file=out)
            print(f"다음: python -m ops metrics --run {result.run_id}", file=out)
        elif args.command == "metrics":
            path = opsrun.metrics(runner, args.run, fault_runs=args.fault_runs,
                                  fixed_monthly_usd=args.fixed_monthly_usd, fx_krw_per_usd=args.fx_krw_per_usd,
                                  recollect=args.recollect)
            print(f"보고서: {path / 'report.md'}", file=out)
        elif args.command == "fault" and args.action == "status":
            for target, mode in faults.status(runner).items():
                print(f"{target}: {mode}", file=out)
        elif args.command == "fault" and args.action == "run":
            result = faults.run_scenario(runner, args.scenario, progress=lambda m: print(m, file=out))
            print(f"{args.scenario} 완료, 원복 확인: run {result.run_id} "
                  f"(도달 {result.summary['reached']}/{result.summary['submitted']})", file=out)
        elif args.command == "fault":
            faults.set_fault(runner, args.action, args.mode in ("flaky", "on"))
            print(f"{args.action} 장애 모드: {args.mode}", file=out)
        elif args.command == "teardown":
            teardown(runner, args.confirm)
            print(f"{args.confirm} 삭제를 마쳤습니다.", file=out)
    except OpsError as err:
        print(f"오류: {err}", file=sys.stderr)
        return 1
    return 0
