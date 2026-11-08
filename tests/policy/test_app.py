import importlib
import sys

import pytest
import yaml
from fastapi.testclient import TestClient

from policy.app import create_app
from policy.rules import DEFAULT_RULES_PATH
from shared.contracts import PolicyDecision


@pytest.fixture
def client(rules):
    return TestClient(create_app(rules))


def test_healthz_reports_rules_version(client):
    assert client.get("/healthz").json() == {"status": "ok", "rules_version": "2026.11-v3"}


def test_decision_roundtrip(client, make_features):
    r = client.post("/v1/decisions", json=make_features().model_dump(mode="json"))
    assert r.status_code == 200
    d = PolicyDecision.model_validate(r.json())
    assert d.decision == "eligible"
    assert d.rules_version == "2026.11-v3"


def test_ineligible_response_shape(client, make_features):
    r = client.post("/v1/decisions", json=make_features(months_of_history=3).model_dump(mode="json"))
    body = r.json()
    assert body["decision"] == "ineligible"
    assert body["max_amount_krw"] == 0
    assert body["rate_band"] is None
    assert [c["code"] for c in body["reason_codes"]] == ["R001"]


@pytest.mark.parametrize(
    "patch",
    [
        {"dscr": -1},
        {"requested_amount_krw": 0},
        {"unexpected": "x"},
        {"schema_version": "2"},
        {"dscr": None},
        {"dscr": True},
        {"months_of_history": True},
        {"dscr": "1.8"},
        {"months_of_history": "12"},
    ],
)
def test_invalid_input_is_422(client, make_features, patch):
    body = {**make_features().model_dump(mode="json"), **patch}
    r = client.post("/v1/decisions", json=body)
    assert r.status_code == 422


def test_main_uses_env_rules_path(monkeypatch, tmp_path):
    data = yaml.safe_load(DEFAULT_RULES_PATH.read_text(encoding="utf-8"))
    data["version"] = "test-v9"
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    monkeypatch.setenv("POLICY_RULES_PATH", str(path))
    sys.modules.pop("policy.main", None)
    module = importlib.import_module("policy.main")
    assert TestClient(module.app).get("/healthz").json()["rules_version"] == "test-v9"


def test_unavailable_fault_returns_503_for_decisions_only(rules, make_features):
    c = TestClient(create_app(rules, fault="unavailable"))
    assert c.get("/healthz").status_code == 200
    r = c.post("/v1/decisions", json=make_features().model_dump(mode="json"))
    assert r.status_code == 503 and r.json() == {"code": "unavailable"}


def test_unknown_fault_mode_is_rejected(rules):
    with pytest.raises(ValueError):
        create_app(rules, fault="weird")


def test_main_reads_fault_mode(monkeypatch, make_features):
    monkeypatch.setenv("FAULT_MODE", "unavailable")
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    sys.modules.pop("policy.main", None)
    module = importlib.import_module("policy.main")
    r = TestClient(module.app).post("/v1/decisions", json=make_features().model_dump(mode="json"))
    assert r.status_code == 503
    monkeypatch.delenv("FAULT_MODE")
    sys.modules.pop("policy.main", None)


def test_instrumented_app_emits_no_asgi_send_receive_spans(rules, make_features):
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from shared.otel import instrument_fastapi

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    app = create_app(rules)
    assert instrument_fastapi(app, tracer_provider=provider) is True
    try:
        r = TestClient(app).post("/v1/decisions", json=make_features().model_dump(mode="json"))
    finally:
        FastAPIInstrumentor.uninstrument_app(app)
    assert r.status_code == 200
    names = [s.name for s in exporter.get_finished_spans()]
    assert "POST /v1/decisions" in names
    assert not [n for n in names if n.endswith(" http send") or n.endswith(" http receive")]


@pytest.mark.parametrize("configured", [True, False])
def test_main_instruments_fastapi_manually_only_when_monitor_is_on(monkeypatch, configured):
    import shared.otel

    seen = {}
    monkeypatch.setattr(shared.otel, "configure_monitor", lambda name, **kw: seen.update(name=name, **kw) or configured)
    monkeypatch.setattr(shared.otel, "instrument_fastapi", lambda app, **kw: seen.setdefault("app", app))
    monkeypatch.delenv("FAULT_MODE", raising=False)
    sys.modules.pop("policy.main", None)
    try:
        module = importlib.import_module("policy.main")
    finally:
        sys.modules.pop("policy.main", None)
    assert seen["name"] == "policy.otel" and seen["fastapi_auto"] is False
    assert (seen.get("app") is module.app) is configured
