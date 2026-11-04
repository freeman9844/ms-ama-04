from datetime import date

import pytest

from agents.forecast import HORIZON_DAYS, attainment, outflow_schedule, project, summarize
from agents.monitoring import categorize
from mockapis.demo import build_demo_cases
from mockapis.sellerdata.costs import RENT_RATIO
from shared.contracts import Transaction

AS_OF = date(2026, 11, 2)


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
