"""재생 대기 항목(스펙 §6.3). 재생은 대기 항목이 모두 끝날 때까지 멈추고, 끝나면 재개 대기로 간다.

- collections_proposal: 추심 제안이 심사역 승인을 기다린다(추심 건 proposal).
- customer_response: 셀프 상환 플랜 제안이 고객 선택을 기다린다(대출 repayment.offer).
- plan_change: 고객이 고른 플랜 변경이 심사역 승인을 기다린다(대출 repayment.plan_change).
- restructure: 재조정 상담 요청이 심사역 결정을 기다린다(추심 건 restructure).
옛 문서(proposal·offer·plan_change 없음)는 해당 항목이 없는 것으로 본다.
"""

from collections.abc import Mapping, Sequence
from typing import Any

HOLD_ORDER = ("collections_proposal", "customer_response", "plan_change", "restructure")
OFFICER_HOLDS = frozenset({"collections_proposal", "plan_change", "restructure"})


def _status(doc: Mapping[str, Any] | None, key: str) -> Any:
    return ((doc or {}).get(key) or {}).get("status")


def pending_holds(loan: Mapping[str, Any], case: Mapping[str, Any] | None) -> list[str]:
    """아직 끝나지 않은 대기 항목(HOLD_ORDER 순서). case는 재생을 멈춘 추심 건(없으면 None)."""
    repayment = (loan or {}).get("repayment")
    pending = {
        "collections_proposal": _status(case, "proposal") == "pending_officer",
        "customer_response": _status(repayment, "offer") == "open",
        "plan_change": _status(repayment, "plan_change") == "pending_officer",
        "restructure": _status(case, "restructure") == "pending_officer",
    }
    return [h for h in HOLD_ORDER if pending[h]]


def held_status(holds: Sequence[str]) -> str:
    """대기 항목에 맞는 재생 상태. 심사역 항목이 하나라도 있으면 paused, 고객 응답만 남았으면 awaiting_customer."""
    if not holds:
        return "awaiting_resume"
    if any(h in OFFICER_HOLDS for h in holds):
        return "paused"
    return "awaiting_customer"
