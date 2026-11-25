from datetime import date

import pytest

from agents.forecast import HORIZON_DAYS, attainment, outflow_schedule, project, summarize
from agents.monitoring import categorize
from mockapis.demo import build_demo_cases
from mockapis.sellerdata.costs import RENT_RATIO
from shared.contracts import Transaction

AS_OF = date(2026, 11, 2)


def test_usd_forecast_does_not_repeat_initial_stock_purchase_or_surplus_sweeps():
    from agents.forecast import project_usd
    from mockapis.demo import FlowStore

    start = date(2026, 11, 24)
    store = FlowStore(build_demo_cases(start), start)
    flow = store.usd_flow("seller-bakery-20", "loan-bakery", 500000, 83333, 6)
    first_week = [t for t in flow.transactions if t.booked_on <= date(2026, 11, 30)]
    due = date(2026, 12, 24)
    path = project_usd(first_week, start, date(2026, 11, 30), {due: 83333})["path"]
    # Its ordinary business covers the payment. The opening inventory purchase happened once;
    # a transfer of surplus to the owner is not a fixed bill that can overdraw this account.
    assert min(p["balance_cents"] for p in path) > 0
    payment = next(p for p in path if p["date"] == due.isoformat())
    assert payment["before_payment_cents"] - payment["balance_cents"] == 83333


def _usd_history(hi):
    from shared.contracts import USD_V1, UsdTransaction

    return [UsdTransaction(**USD_V1.model_dump(), txn_id="c1", booked_on=hi, amount_cents=700,
                           balance_after_cents=10000, description="Card sales", txn_type_code="CARD_SETTLE")]


def test_project_usd_without_schedule_is_unchanged():
    from agents.forecast import project_usd

    hi = date(2026, 11, 27)
    visible = _usd_history(hi)
    dues = {date(2026, 12, 4): 5000}
    assert project_usd(visible, date(2026, 11, 2), hi, dues) == project_usd(visible, date(2026, 11, 2), hi, dues, None)


def test_scheduled_payouts_replace_trailing_average_until_known_day():
    from agents.forecast import project_usd

    hi, start = date(2026, 11, 27), date(2026, 11, 2)
    visible = _usd_history(hi)  # no Naver payouts in the last 4 weeks: they are held
    known = {date(2026, 11, 30): 1000, date(2026, 12, 1): 1000, date(2026, 12, 2): 1000}
    base = project_usd(visible, start, hi, {})["path"]
    planned = project_usd(visible, start, hi, {}, {"naver_smart_store": (date(2026, 12, 2), known)})["path"]
    gain = {b["date"]: p["before_payment_cents"] - b["before_payment_cents"] for b, p in zip(base, planned)}
    assert gain["2026-11-30"] == 1000 and gain["2026-12-01"] == 2000 and gain["2026-12-02"] == 3000
    assert gain["2026-12-03"] == 3000 and gain["2026-12-10"] == 3000  # the platform's own average (0) after the known day


def test_scheduled_platform_deposits_leave_the_general_average_and_set_its_own():
    from agents.forecast import project_usd
    from shared.contracts import USD_V1, UsdTransaction

    hi, start = date(2026, 11, 27), date(2026, 11, 2)  # 20 business days in the 4-week window
    naver = UsdTransaction(**USD_V1.model_dump(), txn_id="n1", booked_on=date(2026, 11, 10), amount_cents=4000,
                           platform_code="naver_smart_store", balance_after_cents=9000, description="Naver payout",
                           txn_type_code="PG_SETTLE")
    visible = [naver, *_usd_history(hi)]
    known = {date(2026, 11, 30): 1000, date(2026, 12, 1): 1000, date(2026, 12, 2): 1000}
    path = project_usd(visible, start, hi, {}, {"naver_smart_store": (date(2026, 12, 2), known)})["path"]
    balance = {p["date"]: p["balance_cents"] for p in path}
    # General average 700 // 20 = 35 a day; Naver's own average 4000 // 20 = 200 a day only after the known day.
    assert balance["2026-11-30"] == 10000 + 35 + 1000
    assert balance["2026-12-02"] == 10000 + 3 * (35 + 1000)
    assert balance["2026-12-03"] - balance["2026-12-02"] == 35 + 200
    assert balance["2026-12-04"] - balance["2026-12-03"] == 35 + 200
    assert balance["2026-12-06"] == balance["2026-12-04"]  # weekend: no payouts


def test_scheduled_payouts_are_discounted_by_reconciliation():
    from agents.usd_monitoring import _scheduled
    from shared.contracts import USD_V1
    from shared.usd_source import UsdPgFlow, UsdSettlement

    hi = date(2026, 11, 27)  # Friday; Naver pays within 5 business days -> known through 2026-12-04

    def paid(on, net, released=0):
        return UsdSettlement(**USD_V1.model_dump(), settled_on=on, platform_code="naver_smart_store",
                             gross_cents=net - released, fee_cents=0, released_cents=released, net_cents=net)

    pg = UsdPgFlow(**USD_V1.model_dump(), orders=(), settlements=(
        paid(date(2026, 11, 27), 9000),  # already settled by hi
        paid(date(2026, 12, 1), 1000),
        paid(date(2026, 12, 2), 1000, released=500),  # a hold release is not a recurring payout
        paid(date(2026, 12, 5), 1000),  # Saturday: rolls to Monday, past the known day
        paid(date(2026, 12, 8), 1000),  # past the known day
    ))
    assert _scheduled(pg, hi, .4) == {"naver_smart_store": (date(2026, 12, 4), {date(2026, 12, 1): 400})}
    assert _scheduled(pg, hi, None) == {"naver_smart_store": (date(2026, 12, 4), {date(2026, 12, 1): 1000})}


