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
from mockapis.demo import POSTLOAN_SCENARIOS, build_demo_cases
from mockapis.postloan import FlowScenario, generate_postloan
from mockapis.postloan_scenarios import PROCEEDS_KRW
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
    flow = generate_postloan(cafe, FlowScenario.SALES_SLUMP, AS_OF, 7)
    card = next(t for t in flow.transactions if t.txn_type_code == "CARD_SETTLE")
    hidden = next(t for t in flow.transactions if t.description == "머니무브캐피탈")
    cats = categorize(flow.transactions, {card.txn_id: "high_cost_lender", hidden.txn_id: "high_cost_lender"})
    assert cats[card.txn_id] is Category.REVENUE
    assert cats[hidden.txn_id] is Category.HIGH_COST_LENDER
    assert classify(hidden) is Category.UNCLASSIFIED_OUT


def test_weekly_series_shapes_sales_slump(cafe, baseline):
    flow, rows = _rows(cafe, FlowScenario.SALES_SLUMP, baseline, 6)
    assert [r["week"] for r in rows] == [1, 2, 3, 4, 5, 6]
    assert rows[5]["loan_proceeds"] == PROCEEDS_KRW and rows[5]["high_cost_count"] >= 1  # 첫 대부는 6주차
    # 설비 선투자로 1주차 잔고가 줄긴 해도 경보선(기준 버퍼의 절반) 위에 있다: 원인(판매 부진)보다
    # 결과(잔고 하락)가 먼저 울리면 안 된다(스펙 §8, Task 5에서 선투자 하한을 올렸다).
    assert baseline.cash_buffer_days / 2 < rows[0]["buffer_days"] < baseline.cash_buffer_days
    last = [t for t in flow.transactions if t.booked_on <= week_window(flow.start, 6)[1]][-1]
    assert rows[5]["end_balance"] == last.balance_after_krw


def test_detector_trips_on_new_debt_then_buffer(cafe, baseline):
    # 판매 부진 셀러는 6주차에 대부(new_debt)를 쓰고, 그 뒤에야 잔고 경보(buffer_drop)가 뜬다.
    _, rows = _rows(cafe, FlowScenario.SALES_SLUMP, baseline, 8)
    assert "buffer_drop" not in detect(rows[:1], baseline)
    assert "new_debt" not in detect(rows[:5], baseline)
    assert "new_debt" in detect(rows[:6], baseline)
    assert any("buffer_drop" in detect(rows[:w], baseline) for w in range(6, 9))


def test_expected_revenue_absorbs_season_and_settlement_timing(cafe, baseline):
    # 단순 평균 대비로는 정산이 몰리지 않은 주가 0.85 아래로 떨어지지만, 기대 입금 대비로는 정상 셀러에 경보가 없다.
    _, rows = _rows(cafe, FlowScenario.STABLE, baseline, 13)
    assert any(r["revenue_ratio"] < 0.85 for r in rows)
    # 1주차는 대출 전 주문의 정산이 상계·지연을 거쳐 들어와 기대 달력과 가장 많이 어긋난다.
    assert all(r["revenue_vs_expected"] >= 0.85 for r in rows[1:])
    assert [detect(rows[:w], baseline) for w in range(1, 14)] == [[]] * 13


def test_without_profile_expected_falls_back_to_average(cafe, baseline):
    plain = Baseline.from_dict({**baseline.to_dict(), "settlement": None})
    _, rows = _rows(cafe, FlowScenario.STABLE, plain, 2)
    assert rows[0]["expected_revenue"] == plain.avg_weekly_revenue
    assert [r["revenue_vs_expected"] for r in rows] == [r["revenue_ratio"] for r in rows]


def test_sales_slump_inflow_drop_shows_up_within_two_months(cafe, baseline):
    _, rows = _rows(cafe, FlowScenario.SALES_SLUMP, baseline, 8)
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
    # 라이브 데모는 오늘 날짜로 흐름을 만든다: 추석·연말·설이 낀 13주에도 데모 셀러 매출이 꺾였다고 보면 안 된다.
    for seller, case in build_demo_cases(as_of).items():
        _, rows, base = _demo_series(case, FlowScenario.STABLE, as_of)
        hits = {(w, t) for w in range(1, 14) for t in detect(rows[:w], base)}
        drops = sorted(w for w, t in hits if t == "inflow_drop")
        spikes = sorted(w for w, t in hits if t == "volatility_spike")
        # 정산 양도(선정산) 셀러는 양도한 플랫폼 정산이 선지급사를 거쳐 85%만 5~7영업일 일찍 들어와
        # 심사 시점 정산 프로필(기대 입금)과 금액·시점이 어긋난다. 은행 입금 기준 트리거는 스펙 §8대로
        # 울릴 수 있어 가끔 울리는 것까지만 허용하고(매주 울리면 양도를 매출 급감으로 읽은 것이다),
        # 원장 기준 신규 트리거 4종이 0건인지는 test_ledger_monitor.py가 본다.
        if any("선정산" in t.description for t in case.transactions):
            assert len(drops) <= 3, (seller, drops)
            continue
        assert not drops, (seller, sorted(hits))
        # 스펙 §8: 기존 트리거는 STABLE에서도 울릴 수 있다. 정산이 하루 이틀 밀린 주는 3주 누적 비율을 흔드니
        # 한 주 튀는 변동성 경보까지는 두고 연속 경보만 막는다.
        assert all(b - a > 1 for a, b in zip(spikes, spikes[1:])), (seller, spikes)


