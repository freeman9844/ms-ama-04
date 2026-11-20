"""심사역 콘솔 화면 데이터. 가명 토큰 복원과 근거 값 조회를 여기서 한다.

에이전트 결과(assessment)는 가명 토큰([[KIND_hex]])이 들어간 채 저장된다. 복원 권한이 있으면
원천 데이터(Blob)로 같은 키의 가명처리기를 다시 만들어 토큰→원문 매핑을 채운 뒤 되돌린다.
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from agents.data_assembly import build_pseudonymizer
from agents.evidence import build_catalog
from console.i18n import _
from console.officer_chat import policy_trace
from shared.contracts import ApplicantProfile, Transaction
from shared.opinions import opinion_state, published_opinion

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
    state = opinion_state(doc.get("assessment"))
    assessment = _map_strings(published_opinion(doc.get("assessment"), doc.get("requested_amount_krw")), model_text)
    display_doc = {k: v for k, v in doc.items() if k not in ("assessment", "assessment_run")}
    if (error := doc.get("error")) and error.get("stage") in ("assess", "assess_credit"):
        display_doc["error"] = {"stage": error["stage"], "message": _("의견 없음")}
    claims = [
        {
            "text": c["text"],
            "evidence": [(m, catalog.get(m)) for m in c.get("metric_ids", ())],
            "txn_ids": list(c.get("txn_ids", ())),
        }
        for c in (assessment or {}).get("claims", ())
    ]
    officer_checks = None
    if assessment is not None and "officer_checks" in assessment:
        officer_checks = [
            {
                "text": c.get("text", ""),
                "evidence": [(m, catalog.get(m)) for m in c.get("metric_ids", ())],
                "supported": c.get("supported"),
            }
            for c in assessment["officer_checks"] or ()
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
        "doc": display_doc,
        "pii": pii,
        "opinion_state": state,
        "assessment": assessment,
        "claims": claims,
        "officer_checks": officer_checks,
        "transactions": transactions,
        "features": uw.get("features") or {},
        "policy": policy,
        "decidable": doc.get("status") in DECIDABLE,
        "max_amount_krw": (policy or {}).get("max_amount_krw"),
        "policy_blocked": (policy or {}).get("decision") not in ("eligible", "refer"),
        "policy_compare": _policy_compare(policy),
        "substitutions": _substitutions(policy, uw.get("features"), doc.get("received_at")),
    }


def _policy_compare(policy: Any) -> dict[str, Any] | None:
    """v4 판정의 '현금흐름만 볼 때' 비교. cash_only가 null이거나 없는 판정(v3 이하)은 None."""
    cash = policy.get("cash_only") if isinstance(policy, Mapping) else None
    if not isinstance(cash, Mapping):
        return None
    return {"cash": {"decision": cash.get("decision"), "max": cash.get("max_amount_krw")},
            "seller": {"decision": policy.get("decision"), "max": policy.get("max_amount_krw")}}


def _substitutions(policy: Any, features: Any, received_at: Any) -> list[dict[str, Any]]:
    """대상 규칙에 걸린 셀러 데이터 대체(S0xx)만 고른다. 계산 과정을 만들 수 없으면 빈 목록."""
    trace = (policy_trace(policy, features, received_at) or {}).get("trace") or {}
    rows = []
    for s in trace.get("substitutions") or ():
        if not s.get("target_hit"):
            continue
        state = "applied" if s.get("applied") else ("blocked" if s.get("conditions_met") else "unmet")
        rows.append({"code": s.get("code"), "replaces": s.get("replaces"), "state": state,
                     "blocked_by": list(s.get("blocked_by") or ())})
    return rows
