import base64
import json

from console.auth import AUDITOR, CREDIT_MANAGER, CREDIT_OFFICER, Principal, csrf_token, parse_principal, verify_csrf

OID = "http://schemas.microsoft.com/identity/claims/objectidentifier"
ROLE = "http://schemas.microsoft.com/ws/2008/06/identity/claims/role"


def header(claims, **extra):
    body = {"auth_typ": "aad", "name_typ": "name", "role_typ": ROLE, "claims": claims, **extra}
    return base64.b64encode(json.dumps(body).encode()).decode()


def claims(*roles, oid="0f0e-officer", name="김심사"):
    out = [{"typ": OID, "val": oid}, {"typ": "name", "val": name}]
    return out + [{"typ": "roles", "val": r} for r in roles]


def test_parses_user_and_roles_from_easy_auth_header():
    p = parse_principal(header(claims(CREDIT_OFFICER)))
    assert p == Principal(user_id="0f0e-officer", name="김심사", roles=frozenset({CREDIT_OFFICER}))


def test_role_claims_under_role_typ_are_also_read():
    p = parse_principal(header([{"typ": OID, "val": "u1"}, {"typ": ROLE, "val": AUDITOR}]))
    assert p.roles == frozenset({AUDITOR})
    assert p.name == "u1"


def test_short_oid_claim_is_accepted():
    p = parse_principal(header([{"typ": "oid", "val": "u2"}, {"typ": "roles", "val": CREDIT_MANAGER}]))
    assert p.user_id == "u2"


def test_missing_or_garbled_header_has_no_principal():
    assert parse_principal(None) is None
    assert parse_principal("") is None
    assert parse_principal("not-base64!!") is None
    assert parse_principal(base64.b64encode(b"[1, 2]").decode()) is None
    assert parse_principal(base64.b64encode(b"\xff\xfe").decode()) is None


def test_principal_without_object_id_is_rejected():
    assert parse_principal(header([{"typ": "roles", "val": CREDIT_OFFICER}])) is None


def test_permissions_by_role():
    officer = Principal("u", "u", frozenset({CREDIT_OFFICER}))
    manager = Principal("u", "u", frozenset({CREDIT_MANAGER}))
    auditor = Principal("u", "u", frozenset({AUDITOR}))
    nobody = Principal("u", "u", frozenset({"Reader"}))
    assert [officer.can_view, officer.can_decide, officer.can_restore] == [True, True, True]
    assert [manager.can_view, manager.can_decide, manager.can_restore] == [True, True, True]
    assert [auditor.can_view, auditor.can_decide, auditor.can_restore] == [True, False, False]
    assert [nobody.can_view, nobody.can_decide, nobody.can_restore] == [False, False, False]


def test_csrf_token_is_bound_to_user_and_key():
    token = csrf_token(b"k" * 32, "u1")
    assert verify_csrf(b"k" * 32, "u1", token)
    assert not verify_csrf(b"k" * 32, "u2", token)
    assert not verify_csrf(b"x" * 32, "u1", token)
    assert not verify_csrf(b"k" * 32, "u1", "")
    assert not verify_csrf(b"k" * 32, "u1", None)
