"""심사역 콘솔 화면 데이터. 가명 토큰 복원과 근거 값 조회를 여기서 한다.

에이전트 결과(assessment)는 가명 토큰([[KIND_hex]])이 들어간 채 저장된다. 복원 권한이 있으면
원천 데이터(Blob)로 같은 키의 가명처리기를 다시 만들어 토큰→원문 매핑을 채운 뒤 되돌린다.
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from agents.data_assembly import build_pseudonymizer, case_entities
from agents.pretranslate import load_case_raw
from agents.evidence import build_catalog
from console.i18n import CATALOGS, _, label
from console.officer_chat import verified_policy
from policy.rules import UsdRuleSet, rules_for_version
from shared.contracts import ApplicantProfile, Transaction, UsdProduct, UsdTransaction
from shared.product_context import product_of, validate_usd_evidence
from shared.opinions import opinion_state, published_opinion
from shared.pseudonymize import Pseudonymizer

DECIDABLE = frozenset({"awaiting_officer", "escalated"})


class RawStore(Protocol):
    def get_json(self, container: str, name: str) -> Any: ...


@dataclass(frozen=True)
class RawData:
    profile: ApplicantProfile
    transactions: list[Transaction | UsdTransaction]


def raw_container(consent_id: str) -> str:
    # journey.activities.raw_container와 같아야 한다(테스트로 고정). 콘솔은 오케스트레이터 의존성을 끌어오지 않는다.
    return f"consent-{consent_id}"


def load_raw(store: RawStore, doc: Mapping[str, Any]) -> RawData | None:
    raw = load_case_raw("applications", doc, store, lambda *_: None)
    return RawData(*raw) if raw else None


def _map_strings(value: Any, fn: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, list):
        return [_map_strings(v, fn) for v in value]
    if isinstance(value, dict):
        return {k: _map_strings(v, fn) for k, v in value.items()}
    return value


def case_masker(raw: RawData | None, key: bytes, *, display_name: str | None = None) -> Pseudonymizer:
    entities = case_entities(raw.profile, raw.transactions) if raw else {}
    if display_name:
        entities[display_name] = "BIZNAME"
    # Officer text can contain a display name from another UI language.
    aliases = {catalog[name]: kind for name, kind in entities.items() for catalog in CATALOGS.values() if name in catalog}
    return Pseudonymizer(key, aliases | entities)


def decision_for_display(decision: Mapping[str, Any], mask: Callable[[str], str] | None) -> dict[str, Any]:
    """An auditor's copy: officer-entered text was not pseudonymized at write time."""
    shown = dict(decision)
    if mask is not None:
        for field in ("reason", "conditions"):
            if field in shown:
                shown[field] = _map_strings(shown[field], mask)
    elif shown.get("reason") or shown.get("conditions"):
        shown.update(reason=_("원천 자료가 없어 결정 사유와 조건을 가명 표시할 수 없습니다."), conditions=[])
    return shown


def _text_functions(raw: RawData | None, key: bytes, restore: bool) -> tuple[str, Callable[[str], str], Callable[[str], str]]:
    """(pii 상태, 모델 출력 변환, 원천 텍스트 변환)."""
    if raw is None:
        return ("unavailable" if restore else "masked"), (lambda s: s), (lambda s: s)
    if not restore:
        return "masked", (lambda s: s), case_masker(raw, key).mask
    p = build_pseudonymizer(key, raw.profile, raw.transactions)
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


def _usd_policy_evidence(policy: Mapping[str, Any], product: UsdProduct) -> dict[str, Any]:
    """Read only cited policy values. Never repair product tags on the original decision."""
    tags = product.model_dump()
    policy_fields = dict(policy)
    cash = policy_fields.pop("cash_only", None)
    for record in (policy_fields, cash):
        if record is None:
            continue
        if not isinstance(record, Mapping) or any(k in record and record[k] != v for k, v in tags.items()):
            raise ValueError("Stored policy contradicts the application product")
        validate_usd_evidence({k: v for k, v in record.items() if k not in tags})
    return {k: policy[k] for k in ("max_amount_cents", "reason_codes") if k in policy}


