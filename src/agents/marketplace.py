"""마켓 셀러 지표(스펙 4.2). 플랫폼별 성과·제재 이력에서 결정적 코드만 지표를 만든다."""

from collections.abc import Mapping
from datetime import date

from shared.contracts import MKT_FEATURES, MarketplaceStore, USD_V1
from shared.usd_source import UsdMarketplace

FLAG_MARKETPLACE_UNAVAILABLE = "marketplace_unavailable"


def _tenure_months(opened_on: date, as_of: date) -> int:
    months = (as_of.year - opened_on.year) * 12 + as_of.month - opened_on.month
    return max(0, months - 1 if as_of.day < opened_on.day else months)


def store_summary(store: MarketplaceStore | UsdMarketplace | None, as_of: date) -> dict:
    """키: available, as_of, platforms[...], penalties[{platform,occurred_on,kind}], MKT_FEATURES 7개.

    자료가 없거나 플랫폼이 0개면 {"available": False}다."""
    if store is None or not store.platforms:
        return {"available": False}
    platforms = store.platforms
    reviews = sum(p.review_count for p in platforms)
    rating = (
        sum(p.rating * p.review_count for p in platforms) / reviews
        if reviews else sum(p.rating for p in platforms) / len(platforms)
    )
    usd = isinstance(store, UsdMarketplace)
    unit = "cents" if usd else "krw"
    def money(p, field):
        return getattr(p, f"{field}_{unit}")
    gross = sum(money(p, "gross_90d") for p in platforms)
    suspensions = [p.occurred_on for p in store.penalties if p.kind == "suspension" and p.occurred_on <= as_of]
    return {
        "available": True,
        **(USD_V1.model_dump() if usd else {}),
        "as_of": as_of.isoformat(),
        "platforms": [
            {"platform": p.platform_code if usd else p.platform, "opened_on": p.opened_on.isoformat(), "rating": p.rating,
             "review_count": p.review_count, f"gross_90d_{unit}": money(p, "gross_90d"),
             "return_rate_90d": round(money(p, "return_90d") / money(p, "gross_90d"), 4) if money(p, "gross_90d") else None,
             "cancel_rate_90d": round(money(p, "cancel_90d") / money(p, "gross_90d"), 4) if money(p, "gross_90d") else None,
             f"pending_settlement_{unit}": money(p, "pending_settlement"),
             **({} if usd else {"orders_90d": p.orders_90d, "seller_grade": p.seller_grade,
                                "assigned_pending_krw": p.assigned_pending_krw})}
            for p in platforms
        ],
        "penalties": [{"platform": p.platform, "occurred_on": p.occurred_on.isoformat(), "kind": p.kind}
                      for p in store.penalties],
        "mkt_tenure_months": _tenure_months(min(p.opened_on for p in platforms), as_of),
        "mkt_rating": round(rating, 2),
        "mkt_review_count": reviews,
        "mkt_return_rate_90d": round(sum(money(p, "return_90d") for p in platforms) / gross, 4) if gross else None,
        "mkt_cancel_rate_90d": round(sum(money(p, "cancel_90d") for p in platforms) / gross, 4) if gross else None,
        "mkt_days_since_suspension": (as_of - max(suspensions)).days if suspensions else None,
        f"mkt_pending_settlement_{unit}": max(
            0, sum(money(p, "pending_settlement") - (0 if usd else p.assigned_pending_krw) for p in platforms)),
    }


def features_of(summary: Mapping) -> dict[str, int | float | None]:
    return {k: summary.get(k) for k in MKT_FEATURES}
