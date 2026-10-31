SB = "Microsoft.ServiceBus/namespaces"
ROLE_SB_SENDER = "69a216fc-b8fb-44d8-bc22-1f3c2cd27a39"
ROLE_SB_RECEIVER = "4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0"


def test_service_bus_is_premium_keyless_and_private(closed):
    sb = closed.one(SB, "sb-")
    assert sb["sku"] == {"name": "Premium", "tier": "Premium", "capacity": 1}
    assert sb["properties"]["disableLocalAuth"] is True
    rules = closed.one(f"{SB}/networkRuleSets")
    assert rules["properties"]["publicNetworkAccess"] == "Disabled"
    assert rules["properties"]["defaultAction"] == "Deny"
    assert closed.private_endpoints("sb-") == {"namespace": {"privatelink.servicebus.windows.net"}}
    [pe] = [p for p in closed.of_type("Microsoft.Network/privateEndpoints") if p["name"].startswith("pe-sb-")]
    assert pe["name"] == f"pe-{sb['name']}-namespace"


def test_service_bus_topics(closed):
    topics = {t["name"].split("/")[-1] for t in closed.of_type(f"{SB}/topics")}
    assert topics == {"consent", "flow.updated", "alert.raised", "grievance"}


def test_service_bus_roles(closed):
    assert closed.roles_on("sb-") == {ROLE_SB_SENDER, ROLE_SB_RECEIVER}


def test_paused_environment_has_no_service_bus(variant):
    assert variant.of_type(SB) == []
    assert variant.private_endpoints("sb-") == {}
