"""운영 CLI: python -m ops <명령>."""
from __future__ import annotations

import argparse
import sys
from typing import Callable

from ops import deploy as deploy_module
from ops.azcli import OpsError, Runner, subprocess_runner
from ops.azdenv import get_env
from ops.console_gate import check_console_gate
from ops.eval_runner import run_eval
from ops.lifecycle import pause, resume, teardown
from ops.smoke_runner import run_smoke
from ops.window import close_window, fetch_public_ip, open_window, window_status

EVAL_SUITES = ("all", "classify", "underwriting", "early_warning", "safety")


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
        elif args.command == "teardown":
            teardown(runner, args.confirm)
            print(f"{args.confirm} 삭제를 마쳤습니다.", file=out)
    except OpsError as err:
        print(f"오류: {err}", file=sys.stderr)
        return 1
    return 0
