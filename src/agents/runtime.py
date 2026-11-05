"""Foundry 프롬프트 에이전트 런타임(function tool 루프 + 구조화 출력).

probe(2026-10-31)로 확인한 REST 형태만 쓴다.
- 버전 생성: POST {project}/agents/{name}/versions?api-version=v1 (같은 정의면 같은 버전)
- 대화 생성: POST {project}/openai/v1/conversations
- 응답 생성: POST {project}/openai/v1/responses (agent_reference + conversation)
도구는 이 프로세스 안에서 실행하고, 도구 예외 내용은 모델에 보내지 않는다.
"""

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

AGENTS_API_VERSION = "v1"
ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


class AgentError(RuntimeError):
    def __init__(
        self, message: str, conversation_id: str | None = None, containers: tuple[str, ...] = ()
    ) -> None:
        super().__init__(message)
        self.conversation_id = conversation_id
        self.containers = containers


MAX_RETRY_AFTER_S = 10.0
DEFAULT_RETRY_AFTER_S = 1.0
# 소문자 부분 문자열. AI 게이트웨이(APIM llm-content-safety·llm-token-limit)와 모델 자체 필터의 오류 본문을 가른다.
# Task 4 관문에서 관측한 본문에 맞춰 보정한다.
# "'innerstatuscode': 403": Agent Service가 APIM llm-content-safety의 403을 HTTP 424 CustomerManagedDownstreamError로
# 감싸 돌려준다(Task 4 관문 3). openai API에서 내부 403은 이 정책뿐이다(키 오류는 401).
BLOCK_MARKERS = (
    "content_filter", "content safety", "contentsafety", "responsibleaipolicyviolation", "jailbreak", "prompt shield",
    "'innerstatuscode': 403",
)
THROTTLE_MARKERS = (
    "rate_limit", "rate limit", "ratelimit", "too many requests", "token limit", "tokens per minute",
)


class GatewayBlocked(AgentError):
    """콘텐츠 안전 정책이 요청·응답을 막았다. 메시지는 'content_blocked:'로 시작하고 본문을 담지 않는다."""


class GatewayThrottled(AgentError):
    """게이트웨이 토큰 한도(429)에 걸렸다. 메시지는 'rate_limited:'로 시작한다."""


def _has(text: str, markers: tuple[str, ...]) -> bool:
    low = text.lower()
    return any(m in low for m in markers)


def _retry_after(response: httpx.Response) -> float:
    try:
        value = float(response.headers.get("retry-after", DEFAULT_RETRY_AFTER_S))
    except ValueError:
        value = DEFAULT_RETRY_AFTER_S
    return min(max(value, 0.0), MAX_RETRY_AFTER_S)


def _status_error(response: dict[str, Any], conversation_id: str | None) -> AgentError:
    """completed가 아닌 응답을 오류로. 차단·한도를 구분하고 본문은 메시지에 넣지 않는다."""
    status = response.get("status")
    detail = json.dumps({k: response.get(k) for k in ("error", "incomplete_details")}, ensure_ascii=False)
    if _has(detail, BLOCK_MARKERS):
        return GatewayBlocked(f"content_blocked: 응답 상태 {status}", conversation_id)
    if _has(detail, THROTTLE_MARKERS):
        return GatewayThrottled(f"rate_limited: 응답 상태 {status}", conversation_id)
    return AgentError(f"응답 상태 {status}", conversation_id)


@dataclass(frozen=True)
class AgentSpec:
    name: str
    model: str
    instructions: str
    tools: tuple[dict[str, Any], ...]
    output_name: str
    output_schema: dict[str, Any]
    output_model: type[BaseModel]

    def definition(self) -> dict[str, Any]:
        return {
            "kind": "prompt",
            "model": self.model,
            "instructions": self.instructions,
            "tools": list(self.tools),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": self.output_name,
                    "strict": True,
                    "schema": self.output_schema,
                }
            },
        }


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    output: dict[str, Any]


