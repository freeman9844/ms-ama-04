"""데이터·비밀·이미지 저장소의 감사 로그가 같은 Log Analytics로 모이는지 확인합니다."""
import pytest

STORAGE_AUDIT = {"StorageWrite", "StorageDelete"}


def _diag_for(expanded, scope_fragment: str) -> dict:
    found = [d for d in expanded.of_type("Microsoft.Insights/diagnosticSettings")
             if scope_fragment in d.get("scope", "") + "/" + d["name"]]
    assert len(found) == 1, (scope_fragment, [d["name"] for d in found])
    return found[0]["properties"]


@pytest.mark.parametrize(("scope_fragment", "categories"), [
    ("/kv", {"AuditEvent"}),
    ("/cosmos-", {"ControlPlaneRequests", "DataPlaneRequests"}),
    ("/srch-", {"OperationLogs"}),
    ("/acr", {"ContainerRegistryLoginEvents", "ContainerRegistryRepositoryEvents"}),
    ("/stagt", STORAGE_AUDIT),
    ("/stdat", STORAGE_AUDIT),
    ("/stfn", STORAGE_AUDIT),
])
def test_audit_logs_go_to_workspace(closed, scope_fragment, categories):
    props = _diag_for(closed, scope_fragment)
    assert props["workspaceId"].endswith("/workspaces/log-amatest")
    assert props["logAnalyticsDestinationType"] == "Dedicated"
    assert {log["category"] for log in props["logs"] if log["enabled"]} == categories


@pytest.mark.parametrize("prefix", ["stagt", "stdat", "stfn"])
def test_storage_audit_targets_blob_service(closed, prefix):
    diags = [d for d in closed.of_type("Microsoft.Insights/diagnosticSettings")
             if f"/{prefix}" in d.get("scope", "") + "/" + d["name"]]
    assert all("blobServices/default" in d.get("scope", "") + "/" + d["name"] for d in diags)
