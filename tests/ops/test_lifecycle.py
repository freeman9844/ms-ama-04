import pytest

from ops.azcli import OpsError
from ops.lifecycle import pause, resume, teardown

ENV = {
    "AZURE_RESOURCE_GROUP": "rg-amalend",
    "AZURE_LOCATION": "koreacentral",
    "AI_ACCOUNT_NAME": "ais-x",
    "AI_PROJECT_NAME": "proj-lending",
    "FOUNDRY_PROJECT_CAPHOST": "caphost-lending",
    "KEYVAULT_NAME": "kvx",
    "SERVICEBUS_NAME": "sb-x",
    "DEPLOY_CAPHOST": "false",
}
NOT_FOUND = OpsError("(ResourceNotFound) The resource was not found")


def test_pause_deletes_endpoint_then_namespace_then_provisions(fake_runner):
    runner = fake_runner(ENV).on("az", "servicebus", "namespace", "list", results=['["sb-x"]'])
    assert pause(runner) is True
    assert runner.env["SERVICEBUS_ENABLED"] == "false"
    pe = runner.index("az", "network", "private-endpoint", "delete")
    assert runner.calls[pe][-2:] == ["-n", "pe-sb-x-namespace"]
    assert pe < runner.index("az", "servicebus", "namespace", "delete") < runner.index("azd", "provision")


def test_pause_is_idempotent_when_already_deleted(fake_runner):
    runner = fake_runner(ENV).on("az", "servicebus", "namespace", "list", results=["[]"])
    assert pause(runner) is False
    assert runner.commands("az", "servicebus", "namespace", "delete") == []
    assert len(runner.commands("azd", "provision")) == 1


def test_resume_enables_service_bus_and_provisions(fake_runner):
    runner = fake_runner({**ENV, "SERVICEBUS_ENABLED": "false"})
    resume(runner)
    assert runner.env["SERVICEBUS_ENABLED"] == "true"
    assert len(runner.commands("azd", "provision")) == 1


def test_teardown_requires_matching_confirmation(fake_runner):
    runner = fake_runner(ENV)
    with pytest.raises(OpsError, match="rg-amalend"):
        teardown(runner, "rg-other")
    assert runner.commands("az", "group", "delete") == []


def test_teardown_order_and_group_retry(fake_runner):
    # Review Focus #4: caphost → 계정 → purge 후 RG 삭제. 서브넷 점유로 실패하면 기다렸다 재시도합니다.
    in_use = OpsError("InUseSubnetCannotBeDeleted: Subnet snet-agents is in use")
    runner = (fake_runner(ENV)
              .on("az", "account", "show", results=["sub-1\n"])
              .on("az", "rest", "--method", "get", results=[NOT_FOUND])
              .on("az", "group", "delete", results=[in_use, in_use, ""]))
    sleeps = []
    teardown(runner, "rg-amalend", sleep=sleeps.append)

    project_caphost = runner.index("az", "rest", "--method", "delete")
    account_caphost = runner.index("az", "rest", "--method", "delete", start=project_caphost + 1)
    assert "/projects/proj-lending/capabilityHosts/caphost-lending" in runner.calls[project_caphost][-1]
    assert "/capabilityHosts/ais-x@aml_aiagentservice" in runner.calls[account_caphost][-1]
    order = [
        account_caphost,
        runner.index("az", "cognitiveservices", "account", "delete"),
        runner.index("az", "cognitiveservices", "account", "purge"),
        runner.index("az", "group", "delete"),
        runner.index("az", "keyvault", "purge"),
    ]
    assert order == sorted(order)
    assert len(runner.commands("az", "group", "delete")) == 3
    assert sleeps.count(300) == 2
    assert runner.env["DEPLOY_CAPHOST"] == "true"


def test_teardown_tolerates_already_deleted_resources(fake_runner):
    runner = (fake_runner(ENV)
              .on("az", "account", "show", results=["sub-1\n"])
              .on("az", "rest", results=[NOT_FOUND])
              .on("az", "cognitiveservices", results=[NOT_FOUND])
              .on("az", "group", "delete", results=[OpsError("(ResourceGroupNotFound) missing")])
              .on("az", "keyvault", "purge", results=[NOT_FOUND]))
    teardown(runner, "rg-amalend", sleep=lambda s: None)
    assert len(runner.commands("az", "group", "delete")) == 1
    assert runner.env["DEPLOY_CAPHOST"] == "true"


def test_teardown_gives_up_after_retries(fake_runner):
    in_use = OpsError("InUseSubnetCannotBeDeleted")
    runner = (fake_runner(ENV)
              .on("az", "account", "show", results=["sub-1\n"])
              .on("az", "rest", "--method", "get", results=[NOT_FOUND])
              .on("az", "group", "delete", results=[in_use]))
    with pytest.raises(OpsError, match="InUseSubnet"):
        teardown(runner, "rg-amalend", sleep=lambda s: None, group_retries=2)
    assert len(runner.commands("az", "group", "delete")) == 2
