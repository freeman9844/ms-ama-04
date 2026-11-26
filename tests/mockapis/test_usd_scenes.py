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
from agents.usd_monitoring import approval_basis, observe_usd, track_summary, usd_baseline
from mockapis import demo, postloan
from mockapis.demo import SCENE_REQUEST_CENTS
from mockapis.sellerdata.usd import simulate_usd
from mockapis.synth import generate_usd_case
from policy.engine import explain
from policy.rules import rules_for_version
from shared.contracts import USD_V1, AlertCause
from shared.sellers import USD_SELLERS
from shared.usd_source import UsdMarketFlow, UsdPgFlow

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


SCENES = {  # seller: (bank-only decision, bank-only codes, final decision, final codes)
    "seller-snackbox-22": (R, F({"R103"}), E, F()),
    "seller-campgear-23": (R, F({"R102"}), E, F()),
    "seller-teahouse-24": (I, F({"R004"}), R, F({"S005"})),
    "seller-phonecase-25": (I, F({"R001", "R104"}), I, F({"R001", "R104"})),
    "seller-toyland-26": (E, F(), R, F({"R111"})),
    "seller-vitamin-27": (E, F(), R, F({"R108", "R112"})),
    "seller-golfgear-28": (E, F(), R, F({"R113"})),
    "seller-popup-29": (E, F(), E, F()),
}


@pytest.mark.parametrize("as_of", [AS_OF, date(2026, 11, 25)])
@pytest.mark.parametrize("seller", sorted(SCENES))
def test_scene_verdicts(seller, as_of):
    d = explain(underwrite_at(seller, as_of, SCENE_REQUEST_CENTS.get(seller, 500_000)).features,
                rules_for_version("usd-v2")).decision
    bank, bank_codes, final, codes = SCENES[seller]
    assert (d.cash_only.decision, {r.code for r in d.cash_only.reason_codes}) == (bank, bank_codes)
    assert d.decision == final and {r.code for r in d.reason_codes} == codes


def test_scene_limits_and_substitutions():
    by = {s: explain(underwrite_at(s, AS_OF, SCENE_REQUEST_CENTS.get(s, 500_000)).features,
                     rules_for_version("usd-v2")).decision for s in SCENES}
    assert by["seller-snackbox-22"].applied_substitutions == ("S003",)
    assert by["seller-campgear-23"].applied_substitutions == ("S004",)
    assert by["seller-teahouse-24"].max_amount_cents == 500_000
    assert "S001" not in by["seller-phonecase-25"].applied_substitutions
    popup = by["seller-popup-29"]
    assert popup.max_amount_cents >= popup.cash_only.max_amount_cents + 100_000


MARGINS = {  # seller: [(feature, op, threshold)] - deciding features only, 10% margin
    "seller-snackbox-22": [("cash_buffer_days", "lt", 10), ("pg_cash_conversion_days", "le", 10)],
    "seller-campgear-23": [("revenue_volatility", "gt", .5), ("mkt_tenure_months", "ge", 24),
                           ("mkt_review_count", "ge", 300), ("revenue_trend_ratio", "ge", .8)],
    "seller-teahouse-24": [("tax_bank_match_ratio", "lt", .5)],
    "seller-toyland-26": [("mkt_cancel_rate_90d", "gt", .08)],
    "seller-golfgear-28": [("pg_offset_rate_trend_pp", "gt", 3.0)],
    "seller-popup-29": [("revenue_volatility", "le", .5), ("pg_top_platform_share", "le", .8), ("dscr", "ge", 1.0)],
    "seller-vitamin-27": [("pg_reconciliation_ratio", "lt", .9), ("pg_advance_suspected_count", "gt", 0),
                          ("revenue_trend_ratio", "ge", .8)],
}
SCENE_SUBSTITUTIONS = {"seller-snackbox-22": "S003", "seller-campgear-23": "S004", "seller-teahouse-24": "S005"}


def clears(field, op, v, t):
    """10% away from the threshold. Ratings need 0.1; a fully reconciled ledger (1.0) clears any floor."""
    low = op in ("lt", "le")
    if field == "mkt_rating":
        return v <= t - .1 if low else v >= t + .1
    if field == "pg_reconciliation_ratio" and not low and v == 1.0:
        return True
    return (v <= t / 1.1 and (op != "lt" or v < t)) if low else (v >= t * 1.1 and (op != "gt" or v > t))


@pytest.mark.parametrize("seller", sorted(MARGINS))
def test_scene_features_keep_ten_percent_margin(seller):
    f = underwrite_at(seller, AS_OF, SCENE_REQUEST_CENTS.get(seller, 500_000)).features
    for field, op, t in MARGINS[seller]:
        v = getattr(f, field)
        assert clears(field, op, v, t), (field, v, t)


