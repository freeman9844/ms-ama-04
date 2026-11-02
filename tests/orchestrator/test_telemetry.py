import logging
import sys

import pytest
from opentelemetry import trace

from journey import telemetry


def test_record_step_logs_without_pii(caplog):
    caplog.set_level(logging.INFO, logger="journey.otel")
    rec = {"step": "assemble", "outcome": "ok", "duration_ms": 10, "agent": True,
           "usage": [{"model": "m", "input_tokens": 3, "output_tokens": 2, "calls": 1},
                     {"model": "n", "input_tokens": 1, "output_tokens": 1, "calls": 1}]}
    telemetry.record_step(rec, application_id="a-1", tags={"source": "load", "run_id": "r"})
    (r,) = [x for x in caplog.records if x.getMessage() == "journey.step"]
    assert (r.application_id, r.step, r.outcome, r.agent, r.input_tokens, r.output_tokens) == (
        "a-1", "assemble", "ok", True, 4, 3)
    assert (r.source, r.run_id) == ("load", "r")


def test_circuit_changed_logs_warning(caplog):
    caplog.set_level(logging.INFO, logger="journey.otel")
    telemetry.circuit_changed("open")
    (r,) = [x for x in caplog.records if x.getMessage() == "policy.circuit"]
    assert r.levelno == logging.WARNING and r.state == "open"


def test_span_without_sdk_is_context_manager(monkeypatch):
    monkeypatch.setitem(sys.modules, "opentelemetry", None)
    monkeypatch.setitem(sys.modules, "opentelemetry.trace", None)
    with telemetry.span("journey.x", {"application_id": "a", "skip": None}) as sp:
        assert sp is None


def test_telemetry_failures_are_swallowed():
    class BadSpan:
        def set_attribute(self, k, v):
            raise RuntimeError("x")

    telemetry.set_attrs(BadSpan(), {"a": 1})
    telemetry.record_step({"usage": [{"input_tokens": "x"}]}, application_id="a", tags={})
    with telemetry.span("journey.x", None):
        pass


class FakeCM:
    def __init__(self, fail_enter=False, fail_exit=False):
        self.fail_enter, self.fail_exit, self.exits = fail_enter, fail_exit, []

    def __enter__(self):
        if self.fail_enter:
            raise RuntimeError("start")
        return "span"

    def __exit__(self, *exc):
        self.exits.append(exc[0])
        if self.fail_exit:
            raise RuntimeError("end")
        if exc[1] is not None:
            raise exc[1]  # OTel은 본문 예외를 스스로 다시 던진다


def use_tracer(monkeypatch, cm):
    tracer = type("T", (), {"start_as_current_span": lambda self, name, attributes: cm})()
    monkeypatch.setattr(trace, "get_tracer", lambda name: tracer)


def test_span_start_failure_still_runs_body(monkeypatch):
    use_tracer(monkeypatch, FakeCM(fail_enter=True))
    ran = []
    with telemetry.span("journey.x", {}) as sp:
        ran.append(sp)
    assert ran == [None]


def test_span_body_exception_propagates_unchanged(monkeypatch):
    cm = FakeCM()
    use_tracer(monkeypatch, cm)
    err = ValueError("body")
    with pytest.raises(ValueError) as info:
        with telemetry.span("journey.x", {}):
            raise err
    assert info.value is err and cm.exits == [ValueError]


def test_span_end_failure_is_swallowed(monkeypatch):
    cm = FakeCM(fail_exit=True)
    use_tracer(monkeypatch, cm)
    with telemetry.span("journey.x", {}) as sp:
        assert sp == "span"
    assert cm.exits == [None]


def test_circuit_changed_swallows_errors(monkeypatch, caplog):
    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(trace, "get_current_span", boom)
    caplog.set_level(logging.INFO, logger="journey.otel")
    telemetry.circuit_changed("half_open")
    assert [r.state for r in caplog.records if r.getMessage() == "policy.circuit"] == ["half_open"]
