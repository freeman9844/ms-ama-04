APIM = "Microsoft.ApiManagement/service"
NSG = "Microsoft.Network/networkSecurityGroups"
COGNITIVE_USER = "a97b65f3-24c7-4388-baec-2e87135dc908"
METRICS_PUBLISHER = "3913510d-42f4-4e42-8a64-420c390055eb"
GATEWAYS = {
    "gw-analyst": 20000,
    "gw-early-warning": 20000,
    "gw-reviewer": 15000,
    "gw-data-assembly": 15000,
    "gw-txn-classifier": 10000,
    "gw-collections": 10000,
    "gw-consent-notice": 5000,
    "gw-grievance": 5000,
    "gw-probe": 1000,
}


def _leaf(resource: dict, depth: int = 1) -> str:
    return resource["name"].split("/")[-depth]


def _by_leaf(exp, type_: str, depth: int = 1) -> dict[str, dict]:
    return {_leaf(r, depth): r for r in exp.of_type(type_)}


def _apim(exp) -> dict:
    return exp.one(APIM, "apim-")


def test_apim_developer_internal_vnet(closed):
    apim = _apim(closed)
    assert apim["sku"] == {"name": "Developer", "capacity": 1}
    assert apim["identity"]["type"] == "SystemAssigned"
    props = apim["properties"]
    assert props["virtualNetworkType"] == "Internal"
    assert props["virtualNetworkConfiguration"]["subnetResourceId"].endswith("/subnets/snet-apim")
    assert "publicIpAddressId" not in props


def test_apim_nsg_rules(closed):
    rules = {r["name"]: r["properties"] for r in closed.one(NSG, "nsg-apim")["properties"]["securityRules"]}
    assert rules["allow-apim-management"]["destinationPortRange"] == "3443"
    assert rules["allow-apim-management"]["sourceAddressPrefix"] == "ApiManagement"
    assert rules["allow-lb-6390"]["destinationPortRange"] == "6390"
    assert rules["allow-vnet-https"]["sourceAddressPrefix"] == "VirtualNetwork"
    out = {r["destinationAddressPrefix"] for r in rules.values() if r["direction"] == "Outbound"}
    assert out == {"Storage", "Sql", "AzureKeyVault", "AzureMonitor"}
    assert not any(r["sourceAddressPrefix"] == "Internet" for r in rules.values())


def test_apim_roles(closed):
    assert COGNITIVE_USER in closed.roles_on("ais-")
    assert METRICS_PUBLISHER in closed.roles_on("appi-")


def test_apim_logger_uses_managed_identity(closed):
    logger = closed.one("Microsoft.ApiManagement/service/loggers", "appi")
    assert logger["name"].startswith("apim-")
    creds = logger["properties"]["credentials"]
    assert creds["identityClientId"] == "SystemAssigned"
    assert "instrumentationKey" not in creds
    diag = closed.one("Microsoft.ApiManagement/service/diagnostics", "applicationinsights")
    assert diag["name"].startswith("apim-")
    assert diag["properties"]["metrics"] is True
    assert diag["properties"]["frontend"]["request"]["body"]["bytes"] == 0


def test_apim_private_a_record(closed):
    record = closed.one("Microsoft.Network/privateDnsZones/A", "apim-")
    assert record["name"].startswith("azure-api.net/apim-")
    assert record["properties"]["ttl"] == 300


def test_backends_use_managed_identity(closed):
    backends = _by_leaf(closed, "Microsoft.ApiManagement/service/backends")
    assert set(backends) == {"foundry-openai", "foundry-decision", "content-safety"}
    assert backends["foundry-openai"]["properties"]["url"].endswith(".openai.azure.com/openai")
    assert backends["foundry-decision"]["properties"]["url"].endswith(".services.ai.azure.com")
    safety = backends["content-safety"]["properties"]
    assert safety["url"].endswith(".cognitiveservices.azure.com")
    assert safety["credentials"]["managedIdentity"]["resource"] == "https://cognitiveservices.azure.com"
    assert all("header" not in b["properties"].get("credentials", {}) for b in backends.values())


def test_openai_api_policy(closed):
    apis = _by_leaf(closed, "Microsoft.ApiManagement/service/apis")
    openai = apis["openai"]["properties"]
    assert openai["path"] == "openai"
    assert openai["subscriptionRequired"] is True
    assert openai["subscriptionKeyParameterNames"]["header"] == "api-key"
    ops = _by_leaf(closed, "Microsoft.ApiManagement/service/apis/operations")
    assert ops["chat-completions"]["properties"]["urlTemplate"] == "/deployments/{deployment-id}/chat/completions"
    policy = _by_leaf(closed, "Microsoft.ApiManagement/service/apis/policies", 2)["openai"]["properties"]["value"]
    assert 'llm-content-safety backend-id="content-safety" shield-prompt="true"' in policy
    assert policy.count('threshold="4"') == 4
    assert '<set-header name="api-key" exists-action="delete" />' in policy
    assert 'authentication-managed-identity resource="https://cognitiveservices.azure.com"' in policy
    assert 'set-backend-service backend-id="foundry-openai"' in policy
    assert 'llm-emit-token-metric namespace="ama-llm"' in policy
    assert policy.index("llm-content-safety") < policy.index("<base />")


def test_decision_api_validates_entra_token(closed):
    decision = _by_leaf(closed, "Microsoft.ApiManagement/service/apis")["decision"]["properties"]
    assert decision["path"] == "decision"
    assert decision["subscriptionRequired"] is False
    ops = _by_leaf(closed, "Microsoft.ApiManagement/service/apis/operations")
    assert ops["systemone"]["properties"]["urlTemplate"] == "/providers/microsoft/v1/systemone"
    policy = _by_leaf(closed, "Microsoft.ApiManagement/service/apis/policies", 2)["decision"]["properties"]["value"]
    assert "<validate-azure-ad-token" in policy and "__TENANT__" not in policy
    assert "<audience>https://cognitiveservices.azure.com</audience>" in policy
    assert policy.count("<value>") == 2 and "__APPIDS__" not in policy
    assert 'rate-limit-by-key calls="120" renewal-period="60"' in policy
    assert 'set-backend-service backend-id="foundry-decision"' in policy


def test_products_policies_and_subscriptions(closed):
    products = _by_leaf(closed, "Microsoft.ApiManagement/service/products")
    assert set(products) == set(GATEWAYS)
    assert all(p["properties"]["subscriptionRequired"] for p in products.values())
    links = closed.of_type("Microsoft.ApiManagement/service/products/apis")
    assert sorted(_leaf(link, 2) for link in links) == sorted(GATEWAYS)
    assert {_leaf(link) for link in links} == {"openai"}
    policies = _by_leaf(closed, "Microsoft.ApiManagement/service/products/policies", 2)
    for name, tpm in GATEWAYS.items():
        value = policies[name]["properties"]["value"]
        assert f'tokens-per-minute="{tpm}"' in value
        assert 'estimate-prompt-tokens="false"' in value
    subs = _by_leaf(closed, "Microsoft.ApiManagement/service/subscriptions")
    assert set(subs) == set(GATEWAYS)
    for name, sub in subs.items():
        assert sub["properties"]["scope"].endswith(f"/products/{name}")
        assert sub["properties"]["displayName"] == name
