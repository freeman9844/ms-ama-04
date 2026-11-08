"""평가용 에이전트 runner. 운영 에이전트 이름과 겹치지 않게 이름을 바꾸고, 출력과 대화를 기록·정리한다.

운영 코드는 같은 이름의 최신 버전을 부른다. 평가가 다른 모델로 같은 이름의 버전을 만들면 운영 동작이 바뀌므로
이름 앞에 eval-, 뒤에 모델명을 붙인다(예: eval-underwriting-analyst-gpt-5-6-luna).
"""

import dataclasses
from collections.abc import Mapping
from typing import Any, Protocol

from agents.gateway import DIRECT_AGENTS, GATEWAY_MAX_ROUNDS, gateway_model
from agents.runtime import AgentError, AgentRun, AgentSpec, ToolHandler
from evals.metering import Meter

MAX_NAME = 63
GATEWAY_ERRORS = ("content_blocked", "rate_limited")


class Agents(Protocol):
    def run(self, spec: AgentSpec, input_text: str, handlers: Mapping[str, ToolHandler], *,
            max_rounds: int = ...) -> AgentRun: ...
    def delete_container(self, container_id: str) -> bool: ...
    def delete_conversation(self, conversation_id: str) -> str: ...


def eval_agent_name(name: str, model: str) -> str:
    return f"eval-{name}-{model.replace('.', '-').replace('_', '-')}"[:MAX_NAME].rstrip("-")


class EvalRunner:
    def __init__(self, agents: Agents, meter: Meter, gateway: bool = False) -> None:
        self._agents, self._meter, self._gateway = agents, meter, gateway
        self.runs: list[dict[str, Any]] = []
        self.conversations: list[str] = []

    def _routed(self, spec: AgentSpec) -> bool:
        # 운영과 같은 게이트웨이 연결로 잰다. 채점자는 직접 호출(운영 TPM을 쓰지 않음).
        return self._gateway and spec.name not in DIRECT_AGENTS

    def _model(self, spec: AgentSpec) -> str:
        return gateway_model(spec.name, spec.model) if self._routed(spec) else spec.model

    def run(self, spec: AgentSpec, input_text: str, handlers: Mapping[str, ToolHandler]) -> AgentRun:
        named = dataclasses.replace(spec, name=eval_agent_name(spec.name, spec.model), model=self._model(spec))
        entry: dict[str, Any] = {"suite": self._meter.get("suite"), "config": self._meter.get("config"),
                                 "case": self._meter.get("case"), "agent": spec.name, "model": spec.model}
        self.runs.append(entry)
        with self._meter.scope(model=spec.model):
            try:
                if self._routed(spec):
                    run = self._agents.run(named, input_text, handlers, max_rounds=GATEWAY_MAX_ROUNDS)
                else:
                    run = self._agents.run(named, input_text, handlers)
            except AgentError as e:
                if e.conversation_id:
                    self.conversations.append(e.conversation_id)
                entry["error"] = str(e)[:300]
                raise
        self.conversations.append(run.conversation_id)
        entry.update(output=run.output.model_dump(mode="json"), tokens=run.total_tokens, rounds=run.rounds,
                     tools=[c.name for c in run.tool_calls])
        return dataclasses.replace(run, model=spec.model)

    def gateway_summary(self) -> dict[str, Any]:
        counts = {k: sum(1 for r in self.runs if str(r.get("error", "")).startswith(k)) for k in GATEWAY_ERRORS}
        return {"enabled": self._gateway, **counts}

    def delete_container(self, container_id: str) -> bool:
        return self._agents.delete_container(container_id)

    def outputs(self, **where: str) -> list[dict[str, Any]]:
        return [r["output"] for r in self.runs if "output" in r and all(r.get(k) == v for k, v in where.items())]

    def cleanup(self) -> dict[str, int]:
        """평가 중 만든 Foundry 대화를 지운다. 결과 건수만 돌려준다."""
        counts = {"deleted": 0, "absent": 0, "failed": 0}
        with self._meter.scope(suite="cleanup", config="", case=""):
            for cid in dict.fromkeys(self.conversations):
                outcome = self._agents.delete_conversation(cid)
                counts[outcome if outcome in counts else "failed"] += 1
        return counts
