"""심사역 콘솔(P4b) IaC: 외부 환경 Container App, IP 허용 목록, Easy Auth, Graph 앱 등록, 최소 권한."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

INFRA = Path(__file__).resolve().parents[2] / "infra"
CA = "Microsoft.App/containerApps"
COSMOS_READER = "00000000-0000-0000-0000-000000000001"
BICEP = shutil.which("bicep") or str(Path.home() / ".azure" / "bin" / "bicep.exe")


def _deployment(exp, name: str) -> dict:
    [d] = [r for r in exp.raw if r["type"] == "Microsoft.Resources/deployments" and r["name"] == name]
    return {k: v["value"] for k, v in d["properties"]["parameters"].items()}


def _compile(path: Path) -> dict:
    proc = subprocess.run([BICEP, "build", str(path), "--stdout"], capture_output=True, text=True,
                          encoding="utf-8", check=True)
    return json.loads(proc.stdout)


def _resources(template: dict) -> list[dict]:
    res = template["resources"]
    return list(res.values()) if isinstance(res, dict) else res


def test_console_runs_in_web_environment_with_own_identity(closed):
    app = closed.one(CA, "ca-console")
    assert app["properties"]["environmentId"].endswith("/managedEnvironments/cae-web-amatest")
    assert list(app["identity"]["userAssignedIdentities"]) == [
        next(i["id"] for i in closed.of_type("Microsoft.ManagedIdentity/userAssignedIdentities")
             if i["name"] == "id-console-amatest")]
    ingress = app["properties"]["configuration"]["ingress"]
    assert ingress["external"] is True and ingress["targetPort"] == 8000
    assert ingress["allowInsecure"] is False
    env = {e["name"] for e in app["properties"]["template"]["containers"][0]["env"]}
    assert env == {"AZURE_CLIENT_ID", "COSMOS_ENDPOINT", "COSMOS_DATABASE", "DATA_BLOB_ENDPOINT",
                   "QUEUE_ENDPOINT", "KEYVAULT_URI"}  # 가명 키는 Key Vault에서만 읽습니다.
    queue = next(e["value"] for e in app["properties"]["template"]["containers"][0]["env"]
                 if e["name"] == "QUEUE_ENDPOINT")
    assert re.fullmatch(r"https://stfn[0-9a-z]+\.queue\.core\.windows\.net/", queue)


def test_console_ip_allow_list_fails_closed(closed, opened):
    # 운영자 IP가 없으면 TEST-NET 주소만 허용해 아무도 들어오지 못하게 합니다.
    rules = closed.one(CA, "ca-console")["properties"]["configuration"]["ingress"]["ipSecurityRestrictions"]
    assert rules == [{"name": "allow-0", "ipAddressRange": "192.0.2.0/32", "action": "Allow"}]
    rules = opened.one(CA, "ca-console")["properties"]["configuration"]["ingress"]["ipSecurityRestrictions"]
    assert rules == [{"name": "allow-0", "ipAddressRange": "20.30.40.50/32", "action": "Allow"}]


def test_vnet_only_apps_have_no_ip_rules_or_auth(closed):
    for name in ("ca-policy", "ca-mockapi"):
        ingress = closed.one(CA, name)["properties"]["configuration"]["ingress"]
        assert ingress.get("ipSecurityRestrictions") in (None, []), name
    assert not [r for r in closed.of_type(f"{CA}/authConfigs") if not r["name"].startswith("ca-console")]


def test_console_image_param(closed, variant):
    assert _deployment(closed, "ca-console")["image"] == ""
    assert _deployment(variant, "ca-console")["image"] == "acramatest.azurecr.io/console:v1"


def test_console_auth_registration_wiring(closed):
    params = _deployment(closed, "console-auth")
    # PSRule은 환경 기본 도메인을 모르므로 앞뒤만 확인합니다.
    assert params["redirectUri"].startswith("https://ca-console.")
    assert params["redirectUri"].endswith("/.auth/login/aad/callback")
    assert params["operatorPrincipalId"] == "11111111-1111-1111-1111-111111111111"


def test_console_easy_auth_requires_entra_login(console_app):
    [auth] = console_app.of_type(f"{CA}/authConfigs")
    props = auth["properties"]
    assert props["platform"]["enabled"] is True
    assert props["globalValidation"] == {"unauthenticatedClientAction": "RedirectToLoginPage",
                                         "redirectToProvider": "azureactivedirectory"}
    aad = props["identityProviders"]["azureActiveDirectory"]
    assert aad["registration"]["clientId"] == "22222222-2222-2222-2222-222222222222"
    assert aad["registration"]["openIdIssuer"].startswith("https://login.microsoftonline.com/")
    assert aad["registration"]["openIdIssuer"].endswith("/v2.0")
    assert "clientSecretSettingName" not in aad["registration"]  # ID 토큰만(비밀 없음)
    assert aad["validation"]["allowedAudiences"] == ["22222222-2222-2222-2222-222222222222"]


def test_console_app_registration_defines_roles_and_requires_assignment():
    template = _compile(INFRA / "modules" / "console-auth.bicep")
    by_type = {r["type"]: r for r in _resources(template)}
    app = by_type["Microsoft.Graph/applications@v1.0"]
    assert app["properties"]["signInAudience"] == "AzureADMyOrg"
    [roles] = [c for c in app["properties"]["copy"] if c["name"] == "appRoles"]
    assert roles["input"]["allowedMemberTypes"] == ["User"]
    assert {r["value"] for r in template["variables"]["roles"]} == {"CreditOfficer", "CreditManager", "Auditor"}
    web = app["properties"]["web"]
    assert web["implicitGrantSettings"] == {"enableIdTokenIssuance": True, "enableAccessTokenIssuance": False}
    assert web["redirectUris"] == ["[parameters('redirectUri')]"]
    sp = by_type["Microsoft.Graph/servicePrincipals@v1.0"]
    assert sp["properties"]["appRoleAssignmentRequired"] is True
    assert "Microsoft.Graph/appRoleAssignedTo@v1.0" in by_type


# 콘솔 ID는 읽기 + 결정 큐 보내기만 합니다(쓰기·삭제 없음).
EXPECTED_CONSOLE_GRANTS = {
    "keyvault": {"kvValueReader"},
    "storage-data": {"blobReader"},
    "storage-functions": {"queueSender"},
    "acr": {"acrPull"},
    "cosmos": {COSMOS_READER},
}


def test_console_identity_grants_are_least_privilege(closed):
    grants: dict[str, set[str]] = {}
    for module in _resources(_compile(INFRA / "resources.bicep")):
        text = json.dumps(module.get("properties", {}).get("parameters", {}))
        found = re.findall(r"consolePrincipalId\.value.{0,80}?(?:role'\)\.(\w+)|roleDefinitionGuid', '([0-9a-f-]+)')",
                           text)
        if found:
            grants[module["name"]] = {a or b for a, b in found}
    assert grants == EXPECTED_CONSOLE_GRANTS
    # 계정 범위가 아니라 lending DB 범위의 읽기 전용입니다(smoke는 계정 범위).
    assert any(a["properties"]["roleDefinitionId"].endswith(COSMOS_READER)
               and a["properties"]["scope"].endswith("/dbs/lending")
               for a in closed.of_type("Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments"))


@pytest.mark.parametrize("output", ["CONSOLE_URL", "CONSOLE_CLIENT_ID", "CONSOLE_APP_NAME"])
def test_console_outputs_exposed_to_azd(output):
    assert output in _compile(INFRA / "main.bicep")["outputs"]


def test_console_easy_auth_can_be_switched_off_for_demo(console_app_noauth, expand_azd):
    # 증분 배포는 리소스를 지우지 않으므로 authConfigs를 남기고 platform만 끕니다.
    [auth] = console_app_noauth.of_type(f"{CA}/authConfigs")
    assert auth["properties"]["platform"]["enabled"] is False
    demo = expand_azd({"CONSOLE_AUTH_ENABLED": "false", "OPERATOR_IP": "closed"})
    assert _deployment(demo, "ca-console")["authEnabled"] is False
    env = {e["name"]: e.get("value") for e in demo.one(CA, "ca-console")["properties"]["template"]["containers"][0]["env"]}
    assert env["CONSOLE_AUTH_MODE"] == "demo"
    default = expand_azd({"OPERATOR_IP": "closed"})
    assert _deployment(default, "ca-console")["authEnabled"] is True
