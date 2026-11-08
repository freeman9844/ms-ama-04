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
import xml.sax.saxutils
from dataclasses import dataclass
from typing import Callable

AUDIENCE_COGNITIVE = "https://cognitiveservices.azure.com"
AUDIENCE_FOUNDRY = "https://ai.azure.com"
AUDIENCE_STORAGE = "https://storage.azure.com"
AUDIENCE_KEYVAULT = "https://vault.azure.net"
AUDIENCE_SERVICEBUS = "https://servicebus.azure.net"
E2E_REQUESTED_KRW = 3_300_000
E2E_TENOR_MONTHS = 6
E2E_INTERVAL_SECONDS = 10.0
E2E_ENDED = ("failed", "closed_no_decision", "approved", "conditionally_approved", "declined", "withdrawn")
FLAG_MONITORING_UNAVAILABLE = "monitoring_unavailable"


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


def _cosmos_headers(cfg: dict, token: TokenProvider, now: str | None = None,
                    partition_key: str | None = None) -> dict[str, str]:
    signature = token(f"https://{cfg['cosmos_host']}")
    headers = {
        "Authorization": urllib.parse.quote(f"type=aad&ver=1.0&sig={signature}", safe=""),
        "x-ms-date": now or email.utils.formatdate(usegmt=True),
        "x-ms-version": "2018-12-31",
    }
    if partition_key is not None:
        headers["x-ms-documentdb-partitionkey"] = json.dumps([partition_key])
    return headers


def check_cosmos(cfg: dict, http: Http, token: TokenProvider, now: str | None = None) -> str:
    resp = expect(http("GET", f"https://{cfg['cosmos_host']}/dbs", _cosmos_headers(cfg, token, now)),
                  200, what="cosmos list databases")
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


def check_agent(cfg: dict, http: Http, token: TokenProvider, name: str = "p2-smoke") -> str:
    """프롬프트 에이전트 버전 생성 → responses 호출 → 완료 확인. 에이전트는 항상 삭제합니다.

    capability host(Cosmos 대화 저장, Storage, Search)와 agent 서브넷 경로를 함께 검증합니다.
    클래식 Assistants API(/assistants, /threads)는 새 Foundry 프로젝트에서 invalid_engine_error로
    실패하므로 새 Agents API를 사용합니다.
    """
    base = cfg["project_endpoint"].rstrip("/")
    headers = _bearer(token(AUDIENCE_FOUNDRY), json_body=True)
    definition = {"kind": "prompt", "model": cfg["agent_model"], "instructions": "Reply with OK."}
    expect(http("POST", f"{base}/agents/{name}/versions?api-version=v1", headers, _json({"definition": definition})),
           200, 201, what="agent create")
    try:
        resp = expect(http("POST", f"{base}/openai/v1/responses", headers,
                           _json({"input": "ping", "agent_reference": {"name": name, "type": "agent_reference"}})),
                      200, what="agent response").json()
        if resp.get("status") != "completed":
            raise CheckFailed(f"agent response {resp.get('status')}: {resp.get('error')}")
        return resp["id"]
    finally:
        http("DELETE", f"{base}/agents/{name}?api-version=v1", headers)


def check_decision(cfg: dict, http: Http, token: TokenProvider) -> str:
    """Microsoft-Decision-1 systemone 선택형 질문 1건(거래 의미 분류 경로)."""
    url = f"{cfg['ai_account_endpoint'].rstrip('/')}/providers/microsoft/v1/systemone"
    payload = {"model": cfg["decision_deployment"], "state": {"text": "smoke"},
               "questions": {"ping": {"type": "choice", "instructions": "Answer yes.",
                                      "criteria": {"yes": "always", "no": "never"}}}}
    resp = expect(http("POST", url, _bearer(token(AUDIENCE_COGNITIVE), json_body=True), _json(payload)),
                  200, what="decision systemone")
    try:
        answer = resp.json()["answers"]["ping"]
        return f"{answer['choice']} ({float(answer['confidence']):.2f})"
    except (KeyError, TypeError, ValueError) as e:
        raise CheckFailed(f"decision 응답 형식 오류: {resp.body[:200]!r}") from e


