# P2 Azure 인프라 구현 계획

> **For agentic workers:** 이 계획은 `superpowers:subagent-driven-development`(태스크별 구현자+리뷰어) 또는 `superpowers:executing-plans`(현 세션에서 순차 실행)로 실행한다. 각 태스크는 TDD 순서(실패 테스트 → 실패 확인 → 구현 → 통과 확인 → 커밋)를 지킨다. 체크박스는 진행에 따라 갱신한다.

**Goal:** Korea Central에 전체 비공개(Private Endpoint) 데모 환경을 Bicep + azd로 만든다. Foundry private standard agent setup, 데이터 저장소, Service Bus, ACR, Container Apps 환경, Flex Consumption Function App을 포함하고, 배포 창·pause·teardown·smoke 운영 도구까지 갖춘다.

**Architecture:** `infra/main.bicep`(구독 범위)이 리소스 그룹을 만들고 `infra/resources.bicep`(RG 범위)이 기능별 모듈(`infra/modules/*`)을 조립한다. 모든 PaaS는 평상시 public access를 끄고, `operatorIp` 파라미터가 있을 때만 그 IP만 허용한다(배포 창). Container Apps는 VNet 전용 internal 환경(`cae-int`: Policy Engine·Mock API·smoke Job)과 콘솔 전용 external 환경(`cae-web`) 두 개로 나눈다. 오프라인 테스트는 PSRule for Azure로 Bicep을 펼친 리소스 JSON을 pytest로 검증하고, 배포 후 확인은 VNet 안의 Container Apps Job(smoke)이 맡는다. 운영 도구는 Python 패키지 `src/ops`(`python -m ops ...`)로 만들고 `az`/`azd` 호출을 주입 가능하게 해 단위 테스트한다.

**Tech Stack:** Bicep 0.48, Azure Developer CLI 1.37, Azure CLI 2.91, PowerShell 7, PSRule.Rules.Azure 1.47.0, Python 3.12(pytest, 표준 라이브러리).

**Spec:** `docs/superpowers/specs/2026-10-31-agentic-cashflow-lending-design.md` (§1.2 결정 사항, §2.2 네트워크, §2.3 컴포넌트·도구 실행 방식, §4.2 ADR-002, §6.2 배포 경로, §6.4 IaC 테스트, §7 위험)

**범위 밖(후속 계획):** 앱 코드·컨테이너 이미지·Function 코드 배포, Cosmos 컨테이너, Service Bus 구독(P3). 콘솔 앱과 그 ingress·Entra 인증(P3/P4). Application Gateway(WAF)는 문서로만(스펙 §2.2). Azure Monitor Private Link Scope는 쓰지 않는다(App Insights 수집은 public 엔드포인트 + Entra 인증, 한계로 문서화).

## Global Constraints

- 리전 `koreacentral`. VNet 주소 공간 `192.168.0.0/16`(RFC1918 class C 대역).
- 모든 최상위 리소스 태그: `azd-env-name`, `project: ama-cashflow-lending`, `Control: Yes`(구독 정책 "Enforce Control=Yes tag"의 modify 효과와 what-if 드리프트 방지).
- 무키(keyless): Storage `allowSharedKeyAccess: false`, Cosmos·AI Services·Service Bus `disableLocalAuth: true`, Key Vault RBAC 모드, App Insights `DisableLocalAuth: true`.
- 평상시 public network access는 `Disabled`. 배포 창(`operatorIp` 비어 있지 않음)에서만 운영자 IP 하나만 허용하고 기본 거부를 유지한다.
- 항상 비공개: Foundry BYO Storage(`stagt*`), AI Search, Service Bus. ACR은 `acrPublic=true`(빌드 창)일 때만 전체 공개.
- 모델 배포 기본 SKU `GlobalStandard`(ADR-002). `chatModelSku`·`decisionModelSku` 파라미터로 `DataZoneStandard` 전환 가능.
- Function App 런타임 Python 3.12, Flex Consumption(FC1). Durable Functions는 P3에서 classic 1.x Python SDK + Azure Storage 백엔드를 쓴다(스펙 §7).
- 도구 버전 하한: Bicep 0.48, azd 1.37, az 2.91, PowerShell 7, PSRule.Rules.Azure 1.47.0.
- 문서·주석은 한국어, 식별자는 영어. 비밀 값을 커밋하지 않는다(`.azure/`는 이미 gitignore).
- Windows PowerShell 기준 명령. venv 파이썬은 `.\.venv\Scripts\python`.
- 커밋 메시지 끝에 두 trailer를 둔다: `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`, `Copilot-Session: 05f1ca1b-6073-417d-bfb1-6b546f82cb58`.

## Review Focus

| # | 위험 지점 | 기대 동작 | 고정 테스트 |
|---|-----------|-----------|-------------|
| 1 | 배포 창을 연 뒤 운영자 IP가 바뀜(재택·VPN) | `window status`가 경고하고 다시 열도록 안내 | T9 `test_status_warns_when_public_ip_changed` |
| 2 | Cosmos는 `ipRules`가 비면 public 허용 시 전체 개방 | 창이 열려도 Cosmos는 운영자 IP 정확히 1개 | T3에서 `tests/infra/test_window.py`에 Cosmos 행 추가(`test_open_window_allows_only_operator_ip`) |
| 3 | 첫 배포 뒤 재프로비저닝에서 capability host 재-PUT 실패 | 이미 배포된 환경은 caphost 모듈을 건너뜀 | T4 `test_capability_host_skipped_when_disabled`, T9 `test_open_window_skips_caphost_after_first_provision` |
| 4 | teardown 중 에이전트 서브넷이 약 20분 점유되어 RG 삭제 실패 | caphost→계정→purge 순서 후 RG 삭제를 대기·재시도 | T9 `test_teardown_order_and_group_retry` |
| 5 | 원천 데이터 삭제 증명(동의 만료)인데 blob soft delete가 켜져 있음 | 원천 데이터 Storage(`stdat*`)는 soft delete 끔 | T3 `test_data_storage_has_no_soft_delete` |

## 파일 구조

```
azure.yaml                                 azd 프로젝트(인프라만, 서비스는 P3)
infra/main.bicep                           구독 범위: RG + resources 모듈 + azd 출력
infra/main.bicepparam                      azd 환경 변수 → 파라미터
infra/resources.bicep                      RG 범위 조립: 이름·역할 ID·모듈 호출·출력
infra/modules/network.bicep                VNet + 서브넷(위임)
infra/modules/dns.bicep                    Private DNS zone + VNet 링크
infra/modules/pe.bicep                     Private Endpoint + DNS zone group(공통)
infra/modules/monitoring.bicep             Log Analytics + App Insights
infra/modules/identities.bicep             사용자 할당 관리 ID 3개
infra/modules/keyvault.bicep               Key Vault + PE + 역할
infra/modules/storage.bicep                Storage(공통) + PE + 역할
infra/modules/cosmos.bicep                 Cosmos DB(serverless) + DB + PE + SQL 역할
infra/modules/search.bicep                 AI Search Basic + PE
infra/modules/foundry/account.bicep        AI Services 계정(network injection) + 모델 배포 + PE + 역할
infra/modules/foundry/project.bicep        프로젝트 + BYO 연결 3개
infra/modules/foundry/roles-pre.bicep      caphost 이전 역할(Cosmos Operator, Blob Contributor, Search)
infra/modules/foundry/caphost.bicep        프로젝트 capability host
infra/modules/foundry/roles-post.bicep     caphost 이후 역할(Blob Owner ABAC, Cosmos SQL)
infra/modules/servicebus.bicep             Service Bus Premium + 토픽 + PE + 역할
infra/modules/acr.bicep                    ACR Premium + PE + 역할
infra/modules/aca-env.bicep                Container Apps 환경(VNet 통합, internal/external 공통)
infra/modules/aca-dns.bicep                internal 환경 기본 도메인 Private DNS zone(와일드카드)
infra/modules/functions.bicep              Flex Consumption Function App + PE
infra/modules/smoke-job.bicep              VNet 안 smoke Container Apps Job
infra/tests/expand.ps1                     PSRule로 Bicep 펼치기 → JSON
infra/tests/closed.bicepparam              테스트 파라미터: 창 닫힘(기본)
infra/tests/open.bicepparam                테스트 파라미터: 창 열림
infra/tests/variant.bicepparam             테스트 파라미터: pause + ACR 빌드 창 + caphost 생략 + DataZone
src/ops/__init__.py
src/ops/smoke_checks.py                    smoke 점검(표준 라이브러리 전용, Job에서 실행)
src/ops/azcli.py                           명령 실행기(Runner)와 OpsError
src/ops/azdenv.py                          azd env get-values 파싱
src/ops/window.py                          배포 창 open/close/status
src/ops/lifecycle.py                       pause/resume/teardown
src/ops/smoke_runner.py                    smoke Job 실행·대기
src/ops/cli.py                             argparse 진입점
src/ops/__main__.py
tests/infra/conftest.py                    펼치기 fixture + Expanded 헬퍼
tests/infra/test_network.py
tests/infra/test_window.py                 배포 창 불변식(태스크마다 행 추가)
tests/infra/test_platform.py
tests/infra/test_data.py
tests/infra/test_foundry.py
tests/infra/test_messaging.py
tests/infra/test_compute.py
tests/infra/test_functions.py
tests/infra/test_smoke_job.py
tests/ops/conftest.py                      FakeRunner
tests/ops/test_smoke_checks.py
tests/ops/test_window.py
tests/ops/test_lifecycle.py
tests/ops/test_smoke_runner.py
tests/ops/test_cli.py
README.md                                  (수정) Azure 인프라 절
pyproject.toml                             (수정) ops 패키지 등록
```

**인프라 테스트 실행 방법(모든 태스크 공통):** 펼치기가 파라미터 파일당 수십 초 걸리므로 인프라 테스트는 opt-in이다.

```powershell
$env:RUN_INFRA_TESTS = '1'; .\.venv\Scripts\python -m pytest tests/infra -q
```

`RUN_INFRA_TESTS`가 없으면 `tests/infra`는 skip되고, 기존 `.\.venv\Scripts\python -m pytest -q`는 빠르게 유지된다.

---

### Task 1: azd 골격, IaC 테스트 하네스, 네트워크와 Private DNS

**Files:**
- Create: `azure.yaml`, `infra/main.bicep`, `infra/main.bicepparam`, `infra/resources.bicep`
- Create: `infra/modules/network.bicep`, `infra/modules/dns.bicep`
- Create: `infra/tests/expand.ps1`, `infra/tests/closed.bicepparam`, `infra/tests/open.bicepparam`, `infra/tests/variant.bicepparam`
- Test: `tests/infra/conftest.py`, `tests/infra/test_network.py`

**Interfaces:**
- Consumes: 없음(첫 태스크).
- Produces:
  - `main.bicep` 파라미터(이후 변경 없음): `environmentName`(3~12자), `location`, `operatorIp`, `acrPublic`, `operatorPrincipalId`, `serviceBusEnabled`, `deployCapabilityHost`, `chatModelSku`, `decisionModelSku`.
  - `resources.bicep` 변수: `names`(모든 리소스 이름), `role`(역할 정의 GUID), `zoneId`(zone 이름 → 리소스 ID), `subnetId`(서브넷 이름 → 리소스 ID), `blobZone`·`queueZone`·`tableZone`·`storageSuffix`, `sp`(`'ServicePrincipal'`), `hasOperator`.
  - pytest fixture: `closed`, `opened`, `variant`(각각 `Expanded`), `expand_azd(env: dict) -> Expanded`.
  - `Expanded` 메서드: `of_type(type) -> list[dict]`, `one(type, name_prefix='') -> dict`, `roles_on(scope_prefix) -> set[str]`(역할 GUID 집합), `role_assignments_on(scope_prefix) -> list[dict]`, `private_endpoints(target_prefix) -> dict[str, set[str]]`(groupId → zone 이름), `root_parameters() -> dict`.
  - azd 출력: `AZURE_RESOURCE_GROUP`, `AZURE_LOCATION`, `VNET_NAME`.

- [ ] **Step 1: 도구 준비 확인**

```powershell
$env:Path = [Environment]::GetEnvironmentVariable("Path","User") + ";" + [Environment]::GetEnvironmentVariable("Path","Machine")
pwsh -NoProfile -Command "Get-Module -ListAvailable PSRule.Rules.Azure | Select-Object -First 1 Name,Version"
Test-Path "$HOME\.azure\bin\bicep.exe"
```

Expected: `PSRule.Rules.Azure 1.47.0`, `True`. 모듈이 없으면 `pwsh -NoProfile -Command "Install-Module PSRule.Rules.Azure -Scope CurrentUser -Force"`, bicep이 없으면 `az bicep install`.

- [ ] **Step 2: 펼치기 스크립트와 테스트 하네스 작성**

`infra/tests/expand.ps1`:

```powershell
<#
.SYNOPSIS
  Bicep 템플릿을 .bicepparam 값으로 펼쳐(PSRule for Azure) 리소스 JSON 배열로 저장합니다.
  tests/infra 의 pytest fixture가 호출합니다.
#>
param(
    [Parameter(Mandatory)][string]$TemplateFile,
    [Parameter(Mandatory)][string]$ParamFile,
    [Parameter(Mandatory)][string]$OutFile
)
$ErrorActionPreference = 'Stop'
Import-Module PSRule.Rules.Azure -ErrorAction Stop
$bicep = if ($env:BICEP_PATH) { $env:BICEP_PATH } else { Join-Path $HOME '.azure/bin/bicep.exe' }
$work = Join-Path ([IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString())
New-Item -ItemType Directory -Path $work | Out-Null
try {
    $template = Join-Path $work 'main.json'
    $parameters = Join-Path $work 'parameters.json'
    & $bicep build $TemplateFile --outfile $template
    if ($LASTEXITCODE -ne 0) { throw "bicep build 실패: $TemplateFile" }
    & $bicep build-params $ParamFile --outfile $parameters
    if ($LASTEXITCODE -ne 0) { throw "bicep build-params 실패: $ParamFile" }
    $resources = Export-AzRuleTemplateData -TemplateFile $template -ParameterFile $parameters -PassThru
    $resources | ConvertTo-Json -Depth 100 -AsArray | Set-Content -Path $OutFile -Encoding utf8
}
finally {
    Remove-Item -Recurse -Force $work
}
```

`tests/infra/conftest.py`:

```python
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


def _expand(param_file: Path, extra_env: dict[str, str] | None = None) -> list[dict]:
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.fail("PowerShell 7(pwsh)이 필요합니다")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "expanded.json"
        proc = subprocess.run(
            [pwsh, "-NoProfile", "-NonInteractive", "-File", str(EXPAND),
             "-TemplateFile", str(INFRA / "main.bicep"),
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
def expand_azd():
    """azd용 infra/main.bicepparam을 주어진 환경 변수로 펼칩니다."""
    def run(env: dict[str, str]) -> Expanded:
        return Expanded(_expand(INFRA / "main.bicepparam", env))
    return run
```

- [ ] **Step 3: 실패하는 네트워크 테스트 작성**

`tests/infra/test_network.py`:

```python
STORAGE_SUFFIX = "core.windows.net"
EXPECTED_ZONES = {
    "privatelink.services.ai.azure.com",
    "privatelink.openai.azure.com",
    "privatelink.cognitiveservices.azure.com",
    "privatelink.search.windows.net",
    f"privatelink.blob.{STORAGE_SUFFIX}",
    f"privatelink.queue.{STORAGE_SUFFIX}",
    f"privatelink.table.{STORAGE_SUFFIX}",
    "privatelink.documents.azure.com",
    "privatelink.vaultcore.azure.net",
    "privatelink.servicebus.windows.net",
    "privatelink.azurecr.io",
    "privatelink.azurewebsites.net",
}
# 태그를 받지 않는 확장 리소스 유형
UNTAGGED = {"Microsoft.Authorization/roleAssignments"}


def _subnets(exp):
    vnet = exp.one("Microsoft.Network/virtualNetworks")
    return {s["name"]: s["properties"] for s in vnet["properties"]["subnets"]}


def test_vnet_uses_class_c_private_range(closed):
    vnet = closed.one("Microsoft.Network/virtualNetworks")
    assert vnet["properties"]["addressSpace"]["addressPrefixes"] == ["192.168.0.0/16"]


def test_subnets_and_delegations(closed):
    subnets = _subnets(closed)
    assert set(subnets) == {"snet-agents", "snet-pe", "snet-functions", "snet-apps-int", "snet-apps-web"}
    for name in ("snet-agents", "snet-functions", "snet-apps-int", "snet-apps-web"):
        services = [d["properties"]["serviceName"] for d in subnets[name]["delegations"]]
        assert services == ["Microsoft.App/environments"], name
    assert subnets["snet-pe"]["delegations"] == []
    assert subnets["snet-agents"]["addressPrefix"] == "192.168.0.0/24"
    assert subnets["snet-functions"]["addressPrefix"] == "192.168.2.0/26"
    assert subnets["snet-apps-int"]["addressPrefix"] == "192.168.4.0/24"
    assert subnets["snet-apps-web"]["addressPrefix"] == "192.168.5.0/24"


def test_all_private_dns_zones_exist_and_are_linked(closed):
    # internal Container Apps 환경의 기본 도메인 zone(T6)은 privatelink.* 가 아니므로 제외합니다.
    zones = {z["name"] for z in closed.of_type("Microsoft.Network/privateDnsZones")
             if z["name"].startswith("privatelink.")}
    assert zones == EXPECTED_ZONES
    links = closed.of_type("Microsoft.Network/privateDnsZones/virtualNetworkLinks")
    assert {link["name"].split("/")[0] for link in links} >= EXPECTED_ZONES
    assert all(link["properties"]["registrationEnabled"] is False for link in links)


def test_every_top_level_resource_has_control_tag(closed):
    checked = 0
    for resource in closed.all:
        if resource["type"].count("/") != 1 or resource["type"] in UNTAGGED:
            continue
        assert (resource.get("tags") or {}).get("Control") == "Yes", resource["name"]
        checked += 1
    assert checked > 0


def test_azd_params_map_closed_sentinel_to_empty_ip(expand_azd):
    params = expand_azd({"AZURE_ENV_NAME": "amatest", "OPERATOR_IP": "closed"}).root_parameters()
    assert params["operatorIp"] == ""
    assert params["serviceBusEnabled"] is True
    assert params["chatModelSku"] == "GlobalStandard"


def test_azd_params_pass_operator_ip_and_flags(expand_azd):
    params = expand_azd({
        "AZURE_ENV_NAME": "amatest", "OPERATOR_IP": "20.30.40.50",
        "SERVICEBUS_ENABLED": "false", "DEPLOY_CAPHOST": "false", "ACR_PUBLIC": "true",
    }).root_parameters()
    assert params["operatorIp"] == "20.30.40.50"
    assert params["serviceBusEnabled"] is False
    assert params["deployCapabilityHost"] is False
    assert params["acrPublic"] is True
```

- [ ] **Step 4: 실패 확인**

Run: `$env:RUN_INFRA_TESTS = '1'; .\.venv\Scripts\python -m pytest tests/infra/test_network.py -q`
Expected: FAIL — `Bicep 펼치기 실패: closed.bicepparam` (infra 파일 없음).

- [ ] **Step 5: azd 프로젝트와 진입 템플릿 작성**

`azure.yaml`:

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/Azure/azure-dev/main/schemas/v1.0/azure.yaml.json
# 인프라만 정의합니다. 서비스(콘솔, Policy Engine, Mock API, 오케스트레이터)는 P3에서 추가합니다.
name: ama-cashflow-lending
infra:
  provider: bicep
  path: infra
  module: main
```

`infra/main.bicep`:

```bicep
// 구독 범위 진입점. 리소스 그룹을 만들고 resources.bicep을 호출합니다.
// azd는 이 파일의 출력을 .azure/<env>/.env 환경 변수로 저장합니다.
targetScope = 'subscription'

@minLength(3)
@maxLength(12)
@description('azd 환경 이름. 리소스 그룹 이름과 리소스 이름 접미사에 쓰입니다.')
param environmentName string

@description('배포 리전. Foundry Agents + Private VNet을 지원해야 합니다.')
param location string = 'koreacentral'

@description('배포 창의 운영자 공인 IPv4. 비어 있으면 모든 PaaS의 public access를 끕니다.')
param operatorIp string = ''

@description('true면 az acr build를 위해 ACR public access를 전체 허용합니다(빌드 창).')
param acrPublic bool = false

@description('운영자(배포자) Entra 객체 ID. 비어 있으면 운영자 데이터 평면 역할을 만들지 않습니다.')
param operatorPrincipalId string = ''

@description('false면 Service Bus를 배포하지 않습니다(pause 상태).')
param serviceBusEnabled bool = true

@description('false면 Foundry 프로젝트 capability host 배포를 건너뜁니다(최초 배포 이후).')
param deployCapabilityHost bool = true

@allowed([ 'GlobalStandard', 'DataZoneStandard' ])
@description('gpt-5.4 계열 배포 SKU (ADR-002).')
param chatModelSku string = 'GlobalStandard'

@allowed([ 'GlobalStandard', 'DataZoneStandard' ])
@description('Microsoft-Decision-1 배포 SKU. APAC Global Standard 지원이 문서와 CLI에서 엇갈리므로 실패 시 DataZoneStandard로 전환합니다.')
param decisionModelSku string = 'GlobalStandard'

var tags = {
  'azd-env-name': environmentName
  project: 'ama-cashflow-lending'
  Control: 'Yes'
}