@pytest.fixture(scope="module")
def cafe():
    return build_demo_cases(AS_OF)["seller-dailylook-01"]


@pytest.fixture(scope="module")
def schedule(cafe):
    return outflow_schedule(cafe.transactions, categorize(cafe.transactions, {}), AS_OF)


def test_schedule_reads_recurring_outflows_from_history(cafe, schedule):
    items = {i["key"]: i for i in schedule["items"]}
    assert {"rent", "payroll", "social_insurance", "loan_repayment", "owner_transfer", "withholding", "vat"} <= set(items)
    rent = items["rent"]
    assert 5 <= rent["day"] <= 7 and rent["amount_krw"] == round(cafe.persona.monthly_sales_krw * RENT_RATIO, -4)
    assert items["vat"]["months"] == [1, 4, 7, 10] and items["vat"]["day"] == 25
    # 매입·택배·광고·카드대금은 매출에 비례하는 운영비로 둔다.
    assert 0.3 < schedule["variable_ratio"] < 0.9


def test_schedule_needs_two_complete_months(cafe):
    recent = [t for t in cafe.transactions if t.booked_on >= date(2026, 9, 20)]
    assert outflow_schedule(recent, categorize(recent, {}), AS_OF) is None


def _profile(schedule):
    channel = {"payer": "네이버파이낸셜", "cadence": "business_daily", "anchors": [], "payouts_per_month": 21,
               "monthly_avg_krw": 2_100_000, "per_payout_krw": 100_000}
    return {"channels": [channel], "seasonal_index": {f"{m:02d}": 1.0 for m in range(1, 13)}, "own_months": [],
            "outflows": schedule}


def _row(end_balance, revenue=700_000, expected=700_000):
    return {"week": 1, "start": "2026-11-02", "end": "2026-11-08", "end_balance": end_balance,
            "revenue": revenue, "expected_revenue": expected}


def test_projection_adds_installment_and_repeats_high_cost_weekly():
    schedule = {"items": [], "variable_ratio": 0.0}
    hc = Transaction(txn_id="h1", booked_on=date(2026, 11, 6), amount_krw=-70_000, balance_after_krw=0,
                     description="한빛대부 상환", txn_type_code="AUTO_DEBIT")
    out = project(_profile(schedule), [_row(1_000_000)], [hc], 287_000)
    assert len(out["path"]) == HORIZON_DAYS and out["path"][0]["date"] == "2026-11-09"
    assert out["totals"]["installment"] == -287_000  # 11월 16일(15일이 일요일이라 다음 영업일) 한 번
    assert out["totals"]["high_cost_lender"] == -70_000 * 4  # 11/13·20·27·12/4
    assert out["path"][-1]["balance"] == 1_000_000 + out["totals"]["inflow"] - 287_000 - 280_000


def test_projection_uses_given_installment_dates():
    schedule = {"items": [], "variable_ratio": 0.0}
    inside = project(_profile(schedule), [_row(1_000_000)], [], 287_000, installment_due=[date(2026, 11, 20)])
    assert inside["totals"]["installment"] == -287_000  # 15일 규칙이면 11/16
    outside = project(_profile(schedule), [_row(1_000_000)], [], 287_000, installment_due=[date(2027, 3, 2)])
    assert "installment" not in outside["totals"]


def test_projection_discounts_inflow_by_recent_attainment():
    schedule = {"items": [], "variable_ratio": 0.5}
    full = project(_profile(schedule), [_row(0)], [], 0)
    half = project(_profile(schedule), [_row(0, revenue=350_000)], [], 0)
    assert half["attainment"] == 0.5 and attainment([_row(0, revenue=900_000)]) == 1.0
    assert half["totals"]["inflow"] == pytest.approx(full["totals"]["inflow"] / 2, abs=50)
    assert half["totals"]["variable"] == pytest.approx(-half["totals"]["inflow"] / 2, abs=50)


def test_summary_reports_first_negative_day():
    schedule = {"items": [{"key": "rent", "day": 20, "amount_krw": 5_000_000}], "variable_ratio": 0.0}
    s = summarize(project(_profile(schedule), [_row(1_000_000)], [], 0))
    assert s["shortfall_date"] == "2026-11-20" and s["projected_min_balance"] < 0
    assert summarize(None) == {}


def test_no_projection_without_schedule():
    assert project(_profile(None), [_row(1)], [], 0) is None
    assert project(None, [_row(1)], [], 0) is None
