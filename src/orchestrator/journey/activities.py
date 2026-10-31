"""신청 여정 활동. 외부 I/O는 Deps 포트로만 한다(Azure 어댑터는 adapters.py).

활동은 (deps, payload) -> JSON 직렬화 가능한 값. function_app이 Deps를 주입한다.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

from pydantic import TypeAdapter

from agents.classify import Category, classify
from agents.data_assembly import DEFAULT_MODEL, assemble, build_pseudonymizer
from agents.features import compute_underwriting
from agents.txn_classify import ClassificationCache, Decider, LlmFallback, classify_unresolved
from agents.underwriting import FLAG_ASSESSMENT_UNAVAILABLE, AgentRunner, UnderwritingContext, assess
from journey.models import CONSENT_PURPOSE, CONSENT_SCOPES, CONSENT_TTL_DAYS
from shared.contracts import ApplicantProfile, TaxFiling, Transaction

CONTAINERS = ("applications", "consents")
CLASSIFICATION_CONTAINER = "classifications"
ANALYST_MODEL = "gpt-5.4"
REVIEWER_MODEL = "gpt-5.4-mini"
CLASSIFIER_MODEL = "gpt-5.4-mini"
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
    decider: Decider | None = None
    cache: ClassificationCache | None = None
    analyst_model: str = ANALYST_MODEL
    reviewer_model: str = REVIEWER_MODEL
    classifier_model: str = CLASSIFIER_MODEL


class StoreCache:
    """의미 분류 캐시(Cosmos classifications, pk=/id). 키는 가명·정규화 적요의 해시다."""

    def __init__(self, store: DocumentStore, container: str = CLASSIFICATION_CONTAINER) -> None:
        self._store, self._container = store, container

    def get(self, key: str) -> dict[str, Any] | None:
        return self._store.read(self._container, key, key)

    def put(self, key: str, entry: dict[str, Any]) -> None:
        self._store.upsert(self._container, {**entry, "id": key})


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
    _remember_foundry(deps, consent_id, [result.conversation_id] if result.conversation_id else [], [])
    return result.report


def _remember_foundry(
    deps: Deps, consent_id: str, conversations: list[str], containers: list[dict[str, Any]]
) -> None:
    """동의 철회 시 삭제할 Foundry 대화·컨테이너를 동의 문서에 남긴다(P5 철회 처리)."""
    if not conversations and not containers:
        return
    consent = deps.store.read("consents", consent_id, consent_id)
    if consent is None:
        return
    known = consent.setdefault("foundry_conversations", [])
    known.extend(c for c in conversations if c not in known)
    consent.setdefault("foundry_containers", []).extend(containers)
    deps.store.upsert("consents", consent)


def _load(deps: Deps, consent_id: str) -> tuple[ApplicantProfile, list[Transaction], list[TaxFiling]]:
    container = raw_container(consent_id)
    profile = deps.raw.get_json(container, "profile.json")
    transactions = deps.raw.get_json(container, "transactions.json")
    if profile is None or transactions is None:
        raise RuntimeError(f"원천 데이터 없음: {container}")
    return (
        ApplicantProfile.model_validate(profile),
        _TRANSACTIONS.validate_python(transactions),
        _FILINGS.validate_python(deps.raw.get_json(container, "tax.json") or []),
    )


def _semantic(
    deps: Deps, profile: ApplicantProfile, txns: list[Transaction], consent_id: str
) -> tuple[dict[str, Any] | None, dict[str, Category]]:
    if deps.decider is None:
        return None, {}
    fallback = LlmFallback(deps.agents, deps.classifier_model) if deps.agents is not None else None
    try:
        result = classify_unresolved(
            txns,
            industry=profile.industry,
            mask=build_pseudonymizer(deps.pseudonym_key, profile, txns).mask,
            decider=deps.decider,
            fallback=fallback,
            cache=deps.cache,
        )
    except Exception as e:  # noqa: BLE001 - 의미 분류 실패는 규칙 분류만으로 진행한다
        return {"error": f"{type(e).__name__}: {e}"[:300]}, {}
    finally:
        if fallback is not None:
            _remember_foundry(deps, consent_id, fallback.conversations, [])
    return result.record(), result.overrides


def underwrite(deps: Deps, payload: dict[str, Any]) -> dict[str, Any]:
    profile, txns, filings = _load(deps, payload["consent_id"])
    classification, overrides = _semantic(deps, profile, txns, payload["consent_id"])
    result = compute_underwriting(
        payload["application_id"],
        profile,
        txns,
        filings,
        as_of=date.fromisoformat(payload["as_of"]),
        requested_amount_krw=payload["requested_amount_krw"],
        requested_tenor_months=payload["requested_tenor_months"],
        overrides=overrides,
    )
    return {
        "features": result.features.model_dump(mode="json"),
        "signals": result.signals,
        "flags": result.flags,
        "monthly": result.monthly,
        "classification": classification,
        "category_overrides": {k: v.value for k, v in overrides.items()},
    }


def evaluate_policy(deps: Deps, features: dict[str, Any]) -> dict[str, Any]:
    return deps.policy.decide(features)


def assess_credit(deps: Deps, payload: dict[str, Any]) -> dict[str, Any]:
    if deps.agents is None:
        return {"assessment": None, "flags": [FLAG_ASSESSMENT_UNAVAILABLE], "record": {"error": "agents_disabled"}}
    profile, txns, _ = _load(deps, payload["consent_id"])
    uw = payload["underwriting"]
    overrides = uw.get("category_overrides") or {}
    ctx = UnderwritingContext(
        application_id=payload["application_id"],
        features=uw["features"],
        signals=uw["signals"],
        flags=tuple(uw.get("flags", [])),
        monthly=uw["monthly"],
        policy=payload["policy"],
        classification=(uw.get("classification") or {}).get("metrics"),
        transactions=txns,
        categories={t.txn_id: overrides.get(t.txn_id) or classify(t).value for t in txns},
        mask=build_pseudonymizer(deps.pseudonym_key, profile, txns).mask,
    )
    result = assess(ctx, runner=deps.agents, analyst_model=deps.analyst_model, reviewer_model=deps.reviewer_model)
    _remember_foundry(deps, payload["consent_id"], result.conversations, result.containers)
    return {
        "assessment": result.assessment.model_dump(mode="json") if result.assessment else None,
        "flags": result.flags,
        "record": result.record,
    }


ACTIVITIES: dict[str, Callable[[Deps, Any], Any]] = {
    f.__name__: f
    for f in (save_document, request_consent, assemble_data, underwrite, evaluate_policy, assess_credit)
}
