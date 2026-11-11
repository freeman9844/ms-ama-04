"""콘솔의 사후 모니터링 화면 데이터(P5a). 경보 문장의 가명 토큰 복원과 근거 값 조회를 한다.

오케스트레이터는 심사 동의의 프로필 + 모니터링 동의의 사후 흐름(flow.json)으로 가명처리기를 만든다.
복원도 같은 입력으로 해야 같은 토큰 매핑이 나온다.
"""

from collections.abc import Mapping
from typing import Any, Protocol

from agents.ledger_monitor import LedgerBaseline, ledger_catalog
from agents.monitoring import Baseline, evidence_catalog
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
    "paused": "재조정 대기",
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
}
REPAYMENT_LABELS = {"scheduled": "예정", "paid": "납부", "missed": "미납"}
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
_SHOWN_FIELDS = ("summary", "signals", "collections", "restructure")
_CASE_FIELDS = ("recent_alert", "collections", "restructure")
_LEDGER_ROW_FIELDS = ("week", "order_gross", "returns", "held", "deductions", "settle_net")


class MonitoringStore(Protocol):
    def list_loans(self, limit: int) -> list[dict[str, Any]]: ...

    def get_loan(self, loan_id: str) -> dict[str, Any] | None: ...

    def list_alerts(self, loan_id: str, replay_id: str) -> list[dict[str, Any]]: ...


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
    loan: Mapping[str, Any], alerts: list[dict[str, Any]], raw: RawData | None, key: bytes, *, restore: bool
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
            cases.append({**doc, **text, "decidable": _pending(text["restructure"])})
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
    }


def _pending(restructure: Mapping[str, Any] | None) -> bool:
    return bool(restructure) and restructure.get("status") == "pending_officer"
