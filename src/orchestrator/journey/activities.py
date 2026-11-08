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
from agents.features import compute_underwriting, monthly_installment
from agents.monitoring import Baseline, settlement_for
from agents.txn_classify import ClassificationCache, Decider, LlmFallback, classify_unresolved
from agents.underwriting import FLAG_ASSESSMENT_UNAVAILABLE, AgentRunner, UnderwritingContext, assess
from journey.models import CONSENT_PURPOSE, CONSENT_SCOPES, CONSENT_TTL_DAYS, LifecycleStart
from shared.contracts import ApplicantProfile, TaxFiling, Transaction

CONTAINERS = ("applications", "consents")
CLASSIFICATION_CONTAINER = "classifications"
ANALYST_MODEL = "gpt-5.6-sol"
REVIEWER_MODEL = "gpt-5.6-luna"
CLASSIFIER_MODEL = "gpt-5.6-luna"
EW_MODEL = "gpt-5.4"
COLLECTIONS_MODEL = "gpt-5.6-luna"
NOTICE_MODEL = "gpt-5.6-luna"
GRIEVANCE_MODEL = "gpt-5.6-luna"
LIFECYCLE_QUEUE = "consent-lifecycle"
ENDED_CONSENT = ("expired", "revoked")
LOANS_CONTAINER = "loans"
TOPIC_CONSENT = "consent"
EVENT_CONSENT_GRANTED = "consent.granted"
_TRANSACTIONS = TypeAdapter(list[Transaction])
_FILINGS = TypeAdapter(list[TaxFiling])


class DocumentStore(Protocol):
    def upsert(self, container: str, document: dict[str, Any]) -> None: ...
    def read(self, container: str, item_id: str, partition_key: str) -> dict[str, Any] | None: ...
    def replace(self, container: str, document: dict[str, Any], etag: str | None) -> bool:
        """etag가 같을 때만 교체한다. 다른 쓰기가 끼어들었으면 False."""
        ...


class RawStore(Protocol):
    def put_json(self, container: str, name: str, value: Any) -> None: ...
    def get_json(self, container: str, name: str) -> Any | None: ...
    def manifest(self, container: str) -> list[dict[str, Any]] | None:
        """[{name, size, sha256}]. 컨테이너가 없으면 None."""
        ...
    def delete_container(self, container: str) -> bool:
        """삭제했으면 True, 원래 없었으면 False."""
        ...


class MockApi(Protocol):
    def create_consent(self, applicant_id: str, scopes: list[str], purpose: str, ttl_days: int) -> dict[str, Any]: ...
    def fetch(self, source: str, token: str) -> Any: ...
    def revoke(self, consent_id: str) -> str:
        """revoked | absent(레지스트리에 없음)."""
        ...


class QueueSender(Protocol):
    def send(self, queue: str, message: dict[str, Any]) -> None: ...


class PolicyApi(Protocol):
    def decide(self, features: dict[str, Any]) -> dict[str, Any]: ...


class Publisher(Protocol):
    def publish(
        self, topic: str, message: dict[str, Any], message_id: str, session_id: str | None = None
    ) -> None: ...


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
    ew_model: str = EW_MODEL
    collections_model: str = COLLECTIONS_MODEL
    queue: QueueSender | None = None
    notice_model: str = NOTICE_MODEL
    grievance_model: str = GRIEVANCE_MODEL


class Conflict(RuntimeError):
    """etag 경합이 계속돼 문서를 고치지 못했다."""


def update_doc(
    store: DocumentStore,
    container: str,
    item_id: str,
    partition_key: str,
    mutate: Callable[[dict[str, Any]], Any],
    attempts: int = 5,
) -> dict[str, Any] | None:
    """읽기-수정-조건부 교체. mutate가 False를 돌려주면 쓰지 않는다. 문서가 없으면 None."""
    for _ in range(attempts):
        doc = store.read(container, item_id, partition_key)
        if doc is None:
            return None
        if mutate(doc) is False:
            return doc
        if store.replace(container, doc, doc.get("_etag")):
            return doc
    raise Conflict(f"{container}/{item_id}: 동시 수정 충돌")


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


