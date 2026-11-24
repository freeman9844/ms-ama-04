from datetime import date
from functools import partial

from agents.repayment import (
    EARLY_REMINDER_DAYS,
    RepaymentWeek,
    build_schedule,
    early_reminder_on,
    item_amount,
    item_key,
    reminder_on,
    week_events as repayment_events,
)
from shared.contracts import Transaction
from shared.repayment import LENDER_DEBIT

START = date(2026, 11, 2)
week_events = partial(repayment_events, installment_krw=287_000, loan_id="loan-a")


def _debit(on, desc=LENDER_DEBIT, amount=-287_000, txn_id="t1"):
    return Transaction(txn_id=txn_id, booked_on=on, amount_krw=amount, balance_after_krw=0,
                       description=desc, txn_type_code="AUTO_DEBIT")


def test_build_schedule():
    led = build_schedule(START, 91, 287_000)
    assert led["installment_krw"] == 287_000
    assert [s["due_on"] for s in led["schedule"]] == ["2026-12-02", "2027-01-04", "2027-01-29"]
    assert led["schedule"][0] == {"no": 1, "due_on": "2026-12-02", "status": "scheduled", "reminded_on": None,
                                  "reminder_status": None, "txn_id": None, "case_id": None}


def test_usd_fifo_cent_and_remainder():
    from agents import repayment
    from shared.contracts import USD_V1, UsdTransaction

    assert hasattr(repayment, "usd_schedule")
    ledger = repayment.usd_schedule(START, 150001, 3)
    items = ledger["schedule"]
    assert [s["amount_cents"] for s in items] == [50000, 50000, 50001]
    due = date.fromisoformat(items[1]["due_on"])
    penny = UsdTransaction(**USD_V1.model_dump(), txn_id="penny", loan_id="A", amount_cents=-1,
                           balance_after_cents=42, booked_on=due, description="AMALend loan repayment",
                           txn_type_code="AUTO_DEBIT")
    events = partial(repayment_events, items, lo=due, hi=due, installment_cents=50000, loan_id="A")
    foreign = penny.model_copy(update={"txn_id": "other", "loan_id": "B"})
    penny = penny.model_copy(update={"description": "Scheduled payment", "scheduled_item_key": "i2"})
    allocations = events([foreign, penny, penny]).allocations
    assert list(allocations) == ["i1"]
    assert items[0]["amount_cents"] - sum(a["amount_cents"] for a in allocations["i1"]) == 49999
    items[0].update(paid_cents=1, status="partial", allocations=allocations["i1"])
    assert events([penny]).allocations == {}


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
    assert (paid.paid, paid.missed) == ((("i1", "t1"),), ())
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
    assert (got.paid, got.missed) == ((("i2p1", "t1"),), ("i2p2",))


def test_week_events_same_day_items_need_separate_debits():
    sched = [
        {"no": 2, "part": 1, "due_on": "2026-12-02", "status": "scheduled", "reminded_on": "x"},
        {"no": 3, "due_on": "2026-12-02", "status": "scheduled", "reminded_on": "x"},
    ]
    got = week_events(sched, [_debit(date(2026, 12, 2))], date(2026, 11, 30), date(2026, 12, 6))
    assert (got.paid, got.missed) == ((("i2p1", "t1"),), ("i3",))


def test_one_won_of_500k_is_partial_and_only_confirmed_loan_debits_count():
    due = date(2026, 12, 2)
    row = {"no": 1, "due_on": str(due), "status": "scheduled"}
    wrong_loan = _debit(due, txn_id="other").model_copy(update={"loan_id": "loan-b"})
    debit = _debit(due, amount=-1)
    ev = week_events([row], [wrong_loan, debit, debit], due, due, installment_krw=500_000)
    assert ev.paid == () and ev.missed == ("i1",)
    assert ev.allocations == {"i1": [{"txn_id": "t1", "booked_on": str(due), "amount_krw": 1}]}


def test_late_multiple_debits_clear_oldest_first_and_never_prepay():
    day = date(2026, 12, 10)
    legacy = {"no": 1, "due_on": "2026-11-01", "status": "paid", "txn_id": "legacy"}
    oldest = {"no": 2, "due_on": "2026-12-01", "status": "partial", "paid_krw": 1,
              "allocations": [{"txn_id": "first", "booked_on": "2026-12-01", "amount_krw": 1}]}
    second = {"no": 3, "due_on": str(day), "status": "scheduled"}
    future = {"no": 4, "due_on": "2026-12-11", "status": "scheduled"}
    earlier = _debit(date(2026, 12, 9), amount=-250_000, txn_id="z")
    later = _debit(day, amount=-800_000, txn_id="a")
    replayed_on = date(2026, 12, 8)
    txns = [later, earlier, earlier, _debit(replayed_on, txn_id="legacy"), _debit(replayed_on, txn_id="first")]
    ev = week_events([future, second, oldest, legacy], txns, date(2026, 12, 7), day, installment_krw=500_000)
    assert ev.paid == (("i2", "a"), ("i3", "a"))
    assert {k: [(a["txn_id"], a["amount_krw"]) for a in v] for k, v in ev.allocations.items()} == {
        "i2": [("z", 250_000), ("a", 249_999)], "i3": [("a", 500_000)]}


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
