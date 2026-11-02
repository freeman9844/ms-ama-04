from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from journey import metering
from journey.metering import METER_KEY, MeteredRunner, failed_record, outcome_of, run_metered, safe_detail, step_records

T0 = datetime(2026, 11, 2, 1, 0, tzinfo=UTC)


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        self.now += timedelta(seconds=2)
        return self.now


@dataclass(frozen=True)
class FakeDeps:
    clock: Clock
    agents: object = None


class Runner:
    def __init__(self, fail=False):
        self.fail = fail

    def run(self, spec, text, handlers):
        if self.fail:
            raise RuntimeError("agent down")
        return SimpleNamespace(rounds=1, input_tokens=100, output_tokens=20)

    def delete_conversation(self, cid):
        return "deleted"


SPEC = SimpleNamespace(model="gpt-5.4-mini")


def test_metered_runner_records_usage_and_delegates():
    r = MeteredRunner(Runner())
    r.run(SPEC, "x", {})
    with pytest.raises(RuntimeError):
        MeteredRunner(Runner(fail=True)).run(SPEC, "x", {})
    assert r.entries() == [{"model": "gpt-5.4-mini", "input_tokens": 100, "output_tokens": 20, "calls": 2}]
    assert r.delete_conversation("c") == "deleted"


def test_failed_agent_call_counts_call_only():
    r = MeteredRunner(Runner(fail=True))
    with pytest.raises(RuntimeError):
        r.run(SPEC, "x", {})
    assert r.entries() == [{"model": "gpt-5.4-mini", "input_tokens": 0, "output_tokens": 0, "calls": 1}]


@pytest.mark.parametrize(
    ("step", "result", "outcome"),
    [
        ("assemble", {"status": "complete", "flags": []}, "ok"),
        ("assemble", {"status": "complete", "flags": ["agent_fallback"]}, "degraded"),
        ("assemble", {"status": "partial", "flags": []}, "degraded"),
        ("assess", {"assessment": {"recommendation": "approve"}}, "ok"),
        ("assess", {"assessment": None, "record": {"error": "agents_disabled"}}, "failed"),
        ("policy", {"decision": "eligible"}, "ok"),
    ],
)
def test_outcome_of(step, result, outcome):
    assert outcome_of(step, result)[0] == outcome


def test_run_metered_strips_tags_wraps_agents_and_meters():
    seen = {}

    def activity(deps, payload):
        seen["payload"] = payload
        deps.agents.run(SPEC, "x", {})
        return {"status": "complete", "flags": []}

    deps = FakeDeps(Clock(), Runner())
    out = run_metered("assemble_data", activity, deps, {"application_id": "a-1", "_tags": {"source": "load"}})
    assert seen["payload"] == {"application_id": "a-1"}
    meter = out[METER_KEY]
    assert meter["outcome"] == "ok" and meter["usage"][0]["calls"] == 2
    assert meter["started_at"] < meter["ended_at"]
    assert out["status"] == "complete"


def test_run_metered_does_not_mutate_result():
    original = {"status": "complete", "flags": []}
    out = run_metered("assemble_data", lambda d, p: original, FakeDeps(Clock(), None), {"application_id": "a"})
    assert METER_KEY not in original and out is not original


def test_run_metered_records_classify_substep():
    uw = {"features": {}, "classification": {"metrics": {"decision_calls": 3}}}
    out = run_metered("underwrite", lambda d, p: uw, FakeDeps(Clock(), Runner()), {"application_id": "a"})
    (sub,) = out[METER_KEY]["substeps"]
    assert sub["step"] == "classify" and sub["outcome"] == "ok"
    assert {"model": "decision-1", "input_tokens": 0, "output_tokens": 0, "calls": 3} in sub["usage"]
    assert out[METER_KEY]["usage"] == []


