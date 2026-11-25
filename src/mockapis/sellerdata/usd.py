"""Seeded USD order, payout and cash books for the opt-in synthetic product."""

import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from shared.contracts import USD_V1, UsdTransaction
from shared.krcalendar import add_business_days, is_business_day
from shared.sellers import USD_CHANNEL_NAMES, USD_PAYOUT_BUSINESS_DAYS, UsdSeller
from shared.usd_source import UsdOrderDay, UsdSettlement

FEE = {"naver_smart_store": Decimal(".066"), "coupang": Decimal(".1155"),
       "own_store": Decimal(".034"), "11st": Decimal(".12")}


BASE_RETURN_RATE = Decimal(".02")


def booked(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class UsdPayoutHold:
    """Payouts of one platform due in [held_from, held_to] are withheld and paid together on release_on."""
    platform_code: str
    held_from: date
    held_to: date
    release_on: date


@dataclass(frozen=True)
class UsdBooks:
    transactions: tuple[UsdTransaction, ...]
    settlements: tuple[UsdSettlement, ...]
    orders: tuple[UsdOrderDay, ...]
    pending: tuple[UsdSettlement, ...]


def simulate_usd(seller: UsdSeller, key: str, start: date, end: date, seed: int, opening: int, *,
                 pending=(), slump_on=None, loan_id=None, principal=0, installments=None,
                 return_rate=None, deduction_rate=None, hold: UsdPayoutHold | None = None) -> UsdBooks:
    """Inclusive period. Materialize a payout once; that same net books the bank credit.

    return_rate(order_day) and deduction_rate(payout_day) return Decimal shares; hold withholds one platform's payouts.
    """
    rng = random.Random(seed)
    meta = USD_V1.model_dump()
    balance = opening
    deposits = defaultdict(list)
    for s in pending:
        deposits[s.settled_on].append(s)
    txns, payouts, orders = [], [], []

    def post(amount, description, code, *, platform=None, loan=None, item=None):
        nonlocal balance
        balance += amount
        txns.append(UsdTransaction(**meta, txn_id=f"usd-v1:{key}:{d}:{len(txns)}", booked_on=d,
                                  amount_cents=amount, balance_after_cents=balance, description=description,
                                  txn_type_code=code, platform_code=platform, loan_id=loan, scheduled_item_key=item))

    d = start
    held_total, released = 0, False
    while d <= end:
        if d == start and principal:
            post(principal, "AMALend loan proceeds", "TRANSFER_IN", loan=loan_id)
            post(-principal * 3 // 4, f"{seller.supplier} inventory purchase", "TRANSFER_OUT", loan=loan_id)
        if is_business_day(d):
            daily = seller.weekly_sales_cents * rng.randint(90, 110) // 500
            if slump_on and d >= slump_on:
                daily //= 4
            for platform, percent in seller.channel_percent:
                gross = daily * percent // 100
                returns = booked(gross * (return_rate(d) if return_rate else BASE_RETURN_RATE))
                orders.append(UsdOrderDay(**meta, order_on=d, platform_code=platform,
                                          gross_cents=gross, returns_cents=returns))
                paid_on = add_business_days(d, USD_PAYOUT_BUSINESS_DAYS[platform])
                settleable = gross - returns
                fee = booked(settleable * FEE[platform])
                deduction = booked(settleable * deduction_rate(paid_on)) if deduction_rate else 0
                deposits[paid_on].append(UsdSettlement(**meta, settled_on=paid_on, platform_code=platform,
                                                       gross_cents=settleable, fee_cents=fee,
                                                       deduction_cents=deduction,
                                                       net_cents=settleable - fee - deduction))
            if hold and held_total and not released and d >= hold.release_on:
                released = True
                deposits[d].insert(0, UsdSettlement(**meta, settled_on=d, platform_code=hold.platform_code,
                                                    gross_cents=0, fee_cents=0, released_cents=held_total,
                                                    net_cents=held_total))
            for s in deposits.pop(d, []):
                if (hold and s.platform_code == hold.platform_code and hold.held_from <= d <= hold.held_to
                        and s.net_cents > 0):
                    held_total += s.net_cents
                    s = UsdSettlement(**(s.model_dump() | {"held_cents": s.net_cents, "net_cents": 0}))
                payouts.append(s)
                if s.net_cents:
                    post(s.net_cents, f"{USD_CHANNEL_NAMES[s.platform_code]} settlement", "PG_SETTLE",
                         platform=s.platform_code)
            post(-(seller.weekly_sales_cents // 10), f"{seller.supplier} inventory", "TRANSFER_OUT")
            if d.weekday() == 4:
                post(-(seller.weekly_sales_cents // 10), "Staff payroll", "TRANSFER_OUT")
                if balance > seller.weekly_sales_cents * 2:
                    post(seller.weekly_sales_cents * 2 - balance, "Owner withdrawal", "TRANSFER_OUT")
            if installments and d in installments:
                number, amount = installments[d]
                if balance >= amount:
                    post(-amount, "AMALend loan repayment", "AUTO_DEBIT", loan=loan_id, item=f"i{number}")
        d += timedelta(days=1)
    return UsdBooks(tuple(txns), tuple(payouts), tuple(orders),
                    tuple(s for day in sorted(deposits) for s in deposits[day]))
