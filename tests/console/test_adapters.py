import json

import pytest

from console.adapters import (
    ALERTS_QUERY,
    LIST_QUERY,
    CONSENTS_BY_APPLICANT,
    CONSENTS_BY_APPLICATION,
    GRIEVANCES_BY_APPLICANT,
    GRIEVANCES_QUERY,
    LOANS_QUERY,
    CosmosApplications,
    CosmosMonitoring,
    CosmosPrivacy,
    StorageQueueSender,
    build_deps,
)


class FakeContainer:
    def __init__(self, items=None, missing=False):
        self.items = items or []
        self.missing = missing
        self.calls = []

    def query_items(self, query, parameters, enable_cross_partition_query=None, partition_key=None):
        self.calls.append(("query", query, parameters, enable_cross_partition_query or partition_key))
        return iter(self.items)

    def read_item(self, item, partition_key):
        self.calls.append(("read", item, partition_key))
        if self.missing:
            raise NotFound()
        return {"id": item}


class NotFound(Exception):
    status_code = 404


class FakeDbAny:
    def get_container_client(self, name):
        return FakeContainer()


def test_list_recent_queries_projection_across_partitions():
    container = FakeContainer([{"id": "app-1"}])
    assert CosmosApplications(container).list_recent(50) == [{"id": "app-1"}]
    kind, query, params, cross = container.calls[0]
    assert (kind, query, cross) == ("query", LIST_QUERY, True)
    assert params == [{"name": "@limit", "value": 50}]
    assert "ORDER BY c.updated_at DESC" in query and "c.assessment.recommendation AS recommendation" in query
    assert "c.source" in query
    assert "c.assessment " not in query.replace("c.assessment.recommendation", "")


def test_get_reads_by_application_partition_and_maps_missing_to_none():
    container = FakeContainer()
    assert CosmosApplications(container).get("app-1") == {"id": "app-1"}
    assert container.calls == [("read", "app-1", "app-1")]
    assert CosmosApplications(FakeContainer(missing=True)).get("app-x") is None


def test_monitoring_store_reads_loans_and_partitioned_alerts():
    loans, alerts = FakeContainer([{"id": "app-1"}]), FakeContainer([{"id": "ew-rp-1-w02"}])
    store = CosmosMonitoring(loans, alerts)
    assert store.list_loans(50) == [{"id": "app-1"}]
    assert loans.calls[0] == ("query", LOANS_QUERY, [{"name": "@limit", "value": 50}], True)
    assert store.get_loan("app-1") == {"id": "app-1"}
    assert CosmosMonitoring(FakeContainer(missing=True), alerts).get_loan("app-x") is None
    assert store.list_alerts("app-1", "rp-1") == [{"id": "ew-rp-1-w02"}]
    # 대출 파티션 안에서만 찾고, 연락 기록 문서(contacts-*)는 제외한다.
    assert alerts.calls[0] == ("query", ALERTS_QUERY, [{"name": "@replay", "value": "rp-1"}], "app-1")
    assert "STARTSWITH(c.id, 'ew-')" in ALERTS_QUERY


def test_privacy_store_reads_consents_proofs_and_grievances():
    consents, proofs, grievances = FakeContainer([{"id": "c-1"}]), FakeContainer(), FakeContainer([{"id": "grv-1"}])
    store = CosmosPrivacy(consents, proofs, grievances)
    assert store.consents_for_applicant("seller-dailylook-01") == [{"id": "c-1"}]
    assert store.consents_for_application("app-1") == [{"id": "c-1"}]
    assert consents.calls == [
        ("query", CONSENTS_BY_APPLICANT, [{"name": "@applicant", "value": "seller-dailylook-01"}], True),
        ("query", CONSENTS_BY_APPLICATION, [{"name": "@application", "value": "app-1"}], True),
    ]
    assert store.get_consent("c-1") == {"id": "c-1"} and consents.calls[-1] == ("read", "c-1", "c-1")
    assert store.get_proof("c-1") == {"id": "c-1"} and proofs.calls[-1] == ("read", "c-1", "c-1")
    assert CosmosPrivacy(FakeContainer(missing=True), FakeContainer(missing=True), grievances).get_proof("c-x") is None
    assert store.list_grievances(50) == [{"id": "grv-1"}]
    assert store.grievances_for_applicant("seller-dailylook-01") == [{"id": "grv-1"}]
    assert grievances.calls == [
        ("query", GRIEVANCES_QUERY, [{"name": "@limit", "value": 50}], True),
        ("query", GRIEVANCES_BY_APPLICANT, [{"name": "@applicant", "value": "seller-dailylook-01"}], True),
    ]
    # 이의제기는 신청 파티션(/application_id)에 있다.
    assert store.get_grievance("app-1", "grv-1") == {"id": "grv-1"}
    assert grievances.calls[-1] == ("read", "grv-1", "app-1")
    assert "c.text" not in GRIEVANCES_QUERY


def test_queue_sender_sends_plain_utf8_json():
    sent = {}

    class FakeQueueClient:
        def __init__(self, name):
            self.name = name

        def send_message(self, content):
            sent[self.name] = content

    StorageQueueSender(FakeQueueClient).send("officer-decisions", {"reason": "상환 여력 <부족>"})
    assert json.loads(sent["officer-decisions"]) == {"reason": "상환 여력 <부족>"}
    assert "상환" in sent["officer-decisions"]


