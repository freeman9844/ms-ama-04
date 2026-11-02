import logging

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


def test_span_without_sdk_is_context_manager():
    with telemetry.span("journey.x", {"application_id": "a", "skip": None}):
        pass


def test_telemetry_failures_are_swallowed():
    class BadSpan:
        def set_attribute(self, k, v):
            raise RuntimeError("x")

    telemetry.set_attrs(BadSpan(), {"a": 1})
    telemetry.record_step({"usage": [{"input_tokens": "x"}]}, application_id="a", tags={})
    with telemetry.span("journey.x", None):
        pass
