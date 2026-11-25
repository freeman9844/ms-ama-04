"""Weekly observations and a trailing cash-flow forecast for the synthetic USD product.

Two tracks run side by side. The bank-only track reads the account alone (inflow drop and the trailing forecast).
The full track adds the settlement ledger: order and payout signals, the approval basis the loan relied on, and a
forecast that knows scheduled payouts. A bank-only signal that the ledger explains is not raised again."""

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any, NamedTuple

from agents.classify import Category, classify
from agents.forecast import project_usd
from agents.ledger_monitor import (CAUSE_FIELDS, CAUSE_OF_TRIGGER, COST_SURGE_PP, HOLD_SHARE, PERSIST_WEEKS,
                                   PRE_LOAN_DAYS, RETURN_SPIKE_MULT, RETURN_SPIKE_PP, CauseCandidate)
from agents.monitoring import week_window
from agents.pg_ledger import weekly_reconciliation
from shared.contracts import AlertCause
from shared.krcalendar import add_business_days, is_business_day, next_business_day
from shared.sellers import USD_PAYOUT_BUSINESS_DAYS

_CENT_EVIDENCE = {"order_gross", "held", "advance_in", "returns", "deductions", "high_cost_lender", "loan_proceeds",
                  "unreconciled_payout"}
EXPLAIN_COVERAGE = 0.95  # bank receipts / ledger payouts at or above this: the drop is payout timing
EXPLAIN_ORDERS = 0.75  # orders per business day vs expected below this: the drop is real and never explained
SEASON_BAND = (0.8, 1.25)  # last year's loan-period pace vs its pre-loan pace inside this band: no season
PRIOR_YEAR_DAYS = 364  # 52 weeks: same weekday
PRE3_DAYS = 21
RECON_DAYS = 21
# Substitution code -> (week-row metric, underwriting feature holding the approved value, floor)
BASIS_METRICS = {
    "S003": ("recon_ratio", "pg_reconciliation_ratio", 0.95),
    "S005": ("recon_ratio", "pg_reconciliation_ratio", 0.90),
    "S004": ("orders_vs_prior_year", None, 0.75),
}
_SETTLEMENT_BASIS = {"S003", "S005"}


class UsdObservation(NamedTuple):
    rows: list[dict]
    triggers: list[str]  # full track
    candidates: list[CauseCandidate]
    cats: dict[str, Category]
    proj: dict[str, Any]  # full-track forecast
    bank_only: list[str]
    explained: list[dict]
    basis: list[dict]


def _rate(part, whole):
    return round(part / whole, 4) if whole else None


def _basis_causes(row, triggers):
    for code in row.get("basis_breached", ()):
        if code in _SETTLEMENT_BASIS:
            yield AlertCause.SETTLEMENT_HOLD if "settlement_hold" in triggers else AlertCause.SETTLEMENT_ASSIGNED
        elif code == "S004":
            yield AlertCause.SALES_SLUMP


def _candidates(history, rows):
    """Causes in order of first firing week; evidence cites that week's USD row fields."""
    by_week = {r["week"]: r for r in rows}
    first = {}
    for entry in history:
        for trigger in entry["triggers"]:
            if trigger == "basis_breach":
                causes = list(_basis_causes(by_week[entry["week"]], entry["triggers"]))
            else:
                causes = [c] if (c := CAUSE_OF_TRIGGER.get(trigger)) is not None else []
            for cause in causes:
                first.setdefault(cause, entry["week"])
    out = []
    for cause, week in sorted(first.items(), key=lambda kv: kv[1]):
        fields = [f + "_cents" if f in _CENT_EVIDENCE else f for f in CAUSE_FIELDS[cause]]
        out.append(CauseCandidate(cause, week, tuple(f"W:{week}:{f}" for f in fields
                                                     if by_week[week].get(f) is not None), False))
    return out