// capacity 단위는 1K TPM. 구독 쿼터(GlobalStandard): gpt-5.4 250, gpt-5.4-mini 1000.
var modelDeployments = [
  { name: 'gpt-5.4', format: 'OpenAI', version: '2026-03-05', sku: chatModelSku, capacity: 50 }
  { name: 'gpt-5.4-mini', format: 'OpenAI', version: '2026-03-17', sku: chatModelSku, capacity: 100 }
  { name: 'Microsoft-Decision-1', format: 'Microsoft', version: '1', sku: decisionModelSku, capacity: 50 }
]

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-${environmentName}'
  location: location
  tags: tags
}

module resources 'resources.bicep' = {
  name: 'resources-${environmentName}'
  scope: rg
  params: {
    environmentName: environmentName
    location: location
    tags: tags
    operatorIp: operatorIp
    acrPublic: acrPublic
    operatorPrincipalId: operatorPrincipalId
    serviceBusEnabled: serviceBusEnabled
    deployCapabilityHost: deployCapabilityHost
    modelDeployments: modelDeployments
  }
}

output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_LOCATION string = location
output VNET_NAME string = resources.outputs.VNET_NAME
```

`infra/main.bicepparam`:

```bicep
// azd 환경 변수를 파라미터로 옮깁니다. azd env set 은 빈 값을 다루기 어려우므로
// 배포 창이 닫힌 상태는 OPERATOR_IP=closed 로 표현합니다.
using 'main.bicep'

var operatorIpRaw = readEnvironmentVariable('OPERATOR_IP', 'closed')

param environmentName = readEnvironmentVariable('AZURE_ENV_NAME', 'amalend')
param location = readEnvironmentVariable('AZURE_LOCATION', 'koreacentral')
param operatorIp = operatorIpRaw == 'closed' ? '' : operatorIpRaw
param acrPublic = readEnvironmentVariable('ACR_PUBLIC', 'false') == 'true'
param operatorPrincipalId = readEnvironmentVariable('AZURE_PRINCIPAL_ID', '')
param serviceBusEnabled = readEnvironmentVariable('SERVICEBUS_ENABLED', 'true') == 'true'
param deployCapabilityHost = readEnvironmentVariable('DEPLOY_CAPHOST', 'true') == 'true'
param chatModelSku = readEnvironmentVariable('CHAT_MODEL_SKU', 'GlobalStandard')
param decisionModelSku = readEnvironmentVariable('DECISION_MODEL_SKU', 'GlobalStandard')
```

- [ ] **Step 6: 테스트 파라미터 파일 작성**

`infra/tests/closed.bicepparam`:

```bicep
// 평상시: 배포 창 닫힘
using '../main.bicep'

param environmentName = 'amatest'
param operatorIp = ''
param acrPublic = false
param operatorPrincipalId = '11111111-1111-1111-1111-111111111111'
param serviceBusEnabled = true
param deployCapabilityHost = true
```

`infra/tests/open.bicepparam`:

```bicep
// 배포 창 열림: 운영자 IP만 허용
using '../main.bicep'

param environmentName = 'amatest'
param operatorIp = '20.30.40.50'
param acrPublic = false
param operatorPrincipalId = '11111111-1111-1111-1111-111111111111'
param serviceBusEnabled = true
param deployCapabilityHost = true
```

`infra/tests/variant.bicepparam`:

```bicep
// 변형: pause(Service Bus 없음) + ACR 빌드 창 + caphost 생략(재배포) + Data Zone SKU + 운영자 없음
using '../main.bicep'

param environmentName = 'amatest'
param operatorIp = ''
param acrPublic = true
param operatorPrincipalId = ''
param serviceBusEnabled = false
param deployCapabilityHost = false
param chatModelSku = 'DataZoneStandard'
param decisionModelSku = 'DataZoneStandard'
```

- [ ] **Step 7: resources.bicep와 네트워크 모듈 작성**

`infra/resources.bicep`:

```bicep
// 리소스 그룹 범위의 전체 인프라. main.bicep이 호출합니다.
// 섹션은 P2 계획의 태스크 순서대로 추가됩니다(Bicep 선언 순서는 배포 순서와 무관).

param environmentName string
param location string
param tags object
param operatorIp string
param acrPublic bool
param operatorPrincipalId string
param serviceBusEnabled bool
param deployCapabilityHost bool
param modelDeployments array

var sfx = uniqueString(subscription().id, resourceGroup().id, environmentName)

var names = {
  vnet: 'vnet-${environmentName}'
  law: 'log-${environmentName}'
  appi: 'appi-${environmentName}'
  kv: 'kv${sfx}'
  stAgents: 'stagt${sfx}'
  stData: 'stdat${sfx}'
  stFunc: 'stfn${sfx}'
  cosmos: 'cosmos-${sfx}'
  search: 'srch-${sfx}'
  ais: 'ais-${sfx}'
  project: 'proj-lending'
  projectCapHost: 'caphost-lending'
  sb: 'sb-${sfx}'
  acr: 'acr${sfx}'
  caeInt: 'cae-int-${environmentName}'
  caeWeb: 'cae-web-${environmentName}'
  plan: 'plan-${environmentName}'
  func: 'func-${sfx}'
  idOrchestrator: 'id-orch-${environmentName}'
  idApps: 'id-apps-${environmentName}'
  idSmoke: 'id-smoke-${environmentName}'
  smokeJob: 'job-smoke'
}

var storageSuffix = environment().suffixes.storage
var blobZone = 'privatelink.blob.${storageSuffix}'
var queueZone = 'privatelink.queue.${storageSuffix}'
var tableZone = 'privatelink.table.${storageSuffix}'
var dnsZoneNames = [
  'privatelink.services.ai.azure.com'
  'privatelink.openai.azure.com'
  'privatelink.cognitiveservices.azure.com'
  'privatelink.search.windows.net'
  blobZone
  queueZone
  tableZone
  'privatelink.documents.azure.com'
  'privatelink.vaultcore.azure.net'
  'privatelink.servicebus.windows.net'
  'privatelink.azurecr.io'
  'privatelink.azurewebsites.net'
]

// 기본 제공 역할 정의 GUID (az role definition list로 확인, 2026-10-31)
var role = {
  blobOwner: 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b'
  blobContributor: 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
  blobReader: '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
  queueContributor: '974c5e8b-45b9-4653-ba55-5f855dd0fb88'
  tableContributor: '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'
  kvValueReader: '4633458b-17de-408a-b874-0445c86b69e6'
  kvValueAdmin: 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'
  foundryUser: '53ca6127-db72-4b80-b1b0-d745d6d5456d'
  openAiUser: '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
  sbSender: '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39'
  sbReceiver: '4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0'
  acrPull: '7f951dda-4ed3-4680-a7ca-43fe172d538d'
  acrPush: '8311e382-0749-4cb8-b61a-304f252e45ec'
  metricsPublisher: '3913510d-42f4-4e42-8a64-420c390055eb'
}
var sp = 'ServicePrincipal'
var hasOperator = !empty(operatorPrincipalId)

// ── 네트워크와 Private DNS (T1) ─────────────────────────────────────────
module network 'modules/network.bicep' = {
  name: 'network'
  params: {
    name: names.vnet
    location: location
    tags: tags
  }
}

module dns 'modules/dns.bicep' = {
  name: 'dns'
  params: {
    zoneNames: dnsZoneNames
    vnetId: network.outputs.vnetId
    tags: tags
  }
}

var zoneId = toObject(dns.outputs.zones, z => z.name, z => z.id)
var subnetId = network.outputs.subnetIds

output VNET_NAME string = network.outputs.vnetName
```

`infra/modules/network.bicep`:

```bicep
// Spoke VNet 하나와 서브넷 4개. Korea Central에서는 Foundry 샘플 안내대로 class B/C 대역을 씁니다.
param name string
param location string
param tags object

var subnets = [
  { name: 'snet-agents', prefix: '192.168.0.0/24', delegated: true }    // Foundry Agent Service 전용(위임, 단독)
  { name: 'snet-pe', prefix: '192.168.1.0/24', delegated: false }       // Private Endpoint
  { name: 'snet-functions', prefix: '192.168.2.0/26', delegated: true } // Flex Consumption VNet 통합
  { name: 'snet-apps-int', prefix: '192.168.4.0/24', delegated: true }  // internal Container Apps 환경(Policy, Mock API, smoke)
  { name: 'snet-apps-web', prefix: '192.168.5.0/24', delegated: true }  // external Container Apps 환경(콘솔만)
]

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: [ '192.168.0.0/16' ] }
    subnets: [for s in subnets: {
      name: s.name
      properties: {
        addressPrefix: s.prefix
        privateEndpointNetworkPolicies: 'Disabled'
        delegations: s.delegated ? [
          {
            name: 'Microsoft.App.environments'
            properties: { serviceName: 'Microsoft.App/environments' }
          }
        ] : []
      }
    }]
  }
}

output vnetId string = vnet.id
output vnetName string = vnet.name
output subnetIds object = toObject(subnets, s => s.name, s => resourceId('Microsoft.Network/virtualNetworks/subnets', name, s.name))
```

`infra/modules/dns.bicep`:

```bicep
// Private DNS zone과 VNet 링크. 출력은 [{ name, id }] 배열입니다.
param zoneNames array
param vnetId string
param tags object

resource zones 'Microsoft.Network/privateDnsZones@2024-06-01' = [for z in zoneNames: {
  name: z
  location: 'global'
  tags: tags
}]

resource links 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = [for (z, i) in zoneNames: {
  parent: zones[i]
  name: 'link-vnet'
  location: 'global'
  tags: tags
  properties: {
    registrationEnabled: false
    virtualNetwork: { id: vnetId }
  }
}]

output zones array = [for (z, i) in zoneNames: { name: z, id: zones[i].id }]
```

- [ ] **Step 8: 통과 확인**

Run: `$env:RUN_INFRA_TESTS = '1'; .\.venv\Scripts\python -m pytest tests/infra/test_network.py -q`
Expected: `6 passed`. bicep 경고(미사용 파라미터 `no-unused-params`)는 이후 태스크에서 사라지므로 무시한다. 오류(error)는 없어야 한다.

또 기본 실행에서 skip되는지 확인: `Remove-Item Env:RUN_INFRA_TESTS; .\.venv\Scripts\python -m pytest -q` → 기존 100 passed + infra skip.

- [ ] **Step 9: 커밋**

```powershell
git add azure.yaml infra tests/infra
git commit -m "feat(infra): azd scaffold, PSRule expansion harness, network and private DNS" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`nCopilot-Session: 05f1ca1b-6073-417d-bfb1-6b546f82cb58"
```

---

### Task 2: 공통 PE 모듈, 모니터링, 관리 ID, Key Vault, 배포 창 불변식 테스트

**Files:**
- Create: `infra/modules/pe.bicep`, `infra/modules/monitoring.bicep`, `infra/modules/identities.bicep`, `infra/modules/keyvault.bicep`
- Modify: `infra/resources.bicep`(끝에 T2 섹션 추가), `infra/main.bicep`(출력 추가)
- Test: `tests/infra/test_platform.py`, `tests/infra/test_window.py`

**Interfaces:**
- Consumes: T1의 `names`, `role`, `zoneId`, `subnetId`, `sp`, `hasOperator`, fixture `closed`/`opened`.
- Produces:
  - `modules/pe.bicep` 파라미터: `name`, `location`, `tags`, `subnetId`, `targetId`, `groupId`, `zoneIds`(array).
  - 역할 할당 파라미터 규약(모든 리소스 모듈): `roleAssignments` = `[{ principalId, roleId, principalType }]`, 리소스 범위에 `guid(<resource>.id, principalId, roleId)` 이름으로 생성.
  - `identities` 모듈 출력: `orchestratorId`, `orchestratorPrincipalId`, `orchestratorClientId`, `appsId`, `appsPrincipalId`, `appsClientId`, `smokeId`, `smokePrincipalId`, `smokeClientId`.
  - `monitoring` 모듈 출력: `lawId`, `lawName`, `lawCustomerId`, `appiConnectionString`.
  - `tests/infra/test_window.py`의 `WINDOWED`, `ALWAYS_PRIVATE` 행 목록(이후 태스크가 행을 추가).
  - azd 출력: `KEYVAULT_NAME`, `KEYVAULT_URI`, `LOG_ANALYTICS_CUSTOMER_ID`, `APPLICATIONINSIGHTS_CONNECTION_STRING`, `ORCHESTRATOR_IDENTITY_ID`, `ORCHESTRATOR_IDENTITY_CLIENT_ID`, `APPS_IDENTITY_ID`, `APPS_IDENTITY_CLIENT_ID`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/infra/test_window.py`:

```python
"""배포 창 불변식.

- 창이 닫혀 있으면(closed) 모든 대상의 public network access가 꺼져 있다.
- 창이 열려 있으면(open) WINDOWED 대상은 기본 거부 + 운영자 IP 정확히 1개만 허용한다.
- ALWAYS_PRIVATE 대상은 창이 열려도 꺼져 있다.
태스크마다 자기 리소스 행을 추가합니다.
"""
import pytest

OPERATOR_IP = "20.30.40.50"

WINDOWED = [
    ("Microsoft.KeyVault/vaults", "kv"),
]
ALWAYS_PRIVATE: list[tuple[str, str]] = []


def _public(resource: dict) -> str:
    return (resource["properties"].get("publicNetworkAccess") or "").lower()


def _allowed_ips(resource: dict) -> list[str]:
    props = resource["properties"]
    kind = resource["type"].lower()
    if kind == "microsoft.documentdb/databaseaccounts":
        return [r["ipAddressOrRange"] for r in props.get("ipRules", [])]
    if kind == "microsoft.web/sites":
        return [r["ipAddress"].removesuffix("/32") for r in props["siteConfig"].get("ipSecurityRestrictions", [])]
    if kind == "microsoft.search/searchservices":
        return [r["value"] for r in props["networkRuleSet"].get("ipRules", [])]
    return [r["value"] for r in props["networkAcls"].get("ipRules", [])]


def _default_deny(resource: dict) -> bool:
    props = resource["properties"]
    kind = resource["type"].lower()
    if kind == "microsoft.documentdb/databaseaccounts":
        # Cosmos는 기본 동작 설정이 없고 ipRules가 비면 전체 허용이므로 규칙이 반드시 있어야 합니다.
        return bool(props.get("ipRules"))
    if kind == "microsoft.web/sites":
        return props["siteConfig"].get("ipSecurityRestrictionsDefaultAction") == "Deny"
    return props["networkAcls"]["defaultAction"] == "Deny"


@pytest.mark.parametrize(("type_", "prefix"), WINDOWED + ALWAYS_PRIVATE)
def test_closed_window_disables_public_access(closed, type_, prefix):
    assert _public(closed.one(type_, prefix)) == "disabled"


@pytest.mark.parametrize(("type_", "prefix"), WINDOWED)
def test_open_window_allows_only_operator_ip(opened, type_, prefix):
    resource = opened.one(type_, prefix)
    assert _public(resource) == "enabled"
    assert _default_deny(resource)
    assert _allowed_ips(resource) == [OPERATOR_IP]


@pytest.mark.parametrize(("type_", "prefix"), ALWAYS_PRIVATE)
def test_always_private_stays_closed_when_window_open(opened, type_, prefix):
    assert _public(opened.one(type_, prefix)) == "disabled"
```

`tests/infra/test_platform.py`:

```python
KV = "Microsoft.KeyVault/vaults"
ROLE_KV_VALUE_READER = "4633458b-17de-408a-b874-0445c86b69e6"
ROLE_KV_VALUE_ADMIN = "b86a8fe4-44ce-4948-aee5-eccb2c155cd7"
ROLE_METRICS_PUBLISHER = "3913510d-42f4-4e42-8a64-420c390055eb"
OPERATOR = "11111111-1111-1111-1111-111111111111"


def test_key_vault_is_rbac_only_with_private_endpoint(closed):
    props = closed.one(KV, "kv")["properties"]
    assert props["enableRbacAuthorization"] is True
    assert props["networkAcls"]["bypass"] == "None"
    assert closed.private_endpoints("kv") == {"vault": {"privatelink.vaultcore.azure.net"}}


def test_key_vault_roles_for_identities_and_operator(closed):
    assert closed.roles_on("kv") == {ROLE_KV_VALUE_READER, ROLE_KV_VALUE_ADMIN}
    operator = [r for r in closed.role_assignments_on("kv") if r["properties"]["principalId"] == OPERATOR]
    assert [r["properties"]["principalType"] for r in operator] == ["User"]


def test_no_operator_roles_without_operator_principal(variant):
    assert variant.roles_on("kv") == {ROLE_KV_VALUE_READER}


def test_app_insights_is_workspace_based_and_entra_only(closed):
    appi = closed.one("Microsoft.Insights/components", "appi-")
    assert appi["properties"]["DisableLocalAuth"] is True
    assert appi["properties"]["WorkspaceResourceId"].endswith("/workspaces/log-amatest")
    assert closed.roles_on("appi-") == {ROLE_METRICS_PUBLISHER}


def test_three_user_assigned_identities(closed):
    names = {r["name"] for r in closed.of_type("Microsoft.ManagedIdentity/userAssignedIdentities")}
    assert names == {"id-orch-amatest", "id-apps-amatest", "id-smoke-amatest"}
```

- [ ] **Step 2: 실패 확인**

Run: `$env:RUN_INFRA_TESTS = '1'; .\.venv\Scripts\python -m pytest tests/infra/test_platform.py tests/infra/test_window.py -q`
Expected: FAIL — `Microsoft.KeyVault/vaults 'kv': []` 등 리소스 없음.

- [ ] **Step 3: 모듈 작성**

`infra/modules/pe.bicep`:

```bicep
// 대상 리소스 하나의 하위 서비스(groupId) 하나에 대한 Private Endpoint와 DNS zone group.
param name string
param location string
param tags object
param subnetId string
param targetId string
param groupId string
@description('이 PE의 A 레코드를 등록할 Private DNS zone ID 목록')
param zoneIds array

resource pe 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    subnet: { id: subnetId }
    privateLinkServiceConnections: [
      {
        name: name
        properties: {
          privateLinkServiceId: targetId
          groupIds: [ groupId ]
        }
      }
    ]
  }

  resource zoneGroup 'privateDnsZoneGroups@2024-05-01' = {
    name: 'default'
    properties: {
      privateDnsZoneConfigs: [for (z, i) in zoneIds: {
        name: 'zone${i}'
        properties: { privateDnsZoneId: z }
      }]
    }
  }
}

output name string = pe.name
```

`infra/modules/monitoring.bicep`:

```bicep
// Log Analytics + 작업 영역 기반 App Insights. 로컬 인증을 끄므로 쓰는 쪽은 Monitoring Metrics Publisher 역할이 필요합니다.
param lawName string
param appiName string
param location string
param tags object
@description('[{ principalId, roleId, principalType }]')
param roleAssignments array = []

resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: lawName
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appi 'Microsoft.Insights/components@2020-02-02' = {
  name: appiName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: law.id
    DisableLocalAuth: true
  }
}

resource ra 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for a in roleAssignments: {
  scope: appi
  name: guid(appi.id, a.principalId, a.roleId)
  properties: {
    principalId: a.principalId
    principalType: a.principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', a.roleId)
  }
}]

output lawId string = law.id
output lawName string = law.name
output lawCustomerId string = law.properties.customerId
output appiConnectionString string = appi.properties.ConnectionString
```

`infra/modules/identities.bicep`:

```bicep
// 워크로드별 사용자 할당 관리 ID. 오케스트레이터(Functions), 앱(Container Apps), smoke Job.
param names object
param location string
param tags object

resource orchestrator 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: names.orchestrator
  location: location
  tags: tags
}

resource apps 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: names.apps
  location: location
  tags: tags
}

resource smoke 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: names.smoke
  location: location
  tags: tags
}

output orchestratorId string = orchestrator.id
output orchestratorPrincipalId string = orchestrator.properties.principalId
output orchestratorClientId string = orchestrator.properties.clientId
output appsId string = apps.id
output appsPrincipalId string = apps.properties.principalId
output appsClientId string = apps.properties.clientId
output smokeId string = smoke.id
output smokePrincipalId string = smoke.properties.principalId
output smokeClientId string = smoke.properties.clientId
```

`infra/modules/keyvault.bicep`:

```bicep
// Key Vault(RBAC 모드). 배포 창에서만 운영자 IP를 허용합니다.
param name string
param location string
param tags object
param operatorIp string
param peSubnetId string
param zoneId string
@description('[{ principalId, roleId, principalType }]')
param roleAssignments array = []

var windowOpen = !empty(operatorIp)

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    tenantId: tenant().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    publicNetworkAccess: windowOpen ? 'Enabled' : 'Disabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'None'
      ipRules: windowOpen ? [ { value: operatorIp } ] : []
    }
  }
}

module pe 'pe.bicep' = {
  name: 'pe-${name}'
  params: {
    name: 'pe-${name}-vault'
    location: location
    tags: tags
    subnetId: peSubnetId
    targetId: kv.id
    groupId: 'vault'
    zoneIds: [ zoneId ]
  }
}

resource ra 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for a in roleAssignments: {
  scope: kv
  name: guid(kv.id, a.principalId, a.roleId)
  properties: {
    principalId: a.principalId
    principalType: a.principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', a.roleId)
  }
}]

output name string = kv.name
output uri string = kv.properties.vaultUri
```

- [ ] **Step 4: resources.bicep에 T2 섹션 추가**

`infra/resources.bicep` 끝에 추가:

```bicep
// ── 모니터링, 관리 ID, Key Vault (T2) ───────────────────────────────────
module identities 'modules/identities.bicep' = {
  name: 'identities'
  params: {
    names: {
      orchestrator: names.idOrchestrator
      apps: names.idApps
      smoke: names.idSmoke
    }
    location: location
    tags: tags
  }
}

