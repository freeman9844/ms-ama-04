from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from mockapis.app import create_app
from mockapis.synth import INJECTION_TEXT, Archetype, generate_case
from shared.contracts import ApplicantProfile, PgSettlement, TaxFiling, Transaction

KST = timezone(timedelta(hours=9))
AS_OF = date(2026, 10, 1)
SECRET = b"s" * 32


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 1, 9, 0, tzinfo=KST)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def client(clock):
    cases = {"a1": generate_case("a1", Archetype.HEALTHY_SEASONAL, AS_OF, 1, inject_prompt=True)}
    return TestClient(create_app(cases, SECRET, clock))


def _consent(client, scopes=("account", "tax", "pg"), ttl_days=90):
    r = client.post(
        "/consents",
        json={"applicant_id": "a1", "scopes": list(scopes), "purpose": "loan-application", "ttl_days": ttl_days},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_full_flow_returns_valid_contracts(client):
    h = _auth(_consent(client)["token"])
    ApplicantProfile.model_validate(client.get("/mydata/profile", headers=h).json())
    txns = client.get("/mydata/transactions", headers=h).json()
    assert len(txns) > 300
    for t in txns:
        Transaction.model_validate(t)
    filings = client.get("/tax/filings", headers=h).json()
    assert [TaxFiling.model_validate(f) for f in filings]
    settlements = client.get("/pg/settlements", headers=h).json()
    assert [PgSettlement.model_validate(s) for s in settlements]


def test_labels_not_exposed(client):
    r = client.get("/mydata/transactions", headers=_auth(_consent(client)["token"]))
    assert all(set(t) == set(Transaction.model_fields) for t in r.json())
    assert "label" not in r.text and "category" not in r.text


def test_injection_text_returned_verbatim(client):
    txns = client.get("/mydata/transactions", headers=_auth(_consent(client)["token"])).json()
    assert sum(t["description"] == INJECTION_TEXT for t in txns) == 1


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer garbage"}, {"Authorization": "Basic x"}])
def test_missing_or_bad_token_401(client, headers):
    r = client.get("/mydata/transactions", headers=headers)
    assert r.status_code == 401
    assert r.json() == {"detail": {"code": "invalid"}}


def test_expired_token_returns_401(client, clock):
    h = _auth(_consent(client, ttl_days=1)["token"])
    clock.now += timedelta(days=1)  # 정확히 expires_at
    r = client.get("/mydata/transactions", headers=h)
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "expired"


def test_scope_enforced_403(client):
    h = _auth(_consent(client, scopes=("account",))["token"])
    assert client.get("/mydata/transactions", headers=h).status_code == 200
    r = client.get("/tax/filings", headers=h)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "scope"


def test_revoke_then_401(client):
    c = _consent(client)
    assert client.post(f"/consents/{c['consent_id']}/revoke").status_code == 204
    r = client.get("/mydata/transactions", headers=_auth(c["token"]))
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "revoked"
    assert client.post("/consents/unknown/revoke").status_code == 404


def test_unknown_applicant_404(client):
    r = client.post("/consents", json={"applicant_id": "zz", "scopes": ["account"], "purpose": "p"})
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "unknown_applicant"


@pytest.mark.parametrize(
    "body",
    [
        {"applicant_id": "a1", "scopes": [], "purpose": "p"},
        {"applicant_id": "a1", "scopes": ["account"], "purpose": "p", "ttl_days": 0},
        {"applicant_id": "a1", "scopes": ["account"], "purpose": "p", "extra": 1},
    ],
)
def test_invalid_consent_request_422(client, body):
    assert client.post("/consents", json=body).status_code == 422


def test_fault_injection_503(client):
    h = _auth(_consent(client)["token"])
    assert client.post("/admin/faults", json={"mode": "unavailable"}).status_code == 204
    r = client.get("/mydata/transactions", headers=h)
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "unavailable"
    assert client.post("/admin/faults", json={"mode": "none"}).status_code == 204
    assert client.get("/mydata/transactions", headers=h).status_code == 200
