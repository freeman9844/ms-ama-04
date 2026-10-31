import json
import threading
from datetime import date

import httpx
import pytest

from agents.classify import Category
from agents.runtime import AgentError, AgentRun
from agents.txn_classify import (
    ABSTAIN,
    CRITERIA,
    FALLBACK_SCHEMA,
    LABELS,
    DecisionClient,
    DecisionError,
    FallbackOutput,
    LlmFallback,
    classify_unresolved,
    normalize,
)
from mockapis.demo import build_demo_cases
from shared.contracts import Transaction
from shared.pseudonymize import Pseudonymizer, find_pii

AS_OF = date(2026, 11, 2)
KEY = b"c" * 32


@pytest.fixture(scope="module")
def cafe():
    return build_demo_cases(AS_OF)["demo-cafe-001"]


def txn(i, description, amount=-300_000, code="TRANSFER_OUT"):
    return Transaction(
        txn_id=f"t-{i:03d}",
        booked_on=date(2026, 9, 1 + i % 28),
        amount_krw=amount,
        balance_after_krw=10_000_000,
        description=description,
        txn_type_code=code,
    )


class FakeDecider:
    def __init__(self, answers=None, default=("supplier", 0.95), errors=()):
        self.answers, self.default, self.errors = answers or {}, default, set(errors)
        self.states: list[dict] = []
        self._lock = threading.Lock()

    def choose(self, state):
        with self._lock:
            self.states.append(state)
        if state["description"] in self.errors:
            raise DecisionError("down")
        return self.answers.get(state["description"], self.default)


class FakeFallback:
    def __init__(self, labels=None, error=None):
        self.labels, self.error = labels or {}, error
        self.calls: list[list[dict]] = []

    def classify(self, items):
        self.calls.append(items)
        if self.error:
            raise self.error
        return {i["key"]: self.labels.get(i["description"], ABSTAIN) for i in items}


class MemoryCache:
    def __init__(self, entries=None, fail=False):
        self.entries, self.fail = dict(entries or {}), fail
        self.gets: list[str] = []

    def get(self, key):
        self.gets.append(key)
        if self.fail:
            raise RuntimeError("cosmos down")
        return self.entries.get(key)

    def put(self, key, entry):
        if self.fail:
            raise RuntimeError("cosmos down")
        self.entries[key] = entry


def run(txns, decider, **kw):
    kw.setdefault("industry", "카페")
    kw.setdefault("mask", lambda s: s)
    return classify_unresolved(txns, decider=decider, **kw)


def test_criteria_have_explicit_abstain_choice():
    assert ABSTAIN == "cannot_tell" and ABSTAIN in CRITERIA and ABSTAIN not in LABELS
    assert {"supplier", "loan_repayment", "high_cost_lender", "owner_transfer"} <= set(LABELS)


def test_normalize_collapses_width_space_and_numbers():
    assert normalize("  (주)빈스토리　원두  1102 ") == "(주)빈스토리 원두"
    assert normalize("ＡＢＣ캐피탈  상환") == "ABC캐피탈 상환"
    assert normalize("P2P 상환") == "P2P 상환"


def test_only_unique_unclassified_descriptions_reach_decision_model(cafe):
    decider = FakeDecider()
    result = run(cafe.transactions, decider)
    sent = sorted(s["description"] for s in decider.states)
    assert sent == ["(주)빈스토리 원두", "서울우유 유제품"]
    assert {s["business_industry"] for s in decider.states} == {"카페"}
    assert {(s["direction"], s["transaction_type"]) for s in decider.states} == {("outflow", "TRANSFER_OUT")}
    supplier_txns = [t for t in cafe.transactions if t.description in sent]
    m = result.metrics
    assert (m["unclassified_txns"], m["unique_descriptions"], m["decision_calls"]) == (len(supplier_txns), 2, 2)
    assert result.overrides == {}  # 공급처는 이미 영업비용이라 특성에 반영할 것이 없다
    assert {i["label"] for i in result.items} == {"supplier"}
    assert sum(i["count"] for i in result.items) == len(supplier_txns)


