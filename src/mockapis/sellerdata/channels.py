"""판매 채널별 수수료·반품률·정산 일정(2026년 공개 기준의 근사).

| 채널 | 적요 | 정산 |
|---|---|---|
| 스마트스토어 | 네이버파이낸셜 | 구매확정(주문 후 5~9일) 다음 영업일 |
| 쿠팡 | 쿠팡(주) | 주정산: 일요일 마감 후 15영업일에 70%, 익익월 첫 영업일에 나머지 30%(최종액) |
| 11번가 | 11번가 | 월 2회: 1~15일분 → 25일, 16일~말일분 → 다음 달 10일 |
| 자사몰 | 토스페이먼츠 | 카드 결제 D+2영업일 |
"""

import random
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta

from shared.krcalendar import add_business_days, month_add, next_business_day
from shared.contracts import TxnTypeCode


@dataclass(frozen=True)
class Channel:
    name: str
    payer: str
    code: TxnTypeCode
    fee_rate: float
    return_rate: float


SMARTSTORE = Channel("스마트스토어", "네이버파이낸셜", "PG_SETTLE", 0.066, 0.04)
COUPANG = Channel("쿠팡", "쿠팡(주)", "PG_SETTLE", 0.1155, 0.05)
ELEVENST = Channel("11번가", "11번가", "PG_SETTLE", 0.12, 0.04)
OWNMALL = Channel("자사몰", "토스페이먼츠", "CARD_SETTLE", 0.034, 0.02)
CHANNELS: dict[str, Channel] = {c.name: c for c in (SMARTSTORE, COUPANG, ELEVENST, OWNMALL)}
# 쿠팡 판매수수료는 카테고리마다 다르다(VAT 포함 근사).
COUPANG_FEE_BY_INDUSTRY = {"패션의류": 0.1155, "뷰티": 0.1056, "식품": 0.1166, "리빙": 0.1188, "디지털": 0.0858,
                           "반려동물": 0.1188, "핸드메이드": 0.1188}
COUPANG_FIRST_SHARE = 0.7
COUPANG_WEEKLY_LAG_BD = 15
DESCRIPTIONS = {
    "스마트스토어": "네이버파이낸셜 정산",
    "11번가": "11번가 정산대금",
    "자사몰": "토스페이먼츠 정산",
    "쿠팡:weekly": "쿠팡(주) 주정산",
    "쿠팡:final": "쿠팡(주) 최종액정산",
}


@dataclass(frozen=True)
class Payout:
    paid_on: date
    channel: str
    description: str
    code: TxnTypeCode
    gross_krw: int
    fee_krw: int
    deduction_krw: int = 0  # 광고비 상계(receipts.bank_receipts가 정한다)

    @property
    def net_krw(self) -> int:
        return self.gross_krw - self.fee_krw - self.deduction_krw


def fee_rate(channel: Channel, industry: str) -> float:
    return COUPANG_FEE_BY_INDUSTRY.get(industry, channel.fee_rate) if channel is COUPANG else channel.fee_rate


def _sunday_close(d: date) -> date:
    return d + timedelta(days=6 - d.weekday())


def payout_schedule(channel: Channel, order_day: date, rng: random.Random) -> tuple[tuple[date, float, str], ...]:
    """주문일 하루치 매출이 언제 몇 비율로 들어오는지: (지급일, 비율, 적요 키)."""
    if channel is SMARTSTORE:
        # 하루 주문이 고객 구매확정 시점에 따라 세 묶음으로 나뉜다.
        parts = sorted(rng.sample(range(5, 10), 3))
        return tuple((add_business_days(order_day + timedelta(days=p), 1), 1 / 3, channel.name) for p in parts)
    if channel is OWNMALL:
        return ((add_business_days(order_day, 2), 1.0, channel.name),)
    if channel is ELEVENST:
        if order_day.day <= 15:
            return ((next_business_day(order_day.replace(day=25)), 1.0, channel.name),)
        y, m = month_add(order_day.year, order_day.month, 1)
        return ((next_business_day(date(y, m, 10)), 1.0, channel.name),)
    close = _sunday_close(order_day)
    y, m = month_add(close.year, close.month, 2)
    return (
        (add_business_days(close, COUPANG_WEEKLY_LAG_BD), COUPANG_FIRST_SHARE, "쿠팡:weekly"),
        (next_business_day(date(y, m, 1)), 1 - COUPANG_FIRST_SHARE, "쿠팡:final"),
    )


def payouts(
    channel: Channel, industry: str, daily_sales: Mapping[date, int], rng: random.Random
) -> list[Payout]:
    """채널 일별 주문액(반품 전) → 지급일별 정산(반품·수수료 차감)."""
    rate = fee_rate(channel, industry)
    acc: dict[tuple[date, str], float] = defaultdict(float)
    for day in sorted(daily_sales):
        kept = daily_sales[day] * (1 - channel.return_rate * rng.uniform(0.6, 1.4))
        for paid_on, share, key in payout_schedule(channel, day, rng):
            acc[(paid_on, key)] += kept * share
    out = []
    for (paid_on, key), gross in sorted(acc.items()):
        gross_krw = int(gross)
        out.append(Payout(paid_on, channel.name, DESCRIPTIONS[key], channel.code, gross_krw, round(gross_krw * rate)))
    return out
