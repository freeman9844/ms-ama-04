KV = "Microsoft.KeyVault/vaults"
ROLE_KV_VALUE_READER = "4633458b-17de-408a-b874-0445c86b69e6"
ROLE_KV_VALUE_ADMIN = "b86a8fe4-44ce-4948-aee5-eccb2c155cd7"
ROLE_METRICS_PUBLISHER = "3913510d-42f4-4e42-8a64-420c390055eb"
OPERATOR = "11111111-1111-1111-1111-111111111111"


def test_key_vault_is_rbac_only_with_private_endpoint(closed):
    props = closed.one(KV, "kv")["properties"]
    assert props["enableRbacAuthorization"] is True
    assert props["networkAcls"]["bypass"] == "AzureServices"
    assert props["publicNetworkAccess"] == "Disabled"
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


def test_workload_user_assigned_identities(closed):
    names = {r["name"] for r in closed.of_type("Microsoft.ManagedIdentity/userAssignedIdentities")}
    assert names == {"id-orch-amatest", "id-apps-amatest", "id-smoke-amatest", "id-console-amatest", "id-eval-amatest",
                     "id-ops-amatest"}
