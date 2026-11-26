"""USD 감시 일지 두 단 그래프 기하(순수 함수). 마크업은 `templates/_charts.html` 매크로가 그린다.

세로 배치(위에서 아래로): 경보 글자 → 단계 띠 → 위 단(기대 대비 비율) → 마켓 칩 줄 → 아래 단(현금 여력)
→ 상환일 줄 → 주차 축 글자. 가로는 `charts.PAD_L`/`PAD_R` 안을 `total`칸으로 고르게 나눈다.
"""

from collections.abc import Callable, Iterable, Mapping
from datetime import date, timedelta
from typing import Any

from console.charts import PAD_L, PAD_R, PAD_T, _r, is_num

BAND_Y, BAND_H = PAD_T + 18, 14
RATIO_TOP, RATIO_BASE = BAND_Y + BAND_H + 14, BAND_Y + BAND_H + 134
RATIO_MAX, THRESHOLD = 1.2, 0.75
GRADE_Y = RATIO_BASE + 20
CASH_TOP, CASH_BASE = GRADE_Y + 24, GRADE_Y + 144
DUE_Y = CASH_BASE + 18
LABEL_Y = DUE_Y + 20
HEIGHT = LABEL_Y + 8
TICK_GAP = 10


def _orders1(row: Mapping[str, Any]) -> Any:
    gross, expected = row.get("order_gross_cents"), row.get("expected_orders_cents")
    return gross / expected if is_num(gross) and is_num(expected) and expected > 0 else None


def _polyline(values: Iterable[tuple[int, Any]], cx: Callable[[int], float],
              y: Callable[[float], float], **extra: Callable[[int, float], Any]) -> dict[str, Any]:
    """주차순 `(week, value)`의 꺾은선. 결측 값이나 빠진 주차에서 선을 끊는다."""
    runs: list[list[tuple[float, float]]] = [[]]
    points, prev = [], None
    for week, v in values:
        if not is_num(v) or (prev is not None and week != prev + 1):
            runs.append([])
        prev = week
        if not is_num(v):
            continue
        x, py = cx(week), y(v)
        points.append({"week": week, "x": x, "y": py, "value": v, **{k: f(week, v) for k, f in extra.items()}})
        runs[-1].append((x, py))
    return {"segments": ["M " + " L ".join(f"{px} {py}" for px, py in run) for run in runs if run],
            "points": points}


def _week_one(weeks: list[dict[str, Any]]) -> date | None:
    for w in weeks:
        start = (w.get("row") or {}).get("start")
        if isinstance(start, str):
            try:
                return date.fromisoformat(start[:10]) - timedelta(days=7 * (w["week"] - 1))
            except ValueError:
                continue
    return None


