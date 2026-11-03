import ast
import io
import json
import sys
from pathlib import Path

import xml.etree.ElementTree as ET

import pytest

from ops import smoke_checks as sc

CFG = {
    "openai_host": "ais-x.openai.azure.com",
    "project_endpoint": "https://ais-x.services.ai.azure.com/api/projects/proj-lending",
    "blob_host": "stdatx.blob.core.windows.net",
    "cosmos_host": "cosmos-x.documents.azure.com",
    "keyvault_host": "kvx.vault.azure.net",
    "servicebus_host": "sb-x.servicebus.windows.net",
    "servicebus_topic": "flow.updated",
    "chat_models": ["gpt-5.4", "gpt-5.4-mini"],
    "agent_model": "gpt-5.4-mini",
    "private_hosts": ["ais-x.openai.azure.com", "kvx.vault.azure.net"],
    "queue_host": "stfnx.queue.core.windows.net",
    "start_queue": "applications-start",
    "decision_queue": "officer-decisions",
    "consent_command_queue": "consent-commands",
    "cosmos_database": "lending",
    "policy_url": "https://ca-policy.int.example",
    "mockapi_url": "https://ca-mockapi.int.example",
    "e2e_applicant": "demo-healthy-001",
    "ai_account_endpoint": "https://ais-x.services.ai.azure.com",
    "decision_deployment": "decision-1",
}
ENV = {
    "SMOKE_CONFIG": json.dumps(CFG),
    "AZURE_CLIENT_ID": "cid",
    "IDENTITY_ENDPOINT": "http://localhost:42356/msi/token",
    "IDENTITY_HEADER": "secret",
}


def ok(obj=None, status=200):
    return sc.Response(status, json.dumps(obj or {}).encode())


def private(host):
    return "192.168.1.10"


class FakeHttp:
    """(method, URL 부분 문자열) → 응답 또는 응답 목록(순서대로 소비, 마지막 응답은 반복)."""

    def __init__(self, routes):
        self.routes = dict(routes)
        self.calls = []

    def __call__(self, method, url, headers=None, body=None):
        self.calls.append({"method": method, "url": url, "headers": headers or {}, "body": body})
        for (route_method, part), resp in self.routes.items():
            if route_method == method and part in url:
                if isinstance(resp, list):
                    return resp.pop(0) if len(resp) > 1 else resp[0]
                return resp
        raise AssertionError(f"예상하지 못한 요청: {method} {url}")

    def find(self, method, part):
        return [c for c in self.calls if c["method"] == method and part in c["url"]]


ELIGIBLE = {"decision": "eligible", "max_amount_krw": 2_500_000}
ASSESSMENT = {"recommendation": "conditional", "review": {"passed": True, "rounds": 2, "issues": []}}


MONITORING = {"loan_id": "smoke-e2e-test", "registered_at": "2026-11-02T09:00:00+09:00"}
LOAN = {"id": "smoke-e2e-test", "industry": "패션의류", "baseline": {"avg_weekly_revenue": 1_500_000}, "monitoring": None}
CONSENT = {"consent_id": "c-smoke", "scopes": ["account", "tax", "pg"]}
PROOF = {"id": "c-smoke", "complete": True, "reason": "expired",
         "items": [{"kind": "raw_container", "verified": True}, {"kind": "consent_registry", "verified": True},
                   {"kind": "conversation", "verified": True}],
         "manifest": [{"name": "profile.json"}, {"name": "transactions.json"}]}


def app_doc(status, **extra):
    return ok({"id": "smoke-e2e-test", "status": status, "timeline": [{}, {}, {}], **extra})


