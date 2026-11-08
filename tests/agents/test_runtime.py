import dataclasses
import json

import httpx
import pytest
from pydantic import BaseModel, ConfigDict

from agents.runtime import AgentError, AgentSpec, FoundryAgents, GatewayBlocked, GatewayThrottled, THROTTLE_MARKERS, _has, _status_error

BASE = "https://ais.example/api/projects/proj-lending"


class Out(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str


SPEC = AgentSpec(
    name="data-assembly",
    model="gpt-5.4-mini",
    instructions="지시문",
    tools=({"type": "function", "name": "fetch_source", "parameters": {"type": "object"}, "strict": True},),
    output_name="assembly_result",
    output_schema={
        "type": "object",
        "properties": {"status": {"type": "string"}},
        "required": ["status"],
        "additionalProperties": False,
    },
    output_model=Out,
)


def fc(call_id: str, name: str, args: dict) -> dict:
    return {"type": "function_call", "call_id": call_id, "name": name, "arguments": json.dumps(args)}


def tool_round(*calls: dict, tokens: int = 50) -> httpx.Response:
    return httpx.Response(200, json={"status": "completed", "output": list(calls), "usage": {"total_tokens": tokens}})


def done(obj, status: str = "completed", tokens: int = 100) -> httpx.Response:
    text = obj if isinstance(obj, str) else json.dumps(obj)
    message = {"type": "message", "content": [{"type": "output_text", "text": text}]}
    return httpx.Response(200, json={"status": status, "output": [message], "usage": {"total_tokens": tokens}})


class FakeFoundry:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[dict] = []
        self.delete_response = httpx.Response(200, json={"id": "cntr", "object": "container.deleted", "deleted": True})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.requests.append(
            {
                "method": request.method,
                "path": request.url.path,
                "api_version": request.url.params.get("api-version"),
                "body": body,
                "auth": request.headers.get("authorization"),
            }
        )
        if request.url.path.endswith("/versions"):
            return httpx.Response(200, json={"name": SPEC.name, "version": "3"})
        if request.url.path.endswith("/conversations"):
            return httpx.Response(200, json={"id": "conv_1"})
        if request.url.path.endswith("/responses"):
            item = self.responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        if request.method == "DELETE" and "/containers/" in request.url.path:
            return self.delete_response
        return httpx.Response(404)

    def responses_bodies(self) -> list[dict]:
        return [r["body"] for r in self.requests if r["path"].endswith("/responses")]


def agents(fake: FakeFoundry) -> FoundryAgents:
    return FoundryAgents(BASE, lambda: "tok", client=httpx.Client(transport=httpx.MockTransport(fake)))


def test_definition_has_function_tools_and_strict_json_schema():
    d = SPEC.definition()
    assert d["kind"] == "prompt" and d["model"] == "gpt-5.4-mini"
    assert d["tools"][0]["name"] == "fetch_source"
    fmt = d["text"]["format"]
    assert fmt == {"type": "json_schema", "name": "assembly_result", "strict": True, "schema": SPEC.output_schema}


def test_run_executes_tool_calls_and_validates_output():
    fake = FakeFoundry(
        tool_round(fc("c1", "fetch_source", {"source": "profile"}), fc("c2", "fetch_source", {"source": "tax"})),
        done({"status": "complete"}),
    )
    seen = []
    run = agents(fake).run(SPEC, "신청 app-1", {"fetch_source": lambda a: seen.append(a) or {"ok": a["source"]}})

    assert seen == [{"source": "profile"}, {"source": "tax"}]
    assert run.output == Out(status="complete")
    assert (run.agent_name, run.agent_version, run.conversation_id) == ("data-assembly", "3", "conv_1")
    assert (run.rounds, run.total_tokens, len(run.tool_calls)) == (1, 150, 2)
    first, second = fake.responses_bodies()
    assert first["input"] == "신청 app-1"
    for body in (first, second):
        assert body["conversation"] == "conv_1"
        assert body["agent_reference"] == {"type": "agent_reference", "name": "data-assembly"}
    assert [(o["type"], o["call_id"], json.loads(o["output"])) for o in second["input"]] == [
        ("function_call_output", "c1", {"ok": "profile"}),
        ("function_call_output", "c2", {"ok": "tax"}),
    ]
    assert {r["auth"] for r in fake.requests} == {"Bearer tok"}
    assert run.record()["tool_calls"] == [
        {"name": "fetch_source", "arguments": {"source": "profile"}, "error": None},
        {"name": "fetch_source", "arguments": {"source": "tax"}, "error": None},
    ]


def test_agent_version_is_ensured_once_per_process():
    fake = FakeFoundry(done({"status": "a"}), done({"status": "b"}))
    client = agents(fake)
    client.run(SPEC, "x", {})
    client.run(SPEC, "y", {})
    versions = [r for r in fake.requests if r["path"].endswith("/versions")]
    assert len(versions) == 1
    assert versions[0]["path"] == "/api/projects/proj-lending/agents/data-assembly/versions"
    assert versions[0]["api_version"] == "v1"
    assert versions[0]["body"] == {"definition": SPEC.definition()}


def test_unknown_tool_bad_arguments_and_handler_failure_are_reported_without_details():
    bad_args = {"type": "function_call", "call_id": "c3", "name": "fetch_source", "arguments": "{not json"}
    fake = FakeFoundry(
        tool_round(fc("c1", "delete_everything", {}), fc("c2", "fetch_source", {"source": "pg"}), bad_args),
        done({"status": "complete"}),
    )

    def boom(_args):
        raise ValueError("내부 경로 /secret")

    run = agents(fake).run(SPEC, "x", {"fetch_source": boom})
    outputs = [json.loads(o["output"]) for o in fake.responses_bodies()[1]["input"]]
    assert outputs == [{"error": "unknown_tool"}, {"error": "tool_failed"}, {"error": "invalid_arguments"}]
    assert "secret" not in json.dumps(fake.responses_bodies()[1])
    assert [c["error"] for c in run.record()["tool_calls"]] == ["unknown_tool", "tool_failed", "invalid_arguments"]


def test_round_limit_raises_with_conversation_id():
    loop = [tool_round(fc(f"c{i}", "fetch_source", {"source": "pg"})) for i in range(3)]
    with pytest.raises(AgentError) as err:
        agents(FakeFoundry(*loop)).run(SPEC, "x", {"fetch_source": lambda a: {}}, max_rounds=2)
    assert err.value.conversation_id == "conv_1"


@pytest.mark.parametrize(
    "final",
    [
        httpx.Response(500, json={"error": "boom"}),
        done("not json"),
        done({"status": 1}),
        done({"status": "complete"}, status="incomplete"),
        httpx.ConnectError("down"),
    ],
)
def test_failures_raise_agent_error_with_conversation_id(final):
    with pytest.raises(AgentError) as err:
        agents(FakeFoundry(final)).run(SPEC, "x", {})
    assert err.value.conversation_id == "conv_1"


def test_failure_before_conversation_has_no_conversation_id():
    def down(_request):
        raise httpx.ConnectError("down")

    client = FoundryAgents(BASE, lambda: "tok", client=httpx.Client(transport=httpx.MockTransport(down)))
    with pytest.raises(AgentError) as err:
        client.run(SPEC, "x", {})
    assert err.value.conversation_id is None


def ci(container_id: str) -> dict:
    return {"type": "code_interpreter_call", "id": "ci_1", "container_id": container_id, "code": "print(1)", "status": "completed"}


def test_run_records_code_interpreter_containers_across_rounds():
    first = httpx.Response(
        200,
        json={"status": "completed", "output": [ci("cntr_a"), fc("c1", "fetch_source", {})], "usage": {"total_tokens": 5}},
    )
    final = done({"status": "ok"})
    final_body = final.json()
    final_body["output"] = [ci("cntr_a"), ci("cntr_b"), *final_body["output"]]
    fake = FakeFoundry(first, httpx.Response(200, json=final_body))
    run = agents(fake).run(SPEC, "입력", {"fetch_source": lambda a: {"ok": True}})
    assert run.containers == ("cntr_a", "cntr_b")
    assert run.record()["containers"] == ["cntr_a", "cntr_b"]


def test_agent_error_carries_containers_for_cleanup():
    body = done("not json").json()
    body["output"] = [ci("cntr_x"), *body["output"]]
    fake = FakeFoundry(httpx.Response(200, json=body))
    with pytest.raises(AgentError) as e:
        agents(fake).run(SPEC, "입력", {})
    assert e.value.containers == ("cntr_x",)
    assert e.value.conversation_id == "conv_1"


@pytest.mark.parametrize(
    "first",
    [
        httpx.Response(200, json={"status": "completed", "output": [ci("cntr_x"), {"type": "function_call", "name": "fetch_source"}]}),
        httpx.Response(200, content=b"<html>gateway</html>"),
    ],
    ids=["call_without_call_id", "non_json_body"],
)
def test_unexpected_errors_become_agent_error_with_ids(first):
    with pytest.raises(AgentError) as e:
        agents(FakeFoundry(first)).run(SPEC, "입력", {"fetch_source": lambda a: {}})
    assert e.value.conversation_id == "conv_1"
    assert e.value.containers == (("cntr_x",) if first.content.startswith(b"{") else ())


def test_run_without_code_interpreter_has_no_containers():
    run = agents(FakeFoundry(done({"status": "ok"}))).run(SPEC, "입력", {})
    assert run.containers == ()


def test_delete_container_calls_containers_api():
    fake = FakeFoundry()
    assert agents(fake).delete_container("cntr_a") is True
    (req,) = fake.requests
    assert (req["method"], req["path"], req["auth"]) == ("DELETE", "/api/projects/proj-lending/openai/v1/containers/cntr_a", "Bearer tok")


def test_delete_container_failure_returns_false():
    fake = FakeFoundry()
    fake.delete_response = httpx.Response(404, json={"error": {"code": "not_found"}})
    assert agents(fake).delete_container("cntr_a") is False


def conversations_api(status: int, *, get_status: int = 404):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.method == "DELETE":
            return httpx.Response(status, json={"id": "conv_a", "deleted": True} if status == 200 else {"error": {}})
        return httpx.Response(get_status, json={"id": "conv_a"} if get_status == 200 else {"error": {}})

    client = FoundryAgents(BASE, lambda: "tok", client=httpx.Client(transport=httpx.MockTransport(handler)))
    return client, seen


@pytest.mark.parametrize(("status", "outcome"), [(200, "deleted"), (404, "absent"), (500, "failed")])
def test_delete_conversation_outcomes(status, outcome):
    client, seen = conversations_api(status)
    assert client.delete_conversation("conv_a") == outcome
    assert seen == [("DELETE", "/api/projects/proj-lending/openai/v1/conversations/conv_a")]


def test_delete_conversation_network_error_is_failed():
    def down(request):
        raise httpx.ConnectError("down")

    client = FoundryAgents(BASE, lambda: "tok", client=httpx.Client(transport=httpx.MockTransport(down)))
    assert client.delete_conversation("conv_a") == "failed"
    assert client.conversation_exists("conv_a") is None


@pytest.mark.parametrize(("get_status", "exists"), [(200, True), (404, False), (500, None)])
def test_conversation_exists(get_status, exists):
    client, seen = conversations_api(200, get_status=get_status)
    assert client.conversation_exists("conv_a") is exists
    assert seen == [("GET", "/api/projects/proj-lending/openai/v1/conversations/conv_a")]

def usage_round(*calls: dict, inp: int, out: int) -> httpx.Response:
    usage = {"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out}
    return httpx.Response(200, json={"status": "completed", "output": list(calls), "usage": usage})


def usage_done(obj, inp: int, out: int) -> httpx.Response:
    message = {"type": "message", "content": [{"type": "output_text", "text": json.dumps(obj)}]}
    usage = {"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out}
    return httpx.Response(200, json={"status": "completed", "output": [message], "usage": usage})


def test_run_accumulates_input_output_tokens_and_model():
    fake = FakeFoundry(
        usage_round(fc("c1", "fetch_source", {"source": "profile"}), inp=40, out=10),
        usage_done({"status": "complete"}, inp=70, out=30),
    )
    run = agents(fake).run(SPEC, "신청", {"fetch_source": lambda a: {"ok": 1}})
    assert (run.input_tokens, run.output_tokens, run.total_tokens) == (110, 40, 150)
    assert run.model == "gpt-5.4-mini"
    assert "input_tokens" not in run.record()  # 감사 기록 형식은 그대로


def test_missing_usage_split_counts_zero():
    run = agents(FakeFoundry(done({"status": "complete"}, tokens=100))).run(SPEC, "신청", {})
    assert (run.input_tokens, run.output_tokens, run.total_tokens) == (0, 0, 100)


def _gateway_agents(responses, sleeps, **kw):
    def handle(request: httpx.Request) -> httpx.Response:
        if "/versions" in request.url.path:
            return httpx.Response(200, json={"version": "1"})
        if request.url.path.endswith("/conversations"):
            return httpx.Response(200, json={"id": "conv-1"})
        return responses.pop(0)

    client = httpx.Client(transport=httpx.MockTransport(handle))
    return FoundryAgents(BASE, lambda: "tok", client=client, sleep=sleeps.append, **kw)


def test_runtime_retries_429_once():
    sleeps = []
    a = _gateway_agents([httpx.Response(429, headers={"retry-after": "2"}), httpx.Response(200, json={"ok": 1})], sleeps)
    assert a._call("POST", "/openai/v1/responses", {}) == {"ok": 1}
    assert sleeps == [2.0]


def test_runtime_without_throttle_retry_raises_on_first_429():
    sleeps = []
    responses = [httpx.Response(429, headers={"retry-after": "2"}), httpx.Response(200, json={"ok": 1})]
    a = _gateway_agents(responses, sleeps, retry_throttle=False)
    with pytest.raises(GatewayThrottled):
        a._call("POST", "/openai/v1/responses", {})
    assert sleeps == [] and len(responses) == 1


def test_runtime_second_429_raises_throttled_without_body():
    sleeps = []
    body = {"error": {"message": "secret-ish detail"}}
    a = _gateway_agents([httpx.Response(429, json=body), httpx.Response(429, json=body)], sleeps)
    with pytest.raises(GatewayThrottled) as info:
        a._call("POST", "/openai/v1/responses", {}, "conv-9")
    assert str(info.value) == "rate_limited: POST /openai/v1/responses"
    assert info.value.conversation_id == "conv-9"
    assert sleeps == [1.0]


@pytest.mark.parametrize("header, expected", [("120", 10.0), ("abc", 1.0), ("-3", 1.0), ("nan", 1.0)])
def test_retry_after_is_clamped(header, expected):
    sleeps = []
    a = _gateway_agents([httpx.Response(429, headers={"retry-after": header}), httpx.Response(200, json={})], sleeps)
    a._call("POST", "/x", {})
    assert sleeps == [expected]


def test_spec_throttle_retries_back_off_exponentially_then_raise():
    sleeps = []
    responses = [httpx.Response(429) for _ in range(6)]
    a = _gateway_agents(responses, sleeps)
    spec = dataclasses.replace(SPEC, throttle_retries=5)
    with pytest.raises(GatewayThrottled):
        a.run(spec, "신청", {})
    assert sleeps == [1.0, 2.0, 4.0, 8.0, 16.0] and responses == []


def test_backoff_honours_longer_retry_after_and_caps_at_max():
    sleeps = []
    responses = [httpx.Response(429, headers={"retry-after": "9"}), httpx.Response(429), httpx.Response(429),
                 httpx.Response(429), httpx.Response(429), httpx.Response(429), done({"status": "ok"})]
    a = _gateway_agents(responses, sleeps)
    assert a._call("POST", "/openai/v1/responses", {}, throttle_retries=6)["status"] == "completed"
    assert sleeps == [9.0, 2.0, 4.0, 8.0, 16.0, 30.0]


def test_spec_throttle_retries_recover_after_three_429s():
    sleeps = []
    responses = [httpx.Response(429), httpx.Response(429), httpx.Response(429), done({"status": "ok"})]
    a = _gateway_agents(responses, sleeps)
    run = a.run(dataclasses.replace(SPEC, throttle_retries=5), "신청", {})
    assert run.output.status == "ok"
    assert sleeps == [1.0, 2.0, 4.0]


def test_context_length_400_is_not_throttled():
    sleeps = []
    body = {
        "error": {
            "code": "context_length_exceeded",
            "message": "This model's maximum context length is 128000 tokens. Please reduce the token limit of your messages.",
        }
    }
    a = _gateway_agents([httpx.Response(400, json=body)], sleeps)
    with pytest.raises(AgentError) as info:
        a._call("POST", "/openai/v1/responses", {})
    assert type(info.value) is AgentError
    assert "HTTP 400" in str(info.value)
    assert sleeps == []


def test_gate2_throttle_body_matches_throttle_markers():
    # Task 4 관문 2 실측 본문(HTTP 429). 상태와 별개로 표지어도 걸려야 한다.
    body = {
        "error": {
            "code": "rate_limit_exceeded",
            "message": "Rate limit exceeded. Model could not be accessed - Received 429 status code from model. "
            "ModelGateway response: {'statusCode': 429, 'message': 'Token limit is exceeded. Try again in 53 seconds.'} "
            "Request ID: req-2.",
            "type": "error",
            "additionalInfo": {"request_id": "req-2", "rate_limit_source": "unknown"},
        }
    }
    assert _has(json.dumps(body), THROTTLE_MARKERS)


# Task 4 관문 3 실측: Agent Service가 APIM llm-content-safety의 403을 424 CustomerManagedDownstreamError로 감싸 돌려준다.
GATE3_424_BODY = {
    "error": {
        "message": "CustomerManagedDownstreamError ModelGateway response: {'error': 'CustomerManagedDownstreamError', "
        "'message': 'Data proxy returned 424 because the customer-managed downstream service responded with 403. "
        "This indicates a customer-managed downstream issue rather than a data proxy failure.', "
        "'innerStatusCode': 403, 'innerReasonPhrase': 'Forbidden'} Request ID: req-1",
        "type": "server_error",
        "param": None,
        "code": "model_error",
        "request_id": "req-1",
    }
}


@pytest.mark.parametrize(
    "status, body",
    [
        (403, {"error": {"code": "ContentSafety", "message": "Prompt Shield detected jailbreak in 사용자 입력"}}),
        (424, GATE3_424_BODY),
    ],
)
def test_runtime_block_marker_raises_blocked_without_body(status, body):
    sleeps = []
    a = _gateway_agents([httpx.Response(status, json=body)], sleeps)
    with pytest.raises(GatewayBlocked) as info:
        a._call("POST", "/openai/v1/responses", {})
    assert str(info.value) == "content_blocked: POST /openai/v1/responses"
    assert sleeps == []


def test_status_error_classifies_failed_responses():
    blocked = _status_error({"status": "failed", "error": {"code": "content_filter"}}, "c1")
    throttled = _status_error({"status": "failed", "error": {"code": "rate_limit_exceeded"}}, "c1")
    other = _status_error({"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}, "c1")
    assert type(blocked) is GatewayBlocked and str(blocked) == "content_blocked: 응답 상태 failed"
    assert type(throttled) is GatewayThrottled and str(throttled) == "rate_limited: 응답 상태 failed"
    assert type(other) is AgentError and str(other) == "응답 상태 incomplete"


def test_run_preserves_gateway_error_type():
    a = _gateway_agents([httpx.Response(403, json={"error": {"code": "ContentSafety"}})], [])
    with pytest.raises(GatewayBlocked) as info:
        a.run(SPEC, "입력", {})
    assert info.value.conversation_id == "conv-1"
