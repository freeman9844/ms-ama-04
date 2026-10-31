"""배포 후 VNet 안에서 실행하는 smoke 점검(표준 라이브러리 전용).

Container Apps Job(job-smoke)이 `python3 -c <이 파일 내용>`으로 실행합니다. 설정은 환경 변수
SMOKE_CONFIG(JSON), 관리 ID는 AZURE_CLIENT_ID, IDENTITY_ENDPOINT, IDENTITY_HEADER로 받습니다.
점검마다 JSON 줄을 출력하고, 하나라도 실패하면 종료 코드 1을 반환합니다.
"""
from __future__ import annotations

import email.utils
import ipaddress
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable

AUDIENCE_COGNITIVE = "https://cognitiveservices.azure.com"
AUDIENCE_FOUNDRY = "https://ai.azure.com"
AUDIENCE_STORAGE = "https://storage.azure.com"
AUDIENCE_KEYVAULT = "https://vault.azure.net"
AUDIENCE_SERVICEBUS = "https://servicebus.azure.net"


class CheckFailed(Exception):
    """점검 실패."""


@dataclass
class Response:
    status: int
    body: bytes

    def json(self):
        return json.loads(self.body.decode("utf-8") or "null")


Http = Callable[..., Response]


def urllib_http(method: str, url: str, headers: dict[str, str] | None = None,
                body: bytes | None = None) -> Response:
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            return Response(resp.status, resp.read())
    except urllib.error.HTTPError as err:
        return Response(err.code, err.read())


def expect(resp: Response, *statuses: int, what: str) -> Response:
    if resp.status not in statuses:
        raise CheckFailed(f"{what}: HTTP {resp.status} {resp.body[:300]!r}")
    return resp


def _bearer(token: str, json_body: bool = False) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def _json(obj) -> bytes:
    return json.dumps(obj).encode("utf-8")


class TokenProvider:
    """Container Apps 관리 ID 엔드포인트에서 토큰을 받아 audience별로 캐시합니다."""

    def __init__(self, http: Http, client_id: str, endpoint: str, secret_header: str):
        self.http = http
        self.client_id = client_id
        self.endpoint = endpoint
        self.secret_header = secret_header
        self._cache: dict[str, str] = {}

    def __call__(self, audience: str) -> str:
        if audience not in self._cache:
            query = urllib.parse.urlencode(
                {"api-version": "2019-08-01", "resource": audience, "client_id": self.client_id})
            resp = expect(self.http("GET", f"{self.endpoint}?{query}", {"X-IDENTITY-HEADER": self.secret_header}),
                          200, what=f"token {audience}")
            self._cache[audience] = resp.json()["access_token"]
        return self._cache[audience]


def check_private_dns(host: str, resolver: Callable[[str], str] = socket.gethostbyname) -> str:
    ip = resolver(host)
    if not ipaddress.ip_address(ip).is_private:
        raise CheckFailed(f"{host} -> {ip}: Private Endpoint가 아니라 공인 IP로 해석됩니다")
    return ip


def check_chat(cfg: dict, http: Http, token: TokenProvider, model: str) -> str:
    url = f"https://{cfg['openai_host']}/openai/v1/chat/completions"
    payload = {"model": model, "messages": [{"role": "user", "content": "Reply with OK."}],
               "max_completion_tokens": 64}
    resp = expect(http("POST", url, _bearer(token(AUDIENCE_COGNITIVE), json_body=True), _json(payload)),
                  200, what=f"chat {model}")
    return resp.json().get("model", model)


def check_blob(cfg: dict, http: Http, token: TokenProvider) -> str:
    headers = {**_bearer(token(AUDIENCE_STORAGE)), "x-ms-version": "2023-11-03"}
    expect(http("GET", f"https://{cfg['blob_host']}/?comp=list", headers), 200, what="blob list containers")
    return "containers listed"


def check_cosmos(cfg: dict, http: Http, token: TokenProvider, now: str | None = None) -> str:
    host = cfg["cosmos_host"]
    signature = token(f"https://{host}")
    headers = {
        "Authorization": urllib.parse.quote(f"type=aad&ver=1.0&sig={signature}", safe=""),
        "x-ms-date": now or email.utils.formatdate(usegmt=True),
        "x-ms-version": "2018-12-31",
    }
    resp = expect(http("GET", f"https://{host}/dbs", headers), 200, what="cosmos list databases")
    return ",".join(sorted(db["id"] for db in resp.json().get("Databases", [])))