def check_health(url: str, http: Http) -> str:
    """정책 엔진·Mock API의 /healthz. 자리표시 이미지(http.server)는 404를 줍니다."""
    target = f"{url.rstrip('/')}/healthz"
    resp = http("GET", target)
    if resp.status == 404:
        raise CheckFailed(f"{target}: 404 - 앱 이미지가 아직 배포되지 않았습니다(python -m ops deploy)")
    body = expect(resp, 200, what=f"health {target}").json()
    if not isinstance(body, dict) or body.get("status") != "ok":
        raise CheckFailed(f"health {target}: {body!r}")
    return str(body.get("rules_version", "ok"))


def enqueue(cfg: dict, http: Http, token: TokenProvider, queue: str, message: dict) -> None:
    """Storage Queue에 JSON 원문을 넣습니다(host.json messageEncoding: none)."""
    text = xml.sax.saxutils.escape(json.dumps(message))
    body = f"<QueueMessage><MessageText>{text}</MessageText></QueueMessage>".encode("utf-8")
    headers = {**_bearer(token(AUDIENCE_STORAGE)), "x-ms-version": "2023-11-03", "Content-Type": "application/xml"}
    expect(http("POST", f"https://{cfg['queue_host']}/{queue}/messages", headers, body), 201,
           what=f"queue {queue}")


def read_doc(cfg: dict, http: Http, token: TokenProvider, container: str, doc_id: str) -> dict | None:
    """id = 파티션 키인 문서(applications·loans·consents·deletion_proofs)를 읽습니다."""
    url = f"https://{cfg['cosmos_host']}/dbs/{cfg['cosmos_database']}/colls/{container}/docs/{doc_id}"
    resp = expect(http("GET", url, _cosmos_headers(cfg, token, partition_key=doc_id)), 200, 404,
                  what=f"cosmos read {container}")
    return None if resp.status == 404 else resp.json()


def read_application(cfg: dict, http: Http, token: TokenProvider, application_id: str) -> dict | None:
    return read_doc(cfg, http, token, "applications", application_id)


def wait_for_status(read: Callable[[], dict | None], target: str, sleep: Callable[[float], None],
                    attempts: int) -> dict:
    status = None
    for _ in range(attempts):
        doc = read()
        status = doc.get("status") if doc else None
        if status == target:
            return doc
        if status in E2E_ENDED:
            raise CheckFailed(f"신청이 {status}로 끝났습니다: error={doc.get('error')} flags={doc.get('flags')}")
        sleep(E2E_INTERVAL_SECONDS)
    raise CheckFailed(f"{attempts * E2E_INTERVAL_SECONDS:.0f}초 안에 {target}에 도달하지 못했습니다"
                      f"(마지막 상태: {status})")


def wait_for_monitoring(doc: dict, read: Callable[[], dict | None], sleep: Callable[[float], None],
                        attempts: int) -> dict:
    """승인 저장 뒤 오케스트레이터가 사후 모니터링(loans)을 등록하고 신청 문서를 한 번 더 저장합니다."""
    for _ in range(attempts):
        if doc.get("monitoring"):
            return doc
        if FLAG_MONITORING_UNAVAILABLE in doc.get("flags", ()):
            raise CheckFailed(f"사후 모니터링 등록 실패: flags={doc.get('flags')}")
        sleep(E2E_INTERVAL_SECONDS)
        doc = read() or {}
    raise CheckFailed(f"{attempts * E2E_INTERVAL_SECONDS:.0f}초 안에 모니터링 등록이 기록되지 않았습니다")


