from datetime import date, timedelta

import pytest

from shared.krcalendar import is_business_day
from shared.repayment import due_dates

START = date(2026, 11, 2)


def test_due_dates_for_demo_start():
    # 12/2(수), 1/1 공휴일 → 1/4(월), 1/31(일)은 범위(1/31) 안이지만 다음 영업일 2/1이 넘쳐 앞 영업일 1/29(금)
    assert due_dates(START, 91) == [date(2026, 12, 2), date(2027, 1, 4), date(2027, 1, 29)]


def test_due_dates_short_ranges():
    assert due_dates(START, 30) == []
    assert due_dates(START, 31) == [date(2026, 12, 2)]


@pytest.mark.parametrize("start", [date(2026, 1, 5) + timedelta(weeks=k) for k in range(52)])
def test_three_business_dues_inside_range(start):
    dues = due_dates(start, 91)
    assert len(dues) == 3 and dues == sorted(set(dues))
    assert all(is_business_day(d) and start < d <= start + timedelta(days=90) for d in dues)
