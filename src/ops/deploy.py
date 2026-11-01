"""앱 배포: ACR Build 이미지 → azd 환경 기록 → 프로비저닝, Function 코드 zip 배포."""
from __future__ import annotations

import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from ops.azcli import OpsError, Runner, subscription_runner
from ops.azdenv import get_env, provision, set_env
from ops.window import CLOSED

ROOT = Path(__file__).resolve().parents[2]
TARGETS = ("policy", "mockapi", "console", "orchestrator", "all")
# pip는 manylinux_2_28만 주면 manylinux_2_17/2014 wheel(pydantic-core 등)을 고르지 않습니다.
PIP_PLATFORMS = ("manylinux_2_28_x86_64", "manylinux_2_17_x86_64", "manylinux2014_x86_64")
SITE_PACKAGES = Path(".python_packages") / "lib" / "site-packages"
_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "local.settings.json", ".venv")


@dataclass(frozen=True)
class ContainerService:
    name: str
    dockerfile: str
    env_key: str


CONTAINERS = (
    ContainerService("policy", "src/policy/Dockerfile", "SERVICE_POLICY_IMAGE_NAME"),
    ContainerService("mockapi", "src/mockapis/Dockerfile", "SERVICE_MOCKAPI_IMAGE_NAME"),
    ContainerService("console", "src/console/Dockerfile", "SERVICE_CONSOLE_IMAGE_NAME"),
)


def default_tag() -> str:
    return time.strftime("%Y%m%d%H%M%S", time.gmtime())


def deploy_images(runner: Runner, services: list[ContainerService], root: Path, tag: str) -> list[str]:
    env = get_env(runner)
    if env.get("ACR_PUBLIC") != "true":
        raise OpsError("ACR 빌드 창이 닫혀 있습니다. `python -m ops window open --acr`를 먼저 실행하세요.")
    az = subscription_runner(runner, env)
    images = []
    for service in services:
        repository = f"{service.name}:{tag}"
        az(["az", "acr", "build", "-r", env["ACR_NAME"], "-t", repository, "-f", service.dockerfile,
            "--platform", "linux/amd64", str(root)], stream=True)
        image = f"{env['AZURE_CONTAINER_REGISTRY_ENDPOINT']}/{repository}"
        set_env(runner, service.env_key, image)
        images.append(image)
    provision(runner)  # Container App 이미지 갱신(자리표시 command·레지스트리 설정도 Bicep이 정리)
    return images


def package_function(runner: Runner, root: Path, work: Path) -> Path:
    stage = work / "app"
    shutil.copytree(root / "src" / "orchestrator", stage, ignore=_IGNORE)
    for package in ("agents", "shared"):
        shutil.copytree(root / "src" / package, stage / package, ignore=_IGNORE)
    platforms = [arg for platform in PIP_PLATFORMS for arg in ("--platform", platform)]
    runner([sys.executable, "-m", "pip", "install", "-r", str(stage / "requirements.txt"),
            "--target", str(stage / SITE_PACKAGES), *platforms, "--python-version", "3.12",
            "--implementation", "cp", "--only-binary=:all:", "--quiet"], stream=True)
    return Path(shutil.make_archive(str(work / "orchestrator"), "zip", stage))


def deploy_function(runner: Runner, root: Path) -> str:
    env = get_env(runner)
    if env.get("OPERATOR_IP", CLOSED) in ("", CLOSED):
        raise OpsError("배포 창이 닫혀 있습니다. `python -m ops window open`을 먼저 실행하세요.")
    az = subscription_runner(runner, env)
    name = env["FUNCTION_APP_NAME"]
    with tempfile.TemporaryDirectory() as tmp:
        archive = package_function(runner, root, Path(tmp))
        az(["az", "functionapp", "deployment", "source", "config-zip", "-g", env["AZURE_RESOURCE_GROUP"],
            "-n", name, "--src", str(archive), "--build-remote", "false"], stream=True)
    return name


def deploy(runner: Runner, target: str, *, root: Path | None = None, tag: str | None = None) -> list[str]:
    """target: policy | mockapi | console | orchestrator | all. 배포한 이미지·앱 이름 목록을 돌려줍니다."""
    if target not in TARGETS:
        raise OpsError(f"알 수 없는 배포 대상: {target}")
    root = root or ROOT
    deployed: list[str] = []
    services = [s for s in CONTAINERS if target in (s.name, "all")]
    if services:
        deployed += deploy_images(runner, services, root, tag or default_tag())
    if target in ("orchestrator", "all"):
        deployed.append(deploy_function(runner, root))
    return deployed
