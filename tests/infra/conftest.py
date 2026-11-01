"""IaC 오프라인 테스트 fixture.

PSRule for Azure로 Bicep을 펼친 리소스 목록을 제공합니다. 파라미터 파일당 수십 초가
걸리므로 RUN_INFRA_TESTS=1 일 때만 실행합니다.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INFRA = ROOT / "infra"
EXPAND = INFRA / "tests" / "expand.ps1"


@pytest.fixture(autouse=True)
def _require_opt_in():
    if os.environ.get("RUN_INFRA_TESTS") != "1":
        pytest.skip("RUN_INFRA_TESTS=1 일 때만 실행합니다")


def _expand(param_file: Path, extra_env: dict[str, str] | None = None,
            template: Path = INFRA / "main.bicep") -> list[dict]:
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.fail("PowerShell 7(pwsh)이 필요합니다")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "expanded.json"
        proc = subprocess.run(
            [pwsh, "-NoProfile", "-NonInteractive", "-File", str(EXPAND),
             "-TemplateFile", str(template),
             "-ParamFile", str(param_file), "-OutFile", str(out)],
            capture_output=True, text=True, encoding="utf-8",
            env={**os.environ, **(extra_env or {})},
        )
        if proc.returncode != 0:
            pytest.fail(f"Bicep 펼치기 실패: {param_file.name}\n{proc.stdout}\n{proc.stderr}")
        return json.loads(out.read_text(encoding="utf-8-sig"))


def _flatten(resources: list[dict]) -> list[dict]:
    """부모의 `resources` 안에 중첩된 자식 리소스까지 한 목록으로 펼칩니다."""
    flat: list[dict] = []
    queue = list(resources)
    while queue:
        resource = queue.pop(0)
        flat.append(resource)
        queue.extend(resource.get("resources") or [])
    return flat


def _last(resource_id: str) -> str:
    return resource_id.rstrip("/").rsplit("/", 1)[-1]


class Expanded:
    """펼쳐진 리소스 목록과 조회 헬퍼."""

    def __init__(self, resources: list[dict]):
        self.raw = _flatten(resources)
        self.all = [r for r in self.raw if r.get("type") != "Microsoft.Resources/deployments"]

    def of_type(self, type_: str) -> list[dict]:
        return [r for r in self.all if r["type"].lower() == type_.lower()]

    def one(self, type_: str, name_prefix: str = "") -> dict:
        found = [r for r in self.of_type(type_) if r["name"].split("/")[-1].startswith(name_prefix)]
        assert len(found) == 1, f"{type_} '{name_prefix}': {[r['name'] for r in found]}"
        return found[0]

    def role_assignments_on(self, scope_prefix: str) -> list[dict]:
        return [
            r for r in self.of_type("Microsoft.Authorization/roleAssignments")
            if _last(r.get("scope", "")).startswith(scope_prefix)
        ]

    def roles_on(self, scope_prefix: str) -> set[str]:
        return {_last(r["properties"]["roleDefinitionId"]) for r in self.role_assignments_on(scope_prefix)}

    def private_endpoints(self, target_prefix: str) -> dict[str, set[str]]:
        groups = self.of_type("Microsoft.Network/privateEndpoints/privateDnsZoneGroups")
        result: dict[str, set[str]] = {}
        for pe in self.of_type("Microsoft.Network/privateEndpoints"):
            conn = pe["properties"]["privateLinkServiceConnections"][0]["properties"]
            if not _last(conn["privateLinkServiceId"]).startswith(target_prefix):
                continue
            zones = {
                _last(cfg["properties"]["privateDnsZoneId"])
                for group in groups if group["name"].split("/")[0] == pe["name"]
                for cfg in group["properties"]["privateDnsZoneConfigs"]
            }
            result[conn["groupIds"][0]] = zones
        return result

    def root_parameters(self) -> dict:
        root = self.raw[0]
        return {k: v["value"] for k, v in root["properties"]["parameters"].items()}


_CACHE: dict[str, Expanded] = {}


def _cached(name: str) -> Expanded:
    if name not in _CACHE:
        _CACHE[name] = Expanded(_expand(INFRA / "tests" / f"{name}.bicepparam"))
    return _CACHE[name]


@pytest.fixture(scope="session")
def closed() -> Expanded:
    return _cached("closed")


@pytest.fixture(scope="session")
def opened() -> Expanded:
    return _cached("open")


@pytest.fixture(scope="session")
def variant() -> Expanded:
    return _cached("variant")


@pytest.fixture(scope="session")
def console_app() -> Expanded:
    """콘솔용 container-app 모듈만 펼친 결과(Graph appId를 아는 상태의 authConfigs 확인용)."""
    return Expanded(_expand(INFRA / "tests" / "console-app.bicepparam",
                            template=INFRA / "modules" / "container-app.bicep"))


@pytest.fixture(scope="session")
def expand_azd():
    """azd용 infra/main.bicepparam을 주어진 환경 변수로 펼칩니다."""
    def run(env: dict[str, str]) -> Expanded:
        return Expanded(_expand(INFRA / "main.bicepparam", env))
    return run
