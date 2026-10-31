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
    assert client.get("/healthz").json() == {"status": "ok", "rules_version": "2026.11-v1"}


def test_decision_roundtrip(client, make_features):
    r = client.post("/v1/decisions", json=make_features().model_dump(mode="json"))
    assert r.status_code == 200
    d = PolicyDecision.model_validate(r.json())
    assert d.decision == "eligible"
    assert d.rules_version == "2026.11-v1"


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
