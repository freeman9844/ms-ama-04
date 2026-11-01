import json

import pytest

from console.adapters import LIST_QUERY, CosmosApplications, StorageQueueSender, build_deps


class FakeContainer:
    def __init__(self, items=None, missing=False):
        self.items = items or []
        self.missing = missing
        self.calls = []

    def query_items(self, query, parameters, enable_cross_partition_query):
        self.calls.append(("query", query, parameters, enable_cross_partition_query))
        return iter(self.items)

    def read_item(self, item, partition_key):
        self.calls.append(("read", item, partition_key))
        if self.missing:
            raise NotFound()
        return {"id": item}


class NotFound(Exception):
    status_code = 404


def test_list_recent_queries_projection_across_partitions():
    container = FakeContainer([{"id": "app-1"}])
    assert CosmosApplications(container).list_recent(50) == [{"id": "app-1"}]
    kind, query, params, cross = container.calls[0]
    assert (kind, query, cross) == ("query", LIST_QUERY, True)
    assert params == [{"name": "@limit", "value": 50}]
    assert "ORDER BY c.updated_at DESC" in query and "c.assessment.recommendation AS recommendation" in query
    assert "c.assessment " not in query.replace("c.assessment.recommendation", "")


def test_get_reads_by_application_partition_and_maps_missing_to_none():
    container = FakeContainer()
    assert CosmosApplications(container).get("app-1") == {"id": "app-1"}
    assert container.calls == [("read", "app-1", "app-1")]
    assert CosmosApplications(FakeContainer(missing=True)).get("app-x") is None


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

    for name in ("ManagedIdentityCredential", "CosmosApplications", "BlobRawStore", "StorageQueueSender"):
        monkeypatch.setattr(f"console.adapters.{name}", type(name, (Recorder,), {}), raising=True)
    monkeypatch.setattr("console.adapters._cosmos_container", lambda endpoint, db, cred: ("container", endpoint, db))
    monkeypatch.setattr("console.adapters._queue_factory", lambda endpoint, cred: ("queues", endpoint))
    env = {
        "AZURE_CLIENT_ID": "cid", "COSMOS_ENDPOINT": "https://cos", "COSMOS_DATABASE": "lending",
        "DATA_BLOB_ENDPOINT": "https://blob", "QUEUE_ENDPOINT": "https://queue",
        "PSEUDONYM_KEY": "k" * 32,
    }
    deps = build_deps(env)
    assert made["ManagedIdentityCredential"][1] == {"client_id": "cid"}
    assert made["CosmosApplications"][0] == (("container", "https://cos", "lending"),)
    assert made["BlobRawStore"][0][0] == "https://blob"
    assert made["StorageQueueSender"][0] == (("queues", "https://queue"),)
    assert deps.pseudonym_key == b"k" * 32


def test_build_deps_requires_endpoints():
    with pytest.raises(KeyError):
        build_deps({"AZURE_CLIENT_ID": "cid", "PSEUDONYM_KEY": "k" * 32})
