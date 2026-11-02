"""평가 실행 백엔드: 실제 Azure(관리 ID) 또는 가짜 HTTP 에뮬레이터. 둘 다 같은 계측 transport를 거친다."""

import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from agents.runtime import FoundryAgents
from agents.txn_classify import DecisionClient
from evals.fake import FakeAzure
from evals.metering import Meter, MeteredTransport

FOUNDRY_SCOPE = "https://ai.azure.com/.default"
COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"
FAKE_ENDPOINT = "https://fake.invalid"
TIMEOUT_S = 120.0
REFRESH_SKEW_S = 300


class CachedToken:
    """만료 5분 전까지 토큰을 재사용한다. AzureCliCredential은 호출마다 az 프로세스를 띄워 느리고 간헐적으로 실패한다."""

    def __init__(self, credential: Any, scope: str, clock: Callable[[], float] = time.time) -> None:
        self._credential, self._scope, self._clock = credential, scope, clock
        self._token: Any = None
        self._lock = threading.Lock()

    def __call__(self) -> str:
        with self._lock:
            if self._token is None or self._token.expires_on - REFRESH_SKEW_S <= self._clock():
                self._token = self._credential.get_token(self._scope)
            return self._token.token


@dataclass(frozen=True)
class Models:
    """운영 기본값(infra/resources.bicep 오케스트레이터 환경변수)과 같게 둔다."""

    full: str = "gpt-5.4"
    mini: str = "gpt-5.4-mini"
    decision: str = "decision-1"
    reviewer: str = "gpt-5.4-mini"
    judge: str = "gpt-5.4"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Models":
        d = cls()
        return cls(
            full=env.get("EVAL_FULL_MODEL", d.full),
            mini=env.get("EVAL_MINI_MODEL", d.mini),
            decision=env.get("DECISION_DEPLOYMENT", d.decision),
            reviewer=env.get("REVIEWER_MODEL", d.reviewer),
            judge=env.get("EVAL_JUDGE_MODEL", d.judge),
        )


@dataclass
class Backend:
    agents: FoundryAgents
    decider: DecisionClient
    client: httpx.Client
    fake: FakeAzure | None = None
    info: dict[str, Any] = field(default_factory=dict)

    def close(self) -> None:
        self.client.close()


def _client(meter: Meter, inner: httpx.BaseTransport | None, sleep: Callable[[float], None] | None = None) -> httpx.Client:
    kw: dict[str, Any] = {"sleep": sleep} if sleep else {}
    return httpx.Client(transport=MeteredTransport(meter, inner, **kw), timeout=TIMEOUT_S)


def fake_backend(meter: Meter, models: Models) -> Backend:
    fake = FakeAzure()
    client = _client(meter, httpx.MockTransport(fake), sleep=lambda _: None)
    return Backend(
        agents=FoundryAgents(FAKE_ENDPOINT, lambda: "fake", client=client),
        decider=DecisionClient(FAKE_ENDPOINT, models.decision, lambda: "fake", client=client),
        client=client,
        fake=fake,
        info={"mode": "fake"},
    )


def azure_backend(meter: Meter, models: Models, env: Mapping[str, str] = os.environ) -> Backend:
    from azure.identity import AzureCliCredential, ManagedIdentityCredential

    # 컨테이너 Job은 사용자 할당 관리 ID, 운영자 PC 프로브는 az login 자격 증명을 쓴다.
    credential: Any = (
        ManagedIdentityCredential(client_id=env["AZURE_CLIENT_ID"]) if env.get("AZURE_CLIENT_ID") else AzureCliCredential()
    )
    client = _client(meter, None)
    return Backend(
        agents=FoundryAgents(env["AI_PROJECT_ENDPOINT"], CachedToken(credential, FOUNDRY_SCOPE), client=client),
        decider=DecisionClient(
            env["AI_ACCOUNT_ENDPOINT"], models.decision, CachedToken(credential, COGNITIVE_SCOPE), client=client
        ),
        client=client,
        info={"mode": "azure", "project_endpoint": env["AI_PROJECT_ENDPOINT"]},
    )
