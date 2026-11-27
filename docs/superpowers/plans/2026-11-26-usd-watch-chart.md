# USD 감시 일지 두 단 그래프 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** USD 대출 화면의 감시 일지 카드 맨 위에 "선행·후행 신호 + 현금 여력" 두 단 SVG 그래프를 두고, 일지 증거 칩을 사람이 읽는 표기로 바꾸고, 정산·마켓·흐름 표를 접힌 "원장 전체 보기"로 옮겨 신호 칸을 강조한다.

**Architecture:**
- 그래프 기하는 새 순수 함수 모듈 `src\console\watch_chart.py`가 계산한다. 마크업은 `_charts.html` 매크로가 그린다. 기존 `charts.py`/`_charts.html` 분담(P7)을 그대로 따른다.
- `dashboard.loan_summary`가 결과를 `ui.watch_chart`로 넘긴다.
- `loan.html`은 `usd and v.journal`일 때만 새 배치를 쓴다. 그 밖에는 지금 마크업을 그대로 쓴다.

**Tech Stack:** Python 3.12, Jinja2(콘솔), 서버 렌더 SVG, pytest

**Spec:** `docs\superpowers\specs\2026-11-26-usd-watch-chart-design.md`

## Global Constraints

- 모든 경로는 worktree `C:\Users\jungwoonlee\orca\projects\ama-agent-04\.worktrees\watch-chart`(브랜치 `feat/usd-watch-chart`, main `574b591`에서 분기) 기준이다.
  - 테스트는 거기서 `..\..\.venv\Scripts\python.exe -m pytest <대상> -q`로 돌린다.
- KRW 화면·경로(`/legacy`)와 일지 필드가 없는 옛 USD 재생 화면의 마크업은 바뀌지 않는다.
- 새 클라이언트 스크립트나 차트 라이브러리는 금지다. 갱신은 기존 meta refresh만 쓴다.
- CSP(`style-src 'self'`) 때문에 `style=` 속성은 금지다. 색과 선 모양은 `console.css` 클래스로 준다.
- 화면 문구는 한국어 원문을 `_()`/`pgettext()`로 감싸고, `src\console\locales\en\monitoring.json`에 영어를 넣는다.
  - `*_LABELS` 사전의 한국어 값도 영어가 있어야 한다(`tests\console\test_i18n.py`).
- 처리되지 않은 주에는 관측값(비율 점, 막대, 잔액 점, 등급 칩, 단계색, 링크)을 그리지 않는다.
  - 상환일 💵만 회색 약정 표시로 예외다(spec §5.2).
- 탐지 기준선은 0.75다(`sales_slump`, `inflow_drop`, `ORDERS_DIP_RATIO`가 모두 같은 값). 위 단 y축 최대는 1.2이고, 넘는 값은 1.2에서 자른다.
- 비교 트랙 문구("bank only", "N weeks earlier")는 다시 쓰지 않는다.
- 커밋 트레일러:
  - `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`
  - `Copilot-Session: 05f1ca1b-6073-417d-bfb1-6b546f82cb58`

## Review Focus

1. **재생 도중(예: 7/13주 처리)인 화면:** 8~13주 칸은 점선 빈칸이고 링크가 없다. 상환일만 회색으로 보인다. → Task 2 `test_unprocessed_slots_are_empty`
2. **비율 값이 없거나(None, 기대값 0) 1.2를 넘는 주:** 선이 그 주에서 끊기거나 1.2에서 잘린다. 예외가 나지 않는다. → Task 2 `test_ratio_gaps_and_clipping`
3. **마켓 스냅샷 행이 없는 주:** 등급 칩은 "–"이고 신호 강조가 없다. → Task 2 `test_grade_chip_missing_market_row`
4. **상환 일정이 없거나 납기가 13주 범위 밖인 대출:** 💵 표시 없이 그래프가 그려진다. → Task 2 `test_due_marks_only_inside_window`
5. **영어 화면과 옛 화면:** 새 배치에 한국어가 남지 않는다. KRW 화면과 일지 없는 옛 USD 화면에는 새 그래프나 `<details>`가 생기지 않는다. → Task 3 `test_krw_and_legacy_usd_layout_unchanged`, 그리고 기존 `test_english_page_has_no_korean_ui_text`

