"""azd 환경 값 읽기/쓰기와 프로비저닝."""
from __future__ import annotations

from ops.azcli import Runner


def parse_env_values(text: str) -> dict[str, str]:
    """`azd env get-values` 출력(KEY="VALUE" 줄)을 사전으로 바꿉니다."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        raw = raw.strip()
        if len(raw) >= 2 and raw[0] == raw[-1] == '"':
            raw = raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        values[key.strip()] = raw
    return values


def get_env(runner: Runner) -> dict[str, str]:
    return parse_env_values(runner(["azd", "env", "get-values"]))


def set_env(runner: Runner, key: str, value: str) -> None:
    runner(["azd", "env", "set", key, value])


def provision(runner: Runner) -> None:
    runner(["azd", "provision", "--no-prompt"], stream=True)
