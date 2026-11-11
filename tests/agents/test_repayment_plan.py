from datetime import date

from agents.ledger_monitor import LedgerBaseline
from agents.repayment import item_key
from agents.repayment_plan import (
    apply_change,
    apply_plan,
    main_platform,
    next_payout,
    payout_days,
    plan_options,
)
from shared.contracts import PgFlow, PgSettlement, Transaction
from shared.repayment import LENDER_DEBIT

# 2026-12 공휴일 없음(12/25 제외), 금요일 주간 지급
FRI = [date(2026, 11, 6), date(2026, 11, 13), date(2026, 11, 20), date(2026, 11, 27)]
SCHED = [{"no": 1, "due_on": "2026-11-02", "status": "paid"}, {"no": 2, "due_on": "2026-12-02", "status": "scheduled"},
         {"no": 3, "due_on": "2027-01-01", "status": "scheduled"}]


def opt(options, name):
    return next(o for o in options if o["option"] == name)


def _txn(txn_id, on, amount, balance, desc="입금", code="TRANSFER_IN"):
    return Transaction(txn_id=txn_id, booked_on=on, amount_krw=amount, balance_after_krw=balance,
                       description=desc, txn_type_code=code)


TXNS = (
    _txn("t0", date(2026, 11, 30), 2_000_000, 2_000_000),
    _txn("t1", date(2026, 12, 2), -1_000_000, 1_000_000, LENDER_DEBIT, "AUTO_DEBIT"),
    _txn("t2", date(2026, 12, 3), 500_000, 1_500_000),
    _txn("t3", date(2026, 12, 7), -300_000, 1_200_000, "임차료", "TRANSFER_OUT"),
    _txn("t4", date(2026, 12, 9), 100_000, 1_300_000),
)


def _move(amount=1_000_000, applied_from="2026-12-01"):
    return {"change_id": "c1", "applied_from": applied_from,
            "before": [{"key": "i2", "due_on": "2026-12-02", "amount_krw": 1_000_000}],
            "after": [{"key": "i2", "due_on": "2026-12-07", "amount_krw": amount}]}


def test_main_platform_and_payout_days():
    lb = LedgerBaseline(weekly_orders=1, return_rate=0.0, deduction_rate=0.0, weekly_settle_net=1,
                        platform_weekly_net={"naver": 900_000, "coupang": 500_000}, platform_weekly_orders={},
                        prior_advance=False)
    assert main_platform(lb) == "naver" and main_platform(None) is None

    def s(on, platform, net):
        return PgSettlement(settled_on=on, platform=platform, gross_krw=max(net, 0), fee_krw=0, net_krw=max(net, 0))

    pg = PgFlow(settlements=(s(date(2026, 11, 13), "naver", 10), s(date(2026, 11, 6), "naver", 10),
                             s(date(2026, 11, 6), "naver", 5), s(date(2026, 11, 9), "coupang", 10),
                             s(date(2026, 11, 20), "naver", 0), s(date(2026, 11, 27), "naver", 10)))
    assert payout_days(pg, "naver", date(2026, 11, 20)) == [date(2026, 11, 6), date(2026, 11, 13)]
    assert payout_days(None, "naver", date(2026, 11, 20)) == [] and payout_days(pg, None, date(2026, 11, 20)) == []


def test_next_payout_projects_median_period():
    assert next_payout(FRI, date(2026, 12, 2)) == date(2026, 12, 4)
    assert next_payout(FRI, date(2026, 12, 4)) == date(2026, 12, 11)
    assert next_payout(FRI, date(2026, 11, 10)) == date(2026, 11, 13)
    assert next_payout(FRI[:1], date(2026, 12, 2)) is None


def test_move_goes_to_business_day_after_next_payout():
    move = opt(plan_options(SCHED, 1_170_001, after=date(2026, 11, 29), flow_end=date(2026, 12, 31), payouts=FRI), "move")
    assert [(i["no"], i["due_on"], i["from_due_on"]) for i in move["items"]] == [(2, "2026-12-07", "2026-12-02")]
    assert move["items"][0]["amount_krw"] == 1_170_001
    assert move["replaces"] == ["i2"]  # 3회차는 flow_end 밖이라 그대로


def test_move_is_capped_at_ten_days():
    monthly = [date(2026, 10, 25), date(2026, 11, 25)]
    move = opt(plan_options(SCHED, 1, after=date(2026, 11, 29), flow_end=date(2027, 1, 31), payouts=monthly), "move")
    assert move["items"][0]["due_on"] == "2026-12-11"


