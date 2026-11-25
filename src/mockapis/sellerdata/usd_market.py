"""USD marketplace performance per platform, derived from the seller's books.

The market stream is its own `Random(f"market:{seed}")`, so the bank, payout and tax books never move.
90-day sales and pending payouts come straight from the books; cancellations are added only here.
"""

import random
from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal

from shared.contracts import USD_V1, StorePenalty
from shared.krcalendar import month_add
from shared.sellers import UsdSeller
from shared.usd_source import UsdMarketplace, UsdOrderDay, UsdSettlement, UsdStorePlatform

WINDOW_DAYS = 90
MAIN_TENURE_MONTHS = (20, 60)
MAIN_TENURE_BACK_OFF_MONTHS = (0, 6)
OTHER_TENURE_PERCENT = (45, 85)
OWN_STORE_TENURE_PERCENT = (25, 50)
MIN_TENURE_MONTHS = 6
RATING_STEPS = (-0.2, -0.1, 0, 0.1)
RATING_CAP = 4.9
CANCEL_JITTER_PERCENT = (70, 130)
REVIEW_PERCENT = (3, 8)
ASSIGNED_ADVANCE_PERCENT = 85
PREMIUM_ORDER_VALUE_CENTS = 4_000_000

CANCEL_RATE = {"Apparel": Decimal(".025"), "Electronics": Decimal(".02"), "Beauty": Decimal(".015"),
               "Home and living": Decimal(".012"), "Food": Decimal(".01"), "Pet supplies": Decimal(".01"),
               "Handmade": Decimal(".008")}
DEFAULT_CANCEL_RATE = Decimal(".012")
AVERAGE_ORDER_CENTS = {"Food": 2800, "Apparel": 4500, "Beauty": 3200, "Handmade": 5500, "Home and living": 6000,
                       "Electronics": 8500, "Pet supplies": 4000}
DEFAULT_AVERAGE_ORDER_CENTS = 4000


def _months_before(as_of: date, months: int) -> date:
    y, m = month_add(as_of.year, as_of.month, -months)
    return date(y, m, min(as_of.day, 28))


def _grade(tenure: int, rating: float, order_value_cents: int) -> str:
    if tenure >= 36 and rating >= 4.7 and order_value_cents >= PREMIUM_ORDER_VALUE_CENTS:
        return "Premium"
    if tenure >= 24 and rating >= 4.5:
        return "Gold"
    return "Silver" if tenure >= 12 else "Standard"


def build_usd_market(seller: UsdSeller, as_of: date, seed: int, orders: Sequence[UsdOrderDay],
                     pending: Sequence[UsdSettlement], penalties: tuple[StorePenalty, ...]) -> UsdMarketplace:
    rng = random.Random(f"market:{seed}")
    since = as_of - timedelta(days=WINDOW_DAYS)
    main_tenure = seller.market_months or min(max(
        seller.business_age_months - rng.randint(*MAIN_TENURE_BACK_OFF_MONTHS), MAIN_TENURE_MONTHS[0]),
        MAIN_TENURE_MONTHS[1])
    rate = seller.cancel_rate if seller.cancel_rate is not None else (
        CANCEL_RATE.get(seller.industry, DEFAULT_CANCEL_RATE) * rng.randint(*CANCEL_JITTER_PERCENT) / 100)
    average_order = AVERAGE_ORDER_CENTS.get(seller.industry, DEFAULT_AVERAGE_ORDER_CENTS)
    platforms = []
    for index, (code, _) in enumerate(seller.channel_percent):
        recent = [o for o in orders if o.platform_code == code and o.order_on >= since]
        sales = sum(o.gross_cents for o in recent)
        cancel = int((sales * rate / (1 - rate)).to_integral_value())
        total = sales + cancel
        percent = OWN_STORE_TENURE_PERCENT if code == "own_store" else OTHER_TENURE_PERCENT
        tenure = main_tenure if index == 0 else max(MIN_TENURE_MONTHS, main_tenure * rng.randint(*percent) // 100)
        rating = seller.rating
        if index:
            rating = max(1.0, min(5.0, round(seller.rating + rng.choice(RATING_STEPS), 1)))
            if seller.rating <= RATING_CAP:
                rating = min(rating, RATING_CAP)
        count = total // average_order
        held = sum(s.net_cents for s in pending if s.platform_code == code)
        platforms.append(UsdStorePlatform(
            **USD_V1.model_dump(), platform_code=code, opened_on=_months_before(as_of, tenure), rating=rating,
            review_count=count * tenure * rng.randint(*REVIEW_PERCENT) // 300, gross_90d_cents=total,
            return_90d_cents=sum(o.returns_cents for o in recent), cancel_90d_cents=cancel,
            pending_settlement_cents=held, orders_90d=count, seller_grade=_grade(tenure, rating, total),
            assigned_pending_cents=held * ASSIGNED_ADVANCE_PERCENT // 100 if index == 0 and seller.risk == "assigned" else 0))
    return UsdMarketplace(**USD_V1.model_dump(), as_of=as_of, platforms=tuple(platforms), penalties=penalties)