---

### Task 1: 일지 증거 칩 사람 표기

**Files:**
- Modify: `src\console\monitoring.py` (`_journal_sources`와 라벨 상수 영역 52~55행 부근)
- Modify: `src\console\templates\loan.html` (일지 칩 줄, 현재 179행 부근)
- Modify: `src\console\locales\en\monitoring.json`
- Test: `tests\console\test_usd_views.py`

**Interfaces:**
- Produces: `monitoring.EVIDENCE_LABELS: dict[str, tuple[str, str]]`. 지표 이름 → (한국어 라벨, 단위). 단위는 `"pct" | "usd" | "text" | "rating" | "count"`.
- Produces: 일지 줄 `sources[].signals[].evidence`의 원소를 `(id, value)` 튜플에서 `{"id": str, "metric": str, "label": str | None, "unit": str | None, "value": Any}`로 바꾼다.
  - `metric`은 `id.split(":")[-1]`이다.
  - 라벨이 없는 지표는 `label=None`이고, 템플릿이 지금처럼 `id = 값`으로 보인다.
- Produces: `monitoring.signal_metrics(weeks: list[dict]) -> dict[int, set[str]]`. 주차 → 그 주 `watch.signals[].metric_ids`의 지표 이름 집합. Task 3이 표 강조에 쓴다.

라벨 표(spec §5.3, 값 그대로):

| 지표 | 라벨 | 단위 |
|---|---|---|
| `orders_vs_expected` | 정산 주문 기대 대비 | pct |
| `revenue_vs_expected` | 은행 매출 기대 대비 | pct |
| `projected_min_balance_cents` | 예상 최저 잔액 | usd |
| `due_balance_cents` | 상환일 잔액 | usd |
| `order_gross_cents` | 주문액 | usd |
| `expected_orders_cents` | 기대 주문액 | usd |
| `held_cents` | 보류액 | usd |
| `mkt_grade` | 마켓 등급 | text |
| `mkt_cancel_rate` | 취소율 | pct |
| `return_rate` | 반품률 | pct |
| `deduction_rate` | 공제율 | pct |
| `mkt_rating` | 평점 | rating |
| `mkt_penalties` | 계정 경고 | count |
| `recon_ratio` | 정산 대사율 | pct |
| `orders_vs_prior_year` | 작년 대비 주문 | pct |

- 한국어 원문이 이미 `src\console\locales\en\*.json` 어딘가에 있으면(예: "정산 대사율", "작년 대비 주문") 기존 영어를 그대로 쓴다. "보류"는 이미 "Deferred"이므로 "보류액"은 별도 원문으로 둔다.
- 영어가 겹쳐 충돌하면 `test_conflicting_duplicates_are_rejected`가 잡는다. 그때는 `evidence|` 문맥 키를 쓴다.

- [ ] **Step 1: 실패하는 테스트 쓰기.**
  - `test_journal_newest_first_with_source_chips`의 원시 ID 단언을 바꾼다.
    - `"W:2:mkt_cancel_rate = 0.046"`과 `"W:3:held_cents = $3800"`, `"F:5:projected_min_balance_cents = -$480"` 대신 아래가 카드에 있어야 한다.
    - `"Cancel rate 4.6%"`, `"Held $3800"`, `"Projected min balance -$480"`
    - 영어 라벨은 `monitoring.json`에 넣은 값과 일치시킨다.
  - 새 테스트 `test_journal_chips_hide_metric_ids`:
    - `_scene_weeks()` 화면에서 일지 카드의 태그를 지운 보이는 글자(`re.sub(r"<[^>]+>", " ", card)`)에 `"W:"`와 `"F:"`가 없다.
    - 원 ID는 `title="W:3:held_cents"`처럼 속성으로 남는다.
  - 새 테스트 `test_journal_unknown_metric_falls_back_to_id`:
    - 신호 `metric_ids=["W:2:mystery_metric"]`, 행 `mystery_metric=7`이면 카드에 `"W:2:mystery_metric = 7"`이 보인다.
  - 새 테스트 `test_signal_metrics_by_week`:
    - `signal_metrics(_scene_weeks()) == {1: set(), 2: {"mkt_cancel_rate"}, 3: {"held_cents", "mkt_penalties"}, 4: {"held_cents"}, 5: {"projected_min_balance_cents"}}`