def check_keyvault(cfg: dict, http: Http, token: TokenProvider) -> str:
    url = f"https://{cfg['keyvault_host']}/secrets?api-version=7.4"
    expect(http("GET", url, _bearer(token(AUDIENCE_KEYVAULT))), 200, what="key vault list secrets")
    return "secrets listed"


def check_servicebus(cfg: dict, http: Http, token: TokenProvider) -> str:
    url = f"https://{cfg['servicebus_host']}/{cfg['servicebus_topic']}/messages"
    body = _json({"type": "smoke", "at": time.time()})
    expect(http("POST", url, _bearer(token(AUDIENCE_SERVICEBUS), json_body=True), body), 201,
           what="service bus send")
    return cfg["servicebus_topic"]


def check_agent(cfg: dict, http: Http, token: TokenProvider, sleep: Callable[[float], None] = time.sleep,
                poll_seconds: float = 2.0, max_polls: int = 60) -> str:
    """에이전트 생성 → 스레드 실행 → 완료 대기. 에이전트는 항상 삭제합니다.

    capability host(Cosmos 스레드, Storage, Search)와 agent 서브넷 경로를 함께 검증합니다.
    """
    base = cfg["project_endpoint"].rstrip("/")
    query = "api-version=v1"
    headers = _bearer(token(AUDIENCE_FOUNDRY), json_body=True)
    agent = expect(http("POST", f"{base}/assistants?{query}", headers,
                        _json({"name": "p2-smoke", "model": cfg["agent_model"], "instructions": "Reply with OK."})),
                   200, 201, what="agent create").json()
    try:
        run = expect(http("POST", f"{base}/threads/runs?{query}", headers,
                          _json({"assistant_id": agent["id"],
                                 "thread": {"messages": [{"role": "user", "content": "ping"}]}})),
                     200, 201, what="agent run create").json()
        for _ in range(max_polls):
            status = expect(http("GET", f"{base}/threads/{run['thread_id']}/runs/{run['id']}?{query}", headers),
                            200, what="agent run get").json()
            if status["status"] == "completed":
                return run["id"]
            if status["status"] in ("failed", "cancelled", "expired"):
                raise CheckFailed(f"agent run {status['status']}: {status.get('last_error')}")
            sleep(poll_seconds)
        raise CheckFailed("agent run 대기 시간 초과")
    finally:
        http("DELETE", f"{base}/assistants/{agent['id']}?{query}", headers)


def build_checks(cfg: dict, http: Http, token: TokenProvider, resolver: Callable[[str], str],
                 sleep: Callable[[float], None]) -> list[tuple[str, Callable[[], str]]]:
    checks: list[tuple[str, Callable[[], str]]] = [
        (f"dns:{host}", lambda host=host: check_private_dns(host, resolver)) for host in cfg["private_hosts"]
    ]
    checks += [(f"chat:{model}", lambda model=model: check_chat(cfg, http, token, model))
               for model in cfg["chat_models"]]
    checks += [
        ("blob", lambda: check_blob(cfg, http, token)),
        ("cosmos", lambda: check_cosmos(cfg, http, token)),
        ("keyvault", lambda: check_keyvault(cfg, http, token)),
    ]
    if cfg.get("servicebus_host"):
        checks.append(("servicebus", lambda: check_servicebus(cfg, http, token)))
    checks.append(("agent", lambda: check_agent(cfg, http, token, sleep)))
    return checks


def run_checks(checks: list[tuple[str, Callable[[], str]]]) -> list[dict]:
    results = []
    for name, check in checks:
        started = time.monotonic()
        try:
            detail, ok = check(), True
        except Exception as exc:  # 한 점검이 실패해도 나머지를 계속 실행합니다.
            detail, ok = f"{type(exc).__name__}: {exc}", False
        results.append({"check": name, "ok": ok, "detail": detail,
                        "seconds": round(time.monotonic() - started, 2)})
    return results


def main(environ=None, http: Http = urllib_http, resolver: Callable[[str], str] = socket.gethostbyname,
         sleep: Callable[[float], None] = time.sleep, out=None) -> int:
    environ = os.environ if environ is None else environ
    out = out or sys.stdout
    cfg = json.loads(environ["SMOKE_CONFIG"])
    token = TokenProvider(http, environ["AZURE_CLIENT_ID"], environ["IDENTITY_ENDPOINT"], environ["IDENTITY_HEADER"])
    results = run_checks(build_checks(cfg, http, token, resolver, sleep))
    for result in results:
        print(json.dumps(result, ensure_ascii=False), file=out)
    failed = sum(not r["ok"] for r in results)
    print(json.dumps({"summary": {"total": len(results), "failed": failed}}), file=out)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
