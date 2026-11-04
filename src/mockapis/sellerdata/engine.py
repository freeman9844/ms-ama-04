"""셀러 하루하루 흐름: 일별 주문 → 채널 정산 → 비용 일정 → 시간순 거래.

정산 지연 때문에 시작일 이전 주문도 시작일 이후 입금으로 들어온다. 그래서 WARMUP_DAYS만큼 앞서 주문을 만들고
시작일 이후 거래만 남긴다.
"""

import random
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta

from mockapis.sellerdata.categories import TxnCategory
from mockapis.sellerdata.channels import CHANNELS, payouts
from mockapis.sellerdata.costs import CostSchedule, Event
from mockapis.sellerdata.personas import Persona
from mockapis.sellerdata.seasonality import day_factor
from shared.contracts import PgSettlement

WARMUP_DAYS = 75
DAYS_PER_MONTH = 30.4
DAILY_NOISE = 0.18


def _one(d: date) -> float:
    return 1.0


def _ten(year: int, month: int) -> int:
    return 10


@dataclass(frozen=True)
class Ledger:
    events: tuple[Event, ...]
    pg_settlements: tuple[PgSettlement, ...]
    daily_sales: dict[date, int]


def daily_sales(persona: Persona, start: date, end: date, rng: random.Random,
                sales_factor: Callable[[date], float] = _one) -> dict[str, dict[date, int]]:
    """채널별 일 주문액(반품 전)."""
    base = persona.monthly_sales_krw / DAYS_PER_MONTH
    out: dict[str, dict[date, int]] = {c: {} for c, _ in persona.channels}
    d = start
    while d < end:
        total = base * day_factor(persona.industry, d) * sales_factor(d) * rng.uniform(1 - DAILY_NOISE, 1 + DAILY_NOISE)
        for channel, weight in persona.channels:
            out[channel][d] = int(total * weight * rng.uniform(0.9, 1.1))
        d += timedelta(days=1)
    return out


def simulate(
    persona: Persona,
    start: date,
    end: date,
    rng: random.Random,
    *,
    sales_factor: Callable[[date], float] = _one,
    payroll_day: Callable[[int, int], int] = _ten,
    owner_draw_factor: Callable[[date], float] = _one,
    extra: Iterable[Event] = (),
) -> Ledger:
    """[start, end) 기간의 거래 사건. 같은 날은 입금 먼저, 출금 나중."""
    by_channel = daily_sales(persona, start - timedelta(days=WARMUP_DAYS), end, rng, sales_factor)
    events: list[Event] = []
    settlements: list[PgSettlement] = []
    for channel, sales in by_channel.items():
        for p in payouts(CHANNELS[channel], persona.industry, sales, rng):
            if not start <= p.paid_on < end:
                continue
            events.append(Event(p.paid_on, p.net_krw, p.description, p.code, TxnCategory.REVENUE))
            if p.code == "PG_SETTLE":
                settlements.append(PgSettlement(settled_on=p.paid_on, platform=p.channel, gross_krw=p.gross_krw,
                                                fee_krw=p.fee_krw, net_krw=p.net_krw))
    total: dict[date, int] = {}
    for sales in by_channel.values():
        for d, v in sales.items():
            total[d] = total.get(d, 0) + v
    events += CostSchedule(persona, start, end, total, rng, payroll_day, owner_draw_factor).build()
    events += [e for e in extra if start <= e.booked_on < end]
    events.sort(key=lambda e: (e.booked_on, e.amount_krw < 0))
    return Ledger(tuple(events), tuple(sorted(settlements, key=lambda s: s.settled_on)), total)
