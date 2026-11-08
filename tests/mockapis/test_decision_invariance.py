"""셀러 데이터를 바꿔도 시나리오별 정책 판정·사유 코드·금리 구간은 그대로다(P9 스펙 10.3)."""

from datetime import date

import pytest

from agents.features import compute_underwriting
from mockapis.demo import build_demo_cases, build_load_cases
from policy.engine import evaluate
from policy.rules import load_rules
from shared.segments import LOAD_GRID

AS_OF = date(2026, 11, 1)
DEMO_AMOUNT_KRW = 3_300_000
LOAD_AMOUNT_KRW = 1_700_000
DEMO_EXPECTED = {
    "seller-dailylook-01": ("eligible", (), "A"),
    "seller-mangwon-02": ("eligible", (), "A"),
    "seller-glowlab-03": ("refer", ("R105", "R106"), "A"),
    "seller-knitroom-04": ("ineligible", ("R001", "R104", "R107"), None),
    "seller-candle-05": ("refer", ("R107",), "A"),
    "seller-gearstore-06": ("eligible", (), "A"),
    "seller-petfood-07": ("eligible", (), "A"),
    "seller-ceramic-08": ("refer", ("R107",), "A"),
    "seller-kimchi-09": ("eligible", (), "A"),
    "seller-linen-10": ("eligible", (), "A"),
    "seller-skinlab-11": ("refer", ("R105", "R106"), "A"),
    "seller-kitchen-12": ("eligible", (), "A"),
    "seller-cable-13": ("refer", ("R105", "R106"), "A"),
    "seller-leather-14": ("ineligible", ("R001", "R104", "R107"), None),
    "seller-tangerine-15": ("eligible", (), "A"),
    "seller-petcare-16": ("eligible", (), "A"),
    "seller-tint-17": ("ineligible", ("R001", "R104", "R107"), None),
    "seller-sofa-18": ("refer", ("R105", "R106"), "A"),
    "seller-streetfit-19": ("ineligible", ("R001", "R104"), None),
    "seller-bakery-20": ("eligible", (), "A"),
    "seller-mugshop-21": ("ineligible", ("R002", "R106", "R108", "R901"), None),
}
LOAD_EXPECTED = {
    "healthy": ("eligible", (), "A"),
    "mismatch": ("eligible", (), "A"),
    "stress": ("refer", ("R105", "R106"), "A"),
    "thin": ("ineligible", ("R001",), None),
}


def _decision(case, amount):
    uw = compute_underwriting(case.profile.applicant_id, case.profile, case.transactions, case.tax_filings,
                              as_of=AS_OF, requested_amount_krw=amount, requested_tenor_months=12,
                              pg_settlements=case.pg_settlements)
    p = evaluate(uw.features, load_rules())
    return p.decision.value, tuple(sorted(r.code for r in p.reason_codes)), p.rate_band


def _with_young_business(expected):
    """업력 12개월 미만이면 R104가 더해지고, 적격은 회부가 된다."""
    decision, codes, band = expected
    return ("refer" if decision == "eligible" else decision), tuple(sorted((*codes, "R104"))), band


@pytest.fixture(scope="module")
def load_cases():
    return build_load_cases(AS_OF)


@pytest.mark.parametrize("applicant_id", list(DEMO_EXPECTED))
def test_demo_seller_decisions_unchanged(applicant_id):
    case = build_demo_cases(AS_OF)[applicant_id]
    assert _decision(case, DEMO_AMOUNT_KRW) == DEMO_EXPECTED[applicant_id]


def test_load_seller_decisions_unchanged(load_cases):
    for a in LOAD_GRID:
        expected = LOAD_EXPECTED[a.archetype]
        if a.business_age_months < 12:
            expected = _with_young_business(expected)
        assert _decision(load_cases[a.applicant_id], LOAD_AMOUNT_KRW) == expected, a.applicant_id


def test_load_grid_amounts_keep_decision_and_reasons(load_cases):
    """부하 신청 금액(50만~1,000만)이 달라도 판정·사유는 원형대로다. 금리 구간만 금액에 따라 내려갈 수 있다."""
    for a in LOAD_GRID:
        expected = LOAD_EXPECTED[a.archetype]
        if a.business_age_months < 12:
            expected = _with_young_business(expected)
        assert _decision(load_cases[a.applicant_id], a.requested_amount_krw)[:2] == expected[:2], a.applicant_id