def schedule_lifecycle(deps: Deps, doc: dict[str, Any]) -> None:
    """동의 수명주기(만료 타이머·갱신 안내·삭제)를 예약한다.

    큐가 없으면(로컬·테스트) 기록만 남긴다. 전송이 실패하면 동의를 무효(void)로 남기고 예외를 올린다.
    만료 처리가 예약되지 않은 동의로는 데이터를 가져오지 않는다(fail-closed).
    """
    doc["lifecycle_error"] = None
    if deps.queue is None:
        doc["lifecycle_error"] = "queue_disabled"
        deps.store.upsert("consents", doc)
        return
    deps.store.upsert("consents", doc)
    message = LifecycleStart(consent_id=doc["consent_id"], expires_at=doc["expires_at"])
    try:
        deps.queue.send(LIFECYCLE_QUEUE, message.model_dump(mode="json", exclude_defaults=True))
    except Exception as e:
        doc["status"] = "void"
        doc["lifecycle_error"] = f"{type(e).__name__}: {e}"[:300]
        deps.store.upsert("consents", doc)
        raise


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
    schedule_lifecycle(deps, doc)
    return {"consent_id": consent_id, "token": grant["token"], "scopes": scopes, "expires_at": grant["expires_at"]}


class ConsentEnded(RuntimeError):
    """데이터 조립 중 동의가 끝났다(철회·만료). 방금 쓴 원천 데이터를 지우고 여정을 멈춘다."""


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
    container = raw_container(consent_id)
    for source, raw in result.data.items():
        deps.raw.put_json(container, f"{source}.json", raw)
    _remember_foundry(deps, consent_id, [result.conversation_id] if result.conversation_id else [], [])
    _drop_if_consent_ended(deps, consent_id, container)
    return result.report


def _drop_if_consent_ended(deps: Deps, consent_id: str, container: str) -> None:
    """쓰기 뒤에 확인한다. 삭제는 동의를 먼저 끝남으로 표시하므로, 조립 중 삭제가 끝났다면 여기서 보인다."""
    consent = deps.store.read("consents", consent_id, consent_id)
    if consent is None or consent.get("status") not in ENDED_CONSENT:
        return
    try:
        outcome = "deleted" if deps.raw.delete_container(container) else "absent"
    except Exception:  # noqa: BLE001 - 실패도 기록으로 남긴다
        outcome = "failed"
    entry = {"kind": "raw_container", "target": container, "outcome": outcome, "at": deps.clock().isoformat()}
    update_doc(deps.store, "consents", consent_id, consent_id, lambda c: c.setdefault("late_cleanup", []).append(entry))
    raise ConsentEnded(f"데이터 조립 중 동의가 끝났습니다: {consent_id}")


def _remember_foundry(
    deps: Deps, consent_id: str, conversations: list[str], containers: list[dict[str, Any]]
) -> None:
    """동의 만료·철회 때 삭제할 Foundry 대화·컨테이너를 동의 문서에 남긴다.

    삭제가 이미 끝난 동의(늦게 끝난 에이전트 실행)면 바로 지우고 late_cleanup에 기록한다.
    """
    if not conversations and not containers:
        return

    def mutate(consent: dict[str, Any]) -> None:
        if consent.get("status") in ENDED_CONSENT:
            consent.setdefault("late_cleanup", []).extend(_cleanup_now(deps, conversations, containers))
            return
        known = consent.setdefault("foundry_conversations", [])
        known.extend(c for c in conversations if c not in known)
        consent.setdefault("foundry_containers", []).extend(containers)

    update_doc(deps.store, "consents", consent_id, consent_id, mutate)


