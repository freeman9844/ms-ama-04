"""신청 여정 활동. 외부 I/O는 Deps 포트로만 한다(Azure 어댑터는 adapters.py).

활동은 (deps, payload) -> JSON 직렬화 가능한 값. function_app이 Deps를 주입한다.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol, TypeVar
from uuid import uuid4

from pydantic import TypeAdapter

from agents.classify import Category, classify
from agents.data_assembly import DEFAULT_MODEL, assemble, build_pseudonymizer
from agents.features import compute_underwriting, compute_usd_underwriting, monthly_installment
from agents.marketplace import FLAG_MARKETPLACE_UNAVAILABLE
from agents.monitoring import Baseline, settlement_for
from agents.pg_ledger import FLAG_PG_LEDGER_UNAVAILABLE
from agents.translator import TRANSLATE_MODEL
from agents.txn_classify import ClassificationCache, Decider, LlmFallback, classify_unresolved
from agents.underwriting import FLAG_ASSESSMENT_UNAVAILABLE, AgentRunner, UnderwritingContext, assess
from agents.usd_monitoring import approval_basis, usd_baseline
from journey.models import CONSENT_PURPOSE, CONSENT_SCOPES, CONSENT_TTL_DAYS, LifecycleStart, UsdLifecycleStart
from shared.contracts import ApplicantProfile, MarketplaceStore, PgSettlement, TaxFiling, Transaction
from shared.contracts import UsdProduct, UsdTransaction
from shared.product_context import product_of, validate_usd_evidence
from shared.repayment import usd_installments
from shared.usd_source import UsdMarketplace, UsdProfile, UsdSettlement, UsdTaxFiling

CONTAINERS = ("applications", "consents")
CLASSIFICATION_CONTAINER = "classifications"
ANALYST_MODEL = "gpt-5.6-sol"
REVIEWER_MODEL = "gpt-5.6-luna"
CLASSIFIER_MODEL = "gpt-5.6-luna"
EW_MODEL = "gpt-5.6-luna"
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
_PG = TypeAdapter(list[PgSettlement])
_USD_TRANSACTIONS = TypeAdapter(list[UsdTransaction])
_USD_FILINGS = TypeAdapter(list[UsdTaxFiling])
_USD_PG = TypeAdapter(list[UsdSettlement])


class DocumentStore(Protocol):
    def create_if_absent(self, container: str, document: dict[str, Any]) -> None:
        """최초 문서만 원자적으로 생성한다. 이미 있으면 변경 없이 성공한다."""
        ...

    def upsert(self, container: str, document: dict[str, Any]) -> None: ...
    def read(self, container: str, item_id: str, partition_key: str) -> dict[str, Any] | None: ...
    def prior_contact_slots(self, borrower: str, lo: str, hi: str) -> list[dict[str, str]]: ...
    def pending_raw_repairs(self) -> list[str]: ...
    def replace(self, container: str, document: dict[str, Any], etag: str | None) -> bool:
        """etag가 같을 때만 교체한다. 다른 쓰기가 끼어들었으면 False."""
        ...

    def replace_batch(self, container: str, partition_key: str, documents: list[dict[str, Any]]) -> bool:
        """One transaction: replace each observed ETag or create if absent. Never partially commit."""
        ...


class RawStore(Protocol):
    def open_container(self, container: str) -> None:
        """Create the unique namespace once, before publishing a new grant."""
        ...
    def put_json(self, container: str, name: str, value: Any) -> None:
        """Upload only to an existing namespace. Never recreate a deleted container."""
        ...
    def get_json(self, container: str, name: str) -> Any | None: ...
    def manifest(self, container: str) -> list[dict[str, Any]] | None:
        """[{name, size, sha256}]. 컨테이너가 없으면 None."""
        ...
    def delete_container(self, container: str) -> bool:
        """삭제했으면 True, 원래 없었으면 False."""
        ...


class MockApi(Protocol):
    def create_consent(self, applicant_id: str, scopes: list[str], purpose: str, ttl_days: int,
                       *, product: UsdProduct | None = None) -> dict[str, Any]: ...
    def fetch(self, source: str, token: str, params: Mapping[str, str | int] | None = None) -> Any: ...
    def revoke(self, consent_id: str) -> str:
        """Only revoked acknowledges a durable tombstone. Absent/unacknowledged require retry."""
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
    translator: Any | None = None
    translate_model: str = TRANSLATE_MODEL


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
    deps.raw.open_container(doc["raw_container"])
    if deps.queue is None:
        doc["lifecycle_error"] = "queue_disabled"
        deps.store.upsert("consents", doc)
        return
    deps.store.upsert("consents", doc)
    product = product_of(doc)
    message = (UsdLifecycleStart if product else LifecycleStart)(
        **(product.model_dump() if product else {}), consent_id=doc["consent_id"], expires_at=doc["expires_at"])
    try:
        deps.queue.send(LIFECYCLE_QUEUE, message.model_dump(mode="json", exclude_defaults=True))
    except Exception as e:
        doc["status"] = "void"
        doc["lifecycle_error"] = f"{type(e).__name__}: {e}"[:300]
        deps.store.upsert("consents", doc)
        raise


def _product(payload: Mapping[str, Any]) -> UsdProduct | None:
    fields = {k: payload[k] for k in UsdProduct.model_fields if k in payload}
    if not fields and "requested_amount_cents" not in payload:
        return None
    if "requested_amount_krw" in payload:
        raise ValueError("USD activities cannot use KRW principal")
    return UsdProduct.model_validate(fields)


def request_consent(deps: Deps, payload: dict[str, Any]) -> dict[str, Any]:
    product = _product(payload)
    meta = product.model_dump() if product else {}
    scopes = list(CONSENT_SCOPES)
    purpose = "Working capital loan assessment" if product else CONSENT_PURPOSE
    grant = deps.mock.create_consent(payload["applicant_id"], scopes, purpose, CONSENT_TTL_DAYS,
                                     **({"product": product} if product else {}))
    if product_of(grant) != product:
        raise ValueError("Origination consent product mismatch")
    consent_id = grant["consent_id"]
    doc = {
        **meta,
        "id": consent_id,
        "consent_id": consent_id,
        "application_id": payload["application_id"],
        "applicant_id": payload["applicant_id"],
        "scopes": scopes,
        "purpose": purpose,
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
            **meta,
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
    return {**meta, "consent_id": consent_id, "token": grant["token"], "scopes": scopes, "expires_at": grant["expires_at"]}


class ConsentEnded(RuntimeError):
    """데이터 조립 중 동의가 끝났다(철회·만료). 방금 쓴 원천 데이터를 지우고 여정을 멈춘다."""


def put_consent_raw(deps: Deps, cid: str, name: str, value: Any) -> None:
    container = raw_container(cid)

    def begin(doc: dict) -> None:
        if doc.get("status") != "granted":
            raise ConsentEnded(cid)
        doc["raw_write_count"] = doc.get("raw_write_count", 0) + 1
        doc["raw_repair_generation"] = doc.get("raw_repair_generation", 0) + 1

    if update_doc(deps.store, "consents", cid, cid, begin) is None:
        raise ConsentEnded(cid)
    try:
        deps.raw.put_json(container, name, value)
    except Exception:
        _drop_if_consent_ended(deps, cid, container)
        raise

    def acknowledge(doc: dict) -> bool | None:
        if doc.get("status") != "granted":
            return False
        doc["raw_write_count"] -= 1

    doc = update_doc(deps.store, "consents", cid, cid, acknowledge)
    if not doc or doc.get("status") != "granted":
        _drop_if_consent_ended(deps, cid, container)
        raise ConsentEnded(cid)


def assemble_data(deps: Deps, payload: dict[str, Any]) -> dict[str, Any]:
    consent_id = payload["consent_id"]
    product = _product(payload)
    result = assemble(
        payload["application_id"],
        scopes=payload["scopes"],
        token=payload["token"],
        api=deps.mock,
        pseudonym_key=deps.pseudonym_key,
        runner=None if product else deps.agents,
        model=deps.model,
        product=product,
    )
    container = raw_container(consent_id)
    _remember_foundry(deps, consent_id, [result.conversation_id] if result.conversation_id else [], [])
    for source, raw in result.data.items():
        put_consent_raw(deps, consent_id, f"{source}.json", raw)
    _drop_if_consent_ended(deps, consent_id, container)
    return result.report


def _drop_if_consent_ended(deps: Deps, consent_id: str, container: str) -> None:
    """쓰기 뒤에 확인한다. 삭제는 동의를 먼저 끝남으로 표시하므로, 조립 중 삭제가 끝났다면 여기서 보인다."""
    consent = deps.store.read("consents", consent_id, consent_id)
    if consent is None or consent.get("status") not in ENDED_CONSENT:
        return
    from journey.consent import start_raw_repair

    start_raw_repair(deps, consent_id, container)
    raise ConsentEnded(f"데이터 조립 중 동의가 끝났습니다: {consent_id}")


class _TargetResult(Protocol):
    conversations: list[str]


_Result = TypeVar("_Result", bound=_TargetResult)


def run_consent_agent(
    deps: Deps, consent_id: str, agent: Callable[..., _Result], ctx: Any, **settings: str,
) -> _Result:
    """Fence this delivery before I/O; a later delivery can resolve only its own fence."""
    if deps.agents is None:
        return agent(ctx, runner=None, **settings)
    run_id = uuid4().hex

    def begin(doc: dict) -> None:
        if doc.get("status") != "granted":
            raise ConsentEnded(consent_id)
        doc.setdefault("assessment_runs", []).append(run_id)

    if update_doc(deps.store, "consents", consent_id, consent_id, begin) is None:
        raise ConsentEnded(consent_id)
    complete: bool | None = None

    def checkpoint(conversations: list[str], containers: list[dict[str, Any]], enumerated: bool) -> None:
        nonlocal complete
        complete = enumerated and complete is not False
        _remember_foundry(deps, consent_id, conversations, containers, run_id=run_id, complete=complete, final=False)

    result = agent(ctx, runner=deps.agents, checkpoint=checkpoint, **settings)
    # No finally: an interrupted producer may have created unreported targets.
    _remember_foundry(deps, consent_id, result.conversations, getattr(result, "containers", []),
                      run_id=run_id, complete=complete is True)
    return result


def _remember_foundry(
    deps: Deps, consent_id: str, conversations: list[str], containers: list[dict[str, Any]], *,
    run_id: str | None = None, complete: bool = False, final: bool = True,
) -> None:
    """Register known targets; only a fully enumerated, stopped producer may release its fence."""
    if not conversations and not containers and run_id is None:
        return

    def mutate(consent: dict[str, Any]) -> bool | None:
        known = consent.setdefault("foundry_conversations", [])
        saved = consent.setdefault("foundry_containers", [])
        before = len(known) + len(saved)
        known.extend(c for c in conversations if c not in known)
        ids = {c["id"] for c in saved}
        for c in containers:
            if c["id"] not in ids:
                saved.append(c)
                ids.add(c["id"])
        runs = consent.get("assessment_runs", [])
        if run_id in runs and complete and (final or consent.get("status") in ENDED_CONSENT):
            runs.remove(run_id)
        elif before == len(known) + len(saved):
            return False
        if consent.get("status") in ENDED_CONSENT:
            notices = consent.get("notices", [])
            prior = [n for n in notices if n["kind"] == "deletion"]
            if prior:
                consent.setdefault("superseded_deletion_notices", []).extend(prior)
                consent["notices"] = [n for n in notices if n["kind"] != "deletion"]
            consent.update(foundry_repair_pending=True, deletion_complete=False,
                           foundry_generation=consent.get("foundry_generation", 0) + 1)

    doc = update_doc(deps.store, "consents", consent_id, consent_id, mutate)
    if doc is None and run_id:
        raise ConsentEnded(consent_id)
    if doc and doc.get("status") in ENDED_CONSENT:
        if doc.get("foundry_repair_pending"):
            from journey.consent import repair_foundry
            repair_foundry(deps, doc)
        if run_id:
            raise ConsentEnded(consent_id)


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


def _load_pg(deps: Deps, consent_id: str) -> tuple[list[PgSettlement], list[str]]:
    """정산 원장. 없으면 빈 목록, 손상이면 빈 목록 + 플래그(심사는 계속한다)."""
    try:
        raw = deps.raw.get_json(raw_container(consent_id), "pg.json")
        if raw is None:
            return [], []
        return _PG.validate_python(raw), []
    except ValueError:
        return [], [FLAG_PG_LEDGER_UNAVAILABLE]


def _load_marketplace(deps: Deps, consent_id: str) -> tuple[MarketplaceStore | None, list[str]]:
    """마켓 셀러 자료. 없으면 None, 손상이면 None + 플래그(심사는 계속한다)."""
    try:
        raw = deps.raw.get_json(raw_container(consent_id), "marketplace.json")
        if raw is None:
            return None, []
        return MarketplaceStore.model_validate(raw), []
    except ValueError:
        return None, [FLAG_MARKETPLACE_UNAVAILABLE]


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
    product = _product(payload)
    cid = payload["consent_id"]
    if product:
        container = raw_container(cid)
        def read(source):
            return deps.raw.get_json(container, f"{source}.json")
        profile = UsdProfile.model_validate(read("profile"))
        txns = _USD_TRANSACTIONS.validate_python(read("transactions"))
        filings = _USD_FILINGS.validate_python(read("tax") or [])
        pg = _USD_PG.validate_python(read("pg") or [])
        market = read("marketplace")
        store = None if market is None else UsdMarketplace.model_validate(market)
        pg_flags, store_flags = [], []
        classification, overrides, labels = None, {}, {}
    else:
        profile, txns, filings = _load(deps, cid)
        pg, pg_flags = _load_pg(deps, cid)
        store, store_flags = _load_marketplace(deps, cid)
        classification, overrides, labels = _semantic(deps, profile, txns, cid)
    amount_key = "requested_amount_cents" if product else "requested_amount_krw"
    result = (compute_usd_underwriting if product else compute_underwriting)(
        payload["application_id"],
        profile,
        txns,
        filings,
        as_of=date.fromisoformat(payload["as_of"]),
        **{amount_key: payload[amount_key]},
        requested_tenor_months=payload["requested_tenor_months"],
        overrides=overrides,
        semantic_labels=labels,
        pg_settlements=pg,
        marketplace=store,
    )
    as_of = date.fromisoformat(payload["as_of"])
    return {
        **(product.model_dump() if product else {}),
        "features": result.features.model_dump(mode="json"),
        "signals": result.signals,
        "flags": [*result.flags, *pg_flags, *store_flags],
        "monthly": result.monthly,
        "expense_breakdown": result.expense_breakdown,
        "expense_monthly": result.expense_monthly,
        "settlement_profile": None if product else settlement_for(txns, overrides, as_of, profile.industry),
        "classification": classification,
        "category_overrides": {k: v.value for k, v in overrides.items()},
        "pg_ledger": result.pg_ledger,
        "marketplace": result.marketplace,
    }


def evaluate_policy(deps: Deps, features: dict[str, Any]) -> dict[str, Any]:
    return deps.policy.decide(features)


def assess_credit(deps: Deps, payload: dict[str, Any]) -> dict[str, Any]:
    if deps.agents is None:
        return {"assessment": None, "flags": [FLAG_ASSESSMENT_UNAVAILABLE], "record": {"error": "agents_disabled"}}
    uw = payload["underwriting"]
    product = _product(uw)
    if product != _product(uw["features"]) or product != _product(payload["policy"]):
        raise ValueError("Mismatched assessment evidence")
    if _product(payload) not in (None, product):
        raise ValueError("Mismatched assessment request")
    if product:
        container = raw_container(payload["consent_id"])
        profile = UsdProfile.model_validate(deps.raw.get_json(container, "profile.json"))
        txns = _USD_TRANSACTIONS.validate_python(deps.raw.get_json(container, "transactions.json"))
    else:
        profile, txns, _ = _load(deps, payload["consent_id"])
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
        settlement=uw.get("pg_ledger"),
        marketplace=uw.get("marketplace"),
    )
    result = run_consent_agent(deps, payload["consent_id"], assess, ctx,
                               analyst_model=deps.analyst_model, reviewer_model=deps.reviewer_model)
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
    product = product_of(payload)
    uw = payload["underwriting"]
    if product != product_of(profile) or product != product_of(uw["features"]):
        raise ValueError("Registration sources do not match the loan product")
    if product:
        application = deps.store.read("applications", loan_id, loan_id) or {}
        decision = application.get("officer_decision") or {}
        if (application.get("status") != "approved" or product_of(application) != product
                or product_of(decision) != product or decision.get("decision") != "approve"
                or not decision.get("officer_id") or decision.get("at") != payload["decided_at"]
                or decision.get("amount_cents") != payload["amount_cents"]
                or application["applicant_id"] != payload["applicant_id"]
                or application["requested_tenor_months"] != payload["tenor_months"]
                or (application.get("consent") or {}).get("consent_id") != payload["consent_id"]):
            raise ValueError("Loan registration does not match saved officer approval")
        validate_usd_evidence(uw)
        parsed = UsdProfile.model_validate(profile)
        if (payload["decision"] != "approve" or parsed.applicant_id != payload["applicant_id"]
                or uw["features"]["application_id"] != loan_id):
            raise ValueError("Invalid approved loan registration")
        usd_installments(date.fromisoformat(payload["decided_at"][:10]), payload["amount_cents"], payload["tenor_months"])
        terms = {
            **product.model_dump(), "amount_cents": payload["amount_cents"], "baseline": usd_baseline(uw),
            "approval_basis": approval_basis((application.get("policy") or {}).get("applied_substitutions") or (),
                                             uw["features"]),
        }
    else:
        parsed = ApplicantProfile.model_validate(profile)
        terms = {"amount_krw": payload["amount_krw"], "baseline": Baseline.from_underwriting(
            uw, installment_krw=round(monthly_installment(payload["amount_krw"], payload["tenor_months"])),
        ).to_dict()}
    deps.store.create_if_absent(
        LOANS_CONTAINER,
        {
            "id": loan_id,
            "loan_id": loan_id,
            "application_id": loan_id,
            "applicant_id": payload["applicant_id"],
            "industry": parsed.industry,
            "underwriting_consent_id": payload["consent_id"],
            "decision": payload["decision"],
            **terms,
            "tenor_months": payload["tenor_months"],
            "approved_at": payload["decided_at"],
            "monitoring": None,
            "updated_at": deps.clock().isoformat(),
        },
    )
    saved = deps.store.read(LOANS_CONTAINER, loan_id, loan_id)
    if not saved or product != product_of(saved) or (product and any((
        saved["amount_cents"] != payload["amount_cents"], saved["tenor_months"] != payload["tenor_months"],
        saved["applicant_id"] != payload["applicant_id"], saved["underwriting_consent_id"] != payload["consent_id"],
        saved["approved_at"] != payload["decided_at"],
    ))):
        raise ValueError("Conflicting loan registration")
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