def test_build_deps_wires_env(monkeypatch):
    made = {}

    class Recorder:
        def __init__(self, *args, **kwargs):
            made[type(self).__name__] = (args, kwargs)

    class FakeDb:
        def __init__(self, endpoint, db):
            self.where = (endpoint, db)

        def get_container_client(self, name):
            return (name, *self.where)

    for name in ("ManagedIdentityCredential", "CosmosApplications", "CosmosMonitoring", "CosmosPrivacy",
                 "BlobRawStore", "StorageQueueSender", "CosmosOps"):
        monkeypatch.setattr(f"console.adapters.{name}", type(name, (Recorder,), {}), raising=True)
    monkeypatch.setattr("console.adapters._cosmos_database", lambda endpoint, db, cred: FakeDb(endpoint, db))
    monkeypatch.setattr("console.adapters._queue_factory", lambda endpoint, cred: ("queues", endpoint))
    env = {
        "AZURE_CLIENT_ID": "cid", "COSMOS_ENDPOINT": "https://cos", "COSMOS_DATABASE": "lending",
        "DATA_BLOB_ENDPOINT": "https://blob", "QUEUE_ENDPOINT": "https://queue",
        "PSEUDONYM_KEY": "k" * 32,
    }
    deps = build_deps(env)
    assert made["ManagedIdentityCredential"][1] == {"client_id": "cid"}
    assert made["CosmosApplications"][0] == (("applications", "https://cos", "lending"),)
    assert made["CosmosMonitoring"][0] == (("loans", "https://cos", "lending"), ("alerts", "https://cos", "lending"))
    assert made["CosmosPrivacy"][0] == tuple(
        (name, "https://cos", "lending") for name in ("consents", "deletion_proofs", "grievances"))
    assert made["BlobRawStore"][0][0] == "https://blob"
    assert made["StorageQueueSender"][0] == (("queues", "https://queue"),)
    assert deps.pseudonym_key == b"k" * 32
    assert made["CosmosOps"][0] == (("applications", "https://cos", "lending"),)


def test_cosmos_ops_lists_runs_and_projects_docs():
    from console.adapters import CosmosOps

    class Container:
        def __init__(self):
            self.queries = []

        def query_items(self, query, parameters=None, enable_cross_partition_query=False):
            self.queries.append((query, parameters, enable_cross_partition_query))
            if "DISTINCT" in query:
                return iter(["load-20261102010000", "fault-20261102030000"])
            return iter([{"id": "a", "application_id": "a", "run_id": "load-20261102010000",
                          "assembly": {"secret": "x"}, "policy": {"decision": "eligible", "max_amount_krw": 1}}])

    c = Container()
    ops = CosmosOps(c)
    assert ops.runs() == ["load-20261102010000", "fault-20261102030000"]
    docs = ops.by_run("load-20261102010000")
    assert docs[0]["application_id"] == "a" and "assembly" not in docs[0]
    assert docs[0]["policy"] == {"decision": "eligible", "reason_codes": []}
    assert c.queries[1][1] == [{"name": "@run", "value": "load-20261102010000"}]
    assert all(q[2] for q in c.queries)


def test_build_deps_requires_endpoints():
    with pytest.raises(KeyError):
        build_deps({"AZURE_CLIENT_ID": "cid", "PSEUDONYM_KEY": "k" * 32})


@pytest.mark.parametrize("mode, role, expected", [
    (None, None, None),
    ("easyauth", None, None),
    ("demo", None, "CreditOfficer"),
    ("demo", "Auditor", "Auditor"),
])
def test_build_deps_demo_mode_principal(monkeypatch, mode, role, expected):
    monkeypatch.setattr("console.adapters.ManagedIdentityCredential", lambda **kw: None)
    monkeypatch.setattr("console.adapters._cosmos_database", lambda *a: FakeDbAny())
    monkeypatch.setattr("console.adapters._queue_factory", lambda *a: None)
    monkeypatch.setattr("console.adapters.BlobRawStore", lambda *a: None)
    env = {"AZURE_CLIENT_ID": "cid", "COSMOS_ENDPOINT": "https://cos", "COSMOS_DATABASE": "lending",
           "DATA_BLOB_ENDPOINT": "https://blob", "QUEUE_ENDPOINT": "https://queue", "PSEUDONYM_KEY": "k" * 32}
    env |= {k: v for k, v in {"CONSOLE_AUTH_MODE": mode, "CONSOLE_DEMO_ROLE": role}.items() if v}
    demo = build_deps(env).demo_principal
    assert (demo and sorted(demo.roles)) == ([expected] if expected else None)


@pytest.mark.parametrize("env", [{"CONSOLE_AUTH_MODE": "demo", "CONSOLE_DEMO_ROLE": "Admin"},
                                 {"CONSOLE_AUTH_MODE": "none"}])
def test_build_deps_rejects_unknown_auth_settings(monkeypatch, env):
    monkeypatch.setattr("console.adapters.ManagedIdentityCredential", lambda **kw: None)
    base = {"AZURE_CLIENT_ID": "cid", "COSMOS_ENDPOINT": "https://cos", "COSMOS_DATABASE": "lending",
            "DATA_BLOB_ENDPOINT": "https://blob", "QUEUE_ENDPOINT": "https://queue", "PSEUDONYM_KEY": "k" * 32}
    with pytest.raises(ValueError):
        build_deps(base | env)
