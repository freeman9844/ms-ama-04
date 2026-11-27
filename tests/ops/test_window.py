import pytest

from ops.azcli import OpsError
from ops.azdenv import parse_env_values
from ops.window import close_window, open_window, validate_operator_ip, window_status


@pytest.fixture(autouse=True)
def _cert_already_current(monkeypatch):
    # 인증서 확인은 test_appgw_cert.py가 다룹니다. 여기서는 "바뀐 것 없음"으로 둡니다.
    monkeypatch.setattr("ops.window.ensure_console_cert", lambda runner: False)

PROVISIONED = {"AZURE_RESOURCE_GROUP": "rg-amalend", "AI_ACCOUNT_NAME": "ais-x", "OPERATOR_IP": "closed"}


def test_parse_env_values_handles_quotes_and_blank_lines():
    text = 'A="1"\n\nB="say \\"hi\\""\n# comment\nC=plain\n'
    assert parse_env_values(text) == {"A": "1", "B": 'say "hi"', "C": "plain"}


@pytest.mark.parametrize("bad", ["192.168.1.1", "10.0.0.1", "127.0.0.1", "not-an-ip", "2001:db8::1", ""])
def test_validate_operator_ip_rejects_non_public_ipv4(bad):
    with pytest.raises(OpsError):
        validate_operator_ip(bad)


def test_validate_operator_ip_accepts_public_ipv4():
    assert validate_operator_ip(" 20.30.40.50 ") == "20.30.40.50"


def test_open_window_sets_ip_and_provisions(fake_runner):
    runner = fake_runner(PROVISIONED)
    assert open_window(runner, "20.30.40.50") == "20.30.40.50"
    assert runner.env["OPERATOR_IP"] == "20.30.40.50"
    assert runner.env["CONSOLE_IP"] == "20.30.40.50"
    assert runner.env["ACR_PUBLIC"] == "false"
    assert runner.commands("azd", "provision") == [["azd", "provision", "--no-prompt"]]


def test_open_window_with_acr_build_window(fake_runner):
    runner = fake_runner(PROVISIONED)
    open_window(runner, "20.30.40.50", acr=True)
    assert runner.env["ACR_PUBLIC"] == "true"


def test_open_window_skips_caphost_after_first_provision(fake_runner):
    # Review Focus #3: 이미 프로비저닝된 환경에서 caphost를 다시 PUT 하지 않습니다.
    runner = fake_runner(PROVISIONED)
    open_window(runner, "20.30.40.50")
    assert runner.env["DEPLOY_CAPHOST"] == "false"
    assert runner.index("azd", "env", "set", "DEPLOY_CAPHOST") < runner.index("azd", "provision")


def test_open_window_before_first_provision_keeps_caphost(fake_runner):
    runner = fake_runner({"OPERATOR_IP": "closed"})
    open_window(runner, "20.30.40.50")
    assert "DEPLOY_CAPHOST" not in runner.env


def test_close_window_sets_closed_sentinel(fake_runner):
    runner = fake_runner({**PROVISIONED, "OPERATOR_IP": "20.30.40.50", "ACR_PUBLIC": "true",
                          "CONSOLE_IP": "20.30.40.50"})
    close_window(runner)
    assert runner.env["OPERATOR_IP"] == "closed"
    assert runner.env["CONSOLE_IP"] == "20.30.40.50"  # 창을 닫아도 콘솔 시연은 계속됩니다.
    assert runner.env["ACR_PUBLIC"] == "false"
    assert len(runner.commands("azd", "provision")) == 1


def test_close_window_failed_provision_is_not_reported_closed(fake_runner):
    # 프로비저닝이 실패하면 Azure에는 운영자 IP 규칙이 남아 있을 수 있으므로 "닫힘"으로 보이면 안 됩니다.
    runner = (fake_runner({**PROVISIONED, "OPERATOR_IP": "20.30.40.50"})
              .on("azd", "provision", results=[OpsError("provision boom")]))
    with pytest.raises(OpsError, match="provision boom"):
        close_window(runner)
    status = window_status(runner, "20.30.40.50")
    assert status.open is True and status.closing is True
    assert "window close" in status.warning