module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring'
  params: {
    lawName: names.law
    appiName: names.appi
    location: location
    tags: tags
    roleAssignments: [
      { principalId: identities.outputs.orchestratorPrincipalId, roleId: role.metricsPublisher, principalType: sp }
      { principalId: identities.outputs.appsPrincipalId, roleId: role.metricsPublisher, principalType: sp }
    ]
  }
}

module keyVault 'modules/keyvault.bicep' = {
  name: 'keyvault'
  params: {
    name: names.kv
    location: location
    tags: tags
    operatorIp: operatorIp
    peSubnetId: subnetId['snet-pe']
    zoneId: zoneId['privatelink.vaultcore.azure.net']
    roleAssignments: concat([
      { principalId: identities.outputs.orchestratorPrincipalId, roleId: role.kvValueReader, principalType: sp }
      { principalId: identities.outputs.appsPrincipalId, roleId: role.kvValueReader, principalType: sp }
      { principalId: identities.outputs.smokePrincipalId, roleId: role.kvValueReader, principalType: sp }
    ], hasOperator ? [
      { principalId: operatorPrincipalId, roleId: role.kvValueAdmin, principalType: 'User' }
    ] : [])
  }
}

output KEYVAULT_NAME string = keyVault.outputs.name
output KEYVAULT_URI string = keyVault.outputs.uri
output LOG_ANALYTICS_CUSTOMER_ID string = monitoring.outputs.lawCustomerId
output APPLICATIONINSIGHTS_CONNECTION_STRING string = monitoring.outputs.appiConnectionString
output ORCHESTRATOR_IDENTITY_ID string = identities.outputs.orchestratorId
output ORCHESTRATOR_IDENTITY_CLIENT_ID string = identities.outputs.orchestratorClientId
output APPS_IDENTITY_ID string = identities.outputs.appsId
output APPS_IDENTITY_CLIENT_ID string = identities.outputs.appsClientId
```

`infra/main.bicep` 끝에 추가:

```bicep
output KEYVAULT_NAME string = resources.outputs.KEYVAULT_NAME
output KEYVAULT_URI string = resources.outputs.KEYVAULT_URI
output LOG_ANALYTICS_CUSTOMER_ID string = resources.outputs.LOG_ANALYTICS_CUSTOMER_ID
output APPLICATIONINSIGHTS_CONNECTION_STRING string = resources.outputs.APPLICATIONINSIGHTS_CONNECTION_STRING
output ORCHESTRATOR_IDENTITY_ID string = resources.outputs.ORCHESTRATOR_IDENTITY_ID
output ORCHESTRATOR_IDENTITY_CLIENT_ID string = resources.outputs.ORCHESTRATOR_IDENTITY_CLIENT_ID
output APPS_IDENTITY_ID string = resources.outputs.APPS_IDENTITY_ID
output APPS_IDENTITY_CLIENT_ID string = resources.outputs.APPS_IDENTITY_CLIENT_ID
```

- [ ] **Step 5: 통과 확인**

Run: `$env:RUN_INFRA_TESTS = '1'; .\.venv\Scripts\python -m pytest tests/infra -q`
Expected: 전부 PASS(`test_network` 6 + `test_platform` 5 + `test_window` 2, `test_always_private_*`는 행이 없어 0개 수집).

- [ ] **Step 6: 커밋**

```powershell
git add infra tests/infra
git commit -m "feat(infra): private endpoint module, monitoring, identities, key vault with deploy window" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`nCopilot-Session: 05f1ca1b-6073-417d-bfb1-6b546f82cb58"
```

---

### Task 3: 데이터 저장소 — Storage 3개, Cosmos DB, AI Search

**Files:**
- Create: `infra/modules/storage.bicep`, `infra/modules/cosmos.bicep`, `infra/modules/search.bicep`
- Modify: `infra/resources.bicep`(T3 섹션), `infra/main.bicep`(출력), `tests/infra/test_window.py`(행 추가)
- Test: `tests/infra/test_data.py`

**Interfaces:**
- Consumes: T2의 `pe.bicep`, 역할 할당 규약, `identities.outputs.*PrincipalId`.
- Produces:
  - 모듈 심볼 `stAgents`, `stData`, `stFunc`, `cosmos`, `search`(출력 `name`; storage는 `blobEndpoint`, cosmos는 `endpoint`·`databaseName`).
  - `stFunc`에 컨테이너 `deploymentpackage`(T7 Function App 배포 저장소).
  - Cosmos 앱 데이터베이스 `lending`(컨테이너는 P3).
  - azd 출력: `DATA_STORAGE_NAME`, `FUNC_STORAGE_NAME`, `COSMOS_ENDPOINT`, `COSMOS_DATABASE`, `SEARCH_NAME`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/infra/test_data.py`:

```python
ST = "Microsoft.Storage/storageAccounts"
COSMOS = "Microsoft.DocumentDB/databaseAccounts"
BLOB = "privatelink.blob.core.windows.net"
ROLE_BLOB_OWNER = "b7e6dc6d-f1e8-4753-8033-0f276bb0955b"
ROLE_BLOB_CONTRIBUTOR = "ba92f5b4-2d11-453d-a403-e96b0029c9fe"
ROLE_BLOB_READER = "2a2b9908-6ea1-4ae2-8e65-a410df84e7d1"
ROLE_QUEUE_CONTRIBUTOR = "974c5e8b-45b9-4653-ba55-5f855dd0fb88"
ROLE_TABLE_CONTRIBUTOR = "0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3"
COSMOS_READER = "00000000-0000-0000-0000-000000000001"
COSMOS_CONTRIBUTOR = "00000000-0000-0000-0000-000000000002"


def test_storage_accounts_are_keyless(closed):
    for prefix in ("stagt", "stdat", "stfn"):
        props = closed.one(ST, prefix)["properties"]
        assert props["allowSharedKeyAccess"] is False, prefix
        assert props["allowBlobPublicAccess"] is False, prefix
        assert props["minimumTlsVersion"] == "TLS1_2", prefix


def test_storage_private_endpoints(closed):
    assert closed.private_endpoints("stagt") == {"blob": {BLOB}}
    assert closed.private_endpoints("stdat") == {"blob": {BLOB}}
    assert closed.private_endpoints("stfn") == {
        "blob": {BLOB},
        "queue": {"privatelink.queue.core.windows.net"},
        "table": {"privatelink.table.core.windows.net"},
    }


def test_function_storage_has_deployment_container_and_durable_roles(closed):
    containers = {c["name"].split("/")[-1] for c in closed.of_type(f"{ST}/blobServices/containers")
                  if c["name"].startswith("stfn")}
    assert containers == {"deploymentpackage"}
    assert closed.roles_on("stfn") == {ROLE_BLOB_OWNER, ROLE_QUEUE_CONTRIBUTOR, ROLE_TABLE_CONTRIBUTOR}


def test_data_storage_has_no_soft_delete(closed):
    # 동의 만료 시 원천 데이터 삭제를 증명해야 하므로 soft delete를 끕니다(스펙 §4).
    blob_service = next(b for b in closed.of_type(f"{ST}/blobServices") if b["name"].startswith("stdat"))
    assert blob_service["properties"]["deleteRetentionPolicy"]["enabled"] is False
    assert closed.roles_on("stdat") == {ROLE_BLOB_CONTRIBUTOR, ROLE_BLOB_READER}


def test_cosmos_is_serverless_keyless_with_app_database(closed):
    props = closed.one(COSMOS, "cosmos-")["properties"]
    assert [c["name"] for c in props["capabilities"]] == ["EnableServerless"]
    assert props["disableLocalAuth"] is True
    assert closed.private_endpoints("cosmos-") == {"Sql": {"privatelink.documents.azure.com"}}
    dbs = {d["name"].split("/")[-1] for d in closed.of_type(f"{COSMOS}/sqlDatabases")}
    assert dbs == {"lending"}


def cosmos_sql_roles(exp) -> set[tuple[str, str]]:
    """(역할 GUID, 범위) 집합. 범위는 'account' 또는 데이터베이스 이름."""
    result = set()
    for a in exp.of_type(f"{COSMOS}/sqlRoleAssignments"):
        role = a["properties"]["roleDefinitionId"].rsplit("/", 1)[-1]
        scope = a["properties"]["scope"]
        result.add((role, scope.split("/dbs/")[1] if "/dbs/" in scope else "account"))
    return result


def test_cosmos_sql_roles_scope(closed):
    # 앱 ID(오케스트레이터·앱)는 lending DB 쓰기, smoke는 계정 읽기, 운영자는 계정 쓰기.
    # T4에서 Foundry 프로젝트의 enterprise_memory 역할이 추가되므로 부분집합으로 확인합니다.
    assert cosmos_sql_roles(closed) >= {
        (COSMOS_CONTRIBUTOR, "lending"),
        (COSMOS_READER, "account"),
        (COSMOS_CONTRIBUTOR, "account"),
    }


def test_search_is_basic_private_and_aad_enabled(closed):
    search = closed.one("Microsoft.Search/searchServices", "srch-")
    assert search["sku"]["name"] == "basic"
    assert search["properties"]["authOptions"]["aadOrApiKey"]["aadAuthFailureMode"] == "http401WithBearerChallenge"
    assert closed.private_endpoints("srch-") == {"searchService": {"privatelink.search.windows.net"}}
```

`tests/infra/test_window.py`의 목록을 다음으로 바꾼다:

```python
WINDOWED = [
    ("Microsoft.KeyVault/vaults", "kv"),
    ("Microsoft.Storage/storageAccounts", "stdat"),
    ("Microsoft.Storage/storageAccounts", "stfn"),
    ("Microsoft.DocumentDB/databaseAccounts", "cosmos-"),
]
ALWAYS_PRIVATE: list[tuple[str, str]] = [
    ("Microsoft.Storage/storageAccounts", "stagt"),
    ("Microsoft.Search/searchServices", "srch-"),
]
```

- [ ] **Step 2: 실패 확인**

Run: `$env:RUN_INFRA_TESTS = '1'; .\.venv\Scripts\python -m pytest tests/infra/test_data.py tests/infra/test_window.py -q`
Expected: FAIL — `Microsoft.Storage/storageAccounts 'stagt': []`.

- [ ] **Step 3: 모듈 작성**

`infra/modules/storage.bicep`:

```bicep
// 범용 Storage 계정. 공유 키를 끄고, 배포 창에서만 운영자 IP를 허용합니다.
param name string
param location string
param tags object
@description('비어 있으면 public access 차단. 값이 있으면 그 IP만 허용(배포 창).')
param operatorIp string
@description('신뢰할 수 있는 Azure 서비스 우회. Foundry BYO Storage는 샘플대로 AzureServices.')
param bypass string = 'None'
param peSubnetId string
@description('[{ groupId: blob|queue|table, zoneId }]')
param endpoints array
param containers array = []
@description('blob soft delete 보존 일수. 0이면 끕니다(원천 데이터 삭제 증명용).')
param softDeleteDays int = 7
@description('[{ principalId, roleId, principalType }]')
param roleAssignments array = []

var windowOpen = !empty(operatorIp)

resource st 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: name
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    defaultToOAuthAuthentication: true
    publicNetworkAccess: windowOpen ? 'Enabled' : 'Disabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: bypass
      ipRules: windowOpen ? [ { value: operatorIp, action: 'Allow' } ] : []
      virtualNetworkRules: []
    }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: st
  name: 'default'
  properties: {
    deleteRetentionPolicy: softDeleteDays > 0 ? { enabled: true, days: softDeleteDays } : { enabled: false }
  }
}

resource blobContainers 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = [for c in containers: {
  parent: blobService
  name: c
  properties: { publicAccess: 'None' }
}]

module pes 'pe.bicep' = [for e in endpoints: {
  name: 'pe-${name}-${e.groupId}'
  params: {
    name: 'pe-${name}-${e.groupId}'
    location: location
    tags: tags
    subnetId: peSubnetId
    targetId: st.id
    groupId: e.groupId
    zoneIds: [ e.zoneId ]
  }
}]

resource ra 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for a in roleAssignments: {
  scope: st
  name: guid(st.id, a.principalId, a.roleId)
  properties: {
    principalId: a.principalId
    principalType: a.principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', a.roleId)
  }
}]

output name string = st.name
output blobEndpoint string = st.properties.primaryEndpoints.blob
```

`infra/modules/cosmos.bicep`:

```bicep
// Cosmos DB for NoSQL(serverless). Foundry 스레드 저장소(enterprise_memory, caphost가 생성)와 앱 DB(lending)를 함께 둡니다.
param name string
param location string
param tags object
param operatorIp string
param peSubnetId string
param zoneId string
param appDatabaseName string = 'lending'
@description('[{ principalId, roleDefinitionGuid, scope: account|database }] — Cosmos 데이터 평면 SQL 역할')
param sqlRoleAssignments array = []

var windowOpen = !empty(operatorIp)

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = {
  name: name
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    consistencyPolicy: { defaultConsistencyLevel: 'Session' }
    locations: [ { locationName: location, failoverPriority: 0, isZoneRedundant: false } ]
    capabilities: [ { name: 'EnableServerless' } ]
    disableLocalAuth: true
    disableKeyBasedMetadataWriteAccess: true
    publicNetworkAccess: windowOpen ? 'Enabled' : 'Disabled'
    ipRules: windowOpen ? [ { ipAddressOrRange: operatorIp } ] : []
    networkAclBypass: 'None'
  }
}

resource appDb 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-11-15' = {
  parent: cosmos
  name: appDatabaseName
  properties: { resource: { id: appDatabaseName } }
}

module pe 'pe.bicep' = {
  name: 'pe-${name}'
  params: {
    name: 'pe-${name}-sql'
    location: location
    tags: tags
    subnetId: peSubnetId
    targetId: cosmos.id
    groupId: 'Sql'
    zoneIds: [ zoneId ]
  }
}

resource sqlRa 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = [for a in sqlRoleAssignments: {
  parent: cosmos
  name: guid(cosmos.id, a.principalId, a.roleDefinitionGuid, a.scope)
  properties: {
    principalId: a.principalId
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/${a.roleDefinitionGuid}'
    scope: a.scope == 'database' ? '${cosmos.id}/dbs/${appDatabaseName}' : cosmos.id
  }
  dependsOn: [ appDb ]
}]

output name string = cosmos.name
output endpoint string = cosmos.properties.documentEndpoint
output databaseName string = appDb.name
```

`infra/modules/search.bicep`:

```bicep
// AI Search Basic. Foundry 벡터 저장소 연결용. 항상 비공개(에이전트는 VNet injection으로 접근).
param name string
param location string
param tags object
param peSubnetId string
param zoneId string

resource search 'Microsoft.Search/searchServices@2024-06-01-preview' = {
  name: name
  location: location
  tags: tags
  sku: { name: 'basic' }
  identity: { type: 'SystemAssigned' }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    publicNetworkAccess: 'disabled'
    networkRuleSet: { bypass: 'None', ipRules: [] }
    disableLocalAuth: false
    authOptions: { aadOrApiKey: { aadAuthFailureMode: 'http401WithBearerChallenge' } }
    semanticSearch: 'disabled'
  }
}

module pe 'pe.bicep' = {
  name: 'pe-${name}'
  params: {
    name: 'pe-${name}-search'
    location: location
    tags: tags
    subnetId: peSubnetId
    targetId: search.id
    groupId: 'searchService'
    zoneIds: [ zoneId ]
  }
}

output name string = search.name
```

- [ ] **Step 4: resources.bicep에 T3 섹션 추가**

```bicep
// ── 데이터 저장소 (T3) ───────────────────────────────────────────────────
module stAgents 'modules/storage.bicep' = {
  name: 'storage-agents'
  params: {
    name: names.stAgents
    location: location
    tags: tags
    operatorIp: ''  // Foundry BYO Storage는 항상 비공개
    bypass: 'AzureServices'
    peSubnetId: subnetId['snet-pe']
    endpoints: [ { groupId: 'blob', zoneId: zoneId[blobZone] } ]
  }
}

module stData 'modules/storage.bicep' = {
  name: 'storage-data'
  params: {
    name: names.stData
    location: location
    tags: tags
    operatorIp: operatorIp
    peSubnetId: subnetId['snet-pe']
    endpoints: [ { groupId: 'blob', zoneId: zoneId[blobZone] } ]
    softDeleteDays: 0
    roleAssignments: concat([
      { principalId: identities.outputs.orchestratorPrincipalId, roleId: role.blobContributor, principalType: sp }
      { principalId: identities.outputs.appsPrincipalId, roleId: role.blobContributor, principalType: sp }
      { principalId: identities.outputs.smokePrincipalId, roleId: role.blobReader, principalType: sp }
    ], hasOperator ? [
      { principalId: operatorPrincipalId, roleId: role.blobContributor, principalType: 'User' }
    ] : [])
  }
}

module stFunc 'modules/storage.bicep' = {
  name: 'storage-functions'
  params: {
    name: names.stFunc
    location: location
    tags: tags
    operatorIp: operatorIp
    peSubnetId: subnetId['snet-pe']
    endpoints: [
      { groupId: 'blob', zoneId: zoneId[blobZone] }
      { groupId: 'queue', zoneId: zoneId[queueZone] }
      { groupId: 'table', zoneId: zoneId[tableZone] }
    ]
    containers: [ 'deploymentpackage' ]
    roleAssignments: [
      { principalId: identities.outputs.orchestratorPrincipalId, roleId: role.blobOwner, principalType: sp }
      { principalId: identities.outputs.orchestratorPrincipalId, roleId: role.queueContributor, principalType: sp }
      { principalId: identities.outputs.orchestratorPrincipalId, roleId: role.tableContributor, principalType: sp }
    ]
  }
}

module cosmos 'modules/cosmos.bicep' = {
  name: 'cosmos'
  params: {
    name: names.cosmos
    location: location
    tags: tags
    operatorIp: operatorIp
    peSubnetId: subnetId['snet-pe']
    zoneId: zoneId['privatelink.documents.azure.com']
    sqlRoleAssignments: concat([
      { principalId: identities.outputs.orchestratorPrincipalId, roleDefinitionGuid: '00000000-0000-0000-0000-000000000002', scope: 'database' }
      { principalId: identities.outputs.appsPrincipalId, roleDefinitionGuid: '00000000-0000-0000-0000-000000000002', scope: 'database' }
      { principalId: identities.outputs.smokePrincipalId, roleDefinitionGuid: '00000000-0000-0000-0000-000000000001', scope: 'account' }
    ], hasOperator ? [
      { principalId: operatorPrincipalId, roleDefinitionGuid: '00000000-0000-0000-0000-000000000002', scope: 'account' }
    ] : [])
  }
}

module search 'modules/search.bicep' = {
  name: 'search'
  params: {
    name: names.search
    location: location
    tags: tags
    peSubnetId: subnetId['snet-pe']
    zoneId: zoneId['privatelink.search.windows.net']
  }
}

output DATA_STORAGE_NAME string = stData.outputs.name
output FUNC_STORAGE_NAME string = stFunc.outputs.name
output COSMOS_ENDPOINT string = cosmos.outputs.endpoint
output COSMOS_DATABASE string = cosmos.outputs.databaseName
output SEARCH_NAME string = search.outputs.name
```

`infra/main.bicep` 끝에 추가:

```bicep
output DATA_STORAGE_NAME string = resources.outputs.DATA_STORAGE_NAME
output FUNC_STORAGE_NAME string = resources.outputs.FUNC_STORAGE_NAME
output COSMOS_ENDPOINT string = resources.outputs.COSMOS_ENDPOINT
output COSMOS_DATABASE string = resources.outputs.COSMOS_DATABASE
output SEARCH_NAME string = resources.outputs.SEARCH_NAME
```

- [ ] **Step 5: 통과 확인**

Run: `$env:RUN_INFRA_TESTS = '1'; .\.venv\Scripts\python -m pytest tests/infra -q`
Expected: 전부 PASS.

- [ ] **Step 6: 커밋**

```powershell
git add infra tests/infra
git commit -m "feat(infra): storage accounts, serverless cosmos, ai search with private endpoints" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`nCopilot-Session: 05f1ca1b-6073-417d-bfb1-6b546f82cb58"
```

### Task 4: Foundry — AI Services 계정, 모델 배포, 프로젝트, capability host

Foundry 공식 샘플 `15-private-network-standard-agent-setup`(microsoft-foundry/foundry-samples)의 구조와 순서를 따른다: 계정(network injection) → 프로젝트(BYO 연결 3개) → 사전 역할 → 프로젝트 capability host → 사후 역할(ABAC 조건부 Blob Owner, Cosmos `enterprise_memory` SQL 역할). 계정 capability host(`<계정>@aml_aiagentservice`)는 `networkInjections.scenario='agent'` 계정에 플랫폼이 자동 생성하므로 만들지 않는다.

**Files:**
- Create: `infra/modules/foundry/account.bicep`, `infra/modules/foundry/project.bicep`, `infra/modules/foundry/roles-pre.bicep`, `infra/modules/foundry/caphost.bicep`, `infra/modules/foundry/roles-post.bicep`
- Modify: `infra/resources.bicep`(T4 섹션), `infra/main.bicep`(출력), `tests/infra/test_window.py`(행 추가)
- Test: `tests/infra/test_foundry.py`

