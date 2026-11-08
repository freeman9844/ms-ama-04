import random
from datetime import date, timedelta

import pytest

from evals.datasets import TRUTH_LABEL
from mockapis.demo import DEMO_APPLICANTS, build_load_cases
from shared.krcalendar import add_business_days, is_business_day, next_business_day
from mockapis.sellerdata.channels import COUPANG, ELEVENST, OWNMALL, SMARTSTORE, Payout, payout_schedule, payouts
from mockapis.sellerdata.personas import DEMO_PERSONAS, business_reg_no, is_valid_brn
from mockapis.sellerdata.seasonality import day_factor, month_index
from mockapis.synth import TxnCategory
from shared.sellers import DEMO_SELLERS


@pytest.mark.parametrize(
    ("day", "business"),
    [
        (date(2026, 9, 24), False),  # 추석 연휴
        (date(2026, 10, 5), False),  # 개천절 대체공휴일
        (date(2026, 5, 1), False),  # 노동절(2026년부터 공휴일)
        (date(2025, 5, 1), True),
        (date(2026, 7, 17), False),  # 제헌절(2026년부터 공휴일)
        (date(2026, 10, 6), True),
        (date(2026, 10, 10), False),  # 토요일
        (date(2029, 2, 13), True),  # 표 밖 연도는 고정 공휴일만 쓴다
    ],
)
def test_business_days(day, business):
    assert is_business_day(day) is business


def test_business_day_arithmetic_skips_chuseok():
    assert next_business_day(date(2026, 10, 3)) == date(2026, 10, 6)
    assert add_business_days(date(2026, 9, 18), 5) == date(2026, 9, 29)


def test_channel_settlement_dates():
    rng = random.Random(1)
    assert payout_schedule(OWNMALL, date(2026, 9, 23), rng) == ((date(2026, 9, 29), 1.0, "자사몰"),)
    assert payout_schedule(COUPANG, date(2026, 4, 8), rng) == (
        (date(2026, 5, 4), 0.7, "쿠팡:weekly"),  # 4/12(일) 마감 후 15영업일, 5/1 노동절 제외
        (date(2026, 6, 1), pytest.approx(0.3), "쿠팡:final"),  # 익익월 첫 영업일
    )
    assert payout_schedule(ELEVENST, date(2026, 4, 10), rng) == ((date(2026, 4, 27), 1.0, "11번가"),)
    assert payout_schedule(ELEVENST, date(2026, 4, 20), rng) == ((date(2026, 5, 11), 1.0, "11번가"),)
    order = date(2026, 4, 6)
    for paid_on, share, _ in payout_schedule(SMARTSTORE, order, rng):
        assert is_business_day(paid_on) and 6 <= (paid_on - order).days <= 12 and share == pytest.approx(1 / 3)


def test_payouts_deduct_fee_and_aggregate_by_day():
    sales = {date(2026, 4, 6) + (date(2026, 4, 7) - date(2026, 4, 6)) * i: 1_000_000 for i in range(7)}
    out = payouts(COUPANG, "식품", sales, random.Random(2))
    assert [p.description for p in out] == ["쿠팡(주) 주정산", "쿠팡(주) 최종액정산"]
    for p in out:
        assert p.fee_krw == round(p.gross_krw * 0.1166) and p.net_krw == p.gross_krw - p.fee_krw
    assert 0.93 * 7_000_000 < sum(p.gross_krw for p in out) < 0.98 * 7_000_000  # 반품 약 5%


def test_business_reg_no_checksum():
    assert is_valid_brn("123-45-67891") and not is_valid_brn("123-45-67892")
    rng = random.Random(9)
    for _ in range(200):
        brn = business_reg_no(rng)
        assert is_valid_brn(brn) and 1 <= int(brn[4:6]) <= 79


def test_seasonality_november_event_and_food_gifts():
    assert month_index("패션의류", 2026, 11) == pytest.approx(1.3)
    assert month_index("뷰티", 2026, 5) > month_index("뷰티", 2026, 7)
    chuseok_week = date(2026, 9, 14)  # 추석 11일 전, 같은 요일 비교
    assert day_factor("식품", chuseok_week) > 1.3 * day_factor("식품", date(2026, 8, 31))
    assert day_factor("패션의류", date(2026, 9, 25)) < 0.6 * day_factor("패션의류", date(2026, 9, 18))


def test_demo_personas_match_seller_catalog():
    assert [a for a, *_ in DEMO_APPLICANTS] == [s.applicant_id for s in DEMO_SELLERS] == list(DEMO_PERSONAS)
    for s in DEMO_SELLERS:
        p = DEMO_PERSONAS[s.applicant_id]
        assert (p.business_name, p.industry) == (s.business_name, s.industry)
        assert tuple(c for c, _ in p.channels) == s.channels
        assert sum(w for _, w in p.channels) == pytest.approx(1.0)