def test_classify_error_is_failed_and_missing_is_absent():
    err = run_metered("underwrite", lambda d, p: {"classification": {"error": "X: y"}}, FakeDeps(Clock()), {})
    none = run_metered("underwrite", lambda d, p: {"classification": None}, FakeDeps(Clock()), {})
    assert err[METER_KEY]["substeps"][0]["outcome"] == "failed"
    assert none[METER_KEY]["substeps"] == []


def test_run_metered_logs_and_reraises(monkeypatch):
    logged = []
    monkeypatch.setattr(metering.telemetry, "record_step", lambda rec, **kw: logged.append(rec))
    with pytest.raises(ValueError):
        run_metered("underwrite", lambda d, p: (_ for _ in ()).throw(ValueError("bad")), FakeDeps(Clock()), {})
    assert logged[0]["outcome"] == "failed" and logged[0]["detail"].startswith("ValueError")


class RecSpan:
    def __init__(self):
        self.attrs = {}

    def set_attribute(self, k, v):
        self.attrs[k] = v


def _capture_spans(monkeypatch):
    spans = []

    @contextmanager
    def span(name, attributes):
        sp = RecSpan()
        spans.append((name, sp))
        yield sp

    monkeypatch.setattr(metering.telemetry, "span", span)
    return spans


@pytest.mark.parametrize(("result", "outcome"), [
    ({"status": "complete", "flags": []}, "ok"),
    ({"status": "partial", "flags": []}, "degraded"),
])
def test_activity_span_carries_outcome(monkeypatch, result, outcome):
    spans = _capture_spans(monkeypatch)
    run_metered("assemble_data", lambda d, p: result, FakeDeps(Clock()), {"application_id": "a"})
    (name, sp) = spans[0]
    assert name == "journey.assemble_data" and sp.attrs["outcome"] == outcome


def test_activity_span_outcome_failed_on_exception(monkeypatch):
    spans = _capture_spans(monkeypatch)
    with pytest.raises(ValueError):
        run_metered("evaluate_policy", lambda d, p: (_ for _ in ()).throw(ValueError("x")), FakeDeps(Clock()), {})
    assert spans[0][1].attrs["outcome"] == "failed"

def test_step_records_shape_and_fallback():
    t1, t2 = T0, T0 + timedelta(seconds=3)
    meter = {"started_at": t1.isoformat(), "ended_at": t2.isoformat(), "outcome": "ok", "usage": [], "substeps": []}
    (rec,) = step_records("policy", meter, {"decision": "eligible"}, T0, T0)
    assert rec == {"step": "policy", "outcome": "ok", "started_at": t1.isoformat(), "ended_at": t2.isoformat(),
                   "duration_ms": 3000, "agent": False, "usage": []}
    (bad,) = step_records("assemble", "garbage", {"status": "partial"}, t1, t2)
    assert (bad["outcome"], bad["usage"], bad["duration_ms"], bad["agent"]) == ("degraded", [], 3000, True)


def test_failed_record_keeps_only_exception_type():
    rec = failed_record("underwrite", T0, T0 + timedelta(seconds=1), RuntimeError("계좌 110-123 잔액 5,000,000"))
    assert rec["outcome"] == "failed" and rec["detail"] == "RuntimeError"


@pytest.mark.parametrize(("raw", "safe"), [
    ("ValueError: 적요 '홍길동 이체'", "ValueError"),
    ("agents_disabled", "agents_disabled"),
    ("status=partial", "status=partial"),
    ("  KeyError: 'x'", "KeyError"),
    ("동의가 만료되었습니다", "error"),
    ("!! boom", "error"),
])
def test_safe_detail_keeps_leading_code_only(raw, safe):
    assert safe_detail(raw) == safe


def test_safe_detail_truncates_long_code():
    assert len(safe_detail("A" * 500)) == metering.DETAIL_MAX


def test_classify_error_detail_has_no_message():
    out = run_metered("underwrite", lambda d, p: {"classification": {"error": "ValueError: 적요 '홍길동'"}},
                      FakeDeps(Clock()), {})
    assert out[METER_KEY]["substeps"][0]["detail"] == "ValueError"
