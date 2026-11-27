"""콘솔 WAF 운영자 IP 규칙 켜고 끄기(ADR-011). 테스트 기간에는 IP 규칙 대신 데모 비밀번호로 진입을 막습니다.

끌 때는 규칙 상태만 az로 바꾸고(수 초), 켤 때는 허용 IP까지 Bicep으로 다시 맞춥니다(프로비저닝 1회).
관리 규칙(OWASP DRS 2.1, Prevention)은 어느 쪽이든 그대로입니다.
"""
from __future__ import annotations

import json
import secrets
import string
from dataclasses import dataclass

from ops.azcli import OpsError, Runner, subscription_runner
from ops.azdenv import get_env, provision, set_env
from ops.window import validate_operator_ip

RULE = "allowOperatorOnly"
FILTER_ENV = "WAF_IP_FILTER"
PASSWORD_ENV = "CONSOLE_DEMO_PASSWORD"
MIN_PASSWORD = 12
PASSWORD_LENGTH = 20
# azd env set 인자로 넘기므로 '-'로 시작하거나 따옴표가 필요한 문자는 쓰지 않습니다.
PASSWORD_ALPHABET = string.ascii_letters + string.digits


@dataclass(frozen=True)
class IpFilterStatus:
    state: str
    allowed: list[str]
    password_set: bool


def new_password() -> str:
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(PASSWORD_LENGTH))


def _require(env: dict[str, str], *keys: str) -> None:
    missing = [key for key in keys if not env.get(key)]
    if missing:
        raise OpsError(f"azd 환경에 {', '.join(missing)}가 없습니다. 콘솔 App Gateway를 먼저 프로비저닝하세요.")


def _policy_name(az: Runner, env: dict[str, str]) -> str:
    gateway = json.loads(az(["az", "network", "application-gateway", "show", "-g", env["AZURE_RESOURCE_GROUP"],
                             "-n", env["APPGW_NAME"], "-o", "json"]) or "{}")
    policy_id = (gateway.get("firewallPolicy") or {}).get("id", "")
    if not policy_id:
        raise OpsError(f"App Gateway {env['APPGW_NAME']}에 WAF 정책이 없습니다.")
    return policy_id.rstrip("/").split("/")[-1]


def _rule_args(env: dict[str, str], policy: str) -> list[str]:
    return ["-g", env["AZURE_RESOURCE_GROUP"], "--policy-name", policy, "-n", RULE]


def _console_has_password(az: Runner, env: dict[str, str]) -> bool:
    app = json.loads(az(["az", "containerapp", "show", "-g", env["AZURE_RESOURCE_GROUP"],
                         "-n", env["CONSOLE_APP_NAME"], "-o", "json"]) or "{}")
    containers = app.get("properties", {}).get("template", {}).get("containers") or [{}]
    return any(item.get("name") == PASSWORD_ENV for item in containers[0].get("env") or [])


def ip_filter_status(runner: Runner) -> IpFilterStatus:
    env = get_env(runner)
    _require(env, "AZURE_RESOURCE_GROUP", "APPGW_NAME")
    az = subscription_runner(runner, env)
    rule = json.loads(az(["az", "network", "application-gateway", "waf-policy", "custom-rule", "show",
                          *_rule_args(env, _policy_name(az, env)), "-o", "json"]) or "{}")
    allowed = [value for cond in rule.get("matchConditions") or [] for value in cond.get("matchValues") or []]
    return IpFilterStatus(rule.get("state", "?"), allowed, len(env.get(PASSWORD_ENV, "")) >= MIN_PASSWORD)


def ip_filter_off(runner: Runner) -> str:
    """IP 규칙을 끄고 콘솔 데모 비밀번호를 돌려줍니다. 비밀번호가 콘솔에 들어간 뒤에만 규칙을 끕니다."""
    env = get_env(runner)
    _require(env, "AZURE_RESOURCE_GROUP", "APPGW_NAME", "CONSOLE_APP_NAME")
    if env.get("CONSOLE_AUTH_ENABLED") != "false":
        raise OpsError("콘솔이 Entra 로그인 모드입니다. 데모 모드(CONSOLE_AUTH_ENABLED=false)에서만 IP 규칙을 끕니다.")
    az = subscription_runner(runner, env)
    password = env.get(PASSWORD_ENV, "")
    fresh = len(password) < MIN_PASSWORD
    if fresh:
        password = new_password()
        set_env(runner, PASSWORD_ENV, password)
    # 규칙이 켜진 채로 비밀번호를 먼저 배포합니다. 새 비밀번호면 컨테이너 비밀도 바뀌어야 하므로 항상 프로비저닝합니다.
    if fresh or not _console_has_password(az, env):
        provision(runner)
    az(["az", "network", "application-gateway", "waf-policy", "custom-rule", "update",
        *_rule_args(env, _policy_name(az, env)), "--state", "Disabled", "-o", "none"])
    set_env(runner, FILTER_ENV, "false")
    return password


def ip_filter_on(runner: Runner, ip: str) -> str:
    """허용 IP를 정하고 규칙을 다시 켭니다(프로비저닝 1회). 데모 비밀번호는 그대로 둡니다."""
    ip = validate_operator_ip(ip)
    set_env(runner, "CONSOLE_IP", ip)
    set_env(runner, FILTER_ENV, "true")
    provision(runner)
    return ip
