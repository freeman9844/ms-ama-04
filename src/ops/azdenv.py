"""azd 환경 값 읽기/쓰기와 프로비저닝."""
from __future__ import annotations

import secrets

from ops.azcli import Runner

# 프로비저닝 때 Bicep 보안 파라미터로 넘기는 앱 비밀. 처음 한 번만 생성하고 이후 유지합니다.
APP_SECRETS = ("MOCK_CONSENT_SECRET", "PSEUDONYM_KEY")
MIN_SECRET_LENGTH = 32


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


def ensure_app_secrets(runner: Runner, env: dict[str, str]) -> None:
    for key in APP_SECRETS:
        if len(env.get(key, "")) < MIN_SECRET_LENGTH:
            set_env(runner, key, secrets.token_urlsafe(48))


def provision(runner: Runner) -> None:
    ensure_app_secrets(runner, get_env(runner))
    runner(["azd", "provision", "--no-prompt"], stream=True)
