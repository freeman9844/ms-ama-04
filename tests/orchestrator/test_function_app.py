import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import azure.durable_functions as df
import pytest

from journey.activities import ACTIVITIES
from journey.consent import CONSENT_ACTIVITIES, LIFECYCLE_ORCHESTRATOR
from journey.entry import handle_decision, handle_start, should_start
from journey.orchestration import EVENT_OFFICER_DECISION, ORCHESTRATOR

ROOT = Path(__file__).resolve().parents[2] / "src" / "orchestrator"
START = {
    "application_id": "app-1",
    "applicant_id": "seller-dailylook-01",
    "requested_amount_krw": 10_000_000,
    "requested_tenor_months": 12,
}


class FakeClient:
    def __init__(self, status=None):
        self.status = status
        self.started, self.raised = [], []

    async def get_status(self, instance_id):
        rs = df.OrchestrationRuntimeStatus(self.status) if self.status else None
        return SimpleNamespace(runtime_status=rs)

    async def start_new(self, name, instance_id=None, client_input=None):
        self.started.append((name, instance_id, client_input))
        return instance_id

    async def raise_event(self, instance_id, event_name, event_data=None):
        self.raised.append((instance_id, event_name, event_data))


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize(
    "status, expected",
    [(None, True), ("Failed", True), ("Terminated", True), ("Canceled", True),
     ("Running", False), ("Pending", False), ("Completed", False), ("Suspended", False)],
)
def test_should_start(status, expected):
    assert should_start(status) is expected


def test_start_new_instance_uses_application_id():
    client = FakeClient()
    assert run(handle_start(client, json.dumps(START))) == "app-1"
    name, instance_id, payload = client.started[0]
    assert (name, instance_id) == (ORCHESTRATOR, "app-1")
    assert payload == {**START, "sla_hours": 24.0}


def test_duplicate_start_is_ignored():
    client = FakeClient("Running")
    assert run(handle_start(client, json.dumps(START))) is None
    assert client.started == []


@pytest.mark.parametrize("body", ["not json", json.dumps({**START, "requested_amount_krw": -1}), json.dumps({**START, "x": 1})])
def test_invalid_start_is_dropped(body):
    client = FakeClient()
    assert run(handle_start(client, body)) is None
    assert client.started == []


def test_decision_raised_on_running_instance():
    client = FakeClient("Running")
    decision = {"decision": "approve", "officer_id": "o-1", "amount_krw": 10_000_000}
    body = json.dumps({"application_id": "app-1", "decision": decision})
    assert run(handle_decision(client, body)) is True
    assert client.raised == [("app-1", EVENT_OFFICER_DECISION, decision)]


@pytest.mark.parametrize("status", [None, "Completed", "Failed"])
def test_decision_for_inactive_instance_is_dropped(status):
    client = FakeClient(status)
    body = json.dumps({"application_id": "app-1", "decision": {"decision": "decline"}})
    assert run(handle_decision(client, body)) is False
    assert client.raised == []


def test_malformed_decision_is_dropped():
    client = FakeClient("Running")
    assert run(handle_decision(client, json.dumps({"application_id": "APP 1", "decision": {}}))) is False
    assert client.raised == []


QUEUES = (
    ("start_application", "applications-start"),
    ("officer_decision", "officer-decisions"),
    ("monitoring_replay", "monitoring-replay"),
    ("restructure_decision", "restructure-decisions"),
    ("consent_lifecycle_start", "consent-lifecycle"),
    ("consent_command", "consent-commands"),
    ("grievance_submitted", "grievances"),
    ("grievance_decision", "grievance-decisions"),
)
DURABLE_CLIENTS = ("start_application", "officer_decision", "consent_lifecycle_start", "consent_command")


def load_app(monkeypatch, fqdn):
    import importlib

    import function_app

    if fqdn:
        monkeypatch.setenv("SERVICEBUS_FQDN", fqdn)
    else:
        monkeypatch.delenv("SERVICEBUS_FQDN", raising=False)
    return importlib.reload(function_app).app