def detail_view(doc: Mapping[str, Any], raw: RawData | None, key: bytes, *, restore: bool) -> dict[str, Any]:
    pii, model_text, raw_text = _text_functions(raw, key, restore)
    uw = doc.get("underwriting") or {}
    policy = doc.get("policy")
    product = product_of(doc)
    catalog_policy = _usd_policy_evidence(policy, product) if product and policy else policy
    if not product and policy and policy.get("reason_codes") is None:
        # The lookup has no reason values to index; keep the original policy for trace verification.
        catalog_policy = {**policy, "reason_codes": []}
    catalog = (
        build_catalog(uw.get("features") or {}, uw.get("signals") or {}, uw.get("monthly") or [], catalog_policy,
                      partial_usd=bool(product))
        if uw and policy
        else {}
    )
    state = opinion_state(doc.get("assessment"))
    unit = "cents" if product else "krw"
    assessment = _map_strings(published_opinion(doc.get("assessment"), doc.get(f"requested_amount_{unit}")), model_text)
    display_doc = {k: v for k, v in doc.items() if k not in ("assessment", "assessment_run")}
    if not restore and (decision := doc.get("officer_decision")):
        display_doc["officer_decision"] = decision_for_display(decision, raw_text if raw else None)
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
            f"amount_{unit}": getattr(t, f"amount_{unit}"),
            "description": raw_text(t.description),
        }
        for t in (raw.transactions if raw else ())
        if t.txn_id in wanted
    ]
    checked = verified_policy(doc, policy, uw.get("features"))
    trace = (checked or {}).get("trace") or {}
    return {
        "doc": display_doc,
        "pii": pii,
        "opinion_state": state,
        "assessment": assessment,
        "claims": claims,
        "officer_checks": officer_checks,
        "transactions": transactions,
        "missing_transactions": bool(wanted.difference(t["txn_id"] for t in transactions)),
        "features": uw.get("features") or {},
        "policy": policy,
        "decidable": doc.get("status") in DECIDABLE,
        f"max_amount_{unit}": (policy or {}).get(f"max_amount_{unit}"),
        "policy_blocked": (policy or {}).get("decision") not in ("eligible", "refer"),
        "policy_compare": _policy_compare(policy, unit),
        "policy_change": _policy_change(policy, trace, uw.get("features") or {}) if product else None,
        "verified_policy": checked,
        "substitutions": _substitutions(trace),
    }


def _policy_compare(policy: Any, unit: str) -> dict[str, Any] | None:
    """v4 판정의 '현금흐름만 볼 때' 비교. cash_only가 null이거나 없는 판정(v3 이하)은 None.

    bank_tax는 비교에서 지운 것이 마켓 자료뿐이 아니라 대안 데이터(PG·마켓) 전체인 규정(usd-v2~)이면 True다.
    """
    cash = policy.get("cash_only") if isinstance(policy, Mapping) else None
    if not isinstance(cash, Mapping):
        return None
    rules = rules_for_version(policy.get("rules_version"))
    return {"cash": {"decision": cash.get("decision"), "max": cash.get(f"max_amount_{unit}")},
            "seller": {"decision": policy.get("decision"), "max": policy.get(f"max_amount_{unit}")},
            "bank_tax": isinstance(rules, UsdRuleSet) and bool(rules.alt_data_features)}


REASON_UNITS: dict[str, Callable[[float], str]] = {
    "pg_cash_conversion_days": lambda v: _("%(v)s일", v=f"{v:.1f}"),
    "pg_advance_suspected_count": lambda v: _("%(v)s건", v=f"{v:.0f}"),
    "pg_offset_rate_trend_pp": lambda v: f"{v:.1f}%p",
    "mkt_cancel_rate_90d": lambda v: f"{v * 100:.1f}%",
}
# KRW 판정 화면은 이 필드를 원래 값 그대로 보이므로 USD 판정에만 쓴다.
USD_REASON_UNITS: dict[str, Callable[[float], str]] = {
    "mkt_return_rate_90d": lambda v: f"{v * 100:.1f}%",
    "mkt_days_since_suspension": lambda v: _("%(v)s일", v=f"{v:.0f}"),
    "pg_reconciliation_ratio": lambda v: f"{v * 100:.1f}%",
    "tax_bank_match_ratio": lambda v: f"{v * 100:.1f}%",
    "cash_buffer_days": lambda v: _("%(v)s일", v=f"{v:.1f}"),
    "mkt_tenure_months": lambda v: _("%(v)s개월", v=f"{v:.0f}"),
    "mkt_review_count": lambda v: _("%(v)s건", v=f"{v:.0f}"),
}