**Interfaces:**
- Consumes: T1 `modelDeployments` 파라미터(`{name, format, version, sku, capacity}` 배열), T3 모듈 `cosmos`·`stAgents`·`search`의 `outputs.name`, T2 `identities.outputs.*PrincipalId`.
- Produces:
  - 모듈 심볼 `aiAccount`(출력 `name`), `aiProject`(출력 `name`, `principalId`, `workspaceId`, `cosmosConnection`, `storageConnection`, `searchConnection`).
  - 모델 배포 이름(= 모델 이름): `gpt-5.4`, `gpt-5.4-mini`, `Microsoft-Decision-1`. P3 에이전트는 이 이름을 `model`로 쓴다.
  - azd 출력: `AI_ACCOUNT_NAME`, `AI_PROJECT_NAME`, `AI_PROJECT_ENDPOINT`(`https://<계정>.services.ai.azure.com/api/projects/<프로젝트>`), `AI_OPENAI_ENDPOINT`(`https://<계정>.openai.azure.com/`), `FOUNDRY_PROJECT_CAPHOST`(`caphost-lending`).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/infra/test_foundry.py`:

```python
AIS = "Microsoft.CognitiveServices/accounts"
ROLE_FOUNDRY_USER = "53ca6127-db72-4b80-b1b0-d745d6d5456d"
ROLE_OPENAI_USER = "5e0bd9bd-7b93-4f28-af87-19fc36ad61bd"
ROLE_BLOB_OWNER = "b7e6dc6d-f1e8-4753-8033-0f276bb0955b"
ROLE_BLOB_CONTRIBUTOR = "ba92f5b4-2d11-453d-a403-e96b0029c9fe"
ROLE_COSMOS_OPERATOR = "230815da-be43-4aae-9cb4-875f7bd000aa"
ROLE_SEARCH_INDEX_CONTRIBUTOR = "8ebe5a00-799e-43f5-93ac-243d3dce84a7"
ROLE_SEARCH_SERVICE_CONTRIBUTOR = "7ca78c08-252a-4471-8644-bb5ff32d4ba0"
COSMOS_CONTRIBUTOR = "00000000-0000-0000-0000-000000000002"
MODELS = {"gpt-5.4", "gpt-5.4-mini", "Microsoft-Decision-1"}


def _deployments(exp) -> dict[str, dict]:
    return {d["name"].split("/")[-1]: d for d in exp.of_type(f"{AIS}/deployments")}


def test_account_is_keyless_with_agent_network_injection(closed):
    account = closed.one(AIS, "ais-")
    props = account["properties"]
    assert account["kind"] == "AIServices"
    assert props["disableLocalAuth"] is True
    assert props["allowProjectManagement"] is True
    assert props["customSubDomainName"] == account["name"]
    [injection] = props["networkInjections"]
    assert injection["scenario"] == "agent"
    assert injection["subnetArmId"].endswith("/subnets/snet-agents")
    assert injection["useMicrosoftManagedNetwork"] is False
    assert closed.private_endpoints("ais-") == {"account": {
        "privatelink.services.ai.azure.com",
        "privatelink.openai.azure.com",
        "privatelink.cognitiveservices.azure.com",
    }}


def test_model_deployments_default_to_global_standard(closed):
    deployments = _deployments(closed)
    assert set(deployments) == MODELS
    assert {d["sku"]["name"] for d in deployments.values()} == {"GlobalStandard"}
    assert deployments["Microsoft-Decision-1"]["properties"]["model"]["format"] == "Microsoft"
    assert deployments["gpt-5.4"]["properties"]["model"]["version"] == "2026-03-05"


def test_model_sku_parameters_switch_to_data_zone(variant):
    assert {d["sku"]["name"] for d in _deployments(variant).values()} == {"DataZoneStandard"}


def test_account_data_plane_roles(closed):
    assert closed.roles_on("ais-") == {ROLE_FOUNDRY_USER, ROLE_OPENAI_USER}


def test_project_connections_use_entra_auth(closed):
    connections = closed.of_type(f"{AIS}/projects/connections")
    assert {c["properties"]["category"] for c in connections} == {"CosmosDB", "AzureStorageAccount", "CognitiveSearch"}
    assert {c["properties"]["authType"] for c in connections} == {"AAD"}


def test_capability_host_wires_byo_connections(closed):
    caphost = closed.one(f"{AIS}/projects/capabilityHosts")
    assert caphost["name"].endswith("/caphost-lending")
    props = caphost["properties"]
    assert props["capabilityHostKind"] == "Agents"
    assert props["threadStorageConnections"][0].startswith("cosmos-")
    assert props["storageConnections"][0].startswith("stagt")
    assert props["vectorStoreConnections"][0].startswith("srch-")


def test_project_roles_before_and_after_caphost(closed):
    assert closed.roles_on("stagt") == {ROLE_BLOB_CONTRIBUTOR, ROLE_BLOB_OWNER}
    [owner] = [r for r in closed.role_assignments_on("stagt")
               if r["properties"]["roleDefinitionId"].endswith(ROLE_BLOB_OWNER)]
    assert owner["properties"]["conditionVersion"] == "2.0"
    assert "*-azureml-agent" in owner["properties"]["condition"]
    assert closed.roles_on("srch-") == {ROLE_SEARCH_INDEX_CONTRIBUTOR, ROLE_SEARCH_SERVICE_CONTRIBUTOR}
    assert closed.roles_on("cosmos-") == {ROLE_COSMOS_OPERATOR}
    thread_store = [a for a in closed.of_type("Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments")
                    if a["properties"]["scope"].endswith("/dbs/enterprise_memory")]
    assert [a["properties"]["roleDefinitionId"].rsplit("/", 1)[-1] for a in thread_store] == [COSMOS_CONTRIBUTOR]


def test_capability_host_skipped_when_disabled(variant):
    # 재프로비저닝 시 caphost 재-PUT을 피합니다(Review Focus #3). 사후 역할도 함께 생략합니다.
    assert variant.of_type(f"{AIS}/projects/capabilityHosts") == []
    assert variant.roles_on("stagt") == {ROLE_BLOB_CONTRIBUTOR}
```

`tests/infra/test_window.py`의 `WINDOWED` 끝에 행 추가:

```python
    ("Microsoft.CognitiveServices/accounts", "ais-"),
```

- [ ] **Step 2: 실패 확인**

Run: `$env:RUN_INFRA_TESTS = '1'; .\.venv\Scripts\python -m pytest tests/infra/test_foundry.py tests/infra/test_window.py -q`
Expected: FAIL — `Microsoft.CognitiveServices/accounts 'ais-': []`.

- [ ] **Step 3: Foundry 모듈 작성**

`infra/modules/foundry/account.bicep`:

```bicep
// Foundry(AI Services) 계정. Agent Service는 snet-agents에 network injection 됩니다.
// 계정 capability host(<계정>@aml_aiagentservice)는 플랫폼이 자동 생성합니다.
param name string
param location string
param tags object
param operatorIp string
param agentSubnetId string
param peSubnetId string
@description('services.ai, openai, cognitiveservices 순서의 Private DNS zone ID')
param zoneIds array
@description('[{ name, format, version, sku, capacity }]')
param modelDeployments array
@description('[{ principalId, roleId, principalType }]')
param roleAssignments array = []

var windowOpen = !empty(operatorIp)

#disable-next-line BCP036
resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' = {
  name: name
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    allowProjectManagement: true
    customSubDomainName: name
    disableLocalAuth: true
    publicNetworkAccess: windowOpen ? 'Enabled' : 'Disabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'AzureServices'
      ipRules: windowOpen ? [ { value: operatorIp } ] : []
      virtualNetworkRules: []
    }
    networkInjections: [
      {
        scenario: 'agent'
        subnetArmId: agentSubnetId
        useMicrosoftManagedNetwork: false
      }
    ]
  }
}

// 같은 계정의 배포를 동시에 만들면 충돌하므로 하나씩 배포합니다.
@batchSize(1)
resource deployments 'Microsoft.CognitiveServices/accounts/deployments@2025-04-01-preview' = [for m in modelDeployments: {
  parent: account
  name: m.name
  sku: { name: m.sku, capacity: m.capacity }
  properties: {
    model: { name: m.name, format: m.format, version: m.version }
  }
}]

module pe '../pe.bicep' = {
  name: 'pe-${name}'
  params: {
    name: 'pe-${name}-account'
    location: location
    tags: tags
    subnetId: peSubnetId
    targetId: account.id
    groupId: 'account'
    zoneIds: zoneIds
  }
}

resource ra 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for a in roleAssignments: {
  scope: account
  name: guid(account.id, a.principalId, a.roleId)
  properties: {
    principalId: a.principalId
    principalType: a.principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', a.roleId)
  }
}]

output name string = account.name
```

`infra/modules/foundry/project.bicep`:

```bicep
// Foundry 프로젝트와 BYO 리소스 연결 3개(스레드: Cosmos, 파일: Storage, 벡터: AI Search). 모두 Entra 인증.
param accountName string
param name string
param location string
param tags object
param cosmosName string
param storageName string
param searchName string

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: accountName
}

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' existing = {
  name: cosmosName
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageName
}

resource search 'Microsoft.Search/searchServices@2024-06-01-preview' existing = {
  name: searchName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' = {
  parent: account
  name: name
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    displayName: name
    description: 'Agentic cash-flow lending demo project'
  }

  resource cosmosConnection 'connections@2025-04-01-preview' = {
    name: cosmosName
    properties: {
      category: 'CosmosDB'
      target: cosmos.properties.documentEndpoint
      authType: 'AAD'
      metadata: { ApiType: 'Azure', ResourceId: cosmos.id, location: location }
    }
  }

  resource storageConnection 'connections@2025-04-01-preview' = {
    name: storageName
    properties: {
      category: 'AzureStorageAccount'
      target: storage.properties.primaryEndpoints.blob
      authType: 'AAD'
      metadata: { ApiType: 'Azure', ResourceId: storage.id, location: location }
    }
  }

  resource searchConnection 'connections@2025-04-01-preview' = {
    name: searchName
    properties: {
      category: 'CognitiveSearch'
      target: 'https://${searchName}.search.windows.net'
      authType: 'AAD'
      metadata: { ApiType: 'Azure', ResourceId: search.id, location: location }
    }
  }
}

// internalId(32자리 16진수)를 GUID 형식으로 바꿉니다. 사후 역할의 ABAC 조건과 Cosmos 역할 이름에 쓰입니다.
// substring 대신 take/skip을 써서, 값이 비어 있는 오프라인 펼치기(PSRule)에서도 실패하지 않게 합니다.
var internalId = project.properties.internalId
var workspaceGuid = '${take(internalId, 8)}-${take(skip(internalId, 8), 4)}-${take(skip(internalId, 12), 4)}-${take(skip(internalId, 16), 4)}-${skip(internalId, 20)}'

output name string = project.name
output principalId string = project.identity.principalId
output workspaceId string = workspaceGuid
output cosmosConnection string = project::cosmosConnection.name
output storageConnection string = project::storageConnection.name
output searchConnection string = project::searchConnection.name
```

`infra/modules/foundry/roles-pre.bicep`:

```bicep
// capability host 생성 전에 필요한 프로젝트 관리 ID 역할(샘플 15와 동일).
param projectPrincipalId string
param cosmosName string
param storageName string
param searchName string

var cosmosOperator = '230815da-be43-4aae-9cb4-875f7bd000aa'
var blobContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var searchRoles = [
  '8ebe5a00-799e-43f5-93ac-243d3dce84a7' // Search Index Data Contributor
  '7ca78c08-252a-4471-8644-bb5ff32d4ba0' // Search Service Contributor
]

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' existing = {
  name: cosmosName
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageName
}

resource search 'Microsoft.Search/searchServices@2024-06-01-preview' existing = {
  name: searchName
}

resource cosmosRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: cosmos
  name: guid(cosmos.id, projectPrincipalId, cosmosOperator)
  properties: {
    principalId: projectPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cosmosOperator)
  }
}

resource storageRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, projectPrincipalId, blobContributor)
  properties: {
    principalId: projectPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobContributor)
  }
}

resource searchRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for r in searchRoles: {
  scope: search
  name: guid(search.id, projectPrincipalId, r)
  properties: {
    principalId: projectPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', r)
  }
}]
```

`infra/modules/foundry/caphost.bicep`:

```bicep
// 프로젝트 capability host. 같은 이름으로 다시 PUT하면 실패할 수 있으므로 최초 배포에서만 만듭니다(deployCapabilityHost).
param accountName string
param projectName string
param name string
param cosmosConnection string
param storageConnection string
param searchConnection string

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: accountName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' existing = {
  parent: account
  name: projectName
}

resource caphost 'Microsoft.CognitiveServices/accounts/projects/capabilityHosts@2025-04-01-preview' = {
  parent: project
  name: name
  properties: {
    // Bicep 형식 정의에는 없지만 ARM API는 이 값을 요구합니다(샘플 15 주석 참고).
    #disable-next-line BCP037
    capabilityHostKind: 'Agents'
    threadStorageConnections: [ cosmosConnection ]
    storageConnections: [ storageConnection ]
    vectorStoreConnections: [ searchConnection ]
  }
}

output name string = caphost.name
```

`infra/modules/foundry/roles-post.bicep`:

```bicep
// capability host 생성 후 역할(샘플 15와 동일).
// - Foundry BYO Storage: 이 프로젝트의 에이전트 컨테이너(<workspaceId>*-azureml-agent)에만 Blob Data Owner(ABAC 조건)
// - Cosmos: caphost가 만든 enterprise_memory DB에 SQL 기본 제공 Data Contributor
param projectPrincipalId string
param workspaceId string
param cosmosName string
param storageName string

var blobOwner = 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b'
var cosmosContributor = '00000000-0000-0000-0000-000000000002'
var condition = '((!(ActionMatches{\'Microsoft.Storage/storageAccounts/blobServices/containers/blobs/tags/read\'})  AND  !(ActionMatches{\'Microsoft.Storage/storageAccounts/blobServices/containers/blobs/filter/action\'}) AND  !(ActionMatches{\'Microsoft.Storage/storageAccounts/blobServices/containers/blobs/tags/write\'}) ) OR (@Resource[Microsoft.Storage/storageAccounts/blobServices/containers:name] StringStartsWithIgnoreCase \'${workspaceId}\' AND @Resource[Microsoft.Storage/storageAccounts/blobServices/containers:name] StringLikeIgnoreCase \'*-azureml-agent\'))'

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageName
}

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' existing = {
  name: cosmosName
}

resource blobOwnerRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, projectPrincipalId, blobOwner, workspaceId)
  properties: {
    principalId: projectPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobOwner)
    conditionVersion: '2.0'
    condition: condition
  }
}

resource threadStoreRole 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = {
  parent: cosmos
  name: guid(workspaceId, cosmosName, cosmosContributor, projectPrincipalId)
  properties: {
    principalId: projectPrincipalId
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/${cosmosContributor}'
    scope: '${cosmos.id}/dbs/enterprise_memory'
  }
}
```

- [ ] **Step 4: resources.bicep에 T4 섹션 추가**

```bicep
// ── Foundry (T4) ────────────────────────────────────────────────────────
module aiAccount 'modules/foundry/account.bicep' = {
  name: 'foundry-account'
  params: {
    name: names.ais
    location: location
    tags: tags
    operatorIp: operatorIp
    agentSubnetId: subnetId['snet-agents']
    peSubnetId: subnetId['snet-pe']
    zoneIds: [
      zoneId['privatelink.services.ai.azure.com']
      zoneId['privatelink.openai.azure.com']
      zoneId['privatelink.cognitiveservices.azure.com']
    ]
    modelDeployments: modelDeployments
    roleAssignments: concat([
      { principalId: identities.outputs.orchestratorPrincipalId, roleId: role.foundryUser, principalType: sp }
      { principalId: identities.outputs.orchestratorPrincipalId, roleId: role.openAiUser, principalType: sp }
      { principalId: identities.outputs.smokePrincipalId, roleId: role.foundryUser, principalType: sp }
      { principalId: identities.outputs.smokePrincipalId, roleId: role.openAiUser, principalType: sp }
    ], hasOperator ? [
      { principalId: operatorPrincipalId, roleId: role.foundryUser, principalType: 'User' }
      { principalId: operatorPrincipalId, roleId: role.openAiUser, principalType: 'User' }
    ] : [])
  }
}

// cosmos/stAgents/search 출력을 쓰므로 각 모듈(PE 포함)이 끝난 뒤 프로젝트가 만들어집니다.
module aiProject 'modules/foundry/project.bicep' = {
  name: 'foundry-project'
  params: {
    accountName: aiAccount.outputs.name
    name: names.project
    location: location
    tags: tags
    cosmosName: cosmos.outputs.name
    storageName: stAgents.outputs.name
    searchName: search.outputs.name
  }
}

module aiRolesPre 'modules/foundry/roles-pre.bicep' = {
  name: 'foundry-roles-pre'
  params: {
    projectPrincipalId: aiProject.outputs.principalId
    cosmosName: cosmos.outputs.name
    storageName: stAgents.outputs.name
    searchName: search.outputs.name
  }
}

module aiCapHost 'modules/foundry/caphost.bicep' = if (deployCapabilityHost) {
  name: 'foundry-caphost'
  params: {
    accountName: aiAccount.outputs.name
    projectName: aiProject.outputs.name
    name: names.projectCapHost
    cosmosConnection: aiProject.outputs.cosmosConnection
    storageConnection: aiProject.outputs.storageConnection
    searchConnection: aiProject.outputs.searchConnection
  }
  dependsOn: [ aiRolesPre ]
}

module aiRolesPost 'modules/foundry/roles-post.bicep' = if (deployCapabilityHost) {
  name: 'foundry-roles-post'
  params: {
    projectPrincipalId: aiProject.outputs.principalId
    workspaceId: aiProject.outputs.workspaceId
    cosmosName: cosmos.outputs.name
    storageName: stAgents.outputs.name
  }
  dependsOn: [ aiCapHost ]
}

output AI_ACCOUNT_NAME string = aiAccount.outputs.name
output AI_PROJECT_NAME string = aiProject.outputs.name
output AI_PROJECT_ENDPOINT string = 'https://${names.ais}.services.ai.azure.com/api/projects/${names.project}'
output AI_OPENAI_ENDPOINT string = 'https://${names.ais}.openai.azure.com/'
output FOUNDRY_PROJECT_CAPHOST string = names.projectCapHost
```

`infra/main.bicep` 끝에 추가:

```bicep
output AI_ACCOUNT_NAME string = resources.outputs.AI_ACCOUNT_NAME
output AI_PROJECT_NAME string = resources.outputs.AI_PROJECT_NAME
output AI_PROJECT_ENDPOINT string = resources.outputs.AI_PROJECT_ENDPOINT
output AI_OPENAI_ENDPOINT string = resources.outputs.AI_OPENAI_ENDPOINT
output FOUNDRY_PROJECT_CAPHOST string = resources.outputs.FOUNDRY_PROJECT_CAPHOST
```

- [ ] **Step 5: 통과 확인**

Run: `$env:RUN_INFRA_TESTS = '1'; .\.venv\Scripts\python -m pytest tests/infra -q`
Expected: 전부 PASS. `BCP036`/`BCP037` 경고는 샘플과 같은 이유로 억제되어 있다(다른 Bicep 형식 경고는 오류가 아니므로 무시). 펼친 결과에서 `workspaceId`가 `----`처럼 보이는 것은 정상이다(오프라인에서는 `internalId`가 비어 있음).

- [ ] **Step 6: 커밋**

```powershell
git add infra tests/infra
git commit -m "feat(infra): foundry account, model deployments, project and private agent capability host" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`nCopilot-Session: 05f1ca1b-6073-417d-bfb1-6b546f82cb58"
```

---

### Task 5: Service Bus Premium(pause 가능)

**Files:**
- Create: `infra/modules/servicebus.bicep`
- Modify: `infra/resources.bicep`(T5 섹션), `infra/main.bicep`(출력), `tests/infra/test_window.py`(행 추가)
- Test: `tests/infra/test_messaging.py`

**Interfaces:**
- Consumes: T2 `pe.bicep`, 역할 할당 규약, `identities.outputs.*PrincipalId`.
- Produces:
  - 토픽: `consent`, `flow.updated`, `alert.raised`, `grievance`(구독은 P3).
  - PE 이름 규칙 `pe-<네임스페이스>-namespace`(T9 `pause`가 이 이름으로 삭제).
  - azd 출력: `SERVICEBUS_NAME`(항상 네임스페이스 이름, 운영 도구용), `SERVICEBUS_FQDN`(pause 상태면 빈 문자열 — P3 앱은 이 값이 비면 이벤트 발행을 건너뛴다).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/infra/test_messaging.py`:

```python
SB = "Microsoft.ServiceBus/namespaces"
ROLE_SB_SENDER = "69a216fc-b8fb-44d8-bc22-1f3c2cd27a39"
ROLE_SB_RECEIVER = "4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0"


def test_service_bus_is_premium_keyless_and_private(closed):
    sb = closed.one(SB, "sb-")
    assert sb["sku"] == {"name": "Premium", "tier": "Premium", "capacity": 1}
    assert sb["properties"]["disableLocalAuth"] is True
    rules = closed.one(f"{SB}/networkRuleSets")
    assert rules["properties"]["publicNetworkAccess"] == "Disabled"
    assert rules["properties"]["defaultAction"] == "Deny"
    assert closed.private_endpoints("sb-") == {"namespace": {"privatelink.servicebus.windows.net"}}
    [pe] = [p for p in closed.of_type("Microsoft.Network/privateEndpoints") if p["name"].startswith("pe-sb-")]
    assert pe["name"] == f"pe-{sb['name']}-namespace"


def test_service_bus_topics(closed):
    topics = {t["name"].split("/")[-1] for t in closed.of_type(f"{SB}/topics")}
    assert topics == {"consent", "flow.updated", "alert.raised", "grievance"}


def test_service_bus_roles(closed):
    assert closed.roles_on("sb-") == {ROLE_SB_SENDER, ROLE_SB_RECEIVER}


def test_paused_environment_has_no_service_bus(variant):
    assert variant.of_type(SB) == []
    assert variant.private_endpoints("sb-") == {}
