# P7 심사 콘솔 UI 고도화 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 심사 콘솔을 Azure Portal 풍 회사용 화면(왼쪽 사이드바, KPI 카드, SVG 차트, 단계 스테퍼)으로 바꾸고, Azure 없이 합성 데이터로 띄우는 로컬 미리보기(`python -m consolepreview`)를 더한다.

**Architecture:** 차트 기하(`src/console/charts.py`)와 화면 집계(`src/console/dashboard.py`)는 순수 함수로 dict만 돌려주고, Jinja 매크로(`templates/_charts.html`)가 그 값을 SVG 속성에 넣는다. 라우트는 기존 컨텍스트에 `ui=<화면 요약>`만 더한다. CSP(`style-src 'self'`)를 지키려고 `style=` 속성 없이 CSS 클래스(`fill-*`, `stroke-*`, `bg-*`)와 SVG 기하 속성만 쓴다. 미리보기는 `src/consolepreview/`의 메모리 가짜 저장소로 `create_app(ConsoleDeps(...))`을 띄운다.

**Tech Stack:** Python 3.12, FastAPI + Jinja2(서버 렌더링 SVG, JS 차트 라이브러리 없음), CSS, pytest + Starlette TestClient.

**Spec:** `docs/superpowers/specs/2026-11-03-p7-console-ui-design.md`

## Global Constraints

- **데모 대본 문구 유지**(Spec 1.3 원칙 1): 내비게이션 `신청`, 버튼 `새 신청`·`결정 보내기`·`사후 13주 재생`·`지금 만료(데모)`, 섹션 제목 `타임라인`·`정책 판정`·`에이전트 평가`·`주장과 근거`·`제안 구조`·`개선 조건(반사실)`·`근거 거래`·`심사역 결정`·`주간 흐름과 탐지기`·`경보 <N>주차`, 상태 이름(`STATUS_LABELS`). `주간 흐름과 탐지기` 표는 접지 않는다.
- **CSP 불변**: `src/console/app.py`의 `CSP` 상수를 바꾸지 않는다. 템플릿에 `style=` 속성·`<style>`·`src` 없는 `<script>`를 쓰지 않는다(Task 4 테스트가 고정).
- **기존 테스트 유지**: 기존 `tests/console/*` 문자열·정규식이 그대로 통과해야 한다. 특히 `<td>한식</td>.*?<td>표본 부족</td>`는 맞아야 하고 `<td>카페</td>(?:(?!</tr>).)*표본 부족`은 맞지 않아야 한다(`tests/console/test_ops_page.py`). 깨지면 테스트가 아니라 템플릿을 고친다.
- **기존 CSS 클래스 이름 유지**: `.status`, `.s-*`, `.m-*`, `.c-*`, `.g-*`, `.sev-*`, `.chip`, `.ops-alert`, `.demo-banner`, `.error`, `.ok`, `.pii`, `.note`, `.quote`, `.notice`, `.timeline`, `.facts`.
- **코드는 이 계획에 있는 그대로** 붙여 넣는다. 아래 파일 내용은 별도 작업 공간에서 실제 저장소 코드에 대고 전체 콘솔 테스트(120건)와 새 테스트(56건)를 모두 통과시킨 결과다. 다르게 고치고 싶으면 먼저 리뷰어와 합의한다.
- 테스트 명령: `$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python -m pytest -q -p no:cacheprovider` (기준선 1016 passed, 105 skipped). 콘솔만: `... -m pytest -q -p no:cacheprovider tests\console`.
- 파일 인코딩 UTF-8(BOM 없음), 줄 끝 LF.
- 커밋: 메시지를 임시 파일로 써서 `git commit -F <파일>`. 트레일러 두 줄 필수:
  `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>` / `Copilot-Session: 05f1ca1b-6073-417d-bfb1-6b546f82cb58`
- 브랜치: `p7-console-ui`(master `5a29d38` 이후에서 분기). Azure 배포는 이번 범위 밖.

## Review Focus

1. 템플릿 어디에도 `style=`가 들어가지 않는지(운영 CSP에서 차트가 깨진다) → Task 4 `test_templates_have_no_inline_style_or_script`.
2. 차트가 저장되지 않은 판단을 만들지 않는지: 정책 마커 막대는 중립색만, 모니터링 빨간 막대는 `triggers`가 있는 주만(Spec 1.3 원칙 3) → Task 2 `test_detail_summary_*`, `test_loan_summary_*`.
3. `journey_steps`가 철회·실패·미결 종료를 "마지막 도달 단계 다음"에 `failed`로 놓고, 타임라인에 없는 상태 코드를 무시하는지 → Task 2.
4. 홈 KPI가 전체 통계처럼 보이지 않는지("최근 50건 기준") → Task 5.
5. 기존 `/applications/rows` htmx 갱신, 결정·재생 폼, 권한 조건(`user.can_decide`)이 그대로인지 → Task 5~7의 기존 테스트.

## File Structure

| 파일 | 책임 |
|---|---|
| `src/console/charts.py` (신규) | 차트 기하 순수 함수: `donut`, `hbars`, `columns`, `line`, `gauge`, `marker_bar`, `stacked`, 보조 `is_num` |
| `src/console/statuses.py` (신규) | `STATUS_LABELS`·`TERMINAL`(app.py에서 옮김, 대시보드와 공유) |
| `src/console/dashboard.py` (신규) | 화면 요약 순수 함수: `home_summary`, `journey_steps`, `detail_summary`, `loans_summary`, `loan_summary`, `alert_steps`, `ops_summary` |
| `src/console/app.py` (수정) | 상수 이동, 라우트 5곳에 `ui=` 컨텍스트 |
| `src/console/templates/_charts.html` (신규) | 매크로: `kpi`, `legend`, `empty`, `donut`, `hbars`, `columns`, `line_overlay`, `gauge`, `marker_bar`, `stacked`, `stepper`, `progress` |
| `src/console/templates/base.html` (재작성) | 사이드바 셸, 데모 배너, `nav` 강조 |
| `src/console/templates/{list,_rows,detail,loans,loan,ops}.html` (재작성) | 네 화면 |
| `src/console/templates/{new,sent,pending,loan_sent,grievances,grievance,borrower_index,borrower}.html` (한 줄) | `{% set nav = "…" %}` |
| `src/console/static/console.css` (재작성) | Fluent 풍 디자인 토큰·카드·차트·스테퍼·반응형 |
| `src/consolepreview/{__init__,__main__,fixtures}.py` (신규) | 로컬 미리보기 |
| `tests/console/test_charts.py`, `test_dashboard.py`, `test_preview.py`, `test_ui.py` (신규) | 단위·렌더링 테스트 |
| `docs/demo/demo-script.md`, `docs/runbook/setup.md` (수정) | 화면 설명, 미리보기 명령 |

---

### Task 0: 브랜치와 Spec 보정

**Files:**
- Modify: `docs/superpowers/specs/2026-11-03-p7-console-ui-design.md`

- [ ] **Step 1: 브랜치 생성**

```powershell
git switch -c p7-console-ui
```

- [ ] **Step 2: Spec 보정 3곳**

시제품 검증에서 정해진 사항을 Spec에 반영한다.

1. 3.4절 첫 항목을 다음으로 바꾼다:
   `- 라우트에서 dashboard 함수로 화면 요약을 계산해 컨텍스트 `ui`로 넘깁니다(홈 `home_summary(rows)`, 상세 `detail_summary(view)`, 모니터링 목록 `loans_summary(rows)`, 모니터링 상세 `loan_summary(view)`, 운영 `ops_summary(s, STEP_LABELS)`). 홈·모니터링 목록은 `limit=LIST_LIMIT`도 넘깁니다.`
2. 4.3절을 다음으로 바꾼다:
   `- KPI 4장: 모니터링 대출 / 진행 중 재생 / 13주 완주(보조 줄 "미실행 N건") / 누적 경보. 상태 배지 문구(`재생 중`·`재생 완료`·`재생 전`)와 겹치지 않게 KPI 라벨을 달리해, 표의 배지를 확인하는 기존 테스트가 KPI 라벨로 우연히 통과하지 않게 합니다. 아래 표는 열 그대로, 모니터링 상태는 배지.`
3. 7절 둘째 항목을 다음으로 바꾼다:
   `- `docs/runbook/setup.md`의 "로컬 서비스 실행"에 미리보기 명령 한 줄. `docs/demo/demo-script.md`의 사이드바·상단 버튼 위치 표현과 `STATUS_LABELS` 위치(`src/console/statuses.py`)를 고칩니다.`

- [ ] **Step 3: 커밋**

```powershell
git add docs/superpowers/specs/2026-11-03-p7-console-ui-design.md
git commit -F <메시지 파일>   # "docs(p7): spec 보정 — ui 컨텍스트, 모니터링 KPI 라벨, 문서 위치"
```

---

### Task 1: 차트 기하 `charts.py`

**Files:**
- Create: `src/console/charts.py`
- Test: `tests/console/test_charts.py`

규칙(Spec 3.1): SVG 문자열을 만들지 않고 dict/list만 돌려준다. 좌표는 소수 둘째 자리 반올림. 음수·0은 도넛에서 건너뛰고, 막대에서는 0 높이로 그리되 툴팁에 원래 값을 남긴다. `line`은 `None`에서 선을 끊는다. `marker_bar`는 관측값·기준 중 하나라도 `None`·음수·숫자 아님이면 `None`.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/console/test_charts.py`

```python
import math

from console import charts


def test_donut_skips_zero_negative_and_missing_and_chains_offsets():
    d = charts.donut([("a", 3, "blue"), ("b", 1, "red"), ("c", 0, "green"), ("d", -2, "amber"), ("e", None, "grey")])
    assert d["total"] == 4 and d["circumference"] == 289.03
    assert [(a["label"], a["dash"], a["gap"], a["offset"], a["pct"]) for a in d["arcs"]] == [
        ("a", 216.77, 72.26, 0.0, 75.0),
        ("b", 72.26, 216.77, -216.77, 25.0),
    ]


def test_donut_all_zero_has_no_arcs():
    assert charts.donut([("a", 0, "blue"), ("b", None, "red")])["arcs"] == []
    assert charts.donut([])["total"] == 0


def test_hbars_scale_to_max_and_keep_original_value():
    h = charts.hbars([("x", 10, "blue"), ("y", 5, "red"), ("z", -1, "grey")])
    assert h["height"] == 84
    assert [(b["w"], b["y"], b["ty"], b["vx"], b["value"]) for b in h["bars"]] == [
        (208.0, 6.0, 18.0, 318.0, 10), (104.0, 34.0, 46.0, 214.0, 5), (0.0, 62.0, 74.0, 110.0, -1)]


def test_hbars_zero_max_and_empty():
    assert charts.hbars([("a", 0, "blue")])["bars"][0]["w"] == 0.0
    assert charts.hbars([]) == {"width": 360, "height": 0, "bars": []}


def test_columns_stack_parts_bottom_up_and_include_refs_in_axis():
    c = charts.columns(
        [("1월", [[("rev", 100, "blue", "t1")]]),
         ("2월", [[("rev", 50, "blue", "t2")],
                  [("op", 30, "amber", "t3"), ("ds", -5, "red", "t4"), ("dr", 20, "purple", "t5")]])],
        refs=[("기준", 120, "neutral")],
    )
    assert (c["x0"], c["x1"], c["base"], c["label_y"], c["vmax"]) == (56, 592, 214, 230, 120.0)
    assert [t["text"] for t in c["ticks"]] == ["0", "60", "120"]
    g1, g2 = c["groups"]
    assert (g1["cx"], g1["bars"][0]["x"], g1["bars"][0]["w"]) == (190.0, 105.58, 168.84)
    assert (g1["bars"][0]["parts"][0]["y"], g1["bars"][0]["parts"][0]["h"]) == (44.0, 170.0)
    expense = g2["bars"][1]
    assert [(p["part"], p["y"], p["h"]) for p in expense["parts"]] == [
        ("op", 163.0, 51.0), ("ds", 163.0, 0.0), ("dr", 129.0, 34.0)]
    assert expense["parts"][1]["value"] == -5 and expense["parts"][1]["tip"] == "t4"
    assert expense["top"] == 129.0
    assert c["refs"] == [{"label": "기준", "value": 120, "color": "neutral", "y": 10.0, "text": "120"}]


def test_columns_fixed_vmax_clamps_height_and_empty_has_unit_axis():
    c = charts.columns([("a", [[("v", 150, "blue", "")]])], vmax=100)
    assert c["groups"][0]["bars"][0]["parts"][0]["h"] == 204.0 and c["vmax"] == 100.0
    empty = charts.columns([])
    assert empty["groups"] == [] and empty["vmax"] == 1.0 and empty["refs"] == []


def test_columns_skip_non_numeric_refs_and_use_tick_format():
    c = charts.columns([("a", [[("v", 4, "blue", "")]])], refs=[("x", None, "red")], tick_fmt=lambda v: f"{v:.0f}%")
    assert c["refs"] == [] and c["ticks"][-1]["text"] == "4%"


def test_line_breaks_at_missing_values():
    ln = charts.line([10, None, 30, 20])
    assert ln["segments"] == ["M 123.0 146.0", "M 391.0 10.0 L 525.0 78.0"]
    assert [p["i"] for p in ln["points"]] == [0, 2, 3] and ln["x"] == 592 and ln["vmax"] == 30.0
    assert charts.line([])["segments"] == [] and charts.line([None])["points"] == []


def test_gauge_clamps_and_handles_missing():
    assert charts.gauge(15, 20)["pct"] == 75.0
    assert charts.gauge(-1, 20)["frac"] == 0.0 and charts.gauge(50, 20)["frac"] == 1.0
    assert charts.gauge(None, 20)["frac"] is None and charts.gauge(5, 0)["frac"] is None
    assert charts.gauge(5, None)["d"] == charts.GAUGE_ARC


def test_marker_bar_needs_two_non_negative_numbers():
    assert charts.marker_bar(1.8, 1.25) == {"fill": 80.0, "mark": 55.56}
    assert charts.marker_bar(0, 0) == {"fill": 0.0, "mark": 0.0}
    assert charts.marker_bar(None, 1) is None and charts.marker_bar(-1, 1) is None
    assert charts.marker_bar(True, 1) is None and charts.marker_bar(1, math.inf) is None


def test_stacked_percent_parts_and_empty_rows():
    rows = charts.stacked([("a", [("ok", 3, "green"), ("degraded", 1, "amber"), ("failed", 0, "red")]),
                           ("b", [("ok", 0, "green")])])
    assert [(p["part"], p["x"], p["w"]) for p in rows[0]["parts"]] == [("ok", 0.0, 75.0), ("degraded", 75.0, 25.0)]
    assert rows[1]["empty"] and rows[1]["parts"] == []


def test_rounding_never_returns_negative_zero():
    assert str(charts._r(-0.001)) == "0.0"
    assert charts.is_num(1) and not charts.is_num(True) and not charts.is_num(math.nan) and not charts.is_num("1")
```

- [ ] **Step 2: 실패 확인**

Run: `$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python -m pytest -q -p no:cacheprovider tests\console\test_charts.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'console.charts'`

- [ ] **Step 3: 구현** — `src/console/charts.py`

```python
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
```

- [ ] **Step 4: 통과 확인**

Run: 같은 명령. Expected: 모두 PASS.

- [ ] **Step 5: 커밋** — `feat(console): P7 차트 기하 순수 함수(charts.py)`

---

### Task 2: 상태 상수 분리와 화면 집계 `dashboard.py`

**Files:**
- Create: `src/console/statuses.py`, `src/console/dashboard.py`
- Modify: `src/console/app.py`(상수 정의 → import)
- Test: `tests/console/test_dashboard.py`

`dashboard.py`는 `STATUS_LABELS`·`TERMINAL`이 필요하고 `app.py`를 import하면 순환이 생기므로 두 상수를 `statuses.py`로 옮긴다. `console.app.STATUS_LABELS`는 import로 계속 노출된다(`tests/console/test_ops_page.py`가 씀).

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/console/test_dashboard.py`

