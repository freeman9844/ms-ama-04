from collections import Counter

import pytest

from shared.segments import AGE_BANDS, LOAD_GRID, LOAD_PER_ARCHETYPE, age_band, region_of, segment_of


@pytest.mark.parametrize(("months", "band"), [(0, "<1년"), (11, "<1년"), (12, "1~3년"), (35, "1~3년"), (36, "3년+")])
def test_age_band(months, band):
    assert age_band(months) == band and band in AGE_BANDS


def test_region_is_first_word():
    assert region_of("서울 성동구") == "서울" and region_of("부산") == "부산"


def test_segment_of_features():
    f = {"industry": "카페", "region": "경기 성남시", "business_age_months": 24, "dscr": 1.2}
    assert segment_of(f) == {"industry": "카페", "region": "경기", "business_age_band": "1~3년"}


@pytest.mark.parametrize("bad", [None, {}, {"industry": "카페"}, {"industry": "카페", "region": "", "business_age_months": "x"}])
def test_segment_of_bad_features_is_none(bad):
    assert segment_of(bad) is None


def test_load_grid_is_balanced_and_interleaved():
    assert len(LOAD_GRID) == 24 and len({a.applicant_id for a in LOAD_GRID}) == 24
    assert [a.archetype for a in LOAD_GRID[:4]] == ["healthy", "mismatch", "stress", "thin"]
    for arch in ("healthy", "mismatch", "stress", "thin"):
        rows = [a for a in LOAD_GRID if a.archetype == arch]
        assert len(rows) == LOAD_PER_ARCHETYPE
        for axis in ("industry", "region"):
            assert set(Counter(getattr(a, axis) for a in rows).values()) == {2}
        assert set(Counter(age_band(a.business_age_months) for a in rows).values()) == {2}
    assert all(a.applicant_id.startswith(f"load-{a.archetype}-") for a in LOAD_GRID)
    assert len({a.seed for a in LOAD_GRID}) == 24