def healthy_routes():
    return {
        ("GET", "/colls/applications/docs/"): [sc.Response(404, b"{}"),
                                               app_doc("awaiting_officer", policy=ELIGIBLE, assessment=ASSESSMENT),
                                               app_doc("approved", monitoring=MONITORING, consent=CONSENT)],
        ("GET", "/colls/loans/docs/"): ok(LOAN),
        ("GET", "/colls/deletion_proofs/docs/"): ok(PROOF),
        ("GET", "/colls/consents/docs/"): ok({"id": "c-smoke", "status": "expired", "deletion_complete": True}),
        ("POST", "/consent-commands/messages"): ok(status=201),
        ("GET", "/msi/token"): ok({"access_token": "tok"}),
        ("POST", "/openai/v1/chat/completions"): ok({"model": "gpt"}),
        ("GET", "?comp=list"): ok(),
        ("GET", "/dbs"): ok({"Databases": [{"id": "lending"}]}),
        ("GET", "/secrets?"): ok({"value": []}),
        ("GET", "ca-policy.int.example/healthz"): ok({"status": "ok", "rules_version": "r1"}),
        ("GET", "ca-mockapi.int.example/healthz"): ok({"status": "ok"}),
        ("POST", "/flow.updated/messages"): ok(status=201),
        ("POST", "/applications-start/messages"): ok(status=201),
        ("POST", "/officer-decisions/messages"): ok(status=201),
        ("POST", "/agents/p2-smoke/versions?api-version=v1"): ok({"name": "p2-smoke", "version": "1"}),
        ("POST", "/openai/v1/responses"): ok({"id": "resp_1", "status": "completed"}),
        ("DELETE", "/agents/p2-smoke?api-version=v1"): ok(),
        ("POST", "/providers/microsoft/v1/systemone"): ok({"answers": {"ping": {"choice": "yes", "confidence": 0.9}}}),
    }


def token_for(http):
    return sc.TokenProvider(http, "cid", "http://localhost:42356/msi/token", "secret")


def test_private_dns_rejects_public_ip():
    with pytest.raises(sc.CheckFailed, match="공인 IP"):
        sc.check_private_dns("kvx.vault.azure.net", lambda host: "20.40.60.80")
    assert sc.check_private_dns("kvx.vault.azure.net", private) == "192.168.1.10"


def test_token_provider_caches_per_audience_and_sends_identity_header():
    http = FakeHttp({("GET", "/msi/token"): ok({"access_token": "tok"})})
    token = token_for(http)
    assert token(sc.AUDIENCE_STORAGE) == token(sc.AUDIENCE_STORAGE) == "tok"
    token(sc.AUDIENCE_KEYVAULT)
    assert len(http.calls) == 2
    first = http.calls[0]
    assert first["headers"] == {"X-IDENTITY-HEADER": "secret"}
    assert "client_id=cid" in first["url"]
    assert "resource=https%3A%2F%2Fstorage.azure.com" in first["url"]


def test_cosmos_check_uses_aad_authorization_header():
    http = FakeHttp(healthy_routes())
    assert sc.check_cosmos(CFG, http, token_for(http), now="Sat, 31 Oct 2026 00:00:00 GMT") == "lending"
    [call] = http.find("GET", "/dbs")
    assert call["headers"]["Authorization"] == "type%3Daad%26ver%3D1.0%26sig%3Dtok"
    assert call["headers"]["x-ms-version"] == "2018-12-31"
    assert "resource=https%3A%2F%2Fcosmos-x.documents.azure.com" in http.find("GET", "/msi/token")[0]["url"]


def test_agent_roundtrip_uses_prompt_agent_and_responses_then_deletes_agent():
    http = FakeHttp(healthy_routes())
    assert sc.check_agent(CFG, http, token_for(http)) == "resp_1"
    [create] = http.find("POST", "/agents/p2-smoke/versions?api-version=v1")
    assert json.loads(create["body"])["definition"] == {
        "kind": "prompt", "model": "gpt-5.4-mini", "instructions": "Reply with OK."}
    [respond] = http.find("POST", "/openai/v1/responses")
    assert respond["url"].startswith(CFG["project_endpoint"])
    assert json.loads(respond["body"])["agent_reference"] == {"name": "p2-smoke", "type": "agent_reference"}
    assert len(http.find("DELETE", "/agents/p2-smoke?api-version=v1")) == 1


def test_agent_is_deleted_even_when_response_fails():
    routes = healthy_routes()
    routes[("POST", "/openai/v1/responses")] = ok({"id": "resp_1", "status": "failed",
                                                   "error": {"code": "server_error"}})
    http = FakeHttp(routes)
    with pytest.raises(sc.CheckFailed, match="failed"):
        sc.check_agent(CFG, http, token_for(http))
    assert len(http.find("DELETE", "/agents/p2-smoke?api-version=v1")) == 1