- [ ] **Step 2: 실패 확인.** `..\..\.venv\Scripts\python.exe -m pytest tests\console\test_usd_views.py -q -k "journal or signal_metrics"`를 돌린다. 위 테스트들이 FAIL이어야 한다.
- [ ] **Step 3: 구현.**
  - `EVIDENCE_LABELS`, 증거 dict 변환, `signal_metrics`를 넣는다.
  - 템플릿 칩: `<span class="chip" title="{{ e.id }}">{{ _(e.label) }} {{ 값 }}</span>`. 값 형식은 단위별로 정한다.
    - pct → `|pct`
    - usd → `|usd_cents`
    - rating → `"%.2f"|format`
    - count·text → 그대로
    - 값이 None → 지금처럼 `warn` 칩에 "없음"
  - 경보 카드 칩(`loan.html` 404행 부근)은 건드리지 않는다.
- [ ] **Step 4: 통과 확인.** `..\..\.venv\Scripts\python.exe -m pytest tests\console\test_usd_views.py tests\console\test_i18n.py -q`가 모두 PASS여야 한다.
- [ ] **Step 5: 커밋.** `feat(console): 감시 일지 증거 칩을 사람이 읽는 표기로`

### Task 2: 두 단 그래프 기하

**Files:**
- Create: `src\console\watch_chart.py`
- Test: `tests\console\test_watch_chart.py`

**Interfaces:**
- Consumes: 없음. 순수 함수이며 `console.charts`의 `is_num`, `_r`만 쓴다.
- Produces: `watch_chart(weeks: list[dict], lines: list[dict], installments: list[dict], *, total: int = 13, width: int = 960) -> dict | None`
  - `weeks`: `v.weeks`. 각 원소는 `{"week": int, "row": {...}}`.
  - `lines`: `v.journal["lines"]`(최신 주가 먼저). 주차, `stage`, `alert_id`, `sources`를 쓴다.
  - `installments`: `loan["repayment"]["schedule"]`(`loan.html` 상환 원장과 같은 목록). 각 원소는 `due_on`(ISO 날짜)과 `status`(`scheduled` | `partial` | `missed` | `paid`, `src\agents\repayment.py:122`)를 가진다.
  - `weeks`나 `lines`가 비면 `None`을 돌려준다.
