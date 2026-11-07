from datetime import date

from agents.repayment import RepaymentWeek, build_schedule, reminder_on, week_events
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


def test_week_events_reminder_paid_missed():
    sched = build_schedule(START, 91, 287_000)["schedule"]
    assert week_events(sched, [], date(2026, 11, 23), date(2026, 11, 29)) == RepaymentWeek((1,), (), ())
    paid = week_events(sched, [_debit(date(2026, 12, 2))], date(2026, 11, 30), date(2026, 12, 6))
    assert paid == RepaymentWeek((), ((1, "t1"),), ())
    others = [_debit(date(2026, 12, 1)), _debit(date(2026, 12, 2), desc="다른 상환"),
              _debit(date(2026, 12, 2), amount=287_000)]
    assert week_events(sched, others, date(2026, 11, 30), date(2026, 12, 6)) == RepaymentWeek((), (), (1,))


def test_week_events_skips_done_items():
    sched = build_schedule(START, 91, 287_000)["schedule"]
    sched[0].update(status="paid", reminded_on="2026-11-29")
    assert week_events(sched, [], date(2026, 11, 23), date(2026, 12, 6)) == RepaymentWeek((), (), ())
