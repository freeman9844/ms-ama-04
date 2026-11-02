import json
import shutil
import subprocess
from pathlib import Path

AIS = "Microsoft.CognitiveServices/accounts"
ROLE_FOUNDRY_USER = "53ca6127-db72-4b80-b1b0-d745d6d5456d"
ROLE_OPENAI_USER = "5e0bd9bd-7b93-4f28-af87-19fc36ad61bd"
ROLE_BLOB_OWNER = "b7e6dc6d-f1e8-4753-8033-0f276bb0955b"
ROLE_BLOB_CONTRIBUTOR = "ba92f5b4-2d11-453d-a403-e96b0029c9fe"
ROLE_COSMOS_OPERATOR = "230815da-be43-4aae-9cb4-875f7bd000aa"
ROLE_SEARCH_INDEX_CONTRIBUTOR = "8ebe5a00-799e-43f5-93ac-243d3dce84a7"
ROLE_SEARCH_SERVICE_CONTRIBUTOR = "7ca78c08-252a-4471-8644-bb5ff32d4ba0"
COSMOS_CONTRIBUTOR = "00000000-0000-0000-0000-000000000002"
MODELS = {"gpt-5.4", "gpt-5.4-mini", "decision-1"}


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
    assert deployments["decision-1"]["properties"]["model"]["format"] == "Microsoft"
    assert deployments["decision-1"]["properties"]["model"]["name"] == "Microsoft-Decision-1"
    assert deployments["gpt-5.4"]["properties"]["model"]["version"] == "2026-03-05"


def test_model_deployment_names_avoid_reserved_words(closed):
    # ARM은 리소스 이름에 예약어(MICROSOFT 등)를 허용하지 않습니다(ContainsReservedWord).
    assert not [n for n in _deployments(closed) if "microsoft" in n.lower()]


def test_account_private_endpoint_waits_for_model_deployments():
    # 모델 배포 중에는 계정이 Accepted 상태라 PE를 동시에 만들면 AccountProvisioningStateInvalid로 실패합니다.
    bicep = shutil.which("bicep") or str(Path.home() / ".azure" / "bin" / "bicep.exe")
    module = Path(__file__).resolve().parents[2] / "infra" / "modules" / "foundry" / "account.bicep"
    proc = subprocess.run([bicep, "build", str(module), "--stdout"], capture_output=True, text=True,
                          encoding="utf-8", check=True)
    resources = json.loads(proc.stdout)["resources"]
    if isinstance(resources, dict):
        resources = list(resources.values())
    [pe] = [r for r in resources if r["type"] == "Microsoft.Resources/deployments"]
    assert any(d == "deployments" or "/deployments'" in d for d in pe.get("dependsOn", []))


def test_model_sku_parameters_switch_to_data_zone(variant):
    assert {d["sku"]["name"] for d in _deployments(variant).values()} == {"DataZoneStandard"}


def test_account_data_plane_roles(closed):
    assert closed.roles_on("ais-") == {ROLE_FOUNDRY_USER, ROLE_OPENAI_USER}


def test_project_connections_use_entra_auth(closed):
    connections = closed.of_type(f"{AIS}/projects/connections")
    # App Insights 트레이스 연결(P6b)은 프로젝트 관리 ID를 쓴다(test_ops_job.py).
    byo = [c for c in connections if c["properties"]["category"] != "AppInsights"]
    assert {c["properties"]["category"] for c in byo} == {"CosmosDB", "AzureStorageAccount", "CognitiveSearch"}
    assert {c["properties"]["authType"] for c in byo} == {"AAD"}


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
