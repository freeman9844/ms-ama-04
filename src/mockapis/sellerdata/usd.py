"""Seeded USD order, payout and cash books for the opt-in synthetic product."""

import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from shared.contracts import USD_V1, UsdTransaction
from shared.krcalendar import add_business_days, is_business_day
from shared.sellers import USD_CHANNEL_NAMES, UsdSeller
from shared.usd_source import UsdOrderDay, UsdSettlement

FEE = {"naver_smart_store": Decimal(".066"), "coupang": Decimal(".1155"),
       "own_store": Decimal(".034"), "11st": Decimal(".12")}
LAG = {"naver_smart_store": 5, "coupang": 20, "own_store": 2, "11st": 12}


def booked(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class UsdBooks:
    transactions: tuple[UsdTransaction, ...]
    settlements: tuple[UsdSettlement, ...]
    orders: tuple[UsdOrderDay, ...]
    pending: tuple[UsdSettlement, ...]


def simulate_usd(seller: UsdSeller, key: str, start: date, end: date, seed: int, opening: int, *,
                 pending=(), slump_on=None, loan_id=None, principal=0, installments=None) -> UsdBooks:
    """Inclusive period. Materialize a payout once; that same net books the bank credit."""
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
                returns = booked(gross * Decimal(".02"))
                orders.append(UsdOrderDay(**meta, order_on=d, platform_code=platform,
                                          gross_cents=gross, returns_cents=returns))
                paid_on = add_business_days(d, LAG[platform])
                settleable = gross - returns
                fee = booked(settleable * FEE[platform])
                deposits[paid_on].append(UsdSettlement(**meta, settled_on=paid_on, platform_code=platform,
                                                       gross_cents=settleable, fee_cents=fee,
                                                       net_cents=settleable - fee))
            for s in deposits.pop(d, []):
                payouts.append(s)
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
