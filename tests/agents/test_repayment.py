from datetime import date

from agents.repayment import (
    EARLY_REMINDER_DAYS,
    RepaymentWeek,
    build_schedule,
    early_reminder_on,
    item_amount,
    item_key,
    reminder_on,
    week_events,
)
from shared.contracts import Transaction
from shared.repayment import LENDER_DEBIT

START = date(2026, 11, 2)


def _debit(on, desc=LENDER_DEBIT, amount=-287_000, txn_id="t1"):
    return Transaction(txn_id=txn_id, booked_on=on, amount_krw=amount, balance_after_krw=0,
                       description=desc, txn_type_code="AUTO_DEBIT")


def test_build_schedule():
    led = build_schedule(START, 91, 287_000)
    assert led["installment_krw"] == 287_000
    assert [s["due_on"] for s in led["schedule"]] == ["2026-12-02", "2027-01-04", "2027-01-29"]
    assert led["schedule"][0] == {"no": 1, "due_on": "2026-12-02", "status": "scheduled", "reminded_on": None,
                                  "reminder_status": None, "txn_id": None, "case_id": None}


def test_reminder_on():
    assert reminder_on(date(2026, 12, 2)) == date(2026, 11, 29)
    assert EARLY_REMINDER_DAYS == 5 and early_reminder_on(date(2026, 12, 2)) == date(2026, 11, 27)


def test_item_key_and_amount_handle_old_and_split_items():
    old = {"no": 2, "due_on": "2026-12-02", "status": "scheduled"}
    part = {"no": 2, "part": 1, "due_on": "2026-12-02", "status": "scheduled", "amount_krw": 585_000}
    assert item_key(old) == "i2" and item_key({**old, "part": None}) == "i2" and item_key(part) == "i2p1"
    assert item_amount(old, 1_170_001) == 1_170_001 and item_amount(part, 1_170_001) == 585_000


def test_week_events_reminder_paid_missed():
    sched = build_schedule(START, 91, 287_000)["schedule"]
    assert week_events(sched, [], date(2026, 11, 23), date(2026, 11, 29)) == RepaymentWeek(("i1",), (), ())
    paid = week_events(sched, [_debit(date(2026, 12, 2))], date(2026, 11, 30), date(2026, 12, 6))
    assert paid == RepaymentWeek((), (("i1", "t1"),), ())
    others = [_debit(date(2026, 12, 1)), _debit(date(2026, 12, 2), desc="다른 상환"),
              _debit(date(2026, 12, 2), amount=287_000)]
    assert week_events(sched, others, date(2026, 11, 30), date(2026, 12, 6)) == RepaymentWeek((), (), ("i1",))


def test_week_events_skips_done_items():
    sched = build_schedule(START, 91, 287_000)["schedule"]
    sched[0].update(status="paid", reminded_on="2026-11-29")
    assert week_events(sched, [], date(2026, 11, 23), date(2026, 12, 6)) == RepaymentWeek((), (), ())


def test_week_events_returns_item_keys_for_split_parts():
    sched = [
        {"no": 2, "part": 1, "due_on": "2026-12-02", "status": "scheduled", "reminded_on": "2026-11-29",
         "amount_krw": 585_000},
        {"no": 2, "part": 2, "due_on": "2026-12-04", "status": "scheduled", "reminded_on": "2026-12-01",
         "amount_krw": 585_001},
    ]
    got = week_events(sched, [_debit(date(2026, 12, 2), amount=-585_000)], date(2026, 11, 30), date(2026, 12, 6))
    assert got == RepaymentWeek((), (("i2p1", "t1"),), ("i2p2",))


def test_week_events_same_day_items_need_separate_debits():
    sched = [
        {"no": 2, "part": 1, "due_on": "2026-12-02", "status": "scheduled", "reminded_on": "x"},
        {"no": 3, "due_on": "2026-12-02", "status": "scheduled", "reminded_on": "x"},
    ]
    got = week_events(sched, [_debit(date(2026, 12, 2))], date(2026, 11, 30), date(2026, 12, 6))
    assert got == RepaymentWeek((), (("i2p1", "t1"),), ("i3",))


def test_early_reminders_only_after_stage_one():
    sched = build_schedule(START, 91, 287_000)["schedule"]  # 12/2 상환 → 5일 전 11/27
    lo, hi = date(2026, 11, 23), date(2026, 11, 29)
    assert week_events(sched, [], lo, hi).early_reminders == ()
    assert week_events(sched, [], lo, hi, early_from=date(2026, 11, 20)).early_reminders == ("i1",)
    assert week_events(sched, [], lo, hi, early_from=date(2026, 11, 27)).early_reminders == ("i1",)
    assert week_events(sched, [], lo, hi, early_from=date(2026, 11, 28)).early_reminders == ()  # 5일 전이 지난 뒤 1단계
    sched[0]["early_reminded_on"] = "2026-11-27"
    assert week_events(sched, [], lo, hi, early_from=date(2026, 11, 20)).early_reminders == ()
    sched[0].update(early_reminded_on=None, status="paid")
    assert week_events(sched, [], lo, hi, early_from=date(2026, 11, 20)).early_reminders == ()
