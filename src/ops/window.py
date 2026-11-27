"""배포 창: 운영자 IP 하나만 PaaS public access를 잠시 허용합니다(스펙 §2.2). 콘솔 App Gateway 인증서도 여기서 맞춥니다.

창이 바꾸는 리소스는 Functions·Foundry 계정·ACR뿐이라, 이미 프로비저닝된 환경에서는 전체 `azd provision`(4~5분) 대신
그 리소스만 az로 Bicep 선언과 같은 값으로 바꿉니다(빠른 경로). azd 환경 값도 같이 기록하므로 다음 프로비저닝도 같은 상태입니다.
"""
from __future__ import annotations

import ipaddress
import json
import time
import urllib.request
from dataclasses import dataclass
from typing import Callable

from ops.appgw_cert import ensure_console_cert
from ops.azcli import OpsError, Runner, rest_patch, subscription_runner
from ops.azdenv import get_env, provision, set_env

CLOSED = "closed"
CLOSING = "closing"
# close의 프로비저닝이 끝났는지 기록합니다. 없으면(이전 버전 환경) OPERATOR_IP만 봅니다.
WINDOW_STATE = "WINDOW_STATE"
PUBLIC_IP_URL = "https://api.ipify.org"
# 이 값이 모두 있어야(최초 프로비저닝과 콘솔 인증서가 끝난 환경) 빠른 경로를 씁니다.
FAST_PATH_KEYS = ("AZURE_SUBSCRIPTION_ID", "AZURE_RESOURCE_GROUP", "FUNCTION_APP_NAME", "ACR_NAME",
                  "AI_ACCOUNT_NAME", "APPGW_CERT_SECRET_ID")
ARM = "https://management.azure.com"
WEB_API = "2024-04-01"  # infra/modules/functions.bicep
AI_API = "2025-04-01-preview"  # infra/modules/foundry/account.bicep
ACCOUNT_WAIT_TRIES = 36
ACCOUNT_WAIT_S = 5.0


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


def fast_path_ready(env: dict[str, str]) -> bool:
    return all(env.get(key) for key in FAST_PATH_KEYS)


def _wait_account_succeeded(az: Runner, url: str, sleep: Callable[[float], None]) -> None:
    for _ in range(ACCOUNT_WAIT_TRIES):
        account = json.loads(az(["az", "rest", "--method", "get", "--url", url]) or "{}")
        if account.get("properties", {}).get("provisioningState") == "Succeeded":
            return
        sleep(ACCOUNT_WAIT_S)
    raise OpsError("Foundry 계정 네트워크 변경이 끝나지 않았습니다. 잠시 뒤 같은 명령을 다시 실행하세요.")


def apply_window(az: Runner, env: dict[str, str], ip: str | None, acr: bool, *,
                 sleep: Callable[[float], None] = time.sleep) -> None:
    """Bicep(functions.bicep, foundry/account.bicep, acr.bicep)이 창 상태로 만드는 값을 그 리소스에만 씁니다.

    ip가 None이면 닫습니다. 열 때는 규칙을 먼저 넣고 공개하고, 닫을 때는 먼저 비공개로 바꾼 뒤 규칙을 지웁니다.
    """
    providers = (f"{ARM}/subscriptions/{env['AZURE_SUBSCRIPTION_ID']}/resourceGroups/"
                 f"{env['AZURE_RESOURCE_GROUP']}/providers")
    site = f"{providers}/Microsoft.Web/sites/{env['FUNCTION_APP_NAME']}"
    account = f"{providers}/Microsoft.CognitiveServices/accounts/{env['AI_ACCOUNT_NAME']}?api-version={AI_API}"
    public = "Enabled" if ip else "Disabled"
    rules = [{"name": "operator", "ipAddress": f"{ip}/32", "action": "Allow", "priority": 100}] if ip else []
    web = {"properties": {"ipSecurityRestrictionsDefaultAction": "Deny", "ipSecurityRestrictions": rules,
                          "scmIpSecurityRestrictionsUseMain": True}}
    acls = {"properties": {"publicNetworkAccess": public, "networkAcls": {
        "defaultAction": "Deny", "bypass": "AzureServices",
        "ipRules": [{"value": ip}] if ip else [], "virtualNetworkRules": []}}}
    registry = ["az", "acr", "update", "-n", env["ACR_NAME"], "--public-network-enabled", "true" if acr else "false",
                "--default-action", "Allow" if acr else "Deny", "-o", "none"]

    def site_access() -> None:
        rest_patch(az, f"{site}?api-version={WEB_API}", {"properties": {"publicNetworkAccess": public}})

    def site_rules() -> None:
        rest_patch(az, f"{site}/config/web?api-version={WEB_API}", web)

    steps = [site_rules, site_access] if ip else [lambda: az(registry), site_access, site_rules]
    for step in steps:
        step()
    rest_patch(az, account, acls)
    _wait_account_succeeded(az, account, sleep)
    if ip:
        az(registry)