def test_load_sellers_have_unique_names():
    cases = build_load_cases(date(2026, 11, 1))
    businesses = [c.profile.business_name for c in cases.values()]
    people = [n for c in cases.values() for n in (c.persona.representative, *c.persona.employees)]
    demo_people = {n for p in DEMO_PERSONAS.values() for n in (p.representative, *p.employees)}
    assert len(set(businesses)) == len(businesses) == 48
    assert len(set(people)) == len(people) and not set(people) & demo_people


def test_truth_label_covers_every_synthetic_category():
    assert set(TRUTH_LABEL) == set(TxnCategory)


def test_demo_sellers_have_unique_names_apart_from_load_sellers():
    from shared.sellers import LOAD_BUSINESS_NAMES

    businesses = [p.business_name for p in DEMO_PERSONAS.values()]
    people = [n for p in DEMO_PERSONAS.values() for n in (p.representative, *p.employees)]
    load = build_load_cases(date(2026, 11, 1)).values()
    load_people = {n for c in load for n in (c.persona.representative, *c.persona.employees)}
    assert len(DEMO_PERSONAS) == 20
    assert len(set(businesses)) == len(businesses)
    assert not set(businesses) & {n for names in LOAD_BUSINESS_NAMES.values() for n in names}
    assert len(set(people)) == len(people) and not set(people) & load_people


def test_payout_net_subtracts_deduction():
    p = Payout(date(2026, 4, 6), "쿠팡", "쿠팡(주) 주정산", "PG_SETTLE", 1000, 100, 30)
    assert p.net_krw == 870
    assert Payout(date(2026, 4, 6), "쿠팡", "쿠팡(주) 주정산", "PG_SETTLE", 1000, 100).net_krw == 900


def test_generated_case_ledger_and_bank_differ_only_by_timing_and_offset():
    from mockapis.synth import Archetype, generate_case

    case = generate_case("app-r", Archetype.HEALTHY_SEASONAL, date(2026, 11, 1), 7)
    assert any(s.deduction_krw > 0 for s in case.pg_settlements)
    for s in case.pg_settlements:
        assert s.net_krw == s.gross_krw - s.fee_krw - s.deduction_krw
    deposits = {(t.amount_krw) for t in case.transactions if t.txn_type_code == "PG_SETTLE"}
    matched = [s for s in case.pg_settlements if s.net_krw in deposits]
    assert len(matched) >= 0.97 * len(case.pg_settlements)  # 기준일 직후로 밀린 몇 건만 빠진다


def test_simulate_without_receipt_rng_is_unchanged():
    from mockapis.sellerdata.engine import simulate

    persona = DEMO_PERSONAS["seller-dailylook-01"]
    a = simulate(persona, date(2026, 1, 1), date(2026, 4, 1), random.Random(5))
    assert all(s.deduction_krw == 0 for s in a.pg_settlements)
    pg_deposits = sorted(e.amount_krw for e in a.events if e.code == "PG_SETTLE")
    assert pg_deposits == sorted(s.net_krw for s in a.pg_settlements)


def _ad_outflows(ad_deductions, month):
    from mockapis.sellerdata.costs import CostSchedule

    persona = DEMO_PERSONAS["seller-dailylook-01"]
    sales = {date(2026, 1, 1) + timedelta(days=i): 1_000_000 for i in range(120)}
    events = CostSchedule(persona, date(2026, 2, 1), date(2026, 5, 1), sales, random.Random(3),
                          ad_deductions=ad_deductions).build()
    ad = sum(-e.amount_krw for e in events if e.category is TxnCategory.ADVERTISING and e.booked_on.month == month)
    card = sum(-e.amount_krw for e in events if e.category is TxnCategory.CARD_PAYMENT and e.booked_on.month == month)
    return ad, card, events


def test_ad_deductions_reduce_next_months_bank_paid_ads():
    base_ad, base_card, base_events = _ad_outflows(None, 3)
    assert _ad_outflows({}, 3)[2] == base_events
    ad, card, _ = _ad_outflows({(2026, 2): 300_000}, 3)
    assert 300_000 - 3 <= base_ad - ad <= 300_000 and card == base_card  # 충전 3건 정수 절삭
    ad, card, _ = _ad_outflows({(2026, 2): base_ad + 50_000}, 3)
    assert ad == 0 and 0 < base_card - card <= 50_000 + 1
    ad, card, _ = _ad_outflows({(2026, 2): 10**9}, 3)
    assert ad == 0 and card > 0
    assert _ad_outflows({(2026, 3): 300_000}, 3)[:2] == (base_ad, base_card)  # 당월 상계는 다음 달에 반영
