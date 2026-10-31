import json
import shutil
import subprocess
from pathlib import Path

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


def test_cosmos_sql_role_assignments_are_serialized(closed):
    # Cosmos는 계정 단위 배타 잠금 때문에 SQL 역할 할당을 동시에 쓰면 409로 실패합니다.
    # PSRule 펼치기에는 copy 모드가 남지 않으므로 컴파일된 ARM 템플릿을 확인합니다.
    bicep = shutil.which("bicep") or str(Path.home() / ".azure" / "bin" / "bicep.exe")
    module = Path(__file__).resolve().parents[2] / "infra" / "modules" / "cosmos.bicep"
    proc = subprocess.run([bicep, "build", str(module), "--stdout"], capture_output=True, text=True,
                          encoding="utf-8", check=True)
    resources = json.loads(proc.stdout)["resources"]
    [sql_ra] = [r for r in (resources.values() if isinstance(resources, dict) else resources)
                if r["type"] == "Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments"]
    assert sql_ra["copy"]["mode"] == "serial"
    assert sql_ra["copy"]["batchSize"] == 1
    assert any("'pe-" in d for d in sql_ra["dependsOn"])  # PE 승인(계정 쓰기)과도 겹치지 않게