def open_window(runner: Runner, ip: str, *, acr: bool = False, full_provision: bool = False) -> str:
    ip = validate_operator_ip(ip)
    env = get_env(runner)
    ensure_caphost_skip(runner, env)
    set_env(runner, WINDOW_STATE, "open")
    set_env(runner, "OPERATOR_IP", ip)
    # 여기부터 실패하면 네트워크가 일부 열린 채일 수 있습니다. 자동으로 닫지 않고 닫는 명령을 안내합니다.
    try:
        # 콘솔 WAF 허용 IP는 처음 한 번만 정합니다. 바꾸려면 `ops waf ip-filter on --ip`를 씁니다.
        if not env.get("CONSOLE_IP"):
            set_env(runner, "CONSOLE_IP", ip)
        set_env(runner, "ACR_PUBLIC", "true" if acr else "false")
        if not full_provision and fast_path_ready(env):
            apply_window(subscription_runner(runner, env), env, ip, acr)
            if ensure_console_cert(runner):
                provision(runner)
        else:
            provision(runner)
            # 첫 프로비저닝이 App Gateway DNS 이름(APPGW_FQDN)을 만듭니다. 인증서 비밀 ID가 새로 생기면 한 번 더 돌려 게이트웨이를 만듭니다.
            if ensure_console_cert(runner):
                provision(runner)
    except Exception as err:
        raise OpsError(f"{err}\n배포 창이 일부 열렸을 수 있습니다. "
                       "`python -m ops window close`로 닫으세요.") from err
    return ip


def close_window(runner: Runner, *, full_provision: bool = False) -> None:
    env = get_env(runner)
    ensure_caphost_skip(runner, env)
    # 네트워크 변경이 성공해야 Azure에서 운영자 IP 규칙이 빠집니다. 그 전까지는 "닫는 중"으로 남깁니다.
    set_env(runner, WINDOW_STATE, CLOSING)
    set_env(runner, "OPERATOR_IP", CLOSED)
    set_env(runner, "ACR_PUBLIC", "false")
    if not full_provision and fast_path_ready(env):
        apply_window(subscription_runner(runner, env), env, None, False)
    else:
        provision(runner)
    set_env(runner, WINDOW_STATE, CLOSED)


@dataclass(frozen=True)
class WindowStatus:
    open: bool
    allowed_ip: str | None
    current_ip: str | None
    acr_public: bool
    warning: str | None
    closing: bool = False


def window_status(runner: Runner, current_ip: str | None) -> WindowStatus:
    env = get_env(runner)
    if env.get(WINDOW_STATE) == CLOSING:
        return WindowStatus(True, None, current_ip, env.get("ACR_PUBLIC") == "true",
                            "이전 `window close`의 프로비저닝이 끝나지 않아 Azure에 운영자 IP 규칙이 남아 있을 수 있습니다. "
                            "`python -m ops window close`를 다시 실행하세요.", closing=True)
    allowed = env.get("OPERATOR_IP", CLOSED)
    is_open = allowed not in ("", CLOSED)
    warning = None
    if is_open and current_ip and current_ip != allowed:
        warning = (f"현재 공인 IP {current_ip}가 허용된 IP {allowed}와 다릅니다. "
                   "`python -m ops window open`으로 다시 여세요.")
    return WindowStatus(is_open, allowed if is_open else None, current_ip,
                        env.get("ACR_PUBLIC") == "true", warning)
