import json

JOB = "Microsoft.App/jobs"


def _container(exp) -> dict:
    return exp.one(JOB, "job-smoke")["properties"]["template"]["containers"][0]


def _config(exp) -> dict:
    env = {e["name"]: e["value"] for e in _container(exp)["env"]}
    return json.loads(env["SMOKE_CONFIG"])


def test_smoke_job_runs_inline_script_in_internal_environment(closed):
    job = closed.one(JOB, "job-smoke")
    props = job["properties"]
    assert props["environmentId"].endswith("/managedEnvironments/cae-int-amatest")
    assert props["configuration"]["triggerType"] == "Manual"
    assert props["configuration"]["replicaRetryLimit"] == 0
    assert list(job["identity"]["userAssignedIdentities"])[0].endswith("/id-smoke-amatest")
    container = _container(closed)
    assert container["image"] == "mcr.microsoft.com/azurelinux/base/python:3.12"
    assert container["command"][:2] == ["python3", "-c"]
    assert "def main(" in container["command"][2]


def test_smoke_config_covers_all_private_endpoints(closed):
    config = _config(closed)
    hosts = config["private_hosts"]
    assert len(hosts) == 15
    for suffix in (".openai.azure.com", ".services.ai.azure.com", ".cognitiveservices.azure.com",
                   ".search.windows.net", ".documents.azure.com", ".vault.azure.net",
                   ".servicebus.windows.net", ".azurecr.io", ".azurewebsites.net", ".azure-api.net"):
        assert any(h.endswith(suffix) for h in hosts), suffix
    assert sum(".core.windows.net" in h for h in hosts) == 5  # stagt/stdat/stfn blob + stfn queue/table
    assert config["chat_models"] == ["gpt-5.4", "gpt-5.4-mini"]
    assert config["servicebus_host"].endswith(".servicebus.windows.net")
    assert config["project_endpoint"].endswith("/api/projects/proj-lending")


def test_smoke_config_when_service_bus_paused(variant):
    config = _config(variant)
    assert config["servicebus_host"] == ""
    assert len(config["private_hosts"]) == 14
