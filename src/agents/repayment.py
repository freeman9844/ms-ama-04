"""상환 원장(스펙 §3.3): 재생 시작 때 스케줄을 만들고 주차마다 안내·납부·미납을 판정한다.

납부는 상환일 당일 같은 적요(`LENDER_DEBIT`)의 출금 거래로만 본다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from shared.contracts import Transaction
from shared.repayment import LENDER_DEBIT, REMINDER_DAYS, due_dates


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


@dataclass(frozen=True)
class RepaymentWeek:
    reminders: tuple[int, ...]
    paid: tuple[tuple[int, str], ...]
    missed: tuple[int, ...]


def week_events(schedule: Sequence[dict[str, Any]], txns: Sequence[Transaction], lo: date, hi: date) -> RepaymentWeek:
    reminders: list[int] = []
    paid: list[tuple[int, str]] = []
    missed: list[int] = []
    for item in schedule:
        due = date.fromisoformat(item["due_on"])
        if item["reminded_on"] is None and lo <= reminder_on(due) <= hi:
            reminders.append(item["no"])
        if item["status"] != "scheduled" or not lo <= due <= hi:
            continue
        hit = next(
            (t for t in txns if t.booked_on == due and t.description == LENDER_DEBIT and t.amount_krw < 0), None
        )
        if hit is None:
            missed.append(item["no"])
        else:
            paid.append((item["no"], hit.txn_id))
    return RepaymentWeek(tuple(reminders), tuple(paid), tuple(missed))
