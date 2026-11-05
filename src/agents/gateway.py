"""AI 게이트웨이(APIM) 라우팅.

운영 에이전트는 Foundry 프로젝트의 APIM 연결(gw-*)을 거쳐 모델을 부른다. 에이전트 정의의 모델을
'<연결>/<배포>'로 바꾸면 Agent Service가 그 연결(APIM 구독 = 에이전트별 토큰 한도)로 호출한다.
연결 이름은 infra/resources.bicep의 gateways 표와 같아야 한다(테스트가 교차 확인).
Decision-1은 APIM decision API(호출자 Entra 토큰 검증)로 보낸다. AI_GATEWAY=off면 둘 다 직접 호출한다.
"""

import dataclasses
from collections.abc import Mapping
from typing import Any

from agents.runtime import AgentRun

CONNECTIONS: dict[str, str] = {
    "underwriting-analyst": "gw-analyst",
    "early-warning-investigator": "gw-early-warning",
    "underwriting-reviewer": "gw-reviewer",
    "data-assembly": "gw-data-assembly",
    "txn-classifier": "gw-txn-classifier",
    "collections-agent": "gw-collections",
    "consent-notice-agent": "gw-consent-notice",
    "grievance-reply-agent": "gw-grievance",
    "gateway-probe": "gw-probe",
}
PROBE_AGENT = "gateway-probe"
# 평가 채점자는 운영 경로가 아니므로 게이트웨이를 거치지 않는다(운영 TPM을 쓰지 않게).
DIRECT_AGENTS = frozenset({"quality-judge"})
DECISION_PATH = "/decision"
# 게이트웨이 경로(chat completions)에서는 모델이 한 턴에 도구를 적게 불러 라운드가 늘어난다(Task 11 실측) — 직접 호출 기본값 6의 두 배.
GATEWAY_MAX_ROUNDS = 12


def gateway_enabled(env: Mapping[str, str]) -> bool:
    return env.get("AI_GATEWAY", "off") == "apim" and bool(env.get("AI_GATEWAY_URL"))


def gateway_model(agent: str, model: str) -> str:
    """에이전트의 연결을 모델 앞에 붙인다. 표에 없는 에이전트는 KeyError(조용히 우회하지 않는다)."""
    if "/" in model:
        return model
    return f"{CONNECTIONS[agent]}/{model}"


def decision_base(env: Mapping[str, str]) -> str:
    """DecisionClient의 기준 URL. 게이트웨이가 켜져 있으면 APIM decision API."""
    if gateway_enabled(env):
        return env["AI_GATEWAY_URL"].rstrip("/") + DECISION_PATH
    return env["AI_ACCOUNT_ENDPOINT"]


class GatewayRunner:
    """FoundryAgents를 감싸 모델을 게이트웨이 연결로 바꾼다. 결과의 model은 원래 배포 이름으로 되돌린다(계량·단가용)."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def run(self, spec: Any, input_text: str, handlers: Any, **kwargs: Any) -> Any:
        if spec.name in DIRECT_AGENTS:
            return self._inner.run(spec, input_text, handlers, **kwargs)
        routed = dataclasses.replace(spec, model=gateway_model(spec.name, spec.model))
        kwargs.setdefault("max_rounds", GATEWAY_MAX_ROUNDS)
        run = self._inner.run(routed, input_text, handlers, **kwargs)
        return dataclasses.replace(run, model=spec.model) if isinstance(run, AgentRun) else run

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)
