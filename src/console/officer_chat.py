"""심사역 결정 채팅의 콘솔 쪽: 가명 스냅샷, 입력 검증, 안내 문구.

스냅샷은 detail_view(restore=False) 결과에서 정해진 키만 고른다. 원천 Blob을 읽지 않으므로 실명·사업자번호·
거래 원문이 들어갈 길이 없고, 심사역 ID와 결정 사유 원문도 넣지 않는다.
"""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from agents.features import ANNUAL_RATE
from agents.officer_facts import reviewed_detail
from agents.officer_topics import TOPICS, available_topics
from policy.engine import LIMIT_UNIT_KRW, explain
from policy.rules import AMENDED_IN_PLACE, RuleSet, UsdRuleSet, is_usd_rules, rules_for_version
from shared.contracts import CashFlowFeatures, UsdCashFlowFeatures, UsdPolicyDecision
from shared.product_context import product_of, validate_usd_evidence

SNAPSHOT_MAX_CHARS = 24_000
QUESTION_MAX = 500
HISTORY_MESSAGES = 12  # 6턴
MESSAGE_MAX = 2_000
MONTHLY_KEYS = ("month", "revenue", "operating_out", "debt_service", "owner_draw")
SETTLEMENT_KEYS = ("available", "pg_reconciliation_ratio", "pg_top_platform_share", "pg_take_rate_trend_pp",
                   "pg_cash_conversion_days", "pg_offset_rate_trend_pp", "pg_advance_share",
                   "pg_advance_suspected_count", "unmatched_count", "settlement_count", "period")
# 제안 칩·오류 문구는 화면에 낼 때 번역한다(템플릿 `_()`, app의 `_chat_error`). TRACE_NOTES는 모델 입력이라 번역하지 않는다.
# 칩은 신청에 근거가 있을 때만 보이고, 누르면 서버가 정해진 근거로 바로 답한다(agents.officer_topics).
CHAT_TOPICS: dict[str, str] = {
    "policy": "정책 판정과 사유는?",
    "limit": "한도와 금리 구간 계산은?",
    "capacity": "상환 여력이 부족한 달과 이유는?",
    "opinion": "에이전트 권고의 근거는?",
    "checks": "결정 전에 확인할 사항은?",
    "market": "마켓·정산 위험 신호는?",
    "cash_only": "마켓·정산 자료 없이 보면 판정은?",
}
assert tuple(CHAT_TOPICS) == TOPICS
TRACE_NOTES = {
    "unknown_version": "적용 규정 버전을 콘솔에서 찾을 수 없어 계산 과정을 만들지 않았습니다.",
    "invalid_features": "저장된 특성 값이 불완전해 계산 과정을 만들지 않았습니다.",
    "mismatch": "다시 계산한 결과가 저장된 판정과 달라 계산 과정을 싣지 않았습니다.",
    "amended_version": "적용 규정 버전이 판정 뒤 같은 이름으로 수정되어 당시 규정과 계산 과정을 확인할 수 없습니다.",
}
CHAT_ERRORS: dict[str, tuple[int, str]] = {
    "bad_request": (400, "요청 형식이 올바르지 않습니다."),
    "not_found": (404, "신청을 찾을 수 없습니다."),
    "invalid_question": (422, "질문은 1~500자로 입력해 주세요."),
    "blocked": (422, "이 질문에는 답할 수 없습니다."),
    "throttled": (429, "요청이 많습니다. 잠시 후 다시 질문해 주세요."),
    "failed": (502, "답변을 받지 못했습니다."),
    "disabled": (503, "채팅을 쓸 수 없습니다."),
}


