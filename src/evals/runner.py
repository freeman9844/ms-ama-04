"""평가용 에이전트 runner. 운영 에이전트 이름과 겹치지 않게 이름을 바꾸고, 출력과 대화를 기록·정리한다.

운영 코드는 같은 이름의 최신 버전을 부른다. 평가가 다른 모델로 같은 이름의 버전을 만들면 운영 동작이 바뀌므로
이름 앞에 eval-, 뒤에 모델명을 붙인다(예: eval-underwriting-analyst-gpt-5-4-mini).
"""

import dataclasses
from collections.abc import Mapping
from typing import Any, Protocol

from agents.runtime import AgentError, AgentRun, AgentSpec, ToolHandler
from evals.metering import Meter

MAX_NAME = 63


class Agents(Protocol):
    def run(self, spec: AgentSpec, input_text: str, handlers: Mapping[str, ToolHandler]) -> AgentRun: ...
    def delete_container(self, container_id: str) -> bool: ...
    def delete_conversation(self, conversation_id: str) -> str: ...


def eval_agent_name(name: str, model: str) -> str:
    return f"eval-{name}-{model.replace('.', '-').replace('_', '-')}"[:MAX_NAME].rstrip("-")


class EvalRunner:
    def __init__(self, agents: Agents, meter: Meter) -> None:
        self._agents, self._meter = agents, meter
        self.runs: list[dict[str, Any]] = []
        self.conversations: list[str] = []

    def run(self, spec: AgentSpec, input_text: str, handlers: Mapping[str, ToolHandler]) -> AgentRun:
        named = dataclasses.replace(spec, name=eval_agent_name(spec.name, spec.model))
        entry: dict[str, Any] = {"suite": self._meter.get("suite"), "config": self._meter.get("config"),
                                 "case": self._meter.get("case"), "agent": spec.name, "model": spec.model}
        self.runs.append(entry)
        with self._meter.scope(model=spec.model):
            try:
                run = self._agents.run(named, input_text, handlers)
            except AgentError as e:
                if e.conversation_id:
                    self.conversations.append(e.conversation_id)
                entry["error"] = str(e)[:300]
                raise
        self.conversations.append(run.conversation_id)
        entry.update(output=run.output.model_dump(mode="json"), tokens=run.total_tokens, rounds=run.rounds,
                     tools=[c.name for c in run.tool_calls])
        return run

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
