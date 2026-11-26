"""Weekly USD marketplace snapshot generator, replayed over the main platform's post-loan hooks.

The market stream uses its own `Random(f"market-flow:{seed}")`, so the bank, payout and tax books never move.
Cancel rate, rating and grade start from the underwriting-time snapshot (`UsdMarketplace`); scenario hooks
nudge only the main platform (`base.platforms[0]`), the same channel `postloan._usd_scenario` already singles out.
"""

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from mockapis.sellerdata.usd_market import AVERAGE_ORDER_CENTS, DEFAULT_AVERAGE_ORDER_CENTS
from shared.contracts import USD_V1, StorePenalty
from shared.krcalendar import add_business_days
from shared.sellers import USD_PAYOUT_BUSINESS_DAYS
from shared.usd_source import GRADES, UsdMarketFlow, UsdMarketplace, UsdMarketWeek, UsdOrderDay, UsdSettlement

PRE_LOAN_WEEKS = 7  # 반복은 k=-7..0이라 대출 전 주는 8주다(k=0은 대출 시작 전날에 끝나는 주). 이름은 -7까지의 거리다
POST_LOAN_WEEKS = 13
JITTER_PERCENT = (85, 115)


@dataclass(frozen=True)
class MarketHooks:
    """One cause's effect on the main platform only; every lever defaults to a no-op."""

    cancel_mult: Callable[[date], float] = lambda d: 1.0
    rating_delta: Callable[[date], float] = lambda d: 0.0
    grade_steps: Callable[[date], int] = lambda d: 0
    penalties: tuple[StorePenalty, ...] = ()


def build_usd_market_flow(base: UsdMarketplace, start: date, orders: Sequence[UsdOrderDay],
                          settlements: Sequence[UsdSettlement], hooks: MarketHooks, seed: int,
                          industry: str) -> UsdMarketFlow:
    rng = random.Random(f"market-flow:{seed}")
    main_code = base.platforms[0].platform_code
    average_order = AVERAGE_ORDER_CENTS.get(industry, DEFAULT_AVERAGE_ORDER_CENTS)
    weeks = []
    for k in range(-PRE_LOAN_WEEKS, POST_LOAN_WEEKS + 1):
        week_end = start + timedelta(days=7 * k - 1)
        week_start = week_end - timedelta(days=6)
        for platform in base.platforms:
            code = platform.platform_code
            is_main = code == main_code
            gross = sum(o.gross_cents for o in orders
                       if o.platform_code == code and week_start <= o.order_on <= week_end)
            rate = (platform.cancel_90d_cents / platform.gross_90d_cents) if platform.gross_90d_cents else 0.0
            jitter = rng.randint(*JITTER_PERCENT) / 100
            mult = hooks.cancel_mult(week_end) if is_main else 1.0
            cancel = round(gross * rate / (1 - rate) * jitter * mult) if rate < 1 else 0
            delta = round(hooks.rating_delta(week_end), 1) if is_main else 0.0
            rating = max(1.0, min(5.0, platform.rating + delta))
            steps = hooks.grade_steps(week_end) if is_main else 0
            grade = GRADES[max(0, GRADES.index(platform.seller_grade) - steps)]
            payout_days = USD_PAYOUT_BUSINESS_DAYS.get(code, 0)
            future_cutoff = add_business_days(week_end, payout_days)
            pending = sum(s.net_cents for s in settlements
                         if s.platform_code == code and week_end < s.settled_on <= future_cutoff)
            pending += sum(s.held_cents - s.released_cents for s in settlements
                          if s.platform_code == code and s.settled_on <= week_end)
            penalties = tuple(p for p in hooks.penalties
                             if is_main and week_start <= p.occurred_on <= week_end)
            weeks.append(UsdMarketWeek(
                **USD_V1.model_dump(), week_end=week_end, platform_code=code, orders=gross // average_order,
                gross_cents=gross, cancel_cents=cancel, rating=rating, seller_grade=grade,
                pending_settlement_cents=pending, penalties=penalties))
    return UsdMarketFlow(**USD_V1.model_dump(), weeks=tuple(weeks))