def _cleanup_now(
    deps: Deps, conversations: list[str], containers: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    at, out = deps.clock().isoformat(), []
    for cid in conversations:
        outcome = "failed"
        if deps.agents is not None:
            try:
                outcome = str(deps.agents.delete_conversation(cid))  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                outcome = "failed"
        out.append({"kind": "conversation", "target": cid, "outcome": outcome, "at": at})
    for c in containers:
        deleted = bool(c.get("deleted"))
        if not deleted and deps.agents is not None:
            try:
                deleted = bool(deps.agents.delete_container(c["id"]))
            except Exception:  # noqa: BLE001
                deleted = False
        out.append({"kind": "container", "target": c["id"], "outcome": "deleted" if deleted else "failed", "at": at})
    return out


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
) -> tuple[dict[str, Any] | None, dict[str, Category], dict[str, str]]:
    """(분류 기록, 부채 override, 출금 거래별 의미 라벨)."""
    if deps.decider is None:
        return None, {}, {}
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
        return {"error": f"{type(e).__name__}: {e}"[:300]}, {}, {}
    finally:
        if fallback is not None:
            _remember_foundry(deps, consent_id, fallback.conversations, [])
    return result.record(), result.overrides, result.labels


def underwrite(deps: Deps, payload: dict[str, Any]) -> dict[str, Any]:
    profile, txns, filings = _load(deps, payload["consent_id"])
    classification, overrides, labels = _semantic(deps, profile, txns, payload["consent_id"])
    result = compute_underwriting(
        payload["application_id"],
        profile,
        txns,
        filings,
        as_of=date.fromisoformat(payload["as_of"]),
        requested_amount_krw=payload["requested_amount_krw"],
        requested_tenor_months=payload["requested_tenor_months"],
        overrides=overrides,
        semantic_labels=labels,
    )
    as_of = date.fromisoformat(payload["as_of"])
    return {
        "features": result.features.model_dump(mode="json"),
        "signals": result.signals,
        "flags": result.flags,
        "monthly": result.monthly,
        "expense_breakdown": result.expense_breakdown,
        "expense_monthly": result.expense_monthly,
        "settlement_profile": settlement_for(txns, overrides, as_of, profile.industry),
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


def register_monitoring(deps: Deps, payload: dict[str, Any]) -> dict[str, Any]:
    """승인된 대출을 사후 모니터링 대상으로 등록한다. 기준선은 심사 시점 특성·월별 흐름에서 만든다."""
    loan_id = payload["application_id"]
    profile = deps.raw.get_json(raw_container(payload["consent_id"]), "profile.json")
    if profile is None:
        raise RuntimeError(f"원천 프로필 없음: {payload['consent_id']}")
    existing = deps.store.read(LOANS_CONTAINER, loan_id, loan_id) or {}
    deps.store.upsert(
        LOANS_CONTAINER,
        {
            "id": loan_id,
            "loan_id": loan_id,
            "application_id": loan_id,
            "applicant_id": payload["applicant_id"],
            "industry": ApplicantProfile.model_validate(profile).industry,
            "underwriting_consent_id": payload["consent_id"],
            "decision": payload["decision"],
            "amount_krw": payload["amount_krw"],
            "tenor_months": payload["tenor_months"],
            "approved_at": payload["decided_at"],
            "baseline": Baseline.from_underwriting(
                payload["underwriting"],
                installment_krw=round(monthly_installment(payload["amount_krw"], payload["tenor_months"])),
            ).to_dict(),
            "monitoring": existing.get("monitoring"),
            "updated_at": deps.clock().isoformat(),
        },
    )
    return {"loan_id": loan_id}


ACTIVITIES: dict[str, Callable[[Deps, Any], Any]] = {
    f.__name__: f
    for f in (
        save_document,
        request_consent,
        assemble_data,
        underwrite,
        evaluate_policy,
        assess_credit,
        register_monitoring,
    )
}
