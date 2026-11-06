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