def test_function_app_registers_triggers(monkeypatch):
    app = load_app(monkeypatch, "")

    functions = {f.get_function_name(): f for f in app.get_functions()}
    assert set(functions) == {
        *(name for name, _ in QUEUES), ORCHESTRATOR, LIFECYCLE_ORCHESTRATOR, *ACTIVITIES, *CONSENT_ACTIVITIES
    }
    bindings = {name: {b.type: b for b in f.get_bindings()} for name, f in functions.items()}
    assert all("orchestrationTrigger" in bindings[name] for name in (ORCHESTRATOR, LIFECYCLE_ORCHESTRATOR))
    for name in (*ACTIVITIES, *CONSENT_ACTIVITIES):
        assert "activityTrigger" in bindings[name]
    for name, queue in QUEUES:
        trigger = json.loads(functions[name].get_function_json())["bindings"]
        queue_binding = next(b for b in trigger if b["type"] == "queueTrigger")
        assert queue_binding["queueName"] == queue
        assert queue_binding["connection"] == "AzureWebJobsStorage"
    assert {name for name, b in bindings.items() if "durableClient" in b} == set(DURABLE_CLIENTS)


def test_servicebus_triggers_only_when_enabled(monkeypatch):
    app = load_app(monkeypatch, "sb-x.servicebus.windows.net")
    functions = {f.get_function_name(): f for f in app.get_functions()}
    expected = {"flow_updated": ("flow.updated", "early-warning"), "alert_raised": ("alert.raised", "collections")}
    for name, (topic, subscription) in expected.items():
        binding = json.loads(functions[name].get_function_json())["bindings"][0]
        assert binding["type"] == "serviceBusTrigger"
        assert (binding["topicName"], binding["subscriptionName"]) == (topic, subscription)
        assert binding["connection"] == "ServiceBusConnection" and binding["isSessionsEnabled"] is True
    load_app(monkeypatch, "")


def test_host_json():
    host = json.loads((ROOT / "host.json").read_text(encoding="utf-8"))
    assert host["extensionBundle"]["version"] == "[4.*, 5.0.0)"
    assert host["extensions"]["durableTask"]["hubName"] == "lending"
    assert host["extensions"]["queues"]["messageEncoding"] == "none"


def test_host_json_polls_queues_fast():
    # 기본값(큐 1분, Durable 30초)이면 한가한 인스턴스가 심사역 결정을 늦게 집는다.
    ext = json.loads((ROOT / "host.json").read_text(encoding="utf-8"))["extensions"]
    assert ext["queues"]["maxPollingInterval"] == "00:00:02"
    assert ext["durableTask"]["storageProvider"]["maxQueuePollingInterval"] == "00:00:02"


def test_requirements_pin_runtime_sdks():
    lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").split()
    names = {line.split("==")[0] for line in lines}
    assert {
        "azure-functions",
        "azure-functions-durable",
        "azure-identity",
        "azure-cosmos",
        "azure-storage-blob",
        "azure-storage-queue",
        "azure-servicebus",
        "azure-keyvault-secrets",
        "httpx",
        "pydantic",
        "azure-monitor-opentelemetry",
        "opentelemetry-instrumentation-httpx",
    } <= names
    assert all("==" in line for line in lines)


def test_metered_activities_are_wrapped(monkeypatch):
    import function_app

    seen = []
    monkeypatch.setattr(function_app, "run_metered", lambda name, fn, d, p: seen.append((name, fn)) or {"ok": 1})
    monkeypatch.setattr(function_app, "deps", lambda: "deps")
    assert function_app._metered_activity("underwrite", {"application_id": "a"}) == {"ok": 1}
    assert seen == [("underwrite", ACTIVITIES["underwrite"])]
    src = (ROOT / "function_app.py").read_text(encoding="utf-8")
    for name in ("request_consent", "assemble_data", "underwrite", "evaluate_policy", "assess_credit"):
        assert f'_metered_activity("{name}", payload)' in src