- 돌려주는 dict 키(Task 3 템플릿이 쓴다):
  - `width`, `height`, `x0`, `x1`
  - `slots: list[{week, x, w, cx, done: bool, current: bool, stage: str | None, alert_stage: str | None, href: str | None}]`
    - 항상 `total`개다.
    - 처리된 주만 `done=True`, `stage`, `href="#journal-w{n}"`를 가진다.
    - `current`는 처리된 마지막 주다.
  - `band: {y, h}`: 단계 띠 위치
  - `ratio`: 위 단
    - `{top, base, ticks: [{y, text}], threshold_y, lines: {"orders3": L, "orders1": L, "revenue3": L}}`
    - `L = {segments: list[str], points: [{week, x, y, value, clipped: bool}]}`
    - `orders3`는 `row["orders_vs_expected"]`다.
    - `orders1`은 `row["order_gross_cents"] / row["expected_orders_cents"]`다. 둘 중 하나가 숫자가 아니거나 기대값이 0 이하이면 결측이다.
    - `revenue3`은 `row["revenue_vs_expected"]`다.
    - 결측 주에서 선을 끊는다. 값이 1.2를 넘으면 y는 1.2 위치이고 `clipped=True`이며, `value`는 원래 값을 둔다.
    - `ticks`는 0%, 50%, 100% 세 개다. `threshold_y`는 0.75 위치다.
  - `grades: [{week, cx, y, grade: str | None, signal: bool}]`
    - 처리된 주만 담는다.
    - `grade`는 `row.get("mkt_grade")`이고, 없으면 None이다(템플릿이 "–"로 그린다).
    - `signal`은 그 주 일지 `sources`에서 market `state == "signal"`인지다.
  - `cash`: 아래 단
    - `{top, base, zero_y, ticks: [{y, text_cents}], bars: [{week, kind: "orders" | "inflow", x, y, w, h, value}], balance: {segments, points: [{week, x, y, value, negative: bool, shortfall_date}]}}`
    - 막대와 선은 한 개의 부호 있는 $ 축을 함께 쓴다. 축 범위는 `lo = min(0, 값들)`, `hi = max(0, 값들)`이다. (제 판단, spec §6)
    - `orders`는 `order_gross_cents`, `inflow`는 `revenue_cents`다.
    - `balance`는 `projected_min_balance_cents`이고, `shortfall_date`는 `row.get("shortfall_date")`다.
  - `dues: [{week, cx, due_on, state: "paid" | "missed" | "partial" | "scheduled" | "future"}]`
    - 납기 주차는 1주차 `row["start"]` + 7×(n−1)일 창으로 계산한다.
    - 13주 창 밖의 납기는 버린다.
    - 처리된 주의 납기는 `state`가 일정의 `status`다(`paid`/`partial`/`missed`/`scheduled`).
    - 처리되지 않은 주의 납기는 `state="future"`다.

- [ ] **Step 1: 실패하는 테스트 쓰기.** 공통 데이터는 spec §1 dailylook 숫자다. 7주까지 처리된 상태로 `weeks`, `lines`를 만드는 헬퍼 `_dailylook(processed=7)`을 두고, 아래 값을 쓴다.
  - 주 시작일: `2026-11-26`부터 7일 간격
  - `orders_vs_expected`: [1.052, 1.057, 1.059, 0.798, 0.514, 0.228, 0.230]
  - `order_gross_cents`: [615000, 620000, 621200, 157500, 123100, 120000, 160600]
  - `expected_orders_cents`: 584400 (매주)
  - `revenue_vs_expected`: [1.095, 1.091, 1.097, 1.061, 0.846, 0.622, 0.478]
  - `revenue_cents`: [558500, 555300, 564900, 503900, 225800, 222800, 282700]
  - `projected_min_balance_cents`: [1240000, 1240000, 1240000, 1136800, 976100, 532200, -25400]
  - `mkt_grade`: Gold×4, Silver×3
  - 단계: normal×3, watch, medium, medium, high
  - `alert_id`: 5주차, 7주차
  - 5주차 market 신호 있음
  - 납기: `2026-12-28` paid, `2027-01-25` scheduled, `2027-02-24` scheduled

  테스트:
  - `test_slots_and_links`:
    - `len(c["slots"]) == 13`
    - `[s["href"] for s in c["slots"][:7]] == [f"#journal-w{n}" for n in range(1, 8)]`
    - `c["slots"][6]["current"] is True`
    - `c["slots"][4]["alert_stage"] == "medium"`, `c["slots"][6]["alert_stage"] == "high"`
  - `test_unprocessed_slots_are_empty`:
    - 8~13주 slot은 `done False`, `href None`, `stage None`이다.
    - 모든 `lines`의 점 주차와 `bars`·`balance` 점 주차가 ≤ 7이다.
    - `dues`에 `{"week": 9, "state": "future", "due_on": "2027-01-25"}`와 `{"week": 13, "state": "future", ...}`가 있다.
    - 5주차 납기 state는 `"paid"`다.
  - `test_orders_single_week_drops_before_three_week`:
    - `orders1` 4주차 값 ≈ 0.2695(157500/584400), y가 `threshold_y`보다 아래(큰 y)다.
    - `orders3` 4주차 0.798은 `threshold_y`보다 위다.
  - `test_ratio_gaps_and_clipping`:
    - 2주차 `orders_vs_expected=None`이면 `orders3.segments`가 2개가 된다.
    - 1주차 값 1.5이면 `clipped True`, y는 1.2 위치, `value`는 1.5다.
    - `expected_orders_cents=0`이면 `orders1`에 그 주 점이 없다.
  - `test_cash_axis_signed`:
    - 7주차 balance 점은 `negative True`, y > `zero_y`다.
    - 모든 bar의 h는 0 이상이고, 밑변 `y + h`는 `zero_y`다.
  - `test_grade_chip_missing_market_row`:
    - 6주차 행에서 `mkt_grade`를 빼면 그 주 `grade None`, `signal False`다.
    - 5주차는 `grade "Silver"`, `signal True`다.
  - `test_due_marks_only_inside_window`:
    - `installments=[]`이면 `dues == []`다.
    - 납기 `2027-06-01`은 빠진다.
  - `test_none_without_journal`: `watch_chart(weeks, [], [])`는 None이다.
