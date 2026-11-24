"""Mock MyData 동의: HMAC 서명 토큰과 인메모리 동의 레지스트리."""

import base64
import hashlib
import hmac
import re
import uuid
from collections.abc import Collection, Iterable
from datetime import datetime, timedelta
from typing import Literal

from pydantic import TypeAdapter, ValidationError

from shared.contracts import ConsentGrant, ConsentScope, UsdConsentGrant, UsdProduct

_GRANT = TypeAdapter(ConsentGrant | UsdConsentGrant)

ConsentErrorCode = Literal["invalid", "expired", "scope", "revoked"]

_MIN_TTL = timedelta(days=1)
_MAX_TTL = timedelta(days=365)
_CONSENT_ID = re.compile(r"[0-9a-f]{32}")  # uuid4().hex


class ConsentError(Exception):
    def __init__(self, code: ConsentErrorCode) -> None:
        super().__init__(code)
        self.code: ConsentErrorCode = code


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload: bytes, secret: bytes) -> str:
    return _b64e(hmac.new(secret, payload, hashlib.sha256).digest())


def issue_token(grant: ConsentGrant, secret: bytes) -> str:
    payload = grant.model_dump_json().encode()
    return f"{_b64e(payload)}.{_sign(payload, secret)}"


def verify_token(
    token: str,
    secret: bytes,
    required_scope: ConsentScope,
    now: datetime,
    revoked: Collection[str] = frozenset(),
) -> ConsentGrant:
    try:
        body, sig = token.split(".")
        payload = _b64d(body)
    except ValueError:  # binascii.Error 포함
        raise ConsentError("invalid") from None
    if not hmac.compare_digest(sig.encode(), _sign(payload, secret).encode()):
        raise ConsentError("invalid")
    try:
        grant = _GRANT.validate_json(payload)
    except ValidationError:
        raise ConsentError("invalid") from None
    if grant.consent_id in revoked:
        raise ConsentError("revoked")
    if now >= grant.expires_at:
        raise ConsentError("expired")
    if required_scope not in grant.scopes:
        raise ConsentError("scope")
    return grant


class ConsentRegistry:
    """동의 원장(인메모리). P2 이후 Cosmos DB 동의 레지스트리로 대체된다."""

    def __init__(self) -> None:
        self._grants: dict[str, ConsentGrant] = {}
        self._revoked: set[str] = set()

    def grant(
        self,
        applicant_id: str,
        scopes: Iterable[ConsentScope],
        purpose: str,
        ttl: timedelta,
        now: datetime,
        product: UsdProduct | None = None,
    ) -> ConsentGrant:
        if not _MIN_TTL <= ttl <= _MAX_TTL:
            raise ValueError("ttl은 1~365일이어야 한다")
        g = (UsdConsentGrant if product else ConsentGrant)(
            **(product.model_dump() if product else {}),
            consent_id=uuid.uuid4().hex,
            applicant_id=applicant_id,
            scopes=frozenset(scopes),
            purpose=purpose,
            granted_at=now,
            expires_at=now + ttl,
        )
        self._grants[g.consent_id] = g
        return g

    def get(self, consent_id: str) -> ConsentGrant | None:
        return self._grants.get(consent_id)

    def revoke(self, consent_id: str) -> bool:
        # 원장은 메모리에만 있어 재시작 뒤에는 이전 동의를 모른다. 형식이 맞는 ID면 철회로 기록해
        # 그 ID가 든 토큰을 거절한다(같은 ID를 다시 철회해도 성공).
        if consent_id not in self._grants and not _CONSENT_ID.fullmatch(consent_id):
            return False
        self._revoked.add(consent_id)
        return True

    @property
    def revoked_ids(self) -> frozenset[str]:
        return frozenset(self._revoked)
