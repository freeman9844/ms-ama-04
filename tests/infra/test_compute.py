import json
import shutil
import subprocess
from pathlib import Path

INFRA = Path(__file__).resolve().parents[2] / "infra"
BICEP = shutil.which("bicep") or str(Path.home() / ".azure" / "bin" / "bicep.exe")
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


def _compile(path: Path) -> dict:
    proc = subprocess.run([BICEP, "build", str(path), "--stdout"], capture_output=True, text=True,
                          encoding="utf-8", check=True)
    return json.loads(proc.stdout)


def test_single_internal_container_apps_environment(closed):
    # 콘솔도 App Gateway 뒤 내부 환경에서 돕니다(외부 환경 cae-web 없음).
    envs = {e["name"]: e["properties"] for e in closed.of_type(CAE)}
    assert set(envs) == {"cae-int-amatest"}
    internal = envs["cae-int-amatest"]
    assert internal["vnetConfiguration"]["internal"] is True
    assert internal["vnetConfiguration"]["infrastructureSubnetId"].endswith("/subnets/snet-apps-int")
    assert internal["workloadProfiles"] == [{"name": "Consumption", "workloadProfileType": "Consumption"}]
    assert internal["appLogsConfiguration"]["destination"] == "log-analytics"


def test_no_web_environment_outputs():
    outputs = _compile(INFRA / "main.bicep")["outputs"]
    assert not [name for name in outputs if name.startswith("CONTAINER_APPS_WEB")]


def test_internal_environment_domain_resolves_in_vnet(closed):
    records = [r for r in closed.of_type("Microsoft.Network/privateDnsZones/A")
               if not r["name"].startswith("azure-api.net/")]
    assert sorted(r["name"].rsplit("/", 1)[-1] for r in records) == ["*", "@"]
    aca_links = [link for link in closed.of_type("Microsoft.Network/privateDnsZones/virtualNetworkLinks")
                 if not link["name"].startswith("privatelink.") and "azure-api.net" not in link["name"]]
    assert len(aca_links) == 1