- [ ] **Step 2: 실패 확인.** `..\..\.venv\Scripts\python.exe -m pytest tests\console\test_watch_chart.py -q`가 ImportError 또는 FAIL이어야 한다.
- [ ] **Step 3: 구현.**
  - 레이아웃 상수(띠 높이, 두 단 높이, 등급 칩 줄, 축 글자 자리)는 구현자가 정한다.
  - 가로는 `charts.PAD_L`/`PAD_R`와 `width=960`에 맞춘다(`dashboard.LOAN_CHART`와 같은 폭).
  - `total`은 `dashboard.REPLAY_WEEKS`를 넘겨받는다. 순환 import를 피하려고 기본값 13을 둔다.
- [ ] **Step 4: 통과 확인.** 같은 명령이 PASS여야 한다.
- [ ] **Step 5: 커밋.** `feat(console): 감시 일지 두 단 그래프 기하`

### Task 3: 화면 배치·그리기·원장 접기

**Files:**
- Modify: `src\console\dashboard.py` (`loan_summary`, 559행 부근)
- Modify: `src\console\templates\_charts.html` (새 매크로 `watch_chart`)
- Modify: `src\console\templates\loan.html` (164~255행 부근: 일지, 승인 근거, 흐름, PG, 마켓 영역)
- Modify: `src\console\static\console.css`
- Modify: `src\console\locales\en\monitoring.json`
- Test: `tests\console\test_usd_views.py`

**Interfaces:**
- Consumes: Task 2 `watch_chart(...)`, Task 1 `signal_metrics(weeks)`
- Produces:
  - `loan_summary(view)["watch_chart"]`: `usd and view["journal"]`일 때만 dict이고, 아니면 None이다.
  - `view["signal_hits"]`: `signal_metrics(weeks)` 결과다. `monitoring.loan_view`가 USD일 때만 넣는다.

화면 규칙(spec §5.1, §5.4):
- `{% if usd and v.journal %}` 분기:
  1. 일지 카드 `#watch-journal`의 h2 아래, 일지 목록 위에 `ch.watch_chart(ui.watch_chart, _("주간 신호와 현금 여력"))`과 범례를 둔다.
  2. 승인 근거 카드
  3. `<details id="ledger-all" class="card"><summary>{{ _("원장 전체 보기") }}</summary>` 안에 기존 흐름 표, PG 표, 마켓 표를 기존 열 그대로 둔다.
  - 흐름 카드 위쪽 매출 막대 차트(`ui.chart`)는 이 분기에서 그리지 않는다.
