"""Weekly observations and a trailing cash-flow forecast for the synthetic USD product."""

from datetime import date, timedelta

from agents.classify import Category, classify
from agents.forecast import project_usd
from agents.ledger_monitor import (CAUSE_FIELDS, CAUSE_OF_TRIGGER, COST_SURGE_PP, HOLD_SHARE, PERSIST_WEEKS,
                                   PRE_LOAN_DAYS, RETURN_SPIKE_MULT, RETURN_SPIKE_PP, CauseCandidate)
from agents.monitoring import week_window

_CENT_EVIDENCE = {"order_gross", "held", "advance_in", "returns", "deductions", "high_cost_lender", "loan_proceeds"}


def _rate(part, whole):
    return round(part / whole, 4) if whole else None


def _candidates(history, rows):
    """Causes in order of first firing week; evidence cites that week's USD row fields."""
    first = {}
    for entry in history:
        for trigger in entry["triggers"]:
            if (cause := CAUSE_OF_TRIGGER.get(trigger)) is not None and cause not in first:
                first[cause] = entry["week"]
    by_week = {r["week"]: r for r in rows}
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


def observe_usd(loan, visible, pg, week, dues):
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
    triggers, candidates = [], []
    if len(recent) == 3 and expected and row["revenue_vs_expected"] < .75:
        triggers.append("inflow_drop")
    if pg is not None:
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
        row["orders_vs_expected"] = (round(sum(r["order_gross_cents"] for r in recent) / (weekly * len(recent)), 3)
                                     if weekly else None)
        if weekly and len(recent) == 3 and row["orders_vs_expected"] < .75:
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
        candidates = _candidates([*mon["weeks"], {"week": week, "triggers": triggers}], rows)
    proj = project_usd(visible, start, hi, dues)
    low = min(proj["path"], key=lambda p: p["balance_cents"])
    row.update(projected_min_balance_cents=low["balance_cents"], projected_min_date=low["date"],
               shortfall_date=next((p["date"] for p in proj["path"] if p["balance_cents"] < 0), None))
    if low["balance_cents"] < 0:
        triggers.append("projected_shortfall")
    if due := next((p for p in proj["path"] if p["installment_cents"]), None):
        row.update(next_due_on=due["date"], due_balance_cents=due["before_payment_cents"])
        if due["before_payment_cents"] < due["installment_cents"]:
            triggers.append("due_shortfall")
    return rows, triggers, candidates, cats, proj