def reason_text(field: str | None, value: Any, usd: bool = False) -> str | None:
    """단위가 있는 사유 지표의 표시값. 그 밖의 필드는 None이라 호출한 쪽이 기존 표시를 쓴다."""
    units = REASON_UNITS | USD_REASON_UNITS if usd else REASON_UNITS
    if field not in units or not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return units[field](value)


DECISION_RANK = {"ineligible": 0, "refer": 1, "eligible": 2}
OP_LABELS = {"lt": "%(t)s 미만", "le": "%(t)s 이하", "gt": "%(t)s 초과", "ge": "%(t)s 이상"}
POLICY_FIELD_LABELS = {
    "months_of_history": "거래 이력(개월)", "dscr": "DSCR", "tax_bank_match_ratio": "세금·계좌 일치율",
    "revenue_volatility": "매출 변동성", "cash_buffer_days": "현금 완충일수", "business_age_months": "업력(개월)",
    "high_cost_lender_count": "고비용 대출 상환 거래 수", "revenue_trend_ratio": "최근 매출 추세",
    "pg_top_platform_share": "최대 채널 비중", "pg_reconciliation_ratio": "정산금 계좌 입금 비율",
    "pg_cash_conversion_days": "현금화 일수", "pg_advance_suspected_count": "선정산 추정 건수",
    "pg_offset_rate_trend_pp": "광고비 상계율 변화(%p)", "max_amount_cents": "정책 한도",
    "mkt_tenure_months": "마켓 판매 기간", "mkt_rating": "마켓 평점", "mkt_review_count": "마켓 리뷰 수",
    "mkt_return_rate_90d": "마켓 반품률", "mkt_cancel_rate_90d": "마켓 취소율",
    "mkt_days_since_suspension": "판매정지 후 경과일",
}
# 대체 규칙이 왜 원래 사유를 덮을 수 있는지 심사역에게 풀어 쓴다. 기준 숫자는 조건 행이 보여 준다.
SUBSTITUTION_NOTE_LABELS = {
    "S001": "계좌 거래 이력은 짧지만, 오랜 마켓 판매 기간과 좋은 평판이 실제 영업 기간을 보여 줍니다. "
            "이력이 짧은 위험이 남아 있어 탈락 대신 심사역 검토로 넘깁니다.",
    "S002": "사업자 업력은 짧지만, 마켓 판매 기간과 평점이 그 전부터 이어진 실제 영업을 보여 주므로 이 사유를 지웁니다.",
    "S003": "계좌 잔고 여유는 적지만, 정산금이 빠짐없이 계좌로 들어오고 빨리 현금이 되어 "
            "상환 재원이 자주 채워집니다. 잔고가 얇아도 단기 유동성이 끊기지 않는다고 보고 이 사유를 지웁니다.",
    "S004": "월 매출 변동은 크지만, 긴 판매 기간과 많은 리뷰·높은 평점이 수요가 꾸준하다는 것을 보여 줍니다. "
            "변동을 계절 요인으로 보고 이 사유를 지웁니다.",
    "S005": "세금 신고 매출이 계좌 입금보다 적지만, 정산 원장과 계좌 입금이 맞아 실제 매출은 확인됩니다. "
            "신고 누락 위험이 남아 있어 탈락 대신 심사역 검토로 넘깁니다.",
}


def _condition(field: Any, op: Any, observed: Any, threshold: Any) -> dict[str, Any]:
    return {"field": field, "label": label(POLICY_FIELD_LABELS, field), "op": OP_LABELS.get(op),
            "observed": observed, "threshold": threshold}


