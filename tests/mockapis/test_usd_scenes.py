import hashlib
import json
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from agents.classify import Category, classify
from agents.marketplace import store_summary
from agents.runtime import AgentRun
from agents.usd_early_warning import fallback_stage, investigate_usd, usd_fallback_alert
from agents.usd_monitoring import _market_week, approval_basis, observe_usd
from evals.usd_scenes import case_at, prepare, scene_context, scene_replay, underwrite_at
from mockapis import postloan
from mockapis.demo import SCENE_REQUEST_CENTS
from mockapis.sellerdata.usd import simulate_usd
from mockapis.synth import generate_usd_case
from policy.engine import explain
from policy.rules import rules_for_version
from shared.contracts import USD_V1, AlertCause
from shared.sellers import USD_SELLERS
from shared.usd_source import UsdMarketFlow, UsdMarketWeek

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
    scene = prepare(seller, basis=basis, prior_year=prior_year, market=market)
    mon = scene.loan["monitoring"]
    out = []
    for week in range(1, 14):
        obs = observe_usd(scene.loan, scene.visible(week), scene.pg if ledger else None, week, {}, scene.market)
        mon["weeks"].append({"week": week, "row": obs.rows[-1], "triggers": obs.triggers,
                             "bank_only_triggers": obs.bank_only, "explained": obs.explained})
        out.append(obs)
    return out, mon["weeks"]


def test_snackbox_assignment_breaks_s003_basis():
    obs, _ = replay("seller-snackbox-22")
    assert not any(o.bank_only for o in obs)  # bank-only: never detected
    first = next(n for n, o in enumerate(obs, 1) if o.triggers)
    assert first in (3, 4) and "basis_breach" in obs[first - 1].triggers
    assert obs[first - 1].candidates[0].cause is AlertCause.SETTLEMENT_ASSIGNED


def test_stable_s003_loan_never_breaches(monkeypatch):
    monkeypatch.setitem(postloan.USD_POSTLOAN_SCENARIOS, "seller-snackbox-22", postloan.FlowScenario.STABLE)
    obs, _ = replay("seller-snackbox-22")
    assert all(b["status"] != "breach" for o in obs for b in o.basis)
    assert all(o.basis for o in obs)  # the S003 basis is computed from week 1 (Review Focus 2)


def ladder(lines):
    """(stage, first week) without normal weeks and without consecutive repeats."""
    out = []
    for line in lines:
        if line["stage"] != "normal" and (not out or out[-1][0] != line["stage"]):
            out.append((line["stage"], line["week"]))
    return out


STAGE_SCENES = {  # spec section 7; weeks may move by one
    "seller-gearstore-06": [("watch", 2), ("medium", 3), ("resolved", 8)],
    "seller-linen-10": [("medium", 3), ("resolved", 10)],  # returns move settlement and market score together (spec 4.2)
    "seller-dailylook-01": [("watch", 4), ("medium", 5), ("high", 7)],
    "seller-tangerine-15": [("watch", 3)],
    "seller-snackbox-22": [("medium", 3)],
    "seller-tint-17": [("watch", 6), ("resolved", 8)],
}


@pytest.mark.parametrize("seller", sorted(STAGE_SCENES))
def test_scene_stage_ladder(seller):
    got = ladder(scene_replay(seller))
    assert [s for s, _ in got] == [s for s, _ in STAGE_SCENES[seller]], got
    assert all(abs(w - e) <= 1 for (_, w), (_, e) in zip(got, STAGE_SCENES[seller])), got


def test_gearstore_sources_spread_market_then_settlement_then_bank():
    first = {}
    for line in scene_replay("seller-gearstore-06"):
        for s in line["signals"]:
            first.setdefault(s["source"], line["week"])
    assert first["market"] < first["settlement"] < first["bank"], first


def test_campgear_explained_and_no_incident():
    lines = scene_replay("seller-campgear-23")
    assert all(line["stage"] == "normal" for line in lines)
    assert sum(line["kind"] == "explained" for line in lines) >= 3