```python
from console import dashboard
from console.app import STATUS_LABELS
from opsmetrics.labels import STEP_LABELS

T = "2026-10-31T09:{:02d}:00+09:00"


def tl(*pairs):
    return [{"status": s, "at": T.format(m) if m is not None else None} for s, m in pairs]


def states(steps):
    return [s["state"] for s in steps]


def test_home_summary_counts_groups_and_recommendations():
    rows = [{"status": s, "recommendation": r} for s, r in [
        ("awaiting_officer", "approve"), ("escalated", "refer"), ("approved", None), ("conditionally_approved", "weird"),
        ("declined", "decline"), ("withdrawn", "conditional"), ("received", None), ("assessed", "approve")]]
    s = dashboard.home_summary(rows)
    assert (s["total"], s["awaiting"], s["escalated"], s["in_progress"], s["approved"], s["declined"]) == (8, 2, 1, 2, 2, 1)
    assert s["approval_rate"] == 2 / 3
    assert [n for _, _, n in s["groups"]] == [2, 2, 1, 1, 2]
    assert s["recommendations"] == [("승인", 2, "green"), ("조건부", 1, "blue"), ("심사역 회부", 1, "purple"),
                                    ("거절", 1, "red"), ("권고 없음", 3, "neutral")]
    assert s["donut"]["total"] == 8 and len(s["rec_bars"]["bars"]) == 5


def test_home_summary_without_decided_rows_has_no_approval_rate():
    s = dashboard.home_summary([])
    assert s["approval_rate"] is None and s["donut"]["arcs"] == [] and s["total"] == 0
    assert dashboard.home_summary([{"status": "received"}])["approval_rate"] is None


def test_journey_steps_awaiting_officer(make_doc):
    steps = dashboard.journey_steps(make_doc())
    assert [s["label"] for s in steps] == ["접수", "동의 완료", "데이터 수집", "심사 지표 산출", "에이전트 평가", "심사역 대기", "결정"]
    assert states(steps) == ["done"] * 5 + ["current", "todo"]
    assert (steps[0]["time"], steps[1]["time"], steps[5]["time"]) == ("09:00:00", "", "09:02:00")
    assert (steps[5]["elapsed_s"], steps[5]["elapsed"]) == (120, "+2분 0초")


def test_journey_steps_escalated_is_warn_and_keeps_first_arrival(make_doc):
    doc = make_doc("escalated", timeline=tl(("received", 0), ("awaiting_officer", 2), ("escalated", 30)))
    steps = dashboard.journey_steps(doc)
    assert steps[5]["state"] == "warn" and steps[5]["note"] == "SLA 초과" and steps[5]["elapsed_s"] == 120
    assert steps[6]["state"] == "todo"


def test_journey_steps_data_incomplete_warns_data_step(make_doc):
    doc = make_doc("data_incomplete", timeline=tl(("received", 0), ("consent_granted", 0), ("data_incomplete", 1)))
    steps = dashboard.journey_steps(doc)
    assert states(steps) == ["done", "done", "warn", "todo", "todo", "todo", "todo"]
    assert steps[2]["note"] == "데이터 부족" and steps[2]["elapsed_s"] == 60


def test_journey_steps_withdrawn_fails_next_step_with_status_label(make_doc):
    doc = make_doc("withdrawn", timeline=tl(("received", 0), ("consent_granted", 0), ("withdrawn", 3)))
    steps = dashboard.journey_steps(doc)
    assert states(steps) == ["done", "done", "failed", "todo", "todo", "todo", "todo"]
    assert steps[2]["label"] == "철회 종료" and steps[2]["time"] == "09:03:00"


def test_journey_steps_failed_without_timeline_entry(make_doc):
    steps = dashboard.journey_steps(make_doc("failed", timeline=tl(("received", 0))))
    assert states(steps)[:3] == ["done", "failed", "todo"] and steps[1]["label"] == "실패" and steps[1]["at"] is None


def test_journey_steps_closed_without_decision_fails_decision_step(make_doc):
    doc = make_doc("closed_no_decision", timeline=tl(("received", 0), ("awaiting_officer", 2), ("closed_no_decision", 9)))
    steps = dashboard.journey_steps(doc)
    assert states(steps) == ["done"] * 6 + ["failed"] and steps[6]["label"] == "미결 종료"


def test_journey_steps_declined_and_escalated_then_approved(make_doc):
    declined = dashboard.journey_steps(make_doc("declined", timeline=tl(("received", 0), ("awaiting_officer", 2),
                                                                        ("declined", 5))))
    assert states(declined) == ["done"] * 7 and declined[6]["label"] == "거절" and declined[6]["elapsed_s"] == 180
    approved = dashboard.journey_steps(make_doc("approved", timeline=tl(
        ("received", 0), ("awaiting_officer", 2), ("escalated", 30), ("approved", 31))))
    assert approved[5]["state"] == "warn" and approved[6]["state"] == "done" and approved[6]["label"] == "승인"


def test_journey_steps_ignore_unknown_status_and_mixed_timezones(make_doc):
    doc = make_doc(timeline=[{"status": "mystery", "at": T.format(0)}, {"status": "received", "at": "2026-10-31T09:00:00"},
                             {"status": "awaiting_officer", "at": T.format(2)}])
    steps = dashboard.journey_steps(doc)
    assert states(steps) == ["done"] * 5 + ["current", "todo"] and steps[5]["elapsed_s"] is None


def test_detail_summary_monthly_markers_counterfactuals_and_review(make_doc):
    doc = make_doc()
    assessment = {**doc["assessment"], "counterfactuals": [
        {"metric_id": "M:dscr", "current": 0.9, "target": 1.25, "explanation": "x"},
        {"metric_id": "M:note", "current": "낮음", "target": "보통", "explanation": "y"}]}
    policy = {**doc["policy"], "reason_codes": doc["policy"]["reason_codes"] + [
        {"code": "R109", "message": "범주형", "observed": None, "threshold": "A"}]}
    s = dashboard.detail_summary({"doc": doc, "policy": policy, "assessment": assessment})
    assert [s_["state"] for s_ in s["steps"]][5] == "current"
    group = s["monthly"]["groups"][0]
    assert group["label"] == "26-09" and [len(b["parts"]) for b in group["bars"]] == [1, 3]
    assert group["bars"][0]["parts"][0]["tip"] == "2026-09 매출 40,000,000원"
    assert [m["code"] for m in s["markers"]] == ["R101"] and s["markers"][0]["bar"] == {"fill": 80.0, "mark": 55.56}
    assert s["counterfactuals"][0]["bar"]["bars"][1]["w"] == 204.0 and s["counterfactuals"][1]["bar"] is None
    assert [(r["label"], r["state"], r["note"]) for r in s["review"]] == [
        ("분석 에이전트", "done", "gpt-5.4"), ("검토 에이전트", "done", "1회")]


def test_detail_summary_without_underwriting_or_assessment(make_doc):
    s = dashboard.detail_summary({"doc": make_doc("received", underwriting=None, assessment=None), "policy": None,
                                  "assessment": None})
    assert s["monthly"] is None and s["markers"] == [] and s["counterfactuals"] == [] and s["review"] is None


def test_loans_summary_counts():
    rows = [{"monitoring_status": "completed", "alerts": 2}, {"monitoring_status": "replaying", "alerts": 0},
            {"monitoring_status": None, "alerts": None}, {"monitoring_status": "failed", "alerts": True}]
    assert dashboard.loans_summary(rows) == {"total": 4, "replaying": 1, "completed": 1, "not_started": 1, "alerts": 2}


def test_alert_steps_branches():
    full = dashboard.alert_steps({"week": 1, "severity": "high", "collections": {"decision": "routed"},
                                  "restructure": {"status": "pending_officer"}})
    assert [(s["label"], s["state"], s["note"]) for s in full] == [
        ("탐지", "done", "1주차"), ("조기경보 조사", "done", "높음"), ("추심 에이전트", "done", "routed"),
        ("재조정 결정", "current", "심사역 대기")]
    waiting = dashboard.alert_steps({"week": 4, "severity": "medium", "collections": None, "restructure": None})
    assert [s["state"] for s in waiting] == ["done", "done", "current"]
    done = dashboard.alert_steps({"week": 4, "severity": "low", "collections": {"decision": "x"},
                                  "restructure": {"status": "declined"}})
    assert (done[-1]["state"], done[-1]["note"]) == ("done", "거절")


def week(n, buffer, triggers=(), new=()):
    row = {"start": f"2026-09-0{n}", "end": f"2026-09-0{n + 6}", "revenue": 8_000_000 + n, "buffer_days": buffer}
    return {"week": n, "row": row, "triggers": list(triggers), "new_triggers": list(new)}


def test_loan_summary_progress_severity_buffer_and_chart():
    view = {
        "loan": {"baseline": {"avg_weekly_revenue": 9_000_000, "cash_buffer_days": 30.0}},
        "monitoring": {"processed_week": 3},
        "weeks": [week(1, 20.0), week(2, 12.5, ["new_debt"], ["new_debt"]), week(3, None, ["new_debt"])],
        "alerts": [{"alert_id": "ew-1", "week": 2, "severity": "high"}, {"alert_id": "ew-2", "severity": "medium"}],
    }
    s = dashboard.loan_summary(view)
    assert (s["processed"], s["progress_pct"], s["alerts"]) == (3, 23.1, 2)
    assert s["severity"] == {"high": 1, "medium": 1, "low": 0}
    assert (s["min_buffer"], s["min_buffer_week"], s["baseline_buffer"]) == (12.5, 2, 30.0)
    colors = [g["bars"][0]["parts"][0]["color"] for g in s["chart"]["groups"]]
    assert colors == ["blue", "red", "red"] and s["chart"]["refs"][0]["label"] == "기준 주매출"
    assert "신규 차입" in s["chart"]["groups"][1]["bars"][0]["parts"][0]["tip"]
    top = s["chart"]["groups"][1]["bars"][0]["top"]
    assert [(m["week"], m["y"]) for m in s["markers"]] == [(2, round(top - 6, 2))]
    assert len(s["line"]["segments"]) == 1 and len(s["line"]["points"]) == 2
    assert s["line"]["x"] == s["chart"]["x1"] and s["line"]["points"][0]["x"] == s["chart"]["groups"][0]["cx"]
    assert set(s["alert_steps"]) == {"ew-1", "ew-2"}


def test_loan_summary_before_replay():
    s = dashboard.loan_summary({"loan": {"baseline": {}}, "monitoring": {}, "weeks": [], "alerts": []})
    assert s["chart"] is None and s["line"] is None and s["min_buffer"] is None and s["progress_pct"] == 0.0


def test_ops_summary_gauges_steps_and_fairness():
    seg = {"assessed": 5, "insufficient": False}
    s = {
        "latency": {"p95_min": 8.0, "target_min": 10.0, "met": True},
        "agent_success": {"rate": 0.95, "met": False, "by_step": {"underwrite": {"ok": 3, "degraded": 1, "failed": 0},
                                                                  "assess": {"ok": 0, "degraded": 0, "failed": 0}}},
        "fairness": {"axes": {"industry": {"values": {
            "카페": {**seg, "approve_rate": 1.0}, "한식": {**seg, "approve_rate": 0.0},
            "편의점": {"assessed": 2, "insufficient": True, "approve_rate": 0.5},
            "기타": {"assessed": 0, "insufficient": True, "approve_rate": None}}}}},
    }
    u = dashboard.ops_summary(s, STEP_LABELS)
    assert (u["latency"]["pct"], u["latency_tone"], u["success"]["pct"], u["success_tone"]) == (40.0, "green", 95.0, "red")
    assert [r["label"] for r in u["steps"]] == ["신용 평가", "심사 지표 산출"] and u["steps"][0]["empty"]
    chart = u["fairness"]["industry"]
    assert [r["text"] for r in chart["refs"]] == ["100%", "0%"] and chart["vmax"] == 100.0
    parts = [g["bars"][0]["parts"][0] for g in chart["groups"]]
    assert [p["color"] for p in parts] == ["blue", "blue", "grey", "grey"]
    assert parts[2]["tip"].endswith("표본 부족") and parts[3]["tip"] == "기타 추천 없음"


def test_ops_summary_without_samples_is_neutral():
    u = dashboard.ops_summary({"latency": {"p95_min": None, "target_min": 10.0, "met": False},
                               "agent_success": {"rate": None, "met": False, "by_step": {}},
                               "fairness": {"axes": {}}}, STEP_LABELS)
    assert (u["latency_tone"], u["success_tone"], u["steps"], u["fairness"]) == ("neutral", "neutral", [], {})


def test_formatters():
    assert dashboard.elapsed_text(None) == "" and dashboard.elapsed_text(5) == "+5초"
    assert dashboard.elapsed_text(3725) == "+1시간 2분"
    assert dashboard.man(40_000_000) == "4,000만" and dashboard.man(0) == "0"
    assert dashboard.krw_text(1_000) == "1,000원" and dashboard.krw_text(None) == "-"
    assert dashboard.clock("bad") == "" and dashboard.clock(None) == ""


def test_status_labels_are_shared_with_app():
    from console import statuses
    assert STATUS_LABELS is statuses.STATUS_LABELS
```

- [ ] **Step 2: 실패 확인**

Run: `$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python -m pytest -q -p no:cacheprovider tests\console\test_dashboard.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'console.dashboard'`

- [ ] **Step 3: `src/console/statuses.py` 작성**

```python
"""신청 상태 라벨과 종결 상태. app(라우트)과 dashboard(집계)가 함께 쓴다."""

TERMINAL = frozenset({"approved", "conditionally_approved", "declined", "closed_no_decision", "withdrawn", "failed"})
STATUS_LABELS = {
    "received": "접수",
    "consent_granted": "동의 완료",
    "data_assembled": "데이터 수집",
    "data_incomplete": "데이터 부족",
    "underwritten": "심사 지표 산출",
    "assessed": "에이전트 평가",
    "awaiting_officer": "심사역 대기",
    "escalated": "SLA 초과",
    "approved": "승인",
    "conditionally_approved": "조건부 승인",
    "declined": "거절",
    "closed_no_decision": "미결 종료",
    "withdrawn": "철회 종료",
    "failed": "실패",
}
```

- [ ] **Step 4: `src/console/app.py`에서 두 상수를 import로 바꾼다**

`CSP = (...)` 바로 아래의 `TERMINAL = frozenset({...})`와 `STATUS_LABELS = {...}` 정의(약 68~84행)를 지우고, import 블록의 `from console.privacy import ...` 다음 줄에 추가한다:

```python
from console.statuses import STATUS_LABELS, TERMINAL
```

- [ ] **Step 5: `src/console/dashboard.py` 작성**