@pytest.mark.parametrize("seller", sorted(SCENE_SUBSTITUTIONS))
def test_scene_substitution_conditions_keep_margin(seller):
    f = underwrite_at(seller, AS_OF, SCENE_REQUEST_CENTS.get(seller, 500_000)).features
    sub = next(s for s in rules_for_version("usd-v2").substitutions if s.code == SCENE_SUBSTITUTIONS[seller])
    for c in sub.when:
        v = getattr(f, c.field)
        assert clears(c.field, c.op, v, c.threshold), (sub.code, c.field, v, c.threshold)


def replay(seller, *, basis=None, ledger=True, prior_year=True, market=True):
    """Book the loan as a usd-v2 approval and observe 13 weeks. Returns (UsdObservation per week, week entries)."""
    uw = underwrite_at(seller, AS_OF)
    stored = {"features": uw.features.model_dump(mode="json"), "monthly": uw.monthly}
    decision = explain(uw.features, rules_for_version("usd-v2")).decision
    flow = postloan.generate_usd_postloan(case_at(seller, AS_OF), AS_OF, 1001,
                                          loan_id="loan-usd", amount_cents=500_000, installment_cents=84_000)
    pg = UsdPgFlow(**USD_V1.model_dump(), settlements=flow.pg_settlements, orders=flow.orders,
                   prior_year_orders=flow.prior_year_orders if prior_year else ())
    mkt = UsdMarketFlow(**USD_V1.model_dump(), weeks=flow.market_weeks) if market else None
    mon = {"flow_start": flow.start.isoformat(), "weeks": []}
    loan = {"monitoring": mon, "baseline": usd_baseline(stored),
            "approval_basis": approval_basis(decision.applied_substitutions, stored["features"])
            if basis is None else basis}
    out = []
    for week in range(1, 14):
        hi = flow.start + timedelta(days=7 * week - 1)
        obs = observe_usd(loan, [t for t in flow.transactions if t.booked_on <= hi], pg if ledger else None, week, {}, mkt)
        mon["weeks"].append({"week": week, "row": obs.rows[-1], "triggers": obs.triggers,
                             "bank_only_triggers": obs.bank_only, "explained": obs.explained})
        out.append(obs)
    return out, mon["weeks"]


def test_scene1_assignment_breaks_s003_basis():
    obs, weeks = replay("seller-snackbox-22")
    s = track_summary(weeks)
    assert s["bank_first_week"] is None  # bank-only: never detected
    assert s["full_first_week"] in (3, 4) and "basis_breach" in obs[s["full_first_week"] - 1].triggers
    assert obs[s["full_first_week"] - 1].candidates[0].cause is AlertCause.SETTLEMENT_ASSIGNED


def test_stable_s003_loan_never_breaches(monkeypatch):
    monkeypatch.setitem(postloan.USD_POSTLOAN_SCENARIOS, "seller-snackbox-22", postloan.FlowScenario.STABLE)
    obs, _ = replay("seller-snackbox-22")
    assert all(b["status"] != "breach" for o in obs for b in o.basis)
    assert all(o.basis for o in obs)  # the S003 basis is computed from week 1 (Review Focus 2)


SCENE2 = {"seller-dailylook-01": (6, 5), "seller-gearstore-06": (5, 3), "seller-linen-10": (None, 4)}
# (bank-only first week, full first week), measured in Task 4 Step 4.


@pytest.mark.parametrize("seller", sorted(SCENE2))
def test_scene2_full_track_detects_first(seller):
    s = track_summary(replay(seller)[1])
    assert s["full_first_week"] is not None
    assert s["bank_first_week"] is None or s["full_first_week"] < s["bank_first_week"]
    assert (s["bank_first_week"], s["full_first_week"]) == SCENE2[seller]


def test_scene3_seasonal_drop_is_explained():
    obs, weeks = replay("seller-campgear-23")
    s = track_summary(weeks)
    assert s["full_first_week"] is None and s["bank_only_weeks"] >= 3 and s["explained_weeks"] >= 3
    assert all(b["status"] != "breach" for o in obs for b in o.basis)


def test_due_fields_stay_with_the_bank_forecast(monkeypatch):
    def fake(visible, start, hi, dues, scheduled=None):
        bal = 100 if scheduled is None else 999
        return {"path": [{"date": "2026-12-01", "balance_cents": bal, "installment_cents": 50,
                          "before_payment_cents": bal}]}

    monkeypatch.setattr("agents.usd_monitoring.project_usd", fake)
    rows = [w["row"] for w in replay("seller-campgear-23")[1]]
    assert {r["due_balance_cents"] for r in rows} == {100}
    assert {r["projected_min_balance_cents"] for r in rows} == {999}


