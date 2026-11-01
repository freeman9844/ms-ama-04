"""Container Apps 기본 인증(Easy Auth)이 넘기는 사용자 정보와 콘솔 권한.

Easy Auth는 로그인한 사용자의 ID 토큰 클레임을 `X-MS-CLIENT-PRINCIPAL`(base64 JSON)로 넘기고,
외부에서 들어온 같은 이름의 헤더는 지운다. 콘솔은 이 헤더만 믿고 역할(Entra 앱 역할)로 권한을 정한다.
"""

import base64
import binascii
import hashlib
import hmac
import json
from dataclasses import dataclass

CREDIT_OFFICER = "CreditOfficer"
CREDIT_MANAGER = "CreditManager"
AUDITOR = "Auditor"
PRINCIPAL_HEADER = "x-ms-client-principal"

_ROLE_CLAIMS = {"roles", "http://schemas.microsoft.com/ws/2008/06/identity/claims/role"}
_OID_CLAIMS = ("http://schemas.microsoft.com/identity/claims/objectidentifier", "oid")
_DECIDERS = frozenset({CREDIT_OFFICER, CREDIT_MANAGER})


@dataclass(frozen=True)
class Principal:
    user_id: str
    name: str
    roles: frozenset[str]

    @property
    def can_view(self) -> bool:
        return bool(self.roles & (_DECIDERS | {AUDITOR}))

    @property
    def can_decide(self) -> bool:
        """승인·조건부·거절 결정과 신규 신청 제출."""
        return bool(self.roles & _DECIDERS)

    @property
    def can_restore(self) -> bool:
        """가명 토큰을 실명으로 복원해 보기. 감사자는 가명 그대로 본다."""
        return bool(self.roles & _DECIDERS)


def parse_principal(header: str | None) -> Principal | None:
    if not header:
        return None
    try:
        data = json.loads(base64.b64decode(header, validate=True))
    except (binascii.Error, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    claims = [c for c in data.get("claims") or [] if isinstance(c, dict)]

    def first(*types: str) -> str:
        return next((str(c.get("val")) for t in types for c in claims if c.get("typ") == t and c.get("val")), "")

    user_id = first(*_OID_CLAIMS)
    if not user_id:
        return None
    role_types = _ROLE_CLAIMS | {data.get("role_typ")}
    roles = frozenset(str(c["val"]) for c in claims if c.get("typ") in role_types and c.get("val"))
    name = first(data.get("name_typ") or "name", "name", "preferred_username") or user_id
    return Principal(user_id=user_id, name=name, roles=roles)


def csrf_token(key: bytes, user_id: str) -> str:
    """Easy Auth 세션 쿠키로 인증되므로 상태 변경 폼에는 사용자에 묶인 토큰을 함께 보낸다."""
    return hmac.new(key, f"csrf:{user_id}".encode(), hashlib.sha256).hexdigest()


def verify_csrf(key: bytes, user_id: str, token: str | None) -> bool:
    return bool(token) and hmac.compare_digest(csrf_token(key, user_id), token)
