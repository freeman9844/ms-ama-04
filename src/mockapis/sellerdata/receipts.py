"""정산 원장(Payout) → 계좌 입금 사건. 시점 차이·광고비 상계·선정산 양도를 넣는다(스펙 §3.1).

상계(deduction_krw)는 여기서 원장에 기록하고, 계좌 입금액은 언제나 상계 뒤 실지급액(net_krw)이다.
rng는 셀러 rng와 따로 받는다(호출자가 시드 + RECEIPT_SEED_OFFSET으로 만든다). 그래야 매출·비용 난수 순서가 그대로다.
"""

import random
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta

from mockapis.sellerdata.categories import TxnCategory
from mockapis.sellerdata.channels import CHANNELS, Payout
from mockapis.sellerdata.costs import Event
from shared.krcalendar import add_business_days

DELAY_SHARE = 0.15
DELAY_BUSINESS_DAYS = (1, 2)
OFFSET_PLATFORMS = frozenset({"쿠팡", "스마트스토어", "11번가"})
OFFSET_RATE = (0.01, 0.04)
OFFSET_RISE_SHARE = 0.25
OFFSET_RISE_PP = (0.015, 0.025)
OFFSET_RISE_DAYS = 90
ADVANCE_RATIO = (0.83, 0.87)
ADVANCE_LEAD_DAYS = (5, 7)
ADVANCE_DESCRIPTION = "한결선정산 선지급"


@dataclass(frozen=True)
class Receipts:
    payouts: tuple[Payout, ...]
    events: tuple[Event, ...]


@dataclass(frozen=True)
class ReceiptHooks:
    """사후 시나리오가 기본 정산 흐름에 얹는 훅(Task 3). 기본값은 기존 동작과 같다(2026-11-11).

    extra_offset: OFFSET_PLATFORMS 상계율에 더한다(비용 증가 시나리오).
    hold: 참이면 그 지급은 held_krw = 상계 뒤 net이 되고 net 0(계좌 사건 없음).
    release_on: 보류가 있었으면 그날 플랫폼별로 해제 행 1개(누적 보류를 released_krw로 입금).
    advance_platform: 있으면 _top_platform 대신 이 플랫폼을 선정산 양도 대상으로 쓴다.
    """

    extra_offset: Callable[[date], float] = lambda d: 0.0
    hold: Callable[[Payout], bool] = lambda p: False
    release_on: date | None = None
    advance_platform: str | None = None


def _top_platform(payouts: Sequence[Payout], since: date) -> str | None:
    gross: dict[str, int] = defaultdict(int)
    for p in payouts:
        if p.code == "PG_SETTLE" and p.paid_on >= since:
            gross[p.channel] += p.gross_krw
    return max(sorted(gross), key=gross.__getitem__) if gross else None


def bank_receipts(
    payouts: Sequence[Payout], as_of: date, rng: random.Random, *, assigned_from: date | None = None,
    hooks: ReceiptHooks = ReceiptHooks(),
) -> Receipts:
    """원장에 상계를 넣고, 지급을 계좌 입금으로 바꾼다. assigned_from부터 최대 플랫폼 지급은 선정산 선지급으로 대체.

    hooks.hold가 참인 지급은 보류(held_krw)로 바뀌어 입금되지 않고, hooks.release_on에 플랫폼별로 한 번에 풀린다.
    보류·해제 처리는 난수를 쓰지 않아 hooks가 기본값일 때 기존 난수 호출 순서가 그대로 유지된다.
    """
    base = rng.uniform(*OFFSET_RATE)
    rise = rng.uniform(*OFFSET_RISE_PP)
    rising = rng.random() < OFFSET_RISE_SHARE
    rise_from = as_of - timedelta(days=OFFSET_RISE_DAYS)
    ledger: list[Payout] = []
    held_totals: dict[str, int] = defaultdict(int)
    for p in sorted(payouts, key=lambda p: (p.paid_on, p.channel, p.description)):
        if p.channel in OFFSET_PLATFORMS:
            rate = base + (rise if rising and p.paid_on >= rise_from else 0.0) + hooks.extra_offset(p.paid_on)
            p = replace(p, deduction_krw=min(round(p.gross_krw * rate), p.gross_krw - p.fee_krw))
        if hooks.hold(p):
            held_totals[p.channel] += p.net_krw
            p = replace(p, held_krw=p.net_krw)
        ledger.append(p)
    top = hooks.advance_platform or (_top_platform(ledger, assigned_from) if assigned_from else None)
    events: list[Event] = []
    for p in ledger:
        if p.held_krw > 0:
            continue  # 보류된 지급은 계좌 사건을 만들지 않는다(난수도 쓰지 않는다)
        if top is not None and assigned_from is not None and p.channel == top and p.paid_on >= assigned_from:
            amount = round(p.net_krw * rng.uniform(*ADVANCE_RATIO))
            lead = rng.randint(*ADVANCE_LEAD_DAYS)
            if amount > 0:
                events.append(Event(p.paid_on - timedelta(days=lead), amount, ADVANCE_DESCRIPTION, "TRANSFER_IN",
                                    TxnCategory.LOAN_PROCEEDS))
            continue
        booked = p.paid_on
        if rng.random() < DELAY_SHARE:
            booked = add_business_days(booked, rng.randint(*DELAY_BUSINESS_DAYS))
        events.append(Event(booked, p.net_krw, p.description, p.code, TxnCategory.REVENUE))
    if hooks.release_on is not None:
        for channel_name in sorted(held_totals):
            amount = held_totals[channel_name]
            if amount <= 0:
                continue
            channel = CHANNELS[channel_name]
            release = Payout(hooks.release_on, channel_name, f"{channel.payer} 보류 해제", channel.code, 0, 0,
                             released_krw=amount)
            ledger.append(release)
            events.append(Event(hooks.release_on, amount, release.description, release.code, TxnCategory.REVENUE))
    return Receipts(tuple(ledger), tuple(events))
