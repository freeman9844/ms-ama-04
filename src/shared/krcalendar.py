"""한국 영업일 달력. 주말과 공휴일을 뺀다.

2024~2028년은 설·추석·부처님오신날·대체공휴일·선거일·임시공휴일 표를 쓴다(한국천문연구원 월력요항, 2026-11 확인).
노동절(5/1)·제헌절(7/17)은 2026년부터 공휴일이다. 표 밖 연도는 고정 공휴일만 쓴다.
"""

from datetime import date, timedelta
from functools import cache

_FIXED = ((1, 1), (3, 1), (5, 5), (6, 6), (8, 15), (10, 3), (10, 9), (12, 25))
_FIXED_FROM_2026 = ((5, 1), (7, 17))
_TABLE: dict[int, tuple[str, ...]] = {
    2024: ("02-09", "02-10", "02-11", "02-12", "04-10", "05-06", "05-15", "09-16", "09-17", "09-18", "10-01"),
    2025: ("01-27", "01-28", "01-29", "01-30", "03-03", "05-06", "06-03", "10-05", "10-06", "10-07", "10-08"),
    2026: ("02-16", "02-17", "02-18", "03-02", "05-24", "05-25", "06-03", "08-17", "09-24", "09-25", "09-26",
           "10-05"),
    2027: ("02-06", "02-07", "02-08", "02-09", "05-03", "05-13", "07-19", "08-16", "09-14", "09-15", "09-16",
           "10-04", "10-11", "12-27"),
    2028: ("01-26", "01-27", "01-28", "04-12", "05-02", "10-02", "10-03", "10-04", "10-05"),
}
# 명절 당일(식품 선물 수요·명절 주문 공백 계산용).
SEOLLAL = {2024: date(2024, 2, 10), 2025: date(2025, 1, 29), 2026: date(2026, 2, 17), 2027: date(2027, 2, 7),
           2028: date(2028, 1, 27)}
CHUSEOK = {2024: date(2024, 9, 17), 2025: date(2025, 10, 6), 2026: date(2026, 9, 25), 2027: date(2027, 9, 15),
           2028: date(2028, 10, 3)}


@cache
def holidays(year: int) -> frozenset[date]:
    fixed = _FIXED + (_FIXED_FROM_2026 if year >= 2026 else ())
    days = {date(year, m, d) for m, d in fixed}
    days |= {date.fromisoformat(f"{year}-{md}") for md in _TABLE.get(year, ())}
    return frozenset(days)


def is_business_day(d: date) -> bool:
    return d.weekday() < 5 and d not in holidays(d.year)


def next_business_day(d: date) -> date:
    """d가 영업일이면 d, 아니면 다음 영업일."""
    while not is_business_day(d):
        d += timedelta(days=1)
    return d


def previous_business_day(d: date) -> date:
    while not is_business_day(d):
        d -= timedelta(days=1)
    return d


def add_business_days(d: date, n: int) -> date:
    """d 다음날부터 세어 n번째 영업일."""
    while n > 0:
        d += timedelta(days=1)
        if is_business_day(d):
            n -= 1
    return d


def business_days_between(start: date, end: date) -> int:
    """start~end(양끝 포함) 영업일 수."""
    return sum(is_business_day(start + timedelta(days=i)) for i in range((end - start).days + 1))


def month_add(year: int, month: int, n: int) -> tuple[int, int]:
    idx = year * 12 + month - 1 + n
    return idx // 12, idx % 12 + 1


def festival_days(year: int) -> tuple[date, ...]:
    return tuple(d for d in (SEOLLAL.get(year), CHUSEOK.get(year)) if d)
