APIM = "Microsoft.ApiManagement/service"
NSG = "Microsoft.Network/networkSecurityGroups"
COGNITIVE_USER = "a97b65f3-24c7-4388-baec-2e87135dc908"
METRICS_PUBLISHER = "3913510d-42f4-4e42-8a64-420c390055eb"


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