def test_tint_blip_never_alerts():
    assert all(line["stage"] in ("normal", "watch", "resolved") for line in scene_replay("seller-tint-17"))


STABLE_SELLERS = sorted(s for s in USD_SELLERS
                        if postloan.USD_POSTLOAN_SCENARIOS.get(s, postloan.FlowScenario.STABLE)
                        is postloan.FlowScenario.STABLE and case_at(s, AS_OF).marketplace is not None)


@pytest.mark.parametrize("seller", STABLE_SELLERS)
def test_stable_sellers_open_no_incident(seller):
    lines = scene_replay(seller)
    assert all(line["stage"] == "normal" for line in lines), [(l["week"], l["signals"]) for l in lines if l["signals"]]


@pytest.mark.parametrize("seller", sorted(STAGE_SCENES) + ["seller-campgear-23"])
def test_scene_agent_calls_at_most_eight(seller):
    assert sum(line["called"] for line in scene_replay(seller)) <= 8


def test_scene_context_only_on_call_weeks():
    lines = scene_replay("seller-gearstore-06")
    call = next(line for line in lines if line["called"])
    ctx = scene_context("seller-gearstore-06", call["week"])
    assert ctx.week == call["week"] and ctx.base_stage == call["base_stage"] and ctx.signals
    assert ctx.incident_id == call["incident_id"] and ctx.mask("x") == "x"
    with pytest.raises(ValueError):
        scene_context("seller-gearstore-06", next(line["week"] for line in lines if not line["called"]))


class KeepMediumAgent:
    """Lowers a high base to medium citing this week's marketplace grade; otherwise keeps the base stage."""

    def __init__(self, ctx):
        self.ctx = ctx

    def run(self, spec, input_text, handlers):
        ctx = self.ctx
        stage, adjust = ctx.base_stage, ""
        if stage == "high":
            stage = "medium"
            adjust = (f"The marketplace grade in W:{ctx.week}:mkt_grade still lets the seller sell, so the "
                      "projected shortfall can be checked before it becomes high.")
        if stage in ("medium", "high"):
            alert = usd_fallback_alert(ctx, stage).model_dump(mode="json")
            args = {k: alert[k] for k in ("summary", "check_action", "recommended_action", "primary_cause",
                                          "secondary_causes")}
            args["signals"] = [{k: s[k] for k in ("kind", "text", "metric_ids", "txn_ids")} for s in alert["signals"]]
            reply = handlers["raise_alert"]({**args, "severity": stage})
            assert reply["accepted"], reply
        output = {"stage": stage, "why_now": "Orders moved first and bank receipts followed.",
                  "outlook": "Cash may keep falling unless orders recover.",
                  "adjust_reason": adjust, "reason": "Evidence supports the stage."}
        return AgentRun(spec.name, "1", "conv-1", spec.output_model.model_validate(output), [], 1, 1)

    def delete_container(self, container_id):
        return True


def test_dailylook_agent_lowers_first_high_to_medium():
    lines = scene_replay("seller-dailylook-01",
                         decide=lambda ctx: investigate_usd(ctx, runner=KeepMediumAgent(ctx), model="fake"))
    first_high = next(line for line in lines if line["called"] and line["base_stage"] == "high")
    assert (first_high["agent"], first_high["stage"]) == ("agent", "medium")  # the lowered stage passed the checks
    assert first_high["adjust_reason"] and all(line["agent"] == "agent" for line in lines if line["called"])


@pytest.mark.parametrize("seller,first", [("seller-gearstore-06", "Marketplace"), ("seller-dailylook-01", "Settlement"),
                                          ("seller-snackbox-22", "Settlement")])
def test_fallback_why_now_names_the_source_that_moved_first(seller, first):
    called = [line for line in scene_replay(seller) if line["called"]]
    assert called and all(line["agent"] == "fallback" for line in called)
    assert all(line["why_now"].startswith(f"{first} data moved first") for line in called), \
        [(line["week"], line["why_now"]) for line in called]


