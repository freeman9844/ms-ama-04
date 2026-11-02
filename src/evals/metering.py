"""호출 단위 계량: httpx 전송 계층에서 재시도·지연·토큰·PII 검사를 기록하고 단가표로 비용을 계산한다.

에이전트·분류 코드는 고치지 않고 FoundryAgents(client=)·DecisionClient(client=)에 이 전송을 쓴 클라이언트를 넣는다.
요청·응답 본문은 저장하지 않는다(PII 검사 결과의 종류와 건수만 남긴다).
"""

import json
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

from evals.metrics import distribution
from shared.pseudonymize import find_pii

RETRY_STATUS = frozenset({429, 502, 503, 504})
MAX_RETRIES = 3
BACKOFF_S = 2.0
SYSTEMONE_SUFFIX = "/providers/microsoft/v1/systemone"
# 식별자·메타데이터 키는 PII 검사에서 뺀다(유닉스 시각·해시 키의 숫자열이 계좌번호 패턴에 걸린다).
_SKIP_KEYS = frozenset({"id", "call_id", "conversation", "container_id", "previous_response_id", "created_at",
                        "agent_reference", "type", "status", "model", "role", "name", "usage",
                        "key", "txn_id", "txn_ids", "alert_id", "loan_id", "application_id"})


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class CallRecord:
    seq: int
    suite: str
    config: str
    case: str
    kind: str  # agent | decision | judge | setup | aux
    model: str
    method: str
    path: str
    status: int | None
    latency_ms: int
    retries: int
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    pii: list[str] = field(default_factory=list)  # 남은 PII 종류(값은 남기지 않는다)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def kind_of(method: str, path: str) -> str:
    if path.endswith(SYSTEMONE_SUFFIX):
        return "decision"
    if path.endswith("/openai/v1/responses") and method == "POST":
        return "agent"
    if path.endswith("/chat/completions"):
        return "judge"
    if "/agents/" in path and path.endswith("/versions"):
        return "setup"
    return "aux"


def _strings(value: Any) -> Iterator[str]:
    """JSON 문자열 잎. 에이전트 입력·도구 인자·출력 안에 다시 들어 있는 JSON도 풀어서 키 규칙을 적용한다."""
    if isinstance(value, str):
        start = min((i for i in (value.find("{"), value.find("[")) if i >= 0), default=-1)
        inner = _json(value[start:].encode()) if start >= 0 else None
        if isinstance(inner, dict | list):
            if value[:start].strip():
                yield value[:start]
            yield from _strings(inner)
        else:
            yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            if k not in _SKIP_KEYS:
                yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def _json(raw: bytes) -> Any:
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def usage_tokens(body: Any) -> tuple[int, int]:
    usage = body.get("usage") if isinstance(body, dict) else None
    if not isinstance(usage, dict):
        return 0, 0
    inp = usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0
    out = usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0
    return int(inp), int(out)


class Meter:
    def __init__(self, prices: Mapping[str, Mapping[str, float]], budget_usd: float) -> None:
        self.prices = {k: v for k, v in prices.items() if not k.startswith("_")}
        self.budget_usd = budget_usd
        self.records: list[CallRecord] = []
        self._lock = threading.Lock()
        self._scope: dict[str, Any] = {"suite": "", "config": "", "case": "", "model": "", "entities": {}}

    @contextmanager
    def scope(self, **values: Any) -> Iterator[None]:
        saved = dict(self._scope)
        self._scope.update(values)
        try:
            yield
        finally:
            self._scope = saved

    def get(self, key: str) -> Any:
        return self._scope.get(key)

    def cost(self, model: str, input_tokens: int, output_tokens: int) -> float | None:
        price = self.prices.get(model)
        if not price or price.get("input_per_1m") is None or price.get("output_per_1m") is None:
            return None
        return round((input_tokens * price["input_per_1m"] + output_tokens * price["output_per_1m"]) / 1e6, 6)

    @property
    def spent_usd(self) -> float:
        return round(sum(r.cost_usd or 0.0 for r in self.records), 6)

    def over_budget(self) -> bool:
        return self.spent_usd >= self.budget_usd

    def check(self) -> None:
        if self.over_budget():
            raise BudgetExceeded(f"예산 {self.budget_usd} USD 도달(사용 {self.spent_usd})")

    def observe(
        self, request: httpx.Request, response: httpx.Response | None, latency_ms: int, retries: int, error: str | None = None
    ) -> CallRecord:
        kind = kind_of(request.method, request.url.path)
        req, resp = _json(request.content), _json(response.content) if response is not None else None
        model = self._scope["model"]
        if kind == "decision" and isinstance(req, dict):
            model = str(req.get("model") or model)
        inp, out = usage_tokens(resp)
        pii: list[str] = []
        if kind in ("agent", "decision", "judge"):
            text = "\n".join([*_strings(req), *_strings(resp)])
            pii = sorted({k for k, _ in find_pii(text, self._scope.get("entities") or {})})
        with self._lock:
            return self._append(
                seq=len(self.records) + 1,
                suite=self._scope["suite"],
                config=self._scope["config"],
                case=self._scope["case"],
                kind=kind,
                model=model,
                method=request.method,
                path=request.url.path,
                status=response.status_code if response is not None else None,
                latency_ms=latency_ms,
                retries=retries,
                input_tokens=inp,
                output_tokens=out,
                cost_usd=self.cost(model, inp, out) if kind in ("agent", "decision", "judge") else None,
                pii=pii,
                error=error,
            )

    def _append(self, **fields: Any) -> CallRecord:
        record = CallRecord(**fields)
        self.records.append(record)
        return record

    def model_calls(self, **where: str) -> list[CallRecord]:
        return [
            r for r in self.records
            if r.kind in ("agent", "decision", "judge") and all(getattr(r, k) == v for k, v in where.items())
        ]

    def by_model(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for model in sorted({r.model for r in self.model_calls()}):
            calls = [r for r in self.model_calls() if r.model == model]
            costs = [r.cost_usd for r in calls]
            lat = distribution([r.latency_ms for r in calls])
            out[model] = {
                "calls": len(calls),
                "errors": sum(1 for r in calls if r.error or (r.status or 0) >= 400),
                "retries": sum(r.retries for r in calls),
                "p50_ms": lat["p50"],
                "p95_ms": lat["p95"],
                "input_tokens": sum(r.input_tokens for r in calls),
                "output_tokens": sum(r.output_tokens for r in calls),
                "cost_usd": None if any(c is None for c in costs) else round(sum(costs), 6),  # type: ignore[arg-type]
            }
        return out


class MeteredTransport(httpx.BaseTransport):
    """429·502·503·504는 지수 백오프로 최대 3회 재시도하고, 최종 응답 하나를 Meter에 기록한다."""

    def __init__(
        self,
        meter: Meter,
        inner: httpx.BaseTransport | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._meter, self._inner = meter, inner or httpx.HTTPTransport()
        self._sleep, self._clock = sleep, clock

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        request.read()
        retries = 0
        while True:
            started = self._clock()  # 지연은 마지막 시도만 잰다(백오프 대기 제외)
            try:
                response = self._inner.handle_request(request)
            except httpx.HTTPError as e:
                self._meter.observe(request, None, int((self._clock() - started) * 1000), retries, type(e).__name__)
                raise
            if response.status_code in RETRY_STATUS and retries < MAX_RETRIES:
                response.close()
                self._sleep(BACKOFF_S * 2**retries)
                retries += 1
                continue
            response.read()
            self._meter.observe(request, response, int((self._clock() - started) * 1000), retries)
            return response

    def close(self) -> None:
        self._inner.close()
