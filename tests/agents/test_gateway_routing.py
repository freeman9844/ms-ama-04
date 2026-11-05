import dataclasses
import re
from pathlib import Path

import pytest

from agents import collections, data_assembly, early_warning, notices, txn_classify, underwriting
from agents.gateway import (
    CONNECTIONS,
    DIRECT_AGENTS,
    PROBE_AGENT,
    GatewayRunner,
    decision_base,
    gateway_enabled,
    gateway_model,
)
from agents.runtime import AgentRun
from evals.judge import JUDGE_AGENT

RESOURCES = Path(__file__).resolve().parents[2] / "infra" / "resources.bicep"
ON = {"AI_GATEWAY": "apim", "AI_GATEWAY_URL": "https://apim-x.azure-api.net/", "AI_ACCOUNT_ENDPOINT": "https://ais.x"}


@dataclasses.dataclass(frozen=True)
class Spec:
    name: str
    model: str


class Inner:
    def __init__(self):
        self.seen: list[Spec] = []
        self.kwargs: list[dict] = []

    def run(self, spec, input_text, handlers, **kwargs):
        self.seen.append(spec)
        self.kwargs.append(kwargs)
        return AgentRun(spec.name, "1", "conv-1", None, [], 0, 0, model=spec.model)

    def delete_conversation(self, conversation_id):
        return "deleted"


def test_gateway_enabled():
    assert gateway_enabled(ON)
    assert not gateway_enabled({**ON, "AI_GATEWAY": "off"})
    assert not gateway_enabled({"AI_GATEWAY": "apim"})
    assert not gateway_enabled({})


def test_gateway_model_prefixes_connection():
    assert gateway_model("underwriting-analyst", "gpt-5.4") == "gw-analyst/gpt-5.4"
    assert gateway_model(PROBE_AGENT, "gpt-5.4-mini") == "gw-probe/gpt-5.4-mini"


def test_gateway_model_keeps_routed_model():
    assert gateway_model("data-assembly", "gw-data-assembly/gpt-5.4-mini") == "gw-data-assembly/gpt-5.4-mini"


def test_gateway_model_unknown_agent_raises():
    with pytest.raises(KeyError):
        gateway_model("new-agent", "gpt-5.4")


def test_decision_base_on():
    assert decision_base(ON) == "https://apim-x.azure-api.net/decision"


def test_decision_base_off():
    assert decision_base({**ON, "AI_GATEWAY": "off"}) == "https://ais.x"


def test_runner_routes_and_restores_model():
    inner = Inner()
    run = GatewayRunner(inner).run(Spec("collections-agent", "gpt-5.4-mini"), "t", {})
    assert inner.seen[0].model == "gw-collections/gpt-5.4-mini"
    assert run.model == "gpt-5.4-mini"


def test_runner_leaves_direct_agents_and_delegates():
    inner = Inner()
    runner = GatewayRunner(inner)
    runner.run(Spec(JUDGE_AGENT, "gpt-5.4"), "t", {})
    assert inner.seen[0].model == "gpt-5.4"
    assert runner.delete_conversation("c") == "deleted"


def test_runner_raises_round_limit_for_routed_agents_only():
    inner = Inner()
    runner = GatewayRunner(inner)
    runner.run(Spec("early-warning-investigator", "gpt-5.4"), "t", {})
    runner.run(Spec("early-warning-investigator", "gpt-5.4"), "t", {}, max_rounds=3)
    runner.run(Spec(JUDGE_AGENT, "gpt-5.4"), "t", {})
    assert inner.kwargs == [{"max_rounds": 12}, {"max_rounds": 3}, {}]


def test_connections_match_bicep_gateways():
    declared = re.findall(r"\{ name: '(gw-[a-z-]+)', tpm: \d+ \}", RESOURCES.read_text(encoding="utf-8"))
    assert sorted(declared) == sorted(CONNECTIONS.values())


def test_connections_cover_agent_constants():
    agents = {
        data_assembly.AGENT_NAME, underwriting.ANALYST_AGENT, underwriting.REVIEWER_AGENT, early_warning.EW_AGENT,
        txn_classify.FALLBACK_AGENT, collections.COLLECTIONS_AGENT, notices.CONSENT_NOTICE_AGENT,
        notices.GRIEVANCE_AGENT,
    }
    assert set(CONNECTIONS) == agents | {PROBE_AGENT}
    assert JUDGE_AGENT in DIRECT_AGENTS and JUDGE_AGENT not in CONNECTIONS
