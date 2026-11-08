"""P3 앱 런타임 인프라: 큐, Cosmos 컨테이너, 비밀, 정책·Mock Container Apps, Functions 설정."""
import json

APP = "Microsoft.App/containerApps"
SITE = "Microsoft.Web/sites"
PLACEHOLDER = "mcr.microsoft.com/azurelinux/base/python:3.12"
ROLE_QUEUE_SENDER = "c6a89b2d-59bc-44d0-9896-0f6e12d7b80a"


def _settings(exp) -> dict[str, str]:
    site = exp.one(SITE, "func-")
    return {s["name"]: s["value"] for s in site["properties"]["siteConfig"]["appSettings"]}


def _container(exp, name: str) -> dict:
    return exp.one(APP, name)["properties"]["template"]["containers"][0]


def _env(container: dict) -> dict[str, dict]:
    return {e["name"]: e for e in container.get("env", [])}


def _smoke_config(exp) -> dict:
    container = exp.one("Microsoft.App/jobs", "job-smoke")["properties"]["template"]["containers"][0]
    return json.loads(_env(container)["SMOKE_CONFIG"]["value"])


def test_function_queues_on_host_storage(closed):
    queues = closed.of_type("Microsoft.Storage/storageAccounts/queueServices/queues")
    assert {q["name"].split("/")[-1] for q in queues} == {
        "applications-start",
        "officer-decisions",
        "monitoring-replay",
        "restructure-decisions",
        "consent-lifecycle",
        "consent-commands",
        "grievances",
        "grievance-decisions",
    }
    assert all(q["name"].startswith("stfn") for q in queues)


def test_smoke_identity_can_enqueue(closed):
    assert ROLE_QUEUE_SENDER in closed.roles_on("stfn")


def test_cosmos_app_containers(closed):
    containers = {
        c["name"].split("/")[-1]: c["properties"]["resource"]["partitionKey"]["paths"]
        for c in closed.of_type("Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers")
    }
    assert containers == {
        "applications": ["/application_id"],
        "consents": ["/consent_id"],
        "classifications": ["/id"],
        "loans": ["/loan_id"],
        "alerts": ["/loan_id"],
        "deletion_proofs": ["/consent_id"],
        "grievances": ["/application_id"],
    }


def test_pseudonym_key_secret_only_when_provided(closed, variant):
    secrets = closed.of_type("Microsoft.KeyVault/vaults/secrets")
    assert [s["name"].split("/")[-1] for s in secrets] == ["pseudonym-key"]
    assert variant.of_type("Microsoft.KeyVault/vaults/secrets") == []


def test_container_apps_are_vnet_only_single_replica(closed):
    for name in ("ca-policy", "ca-mockapi"):
        app = closed.one(APP, name)
        props = app["properties"]
        assert props["environmentId"].endswith("/managedEnvironments/cae-int-amatest")
        assert list(app["identity"]["userAssignedIdentities"])[0].endswith("/id-apps-amatest")
        ingress = props["configuration"]["ingress"]
        assert ingress["external"] is True
        assert ingress["targetPort"] == 8000
        assert ingress["allowInsecure"] is False
        assert props["template"]["scale"] == {"minReplicas": 1, "maxReplicas": 1}


def test_placeholder_image_until_deployed(closed):
    container = _container(closed, "ca-policy")
    assert container["image"] == PLACEHOLDER
    assert container["command"] == ["python3", "-m", "http.server", "8000"]
    assert closed.one(APP, "ca-policy")["properties"]["configuration"]["registries"] == []


def test_acr_image_pulled_with_app_identity(variant):
    container = _container(variant, "ca-policy")
    assert container["image"] == "acramatest.azurecr.io/policy:v1"
    assert not container.get("command")
    registry = variant.one(APP, "ca-policy")["properties"]["configuration"]["registries"][0]
    assert registry["server"].endswith(".azurecr.io")
    assert registry["identity"].endswith("/id-apps-amatest")