```

`tests/infra/test_window.py`의 `ALWAYS_PRIVATE` 끝에 행 추가:

```python
    ("Microsoft.ServiceBus/namespaces", "sb-"),
```

- [ ] **Step 2: 실패 확인**

Run: `$env:RUN_INFRA_TESTS = '1'; .\.venv\Scripts\python -m pytest tests/infra/test_messaging.py tests/infra/test_window.py -q`
Expected: FAIL — `Microsoft.ServiceBus/namespaces 'sb-': []`.

- [ ] **Step 3: 모듈 작성**

`infra/modules/servicebus.bicep`:

```bicep
// Service Bus Premium(Private Endpoint는 Premium만 지원). 월 약 $677이므로 쓰지 않을 때는 pause(삭제)합니다.
param name string
param location string
param tags object
param peSubnetId string
param zoneId string
param topics array
@description('[{ principalId, roleId, principalType }]')
param roleAssignments array = []

resource sb 'Microsoft.ServiceBus/namespaces@2024-01-01' = {
  name: name
  location: location
  tags: tags
  sku: { name: 'Premium', tier: 'Premium', capacity: 1 }
  properties: {
    disableLocalAuth: true
    publicNetworkAccess: 'Disabled'
    minimumTlsVersion: '1.2'
  }
}

resource networkRules 'Microsoft.ServiceBus/namespaces/networkRuleSets@2024-01-01' = {
  parent: sb
  name: 'default'
  properties: {
    publicNetworkAccess: 'Disabled'
    defaultAction: 'Deny'
    trustedServiceAccessEnabled: false
    ipRules: []
    virtualNetworkRules: []
  }
}

// 중복 감지: 같은 MessageId 재발행(재시도)을 10분 동안 무시해 이벤트 소비를 멱등하게 돕습니다.
resource topicResources 'Microsoft.ServiceBus/namespaces/topics@2024-01-01' = [for t in topics: {
  parent: sb
  name: t
  properties: {
    defaultMessageTimeToLive: 'P14D'
    requiresDuplicateDetection: true
    duplicateDetectionHistoryTimeWindow: 'PT10M'
  }
}]

module pe 'pe.bicep' = {
  name: 'pe-${name}'
  params: {
    name: 'pe-${name}-namespace'
    location: location
    tags: tags
    subnetId: peSubnetId
    targetId: sb.id
    groupId: 'namespace'
    zoneIds: [ zoneId ]
  }
}

resource ra 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for a in roleAssignments: {
  scope: sb
  name: guid(sb.id, a.principalId, a.roleId)
  properties: {
    principalId: a.principalId
    principalType: a.principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', a.roleId)
  }
}]
```

- [ ] **Step 4: resources.bicep에 T5 섹션 추가**

```bicep
// ── Service Bus (T5) ────────────────────────────────────────────────────
module serviceBus 'modules/servicebus.bicep' = if (serviceBusEnabled) {
  name: 'servicebus'
  params: {
    name: names.sb
    location: location
    tags: tags
    peSubnetId: subnetId['snet-pe']
    zoneId: zoneId['privatelink.servicebus.windows.net']
    topics: [ 'consent', 'flow.updated', 'alert.raised', 'grievance' ]
    roleAssignments: [
      { principalId: identities.outputs.orchestratorPrincipalId, roleId: role.sbSender, principalType: sp }
      { principalId: identities.outputs.orchestratorPrincipalId, roleId: role.sbReceiver, principalType: sp }
      { principalId: identities.outputs.appsPrincipalId, roleId: role.sbSender, principalType: sp }
      { principalId: identities.outputs.smokePrincipalId, roleId: role.sbSender, principalType: sp }
    ]
  }
}

// 조건부 모듈의 출력을 참조하지 않도록 이름으로 계산합니다.
output SERVICEBUS_NAME string = names.sb
output SERVICEBUS_FQDN string = serviceBusEnabled ? '${names.sb}.servicebus.windows.net' : ''
```

`infra/main.bicep` 끝에 추가:

```bicep
output SERVICEBUS_NAME string = resources.outputs.SERVICEBUS_NAME
output SERVICEBUS_FQDN string = resources.outputs.SERVICEBUS_FQDN
```

- [ ] **Step 5: 통과 확인**

Run: `$env:RUN_INFRA_TESTS = '1'; .\.venv\Scripts\python -m pytest tests/infra -q`
Expected: 전부 PASS.

- [ ] **Step 6: 커밋**

```powershell
git add infra tests/infra
git commit -m "feat(infra): pausable service bus premium with private endpoint and topics" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`nCopilot-Session: 05f1ca1b-6073-417d-bfb1-6b546f82cb58"
```

---

### Task 6: ACR과 Container Apps 환경 2개(internal + 콘솔용 external)

internal ingress(`external: false`) 앱은 같은 환경 안의 앱만 호출할 수 있다. Functions 오케스트레이터(snet-functions)가 Policy Engine·Mock API를 호출해야 하므로 이들은 **internal 환경**(`internal: true`, 내부 부하 분산기)에 두고 앱 ingress는 `external: true`(= 환경 진입점 노출, 환경 자체가 VNet 전용)로 연다(P3). 환경 기본 도메인은 Private DNS zone의 와일드카드 A 레코드로 VNet에서 해석한다. 콘솔은 **external 환경**에 따로 둔다.

**Files:**
- Create: `infra/modules/acr.bicep`, `infra/modules/aca-env.bicep`, `infra/modules/aca-dns.bicep`
- Modify: `infra/resources.bicep`(T6 섹션), `infra/main.bicep`(출력), `tests/infra/test_window.py`(행 추가)
- Test: `tests/infra/test_compute.py`

**Interfaces:**
- Consumes: T2 `monitoring.outputs.lawName`, `identities.outputs.appsPrincipalId`, T1 `network.outputs.vnetId`, `subnetId['snet-apps-int']`, `subnetId['snet-apps-web']`.
- Produces:
  - 모듈 심볼 `acaInt`, `acaWeb`(출력 `id`, `name`, `defaultDomain`, `staticIp`), `acr`(출력 `name`, `loginServer`).
  - azd 출력: `AZURE_CONTAINER_REGISTRY_ENDPOINT`(azd 컨테이너 배포 규약), `ACR_NAME`, `CONTAINER_APPS_INT_ENVIRONMENT_ID`, `CONTAINER_APPS_INT_ENVIRONMENT_NAME`, `CONTAINER_APPS_INT_DOMAIN`, `CONTAINER_APPS_WEB_ENVIRONMENT_ID`, `CONTAINER_APPS_WEB_ENVIRONMENT_NAME`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/infra/test_compute.py`:

```python
ACR = "Microsoft.ContainerRegistry/registries"
CAE = "Microsoft.App/managedEnvironments"
ROLE_ACR_PULL = "7f951dda-4ed3-4680-a7ca-43fe172d538d"
ROLE_ACR_PUSH = "8311e382-0749-4cb8-b61a-304f252e45ec"


def test_acr_is_premium_private_without_admin_user(closed):
    acr = closed.one(ACR, "acr")
    assert acr["sku"]["name"] == "Premium"
    assert acr["properties"]["adminUserEnabled"] is False
    assert acr["properties"]["networkRuleSet"]["defaultAction"] == "Deny"
    assert closed.private_endpoints("acr") == {"registry": {"privatelink.azurecr.io"}}
    assert closed.roles_on("acr") == {ROLE_ACR_PULL, ROLE_ACR_PUSH}


def test_acr_build_window_opens_registry(variant):
    # az acr build 에이전트는 IP가 고정되지 않으므로 빌드 창 동안만 전체 공개합니다(스펙 §2.2).
    props = variant.one(ACR, "acr")["properties"]
    assert props["publicNetworkAccess"] == "Enabled"
    assert props["networkRuleSet"]["defaultAction"] == "Allow"
    assert variant.roles_on("acr") == {ROLE_ACR_PULL}


def test_two_container_apps_environments(closed):
    envs = {e["name"]: e["properties"] for e in closed.of_type(CAE)}
    assert set(envs) == {"cae-int-amatest", "cae-web-amatest"}
    internal, web = envs["cae-int-amatest"], envs["cae-web-amatest"]
    assert internal["vnetConfiguration"]["internal"] is True
    assert internal["vnetConfiguration"]["infrastructureSubnetId"].endswith("/subnets/snet-apps-int")
    assert web["vnetConfiguration"]["internal"] is False
    assert web["vnetConfiguration"]["infrastructureSubnetId"].endswith("/subnets/snet-apps-web")
    for props in (internal, web):
        assert props["workloadProfiles"] == [{"name": "Consumption", "workloadProfileType": "Consumption"}]
        assert props["appLogsConfiguration"]["destination"] == "log-analytics"


def test_internal_environment_domain_resolves_in_vnet(closed):
    records = closed.of_type("Microsoft.Network/privateDnsZones/A")
    assert sorted(r["name"].rsplit("/", 1)[-1] for r in records) == ["*", "@"]
    aca_links = [link for link in closed.of_type("Microsoft.Network/privateDnsZones/virtualNetworkLinks")
                 if not link["name"].startswith("privatelink.")]
    assert len(aca_links) == 1
```

`tests/infra/test_window.py`의 `ALWAYS_PRIVATE` 끝에 행 추가:

```python
    ("Microsoft.ContainerRegistry/registries", "acr"),
```

- [ ] **Step 2: 실패 확인**

Run: `$env:RUN_INFRA_TESTS = '1'; .\.venv\Scripts\python -m pytest tests/infra/test_compute.py tests/infra/test_window.py -q`
Expected: FAIL — `Microsoft.ContainerRegistry/registries 'acr': []`.

- [ ] **Step 3: 모듈 작성**

`infra/modules/acr.bicep`:

```bicep
// ACR Premium(Private Endpoint 지원). 이미지는 az acr build로 만들므로 빌드 창(publicAccess)에서만 공개합니다.
param name string
param location string
param tags object
param publicAccess bool
param peSubnetId string
param zoneId string
@description('[{ principalId, roleId, principalType }]')
param roleAssignments array = []

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: name
  location: location
  tags: tags
  sku: { name: 'Premium' }
  properties: {
    adminUserEnabled: false
    anonymousPullEnabled: false
    publicNetworkAccess: publicAccess ? 'Enabled' : 'Disabled'
    networkRuleSet: {
      defaultAction: publicAccess ? 'Allow' : 'Deny'
      ipRules: []
    }
    networkRuleBypassOptions: 'AzureServices'
    zoneRedundancy: 'Disabled'
  }
}

module pe 'pe.bicep' = {
  name: 'pe-${name}'
  params: {
    name: 'pe-${name}-registry'
    location: location
    tags: tags
    subnetId: peSubnetId
    targetId: acr.id
    groupId: 'registry'
    zoneIds: [ zoneId ]
  }
}

resource ra 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for a in roleAssignments: {
  scope: acr
  name: guid(acr.id, a.principalId, a.roleId)
  properties: {
    principalId: a.principalId
    principalType: a.principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', a.roleId)
  }
}]

output name string = acr.name
output loginServer string = acr.properties.loginServer
```

`infra/modules/aca-env.bicep`:

```bicep
// VNet 통합 Container Apps 환경(workload profile, Consumption 전용 — 환경 기본 요금 없음).
param name string
param location string
param tags object
param subnetId string
@description('true: 내부 부하 분산기만(VNet 전용), false: 공인 진입점')
param internal bool
param lawName string

resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: lawName
}

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    vnetConfiguration: {
      infrastructureSubnetId: subnetId
      internal: internal
    }
    workloadProfiles: [
      { name: 'Consumption', workloadProfileType: 'Consumption' }
    ]
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: law.properties.customerId
        sharedKey: law.listKeys().primarySharedKey
      }
    }
    zoneRedundant: false
  }
}

output id string = env.id
output name string = env.name
output defaultDomain string = env.properties.defaultDomain
output staticIp string = env.properties.staticIp
```

`infra/modules/aca-dns.bicep`:

```bicep
// internal Container Apps 환경 기본 도메인을 VNet에서 해석하기 위한 Private DNS zone.
// zone 이름이 배포 중에 정해지므로(환경의 defaultDomain) 별도 모듈 파라미터로 받습니다.
param domain string
param staticIp string
param vnetId string
param tags object

resource zone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: domain
  location: 'global'
  tags: tags
}

resource link 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: zone
  name: 'link-vnet'
  location: 'global'
  tags: tags
  properties: {
    registrationEnabled: false
    virtualNetwork: { id: vnetId }
  }
}

resource wildcard 'Microsoft.Network/privateDnsZones/A@2024-06-01' = {
  parent: zone
  name: '*'
  properties: {
    ttl: 300
    aRecords: [ { ipv4Address: staticIp } ]
  }
}

resource apex 'Microsoft.Network/privateDnsZones/A@2024-06-01' = {
  parent: zone
  name: '@'
  properties: {
    ttl: 300
    aRecords: [ { ipv4Address: staticIp } ]
  }
}
```

- [ ] **Step 4: resources.bicep에 T6 섹션 추가**

```bicep
// ── ACR, Container Apps 환경 (T6) ──────────────────────────────────────
module acr 'modules/acr.bicep' = {
  name: 'acr'
  params: {
    name: names.acr
    location: location
    tags: tags
    publicAccess: acrPublic
    peSubnetId: subnetId['snet-pe']
    zoneId: zoneId['privatelink.azurecr.io']
    roleAssignments: concat([
      { principalId: identities.outputs.appsPrincipalId, roleId: role.acrPull, principalType: sp }
    ], hasOperator ? [
      { principalId: operatorPrincipalId, roleId: role.acrPush, principalType: 'User' }
    ] : [])
  }
}

module acaInt 'modules/aca-env.bicep' = {
  name: 'aca-int'
  params: {
    name: names.caeInt
    location: location
    tags: tags
    subnetId: subnetId['snet-apps-int']
    internal: true
    lawName: monitoring.outputs.lawName
  }
}

module acaIntDns 'modules/aca-dns.bicep' = {
  name: 'aca-int-dns'
  params: {
    domain: acaInt.outputs.defaultDomain
    staticIp: acaInt.outputs.staticIp
    vnetId: network.outputs.vnetId
    tags: tags
  }
}

module acaWeb 'modules/aca-env.bicep' = {
  name: 'aca-web'
  params: {
    name: names.caeWeb
    location: location
    tags: tags
    subnetId: subnetId['snet-apps-web']
    internal: false
    lawName: monitoring.outputs.lawName
  }
}

output AZURE_CONTAINER_REGISTRY_ENDPOINT string = acr.outputs.loginServer
output ACR_NAME string = acr.outputs.name
output CONTAINER_APPS_INT_ENVIRONMENT_ID string = acaInt.outputs.id
output CONTAINER_APPS_INT_ENVIRONMENT_NAME string = acaInt.outputs.name
output CONTAINER_APPS_INT_DOMAIN string = acaInt.outputs.defaultDomain
output CONTAINER_APPS_WEB_ENVIRONMENT_ID string = acaWeb.outputs.id
output CONTAINER_APPS_WEB_ENVIRONMENT_NAME string = acaWeb.outputs.name
```

`infra/main.bicep` 끝에 추가:

```bicep
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = resources.outputs.AZURE_CONTAINER_REGISTRY_ENDPOINT
output ACR_NAME string = resources.outputs.ACR_NAME
output CONTAINER_APPS_INT_ENVIRONMENT_ID string = resources.outputs.CONTAINER_APPS_INT_ENVIRONMENT_ID
output CONTAINER_APPS_INT_ENVIRONMENT_NAME string = resources.outputs.CONTAINER_APPS_INT_ENVIRONMENT_NAME
output CONTAINER_APPS_INT_DOMAIN string = resources.outputs.CONTAINER_APPS_INT_DOMAIN
output CONTAINER_APPS_WEB_ENVIRONMENT_ID string = resources.outputs.CONTAINER_APPS_WEB_ENVIRONMENT_ID
output CONTAINER_APPS_WEB_ENVIRONMENT_NAME string = resources.outputs.CONTAINER_APPS_WEB_ENVIRONMENT_NAME
```

- [ ] **Step 5: 통과 확인**

Run: `$env:RUN_INFRA_TESTS = '1'; .\.venv\Scripts\python -m pytest tests/infra -q`
Expected: 전부 PASS. 오프라인 펼치기에서는 `defaultDomain`·`staticIp`가 자리표시 값이므로 zone 이름이 실제 도메인과 다를 수 있다. 테스트가 A 레코드 이름(`*`, `@`)과 링크 수만 확인하는 이유다. PSRule이 빈 zone 이름 때문에 펼치기에 실패하면 `aca-dns` 모듈 호출에만 `domain: empty(acaInt.outputs.defaultDomain) ? 'pending.invalid' : acaInt.outputs.defaultDomain`을 적용한다(실배포에서는 항상 값이 있음).

- [ ] **Step 6: 커밋**

```powershell
git add infra tests/infra
git commit -m "feat(infra): acr premium and split internal/web container apps environments" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`nCopilot-Session: 05f1ca1b-6073-417d-bfb1-6b546f82cb58"
```

### Task 7: Flex Consumption Function App(오케스트레이터 호스트)

**Files:**
- Create: `infra/modules/functions.bicep`
- Modify: `infra/resources.bicep`(T7 섹션), `infra/main.bicep`(출력), `tests/infra/test_window.py`(행 추가)
- Test: `tests/infra/test_functions.py`

**Interfaces:**
- Consumes: T3 `stFunc.outputs.name`·`stFunc.outputs.blobEndpoint`(컨테이너 `deploymentpackage`와 Durable 역할 포함), T2 `identities.outputs.orchestratorId`·`orchestratorClientId`, `monitoring.outputs.appiConnectionString`, T1 `subnetId['snet-functions']`.
- Produces:
  - Function App 태그 `azd-service-name: orchestrator`(P3 `azure.yaml` 서비스 이름).
  - 앱 설정 규약(P3 코드가 사용): `AzureWebJobsStorage__accountName`/`__credential`/`__clientId`(관리 ID 기반 호스트 저장소, Durable Azure Storage 백엔드도 이 연결 사용), `AZURE_CLIENT_ID`, `APPLICATIONINSIGHTS_CONNECTION_STRING`, `APPLICATIONINSIGHTS_AUTHENTICATION_STRING`. P3는 나머지 앱 설정(엔드포인트)을 이 배열에 추가한다.
  - azd 출력: `FUNCTION_APP_NAME`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/infra/test_functions.py`:

```python
SITE = "Microsoft.Web/sites"


def _settings(site: dict) -> dict[str, str]:
    return {s["name"]: s["value"] for s in site["properties"]["siteConfig"]["appSettings"]}


def test_flex_consumption_plan(closed):
    plan = closed.one("Microsoft.Web/serverfarms", "plan-")
    assert plan["sku"] == {"name": "FC1", "tier": "FlexConsumption"}
    assert plan["properties"]["reserved"] is True


def test_function_app_runtime_identity_and_vnet(closed):
    site = closed.one(SITE, "func-")
    props = site["properties"]
    assert site["kind"] == "functionapp,linux"
    assert site["tags"]["azd-service-name"] == "orchestrator"
    assert list(site["identity"]["userAssignedIdentities"])[0].endswith("/id-orch-amatest")
    assert props["virtualNetworkSubnetId"].endswith("/subnets/snet-functions")
    assert props["httpsOnly"] is True
    config = props["functionAppConfig"]
    assert config["runtime"] == {"name": "python", "version": "3.12"}
    storage = config["deployment"]["storage"]
    assert storage["value"].endswith("deploymentpackage")
    assert storage["authentication"]["type"] == "UserAssignedIdentity"


def test_function_app_uses_identity_based_host_storage(closed):
    settings = _settings(closed.one(SITE, "func-"))
    assert settings["AzureWebJobsStorage__accountName"].startswith("stfn")
    assert settings["AzureWebJobsStorage__credential"] == "managedidentity"
    assert "AzureWebJobsStorage" not in settings  # 연결 문자열(공유 키) 금지
    assert "FUNCTIONS_WORKER_RUNTIME" not in settings  # Flex는 functionAppConfig.runtime 사용
    assert settings["APPLICATIONINSIGHTS_AUTHENTICATION_STRING"].endswith(";Authorization=AAD")


def test_function_app_private_endpoint(closed):
    assert closed.private_endpoints("func-") == {"sites": {"privatelink.azurewebsites.net"}}
```

`tests/infra/test_window.py`의 `WINDOWED` 끝에 행 추가:

```python
    ("Microsoft.Web/sites", "func-"),
```

- [ ] **Step 2: 실패 확인**

Run: `$env:RUN_INFRA_TESTS = '1'; .\.venv\Scripts\python -m pytest tests/infra/test_functions.py tests/infra/test_window.py -q`
Expected: FAIL — `Microsoft.Web/serverfarms 'plan-': []`.

- [ ] **Step 3: 모듈 작성**

`infra/modules/functions.bicep`:

```bicep
// Flex Consumption Function App(Linux, Python 3.12). 아웃바운드는 snet-functions VNet 통합,
// 인바운드는 Private Endpoint. 코드 배포(azd deploy)는 배포 창에서 운영자 IP로만 합니다.
param planName string
param name string
param location string
param tags object
param operatorIp string
param identityId string
param identityClientId string
param subnetId string
param peSubnetId string
param zoneId string
param storageName string
param storageBlobEndpoint string
param appInsightsConnectionString string

var windowOpen = !empty(operatorIp)

resource plan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: planName
  location: location
  tags: tags
  kind: 'functionapp'
  sku: { name: 'FC1', tier: 'FlexConsumption' }
  properties: { reserved: true }
}

