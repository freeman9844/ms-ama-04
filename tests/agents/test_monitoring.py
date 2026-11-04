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
from mockapis.demo import STACKING_APPLICANTS, build_demo_cases
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
        },
        installment_krw=287_000,
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


def _demo_series(case, scenario, as_of):
    uw = compute_underwriting(
        "app-1", case.profile, case.transactions, case.tax_filings,
        as_of=as_of, requested_amount_krw=5_000_000, requested_tenor_months=6,
    )
    base = Baseline.from_underwriting(
        {
            "features": uw.features.model_dump(mode="json"),
            "monthly": uw.monthly,
            "settlement_profile": settlement_for(case.transactions, {}, as_of, case.profile.industry),
        },
        installment_krw=900_000,
    )
    flow = generate_postloan(case, scenario, as_of, 7)
    rows = weekly_series(flow.transactions, categorize(flow.transactions, {}), flow.start, 13, base)
    return flow, rows, base


@pytest.mark.parametrize("as_of", [date(2026, 9, 14), date(2026, 12, 28), date(2027, 1, 25)])
def test_stable_sellers_raise_no_revenue_triggers_around_festivals(as_of):
    # 라이브 데모는 오늘 날짜로 흐름을 만든다: 추석·연말·설이 낀 13주에도 데모 셀러 모두 매출 트리거가 없어야 한다.
    for seller, case in build_demo_cases(as_of).items():
        _, rows, base = _demo_series(case, FlowScenario.STABLE, as_of)
        hits = {(w, t) for w in range(1, 14) for t in detect(rows[:w], base)}
        assert not {h for h in hits if h[1] in ("inflow_drop", "volatility_spike")}, (seller, sorted(hits))


@pytest.mark.parametrize("as_of", [AS_OF, date(2026, 12, 28), date(2027, 1, 25)])
@pytest.mark.parametrize("seller", sorted(STACKING_APPLICANTS))
def test_stacking_sellers_warn_before_default(seller, as_of):
    # 대출 중첩 셀러는 업종과 시작일에 관계없이 신규 차입을 잡고, 잔고 부족 전망이 부도보다 먼저 나온다.
    flow, rows, base = _demo_series(build_demo_cases(as_of)[seller], FlowScenario.STACKING, as_of)
    assert any("new_debt" in detect(rows[:w], base) for w in range(1, 14))
    first = next(r for r in rows if "projected_shortfall" in detect(rows[: r["week"]], base))
    assert date.fromisoformat(first["end"]) < flow.default_on


def test_stable_projection_never_runs_short(cafe, baseline):
    _, rows = _rows(cafe, FlowScenario.STABLE, baseline, 13)
    assert all(r["shortfall_date"] is None and r["projected_min_balance"] > 0 for r in rows)


def test_stacking_projected_shortfall_comes_before_default(cafe, baseline):
    flow, rows = _rows(cafe, FlowScenario.STACKING, baseline, 13)
    first = next(r for r in rows if "projected_shortfall" in detect(rows[: r["week"]], baseline))
    assert first["shortfall_date"] and date.fromisoformat(first["shortfall_date"]) <= flow.default_on
    assert date.fromisoformat(first["end"]) < flow.default_on
    assert first["projected_min_balance"] < 0


def test_without_schedule_no_projection(cafe, baseline):
    plain = Baseline.from_dict({**baseline.to_dict(), "settlement": {**baseline.settlement, "outflows": None}})
    _, rows = _rows(cafe, FlowScenario.STACKING, plain, 13)
    assert all("projected_min_balance" not in r for r in rows)
    assert not any("projected_shortfall" in detect(rows[:w], plain) for w in range(1, 14))
    # 설치 원리금이 없던 이전 기준선 문서도 읽는다.
    old = {k: v for k, v in baseline.to_dict().items() if k != "installment_krw"}
    assert Baseline.from_dict(old).installment_krw == 0


def test_catalog_ids_are_evidence_ids(cafe, baseline):
    import re

    _, rows = _rows(cafe, FlowScenario.STACKING, baseline, 4)
    catalog = evidence_catalog(rows, baseline, cafe.profile.industry)
    assert catalog["W:2:loan_proceeds"] == PROCEEDS_KRW
    assert catalog["B:index:01"] == baseline.seasonal_index[1]
    assert catalog["B:cohort:index:01"] == PEER_COHORTS["패션의류"]["index"][1]
    assert all(re.fullmatch(EVIDENCE_ID_PATTERN, k) for k in catalog)
    assert not any(k.startswith("W:5:") for k in catalog)
    assert catalog["F:4:projected_min_balance"] == rows[3]["projected_min_balance"]
    assert catalog["F:4:projected_min_date"] == rows[3]["projected_min_date"]
    assert "F:4:shortfall_date" not in catalog  # 부족 예상일이 없으면 근거도 없다


def test_peer_cohorts_cover_every_seller_industry():
    assert set(PEER_COHORTS) == set(SELLER_INDUSTRIES)
    assert all(set(c["index"]) == set(range(1, 13)) for c in PEER_COHORTS.values())