def test_debt_labels_override_outflows_only():
    txns = [txn(1, "한빛캐피탈"), txn(2, "한빛캐피탈"), txn(3, "한빛캐피탈", amount=500_000, code="TRANSFER_IN")]
    result = run(txns, FakeDecider(default=("high_cost_lender", 0.93)))
    assert result.overrides == {"t-001": Category.HIGH_COST_LENDER, "t-002": Category.HIGH_COST_LENDER}
    assert result.metrics["override_txns"] == 2


def test_owner_transfer_by_model_is_signal_only():
    result = run([txn(1, "PER 송금"), txn(2, "PER 송금")], FakeDecider(default=("owner_transfer", 0.9)))
    assert result.overrides == {}
    assert result.metrics["owner_transfer_txns"] == 2


def test_abstain_low_confidence_and_errors_go_to_one_fallback_call():
    decider = FakeDecider(
        answers={"망원청과": (ABSTAIN, 0.63), "부산수산(주)": ("supplier", 0.66)}, errors={"XK-2231"}
    )
    fallback = FakeFallback(labels={"망원청과": "supplier", "부산수산(주)": "supplier"})
    txns = [txn(1, "망원청과"), txn(2, "부산수산(주)"), txn(3, "XK-2231"), txn(4, "해동주류")]
    result = run(txns, decider, fallback=fallback, threshold=0.7)
    assert len(fallback.calls) == 1
    assert sorted(i["description"] for i in fallback.calls[0]) == ["XK-2231", "망원청과", "부산수산(주)"]
    by_desc = {i["description"]: i for i in result.items}
    assert by_desc["해동주류"]["source"] == "decision" and by_desc["해동주류"]["confidence"] == 0.95
    assert by_desc["망원청과"]["source"] == "fallback" and by_desc["망원청과"]["label"] == "supplier"
    assert by_desc["XK-2231"]["source"] == "none" and by_desc["XK-2231"]["label"] is None
    m = result.metrics
    assert (m["abstained"], m["low_confidence"], m["decision_errors"]) == (1, 1, 1)
    assert (m["fallback_items"], m["fallback_resolved"], m["unresolved"]) == (3, 2, 1)


def test_without_fallback_uncertain_items_stay_unresolved():
    result = run([txn(1, "망원청과")], FakeDecider(default=(ABSTAIN, 0.7)))
    assert result.items[0]["label"] is None and result.metrics["unresolved"] == 1


def test_fallback_failure_never_raises():
    result = run([txn(1, "망원청과")], FakeDecider(default=(ABSTAIN, 0.7)), fallback=FakeFallback(error=RuntimeError()))
    assert result.items[0]["source"] == "none" and result.metrics["fallback_errors"] == 1


def test_cache_hit_skips_model_and_new_results_are_cached():
    cache = MemoryCache()
    first = run([txn(1, "해동주류", code="AUTO_DEBIT")], FakeDecider(), cache=cache)
    key = first.items[0]["key"]
    assert cache.entries[key]["label"] == "supplier" and cache.entries[key]["description"] == "해동주류"

    decider = FakeDecider()
    second = run([txn(2, "해동주류  ", code="AUTO_DEBIT")], decider, cache=cache)
    assert decider.states == [] and second.metrics["cache_hits"] == 1
    assert second.items[0]["source"] == "cache" and second.items[0]["key"] == key


def test_cache_key_depends_on_industry():
    a = run([txn(1, "해동주류")], FakeDecider(), industry="한식").items[0]["key"]
    b = run([txn(1, "해동주류")], FakeDecider(), industry="카페").items[0]["key"]
    assert a != b


def test_pseudonymized_descriptions_are_masked_and_never_cached():
    p = Pseudonymizer(KEY, {"김민수": "PERSON"})
    cache = MemoryCache()
    decider = FakeDecider(default=("owner_transfer", 0.9))
    result = run([txn(1, "김민수 송금")], decider, cache=cache, mask=p.mask)
    (state,) = decider.states
    assert "김민수" not in json.dumps(state, ensure_ascii=False)
    assert find_pii(result.items[0]["description"], {"김민수": "PERSON"}) == []
    assert cache.gets == [] and cache.entries == {}


