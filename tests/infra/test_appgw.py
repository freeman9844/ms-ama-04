"""App Gateway WAF v2 콘솔 진입점 IaC: 공용 IP, WAF 정책(Prevention, 운영자 IP만), 게이트웨이, 진단, 최소 권한."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

INFRA = Path(__file__).resolve().parents[2] / "infra"
BICEP = shutil.which("bicep") or str(Path.home() / ".azure" / "bin" / "bicep.exe")
AGW = "Microsoft.Network/applicationGateways"
WAF = "Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies"
PIP = "Microsoft.Network/publicIPAddresses"
CERT = "https://kvamatest.vault.azure.net/secrets/appgw-console"


def _compile(path: Path) -> dict:
    proc = subprocess.run([BICEP, "build", str(path), "--stdout"], capture_output=True, text=True,
                          encoding="utf-8", check=True)
    return json.loads(proc.stdout)


def _resources(template: dict) -> list[dict]:
    res = template["resources"]
    return list(res.values()) if isinstance(res, dict) else res


def _by_name(items: list[dict]) -> dict[str, dict]:
    return {i["name"]: i["properties"] for i in items}


def test_public_ip_always_exists_with_dns_label(closed):
    # 인증서 CN이 될 DNS 이름은 게이트웨이보다 먼저(첫 프로비저닝에서) 있어야 합니다.
    pip = closed.one(PIP, "pip-agw-amatest")
    assert pip["sku"]["name"] == "Standard"
    props = pip["properties"]
    assert props["publicIPAllocationMethod"] == "Static"
    assert props["publicIPAddressVersion"] == "IPv4"
    assert re.fullmatch(r"amatest-[0-9a-z]{13}", props["dnsSettings"]["domainNameLabel"])


def test_gateway_waits_for_certificate(closed):
    assert closed.of_type(AGW) == []
    assert closed.of_type(WAF) == []


def test_gateway_sku_identity_and_tls(opened):
    gw = opened.one(AGW, "agw-amatest")
    props = gw["properties"]
    assert props["sku"] == {"name": "WAF_v2", "tier": "WAF_v2"}
    assert props["autoscaleConfiguration"] == {"minCapacity": 0, "maxCapacity": 2}
    assert gw["identity"]["type"] == "UserAssigned"
    assert list(gw["identity"]["userAssignedIdentities"]) == [
        next(i["id"] for i in opened.of_type("Microsoft.ManagedIdentity/userAssignedIdentities")
             if i["name"] == "id-appgw-amatest")]
    assert props["sslCertificates"] == [{"name": "console-tls", "properties": {"keyVaultSecretId": CERT}}]
    assert props["sslPolicy"] == {"policyType": "Predefined", "policyName": "AppGwSslPolicy20220101"}
    [ipconf] = props["gatewayIPConfigurations"]
    assert ipconf["properties"]["subnet"]["id"].endswith("/subnets/snet-appgw")
    [frontend] = props["frontendIPConfigurations"]
    assert frontend["properties"]["publicIPAddress"]["id"].endswith("/pip-agw-amatest")
    assert props["firewallPolicy"]["id"].endswith("/waf-amatest")


def test_gateway_routes_https_to_console_and_redirects_http(opened):
    props = opened.one(AGW, "agw-amatest")["properties"]
    listeners = _by_name(props["httpListeners"])
    assert set(listeners) == {"https", "http"}
    assert listeners["https"]["protocol"] == "Https"
    assert listeners["https"]["frontendPort"]["id"].endswith("/frontendPorts/port-443")
    assert listeners["https"]["sslCertificate"]["id"].endswith("/sslCertificates/console-tls")
    assert listeners["http"]["protocol"] == "Http"
    assert listeners["http"]["frontendPort"]["id"].endswith("/frontendPorts/port-80")

    redirect = _by_name(props["redirectConfigurations"])["http-to-https"]
    assert redirect["redirectType"] == "Permanent"
    assert redirect["includePath"] is True and redirect["includeQueryString"] is True
    assert redirect["targetListener"]["id"].endswith("/httpListeners/https")

    rules = _by_name(props["requestRoutingRules"])
    assert set(rules) == {"https-console", "http-redirect"}
    assert rules["https-console"]["priority"] == 100
    assert rules["https-console"]["httpListener"]["id"].endswith("/httpListeners/https")
    assert rules["https-console"]["backendAddressPool"]["id"].endswith("/backendAddressPools/console")
    assert rules["https-console"]["backendHttpSettings"]["id"].endswith("/backendHttpSettingsCollection/console-https")
    assert rules["http-redirect"]["priority"] == 200
    assert rules["http-redirect"]["httpListener"]["id"].endswith("/httpListeners/http")
    assert rules["http-redirect"]["redirectConfiguration"]["id"].endswith("/redirectConfigurations/http-to-https")

    [address] = _by_name(props["backendAddressPools"])["console"]["backendAddresses"]
    assert address["fqdn"].startswith("ca-console.")  # PSRule은 내부 환경 기본 도메인을 모릅니다.
    settings = _by_name(props["backendHttpSettingsCollection"])["console-https"]
    assert (settings["port"], settings["protocol"]) == (443, "Https")
    assert settings["pickHostNameFromBackendAddress"] is True
    assert settings["requestTimeout"] == 180  # 심사역 채팅(429 백오프 재시도) 응답을 기다립니다.
    assert settings["probe"]["id"].endswith("/probes/console-health")
    probe = _by_name(props["probes"])["console-health"]
    assert (probe["protocol"], probe["path"]) == ("Https", "/healthz")
    assert probe["pickHostNameFromBackendHttpSettings"] is True
    assert probe["match"] == {"statusCodes": ["200-399"]}  # Easy Auth 302도 정상


def test_waf_policy_prevention_and_operator_rule(opened):
    props = opened.one(WAF, "waf-amatest")["properties"]
    assert props["policySettings"]["state"] == "Enabled"
    assert props["policySettings"]["mode"] == "Prevention"
    assert props["policySettings"]["requestBodyCheck"] is True
    [rule] = props["customRules"]
    assert (rule["name"], rule["priority"], rule["ruleType"], rule["action"], rule["state"]) == (
        "allowOperatorOnly", 10, "MatchRule", "Block", "Enabled")
    [cond] = rule["matchConditions"]
    assert cond["matchVariables"] == [{"variableName": "RemoteAddr"}]
    assert cond["operator"] == "IPMatch"
    assert cond["negationConditon"] is True  # API 철자 그대로(오타가 정식 이름)
    assert cond["matchValues"] == ["20.30.40.50/32"]
    assert props["managedRules"]["managedRuleSets"] == [
        {"ruleSetType": "Microsoft_DefaultRuleSet", "ruleSetVersion": "2.1"}]


def test_waf_rule_fails_closed_without_ip(expand_azd):
    exp = expand_azd({"APPGW_CERT_SECRET_ID": CERT, "OPERATOR_IP": "closed"})
    assert exp.root_parameters()["appGwCertSecretId"] == CERT
    [rule] = exp.one(WAF, "waf-")["properties"]["customRules"]
    assert rule["matchConditions"][0]["matchValues"] == ["192.0.2.0/32"]


def test_appgw_identity_reads_key_vault_values_only():
    grants: dict[str, set[str]] = {}
    for module in _resources(_compile(INFRA / "resources.bicep")):
        text = json.dumps(module.get("properties", {}).get("parameters", {}))
        found = re.findall(r"appgwPrincipalId\.value.{0,80}?(?:role'\)\.(\w+)|roleDefinitionGuid', '([0-9a-f-]+)')",
                           text)
        if found:
            grants[module["name"]] = {a or b for a, b in found}
    assert grants == {"keyvault": {"kvValueReader"}}


def test_gateway_logs_go_to_workspace(opened):
    diags = [d for d in opened.of_type("Microsoft.Insights/diagnosticSettings")
             if "agw-amatest" in d.get("scope", "") + d["name"]]
    assert len(diags) == 1
    props = diags[0]["properties"]
    assert props["workspaceId"].endswith("/workspaces/log-amatest")
    assert props["logAnalyticsDestinationType"] == "Dedicated"
    assert {log["category"] for log in props["logs"] if log["enabled"]} == {
        "ApplicationGatewayAccessLog", "ApplicationGatewayFirewallLog"}
    assert props["metrics"] == [{"category": "AllMetrics", "enabled": True}]


@pytest.mark.parametrize("output", ["APPGW_NAME", "APPGW_FQDN"])
def test_appgw_outputs_exposed_to_azd(output):
    assert output in _compile(INFRA / "main.bicep")["outputs"]