- `{% else %}` 분기: 지금 마크업을 그대로 둔다(일지 없음 → 지금 순서).
- 매크로 그리기:
  - 단계 띠 칸은 `class="stage-{stage}"`로 그린다. 미처리 칸은 `class="slot-empty"`(점선 테두리)다.
  - 🔔 표시는 `alert_stage`가 있을 때만 그린다.
  - 처리된 주의 세로줄 전체는 `<a href="{{ s.href }}">` 안의 투명 rect로 감싼다(`class="slot-hit"`). `current` 칸은 `class="slot-current"`(옅은 강조)다.
  - 선 클래스:
    - `orders3`: `stroke-orange`
    - `orders1`: `stroke-orange line-dotted line-faint`
    - `revenue3`: `stroke-blue`
    - 기준선: `ref-dashed`
    - 잔액선: `stroke-purple`. 음수 점은 `fill-red`다.
  - 막대: `orders`는 `bar-outline stroke-orange`, `inflow`는 `fill-blue`다.
  - 💵 상환일: `state`별 클래스 `due-{state}`. future는 회색, missed는 빨강이다.
  - 모든 점, 막대, 띠, 💵에 `<title>`을 붙인다.
    - "%(week)s주차 정산 주문(3주) %(v)s"
    - "%(week)s주차 정산 주문(한 주) %(v)s"
    - "%(week)s주차 은행 매출(3주) %(v)s"
    - "%(week)s주차 PG 주문 %(v)s"
    - "%(week)s주차 은행 입금 %(v)s"
    - "%(week)s주차 예상 최저 잔액 %(v)s"(+ 부족일)
    - "%(date)s 상환"
    - "%(week)s주차 · %(stage)s"
- 원장 칸 강조: `hits = v.signal_hits.get(week, ())`. 강조 칸 `<td class="cell-warn">`의 대응은 아래와 같다.
  - 흐름 표:
    - 기대 대비 열 ← `revenue_vs_expected`
    - 전망 최저 잔고 열 ← `projected_min_balance_cents` 또는 `due_balance_cents`
  - PG 표:
    - 주문액 ← `order_gross_cents`, `expected_orders_cents`, `orders_vs_expected`
    - 반품 ← `return_rate`
    - 보류 ← `held_cents`
    - 공제 ← `deduction_rate`
  - 마켓 표:
    - 취소율 ← `mkt_cancel_rate`
    - 평점 ← `mkt_rating`
    - 등급 ← `mkt_grade`
    - 경고 ← `mkt_penalties`
  - `recon_ratio`와 `orders_vs_prior_year`는 강조할 칸이 없어 무시한다.
- 새 CSS 클래스: `stage-normal`, `stage-watch`, `stage-medium`, `stage-high`, `stage-resolved`, `slot-empty`, `slot-hit`, `slot-current`, `line-dotted`, `line-faint`, `ref-dashed`, `bar-outline`, `due-paid`, `due-missed`, `due-partial`, `due-scheduled`, `due-future`, `cell-warn`, `grade-chip`, `grade-signal`. 색은 기존 `--c-*` 변수와 `.sev-*` 색감을 따른다.

- [ ] **Step 1: 실패하는 테스트 쓰기** (`test_usd_views.py`, `_usd_page`·`_scene_weeks` 재사용).
  - `test_watch_chart_inside_journal_card`:
    - `_journal(page)` 안에 `class="chart watch-chart"`가 있고, 그것이 첫 `id="journal-w` 앞에 온다.
    - `href="#journal-w5"`부터 `href="#journal-w1"`까지 다섯 개가 있고, `href="#journal-w6"`은 없다.
    - `class="slot-empty"`가 8개 있다(13−5).
    - 범례 문구 "Settlement orders (3 wks)", "Settlement orders (1 wk)", "Bank revenue (3 wks)", "Projected min balance"가 있다. 영어 값은 json과 일치시킨다.
  - `test_ledger_folded_with_signal_cells`:
    - `<details id="ledger-all"`이 있고, 그 안에 "Weekly settlement (PG ledger)"와 "Weekly market snapshot"이 있다. `open` 속성은 없다.
    - 3주차 PG 행의 보류 칸, 2주차 마켓 행의 취소율 칸, 3주차 마켓 행의 경고 칸은 `class="cell-warn"`이다.
    - 1주차 행에는 `cell-warn`이 없다.
    - 일지 카드 밖에 매출 막대 차트 `aria-label="Weekly revenue and cash buffer"`(현재 영어 값으로 확인)가 없다.
  - `test_krw_and_legacy_usd_layout_unchanged`:
    - 일지 없는 USD 화면(`_watch_week`가 아닌 `watch` 없는 주차 3개)에는 `watch-chart`, `ledger-all`, `<details`가 없다. "Weekly flow and detectors" 카드가 그대로 있다.
    - KRW 미리보기 대출 `/loans/app-20261103-p03`(`test_i18n.EN_PAGES`와 같은 preview 픽스처)도 같다.
