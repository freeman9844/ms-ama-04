"""SVG 차트 기하(순수 함수). 좌표·길이만 돌려주고 마크업은 `templates/_charts.html` 매크로가 그린다(P7).

모든 좌표는 소수점 둘째 자리로 반올림한다. 값은 저장된 그대로 돌려주어 툴팁에 원래 값을 보인다.
"""

import math
from collections.abc import Callable, Sequence
from typing import Any

PAD_L, PAD_R, PAD_T, PAD_B = 56, 48, 10, 26
GAUGE_ARC = "M 10 64 A 50 50 0 0 1 110 64"

Segment = tuple[str, Any, str]
Part = tuple[str, Any, str, str]


def _r(v: float) -> float:
    out = round(float(v), 2)
    return 0.0 if out == 0 else out


def is_num(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v)


def _pos(v: Any) -> float:
    return float(v) if is_num(v) and v > 0 else 0.0


def _plain(v: float) -> str:
    return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.1f}"


def _top(values: Sequence[float], vmax: Any) -> float:
    top = float(vmax) if is_num(vmax) and vmax > 0 else max(values, default=0.0)
    return top if top > 0 else 1.0


def _ticks(top: float, base: float, plot_h: float, fmt: Callable[[float], str]) -> list[dict[str, Any]]:
    return [{"y": _r(base - plot_h * f), "value": top * f, "text": fmt(top * f)} for f in (0.0, 0.5, 1.0)]


def donut(segments: Sequence[Segment], r: float = 46.0) -> dict[str, Any]:
    """도넛 호. 0·음수·결측 값은 건너뛴다. 호는 `rotate(-90)`으로 12시 방향에서 시작한다."""
    c = 2 * math.pi * r
    kept = [(label, v, color) for label, v, color in segments if _pos(v) > 0]
    total = sum(v for _, v, _ in kept)
    arcs, acc = [], 0.0
    for label, v, color in kept:
        dash = c * v / total
        arcs.append({"label": label, "value": v, "color": color, "dash": _r(dash), "gap": _r(c - dash),
                     "offset": _r(-acc), "pct": round(v / total * 100, 1)})
        acc += dash
    return {"r": r, "circumference": _r(c), "total": total, "arcs": arcs}


def hbars(items: Sequence[Segment], width: int = 360, label_w: int = 104, row: int = 28,
          bar: int = 16) -> dict[str, Any]:
    """가로 막대. 최댓값 기준 비율이고, 최댓값이 0이면 모든 막대 너비가 0이다."""
    span = width - label_w - 48
    vals = [_pos(v) for _, v, _ in items]
    vmax = max(vals, default=0.0)
    bars = []
    for i, ((label, v, color), pv) in enumerate(zip(items, vals, strict=True)):
        y = i * row + (row - bar) / 2
        w = span * pv / vmax if vmax else 0.0
        bars.append({"label": label, "value": v, "color": color, "x": label_w, "y": _r(y), "w": _r(w), "h": bar,
                     "ty": _r(y + bar / 2 + 4), "vx": _r(label_w + w + 6)})
    return {"width": width, "height": len(items) * row, "bars": bars}