def usd_baseline(uw):
    rows, features = uw["monthly"], uw["features"]
    days = max(1, 30 * len(rows))
    return {"avg_weekly_revenue_cents": sum(r["revenue_cents"] for r in rows) * 7 // days,
            "operating_daily_cents": sum(r["operating_out_cents"] for r in rows) // days,
            **{k: features[k] for k in ("cash_buffer_days", "revenue_volatility", "tax_bank_match_ratio")}}


def approval_basis(substitutions: Sequence[str], features: Mapping[str, Any]) -> list[dict]:
    """What each applied substitution relied on, and the weekly metric that keeps watching it."""
    out = []
    for code in substitutions:
        if (spec := BASIS_METRICS.get(code)) is not None:
            metric, feature, floor = spec
            out.append({"code": code, "metric": metric, "approved": features.get(feature) if feature else None,
                        "floor": floor})
    return out


def track_summary(weeks: Sequence[Mapping]) -> dict | None:
    """First alert week per track; None when the weeks were recorded before the two tracks existed."""
    if not any("bank_only_triggers" in w for w in weeks):
        return None
    bank = next((w["week"] for w in weeks if w.get("bank_only_triggers")), None)
    full = next((w["week"] for w in weeks if w.get("triggers")), None)
    return {"bank_first_week": bank, "full_first_week": full,
            "lead_weeks": bank - full if bank is not None and full is not None else None,
            "explained_weeks": sum(1 for w in weeks if w.get("explained")),
            "bank_only_weeks": sum(1 for w in weeks if w.get("bank_only_triggers") and not w.get("triggers"))}


def _scheduled(pg, hi: date, recon: float | None) -> dict[str, tuple[date, dict[date, int]]]:
    """Payouts the ledger already shows within each platform's payout lag, discounted by the recent reconciliation
    ratio. Hold releases are one-offs and stay out. A payout dated on a non-business day rolls to the next business
    day, because the forecast adds scheduled amounts on business days only."""
    ratio = 1.0 if recon is None else recon
    out = {}
    for platform in sorted({s.platform_code for s in pg.settlements} & USD_PAYOUT_BUSINESS_DAYS.keys()):
        known = add_business_days(hi, USD_PAYOUT_BUSINESS_DAYS[platform])
        amounts: dict[date, int] = defaultdict(int)
        for s in pg.settlements:
            if (s.platform_code == platform and hi < s.settled_on <= known and s.net_cents > 0
                    and s.released_cents == 0):
                amounts[next_business_day(s.settled_on)] += math.floor(s.net_cents * ratio)
        out[platform] = (known, dict(amounts))
    return out


def _business_days(lo: date, hi: date) -> int:
    return sum(is_business_day(lo + timedelta(days=i)) for i in range((hi - lo).days + 1))


def _prior_year(pg, start: date) -> dict[date, int]:
    """Last year's order gross keyed by this year's date. Empty unless it reaches back to the pre-loan window,
    so a short history never drives a seasonal expectation."""
    year = timedelta(days=PRIOR_YEAR_DAYS)
    if (not pg.prior_year_orders or min(o.order_on for o in pg.prior_year_orders)
            > next_business_day(start - timedelta(days=PRE3_DAYS) - year)):
        return {}
    py: dict[date, int] = defaultdict(int)
    for o in pg.prior_year_orders:
        py[o.order_on + year] += o.gross_cents
    return py


def _window_sum(by_day: Mapping[date, int], lo: date, hi: date) -> int:
    return sum(v for d, v in by_day.items() if lo <= d <= hi)


def _forecast_fields(row, proj):
    low = min(proj["path"], key=lambda p: p["balance_cents"])
    row.update(projected_min_balance_cents=low["balance_cents"], projected_min_date=low["date"],
               shortfall_date=next((p["date"] for p in proj["path"] if p["balance_cents"] < 0), None))
    if due := next((p for p in proj["path"] if p["installment_cents"]), None):
        row.update(next_due_on=due["date"], due_balance_cents=due["before_payment_cents"])
    return low, due


