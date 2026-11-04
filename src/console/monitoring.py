"""콘솔의 사후 모니터링 화면 데이터(P5a). 경보 문장의 가명 토큰 복원과 근거 값 조회를 한다.

오케스트레이터는 심사 동의의 프로필 + 모니터링 동의의 사후 흐름(flow.json)으로 가명처리기를 만든다.
복원도 같은 입력으로 해야 같은 토큰 매핑이 나온다.
"""

from collections.abc import Mapping
from typing import Any, Protocol

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
}
_SHOWN_FIELDS = ("summary", "signals", "collections", "restructure")


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
    shown: list[dict[str, Any]] = []
    wanted: dict[str, None] = {}
    for doc in sorted(alerts, key=lambda d: d.get("week", 0)):
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
                "decidable": bool(restructure) and restructure.get("status") == "pending_officer",
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
        "transactions": transactions,
        "pii": pii,
    }
