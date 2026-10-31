"""외부 명령(az, azd) 실행기. 테스트에서는 같은 호출 형식의 가짜로 바꿉니다."""
from __future__ import annotations

import shutil
import subprocess
from typing import Callable

Runner = Callable[..., str]  # runner(args: list[str], *, stream: bool = False) -> str

_NOT_FOUND_MARKERS = ("ResourceNotFound", "ResourceGroupNotFound", "NotFound", "(404)",
                      "was not found", "could not be found")


class OpsError(RuntimeError):
    """운영 명령 실패."""


def subprocess_runner(args: list[str], *, stream: bool = False) -> str:
    """명령을 실행합니다. stream=True면 출력을 그대로 터미널에 보여 주고 빈 문자열을 돌려줍니다."""
    executable = shutil.which(args[0])
    if executable is None:
        raise OpsError(f"'{args[0]}' 명령을 찾을 수 없습니다. PATH를 확인하세요.")
    command = [executable, *args[1:]]
    if stream:
        completed = subprocess.run(command)
        if completed.returncode != 0:
            raise OpsError(f"명령 실패({completed.returncode}): {' '.join(args)}")
        return ""
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    if completed.returncode != 0:
        raise OpsError(f"명령 실패({completed.returncode}): {' '.join(args)}\n{completed.stderr.strip()}")
    return completed.stdout


def is_not_found(err: OpsError) -> bool:
    text = str(err)
    return any(marker in text for marker in _NOT_FOUND_MARKERS)


def subscription_runner(runner: Runner, env: dict[str, str]) -> Runner:
    """az 호출을 azd 환경의 구독(AZURE_SUBSCRIPTION_ID)으로 고정합니다.

    az CLI 기본 구독이 다르면 not-found를 "이미 삭제됨"으로 오인하거나 같은 이름의 다른 리소스를
    건드릴 수 있습니다. az rest는 URL에 구독이 들어가므로 그대로 둡니다.
    """
    subscription = env.get("AZURE_SUBSCRIPTION_ID", "").strip()
    if not subscription:
        raise OpsError("azd 환경에 AZURE_SUBSCRIPTION_ID가 없습니다. "
                       "`azd env new <이름> --subscription <ID>`로 만든 환경에서 실행하세요.")

    def scoped(args: list[str], *, stream: bool = False) -> str:
        args = list(args)
        if args[:1] == ["az"] and args[1:2] != ["rest"]:
            args += ["--subscription", subscription]
        return runner(args, stream=stream)

    return scoped