resource site 'Microsoft.Web/sites@2024-04-01' = {
  name: name
  location: location
  tags: union(tags, { 'azd-service-name': 'orchestrator' })
  kind: 'functionapp,linux'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identityId}': {} }
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    virtualNetworkSubnetId: subnetId
    publicNetworkAccess: windowOpen ? 'Enabled' : 'Disabled'
    siteConfig: {
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      ipSecurityRestrictionsDefaultAction: 'Deny'
      ipSecurityRestrictions: windowOpen ? [
        { name: 'operator', ipAddress: '${operatorIp}/32', action: 'Allow', priority: 100 }
      ] : []
      scmIpSecurityRestrictionsUseMain: true
      appSettings: [
        { name: 'AzureWebJobsStorage__accountName', value: storageName }
        { name: 'AzureWebJobsStorage__credential', value: 'managedidentity' }
        { name: 'AzureWebJobsStorage__clientId', value: identityClientId }
        { name: 'AZURE_CLIENT_ID', value: identityClientId }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
        { name: 'APPLICATIONINSIGHTS_AUTHENTICATION_STRING', value: 'ClientId=${identityClientId};Authorization=AAD' }
      ]
    }
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${storageBlobEndpoint}deploymentpackage'
          authentication: {
            type: 'UserAssignedIdentity'
            userAssignedIdentityResourceId: identityId
          }
        }
      }
      scaleAndConcurrency: {
        maximumInstanceCount: 40
        instanceMemoryMB: 2048
      }
      runtime: { name: 'python', version: '3.12' }
    }
  }
}

module pe 'pe.bicep' = {
  name: 'pe-${name}'
  params: {
    name: 'pe-${name}-sites'
    location: location
    tags: tags
    subnetId: peSubnetId
    targetId: site.id
    groupId: 'sites'
    zoneIds: [ zoneId ]
  }
}

output name string = site.name
```

- [ ] **Step 4: resources.bicep에 T7 섹션 추가**

```bicep
// ── Functions (T7) ─────────────────────────────────────────────────────
module functions 'modules/functions.bicep' = {
  name: 'functions'
  params: {
    planName: names.plan
    name: names.func
    location: location
    tags: tags
    operatorIp: operatorIp
    identityId: identities.outputs.orchestratorId
    identityClientId: identities.outputs.orchestratorClientId
    subnetId: subnetId['snet-functions']
    peSubnetId: subnetId['snet-pe']
    zoneId: zoneId['privatelink.azurewebsites.net']
    storageName: stFunc.outputs.name
    storageBlobEndpoint: stFunc.outputs.blobEndpoint
    appInsightsConnectionString: monitoring.outputs.appiConnectionString
  }
}

output FUNCTION_APP_NAME string = functions.outputs.name
```

`infra/main.bicep` 끝에 추가:

```bicep
output FUNCTION_APP_NAME string = resources.outputs.FUNCTION_APP_NAME
```

- [ ] **Step 5: 통과 확인**

Run: `$env:RUN_INFRA_TESTS = '1'; .\.venv\Scripts\python -m pytest tests/infra -q`
Expected: 전부 PASS.

- [ ] **Step 6: 커밋**

```powershell
git add infra tests/infra
git commit -m "feat(infra): flex consumption function app with vnet integration and private endpoint" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`nCopilot-Session: 05f1ca1b-6073-417d-bfb1-6b546f82cb58"
```

---

### Task 8: VNet 안 smoke 점검(스크립트 + Container Apps Job)

배포 창이 닫힌 평상시 상태에서 "VNet 안에서는 모든 데이터 평면이 Private Endpoint로 동작한다"를 증명한다. 점검 스크립트는 표준 라이브러리만 써서 MCR Python 기본 이미지에서 `python3 -c`로 바로 실행한다(이미지 빌드 불필요).

**Files:**
- Create: `src/ops/__init__.py`, `src/ops/smoke_checks.py`, `infra/modules/smoke-job.bicep`
- Modify: `pyproject.toml`(ops 패키지 등록), `infra/resources.bicep`(T8 섹션), `infra/main.bicep`(출력)
- Test: `tests/ops/test_smoke_checks.py`, `tests/infra/test_smoke_job.py`

**Interfaces:**
- Consumes: T1~T7의 `names`, `storageSuffix`, `modelDeployments`, `serviceBusEnabled`, `acaInt.outputs.id`, `identities.outputs.smokeId`·`smokeClientId`. smoke ID 역할: stdat Blob Reader(T3), Cosmos 계정 Data Reader(T3), Key Vault Secrets User(T2), Service Bus Sender(T5), AI Services Foundry User + OpenAI User(T4).
- Produces:
  - `ops.smoke_checks.main(environ=None, http=urllib_http, resolver=socket.gethostbyname, sleep=time.sleep, out=None) -> int`(0 성공, 1 실패). 출력: 점검마다 JSON 줄 `{"check","ok","detail","seconds"}`, 마지막 줄 `{"summary":{"total","failed"}}`.
  - SMOKE_CONFIG 키: `openai_host`, `project_endpoint`, `blob_host`, `cosmos_host`, `keyvault_host`, `servicebus_host`(pause면 `""`), `servicebus_topic`, `chat_models`, `agent_model`, `private_hosts`.
  - azd 출력: `SMOKE_JOB_NAME`.

- [ ] **Step 1: 실패하는 스크립트 테스트 작성**

`tests/ops/test_smoke_checks.py`:

```python
import ast
import io
import json
import sys
from pathlib import Path

import pytest

from ops import smoke_checks as sc

CFG = {
    "openai_host": "ais-x.openai.azure.com",
    "project_endpoint": "https://ais-x.services.ai.azure.com/api/projects/proj-lending",
    "blob_host": "stdatx.blob.core.windows.net",
    "cosmos_host": "cosmos-x.documents.azure.com",
    "keyvault_host": "kvx.vault.azure.net",
    "servicebus_host": "sb-x.servicebus.windows.net",
    "servicebus_topic": "flow.updated",
    "chat_models": ["gpt-5.4", "gpt-5.4-mini"],
    "agent_model": "gpt-5.4-mini",
    "private_hosts": ["ais-x.openai.azure.com", "kvx.vault.azure.net"],
}
ENV = {
    "SMOKE_CONFIG": json.dumps(CFG),
    "AZURE_CLIENT_ID": "cid",
    "IDENTITY_ENDPOINT": "http://localhost:42356/msi/token",
    "IDENTITY_HEADER": "secret",
}


def ok(obj=None, status=200):
    return sc.Response(status, json.dumps(obj or {}).encode())


def private(host):
    return "192.168.1.10"


class FakeHttp:
    """(method, URL 부분 문자열) → 응답 또는 응답 목록(순서대로 소비, 마지막 응답은 반복)."""

    def __init__(self, routes):
        self.routes = dict(routes)
        self.calls = []

    def __call__(self, method, url, headers=None, body=None):
        self.calls.append({"method": method, "url": url, "headers": headers or {}, "body": body})
        for (route_method, part), resp in self.routes.items():
            if route_method == method and part in url:
                if isinstance(resp, list):
                    return resp.pop(0) if len(resp) > 1 else resp[0]
                return resp
        raise AssertionError(f"예상하지 못한 요청: {method} {url}")

    def find(self, method, part):
        return [c for c in self.calls if c["method"] == method and part in c["url"]]


def healthy_routes():
    return {
        ("GET", "/msi/token"): ok({"access_token": "tok"}),
        ("POST", "/openai/v1/chat/completions"): ok({"model": "gpt"}),
        ("GET", "?comp=list"): ok(),
        ("GET", "/dbs"): ok({"Databases": [{"id": "lending"}]}),
        ("GET", "/secrets?"): ok({"value": []}),
        ("POST", "/flow.updated/messages"): ok(status=201),
        ("POST", "/assistants?"): ok({"id": "asst_1"}),
        ("POST", "/threads/runs?"): ok({"id": "run_1", "thread_id": "thread_1"}),
        ("GET", "/threads/thread_1/runs/run_1"): ok({"status": "completed"}),
        ("DELETE", "/assistants/asst_1"): ok(),
    }


def token_for(http):
    return sc.TokenProvider(http, "cid", "http://localhost:42356/msi/token", "secret")


def test_private_dns_rejects_public_ip():
    with pytest.raises(sc.CheckFailed, match="공인 IP"):
        sc.check_private_dns("kvx.vault.azure.net", lambda host: "20.40.60.80")
    assert sc.check_private_dns("kvx.vault.azure.net", private) == "192.168.1.10"


def test_token_provider_caches_per_audience_and_sends_identity_header():
    http = FakeHttp({("GET", "/msi/token"): ok({"access_token": "tok"})})
    token = token_for(http)
    assert token(sc.AUDIENCE_STORAGE) == token(sc.AUDIENCE_STORAGE) == "tok"
    token(sc.AUDIENCE_KEYVAULT)
    assert len(http.calls) == 2
    first = http.calls[0]
    assert first["headers"] == {"X-IDENTITY-HEADER": "secret"}
    assert "client_id=cid" in first["url"]
    assert "resource=https%3A%2F%2Fstorage.azure.com" in first["url"]


def test_cosmos_check_uses_aad_authorization_header():
    http = FakeHttp(healthy_routes())
    assert sc.check_cosmos(CFG, http, token_for(http), now="Sat, 31 Oct 2026 00:00:00 GMT") == "lending"
    [call] = http.find("GET", "/dbs")
    assert call["headers"]["Authorization"] == "type%3Daad%26ver%3D1.0%26sig%3Dtok"
    assert call["headers"]["x-ms-version"] == "2018-12-31"
    assert "resource=https%3A%2F%2Fcosmos-x.documents.azure.com" in http.find("GET", "/msi/token")[0]["url"]


def test_agent_roundtrip_polls_until_completed_and_deletes_agent():
    routes = healthy_routes()
    routes[("GET", "/threads/thread_1/runs/run_1")] = [
        ok({"status": "queued"}), ok({"status": "in_progress"}), ok({"status": "completed"}),
    ]
    http = FakeHttp(routes)
    sleeps = []
    assert sc.check_agent(CFG, http, token_for(http), sleep=sleeps.append) == "run_1"
    assert len(sleeps) == 2
    assert json.loads(http.find("POST", "/assistants?")[0]["body"])["model"] == "gpt-5.4-mini"
    assert len(http.find("DELETE", "/assistants/asst_1")) == 1


def test_agent_is_deleted_even_when_run_fails():
    routes = healthy_routes()
    routes[("GET", "/threads/thread_1/runs/run_1")] = ok({"status": "failed", "last_error": {"code": "server_error"}})
    http = FakeHttp(routes)
    with pytest.raises(sc.CheckFailed, match="failed"):
        sc.check_agent(CFG, http, token_for(http), sleep=lambda s: None)
    assert len(http.find("DELETE", "/assistants/asst_1")) == 1


def test_main_succeeds_when_all_checks_pass():
    out = io.StringIO()
    code = sc.main(ENV, http=FakeHttp(healthy_routes()), resolver=private, sleep=lambda s: None, out=out)
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert code == 0
    # dns 2 + chat 2 + blob + cosmos + keyvault + servicebus + agent
    assert lines[-1] == {"summary": {"total": 9, "failed": 0}}


def test_main_reports_failure_and_skips_paused_service_bus():
    routes = healthy_routes()
    routes[("GET", "/secrets?")] = sc.Response(403, b'{"error":"Forbidden"}')
    out = io.StringIO()
    env = {**ENV, "SMOKE_CONFIG": json.dumps({**CFG, "servicebus_host": ""})}
    code = sc.main(env, http=FakeHttp(routes), resolver=private, sleep=lambda s: None, out=out)
    results = {r["check"]: r for r in map(json.loads, out.getvalue().splitlines()) if "check" in r}
    assert code == 1
    assert "servicebus" not in results
    assert results["keyvault"]["ok"] is False
    assert "403" in results["keyvault"]["detail"]


def test_smoke_checks_uses_only_standard_library():
    # Job 컨테이너(MCR Python 기본 이미지)에는 추가 패키지가 없습니다.
    tree = ast.parse(Path(sc.__file__).read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            modules.add((node.module or "").split(".")[0])
    assert modules - {"__future__"} <= set(sys.stdlib_module_names)
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests/ops/test_smoke_checks.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ops'`.

- [ ] **Step 3: 스크립트 구현**

`src/ops/__init__.py`:

```python
"""P2 데모 환경 운영 도구(배포 창, pause/resume, teardown, smoke)."""
```

`src/ops/smoke_checks.py`:

```python
"""배포 후 VNet 안에서 실행하는 smoke 점검(표준 라이브러리 전용).

Container Apps Job(job-smoke)이 `python3 -c <이 파일 내용>`으로 실행합니다. 설정은 환경 변수
SMOKE_CONFIG(JSON), 관리 ID는 AZURE_CLIENT_ID, IDENTITY_ENDPOINT, IDENTITY_HEADER로 받습니다.
점검마다 JSON 줄을 출력하고, 하나라도 실패하면 종료 코드 1을 반환합니다.
"""
from __future__ import annotations

import email.utils
import ipaddress
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable

AUDIENCE_COGNITIVE = "https://cognitiveservices.azure.com"
AUDIENCE_FOUNDRY = "https://ai.azure.com"
AUDIENCE_STORAGE = "https://storage.azure.com"
AUDIENCE_KEYVAULT = "https://vault.azure.net"
AUDIENCE_SERVICEBUS = "https://servicebus.azure.net"


class CheckFailed(Exception):
    """점검 실패."""


@dataclass
class Response:
    status: int
    body: bytes

    def json(self):
        return json.loads(self.body.decode("utf-8") or "null")


Http = Callable[..., Response]


def urllib_http(method: str, url: str, headers: dict[str, str] | None = None,
                body: bytes | None = None) -> Response:
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            return Response(resp.status, resp.read())
    except urllib.error.HTTPError as err:
        return Response(err.code, err.read())


def expect(resp: Response, *statuses: int, what: str) -> Response:
    if resp.status not in statuses:
        raise CheckFailed(f"{what}: HTTP {resp.status} {resp.body[:300]!r}")
    return resp


def _bearer(token: str, json_body: bool = False) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def _json(obj) -> bytes:
    return json.dumps(obj).encode("utf-8")


class TokenProvider:
    """Container Apps 관리 ID 엔드포인트에서 토큰을 받아 audience별로 캐시합니다."""

    def __init__(self, http: Http, client_id: str, endpoint: str, secret_header: str):
        self.http = http
        self.client_id = client_id
        self.endpoint = endpoint
        self.secret_header = secret_header
        self._cache: dict[str, str] = {}

    def __call__(self, audience: str) -> str:
        if audience not in self._cache:
            query = urllib.parse.urlencode(
                {"api-version": "2019-08-01", "resource": audience, "client_id": self.client_id})
            resp = expect(self.http("GET", f"{self.endpoint}?{query}", {"X-IDENTITY-HEADER": self.secret_header}),
                          200, what=f"token {audience}")
            self._cache[audience] = resp.json()["access_token"]
        return self._cache[audience]


def check_private_dns(host: str, resolver: Callable[[str], str] = socket.gethostbyname) -> str:
    ip = resolver(host)
    if not ipaddress.ip_address(ip).is_private:
        raise CheckFailed(f"{host} -> {ip}: Private Endpoint가 아니라 공인 IP로 해석됩니다")
    return ip


def check_chat(cfg: dict, http: Http, token: TokenProvider, model: str) -> str:
    url = f"https://{cfg['openai_host']}/openai/v1/chat/completions"
    payload = {"model": model, "messages": [{"role": "user", "content": "Reply with OK."}],
               "max_completion_tokens": 64}
    resp = expect(http("POST", url, _bearer(token(AUDIENCE_COGNITIVE), json_body=True), _json(payload)),
                  200, what=f"chat {model}")
    return resp.json().get("model", model)


def check_blob(cfg: dict, http: Http, token: TokenProvider) -> str:
    headers = {**_bearer(token(AUDIENCE_STORAGE)), "x-ms-version": "2023-11-03"}
    expect(http("GET", f"https://{cfg['blob_host']}/?comp=list", headers), 200, what="blob list containers")
    return "containers listed"


def check_cosmos(cfg: dict, http: Http, token: TokenProvider, now: str | None = None) -> str:
    host = cfg["cosmos_host"]
    signature = token(f"https://{host}")
    headers = {
        "Authorization": urllib.parse.quote(f"type=aad&ver=1.0&sig={signature}", safe=""),
        "x-ms-date": now or email.utils.formatdate(usegmt=True),
        "x-ms-version": "2018-12-31",
    }
    resp = expect(http("GET", f"https://{host}/dbs", headers), 200, what="cosmos list databases")
    return ",".join(sorted(db["id"] for db in resp.json().get("Databases", [])))


def check_keyvault(cfg: dict, http: Http, token: TokenProvider) -> str:
    url = f"https://{cfg['keyvault_host']}/secrets?api-version=7.4"
    expect(http("GET", url, _bearer(token(AUDIENCE_KEYVAULT))), 200, what="key vault list secrets")
    return "secrets listed"


def check_servicebus(cfg: dict, http: Http, token: TokenProvider) -> str:
    url = f"https://{cfg['servicebus_host']}/{cfg['servicebus_topic']}/messages"
    body = _json({"type": "smoke", "at": time.time()})
    expect(http("POST", url, _bearer(token(AUDIENCE_SERVICEBUS), json_body=True), body), 201,
           what="service bus send")
    return cfg["servicebus_topic"]


def check_agent(cfg: dict, http: Http, token: TokenProvider, sleep: Callable[[float], None] = time.sleep,
                poll_seconds: float = 2.0, max_polls: int = 60) -> str:
    """에이전트 생성 → 스레드 실행 → 완료 대기. 에이전트는 항상 삭제합니다.

    capability host(Cosmos 스레드, Storage, Search)와 agent 서브넷 경로를 함께 검증합니다.
    """
    base = cfg["project_endpoint"].rstrip("/")
    query = "api-version=v1"
    headers = _bearer(token(AUDIENCE_FOUNDRY), json_body=True)
    agent = expect(http("POST", f"{base}/assistants?{query}", headers,
                        _json({"name": "p2-smoke", "model": cfg["agent_model"], "instructions": "Reply with OK."})),
                   200, 201, what="agent create").json()
    try:
        run = expect(http("POST", f"{base}/threads/runs?{query}", headers,
                          _json({"assistant_id": agent["id"],
                                 "thread": {"messages": [{"role": "user", "content": "ping"}]}})),
                     200, 201, what="agent run create").json()
        for _ in range(max_polls):
            status = expect(http("GET", f"{base}/threads/{run['thread_id']}/runs/{run['id']}?{query}", headers),
                            200, what="agent run get").json()
            if status["status"] == "completed":
                return run["id"]
            if status["status"] in ("failed", "cancelled", "expired"):
                raise CheckFailed(f"agent run {status['status']}: {status.get('last_error')}")
            sleep(poll_seconds)
        raise CheckFailed("agent run 대기 시간 초과")
    finally:
        http("DELETE", f"{base}/assistants/{agent['id']}?{query}", headers)


def build_checks(cfg: dict, http: Http, token: TokenProvider, resolver: Callable[[str], str],
                 sleep: Callable[[float], None]) -> list[tuple[str, Callable[[], str]]]:
    checks: list[tuple[str, Callable[[], str]]] = [
        (f"dns:{host}", lambda host=host: check_private_dns(host, resolver)) for host in cfg["private_hosts"]
    ]
    checks += [(f"chat:{model}", lambda model=model: check_chat(cfg, http, token, model))
               for model in cfg["chat_models"]]
    checks += [
        ("blob", lambda: check_blob(cfg, http, token)),
        ("cosmos", lambda: check_cosmos(cfg, http, token)),
        ("keyvault", lambda: check_keyvault(cfg, http, token)),
    ]
    if cfg.get("servicebus_host"):
        checks.append(("servicebus", lambda: check_servicebus(cfg, http, token)))
    checks.append(("agent", lambda: check_agent(cfg, http, token, sleep)))
    return checks


def run_checks(checks: list[tuple[str, Callable[[], str]]]) -> list[dict]:
    results = []
    for name, check in checks:
        started = time.monotonic()
        try:
            detail, ok = check(), True
        except Exception as exc:  # 한 점검이 실패해도 나머지를 계속 실행합니다.
            detail, ok = f"{type(exc).__name__}: {exc}", False
        results.append({"check": name, "ok": ok, "detail": detail,
                        "seconds": round(time.monotonic() - started, 2)})
    return results


def main(environ=None, http: Http = urllib_http, resolver: Callable[[str], str] = socket.gethostbyname,
         sleep: Callable[[float], None] = time.sleep, out=None) -> int:
    environ = os.environ if environ is None else environ
    out = out or sys.stdout
    cfg = json.loads(environ["SMOKE_CONFIG"])
    token = TokenProvider(http, environ["AZURE_CLIENT_ID"], environ["IDENTITY_ENDPOINT"], environ["IDENTITY_HEADER"])
    results = run_checks(build_checks(cfg, http, token, resolver, sleep))
    for result in results:
        print(json.dumps(result, ensure_ascii=False), file=out)
    failed = sum(not r["ok"] for r in results)
    print(json.dumps({"summary": {"total": len(results), "failed": failed}}), file=out)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
```

`pyproject.toml`의 hatch 패키지 목록을 다음으로 바꾼다:

```toml
packages = ["src/shared", "src/policy", "src/mockapis", "src/ops"]
```

- [ ] **Step 4: 스크립트 테스트 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests/ops/test_smoke_checks.py -q`
Expected: `8 passed`.

- [ ] **Step 5: 실패하는 Job 테스트 작성**

`tests/infra/test_smoke_job.py`:

```python
import json

JOB = "Microsoft.App/jobs"


def _container(exp) -> dict:
    return exp.one(JOB, "job-smoke")["properties"]["template"]["containers"][0]