def test_transfer_narratives_are_never_cached():
    # 이체 적요에는 사전에 없는 제3자 이름이 들어갈 수 있다(예: "이영희 생활비"). 공유 캐시에 남기지 않는다.
    cache = MemoryCache()
    decider = FakeDecider()
    txns = [txn(1, "이영희 생활비"), txn(2, "박철수 후원", amount=200_000, code="TRANSFER_IN")]
    result = run(txns, decider, cache=cache)
    assert len(decider.states) == 2 and {i["source"] for i in result.items} == {"decision"}
    assert cache.gets == [] and cache.entries == {}


def test_cache_failure_degrades_to_model_calls():
    result = run([txn(1, "해동주류", code="AUTO_DEBIT")], FakeDecider(), cache=MemoryCache(fail=True))
    assert result.items[0]["source"] == "decision" and result.metrics["cache_errors"] == 2


def test_rule_classified_transactions_are_untouched(cafe):
    decider = FakeDecider()
    run([t for t in cafe.transactions if t.txn_type_code != "TRANSFER_OUT"], decider)
    assert decider.states == []


# --- Decision-1 HTTP 클라이언트 ---

def decision_client(handler):
    return DecisionClient(
        "https://ais.example/", "decision-1", lambda: "tok",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_decision_client_request_and_parse():
    seen = {}

    def handler(request):
        seen["url"], seen["auth"] = str(request.url), request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        answer = {"type": "choice", "choice": "supplier", "confidence": 0.94, "probabilities": {}}
        return httpx.Response(200, json={"answers": {"category": answer}})

    state = {"business_industry": "카페", "direction": "outflow", "transaction_type": "TRANSFER_OUT", "description": "x"}
    assert decision_client(handler).choose(state) == ("supplier", 0.94)
    assert seen["url"] == "https://ais.example/providers/microsoft/v1/systemone"
    assert seen["auth"] == "Bearer tok"
    body = seen["body"]
    assert body["model"] == "decision-1" and body["state"] == state
    question = body["questions"]["category"]
    assert question["type"] == "choice" and question["criteria"] == CRITERIA


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(429, json={"error": "busy"}),
        httpx.Response(200, json={"answers": {}}),
        httpx.Response(200, json={"answers": {"category": {"choice": "casino", "confidence": 0.9}}}),
        httpx.Response(200, json={"answers": {"category": {"choice": "supplier", "confidence": "high"}}}),
    ],
)
def test_decision_client_errors(response):
    with pytest.raises(DecisionError):
        decision_client(lambda _r: response).choose({"description": "x"})


def test_decision_client_network_error():
    def down(_request):
        raise httpx.ConnectError("down")

    with pytest.raises(DecisionError):
        decision_client(down).choose({"description": "x"})


# --- mini LLM 폴백 ---

class ScriptedRunner:
    def __init__(self, output):
        self.output, self.calls = output, []

    def run(self, spec, input_text, handlers):
        self.calls.append((spec, input_text, handlers))
        return AgentRun(spec.name, "1", "conv_f", FallbackOutput.model_validate(self.output), [], 10, 0)


def test_llm_fallback_returns_known_keys_only():
    runner = ScriptedRunner({"items": [{"key": "k1", "label": "supplier"}, {"key": "zz", "label": "rent"}]})
    labels = LlmFallback(runner, "gpt-5.4-mini").classify(
        [{"key": "k1", "description": "망원청과", "direction": "outflow", "transaction_type": "TRANSFER_OUT",
          "business_industry": "분식"}]
    )
    assert labels == {"k1": "supplier"}
    spec, text, handlers = runner.calls[0]
    assert spec.model == "gpt-5.4-mini" and spec.tools == () and handlers == {}
    assert "망원청과" in text and spec.output_schema == FALLBACK_SCHEMA
    assert FALLBACK_SCHEMA["properties"]["items"]["items"]["properties"]["label"]["enum"] == [*LABELS, ABSTAIN]


class FailingFallbackRunner:
    def run(self, spec, input_text, handlers):
        raise AgentError("model down", "conv_e")


def test_llm_fallback_keeps_conversation_ids_for_deletion():
    ok = LlmFallback(ScriptedRunner({"items": []}), "gpt-5.4-mini")
    ok.classify([{"key": "k1", "description": "x"}])
    assert ok.conversations == ["conv_f"]
    failing = LlmFallback(FailingFallbackRunner(), "gpt-5.4-mini")
    with pytest.raises(AgentError):
        failing.classify([{"key": "k1", "description": "x"}])
    assert failing.conversations == ["conv_e"]
