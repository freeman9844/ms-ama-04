import base64
from datetime import datetime, timedelta, timezone

import pytest

from mockapis.consent import ConsentError, ConsentRegistry, issue_token, verify_token
from shared.contracts import ConsentGrant, ConsentScope

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 1, 9, 0, tzinfo=KST)
SECRET = b"s" * 32
ACCOUNT = ConsentScope.ACCOUNT


def _grant(**kw):
    base = dict(
        consent_id="c1",
        applicant_id="a1",
        scopes=frozenset({ACCOUNT, ConsentScope.TAX}),
        purpose="loan-application",
        granted_at=NOW,
        expires_at=NOW + timedelta(days=30),
    )
    base.update(kw)
    return ConsentGrant(**base)


def _code(token, *, secret=SECRET, scope=ACCOUNT, now=NOW, revoked=frozenset()):
    with pytest.raises(ConsentError) as exc:
        verify_token(token, secret, scope, now, revoked)
    return exc.value.code


def test_issue_and_verify_roundtrip():
    g = _grant()
    assert verify_token(issue_token(g, SECRET), SECRET, ACCOUNT, NOW) == g


def test_tampered_payload_is_invalid():
    token = issue_token(_grant(), SECRET)
    forged = _grant(applicant_id="attacker").model_dump_json().encode()
    forged_body = base64.urlsafe_b64encode(forged).rstrip(b"=").decode()
    assert _code(f"{forged_body}.{token.split('.')[1]}") == "invalid"


def test_wrong_secret_is_invalid():
    token = issue_token(_grant(), SECRET)
    assert _code(token, secret=b"x" * 32) == "invalid"


@pytest.mark.parametrize("token", ["", "abc", "a.b.c", "!!!.???", "abc.가나"])
def test_garbage_token_is_invalid(token):
    assert _code(token) == "invalid"


def test_expired_at_exact_boundary():
    g = _grant()
    token = issue_token(g, SECRET)
    assert _code(token, now=g.expires_at) == "expired"
    assert verify_token(token, SECRET, ACCOUNT, g.expires_at - timedelta(microseconds=1)) == g


def test_missing_scope():
    token = issue_token(_grant(), SECRET)
    assert _code(token, scope=ConsentScope.PG) == "scope"


def test_revoked_checked_before_expiry():
    g = _grant()
    token = issue_token(g, SECRET)
    assert _code(token, now=g.expires_at + timedelta(days=1), revoked={"c1"}) == "revoked"


def test_registry_grant_get_and_revoke():
    reg = ConsentRegistry()
    g = reg.grant("a1", [ACCOUNT], "loan-application", timedelta(days=90), NOW)
    assert len(g.consent_id) == 32
    assert g.expires_at == NOW + timedelta(days=90)
    assert reg.get(g.consent_id) == g
    assert reg.revoke(g.consent_id) is True
    assert g.consent_id in reg.revoked_ids


def test_registry_revoke_unknown_returns_false():
    assert ConsentRegistry().revoke("nope") is False


@pytest.mark.parametrize("ttl", [timedelta(hours=23), timedelta(days=366)])
def test_registry_ttl_bounds(ttl):
    with pytest.raises(ValueError):
        ConsentRegistry().grant("a1", [ACCOUNT], "loan-application", ttl, NOW)
