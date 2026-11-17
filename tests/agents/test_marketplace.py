from datetime import date

from agents.marketplace import features_of, store_summary
from shared.contracts import MKT_FEATURES, MarketplaceStore, StorePenalty, StorePlatform

AS_OF = date(2026, 11, 17)


def _platform(**kw) -> StorePlatform:
    base = {
        "platform": "스마트스토어", "opened_on": date(2024, 1, 10), "rating": 4.5, "review_count": 100,
        "orders_90d": 300, "gross_90d_krw": 10_000_000, "cancel_90d_krw": 200_000, "return_90d_krw": 300_000,
        "seller_grade": "파워", "pending_settlement_krw": 1_000_000, "assigned_pending_krw": 0,
    }
    return StorePlatform(**{**base, **kw})


def _store(*platforms: StorePlatform, penalties: tuple[StorePenalty, ...] = ()) -> MarketplaceStore:
    return MarketplaceStore(as_of=AS_OF, platforms=platforms, penalties=penalties)


def test_weighted_rating():
    store = _store(_platform(rating=4.0, review_count=100), _platform(platform="쿠팡", rating=5.0, review_count=300))
    summary = store_summary(store, AS_OF)
    assert summary["mkt_rating"] == 4.75 and summary["mkt_review_count"] == 400


def test_rating_falls_back_to_simple_average_without_reviews():
    store = _store(_platform(rating=4.0, review_count=0), _platform(platform="쿠팡", rating=5.0, review_count=0))
    assert store_summary(store, AS_OF)["mkt_rating"] == 4.5


def test_rates_are_pooled_over_platforms():
    store = _store(_platform(), _platform(platform="쿠팡", gross_90d_krw=30_000_000, cancel_90d_krw=0, return_90d_krw=0))
    summary = store_summary(store, AS_OF)
    assert summary["mkt_return_rate_90d"] == 0.0075 and summary["mkt_cancel_rate_90d"] == 0.005


def test_zero_gross_rates_are_none():
    summary = store_summary(_store(_platform(gross_90d_krw=0, cancel_90d_krw=0, return_90d_krw=0)), AS_OF)
    assert summary["available"] is True
    assert summary["mkt_return_rate_90d"] is None and summary["mkt_cancel_rate_90d"] is None
    assert summary["platforms"][0]["return_rate_90d"] is None


def test_tenure_floors_partial_month():
    summary = store_summary(_store(_platform(opened_on=date(2024, 11, 18))), AS_OF)
    assert summary["mkt_tenure_months"] == 23
    assert store_summary(_store(_platform(opened_on=date(2024, 11, 17))), AS_OF)["mkt_tenure_months"] == 24


def test_tenure_uses_earliest_platform():
    store = _store(_platform(opened_on=date(2025, 5, 1)), _platform(platform="쿠팡", opened_on=date(2023, 11, 17)))
    assert store_summary(store, AS_OF)["mkt_tenure_months"] == 36


def test_warning_not_suspension():
    store = _store(_platform(), penalties=(StorePenalty(platform="쿠팡", occurred_on=date(2026, 10, 1), kind="warning"),))
    assert store_summary(store, AS_OF)["mkt_days_since_suspension"] is None


def test_latest_suspension_up_to_as_of():
    penalties = (
        StorePenalty(platform="쿠팡", occurred_on=date(2026, 1, 1), kind="suspension"),
        StorePenalty(platform="쿠팡", occurred_on=date(2026, 11, 7), kind="suspension"),
        StorePenalty(platform="쿠팡", occurred_on=date(2026, 12, 1), kind="suspension"),
        StorePenalty(platform="쿠팡", occurred_on=date(2026, 11, 10), kind="warning"),
    )
    summary = store_summary(_store(_platform(), penalties=penalties), AS_OF)
    assert summary["mkt_days_since_suspension"] == 10
    assert [p["kind"] for p in summary["penalties"]].count("suspension") == 3


def test_pending_net_of_assigned_floor_zero():
    store = _store(_platform(pending_settlement_krw=1_000_000, assigned_pending_krw=400_000),
                   _platform(platform="쿠팡", pending_settlement_krw=500_000, assigned_pending_krw=500_000))
    assert store_summary(store, AS_OF)["mkt_pending_settlement_krw"] == 600_000
    full = _store(_platform(pending_settlement_krw=500_000, assigned_pending_krw=500_000))
    assert store_summary(full, AS_OF)["mkt_pending_settlement_krw"] == 0


def test_platform_rows_carry_per_platform_fields():
    row = store_summary(_store(_platform()), AS_OF)["platforms"][0]
    assert row["opened_on"] == "2024-01-10" and row["return_rate_90d"] == 0.03 and row["cancel_rate_90d"] == 0.02
    assert row["assigned_pending_krw"] == 0 and row["seller_grade"] == "파워"


def test_unavailable_all_none():
    for store in (None, _store()):
        summary = store_summary(store, AS_OF)
        assert summary == {"available": False}
        assert features_of(summary) == dict.fromkeys(MKT_FEATURES)


def test_features_of_picks_the_seven_features():
    features = features_of(store_summary(_store(_platform()), AS_OF))
    assert tuple(features) == MKT_FEATURES and features["mkt_rating"] == 4.5