def test_main_succeeds_when_all_checks_pass():
    out = io.StringIO()
    code = sc.main(ENV, http=FakeHttp(healthy_routes()), resolver=private, sleep=lambda s: None, out=out)
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert code == 0
    # dns 2 + chat 2 + blob + cosmos + keyvault + health 2 + servicebus + agent + decision + journey + consent
    assert lines[-1] == {"summary": {"total": 14, "failed": 0}}
    assert [r["check"] for r in lines[-3:-1]] == ["journey", "consent"]


def test_main_reports_failure_and_skips_paused_service_bus():
    routes = healthy_routes()
    routes[("GET", "/secrets?")] = sc.Response(403, b'{"error":"Forbidden"}')
    out = io.StringIO()
    env = {**ENV, "SMOKE_CONFIG": json.dumps({**CFG, "servicebus_host": ""})}
    code = sc.main(env, http=FakeHttp(routes), resolver=private, sleep=lambda s: None, out=out)
    results = {r["check"]: r for r in map(json.loads, out.getvalue().splitlines()) if "check" in r}
    assert code == 1
    assert "servicebus" not in results
    assert results["keyvault"]["ok"] is False
    assert "403" in results["keyvault"]["detail"]


def queue_text(call):
    return json.loads(ET.fromstring(call["body"]).findtext("MessageText"))


def journey_http(docs, loan=None):
    routes = {**healthy_routes(), ("GET", "/colls/applications/docs/"): docs}
    if loan is not None:
        routes[("GET", "/colls/loans/docs/")] = loan
    return FakeHttp(routes)


def test_health_check_requires_status_ok_and_flags_placeholder_image():
    http = FakeHttp({("GET", "/healthz"): ok({"status": "ok", "rules_version": "2026.10"})})
    assert sc.check_health("https://ca-policy.int.example/", http) == "2026.10"
    assert http.calls[0]["url"] == "https://ca-policy.int.example/healthz"
    with pytest.raises(sc.CheckFailed, match="ops deploy"):
        sc.check_health("https://ca-policy.int.example", FakeHttp({("GET", "/healthz"): sc.Response(404, b"")}))
    with pytest.raises(sc.CheckFailed, match="degraded"):
        sc.check_health("https://ca-policy.int.example", FakeHttp({("GET", "/healthz"): ok({"status": "degraded"})}))


def test_journey_starts_application_and_approves_within_policy_limit():
    sleeps = []
    http = journey_http([sc.Response(404, b"{}"), app_doc("consent_granted"),
                         app_doc("awaiting_officer", policy=ELIGIBLE, assessment=ASSESSMENT),
                         app_doc("approved", monitoring=MONITORING)])
    detail = sc.check_journey(CFG, http, token_for(http), sleeps.append, "smoke-e2e-test")
    assert detail == ("smoke-e2e-test approved 2,500,000 KRW (3 steps); "
                      "assessment conditional, review passed in 2 rounds; monitoring registered (패션의류)")
    [start] = http.find("POST", "/applications-start/messages")
    assert start["url"] == "https://stfnx.queue.core.windows.net/applications-start/messages"
    assert start["headers"]["x-ms-version"] == "2023-11-03"
    assert queue_text(start) == {"application_id": "smoke-e2e-test", "applicant_id": "demo-healthy-001",
                                 "requested_amount_krw": 3_300_000, "requested_tenor_months": 12}
    [decision] = http.find("POST", "/officer-decisions/messages")
    assert queue_text(decision) == {"application_id": "smoke-e2e-test", "decision": {
        "decision": "approve", "officer_id": "smoke", "amount_krw": 2_500_000, "reason": "smoke e2e"}}
    read = http.find("GET", "/colls/applications/docs/")[0]
    assert read["url"] == "https://cosmos-x.documents.azure.com/dbs/lending/colls/applications/docs/smoke-e2e-test"
    assert read["headers"]["x-ms-documentdb-partitionkey"] == '["smoke-e2e-test"]'
    assert read["headers"]["Authorization"].startswith("type%3Daad")
    assert sleeps == [10.0, 10.0]
    [loan] = http.find("GET", "/colls/loans/docs/")
    assert loan["url"] == "https://cosmos-x.documents.azure.com/dbs/lending/colls/loans/docs/smoke-e2e-test"
    assert loan["headers"]["x-ms-documentdb-partitionkey"] == '["smoke-e2e-test"]'


