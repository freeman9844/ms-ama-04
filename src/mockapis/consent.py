"""Signed grants; the deployed service also requires a durable active marker."""

import base64
import hashlib
import hmac
import re
import uuid
from collections.abc import Collection, Iterable
from abc import ABC, abstractmethod
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


class GrantAuthority(ABC):
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
        self.record(g)
        return g

    @abstractmethod
    def record(self, grant: ConsentGrant) -> None: ...

    @abstractmethod
    def active(self, consent_id: str) -> bool: ...

    @abstractmethod
    def revoke(self, consent_id: str) -> bool: ...


class ConsentRegistry(GrantAuthority):
    """Process-local authority for synthetic, embedded API callers."""

    def __init__(self) -> None:
        self._grants: dict[str, ConsentGrant] = {}
        self._revoked: set[str] = set()

    def record(self, grant: ConsentGrant) -> None:
        self._grants[grant.consent_id] = grant

    def active(self, consent_id: str) -> bool:
        return consent_id in self._grants and consent_id not in self._revoked

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


class AuthorityUnavailable(RuntimeError):
    pass


class BlobConsentRegistry(GrantAuthority):
    """Only opaque grant IDs and state bytes are stored. Reads use the primary, without a cache."""

    def __init__(self, container):
        self.container = container

    def _write(self, cid: str, state: bytes, *, overwrite: bool) -> None:
        try:
            self.container.upload_blob(cid, state, overwrite=overwrite)
        except Exception:
            raise AuthorityUnavailable() from None

    def record(self, grant: ConsentGrant) -> None:
        self._write(grant.consent_id, b"active", overwrite=False)

    def active(self, consent_id: str) -> bool:
        from azure.core.exceptions import ResourceNotFoundError
        try:
            return self.container.download_blob(consent_id).readall() == b"active"
        except ResourceNotFoundError:
            return False
        except Exception:
            raise AuthorityUnavailable() from None

    def revoke(self, consent_id: str) -> bool:
        if not _CONSENT_ID.fullmatch(consent_id):
            return False
        self._write(consent_id, b"revoked", overwrite=True)
        return True
