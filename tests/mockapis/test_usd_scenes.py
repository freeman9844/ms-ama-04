import hashlib
import json
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import pytest

from agents import features
from agents.classify import Category, classify
from agents.marketplace import store_summary
from mockapis import demo
from mockapis.sellerdata.usd import simulate_usd
from mockapis.synth import generate_usd_case
from policy.engine import explain
from policy.rules import rules_for_version
from shared.sellers import USD_SELLERS

AS_OF = date(2026, 11, 24)
E, R, I = "eligible", "refer", "ineligible"
F = frozenset
V1_TABLE = {
    **{s: (E, F(), E, F()) for s in ("seller-dailylook-01", "seller-mangwon-02", "seller-gearstore-06",
       "seller-kimchi-09", "seller-linen-10", "seller-kitchen-12", "seller-tangerine-15",
       "seller-petcare-16", "seller-bakery-20")},
    **{s: (R, F({"R105", "R106"}), R, F({"R105", "R106"})) for s in ("seller-glowlab-03", "seller-skinlab-11",
       "seller-cable-13", "seller-sofa-18")},
    **{s: (R, F({"R107", "S001"}), I, F({"R001", "R104", "R107"})) for s in ("seller-knitroom-04",
       "seller-leather-14", "seller-tint-17")},
    "seller-candle-05": (R, F({"R107"}), R, F({"R107"})),
    "seller-ceramic-08": (R, F({"R107", "R109"}), R, F({"R107"})),
    "seller-petfood-07": (I, F({"R003"}), E, F()),
    "seller-streetfit-19": (I, F({"R001", "R002", "R104", "R110", "R901"}), I, F({"R001", "R002", "R104", "R901"})),
    "seller-mugshop-21": (I, F({"R002", "R106", "R108", "R901"}), I, F({"R002", "R106", "R108", "R901"})),
}
DIGESTS = json.loads((Path(__file__).parent / "usd_books_digest.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def _cases(as_of):
    return demo.build_usd_cases(as_of)


def case_at(seller, as_of):
    return _cases(as_of)[seller]


def underwrite_at(seller, as_of, principal=500_000):
    case = case_at(seller, as_of)
    return features.compute_usd_underwriting(
        "app-demo", case.profile, case.transactions, case.tax_filings, as_of=as_of,
        requested_amount_cents=principal, requested_tenor_months=6,
        pg_settlements=case.pg_settlements, marketplace=case.marketplace,
    )


def books_digest(case) -> str:
    """은행 거래·정산·세무 신고의 sha256. 마켓 자료는 넣지 않는다(Task 6이 의도적으로 바꾼다)."""
    books = {name: [row.model_dump(mode="json") for row in getattr(case, name)]
             for name in ("transactions", "pg_settlements", "tax_filings")}
    return hashlib.sha256(json.dumps(books, sort_keys=True).encode()).hexdigest()


@pytest.mark.parametrize("seller", sorted(V1_TABLE))
def test_usd_v1_verdicts_are_pinned(seller):
    d = explain(underwrite_at(seller, AS_OF).features, rules_for_version("usd-v1")).decision
    final, codes, cash, cash_codes = V1_TABLE[seller]
    assert (d.decision, {r.code for r in d.reason_codes}) == (final, codes)
    assert (d.cash_only.decision, {r.code for r in d.cash_only.reason_codes}) == (cash, cash_codes)
    assert d.max_amount_cents == (0 if final == I else 500_000)


@pytest.mark.parametrize("seller", sorted(V1_TABLE))
def test_existing_seller_books_do_not_change(seller):
    assert books_digest(case_at(seller, AS_OF)) == DIGESTS[seller]


def test_existing_sellers_have_diverse_marketplaces():
    tenures, all_cancel = set(), []
    for seller in V1_TABLE:
        ps = case_at(seller, AS_OF).marketplace.platforms
        if len(ps) > 1:
            assert len({p.opened_on for p in ps}) > 1
            assert len({(p.rating, p.review_count) for p in ps}) > 1
        assert all(p.orders_90d > 0 and p.seller_grade for p in ps)
        assigned = [p for p in ps if p.assigned_pending_cents > 0]
        assert len(assigned) == (1 if seller == "seller-mugshop-21" else 0)
        tenures.add(min(p.opened_on for p in ps))
        summary = store_summary(case_at(seller, AS_OF).marketplace, AS_OF)
        all_cancel.append(summary["mkt_cancel_rate_90d"])
    assert len(tenures) >= 8 and all(0 < c <= 0.04 for c in all_cancel)


V2_FINAL_EXTRA = {"seller-mugshop-21": {"R112"}}


@pytest.mark.parametrize("seller", sorted(V1_TABLE))
def test_existing_sellers_under_usd_v2(seller):
    d = explain(underwrite_at(seller, AS_OF).features, rules_for_version("usd-v2")).decision
    final, codes, cash, cash_codes = V1_TABLE[seller]
    assert (d.decision, {r.code for r in d.reason_codes}) == (final, codes | V2_FINAL_EXTRA.get(seller, set()))
    bank_codes = cash_codes - {"R107", "R108"}
    bank = I if bank_codes & {"R001", "R002", "R003", "R004", "R901"} else R if bank_codes else E
    assert (d.cash_only.decision, {r.code for r in d.cash_only.reason_codes}) == (bank, bank_codes)


def test_usd_underwriting_carries_settlement_features():
    bakery = underwrite_at("seller-bakery-20", AS_OF).features
    assert 12 <= bakery.pg_cash_conversion_days <= 20 and bakery.pg_advance_suspected_count == 0
    assert bakery.pg_offset_rate_trend_pp in (0.0, None)
    assert underwrite_at("seller-mugshop-21", AS_OF).features.pg_advance_suspected_count > 0


BASE = USD_SELLERS["seller-bakery-20"]


def books(seller, **kw):
    return simulate_usd(seller, "k", date(2026, 1, 1), date(2026, 3, 31), 7, 3 * seller.weekly_sales_cents, **kw)


def test_default_levers_keep_books_identical():
    assert books(replace(BASE)) == books(BASE)


def test_season_scales_sales_by_month():
    flat = books(BASE)
    peaked = books(replace(BASE, season_percent=(200,) + (100,) * 11))
    jan = lambda b: sum(o.gross_cents for o in b.orders if o.order_on.month == 1)
    feb = lambda b: sum(o.gross_cents for o in b.orders if o.order_on.month == 2)
    assert jan(peaked) >= 1.95 * jan(flat) and feb(peaked) == feb(flat)
    with pytest.raises(ValueError):
        replace(BASE, season_percent=(100,) * 11)


def test_owner_floor_lowers_friday_balance():
    low = books(replace(BASE, owner_floor_percent=50))
    fridays = [t.balance_after_cents for t in low.transactions if t.description == "Owner withdrawal"]
    assert fridays and max(fridays) == BASE.weekly_sales_cents // 2


def test_growth_from_scales_sales_after_the_date():
    flat = books(BASE)
    grown = books(replace(BASE, growth_percent=150), growth_from=date(2026, 2, 1))
    gross = lambda b, month: sum(o.gross_cents for o in b.orders if o.order_on.month == month)
    assert gross(grown, 1) == gross(flat, 1) and gross(grown, 3) >= 1.45 * gross(flat, 3)


def test_advance_label_books_advances_as_revenue_without_platform():
    b = simulate_usd(replace(BASE, advance_label="Marketplace payout"), "k", date(2026, 1, 1), date(2026, 3, 31),
                     7, 0, advance_from=date(2026, 2, 1))
    adv = [t for t in b.transactions if t.description == "Marketplace payout"]
    assert adv and all(t.platform_code is None and classify(t) is Category.REVENUE for t in adv)


def test_tax_declared_percent_and_offset_rise_in_generated_case(monkeypatch):
    monkeypatch.setitem(USD_SELLERS, "seller-bakery-20",
                        replace(BASE, tax_declared_percent=40, offset_rise_days=90, offset_rise_rate=Decimal(".08")))
    case = generate_usd_case("seller-bakery-20", AS_OF, 7)
    assert case.tax_filings
    for f in case.tax_filings:
        net = sum(s.net_cents for s in case.pg_settlements
                  if f.period_start <= s.settled_on <= f.period_end)
        assert f.declared_revenue_cents == net * 40 // 100
    cutoff = AS_OF - timedelta(days=90)
    late = [s for s in case.pg_settlements if s.settled_on >= cutoff and s.gross_cents]
    early = [s for s in case.pg_settlements if s.settled_on < cutoff]
    assert late and early
    assert all(abs(s.deduction_cents / s.gross_cents - 0.08) < 0.005 for s in late)
    assert all(s.deduction_cents == 0 for s in early)