def approved_then(*later):
    return [app_doc("awaiting_officer", policy=ELIGIBLE, assessment=ASSESSMENT), app_doc("approved"), *later]


def test_journey_waits_for_monitoring_registration_after_approval():
    sleeps = []
    http = journey_http(approved_then(app_doc("approved"), app_doc("approved", monitoring=MONITORING)))
    assert sc.check_journey(CFG, http, token_for(http), sleeps.append, "smoke-e2e-test").endswith("registered (패션의류)")
    assert sleeps == [10.0, 10.0]


@pytest.mark.parametrize("later, loan, match", [
    ([app_doc("approved", flags=["monitoring_unavailable"])], ok(LOAN), "monitoring_unavailable"),
    ([app_doc("approved")], ok(LOAN), "모니터링 등록"),
    ([app_doc("approved", monitoring=MONITORING)], sc.Response(404, b"{}"), "loans"),
])
def test_journey_fails_when_monitoring_is_not_registered(later, loan, match):
    http = journey_http(approved_then(*later), loan=loan)
    with pytest.raises(sc.CheckFailed, match=match):
        sc.check_journey(CFG, http, token_for(http), lambda s: None, "smoke-e2e-test", decision_attempts=3)


def test_journey_fails_fast_when_application_fails():
    http = journey_http(app_doc("failed", error={"stage": "assemble_data", "message": "boom"}))
    with pytest.raises(sc.CheckFailed, match="failed.*assemble_data"):
        sc.check_journey(CFG, http, token_for(http), lambda s: None, "smoke-e2e-test")
    assert http.find("POST", "/officer-decisions/messages") == []


def test_journey_does_not_approve_without_eligible_policy():
    http = journey_http(app_doc("awaiting_officer", policy=None, flags=["policy_unavailable"], assessment=ASSESSMENT))
    with pytest.raises(sc.CheckFailed, match="policy_unavailable"):
        sc.check_journey(CFG, http, token_for(http), lambda s: None, "smoke-e2e-test")
    assert http.find("POST", "/officer-decisions/messages") == []


def test_journey_times_out_when_status_does_not_advance():
    sleeps = []
    http = journey_http(app_doc("received"))
    with pytest.raises(sc.CheckFailed, match="awaiting_officer.*received"):
        sc.check_journey(CFG, http, token_for(http), sleeps.append, "smoke-e2e-test", start_attempts=3)
    assert sleeps == [10.0, 10.0, 10.0]