def watch_chart(weeks: list[dict[str, Any]], lines: list[dict[str, Any]], installments: list[dict[str, Any]],
                *, total: int = 13, width: int = 960) -> dict[str, Any] | None:
    """감시 일지 위 두 단 그래프. 처리된 주(`weeks`)만 값을 그리고, 일지가 없으면 None."""
    if not weeks or not lines:
        return None
    x0, x1 = PAD_L, width - PAD_R
    slot = (x1 - x0) / total
    rows = {w["week"]: w.get("row") or {} for w in weeks if 1 <= w["week"] <= total}
    by_week = {ln["week"]: ln for ln in lines}
    done = sorted(rows)
    current = done[-1] if done else None

    def cx(week: int) -> float:
        return _r(x0 + slot * (week - 0.5))

    slots = []
    for n in range(1, total + 1):
        line = by_week.get(n) if n in rows else None
        stage = line.get("stage") if line else None
        slots.append({"week": n, "x": _r(x0 + slot * (n - 1)), "w": _r(slot), "cx": cx(n), "done": n in rows,
                      "current": n == current, "stage": stage,
                      "alert_stage": stage if line and line.get("alert_id") else None,
                      "href": f"#journal-w{n}" if n in rows else None})

    ratio_h = RATIO_BASE - RATIO_TOP

    def ratio_y(v: float) -> float:
        return _r(RATIO_BASE - ratio_h * min(max(v, 0.0), RATIO_MAX) / RATIO_MAX)

    def clipped(_week: int, v: float) -> bool:
        return v > RATIO_MAX

    series = {
        "orders3": lambda row: row.get("orders_vs_expected"),
        "orders1": _orders1,
        "revenue3": lambda row: row.get("revenue_vs_expected"),
    }
    ratio = {
        "top": RATIO_TOP, "base": RATIO_BASE,
        "ticks": [{"y": ratio_y(f), "text": f"{f:.0%}"} for f in (0.0, 0.5, 1.0)],
        "threshold_y": ratio_y(THRESHOLD),
        "lines": {k: _polyline(((n, get(rows[n])) for n in done), cx, ratio_y, clipped=clipped)
                  for k, get in series.items()},
    }

    def market_signal(week: int) -> bool:
        sources = (by_week.get(week) or {}).get("sources") or ()
        return any(s.get("source") == "market" and s.get("state") == "signal" for s in sources)

    grades = [{"week": n, "cx": cx(n), "y": GRADE_Y, "grade": rows[n].get("mkt_grade"), "signal": market_signal(n)}
              for n in done]

    bar_keys = (("orders", "order_gross_cents"), ("inflow", "revenue_cents"))
    cash_keys = (*(k for _kind, k in bar_keys), "projected_min_balance_cents")
    cash_vals = [float(rows[n][k]) for n in done for k in cash_keys if is_num(rows[n].get(k))]
    lo, hi = min([*cash_vals, 0.0]), max([*cash_vals, 0.0])
    span = (hi - lo) or 1.0
    cash_h = CASH_BASE - CASH_TOP

    def cash_y(v: float) -> float:
        return _r(CASH_BASE - cash_h * (v - lo) / span)

    zero_y = cash_y(0.0)
    bw = slot * 0.7 / len(bar_keys)
    bars = []
    for n in done:
        for j, (kind, key) in enumerate(bar_keys):
            v = rows[n].get(key)
            if not is_num(v):
                continue
            vy = cash_y(v)
            top, bottom = min(vy, zero_y), max(vy, zero_y)
            bars.append({"week": n, "kind": kind, "x": _r(x0 + slot * (n - 1) + slot * 0.15 + bw * j + bw * 0.05),
                         "y": top, "w": _r(bw * 0.9), "h": _r(bottom - top), "value": v})
    cash = {
        "top": CASH_TOP, "base": CASH_BASE, "zero_y": zero_y,
        # $0 눈금과 10px 안으로 붙는 끝 눈금은 글자가 겹쳐 뺀다.
        "ticks": [{"y": cash_y(t), "text_cents": int(t) if float(t).is_integer() else t}
                  for t in sorted({lo, 0.0, hi}) if t == 0 or abs(cash_y(t) - zero_y) >= TICK_GAP],
        "bars": bars,
        "balance": _polyline(((n, rows[n].get("projected_min_balance_cents")) for n in done), cx, cash_y,
                             negative=lambda _week, v: v < 0,
                             shortfall_date=lambda week, _v: rows[week].get("shortfall_date")),
    }

    dues = []
    first = _week_one(weeks)
    for inst in installments if first else ():
        try:
            due = date.fromisoformat(str(inst.get("due_on"))[:10])
        except ValueError:
            continue
        n = (due - first).days // 7 + 1
        if 1 <= n <= total:
            dues.append({"week": n, "cx": cx(n), "due_on": inst["due_on"],
                         "state": inst.get("status") if n in rows else "future"})

    return {"width": width, "height": HEIGHT, "x0": x0, "x1": x1, "label_y": LABEL_Y, "due_y": DUE_Y,
            "slots": slots, "band": {"y": BAND_Y, "h": BAND_H}, "ratio": ratio, "grades": grades,
            "cash": cash, "dues": dues}
