"""배포 창: 운영자 IP 하나만 PaaS public access를 잠시 허용합니다(스펙 §2.2)."""
from __future__ import annotations

import ipaddress
import urllib.request
from dataclasses import dataclass
from typing import Callable

from ops.azcli import OpsError, Runner
from ops.azdenv import get_env, provision, set_env

CLOSED = "closed"
PUBLIC_IP_URL = "https://api.ipify.org"


def validate_operator_ip(ip: str) -> str:
    try:
        address = ipaddress.IPv4Address(ip.strip())
    except ValueError as err:
        raise OpsError(f"IPv4 주소가 아닙니다: {ip!r}") from err
    if not address.is_global:
        raise OpsError(f"공인 IP가 아닙니다: {address}. 운영자 PC의 공인 IP를 지정하세요.")
    return str(address)


def fetch_public_ip(fetch: Callable[[], str] | None = None) -> str:
    def default_fetch() -> str:
        with urllib.request.urlopen(PUBLIC_IP_URL, timeout=10) as resp:
            return resp.read().decode("ascii").strip()
    return validate_operator_ip((fetch or default_fetch)())


def ensure_caphost_skip(runner: Runner, env: dict[str, str]) -> None:
    """최초 프로비저닝 이후에는 capability host 재-PUT을 피합니다(스펙 §7)."""
    if env.get("AI_ACCOUNT_NAME") and env.get("DEPLOY_CAPHOST") != "false":
        set_env(runner, "DEPLOY_CAPHOST", "false")


def open_window(runner: Runner, ip: str, *, acr: bool = False) -> str:
    ip = validate_operator_ip(ip)
    ensure_caphost_skip(runner, get_env(runner))
    set_env(runner, "OPERATOR_IP", ip)
    # 콘솔 허용 IP는 창을 닫아도 유지합니다(시연 중 콘솔 접속). close_window는 건드리지 않습니다.
    set_env(runner, "CONSOLE_IP", ip)
    set_env(runner, "ACR_PUBLIC", "true" if acr else "false")
    provision(runner)
    return ip


def close_window(runner: Runner) -> None:
    ensure_caphost_skip(runner, get_env(runner))
    set_env(runner, "OPERATOR_IP", CLOSED)
    set_env(runner, "ACR_PUBLIC", "false")
    provision(runner)


@dataclass(frozen=True)
class WindowStatus:
    open: bool
    allowed_ip: str | None
    current_ip: str | None
    acr_public: bool
    warning: str | None


def window_status(runner: Runner, current_ip: str | None) -> WindowStatus:
    env = get_env(runner)
    allowed = env.get("OPERATOR_IP", CLOSED)
    is_open = allowed not in ("", CLOSED)
    warning = None
    if is_open and current_ip and current_ip != allowed:
        warning = (f"현재 공인 IP {current_ip}가 허용된 IP {allowed}와 다릅니다. "
                   "`python -m ops window open`으로 다시 여세요.")
    return WindowStatus(is_open, allowed if is_open else None, current_ip,
                        env.get("ACR_PUBLIC") == "true", warning)
