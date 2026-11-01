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


def test_monitoring_subscriptions_are_session_ordered_and_filtered(closed):
    subs = {"/".join(s["name"].split("/")[-2:]): s for s in closed.of_type(f"{SB}/topics/subscriptions")}
    assert set(subs) == {"flow.updated/early-warning", "alert.raised/collections"}
    for sub in subs.values():
        props = sub["properties"]
        assert props["requiresSession"] is True and props["lockDuration"] == "PT5M"
        assert props["maxDeliveryCount"] == 5 and props["deadLetteringOnMessageExpiration"] is True
    rules = {r["name"].split("/")[-2]: r["properties"] for r in closed.of_type(f"{SB}/topics/subscriptions/rules")}
    assert set(rules) == {"early-warning", "collections"}
    # 스모크 메시지(subject 없음)는 구독에 들어가지 않는다.
    assert rules["early-warning"] == {"filterType": "CorrelationFilter", "correlationFilter": {"label": "flow.updated"}}
    assert rules["collections"] == {"filterType": "CorrelationFilter", "correlationFilter": {"label": "alert.raised"}}
    assert all(r["name"].endswith("/$Default") for r in closed.of_type(f"{SB}/topics/subscriptions/rules"))
