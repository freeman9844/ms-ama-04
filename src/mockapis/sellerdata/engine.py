"""셀러 하루하루 흐름: 일별 주문 → 채널 정산 → 비용 일정 → 시간순 거래.

정산 지연 때문에 시작일 이전 주문도 시작일 이후 입금으로 들어온다. 그래서 WARMUP_DAYS만큼 앞서 주문을 만들고
시작일 이후 거래만 남긴다.
"""

import random
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta

from mockapis.sellerdata.categories import TxnCategory
from mockapis.sellerdata.channels import CHANNELS, OrderDay, _same_rate, payouts
from mockapis.sellerdata.costs import CostSchedule, Event
from mockapis.sellerdata.personas import Persona
from mockapis.sellerdata.receipts import ReceiptHooks, bank_receipts
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
    orders: tuple[OrderDay, ...] = ()  # 워밍업 포함 전체(2026-11-11)


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
    receipt_rng: random.Random | None = None,
    assigned_from: date | None = None,
    return_rate: Callable[[date, float], float] = _same_rate,
    receipt_hooks: ReceiptHooks | None = None,
    warmup_days: int = WARMUP_DAYS,
    ledger_from: date | None = None,
) -> Ledger:
    """[start, end) 기간의 거래 사건. 같은 날은 입금 먼저, 출금 나중.

    receipt_rng가 있으면 정산 원장과 계좌 입금을 분리한다(receipts.bank_receipts: 시점 차이·상계·양도).
    없으면 원장 그대로 입금한다(사후 재생).
    return_rate·receipt_hooks는 사후 시나리오 훅(반품률·보류·추가 상계·선정산 플랫폼, 2026-11-11)이고
    기본값에서는 난수 호출 순서·값이 바뀌지 않는다. warmup_days·ledger_from은 원장 워밍업 길이를 늘려
    대출 전 기준선을 만들 때 쓴다(심사 원장과 난수가 섞이지 않게 pg_settlements만 더 이른 날짜까지 남긴다).
    """
    by_channel = daily_sales(persona, start - timedelta(days=warmup_days), end, rng, sales_factor)
    orders: list[OrderDay] = []
    paid = [p for channel, sales in by_channel.items()
            for p in payouts(CHANNELS[channel], persona.industry, sales, rng, return_rate=return_rate, orders=orders)
            if p.paid_on < end]
    settlement_floor = ledger_from or start
    if receipt_rng is None:
        full_ledger = paid
        ledger = [p for p in full_ledger if p.paid_on >= start]
        events = [Event(p.paid_on, p.net_krw, p.description, p.code, TxnCategory.REVENUE) for p in ledger]
    else:
        # 시작일 직전 달 상계도 필요하다(첫 달 광고비 보정). 그래서 워밍업 지급까지 반영기에 넣고 기간 안만 남긴다.
        r = bank_receipts(paid, end, receipt_rng, assigned_from=assigned_from, hooks=receipt_hooks or ReceiptHooks())
        full_ledger = list(r.payouts)
        ledger = [p for p in full_ledger if p.paid_on >= start]
        events = [e for e in r.events if start <= e.booked_on < end]
    settlements = [
        PgSettlement(settled_on=p.paid_on, platform=p.channel, gross_krw=p.gross_krw, fee_krw=p.fee_krw,
                     deduction_krw=p.deduction_krw, held_krw=p.held_krw, released_krw=p.released_krw,
                     net_krw=p.net_krw)
        for p in full_ledger if p.code == "PG_SETTLE" and p.paid_on >= settlement_floor
    ]
    total: dict[date, int] = {}
    for sales in by_channel.values():
        for d, v in sales.items():
            total[d] = total.get(d, 0) + v
    ad_deductions: dict[tuple[int, int], int] | None = None
    if receipt_rng is not None:
        ad_deductions = {}
        for p in full_ledger:
            key = (p.paid_on.year, p.paid_on.month)
            ad_deductions[key] = ad_deductions.get(key, 0) + p.deduction_krw
    events += CostSchedule(persona, start, end, total, rng, payroll_day, owner_draw_factor, ad_deductions).build()
    events += [e for e in extra if start <= e.booked_on < end]
    events.sort(key=lambda e: (e.booked_on, e.amount_krw < 0))
    return Ledger(tuple(events), tuple(sorted(settlements, key=lambda s: s.settled_on)), total, tuple(orders))
