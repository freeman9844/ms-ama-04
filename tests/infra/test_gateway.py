import json
import re

from tests.infra.test_console import INFRA

APIM = "Microsoft.ApiManagement/service"
NSG = "Microsoft.Network/networkSecurityGroups"
COGNITIVE_USER = "a97b65f3-24c7-4388-baec-2e87135dc908"
METRICS_PUBLISHER = "3913510d-42f4-4e42-8a64-420c390055eb"
GATEWAYS = {
    "gw-analyst": 150000,
    "gw-early-warning": 150000,
    "gw-reviewer": 15000,
    "gw-data-assembly": 17000,
    "gw-txn-classifier": 20000,
    "gw-collections": 10000,
    "gw-consent-notice": 5000,
    "gw-grievance": 5000,
    "gw-officer-chat": 250000,
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


def test_gateway_logs_to_azure_monitor(closed):
    diag = _by_leaf(closed, "Microsoft.ApiManagement/service/diagnostics")["azuremonitor"]["properties"]
    assert diag["loggerId"].endswith("/loggers/azuremonitor")
    assert diag["logClientIp"] is False
    for side in ("frontend", "backend"):
        for direction in ("request", "response"):
            part = diag[side][direction]
            assert part["headers"] == [], f"{side}.{direction} headers"
            assert part["body"]["bytes"] == 0, f"{side}.{direction} body"


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
    assert '<set-variable name="promptChars"' in policy
    assert "context.Request.Body.As<JObject>(preserveContent: true)" in policy
    assert policy.index('name="promptChars"') < policy.index("<llm-content-safety")
    assert (
        'llm-content-safety backend-id="content-safety" '
        """shield-prompt='@((int)context.Variables["promptChars"] <= 10000)'"""
    ) in policy
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


CONNECTION = "Microsoft.CognitiveServices/accounts/projects/connections"
JOB = "Microsoft.App/jobs"


def _gateway_connections(exp) -> dict[str, dict]:
    return {k: v["properties"] for k, v in _by_leaf(exp, CONNECTION).items() if k.startswith("gw-")}


def test_gateway_connections(closed):
    conns = _gateway_connections(closed)
    assert set(conns) == set(GATEWAYS)
    for props in conns.values():
        assert props["category"] == "ApiManagement"
        assert re.fullmatch(r"https://apim-[a-z0-9]+\.azure-api\.net/openai", props["target"])
        assert props["authType"] == "ApiKey"
        assert props["isSharedToAll"] is True
        meta = props["metadata"]
        assert meta["deploymentInPath"] == "true"
        assert meta["inferenceAPIVersion"] == "2025-04-01-preview"
        # PSRule은 '['로 시작하는 문자열을 ARM 리터럴 이스케이프('[[')로 펼칩니다.
        models = meta["models"].removeprefix("[") if meta["models"].startswith("[[") else meta["models"]
        assert {m["name"] for m in json.loads(models)} == {"gpt-5.4", "gpt-5.6-luna", "gpt-6.1-sol", "gpt-5.6-sol", "gpt-6-luna"}


def test_connection_keys_from_list_secrets():
    text = (INFRA / "modules" / "foundry" / "gateway-connections.bicep").read_text(encoding="utf-8")
    assert "listSecrets(resourceId('Microsoft.ApiManagement/service/subscriptions', apimName, c)" in text
    assert "@batchSize(1)" in text


def test_no_secret_outputs():
    for path in (INFRA / "modules" / "apim.bicep", INFRA / "modules" / "foundry" / "gateway-connections.bicep"):
        outputs = [line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("output ")]
        assert not [line for line in outputs if re.search(r"key|secret|listSecrets", line, re.IGNORECASE)], path


def _func_settings(exp) -> dict[str, str]:
    site = exp.one("Microsoft.Web/sites", "func-")
    return {s["name"]: s["value"] for s in site["properties"]["siteConfig"]["appSettings"]}


def _eval_env(exp) -> dict[str, str]:
    container = exp.one(JOB, "job-eval")["properties"]["template"]["containers"][0]
    return {e["name"]: e.get("value") for e in container["env"]}


def test_gateway_switch_in_app_settings(closed, variant):
    on = _func_settings(closed)
    assert on["AI_GATEWAY"] == "apim"
    assert re.fullmatch(r"https://apim-[a-z0-9]+\.azure-api\.net", on["AI_GATEWAY_URL"])
    assert _func_settings(variant)["AI_GATEWAY"] == "off"
    assert _eval_env(closed)["AI_GATEWAY"] == "apim"
    assert _eval_env(variant)["AI_GATEWAY"] == "off"
    assert _eval_env(closed)["AI_GATEWAY_URL"] == on["AI_GATEWAY_URL"]


def test_azd_routing_switch(expand_azd):
    assert expand_azd({}).root_parameters()["aiGatewayRouting"] is True
    assert expand_azd({"AI_GATEWAY_ROUTING": "false"}).root_parameters()["aiGatewayRouting"] is False
