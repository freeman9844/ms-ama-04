"""신청 여정 활동. 외부 I/O는 Deps 포트로만 한다(Azure 어댑터는 adapters.py).

활동은 (deps, payload) -> JSON 직렬화 가능한 값. function_app이 Deps를 주입한다.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

from pydantic import TypeAdapter

from agents.data_assembly import DEFAULT_MODEL, AgentRunner, assemble
from agents.features import compute_underwriting
from journey.models import CONSENT_PURPOSE, CONSENT_SCOPES, CONSENT_TTL_DAYS
from shared.contracts import ApplicantProfile, TaxFiling, Transaction

CONTAINERS = ("applications", "consents")
TOPIC_CONSENT = "consent"
EVENT_CONSENT_GRANTED = "consent.granted"
_TRANSACTIONS = TypeAdapter(list[Transaction])
_FILINGS = TypeAdapter(list[TaxFiling])


class DocumentStore(Protocol):
    def upsert(self, container: str, document: dict[str, Any]) -> None: ...
    def read(self, container: str, item_id: str, partition_key: str) -> dict[str, Any] | None: ...


class RawStore(Protocol):
    def put_json(self, container: str, name: str, value: Any) -> None: ...
    def get_json(self, container: str, name: str) -> Any | None: ...


class MockApi(Protocol):
    def create_consent(self, applicant_id: str, scopes: list[str], purpose: str, ttl_days: int) -> dict[str, Any]: ...
    def fetch(self, source: str, token: str) -> Any: ...


class PolicyApi(Protocol):
    def decide(self, features: dict[str, Any]) -> dict[str, Any]: ...


class Publisher(Protocol):
    def publish(self, topic: str, message: dict[str, Any], message_id: str) -> None: ...


@dataclass(frozen=True)
class Deps:
    store: DocumentStore
    raw: RawStore
    mock: MockApi
    policy: PolicyApi
    events: Publisher | None
    pseudonym_key: bytes
    clock: Callable[[], datetime]
    agents: AgentRunner | None = None
    model: str = DEFAULT_MODEL


def raw_container(consent_id: str) -> str:
    return f"consent-{consent_id}"


def save_document(deps: Deps, payload: dict[str, Any]) -> dict[str, Any]:
    container = payload["container"]
    if container not in CONTAINERS:
        raise ValueError(f"허용되지 않은 컨테이너: {container}")
    deps.store.upsert(container, payload["document"])
    return {"id": payload["document"]["id"]}


def request_consent(deps: Deps, payload: dict[str, Any]) -> dict[str, Any]:
    scopes = list(CONSENT_SCOPES)
    grant = deps.mock.create_consent(payload["applicant_id"], scopes, CONSENT_PURPOSE, CONSENT_TTL_DAYS)
    consent_id = grant["consent_id"]
    doc = {
        "id": consent_id,
        "consent_id": consent_id,
        "application_id": payload["application_id"],
        "applicant_id": payload["applicant_id"],
        "scopes": scopes,
        "purpose": CONSENT_PURPOSE,
        "granted_at": deps.clock().isoformat(),
        "expires_at": grant["expires_at"],
        "status": "granted",
        "raw_container": raw_container(consent_id),
        "foundry_conversations": [],
        "event_published": False,
        "event_error": None,
    }
    if deps.events is None:
        doc["event_error"] = "servicebus_disabled"
    else:
        message = {
            "type": EVENT_CONSENT_GRANTED,
            "consent_id": consent_id,
            "application_id": payload["application_id"],
            "scopes": scopes,
            "expires_at": grant["expires_at"],
        }
        try:
            deps.events.publish(TOPIC_CONSENT, message, f"{EVENT_CONSENT_GRANTED}:{consent_id}")
            doc["event_published"] = True
        except Exception as e:  # noqa: BLE001 - 알림 실패가 동의 자체를 무효로 하지 않는다
            doc["event_error"] = f"{type(e).__name__}: {e}"[:300]
    deps.store.upsert("consents", doc)
    return {"consent_id": consent_id, "token": grant["token"], "scopes": scopes, "expires_at": grant["expires_at"]}


def assemble_data(deps: Deps, payload: dict[str, Any]) -> dict[str, Any]:
    consent_id = payload["consent_id"]
    result = assemble(
        payload["application_id"],
        scopes=payload["scopes"],
        token=payload["token"],
        api=deps.mock,
        pseudonym_key=deps.pseudonym_key,
        runner=deps.agents,
        model=deps.model,
    )
    for source, raw in result.data.items():
        deps.raw.put_json(raw_container(consent_id), f"{source}.json", raw)
    if result.conversation_id:
        consent = deps.store.read("consents", consent_id, consent_id)
        if consent is not None:
            conversations = consent.setdefault("foundry_conversations", [])
            if result.conversation_id not in conversations:
                conversations.append(result.conversation_id)
                deps.store.upsert("consents", consent)
    return result.report


def underwrite(deps: Deps, payload: dict[str, Any]) -> dict[str, Any]:
    container = raw_container(payload["consent_id"])
    profile = deps.raw.get_json(container, "profile.json")
    transactions = deps.raw.get_json(container, "transactions.json")
    if profile is None or transactions is None:
        raise RuntimeError(f"원천 데이터 없음: {container}")
    filings = deps.raw.get_json(container, "tax.json") or []
    result = compute_underwriting(
        payload["application_id"],
        ApplicantProfile.model_validate(profile),
        _TRANSACTIONS.validate_python(transactions),
        _FILINGS.validate_python(filings),
        as_of=date.fromisoformat(payload["as_of"]),
        requested_amount_krw=payload["requested_amount_krw"],
        requested_tenor_months=payload["requested_tenor_months"],
    )
    return {"features": result.features.model_dump(mode="json"), "signals": result.signals, "flags": result.flags}


def evaluate_policy(deps: Deps, features: dict[str, Any]) -> dict[str, Any]:
    return deps.policy.decide(features)


ACTIVITIES: dict[str, Callable[[Deps, Any], Any]] = {
    f.__name__: f for f in (save_document, request_consent, assemble_data, underwrite, evaluate_policy)
}