def _config(exp) -> dict:
    env = {e["name"]: e["value"] for e in _container(exp)["env"]}
    return json.loads(env["SMOKE_CONFIG"])


def test_smoke_job_runs_inline_script_in_internal_environment(closed):
    job = closed.one(JOB, "job-smoke")
    props = job["properties"]
    assert props["environmentId"].endswith("/managedEnvironments/cae-int-amatest")
    assert props["configuration"]["triggerType"] == "Manual"
    assert props["configuration"]["replicaRetryLimit"] == 0
    assert list(job["identity"]["userAssignedIdentities"])[0].endswith("/id-smoke-amatest")
    container = _container(closed)
    assert container["image"] == "mcr.microsoft.com/azurelinux/base/python:3.12"
    assert container["command"][:2] == ["python3", "-c"]
    assert "def main(" in container["command"][2]


def test_smoke_config_covers_all_private_endpoints(closed):
    config = _config(closed)
    hosts = config["private_hosts"]
    assert len(hosts) == 14
    for suffix in (".openai.azure.com", ".services.ai.azure.com", ".cognitiveservices.azure.com",
                   ".search.windows.net", ".documents.azure.com", ".vault.azure.net",
                   ".servicebus.windows.net", ".azurecr.io", ".azurewebsites.net"):
        assert any(h.endswith(suffix) for h in hosts), suffix
    assert sum(".core.windows.net" in h for h in hosts) == 5  # stagt/stdat/stfn blob + stfn queue/table
    assert config["chat_models"] == ["gpt-5.4", "gpt-5.4-mini"]
    assert config["servicebus_host"].endswith(".servicebus.windows.net")
    assert config["project_endpoint"].endswith("/api/projects/proj-lending")


def test_smoke_config_when_service_bus_paused(variant):
    config = _config(variant)
    assert config["servicebus_host"] == ""
    assert len(config["private_hosts"]) == 13
```

- [ ] **Step 6: 실패 확인**

Run: `$env:RUN_INFRA_TESTS = '1'; .\.venv\Scripts\python -m pytest tests/infra/test_smoke_job.py -q`
Expected: FAIL — `Microsoft.App/jobs 'job-smoke': []`.

- [ ] **Step 7: Job 모듈과 resources.bicep T8 섹션 작성**

`infra/modules/smoke-job.bicep`:

```bicep
// VNet 안(internal Container Apps 환경)에서 smoke 점검을 실행하는 수동 트리거 Job.
// 이미지 빌드 없이 MCR Python 이미지에서 src/ops/smoke_checks.py를 인라인 실행합니다.
param name string
param location string
param tags object
param environmentId string
param identityId string
param identityClientId string
param config object

