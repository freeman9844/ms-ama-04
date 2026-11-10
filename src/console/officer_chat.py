"""심사역 결정 채팅의 콘솔 쪽: 가명 스냅샷, 입력 검증, 안내 문구.

스냅샷은 detail_view(restore=False) 결과에서 정해진 키만 고른다. 원천 Blob을 읽지 않으므로 실명·사업자번호·
거래 원문이 들어갈 길이 없고, 심사역 ID와 결정 사유 원문도 넣지 않는다.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from agents.features import ANNUAL_RATE
from policy.engine import LIMIT_UNIT_KRW, explain
from policy.rules import AMENDED_IN_PLACE, RuleSet, rules_for_version
from shared.contracts import CashFlowFeatures

SNAPSHOT_MAX_CHARS = 24_000
QUESTION_MAX = 500
HISTORY_MESSAGES = 12  # 6턴
MESSAGE_MAX = 2_000
MONTHLY_KEYS = ("month", "revenue", "operating_out", "debt_service", "owner_draw")
SETTLEMENT_KEYS = ("available", "pg_reconciliation_ratio", "pg_top_platform_share", "pg_take_rate_trend_pp",
                   "pg_cash_conversion_days", "pg_offset_rate_trend_pp", "pg_advance_share",
                   "pg_advance_suspected_count", "unmatched_count", "settlement_count", "period")
STRUCTURE_KEYS = ("amount_krw", "tenor_months", "schedule", "reduced_payment_months", "rationale")
CHAT_SUGGESTIONS = ("상환 여력이 부족한 달과 이유는?", "정책 판정과 회부 사유는?", "에이전트가 이 권고를 낸 근거는?")
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


def _capacity(monthly: Sequence[Mapping[str, Any]], installment: Any) -> dict[str, Any]:
    """콘솔 상환 여력 차트(assessment._capacity)와 같은 규칙: 매출−운영비 < 기존 상환 + 이번 대출 월 상환액."""
    new = installment if _num(installment) else 0
    short = []
    for m in monthly:
        rev, opex, debt = m.get("revenue"), m.get("operating_out"), m.get("debt_service")
        owed = (debt if _num(debt) else 0) + new
        if not (_num(rev) and _num(opex)) or rev - opex < owed:
            short.append(m.get("month"))
    return {"new_installment_krw": new, "months": len(monthly), "short_months": short,
            "assumed_annual_rate": ANNUAL_RATE}


def _settlement(pg: Any) -> dict[str, Any]:
    if not isinstance(pg, Mapping) or not pg.get("available"):
        return {"available": False}
    return {k: pg.get(k) for k in SETTLEMENT_KEYS}


def _rules(rules: RuleSet) -> dict[str, Any]:
    return {
        "version": rules.version,
        "product": rules.product.model_dump() | {"limit_unit_krw": LIMIT_UNIT_KRW},
        "knockouts": [c.model_dump() for c in rules.knockouts],
        "refers": [c.model_dump() for c in rules.refers],
        "rate_bands": [b.model_dump() for b in rules.rate_bands],
    }


def _trace(rules: RuleSet, policy: Mapping[str, Any], features: Any) -> tuple[dict[str, Any] | None, str | None]:
    """저장된 특성으로 판정을 다시 계산한다. 저장된 판정과 다르면 틀린 근거가 되므로 싣지 않는다."""
    try:
        parsed = CashFlowFeatures.model_validate_json(json.dumps(features))
    except (ValidationError, TypeError, ValueError):
        return None, TRACE_NOTES["invalid_features"]
    e = explain(parsed, rules)
    d = e.decision
    stored_codes = {r.get("code") for r in policy.get("reason_codes") or () if isinstance(r, Mapping)}
    if (d.decision.value, d.max_amount_krw, d.rate_band, {r.code for r in d.reason_codes}) != (
        policy.get("decision"), policy.get("max_amount_krw"), policy.get("rate_band"), stored_codes
    ):
        return None, TRACE_NOTES["mismatch"]
    band = asdict(e.band)
    band["bands"] = [{"band": b, "min_dscr": m} for b, m in e.band.bands]
    return {"checks": [asdict(c) for c in e.checks], "limit": asdict(e.limit), "band": band}, None


def _amended_since(version: Any, received_at: Any) -> bool:
    """판정이 지금 파일로 고치기 전 규정으로 났을 수 있으면 True(접수 시각을 모르면 보수적으로 True)."""
    amended = AMENDED_IN_PLACE.get(version) if isinstance(version, str) else None
    if amended is None:
        return False
    try:
        return datetime.fromisoformat(received_at) < amended
    except (TypeError, ValueError):
        return True


def _policy(policy: Any, features: Any, received_at: Any = None) -> dict[str, Any] | None:
    if not isinstance(policy, Mapping):
        return None
    version = policy.get("rules_version")
    rules = rules_for_version(version)
    if rules is None:
        trace, note = None, TRACE_NOTES["unknown_version"]
    elif _amended_since(version, received_at):
        rules, trace, note = None, None, TRACE_NOTES["amended_version"]
    else:
        trace, note = _trace(rules, policy, features)
    return {
        "decision": policy.get("decision"),
        "rules_version": policy.get("rules_version"),
        "max_amount_krw": policy.get("max_amount_krw"),
        "rate_band": policy.get("rate_band"),
        "reason_codes": [{k: r.get(k) for k in ("code", "message", "observed", "threshold")}
                         for r in policy.get("reason_codes") or ()],
        "rules": _rules(rules) if rules else None,
        "trace": trace,
        "trace_note": note,
    }


def _officer_checks(view: Mapping[str, Any]) -> list[dict[str, Any]]:
    checks = view.get("officer_checks")
    if not checks:
        return []
    return [{"text": c.get("text"), "evidence": dict(c.get("evidence") or ()), "supported": c.get("supported")}
            for c in checks]


def _opinion(view: Mapping[str, Any]) -> dict[str, Any] | None:
    assessment = view.get("assessment")
    if not isinstance(assessment, Mapping):
        return None
    structure = assessment.get("proposed_structure") or {}
    return {
        "recommendation": assessment.get("recommendation"),
        "summary": assessment.get("summary"),
        "review_passed": (assessment.get("review") or {}).get("passed"),
        "proposed_structure": {k: structure.get(k) for k in STRUCTURE_KEYS} if structure else None,
        "claims": [{"text": c["text"], "evidence": dict(c["evidence"])} for c in view.get("claims") or ()],
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


def build_snapshot(view: Mapping[str, Any]) -> dict[str, Any]:
    doc = view["doc"]
    uw = doc.get("underwriting") or {}
    features = uw.get("features") or {}
    monthly = [{k: m.get(k) for k in MONTHLY_KEYS} for m in uw.get("monthly") or () if isinstance(m, Mapping)]
    signals = uw.get("signals") or {}
    officer = doc.get("officer_decision")
    snapshot = {
        "application": {
            "application_id": doc.get("application_id"),
            "status": doc.get("status"),
            "requested_amount_krw": doc.get("requested_amount_krw"),
            "requested_tenor_months": doc.get("requested_tenor_months"),
            "industry": features.get("industry"),
        },
        "cashflow": {
            "monthly": monthly,
            "features": _numeric(features),
            "signals": _numeric(signals),
            "capacity": _capacity(monthly, signals.get("new_installment_krw")),
            "settlement": _settlement(uw.get("pg_ledger")),
        },
        "policy": _policy(view.get("policy"), features, doc.get("received_at")),
        "agent_opinion": _opinion(view),
        "officer_decision": {"decision": officer.get("decision"), "amount_krw": officer.get("amount_krw")}
        if isinstance(officer, Mapping) else None,
        "truncated": False,
    }
    return _fit(snapshot)


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
