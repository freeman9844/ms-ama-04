"""상환 원장(스펙 §3.3): 재생 시작 때 스케줄을 만들고 주차마다 안내·납부·미납을 판정한다.

확인된 출금을 출금일 현재 도래한 같은 대출 회차에 오래된 순서로 충당한다.
회차는 `item_key`로 가리킨다. 셀프 상환 플랜으로 나뉜 회차는 `part`·`amount_krw`를 가진다(옛 회차엔 없다).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from shared.contracts import Transaction
from shared.product_context import product_of
from shared.repayment import LENDER_DEBIT, REMINDER_DAYS, due_dates

EARLY_REMINDER_DAYS = 5  # 조기경보 1단계 뒤 상환일 5일 전 추가 안내


def build_schedule(start: date, days: int, installment_krw: int) -> dict[str, Any]:
    return {
        "installment_krw": installment_krw,
        "schedule": [
            {"no": i, "due_on": d.isoformat(), "status": "scheduled", "reminded_on": None,
             "reminder_status": None, "txn_id": None, "case_id": None}
            for i, d in enumerate(due_dates(start, days), start=1)
        ],
    }


def reminder_on(due: date) -> date:
    return due - timedelta(days=REMINDER_DAYS)


def early_reminder_on(due: date) -> date:
    return due - timedelta(days=EARLY_REMINDER_DAYS)


def item_key(item: Mapping[str, Any]) -> str:
    part = item.get("part")
    return f"i{item['no']}p{part}" if part else f"i{item['no']}"


def item_amount(item: Mapping[str, Any], installment_krw: int) -> int:
    return item.get("amount_krw") or installment_krw


def current_quoted_installment(loan: Mapping[str, Any], case: Mapping[str, Any]) -> Mapping[str, Any] | None:
    if (loan.get("monitoring") or {}).get("replay_id") != case["replay_id"]:
        return None
    ledger = loan.get("repayment") or {}
    item = next((s for s in ledger.get("schedule", ()) if s.get("case_id") == case["case_id"]), None)
    if not item or item["status"] == "paid":
        return None
    product = product_of(case)
    if product != product_of(loan):
        return None
    if product:
        balance = item.get("balance_cents")
        quotes = (case["proposal"]["evidence"].get("amount_cents"), case["installment"].get("amount_cents"),
                  case.get("balance_cents", balance))
        if type(balance) is not int or balance <= 0 or any(type(q) is not int or q != balance for q in quotes):
            return None
    else:
        balance = item.get("balance_krw", item_amount(item, ledger["installment_krw"]))
        quoted = case["proposal"]["evidence"]["amount_krw"]
        if not (balance > 0 and balance == quoted == case.get("balance_krw", balance)):
            return None
    return item


def repayment_debit(t: Transaction, loan_id: str | None) -> bool:
    return t.loan_id in (None, loan_id) and t.description == LENDER_DEBIT and t.amount_krw < 0


@dataclass(frozen=True)
class RepaymentWeek:
    reminders: tuple[str, ...]
    paid: tuple[tuple[str, str], ...]
    missed: tuple[str, ...]
    early_reminders: tuple[str, ...] = ()
    allocations: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


def week_events(
    schedule: Sequence[Mapping[str, Any]],
    txns: Sequence[Transaction],
    lo: date,
    hi: date,
    early_from: date | None = None,
    *,
    installment_krw: int,
    loan_id: str,
) -> RepaymentWeek:
    """이번 주(lo~hi) 3일 전 안내·납부·미납 회차 키. early_from(조기경보 1단계 날짜)이 있으면
    그 뒤로 5일 전 안내일이 이번 주인 예정 회차를 early_reminders로 더한다."""
    reminders: list[str] = []
    early: list[str] = []
    paid: list[tuple[str, str]] = []
    missed: list[str] = []
    ordered = sorted(schedule, key=lambda s: (s["due_on"], s["no"], s.get("part") or 0))
    used = {a["txn_id"] for s in schedule for a in s.get("allocations", ())}
    used.update(s["txn_id"] for s in schedule if s.get("txn_id"))
    remaining = {item_key(s): item_amount(s, installment_krw) - s.get("paid_krw", 0)
                 for s in ordered if s["status"] in ("scheduled", "partial", "missed")}
    allocations: dict[str, list[dict[str, Any]]] = {}
    for t in sorted(txns, key=lambda t: (t.booked_on, t.txn_id)):
        if not lo <= t.booked_on <= hi or t.txn_id in used or not repayment_debit(t, loan_id):
            continue
        used.add(t.txn_id)
        funds = -t.amount_krw
        for item in ordered:
            if not funds or item["due_on"] > t.booked_on.isoformat():
                break
            key = item_key(item)
            applied = min(remaining.get(key, 0), funds)
            if applied > 0:
                allocations.setdefault(key, []).append({
                    "txn_id": t.txn_id, "booked_on": t.booked_on.isoformat(), "amount_krw": applied,
                })
                remaining[key] -= applied
                funds -= applied
                if remaining[key] == 0:
                    paid.append((key, t.txn_id))
    for item in ordered:
        key, due = item_key(item), date.fromisoformat(item["due_on"])
        if item["status"] != "scheduled":
            continue
        if item.get("reminded_on") is None and lo <= reminder_on(due) <= hi:
            reminders.append(key)
        early_on = early_reminder_on(due)
        if (early_from is not None and item.get("early_reminded_on") is None
                and early_from <= early_on and lo <= early_on <= hi):
            early.append(key)
        if due <= hi and remaining[key] > 0:
            missed.append(key)
    return RepaymentWeek(tuple(reminders), tuple(paid), tuple(missed), tuple(early), allocations)
