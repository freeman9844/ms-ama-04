import ast
import io
import json
import sys
from pathlib import Path

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


def healthy_routes():
    return {
        ("GET", "/msi/token"): ok({"access_token": "tok"}),
        ("POST", "/openai/v1/chat/completions"): ok({"model": "gpt"}),
        ("GET", "?comp=list"): ok(),
        ("GET", "/dbs"): ok({"Databases": [{"id": "lending"}]}),
        ("GET", "/secrets?"): ok({"value": []}),
        ("POST", "/flow.updated/messages"): ok(status=201),
        ("POST", "/assistants?"): ok({"id": "asst_1"}),
        ("POST", "/threads/runs?"): ok({"id": "run_1", "thread_id": "thread_1"}),
        ("GET", "/threads/thread_1/runs/run_1"): ok({"status": "completed"}),
        ("DELETE", "/assistants/asst_1"): ok(),
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


def test_agent_roundtrip_polls_until_completed_and_deletes_agent():
    routes = healthy_routes()
    routes[("GET", "/threads/thread_1/runs/run_1")] = [
        ok({"status": "queued"}), ok({"status": "in_progress"}), ok({"status": "completed"}),
    ]
    http = FakeHttp(routes)
    sleeps = []
    assert sc.check_agent(CFG, http, token_for(http), sleep=sleeps.append) == "run_1"
    assert len(sleeps) == 2
    assert json.loads(http.find("POST", "/assistants?")[0]["body"])["model"] == "gpt-5.4-mini"
    assert len(http.find("DELETE", "/assistants/asst_1")) == 1


def test_agent_is_deleted_even_when_run_fails():
    routes = healthy_routes()
    routes[("GET", "/threads/thread_1/runs/run_1")] = ok({"status": "failed", "last_error": {"code": "server_error"}})
    http = FakeHttp(routes)
    with pytest.raises(sc.CheckFailed, match="failed"):
        sc.check_agent(CFG, http, token_for(http), sleep=lambda s: None)
    assert len(http.find("DELETE", "/assistants/asst_1")) == 1


def test_main_succeeds_when_all_checks_pass():
    out = io.StringIO()
    code = sc.main(ENV, http=FakeHttp(healthy_routes()), resolver=private, sleep=lambda s: None, out=out)
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert code == 0
    # dns 2 + chat 2 + blob + cosmos + keyvault + servicebus + agent
    assert lines[-1] == {"summary": {"total": 9, "failed": 0}}


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