resource job 'Microsoft.App/jobs@2024-03-01' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identityId}': {} }
  }
  properties: {
    environmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 900
      replicaRetryLimit: 0
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
    }
    template: {
      containers: [
        {
          name: 'smoke'
          image: 'mcr.microsoft.com/azurelinux/base/python:3.12'
          command: [ 'python3', '-c', loadTextContent('../../src/ops/smoke_checks.py') ]
          env: [
            { name: 'AZURE_CLIENT_ID', value: identityClientId }
            { name: 'SMOKE_CONFIG', value: string(config) }
            { name: 'PYTHONUNBUFFERED', value: '1' }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
    }
  }
}

output name string = job.name
```

`infra/resources.bicep` 끝에 추가:

```bicep
// ── VNet 안 smoke Job (T8) ─────────────────────────────────────────────
// Private Endpoint가 있는 모든 호스트. VNet 안에서 사설 IP로 해석되어야 합니다.
var smokePrivateHosts = concat([
  '${names.ais}.openai.azure.com'
  '${names.ais}.services.ai.azure.com'
  '${names.ais}.cognitiveservices.azure.com'
  '${names.search}.search.windows.net'
  '${names.stAgents}.blob.${storageSuffix}'
  '${names.stData}.blob.${storageSuffix}'
  '${names.stFunc}.blob.${storageSuffix}'
  '${names.stFunc}.queue.${storageSuffix}'
  '${names.stFunc}.table.${storageSuffix}'
  '${names.cosmos}.documents.azure.com'
  '${names.kv}${environment().suffixes.keyvaultDns}'
  '${names.acr}${environment().suffixes.acrLoginServer}'
  '${names.func}.azurewebsites.net'
], serviceBusEnabled ? [ '${names.sb}.servicebus.windows.net' ] : [])

module smokeJob 'modules/smoke-job.bicep' = {
  name: 'smoke-job'
  params: {
    name: names.smokeJob
    location: location
    tags: tags
    environmentId: acaInt.outputs.id
    identityId: identities.outputs.smokeId
    identityClientId: identities.outputs.smokeClientId
    config: {
      openai_host: '${names.ais}.openai.azure.com'
      project_endpoint: 'https://${names.ais}.services.ai.azure.com/api/projects/${names.project}'
      blob_host: '${names.stData}.blob.${storageSuffix}'
      cosmos_host: '${names.cosmos}.documents.azure.com'
      keyvault_host: '${names.kv}${environment().suffixes.keyvaultDns}'
      servicebus_host: serviceBusEnabled ? '${names.sb}.servicebus.windows.net' : ''
      servicebus_topic: 'flow.updated'
      chat_models: map(filter(modelDeployments, m => m.format == 'OpenAI'), m => m.name)
      agent_model: 'gpt-5.4-mini'
      private_hosts: smokePrivateHosts
    }
  }
}

output SMOKE_JOB_NAME string = smokeJob.outputs.name
```

`infra/main.bicep` 끝에 추가:

```bicep
output SMOKE_JOB_NAME string = resources.outputs.SMOKE_JOB_NAME
```

- [ ] **Step 8: 통과 확인**

Run: `$env:RUN_INFRA_TESTS = '1'; .\.venv\Scripts\python -m pytest tests/infra tests/ops -q`
Expected: 전부 PASS.

- [ ] **Step 9: 커밋**

```powershell
git add src/ops pyproject.toml infra tests/infra tests/ops
git commit -m "feat(ops): in-vnet smoke checks and container apps job" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`nCopilot-Session: 05f1ca1b-6073-417d-bfb1-6b546f82cb58"
```

### Task 9: 운영 CLI — 배포 창, pause/resume, teardown, smoke 실행

`python -m ops ...` 하나로 운영 절차를 고정해, 배포 창을 열어 둔 채 잊거나 teardown 순서를 틀리는 실수를 줄인다. 모든 외부 명령은 주입된 `runner`로 실행해 단위 테스트에서 가짜로 바꾼다.

**Files:**
- Create: `src/ops/azcli.py`, `src/ops/azdenv.py`, `src/ops/window.py`, `src/ops/lifecycle.py`, `src/ops/smoke_runner.py`, `src/ops/cli.py`, `src/ops/__main__.py`
- Test: `tests/ops/conftest.py`, `tests/ops/test_window.py`, `tests/ops/test_lifecycle.py`, `tests/ops/test_smoke_runner.py`, `tests/ops/test_cli.py`

**Interfaces:**
- Consumes: azd 출력(T1~T8): `AZURE_RESOURCE_GROUP`, `AZURE_LOCATION`, `AI_ACCOUNT_NAME`, `AI_PROJECT_NAME`, `FOUNDRY_PROJECT_CAPHOST`, `KEYVAULT_NAME`, `SERVICEBUS_NAME`, `SMOKE_JOB_NAME`, `LOG_ANALYTICS_CUSTOMER_ID`. azd 환경 변수(T1 `main.bicepparam`): `OPERATOR_IP`(`closed` 감시값), `ACR_PUBLIC`, `SERVICEBUS_ENABLED`, `DEPLOY_CAPHOST`. PE 이름 규칙 `pe-<sb>-namespace`(T5).
- Produces:
  - `ops.azcli`: `OpsError(RuntimeError)`, `Runner = Callable[..., str]`(호출 형식 `runner(args: list[str], *, stream: bool = False) -> str`), `subprocess_runner`, `is_not_found(err: OpsError) -> bool`.
  - `ops.azdenv`: `parse_env_values(text) -> dict[str, str]`, `get_env(runner)`, `set_env(runner, key, value)`, `provision(runner)`.
  - `ops.window`: `validate_operator_ip(ip) -> str`, `fetch_public_ip(fetch=None) -> str`, `ensure_caphost_skip(runner, env)`, `open_window(runner, ip, *, acr=False) -> str`, `close_window(runner)`, `window_status(runner, current_ip) -> WindowStatus`.
  - `ops.lifecycle`: `pause(runner) -> bool`, `resume(runner)`, `teardown(runner, confirm, *, sleep=time.sleep, group_retries=4, retry_wait=300, poll_wait=15, poll_limit=120)`.
  - `ops.smoke_runner`: `run_smoke(runner, *, sleep=time.sleep, poll_wait=10, poll_limit=90) -> str`(실행 이름), `log_hint(env, execution) -> str`.
  - `ops.cli.main(argv=None, *, runner=subprocess_runner, public_ip=fetch_public_ip, out=None) -> int`(0 성공, 1 오류, 2 경고).

- [ ] **Step 1: 가짜 runner fixture 작성**

`tests/ops/conftest.py`:

```python
import pytest


class FakeRunner:
    """az/azd 호출을 기록하는 가짜 runner.

    `azd env get-values`·`azd env set`은 self.env 사전으로 흉내 냅니다. 그 밖의 명령은
    on(접두어..., results=[...])로 등록한 결과를 순서대로 돌려주고(마지막 결과는 반복),
    결과가 예외 인스턴스면 raise 합니다. 등록되지 않은 명령은 빈 문자열을 돌려줍니다.
    """

    def __init__(self, env=None):
        self.env = dict(env or {})
        self.calls: list[list[str]] = []
        self._responses: list[tuple[tuple[str, ...], list]] = []

    def on(self, *prefix, results):
        self._responses.append((prefix, list(results)))
        return self

    def __call__(self, args, *, stream=False):
        args = list(args)
        self.calls.append(args)
        if args[:3] == ["azd", "env", "get-values"]:
            return "\n".join(f'{k}="{v}"' for k, v in self.env.items()) + "\n"
        if args[:3] == ["azd", "env", "set"]:
            self.env[args[3]] = args[4]
            return ""
        for prefix, queue in self._responses:
            if tuple(args[:len(prefix)]) == prefix:
                result = queue.pop(0) if len(queue) > 1 else queue[0]
                if isinstance(result, BaseException):
                    raise result
                return result
        return ""

    def commands(self, *prefix):
        return [c for c in self.calls if tuple(c[:len(prefix)]) == prefix]

    def index(self, *prefix, start=0):
        """접두어와 일치하는 start 이후 첫 호출의 위치."""
        for i, call in enumerate(self.calls[start:], start):
            if tuple(call[:len(prefix)]) == prefix:
                return i
        raise AssertionError(f"호출 없음: {prefix}")


@pytest.fixture
def fake_runner():
    return FakeRunner
```

- [ ] **Step 2: 실패하는 배포 창 테스트 작성**

`tests/ops/test_window.py`:

```python
import pytest

from ops.azcli import OpsError
from ops.azdenv import parse_env_values
from ops.window import close_window, open_window, validate_operator_ip, window_status

PROVISIONED = {"AZURE_RESOURCE_GROUP": "rg-amalend", "AI_ACCOUNT_NAME": "ais-x", "OPERATOR_IP": "closed"}


def test_parse_env_values_handles_quotes_and_blank_lines():
    text = 'A="1"\n\nB="say \\"hi\\""\n# comment\nC=plain\n'
    assert parse_env_values(text) == {"A": "1", "B": 'say "hi"', "C": "plain"}


@pytest.mark.parametrize("bad", ["192.168.1.1", "10.0.0.1", "127.0.0.1", "not-an-ip", "2001:db8::1", ""])
def test_validate_operator_ip_rejects_non_public_ipv4(bad):
    with pytest.raises(OpsError):
        validate_operator_ip(bad)


def test_validate_operator_ip_accepts_public_ipv4():
    assert validate_operator_ip(" 20.30.40.50 ") == "20.30.40.50"


def test_open_window_sets_ip_and_provisions(fake_runner):
    runner = fake_runner(PROVISIONED)
    assert open_window(runner, "20.30.40.50") == "20.30.40.50"
    assert runner.env["OPERATOR_IP"] == "20.30.40.50"
    assert runner.env["ACR_PUBLIC"] == "false"
    assert runner.commands("azd", "provision") == [["azd", "provision", "--no-prompt"]]


def test_open_window_with_acr_build_window(fake_runner):
    runner = fake_runner(PROVISIONED)
    open_window(runner, "20.30.40.50", acr=True)
    assert runner.env["ACR_PUBLIC"] == "true"


def test_open_window_skips_caphost_after_first_provision(fake_runner):
    # Review Focus #3: 이미 프로비저닝된 환경에서 caphost를 다시 PUT 하지 않습니다.
    runner = fake_runner(PROVISIONED)
    open_window(runner, "20.30.40.50")
    assert runner.env["DEPLOY_CAPHOST"] == "false"
    assert runner.index("azd", "env", "set", "DEPLOY_CAPHOST") < runner.index("azd", "provision")


def test_open_window_before_first_provision_keeps_caphost(fake_runner):
    runner = fake_runner({"OPERATOR_IP": "closed"})
    open_window(runner, "20.30.40.50")
    assert "DEPLOY_CAPHOST" not in runner.env


def test_close_window_sets_closed_sentinel(fake_runner):
    runner = fake_runner({**PROVISIONED, "OPERATOR_IP": "20.30.40.50", "ACR_PUBLIC": "true"})
    close_window(runner)
    assert runner.env["OPERATOR_IP"] == "closed"
    assert runner.env["ACR_PUBLIC"] == "false"
    assert len(runner.commands("azd", "provision")) == 1


def test_status_warns_when_public_ip_changed(fake_runner):
    # Review Focus #1: 창을 연 뒤 공인 IP가 바뀌면 경고합니다.
    runner = fake_runner({**PROVISIONED, "OPERATOR_IP": "20.30.40.50"})
    status = window_status(runner, "20.30.40.99")
    assert status.open is True
    assert status.allowed_ip == "20.30.40.50"
    assert "20.30.40.99" in status.warning


def test_status_closed_has_no_warning(fake_runner):
    status = window_status(fake_runner(PROVISIONED), "20.30.40.99")
    assert status.open is False
    assert status.allowed_ip is None
    assert status.warning is None
```

- [ ] **Step 3: 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests/ops/test_window.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ops.azcli'`.

- [ ] **Step 4: 실행기·azd 환경·배포 창 구현**

`src/ops/azcli.py`:

```python
"""외부 명령(az, azd) 실행기. 테스트에서는 같은 호출 형식의 가짜로 바꿉니다."""
from __future__ import annotations

import shutil
import subprocess
from typing import Callable

Runner = Callable[..., str]  # runner(args: list[str], *, stream: bool = False) -> str

_NOT_FOUND_MARKERS = ("ResourceNotFound", "ResourceGroupNotFound", "NotFound", "(404)",
                      "was not found", "could not be found")


class OpsError(RuntimeError):
    """운영 명령 실패."""


def subprocess_runner(args: list[str], *, stream: bool = False) -> str:
    """명령을 실행합니다. stream=True면 출력을 그대로 터미널에 보여 주고 빈 문자열을 돌려줍니다."""
    executable = shutil.which(args[0])
    if executable is None:
        raise OpsError(f"'{args[0]}' 명령을 찾을 수 없습니다. PATH를 확인하세요.")
    command = [executable, *args[1:]]
    if stream:
        completed = subprocess.run(command)
        if completed.returncode != 0:
            raise OpsError(f"명령 실패({completed.returncode}): {' '.join(args)}")
        return ""
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    if completed.returncode != 0:
        raise OpsError(f"명령 실패({completed.returncode}): {' '.join(args)}\n{completed.stderr.strip()}")
    return completed.stdout


def is_not_found(err: OpsError) -> bool:
    text = str(err)
    return any(marker in text for marker in _NOT_FOUND_MARKERS)
```

`src/ops/azdenv.py`:

```python
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
```

`src/ops/window.py`:

```python
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
```

- [ ] **Step 5: 배포 창 테스트 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests/ops/test_window.py -q`
Expected: `15 passed`.

- [ ] **Step 6: 실패하는 수명 주기·smoke 실행 테스트 작성**

`tests/ops/test_lifecycle.py`:

```python
import pytest

from ops.azcli import OpsError
from ops.lifecycle import pause, resume, teardown

ENV = {
    "AZURE_RESOURCE_GROUP": "rg-amalend",
    "AZURE_LOCATION": "koreacentral",
    "AI_ACCOUNT_NAME": "ais-x",
    "AI_PROJECT_NAME": "proj-lending",
    "FOUNDRY_PROJECT_CAPHOST": "caphost-lending",
    "KEYVAULT_NAME": "kvx",
    "SERVICEBUS_NAME": "sb-x",
    "DEPLOY_CAPHOST": "false",
}
NOT_FOUND = OpsError("(ResourceNotFound) The resource was not found")


def test_pause_deletes_endpoint_then_namespace_then_provisions(fake_runner):
    runner = fake_runner(ENV).on("az", "servicebus", "namespace", "list", results=['["sb-x"]'])
    assert pause(runner) is True
    assert runner.env["SERVICEBUS_ENABLED"] == "false"
    pe = runner.index("az", "network", "private-endpoint", "delete")
    assert runner.calls[pe][-2:] == ["-n", "pe-sb-x-namespace"]
    assert pe < runner.index("az", "servicebus", "namespace", "delete") < runner.index("azd", "provision")


def test_pause_is_idempotent_when_already_deleted(fake_runner):
    runner = fake_runner(ENV).on("az", "servicebus", "namespace", "list", results=["[]"])
    assert pause(runner) is False
    assert runner.commands("az", "servicebus", "namespace", "delete") == []
    assert len(runner.commands("azd", "provision")) == 1


def test_resume_enables_service_bus_and_provisions(fake_runner):
    runner = fake_runner({**ENV, "SERVICEBUS_ENABLED": "false"})
    resume(runner)
    assert runner.env["SERVICEBUS_ENABLED"] == "true"
    assert len(runner.commands("azd", "provision")) == 1


def test_teardown_requires_matching_confirmation(fake_runner):
    runner = fake_runner(ENV)
    with pytest.raises(OpsError, match="rg-amalend"):
        teardown(runner, "rg-other")
    assert runner.commands("az", "group", "delete") == []


def test_teardown_order_and_group_retry(fake_runner):
    # Review Focus #4: caphost → 계정 → purge 후 RG 삭제. 서브넷 점유로 실패하면 기다렸다 재시도합니다.
    in_use = OpsError("InUseSubnetCannotBeDeleted: Subnet snet-agents is in use")
    runner = (fake_runner(ENV)
              .on("az", "account", "show", results=["sub-1\n"])
              .on("az", "rest", "--method", "get", results=[NOT_FOUND])
              .on("az", "group", "delete", results=[in_use, in_use, ""]))
    sleeps = []
    teardown(runner, "rg-amalend", sleep=sleeps.append)

    project_caphost = runner.index("az", "rest", "--method", "delete")
    account_caphost = runner.index("az", "rest", "--method", "delete", start=project_caphost + 1)
    assert "/projects/proj-lending/capabilityHosts/caphost-lending" in runner.calls[project_caphost][-1]
    assert "/capabilityHosts/ais-x@aml_aiagentservice" in runner.calls[account_caphost][-1]
    order = [
        account_caphost,
        runner.index("az", "cognitiveservices", "account", "delete"),
        runner.index("az", "cognitiveservices", "account", "purge"),
        runner.index("az", "group", "delete"),
        runner.index("az", "keyvault", "purge"),
    ]
    assert order == sorted(order)
    assert len(runner.commands("az", "group", "delete")) == 3
    assert sleeps.count(300) == 2
    assert runner.env["DEPLOY_CAPHOST"] == "true"


def test_teardown_tolerates_already_deleted_resources(fake_runner):
    runner = (fake_runner(ENV)
              .on("az", "account", "show", results=["sub-1\n"])
              .on("az", "rest", results=[NOT_FOUND])
              .on("az", "cognitiveservices", results=[NOT_FOUND])
              .on("az", "group", "delete", results=[OpsError("(ResourceGroupNotFound) missing")])
              .on("az", "keyvault", "purge", results=[NOT_FOUND]))
    teardown(runner, "rg-amalend", sleep=lambda s: None)
    assert len(runner.commands("az", "group", "delete")) == 1
    assert runner.env["DEPLOY_CAPHOST"] == "true"


def test_teardown_gives_up_after_retries(fake_runner):
    in_use = OpsError("InUseSubnetCannotBeDeleted")
    runner = (fake_runner(ENV)
              .on("az", "account", "show", results=["sub-1\n"])
              .on("az", "rest", "--method", "get", results=[NOT_FOUND])
              .on("az", "group", "delete", results=[in_use]))
    with pytest.raises(OpsError, match="InUseSubnet"):
        teardown(runner, "rg-amalend", sleep=lambda s: None, group_retries=2)
    assert len(runner.commands("az", "group", "delete")) == 2
```

`tests/ops/test_smoke_runner.py`:

```python
import pytest

from ops.azcli import OpsError
from ops.smoke_runner import run_smoke

ENV = {"AZURE_RESOURCE_GROUP": "rg-amalend", "SMOKE_JOB_NAME": "job-smoke", "LOG_ANALYTICS_CUSTOMER_ID": "law-guid"}


def test_run_smoke_polls_until_succeeded(fake_runner):
    runner = (fake_runner(ENV)
              .on("az", "containerapp", "job", "start", results=["job-smoke-abc12\n"])
              .on("az", "containerapp", "job", "execution", "show", results=["Running\n", "Running\n", "Succeeded\n"]))
    sleeps = []
    assert run_smoke(runner, sleep=sleeps.append) == "job-smoke-abc12"
    assert len(sleeps) == 2
    show = runner.commands("az", "containerapp", "job", "execution", "show")[0]
    assert show[show.index("--job-execution-name") + 1] == "job-smoke-abc12"


def test_run_smoke_failure_includes_log_query_hint(fake_runner):
    runner = (fake_runner(ENV)
              .on("az", "containerapp", "job", "start", results=["job-smoke-abc12\n"])
              .on("az", "containerapp", "job", "execution", "show", results=["Failed\n"]))
    with pytest.raises(OpsError) as err:
        run_smoke(runner, sleep=lambda s: None)
    message = str(err.value)
    assert "Failed" in message
    assert "ContainerAppConsoleLogs_CL" in message
    assert "law-guid" in message


def test_run_smoke_times_out(fake_runner):
    runner = (fake_runner(ENV)
              .on("az", "containerapp", "job", "start", results=["job-smoke-abc12\n"])
              .on("az", "containerapp", "job", "execution", "show", results=["Running\n"]))
    with pytest.raises(OpsError, match="시간 초과"):
        run_smoke(runner, sleep=lambda s: None, poll_limit=3)
```

- [ ] **Step 7: 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests/ops/test_lifecycle.py tests/ops/test_smoke_runner.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ops.lifecycle'`.

- [ ] **Step 8: 수명 주기·smoke 실행 구현**

`src/ops/lifecycle.py`:

```python
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
```

`src/ops/smoke_runner.py`:

```python
"""smoke Container Apps Job을 시작하고 끝날 때까지 기다립니다."""
from __future__ import annotations

import time
from typing import Callable

from ops.azcli import OpsError, Runner
from ops.azdenv import get_env

TERMINAL = {"Succeeded", "Failed", "Stopped", "Degraded"}


def log_hint(env: dict[str, str], execution: str) -> str:
    query = (f"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '{execution}' "
             "| project TimeGenerated, Log_s | order by TimeGenerated asc")
    workspace = env.get("LOG_ANALYTICS_CUSTOMER_ID", "<workspace-id>")
    return f'로그 조회: az monitor log-analytics query -w {workspace} --analytics-query "{query}" -o table'


def run_smoke(runner: Runner, *, sleep: Callable[[float], None] = time.sleep, poll_wait: float = 10,
              poll_limit: int = 90) -> str:
    env = get_env(runner)
    group, job = env["AZURE_RESOURCE_GROUP"], env["SMOKE_JOB_NAME"]
    execution = runner(["az", "containerapp", "job", "start", "-g", group, "-n", job,
                        "--query", "name", "-o", "tsv"]).strip()
    for _ in range(poll_limit):
        status = runner(["az", "containerapp", "job", "execution", "show", "-g", group, "-n", job,
                         "--job-execution-name", execution, "--query", "properties.status", "-o", "tsv"]).strip()
        if status in TERMINAL:
            if status != "Succeeded":
                raise OpsError(f"smoke 실패: {execution} 상태 {status}\n{log_hint(env, execution)}")
            return execution
        sleep(poll_wait)
    raise OpsError(f"smoke 대기 시간 초과: {execution}\n{log_hint(env, execution)}")
```

- [ ] **Step 9: 수명 주기·smoke 실행 테스트 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests/ops/test_lifecycle.py tests/ops/test_smoke_runner.py -q`
Expected: `10 passed`.

- [ ] **Step 10: 실패하는 CLI 테스트 작성**

`tests/ops/test_cli.py`:

```python
import io

import pytest

from ops.azcli import OpsError
from ops.cli import main

ENV = {"AZURE_RESOURCE_GROUP": "rg-amalend", "AI_ACCOUNT_NAME": "ais-x", "OPERATOR_IP": "closed"}


def test_window_open_uses_detected_public_ip(fake_runner):
    runner = fake_runner(ENV)
    out = io.StringIO()
    assert main(["window", "open"], runner=runner, public_ip=lambda: "20.30.40.50", out=out) == 0
    assert runner.env["OPERATOR_IP"] == "20.30.40.50"
    assert "window close" in out.getvalue()


def test_window_open_with_explicit_ip_and_acr(fake_runner):
    runner = fake_runner(ENV)
    main(["window", "open", "--ip", "20.30.40.60", "--acr"], runner=runner,
         public_ip=lambda: pytest.fail("감지하지 않아야 합니다"), out=io.StringIO())
    assert runner.env["OPERATOR_IP"] == "20.30.40.60"
    assert runner.env["ACR_PUBLIC"] == "true"


def test_window_status_returns_2_when_ip_changed(fake_runner):
    runner = fake_runner({**ENV, "OPERATOR_IP": "20.30.40.50"})
    out = io.StringIO()
    assert main(["window", "status"], runner=runner, public_ip=lambda: "20.30.40.99", out=out) == 2
    assert "20.30.40.99" in out.getvalue()


def test_window_status_survives_ip_lookup_failure(fake_runner):
    def offline():
        raise OSError("network down")
    out = io.StringIO()
    assert main(["window", "status"], runner=fake_runner(ENV), public_ip=offline, out=out) == 0
    assert "닫힘" in out.getvalue()


def test_teardown_requires_confirm_flag(fake_runner):
    with pytest.raises(SystemExit) as err:
        main(["teardown"], runner=fake_runner(ENV), out=io.StringIO())
    assert err.value.code == 2


def test_ops_error_returns_1(fake_runner, capsys):
    def broken(args, *, stream=False):
        raise OpsError("azd 실패")
    assert main(["resume"], runner=broken, out=io.StringIO()) == 1
    assert "azd 실패" in capsys.readouterr().err
```

- [ ] **Step 11: 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests/ops/test_cli.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ops.cli'`.

- [ ] **Step 12: CLI 구현**

`src/ops/cli.py`:

```python
"""운영 CLI: python -m ops <명령>."""
from __future__ import annotations

import argparse
import sys
from typing import Callable

from ops.azcli import OpsError, Runner, subprocess_runner
from ops.lifecycle import pause, resume, teardown
from ops.smoke_runner import run_smoke
from ops.window import close_window, fetch_public_ip, open_window, window_status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ops", description="P2 데모 환경 운영 도구")
    commands = parser.add_subparsers(dest="command", required=True)

    window = commands.add_parser("window", help="배포 창(운영자 IP 한정 public access)")
    actions = window.add_subparsers(dest="action", required=True)
    opener = actions.add_parser("open", help="배포 창 열기")
    opener.add_argument("--ip", help="허용할 공인 IPv4(기본: 자동 감지)")
    opener.add_argument("--acr", action="store_true", help="az acr build를 위해 ACR도 전체 공개")
    actions.add_parser("close", help="배포 창 닫기")
    actions.add_parser("status", help="배포 창 상태와 IP 변경 확인")

    commands.add_parser("pause", help="Service Bus Premium 삭제(비용 절감)")
    commands.add_parser("resume", help="Service Bus 다시 배포")
    commands.add_parser("smoke", help="VNet 안 smoke Job 실행")
    destroy = commands.add_parser("teardown", help="환경 전체 삭제")
    destroy.add_argument("--confirm", required=True, help="삭제할 리소스 그룹 이름")
    return parser


def main(argv: list[str] | None = None, *, runner: Runner = subprocess_runner,
         public_ip: Callable[[], str] = fetch_public_ip, out=None) -> int:
    out = out or sys.stdout
    args = build_parser().parse_args(argv)
    try:
        if args.command == "window" and args.action == "open":
            ip = open_window(runner, args.ip or public_ip(), acr=args.acr)
            suffix = " (ACR 전체 공개)" if args.acr else ""
            print(f"배포 창을 열었습니다: {ip}{suffix}. 작업 후 `python -m ops window close`로 닫으세요.", file=out)
        elif args.command == "window" and args.action == "close":
            close_window(runner)
            print("배포 창을 닫았습니다.", file=out)
        elif args.command == "window":
            try:
                current = public_ip()
            except (OpsError, OSError):
                current = None
            status = window_status(runner, current)
            state = f"열림({status.allowed_ip})" if status.open else "닫힘"
            print(f"배포 창: {state}, ACR 공개: {status.acr_public}, 현재 공인 IP: {current or '확인 불가'}", file=out)
            if status.warning:
                print(f"경고: {status.warning}", file=out)
                return 2
        elif args.command == "pause":
            deleted = pause(runner)
            print("Service Bus를 삭제했습니다." if deleted else "Service Bus가 이미 없습니다.", file=out)
        elif args.command == "resume":
            resume(runner)
            print("Service Bus를 다시 배포했습니다.", file=out)
        elif args.command == "smoke":
            print(f"smoke 성공: {run_smoke(runner)}", file=out)
        elif args.command == "teardown":
            teardown(runner, args.confirm)
            print(f"{args.confirm} 삭제를 마쳤습니다.", file=out)
    except OpsError as err:
        print(f"오류: {err}", file=sys.stderr)
        return 1
    return 0
```

`src/ops/__main__.py`:

```python
from ops.cli import main

raise SystemExit(main())
```

- [ ] **Step 13: 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests/ops -q; .\.venv\Scripts\python -m ops --help`
Expected: `tests/ops` 전부 PASS(8 + 15 + 10 + 6 = 39), 도움말에 `window`, `pause`, `resume`, `smoke`, `teardown` 표시. `No module named ops`가 나오면 T8의 `pyproject.toml` 변경이 설치에 반영되지 않은 것이므로 `.\.venv\Scripts\python -m pip install -e ".[dev]"` 후 다시 실행한다.

- [ ] **Step 14: 전체 회귀 확인 후 커밋**

Run: `.\.venv\Scripts\python -m pytest -q`
Expected: 기존 100 + ops 39 PASS, `tests/infra` skip.

```powershell
git add src/ops tests/ops
git commit -m "feat(ops): deploy window, pause/resume, teardown and smoke runner CLI" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`nCopilot-Session: 05f1ca1b-6073-417d-bfb1-6b546f82cb58"
```

---

### Task 10: 실제 Azure 배포와 검증(런북) + README

이 태스크는 실제 구독에 배포하므로 비용이 발생한다(Service Bus Premium 포함 시 하루 약 $25~30). 각 단계 결과(소요 시간, 출력)를 기록해 README와 커밋 메시지에 남긴다. 실패하면 아래 "문제 대응"을 먼저 확인한다.

**Files:**
- Modify: `README.md`(Azure 인프라 절 추가)
- 실행만: `azd`, `az`, `python -m ops`

**Interfaces:**
- Consumes: T1~T9 전부.
- Produces: 실제 배포된 `rg-amalend` 환경(배포 창 닫힘, smoke 통과), README의 운영 절차와 실측값.

- [ ] **Step 1: 사전 확인**

```powershell
$env:Path = [Environment]::GetEnvironmentVariable("Path","User") + ";" + [Environment]::GetEnvironmentVariable("Path","Machine")
az account show --query "{sub:id, tenant:tenantId, user:user.name}" -o table
az account set --subscription <구독 ID>
azd config set auth.useAzCliAuth true
az extension add -n containerapp --upgrade --only-show-errors
az provider register -n Microsoft.App --wait
az provider register -n Microsoft.CognitiveServices --wait
$env:RUN_INFRA_TESTS = '1'; .\.venv\Scripts\python -m pytest -q
```

Expected: 구독 <구독 ID 앞 8자리>…, 모든 테스트 PASS.

- [ ] **Step 2: azd 환경 만들기**

```powershell
azd env new amalend --location koreacentral --subscription <구독 ID>
azd env set AZURE_PRINCIPAL_ID (az ad signed-in-user show --query id -o tsv)
azd env set OPERATOR_IP closed
azd env get-values
```

Expected: `AZURE_ENV_NAME="amalend"`, `AZURE_LOCATION="koreacentral"`, `AZURE_PRINCIPAL_ID`, `OPERATOR_IP="closed"`.

- [ ] **Step 3: what-if 미리보기**

Run: `azd provision --preview`
Expected: 모든 리소스가 Create. 오류 없음. 리소스 수와 주요 항목(VNet, PE 17개 전후, 모델 배포 3개, caphost, 환경 2개)을 기록한다.

- [ ] **Step 4: 최초 프로비저닝(약 45~60분)**

```powershell
$elapsed = Measure-Command { azd provision --no-prompt }
"provision: {0:N1} min" -f $elapsed.TotalMinutes
```

Expected: 성공. 소요 시간을 기록한다.

문제 대응:
- `Microsoft-Decision-1` 배포가 SKU/지역 오류로 실패: `azd env set DECISION_MODEL_SKU DataZoneStandard` 후 다시 `azd provision --no-prompt`(ADR-002 전환 조건).
- 재시도 전에 caphost가 이미 만들어졌는지 확인해, 있으면 재-PUT을 막는다:
  ```powershell
  $acct = azd env get-value AI_ACCOUNT_NAME
  az rest --method get --url "https://management.azure.com/subscriptions/<구독 ID>/resourceGroups/rg-amalend/providers/Microsoft.CognitiveServices/accounts/$acct/projects/proj-lending/capabilityHosts/caphost-lending?api-version=2025-04-01-preview" --query properties.provisioningState -o tsv
  ```
  `Succeeded`면 `azd env set DEPLOY_CAPHOST false`.
- 쿼터 오류: `az cognitiveservices usage list -l koreacentral -o table`로 확인하고 `infra/main.bicep`의 `capacity`를 낮춘다(변경은 커밋).

- [ ] **Step 5: 최초 배포 이후 caphost 생략 고정**

```powershell
azd env set DEPLOY_CAPHOST false
azd provision --preview
```

Expected: 변경 없음 또는 무해한 차이(태그 정책, 읽기 전용 속성)만. 실제 변경이 보이면 원인을 기록하고 Bicep을 고친다.

- [ ] **Step 6: VNet 안 smoke**

Run: `.\.venv\Scripts\python -m ops smoke`
Expected: `smoke 성공: job-smoke-xxxxx`. 실패하면 출력된 로그 조회 명령으로 `ContainerAppConsoleLogs_CL`을 확인한다(로그 수집까지 몇 분 걸릴 수 있음). 성공한 실행의 JSON 줄 요약(점검 수, 소요 시간)을 기록한다.

- [ ] **Step 7: 배포 창 동작 검증**

```powershell
$kv = azd env get-value KEYVAULT_NAME
az keyvault secret list --vault-name $kv -o table      # 실패해야 함(public access 꺼짐)
.\.venv\Scripts\python -m ops window open
az keyvault secret list --vault-name $kv -o table      # 성공(빈 목록)
.\.venv\Scripts\python -m ops window status            # 열림(내 IP)
.\.venv\Scripts\python -m ops window close
az keyvault secret list --vault-name $kv -o table      # 다시 실패
```

Expected: 닫힘 → `Forbidden`/`ForbiddenByConnection` 계열 오류, 열림 → 성공, 닫힘 → 오류. 창 열기/닫기 소요 시간을 기록한다.

- [ ] **Step 8: pause/resume 검증**

```powershell
.\.venv\Scripts\python -m ops pause
az servicebus namespace list -g rg-amalend -o table    # 비어 있음
.\.venv\Scripts\python -m ops smoke                    # servicebus 점검 없이 통과
.\.venv\Scripts\python -m ops resume
.\.venv\Scripts\python -m ops smoke                    # servicebus 포함 통과
```

Expected: 모두 성공. 데모 일정이 없으면 이후 `python -m ops pause` 상태로 둔다.

- [ ] **Step 9: README에 Azure 인프라 절 추가**

`README.md` 끝에 다음 절을 추가하고, `<측정값>` 자리는 Step 4~8에서 기록한 실제 값으로 바꾼다(값이 없으면 그 줄을 지운다).

````markdown
## Azure 인프라(P2)

Korea Central에 전체 비공개 데모 환경을 Bicep + azd로 배포합니다. 설계 근거는
`docs/superpowers/specs/2026-10-31-agentic-cashflow-lending-design.md` §2.2, §6.2를 참고하세요.

### 준비

- Azure CLI 2.91+, Azure Developer CLI 1.37+, PowerShell 7, Python 3.12 가상환경(`.venv`)
- `az login` 후 `azd config set auth.useAzCliAuth true`
- `az extension add -n containerapp`
- 오프라인 IaC 테스트: `Install-Module PSRule.Rules.Azure -Scope CurrentUser` 후
  `$env:RUN_INFRA_TESTS = '1'; .\.venv\Scripts\python -m pytest tests/infra -q`

### 최초 배포

```powershell
azd env new amalend --location koreacentral
azd env set AZURE_PRINCIPAL_ID (az ad signed-in-user show --query id -o tsv)
azd env set OPERATOR_IP closed
azd provision --preview
azd provision --no-prompt          # 실측 <측정값>분(capability host 포함)
azd env set DEPLOY_CAPHOST false   # 이후 재프로비저닝에서 capability host 재생성 방지
python -m ops smoke                # VNet 안에서 Private Endpoint 경로 검증
```

### 운영 명령

| 명령 | 용도 |
|------|------|
| `python -m ops window open [--ip <IP>] [--acr]` | 배포 창 열기: 운영자 공인 IP만 허용(`--acr`은 ACR 빌드 창) |
| `python -m ops window status` | 창 상태 확인. 공인 IP가 바뀌었으면 경고(종료 코드 2) |
| `python -m ops window close` | 배포 창 닫기(작업 후 반드시 실행) |
| `python -m ops smoke` | VNet 안 smoke Job 실행 |
| `python -m ops pause` / `resume` | Service Bus Premium 삭제/복구(비용 절감) |
| `python -m ops teardown --confirm rg-amalend` | capability host → Foundry 계정 purge → 리소스 그룹 삭제 → Key Vault purge |

### 비용(월, 대략)

| 항목 | 비용 | 비고 |
|------|------|------|
| Service Bus Premium 1 MU | 약 $677 | 쓰지 않을 때 `pause` |
| AI Search Basic | 약 $74 | Foundry 벡터 저장소 |
| ACR Premium | 약 $50 | Private Endpoint 필요 |
| Private Endpoint 약 17개 | 약 $125 | 개당 약 $7.3 |
| 모델, Functions, Container Apps, Cosmos serverless | 사용량 기반 | 유휴 시 거의 0 |
| Application Gateway WAF v2 | (구축 안 함) | 운영 아키텍처 문서에만 기술, 약 $365 |

### 알려진 제약

- 콘솔은 WAF 없이 external Container Apps 환경에 둡니다(운영자 IP 허용 + Entra 인증, P3). 운영 설계는 App Gateway WAF.
- App Insights 수집은 public 엔드포인트 + Entra 인증을 씁니다(Azure Monitor Private Link Scope 미사용).
- 배포 창 동안에는 운영자 IP에서 데이터 평면에 접근할 수 있습니다. `window status`로 확인하고 작업 후 바로 닫으세요.
- 실측: 배포 창 열기/닫기 각 <측정값>분, teardown <측정값>분.
````

- [ ] **Step 10: 배포 창 닫힘 확인 후 커밋**

```powershell
.\.venv\Scripts\python -m ops window status   # 닫힘이어야 함
git add README.md
git commit -m "docs(infra): azure deployment runbook, ops commands and measured costs" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`nCopilot-Session: 05f1ca1b-6073-417d-bfb1-6b546f82cb58"
```

(teardown은 평가가 끝난 뒤에 실행한다. 실행했다면 소요 시간을 README에 반영해 따로 커밋한다.)

---

## P3 인계 사항

- **서비스 추가:** `azure.yaml`에 `orchestrator`(Function App, 태그 `azd-service-name: orchestrator` 이미 있음), Policy Engine·Mock API(`cae-int` 환경), 콘솔(`cae-web` 환경)을 추가한다. 이미지는 `python -m ops window open --acr`로 빌드 창을 연 뒤 `az acr build`(또는 azd remoteBuild)로 만든다.
- **Container Apps ingress:** `cae-int`의 Policy Engine·Mock API는 `external: true`로 둔다(internal 환경이라 VNet 전용, `https://<앱>.<CONTAINER_APPS_INT_DOMAIN>`). `external: false`로 두면 Functions에서 호출할 수 없다. 콘솔(`cae-web`)은 `ipSecurityRestrictions`에 운영자 IP를 넣고 Easy Auth(Entra)를 켠다. `ipSecurityRestrictions`가 비면 전체 허용이므로, 배포 창이 닫힐 때는 콘솔 ingress를 끄거나(`external: false`) 허용 IP를 유지해야 한다.
- **Functions:** Durable Functions classic 1.x Python SDK + Azure Storage 백엔드(`AzureWebJobsStorage__*` 관리 ID 연결). 앱 설정은 `infra/modules/functions.bicep`의 `appSettings` 배열에 추가한다(Bicep이 배열을 통째로 덮어씀).
- **Service Bus pause 허용:** `SERVICEBUS_FQDN`이 빈 문자열이면 이벤트 발행을 건너뛰고 경고 로그만 남긴다.
- **Cosmos 컨테이너:** `lending` 데이터베이스에 P3가 만든다. 앱 ID(오케스트레이터·앱)는 이 DB 범위 Data Contributor다.
- **모델 이름:** 배포 이름은 `gpt-5.4`(분석), `gpt-5.4-mini`(검토자/대체), `Microsoft-Decision-1`(분류). Decision-1의 데이터 평면 스키마는 P3에서 확인하고, 실패 시 `gpt-5.4-mini` 분류로 대체한다(스펙 §7).
- **연기된 P1 보완:** 엔터티 KIND 검증, 가명화기 계좌번호 패턴 보강을 P3에서 가명화기를 쓰기 전에 처리한다.
