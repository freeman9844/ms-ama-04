"""마켓 셀러 성과 합성(정책 v4, 스펙 4.1·8.1).

계좌·정산 합성과 난수를 섞지 않는다. 호출자가 `Random(seed + MARKETPLACE_SEED_OFFSET)`를 simulate 뒤에 만들어 넘긴다.
90일 수치와 지급 예정액은 셀러 엔진 원장(Ledger)에서 그대로 가져와 계좌 정산 입금과 숫자가 맞는다.
"""

import random
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING

from mockapis.sellerdata.channels import CHANNELS
from mockapis.sellerdata.engine import Ledger
from mockapis.sellerdata.personas import Persona
from shared.contracts import MarketplaceStore, StorePenalty, StorePlatform
from shared.krcalendar import month_add

if TYPE_CHECKING:
    from mockapis.synth import Archetype

MARKETPLACE_SEED_OFFSET = 7000
WINDOW_DAYS = 90
MIN_TENURE_MONTHS = 24
THIN_TENURE_MONTHS = 24
WARNING_DAYS_AGO = (20, 80)
GOOD_GRADE_RATING = 4.5


@dataclass(frozen=True)
class StoreOverride:
    """데모 셀러별 덮어쓰기. 평점은 합성 값을 대체하고, 판매정지·반품률은 계좌 합성에도 훅으로 반영된다."""

    rating: float | None = None
    suspended_days_ago: int | None = None
    suspension_days: int = 7
    return_rate_90d: float | None = None


@dataclass(frozen=True)
class _Defaults:
    tenure: Callable[[int], int]  # 업력(개월) → 가장 오래된 입점 기간
    rating: tuple[float, float]
    reviews: tuple[int, int]
    cancel: tuple[float, float]
    warning: bool = False


def _at_least_two_years(age: int) -> int:
    return max(age, MIN_TENURE_MONTHS)


def _same(age: int) -> int:
    return age


def _thin(age: int) -> int:
    return THIN_TENURE_MONTHS


# 키는 Archetype 값(synth와 순환 import를 피하려고 문자열로 둔다).
DEFAULTS: dict[str, _Defaults] = {
    "healthy_seasonal": _Defaults(_at_least_two_years, (4.5, 4.9), (300, 3000), (0.01, 0.03)),
    "tax_mismatch": _Defaults(_at_least_two_years, (4.5, 4.9), (300, 3000), (0.01, 0.03)),
    "stressed": _Defaults(_same, (4.2, 4.5), (200, 1500), (0.03, 0.06), warning=True),
    "thin_file": _Defaults(_thin, (4.6, 4.9), (150, 800), (0.01, 0.03)),
    "assigned": _Defaults(_same, (4.4, 4.7), (300, 1500), (0.02, 0.04)),
}


def _pg_channels(persona: Persona) -> list[tuple[str, float]]:
    return [(c, w) for c, w in persona.channels if CHANNELS[c].code == "PG_SETTLE"]


def _primary(persona: Persona) -> str | None:
    pg = _pg_channels(persona)
    return max(pg, key=lambda cw: cw[1])[0] if pg else None


def _months_before(as_of: date, months: int) -> date:
    y, m = month_add(as_of.year, as_of.month, -months)
    return date(y, m, min(as_of.day, 28))


def marketplace_hooks(override: StoreOverride | None, persona: Persona, as_of: date) -> dict[str, Callable]:
    """simulate에 넘길 `channel_factor`·`return_rate` 키워드 인자. 계좌에 영향 없는 덮어쓰기면 빈 dict."""
    hooks: dict[str, Callable] = {}
    if override is None:
        return hooks
    primary = _primary(persona)
    if override.suspended_days_ago is not None and primary is not None:
        begin = as_of - timedelta(days=override.suspended_days_ago)
        end = begin + timedelta(days=override.suspension_days)

        def channel_factor(channel: str, day: date) -> float:
            return 0.0 if channel == primary and begin <= day < end else 1.0

        hooks["channel_factor"] = channel_factor
    if override.return_rate_90d is not None:
        since = as_of - timedelta(days=WINDOW_DAYS)
        rate = override.return_rate_90d
        hooks["return_rate"] = lambda day, base: rate if day >= since else base
    return hooks


def _assigned_platform(ledger: Ledger, assigned_from: date) -> str | None:
    """receipts._top_platform과 같은 규칙: 양도일 이후 정산 총액이 가장 큰 플랫폼(동률이면 이름순 마지막)."""
    gross: dict[str, int] = defaultdict(int)
    for s in ledger.pg_settlements:
        if s.settled_on >= assigned_from:
            gross[s.platform] += s.gross_krw
    return max(sorted(gross), key=gross.__getitem__) if gross else None


def synthesize_store(
    persona: Persona,
    archetype: "Archetype",
    as_of: date,
    business_age_months: int,
    ledger: Ledger,
    assigned_from: date | None,
    rng: random.Random,
    override: StoreOverride | None = None,
) -> MarketplaceStore:
    pg = _pg_channels(persona)
    primary = _primary(persona)
    defaults = DEFAULTS[archetype]
    tenure = defaults.tenure(business_age_months)
    since = as_of - timedelta(days=WINDOW_DAYS)
    gross: dict[str, int] = defaultdict(int)
    returns: dict[str, int] = defaultdict(int)
    order_days: dict[str, int] = defaultdict(int)
    for o in ledger.orders:
        if since <= o.day < as_of:
            gross[o.channel] += o.gross_krw
            returns[o.channel] += o.returns_krw
            order_days[o.channel] += 1 if o.gross_krw > 0 else 0
    pending: dict[str, int] = defaultdict(int)
    for p in ledger.pending:
        pending[p.channel] += p.net_krw
    assigned = _assigned_platform(ledger, assigned_from) if assigned_from else None

    platforms = []
    for channel, _ in pg:
        opened_months = tenure if channel == primary else rng.randint(0, max(tenure - 1, 0))
        rating = round(rng.uniform(*defaults.rating), 1)
        reviews = rng.randint(*defaults.reviews)
        cancel = round(gross[channel] * rng.uniform(*defaults.cancel))
        if override is not None and override.rating is not None:
            rating = override.rating
        platforms.append(StorePlatform(
            platform=channel, opened_on=_months_before(as_of, opened_months), rating=rating, review_count=reviews,
            orders_90d=order_days[channel], gross_90d_krw=gross[channel], cancel_90d_krw=cancel,
            return_90d_krw=returns[channel], seller_grade="우수" if rating >= GOOD_GRADE_RATING else "일반",
            pending_settlement_krw=pending[channel],
            assigned_pending_krw=pending[channel] if channel == assigned else 0,
        ))

    penalties = []
    if primary is not None:
        if defaults.warning:
            penalties.append(StorePenalty(
                platform=primary, kind="warning", occurred_on=as_of - timedelta(days=rng.randint(*WARNING_DAYS_AGO))))
        if override is not None and override.suspended_days_ago is not None:
            penalties.append(StorePenalty(
                platform=primary, kind="suspension", occurred_on=as_of - timedelta(days=override.suspended_days_ago)))
    return MarketplaceStore(as_of=as_of, platforms=tuple(platforms), penalties=tuple(penalties))
