import json

import httpx
import pytest

from evals.metering import BudgetExceeded, Meter, MeteredTransport

PRICES = {"_source": "test", "m": {"input_per_1m": 1.0, "output_per_1m": 10.0}, "d": {"input_per_1m": None, "output_per_1m": None}}
USAGE = {"usage": {"input_tokens": 1000, "output_tokens": 100}}


def _client(meter: Meter, statuses: list[int], body: dict | None = None) -> tuple[httpx.Client, list[float]]:
    replies = iter(statuses)
    waits: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(next(replies), json=body or USAGE)

    return httpx.Client(transport=MeteredTransport(meter, httpx.MockTransport(handler), sleep=waits.append)), waits


def test_retries_throttling_and_records_one_call_with_cost():
    meter = Meter(PRICES, 10)
    client, waits = _client(meter, [429, 503, 200])
    with meter.scope(suite="s", config="c", case="k", model="m"):
        assert client.post("https://x/openai/v1/responses", json={"input": "hi"}).status_code == 200
    [r] = meter.records
    assert (r.kind, r.retries, r.status, r.suite, r.case) == ("agent", 2, 200, "s", "k")
    assert waits == [2.0, 4.0]
    assert r.cost_usd == pytest.approx(0.002)


def test_gives_up_after_three_retries():
    meter = Meter(PRICES, 10)
    client, _ = _client(meter, [429] * 4)
    with meter.scope(model="m"):
        assert client.post("https://x/openai/v1/responses", json={}).status_code == 429
    assert meter.records[0].retries == 3


def test_unpriced_model_has_no_cost_and_does_not_spend_budget():
    meter = Meter(PRICES, 10)
    client, _ = _client(meter, [200])
    client.post("https://x/providers/microsoft/v1/systemone", json={"model": "d", "input": []})
    assert (meter.records[0].kind, meter.records[0].model, meter.records[0].cost_usd) == ("decision", "d", None)
    assert meter.spent_usd == 0


def test_budget_check_raises_once_spent():
    meter = Meter(PRICES, 0.001)
    client, _ = _client(meter, [200])
    with meter.scope(model="m"):
        client.post("https://x/openai/v1/responses", json={})
    assert meter.over_budget()
    with pytest.raises(BudgetExceeded):
        meter.check()


def test_pii_scan_reads_embedded_json_but_skips_identifier_keys():
    meter = Meter(PRICES, 10)
    client, _ = _client(meter, [200])
    embedded = json.dumps({"key": "20261101123456", "txn_ids": ["99887766554433"], "description": "연락처 010-1234-5678"})
    with meter.scope(model="m", entities={"홍길동": "PERSON"}):
        client.post("https://x/openai/v1/responses", json={"input": f"분류할 항목: {embedded}"})
    assert meter.records[0].pii == ["PHONE"]


def test_pii_scan_finds_known_entities_in_output():
    meter = Meter(PRICES, 10)
    client, _ = _client(meter, [200], body={**USAGE, "output": [{"type": "message", "content": [{"text": "대표 홍길동"}]}]})
    with meter.scope(model="m", entities={"홍길동": "PERSON"}):
        client.post("https://x/openai/v1/responses", json={"input": "x"})
    assert meter.records[0].pii == ["PERSON"]


def test_pii_scan_ignores_service_ids_under_any_key_but_keeps_real_accounts():
    # 실제 실행에서 Foundry 응답 ID(resp_…16진수)에 섞인 10~14자리 숫자열이 계좌번호로 오탐됐다(75/322 호출).
    meter = Meter(PRICES, 10)
    body = {**USAGE, "object": "response", "response_id": "resp_04503b704cc8dbd4006ae80590811c8190880589361b3c5705",
            "output": [{"type": "message", "item_ref": "msg_0a081ca1c077469d006ae805c86b588190897298c32ccb8d33",
                        "content": [{"text": "입금 계좌 110-123-456789"}]}]}
    client, _ = _client(meter, [200], body=body)
    with meter.scope(model="m"):
        client.post("https://x/openai/v1/responses", json={"input": "x"})
    client2, _ = _client(meter, [200], body={**USAGE, "response_id": body["response_id"]})
    with meter.scope(model="m"):
        client2.post("https://x/openai/v1/responses", json={"input": "x"})
    assert [r.pii for r in meter.records] == [["ACCOUNT"], []]
