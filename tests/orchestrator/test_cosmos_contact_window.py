from unittest.mock import Mock

from journey.adapters import CosmosStore


def test_contact_window_sends_inclusive_comparisons_to_cosmos():
    store = CosmosStore.__new__(CosmosStore)
    loans, alerts = Mock(), Mock()
    store._db = Mock()
    store._db.get_container_client.side_effect = {"loans": loans, "alerts": alerts}.__getitem__
    loans.query_items.return_value = ["loan-a"]
    alerts.query_items.return_value = []

    store.prior_contact_slots("borrower", "2026-11-01", "2026-11-07")

    (query,), options = alerts.query_items.call_args
    assert 's["on"] >= @lo' in query and 's["on"] <= @hi' in query
    assert options["parameters"] == [
        {"name": "@lo", "value": "2026-11-01"}, {"name": "@hi", "value": "2026-11-07"}
    ]
    assert options["partition_key"] == "loan-a"
