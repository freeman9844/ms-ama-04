"""앱 배포: ACR Build 이미지 → azd 환경 기록 → 이미지 교체(필요할 때만 프로비저닝), Function 코드 zip 배포."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ops.azcli import OpsError, Runner, subscription_runner
from ops.azdenv import get_env, provision, set_env
from ops.window import CLOSED, validate_operator_ip

ROOT = Path(__file__).resolve().parents[2]
TARGETS = ("policy", "mockapi", "console", "eval", "orchestrator", "all")
# pip는 manylinux_2_28만 주면 manylinux_2_17/2014 wheel(pydantic-core 등)을 고르지 않습니다.
PIP_PLATFORMS = ("manylinux_2_28_x86_64", "manylinux_2_17_x86_64", "manylinux2014_x86_64")
PYTHON_VERSION = "3.12"
SITE_PACKAGES = Path(".python_packages") / "lib" / "site-packages"
_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "local.settings.json", ".venv")
CACHE_ENV = "AMALEND_OPS_CACHE"
CACHE_DONE = ".complete"
# 회사망·모바일 회선은 App Service가 보는 출구 IP가 api.ipify와 다릅니다. SCM 403 응답 헤더로 실제 IP를 알 수 있습니다.
FORBIDDEN_IP_HEADER = "x-ms-forbidden-ip"
EGRESS_RULE = "operator-egress"
EGRESS_PRIORITY = 110
EGRESS_WAIT_TRIES = 12
EGRESS_WAIT_S = 5.0


@dataclass(frozen=True)
class ContainerService:
    name: str
    dockerfile: str
    env_key: str
    # (종류 app|job, 이름이 든 azd 환경 키). 이미지만 바꿀 때 갱신할 대상입니다.
    targets: tuple[tuple[str, str], ...] = ()


CONTAINERS = (
    ContainerService("policy", "src/policy/Dockerfile", "SERVICE_POLICY_IMAGE_NAME", (("app", "POLICY_APP_NAME"),)),
    ContainerService("mockapi", "src/mockapis/Dockerfile", "SERVICE_MOCKAPI_IMAGE_NAME",
                     (("app", "MOCKAPI_APP_NAME"),)),
    ContainerService("console", "src/console/Dockerfile", "SERVICE_CONSOLE_IMAGE_NAME",
                     (("app", "CONSOLE_APP_NAME"),)),
    ContainerService("eval", "src/evals/Dockerfile", "SERVICE_EVAL_IMAGE_NAME",
                     (("job", "EVAL_JOB_NAME"), ("job", "OPS_JOB_NAME"))),
)


def default_tag() -> str:
    return time.strftime("%Y%m%d%H%M%S", time.gmtime())


def _az_kind(kind: str) -> list[str]:
    return ["az", "containerapp", "job"] if kind == "job" else ["az", "containerapp"]


def _current_image(az: Runner, env: dict[str, str], kind: str, name: str) -> str:
    shown = json.loads(az([*_az_kind(kind), "show", "-g", env["AZURE_RESOURCE_GROUP"], "-n", name, "-o", "json"])
                       or "{}")
    containers = shown.get("properties", {}).get("template", {}).get("containers") or [{}]
    return containers[0].get("image", "")


def _can_swap_image(az: Runner, env: dict[str, str], services: list[ContainerService]) -> bool:
    """모든 대상이 이미 ACR 이미지로 떠 있으면(레지스트리 설정 있음) 이미지만 바꿔도 Bicep 선언과 같아집니다."""
    registry = env["AZURE_CONTAINER_REGISTRY_ENDPOINT"] + "/"
    for service in services:
        if not service.targets:
            return False
        for kind, key in service.targets:
            if not env.get(key) or not _current_image(az, env, kind, env[key]).startswith(registry):
                return False
    return True


def deploy_images(runner: Runner, services: list[ContainerService], root: Path, tag: str, *,
                  full_provision: bool = False) -> list[str]:
    env = get_env(runner)
    if env.get("ACR_PUBLIC") != "true":
        raise OpsError("ACR 빌드 창이 닫혀 있습니다. `python -m ops window open --acr`를 먼저 실행하세요.")
    az = subscription_runner(runner, env)
    swap = not full_provision and _can_swap_image(az, env, services)
    images = []
    for service in services:
        repository = f"{service.name}:{tag}"
        az(["az", "acr", "build", "-r", env["ACR_NAME"], "-t", repository, "-f", service.dockerfile,
            "--platform", "linux/amd64", str(root)], stream=True)
        image = f"{env['AZURE_CONTAINER_REGISTRY_ENDPOINT']}/{repository}"
        # 다음 프로비저닝도 같은 이미지를 쓰도록 먼저 기록합니다.
        set_env(runner, service.env_key, image)
        if swap:
            for kind, key in service.targets:
                az([*_az_kind(kind), "update", "-g", env["AZURE_RESOURCE_GROUP"], "-n", env[key],
                    "--image", image, "-o", "none"])
        images.append(image)
    if not swap:
        provision(runner)  # 자리표시 이미지면 command·레지스트리 설정까지 Bicep이 정리합니다.
    return images


def _cache_root() -> Path:
    configured = os.environ.get(CACHE_ENV)
    return Path(configured) if configured else Path.home() / ".cache" / "amalend" / "site-packages"


def _requirements_key(requirements: Path) -> str:
    digest = hashlib.sha256(requirements.read_bytes())
    digest.update("|".join((*PIP_PLATFORMS, PYTHON_VERSION)).encode())
    return digest.hexdigest()[:16]


def _site_packages(runner: Runner, requirements: Path) -> Path:
    """requirements.txt가 같으면 이전에 받은 Linux wheel을 다시 씁니다. 완료 표시가 있는 캐시만 믿습니다."""
    cached = _cache_root() / _requirements_key(requirements)
    if (cached / CACHE_DONE).is_file():
        return cached
    partial = cached.with_name(cached.name + ".tmp")
    shutil.rmtree(partial, ignore_errors=True)
    shutil.rmtree(cached, ignore_errors=True)
    partial.mkdir(parents=True)
    platforms = [arg for platform in PIP_PLATFORMS for arg in ("--platform", platform)]
    runner([sys.executable, "-m", "pip", "install", "-r", str(requirements),
            "--target", str(partial), *platforms, "--python-version", PYTHON_VERSION,
            "--implementation", "cp", "--only-binary=:all:", "--quiet"], stream=True)
    (partial / CACHE_DONE).write_text("ok", encoding="ascii")
    partial.rename(cached)
    return cached


def package_function(runner: Runner, root: Path, work: Path) -> Path:
    stage = work / "app"
    shutil.copytree(root / "src" / "orchestrator", stage, ignore=_IGNORE)
    for package in ("agents", "shared"):
        shutil.copytree(root / "src" / package, stage / package, ignore=_IGNORE)
    packages = _site_packages(runner, stage / "requirements.txt")
    shutil.copytree(packages, stage / SITE_PACKAGES, ignore=shutil.ignore_patterns(CACHE_DONE))
    return Path(shutil.make_archive(str(work / "orchestrator"), "zip", stage))


def scm_forbidden_ip(app_name: str) -> str | None:
    """SCM 사이트가 이 PC를 403으로 막으면 App Service가 본 출구 IP를 돌려줍니다. 막히지 않았거나 확인 못 하면 None."""
    request = urllib.request.Request(f"https://{app_name}.scm.azurewebsites.net/", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=15):
            return None
    except urllib.error.HTTPError as err:
        return err.headers.get(FORBIDDEN_IP_HEADER) if err.code == 403 else None
    except OSError:
        return None


def _allow_egress_ip(az: Runner, env: dict[str, str], name: str, blocked: str,
                     probe: Callable[[str], str | None], sleep: Callable[[float], None]) -> None:
    ip = validate_operator_ip(blocked)
    az(["az", "functionapp", "config", "access-restriction", "add", "-g", env["AZURE_RESOURCE_GROUP"], "-n", name,
        "--rule-name", EGRESS_RULE, "--action", "Allow", "--ip-address", f"{ip}/32",
        "--priority", str(EGRESS_PRIORITY), "-o", "none"])
    for _ in range(EGRESS_WAIT_TRIES):
        if probe(name) is None:
            return
        sleep(EGRESS_WAIT_S)
    raise OpsError(f"출구 IP {ip}를 허용했지만 SCM이 계속 403입니다.")


def _remove_egress_rule(az: Runner, env: dict[str, str], name: str) -> None:
    az(["az", "functionapp", "config", "access-restriction", "remove", "-g", env["AZURE_RESOURCE_GROUP"],
        "-n", name, "--rule-name", EGRESS_RULE, "-o", "none"])


def deploy_function(runner: Runner, root: Path, *, probe: Callable[[str], str | None] | None = None,
                    sleep: Callable[[float], None] = time.sleep) -> str:
    env = get_env(runner)
    if env.get("OPERATOR_IP", CLOSED) in ("", CLOSED):
        raise OpsError("배포 창이 닫혀 있습니다. `python -m ops window open`을 먼저 실행하세요.")
    probe = probe or scm_forbidden_ip
    az = subscription_runner(runner, env)
    name = env["FUNCTION_APP_NAME"]
    with tempfile.TemporaryDirectory() as tmp:
        archive = package_function(runner, root, Path(tmp))
        blocked = probe(name)
        try:
            if blocked:
                print(f"SCM이 출구 IP {blocked}를 막아 임시 규칙 {EGRESS_RULE}을 넣습니다(배포 뒤 삭제).",
                      file=sys.stderr)
                _allow_egress_ip(az, env, name, blocked, probe, sleep)
            az(["az", "functionapp", "deployment", "source", "config-zip", "-g", env["AZURE_RESOURCE_GROUP"],
                "-n", name, "--src", str(archive), "--build-remote", "false"], stream=True)
        finally:
            if blocked:
                _remove_egress_rule(az, env, name)
    return name


def deploy(runner: Runner, target: str, *, root: Path | None = None, tag: str | None = None,
           full_provision: bool = False) -> list[str]:
    """target: policy | mockapi | console | eval | orchestrator | all. 배포한 이미지·앱 이름 목록을 돌려줍니다."""
    if target not in TARGETS:
        raise OpsError(f"알 수 없는 배포 대상: {target}")
    root = root or ROOT
    deployed: list[str] = []
    services = [s for s in CONTAINERS if target in (s.name, "all")]
    if services:
        deployed += deploy_images(runner, services, root, tag or default_tag(), full_provision=full_provision)
    if target in ("orchestrator", "all"):
        deployed.append(deploy_function(runner, root))
    return deployed