@pytest.mark.parametrize("as_of", [AS_OF, date(2026, 12, 28), date(2027, 1, 25)])
@pytest.mark.parametrize("seller", sorted(POSTLOAN_SCENARIOS))
def test_sales_slump_sellers_warn_before_default(seller, as_of):
    # 매출 부진 셀러는 업종과 시작일에 관계없이 신규 차입을 잡고, 잔고 부족 전망이 부도보다 먼저 나온다.
    flow, rows, base = _demo_series(build_demo_cases(as_of)[seller], FlowScenario.SALES_SLUMP, as_of)
    assert any("new_debt" in detect(rows[:w], base) for w in range(1, 14))
    first = next(r for r in rows if "projected_shortfall" in detect(rows[: r["week"]], base))
    assert date.fromisoformat(first["end"]) < flow.default_on


def test_stable_projection_never_runs_short(cafe, baseline):
    _, rows = _rows(cafe, FlowScenario.STABLE, baseline, 13)
    assert all(r["shortfall_date"] is None and r["projected_min_balance"] > 0 for r in rows)


def test_sales_slump_projected_shortfall_comes_before_default(cafe, baseline):
    flow, rows = _rows(cafe, FlowScenario.SALES_SLUMP, baseline, 13)
    first = next(r for r in rows if "projected_shortfall" in detect(rows[: r["week"]], baseline))
    # 부도보다 앞서 "잔고가 바닥난다"는 전망이 나온다. 다만 전망은 심사 때 본 고정 지출 일정만 쓰므로
    # 설비 선투자의 외상 잔금(사후에 새로 생긴 주간 분납)은 못 보고, 바닥나는 날짜는 실제 부도일보다
    # 조금 뒤로 잡힌다(측정: 전망 2027-01-28 vs 부도 2027-01-20).
    assert first["shortfall_date"] and date.fromisoformat(first["end"]) < flow.default_on
    assert first["projected_min_balance"] < 0


def test_without_schedule_no_projection(cafe, baseline):
    plain = Baseline.from_dict({**baseline.to_dict(), "settlement": {**baseline.settlement, "outflows": None}})
    _, rows = _rows(cafe, FlowScenario.SALES_SLUMP, plain, 13)
    assert all("projected_min_balance" not in r for r in rows)
    assert not any("projected_shortfall" in detect(rows[:w], plain) for w in range(1, 14))
    # 설치 원리금이 없던 이전 기준선 문서도 읽는다.
    old = {k: v for k, v in baseline.to_dict().items() if k != "installment_krw"}
    assert Baseline.from_dict(old).installment_krw == 0


def test_catalog_ids_are_evidence_ids(cafe, baseline):
    import re

    _, rows = _rows(cafe, FlowScenario.SALES_SLUMP, baseline, 6)
    catalog = evidence_catalog(rows, baseline, cafe.profile.industry)
    assert catalog["W:6:loan_proceeds"] == PROCEEDS_KRW
    assert catalog["B:index:01"] == baseline.seasonal_index[1]
    assert catalog["B:cohort:index:01"] == PEER_COHORTS["패션의류"]["index"][1]
    assert all(re.fullmatch(EVIDENCE_ID_PATTERN, k) for k in catalog)
    assert not any(k.startswith("W:7:") for k in catalog)
    assert catalog["F:4:projected_min_balance"] == rows[3]["projected_min_balance"]
    assert catalog["F:4:projected_min_date"] == rows[3]["projected_min_date"]
    assert "F:4:shortfall_date" not in catalog  # 부족 예상일이 없으면 근거도 없다


def test_peer_cohorts_cover_every_seller_industry():
    assert set(PEER_COHORTS) == set(SELLER_INDUSTRIES)
    assert all(set(c["index"]) == set(range(1, 13)) for c in PEER_COHORTS.values())