def test_close_window_marks_closed_only_after_provision(fake_runner):
    runner = fake_runner({**PROVISIONED, "OPERATOR_IP": "20.30.40.50"})
    close_window(runner)
    assert [c[4] for c in runner.commands("azd", "env", "set", "WINDOW_STATE")] == ["closing", "closed"]
    provision_at = runner.index("azd", "provision")
    assert runner.index("azd", "env", "set", "WINDOW_STATE") < provision_at
    assert runner.index("azd", "env", "set", "WINDOW_STATE", start=provision_at) > provision_at
    status = window_status(runner, None)
    assert (status.open, status.closing, status.warning) == (False, False, None)


def test_open_window_after_failed_close_clears_closing_state(fake_runner):
    runner = fake_runner({**PROVISIONED, "WINDOW_STATE": "closing"})
    open_window(runner, "20.30.40.50")
    status = window_status(runner, "20.30.40.50")
    assert (status.open, status.closing, status.allowed_ip, status.warning) == (True, False, "20.30.40.50", None)


def test_open_window_failure_tells_operator_to_close(fake_runner, monkeypatch):
    # 네트워크를 연 뒤 실패하면 자동으로 닫지 않고, 닫는 명령을 안내합니다.
    monkeypatch.setattr("ops.window.ensure_console_cert", lambda r: True)
    runner = fake_runner(PROVISIONED).on("azd", "provision", results=["", OpsError("second provision boom")])
    with pytest.raises(OpsError) as err:
        open_window(runner, "20.30.40.50")
    assert "second provision boom" in str(err.value)
    assert "python -m ops window close" in str(err.value)
    assert len(runner.commands("azd", "provision")) == 2


def test_status_warns_when_public_ip_changed(fake_runner):
    # Review Focus #1: 창을 연 뒤 공인 IP가 바뀌면 경고합니다.
    runner = fake_runner({**PROVISIONED, "OPERATOR_IP": "20.30.40.50"})
    status = window_status(runner, "20.30.40.99")
    assert status.open is True
    assert status.allowed_ip == "20.30.40.50"
    assert "20.30.40.99" in status.warning


def test_status_closed_has_no_warning(fake_runner):
    status = window_status(fake_runner(PROVISIONED), "20.30.40.99")
    assert status.open is False
    assert status.allowed_ip is None
    assert status.warning is None


def test_open_window_checks_console_cert_after_provision(fake_runner, monkeypatch):
    runner = fake_runner(PROVISIONED)
    provisions_seen = []
    monkeypatch.setattr("ops.window.ensure_console_cert",
                        lambda r: provisions_seen.append(len(r.commands("azd", "provision"))) or False)
    open_window(runner, "20.30.40.50")
    assert provisions_seen == [1]  # 첫 프로비저닝이 APPGW_FQDN을 만든 뒤에 확인합니다.
    assert len(runner.commands("azd", "provision")) == 1


def test_open_window_reprovisions_after_new_cert(fake_runner, monkeypatch):
    runner = fake_runner(PROVISIONED)
    monkeypatch.setattr("ops.window.ensure_console_cert", lambda r: True)
    open_window(runner, "20.30.40.50")
    assert len(runner.commands("azd", "provision")) == 2


FAST = {**PROVISIONED, "AZURE_SUBSCRIPTION_ID": "sub-1", "FUNCTION_APP_NAME": "func-x", "ACR_NAME": "acrx",
        "APPGW_CERT_SECRET_ID": "https://kv/secrets/cert", "CONSOLE_IP": "1.2.3.4"}
SUCCEEDED = '{"properties": {"provisioningState": "Succeeded"}}'


@pytest.fixture
def patches(monkeypatch):
    seen = []
    monkeypatch.setattr("ops.window.rest_patch", lambda az, url, body: seen.append((url, body)) or {})
    return seen


def fast_runner(fake_runner, env=FAST, states=(SUCCEEDED,)):
    return fake_runner(env).on("az", "rest", "--method", "get", results=list(states))


