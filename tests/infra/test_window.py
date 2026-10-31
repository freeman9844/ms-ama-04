"""배포 창 불변식.

- 창이 닫혀 있으면(closed) 모든 대상의 public network access가 꺼져 있다.
- 창이 열려 있으면(open) WINDOWED 대상은 기본 거부 + 운영자 IP 정확히 1개만 허용한다.
- ALWAYS_PRIVATE 대상은 창이 열려도 꺼져 있다.
태스크마다 자기 리소스 행을 추가합니다.
"""
import pytest

OPERATOR_IP = "20.30.40.50"

WINDOWED = [
    ("Microsoft.KeyVault/vaults", "kv"),
    ("Microsoft.Storage/storageAccounts", "stdat"),
    ("Microsoft.Storage/storageAccounts", "stfn"),
    ("Microsoft.DocumentDB/databaseAccounts", "cosmos-"),
    ("Microsoft.CognitiveServices/accounts", "ais-"),
]
ALWAYS_PRIVATE: list[tuple[str, str]] = [
    ("Microsoft.Storage/storageAccounts", "stagt"),
    ("Microsoft.Search/searchServices", "srch-"),
]


def _public(resource: dict) -> str:
    return (resource["properties"].get("publicNetworkAccess") or "").lower()


def _allowed_ips(resource: dict) -> list[str]:
    props = resource["properties"]
    kind = resource["type"].lower()
    if kind == "microsoft.documentdb/databaseaccounts":
        return [r["ipAddressOrRange"] for r in props.get("ipRules", [])]
    if kind == "microsoft.web/sites":
        return [r["ipAddress"].removesuffix("/32") for r in props["siteConfig"].get("ipSecurityRestrictions", [])]
    if kind == "microsoft.search/searchservices":
        return [r["value"] for r in props["networkRuleSet"].get("ipRules", [])]
    return [r["value"] for r in props["networkAcls"].get("ipRules", [])]


def _default_deny(resource: dict) -> bool:
    props = resource["properties"]
    kind = resource["type"].lower()
    if kind == "microsoft.documentdb/databaseaccounts":
        # Cosmos는 기본 동작 설정이 없고 ipRules가 비면 전체 허용이므로 규칙이 반드시 있어야 합니다.
        return bool(props.get("ipRules"))
    if kind == "microsoft.web/sites":
        return props["siteConfig"].get("ipSecurityRestrictionsDefaultAction") == "Deny"
    return props["networkAcls"]["defaultAction"] == "Deny"


@pytest.mark.parametrize(("type_", "prefix"), WINDOWED + ALWAYS_PRIVATE)
def test_closed_window_disables_public_access(closed, type_, prefix):
    assert _public(closed.one(type_, prefix)) == "disabled"


@pytest.mark.parametrize(("type_", "prefix"), WINDOWED)
def test_open_window_allows_only_operator_ip(opened, type_, prefix):
    resource = opened.one(type_, prefix)
    assert _public(resource) == "enabled"
    assert _default_deny(resource)
    assert _allowed_ips(resource) == [OPERATOR_IP]


@pytest.mark.parametrize(("type_", "prefix"), ALWAYS_PRIVATE)
def test_always_private_stays_closed_when_window_open(opened, type_, prefix):
    assert _public(opened.one(type_, prefix)) == "disabled"
