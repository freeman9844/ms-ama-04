import json
import re

from tests.infra.test_console import INFRA, _compile, _resources

JOB = "Microsoft.App/jobs"
ROLE_BLOB_CONTRIBUTOR = "ba92f5b4-2d11-453d-a403-e96b0029c9fe"


def _container(exp) -> dict:
    return exp.one(JOB, "job-eval")["properties"]["template"]["containers"][0]


def test_eval_job_is_placeholder_until_image_deployed(closed):
    job = closed.one(JOB, "job-eval")
    props = job["properties"]
    assert props["environmentId"].endswith("/managedEnvironments/cae-int-amatest")
    assert props["configuration"]["triggerType"] == "Manual"
    assert props["configuration"]["replicaTimeout"] == 3600
    assert props["configuration"]["replicaRetryLimit"] == 0
    assert props["configuration"]["registries"] == []
    assert list(job["identity"]["userAssignedIdentities"])[0].endswith("/id-eval-amatest")
    container = _container(closed)
    assert container["image"] == "mcr.microsoft.com/azurelinux/base/python:3.12"
    assert container["resources"] == {"cpu": 1, "memory": "2Gi"}


def test_eval_job_pulls_image_with_eval_identity_and_measures_production_models(variant):
    container = _container(variant)
    assert container["image"] == "acramatest.azurecr.io/eval:v1"
    assert not container.get("command")
    registry = variant.one(JOB, "job-eval")["properties"]["configuration"]["registries"][0]
    assert registry["identity"].endswith("/id-eval-amatest")
    env = {e["name"]: e["value"] for e in container["env"]}
    assert env["AI_PROJECT_ENDPOINT"].endswith("/api/projects/proj-lending")
    assert (env["EVAL_FULL_MODEL"], env["EVAL_MINI_MODEL"], env["REVIEWER_MODEL"], env["DECISION_DEPLOYMENT"]) == (
        "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-mini", "decision-1")
    assert env["EVAL_BUDGET_USD"] == "5" and env["EVAL_EMIT_LOGS"] == "1"
    assert env["EVAL_BLOB_CONTAINER"] == "eval-reports"


def test_eval_identity_is_least_privilege(closed):
    grants: dict[str, set[str]] = {}
    for module in _resources(_compile(INFRA / "resources.bicep")):
        text = json.dumps(module.get("properties", {}).get("parameters", {}))
        found = re.findall(r"evalPrincipalId\.value.{0,80}?(?:role'\)\.(\w+)|roleDefinitionGuid', '([0-9a-f-]+)')", text)
        if found:
            grants[module["name"]] = {a or b for a, b in found}
    # Cosmos·Key Vault·큐 권한은 없고, 스토리지 쓰기는 eval-reports 컨테이너 범위뿐이다.
    assert grants == {"foundry-account": {"foundryUser", "openAiUser"}, "acr": {"acrPull"},
                      "eval-reports-role": {"blobContributor"}}
    assert closed.roles_on("eval-reports") == {ROLE_BLOB_CONTRIBUTOR}
