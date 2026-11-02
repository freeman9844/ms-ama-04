import httpx
import pytest

from journey.adapters import HttpPolicyApi, PolicyUnavailable
from journey.circuit import CircuitBreaker

DECISION = {"application_id": "a", "decision": "eligible", "max_amount_krw": 1, "rate_band": "A",
            "reason_codes": [], "rules_version": "v1"}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def breaker(clock, changes=None):
    return CircuitBreaker(threshold=3, cooldown_s=60, clock=clock,
                          on_change=changes.append if changes is not None else None)


def test_opens_after_threshold_and_blocks():
    clock, changes = Clock(), []
    b = breaker(clock, changes)
    for _ in range(3):
        assert b.allow()
        b.failure()
    assert b.state == "open" and not b.allow()
    assert changes == ["open"]


def test_success_resets_failure_count():
    b = breaker(Clock())
    b.failure(); b.failure(); b.success(); b.failure(); b.failure()
    assert b.state == "closed"


def test_half_open_allows_single_trial():
    clock, changes = Clock(), []
    b = breaker(clock, changes)
    for _ in range(3):
        b.failure()
    clock.t = 61
    assert b.allow() and b.state == "half_open"
    assert not b.allow()  # 시험 진행 중엔 차단
    b.failure()
    assert b.state == "open" and not b.allow()
    clock.t = 122
    assert b.allow()
    b.success()
    assert b.state == "closed" and changes == ["open", "half_open", "open", "half_open", "closed"]


def api(handler, b):
    client = httpx.Client(base_url="http://policy", transport=httpx.MockTransport(handler))
    return HttpPolicyApi(client=client, breaker=b)


def test_server_errors_trip_breaker_and_short_circuit():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503, json={"code": "unavailable"})

    b = breaker(Clock())
    p = api(handler, b)
    for _ in range(3):
        with pytest.raises(PolicyUnavailable):
            p.decide({})
    with pytest.raises(PolicyUnavailable, match="circuit_open"):
        p.decide({})
    assert len(calls) == 3


def test_transport_error_counts_as_failure():
    def handler(request):
        raise httpx.ConnectError("down")

    b = breaker(Clock())
    for _ in range(3):
        with pytest.raises(PolicyUnavailable):
            api(handler, b).decide({})
    assert b.state == "open"


def test_client_error_does_not_trip_breaker():
    b = breaker(Clock())
    p = api(lambda r: httpx.Response(422, json={"detail": "bad"}), b)
    for _ in range(5):
        with pytest.raises(PolicyUnavailable):
            p.decide({})
    assert b.state == "closed"


def test_success_passes_through():
    b = breaker(Clock())
    assert api(lambda r: httpx.Response(200, json=DECISION), b).decide({})["decision"] == "eligible"


def test_unexpected_error_in_half_open_trial_reopens_circuit():
    # httpx 밖의 예외가 시험 호출에서 나도 결과를 기록해야 한다. 아니면 half_open에 갇혀 영영 막힌다.
    clock = Clock()
    b = breaker(clock)
    for _ in range(3):
        b.failure()
    clock.t = 61

    def handler(request):
        raise RuntimeError("boom")

    with pytest.raises(PolicyUnavailable, match="RuntimeError"):
        api(handler, b).decide({})
    assert b.state == "open"
    clock.t = 122
    assert b.allow()
