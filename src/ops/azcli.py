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