def test_fallback_why_now_is_neutral_when_first_source_is_unknown():
    ctx = scene_context("seller-dailylook-01", 6)
    assert not usd_fallback_alert(ctx, "medium").why_now.startswith("Bank")
    no_incident = replace(ctx, incident_id=None)
    truncated = replace(ctx, journal=[line for line in ctx.journal if line["week"] > 4])
    for unknown in (no_incident, truncated):
        result = fallback_stage(unknown, "agents_disabled")
        assert "moved first" not in result.why_now and "moved first" not in result.alert.why_now


def test_market_missing_still_detects_settlement_and_bank():
    lines = scene_replay("seller-gearstore-06", market=False)
    sources = {s["source"] for line in lines for s in line["signals"]}
    assert all(not line["market"] for line in lines) and sources == {"settlement", "bank"}
    assert "medium" in {line["stage"] for line in lines}


def test_due_fields_stay_with_the_bank_forecast(monkeypatch):
    def fake(visible, start, hi, dues, scheduled=None):
        bal = 100 if scheduled is None else 999
        return {"path": [{"date": "2026-12-01", "balance_cents": bal, "installment_cents": 50,
                          "before_payment_cents": bal}]}

    monkeypatch.setattr("agents.usd_monitoring.project_usd", fake)
    rows = [w["row"] for w in replay("seller-campgear-23")[1]]
    assert {r["due_balance_cents"] for r in rows} == {100}
    assert {r["projected_min_balance_cents"] for r in rows} == {999}


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
    assert codes(obs[1]) == ["market_score_drop"]
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


def test_linen_quality_drop_is_a_market_signal():
    obs, _ = replay("seller-linen-10")
    assert any("market_score_drop" in codes(o, "market") for o in obs)


def test_no_market_means_no_market_fields_or_signals():
    obs, _ = replay("seller-gearstore-06", market=False)
    assert not any(k.startswith("mkt_") for k in obs[-1].rows[-1])
    assert all(codes(o, "market") == [] for o in obs)


def _mkt_weeks(*, cancel_cents, grade, start=date(2026, 11, 24), pre_grade="Gold", pre_cancel_cents=10_000):
    def week(end, cancel, grade):
        return UsdMarketWeek(**USD_V1.model_dump(), week_end=end, platform_code="coupang", orders=10,
                             gross_cents=1_000_000, cancel_cents=cancel, rating=4.5, seller_grade=grade,
                             pending_settlement_cents=0)

    pre = [week(start - timedelta(days=7 * k + 1), pre_cancel_cents, pre_grade) for k in range(8)]
    hi = start + timedelta(days=6)
    return UsdMarketFlow(**USD_V1.model_dump(), weeks=(*pre, week(hi, cancel_cents, grade))), start, hi


def test_market_week_writes_score_and_drop():
    market, start, hi = _mkt_weeks(cancel_cents=40_000, grade="Gold")  # 1% -> 4%: 18점
    row = {}
    assert _market_week(row, market, start, hi) == ["market_score_drop"]
    assert (row["mkt_score"], row["mkt_worst_score"], row["mkt_worst_platform"]) == (82, 82, "coupang")
    assert row["mkt_score_parts"][0]["metric"] == "cancel" and row["mkt_cancel_rate"] == 0.04


def test_market_score_crash_supersedes_drop():
    market, start, hi = _mkt_weeks(cancel_cents=100_000, grade="Gold")  # 상한 40 -> 60
    assert _market_week({}, market, start, hi) == ["market_score_crash"]


def test_one_grade_step_is_a_drop():
    market, start, hi = _mkt_weeks(cancel_cents=10_000, grade="Silver")
    assert _market_week({}, market, start, hi) == ["market_score_drop"]


@pytest.mark.parametrize("pre_grade, grade", [("Gold", "Platinum"), ("Platinum", "Gold")])
def test_unknown_grade_is_not_comparable(pre_grade, grade):
    market, start, hi = _mkt_weeks(cancel_cents=10_000, grade=grade, pre_grade=pre_grade)
    assert _market_week({}, market, start, hi) == []


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
