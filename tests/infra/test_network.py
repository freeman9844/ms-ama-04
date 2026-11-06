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
UNTAGGED = {"Microsoft.Authorization/roleAssignments", "Microsoft.Insights/diagnosticSettings"}


def _subnets(exp):
    vnet = exp.one("Microsoft.Network/virtualNetworks")
    return {s["name"]: s["properties"] for s in vnet["properties"]["subnets"]}


def test_vnet_uses_class_c_private_range(closed):
    vnet = closed.one("Microsoft.Network/virtualNetworks")
    assert vnet["properties"]["addressSpace"]["addressPrefixes"] == ["192.168.0.0/16"]


def test_subnets_and_delegations(closed):
    subnets = _subnets(closed)
    assert set(subnets) == {"snet-agents", "snet-pe", "snet-functions", "snet-apps-int", "snet-appgw", "snet-apim"}
    for name in ("snet-agents", "snet-functions", "snet-apps-int"):
        services = [d["properties"]["serviceName"] for d in subnets[name]["delegations"]]
        assert services == ["Microsoft.App/environments"], name
    assert subnets["snet-pe"]["delegations"] == []
    # App Gateway 네트워크 격리 기능이 켜진 구독은 게이트웨이 서브넷 위임이 필수이고, NSG는 두지 않습니다.
    appgw_services = [d["properties"]["serviceName"] for d in subnets["snet-appgw"]["delegations"]]
    assert appgw_services == ["Microsoft.Network/applicationGateways"]
    assert "networkSecurityGroup" not in subnets["snet-appgw"]
    assert subnets["snet-agents"]["addressPrefix"] == "192.168.0.0/24"
    assert subnets["snet-functions"]["addressPrefix"] == "192.168.2.0/26"
    assert subnets["snet-apps-int"]["addressPrefix"] == "192.168.4.0/24"
    assert subnets["snet-appgw"]["addressPrefix"] == "192.168.7.0/24"


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
    assert params["opsAlertEmail"] == ""


def test_azd_params_pass_ops_alert_email(expand_azd):
    params = expand_azd({"AZURE_ENV_NAME": "amatest", "OPS_ALERT_EMAIL": "ops@example.com"}).root_parameters()
    assert params["opsAlertEmail"] == "ops@example.com"


def test_azd_params_pass_operator_ip_and_flags(expand_azd):
    params = expand_azd({
        "AZURE_ENV_NAME": "amatest", "OPERATOR_IP": "20.30.40.50",
        "SERVICEBUS_ENABLED": "false", "DEPLOY_CAPHOST": "false", "ACR_PUBLIC": "true",
    }).root_parameters()
    assert params["operatorIp"] == "20.30.40.50"
    assert params["serviceBusEnabled"] is False
    assert params["deployCapabilityHost"] is False
    assert params["acrPublic"] is True
