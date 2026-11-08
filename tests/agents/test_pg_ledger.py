from datetime import date

from agents.pg_ledger import features_of, ledger_summary
from shared.contracts import PgSettlement, Transaction

AS_OF = date(2026, 11, 1)


def _s(day, platform, gross, fee=0, ded=0):
    return PgSettlement(settled_on=day, platform=platform, gross_krw=gross, fee_krw=fee, deduction_krw=ded,
                        net_krw=gross - fee - ded)


def _t(day, amount, desc, code="PG_SETTLE", i=[0]):
    i[0] += 1
    return Transaction(txn_id=f"t{i[0]}", booked_on=day, amount_krw=amount, balance_after_krw=0, description=desc,
                       txn_type_code=code)


def test_matches_within_three_business_days_and_reports_unpaid():
    s = [_s(date(2026, 10, 13), "쿠팡", 1000, 100, 20), _s(date(2026, 10, 14), "쿠팡", 2000, 200),
         _s(date(2026, 10, 15), "스마트스토어", 500, 30)]
    t = [_t(date(2026, 10, 15), 880, "쿠팡(주) 주정산"),            # +2영업일 → 짝
         _t(date(2026, 10, 20), 1800, "쿠팡(주) 주정산"),           # +4영업일 → 미매칭
         _t(date(2026, 10, 15), 470, "네이버파이낸셜 정산")]
    r = ledger_summary(s, t, AS_OF)
    assert r["available"] and r["settlement_count"] == 3
    assert r["pg_reconciliation_ratio"] == round((880 + 470) / (880 + 1800 + 470), 3)
    assert r["unmatched_count"] == 1 and r["unmatched"][0]["net_krw"] == 1800
    coupang = next(p for p in r["platforms"] if p["platform"] == "쿠팡")
    assert coupang["unpaid_krw"] == 1800 and r["platforms"][0]["platform"] == "쿠팡"


def test_same_amount_same_day_matches_once():
    s = [_s(date(2026, 10, 13), "쿠팡", 1000), _s(date(2026, 10, 13), "쿠팡", 1000)]
    t = [_t(date(2026, 10, 13), 1000, "쿠팡(주) 주정산")]
    r = ledger_summary(s, t, AS_OF)
    assert r["pg_reconciliation_ratio"] == 0.5 and r["unmatched_count"] == 1


def test_recent_settlements_excluded():
    s = [_s(date(2026, 10, 13), "쿠팡", 1000), _s(date(2026, 10, 29), "쿠팡", 5000)]  # 10/29 + 3영업일 ≥ 기준일
    t = [_t(date(2026, 10, 13), 1000, "쿠팡(주) 주정산")]
    r = ledger_summary(s, t, AS_OF)
    assert r["settlement_count"] == 1 and r["pg_reconciliation_ratio"] == 1.0


def test_concentration_take_rate_trend_and_features():
    s = [_s(date(2026, m, 10), "쿠팡", 1000, 100, 10 * (m - 4)) for m in range(5, 11)]
    s += [_s(date(2026, m, 10), "스마트스토어", 1000, 60) for m in range(5, 11)]
    t = [_t(x.settled_on, x.net_krw, "쿠팡(주) 주정산" if x.platform == "쿠팡" else "네이버파이낸셜 정산") for x in s]
    r = ledger_summary(s, t, AS_OF)
    assert r["pg_top_platform_share"] == 0.5 and r["pg_hhi"] == 0.5
    assert [m["month"] for m in r["monthly_take_rate"]] == ["2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]
    assert r["pg_take_rate_trend_pp"] == 1.5  # 최근 3개월 (10.0+10.5+11.0)/3 − 이전 3개월 (8.5+9.0+9.5)/3 = 1.5%p
    assert features_of(r) == {"pg_reconciliation_ratio": 1.0, "pg_top_platform_share": 0.5}


def test_take_rate_trend_is_three_month_means_not_first_vs_last():
    s = [_s(date(2026, m, 10), "쿠팡", 1000, 50 if m == 10 else 10) for m in range(5, 11)]  # 1%×5, 마지막 달 5%
    t = [_t(x.settled_on, x.net_krw, "쿠팡(주) 주정산") for x in s]
    r = ledger_summary(s, t, AS_OF)
    assert r["pg_take_rate_trend_pp"] == 1.33  # (1+1+5)/3 − (1+1+1)/3; 첫·끝 달 차이 4.00pp와 다르다


def test_unavailable_without_settlements():
    assert ledger_summary([], [], AS_OF) == {"available": False}
    assert features_of({"available": False}) == {"pg_reconciliation_ratio": None, "pg_top_platform_share": None}
