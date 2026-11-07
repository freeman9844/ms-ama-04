"""심사역 콘솔 화면 데이터. 가명 토큰 복원과 근거 값 조회를 여기서 한다.

에이전트 결과(assessment)는 가명 토큰([[KIND_hex]])이 들어간 채 저장된다. 복원 권한이 있으면
원천 데이터(Blob)로 같은 키의 가명처리기를 다시 만들어 토큰→원문 매핑을 채운 뒤 되돌린다.
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from agents.data_assembly import build_pseudonymizer
from agents.evidence import build_catalog
from shared.contracts import ApplicantProfile, Transaction

DECIDABLE = frozenset({"awaiting_officer", "escalated"})


class RawStore(Protocol):
    def get_json(self, container: str, name: str) -> Any: ...


@dataclass(frozen=True)
class RawData:
    profile: ApplicantProfile
    transactions: list[Transaction]


def raw_container(consent_id: str) -> str:
    # journey.activities.raw_container와 같아야 한다(테스트로 고정). 콘솔은 오케스트레이터 의존성을 끌어오지 않는다.
    return f"consent-{consent_id}"


def load_raw(store: RawStore, doc: Mapping[str, Any]) -> RawData | None:
    consent = doc.get("consent") or {}
    if not consent.get("consent_id"):
        return None
    container = raw_container(consent["consent_id"])
    profile = store.get_json(container, "profile.json")
    txns = store.get_json(container, "transactions.json")
    if profile is None or txns is None:
        return None
    return RawData(ApplicantProfile.model_validate(profile), [Transaction.model_validate(t) for t in txns])


def _map_strings(value: Any, fn: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, list):
        return [_map_strings(v, fn) for v in value]
    if isinstance(value, dict):
        return {k: _map_strings(v, fn) for k, v in value.items()}
    return value


def _text_functions(raw: RawData | None, key: bytes, restore: bool) -> tuple[str, Callable[[str], str], Callable[[str], str]]:
    """(pii 상태, 모델 출력 변환, 원천 텍스트 변환)."""
    if raw is None:
        return ("unavailable" if restore else "masked"), (lambda s: s), (lambda s: s)
    p = build_pseudonymizer(key, raw.profile, raw.transactions)
    if not restore:
        return "masked", (lambda s: s), p.mask
    # 토큰은 결정적(HMAC)이라 같은 원문을 다시 가명처리하면 저장된 토큰과 같은 매핑이 생긴다.
    p.mask(" ".join((raw.profile.business_name, raw.profile.representative_name, raw.profile.business_reg_no)))
    for t in raw.transactions:
        p.mask(t.description)
    return "restored", p.restore, (lambda s: s)


def _referenced(claims: Iterable[Mapping[str, Any]]) -> list[str]:
    seen: dict[str, None] = {}
    for c in claims:
        for txn_id in c.get("txn_ids", ()):
            seen.setdefault(txn_id, None)
    return list(seen)


def detail_view(doc: Mapping[str, Any], raw: RawData | None, key: bytes, *, restore: bool) -> dict[str, Any]:
    pii, model_text, raw_text = _text_functions(raw, key, restore)
    uw = doc.get("underwriting") or {}
    policy = doc.get("policy")
    catalog = (
        build_catalog(uw.get("features") or {}, uw.get("signals") or {}, uw.get("monthly") or [], policy)
        if uw and policy
        else {}
    )
    assessment = _map_strings(doc["assessment"], model_text) if doc.get("assessment") else None
    assessment_run = _map_strings(doc["assessment_run"], model_text) if doc.get("assessment_run") else None
    claims = [
        {
            "text": c["text"],
            "evidence": [(m, catalog.get(m)) for m in c.get("metric_ids", ())],
            "txn_ids": list(c.get("txn_ids", ())),
        }
        for c in (assessment or {}).get("claims", ())
    ]
    wanted = set(_referenced(claims))
    transactions = [
        {
            "txn_id": t.txn_id,
            "booked_on": t.booked_on.isoformat(),
            "amount_krw": t.amount_krw,
            "description": raw_text(t.description),
        }
        for t in (raw.transactions if raw else ())
        if t.txn_id in wanted
    ]
    return {
        "doc": doc,
        "pii": pii,
        "assessment": assessment,
        "assessment_run": assessment_run,
        "claims": claims,
        "transactions": transactions,
        "features": uw.get("features") or {},
        "policy": policy,
        "decidable": doc.get("status") in DECIDABLE,
        "max_amount_krw": (policy or {}).get("max_amount_krw"),
        "policy_blocked": (policy or {}).get("decision") not in ("eligible", "refer"),
    }