def test_smoke_checks_uses_only_standard_library():
    # Job 컨테이너(MCR Python 기본 이미지)에는 추가 패키지가 없습니다.
    tree = ast.parse(Path(sc.__file__).read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            modules.add((node.module or "").split(".")[0])
    assert modules - {"__future__"} <= set(sys.stdlib_module_names)


def test_journey_requires_credit_assessment():
    http = journey_http(app_doc("awaiting_officer", policy=ELIGIBLE, assessment=None,
                                flags=["assessment_unavailable"]))
    with pytest.raises(sc.CheckFailed, match="assessment_unavailable"):
        sc.check_journey(CFG, http, token_for(http), lambda s: None, "smoke-e2e-test")
    assert http.find("POST", "/officer-decisions/messages") == []


def test_decision_check_asks_systemone_choice_question():
    http = FakeHttp(healthy_routes())
    assert sc.check_decision(CFG, http, token_for(http)) == "yes (0.90)"
    [call] = http.find("POST", "/providers/microsoft/v1/systemone")
    assert call["url"] == "https://ais-x.services.ai.azure.com/providers/microsoft/v1/systemone"
    body = json.loads(call["body"])
    assert body["model"] == "decision-1"
    assert body["questions"]["ping"]["type"] == "choice"
    assert "resource=https%3A%2F%2Fcognitiveservices.azure.com" in http.find("GET", "/msi/token")[0]["url"]


def test_decision_check_rejects_unexpected_answer():
    http = FakeHttp({**healthy_routes(), ("POST", "/providers/microsoft/v1/systemone"): ok({"answers": {}})})
    with pytest.raises(sc.CheckFailed, match="decision"):
        sc.check_decision(CFG, http, token_for(http))


# ── 동의 만료 → 삭제 증명(P5b) ──

def consent_http(app, proofs, consent=None):
    routes = {**healthy_routes(), ("GET", "/colls/applications/docs/"): app, ("GET", "/colls/deletion_proofs/docs/"): proofs}
    if consent is not None:
        routes[("GET", "/colls/consents/docs/")] = consent
    return FakeHttp(routes)


def test_consent_check_expires_underwriting_consent_and_waits_for_complete_proof():
    sleeps = []
    incomplete = ok({**PROOF, "complete": False, "items": [{"kind": "conversation", "verified": False}]})
    http = consent_http(app_doc("approved", consent=CONSENT), [sc.Response(404, b"{}"), incomplete, ok(PROOF)])
    detail = sc.check_consent_deletion(CFG, http, token_for(http), sleeps.append, "smoke-e2e-test")
    assert detail == "c-smoke expired; proof complete (3 items verified, 2 files in manifest); application retained"
    [command] = http.find("POST", "/consent-commands/messages")
    assert command["url"] == "https://stfnx.queue.core.windows.net/consent-commands/messages"
    assert queue_text(command) == {"consent_id": "c-smoke", "action": "expire_now", "requested_by": "smoke",
                                   "application_id": "smoke-e2e-test"}
    proof = http.find("GET", "/colls/deletion_proofs/docs/")[0]
    assert proof["url"].endswith("/dbs/lending/colls/deletion_proofs/docs/c-smoke")
    assert proof["headers"]["x-ms-documentdb-partitionkey"] == '["c-smoke"]'
    assert http.find("GET", "/colls/consents/docs/")[0]["url"].endswith("/colls/consents/docs/c-smoke")
    assert sleeps == [10.0, 10.0]
    # 증명 뒤에 신청 문서(판단 기록)가 남아 있는지 다시 읽는다.
    assert len(http.find("GET", "/colls/applications/docs/")) == 2


@pytest.mark.parametrize("app, match", [
    (app_doc("awaiting_officer", consent=CONSENT), "approved"),
    (sc.Response(404, b"{}"), "approved"),
    (app_doc("approved"), "consent"),
])
def test_consent_check_runs_only_on_approved_application_with_consent(app, match):
    http = consent_http(app, ok(PROOF))
    with pytest.raises(sc.CheckFailed, match=match):
        sc.check_consent_deletion(CFG, http, token_for(http), lambda s: None, "smoke-e2e-test")
    assert http.find("POST", "/consent-commands/messages") == []


def test_consent_check_fails_on_incomplete_proof_timeout_or_lost_record():
    stuck = ok({**PROOF, "complete": False, "items": [{"kind": "conversation", "target": "conv_1", "verified": False}]})
    http = consent_http(app_doc("approved", consent=CONSENT), stuck)
    with pytest.raises(sc.CheckFailed, match="conversation"):
        sc.check_consent_deletion(CFG, http, token_for(http), lambda s: None, "smoke-e2e-test", attempts=3)
    http = consent_http(app_doc("approved", consent=CONSENT), ok(PROOF), consent=ok({"id": "c-smoke", "status": "granted"}))
    with pytest.raises(sc.CheckFailed, match="granted"):
        sc.check_consent_deletion(CFG, http, token_for(http), lambda s: None, "smoke-e2e-test")
    http = consent_http([app_doc("approved", consent=CONSENT), sc.Response(404, b"{}")], ok(PROOF))
    with pytest.raises(sc.CheckFailed, match="신청 문서"):
        sc.check_consent_deletion(CFG, http, token_for(http), lambda s: None, "smoke-e2e-test")


def test_withdrawn_application_ends_the_wait():
    http = journey_http(app_doc("withdrawn"))
    with pytest.raises(sc.CheckFailed, match="withdrawn"):
        sc.check_journey(CFG, http, token_for(http), lambda s: None, "smoke-e2e-test")
