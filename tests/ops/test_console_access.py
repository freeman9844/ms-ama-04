import io
import json

import pytest

from ops.azcli import OpsError
from ops.cli import main
from ops.console_access import (MIN_PASSWORD, ip_filter_off, ip_filter_on, ip_filter_status, new_password)

ENV = {"AZURE_SUBSCRIPTION_ID": "sub-1", "AZURE_RESOURCE_GROUP": "rg-amalend", "APPGW_NAME": "agw-x",
       "CONSOLE_APP_NAME": "ca-console", "CONSOLE_AUTH_ENABLED": "false", "CONSOLE_IP": "20.30.40.50"}
GATEWAY = json.dumps({"firewallPolicy": {"id": "/subscriptions/sub-1/resourceGroups/rg/providers/x/waf-amalend"}})
RULE_UPDATE = ("az", "network", "application-gateway", "waf-policy", "custom-rule", "update")


def console(with_password: bool) -> str:
    env = [{"name": "COSMOS_ENDPOINT", "value": "x"}]
    if with_password:
        env.append({"name": "CONSOLE_DEMO_PASSWORD", "secretRef": "console-demo-password"})
    return json.dumps({"properties": {"template": {"containers": [{"name": "console", "env": env}]}}})


def waf_runner(fake_runner, env, *, has_password=True):
    return (fake_runner(env)
            .on("az", "network", "application-gateway", "show", results=[GATEWAY])
            .on("az", "containerapp", "show", results=[console(has_password)]))


def test_new_password_is_long_and_safe_as_cli_argument():
    passwords = {new_password() for _ in range(50)}
    assert len(passwords) == 50
    assert all(len(p) >= MIN_PASSWORD and p.isalnum() for p in passwords)


def test_off_generates_password_deploys_it_then_disables_rule(fake_runner):
    runner = waf_runner(fake_runner, ENV, has_password=False)
    password = ip_filter_off(runner)
    assert len(password) >= MIN_PASSWORD and runner.env["CONSOLE_DEMO_PASSWORD"] == password
    [update] = runner.commands(*RULE_UPDATE)
    assert update[update.index("--policy-name") + 1] == "waf-amalend"
    assert update[update.index("-n") + 1] == "allowOperatorOnly"
    assert update[update.index("--state") + 1] == "Disabled"
    assert update[-2:] == ["--subscription", "sub-1"]
    # 비밀번호가 콘솔에 들어간 뒤에야 IP 규칙을 끕니다.
    assert runner.index("azd", "env", "set", "CONSOLE_DEMO_PASSWORD") < runner.index("azd", "provision")
    assert runner.index("azd", "provision") < runner.index(*RULE_UPDATE)
    assert runner.index(*RULE_UPDATE) < runner.index("azd", "env", "set", "WAF_IP_FILTER")
    assert runner.env["WAF_IP_FILTER"] == "false"


def test_off_reuses_deployed_password_without_provision(fake_runner):
    runner = waf_runner(fake_runner, {**ENV, "CONSOLE_DEMO_PASSWORD": "Existing0123456789"})
    assert ip_filter_off(runner) == "Existing0123456789"
    assert runner.commands("azd", "provision") == []
    assert len(runner.commands(*RULE_UPDATE)) == 1


def test_off_provisions_when_console_lacks_existing_password(fake_runner):
    runner = waf_runner(fake_runner, {**ENV, "CONSOLE_DEMO_PASSWORD": "Existing0123456789"}, has_password=False)
    ip_filter_off(runner)
    assert len(runner.commands("azd", "provision")) == 1


def test_off_keeps_rule_when_provision_fails(fake_runner):
    runner = waf_runner(fake_runner, ENV, has_password=False).on(
        "azd", "provision", results=[OpsError("provision boom")])
    with pytest.raises(OpsError, match="provision boom"):
        ip_filter_off(runner)
    assert runner.commands(*RULE_UPDATE) == []
    assert "WAF_IP_FILTER" not in runner.env


def test_off_refuses_entra_mode(fake_runner):
    runner = waf_runner(fake_runner, {**ENV, "CONSOLE_AUTH_ENABLED": "true"})
    with pytest.raises(OpsError, match="데모 모드"):
        ip_filter_off(runner)
    assert runner.commands(*RULE_UPDATE) == []


def test_on_sets_ip_and_reprovisions(fake_runner):
    runner = fake_runner({**ENV, "WAF_IP_FILTER": "false"})
    assert ip_filter_on(runner, " 20.30.40.99 ") == "20.30.40.99"
    assert (runner.env["CONSOLE_IP"], runner.env["WAF_IP_FILTER"]) == ("20.30.40.99", "true")
    assert len(runner.commands("azd", "provision")) == 1


def test_on_rejects_private_ip(fake_runner):
    runner = fake_runner(ENV)
    with pytest.raises(OpsError):
        ip_filter_on(runner, "10.0.0.1")
    assert runner.commands("azd", "provision") == []


def test_status_reads_rule(fake_runner):
    rule = json.dumps({"state": "Disabled", "matchConditions": [{"matchValues": ["20.30.40.50/32"]}]})
    runner = (waf_runner(fake_runner, {**ENV, "CONSOLE_DEMO_PASSWORD": "Existing0123456789"})
              .on("az", "network", "application-gateway", "waf-policy", "custom-rule", "show", results=[rule]))
    status = ip_filter_status(runner)
    assert (status.state, status.allowed, status.password_set) == ("Disabled", ["20.30.40.50/32"], True)


def test_cli_off_prints_password_and_how_to_turn_back_on(fake_runner):
    runner = waf_runner(fake_runner, {**ENV, "CONSOLE_DEMO_PASSWORD": "Existing0123456789"})
    out = io.StringIO()
    assert main(["waf", "ip-filter", "off"], runner=runner, out=out) == 0
    text = out.getvalue()
    assert "demo.officer / Existing0123456789" in text and "waf ip-filter on" in text


def test_cli_on_uses_detected_ip(fake_runner):
    runner = fake_runner(ENV)
    assert main(["waf", "ip-filter", "on"], runner=runner, public_ip=lambda: "20.30.40.77", out=io.StringIO()) == 0
    assert runner.env["CONSOLE_IP"] == "20.30.40.77"
