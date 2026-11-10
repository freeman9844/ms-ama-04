import math
from datetime import date

import pytest

from agents.pg_ledger import _trend_pp, features_of, ledger_summary
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
    may = r["monthly_take_rate"][0]
    assert (may["fee_rate"], may["offset_rate"], may["rate"]) == (0.08, 0.005, 0.085)
    assert r["pg_offset_rate_trend_pp"] == 1.5  # 상계 0.5~3.0%: (2.0+2.5+3.0)/3 − (0.5+1.0+1.5)/3


def test_take_rate_trend_is_three_month_means_not_first_vs_last():
    s = [_s(date(2026, m, 10), "쿠팡", 1000, 50 if m == 10 else 10) for m in range(5, 11)]  # 1%×5, 마지막 달 5%
    t = [_t(x.settled_on, x.net_krw, "쿠팡(주) 주정산") for x in s]
    r = ledger_summary(s, t, AS_OF)
    assert r["pg_take_rate_trend_pp"] == 1.33  # (1+1+5)/3 − (1+1+1)/3; 첫·끝 달 차이 4.00pp와 다르다


def test_trend_pp_normalises_negative_zero():
    """부동소수 오차로 차이가 0 근처 아주 작은 음수가 되면 round()가 -0.0을 돌려줄 수 있다; 0.0으로 정규화해야 한다."""
    rates = [0.1, 0.2, 0.30000000000000004, 0.1, 0.2, 0.3]
    result = _trend_pp(rates)
    assert result == 0.0 and math.copysign(1.0, result) == 1.0


def test_unavailable_without_settlements():
    assert ledger_summary([], [], AS_OF) == {"available": False}
    assert features_of({"available": False}) == {"pg_reconciliation_ratio": None, "pg_top_platform_share": None}


def test_advance_pairs_unmatched_settlement_with_earlier_partial_deposit():
    s = [_s(date(2026, 10, 13), "쿠팡", 1000)]
    t = [_t(date(2026, 10, 7), 850, "한결선정산 선지급", code="TRANSFER_IN")]
    r = ledger_summary(s, t, AS_OF)
    assert r["pg_advance_suspected_count"] == 1 and r["pg_advance_share"] == 1.0
    assert r["unmatched"][0]["advance"] == {"booked_on": "2026-10-07", "amount_krw": 850, "lead_days": 6}


@pytest.mark.parametrize("day, amount, desc", [
    (date(2026, 10, 2), 850, "선지급"),           # 11일 전 → 창 밖
    (date(2026, 10, 14), 850, "선지급"),          # 정산일 뒤
    (date(2026, 10, 7), 960, "선지급"),           # 0.96
    (date(2026, 10, 7), 740, "선지급"),           # 0.74
    (date(2026, 10, 7), 850, "쿠팡(주) 주정산"),  # 플랫폼 입금
])
def test_advance_rejects_out_of_window_ratio_or_platform(day, amount, desc):
    r = ledger_summary([_s(date(2026, 10, 13), "쿠팡", 1000)], [_t(day, amount, desc, code="TRANSFER_IN")], AS_OF)
    assert r["pg_advance_suspected_count"] == 0 and r["unmatched"][0]["advance"] is None


def test_advance_deposit_used_once():
    s = [_s(date(2026, 10, 13), "쿠팡", 1000), _s(date(2026, 10, 13), "쿠팡", 1000)]
    r = ledger_summary(s, [_t(date(2026, 10, 8), 850, "선지급", code="TRANSFER_IN")], AS_OF)
    assert r["pg_advance_suspected_count"] == 1 and r["pg_advance_share"] == 0.5


def test_advance_share_is_amount_weighted_not_count():
    """스펙 §4.1: pg_advance_share는 짝지어진 미매칭 실지급액 ÷ 전체 미매칭 실지급액이다(건수 비율이 아니다).
    1000원짜리 정산만 선지급과 짝지어지고 3000원짜리는 짝이 없으므로 건수 비율(1/2=0.5)과 금액 비율(1000/4000=0.25)이 달라야 한다."""
    s = [_s(date(2026, 10, 10), "쿠팡", 1000), _s(date(2026, 10, 13), "쿠팡", 3000)]
    t = [_t(date(2026, 10, 5), 850, "선지급", code="TRANSFER_IN")]  # 850/1000=0.85(쪽 1000과만 짝), 850/3000=0.28(쪽 3000과는 비율 밖)
    r = ledger_summary(s, t, AS_OF)
    assert r["pg_advance_suspected_count"] == 1
    assert r["pg_advance_share"] == 0.25  # 1000 / (1000 + 3000), 건수 비율 0.5가 아니다


def test_advance_share_none_without_unmatched():
    s = [_s(date(2026, 10, 13), "쿠팡", 1000)]
    r = ledger_summary(s, [_t(date(2026, 10, 13), 1000, "쿠팡(주) 주정산")], AS_OF)
    assert r["pg_advance_suspected_count"] == 0 and r["pg_advance_share"] is None


def test_cash_conversion_days_weighted_and_renormalised():
    def run(rows):
        return ledger_summary(rows, [], AS_OF)
    r = run([_s(date(2026, 10, 13), "쿠팡", 600), _s(date(2026, 10, 13), "스마트스토어", 400)])
    assert r["pg_cash_conversion_days"] == 22.4  # 0.6×32 + 0.4×8
    assert {p["platform"]: p["lag_days"] for p in r["platforms"]} == {"쿠팡": 32, "스마트스토어": 8}
    r = run([_s(date(2026, 10, 13), "쿠팡", 1000), _s(date(2026, 10, 13), "기타몰", 1000)])
    assert r["pg_cash_conversion_days"] == 32.0
    assert next(p for p in r["platforms"] if p["platform"] == "기타몰")["lag_days"] is None
    assert run([_s(date(2026, 10, 13), "기타몰", 1000)])["pg_cash_conversion_days"] is None
