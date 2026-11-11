"""콘솔의 사후 모니터링 화면 데이터(P5a). 경보 문장의 가명 토큰 복원과 근거 값 조회를 한다.

오케스트레이터는 심사 동의의 프로필 + 모니터링 동의의 사후 흐름(flow.json)으로 가명처리기를 만든다.
복원도 같은 입력으로 해야 같은 토큰 매핑이 나온다.
"""

import re
from collections.abc import Mapping
from typing import Any, Protocol

from agents.collections import SCRIPTS
from agents.ledger_monitor import LedgerBaseline, ledger_catalog
from agents.monitoring import Baseline, evidence_catalog
from agents.repayment import item_amount, item_key
from console.views import RawData, RawStore, _map_strings, _text_functions, raw_container
from shared.contracts import ApplicantProfile, Transaction

SEVERITY_LABELS = {"low": "낮음", "medium": "중간", "high": "높음"}
ACTION_LABELS = {
    "monitor": "관찰",
    "contact_borrower": "차주 연락",
    "hardship_review": "상환 곤란 검토",
    "officer_review": "심사역 확인",
}
MONITORING_LABELS = {
    "replaying": "재생 중",
    "paused": "심사역 결정 대기",
    "awaiting_customer": "고객 응답 대기",
    "awaiting_resume": "재개 확인 대기",
    "stopped": "재생 종료",
    "completed": "재생 완료",
    "failed": "재생 실패",
}
TRIGGER_LABELS = {
    "inflow_drop": "매출 급감",
    "buffer_drop": "현금 버퍼 감소",
    "volatility_spike": "변동성 급증",
    "new_debt": "신규 차입",
    "projected_shortfall": "잔고 부족 전망",
    "sales_slump": "주문 감소",
    "settlement_hold": "정산 보류",
    "return_spike": "반품 급증",
    "cost_surge": "공제 증가",
    "due_shortfall": "상환일 잔고 부족",
}
REPAYMENT_LABELS = {"scheduled": "예정", "paid": "납부", "missed": "미납"}
# 재생을 멈춘 대기 항목(2026-11-11). 이 순서대로 멈춤 배너에 보인다(journey.holds.HOLD_ORDER와 같은 순서).
HOLD_LABELS = {
    "collections_proposal": "추심 승인 대기",
    "customer_response": "고객 응답 대기",
    "plan_change": "플랜 변경 승인 대기",
    "restructure": "재조정 결정 대기",
}
# 추심 제안서의 정책 확인 항목(agents.collections.build_proposal의 checks[].id).
CHECK_LABELS = {
    "approved_script": "승인 스크립트",
    "contact_hours": "연락 시간",
    "weekly_limit": "7일 7회",
    "banned_words": "금지 표현 없음",
    "pii_tokens": "가명 토큰 없음",
    "hardship_grounds": "재조정 근거",
}
SCRIPT_LABELS = {k: v["purpose"] for k, v in SCRIPTS.items()} | {
    "balance_warning": "잔고 부족 예상 알림",
    "early_reminder": "5일 전 추가 안내",
    "self_plan_offer": "상환 플랜 제안",
}
CONTACT_STATUS_LABELS = {"sent": "발송", "deferred": "보류", "skipped": "건너뜀"}
CONTACT_REASON_LABELS = {"contact_hours": "연락 시간 밖", "weekly_limit": "7일 7회 초과", "no_options": "고를 옵션 없음"}
PROPOSAL_LABELS = {
    "pending_officer": "심사역 승인 대기",
    "approved": "승인",
    "modified": "수정 후 승인",
    "rejected": "반려",
}
PLAN_OPTION_LABELS = {"move": "상환일 이동", "split": "분할납부", "keep": "지금 일정 유지"}
OFFER_LABELS = {"open": "고객 응답 대기", "chosen": "고객 선택", "kept": "유지 선택", "expired": "만료(고객 응답 없이 계속)"}
PLAN_CHANGE_LABELS = {"pending_officer": "심사역 승인 대기", "approved": "승인", "declined": "반려"}
# 차주 화면의 셀프 플랜 상태(borrower_plan의 status). 심사역 ID·결정 사유는 차주에게 보이지 않는다.
BORROWER_PLAN_LABELS = {
    "open": "플랜을 골라 주세요",
    "reviewing": "심사역 확인 중",
    "approved": "승인(상환 일정 변경)",
    "declined": "반려(지금 일정대로 상환)",
    "kept": "지금 일정 유지",
    "expired": "만료",
}
# 차주 화면 "받은 안내"에 싣는 연락(조기경보 단계 안내·5일 전 추가 안내·심사역 승인 추심 문구).
BORROWER_NOTICE_BY = ("early_warning", "early_reminder", "officer")
# 템플릿 필터 이름 → 라벨(없는 값은 원문 그대로).
MONITORING_FILTERS = {
    "hold_label": HOLD_LABELS,
    "check_label": CHECK_LABELS,
    "script_label": SCRIPT_LABELS,
    "contact_status_label": CONTACT_STATUS_LABELS,
    "contact_reason_label": CONTACT_REASON_LABELS,
    "proposal_label": PROPOSAL_LABELS,
    "plan_option_label": PLAN_OPTION_LABELS,
    "offer_label": OFFER_LABELS,
    "plan_change_label": PLAN_CHANGE_LABELS,
    "borrower_plan_label": BORROWER_PLAN_LABELS,
}
# 조기경보 원인 라벨(2026-11-11). 경보 문서 primary_cause·secondary_causes 값(str)을 그대로 키로 쓴다.
CAUSE_LABELS = {
    "sales_slump": "매출 부진",
    "settlement_hold": "정산 보류",
    "return_spike": "반품 급증",
    "cost_surge": "비용 증가",
    "high_cost_borrowing": "고금리 차입",
}
NO_CAUSE_LABEL = "—"
NO_LEDGER_LABEL = "정산 원장 없음"
# collections·restructure는 미납 추심 전환 전에 경보 문서에 붙던 필드다(옛 문서 표시용).
_SHOWN_FIELDS = ("summary", "check_action", "signals", "collections", "restructure")
_CASE_FIELDS = ("recent_alert", "collections", "restructure")
# 제안서에서 가명 토큰을 복원할 필드. 고객 문구(text·sent_text)는 토큰이 없어 그대로 보인다.
_PROPOSAL_FIELDS = ("reason", "hardship", "evidence")
_ITEM_KEY = re.compile(r"i(\d+)(?:p(\d+))?")
_LEDGER_ROW_FIELDS = ("week", "order_gross", "returns", "held", "deductions", "settle_net")


