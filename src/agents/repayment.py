"""상환 원장(스펙 §3.3): 재생 시작 때 스케줄을 만들고 주차마다 안내·납부·미납을 판정한다.

납부는 상환일 당일 같은 적요(`LENDER_DEBIT`)의 출금 거래로만 본다(한 출금은 한 회차에만 쓴다).
회차는 `item_key`로 가리킨다. 셀프 상환 플랜으로 나뉜 회차는 `part`·`amount_krw`를 가진다(옛 회차엔 없다).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from shared.contracts import Transaction
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


@dataclass(frozen=True)
class RepaymentWeek:
    reminders: tuple[str, ...]
    paid: tuple[tuple[str, str], ...]
    missed: tuple[str, ...]
    early_reminders: tuple[str, ...] = ()


def week_events(
    schedule: Sequence[Mapping[str, Any]],
    txns: Sequence[Transaction],
    lo: date,
    hi: date,
    early_from: date | None = None,
) -> RepaymentWeek:
    """이번 주(lo~hi) 3일 전 안내·납부·미납 회차 키. early_from(조기경보 1단계 날짜)이 있으면
    그 뒤로 5일 전 안내일이 이번 주인 예정 회차를 early_reminders로 더한다."""
    reminders: list[str] = []
    early: list[str] = []
    paid: list[tuple[str, str]] = []
    missed: list[str] = []
    used: set[str] = set()
    for item in sorted(schedule, key=lambda s: s["due_on"]):
        key, due = item_key(item), date.fromisoformat(item["due_on"])
        if item.get("reminded_on") is None and lo <= reminder_on(due) <= hi:
            reminders.append(key)
        if item["status"] != "scheduled":
            continue
        early_on = early_reminder_on(due)
        if (early_from is not None and item.get("early_reminded_on") is None
                and early_from <= early_on and lo <= early_on <= hi):
            early.append(key)
        if not lo <= due <= hi:
            continue
        hit = next(
            (t for t in txns if t.booked_on == due and t.description == LENDER_DEBIT and t.amount_krw < 0
             and t.txn_id not in used),
            None,
        )
        if hit is None:
            missed.append(key)
        else:
            used.add(hit.txn_id)
            paid.append((key, hit.txn_id))
    return RepaymentWeek(tuple(reminders), tuple(paid), tuple(missed), tuple(early))
