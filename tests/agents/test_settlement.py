from datetime import date, timedelta

import pytest

from agents.classify import Category
from agents.monitoring import categorize, settlement_for
from agents.settlement import (
    expected_payouts,
    expected_revenue,
    payer_of,
    seasonal_factor,
    settlement_profile,
)
from mockapis.demo import build_demo_cases
from shared.contracts import Transaction
from shared.krcalendar import add_business_days

AS_OF = date(2026, 11, 2)
FLAT = {m: 1.0 for m in range(1, 13)}


@pytest.fixture(scope="module")
def cases():
    return build_demo_cases(AS_OF)


def _profile(case):
    return settlement_for(case.transactions, {}, AS_OF, case.profile.industry)


def _channels(profile):
    return {c["payer"]: (c["cadence"], c["anchors"]) for c in profile["channels"]}


def _daily(lo: date, hi: date, amount_of) -> list[Transaction]:
    out, d, bal = [], lo, 0
    while d <= hi:
        bal += amount_of(d)
        out.append(Transaction(txn_id=f"t{d}", booked_on=d, amount_krw=amount_of(d), balance_after_krw=bal,
                               description="토스페이먼츠 정산", txn_type_code="PG_SETTLE"))
        d += timedelta(days=1)
    return out


def test_payer_of_reads_settlement_descriptions():
    assert payer_of("쿠팡 주정산 2026-10-26") == "쿠팡 주정산"
    assert payer_of("쿠팡 최종액정산") == "쿠팡 최종액정산"
    assert payer_of("네이버파이낸셜 정산") == "네이버파이낸셜"
    assert payer_of("카드매출") == "기타"


def test_channels_follow_marketplace_rules(cases):
    dailylook = _channels(_profile(cases["seller-dailylook-01"]))
    assert dailylook["네이버파이낸셜"] == ("business_daily", [])
    assert dailylook["쿠팡 주정산"] == ("weekly", [15])
    assert dailylook["쿠팡 최종액정산"] == ("monthly", [1])
    assert _channels(_profile(cases["seller-mangwon-02"]))["11번가"] == ("semimonthly", [10, 25])


def test_one_off_deposit_is_not_a_settlement_channel(cases):
    # candle 이력에는 일회성 '카드매출' 입금(프롬프트 주입 문구)이 한 번 있다.
    assert set(_channels(_profile(cases["seller-candle-05"]))) == {"네이버파이낸셜", "토스페이먼츠"}


def test_thin_history_uses_cohort_for_unseen_months(cases):
    profile = _profile(cases["seller-knitroom-04"])
    assert profile["own_months"] == ["07", "08", "09", "10"]
    assert set(profile["seasonal_index"]) == {f"{m:02d}" for m in range(1, 13)}
    # 핸드메이드 코호트: 연말 선물 수요가 여름보다 크다.
    assert profile["seasonal_index"]["12"] > 1.3 > 0.95 > profile["seasonal_index"]["07"]


def test_own_index_is_blended_halfway_with_flat_cohort():
    txns = _daily(date(2026, 3, 1), date(2026, 4, 30), lambda d: 1_200 if d.month == 3 else 800)
    profile = settlement_profile(txns, {t.txn_id: Category.REVENUE for t in txns}, date(2026, 5, 1), FLAT)
    own_mar, own_apr = 37_200 / 30_600, 24_000 / 30_600
    assert profile["own_months"] == ["03", "04"]
    assert profile["seasonal_index"]["03"] == round((own_mar + 1) / 2, 3)
    assert profile["seasonal_index"]["04"] == round((own_apr + 1) / 2, 3)
    assert profile["seasonal_index"]["07"] == 1.0


def test_cohort_index_is_moved_to_cash_month():
    # 11월 행사 주문의 일부는 12월에 입금된다: 입금 달 지수에 앞 달 주문 지수가 30% 섞인다.
    txns = _daily(date(2026, 3, 1), date(2026, 4, 30), lambda d: 1_000)
    profile = settlement_profile(txns, {t.txn_id: Category.REVENUE for t in txns}, date(2026, 5, 1),
                                 {**FLAT, 11: 1.3})
    assert profile["seasonal_index"]["11"] == 1.21
    assert profile["seasonal_index"]["12"] == 1.09
    assert profile["seasonal_index"]["10"] == 1.0


def test_seasonal_factor_interpolates_between_mid_months(cases):
    profile = _profile(cases["seller-candle-05"])
    index = profile["seasonal_index"]
    assert seasonal_factor(profile, date(2026, 11, 15)) == pytest.approx(index["11"])
    assert seasonal_factor(profile, date(2026, 12, 15)) == pytest.approx(index["12"])
    mid = seasonal_factor(profile, date(2026, 11, 30))
    assert min(index["11"], index["12"]) < mid < max(index["11"], index["12"])


def test_holiday_week_expects_less(cases):
    # 2026 추석(9/24~26) 주는 영업일이 3일뿐이라 매 영업일 정산만 받는 셀러의 기대 입금이 낮다.
    profile = _profile(cases["seller-candle-05"])
    normal = expected_revenue(profile, date(2026, 9, 7), date(2026, 9, 13))
    chuseok = expected_revenue(profile, date(2026, 9, 21), date(2026, 9, 27))
    assert chuseok < 0.7 * normal


def test_weekly_payouts_land_lag_business_days_after_sunday(cases):
    profile = _profile(cases["seller-dailylook-01"])
    lo, hi = date(2026, 11, 2), date(2027, 1, 31)
    coupang = {d for d, payer, _ in expected_payouts(profile, lo, hi) if payer == "쿠팡 주정산"}
    sundays = [date(2026, 10, 4) + timedelta(days=7 * i) for i in range(20)]
    assert coupang == {p for s in sundays if lo <= (p := add_business_days(s, 15)) <= hi}


def test_no_revenue_means_no_profile(cases):
    case = cases["seller-dailylook-01"]
    out = [t for t in case.transactions if t.amount_krw < 0]
    assert settlement_profile(out, categorize(out, {}), AS_OF, FLAT) is None