class MonitoringStore(Protocol):
    def list_loans(self, limit: int) -> list[dict[str, Any]]: ...

    def get_loan(self, loan_id: str) -> dict[str, Any] | None: ...

    def list_alerts(self, loan_id: str, replay_id: str) -> list[dict[str, Any]]: ...

    def get_contacts(self, loan_id: str, replay_id: str) -> dict[str, Any] | None: ...


def load_monitoring_raw(store: RawStore, loan: Mapping[str, Any]) -> RawData | None:
    mon = loan.get("monitoring") or {}
    if not loan.get("underwriting_consent_id") or not mon.get("consent_id"):
        return None
    profile = store.get_json(raw_container(loan["underwriting_consent_id"]), "profile.json")
    flow = store.get_json(raw_container(mon["consent_id"]), "flow.json")
    if profile is None or flow is None:
        return None
    return RawData(ApplicantProfile.model_validate(profile), [Transaction.model_validate(t) for t in flow])


def loan_view(
    loan: Mapping[str, Any],
    alerts: list[dict[str, Any]],
    raw: RawData | None,
    key: bytes,
    *,
    restore: bool,
    contacts: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    pii, model_text, raw_text = _text_functions(raw, key, restore)
    mon = loan.get("monitoring") or {}
    weeks = mon.get("weeks") or []
    rows = [w["row"] for w in weeks]
    catalog = evidence_catalog(rows, Baseline.from_dict(loan["baseline"]), loan.get("industry", "")) if rows else {}
    lb = mon.get("ledger_baseline")
    catalog |= ledger_catalog(LedgerBaseline.from_dict(lb) if lb else None)
    ledger_rows = [{k: r[k] for k in _LEDGER_ROW_FIELDS} for r in rows if "order_gross" in r]
    ledger_note = NO_LEDGER_LABEL if weeks and not ledger_rows else None
    shown: list[dict[str, Any]] = []
    cases: list[dict[str, Any]] = []
    wanted: dict[str, None] = {}
    for doc in sorted(alerts, key=lambda d: d.get("week", 0)):
        if doc.get("kind") == "repayment_case":
            text = _map_strings({k: doc.get(k) for k in _CASE_FIELDS}, model_text)
            proposal = doc.get("proposal")
            if proposal:
                proposal = {**proposal, **_map_strings({k: proposal.get(k) for k in _PROPOSAL_FIELDS}, model_text)}
            cases.append(
                {
                    **doc,
                    **text,
                    "proposal": proposal,
                    "decidable": _pending(text["restructure"]),
                    "proposal_decidable": _pending(proposal),
                }
            )
            continue
        text = _map_strings({k: doc.get(k) for k in _SHOWN_FIELDS}, model_text)
        signals = []
        for s in text["signals"] or ():
            signals.append(
                {
                    "kind": s["kind"],
                    "text": s["text"],
                    "evidence": [(m, catalog.get(m)) for m in s.get("metric_ids", ())],
                    "txn_ids": list(s.get("txn_ids", ())),
                }
            )
            for t in s.get("txn_ids", ()):
                wanted.setdefault(t, None)
        restructure = text["restructure"]
        shown.append(
            {
                **doc,
                **text,
                "signals": signals,
                "decidable": _pending(restructure),
                "cause_label": CAUSE_LABELS.get(doc.get("primary_cause") or "", NO_CAUSE_LABEL),
                "secondary_labels": [CAUSE_LABELS[c] for c in doc.get("secondary_causes") or ()],
                "auto_contacts": _auto_contacts(mon, doc.get("alert_id"), contacts),
            }
        )
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
        "loan": loan,
        "monitoring": mon,
        "weeks": weeks,
        "alerts": shown,
        "cases": cases,
        "transactions": transactions,
        "pii": pii,
        "ledger_rows": ledger_rows,
        "ledger_note": ledger_note,
        **_plan_view(loan.get("repayment") or {}),
        "holds": sorted(mon.get("holds") or (), key=lambda h: list(HOLD_LABELS).index(h) if h in HOLD_LABELS else 99),
    }