def test_move_absent_without_payout_history():
    assert [o["option"] for o in plan_options(SCHED, 1, after=date(2026, 11, 29), flow_end=date(2026, 12, 31), payouts=[])] == ["split", "keep"]


def test_split_halves_next_installment_keeping_total():
    split = opt(plan_options(SCHED, 1_170_001, after=date(2026, 11, 29), flow_end=date(2026, 12, 31), payouts=FRI), "split")
    assert [(i["part"], i["due_on"], i["amount_krw"]) for i in split["items"]] == [(1, "2026-12-02", 585_000), (2, "2026-12-17", 585_001)]
    assert split["replaces"] == ["i2"]


def test_split_absent_when_second_part_outside_flow():
    options = plan_options(SCHED, 1, after=date(2026, 11, 29), flow_end=date(2026, 12, 10), payouts=[])
    assert options == [{"option": "keep", "items": [], "replaces": []}]


def test_apply_change_replaces_items_and_resets_reminders():
    sched = [{**s, "reminded_on": "2026-11-29", "reminder_status": "sent", "early_reminded_on": "2026-11-27",
              "txn_id": None, "case_id": None} for s in SCHED]
    split = opt(plan_options(sched, 1_170_001, after=date(2026, 11, 29), flow_end=date(2027, 1, 31), payouts=FRI), "split")
    out = apply_change(sched, split)
    assert [item_key(s) for s in out] == ["i1", "i2p1", "i2p2", "i3"]
    new = [s for s in out if s.get("part")]
    assert all(s["status"] == "scheduled" and s["reminded_on"] is None and s["early_reminded_on"] is None for s in new)
    assert [(s["due_on"], s["amount_krw"]) for s in new] == [("2026-12-02", 585_000), ("2026-12-17", 585_001)]
    assert out[0] == sched[0] and sched[1]["reminded_on"] == "2026-11-29"  # 나머지는 그대로, 원본은 바뀌지 않는다
    moved = apply_change(sched, {"items": [{"no": 3, "part": None, "due_on": "2026-11-20", "amount_krw": 1,
                                            "from_due_on": "2027-01-01"}], "replaces": ["i3"]})
    assert [s["due_on"] for s in moved] == ["2026-11-02", "2026-11-20", "2026-12-02"]


def test_apply_plan_moves_debit_and_recomputes_later_balances():
    out = apply_plan(TXNS, [_move()])
    assert [t.txn_id for t in out] == ["t0", "t2", "t3", "c1-i2", "t4"]
    by_id = {t.txn_id: t for t in out}
    assert by_id["t2"].balance_after_krw == 2_500_000 and by_id["t3"].balance_after_krw == 2_200_000
    debit = by_id["c1-i2"]
    assert (debit.booked_on, debit.amount_krw, debit.balance_after_krw) == (date(2026, 12, 7), -1_000_000, 1_200_000)
    assert debit.description == LENDER_DEBIT and debit.txn_type_code == "AUTO_DEBIT"
    assert by_id["t4"].balance_after_krw == 1_300_000


def test_apply_plan_misses_when_balance_is_short():
    out = apply_plan(TXNS, [_move(amount=3_000_000)])
    assert [t.txn_id for t in out] == ["t0", "t2", "t3", "t4"]
    assert out[-1].balance_after_krw == 2_300_000


def test_apply_plan_applies_changes_in_order():
    split = {"change_id": "c2", "applied_from": "2026-12-03",
             "before": [{"key": "i2", "due_on": "2026-12-07", "amount_krw": 1_000_000}],
             "after": [{"key": "i2p1", "due_on": "2026-12-07", "amount_krw": 500_000},
                       {"key": "i2p2", "due_on": "2026-12-09", "amount_krw": 500_000}]}
    out = apply_plan(TXNS, [_move(), split])
    assert [(t.txn_id, t.balance_after_krw) for t in out if t.booked_on >= date(2026, 12, 7)] == [
        ("t3", 2_200_000), ("c2-i2p1", 1_700_000), ("t4", 1_800_000), ("c2-i2p2", 1_300_000)]


def test_apply_plan_never_touches_txns_before_applied_from():
    out = apply_plan(TXNS, [_move(applied_from="2026-12-03")])  # 12/2 출금은 공개된 주차라 남는다
    assert out[:2] == list(TXNS[:2])
    assert [t.txn_id for t in out] == ["t0", "t1", "t2", "t3", "c1-i2", "t4"]
    assert out[-1].balance_after_krw == 300_000


def test_apply_plan_without_history_is_identity():
    assert apply_plan(TXNS, []) == list(TXNS)