```python
"""화면 집계(순수 함수, P7): 홈 KPI·분포, 신청 7단계 진행, 사후 모니터링 요약, 운영 지표 게이지.

저장된 값만 쓴다. 홈 KPI는 목록 쿼리 결과(최근 LIST_LIMIT건)로만 계산한다.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from console import charts
from console.monitoring import SEVERITY_LABELS, TRIGGER_LABELS
from console.statuses import STATUS_LABELS, TERMINAL

AWAITING = frozenset({"awaiting_officer", "escalated"})
APPROVED = frozenset({"approved", "conditionally_approved"})
DECIDED = APPROVED | {"declined"}
CLOSED = frozenset({"withdrawn", "closed_no_decision", "failed"})
GROUPS = (
    ("approved", "승인·조건부", "green"),
    ("awaiting", "심사역 대기", "amber"),
    ("declined", "거절", "red"),
    ("closed", "기타 종료", "neutral"),
    ("progress", "진행 중", "blue"),
)
RECOMMENDATIONS = (
    ("approve", "승인", "green"),
    ("conditional", "조건부", "blue"),
    ("refer", "심사역 회부", "purple"),
    ("decline", "거절", "red"),
    ("none", "권고 없음", "neutral"),
)
STEPS = (
    ("received", "접수"),
    ("consent", "동의 완료"),
    ("data", "데이터 수집"),
    ("underwrite", "심사 지표 산출"),
    ("assess", "에이전트 평가"),
    ("officer", "심사역 대기"),
    ("decision", "결정"),
)
STEP_OF = {
    "received": 0,
    "consent_granted": 1,
    "data_assembled": 2,
    "data_incomplete": 2,
    "underwritten": 3,
    "assessed": 4,
    "awaiting_officer": 5,
    "escalated": 5,
    "approved": 6,
    "conditionally_approved": 6,
    "declined": 6,
}
STEP_NOTES = {"data_incomplete": "데이터 부족", "escalated": "SLA 초과"}
EXPENSES = (("operating_out", "운영비", "amber"), ("debt_service", "상환", "red"), ("owner_draw", "대표자 인출", "purple"))
RESTRUCTURE_NOTES = {"pending_officer": "심사역 대기", "approved": "승인", "declined": "거절"}
REPLAY_WEEKS = 13
LOAN_CHART = {"width": 960, "height": 300}  # 13주 막대와 버퍼 선이 같은 칸을 쓰도록 크기를 맞춘다.


def _dt(iso: Any) -> datetime | None:
    if not isinstance(iso, str):
        return None
    try:
        return datetime.fromisoformat(iso)
    except ValueError:
        return None


def clock(iso: Any) -> str:
    d = _dt(iso)
    return d.strftime("%H:%M:%S") if d else ""


def elapsed_text(seconds: int | None) -> str:
    if seconds is None:
        return ""
    if seconds < 60:
        return f"+{seconds}초"
    if seconds < 3600:
        return f"+{seconds // 60}분 {seconds % 60}초"
    return f"+{seconds // 3600}시간 {seconds % 3600 // 60}분"


def krw_text(v: Any) -> str:
    return f"{v:,.0f}원" if charts.is_num(v) else "-"


def man(v: float) -> str:
    """금액 축 눈금(만 원 단위)."""
    return f"{v / 10_000:,.0f}만" if abs(v) >= 10_000 else f"{v:,.0f}"


def _int(v: Any) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def _step(key: str, label: str, state: str, note: str = "") -> dict[str, Any]:
    return {"key": key, "label": label, "state": state, "at": None, "time": "", "elapsed": "", "elapsed_s": None,
            "note": note}


def _group(status: Any) -> str:
    if status in APPROVED:
        return "approved"
    if status in AWAITING:
        return "awaiting"
    if status == "declined":
        return "declined"
    if status in TERMINAL:
        return "closed"
    return "progress"


def home_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = dict.fromkeys((k for k, _, _ in GROUPS), 0)
    recs = dict.fromkeys((k for k, _, _ in RECOMMENDATIONS), 0)
    escalated = 0
    for r in rows:
        counts[_group(r.get("status"))] += 1
        escalated += r.get("status") == "escalated"
        rec = r.get("recommendation")
        recs[rec if rec in recs else "none"] += 1
    decided = counts["approved"] + counts["declined"]
    groups = [(label, color, counts[k]) for k, label, color in GROUPS]
    recommendations = [(label, recs[k], color) for k, label, color in RECOMMENDATIONS]
    return {
        "total": len(rows),
        "awaiting": counts["awaiting"],
        "escalated": escalated,
        "in_progress": counts["progress"],
        "approved": counts["approved"],
        "declined": counts["declined"],
        "approval_rate": counts["approved"] / decided if decided else None,
        "groups": groups,
        "donut": charts.donut([(label, n, color) for label, color, n in groups]),
        "recommendations": recommendations,
        "rec_bars": charts.hbars(recommendations),
    }


def journey_steps(doc: Mapping[str, Any]) -> list[dict[str, Any]]:
    """7단계 진행. 단계마다 처음 도달한 시각과 직전 시각이 있는 단계로부터 걸린 초."""
    status = doc.get("status")
    timeline = [t for t in doc.get("timeline") or [] if isinstance(t, Mapping)]
    if status and all(t.get("status") != status for t in timeline):
        timeline.append({"status": status, "at": None})
    first_at: dict[int, Any] = {}
    notes: dict[int, str] = {}
    last, closed_at = -1, None
    for t in timeline:
        s = t.get("status")
        if s in CLOSED:
            closed_at = closed_at or t.get("at")
            continue
        i = STEP_OF.get(s)
        if i is None:
            continue
        first_at.setdefault(i, t.get("at"))
        last = max(last, i)
        if s in STEP_NOTES:
            notes[i] = STEP_NOTES[s]
    fail_at = min(last + 1, len(STEPS) - 1) if status in CLOSED else None
    finished = status in DECIDED or fail_at is not None
    steps, prev = [], None
    for i, (key, label) in enumerate(STEPS):
        at = first_at.get(i)
        if fail_at is not None and i == fail_at:
            state, label, at = "failed", STATUS_LABELS.get(status, status), closed_at
        elif fail_at is not None and i > fail_at:
            state = "todo"
        elif i < last or (i == last and finished):
            state = "done"
        elif i == last:
            state = "current"
        else:
            state = "todo"
        if i == len(STEPS) - 1 and status in DECIDED:
            label = STATUS_LABELS[status]
        note = notes.get(i, "")
        if note and state in ("done", "current"):
            state = "warn"
        elapsed = None
        a, b = _dt(at), _dt(prev)
        if a and b:
            try:
                elapsed = int((a - b).total_seconds())
            except TypeError:
                elapsed = None
            if elapsed is not None and elapsed < 0:
                elapsed = None
        if a:
            prev = at
        steps.append({"key": key, "label": label, "state": state, "at": at, "time": clock(at),
                      "elapsed": elapsed_text(elapsed), "elapsed_s": elapsed, "note": note})
    return steps


def detail_summary(view: Mapping[str, Any]) -> dict[str, Any]:
    doc = view["doc"]
    monthly = [m for m in (doc.get("underwriting") or {}).get("monthly") or [] if isinstance(m, Mapping)]
    groups = []
    for m in monthly:
        month = str(m.get("month") or "")
        revenue = [("revenue", m.get("revenue"), "blue", f"{month} 매출 {krw_text(m.get('revenue'))}")]
        expenses = [(k, m.get(k), color, f"{month} {label} {krw_text(m.get(k))}") for k, label, color in EXPENSES]
        groups.append((month[2:] if len(month) == 7 else month, [revenue, expenses]))
    markers = []
    for r in (view.get("policy") or {}).get("reason_codes") or []:
        bar = charts.marker_bar(r.get("observed"), r.get("threshold"))
        if bar:
            markers.append({"code": r.get("code"), "message": r.get("message"), "observed": r.get("observed"),
                            "threshold": r.get("threshold"), "bar": bar})
    assessment = view.get("assessment") or {}
    counterfactuals = []
    for cf in assessment.get("counterfactuals") or []:
        cur, tgt = cf.get("current"), cf.get("target")
        comparable = charts.is_num(cur) and charts.is_num(tgt) and cur >= 0 and tgt >= 0
        bar = charts.hbars([("현재", cur, "neutral"), ("목표", tgt, "blue")], width=300, label_w=48)
        counterfactuals.append({**cf, "bar": bar if comparable else None})
    review = None
    if assessment.get("review"):
        rv = assessment["review"]
        rounds = rv.get("rounds")
        review = [
            _step("analyst", "분석 에이전트", "done", (assessment.get("models") or {}).get("analyst") or ""),
            _step("reviewer", "검토 에이전트", "done" if rv.get("passed") else "failed",
                  f"{rounds}회" if rounds is not None else ""),
        ]
    return {
        "steps": journey_steps(doc),
        "monthly": charts.columns(groups, tick_fmt=man) if groups else None,
        "monthly_legend": [("매출", "blue")] + [(label, color) for _, label, color in EXPENSES],
        "markers": markers,
        "counterfactuals": counterfactuals,
        "review": review,
    }


def loans_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    statuses = [r.get("monitoring_status") for r in rows]
    return {
        "total": len(rows),
        "replaying": statuses.count("replaying"),
        "completed": statuses.count("completed"),
        "not_started": statuses.count(None),
        "alerts": sum(_int(r.get("alerts")) for r in rows),
    }


def alert_steps(alert: Mapping[str, Any]) -> list[dict[str, Any]]:
    collections = alert.get("collections") or {}
    steps = [
        _step("detect", "탐지", "done", f"{alert.get('week')}주차"),
        _step("investigate", "조기경보 조사", "done", SEVERITY_LABELS.get(alert.get("severity") or "", "")),
        _step("collections", "추심 에이전트", "done" if collections else "current", collections.get("decision") or ""),
    ]
    restructure = alert.get("restructure")
    if restructure:
        st = restructure.get("status")
        steps.append(_step("restructure", "재조정 결정", "current" if st == "pending_officer" else "done",
                           RESTRUCTURE_NOTES.get(st, st or "")))
    return steps


def loan_summary(view: Mapping[str, Any]) -> dict[str, Any]:
    loan, mon, weeks, alerts = view["loan"], view["monitoring"] or {}, view["weeks"], view["alerts"]
    baseline = loan.get("baseline") or {}
    processed = _int(mon.get("processed_week"))
    severity = dict.fromkeys(("high", "medium", "low"), 0)
    for a in alerts:
        if a.get("severity") in severity:
            severity[a["severity"]] += 1
    buffers = [(w.get("week"), (w.get("row") or {}).get("buffer_days")) for w in weeks]
    numeric = [(wk, b) for wk, b in buffers if charts.is_num(b)]
    min_week, min_buffer = min(numeric, key=lambda x: x[1]) if numeric else (None, None)
    groups = []
    for w in weeks:
        r = w.get("row") or {}
        triggers = w.get("triggers") or []
        tip = (f"{w.get('week')}주차 {r.get('start', '')}~{r.get('end', '')} · 매출 {krw_text(r.get('revenue'))}"
               f" · 버퍼 {r.get('buffer_days', '-')}일")
        if triggers:
            tip += " · " + ", ".join(TRIGGER_LABELS.get(t, t) for t in triggers)
        groups.append((f"{w.get('week')}주", [[("revenue", r.get("revenue"), "red" if triggers else "blue", tip)]]))
    base_rev = baseline.get("avg_weekly_revenue")
    refs = [("기준 주매출", base_rev, "neutral")] if charts.is_num(base_rev) else []
    chart = charts.columns(groups, refs=refs, tick_fmt=man, **LOAN_CHART) if groups else None
    markers = []
    if chart:
        for w, g in zip(weeks, chart["groups"], strict=True):
            if w.get("new_triggers"):
                names = ", ".join(TRIGGER_LABELS.get(t, t) for t in w["new_triggers"])
                markers.append({"x": g["cx"], "y": round(g["bars"][0]["top"] - 6, 2), "week": w.get("week"),
                                "tip": f"{w.get('week')}주차 새 트리거: {names}"})
    return {
        "processed": processed,
        "weeks_total": REPLAY_WEEKS,
        "progress_pct": round(min(max(processed / REPLAY_WEEKS, 0.0), 1.0) * 100, 1),
        "severity": severity,
        "alerts": len(alerts),
        "min_buffer": min_buffer,
        "min_buffer_week": min_week,
        "baseline_buffer": baseline.get("cash_buffer_days"),
        "chart": chart,
        "line": charts.line([b for _, b in buffers], **LOAN_CHART) if groups else None,
        "markers": markers,
        "legend": [("매출", "blue"), ("트리거 주", "red"), ("기준 주매출", "neutral"), ("현금 버퍼(일, 오른쪽 축)", "purple")],
        "alert_steps": {a.get("alert_id"): alert_steps(a) for a in alerts},
    }


def _pct_text(v: float) -> str:
    return f"{v:.0f}%"


def ops_summary(s: Mapping[str, Any], step_labels: Mapping[str, str]) -> dict[str, Any]:
    lat = s.get("latency") or {}
    target = lat.get("target_min")
    latency = charts.gauge(lat.get("p95_min"), target * 2 if charts.is_num(target) else None)
    success_src = s.get("agent_success") or {}
    success = charts.gauge(success_src.get("rate"), 1.0)
    steps = charts.stacked([
        (step_labels.get(name, name), [("ok", c.get("ok"), "green"), ("degraded", c.get("degraded"), "amber"),
                                       ("failed", c.get("failed"), "red")])
        for name, c in sorted((success_src.get("by_step") or {}).items())
    ])
    fairness = {}
    for axis, a in ((s.get("fairness") or {}).get("axes") or {}).items():
        values = a.get("values") or {}
        groups, sufficient = [], []
        for value, r in values.items():
            rate = r.get("approve_rate")
            pct = rate * 100 if charts.is_num(rate) else None
            if pct is None:
                tip = f"{value} 추천 없음"
            else:
                tip = f"{value} 추천 approve율 {pct:.1f}% · 추천 완료 {r.get('assessed')}건"
                if r.get("insufficient"):
                    tip += " · 표본 부족"
                else:
                    sufficient.append(pct)
            groups.append((value, [[("approve_rate", pct, "grey" if r.get("insufficient") else "blue", tip)]]))
        refs = [("최대", max(sufficient), "red"), ("최소", min(sufficient), "red")] if len(sufficient) >= 2 else []
        fairness[axis] = (charts.columns(groups, height=200, refs=refs, vmax=100, tick_fmt=_pct_text)
                          if groups else None)
    return {
        "latency": latency,
        "latency_tone": "neutral" if latency["frac"] is None else ("green" if lat.get("met") else "red"),
        "success": success,
        "success_tone": "neutral" if success["frac"] is None else ("green" if success_src.get("met") else "red"),
        "steps": steps,
        "fairness": fairness,
    }
```

- [ ] **Step 6: 통과 확인**

Run: `$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python -m pytest -q -p no:cacheprovider tests\console`
Expected: 모두 PASS(기존 콘솔 테스트 포함).

- [ ] **Step 7: 커밋** — `feat(console): P7 화면 집계(dashboard.py)와 상태 상수 분리`

---

### Task 3: 로컬 미리보기 `consolepreview`

**Files:**
- Create: `src/consolepreview/__init__.py`, `src/consolepreview/__main__.py`, `src/consolepreview/fixtures.py`
- Test: `tests/console/test_preview.py`

`pyproject.toml`의 `packages`에는 넣지 않는다(편집 설치의 `.pth`가 `src`를 경로에 두므로 `python -m consolepreview`가 동작한다). 콘솔 Dockerfile은 `src/console`만 복사하므로 이미지에 들어가지 않는다. 고정 시각 `2026-11-03 10:00 KST`, 신청 11건(`app-20261103-p01`~`p11`), 대출 3건(p03 재생 완료·경보 2건, p04 재생 중, p11 재생 전), 운영 측정 1건(24건, 업종 경보·표본 부족 편의점).

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/console/test_preview.py`

```python
import pytest
from fastapi.testclient import TestClient

from console.app import create_app
from consolepreview.fixtures import build_deps


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(build_deps()), follow_redirects=False)


@pytest.mark.parametrize("path", [
    "/",
    "/applications/rows",
    "/applications/app-20261103-p01",
    "/applications/app-20261103-p06",
    "/loans",
    "/loans/app-20261103-p03",
    "/loans/app-20261103-p04",
    "/loans/app-20261103-p11",
    "/ops",
    "/grievances",
    "/borrower",
])
def test_preview_pages_open(client, path):
    assert client.get(path).status_code == 200
```

- [ ] **Step 2: 실패 확인**

Run: `$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python -m pytest -q -p no:cacheprovider tests\console\test_preview.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'consolepreview'`

- [ ] **Step 3: `src/consolepreview/__init__.py`**

```python
"""로컬 미리보기(P7): 합성 고정 데이터로 심사 콘솔을 띄운다. Azure 자원·Entra 로그인 없이 화면만 확인한다."""

import sys
from pathlib import Path

_ORCHESTRATOR = str(Path(__file__).resolve().parents[1] / "orchestrator")
if _ORCHESTRATOR not in sys.path:
    sys.path.insert(0, _ORCHESTRATOR)
