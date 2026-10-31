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