def columns(groups: Sequence[tuple[str, Sequence[Sequence[Part]]]], width: int = 640, height: int = 240,
            refs: Sequence[Segment] = (), vmax: Any = None,
            tick_fmt: Callable[[float], str] = _plain) -> dict[str, Any]:
    """세로 막대. 그룹마다 막대 여러 개, 막대마다 아래에서 위로 쌓는 조각 `(part, value, color, tip)`.

    음수·결측 조각은 높이 0이고 `value`·`tip`은 그대로 둔다. 기준선 `refs`도 축 최댓값 계산에 넣는다.
    """
    x0, x1, top_y, base = PAD_L, width - PAD_R, PAD_T, height - PAD_B
    plot_h = base - top_y
    stacks = [sum(_pos(p[1]) for p in bar) for _, bars in groups for bar in bars]
    top = _top(stacks + [_pos(v) for _, v, _ in refs], vmax)
    slot = (x1 - x0) / len(groups) if groups else 0.0
    out_groups = []
    for i, (label, bars) in enumerate(groups):
        bw = slot * 0.7 / max(len(bars), 1)
        out_bars = []
        for j, bar in enumerate(bars):
            cursor = float(base)
            parts = []
            for part, value, color, tip in bar:
                h = min(plot_h * _pos(value) / top, cursor - top_y)
                cursor -= h
                parts.append({"part": part, "value": value, "color": color, "tip": tip, "y": _r(cursor), "h": _r(h)})
            out_bars.append({"x": _r(x0 + slot * i + slot * 0.15 + bw * j + bw * 0.05), "w": _r(bw * 0.9),
                             "top": _r(cursor), "parts": parts})
        out_groups.append({"label": label, "cx": _r(x0 + slot * (i + 0.5)), "bars": out_bars})
    out_refs = [{"label": label, "value": v, "color": color,
                 "y": _r(base - plot_h * min(_pos(v) / top, 1.0)), "text": tick_fmt(v)}
                for label, v, color in refs if is_num(v)]
    return {"width": width, "height": height, "x0": x0, "x1": x1, "base": base, "label_y": base + 16,
            "vmax": top, "ticks": _ticks(top, base, plot_h, tick_fmt), "groups": out_groups, "refs": out_refs}


def line(values: Sequence[Any], width: int = 640, height: int = 240, vmax: Any = None,
         tick_fmt: Callable[[float], str] = _plain) -> dict[str, Any]:
    """`columns`와 같은 칸 배치의 꺾은선(오른쪽 축). 결측 값에서 선을 끊는다."""
    x0, x1, top_y, base = PAD_L, width - PAD_R, PAD_T, height - PAD_B
    plot_h = base - top_y
    top = _top([_pos(v) for v in values], vmax)
    slot = (x1 - x0) / len(values) if values else 0.0
    runs: list[list[tuple[float, float]]] = [[]]
    points = []
    for i, v in enumerate(values):
        if not is_num(v):
            runs.append([])
            continue
        x, y = _r(x0 + slot * (i + 0.5)), _r(base - plot_h * min(_pos(v) / top, 1.0))
        points.append({"i": i, "x": x, "y": y, "value": v})
        runs[-1].append((x, y))
    segments = ["M " + " L ".join(f"{x} {y}" for x, y in run) for run in runs if run]
    return {"vmax": top, "x": x1, "segments": segments, "points": points,
            "ticks": _ticks(top, base, plot_h, tick_fmt)}


def gauge(value: Any, vmax: Any) -> dict[str, Any]:
    """반원 게이지. 채움 비율은 0..1로 자르고, 값이나 최댓값이 없으면 `frac`이 None이다."""
    if not is_num(value) or not is_num(vmax) or vmax <= 0:
        return {"d": GAUGE_ARC, "frac": None, "pct": 0.0}
    frac = min(max(value / vmax, 0.0), 1.0)
    return {"d": GAUGE_ARC, "frac": round(frac, 4), "pct": _r(frac * 100)}


def marker_bar(observed: Any, threshold: Any) -> dict[str, float] | None:
    """관측값 채움 + 기준선 위치(%). 둘 중 하나라도 숫자가 아니거나 음수면 None."""
    if not (is_num(observed) and is_num(threshold)) or observed < 0 or threshold < 0:
        return None
    scale = max(observed, threshold) * 1.25 or 1.0
    return {"fill": _r(observed / scale * 100), "mark": _r(threshold / scale * 100)}


def stacked(rows: Sequence[tuple[str, Sequence[Segment]]]) -> list[dict[str, Any]]:
    """100% 누적 가로 막대(x·w는 %). 행 합계가 0이면 `empty`."""
    out = []
    for label, parts in rows:
        vals = [_pos(v) for _, v, _ in parts]
        total = sum(vals)
        segs, x = [], 0.0
        for (part, v, color), pv in zip(parts, vals, strict=True):
            if pv <= 0:
                continue
            w = pv / total * 100
            segs.append({"part": part, "value": v, "color": color, "x": _r(x), "w": _r(w), "pct": round(w, 1)})
            x += w
        out.append({"label": label, "total": total, "empty": not total, "parts": segs})
    return out
