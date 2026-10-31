import pytest

from ops.azcli import OpsError
from ops.smoke_runner import run_smoke

ENV = {"AZURE_SUBSCRIPTION_ID": "sub-azd", "AZURE_RESOURCE_GROUP": "rg-amalend", "SMOKE_JOB_NAME": "job-smoke",
       "LOG_ANALYTICS_CUSTOMER_ID": "law-guid"}


def test_run_smoke_polls_until_succeeded(fake_runner):
    runner = (fake_runner(ENV)
              .on("az", "containerapp", "job", "start", results=["job-smoke-abc12\n"])
              .on("az", "containerapp", "job", "execution", "show", results=["Running\n", "Running\n", "Succeeded\n"]))
    sleeps = []
    assert run_smoke(runner, sleep=sleeps.append) == "job-smoke-abc12"
    assert len(sleeps) == 2
    show = runner.commands("az", "containerapp", "job", "execution", "show")[0]
    assert show[show.index("--job-execution-name") + 1] == "job-smoke-abc12"
    for call in runner.commands("az"):
        assert call[call.index("--subscription") + 1] == "sub-azd"


def test_run_smoke_failure_includes_log_query_hint(fake_runner):
    runner = (fake_runner(ENV)
              .on("az", "containerapp", "job", "start", results=["job-smoke-abc12\n"])
              .on("az", "containerapp", "job", "execution", "show", results=["Failed\n"]))
    with pytest.raises(OpsError) as err:
        run_smoke(runner, sleep=lambda s: None)
    message = str(err.value)
    assert "Failed" in message
    assert "ContainerAppConsoleLogs_CL" in message
    assert "law-guid" in message


def test_run_smoke_times_out(fake_runner):
    runner = (fake_runner(ENV)
              .on("az", "containerapp", "job", "start", results=["job-smoke-abc12\n"])
              .on("az", "containerapp", "job", "execution", "show", results=["Running\n"]))
    with pytest.raises(OpsError, match="시간 초과"):
        run_smoke(runner, sleep=lambda s: None, poll_limit=3)