def _ledger_week(row, rows, recent, pg, visible, start, lo, hi):
    """Settlement-ledger fields for this week's row; returns the ledger triggers in their fixed order."""
    pre = start - timedelta(days=PRE_LOAN_DAYS)
    historical = [o for o in pg.orders if pre <= o.order_on < start]
    paid = [s for s in pg.settlements if pre <= s.settled_on < start]
    weekly = sum(o.gross_cents for o in historical) // 8
    weekly_net = sum(s.net_cents for s in paid) // 8
    base_returns = _rate(sum(o.returns_cents for o in historical), sum(o.gross_cents for o in historical)) or 0
    base_deductions = _rate(sum(s.deduction_cents for s in paid), sum(s.gross_cents for s in paid)) or 0
    week_orders = [o for o in pg.orders if lo <= o.order_on <= hi]
    week_paid = [s for s in pg.settlements if lo <= s.settled_on <= hi]
    row["order_gross_cents"] = sum(o.gross_cents for o in week_orders)
    row["returns_cents"] = sum(o.returns_cents for o in week_orders)
    row["return_rate"] = _rate(row["returns_cents"], row["order_gross_cents"])
    row["held_cents"] = sum(s.held_cents for s in week_paid)
    row["deductions_cents"] = sum(s.deduction_cents for s in week_paid)
    row["deduction_rate"] = _rate(row["deductions_cents"], sum(s.gross_cents for s in week_paid))
    row["settle_net_cents"] = sum(s.net_cents for s in week_paid)

    year = timedelta(days=PRIOR_YEAR_DAYS)
    py = _prior_year(pg, start)
    pre3_lo, pre3_hi = start - timedelta(days=PRE3_DAYS), start - timedelta(days=1)
    pre3 = sum(o.gross_cents for o in pg.orders if pre3_lo <= o.order_on <= pre3_hi)
    py_pre3 = _window_sum(py, pre3_lo, pre3_hi)
    py_loan = _window_sum(py, start, week_window(start, 13)[1]) / 13
    seasonal = pre3 > 0 and py_pre3 > 0 and not SEASON_BAND[0] <= py_loan / (py_pre3 / 3) <= SEASON_BAND[1]
    row["expected_orders_cents"] = _window_sum(py, lo, hi) * pre3 // py_pre3 if seasonal else weekly
    actual = sum(r.get("order_gross_cents", 0) for r in recent)
    expected = sum(r.get("expected_orders_cents", weekly) for r in recent)
    row["orders_vs_expected"] = round(actual / expected, 3) if expected else None
    span_lo = date.fromisoformat(recent[0]["start"])
    span_days = _business_days(span_lo, hi)
    base_days = (_business_days(span_lo - year, hi - year) if seasonal
                 else _business_days(pre, pre3_hi) * ((hi - span_lo).days + 1) / PRE_LOAN_DAYS)
    row["orders_per_day_vs_expected"] = (round(actual / span_days / (expected / base_days), 3)
                                         if span_days and expected and base_days else None)
    py_span = _window_sum(py, span_lo, hi)
    row["orders_vs_prior_year"] = round(actual / py_span, 3) if py_span else None
    row["recon_ratio"], row["unreconciled_payout_cents"] = weekly_reconciliation(
        pg.settlements, visible, max(start, hi - timedelta(days=RECON_DAYS - 1)), hi)
    settled = sum(r.get("settle_net_cents", 0) for r in recent)
    row["settle_coverage"] = round(sum(r["revenue_cents"] for r in recent) / settled, 3) if settled > 0 else None

    triggers = []
    if row["orders_vs_expected"] is not None and len(recent) == 3 and row["orders_vs_expected"] < .75:
        triggers.append("sales_slump")
    if weekly_net > 0 and row["held_cents"] >= HOLD_SHARE * weekly_net:
        triggers.append("settlement_hold")
    last = rows[-PERSIST_WEEKS:]
    if len(last) == PERSIST_WEEKS:
        spike = max(RETURN_SPIKE_MULT * base_returns, base_returns + RETURN_SPIKE_PP)
        if all((r.get("return_rate") or 0) >= spike for r in last):
            triggers.append("return_spike")
        if all(r.get("deduction_rate") is not None and r["deduction_rate"] >= base_deductions + COST_SURGE_PP
               for r in last):
            triggers.append("cost_surge")
    return triggers


