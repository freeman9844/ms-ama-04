from datetime import date, timedelta

import pytest

from mockapis import demo, postloan

AS_OF = date(2026, 11, 24)


def cases():
    return demo.build_usd_cases(AS_OF)


def flow_for(seller):
    case = cases()[seller]
    return case, postloan.generate_usd_postloan(case, AS_OF, 1001, loan_id="loan-usd",
                                                 amount_cents=500_000, installment_cents=84_000)


def week_rows(flow, n, platform=None):  # n주차: week_end == flow.start + 7n - 1
    end = flow.start + timedelta(days=7 * n - 1)
    return [w for w in flow.market_weeks if w.week_end == end and (platform is None or w.platform_code == platform)]


def cancel_rate(w):
    return w.cancel_cents / (w.gross_cents + w.cancel_cents)


def main_platform(case):
    return case.marketplace.platforms[0].platform_code


def main_baseline_cancel_rate(flow, platform):
    """주력 플랫폼 기준선: 대출 전 8주(-7..0) 취소율 평균."""
    rates = [cancel_rate(week_rows(flow, n, platform)[0]) for n in range(-7, 1)]
    return sum(rates) / len(rates)


@pytest.mark.parametrize("seller", ["seller-gearstore-06", "seller-linen-10", "seller-dailylook-01",
                                    "seller-tangerine-15", "seller-snackbox-22", "seller-tint-17",
                                    "seller-campgear-23"])
def test_preloan_weeks_match_underwriting_store(seller):
    case, flow = flow_for(seller)
    platforms = case.marketplace.platforms
    assert len(flow.market_weeks) == 21 * len(platforms)
    by_code = {p.platform_code: p for p in platforms}
    for n in range(-7, 1):
        for platform_code, underwriting in by_code.items():
            rows = week_rows(flow, n, platform_code)
            assert len(rows) == 1
            assert rows[0].rating == underwriting.rating
            assert rows[0].seller_grade == underwriting.seller_grade


def test_gearstore_cancel_rises_before_hold_and_warns_at_hold():
    case, flow = flow_for("seller-gearstore-06")
    platform = main_platform(case)
    base = main_baseline_cancel_rate(flow, platform)
    week1 = cancel_rate(week_rows(flow, 1, platform)[0])
    week2 = cancel_rate(week_rows(flow, 2, platform)[0])
    week9 = cancel_rate(week_rows(flow, 9, platform)[0])
    assert week1 < base * 1.8
    assert week2 >= base * 1.8 and week2 >= base + 0.02 and week2 <= 0.08
    assert week9 < base * 1.8
    week3 = week_rows(flow, 3, platform)[0]
    assert len(week3.penalties) == 1 and week3.penalties[0].kind == "warning"


def test_linen_rating_drops_after_spike_then_recovers():
    case, flow = flow_for("seller-linen-10")
    platform = main_platform(case)
    baseline = next(p for p in case.marketplace.platforms if p.platform_code == platform).rating
    for n in range(4, 9):
        assert week_rows(flow, n, platform)[0].rating <= baseline - 0.2
    assert week_rows(flow, 9, platform)[0].rating >= baseline - 0.1


def test_dailylook_main_grade_downgraded_from_week5():
    case, flow = flow_for("seller-dailylook-01")
    platform = main_platform(case)
    baseline = next(p for p in case.marketplace.platforms if p.platform_code == platform).seller_grade
    assert week_rows(flow, 4, platform)[0].seller_grade == baseline
    from mockapis.sellerdata.usd_market_flow import GRADES
    expected = GRADES[max(0, GRADES.index(baseline) - 1)]
    for n in range(5, 14):
        assert week_rows(flow, n, platform)[0].seller_grade == expected


def test_tint_blip_is_week6_only():
    case, flow = flow_for("seller-tint-17")
    platform = main_platform(case)
    base = main_baseline_cancel_rate(flow, platform)
    week5 = cancel_rate(week_rows(flow, 5, platform)[0])
    week6 = cancel_rate(week_rows(flow, 6, platform)[0])
    week7 = cancel_rate(week_rows(flow, 7, platform)[0])
    assert week5 < base * 1.8
    assert week6 >= base * 1.8 and week6 >= base + 0.02
    assert week7 < base * 1.8
    assert not any(w.penalties for w in flow.market_weeks)


@pytest.mark.parametrize("seller", ["seller-tangerine-15", "seller-snackbox-22", "seller-campgear-23",
                                    "seller-bakery-20"])
def test_quiet_scenarios_have_no_market_events(seller):
    case, flow = flow_for(seller)
    platform = main_platform(case)
    baseline = next(p for p in case.marketplace.platforms if p.platform_code == platform)
    assert not any(w.penalties for w in flow.market_weeks)
    for n in range(-7, 14):
        row = week_rows(flow, n, platform)[0]
        assert row.rating == baseline.rating
        assert row.seller_grade == baseline.seller_grade


def test_pending_grows_during_gearstore_hold():
    case, flow = flow_for("seller-gearstore-06")
    platform = main_platform(case)
    week1 = week_rows(flow, 1, platform)[0].pending_settlement_cents
    week5 = week_rows(flow, 5, platform)[0].pending_settlement_cents
    assert week5 > week1
