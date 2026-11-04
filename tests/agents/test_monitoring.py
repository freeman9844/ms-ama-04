from datetime import date, timedelta

import pytest

from agents.classify import Category, classify
from agents.features import compute_underwriting
from agents.monitoring import (
    PEER_COHORTS,
    Baseline,
    categorize,
    detect,
    evidence_catalog,
    settlement_for,
    week_window,
    weekly_series,
)
from mockapis.demo import build_demo_cases
from mockapis.postloan import PROCEEDS_KRW, FlowScenario, generate_postloan
from shared.contracts import EVIDENCE_ID_PATTERN
from shared.segments import SELLER_INDUSTRIES

AS_OF = date(2026, 11, 2)


@pytest.fixture(scope="module")
def cafe():
    return build_demo_cases(AS_OF)["seller-dailylook-01"]


@pytest.fixture(scope="module")
def baseline(cafe):
    uw = compute_underwriting(
        "app-1", cafe.profile, cafe.transactions, cafe.tax_filings,
        as_of=AS_OF, requested_amount_krw=50_000_000, requested_tenor_months=12,
    )
    return Baseline.from_underwriting(
        {
            "features": uw.features.model_dump(mode="json"),
            "monthly": uw.monthly,
            "settlement_profile": settlement_for(cafe.transactions, {}, AS_OF, cafe.profile.industry),
        }
    )


def _rows(case, scenario, baseline, weeks):
    flow = generate_postloan(case, scenario, AS_OF, 7)
    cats = categorize(flow.transactions, {})
    return flow, weekly_series(flow.transactions, cats, flow.start, weeks, baseline)


def test_baseline_from_underwriting(baseline):
    assert baseline.avg_weekly_revenue > 0 and baseline.operating_daily_krw > 0
    assert set(baseline.seasonal_index) == set(range(1, 13))
    # 패션: 설 연휴가 낀 2월이 가장 약하고, 연말 겨울 아우터가 가장 강하다.
    assert baseline.seasonal_index[2] < 0.85 < 1.1 < baseline.seasonal_index[12]
    assert baseline.settlement and baseline.settlement["channels"]
    assert Baseline.from_dict(baseline.to_dict()) == baseline


def test_week_window_is_seven_days():
    assert week_window(AS_OF, 1) == (AS_OF, AS_OF + timedelta(days=6))
    assert week_window(AS_OF, 2)[0] == AS_OF + timedelta(days=7)


def test_categorize_applies_overrides_only_to_unclassified(cafe):
    flow = generate_postloan(cafe, FlowScenario.STACKING, AS_OF, 7)
    card = next(t for t in flow.transactions if t.txn_type_code == "CARD_SETTLE")
    hidden = next(t for t in flow.transactions if t.description == "머니무브캐피탈")
    cats = categorize(flow.transactions, {card.txn_id: "high_cost_lender", hidden.txn_id: "high_cost_lender"})
    assert cats[card.txn_id] is Category.REVENUE
    assert cats[hidden.txn_id] is Category.HIGH_COST_LENDER
    assert classify(hidden) is Category.UNCLASSIFIED_OUT


def test_weekly_series_shapes_stacking(cafe, baseline):
    flow, rows = _rows(cafe, FlowScenario.STACKING, baseline, 3)
    assert [r["week"] for r in rows] == [1, 2, 3]
    assert rows[1]["loan_proceeds"] == PROCEEDS_KRW and rows[1]["high_cost_count"] >= 1
    assert rows[0]["buffer_days"] < baseline.cash_buffer_days / 2
    last = [t for t in flow.transactions if t.booked_on <= week_window(flow.start, 3)[1]][-1]
    assert rows[2]["end_balance"] == last.balance_after_krw


def test_detector_trips_on_buffer_then_new_debt(cafe, baseline):
    _, rows = _rows(cafe, FlowScenario.STACKING, baseline, 2)
    assert "buffer_drop" in detect(rows[:1], baseline)
    assert "new_debt" in detect(rows, baseline)


def test_expected_revenue_absorbs_season_and_settlement_timing(cafe, baseline):
    # 단순 평균 대비로는 정산이 몰리지 않은 주가 0.85 아래로 떨어지지만, 기대 입금 대비로는 정상 셀러에 경보가 없다.
    _, rows = _rows(cafe, FlowScenario.STABLE, baseline, 13)
    assert any(r["revenue_ratio"] < 0.85 for r in rows)
    assert all(r["revenue_vs_expected"] >= 0.85 for r in rows)
    assert [detect(rows[:w], baseline) for w in range(1, 14)] == [[]] * 13


def test_without_profile_expected_falls_back_to_average(cafe, baseline):
    plain = Baseline.from_dict({**baseline.to_dict(), "settlement": None})
    _, rows = _rows(cafe, FlowScenario.STABLE, plain, 2)
    assert rows[0]["expected_revenue"] == plain.avg_weekly_revenue
    assert [r["revenue_vs_expected"] for r in rows] == [r["revenue_ratio"] for r in rows]


def test_stacking_inflow_drop_shows_up_within_two_months(cafe, baseline):
    _, rows = _rows(cafe, FlowScenario.STACKING, baseline, 8)
    assert "inflow_drop" in detect(rows, baseline)


def test_catalog_ids_are_evidence_ids(cafe, baseline):
    import re

    _, rows = _rows(cafe, FlowScenario.STACKING, baseline, 4)
    catalog = evidence_catalog(rows, baseline, cafe.profile.industry)
    assert catalog["W:2:loan_proceeds"] == PROCEEDS_KRW
    assert catalog["B:index:01"] == baseline.seasonal_index[1]
    assert catalog["B:cohort:index:01"] == PEER_COHORTS["패션의류"]["index"][1]
    assert all(re.fullmatch(EVIDENCE_ID_PATTERN, k) for k in catalog)
    assert not any(k.startswith("W:5:") for k in catalog)


def test_peer_cohorts_cover_every_seller_industry():
    assert set(PEER_COHORTS) == set(SELLER_INDUSTRIES)
    assert all(set(c["index"]) == set(range(1, 13)) for c in PEER_COHORTS.values())