- [ ] **Step 2: 실패 확인.** `..\..\.venv\Scripts\python.exe -m pytest tests\console\test_usd_views.py -q -k "watch_chart or ledger_folded or layout_unchanged"`가 FAIL이어야 한다.
- [ ] **Step 3: 구현.** 위 규칙대로 `loan_summary`, `loan_view`(`signal_hits`), 매크로, 템플릿, CSS, 영어 번역을 넣는다.
- [ ] **Step 4: 통과 확인.** `..\..\.venv\Scripts\python.exe -m pytest tests\console -q`가 모두 PASS여야 한다. i18n의 영어 화면 한국어 잔존 검사와 템플릿 gettext 검사를 포함한다.
- [ ] **Step 5: 눈으로 확인.**
  - `consolepreview`로 로컬 콘솔을 띄운다(실행 방법은 `src\consolepreview` README나 기존 사용 방식을 따른다).
  - 일지가 있는 USD 미리보기 대출 화면에서 두 단 그래프가 겹침이나 넘침 없이 보이는지 확인한다.
  - 미리보기 픽스처에 일지 대출이 없으면 이 확인은 Task 4의 실제 화면 확인으로 대신한다.
- [ ] **Step 6: 전체 회귀.** `..\..\.venv\Scripts\python.exe -m pytest -q`가 PASS여야 한다(main 대비 새 실패 0).
- [ ] **Step 7: 커밋.** `feat(console): 감시 일지 두 단 그래프와 원장 접기`

### Task 4: 배포와 실제 화면 확인

**Files:**
- Modify: `docs\runbook\deploy-history.md` (새 절 추가)

- [ ] **Step 1:** 브랜치를 푸시하고 PR을 연다. 제목은 `feat: USD 감시 일지 두 단 그래프`, 본문에 spec과 plan 링크를 넣는다.
- [ ] **Step 2:** 콘솔만 배포한다.
  - `docs\runbook\deploy.md`의 "구성요소별 배포 › 공통 순서"를 따른다. ACR 네트워크 창을 열고, 이미지를 빌드·푸시하고, `ca-console` 새 리비전을 만들고, 창을 닫는다.
  - 기대: 새 리비전 `ca-console--00000NN`이 100% Healthy다. 롤백 대상은 `ca-console--0000060`이다.
  - 창을 닫은 뒤 ACR 공용 네트워크가 Disabled이고 Allow 규칙이 0개인지 확인한다.
- [ ] **Step 3:** `https://amalend-gaz7yh2zic7zc.koreacentral.cloudapp.azure.com/loans/app-20261126-373b6b`에서 spec §7 확인 항목을 본다.
  - 4주차 점선이 75% 아래
  - 5주차 Silver 칩 강조
  - 6주차 은행 선이 75% 아래
  - 7주차 잔액 선이 $0 아래
  - 13주차 미납 💵 빨강
  - 주차 칸을 누르면 일지로 이동
  - 원장이 접혀 있음
  - 이어서 KRW 대출 화면 하나(`/legacy/loans/...`)가 그대로인지 확인한다.
- [ ] **Step 4:** 재생 중 모습을 확인한다.
  - 새 dailylook 신청을 승인하고 재생한다. 방법은 세션 파일 `scenario-e2e.py`와 런처 `scenario-pr7.ps1`이다.
  - 새로 고침마다 한 주씩 채워지고, 미처리 칸이 빈칸인지 본다.
- [ ] **Step 5:** `deploy-history.md`에 리비전, 롤백 대상, 확인 결과를 적고 커밋·푸시한다.
