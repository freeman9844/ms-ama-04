from collections import Counter

import pytest

from shared.segments import (
    AGE_BANDS,
    LOAD_GRID,
    LOAD_INDUSTRIES,
    LOAD_PER_ARCHETYPE,
    SELLER_INDUSTRIES,
    age_band,
    region_of,
    segment_of,
)


@pytest.mark.parametrize(("months", "band"), [(0, "<1년"), (11, "<1년"), (12, "1~3년"), (35, "1~3년"), (36, "3년+")])
def test_age_band(months, band):
    assert age_band(months) == band and band in AGE_BANDS


def test_region_is_first_word():
    assert region_of("서울 성동구") == "서울" and region_of("부산") == "부산"


def test_segment_of_features():
    f = {"industry": "패션의류", "region": "경기 성남시", "business_age_months": 24, "dscr": 1.2}
    assert segment_of(f) == {"industry": "패션의류", "region": "경기", "business_age_band": "1~3년"}


@pytest.mark.parametrize("bad", [None, {}, {"industry": "패션의류"}, {"industry": "패션의류", "region": "", "business_age_months": "x"}])
def test_segment_of_bad_features_is_none(bad):
    assert segment_of(bad) is None


def test_load_grid_is_balanced_and_interleaved():
    assert len(LOAD_GRID) == 48 and len({a.applicant_id for a in LOAD_GRID}) == 48
    assert [a.archetype for a in LOAD_GRID[:4]] == ["healthy", "mismatch", "stress", "thin"]
    for arch in ("healthy", "mismatch", "stress", "thin"):
        rows = [a for a in LOAD_GRID if a.archetype == arch]
        assert len(rows) == LOAD_PER_ARCHETYPE
        assert set(Counter(a.industry for a in rows).values()) == {2}
        assert set(Counter(a.region for a in rows).values()) == {4}
        assert set(Counter(age_band(a.business_age_months) for a in rows).values()) == {4}
    assert all(a.applicant_id.startswith(f"load-{a.archetype}-") for a in LOAD_GRID)
    assert len({a.seed for a in LOAD_GRID}) == 48


def test_load_amounts_vary_within_product_limit():
    from policy.rules import load_rules

    product = load_rules().product
    amounts = [a.requested_amount_krw for a in LOAD_GRID]
    assert all(product.min_amount_krw <= x <= product.max_amount_krw for x in amounts)
    assert max(amounts) == 10_000_000 and len(set(amounts)) >= 10
    for arch in ("healthy", "mismatch", "stress", "thin"):
        assert len({a.requested_amount_krw for a in LOAD_GRID if a.archetype == arch}) == LOAD_PER_ARCHETYPE


def test_load_amounts_do_not_skew_industry_comparison():
    """업종 공정성 격차가 신청 금액 차이로 생기지 않도록 업종마다 신청액 합이 같다."""
    totals = Counter()
    for a in LOAD_GRID:
        totals[a.industry] += a.requested_amount_krw
    assert len(set(totals.values())) == 1


def test_load_grid_uses_seller_industries():
    assert len(SELLER_INDUSTRIES) == 10 and set(LOAD_INDUSTRIES) <= set(SELLER_INDUSTRIES)
    assert set(Counter(a.industry for a in LOAD_GRID).values()) == {8}
    assert set(Counter(a.region for a in LOAD_GRID).values()) == {16}
    assert set(Counter(age_band(a.business_age_months) for a in LOAD_GRID).values()) == {16}