def _num(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _numeric(source: Any) -> dict[str, Any]:
    return {k: v for k, v in source.items() if _num(v)} if isinstance(source, Mapping) else {}


def _capacity(monthly: Sequence[Mapping[str, Any]], installment: Any, usd: bool = False) -> dict[str, Any]:
    """콘솔 상환 여력 차트(assessment._capacity)와 같은 규칙: 매출−운영비 < 기존 상환 + 이번 대출 월 상환액."""
    new = installment if _num(installment) else 0
    short = []
    for m in monthly:
        rev, opex, debt = (m.get(k + ("_cents" if usd else "")) for k in ("revenue", "operating_out", "debt_service"))
        owed = (debt if _num(debt) else 0) + new
        if not (_num(rev) and _num(opex)) or rev - opex < owed:
            short.append(m.get("month"))
    return {("new_installment_cents" if usd else "new_installment_krw"): new, "months": len(monthly), "short_months": short,
            "assumed_annual_rate": ANNUAL_RATE}


def _settlement(pg: Any) -> dict[str, Any]:
    if not isinstance(pg, Mapping) or not pg.get("available"):
        return {"available": False}
    return {k: pg.get(k) for k in SETTLEMENT_KEYS}


def _rules(rules: RuleSet) -> dict[str, Any]:
    return {
        "version": rules.version,
        "product": rules.product.model_dump() | ({} if isinstance(rules, UsdRuleSet) else {"limit_unit_krw": LIMIT_UNIT_KRW}),
        "knockouts": [c.model_dump() for c in rules.knockouts],
        "refers": [c.model_dump() for c in rules.refers],
        "rate_bands": [b.model_dump() for b in rules.rate_bands],
        "substitutions": [s.model_dump() for s in rules.substitutions],
        "limit_boost": rules.limit_boost.model_dump() if rules.limit_boost else None,
    }


def _trace(rules: RuleSet, policy: Mapping[str, Any], features: Any) -> tuple[dict[str, Any] | None, str | None]:
    """저장된 특성으로 판정을 다시 계산한다. 저장된 판정과 다르면 틀린 근거가 되므로 싣지 않는다."""
    try:
        parsed = (UsdCashFlowFeatures if isinstance(rules, UsdRuleSet) else CashFlowFeatures).model_validate_json(json.dumps(features))
    except (ValidationError, TypeError, ValueError):
        return None, TRACE_NOTES["invalid_features"]
    e = explain(parsed, rules)
    d = e.decision
    stored_codes = {r.get("code") for r in policy.get("reason_codes") or () if isinstance(r, Mapping)}
    limit = "max_amount_cents" if isinstance(rules, UsdRuleSet) else "max_amount_krw"
    if (d.decision.value, getattr(d, limit), d.rate_band, {r.code for r in d.reason_codes}) != (
        policy.get("decision"), policy.get(limit), policy.get("rate_band"), stored_codes
    ):
        return None, TRACE_NOTES["mismatch"]
    if isinstance(rules, UsdRuleSet) and (stored_cash := policy.get("cash_only")) is not None:
        cash = d.cash_only
        if (not isinstance(stored_cash, Mapping) or cash is None
                or (cash.decision.value, cash.max_amount_cents) != (stored_cash.get("decision"), stored_cash.get(limit))):
            return None, TRACE_NOTES["mismatch"]
    band = asdict(e.band)
    band["bands"] = [{"band": b, "min_dscr": m} for b, m in e.band.bands]
    return {"checks": [asdict(c) for c in e.checks], "limit": asdict(e.limit), "band": band,
            "substitutions": [asdict(s) for s in e.substitutions]}, None


def _amended_since(version: Any, received_at: Any) -> bool:
    """판정이 지금 파일로 고치기 전 규정으로 났을 수 있으면 True(접수 시각을 모르면 보수적으로 True)."""
    amended = AMENDED_IN_PLACE.get(version) if isinstance(version, str) else None
    if amended is None:
        return False
    try:
        return datetime.fromisoformat(received_at) < amended
    except (TypeError, ValueError):
        return True


def verified_policy(doc: Mapping[str, Any], policy: Any, features: Any) -> dict[str, Any] | None:
    """One policy projection for the detail panel and chat; conflicting requests cannot share evidence."""
    if not isinstance(policy, Mapping):
        return None
    product = product_of(doc)
    requested = "requested_amount_cents" if product else "requested_amount_krw"
    if isinstance(features, Mapping) and requested in features and features[requested] != doc.get(requested):
        return None
    version = policy.get("rules_version")
    if product:
        tags = product.model_dump().items()
        records = (policy, policy.get("cash_only"))
        if not is_usd_rules(version) or any(
            not (tags <= record.items()) for record in records if isinstance(record, Mapping)
        ):
            return None
    limit = "max_amount_cents" if product else "max_amount_krw"
    rules = rules_for_version(version)
    alt_data = isinstance(rules, UsdRuleSet) and bool(rules.alt_data_features)
    if rules is not None and isinstance(rules, UsdRuleSet) != (limit == "max_amount_cents"):
        raise ValueError("Policy and ruleset products differ")
    if rules is None:
        trace, note = None, TRACE_NOTES["unknown_version"]
    elif _amended_since(version, doc.get("received_at")):
        rules, trace, note = None, None, TRACE_NOTES["amended_version"]
    elif (not isinstance(features, Mapping) or requested not in features or "rate_band" not in policy
          or not isinstance(policy.get("reason_codes"), (list, tuple))):
        trace, note = None, TRACE_NOTES["invalid_features"]
    else:
        trace, note = _trace(rules, policy, features)
    cash = policy.get("cash_only")
    return {
        "decision": policy.get("decision"),
        "rules_version": policy.get("rules_version"),
        limit: policy.get(limit),
        "rate_band": policy.get("rate_band"),
        "reason_codes": [{k: r.get(k) for k in ("code", "message", "observed", "threshold")}
                         for r in policy.get("reason_codes") or ()],
        # 비교 판정. usd-v2~는 마켓뿐 아니라 대안 데이터(PG·마켓) 전체를 빼고 본 판정이다(views._policy_compare).
        "cash_only": {
            "decision": cash.get("decision"),
            limit: cash.get(limit),
            "reason_codes": [{k: r.get(k) for k in ("code", "message", "observed", "threshold")}
                             for r in cash.get("reason_codes") or () if isinstance(r, Mapping)],
            "excludes": "alt_data" if alt_data else "marketplace",
        } if isinstance(cash, Mapping) else None,
        "rules": _rules(rules) if rules else None,
        "trace": trace,
        "trace_note": "The stored evidence could not verify this policy trace." if note and limit == "max_amount_cents" else note,
    }


def policy_for_view(view: Mapping[str, Any]) -> dict[str, Any] | None:
    if "verified_policy" in view:
        return view["verified_policy"]
    doc = view["doc"]
    return verified_policy(doc, view.get("policy"), (doc.get("underwriting") or {}).get("features"))


def _officer_checks(view: Mapping[str, Any]) -> list[dict[str, Any]]:
    checks = view.get("officer_checks")
    if not checks:
        return []
    return [{"text": c.get("text"), "evidence": dict(c.get("evidence") or ()), "supported": c.get("supported")}
            for c in checks]


def _opinion(view: Mapping[str, Any]) -> dict[str, Any] | None:
    assessment = view.get("assessment")
    if not isinstance(assessment, Mapping):
        return {"state": "legacy"} if view.get("opinion_state") == "legacy" else None
    return {
        "recommendation": assessment.get("recommendation"),
        "summary": assessment.get("summary"),
        "review_passed": (assessment.get("review") or {}).get("passed"),
        "claims": [{"text": c["text"], "evidence": dict(c["evidence"]),
                    **({"txn_ids": c["txn_ids"]} if c.get("txn_ids") else {})} for c in view.get("claims") or ()],
        "counterfactuals": [{k: c.get(k) for k in ("explanation", "current", "target")}
                            for c in assessment.get("counterfactuals") or ()],
        "officer_checks": _officer_checks(view),
    }


def _size(snapshot: Mapping[str, Any]) -> int:
    return len(json.dumps(snapshot, ensure_ascii=False))


def _fit(snapshot: dict[str, Any]) -> dict[str, Any]:
    if _size(snapshot) <= SNAPSHOT_MAX_CHARS:
        return snapshot
    snapshot["truncated"] = True
    opinion = snapshot.get("agent_opinion") or {}
    for key in ("claims", "counterfactuals", "officer_checks"):
        while _size(snapshot) > SNAPSHOT_MAX_CHARS and opinion.get(key):
            opinion[key].pop()
    monthly = snapshot["cashflow"]["monthly"]
    while _size(snapshot) > SNAPSHOT_MAX_CHARS and monthly:
        monthly.pop(0)
    return snapshot


def build_snapshot(view: Mapping[str, Any], *, translate: Callable[[str], str | None] | None = None) -> dict[str, Any]:
    doc = view["doc"]
    product = product_of(doc)
    unit = "cents" if product else "krw"
    uw = doc.get("underwriting") or {}
    features = uw.get("features") or {}
    policy = view.get("policy")
    if ((features and product_of(features) != product) or (policy and product_of(policy) != product)):
        raise ValueError("Chat evidence does not match the application product")
    checked = policy_for_view(view)
    if policy is not None and checked is None:
        raise ValueError("Policy evidence does not match the application")
    keys = [k + ("_cents" if product and k != "month" else "") for k in MONTHLY_KEYS]
    if product:
        # Revalidate originals at the model boundary; the display view may accept incomplete evidence.
        UsdCashFlowFeatures.model_validate(features)
        UsdPolicyDecision.model_validate(policy)
        validate_usd_evidence((uw, policy, doc.get("officer_decision")))
        if type((uw.get("signals") or {}).get("new_installment_cents")) is not int:
            raise ValueError("Missing USD installment evidence")
        rows = uw.get("monthly")
        if features["months_of_history"] > 0 and not rows:
            raise ValueError("Missing USD monthly history")
        if not isinstance(rows, (list, tuple)) or not all(
            isinstance(m, Mapping) and m.get("month") and all(type(m.get(k)) is int for k in keys[1:]) for m in rows
        ):
            raise ValueError("Incomplete USD monthly evidence")
    monthly = [{k: m.get(k) for k in keys} for m in uw.get("monthly") or () if isinstance(m, Mapping)]
    signals = uw.get("signals") or {}
    officer = doc.get("officer_decision")
    snapshot = {
        "application": {
            **(product.model_dump() if product else {}),
            "application_id": doc.get("application_id"),
            "status": doc.get("status"),
            f"requested_amount_{unit}": doc.get(f"requested_amount_{unit}"),
            "requested_tenor_months": doc.get("requested_tenor_months"),
            "industry": features.get("industry"),
        },
        "cashflow": {
            "monthly": monthly,
            "features": _numeric(features),
            "signals": _numeric(signals),
            "capacity": _capacity(monthly, signals.get(f"new_installment_{unit}"), bool(product)),
            "settlement": _settlement(uw.get("pg_ledger")),
        },
        "policy": checked,
        "agent_opinion": _opinion(view),
        "officer_decision": {"decision": officer.get("decision"), f"amount_{unit}": officer.get(f"amount_{unit}")}
        if isinstance(officer, Mapping) else None,
        "truncated": False,
    }
    opinion = snapshot.get("agent_opinion") or {}
    for row in [*opinion.get("claims", ()), *opinion.get("officer_checks", ())]:
        original = row.get("text") or ""
        if not reviewed_detail(original, doc.get(f"requested_amount_{unit}")):
            row["text"] = ""
        elif not product and translate and (translated := translate(original)) and reviewed_detail(translated, doc.get("requested_amount_krw")):
            row["text"] = translated
    return _fit(snapshot)


def chat_topics(view: Mapping[str, Any]) -> list[tuple[str, str]]:
    """이 신청에 근거가 있는 제안 칩 (topic, 한국어 문구). 스냅샷을 만들 수 없는 건은 칩이 없다."""
    try:
        snapshot = build_snapshot(view)
    except ValueError:
        return []
    return [(t, CHAT_TOPICS[t]) for t in available_topics(snapshot)]


def validate_question(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text if 0 < len(text) <= QUESTION_MAX else None


def clean_history(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    messages = [
        {"role": m["role"], "text": m["text"].strip()[:MESSAGE_MAX]}
        for m in value
        if isinstance(m, Mapping) and m.get("role") in ("user", "assistant")
        and isinstance(m.get("text"), str) and m["text"].strip()
    ]
    return messages[-HISTORY_MESSAGES:]
