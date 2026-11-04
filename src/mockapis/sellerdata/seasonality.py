"""업종별 월 지수, 11월 행사, 요일·명절 효과."""

from datetime import date, timedelta

from shared.krcalendar import festival_days

_MONTHLY: dict[str, dict[int, float]] = {
    "패션의류": {1: 0.82, 2: 0.86, 3: 1.12, 4: 1.15, 5: 1.02, 6: 0.9, 7: 0.84, 8: 0.86, 9: 1.1, 10: 1.18, 11: 1.0,
              12: 1.08},
    "뷰티": {1: 0.9, 2: 0.92, 3: 1.0, 4: 1.02, 5: 1.2, 6: 0.98, 7: 0.92, 8: 0.92, 9: 0.98, 10: 1.0, 11: 1.12,
           12: 1.04},
    "식품": {1: 1.0, 2: 1.0, 3: 0.95, 4: 0.96, 5: 1.0, 6: 0.95, 7: 0.98, 8: 1.0, 9: 1.0, 10: 0.98, 11: 1.0, 12: 1.06},
    "리빙": {1: 0.92, 2: 0.95, 3: 1.08, 4: 1.06, 5: 1.0, 6: 0.95, 7: 0.92, 8: 0.94, 9: 1.02, 10: 1.02, 11: 1.0,
           12: 1.12},
    "디지털": {1: 0.95, 2: 1.05, 3: 1.05, 4: 0.95, 5: 0.98, 6: 0.94, 7: 0.98, 8: 0.98, 9: 0.98, 10: 1.0, 11: 1.05,
            12: 1.08},
    "반려동물": {1: 1.0, 2: 0.96, 3: 1.0, 4: 1.0, 5: 1.02, 6: 0.98, 7: 1.0, 8: 1.0, 9: 1.0, 10: 1.0, 11: 1.02,
             12: 1.02},
    "핸드메이드": {1: 0.85, 2: 0.95, 3: 0.95, 4: 0.92, 5: 1.08, 6: 0.88, 7: 0.85, 8: 0.86, 9: 1.0, 10: 1.08,
              11: 1.15, 12: 1.3},
}
NOVEMBER_EVENT = 1.3  # 11월 쇼핑 행사(11·11, 블랙프라이데이)
# 월~일. 온라인 주문은 일·월요일이 많고 금·토요일이 적다.
WEEKDAY = (1.08, 1.03, 1.0, 0.98, 0.94, 0.9, 1.07)
FOOD_GIFT_WINDOW_DAYS = 21  # 식품: 명절 3주 전부터 선물 수요
FOOD_GIFT_BOOST = 1.45
FESTIVAL_ORDER_DIP = 0.55  # 명절 당일 전후 주문 감소


def month_index(industry: str, year: int, month: int) -> float:
    base = _MONTHLY.get(industry, _MONTHLY["리빙"])[month]
    return base * (NOVEMBER_EVENT if month == 11 else 1.0)


def festival_factor(industry: str, d: date) -> float:
    for fest in (*festival_days(d.year), *festival_days(d.year + 1)):
        if abs((d - fest).days) <= 1:
            return FESTIVAL_ORDER_DIP
        if industry == "식품" and timedelta(days=2) <= fest - d <= timedelta(days=FOOD_GIFT_WINDOW_DAYS):
            return FOOD_GIFT_BOOST
    return 1.0


def day_factor(industry: str, d: date) -> float:
    return month_index(industry, d.year, d.month) * WEEKDAY[d.weekday()] * festival_factor(industry, d)