```

- [ ] **Step 4: `src/consolepreview/fixtures.py`**

```python
"""미리보기용 합성 데이터와 메모리 저장소. 실제 차주·실제 측정값이 아니다(화면 확인 전용).

신청 11건(상태 골고루), 대출 3건(재생 완료·재생 중·재생 전), 운영 측정 실행 1개를 만든다.
원천 데이터(RawStore)는 비워 두어 상세 화면은 가명 토큰 복원 불가 상태로 보인다.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from console.app import ConsoleDeps
from console.auth import CREDIT_OFFICER, Principal

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 11, 3, 10, 0, tzinfo=KST)
DAY = NOW.replace(hour=9, minute=0)
RUN_ID = "load-20261103090000"
PRINCIPAL = Principal(user_id="preview-officer", name="미리보기 심사역", roles=frozenset({CREDIT_OFFICER}))
FLOW = ("received", "consent_granted", "data_assembled", "underwritten", "assessed", "awaiting_officer")
OFFSETS = (0, 20, 65, 100, 190, 195)
MONTHS = ("2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09")
REVENUE = (38_000_000, 41_000_000, 36_000_000, 44_000_000, 47_000_000, 42_000_000)


def _at(start: datetime, seconds: int) -> str:
    return (start + timedelta(seconds=seconds)).isoformat()


def _timeline(start: datetime, statuses: tuple[str, ...], extra: tuple[tuple[str, int], ...] = ()) -> list[dict]:
    out = [{"status": s, "at": _at(start, OFFSETS[i])} for i, s in enumerate(statuses)]
    out += [{"status": s, "at": _at(start, sec)} for s, sec in extra]
    return out


def _monthly() -> list[dict[str, Any]]:
    return [{"month": m, "revenue": r, "operating_out": round(r * 0.62), "debt_service": 2_400_000,
             "owner_draw": 3_000_000 + i * 200_000} for i, (m, r) in enumerate(zip(MONTHS, REVENUE, strict=True))]


def _assessment(app_id: str, rec: str) -> dict[str, Any]:
    return {
        "application_id": app_id,
        "recommendation": rec,
        "summary": "최근 6개월 매출이 월 3,600만~4,700만 원으로 안정적이고 상환 여력(DSCR 1.8)이 정책 기준을 넘는다.",
        "claims": [
            {"text": "DSCR 1.8로 정책 기준 1.25를 넘는다", "metric_ids": ["M:dscr", "P:R101:threshold"], "txn_ids": []},
            {"text": "대표자 인출 비율 18%로 기준 35% 안에 있다", "metric_ids": ["S:owner_draw_ratio"], "txn_ids": []},
        ],
        "proposed_structure": {"amount_krw": 30_000_000, "tenor_months": 24, "schedule": "level",
                               "reduced_payment_months": ["02"], "rationale": "비수기 2월 상환액을 줄인다."},
        "counterfactuals": [
            {"metric_id": "M:dscr", "current": 1.8, "target": 2.2, "explanation": "DSCR이 2.2 이상이면 금리 구간 A"},
            {"metric_id": "S:owner_draw_ratio", "current": 0.18, "target": 0.1,
             "explanation": "대표자 인출을 10% 이하로 줄이면 한도 상향"},
        ],
        "review": {"passed": True, "rounds": 2, "issues": []},
        "models": {"analyst": "gpt-5.4", "reviewer": "gpt-5.4-mini"},
    }


def _doc(n: int, status: str, timeline: list[dict], *, rec: str | None = "approve", amount: int = 30_000_000,
         **overrides: Any) -> dict[str, Any]:
    app_id = f"app-20261103-p{n:02d}"
    doc: dict[str, Any] = {
        "id": app_id, "application_id": app_id, "applicant_id": f"preview-{n:02d}",
        "requested_amount_krw": amount, "requested_tenor_months": 24, "status": status,
        "received_at": timeline[0]["at"], "updated_at": timeline[-1]["at"], "timeline": timeline,
        "underwriting": None, "policy": None, "assessment": None, "flags": [],
        "sla": {"hours": 24, "deadline": (datetime.fromisoformat(timeline[0]["at"]) + timedelta(hours=24)).isoformat(),
                "escalated": False},
        "decision_attempts": [], "officer_decision": None, "error": None,
    }
    if any(t["status"] == "underwritten" for t in timeline):
        doc["underwriting"] = {
            "features": {"dscr": 1.8, "avg_monthly_inflow_krw": 41_333_333, "industry": "cafe"},
            "signals": {"owner_draw_ratio": 0.18}, "flags": [], "monthly": _monthly(),
            "classification": None, "category_overrides": {},
        }
        doc["policy"] = {
            "application_id": app_id, "decision": "eligible", "max_amount_krw": 35_000_000, "rate_band": "B",
            "reason_codes": [
                {"code": "R101", "message": "DSCR 기준 충족", "field": "dscr", "observed": 1.8, "threshold": 1.25},
                {"code": "R102", "message": "대표자 인출 비율 기준 이내", "field": "owner_draw_ratio",
                 "observed": 0.18, "threshold": 0.35},
                {"code": "R104", "message": "업력 기준 충족(개월)", "field": "business_age_months",
                 "observed": 48, "threshold": 36},
            ],
            "rules_version": "2026.11-v1",
        }
    if rec is not None and doc["policy"] is not None:
        doc["assessment"] = _assessment(app_id, rec)
    doc.update(overrides)
    return doc


def _start(minutes_ago: int) -> datetime:
    return NOW - timedelta(minutes=minutes_ago)


def applications() -> list[dict[str, Any]]:
    s = [_start(m) for m in (6, 1500, 300, 280, 260, 240, 30, 2, 90, 12, 1800)]
    return [
        _doc(1, "awaiting_officer", _timeline(s[0], FLOW)),
        _doc(2, "escalated", _timeline(s[1], FLOW, (("escalated", 86_400),)), rec="refer",
             sla={"hours": 24, "deadline": _at(s[1], 86_400), "escalated": True}),
        _doc(3, "approved", _timeline(s[2], FLOW, (("approved", 3_600),)),
             officer_decision={"decision": "approve", "amount_krw": 30_000_000, "officer_id": "preview-officer",
                               "conditions": [], "reason": "정책 한도 안, 상환 여력 충분"}),
        _doc(4, "conditionally_approved", _timeline(s[3], FLOW, (("conditionally_approved", 4_000),)),
             rec="conditional",
             officer_decision={"decision": "conditional", "amount_krw": 25_000_000, "officer_id": "preview-officer",
                               "conditions": ["월별 매출 보고"], "reason": "비수기 대비 조건 부여"}),
        _doc(5, "declined", _timeline(s[4], FLOW, (("declined", 3_000),)), rec="decline",
             officer_decision={"decision": "decline", "amount_krw": None, "officer_id": "preview-officer",
                               "conditions": [], "reason": "고금리 차입 중첩"}),
        _doc(6, "withdrawn", _timeline(s[5], FLOW[:3], (("withdrawn", 120),)), rec=None),
        _doc(7, "data_incomplete", _timeline(s[6], FLOW[:2], (("data_incomplete", 70),)), rec=None),
        _doc(8, "received", _timeline(s[7], FLOW[:1]), rec=None),
        _doc(9, "failed", _timeline(s[8], FLOW[:4], (("failed", 150),)), rec=None,
             error={"stage": "assess", "message": "모델 응답 시간 초과(합성 예시)"}),
        _doc(10, "assessed", _timeline(s[9], FLOW[:5]), rec="conditional"),
        _doc(11, "approved", _timeline(s[10], FLOW, (("approved", 2_400),)), amount=20_000_000,
             officer_decision={"decision": "approve", "amount_krw": 20_000_000, "officer_id": "preview-officer",
                               "conditions": [], "reason": "정책 한도 안"}),
    ]


BASELINE = {
    "avg_weekly_revenue": 9_500_000, "revenue_volatility": 0.12, "cash_buffer_days": 30.0,
    "operating_daily_krw": 900_000, "tax_bank_match_ratio": 0.97,
    "seasonal_index": {f"{m:02d}": 1.0 for m in range(1, 13)},
}
WEEK_REVENUE = (9_800_000, 9_400_000, 10_100_000, 9_000_000, 8_700_000, 6_100_000, 7_400_000,
                8_200_000, 7_000_000, 4_800_000, 5_600_000, 6_900_000, 7_800_000)
WEEK_BUFFER = (31.0, 30.2, 31.5, 29.0, 27.4, 21.0, 22.3, 23.9, 19.5, 12.8, 11.2, 13.6, 15.9)
TRIGGERS = {6: ["inflow_drop"], 7: ["inflow_drop"], 10: ["inflow_drop", "new_debt"], 11: ["new_debt"]}


def _row(week: int) -> dict[str, Any]:
    start = datetime(2026, 11, 2) + timedelta(weeks=week - 1)
    revenue = WEEK_REVENUE[week - 1]
    return {
        "week": week, "start": start.date().isoformat(), "end": (start + timedelta(days=6)).date().isoformat(),
        "revenue": revenue, "revenue_ratio": round(revenue / BASELINE["avg_weekly_revenue"], 3),
        "operating_out": round(revenue * 0.6), "debt_service": 1_000_000,
        "high_cost_lender": 1_500_000 if week >= 10 else 0, "high_cost_count": 1 if week >= 10 else 0,
        "loan_proceeds": 0, "unclassified_in": 0, "end_balance": 20_000_000 - week * 600_000,
        "buffer_days": WEEK_BUFFER[week - 1],
    }


def _weeks(replay_id: str, count: int) -> list[dict[str, Any]]:
    out, seen = [], set()
    for week in range(1, count + 1):
        triggers = TRIGGERS.get(week, [])
        new = [t for t in triggers if t not in seen]
        seen.update(triggers)
        alert_id = f"ew-{replay_id}-w{week:02d}" if new else None
        out.append({"week": week, "row": _row(week), "triggers": triggers, "new_triggers": new,
                    "classification": None,
                    "investigation": {"decision": "alert", "reason": "합성 예시", "record": {}} if new else None,
                    "alert_id": alert_id})
    return out


def _loan(app_id: str, applicant: str, industry: str, amount: int, monitoring: dict | None) -> dict[str, Any]:
    return {
        "id": app_id, "loan_id": app_id, "application_id": app_id, "applicant_id": applicant, "industry": industry,
        "underwriting_consent_id": None, "decision": "approve", "amount_krw": amount, "tenor_months": 24,
        "approved_at": (NOW - timedelta(days=1)).isoformat(), "baseline": BASELINE, "monitoring": monitoring,
        "updated_at": NOW.isoformat(),
    }


def _monitoring(replay_id: str, status: str, weeks: int, started: datetime) -> dict[str, Any]:
    rows = _weeks(replay_id, weeks)
    return {
        "replay_id": replay_id, "consent_id": None, "flow_start": "2026-11-02", "started_at": started.isoformat(),
        "mode": "inline", "status": status, "processed_week": weeks,
        "seen_triggers": sorted({t for w in rows for t in w["triggers"]}), "overrides": {}, "weeks": rows,
        "alert_ids": [w["alert_id"] for w in rows if w["alert_id"]], "error": None,
    }


def _alert(loan_id: str, replay_id: str, week: int, severity: str, restructure: str | None) -> dict[str, Any]:
    alert_id = f"ew-{replay_id}-w{week:02d}"
    doc: dict[str, Any] = {
        "id": alert_id, "schema_version": "1", "alert_id": alert_id, "loan_id": loan_id, "week": week,
        "as_of": _row(week)["end"], "severity": severity,
        "summary": f"{week}주차 매출이 기준 대비 크게 줄고 현금 버퍼가 감소했다(합성 예시).",
        "signals": [{"kind": t, "text": f"{week}주차 {t} 신호", "metric_ids": [f"W:{week}:revenue"], "txn_ids": []}
                    for t in TRIGGERS[week]],
        "recommended_action": "hardship_review" if restructure else "monitor", "triggers": TRIGGERS[week],
        "models": {"investigator": "gpt-5.4"}, "replay_id": replay_id,
        "status": "restructure_pending" if restructure == "pending_officer" else "handled",
        "created_at": NOW.isoformat(), "event_error": None,
        "collections": {"decision": "routed" if restructure else "reminder", "reason": "합성 예시",
                        "at": NOW.isoformat(),
                        "contacts": [{"alert_id": alert_id, "on": _row(week)["end"], "at": NOW.isoformat(),
                                      "script_id": "hardship_offer" if restructure else "soft_reminder",
                                      "status": "sent", "reason": None, "by": "collections-agent"}],
                        "record": {}},
        "restructure": None,
    }
    if restructure:
        doc["restructure"] = {"request_id": f"rs-{alert_id}", "category": "debt_overload",
                              "note": "고금리 차입 중첩(합성 예시)", "status": restructure,
                              "created_at": NOW.isoformat()}
    return doc


def loans() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    done = _loan("app-20261103-p03", "preview-03", "카페", 30_000_000,
                 _monitoring("rp-preview-1", "completed", 13, NOW - timedelta(hours=2)))
    running = _loan("app-20261103-p04", "preview-04", "한식", 25_000_000,
                    _monitoring("rp-preview-2", "replaying", 4, NOW - timedelta(minutes=2)))
    idle = _loan("app-20261103-p11", "preview-11", "편의점", 20_000_000, None)
    alerts = [_alert(done["id"], "rp-preview-1", 6, "medium", None),
              _alert(done["id"], "rp-preview-1", 10, "high", "pending_officer")]
    return [done, running, idle], alerts


def _projected(i: int, industry: str, rec: str, region: str, age: str, outcomes: tuple[str, str]) -> dict:
    sub = DAY + timedelta(minutes=i)
    usage = [{"model": "gpt-5.4-mini", "input_tokens": 1200, "output_tokens": 150, "calls": 1}]
    return {
        "application_id": f"r-{i:02d}", "applicant_id": f"load-healthy-{i:02d}", "status": "awaiting_officer",
        "submitted_at": sub.isoformat(), "received_at": sub.isoformat(), "source": "load", "run_id": RUN_ID,
        "segment": {"industry": industry, "region": region, "business_age_band": age},
        "steps": [{"step": "classify", "outcome": outcomes[0], "agent": True, "usage": usage},
                  {"step": "assess", "outcome": outcomes[1], "agent": True, "usage": usage}],
        "timeline": [{"status": "awaiting_officer", "at": (sub + timedelta(minutes=3 + i % 5)).isoformat()}],
        "flags": [], "policy": {"decision": "eligible", "reason_codes": []},
        "assessment": {"recommendation": rec}, "officer_decision": None,
    }


def ops_docs() -> list[dict[str, Any]]:
    plan = ([("카페", "approve")] * 10 + [("한식", r) for r in ("approve", "decline", "refer", "decline", "approve",
                                                              "decline", "conditional", "decline", "approve", "decline",
                                                              "refer")]
            + [("편의점", "approve"), ("편의점", "decline"), ("편의점", "approve")])
    docs = []
    for i, (industry, rec) in enumerate(plan):
        outcomes = ("ok", "degraded" if i % 7 == 3 else ("failed" if i == 20 else "ok"))
        docs.append(_projected(i, industry, rec, "서울" if i % 2 else "부산", "3년+" if i % 3 else "1~3년", outcomes))
    return docs


class MemoryApplications:
    def __init__(self, docs: list[dict[str, Any]]):
        self.docs = {d["id"]: d for d in docs}

    def list_recent(self, limit: int) -> list[dict[str, Any]]:
        rows = [{"id": d["id"], "applicant_id": d["applicant_id"], "status": d["status"],
                 "requested_amount_krw": d["requested_amount_krw"], "updated_at": d["updated_at"],
                 "recommendation": (d.get("assessment") or {}).get("recommendation")} for d in self.docs.values()]
        return sorted(rows, key=lambda r: r["updated_at"], reverse=True)[:limit]

    def get(self, application_id: str) -> dict[str, Any] | None:
        return self.docs.get(application_id)


class NoRaw:
    def get_json(self, container: str, name: str) -> None:
        return None


class RecordingQueue:
    """결정·재생 메시지를 기록만 한다(오케스트레이터 없음)."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    def send(self, queue: str, message: dict[str, Any]) -> None:
        self.sent.append((queue, message))


class MemoryMonitoring:
    def __init__(self, loans: list[dict[str, Any]], alerts: list[dict[str, Any]]):
        self.loans = {d["id"]: d for d in loans}
        self.alerts = alerts

    def list_loans(self, limit: int) -> list[dict[str, Any]]:
        return [{"id": d["id"], "applicant_id": d["applicant_id"], "industry": d["industry"],
                 "amount_krw": d["amount_krw"], "tenor_months": d["tenor_months"],
                 "monitoring_status": (d["monitoring"] or {}).get("status"),
                 "alerts": len(d["monitoring"]["alert_ids"]) if d["monitoring"] else None,
                 "updated_at": d["updated_at"]} for d in self.loans.values()][:limit]

    def get_loan(self, loan_id: str) -> dict[str, Any] | None:
        return self.loans.get(loan_id)

    def list_alerts(self, loan_id: str, replay_id: str) -> list[dict[str, Any]]:
        return [a for a in self.alerts if a["loan_id"] == loan_id and a["replay_id"] == replay_id]


class EmptyPrivacy:
    def consents_for_applicant(self, applicant_id: str) -> list[dict[str, Any]]:
        return []

    def consents_for_application(self, application_id: str) -> list[dict[str, Any]]:
        return []

    def get_consent(self, consent_id: str) -> None:
        return None

    def get_proof(self, consent_id: str) -> None:
        return None

    def list_grievances(self, limit: int) -> list[dict[str, Any]]:
        return []

    def grievances_for_applicant(self, applicant_id: str) -> list[dict[str, Any]]:
        return []

    def get_grievance(self, application_id: str, grievance_id: str) -> None:
        return None


class MemoryOps:
    def __init__(self, runs: dict[str, list[dict[str, Any]]]):
        self.data = runs

    def runs(self) -> list[str]:
        return list(self.data)

    def by_run(self, run_id: str) -> list[dict[str, Any]]:
        return self.data[run_id]


def build_deps() -> ConsoleDeps:
    loan_docs, alerts = loans()
    return ConsoleDeps(
        store=MemoryApplications(applications()), raw=NoRaw(), queue=RecordingQueue(),
        pseudonym_key=b"preview-pseudonym-key-not-secret-0000", demo_principal=PRINCIPAL,
        monitoring=MemoryMonitoring(loan_docs, alerts), clock=lambda: NOW, privacy=EmptyPrivacy(),
        ops=MemoryOps({RUN_ID: ops_docs()}),
    )
```

- [ ] **Step 5: `src/consolepreview/__main__.py`**

```python
"""`python -m consolepreview [--port 8765]`: 127.0.0.1에서만 연다."""

import argparse

import uvicorn