def _weeks(*specs):
    return [{"week": n, "row": {}, "triggers": full, "explained": explained,
             **({"bank_only_triggers": bank} if bank is not None else {})} for n, full, bank, explained in specs]


def test_track_summary_needs_two_track_keys_on_every_week():
    assert track_summary(_weeks((1, [], None, []), (2, ["inflow_drop"], ["inflow_drop"], []))) is None
    assert track_summary([]) is None
    assert track_summary(_weeks((1, [], [], []))) is not None


def test_explained_weeks_count_only_inflow_drop_explanations():
    s = track_summary(_weeks((1, [], [], [{"trigger": "inflow_drop"}]), (2, [], [], [{"trigger": "other"}]), (3, [], [], [])))
    assert s["explained_weeks"] == 1


@pytest.mark.parametrize("seller", ["seller-campgear-23", "seller-dailylook-01"])
def test_bank_only_track_equals_detector_without_ledger(seller):
    _, weeks = replay(seller)
    no_ledger, _ = replay(seller, ledger=False)
    assert [w["bank_only_triggers"] for w in weeks] == [o.triggers for o in no_ledger]
    assert all(o.explained == [] and o.basis == [] for o in no_ledger)


def test_order_slump_is_never_explained():
    obs, _ = replay("seller-dailylook-01")
    assert not any(e["trigger"] == "inflow_drop" for o in obs for e in o.explained
                   if o.rows[-1]["orders_per_day_vs_expected"] < .75)


def test_missing_prior_year_keeps_flat_expectation():
    obs, _ = replay("seller-campgear-23", prior_year=False)
    assert len({o.rows[-1]["expected_orders_cents"] for o in obs}) == 1
    assert all(o.rows[-1]["orders_vs_prior_year"] is None for o in obs)
    assert all(b["status"] == "unknown" for o in obs for b in o.basis if b["code"] == "S004")
    assert not any("basis_breach" in o.triggers for o in obs)


def test_no_substitution_loan_has_empty_basis():
    assert approval_basis((), {"pg_reconciliation_ratio": .99}) == []
    assert all(o.basis == [] for o in replay("seller-bakery-20")[0])


def codes(o, source=None):
    return [s["code"] for s in o.signals if source is None or s["source"] == source]


def test_gearstore_market_signal_comes_first():
    obs, _ = replay("seller-gearstore-06")
    assert codes(obs[1]) == ["cancel_rise"]
    assert {s["code"]: s["strength"] for s in obs[2].signals if s["code"] in ("account_warning", "settlement_hold")} \
        == {"account_warning": "strong", "settlement_hold": "strong"}


def test_single_week_signal_dropped_when_persistent_fires():
    obs, _ = replay("seller-linen-10")
    spiked = [o for o in obs if "return_spike" in codes(o)]
    assert spiked and all("returns_rising" not in codes(o) for o in spiked)


def test_explained_inflow_is_not_a_signal():
    obs, _ = replay("seller-campgear-23")
    explained = [o for o in obs if o.explained]
    assert explained and all("inflow_drop" not in codes(o) for o in explained)


def test_seasonal_scene_raises_no_signal():
    assert all(o.signals == () for o in replay("seller-campgear-23")[0])


def test_linen_rating_drop_is_a_market_signal():
    obs, _ = replay("seller-linen-10")
    assert any("rating_drop" in codes(o, "market") for o in obs)


def test_no_market_means_no_market_fields_or_signals():
    obs, _ = replay("seller-gearstore-06", market=False)
    assert not any(k.startswith("mkt_") for k in obs[-1].rows[-1])
    assert all(codes(o, "market") == [] for o in obs)


@pytest.mark.parametrize("seller", ["seller-gearstore-06", "seller-linen-10", "seller-dailylook-01",
                                    "seller-tangerine-15", "seller-snackbox-22", "seller-tint-17",
                                    "seller-campgear-23"])
def test_signal_metric_ids_exist_in_rows(seller):
    obs, _ = replay(seller)
    for week, o in enumerate(obs, 1):
        row = o.rows[-1]
        for s in o.signals:
            assert s["metric_ids"]
            for mid in s["metric_ids"]:
                kind, n, field = mid.split(":")
                assert kind in ("W", "F") and int(n) == week and field in row, (week, s["code"], mid)
