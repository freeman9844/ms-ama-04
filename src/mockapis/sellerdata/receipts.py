"""정산 원장(Payout) → 계좌 입금 사건. 시점 차이·광고비 상계·선정산 양도를 넣는다(스펙 §3.1).

상계(deduction_krw)는 여기서 원장에 기록하고, 계좌 입금액은 언제나 상계 뒤 실지급액(net_krw)이다.
rng는 셀러 rng와 따로 받는다(호출자가 시드 + RECEIPT_SEED_OFFSET으로 만든다). 그래야 매출·비용 난수 순서가 그대로다.
"""

import random
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta

from mockapis.sellerdata.categories import TxnCategory
from mockapis.sellerdata.channels import Payout
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


def _top_platform(payouts: Sequence[Payout], since: date) -> str | None:
    gross: dict[str, int] = defaultdict(int)
    for p in payouts:
        if p.code == "PG_SETTLE" and p.paid_on >= since:
            gross[p.channel] += p.gross_krw
    return max(sorted(gross), key=gross.__getitem__) if gross else None


def bank_receipts(
    payouts: Sequence[Payout], as_of: date, rng: random.Random, *, assigned_from: date | None = None
) -> Receipts:
    """원장에 상계를 넣고, 지급을 계좌 입금으로 바꾼다. assigned_from부터 최대 플랫폼 지급은 선정산 선지급으로 대체."""
    base = rng.uniform(*OFFSET_RATE)
    rise = rng.uniform(*OFFSET_RISE_PP)
    rising = rng.random() < OFFSET_RISE_SHARE
    rise_from = as_of - timedelta(days=OFFSET_RISE_DAYS)
    ledger: list[Payout] = []
    for p in sorted(payouts, key=lambda p: (p.paid_on, p.channel, p.description)):
        if p.channel in OFFSET_PLATFORMS:
            rate = base + (rise if rising and p.paid_on >= rise_from else 0.0)
            p = replace(p, deduction_krw=min(round(p.gross_krw * rate), p.gross_krw - p.fee_krw))
        ledger.append(p)
    top = _top_platform(ledger, assigned_from) if assigned_from else None
    events: list[Event] = []
    for p in ledger:
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
    return Receipts(tuple(ledger), tuple(events))