def installment_label(item: Mapping[str, Any]) -> str:
    """회차 표시: 분할 회차는 "2-1회차"처럼."""
    part = item.get("part")
    return f"{item['no']}-{part}회차" if part else f"{item['no']}회차"


def _key_label(key: str) -> str:
    m = _ITEM_KEY.fullmatch(key)
    if not m:
        return key
    return installment_label({"no": m.group(1), "part": m.group(2)})


def _auto_contacts(
    mon: Mapping[str, Any], alert_id: str | None, contacts: Mapping[str, Any] | None
) -> list[dict[str, Any]]:
    """이 경보로 나간 단계 자동 안내(조기경보 1·2단계). 문구는 연락 기록 문서에서 ref로 찾는다."""
    escalation = mon.get("escalation") or {}
    by_ref = {c.get("ref"): c for c in (contacts or {}).get("contacts") or () if c.get("by") == "early_warning"}
    out = []
    for stage in (1, 2):
        rec = escalation.get(f"stage{stage}")
        if not rec or not alert_id or rec.get("alert_id") != alert_id:
            continue
        sent = by_ref.get(f"{mon.get('replay_id')}-s{stage}") if rec.get("status") != "skipped" else None
        out.append(
            {
                "stage": stage,
                "on": rec.get("on"),
                "status": rec.get("status"),
                "reason": rec.get("reason"),
                "script_id": (sent or {}).get("script_id") or ("balance_warning" if stage == 1 else "self_plan_offer"),
                "text": (sent or {}).get("text"),
            }
        )
    return out


def _plan_view(led: Mapping[str, Any]) -> dict[str, Any]:
    """플랜 제안·변경 요청·변경 이력(2026-11-11). 옛 문서는 셋 다 없다."""
    installment = led.get("installment_krw") or 0
    by_key = {item_key(s): s for s in led.get("schedule") or ()}
    change = led.get("plan_change")
    if change:
        before = [
            {"label": _key_label(k), "due_on": s.get("due_on") if s else None,
             "amount_krw": item_amount(s, installment) if s else None}
            for k in change.get("replaces") or ()
            for s in (by_key.get(k),)
        ]
        after = [{"label": installment_label(i), "due_on": i["due_on"], "amount_krw": i["amount_krw"]}
                 for i in change.get("items") or ()]
        change = {**change, "before": before, "after": after}
    history = [
        {
            **h,
            "before": [{**b, "label": _key_label(b["key"])} for b in h.get("before") or ()],
            "after": [{**a, "label": _key_label(a["key"])} for a in h.get("after") or ()],
        }
        for h in led.get("plan_history") or ()
    ]
    return {
        "offer": led.get("offer"),
        "plan_change": change,
        "plan_decidable": _pending(change),
        "plan_history": history,
    }


def _pending(request: Mapping[str, Any] | None) -> bool:
    """재조정 요청·추심 제안·플랜 변경 요청이 심사역 결정을 기다리는지."""
    return bool(request) and request.get("status") == "pending_officer"


def borrower_plan(loan: Mapping[str, Any], contacts: Mapping[str, Any] | None) -> dict[str, Any]:
    """차주 화면의 대출별 셀프 플랜 제안·플랜 변경 결과·받은 안내(2026-11-11)."""
    led = loan.get("repayment") or {}
    offer, change = led.get("offer"), led.get("plan_change")
    notices = sorted(
        (
            {"on": c.get("on") or "", "script_id": c.get("script_id"), "text": c.get("text"),
             "status": c.get("status"), "reason": c.get("reason")}
            for c in (contacts or {}).get("contacts") or ()
            if c.get("by") in BORROWER_NOTICE_BY
        ),
        key=lambda n: n["on"],
    )
    return {"loan_id": loan["id"], "offer": offer, "plan_change": change, "notices": notices,
            "status": _borrower_plan_status(offer, change)}


def _borrower_plan_status(offer: Mapping[str, Any] | None, change: Mapping[str, Any] | None) -> str | None:
    if not offer:
        return None
    status = offer.get("status")
    if status != "chosen":
        return status
    if change and change.get("status") in ("approved", "declined"):
        return change["status"]
    return "reviewing"
