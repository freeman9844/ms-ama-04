"""Foundry 프롬프트 에이전트 런타임(function tool 루프 + 구조화 출력).

probe(2026-10-31)로 확인한 REST 형태만 쓴다.
- 버전 생성: POST {project}/agents/{name}/versions?api-version=v1 (같은 정의면 같은 버전)
- 대화 생성: POST {project}/openai/v1/conversations
- 응답 생성: POST {project}/openai/v1/responses (agent_reference + conversation)
도구는 이 프로세스 안에서 실행하고, 도구 예외 내용은 모델에 보내지 않는다.
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

AGENTS_API_VERSION = "v1"
ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


class AgentError(RuntimeError):
    def __init__(self, message: str, conversation_id: str | None = None) -> None:
        super().__init__(message)
        self.conversation_id = conversation_id


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

    def record(self) -> dict[str, Any]:
        """감사 기록용 요약. 도구 출력 본문은 원천 Blob에 있으므로 남기지 않는다."""
        return {
            "agent_name": self.agent_name,
            "agent_version": self.agent_version,
            "conversation_id": self.conversation_id,
            "rounds": self.rounds,
            "total_tokens": self.total_tokens,
            "tool_calls": [
                {"name": c.name, "arguments": c.arguments, "error": c.output.get("error")} for c in self.tool_calls
            ],
        }


def _tokens(response: dict[str, Any]) -> int:
    return int((response.get("usage") or {}).get("total_tokens") or 0)


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
    ) -> None:
        self._base = project_endpoint.rstrip("/")
        self._token = token
        self._client = client or httpx.Client(timeout=timeout)
        self._versions: dict[str, str] = {}

    def _call(self, method: str, path: str, body: Any = None, conversation_id: str | None = None) -> dict[str, Any]:
        try:
            r = self._client.request(
                method, self._base + path, json=body, headers={"Authorization": f"Bearer {self._token()}"}
            )
        except httpx.HTTPError as e:
            raise AgentError(f"{method} {path}: {type(e).__name__}", conversation_id) from e
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
        ref = {"type": "agent_reference", "name": spec.name}

        def respond(payload: Any) -> dict[str, Any]:
            body = {"agent_reference": ref, "conversation": conversation_id, "input": payload}
            return self._call("POST", "/openai/v1/responses", body, conversation_id)

        response = respond(input_text)
        tokens = _tokens(response)
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
            tokens += _tokens(response)

        if response.get("status") != "completed":
            raise AgentError(f"응답 상태 {response.get('status')}", conversation_id)
        try:
            output = spec.output_model.model_validate_json(_output_text(response))
        except ValidationError as e:
            raise AgentError("출력이 스키마와 맞지 않음", conversation_id) from e
        return AgentRun(spec.name, version, conversation_id, output, tool_calls, tokens, rounds)