def check_journey(cfg: dict, http: Http, token: TokenProvider, sleep: Callable[[float], None],
                  application_id: str, start_attempts: int = 120, decision_attempts: int = 30) -> str:
    """신청 접수 → 동의 → 데이터 조립(에이전트) → 심사 → 정책 → 심사 에이전트 → 심사역 승인 → 모니터링 등록.

    사후 13주 재생(LLM 비용·수 분)은 smoke에서 돌리지 않습니다. 콘솔의 재생 버튼으로 시연합니다.
    """
    enqueue(cfg, http, token, cfg["start_queue"], {
        "application_id": application_id, "applicant_id": cfg["e2e_applicant"],
        "requested_amount_krw": E2E_REQUESTED_KRW, "requested_tenor_months": E2E_TENOR_MONTHS})

    def read() -> dict | None:
        return read_application(cfg, http, token, application_id)

    doc = wait_for_status(read, "awaiting_officer", sleep, start_attempts)
    policy = doc.get("policy") or {}
    if policy.get("decision") not in ("eligible", "refer"):
        raise CheckFailed(f"승인할 수 없는 정책 결과: {policy.get('decision')} flags={doc.get('flags')}")
    assessment = doc.get("assessment")
    if not assessment:
        raise CheckFailed(f"심사 에이전트 결과 없음: flags={doc.get('flags')} run={doc.get('assessment_run')}")
    review = assessment["review"]
    amount = min(E2E_REQUESTED_KRW, int(policy["max_amount_krw"]))
    enqueue(cfg, http, token, cfg["decision_queue"], {"application_id": application_id, "decision": {
        "decision": "approve", "officer_id": "smoke", "amount_krw": amount, "reason": "smoke e2e"}})
    doc = wait_for_status(read, "approved", sleep, decision_attempts)
    steps = len(doc.get("timeline", []))
    wait_for_monitoring(doc, read, sleep, decision_attempts)
    loan = read_doc(cfg, http, token, "loans", application_id)
    if not loan or not loan.get("baseline"):
        raise CheckFailed(f"loans 문서가 없거나 기준선이 비었습니다: {application_id}")
    return (f"{application_id} approved {amount:,} KRW ({steps} steps); "
            f"assessment {assessment['recommendation']}, "
            f"review {'passed' if review['passed'] else 'not passed'} in {review['rounds']} rounds; "
            f"monitoring registered ({loan.get('industry')})")


def check_consent_deletion(cfg: dict, http: Http, token: TokenProvider, sleep: Callable[[float], None],
                           application_id: str, attempts: int = 30) -> str:
    """승인된 smoke 신청의 심사 동의를 '지금 만료'시키고 삭제 증명이 완료되는지 확인합니다(P5b).

    원천 데이터·Foundry 대화·컨테이너가 지워지고, 신청 문서(판단 기록)는 남아야 합니다.
    """
    doc = read_application(cfg, http, token, application_id)
    if not doc or doc.get("status") != "approved":
        raise CheckFailed(f"approved 신청이 없어 건너뜁니다(상태: {doc.get('status') if doc else None})")
    consent_id = (doc.get("consent") or {}).get("consent_id")
    if not consent_id:
        raise CheckFailed(f"신청에 consent가 없습니다: {application_id}")
    enqueue(cfg, http, token, cfg["consent_command_queue"], {
        "consent_id": consent_id, "action": "expire_now", "requested_by": "smoke", "application_id": application_id})
    proof = None
    for _ in range(attempts):
        proof = read_doc(cfg, http, token, "deletion_proofs", consent_id)
        if proof and proof.get("complete"):
            break
        sleep(E2E_INTERVAL_SECONDS)
    else:
        pending = [f"{i.get('kind')}:{i.get('target')}" for i in (proof or {}).get("items", []) if not i.get("verified")]
        raise CheckFailed(f"{attempts * E2E_INTERVAL_SECONDS:.0f}초 안에 삭제 증명이 완료되지 않았습니다"
                          f"(증명 {'있음' if proof else '없음'}, 미확인 {pending})")
    consent = read_doc(cfg, http, token, "consents", consent_id) or {}
    if consent.get("status") != "expired":
        raise CheckFailed(f"동의 상태가 expired가 아닙니다: {consent.get('status')}")
    if read_application(cfg, http, token, application_id) is None:
        raise CheckFailed(f"삭제 뒤 신청 문서(판단 기록)가 사라졌습니다: {application_id}")
    return (f"{consent_id} expired; proof complete ({len(proof['items'])} items verified, "
            f"{len(proof.get('manifest', []))} files in manifest); application retained")


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
    checks += [
        ("health:policy", lambda: check_health(cfg["policy_url"], http)),
        ("health:mockapi", lambda: check_health(cfg["mockapi_url"], http)),
    ]
    if cfg.get("servicebus_host"):
        checks.append(("servicebus", lambda: check_servicebus(cfg, http, token)))
    checks.append(("agent", lambda: check_agent(cfg, http, token)))
    checks.append(("decision", lambda: check_decision(cfg, http, token)))
    application_id = time.strftime("smoke-%Y%m%d-%H%M%S", time.gmtime())
    checks.append(("journey", lambda: check_journey(cfg, http, token, sleep, application_id)))
    checks.append(("consent", lambda: check_consent_deletion(cfg, http, token, sleep, application_id)))
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