def test_open_window_fast_path_patches_only_window_resources(fake_runner, patches):
    runner = fast_runner(fake_runner)
    open_window(runner, "20.30.40.50")
    assert runner.commands("azd", "provision") == []
    urls = [url for url, _ in patches]
    assert urls[0].endswith("/Microsoft.Web/sites/func-x/config/web?api-version=2024-04-01")
    assert urls[1].endswith("/Microsoft.Web/sites/func-x?api-version=2024-04-01")
    assert "/subscriptions/sub-1/resourceGroups/rg-amalend/" in urls[2]
    assert urls[2].endswith("/Microsoft.CognitiveServices/accounts/ais-x?api-version=2025-04-01-preview")
    web, site, account = (body["properties"] for _, body in patches)
    assert web == {"ipSecurityRestrictionsDefaultAction": "Deny", "scmIpSecurityRestrictionsUseMain": True,
                   "ipSecurityRestrictions": [{"name": "operator", "ipAddress": "20.30.40.50/32",
                                               "action": "Allow", "priority": 100}]}
    assert site == {"publicNetworkAccess": "Enabled"}
    assert account == {"publicNetworkAccess": "Enabled", "networkAcls": {
        "defaultAction": "Deny", "bypass": "AzureServices", "ipRules": [{"value": "20.30.40.50"}],
        "virtualNetworkRules": []}}
    [acr] = runner.commands("az", "acr", "update")
    assert acr[acr.index("--public-network-enabled") + 1] == "false"
    assert acr[acr.index("--default-action") + 1] == "Deny"
    assert acr[-2:] == ["--subscription", "sub-1"]
    assert runner.env["OPERATOR_IP"] == "20.30.40.50"
    assert runner.env["CONSOLE_IP"] == "1.2.3.4"  # 콘솔 WAF 허용 IP는 창과 따로 관리합니다.


def test_open_window_fast_path_with_acr_build_window(fake_runner, patches):
    runner = fast_runner(fake_runner)
    open_window(runner, "20.30.40.50", acr=True)
    [acr] = runner.commands("az", "acr", "update")
    assert acr[acr.index("--public-network-enabled") + 1] == "true"
    assert acr[acr.index("--default-action") + 1] == "Allow"


def test_close_window_fast_path_goes_private_before_dropping_rules(fake_runner, patches):
    runner = fast_runner(fake_runner, {**FAST, "OPERATOR_IP": "20.30.40.50", "ACR_PUBLIC": "true"})
    close_window(runner)
    assert runner.commands("azd", "provision") == []
    (_, site), (_, web), (_, account) = patches
    assert site["properties"] == {"publicNetworkAccess": "Disabled"}
    assert web["properties"]["ipSecurityRestrictions"] == []
    assert account["properties"]["publicNetworkAccess"] == "Disabled"
    assert account["properties"]["networkAcls"]["ipRules"] == []
    [acr] = runner.commands("az", "acr", "update")
    assert acr[acr.index("--public-network-enabled") + 1] == "false"
    assert runner.index("az", "acr", "update") < runner.index("az", "rest", "--method", "get")
    assert (runner.env["WINDOW_STATE"], runner.env["OPERATOR_IP"]) == ("closed", "closed")


def test_fast_path_waits_for_foundry_account_update(fake_runner, patches, monkeypatch):
    monkeypatch.setattr("ops.window.ACCOUNT_WAIT_S", 0.0)
    running = '{"properties": {"provisioningState": "Accepted"}}'
    runner = fast_runner(fake_runner, states=(running, running, SUCCEEDED))
    open_window(runner, "20.30.40.50")
    assert len(runner.commands("az", "rest", "--method", "get")) == 3


def test_close_window_fast_path_failure_stays_closing(fake_runner, monkeypatch):
    def boom(az, url, body):
        raise OpsError("patch boom")
    monkeypatch.setattr("ops.window.rest_patch", boom)
    runner = fast_runner(fake_runner, {**FAST, "OPERATOR_IP": "20.30.40.50"})
    with pytest.raises(OpsError, match="patch boom"):
        close_window(runner)
    assert window_status(runner, None).closing is True


def test_window_provision_flag_skips_fast_path(fake_runner, patches):
    runner = fast_runner(fake_runner)
    open_window(runner, "20.30.40.50", full_provision=True)
    close_window(runner, full_provision=True)
    assert patches == []
    assert len(runner.commands("azd", "provision")) == 2


def test_fast_path_runs_provision_when_console_cert_changes(fake_runner, patches, monkeypatch):
    monkeypatch.setattr("ops.window.ensure_console_cert", lambda r: True)
    runner = fast_runner(fake_runner)
    open_window(runner, "20.30.40.50")
    assert len(patches) == 3
    assert len(runner.commands("azd", "provision")) == 1