@dataclass(frozen=True)
class AgentRun:
    agent_name: str
    agent_version: str
    conversation_id: str
    output: BaseModel
    tool_calls: list[ToolCall]
    total_tokens: int
    rounds: int
    containers: tuple[str, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""

    def record(self) -> dict[str, Any]:
        """감사 기록용 요약. 도구 출력 본문은 원천 Blob에 있으므로 남기지 않는다."""
        return {
            "agent_name": self.agent_name,
            "agent_version": self.agent_version,
            "conversation_id": self.conversation_id,
            "rounds": self.rounds,
            "total_tokens": self.total_tokens,
            "containers": list(self.containers),
            "tool_calls": [
                {"name": c.name, "arguments": c.arguments, "error": c.output.get("error")} for c in self.tool_calls
            ],
        }


def _tokens(response: dict[str, Any]) -> int:
    return int((response.get("usage") or {}).get("total_tokens") or 0)


def _usage(response: dict[str, Any]) -> tuple[int, int, int]:
    usage = response.get("usage") or {}
    return (
        int(usage.get("input_tokens") or 0),
        int(usage.get("output_tokens") or 0),
        int(usage.get("total_tokens") or 0),
    )


def _output_text(response: dict[str, Any]) -> str:
    if text := response.get("output_text"):
        return text
    return "".join(
        part.get("text", "")
        for item in response.get("output", [])
        if item.get("type") == "message"
        for part in item.get("content", [])
    )


class FoundryAgents:
    def __init__(
        self,
        project_endpoint: str,
        token: Callable[[], str],
        *,
        client: httpx.Client | None = None,
        timeout: float = 120.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._base = project_endpoint.rstrip("/")
        self._token = token
        self._client = client or httpx.Client(timeout=timeout)
        self._sleep = sleep
        self._versions: dict[str, str] = {}

    def _request(self, method: str, path: str, body: Any) -> httpx.Response:
        return self._client.request(
            method, self._base + path, json=body, headers={"Authorization": f"Bearer {self._token()}"}
        )

    def _call(self, method: str, path: str, body: Any = None, conversation_id: str | None = None) -> dict[str, Any]:
        try:
            r = self._request(method, path, body)
            if r.status_code == 429:
                # 게이트웨이 토큰 한도. 429는 응답을 만들지 않으므로 한 번만 다시 보낸다(입력 중복 없음).
                self._sleep(_retry_after(r))
                r = self._request(method, path, body)
        except httpx.HTTPError as e:
            raise AgentError(f"{method} {path}: {type(e).__name__}", conversation_id) from e
        if r.status_code == 429:
            raise GatewayThrottled(f"rate_limited: {method} {path}", conversation_id)
        if r.status_code >= 400 and _has(r.text, BLOCK_MARKERS):
            raise GatewayBlocked(f"content_blocked: {method} {path}", conversation_id)
        if r.status_code >= 400 and _has(r.text, THROTTLE_MARKERS):
            raise GatewayThrottled(f"rate_limited: {method} {path}", conversation_id)
        if r.status_code >= 400:
            raise AgentError(f"{method} {path}: HTTP {r.status_code} {r.text[:300]}", conversation_id)
        return r.json() if r.content else {}

    def ensure_agent(self, spec: AgentSpec) -> str:
        if spec.name not in self._versions:
            created = self._call(
                "POST",
                f"/agents/{spec.name}/versions?api-version={AGENTS_API_VERSION}",
                {"definition": spec.definition()},
            )
            self._versions[spec.name] = str(created.get("version", ""))
        return self._versions[spec.name]

    @staticmethod
    def _invoke(call: dict[str, Any], handlers: Mapping[str, ToolHandler]) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            args = json.loads(call.get("arguments") or "{}")
        except json.JSONDecodeError:
            return {}, {"error": "invalid_arguments"}
        if not isinstance(args, dict):
            return {}, {"error": "invalid_arguments"}
        handler = handlers.get(call.get("name", ""))
        if handler is None:
            return args, {"error": "unknown_tool"}
        try:
            return args, handler(args)
        except Exception:  # noqa: BLE001 - 예외 내용은 모델에 노출하지 않는다
            return args, {"error": "tool_failed"}

    def run(
        self,
        spec: AgentSpec,
        input_text: str,
        handlers: Mapping[str, ToolHandler],
        *,
        max_rounds: int = 6,
    ) -> AgentRun:
        version = self.ensure_agent(spec)
        conversation_id = self._call("POST", "/openai/v1/conversations", {})["id"]
        containers: list[str] = []
        try:
            return self._loop(spec, version, conversation_id, input_text, handlers, max_rounds, containers)
        except AgentError as e:
            raise type(e)(str(e), conversation_id, tuple(containers)) from e
        except Exception as e:  # noqa: BLE001 - 정리(컨테이너 삭제·대화 기록)에 필요한 ID를 잃지 않는다
            raise AgentError(f"예상치 못한 오류: {type(e).__name__}", conversation_id, tuple(containers)) from e

    def delete_container(self, container_id: str) -> bool:
        """Code Interpreter 컨테이너(가명 집계 데이터가 올라간 곳)를 즉시 삭제한다. 실패해도 예외 없음."""
        try:
            return bool(self._call("DELETE", f"/openai/v1/containers/{container_id}").get("deleted"))
        except AgentError:
            return False

    def _status(self, method: str, path: str) -> int | None:
        try:
            r = self._client.request(method, self._base + path, headers={"Authorization": f"Bearer {self._token()}"})
        except Exception:  # noqa: BLE001 - 토큰 발급·네트워크 실패도 결과(None)로 돌려준다
            return None
        return r.status_code

    def delete_conversation(self, conversation_id: str) -> str:
        """대화(입력·출력 항목 포함)를 삭제한다. deleted | absent(이미 없음) | failed. 예외 없음."""
        status = self._status("DELETE", f"/openai/v1/conversations/{conversation_id}")
        if status == 404:
            return "absent"
        return "deleted" if status is not None and status < 400 else "failed"

    def conversation_exists(self, conversation_id: str) -> bool | None:
        """삭제 검증용. 알 수 없으면 None."""
        status = self._status("GET", f"/openai/v1/conversations/{conversation_id}")
        if status == 404:
            return False
        return True if status is not None and status < 400 else None

    def _loop(
        self,
        spec: AgentSpec,
        version: str,
        conversation_id: str,
        input_text: str,
        handlers: Mapping[str, ToolHandler],
        max_rounds: int,
        containers: list[str],
    ) -> AgentRun:
        ref = {"type": "agent_reference", "name": spec.name}

        def respond(payload: Any) -> dict[str, Any]:
            body = {"agent_reference": ref, "conversation": conversation_id, "input": payload}
            response = self._call("POST", "/openai/v1/responses", body, conversation_id)
            for item in response.get("output", []):
                cid = item.get("container_id") if item.get("type") == "code_interpreter_call" else None
                if cid and cid not in containers:
                    containers.append(cid)
            return response

        response = respond(input_text)
        inp, out, tokens = _usage(response)
        tool_calls: list[ToolCall] = []
        rounds = 0
        while pending := [o for o in response.get("output", []) if o.get("type") == "function_call"]:
            if rounds >= max_rounds:
                raise AgentError(f"도구 호출 라운드 한도({max_rounds}) 초과", conversation_id)
            rounds += 1
            outputs = []
            for call in pending:
                args, result = self._invoke(call, handlers)
                tool_calls.append(ToolCall(call.get("name", ""), args, result))
                outputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": call["call_id"],
                        "output": json.dumps(result, ensure_ascii=False),
                    }
                )
            response = respond(outputs)
            i, o, t = _usage(response)
            inp, out, tokens = inp + i, out + o, tokens + t

        if response.get("status") != "completed":
            raise _status_error(response, conversation_id)
        try:
            output = spec.output_model.model_validate_json(_output_text(response))
        except ValidationError as e:
            raise AgentError("출력이 스키마와 맞지 않음", conversation_id) from e
        return AgentRun(
            spec.name, version, conversation_id, output, tool_calls, tokens, rounds, tuple(containers),
            input_tokens=inp, output_tokens=out, model=spec.model,
        )
