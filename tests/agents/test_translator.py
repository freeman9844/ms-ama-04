import dataclasses

import pytest

from agents.gateway import GatewayRunner
from agents.runtime import AgentError, AgentRun
from agents.translator import TRANSLATOR_AGENT, Translations, Translator, translator_spec
from tests.agents.test_officer_chat import FakeRunner


def _run(translations, cid="conv-1"):
    return AgentRun(TRANSLATOR_AGENT, "1", cid, Translations(translations=translations), [], 30, 1,
                    input_tokens=40, output_tokens=12)


def test_translate_sends_texts_in_order_and_deletes_conversation():
    runner = FakeRunner(result=_run(["  Food  ", "Sharp revenue drop"]))
    result = Translator(runner, "gpt-6-luna").translate(["식품", "매출 급감"])
    assert result.translations == ["Food", "Sharp revenue drop"]
    assert (result.input_tokens, result.output_tokens) == (40, 12)
    spec, payload, handlers, _ = runner.calls[0]
    assert spec.name == TRANSLATOR_AGENT and spec.tools == () and handlers == {}
    assert payload == {"texts": ["식품", "매출 급감"]}
    assert runner.deleted == ["conv-1"]


def test_translate_rejects_count_mismatch():
    runner = FakeRunner(result=_run(["only one"]))
    with pytest.raises(AgentError):
        Translator(runner, "gpt-6-luna").translate(["하나", "둘"])
    assert runner.deleted == ["conv-1"]


def test_gateway_routes_translator_connection():
    seen = []

    class Inner:
        def run(self, spec, input_text, handlers, **kwargs):
            seen.append(spec.model)
            return dataclasses.replace(_run(["x"]), model=spec.model)

    GatewayRunner(Inner()).run(translator_spec("gpt-6-luna"), "{}", {})
    assert seen == ["gw-translator/gpt-6-luna"]
