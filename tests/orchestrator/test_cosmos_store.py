import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from journey.adapters import CosmosStore


@pytest.fixture
def cosmos(monkeypatch):
    class CosmosResourceExistsError(Exception):
        pass

    client = Mock()
    monkeypatch.setitem(sys.modules, "azure.cosmos", SimpleNamespace(CosmosClient=Mock(return_value=client)))
    monkeypatch.setitem(sys.modules, "azure.cosmos.exceptions", SimpleNamespace(CosmosResourceExistsError=CosmosResourceExistsError))
    store = CosmosStore("local", "db", None)
    container = client.get_database_client.return_value.get_container_client.return_value
    return store, container, CosmosResourceExistsError


def test_create_if_absent_uses_create_and_accepts_duplicate(cosmos):
    store, container, conflict = cosmos
    doc = {"id": "app-1", "loan_id": "app-1"}
    container.create_item.side_effect = [None, conflict()]
    store.create_if_absent("loans", doc)
    store.create_if_absent("loans", doc)
    assert container.create_item.call_count == 2
    container.create_item.assert_called_with(doc)
    container.upsert_item.assert_not_called()


def test_create_if_absent_propagates_other_errors(cosmos):
    store, container, _ = cosmos
    container.create_item.side_effect = TimeoutError()
    with pytest.raises(TimeoutError):
        store.create_if_absent("loans", {"id": "app-1", "loan_id": "app-1"})
