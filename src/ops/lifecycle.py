"""비용 수명 주기: Service Bus pause/resume과 전체 teardown(스펙 §6.2)."""
from __future__ import annotations

import json
import time
from typing import Callable

from ops.azcli import OpsError, Runner, is_not_found
from ops.azdenv import get_env, provision, set_env
from ops.window import ensure_caphost_skip

CAPHOST_API_VERSION = "2025-04-01-preview"


def _ignore_not_found(action: Callable[[], object]) -> None:
    try:
        action()
    except OpsError as err:
        if not is_not_found(err):
            raise


def pause(runner: Runner) -> bool:
    """Service Bus Premium(월 약 $677)을 삭제합니다. 삭제했으면 True, 이미 없으면 False."""
    env = get_env(runner)
    group, namespace = env["AZURE_RESOURCE_GROUP"], env["SERVICEBUS_NAME"]
    ensure_caphost_skip(runner, env)
    set_env(runner, "SERVICEBUS_ENABLED", "false")
    existing = json.loads(runner(["az", "servicebus", "namespace", "list", "-g", group,
                                  "--query", "[].name", "-o", "json"]) or "[]")
    deleted = namespace in existing
    if deleted:
        _ignore_not_found(lambda: runner(["az", "network", "private-endpoint", "delete", "-g", group,
                                          "-n", f"pe-{namespace}-namespace"]))
        runner(["az", "servicebus", "namespace", "delete", "-g", group, "-n", namespace], stream=True)
    provision(runner)  # SERVICEBUS_FQDN, smoke 설정 등 출력을 pause 상태로 갱신
    return deleted


def resume(runner: Runner) -> None:
    ensure_caphost_skip(runner, get_env(runner))
    set_env(runner, "SERVICEBUS_ENABLED", "true")
    provision(runner)


def _delete_and_wait(runner: Runner, url: str, sleep: Callable[[float], None], poll_wait: float,
                     poll_limit: int) -> None:
    try:
        runner(["az", "rest", "--method", "delete", "--url", url])
    except OpsError as err:
        if is_not_found(err):
            return
        raise
    for _ in range(poll_limit):
        try:
            runner(["az", "rest", "--method", "get", "--url", url])
        except OpsError as err:
            if is_not_found(err):
                return
            raise
        sleep(poll_wait)
    raise OpsError(f"삭제 대기 시간 초과: {url}")


def teardown(runner: Runner, confirm: str, *, sleep: Callable[[float], None] = time.sleep,
             group_retries: int = 4, retry_wait: float = 300, poll_wait: float = 15,
             poll_limit: int = 120) -> None:
    """capability host → Foundry 계정 삭제·purge → 리소스 그룹 삭제(재시도) → Key Vault purge.

    capability host를 먼저 지우지 않으면 에이전트 서브넷이 약 20분 점유되어 RG 삭제가 실패합니다.
    """
    env = get_env(runner)
    group, location = env["AZURE_RESOURCE_GROUP"], env["AZURE_LOCATION"]
    if confirm != group:
        raise OpsError(f"--confirm 값이 리소스 그룹 이름({group})과 다릅니다")
    subscription = runner(["az", "account", "show", "--query", "id", "-o", "tsv"]).strip()
    account = env.get("AI_ACCOUNT_NAME")
    if account:
        base = (f"https://management.azure.com/subscriptions/{subscription}/resourceGroups/{group}"
                f"/providers/Microsoft.CognitiveServices/accounts/{account}")
        project, caphost = env.get("AI_PROJECT_NAME"), env.get("FOUNDRY_PROJECT_CAPHOST")
        if project and caphost:
            _delete_and_wait(runner, f"{base}/projects/{project}/capabilityHosts/{caphost}"
                                     f"?api-version={CAPHOST_API_VERSION}", sleep, poll_wait, poll_limit)
        _delete_and_wait(runner, f"{base}/capabilityHosts/{account}@aml_aiagentservice"
                                 f"?api-version={CAPHOST_API_VERSION}", sleep, poll_wait, poll_limit)
        _ignore_not_found(lambda: runner(["az", "cognitiveservices", "account", "delete", "-g", group,
                                          "-n", account]))
        _ignore_not_found(lambda: runner(["az", "cognitiveservices", "account", "purge", "-g", group,
                                          "-n", account, "-l", location]))
    for attempt in range(1, group_retries + 1):
        try:
            runner(["az", "group", "delete", "-n", group, "--yes"])
            break
        except OpsError as err:
            if is_not_found(err):
                break
            if attempt == group_retries:
                raise
            sleep(retry_wait)
    keyvault = env.get("KEYVAULT_NAME")
    if keyvault:
        _ignore_not_found(lambda: runner(["az", "keyvault", "purge", "-n", keyvault, "-l", location]))
    set_env(runner, "DEPLOY_CAPHOST", "true")  # 다음 새 배포에서는 caphost를 다시 만듭니다.
