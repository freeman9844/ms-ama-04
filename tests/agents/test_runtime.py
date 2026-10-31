import json

import httpx
import pytest
from pydantic import BaseModel, ConfigDict

from agents.runtime import AgentError, AgentSpec, FoundryAgents

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