def _substitutions(trace: Mapping[str, Any]) -> list[dict[str, Any]]:
    """대상 규칙에 걸린 셀러 데이터 대체(S0xx)만 고른다. 계산 과정을 만들 수 없으면 빈 목록."""
    checks = {c.get("code"): c for c in trace.get("checks") or () if isinstance(c, Mapping)}
    rows = []
    for s in trace.get("substitutions") or ():
        if not s.get("target_hit"):
            continue
        state = "applied" if s.get("applied") else ("blocked" if s.get("conditions_met") else "unmet")
        rows.append({"code": s.get("code"), "replaces": s.get("replaces"), "state": state,
                     "blocked_by": list(s.get("blocked_by") or ()), "message": s.get("message"),
                     "outcome": s.get("outcome"), "note": SUBSTITUTION_NOTE_LABELS.get(s.get("code")),
                     "replaces_message": (checks.get(s.get("replaces")) or {}).get("message"),
                     "conditions": [_condition(c.get("field"), c.get("op"), c.get("observed"), c.get("threshold"))
                                    | {"met": c.get("result") == "hit"} for c in s.get("conditions") or ()]})
    return rows


def _policy_change(policy: Any, trace: Mapping[str, Any], features: Mapping[str, Any]) -> dict[str, Any] | None:
    """usd-v2~ 판정에서 '은행·세무 기록만 볼 때'와 최종 판정이 왜 다른지 사유 코드별로 풀어 쓴다.

    저장된 두 판정의 사유 코드와 적용된 대체만으로 만든다. 한도 설명은 다시 계산한 과정(trace)이 있을 때만 붙인다.
    """
    compare = _policy_compare(policy, "cents")
    if not compare or not compare["bank_tax"]:
        return None
    rules = rules_for_version(policy.get("rules_version"))
    cash = policy["cash_only"]
    ops = {c.code: c.op for c in (*rules.knockouts, *rules.refers)} | {"R900": "lt", "R901": "lt"}
    subs = {s.code: s for s in rules.substitutions}
    applied = {subs[c].replaces: subs[c] for c in policy.get("applied_substitutions") or () if c in subs}
    final = [r for r in policy.get("reason_codes") or () if isinstance(r, Mapping)]
    final_codes = {r.get("code") for r in final}

    def step(r: Mapping[str, Any], kind: str, by: str | None = None) -> dict[str, Any]:
        return {"code": r.get("code"), "message": r.get("message"), "kind": kind, "by": by,
                **_condition(r.get("field"), ops.get(r.get("code")), r.get("observed"), r.get("threshold"))}

    steps = []
    for r in cash.get("reason_codes") or ():
        if not isinstance(r, Mapping):
            continue
        sub = applied.get(r.get("code"))
        if r.get("code") in final_codes:
            steps.append(step(r, "kept"))
        elif sub:
            steps.append(step(r, "softened" if sub.outcome == "refer" else "cleared", sub.code))
        else:
            steps.append(step(r, "resolved"))
    cash_codes = {s["code"] for s in steps}
    steps += [step(r, "added") for r in final if r.get("code") not in cash_codes and r.get("code") not in subs]

    before, after = cash.get("decision"), policy.get("decision")
    low, high = cash.get("max_amount_cents"), policy.get("max_amount_cents")
    if before != after:
        headline = "up" if DECISION_RANK.get(after, -1) > DECISION_RANK.get(before, -1) else "down"
    elif isinstance(low, int) and isinstance(high, int) and low != high:
        headline = "limit_up" if high > low else "limit_down"
    else:
        headline = "reasons" if any(s["kind"] != "kept" for s in steps) else "same"

    notes = []
    limit = (trace or {}).get("limit") or {}
    if (limit and isinstance(low, int) and isinstance(high, int) and high > low
            and (limit.get("settlement_cap_cents") or 0) > (limit.get("inflow_cap_cents") or 0)):
        notes.append({"kind": "settlement", "avg": limit.get("avg_monthly_inflow_cents"),
                      "multiplier": f"{limit.get('inflow_multiplier'):g}", "inflow": limit.get("inflow_cap_cents"),
                      "pending": features.get("mkt_pending_settlement_cents"),
                      "ratio": f"{limit.get('pending_settlement_ratio') * 100:g}%",
                      "settlement": limit.get("settlement_cap_cents")})
    if limit.get("substituted_cap_cents") is not None and after != "ineligible":
        notes.append({"kind": "substituted", "cap": limit["substituted_cap_cents"]})
    return {"headline": headline, "before": before, "after": after, "low": low, "high": high,
            "steps": steps, "limit_notes": notes}
