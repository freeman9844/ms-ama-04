"""Weekly observations and a trailing cash-flow forecast for the synthetic USD product."""

from datetime import date, timedelta

from agents.classify import Category, classify
from agents.forecast import project_usd
from agents.ledger_monitor import CauseCandidate
from agents.monitoring import week_window
from shared.contracts import AlertCause


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
        historical = [o for o in pg.orders if start - timedelta(weeks=8) <= o.order_on < start]
        weekly = sum(o.gross_cents for o in historical) // 8
        row["order_gross_cents"] = sum(o.gross_cents for o in pg.orders if lo <= o.order_on <= hi)
        row["settle_net_cents"] = sum(s.net_cents for s in pg.settlements if lo <= s.settled_on <= hi)
        row["orders_vs_expected"] = (round(sum(r["order_gross_cents"] for r in recent) / (weekly * len(recent)), 3)
                                     if weekly else None)
        if weekly and len(recent) == 3 and row["orders_vs_expected"] < .75:
            triggers.append("sales_slump")
            first = next((w["week"] for w in mon["weeks"] if "sales_slump" in w["triggers"]), week)
            candidates.append(CauseCandidate(AlertCause.SALES_SLUMP, first, (f"W:{first}:orders_vs_expected",), False))
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
