from datetime import date, timedelta

import pytest

from agents.usd_market_score import market_score
from shared.contracts import USD_V1, StorePenalty
from shared.usd_source import UsdMarketWeek

START = date(2026, 11, 24)


def end(n):
    return START + timedelta(days=7 * n - 1)


def wk(n, code, gross=1_000_000, cancel=20_000, returns=20_000, rating=4.5, grade="Gold", penalties=()):
    return UsdMarketWeek(**USD_V1.model_dump(), week_end=end(n), platform_code=code, orders=10, gross_cents=gross,
                         cancel_cents=cancel, returns_cents=returns, rating=rating, seller_grade=grade,
                         pending_settlement_cents=0, penalties=penalties)


def flow(post=None, gross=None, pre=None):
    """대출 전 -7..0주는 기준값, 1..max(post)주는 post[n][code] 덮어쓰기."""
    gross = gross or {"coupang": 1_000_000, "naver_smart_store": 1_000_000}
    weeks = [wk(n, c, gross=g, **(pre or {}).get(c, {})) for n in range(-7, 1) for c, g in gross.items()]
    for n in range(1, max((post or {1: {}}).keys()) + 1):
        weeks += [wk(n, c, **{"gross": g, **(post or {}).get(n, {}).get(c, {})}) for c, g in gross.items()]
    return weeks


def penalty(n, kind):
    return (StorePenalty(platform="coupang", occurred_on=end(n), kind=kind),)


def test_unchanged_markets_score_100():
    s = market_score(flow(), START, end(1))
    assert (s.score, s.worst_score, s.parts) == (100, 100, [])
    assert s.platforms == {"coupang": {"score": 100, "share": 0.5}, "naver_smart_store": {"score": 100, "share": 0.5}}


def test_one_market_cancel_rise_is_diluted_but_worst_shows_it():
    s = market_score(flow({1: {"coupang": {"cancel": 60_000}}}), START, end(1))
    assert (s.score, s.worst_platform, s.worst_score) == (88, "coupang", 76)
    assert s.parts == [{"platform": "coupang", "metric": "cancel", "points": 12.0, "value": 0.06, "baseline": 0.02}]


def test_deductions_are_capped():
    s = market_score(flow({1: {"coupang": {"cancel": 300_000}}}), START, end(1))
    assert s.platforms["coupang"]["score"] == 60


def test_returns_rating_and_grade_add_up():
    s = market_score(flow({1: {"coupang": {"returns": 60_000, "rating": 4.2, "grade": "Silver"}}}), START, end(1))
    assert s.platforms["coupang"]["score"] == 62  # 반품 8 + 평점 15 + 등급 15
    assert [(p["metric"], p["points"]) for p in s.parts] == [("rating", 7.5), ("grade", 7.5), ("returns", 4.0)]


def test_penalty_lasts_four_weeks():
    weeks = flow({n: ({"coupang": {"penalties": penalty(3, "warning")}} if n == 3 else {}) for n in range(1, 8)})
    scores = [market_score(weeks, START, end(n)).platforms["coupang"]["score"] for n in range(1, 8)]
    assert scores == [100, 100, 80, 80, 80, 80, 100]
    weeks = flow({n: ({"coupang": {"penalties": penalty(2, "suspension")}} if n == 2 else {}) for n in range(1, 3)})
    assert market_score(weeks, START, end(2)).platforms["coupang"]["score"] == 60


def test_preloan_penalty_ignored():
    weeks = [wk(0, "coupang", penalties=penalty(0, "warning")) if (w.week_end, w.platform_code) == (end(0), "coupang")
             else w for w in flow()]
    assert market_score(weeks, START, end(1)).score == 100


def test_zero_gross_week_skips_ratio_metrics():
    s = market_score(flow({1: {"coupang": {"gross": 0, "cancel": 0, "returns": 0}}}), START, end(1))
    assert s.platforms["coupang"]["score"] == 100


def test_market_without_preloan_sales_is_excluded():
    s = market_score(flow(gross={"coupang": 1_000_000, "own_store": 0}), START, end(1))
    assert set(s.platforms) == {"coupang"} and s.platforms["coupang"]["share"] == 1.0


def test_minor_market_is_not_worst():
    s = market_score(flow({1: {"own_store": {"cancel": 300_000}}},
                          gross={"coupang": 900_000, "own_store": 100_000}), START, end(1))
    assert s.platforms["own_store"]["score"] == 60 and s.score == 96
    assert (s.worst_platform, s.worst_score) == ("coupang", 100)


@pytest.mark.parametrize("pre, now", [("Gold", "Platinum"), ("Platinum", "Gold")])
def test_unknown_grade_is_not_compared(pre, now):
    s = market_score(flow({1: {"coupang": {"grade": now}}}, pre={"coupang": {"grade": pre}}), START, end(1))
    assert s.platforms["coupang"]["score"] == 100


def test_none_without_this_week_or_preloan():
    assert market_score(flow(), START, end(2)) is None
    assert market_score([w for w in flow() if w.week_end >= START], START, end(1)) is None
