import json
import re

import pytest

from tests.infra.test_console import INFRA, _compile, _resources

JOB = "Microsoft.App/jobs"
CA = "Microsoft.App/containerApps"
COSMOS_READER = "00000000-0000-0000-0000-000000000001"
METRICS_PUBLISHER = "3913510d-42f4-4e42-8a64-420c390055eb"


def _ops(exp) -> dict:
    return exp.one(JOB, "job-ops")


def test_ops_job_runs_opsjob_with_ops_identity(variant):
    job = _ops(variant)
    props = job["properties"]
    assert props["configuration"]["triggerType"] == "Manual"
    assert props["configuration"]["replicaTimeout"] == 3600
    assert list(job["identity"]["userAssignedIdentities"])[0].endswith("/id-ops-amatest")
    container = props["template"]["containers"][0]
    assert container["name"] == "ops" and container["image"] == "acramatest.azurecr.io/eval:v1"
    assert container["command"] == ["/opt/venv/bin/python", "-m", "opsjob"]
    env = {e["name"]: e["value"] for e in container["env"]}
    assert env["COSMOS_DATABASE"] and env["COSMOS_ENDPOINT"].startswith("https://")
    assert re.match(r"^https://st[a-z0-9]+\.queue\.core\.windows\.net$", env["QUEUE_ENDPOINT"])


def test_eval_job_unchanged_by_shared_module(variant):
    container = variant.one(JOB, "job-eval")["properties"]["template"]["containers"][0]
    assert container["name"] == "eval" and not container.get("command")
    assert variant.one(JOB, "job-eval")["properties"]["configuration"]["replicaTimeout"] == 3600


def test_ops_identity_is_least_privilege():
    grants: dict[str, set[str]] = {}
    for module in _resources(_compile(INFRA / "resources.bicep")):
        text = json.dumps(module.get("properties", {}).get("parameters", {}))
        found = re.findall(r"opsPrincipalId\.value.{0,80}?(?:role'\)\.(\w+)|roleDefinitionGuid', '([0-9a-f-]+)')", text)
        if found:
            grants[module["name"]] = {a or b for a, b in found}
    # 큐 보내기(신청 제출)·Cosmos 읽기·이미지 pull뿐이다. 모델·Key Vault·쓰기 권한 없음.
    assert grants == {"storage-functions": {"queueSender"}, "cosmos": {COSMOS_READER}, "acr": {"acrPull"}}


def test_policy_exports_otel(variant):
    container = variant.one(CA, "ca-policy")["properties"]["template"]["containers"][0]
    env = {e["name"]: e.get("value") for e in container["env"]}
    assert env["OTEL_SERVICE_NAME"] == "policy"
    assert "APPLICATIONINSIGHTS_CONNECTION_STRING" in env
    assert env["OTEL_PYTHON_FASTAPI_EXCLUDED_URLS"] == "healthz"
    # send/receive 스팬 제외는 env가 아니라 코드(shared.otel.instrument_fastapi)에서 한다.
    assert "OTEL_PYTHON_FASTAPI_EXCLUDE_SPANS" not in env
    # 연결 문자열은 배포 시점 값이라 PSRule 펼치기에서는 빈 문자열이다. 컴파일 결과로 배선을 확인한다.
    [policy] = [m for m in _resources(_compile(INFRA / "resources.bicep")) if m.get("name") == "ca-policy"]
    wired = {e["name"]: e["value"] for e in policy["properties"]["parameters"]["envVars"]["value"]}
    assert "'monitoring')" in wired["APPLICATIONINSIGHTS_CONNECTION_STRING"]
    assert wired["APPLICATIONINSIGHTS_CONNECTION_STRING"].endswith(".outputs.appiConnectionString.value]")


def test_functions_service_name(variant):
    site = variant.one("Microsoft.Web/sites", "func-")
    settings = {s["name"]: s["value"] for s in site["properties"]["siteConfig"]["appSettings"]}
    assert settings["OTEL_SERVICE_NAME"] == "orchestrator"


def test_alerts_and_workbook(variant):
    rules = variant.of_type("Microsoft.Insights/scheduledQueryRules")
    queries = {r["name"]: r["properties"]["criteria"]["allOf"][0]["query"] for r in rules}
    assert any("journey.step" in q for q in queries.values())
    assert any("policy.circuit" in q for q in queries.values())
    windows = sorted(r["properties"]["windowSize"] for r in rules)
    assert windows == ["PT15M", "PT1H"]
    wb = variant.one("Microsoft.Insights/workbooks")
    data = json.loads(wb["properties"]["serializedData"])
    assert "journey.step" in json.dumps(data) and wb["properties"]["category"] == "workbook"


def test_foundry_project_traces_to_app_insights(variant):
    conn = variant.one("Microsoft.CognitiveServices/accounts/projects/connections", "appi-")
    props = conn["properties"]
    assert (props["category"], props["authType"]) == ("AppInsights", "ProjectManagedIdentity")
    assert set(props["metadata"]) >= {"ApplicationInsightsConnectionString", "ResourceId", "ApiType"}
    assert METRICS_PUBLISHER in {
        r["properties"]["roleDefinitionId"].rsplit("/", 1)[-1] for r in variant.role_assignments_on("appi-")
    }


@pytest.mark.parametrize("output", ["OPS_JOB_NAME", "APPLICATIONINSIGHTS_RESOURCE_ID"])
def test_ops_outputs_exposed_to_azd(output):
    assert output in _compile(INFRA / "main.bicep")["outputs"]


def test_trace_connection_waits_for_capability_host():
    # caphost 프로비저닝 중 프로젝트 연결 PUT은 409가 난다.
    [insights] = [m for m in _resources(_compile(INFRA / "resources.bicep")) if m.get("name") == "ops-insights"]
    assert any("'foundry-caphost'" in d for d in insights.get("dependsOn", []))
