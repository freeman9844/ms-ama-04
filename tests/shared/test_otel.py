import sys
import types

from shared.otel import configure_monitor


def test_without_connection_string_is_noop():
    assert configure_monitor("x.otel", env={}) is False


def test_configures_with_managed_identity(monkeypatch):
    seen = {}
    mon = types.ModuleType("azure.monitor.opentelemetry")
    mon.configure_azure_monitor = lambda **kw: seen.update(kw)
    ident = types.ModuleType("azure.identity")
    ident.ManagedIdentityCredential = lambda client_id=None: ("mi", client_id)
    monkeypatch.setitem(sys.modules, "azure.monitor.opentelemetry", mon)
    monkeypatch.setitem(sys.modules, "azure.identity", ident)
    env = {"APPLICATIONINSIGHTS_CONNECTION_STRING": "InstrumentationKey=k", "AZURE_CLIENT_ID": "cid"}
    assert configure_monitor("journey.otel", env=env) is True
    assert seen["connection_string"] == "InstrumentationKey=k" and seen["credential"] == ("mi", "cid")
    assert seen["logger_name"] == "journey.otel" and seen["enable_live_metrics"] is False


def test_failure_is_warning_not_error(monkeypatch, caplog):
    mon = types.ModuleType("azure.monitor.opentelemetry")

    def boom(**kw):
        raise RuntimeError("no")

    mon.configure_azure_monitor = boom
    monkeypatch.setitem(sys.modules, "azure.monitor.opentelemetry", mon)
    assert configure_monitor("x", env={"APPLICATIONINSIGHTS_CONNECTION_STRING": "k"}) is False
    assert "OTel" in caplog.text


def _stub_monitor(monkeypatch, seen):
    mon = types.ModuleType("azure.monitor.opentelemetry")
    mon.configure_azure_monitor = lambda **kw: seen.update(kw)
    ident = types.ModuleType("azure.identity")
    ident.ManagedIdentityCredential = lambda client_id=None: ("mi", client_id)
    monkeypatch.setitem(sys.modules, "azure.monitor.opentelemetry", mon)
    monkeypatch.setitem(sys.modules, "azure.identity", ident)


def test_fastapi_auto_instrumentation_can_be_disabled(monkeypatch):
    seen, default = {}, {}
    _stub_monitor(monkeypatch, seen)
    env = {"APPLICATIONINSIGHTS_CONNECTION_STRING": "k"}
    assert configure_monitor("policy.otel", env=env, fastapi_auto=False) is True
    assert seen["instrumentation_options"] == {"fastapi": {"enabled": False}}
    _stub_monitor(monkeypatch, default)
    configure_monitor("x", env=env)
    assert "instrumentation_options" not in default
