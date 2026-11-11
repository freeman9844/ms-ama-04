"""PG 원장 모니터(결정적): 대출 전 8주 기준선, 주간 원장 필드, 새 트리거 4개, 원인 후보(스펙 §6).

- 기준선은 대출 전 8주(PRE_LOAN_DAYS) 주문·정산으로 만든다. 저장된 `Baseline`(심사 기준선)은 건드리지 않는다.
  주문액은 업종 집단 월 지수로 계절을 뺀다(`weekly_orders`). 원장이 없으면 기준선도 없고 새 트리거도 없다.
- 주간 행: 주문일 기준(order_gross·returns·return_rate·orders_vs_expected)과 지급일 기준(held·deductions·
  deduction_rate·settle_net)을 나눠 센다. 쿠팡 주정산처럼 지급이 몇 주 늦는 플랫폼 때문에 주문 하락은
  주문일로만 제때 보인다(스펙 §5.2a).
- 트리거는 마지막 주차 행 기준이고, 원인 후보는 1~week 각 시점의 트리거에서 첫 발화 주차를 모아 만든다.
  차입(new_debt)은 다른 원인이 먼저(또는 같은 주) 났으면 결과(consequence)로 표시한다.
- 근거 ID: 주간은 기존 `W:{주차}:{필드}`, 원장 기준선은 `B:ledger:{이름}`.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from agents.classify import Category
from agents.monitoring import (
    ADVANCE_KEYWORD,
    TRIGGERS,
    Baseline,
    cohort,
    detect,
    week_window,
    weekly_series,
)
from shared.contracts import AlertCause, PgFlow, Transaction

PRE_LOAN_DAYS = 56  # 원장 기준선 구간(대출 전 8주)
PRE_ADVANCE_DAYS = 90  # 기존 선정산 양도를 보는 대출 전 구간
SLUMP_RATIO = 0.75
SLUMP_WEEKS = 3
HOLD_SHARE = 0.30
HOLD_ZERO_WEEKS = 2
HOLD_ORDER_FLOOR = 0.75
HOLD_MIN_PLATFORM_SHARE = 0.10
RETURN_SPIKE_MULT = 2.0
RETURN_SPIKE_PP = 0.05
COST_SURGE_PP = 0.04
PERSIST_WEEKS = 2

CAUSE_OF_TRIGGER: dict[str, AlertCause] = {
    "sales_slump": AlertCause.SALES_SLUMP,
    "settlement_hold": AlertCause.SETTLEMENT_HOLD,
    "return_spike": AlertCause.RETURN_SPIKE,
    "cost_surge": AlertCause.COST_SURGE,
    "new_debt": AlertCause.HIGH_COST_BORROWING,
}
CAUSE_FIELDS: dict[AlertCause, tuple[str, ...]] = {
    AlertCause.SALES_SLUMP: ("order_gross", "orders_vs_expected"),
    AlertCause.SETTLEMENT_HOLD: ("held", "advance_in"),
    AlertCause.RETURN_SPIKE: ("returns", "return_rate"),
    AlertCause.COST_SURGE: ("deductions", "deduction_rate"),
    AlertCause.HIGH_COST_BORROWING: ("high_cost_lender", "high_cost_count", "loan_proceeds"),
}
PLATFORM_FIELDS = ("order_gross", "returns", "held", "released", "deductions", "settle_net")


@dataclass(frozen=True)
class LedgerBaseline:
    """대출 전 8주 원장 기준선. 주문액은 계절을 뺀 주평균이다.

    month_index는 기준선을 만들 때 쓴 업종 집단 월 지수다. 주차 기대 주문액을 같은 지수로 되돌려야 해서
    기준선이 들고 다닌다(observe는 업종을 받지 않는다). 지수가 없으면 계절 보정 없이 주평균을 그대로 쓴다.
    """

    weekly_orders: int
    return_rate: float
    deduction_rate: float
    weekly_settle_net: int
    platform_weekly_net: dict[str, int]
    platform_weekly_orders: dict[str, int]
    prior_advance: bool
    month_index: dict[int, float] = field(default_factory=lambda: dict.fromkeys(range(1, 13), 1.0))

    def to_dict(self) -> dict[str, Any]:
        return {
            "weekly_orders": self.weekly_orders,
            "return_rate": self.return_rate,
            "deduction_rate": self.deduction_rate,
            "weekly_settle_net": self.weekly_settle_net,
            "platform_weekly_net": dict(self.platform_weekly_net),
            "platform_weekly_orders": dict(self.platform_weekly_orders),
            "prior_advance": self.prior_advance,
            "month_index": {f"{m:02d}": v for m, v in self.month_index.items()},
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "LedgerBaseline":
        index = d.get("month_index") or {}
        return cls(
            weekly_orders=int(d["weekly_orders"]),
            return_rate=float(d["return_rate"]),
            deduction_rate=float(d["deduction_rate"]),
            weekly_settle_net=int(d["weekly_settle_net"]),
            platform_weekly_net={str(k): int(v) for k, v in d["platform_weekly_net"].items()},
            platform_weekly_orders={str(k): int(v) for k, v in d["platform_weekly_orders"].items()},
            prior_advance=bool(d["prior_advance"]),
            month_index={int(k): float(v) for k, v in index.items()} or dict.fromkeys(range(1, 13), 1.0),
        )


@dataclass(frozen=True)
class CauseCandidate:
    """원인 후보: 첫 발화 주차와 그 주차 근거 ID. consequence는 다른 원인이 먼저(또는 같은 주) 났다는 뜻."""

    cause: AlertCause
    first_week: int
    evidence_ids: tuple[str, ...]
    consequence: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "cause": self.cause.value,
            "first_week": self.first_week,
            "evidence_ids": list(self.evidence_ids),
            "consequence": self.consequence,
        }


@dataclass(frozen=True)
class Observation:
    """주간 행·마지막 주차 트리거·원인 후보. history는 주차별 트리거 이력이다."""

    rows: list[dict[str, Any]]
    triggers: list[str]
    candidates: list[CauseCandidate]
    history: dict[int, list[str]]


def _rate(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0


def ledger_baseline(
    pg: PgFlow, start: date, industry: str, prior_txns: Sequence[Transaction]
) -> LedgerBaseline | None:
    """[start-56, start) 원장·주문으로 만든 기준선. 그 구간 주문이 없으면 None."""
    lo = start - timedelta(days=PRE_LOAN_DAYS)
    orders = [o for o in pg.orders if lo <= o.order_on < start]
    if not orders:
        return None
    index = cohort(industry)["index"]
    season = sum(index[d.month] for d in sorted({o.order_on for o in orders}))
    gross = sum(o.gross_krw for o in orders)
    settlements = [s for s in pg.settlements if lo <= s.settled_on < start]
    settle_gross = sum(s.gross_krw for s in settlements)
    weeks = PRE_LOAN_DAYS / 7
    platform_net: dict[str, int] = {}
    for s in settlements:
        platform_net[s.platform] = platform_net.get(s.platform, 0) + s.net_krw
    platform_orders: dict[str, int] = {}
    for o in orders:
        platform_orders[o.platform] = platform_orders.get(o.platform, 0) + o.gross_krw
    advance_from = start - timedelta(days=PRE_ADVANCE_DAYS)
    return LedgerBaseline(
        weekly_orders=round(gross / season * 7) if season else 0,
        return_rate=_rate(sum(o.returns_krw for o in orders), gross),
        deduction_rate=_rate(sum(s.deduction_krw for s in settlements), settle_gross),
        weekly_settle_net=round(sum(s.net_krw for s in settlements) / weeks),
        platform_weekly_net={p: round(v / weeks) for p, v in sorted(platform_net.items())},
        platform_weekly_orders={p: round(v / weeks) for p, v in sorted(platform_orders.items())},
        prior_advance=any(
            t.amount_krw > 0 and ADVANCE_KEYWORD in t.description and advance_from <= t.booked_on < start
            for t in prior_txns
        ),
        month_index=dict(index),
    )


def ledger_catalog(lb: LedgerBaseline | None) -> dict[str, float | int]:
    """원장 기준선 근거 ID → 값. 기준선이 없으면 빈 사전."""
    if lb is None:
        return {}
    return {
        "B:ledger:weekly_orders": lb.weekly_orders,
        "B:ledger:return_rate": lb.return_rate,
        "B:ledger:deduction_rate": lb.deduction_rate,
        "B:ledger:weekly_settle_net": lb.weekly_settle_net,
    }


def _expected_orders(lb: LedgerBaseline, lo: date) -> float:
    """그 주 기대 주문액: 계절 뺀 주평균 × 그 주 날짜들의 월 지수 평균."""
    days = [lo + timedelta(days=k) for k in range(7)]
    return lb.weekly_orders * sum(lb.month_index[d.month] for d in days) / len(days)


def _platform_week(pg: PgFlow, start: date, week: int) -> dict[str, dict[str, int]]:
    lo, hi = week_window(start, week)
    out: dict[str, dict[str, int]] = {}

    def slot(platform: str) -> dict[str, int]:
        return out.setdefault(platform, dict.fromkeys(PLATFORM_FIELDS, 0))

    for o in pg.orders:
        if lo <= o.order_on <= hi:
            row = slot(o.platform)
            row["order_gross"] += o.gross_krw
            row["returns"] += o.returns_krw
    for s in pg.settlements:
        if lo <= s.settled_on <= hi:
            row = slot(s.platform)
            row["held"] += s.held_krw
            row["released"] += s.released_krw
            row["deductions"] += s.deduction_krw
            row["settle_net"] += s.net_krw
    return out


def platform_weeks(pg: PgFlow | None, start: date, from_week: int, to_week: int) -> list[dict[str, Any]] | None:
    """플랫폼×주 원장 집계(조기경보 도구용). 원장이 없으면 None."""
    if pg is None:
        return None
    rows: list[dict[str, Any]] = []
    for week in range(from_week, to_week + 1):
        for platform, values in sorted(_platform_week(pg, start, week).items()):
            rows.append({"week": week, "platform": platform, **values})
    return rows


def _ledger_rows(rows: Sequence[dict[str, Any]], pg: PgFlow, start: date, lb: LedgerBaseline) -> None:
    """주간 행에 원장 집계를 더한다(제자리 수정)."""
    for row in rows:
        lo, hi = week_window(start, row["week"])
        orders = [o for o in pg.orders if lo <= o.order_on <= hi]
        settlements = [s for s in pg.settlements if lo <= s.settled_on <= hi]
        order_gross = sum(o.gross_krw for o in orders)
        deductions = sum(s.deduction_krw for s in settlements)
        recent = rows[max(0, row["week"] - SLUMP_WEEKS) : row["week"]]
        expected = sum(_expected_orders(lb, date.fromisoformat(r["start"])) for r in recent)
        row.update(
            {
                "order_gross": order_gross,
                "returns": sum(o.returns_krw for o in orders),
                "held": sum(s.held_krw for s in settlements),
                "deductions": deductions,
                "settle_net": sum(s.net_krw for s in settlements),
                "deduction_rate": _rate(deductions, sum(s.gross_krw for s in settlements)),
            }
        )
        row["return_rate"] = _rate(row["returns"], order_gross)
        row["orders_vs_expected"] = (
            round((sum(r["order_gross"] for r in recent[:-1]) + order_gross) / expected, 3) if expected else 0.0
        )


def _steady_payout_platforms(pg: PgFlow, start: date) -> set[str]:
    """대출 전 8주 지급 간격이 HOLD_ZERO_WEEKS주보다 짧았던 플랫폼.

    11번가처럼 월 2회 정산하는 플랫폼은 보름 간격 지급 공백이 원래 모습이라 보류 신호로 쓸 수 없다.
    구간 앞뒤 끝도 간격으로 센다(대출 전에 한 번만 지급한 플랫폼은 제외된다).
    """
    pre_start = start - timedelta(days=PRE_LOAN_DAYS)
    paid: dict[str, set[date]] = {}
    for s in pg.settlements:
        if pre_start <= s.settled_on < start and s.net_krw > 0:
            paid.setdefault(s.platform, set()).add(s.settled_on)
    steady = set()
    for platform, days in paid.items():
        marks = [pre_start - timedelta(days=1), *sorted(days), start]
        if max((b - a).days for a, b in zip(marks, marks[1:])) < HOLD_ZERO_WEEKS * 7:
            steady.add(platform)
    return steady


def _zero_payout_platform(
    rows: Sequence[dict[str, Any]],
    lb: LedgerBaseline,
    by_week: Mapping[int, Mapping[str, Mapping[str, int]]],
    steady: set[str],
) -> bool:
    """기준 비중 10% 이상인 플랫폼이 2주 연속 지급 0이고 그 플랫폼 주문은 정상인가.

    대출 전 8주에 2주 연속 지급이 없던 플랫폼(11번가처럼 월 2회 정산)은 보지 않는다: 지급 공백이 원래 모습이다.
    """
    if len(rows) < HOLD_ZERO_WEEKS or lb.weekly_settle_net <= 0:
        return False
    weeks = [r["week"] for r in rows[-HOLD_ZERO_WEEKS:]]
    for platform, weekly_net in lb.platform_weekly_net.items():
        if weekly_net < HOLD_MIN_PLATFORM_SHARE * lb.weekly_settle_net or platform not in steady:
            continue
        base_orders = lb.platform_weekly_orders.get(platform, 0)
        if base_orders <= 0:
            continue
        nets = [by_week[w].get(platform, {}).get("settle_net", 0) for w in weeks]
        orders = sum(by_week[w].get(platform, {}).get("order_gross", 0) for w in weeks)
        if all(n <= 0 for n in nets) and orders >= HOLD_ORDER_FLOOR * base_orders * HOLD_ZERO_WEEKS:
            return True
    return False


def _ledger_triggers(
    rows: Sequence[dict[str, Any]],
    lb: LedgerBaseline,
    by_week: Mapping[int, Mapping[str, Mapping[str, int]]],
    steady: set[str],
) -> list[str]:
    """마지막 주차 행에 걸린 원장 트리거."""
    row = rows[-1]
    out: list[str] = []
    if len(rows) >= SLUMP_WEEKS and row["orders_vs_expected"] < SLUMP_RATIO:
        out.append("sales_slump")
    if (lb.weekly_settle_net > 0 and row["held"] >= HOLD_SHARE * lb.weekly_settle_net) or _zero_payout_platform(
        rows, lb, by_week, steady
    ):
        out.append("settlement_hold")
    recent = rows[-PERSIST_WEEKS:]
    if len(recent) == PERSIST_WEEKS:
        spike = max(RETURN_SPIKE_MULT * lb.return_rate, lb.return_rate + RETURN_SPIKE_PP)
        if all(r["return_rate"] >= spike for r in recent):
            out.append("return_spike")
        if all(r["deduction_rate"] >= lb.deduction_rate + COST_SURGE_PP for r in recent):
            out.append("cost_surge")
    return out


def _candidates(history: Mapping[int, Sequence[str]], rows: Sequence[Mapping[str, Any]]) -> list[CauseCandidate]:
    first: dict[AlertCause, int] = {}
    for week in sorted(history):
        for trigger in history[week]:
            cause = CAUSE_OF_TRIGGER.get(trigger)
            if cause is not None and cause not in first:
                first[cause] = week
    order = {name: i for i, name in enumerate(TRIGGERS)}
    rank = {cause: order[trigger] for trigger, cause in CAUSE_OF_TRIGGER.items()}
    out: list[CauseCandidate] = []
    for cause, week in sorted(first.items(), key=lambda kv: (kv[1], rank[kv[0]])):
        row = rows[week - 1]
        others = [w for c, w in first.items() if c is not cause]
        out.append(
            CauseCandidate(
                cause=cause,
                first_week=week,
                evidence_ids=tuple(f"W:{week}:{f}" for f in CAUSE_FIELDS[cause] if f in row),
                consequence=cause is AlertCause.HIGH_COST_BORROWING and any(w <= week for w in others),
            )
        )
    return out


def observe(
    transactions: Sequence[Transaction],
    categories: Mapping[str, Category],
    start: date,
    week: int,
    baseline: Baseline,
    pg: PgFlow | None,
    lb: LedgerBaseline | None,
) -> Observation:
    """주간 행(원장 필드 포함)·주차별 트리거·원인 후보. 원장이나 기준선이 없으면 계좌 지표만 쓴다."""
    rows = weekly_series(transactions, categories, start, week, baseline)
    ledger = pg is not None and lb is not None
    by_week: dict[int, dict[str, dict[str, int]]] = {}
    steady: set[str] = set()
    if ledger:
        _ledger_rows(rows, pg, start, lb)
        by_week = {r["week"]: _platform_week(pg, start, r["week"]) for r in rows}
        steady = _steady_payout_platforms(pg, start)
    order = {name: i for i, name in enumerate(TRIGGERS)}
    history: dict[int, list[str]] = {}
    for n in range(1, week + 1):
        hits = detect(rows[:n], baseline)
        if ledger:
            hits += _ledger_triggers(rows[:n], lb, by_week, steady)
        history[n] = sorted(set(hits), key=order.__getitem__)
    return Observation(
        rows=rows,
        triggers=history.get(week, []),
        candidates=_candidates(history, rows),
        history=history,
    )