from console.app import create_app
from consolepreview.fixtures import build_deps


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m consolepreview", description="심사 콘솔 로컬 미리보기(합성 데이터)")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    print(f"미리보기: http://127.0.0.1:{args.port}/  (합성 데이터, 결정·재생 버튼은 기록만 하고 처리하지 않음)")
    uvicorn.run(create_app(build_deps()), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: 통과 확인**

Run: Step 2 명령. Expected: 11개 PASS(아직 옛 화면이지만 모든 경로가 200).

- [ ] **Step 7: 커밋** — `feat(consolepreview): 합성 데이터 로컬 미리보기`

---

### Task 4: 공통 셸 — CSS, `base.html`, `_charts.html`, 메뉴 강조

**Files:**
- Rewrite: `src/console/static/console.css`, `src/console/templates/base.html`
- Create: `src/console/templates/_charts.html`
- Modify: 템플릿 13개에 `{% set nav = "…" %}` 한 줄
- Test: `tests/console/test_ui.py`(이 Task에서 만들고 Task 5~8에서 덧붙임)

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/console/test_ui.py`

```python
"""P7 화면 구성 고정: 차트·스테퍼·KPI가 나오고, 데모 대본 문구와 CSP 제약이 유지된다."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import console.app as console_app
from console.app import create_app
from consolepreview.fixtures import build_deps

TEMPLATES = Path(console_app.__file__).parent / "templates"


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(build_deps()), follow_redirects=False)


def page(client, path):
    resp = client.get(path)
    assert resp.status_code == 200, path
    return resp.text


def test_templates_have_no_inline_style_or_script():
    for path in TEMPLATES.glob("*.html"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\sstyle\s*=", text), path.name
        assert not re.search(r"<style", text), path.name
        for tag in re.findall(r"<script\b[^>]*>", text):
            assert "src=" in tag, path.name


def test_csp_is_unchanged(client):
    assert console_app.CSP == (
        "default-src 'self'; script-src 'self' https://unpkg.com; style-src 'self'; "
        "img-src 'self' data:; frame-ancestors 'none'"
    )
    assert client.get("/").headers["content-security-policy"] == console_app.CSP


@pytest.mark.parametrize(("path", "item"), [
    ("/", '<li class="n-home active"><a href="/">신청</a></li>'),
    ("/loans", '<li class="n-loans active">'),
    ("/grievances", '<li class="n-grievances active">'),
    ("/borrower", '<li class="n-borrower active">'),
    ("/ops", '<li class="n-ops active">'),
])
def test_sidebar_marks_current_screen(client, path, item):
    html = page(client, path)
    assert '<aside class="sidebar">' in html and item in html
    assert html.count(" active\">") == 1
```

- [ ] **Step 2: 실패 확인**

Run: `$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python -m pytest -q -p no:cacheprovider tests\console\test_ui.py`
Expected: `test_sidebar_marks_current_screen` 5건 FAIL(`<aside class="sidebar">` 없음). 나머지 2건은 PASS(지금도 지켜지는 제약을 고정하는 가드).

- [ ] **Step 3: `src/console/static/console.css` 전체 교체**

```css
/* AMA Lending 심사 콘솔 — Azure Portal 풍(P7). 색은 Fluent 팔레트, 차트는 서버 SVG + CSS 애니메이션.
   CSP style-src 'self'라서 인라인 style 없이 클래스만 쓴다. */
:root {
  --fg: #201f1e; --muted: #605e5c; --line: #e1dfdd; --bg: #f3f2f1; --card: #fff;
  --accent: #0078d4; --accent-dark: #005a9e; --nav: #1b2a4a; --nav-fg: #c8d1e0; --nav-active: #2b3d63;
  --c-blue: #0078d4; --c-green: #107c10; --c-amber: #d29200; --c-red: #d13438; --c-purple: #8764b8;
  --c-neutral: #8a8886; --c-grey: #c8c6c4;
  --warn: #8a5d00; --bad: #a4262c; --good: #0b6a0b;
  --shadow: 0 1.6px 3.6px rgba(0, 0, 0, .13), 0 .3px .9px rgba(0, 0, 0, .11);
}
* { box-sizing: border-box; }
body { margin: 0; font: 14px/1.5 "Segoe UI", system-ui, "Malgun Gothic", sans-serif; color: var(--fg); background: var(--bg); }
a { color: var(--accent); }
a:hover { color: var(--accent-dark); }

/* 셸: 왼쪽 사이드바 + 본문 */
.shell { display: grid; grid-template-columns: 224px 1fr; min-height: 100vh; }
.sidebar { display: flex; flex-direction: column; background: var(--nav); color: var(--nav-fg); position: sticky; top: 0; height: 100vh; }
.sidebar .brand { display: block; padding: 1rem 1.1rem; color: #fff; font-weight: 600; font-size: 1.05rem; text-decoration: none; border-bottom: 1px solid rgba(255, 255, 255, .08); }
.sidebar .brand small { display: block; color: var(--nav-fg); font-weight: 400; font-size: .8rem; }
.sidebar nav ul { list-style: none; margin: .6rem 0; padding: 0; }
.sidebar nav li a { display: flex; align-items: center; gap: .7rem; padding: .55rem 1.1rem; color: var(--nav-fg); text-decoration: none; border-left: 3px solid transparent; }
.sidebar nav li a::before { width: 1.1rem; text-align: center; font-size: 1rem; }
.n-home a::before { content: "▤"; }
.n-loans a::before { content: "◔"; }
.n-grievances a::before { content: "✎"; }
.n-borrower a::before { content: "☺"; }
.n-ops a::before { content: "▥"; }
.sidebar nav li a:hover { background: rgba(255, 255, 255, .06); color: #fff; }
.sidebar nav li.active a { background: var(--nav-active); color: #fff; border-left-color: var(--accent); font-weight: 600; }
.who { margin-top: auto; padding: .9rem 1.1rem; border-top: 1px solid rgba(255, 255, 255, .08); font-size: .85rem; }
.who span { display: block; }
.who-name { color: #fff; font-weight: 600; }
.who a { color: #9cc7f0; }
.content { min-width: 0; }
.demo-banner { margin: 0; padding: .5rem 1.5rem; background: #fff4ce; color: #5c4400; border-bottom: 1px solid #e6c84f; }
main { max-width: 1280px; padding: 1.2rem 1.5rem 3rem; }

/* 페이지 머리 */
.page-head { display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between; gap: 1rem; margin-bottom: 1rem; }
.page-head h1 { margin: 0; font-size: 1.5rem; font-weight: 600; display: flex; flex-wrap: wrap; align-items: center; gap: .5rem; }
.crumb { margin: 0 0 .2rem; color: var(--muted); font-size: .85rem; }
.crumb a { color: var(--muted); text-decoration: none; }
.actions { display: flex; flex-wrap: wrap; align-items: flex-end; gap: .5rem; }
.actions form { display: flex; align-items: flex-end; gap: .5rem; margin: 0; }
.actions form label { margin: 0; }
h1 small, h2 small, h3 small { color: var(--muted); font-weight: 400; font-size: .8em; }
h2 { margin: 0 0 .7rem; font-size: 1.1rem; font-weight: 600; }
h3 { font-size: 1rem; font-weight: 600; }

/* 카드와 격자 */
.card { background: var(--card); border-radius: 4px; box-shadow: var(--shadow); padding: 1rem 1.2rem; margin: 0 0 1rem; min-width: 0; }
section { margin: 0 0 1rem; }
.grid-2, .grid-4 { display: grid; gap: 1rem; margin-bottom: 1rem; }
.grid-2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.grid-4 { grid-template-columns: repeat(4, minmax(0, 1fr)); }
.grid-2 > .card, .grid-4 > .card { margin: 0; }
.kpi { border-top: 3px solid var(--c-blue); }
.kpi-label { color: var(--muted); font-size: .85rem; }
.kpi-value { font-size: 1.75rem; font-weight: 600; line-height: 1.2; margin: .2rem 0; }
.kpi-sub { color: var(--muted); font-size: .8rem; }
.kpi-blue { border-top-color: var(--c-blue); }
.kpi-green { border-top-color: var(--c-green); }
.kpi-amber { border-top-color: var(--c-amber); }
.kpi-red { border-top-color: var(--c-red); }
.kpi-purple { border-top-color: var(--c-purple); }
.kpi-neutral { border-top-color: var(--c-neutral); }

/* 표·목록 */
table { border-collapse: collapse; width: 100%; margin: .5rem 0 1rem; background: var(--card); }
th, td { border-bottom: 1px solid var(--line); padding: .45rem .6rem; text-align: left; vertical-align: top; }
thead th { color: var(--muted); font-weight: 600; font-size: .85rem; background: #faf9f8; }
tbody tr:hover { background: #f5f9fd; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
table.kv th { width: 40%; font-weight: 500; color: var(--muted); background: none; }
dl.facts { display: grid; grid-template-columns: max-content 1fr; gap: .3rem 1.2rem; margin: 0; }
dl.facts dt { color: var(--muted); }
dl.facts dd { margin: 0; }
details { margin-top: .6rem; }
details summary { cursor: pointer; color: var(--accent); font-size: .9rem; }

/* 상태 배지 */
.status, .badge, .pill { display: inline-block; padding: 0 .55rem; border-radius: 1rem; background: #edebe9; font-size: .8rem; font-weight: 600; line-height: 1.6rem; white-space: nowrap; }
.s-awaiting_officer, .s-escalated, .s-data_incomplete { background: #fff4ce; color: var(--warn); }
.s-approved, .s-conditionally_approved { background: #dff6dd; color: var(--good); }
.s-declined, .s-failed, .s-closed_no_decision { background: #fde7e9; color: var(--bad); }
.s-received, .s-consent_granted, .s-data_assembled, .s-underwritten, .s-assessed { background: #deecf9; color: var(--accent-dark); }
.sev-high, .m-failed, .badge-red { background: #fde7e9; color: var(--bad); }
.sev-medium, .m-replaying { background: #fff4ce; color: var(--warn); }
.sev-low { background: #deecf9; color: var(--accent-dark); }
.m-completed { background: #dff6dd; color: var(--good); }
.badge-neutral { background: #edebe9; color: var(--muted); }
.rec-approve { background: #dff6dd; color: var(--good); }
.rec-conditional { background: #deecf9; color: var(--accent-dark); }
.rec-refer { background: #efe6f8; color: #5c2e91; }
.rec-decline { background: #fde7e9; color: var(--bad); }
.s-withdrawn, .c-revoked, .c-void, .warn.status { background: #fde7e9; color: var(--bad); }
.c-granted, .g-resolved_upheld, .g-resolved_rereviewed, .ok.status { background: #dff6dd; color: var(--good); }
.c-expired, .g-pending_officer { background: #fff4ce; color: var(--warn); }

/* 기존 상세 요소 */
.chip { display: inline-block; margin: .15rem .25rem 0 0; padding: 0 .4rem; border: 1px solid var(--line); border-radius: .3rem; font: 12px ui-monospace, monospace; background: #fff; }
.chip.warn { border-color: var(--c-amber); color: var(--warn); }
.chip.txn { background: #faf9f8; }
.claims li, .counterfactuals li { margin-bottom: .6rem; }
.pii { padding: .4rem .7rem; border-left: 3px solid var(--line); color: var(--muted); background: var(--card); }
.pii.restored { border-color: var(--c-amber); }
.error { color: var(--bad); font-weight: 500; }
.ok { color: var(--good); font-weight: 500; }
.timeline { padding-left: 1.2rem; }
.note { color: var(--muted); }
.notice { white-space: pre-wrap; }
.quote { white-space: pre-wrap; padding: .4rem .6rem; border-left: 3px solid var(--line); background: #faf9f8; }
article.consent, article.grievance { background: var(--card); border: 1px solid var(--line); border-radius: 4px; padding: .2rem .9rem .7rem; margin: .6rem 0; }
.ops-alert { margin: .5rem 0 1rem; padding: .6rem 1rem; background: #fde7e9; color: var(--bad); border-left: 4px solid var(--c-red); border-radius: 2px; }
.alert-card { border-left: 4px solid var(--c-neutral); }
.alert-high { border-left-color: var(--c-red); }
.alert-medium { border-left-color: var(--c-amber); }
.alert-low { border-left-color: var(--c-blue); }

/* 폼·버튼 */
form label { display: block; margin: .5rem 0; }
form input[name], form select, form textarea { display: block; width: 100%; max-width: 32rem; padding: .35rem .5rem; font: inherit; border: 1px solid #8a8886; border-radius: 2px; background: #fff; }
form input[type=radio] { display: inline; width: auto; }
.actions select { min-width: 14rem; }
fieldset { border: 1px solid var(--line); max-width: 32rem; }
button, .button { display: inline-block; padding: .4rem 1rem; border: 1px solid var(--accent); border-radius: 2px; background: var(--accent); color: #fff; font: inherit; font-weight: 600; text-decoration: none; cursor: pointer; }
button:hover, .button:hover { background: var(--accent-dark); color: #fff; }
.button.secondary { background: #fff; color: var(--fg); border-color: #8a8886; }
.button.secondary:hover { background: #f3f2f1; color: var(--fg); }
button[disabled] { opacity: .5; cursor: not-allowed; }
form.inline { display: inline-block; margin: 0 .25rem .25rem 0; }
form.inline button { padding: .2rem .6rem; font-size: .85em; }

/* 차트 공통 */
.chart { display: block; width: 100%; height: auto; overflow: visible; }
.chart-row { display: flex; align-items: center; gap: 1.5rem; }
.chart-row .donut { width: 180px; flex: none; }
.axis-label, .value-label, .ref-label { font-size: 11px; fill: var(--muted); }
.value-label { fill: var(--fg); font-weight: 600; }
.ref-label { font-weight: 600; paint-order: stroke; stroke: #fff; stroke-width: 3px; stroke-linejoin: round; }
.gridline { stroke: var(--line); stroke-width: 1; }
.track { fill: #edebe9; }
.ref { stroke-width: 1.5; stroke-dasharray: 5 4; }
.legend { list-style: none; display: flex; flex-wrap: wrap; gap: .4rem 1rem; margin: .5rem 0 0; padding: 0; font-size: .85rem; color: var(--muted); }
.chart-row .legend { flex-direction: column; }
.legend b { color: var(--fg); margin-left: .25rem; }
.swatch { display: inline-block; width: .75rem; height: .75rem; border-radius: 2px; margin-right: .4rem; vertical-align: -1px; }
.fill-blue { fill: var(--c-blue); } .stroke-blue { stroke: var(--c-blue); } .bg-blue { background: var(--c-blue); }
.fill-green { fill: var(--c-green); } .stroke-green { stroke: var(--c-green); } .bg-green { background: var(--c-green); }
.fill-amber { fill: var(--c-amber); } .stroke-amber { stroke: var(--c-amber); } .bg-amber { background: var(--c-amber); }
.fill-red { fill: var(--c-red); } .stroke-red { stroke: var(--c-red); } .bg-red { background: var(--c-red); }
.fill-purple { fill: var(--c-purple); } .stroke-purple { stroke: var(--c-purple); } .bg-purple { background: var(--c-purple); }
.fill-neutral { fill: var(--c-neutral); } .stroke-neutral { stroke: var(--c-neutral); } .bg-neutral { background: var(--c-neutral); }
.fill-grey { fill: var(--c-grey); } .stroke-grey { stroke: var(--c-grey); } .bg-grey { background: var(--c-grey); }

/* 도넛·게이지·선 */
.donut-track { fill: none; stroke: #edebe9; stroke-width: 14; }
.arc { fill: none; stroke-width: 14; }
.donut-total { font-size: 24px; font-weight: 600; fill: var(--fg); }
.donut-caption { font-size: 11px; fill: var(--muted); }
.gauge-card .gauge { max-width: 220px; margin: .2rem auto 0; }
.gauge-track, .gauge-fill { fill: none; stroke-width: 12; stroke-linecap: round; }
.gauge-track { stroke: #edebe9; }
.gauge-value { font-size: 16px; font-weight: 600; fill: var(--fg); }
.line { fill: none; stroke-width: 2.5; stroke-linejoin: round; stroke-dasharray: 100 0; }
.dot { stroke: #fff; stroke-width: 1.5; }
.trigger-mark { font-size: 12px; fill: var(--c-red); }
.hbars { max-width: 480px; }
.counterfactuals .hbars { max-width: 360px; margin-top: .3rem; }

/* 정책 기준선 막대 */
.marker-row { display: grid; grid-template-columns: 3.5rem 1fr 7rem; align-items: center; gap: .6rem; margin: .4rem 0; }
.marker-code { font: 600 12px ui-monospace, monospace; }
.marker-values { color: var(--muted); font-size: .85rem; text-align: right; font-variant-numeric: tabular-nums; }
.marker-bar { height: 14px; }
.marker-bar .mark { stroke: var(--fg); stroke-width: 2; }

/* 100% 누적 막대 */
.stacked-row { display: grid; grid-template-columns: 8rem 1fr 3.5rem; align-items: center; gap: .6rem; margin: .35rem 0; }
.stacked-label { font-size: .9rem; }
.stacked-bar { height: 14px; border-radius: 2px; }
.stacked-total { color: var(--muted); font-size: .85rem; text-align: right; }
.progress { height: 6px; margin-top: .5rem; }
.axis-block h3 { font-size: .95rem; margin: .2rem 0 .5rem; }

/* 단계 표시기 */
.stepper { list-style: none; display: flex; margin: .4rem 0 .2rem; padding: 0; }
.step { position: relative; flex: 1 1 0; display: flex; flex-direction: column; align-items: center; text-align: center; gap: .15rem; padding: 0 .25rem; min-width: 0; }
.step::before { content: ""; position: absolute; top: 15px; left: -50%; width: 100%; height: 3px; background: var(--line); }
.step:first-child::before { display: none; }
.step-done::before, .step-warn::before, .step-current::before, .step-failed::before { background: var(--c-blue); }
.step-dot { position: relative; z-index: 1; display: flex; align-items: center; justify-content: center; width: 32px; height: 32px; border-radius: 50%; border: 2px solid var(--c-grey); background: #fff; color: var(--muted); font-weight: 600; }
.step-done .step-dot { background: var(--c-blue); border-color: var(--c-blue); color: #fff; }
.step-current .step-dot { border-color: var(--c-blue); color: var(--c-blue); box-shadow: 0 0 0 4px #deecf9; animation: pulse 1.6s ease-in-out infinite; }
.step-warn .step-dot { background: var(--c-amber); border-color: var(--c-amber); color: #fff; }
.step-failed .step-dot { background: var(--c-red); border-color: var(--c-red); color: #fff; }
.step-label { font-size: .85rem; font-weight: 600; }
.step-todo .step-label { color: var(--muted); font-weight: 400; }
.step-time, .step-elapsed, .step-note { font-size: .75rem; color: var(--muted); }
.step-elapsed { color: var(--accent-dark); }
.step-note { color: var(--warn); }
.stepper.compact { flex-wrap: wrap; gap: .4rem 0; margin: .2rem 0 .8rem; }
.stepper.compact .step { flex: 0 1 auto; flex-direction: row; text-align: left; gap: .4rem; padding: 0; }
.stepper.compact .step::before { display: none; }
.stepper.compact .step + .step { padding-left: 2.2rem; }
.stepper.compact .step + .step .step-dot::before { content: ""; position: absolute; right: calc(100% + .4rem); top: 50%; width: 1.4rem; height: 2px; background: var(--c-grey); }
.stepper.compact .step-done + .step .step-dot::before, .stepper.compact .step-warn + .step .step-dot::before { background: var(--c-blue); }
.stepper.compact .step-dot { width: 24px; height: 24px; font-size: .75rem; flex: none; }
.stepper.compact .step-note { color: var(--muted); }

/* 애니메이션(600ms 이하, 동작 줄이기 설정이면 끔) */
.bar-v { transform-box: fill-box; transform-origin: bottom; animation: grow-y .6s ease-out; }
.bar-h { transform-box: fill-box; transform-origin: left; animation: grow-x .6s ease-out; }
.arc { animation: arc-in .6s ease-out; }
.gauge-fill { animation: gauge-in .6s ease-out; }
.line { animation: draw .6s ease-out; }
@keyframes grow-y { from { transform: scaleY(0); } }
@keyframes grow-x { from { transform: scaleX(0); } }
@keyframes arc-in { from { stroke-dasharray: 0 1000; } }
@keyframes gauge-in { from { stroke-dasharray: 0 100; } }
@keyframes draw { from { stroke-dasharray: 0 100; } to { stroke-dasharray: 100 0; } }
@keyframes pulse { 50% { box-shadow: 0 0 0 7px #deecf9; } }
@media (prefers-reduced-motion: reduce) {
  .bar-v, .bar-h, .arc, .gauge-fill, .line, .step-current .step-dot { animation: none; }
}

/* 좁은 화면 */
@media (max-width: 1000px) {
  .grid-4 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .grid-2 { grid-template-columns: minmax(0, 1fr); }
}
@media (max-width: 900px) {
  .shell { grid-template-columns: 1fr; }
  .sidebar { position: static; height: auto; }
  .sidebar nav ul { display: flex; flex-wrap: wrap; }
  .who { display: none; }
  .grid-4 { grid-template-columns: minmax(0, 1fr); }
  .stepper { flex-direction: column; gap: .5rem; }
  .step { flex-direction: row; text-align: left; }
  .step::before { display: none; }
}
```

- [ ] **Step 4: `src/console/templates/base.html` 전체 교체**

```html
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="htmx-config" content='{"includeIndicatorStyles":false}'>
{% block head %}{% endblock %}
<title>{% block title %}AMA Lending 심사 콘솔{% endblock %}</title>
<link rel="stylesheet" href="/static/console.css">
<script src="{{ htmx_src }}" integrity="{{ htmx_sri }}" crossorigin="anonymous"></script>
</head>
<body>
<div class="shell">
<aside class="sidebar">
  <a class="brand" href="/">AMA Lending <small>심사 콘솔</small></a>
  <nav aria-label="주 메뉴">
    <ul>
      <li class="n-home{% if nav == 'home' %} active{% endif %}"><a href="/">신청</a></li>
      <li class="n-loans{% if nav == 'loans' %} active{% endif %}"><a href="/loans">사후 모니터링</a></li>
      <li class="n-grievances{% if nav == 'grievances' %} active{% endif %}"><a href="/grievances">이의제기</a></li>
      <li class="n-borrower{% if nav == 'borrower' %} active{% endif %}"><a href="/borrower">차주 시뮬레이터</a></li>
      <li class="n-ops{% if nav == 'ops' %} active{% endif %}"><a href="/ops">운영 지표</a></li>
    </ul>
  </nav>
  <div class="who">
    <span class="who-name">{{ user.name }}</span>
    <span class="who-roles">{{ user.roles|sort|join(", ") }}</span>
    {% if not demo %}<a href="/.auth/logout">로그아웃</a>{% endif %}
  </div>
</aside>
<div class="content">
{% if demo %}<p class="demo-banner">데모 모드: Entra 로그인이 꺼져 있습니다. 운영자 IP만 접속할 수 있습니다.</p>{% endif %}
<main>
{% block main %}{% endblock %}
</main>
</div>
</div>
</body>
</html>
```

- [ ] **Step 5: `src/console/templates/_charts.html` 작성**

```html
{# P7 차트·KPI·스테퍼 매크로. 계산은 console/charts.py·dashboard.py가 하고 여기서는 그리기만 한다.
   CSP(style-src 'self') 때문에 style 속성을 쓰지 않고 색은 fill-*/stroke-*/bg-* 클래스로 준다. #}

{% macro kpi(label, value, sub="", tone="blue") -%}
<div class="card kpi kpi-{{ tone }}">
  <div class="kpi-label">{{ label }}</div>
  <div class="kpi-value">{{ value }}</div>
  {% if sub %}<div class="kpi-sub">{{ sub }}</div>{% endif %}
  {% if caller is defined %}{{ caller() }}{% endif %}
</div>
{%- endmacro %}

{% macro legend(items) -%}
<ul class="legend">
{% for item in items %}<li><span class="swatch bg-{{ item[1] }}"></span>{{ item[0] }}{% if item|length > 2 %} <b>{{ item[2] }}</b>{% endif %}</li>{% endfor %}
</ul>
{%- endmacro %}

{% macro empty() -%}<p class="note">표시할 데이터가 없습니다.</p>{%- endmacro %}

{% macro donut(d, label, caption="건") -%}
{% if d.arcs %}
<svg class="chart donut" viewBox="0 0 120 120" role="img" aria-label="{{ label }}">
  <circle class="donut-track" cx="60" cy="60" r="{{ d.r }}"></circle>
  {% for a in d.arcs %}<circle class="arc stroke-{{ a.color }}" cx="60" cy="60" r="{{ d.r }}" stroke-dasharray="{{ a.dash }} {{ a.gap }}" stroke-dashoffset="{{ a.offset }}" transform="rotate(-90 60 60)"><title>{{ a.label }} {{ a.value }}{{ caption }} ({{ a.pct }}%)</title></circle>{% endfor %}
  <text class="donut-total" x="60" y="62" text-anchor="middle">{{ d.total }}</text>
  <text class="donut-caption" x="60" y="78" text-anchor="middle">{{ caption }}</text>
</svg>
{% else %}{{ empty() }}{% endif %}
{%- endmacro %}

{% macro hbars(h, label, unit="건") -%}
{% if h.bars %}
<svg class="chart hbars" viewBox="0 0 {{ h.width }} {{ h.height }}" role="img" aria-label="{{ label }}">
{% for b in h.bars %}
  <text class="axis-label" x="{{ b.x - 8 }}" y="{{ b.ty }}" text-anchor="end">{{ b.label }}</text>
  <rect class="track" x="{{ b.x }}" y="{{ b.y }}" width="{{ h.width - b.x - 48 }}" height="{{ b.h }}" rx="3"></rect>
  <rect class="bar-h fill-{{ b.color }}" x="{{ b.x }}" y="{{ b.y }}" width="{{ b.w }}" height="{{ b.h }}" rx="3"><title>{{ b.label }} {{ b.value }}{{ unit }}</title></rect>
  <text class="value-label" x="{{ b.vx }}" y="{{ b.ty }}">{{ b.value }}{{ unit }}</text>
{% endfor %}
</svg>
{% else %}{{ empty() }}{% endif %}
{%- endmacro %}

{% macro columns(c, label) -%}
<svg class="chart columns" viewBox="0 0 {{ c.width }} {{ c.height }}" role="img" aria-label="{{ label }}">
  {% for t in c.ticks %}<line class="gridline" x1="{{ c.x0 }}" x2="{{ c.x1 }}" y1="{{ t.y }}" y2="{{ t.y }}"></line><text class="axis-label" x="{{ c.x0 - 6 }}" y="{{ t.y + 4 }}" text-anchor="end">{{ t.text }}</text>{% endfor %}
  {% for g in c.groups %}
  {% for b in g.bars %}{% for p in b.parts %}<rect class="bar-v fill-{{ p.color }}" x="{{ b.x }}" y="{{ p.y }}" width="{{ b.w }}" height="{{ p.h }}"><title>{{ p.tip }}</title></rect>{% endfor %}{% endfor %}
  <text class="axis-label" x="{{ g.cx }}" y="{{ c.label_y }}" text-anchor="middle">{{ g.label }}</text>
  {% endfor %}
  {% for r in c.refs %}<line class="ref stroke-{{ r.color }}" x1="{{ c.x0 }}" x2="{{ c.x1 }}" y1="{{ r.y }}" y2="{{ r.y }}"><title>{{ r.label }} {{ r.text }}</title></line><text class="ref-label" x="{{ c.x1 if loop.index is odd else c.x0 + 4 }}" y="{{ r.y - 4 }}" text-anchor="{{ 'end' if loop.index is odd else 'start' }}">{{ r.label }} {{ r.text }}</text>{% endfor %}
  {% if caller is defined %}{{ caller() }}{% endif %}
</svg>
{%- endmacro %}

{% macro line_overlay(l, label) -%}
{% for d in l.segments %}<path class="line stroke-purple" d="{{ d }}" pathLength="100"></path>{% endfor %}
{% for p in l.points %}<circle class="dot fill-purple" cx="{{ p.x }}" cy="{{ p.y }}" r="3.5"><title>{{ label }} {{ p.value }}</title></circle>{% endfor %}
{% for t in l.ticks %}<text class="axis-label" x="{{ l.x + 6 }}" y="{{ t.y + 4 }}">{{ t.text }}</text>{% endfor %}
{%- endmacro %}

{% macro gauge(g, label, value_text, sub="", tone="blue") -%}
<div class="card kpi gauge-card kpi-{{ tone }}">
  <div class="kpi-label">{{ label }}</div>
  <svg class="chart gauge" viewBox="0 0 120 74" role="img" aria-label="{{ label }} {{ value_text }}">
    <path class="gauge-track" d="{{ g.d }}" pathLength="100"></path>
    {% if g.frac is not none %}<path class="gauge-fill stroke-{{ tone }}" d="{{ g.d }}" pathLength="100" stroke-dasharray="{{ g.pct }} 100"><title>{{ label }} {{ value_text }}</title></path>{% endif %}
    <text class="gauge-value" x="60" y="60" text-anchor="middle">{{ value_text }}</text>
  </svg>
  {% if sub %}<div class="kpi-sub">{{ sub }}</div>{% endif %}
</div>
{%- endmacro %}

{% macro marker_bar(m, label) -%}
<svg class="chart marker-bar" viewBox="0 0 100 14" preserveAspectRatio="none" role="img" aria-label="{{ label }}">
  <rect class="track" x="0" y="3" width="100" height="8"></rect>
  <rect class="bar-h fill-blue" x="0" y="3" width="{{ m.fill }}" height="8"><title>{{ label }}</title></rect>
  <line class="mark" x1="{{ m.mark }}" x2="{{ m.mark }}" y1="0" y2="14" vector-effect="non-scaling-stroke"></line>
</svg>
{%- endmacro %}

{% macro stacked(rows, label) -%}
{% if rows %}
<div class="stacked" aria-label="{{ label }}">
{% for r in rows %}
  <div class="stacked-row">
    <span class="stacked-label">{{ r.label }}</span>
    <svg class="chart stacked-bar" viewBox="0 0 100 10" preserveAspectRatio="none" role="img" aria-label="{{ r.label }}">
      {% if r.empty %}<rect class="track" x="0" y="0" width="100" height="10"></rect>{% endif %}
      {% for p in r.parts %}<rect class="bar-h fill-{{ p.color }}" x="{{ p.x }}" y="0" width="{{ p.w }}" height="10"><title>{{ r.label }} {{ p.part }} {{ p.value }}건 ({{ p.pct }}%)</title></rect>{% endfor %}
    </svg>
    <span class="stacked-total">{{ r.total|int }}건</span>
  </div>
{% endfor %}
</div>
{% else %}{{ empty() }}{% endif %}
{%- endmacro %}

{% macro stepper(steps, label, compact=false) -%}
<ol class="stepper{% if compact %} compact{% endif %}" aria-label="{{ label }}">
{% for s in steps %}
  <li class="step step-{{ s.state }}">
    <span class="step-dot">{% if s.state == "done" %}✓{% elif s.state == "failed" %}✕{% elif s.state == "warn" %}!{% else %}{{ loop.index }}{% endif %}</span>
    <span class="step-label">{{ s.label }}</span>
    {% if s.time %}<span class="step-time">{{ s.time }}</span>{% endif %}
    {% if s.elapsed %}<span class="step-elapsed">{{ s.elapsed }}</span>{% endif %}
    {% if s.note %}<span class="step-note">{{ s.note }}</span>{% endif %}
  </li>
{% endfor %}
</ol>
{%- endmacro %}

{% macro progress(pct, label) -%}
<svg class="chart progress" viewBox="0 0 100 6" preserveAspectRatio="none" role="img" aria-label="{{ label }}">
  <rect class="track" x="0" y="0" width="100" height="6"></rect>
  <rect class="bar-h fill-blue" x="0" y="0" width="{{ pct }}" height="6"></rect>
</svg>
{%- endmacro %}
```

- [ ] **Step 6: 템플릿마다 `{% extends "base.html" %}` 바로 다음 줄에 한 줄 추가**

| 템플릿 | 추가할 줄 |
|---|---|
| `list.html`, `detail.html`, `new.html`, `sent.html`, `pending.html` | `{% set nav = "home" %}` |
| `loans.html`, `loan.html`, `loan_sent.html` | `{% set nav = "loans" %}` |
| `grievances.html`, `grievance.html` | `{% set nav = "grievances" %}` |
| `borrower_index.html`, `borrower.html` | `{% set nav = "borrower" %}` |
| `ops.html` | `{% set nav = "ops" %}` |

`proof.html`, `privacy_sent.html`, `_rows.html`, `_consents.html`에는 넣지 않는다(강조할 메뉴 없음 또는 부분 템플릿).

- [ ] **Step 7: 통과 확인**

Run: `$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python -m pytest -q -p no:cacheprovider tests\console`
Expected: 모두 PASS.

- [ ] **Step 8: 커밋** — `feat(console): P7 사이드바 셸, 디자인 토큰 CSS, 차트 매크로`

---

### Task 5: 홈 `/` — KPI, 상태 도넛, 권고 막대

**Files:**
- Rewrite: `src/console/templates/list.html`, `src/console/templates/_rows.html`
- Modify: `src/console/app.py`(`index` 라우트, dashboard import)
- Test: `tests/console/test_ui.py`(덧붙임)

- [ ] **Step 1: 실패하는 테스트를 `tests/console/test_ui.py` 끝에 덧붙인다**

```python


def test_home_has_kpis_donut_and_scope_note(client):
    html = page(client, "/")
    assert html.count('class="card kpi ') == 4
    assert 'class="chart donut"' in html and 'class="chart hbars"' in html
    assert "최근 50건 기준" in html
    assert ">새 신청<" in html
    assert 'hx-get="/applications/rows"' in html
```

- [ ] **Step 2: 실패 확인** — Run: `... -m pytest -q -p no:cacheprovider tests\console\test_ui.py -k home` / Expected: FAIL(`class="card kpi "` 0개).

- [ ] **Step 3: `src/console/app.py` 수정**

import 블록의 `from console.auth import ...` 다음 줄에 추가(Task 6~8에서 쓰는 함수도 한 번에 import한다):

```python
from console.dashboard import detail_summary, home_summary, loan_summary, loans_summary, ops_summary
```

`index` 라우트를 바꾼다:

```python
    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        user = viewer(request)
        rows = deps.store.list_recent(LIST_LIMIT)
        return render("list.html", user, rows=rows, ui=home_summary(rows), limit=LIST_LIMIT)
```

`/applications/rows`(htmx 부분 갱신) 라우트는 그대로 둔다.

- [ ] **Step 4: `src/console/templates/list.html` 전체 교체**

```html
{% extends "base.html" %}
{% set nav = "home" %}
{% import "_charts.html" as ch %}
{% block main %}
<div class="page-head">
  <div>
    <p class="crumb">홈 › 신청</p>
    <h1>신청 대시보드</h1>
  </div>
  <div class="actions">{% if user.can_decide %}<a class="button" href="/applications/new">새 신청</a>{% endif %}</div>
</div>
<p class="note">최근 {{ limit }}건 기준 집계입니다. 전체 신청 통계가 아닙니다.</p>
<div class="grid-4">
  {{ ch.kpi("최근 신청", ui.total ~ "건", "최근 " ~ limit ~ "건 기준", "blue") }}
  {{ ch.kpi("심사역 대기", ui.awaiting ~ "건", "SLA 초과 " ~ ui.escalated ~ "건", "amber" if ui.awaiting else "neutral") }}
  {{ ch.kpi("진행 중", ui.in_progress ~ "건", "에이전트 파이프라인", "blue") }}
  {{ ch.kpi("승인율", "-" if ui.approval_rate is none else "%.0f%%"|format(ui.approval_rate * 100),
            "승인 " ~ ui.approved ~ " · 거절 " ~ ui.declined, "green") }}
</div>
<div class="grid-2">
  <section class="card">
    <h2>상태 분포</h2>
    <div class="chart-row">
      {{ ch.donut(ui.donut, "상태 분포") }}
      {{ ch.legend(ui.groups) }}
    </div>
  </section>
  <section class="card">
    <h2>에이전트 권고</h2>
    {{ ch.hbars(ui.rec_bars, "에이전트 권고 분포") }}
  </section>
</div>
<section class="card">
  <h2>신청 목록</h2>
  <table>
    <thead><tr><th>신청 ID</th><th>신청인</th><th>상태</th><th>신청 금액</th><th>에이전트 권고</th><th>갱신 시각</th></tr></thead>
    <tbody hx-get="/applications/rows" hx-trigger="every 15s" hx-swap="innerHTML">
{% include "_rows.html" %}
    </tbody>
  </table>
</section>
{% endblock %}
```

- [ ] **Step 5: `src/console/templates/_rows.html` 전체 교체**

```html
{% for r in rows %}
<tr>
  <td><a href="/applications/{{ r.id|path }}">{{ r.id }}</a></td>
  <td>{{ r.applicant_id }}</td>
  <td><span class="status s-{{ r.status }}">{{ r.status|status_label }}</span></td>
  <td class="num">{{ r.requested_amount_krw|krw }}</td>
  <td>{% if r.recommendation %}<span class="pill rec-{{ r.recommendation }}">{{ r.recommendation }}</span>{% else %}-{% endif %}</td>
  <td>{{ r.updated_at or "" }}</td>
</tr>
{% else %}
<tr><td colspan="6">신청이 없습니다.</td></tr>
{% endfor %}
```

- [ ] **Step 6: 통과 확인** — Run: `... -m pytest -q -p no:cacheprovider tests\console` / Expected: 모두 PASS.

- [ ] **Step 7: 커밋** — `feat(console): P7 홈 대시보드(KPI·상태 도넛·권고 막대)`

---

### Task 6: 신청 상세 — 7단계 타임라인, 현금흐름·정책 차트

**Files:**
- Rewrite: `src/console/templates/detail.html`
- Modify: `src/console/app.py`(`detail` 렌더 함수)
- Test: `tests/console/test_ui.py`(덧붙임)

- [ ] **Step 1: 실패하는 테스트를 `tests/console/test_ui.py` 끝에 덧붙인다**

```python


def test_detail_has_timeline_stepper_and_charts(client):
    html = page(client, "/applications/app-20261103-p01")
    assert '<ol class="stepper"' in html and html.count('class="step step-') >= 7
    assert 'class="chart columns"' in html and 'class="chart marker-bar"' in html
    for text in ("타임라인", "정책 판정", "에이전트 평가", "주장과 근거", "제안 구조", "개선 조건(반사실)", "심사역 결정", "결정 보내기"):
        assert text in html, text
    assert "<details" in html and 'class="timeline"' in html


def test_withdrawn_detail_marks_failed_step(client):
    html = page(client, "/applications/app-20261103-p06")
    assert "step-failed" in html and "철회 종료" in html
```

- [ ] **Step 2: 실패 확인** — Run: `... tests\console\test_ui.py -k detail` / Expected: FAIL(`<ol class="stepper"` 없음).

- [ ] **Step 3: `src/console/app.py`의 상세 렌더 호출을 바꾼다**

```python
        return render("detail.html", user, status_code, v=view, ui=detail_summary(view), in_progress=in_progress,
                      consents=consents, **ctx)
```

(바꾸기 전: `return render("detail.html", user, status_code, v=view, in_progress=in_progress, consents=consents, **ctx)`)

- [ ] **Step 4: `src/console/templates/detail.html` 전체 교체**

```html
{% extends "base.html" %}
{% set nav = "home" %}
{% import "_charts.html" as ch %}
{% set d = v.doc %}
{% block head %}{% if in_progress %}<meta http-equiv="refresh" content="10">{% endif %}{% endblock %}
{% block title %}{{ d.application_id }} · AMA Lending 심사 콘솔{% endblock %}
{% block main %}
<div class="page-head">
  <div>
    <p class="crumb"><a href="/">홈 › 신청</a> › {{ d.application_id }}</p>
    <h1>{{ d.application_id }} <span class="status s-{{ d.status }}">{{ d.status|status_label }}</span>
      {% if d.sla %}<span class="badge {{ 'badge-red' if d.sla.escalated else 'badge-neutral' }}">SLA {{ "초과" if d.sla.escalated else "마감 " ~ d.sla.deadline }}</span>{% endif %}</h1>
  </div>
  <div class="actions">{% if d.monitoring %}<a class="button secondary" href="/loans/{{ d.application_id|path }}">사후 모니터링</a>{% endif %}</div>
</div>
<section class="card">
<h2>타임라인</h2>
{{ ch.stepper(ui.steps, "신청 진행 단계") }}
<details><summary>상태 기록 {{ (d.timeline or [])|length }}건</summary>
<ol class="timeline">{% for t in d.timeline %}<li><span class="status s-{{ t.status }}">{{ t.status|status_label }}</span> {{ t.at }}</li>{% endfor %}</ol>
</details>
</section>
<section class="card">
<dl class="facts">
  <dt>신청인</dt><dd>{{ d.applicant_id }}</dd>
  <dt>신청 금액</dt><dd>{{ d.requested_amount_krw|krw }} / {{ d.requested_tenor_months }}개월</dd>
  <dt>접수</dt><dd>{{ d.received_at }}</dd>
  {% if d.sla %}<dt>SLA 마감</dt><dd>{{ d.sla.deadline }}{% if d.sla.escalated %} (초과){% endif %}</dd>{% endif %}
  {% if d.flags %}<dt>플래그</dt><dd>{% for f in d.flags %}<span class="chip warn">{{ f }}</span>{% endfor %}</dd>{% endif %}
  {% if d.withdrawal %}<dt>동의 철회</dt><dd>{{ d.withdrawal.consent_id }} · {{ d.withdrawal.requested_by }} · {{ d.withdrawal.at }}</dd>{% endif %}
  {% if d.error %}<dt>오류</dt><dd class="error">{{ d.error.stage }}: {{ d.error.message }}</dd>{% endif %}
</dl>
</section>
{% if v.pii == "restored" %}<p class="pii restored">실명 복원 표시 중(CreditOfficer·CreditManager 권한).</p>
{% elif v.pii == "unavailable" %}<p class="pii">원천 데이터가 없어 가명 토큰을 복원하지 못했습니다.</p>
{% else %}<p class="pii">가명 표시 중(감사자 권한은 실명을 볼 수 없습니다).</p>{% endif %}

{% if consents is not none %}{% include "_consents.html" %}{% endif %}

{% if ui.monthly or v.policy %}
<div class="grid-2">
{% if ui.monthly %}
<section class="card">
<h2>월별 현금흐름</h2>
{{ ch.columns(ui.monthly, "월별 매출과 지출") }}
{{ ch.legend(ui.monthly_legend) }}
</section>
{% endif %}
{% if v.policy %}
<section class="card">
<h2>정책 판정 <small>{{ v.policy.rules_version }}</small></h2>
<p>판정 <strong>{{ v.policy.decision }}</strong> · 한도 {{ v.policy.max_amount_krw|krw }} · 금리 구간 {{ v.policy.rate_band or "-" }}</p>
{% for m in ui.markers %}
<div class="marker-row">
  <span class="marker-code">{{ m.code }}</span>
  {{ ch.marker_bar(m.bar, m.code ~ " 관측 " ~ m.observed ~ " / 기준 " ~ m.threshold) }}
  <span class="marker-values">{{ m.observed }} / {{ m.threshold }}</span>
</div>
{% endfor %}
<table>
  <thead><tr><th>코드</th><th>내용</th><th>관측값</th><th>기준</th></tr></thead>
  <tbody>{% for r in v.policy.reason_codes %}<tr><td>{{ r.code }}</td><td>{{ r.message }}</td><td>{{ r.observed }}</td><td>{{ r.threshold }}</td></tr>{% endfor %}</tbody>
</table>
</section>
{% endif %}
</div>
{% endif %}

{% if v.features %}
<section class="card">
<h2>현금흐름 지표</h2>
<table class="kv">{% for k, val in v.features|dictsort %}<tr><th>M:{{ k }}</th><td>{{ val }}</td></tr>{% endfor %}</table>
</section>
{% endif %}

{% if v.assessment %}
{% set a = v.assessment %}
<section class="card">
<h2>에이전트 평가 <small>권고: {{ a.recommendation }}</small></h2>
{% if ui.review %}{{ ch.stepper(ui.review, "에이전트 검토 단계", compact=true) }}{% endif %}
<p class="summary">{{ a.summary }}</p>
<h3>주장과 근거</h3>
<ul class="claims">
{% for c in v.claims %}
  <li>{{ c.text }}
    <div>{% for id, val in c.evidence %}<span class="chip{% if val is none %} warn{% endif %}">{{ id }} = {{ val if val is not none else "없음" }}</span>{% endfor %}
    {% for t in c.txn_ids %}<span class="chip txn">{{ t }}</span>{% endfor %}</div>
  </li>
{% endfor %}
</ul>
{% if a.proposed_structure %}{% set s = a.proposed_structure %}
<h3>제안 구조</h3>
<p>{{ s.amount_krw|krw }} · {{ s.tenor_months }}개월 · {{ s.schedule }}{% if s.reduced_payment_months %} · 감액 월 {{ s.reduced_payment_months|join(", ") }}{% endif %}</p>
<p>{{ s.rationale }}</p>
{% endif %}
{% if a.counterfactuals %}
<h3>개선 조건(반사실)</h3>
<ul class="counterfactuals">{% for cf in ui.counterfactuals %}<li>{{ cf.metric_id }}: {{ cf.current }} → {{ cf.target }} — {{ cf.explanation }}
  {% if cf.bar %}{{ ch.hbars(cf.bar, cf.metric_id ~ " 현재와 목표", "") }}{% endif %}</li>{% endfor %}</ul>
{% endif %}
{% if a.review %}<p class="review">검토 에이전트: {{ "통과" if a.review.passed else "미통과" }} ({{ a.review.rounds }}회){% for i in a.review.issues %} · {{ i }}{% endfor %}</p>{% endif %}
</section>
{% endif %}

{% if v.transactions %}
<section class="card">
<h2>근거 거래</h2>
<table>
  <thead><tr><th>거래 ID</th><th>일자</th><th>금액</th><th>적요</th></tr></thead>
  <tbody>{% for t in v.transactions %}<tr><td>{{ t.txn_id }}</td><td>{{ t.booked_on }}</td><td>{{ t.amount_krw|krw }}</td><td>{{ t.description }}</td></tr>{% endfor %}</tbody>
</table>
</section>
{% endif %}

<section class="card">
<h2>심사역 결정</h2>
{% if d.officer_decision %}
<p>{{ d.officer_decision.decision }} · {{ d.officer_decision.amount_krw|krw }} · {{ d.officer_decision.officer_id }}</p>
{% if d.officer_decision.conditions %}<ul>{% for c in d.officer_decision.conditions %}<li>{{ c }}</li>{% endfor %}</ul>{% endif %}
{% if d.officer_decision.reason %}<p>{{ d.officer_decision.reason }}</p>{% endif %}
{% endif %}
{% if d.decision_attempts %}
<details><summary>거부된 결정 시도 {{ d.decision_attempts|length }}건</summary>
<ul>{% for at in d.decision_attempts %}<li>{{ at.at }} {{ at.error }}</li>{% endfor %}</ul></details>
{% endif %}
{% if v.decidable and user.can_decide %}
{% if error %}<p class="error" role="alert">{{ error }}{% if error_code %} ({{ error_code }}){% endif %}</p>{% endif %}
<form method="post" action="/applications/{{ d.application_id|path }}/decision" class="decision">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <fieldset>
    <legend>결정</legend>
    {% set chosen = form.decision if form else "approve" %}
    {% for value, label in (("approve", "승인"), ("conditional", "조건부 승인"), ("decline", "거절")) %}
    <label><input type="radio" name="decision" value="{{ value }}"{% if chosen == value %} checked{% endif %}> {{ label }}</label>
    {% endfor %}
  </fieldset>
  <label>승인 금액(원, 한도 {{ v.max_amount_krw|krw }})
    <input name="amount_krw" inputmode="numeric" value="{{ form.amount_krw if form else ((v.assessment.proposed_structure.amount_krw if v.assessment and v.assessment.proposed_structure) or v.max_amount_krw or '') }}"></label>
  <label>조건(한 줄에 하나) <textarea name="conditions" rows="3">{{ form.conditions if form else "" }}</textarea></label>
  <label>사유 <textarea name="reason" rows="3">{{ form.reason if form else "" }}</textarea></label>
  <button type="submit">결정 보내기</button>
</form>
{% elif not d.officer_decision %}
<p>{% if v.decidable %}감사자 권한은 결정할 수 없습니다.{% else %}심사역 결정 단계가 아닙니다.{% endif %}</p>
{% endif %}
</section>
{% endblock %}
```

- [ ] **Step 5: 통과 확인** — Run: `... tests\console` / Expected: 모두 PASS.

- [ ] **Step 6: 커밋** — `feat(console): P7 신청 상세(7단계 타임라인·월별 현금흐름·정책 마커)`

---

### Task 7: 사후 모니터링 목록·상세 — KPI, 주간 차트, 경보 스테퍼

**Files:**
- Rewrite: `src/console/templates/loans.html`, `src/console/templates/loan.html`
- Modify: `src/console/app.py`(`loan_page` 렌더 함수, `loans` 라우트)
- Test: `tests/console/test_ui.py`(덧붙임)

- [ ] **Step 1: 실패하는 테스트를 `tests/console/test_ui.py` 끝에 덧붙인다**

```python


def test_loans_list_has_kpis(client):
    html = page(client, "/loans")
    assert html.count('class="card kpi ') == 4
    assert "13주 완주" in html


def test_loan_has_chart_triggers_and_alert_steppers(client):
    html = page(client, "/loans/app-20261103-p03")
    assert html.count('class="card kpi ') == 4
    assert 'class="chart columns"' in html and 'class="line ' in html and 'class="trigger-mark"' in html
    for text in ("주간 흐름과 탐지기", "경보 6주차", "경보 10주차", "사후 13주 재생", "결정 보내기"):
        assert text in html, text
    assert 'class="stepper compact"' in html
    table = html.split("주간 흐름과 탐지기", 1)[1]
    assert "<details" not in table.split("</table>", 1)[0]
```

- [ ] **Step 2: 실패 확인** — Run: `... tests\console\test_ui.py -k loan` / Expected: FAIL.

- [ ] **Step 3: `src/console/app.py` 수정**

`loan_page`의 렌더 호출:

```python
        return render("loan.html", user, status_code, v=view, ui=loan_summary(view), replay_blocked=blocked, **ctx)
```

`loans` 라우트:

```python
    @app.get("/loans", response_class=HTMLResponse)
    def loans(request: Request) -> HTMLResponse:
        user = viewer(request)
        rows = monitoring_store().list_loans(LIST_LIMIT)
        return render("loans.html", user, rows=rows, ui=loans_summary(rows), limit=LIST_LIMIT)
```

- [ ] **Step 4: `src/console/templates/loans.html` 전체 교체**

```html
{% extends "base.html" %}
{% set nav = "loans" %}
{% import "_charts.html" as ch %}
{% block title %}사후 모니터링 · AMA Lending 심사 콘솔{% endblock %}
{% block main %}
<div class="page-head">
  <div>
    <p class="crumb">홈 › 사후 모니터링</p>
    <h1>사후 모니터링</h1>
  </div>
</div>
<p class="note">승인된 대출의 사후 13주 거래 흐름을 재생해 조기경보(Early-Warning)와 추심(Collections) 에이전트를 시연합니다. 최근 {{ limit }}건 기준입니다.</p>
<div class="grid-4">
  {{ ch.kpi("모니터링 대출", ui.total ~ "건", "최근 " ~ limit ~ "건 기준", "blue") }}
  {{ ch.kpi("진행 중 재생", ui.replaying ~ "건", "10초마다 갱신", "blue" if ui.replaying else "neutral") }}
  {{ ch.kpi("13주 완주", ui.completed ~ "건", "미실행 " ~ ui.not_started ~ "건", "green") }}
  {{ ch.kpi("누적 경보", ui.alerts ~ "건", "조기경보 에이전트 발행", "red" if ui.alerts else "neutral") }}
</div>
<section class="card">
<h2>대출 목록</h2>
<table>
  <thead><tr><th>대출 ID</th><th>신청인</th><th>업종</th><th>승인 금액</th><th>모니터링</th><th>경보</th><th>갱신 시각</th></tr></thead>
  <tbody>
{% for r in rows %}
<tr>
  <td><a href="/loans/{{ r.id|path }}">{{ r.id }}</a></td>
  <td>{{ r.applicant_id }}</td>
  <td>{{ r.industry }}</td>
  <td class="num">{{ r.amount_krw|krw }} / {{ r.tenor_months }}개월</td>
  <td><span class="status m-{{ r.monitoring_status or 'none' }}">{{ r.monitoring_status|monitoring_label }}</span></td>
  <td>{% if r.alerts %}<span class="badge badge-red">{{ r.alerts }}</span>{% else %}{{ r.alerts if r.alerts is not none else "-" }}{% endif %}</td>
  <td>{{ r.updated_at or "" }}</td>
</tr>
{% else %}
<tr><td colspan="7">승인된 대출이 없습니다. 신청을 승인하면 모니터링 대상이 됩니다.</td></tr>
{% endfor %}
  </tbody>
</table>
</section>
{% endblock %}
```

- [ ] **Step 5: `src/console/templates/loan.html` 전체 교체**

```html
{% extends "base.html" %}
{% set nav = "loans" %}
{% import "_charts.html" as ch %}
{% set l = v.loan %}{% set m = v.monitoring %}
{% block head %}{% if replay_blocked %}<meta http-equiv="refresh" content="10">{% endif %}{% endblock %}
{% block title %}{{ l.id }} 사후 모니터링 · AMA Lending 심사 콘솔{% endblock %}
{% block main %}
<div class="page-head">
  <div>
    <p class="crumb"><a href="/loans">홈 › 사후 모니터링</a> › {{ l.id }}</p>
    <h1>{{ l.id }} <span class="status m-{{ m.status or 'none' }}">{{ m.status|monitoring_label }}</span></h1>
  </div>
  <div class="actions">
    <a class="button secondary" href="/applications/{{ l.application_id|path }}">신청 상세</a>
    {% if user.can_decide %}
    <form method="post" action="/loans/{{ l.id|path }}/replay">
      <input type="hidden" name="csrf" value="{{ csrf }}">
      <button type="submit"{% if replay_blocked %} disabled{% endif %}>사후 13주 재생{% if m.replay_id %}(다시){% endif %}</button>
    </form>
    {% endif %}
  </div>
</div>
{% if error %}<p class="error" role="alert">{{ error }}</p>{% endif %}
{% if replay_blocked %}<p class="note">재생이 진행 중입니다. 10초마다 새로 고칩니다.</p>{% endif %}
<div class="grid-4">
  {{ ch.kpi("대출", l.amount_krw|krw, l.tenor_months ~ "개월 · " ~ l.decision, "neutral") }}
  {% call ch.kpi("재생 진행", ui.processed ~ "/" ~ ui.weeks_total ~ "주", m.mode or "재생 전", "blue") %}{{ ch.progress(ui.progress_pct, "재생 진행률") }}{% endcall %}
  {{ ch.kpi("경보", ui.alerts ~ "건", "높음 " ~ ui.severity.high ~ " · 중간 " ~ ui.severity.medium ~ " · 낮음 " ~ ui.severity.low, "red" if ui.severity.high else ("amber" if ui.alerts else "neutral")) }}
  {{ ch.kpi("최저 현금 버퍼", "-" if ui.min_buffer is none else ui.min_buffer ~ "일", ("" if ui.min_buffer_week is none else ui.min_buffer_week ~ "주차 · ") ~ "기준 " ~ ("-" if ui.baseline_buffer is none else ui.baseline_buffer ~ "일"), "purple") }}
</div>
<section class="card">
<dl class="facts">
  <dt>신청인</dt><dd>{{ l.applicant_id }} · {{ l.industry }}</dd>
  <dt>대출</dt><dd>{{ l.amount_krw|krw }} / {{ l.tenor_months }}개월 · {{ l.decision }} · {{ l.approved_at }}</dd>
  <dt>기준선</dt><dd>주 평균 매출 {{ l.baseline.avg_weekly_revenue|krw }} · 현금 버퍼 {{ l.baseline.cash_buffer_days }}일</dd>
  {% if m.replay_id %}<dt>재생</dt><dd>{{ m.replay_id }} · {{ m.mode }} · {{ m.processed_week }}/13주 · 시작 {{ m.started_at }}</dd>{% endif %}
  {% if m.error %}<dt>오류</dt><dd class="error">{{ m.error }}</dd>{% endif %}
</dl>
</section>
{% if v.pii == "restored" %}<p class="pii restored">실명 복원 표시 중(CreditOfficer·CreditManager 권한).</p>
{% elif v.pii == "unavailable" %}<p class="pii">원천 데이터가 없어 가명 토큰을 복원하지 못했습니다.</p>
{% else %}<p class="pii">가명 표시 중.</p>{% endif %}

{% if v.weeks %}
<section class="card">
<h2>주간 흐름과 탐지기</h2>
{% if ui.chart %}
{% call ch.columns(ui.chart, "주간 매출과 현금 버퍼") %}
{{ ch.line_overlay(ui.line, "현금 버퍼(일)") }}
{% for mk in ui.markers %}<text class="trigger-mark" x="{{ mk.x }}" y="{{ mk.y }}" text-anchor="middle">▲<title>{{ mk.tip }}</title></text>{% endfor %}
{% endcall %}
{{ ch.legend(ui.legend) }}
{% endif %}
<table>
  <thead><tr><th>주차</th><th>기간</th><th>매출(W:n:revenue)</th><th>기준 대비</th><th>버퍼(일)</th><th>고금리 거래</th><th>트리거</th><th>조사</th></tr></thead>
  <tbody>
{% for w in v.weeks %}{% set r = w.row %}
<tr>
  <td>{{ w.week }}</td><td>{{ r.start }} ~ {{ r.end }}</td><td>{{ r.revenue|krw }}</td><td>{{ r.revenue_ratio }}</td>
  <td>{{ r.buffer_days }}</td><td>{{ r.high_cost_count }}</td>
  <td>{% for t in w.triggers %}<span class="chip{% if t in w.new_triggers %} warn{% endif %}">{{ t|trigger_label }}</span>{% endfor %}</td>
  <td>{% if w.investigation %}{{ w.investigation.decision }}{% if w.alert_id %} · <a href="#{{ w.alert_id }}">경보</a>{% endif %}{% else %}-{% endif %}</td>
</tr>
{% endfor %}
  </tbody>
</table>
<p><small>노란 트리거는 이번 재생에서 처음 걸린 것으로, 이때만 조기경보 에이전트가 조사합니다.</small></p>
</section>
{% endif %}

{% for a in v.alerts %}
<section id="{{ a.alert_id }}" class="card alert-card alert-{{ a.severity }}">
<h2>경보 {{ a.week }}주차 <span class="status sev-{{ a.severity }}">{{ a.severity|severity_label }}</span> <small>{{ a.recommended_action|action_label }} · {{ a.status }}</small></h2>
{% if ui.alert_steps[a.alert_id] %}{{ ch.stepper(ui.alert_steps[a.alert_id], "경보 처리 단계", compact=true) }}{% endif %}
<p class="summary">{{ a.summary }}</p>
<ul class="claims">
{% for s in a.signals %}
  <li><strong>{{ s.kind }}</strong> {{ s.text }}
    <div>{% for id, val in s.evidence %}<span class="chip{% if val is none %} warn{% endif %}">{{ id }} = {{ val if val is not none else "없음" }}</span>{% endfor %}
    {% for t in s.txn_ids %}<span class="chip txn">{{ t }}</span>{% endfor %}</div>
  </li>
{% endfor %}
</ul>
<p class="review">조사 모델: {% for role, model in a.models|dictsort %}{{ role }}={{ model }} {% endfor %}{% if a.event_error %} · <span class="error">이벤트 발행 실패: {{ a.event_error }}</span>{% endif %}</p>
{% if a.collections %}{% set c = a.collections %}
<h3>추심 에이전트 <small>{{ c.decision }}</small></h3>
<p>{{ c.reason }}</p>
{% if c.contacts %}<table>
  <thead><tr><th>일자</th><th>스크립트</th><th>상태</th><th>사유</th></tr></thead>
  <tbody>{% for x in c.contacts %}<tr><td>{{ x.at }}</td><td>{{ x.script_id }}</td><td>{{ x.status }}</td><td>{{ x.reason or "" }}</td></tr>{% endfor %}</tbody>
</table>{% endif %}
{% elif a.status == "open" %}<p>추심 에이전트 처리 대기 중.</p>{% endif %}
{% if a.restructure %}{% set rs = a.restructure %}
<h3>재조정 상담 요청 <small>{{ rs.status }}</small></h3>
<p>{{ rs.category }} · {{ rs.note }}</p>
{% if rs.officer_id %}<p>{{ rs.officer_id }} · {{ rs.decided_at }} · {{ rs.reason }}</p>{% endif %}
{% if a.decidable and user.can_decide and replay_blocked %}
<p><small>재생이 끝나면 재조정 결정을 내릴 수 있습니다.</small></p>
{% elif a.decidable and user.can_decide %}
<form method="post" action="/loans/{{ l.id|path }}/alerts/{{ a.alert_id|path }}/restructure" class="decision">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <fieldset><legend>재조정 결정</legend>
    <label><input type="radio" name="decision" value="approve" checked> 승인</label>
    <label><input type="radio" name="decision" value="decline"> 거절</label>
  </fieldset>
  <label>사유 <textarea name="reason" rows="2"></textarea></label>
  <button type="submit">결정 보내기</button>
</form>
{% endif %}
{% endif %}
</section>
{% endfor %}

{% if v.transactions %}
<section class="card">
<h2>근거 거래</h2>
<table>
  <thead><tr><th>거래 ID</th><th>일자</th><th>금액</th><th>적요</th></tr></thead>
  <tbody>{% for t in v.transactions %}<tr><td>{{ t.txn_id }}</td><td>{{ t.booked_on }}</td><td>{{ t.amount_krw|krw }}</td><td>{{ t.description }}</td></tr>{% endfor %}</tbody>
</table>
</section>
{% endif %}
{% endblock %}
```

- [ ] **Step 6: 통과 확인** — Run: `... tests\console` / Expected: 모두 PASS(특히 `test_monitoring.py`의 재생 버튼 비활성·경보 앵커·재조정 폼).

- [ ] **Step 7: 커밋** — `feat(console): P7 사후 모니터링(KPI·주간 매출·버퍼 차트·경보 스테퍼)`

---

### Task 8: 운영 지표 — 게이지, 단계별 누적 막대, 공정성 막대

**Files:**
- Rewrite: `src/console/templates/ops.html`
- Modify: `src/console/app.py`(`ops_page` 렌더 호출)
- Test: `tests/console/test_ui.py`(덧붙임)

- [ ] **Step 1: 실패하는 테스트를 `tests/console/test_ui.py` 끝에 덧붙인다**

```python


def test_ops_has_gauges_stacked_and_fairness_columns(client):
    html = page(client, "/ops")
    assert html.count('class="chart gauge"') == 2
    assert 'class="chart stacked-bar"' in html
    assert html.count('class="chart columns"') >= 2
    assert 'class="ops-alert"' in html
```

- [ ] **Step 2: 실패 확인** — Run: `... tests\console\test_ui.py -k ops` / Expected: FAIL.

- [ ] **Step 3: `src/console/app.py`의 `ops_page` 렌더 호출**

```python
        return render("ops.html", user, runs=runs, selected=selected, s=summary,
                      ui=ops_summary(summary, STEP_LABELS) if summary else None,
                      AXIS_LABELS=AXIS_LABELS, STEP_LABELS=STEP_LABELS)
```

- [ ] **Step 4: `src/console/templates/ops.html` 전체 교체**

```html
{% extends "base.html" %}
{% set nav = "ops" %}
{% import "_charts.html" as ch %}
{% block title %}운영 지표 · AMA Lending 심사 콘솔{% endblock %}
{% block main %}
<div class="page-head">
  <div>
    <p class="crumb">홈 › 운영 지표</p>
    <h1>운영 지표 <small>합성 데이터 기준</small></h1>
  </div>
  {% if selected %}
  <div class="actions">
    <form method="get" action="/ops">
      <label>측정 실행
        <select name="run">
        {% for r in runs %}<option value="{{ r }}"{% if r == selected %} selected{% endif %}>{{ r }}</option>{% endfor %}
        </select>
      </label>
      <button type="submit">보기</button>
    </form>
  </div>
  {% endif %}
</div>
{% if not selected %}
<p>측정 실행이 없습니다. 운영자 PC에서 <code>python -m ops load --count 24</code>로 합성 신청을 제출하면 여기에 표시됩니다.</p>
{% else %}
<p class="note">실행 <code>{{ selected }}</code> · 신청 {{ s.applications }}건 · {{ s.basis }}. 고정비·건당 총비용은 <code>ops/reports/{{ selected }}/report.md</code>를 봅니다.</p>

{% for axis in s.fairness.alerts %}
<p class="ops-alert">공정성 경보: {{ AXIS_LABELS.get(axis, axis) }} 추천 approve율 격차 {{ s.fairness.axes[axis].gap_pp|num }}%p &gt; 임계값 {{ s.fairness.threshold_pp|num }}%p. 원형 통제 최대 격차 {{ s.fairness.axes[axis].controlled.max_pp|num }}%p를 함께 확인하세요.</p>
{% endfor %}

<div class="grid-4">
  {{ ch.gauge(ui.latency, "접수 → 심사역 대기 p95", s.latency.p95_min|num ~ "분", "목표 ≤ " ~ s.latency.target_min|num(0) ~ "분 · " ~ ("충족" if s.latency.met else "미충족"), ui.latency_tone) }}
  {{ ch.gauge(ui.success, "에이전트 단계 성공률", s.agent_success.rate|pct, "목표 " ~ s.agent_success.target|pct, ui.success_tone) }}
  {{ ch.kpi("측정 표본", s.latency.n ~ "건", "표본 부족(20건 미만)" if s.latency.insufficient else "p50 " ~ s.latency.p50_min|num ~ "분", "neutral" if s.latency.insufficient else "blue") }}
  {{ ch.kpi("AI 비용(건당)", s.ai_cost.per_application_usd|usd, "캐시 할인 미반영", "purple") }}
</div>

<section class="card">
<h2>SLO</h2>
<details><summary>SLO 상세</summary>
<table class="kv">
  <tr><th>접수 → 심사역 대기 p50 / p95 / 최대(분)</th><td>{{ s.latency.p50_min|num }} / {{ s.latency.p95_min|num }} / {{ s.latency.max_min|num }} (목표 p95 ≤ {{ s.latency.target_min|num(0) }}분, {{ "충족" if s.latency.met else "미충족" }})</td></tr>
  <tr><th>측정 표본</th><td>{{ s.latency.n }}건{% if s.latency.insufficient %} · 표본 부족(20건 미만){% endif %}{% if s.latency.unreached %} · 미도달 {{ s.latency.unreached }}건{% endif %}{% if s.latency.received_only %} · 접수 시각 기준 {{ s.latency.received_only }}건{% endif %}</td></tr>
  <tr><th>에이전트 단계 성공률</th><td>{{ s.agent_success.rate|pct }} (목표 {{ s.agent_success.target|pct }}, ok {{ s.agent_success.ok }} · degraded {{ s.agent_success.degraded }} · failed {{ s.agent_success.failed }})</td></tr>
  <tr><th>AI 비용(건당, 캐시 할인 미반영)</th><td>{{ s.ai_cost.per_application_usd|usd }}{% if s.ai_cost.unpriced_calls %} · 단가 미확인 호출 {{ s.ai_cost.unpriced_calls }}회 제외{% endif %}</td></tr>
</table>
</details>
</section>

{% if s.agent_success.by_step %}
<section class="card">
<h2>에이전트 단계별 결과</h2>
{{ ch.stacked(ui.steps, "에이전트 단계별 결과") }}
{{ ch.legend([("ok", "green"), ("degraded", "amber"), ("failed", "red")]) }}
<details><summary>단계별 표</summary>
<table>
  <thead><tr><th>단계</th><th>ok</th><th>degraded</th><th>failed</th></tr></thead>
  <tbody>
  {% for name, c in s.agent_success.by_step|dictsort %}
  <tr><td>{{ STEP_LABELS.get(name, name) }}</td><td>{{ c.ok }}</td><td>{{ c.degraded }}</td><td>{{ c.failed }}</td></tr>
  {% endfor %}
  </tbody>
</table>
</details>
</section>
{% endif %}

<section class="card">
<h2>공정성(세그먼트별)</h2>
<p>격차는 표본 {{ s.fairness.min_samples }}건 이상 세그먼트의 추천 approve율 최대 − 최소입니다. 임계값 {{ s.fairness.threshold_pp|num }}%p. 회색 막대는 표본 부족 세그먼트로 격차 계산에서 빠집니다.</p>
<div class="grid-2">
{% for axis, a in s.fairness.axes.items() %}
<div class="axis-block">
<h3>{{ AXIS_LABELS.get(axis, axis) }} — 격차 {{ a.gap_pp|num }}%p · eligible 격차 {{ a.eligible_gap_pp|num }}%p · 원형 통제 최대 {{ a.controlled.max_pp|num }}%p
{% if a.policy_explained %}<small>(규칙 R104로 설명되는 축, 경보 대상 아님)</small>{% endif %}</h3>
{% if ui.fairness[axis] %}{{ ch.columns(ui.fairness[axis], AXIS_LABELS.get(axis, axis) ~ " 추천 approve율") }}{% endif %}
<details><summary>세그먼트 표</summary>
<table>
  <thead><tr><th>값</th><th>신청</th><th>추천 완료</th><th>추천 approve율</th><th>정책 eligible율</th><th>approve / conditional / refer / decline</th><th>심사역 승인율</th><th></th></tr></thead>
  <tbody>
  {% for value, r in a["values"].items() %}
  <tr>
    <td>{{ value }}</td><td>{{ r.n }}</td><td>{{ r.assessed }}</td><td>{{ r.approve_rate|pct }}</td><td>{{ r.eligible_rate|pct }}</td>
    <td>{{ r.recommendations.approve }} / {{ r.recommendations.conditional }} / {{ r.recommendations.refer }} / {{ r.recommendations.decline }}</td>
    <td>{{ r.officer_approve_rate|pct }}</td><td>{% if r.insufficient %}표본 부족{% endif %}</td>
  </tr>
  {% endfor %}
  </tbody>
</table>
</details>
</div>
{% endfor %}
</div>
</section>
{% endif %}
{% endblock %}
```

- [ ] **Step 5: 통과 확인** — Run: `... tests\console` / Expected: 모두 PASS(특히 `test_ops_page.py`의 한식·카페 표본 부족 정규식).

- [ ] **Step 6: 커밋** — `feat(console): P7 운영 지표(게이지·단계 누적 막대·공정성 막대)`

---

### Task 9: 문서, 전체 테스트, 미리보기 확인

**Files:**
- Modify: `docs/demo/demo-script.md`, `docs/runbook/setup.md`

- [ ] **Step 1: `docs/demo/demo-script.md` 세 곳**

1. 26행 `- **화면:** 탭 1, 상단 `AMA Lending 심사 콘솔`의 `신청` 목록(`/`). 목록에 사전 건이 보입니다.` →
   `- **화면:** 탭 1, 왼쪽 사이드바 `AMA Lending 심사 콘솔`의 `신청` 대시보드(`/`). KPI 카드·상태 도넛 아래 `신청 목록`에 사전 건이 보입니다.`
2. 38행의 `(상태 이름은 `src/console/app.py`의 `STATUS_LABELS`)` → `(상태 이름은 `src/console/statuses.py`의 `STATUS_LABELS`)`, 그리고 같은 줄 `진행 중에는 10초마다…` 앞에 다음 문장을 넣는다:
   `` `타임라인`은 7단계 진행 단계(완료 ✓, 진행 중 강조, 단계 사이 경과 초)로 보이고, 모든 상태 기록은 그 아래 `상태 기록 N건`을 펼치면 보입니다. ``
3. 52행 `승인되면 상단에 `사후 모니터링` 링크가 생깁니다.` → `승인되면 제목 오른쪽에 `사후 모니터링` 버튼이 생깁니다.`

- [ ] **Step 2: `docs/runbook/setup.md`의 "로컬 서비스 실행" 코드 블록 아래에 추가**

````markdown
심사 콘솔 화면만 Azure 없이 확인할 때(합성 데이터, 결정·재생 버튼은 메모리에 기록만 함):

```powershell
.\.venv\Scripts\python -m consolepreview          # http://127.0.0.1:8765/
```
````

- [ ] **Step 3: 전체 테스트**

Run: `$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python -m pytest -q -p no:cacheprovider`
Expected: 1072 passed, 105 skipped(기준선 1016 + 새 56). 수가 다르면 새 테스트 수를 세어 확인하고, 실패가 있으면 멈추고 보고한다.

- [ ] **Step 4: 미리보기 눈 확인**

```powershell
.\.venv\Scripts\python -m consolepreview --port 8765
```

브라우저로 `/`, `/applications/app-20261103-p01`, `/applications/app-20261103-p06`, `/loans`, `/loans/app-20261103-p03`, `/ops`를 열어 차트·스테퍼가 보이고 브라우저 개발자 도구 콘솔에 CSP 위반이 없는지 확인한다. 확인 뒤 서버를 끈다(Ctrl+C).

- [ ] **Step 5: 커밋** — `docs(p7): 데모 대본 화면 설명, 콘솔 미리보기 명령`