def test_mock_consent_secret_is_app_secret(closed, variant):
    props = closed.one(APP, "ca-mockapi")["properties"]
    assert [s["name"] for s in props["configuration"]["secrets"]] == ["mock-consent-secret"]
    assert _env(_container(closed, "ca-mockapi"))["MOCK_CONSENT_SECRET"]["secretRef"] == "mock-consent-secret"
    assert "MOCK_CONSENT_SECRET" not in _env(_container(closed, "ca-policy"))
    assert variant.one(APP, "ca-mockapi")["properties"]["configuration"]["secrets"] == []


def test_function_app_settings_point_to_runtime(closed, variant):
    settings = _settings(closed)
    assert settings["POLICY_URL"].startswith("https://ca-policy.")
    assert settings["MOCKAPI_URL"].startswith("https://ca-mockapi.")
    assert settings["COSMOS_DATABASE"] == "lending"
    assert settings["AI_PROJECT_ENDPOINT"].endswith("/api/projects/proj-lending")
    assert settings["DATA_ASSEMBLY_MODEL"] == "gpt-5.6-luna"
    assert settings["AI_ACCOUNT_ENDPOINT"].endswith(".services.ai.azure.com")
    assert settings["DECISION_DEPLOYMENT"] == "decision-1"
    assert (settings["ANALYST_MODEL"], settings["REVIEWER_MODEL"]) == ("gpt-5.6-sol", "gpt-5.6-luna")
    assert settings["CLASSIFIER_MODEL"] == "gpt-5.6-luna"
    assert (settings["EW_MODEL"], settings["COLLECTIONS_MODEL"]) == ("gpt-5.6-luna", "gpt-5.6-luna")
    assert (settings["NOTICE_MODEL"], settings["GRIEVANCE_MODEL"]) == ("gpt-5.6-luna", "gpt-5.6-luna")
    # 동의 수명주기 예약은 함수 호스트 스토리지의 큐로 보낸다(오케스트레이터 ID는 큐 기여자).
    assert settings["QUEUE_ENDPOINT"].startswith("https://stfn") and ".queue." in settings["QUEUE_ENDPOINT"]
    assert settings["SERVICEBUS_FQDN"].endswith(".servicebus.windows.net")
    # 런타임 속성(엔드포인트)은 PSRule 펼침에서 빈 값이므로 키 존재만 확인합니다.
    assert {"COSMOS_ENDPOINT", "DATA_BLOB_ENDPOINT", "KEYVAULT_URI"} <= settings.keys()
    assert "PSEUDONYM_KEY" not in settings
    assert _settings(variant)["SERVICEBUS_FQDN"] == ""


def test_function_servicebus_trigger_connection_uses_identity(closed, variant):
    settings = _settings(closed)
    assert settings["ServiceBusConnection__fullyQualifiedNamespace"] == settings["SERVICEBUS_FQDN"]
    assert settings["ServiceBusConnection__credential"] == "managedidentity"
    assert "ServiceBusConnection__clientId" in settings
    # pause 상태에서는 트리거를 등록하지 않으므로 연결 설정도 두지 않는다.
    assert not any(k.startswith("ServiceBusConnection__") for k in _settings(variant))


def test_smoke_job_has_e2e_config(closed):
    job = closed.one("Microsoft.App/jobs", "job-smoke")
    assert job["properties"]["configuration"]["replicaTimeout"] == 1800
    config = _smoke_config(closed)
    assert config["queue_host"].startswith("stfn") and ".queue." in config["queue_host"]
    assert config["start_queue"] == "applications-start"
    assert config["decision_queue"] == "officer-decisions"
    assert config["consent_command_queue"] == "consent-commands"
    assert config["cosmos_database"] == "lending"
    assert config["policy_url"].startswith("https://ca-policy.")
    assert config["mockapi_url"].startswith("https://ca-mockapi.")
    assert config["e2e_applicant"] == "seller-dailylook-01"
    assert config["ai_account_endpoint"].endswith(".services.ai.azure.com")
    assert config["decision_deployment"] == "decision-1"