def observe_usd(loan, visible, pg, week, dues) -> UsdObservation:
    mon, base = loan["monitoring"], loan["baseline"]
    start = date.fromisoformat(mon["flow_start"])
    lo, hi = week_window(start, week)
    cats = {t.txn_id: classify(t) for t in visible}
    latest = [t for t in visible if lo <= t.booked_on]
    receipts = sum(t.amount_cents for t in latest if cats[t.txn_id] == Category.REVENUE)
    expected = base["avg_weekly_revenue_cents"]
    row = {"week": week, "start": lo.isoformat(), "end": hi.isoformat(), "end_balance_cents": visible[-1].balance_after_cents,
           "revenue_cents": receipts, "expected_revenue_cents": expected}
    rows = [w["row"] for w in mon["weeks"]] + [row]
    recent = rows[-3:]
    row["revenue_vs_expected"] = (round(sum(r["revenue_cents"] for r in recent) / (len(recent) * expected), 3)
                                  if expected else None)
    bank_proj = project_usd(visible, start, hi, dues)
    bank_low, due = _forecast_fields(row, bank_proj)
    due_short = due is not None and due["before_payment_cents"] < due["installment_cents"]
    bank_only = [t for t, fired in (("inflow_drop", len(recent) == 3 and expected and row["revenue_vs_expected"] < .75),
                                    ("projected_shortfall", bank_low["balance_cents"] < 0),
                                    ("due_shortfall", due_short)) if fired]
    if pg is None:
        return UsdObservation(rows, list(bank_only), [], cats, bank_proj, bank_only, [], [])

    ledger = _ledger_week(row, rows, recent, pg, visible, start, lo, hi)
    basis = []
    for item in loan.get("approval_basis") or []:
        value = row.get(item["metric"])
        status = "unknown" if value is None else "breach" if value < item["floor"] else "ok"
        basis.append({**item, "value": value, "status": status})
    if breached := [b["code"] for b in basis if b["status"] == "breach"]:
        row["basis_breached"] = breached
    proj = project_usd(visible, start, hi, dues, scheduled=_scheduled(pg, hi, row["recon_ratio"]))
    low, _ = _forecast_fields(row, proj)

    explained = []
    inflow = "inflow_drop" in bank_only
    if (inflow and "sales_slump" not in ledger
            and (row["settle_coverage"] or 0) >= EXPLAIN_COVERAGE
            and (row["orders_per_day_vs_expected"] or 0) >= EXPLAIN_ORDERS):
        inflow = False
        explained.append({"trigger": "inflow_drop", "reason": "scheduled_settlements",
                          "metric_ids": [f"W:{week}:revenue_vs_expected", f"W:{week}:settle_coverage",
                                         f"W:{week}:orders_per_day_vs_expected"]})
    if "projected_shortfall" in bank_only and low["balance_cents"] >= 0:
        explained.append({"trigger": "projected_shortfall", "reason": "scheduled_payouts",
                          "metric_ids": [f"F:{week}:projected_min_balance_cents"]})
    triggers = (["inflow_drop"] if inflow else []) + ledger + (["basis_breach"] if breached else [])
    if low["balance_cents"] < 0:
        triggers.append("projected_shortfall")
    if due_short:
        triggers.append("due_shortfall")
    candidates = _candidates([*mon["weeks"], {"week": week, "triggers": triggers}], rows)
    return UsdObservation(rows, triggers, candidates, cats, proj, bank_only, explained, basis)
