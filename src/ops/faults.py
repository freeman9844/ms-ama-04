"""장애 주입: FAULT_MODE 환경 변수로 리비전을 갱신하고, 시나리오는 제출 뒤 finally로 원복한다(Spec §6)."""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ops import opsrun
from ops.azcli import OpsError, Runner, subscription_runner
from ops.azdenv import get_env

FAULT_VAR = "FAULT_MODE"
TARGETS = {"source": ("MOCKAPI_APP_NAME", "flaky"), "policy": ("POLICY_APP_NAME", "unavailable")}


@dataclass(frozen=True)
class Scenario:
    name: str
    target: str
    applicants: tuple[str, ...]
    interval_s: float


SCENARIOS = {
    "source-flaky": Scenario("source-flaky", "source", ("demo-cafe-001", "demo-mismatch-002"), 5),
    # 첫 신청이 Durable 재시도(5초 간격 3회)로 서킷을 연 뒤 다음 신청이 Policy 단계에 닿도록 간격을 둔다.
    "policy-down": Scenario("policy-down", "policy", ("demo-cafe-001", "demo-stress-003", "demo-thin-004"), 90),
}


def _props(az: Runner, group: str, app: str) -> dict[str, Any]:
    return json.loads(az(["az", "containerapp", "show", "-g", group, "-n", app, "--query", "properties", "-o", "json"]))


def _mode(props: dict[str, Any]) -> str | None:
    env = props["template"]["containers"][0].get("env") or []
    return next((e.get("value") for e in env if e.get("name") == FAULT_VAR), None)


def _set(az: Runner, group: str, app: str, mode: str | None, *, sleep: Callable[[float], None],
         poll_wait: float, poll_limit: int) -> None:
    if _mode(_props(az, group, app)) == mode:
        return
    change = ["--set-env-vars", f"{FAULT_VAR}={mode}"] if mode else ["--remove-env-vars", FAULT_VAR]
    az(["az", "containerapp", "update", "-g", group, "-n", app, *change, "-o", "none"])
    # Probe P5: 새 리비전 Healthy·트래픽 전환까지 약 40초.
    for _ in range(poll_limit):
        props = _props(az, group, app)
        if props.get("latestRevisionName") == props.get("latestReadyRevisionName") and _mode(props) == mode:
            return
        sleep(poll_wait)
    raise OpsError(f"{app}: 새 리비전이 준비되지 않았습니다({FAULT_VAR}={mode or '없음'})")


def _context(runner: Runner) -> tuple[Runner, str, dict[str, str]]:
    env = get_env(runner)
    return subscription_runner(runner, env), env["AZURE_RESOURCE_GROUP"], env


def set_fault(runner: Runner, target: str, on: bool, *, sleep: Callable[[float], None] = time.sleep,
              poll_wait: float = 10, poll_limit: int = 30) -> None:
    az, group, env = _context(runner)
    key, mode = TARGETS[target]
    _set(az, group, env[key], mode if on else None, sleep=sleep, poll_wait=poll_wait, poll_limit=poll_limit)


def status(runner: Runner) -> dict[str, str]:
    az, group, env = _context(runner)
    return {target: _mode(_props(az, group, env[key])) or "off" for target, (key, _) in TARGETS.items()}


def run_scenario(runner: Runner, name: str, *, submit_fn: Callable[..., Any] = opsrun.submit,
                 sleep: Callable[[float], None] = time.sleep, poll_wait: float = 10, poll_limit: int = 30,
                 **job_kw: Any) -> Any:
    scenario = SCENARIOS[name]
    az, group, env = _context(runner)
    key, mode = TARGETS[scenario.target]
    app = env[key]
    toggle = {"sleep": sleep, "poll_wait": poll_wait, "poll_limit": poll_limit}
    failure: BaseException | None = None
    try:
        # 켜기 갱신이 적용된 뒤 준비 대기가 실패하거나 중단돼도 finally에서 원복한다(이미 꺼져 있으면 no-op).
        _set(az, group, app, mode, **toggle)
        result = submit_fn(runner, scenario.applicants, interval_s=scenario.interval_s, sleep=sleep, **job_kw)
    except BaseException as err:
        failure = err
        raise
    finally:
        try:
            _set(az, group, app, None, **toggle)
        except Exception as err:  # 조회 응답 해석 오류(JSON·키)도 원복 실패로 알린다
            raise OpsError(f"장애 원복 실패: {app}({FAULT_VAR}={mode}). "
                           f"수동 원복: python -m ops fault {scenario.target} off\n{err}") from (failure or err)
    opsrun.update_meta(result.out_dir, {"fault": {"scenario": name, "app": app, "mode": mode, "reverted": True}})
    return result
