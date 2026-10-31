SITE = "Microsoft.Web/sites"


def _settings(site: dict) -> dict[str, str]:
    return {s["name"]: s["value"] for s in site["properties"]["siteConfig"]["appSettings"]}


def test_flex_consumption_plan(closed):
    plan = closed.one("Microsoft.Web/serverfarms", "plan-")
    assert plan["sku"] == {"name": "FC1", "tier": "FlexConsumption"}
    assert plan["properties"]["reserved"] is True


def test_function_app_runtime_identity_and_vnet(closed):
    site = closed.one(SITE, "func-")
    props = site["properties"]
    assert site["kind"] == "functionapp,linux"
    assert site["tags"]["azd-service-name"] == "orchestrator"
    assert list(site["identity"]["userAssignedIdentities"])[0].endswith("/id-orch-amatest")
    assert props["virtualNetworkSubnetId"].endswith("/subnets/snet-functions")
    assert props["httpsOnly"] is True
    config = props["functionAppConfig"]
    assert config["runtime"] == {"name": "python", "version": "3.12"}
    storage = config["deployment"]["storage"]
    assert storage["value"].endswith("deploymentpackage")
    assert storage["authentication"]["type"] == "UserAssignedIdentity"


def test_function_app_uses_identity_based_host_storage(closed):
    settings = _settings(closed.one(SITE, "func-"))
    assert settings["AzureWebJobsStorage__accountName"].startswith("stfn")
    assert settings["AzureWebJobsStorage__credential"] == "managedidentity"
    assert "AzureWebJobsStorage" not in settings  # 연결 문자열(공유 키) 금지
    assert "FUNCTIONS_WORKER_RUNTIME" not in settings  # Flex는 functionAppConfig.runtime 사용
    assert settings["APPLICATIONINSIGHTS_AUTHENTICATION_STRING"].endswith(";Authorization=AAD")


def test_function_app_private_endpoint(closed):
    assert closed.private_endpoints("func-") == {"sites": {"privatelink.azurewebsites.net"}}
