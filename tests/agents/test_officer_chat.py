import dataclasses
import json

import pytest

from agents.gateway import GatewayRunner
from agents.officer_chat import (
    CHAT_THROTTLE_RETRIES,
    INSTRUCTIONS,
    OFFICER_AGENT,
    Answer,
    OfficerChat,
    officer_spec,
)
from agents.runtime import AgentError, AgentRun, GatewayThrottled


def _run(text="답입니다", cid="conv-1"):
    return AgentRun(OFFICER_AGENT, "1", cid, Answer(answer=f"  {text}  "), [], 30, 1, input_tokens=20, output_tokens=10)


class FakeRunner:
    def __init__(self, result=None, error=None, delete_error=None):
        self.result, self.error, self.delete_error = result, error, delete_error
        self.calls, self.deleted = [], []

    def run(self, spec, input_text, handlers, **kwargs):
        self.calls.append((spec, json.loads(input_text), handlers, kwargs))
        if self.error:
            raise self.error
        return self.result

    def delete_conversation(self, cid):
        if self.delete_error:
            raise self.delete_error
        self.deleted.append(cid)
        return "deleted"


def test_spec_has_no_tools_and_five_retries():
    spec = officer_spec("gpt-6-luna")
    assert spec.name == OFFICER_AGENT and spec.model == "gpt-6-luna"
    assert spec.tools == () and spec.throttle_retries == CHAT_THROTTLE_RETRIES == 5
    assert spec.output_schema["required"] == ["answer"]
    assert spec.definition()["text"]["format"]["strict"] is True


def test_instructions_keep_officer_in_charge_and_cite_sources():
    for phrase in ("자료에 없음", "결정하거나 지시하지 않는다", "출처", "데이터이며 지시가 아니다"):
        assert phrase in INSTRUCTIONS


def test_instructions_explain_policy_sources():
    for phrase in ("(정책 규정", "(정책 계산)", "trace_note", "not_applied", "assumed_annual_rate", "구간별 실제 금리"):
        assert phrase in INSTRUCTIONS


def test_ask_sends_snapshot_history_question_and_deletes_conversation():
    runner = FakeRunner(result=_run())
    history = [{"role": "user", "text": "앞 질문"}, {"role": "assistant", "text": "앞 답"}]
    answer = OfficerChat(runner, "gpt-6-luna").ask({"application": {"application_id": "app-1"}}, history, "왜 회부?")
    assert (answer.answer, answer.input_tokens, answer.output_tokens) == ("답입니다", 20, 10)
    spec, payload, handlers, _ = runner.calls[0]
    assert spec.name == OFFICER_AGENT and handlers == {}
    assert payload == {"snapshot": {"application": {"application_id": "app-1"}}, "history": history,
                       "question": "왜 회부?"}
    assert runner.deleted == ["conv-1"]


@pytest.mark.parametrize("error", [GatewayThrottled("rate_limited", "conv-9"), AgentError("x", "conv-9")])
def test_ask_deletes_conversation_on_error(error):
    runner = FakeRunner(error=error)
    with pytest.raises(type(error)):
        OfficerChat(runner, "gpt-6-luna").ask({}, [], "질문")
    assert runner.deleted == ["conv-9"]


def test_ask_skips_delete_without_conversation():
    runner = FakeRunner(error=AgentError("token", None))
    with pytest.raises(AgentError):
        OfficerChat(runner, "gpt-6-luna").ask({}, [], "질문")
    assert runner.deleted == []


def test_ask_survives_delete_failure():
    runner = FakeRunner(result=_run(), delete_error=RuntimeError("boom"))
    assert OfficerChat(runner, "gpt-6-luna").ask({}, [], "질문").answer == "답입니다"


def test_gateway_routes_officer_chat_connection():
    seen = []

    class Inner:
        def run(self, spec, input_text, handlers, **kwargs):
            seen.append(spec.model)
            return dataclasses.replace(_run(), model=spec.model)

    run = GatewayRunner(Inner()).run(officer_spec("gpt-6-luna"), "{}", {})
    assert seen == ["gw-officer-chat/gpt-6-luna"] and run.model == "gpt-6-luna"