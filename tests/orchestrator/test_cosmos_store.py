import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from journey.adapters import CosmosStore


@pytest.fixture
def cosmos(monkeypatch):
    class CosmosResourceExistsError(Exception):
        pass

    class CosmosBatchOperationError(Exception):
        def __init__(self, status_code):
            self.status_code = status_code

    client = Mock()
    monkeypatch.setitem(sys.modules, "azure.cosmos", SimpleNamespace(CosmosClient=Mock(return_value=client)))
    monkeypatch.setitem(sys.modules, "azure.cosmos.exceptions", SimpleNamespace(
        CosmosResourceExistsError=CosmosResourceExistsError, CosmosBatchOperationError=CosmosBatchOperationError))
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


@pytest.mark.parametrize("existing", [True, False])
def test_contact_batch_conditions_every_write_in_the_loan_partition(cosmos, existing):
    store, container, _ = cosmos
    case = {"id": "rc", "_etag": "case-v1", "loan_id": "loan"}
    log = {"id": "contacts", "loan_id": "loan"}
    if existing:
        log["_etag"] = "log-v2"
    assert store.replace_batch("alerts", "loan", [case, log])
    expected_log = ("replace", ("contacts", log), {"if_match_etag": "log-v2"}) if existing else ("create", (log,))
    container.execute_item_batch.assert_called_once_with(
        batch_operations=[("replace", ("rc", case), {"if_match_etag": "case-v1"}), expected_log],
        partition_key="loan")


@pytest.mark.parametrize("status", [409, 412, 503])
def test_batch_conflict_requires_reread_other_errors_propagate(cosmos, status):
    from azure.cosmos.exceptions import CosmosBatchOperationError

    store, container, _ = cosmos
    container.execute_item_batch.side_effect = CosmosBatchOperationError(status)
    doc = {"id": "rc", "_etag": "v1", "loan_id": "loan"}
    if status == 503:
        with pytest.raises(CosmosBatchOperationError):
            store.replace_batch("alerts", "loan", [doc])
    else:
        assert not store.replace_batch("alerts", "loan", [doc])
