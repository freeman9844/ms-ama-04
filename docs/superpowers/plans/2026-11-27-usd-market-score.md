# USD 마켓 점수 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**목표:** 셀러의 여러 마켓·여러 지표를 대출 시점 = 100인 마켓 점수 하나로 합쳐, USD 감시 그래프에 보이고 마켓 데이터원의 경보 신호로 쓴다.

**구조:** 순수 함수 모듈 `agents\usd_market_score.py`가 주간 마켓 스냅샷에서 마켓별 점수·가중 점수·감점 내역을 계산한다. 탐지기 `_market_week`가 그 결과를 주차 행에 넣고 `market_score_drop`·`market_score_crash` 신호를 낸다(기존 `cancel_rise`·`cancel_spike`·`rating_drop`·`grade_down` 대체). 콘솔은 등급 칩 줄을 점수 칩 줄로 바꾸고 원장 표에 점수 열을 더한다.

**기술:** Python 3.12, pydantic(계약), FastAPI/Jinja(콘솔), pytest.

**스펙:** `docs\superpowers\specs\2026-11-27-usd-market-score-design.md`

## 전역 제약

- USD 전용. KRW 탐지·화면·라벨은 바꾸지 않는다.
- 점수 계산은 결정적이고 LLM을 쓰지 않는다.
- 기준선은 각 마켓의 대출 전 주(`week_end < start`). 개선은 가점하지 않는다. 점수 범위 0–100, 대출 시점 = 100.
- 감점 출발값: 취소율 %p당 6(상한 40), 반품률 %p당 2(상한 25), 평점 0.1점당 5(상한 25), 등급 한 단계당 15(상한 30), 경고 20·정지 40(합 상한 40, 생긴 주 + 뒤 3주, 대출 뒤 주만).
- 가중치: 대출 전 매출 비중, 13주 고정. 대출 전 매출 0인 마켓은 제외. 주요 마켓 = 비중 ≥ 20%.
- 신호: 약 `market_score_drop` = 가중 점수 ≤ 85 또는 주요 마켓 최저 ≤ 85. 강 `market_score_crash` = 같은 조건으로 ≤ 60(crash가 서면 drop은 뺀다). `account_warning`은 그대로.
- 정산 반품 신호(`returns_rising`, `return_spike`)는 그대로 둔다.
- 화면 문구는 한국어 msgid + `src\console\locales\en\monitoring.json` 영어 번역. 영어 화면에 한국어가 보이면 안 된다.
- 옛 기록 호환: `returns_cents` 없는 스냅샷, `mkt_score` 없는 주차 행, 옛 신호 코드가 모두 지금처럼 읽히고 보인다.
- 계수·경계를 바꾸면 스펙 5.2·5.4 표와 이 문서의 전역 제약을 같이 고친다.
- 커밋 트레일러: `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`, `Copilot-Session: 05f1ca1b-6073-417d-bfb1-6b546f82cb58`.
- 테스트 명령: `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider <경로>`.

## 검토 초점

1. 대출 전 경고가 있는 셀러: 대출 첫 주 점수가 100이어야 한다(경고 감점은 대출 뒤 주만). → Task 2 `test_preloan_penalty_ignored`
2. 대출 전 매출이 0인 마켓(작은 자사몰 등): 0으로 나누지 않고 가중치에서 빠진다. → Task 2 `test_market_without_preloan_sales_is_excluded`
3. 대출 뒤 한 주 매출이 0인 마켓: 비율 지표를 감점하지 않는다(취소 0/매출 0을 0%로 보거나 오류를 내면 안 됨). → Task 2 `test_zero_gross_week_skips_ratio_metrics`
4. 배포 직전에 저장된 재생 흐름(`flow.json`)과 옛 mockapi 페이로드에는 `returns_cents`가 없다: 읽혀야 하고 반품 감점은 0이다. → Task 1 `test_market_week_without_returns_still_reads`
5. 점수 필드가 없는 옛 주차 행과 새 행이 한 대출에 섞일 때: 옛 주는 등급 칩, 새 주는 점수 칩으로 그린다. → Task 4 `test_market_chip_falls_back_to_grade_for_old_rows`

---

### Task 1: 주간 마켓 스냅샷에 반품액 추가

**Files:**
- Modify: `src\shared\usd_source.py:74-86` (`UsdMarketWeek`)
- Modify: `src\mockapis\sellerdata\usd_market_flow.py:34-69` (`build_usd_market_flow`)
- Test: `tests\mockapis\test_usd_market_flow.py`

**Interfaces:**
- Produces: `UsdMarketWeek.returns_cents: Cents = 0` — 그 주(월~일) 그 마켓의 `UsdOrderDay.returns_cents` 합.

- [ ] **Step 1: 실패하는 테스트 작성** (`tests\mockapis\test_usd_market_flow.py` 끝에 추가)

```python
@pytest.mark.parametrize("seller", ["seller-gearstore-06", "seller-linen-10"])
def test_market_week_returns_are_order_returns_of_that_week(seller):
    case, flow = flow_for(seller)
    for n in (-3, 1, 5):
        end = flow.start + timedelta(days=7 * n - 1)
        for w in week_rows(flow, n):
            want = sum(o.returns_cents for o in flow.orders
                       if o.platform_code == w.platform_code and end - timedelta(days=6) <= o.order_on <= end)
            assert w.returns_cents == want


def test_linen_returns_rise_on_every_platform():
    case, flow = flow_for("seller-linen-10")

    def rate(n, code):
        w = week_rows(flow, n, code)[0]
        return w.returns_cents / w.gross_cents

    for p in case.marketplace.platforms:
        base = sum(rate(n, p.platform_code) for n in range(-7, 1)) / 8
        assert rate(4, p.platform_code) >= base + 0.04


def test_market_week_without_returns_still_reads():
    from shared.usd_source import UsdMarketWeek
    _case, flow = flow_for("seller-gearstore-06")
    payload = flow.market_weeks[0].model_dump(mode="json")
    del payload["returns_cents"]
    assert UsdMarketWeek.model_validate(payload).returns_cents == 0
```

`flow.orders`는 `UsdPostLoanFlow.orders`(대출 전 기준 주문 + 사후 주문), `flow.market_weeks`는 주간 스냅샷이다. linen은 실제로 세 마켓 모두 대출 전 2.0%, 3~8주 18.0%, 9~13주 6.0%다.

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests\mockapis\test_usd_market_flow.py`
Expected: 새 3개 FAIL(`returns_cents` 속성 없음).

- [ ] **Step 3: 구현** — `UsdMarketWeek`에 `returns_cents: Cents = 0` 추가. `build_usd_market_flow`에서 `gross`와 같은 창으로 `returns = sum(o.returns_cents ...)`를 구해 넣는다. RNG 호출 순서는 바꾸지 않는다(다른 장부 고정).

- [ ] **Step 4: 통과 확인** — 같은 명령 PASS, 이어서 `tests\mockapis\test_usd_scenes.py::test_existing_seller_books_do_not_change` PASS.

- [ ] **Step 5: 커밋** — `feat(mockapis): USD 주간 마켓 스냅샷에 반품액 추가`

---

### Task 2: 마켓 점수 계산 모듈

**Files:**
- Create: `src\agents\usd_market_score.py`
- Test: `tests\agents\test_usd_market_score.py`

**Interfaces:**
- Consumes: Task 1의 `UsdMarketWeek.returns_cents`, `shared.usd_source.GRADES`.
- Produces:

```python
SCORE_DROP = 85
SCORE_CRASH = 60
MAJOR_SHARE = 0.20

@dataclass(frozen=True)
class MarketScore:
    score: int                                  # 가중 셀러 점수
    platforms: dict[str, dict[str, float | int]]  # code -> {"score": int, "share": float(소수 3자리)}
    worst_platform: str                         # 주요 마켓 중 최저 점수 마켓(동점이면 비중 큰 쪽)
    worst_score: int
    parts: list[dict[str, Any]]                 # {"platform","metric","points","value","baseline"}, points 내림차순

def market_score(weeks: Sequence[UsdMarketWeek], start: date, hi: date) -> MarketScore | None
```

`hi` 주 스냅샷이 없거나 대출 전 스냅샷이 없으면 None. `metric`은 `cancel`·`returns`·`rating`·`grade`·`penalty`. `points` = 마켓 감점 × 비중(소수 1자리), 0인 항목은 넣지 않는다. 정렬은 `points` 내림차순, 동점이면 플랫폼 비중 큰 순, 그다음 지표 순서 cancel→returns→rating→grade→penalty(안정 정렬). `value`·`baseline`: 비율은 소수 4자리, 평점은 2자리, 등급은 문자열, 경고는 감점 기간 안의 건수와 0.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
from datetime import date, timedelta

import pytest

from agents.usd_market_score import market_score
from shared.contracts import USD_V1, StorePenalty
from shared.usd_source import UsdMarketWeek

START = date(2026, 11, 24)


def end(n):
    return START + timedelta(days=7 * n - 1)


def wk(n, code, gross=1_000_000, cancel=20_000, returns=20_000, rating=4.5, grade="Gold", penalties=()):
    return UsdMarketWeek(**USD_V1.model_dump(), week_end=end(n), platform_code=code, orders=10, gross_cents=gross,
                         cancel_cents=cancel, returns_cents=returns, rating=rating, seller_grade=grade,
                         pending_settlement_cents=0, penalties=penalties)


def flow(post=None, gross=None, pre=None):
    """대출 전 -7..0주는 기준값, 1..max(post)주는 post[n][code] 덮어쓰기."""
    gross = gross or {"coupang": 1_000_000, "naver_smart_store": 1_000_000}
    weeks = [wk(n, c, gross=g, **(pre or {}).get(c, {})) for n in range(-7, 1) for c, g in gross.items()]
    for n in range(1, max((post or {1: {}}).keys()) + 1):
        weeks += [wk(n, c, **{"gross": g, **(post or {}).get(n, {}).get(c, {})}) for c, g in gross.items()]
    return weeks


def penalty(n, kind):
    return (StorePenalty(platform="coupang", occurred_on=end(n), kind=kind),)


def test_unchanged_markets_score_100():
    s = market_score(flow(), START, end(1))
    assert (s.score, s.worst_score, s.parts) == (100, 100, [])
    assert s.platforms == {"coupang": {"score": 100, "share": 0.5}, "naver_smart_store": {"score": 100, "share": 0.5}}


def test_one_market_cancel_rise_is_diluted_but_worst_shows_it():
    s = market_score(flow({1: {"coupang": {"cancel": 60_000}}}), START, end(1))
    assert (s.score, s.worst_platform, s.worst_score) == (88, "coupang", 76)
    assert s.parts == [{"platform": "coupang", "metric": "cancel", "points": 12.0, "value": 0.06, "baseline": 0.02}]


def test_deductions_are_capped():
    s = market_score(flow({1: {"coupang": {"cancel": 300_000}}}), START, end(1))
    assert s.platforms["coupang"]["score"] == 60


def test_returns_rating_and_grade_add_up():
    s = market_score(flow({1: {"coupang": {"returns": 60_000, "rating": 4.2, "grade": "Silver"}}}), START, end(1))
    assert s.platforms["coupang"]["score"] == 62  # 반품 8 + 평점 15 + 등급 15
    assert [(p["metric"], p["points"]) for p in s.parts] == [("rating", 7.5), ("grade", 7.5), ("returns", 4.0)]


def test_penalty_lasts_four_weeks():
    weeks = flow({n: ({"coupang": {"penalties": penalty(3, "warning")}} if n == 3 else {}) for n in range(1, 8)})
    scores = [market_score(weeks, START, end(n)).platforms["coupang"]["score"] for n in range(1, 8)]
    assert scores == [100, 100, 80, 80, 80, 80, 100]
    weeks = flow({n: ({"coupang": {"penalties": penalty(2, "suspension")}} if n == 2 else {}) for n in range(1, 3)})
    assert market_score(weeks, START, end(2)).platforms["coupang"]["score"] == 60


def test_preloan_penalty_ignored():
    weeks = [wk(0, "coupang", penalties=penalty(0, "warning")) if (w.week_end, w.platform_code) == (end(0), "coupang")
             else w for w in flow()]
    assert market_score(weeks, START, end(1)).score == 100


def test_zero_gross_week_skips_ratio_metrics():
    s = market_score(flow({1: {"coupang": {"gross": 0, "cancel": 0, "returns": 0}}}), START, end(1))
    assert s.platforms["coupang"]["score"] == 100


def test_market_without_preloan_sales_is_excluded():
    s = market_score(flow(gross={"coupang": 1_000_000, "own_store": 0}), START, end(1))
    assert set(s.platforms) == {"coupang"} and s.platforms["coupang"]["share"] == 1.0


def test_minor_market_is_not_worst():
    s = market_score(flow({1: {"own_store": {"cancel": 300_000}}},
                          gross={"coupang": 900_000, "own_store": 100_000}), START, end(1))
    assert s.platforms["own_store"]["score"] == 60 and s.score == 96
    assert (s.worst_platform, s.worst_score) == ("coupang", 100)


@pytest.mark.parametrize("pre, now", [("Gold", "Platinum"), ("Platinum", "Gold")])
def test_unknown_grade_is_not_compared(pre, now):
    s = market_score(flow({1: {"coupang": {"grade": now}}}, pre={"coupang": {"grade": pre}}), START, end(1))
    assert s.platforms["coupang"]["score"] == 100


def test_none_without_this_week_or_preloan():
    assert market_score(flow(), START, end(2)) is None
    assert market_score([w for w in flow() if w.week_end >= START], START, end(1)) is None
```

`wk()`의 반품액 기본값 20,000은 `returns_cents`가 Task 1에서 생긴 뒤에만 통과한다. 그래서 이 Task는 Task 1 다음에 한다.

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests\agents\test_usd_market_score.py`
Expected: FAIL(모듈 없음).

- [ ] **Step 3: 구현** — 전역 제약의 계수를 모듈 상수로 둔다. 마켓별 감점은 지표마다 `round(min(cap, raw), 1)`. 취소·반품 비율 = `cents / gross_cents`(이번 주 gross 0이면 그 두 지표 생략, 기준선은 gross > 0인 대출 전 주 평균). 평점 감점 = `max(0, 기준 평균 - 지금) * 50`. 등급 감점 = 기준(대출 전 마지막 주) 대비 `GRADES` 순위 하락 단계 × 15, 어느 쪽이든 `GRADES`에 없으면 생략. 경고 = `week_end`가 `[hi-21일, hi]`이고 `>= start`인 그 마켓 스냅샷의 `penalties`를 `kind`별로 합산. 마켓 점수 = `round(100 - min(100, 합))`. 가중 점수 = `round(Σ 비중 × (100 - min(100, 합)))`.

- [ ] **Step 4: 통과 확인** — 같은 명령 PASS.

- [ ] **Step 5: 커밋** — `feat(agents): USD 마켓 점수 계산 모듈`

---

### Task 3: 탐지기·조기경보에 점수 신호 연결과 장면 재조정

**Files:**
- Modify: `src\agents\usd_monitoring.py:42-64`(`SIGNALS`, 상수, `_EVIDENCE`), `:246-297`(`_market_week`)
- Modify: `src\agents\monitoring.py:83-89`(`WEEK_FIELDS`에 `mkt_score`, `mkt_worst_score` 추가)
- Modify: `src\agents\usd_early_warning.py:44-47`(지시문 market 줄), `:92-104`(`_SIGNAL_TEXT`)
- Modify: `src\evals\suites\early_warning_usd.py:26-31`(주석, 장면 주차가 바뀌면 값)
- Test: `tests\mockapis\test_usd_scenes.py`, `tests\agents\test_usd_early_warning.py`

**Interfaces:**
- Consumes: Task 2 `market_score`, `SCORE_DROP`, `SCORE_CRASH`.
- Produces (주차 행 필드, Task 4·5가 읽음): `mkt_score: int`, `mkt_worst_score: int`, `mkt_worst_platform: str`, `mkt_platform_scores: dict`, `mkt_score_parts: list[dict]`(Task 2 `parts`와 같은 모양). 기존 `mkt_*` 필드는 그대로.
- Produces (신호 코드): `market_score_drop`(market, weak), `market_score_crash`(market, strong). 둘 다 근거 `("W:mkt_score", "W:mkt_worst_score")`.

- [ ] **Step 1: 테스트 고치기** (`tests\mockapis\test_usd_scenes.py`)
  - `test_cancel_spike_supersedes_cancel_rise`, `test_cancel_spike_needs_a_rise_over_the_baseline`, `test_grade_down_fires_on_one_step_downgrade`를 지우고 아래로 바꾼다. `test_unknown_grade_is_not_comparable`는 그대로 둔다(기대값 `[]` 유지).

```python
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
```

  - `test_gearstore_market_signal_comes_first`: `codes(obs[1]) == ["market_score_drop"]`.
  - `test_linen_rating_drop_is_a_market_signal` → 이름 `test_linen_quality_drop_is_a_market_signal`, `"market_score_drop" in codes(o, "market")`.
  - `tests\agents\test_usd_early_warning.py`에 추가:

```python
def test_market_score_signals_have_fallback_text():
    from agents.usd_early_warning import _SIGNAL_TEXT
    assert _SIGNAL_TEXT["market_score_drop"][0] == "The marketplace score fell below its pre-loan level."
    assert _SIGNAL_TEXT["market_score_crash"][0] == "The marketplace score fell sharply."
    assert _SIGNAL_TEXT["market_score_drop"][1] == "Check the score breakdown on the marketplace accounts."
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests\mockapis\test_usd_scenes.py tests\agents\test_usd_early_warning.py`
Expected: 새·고친 테스트 FAIL.

- [ ] **Step 3: 구현**
  - `SIGNALS`에서 `cancel_rise`·`rating_drop`·`grade_down`·`cancel_spike`를 빼고 그 자리에 `"market_score_drop": ("market", "weak")`, `"market_score_crash": ("market", "strong")`. `_EVIDENCE`도 같이. `CANCEL_RISE_*`, `CANCEL_SPIKE_RATE`, `RATING_DROP` 상수 삭제.
  - `_market_week`: 기존 `mkt_*` 행 필드 채우기는 그대로 두고, 신호 판정을 `market_score(market.weeks, start, hi)` 결과로 바꾼다. 결과가 None이면 점수 필드를 넣지 않고 점수 신호도 없다. `account_warning`은 그대로.
  - `WEEK_FIELDS`에 `"mkt_score"`, `"mkt_worst_score"` 추가(에이전트 근거 카탈로그·숫자 검사용).
  - 지시문 market 줄: `W:n:mkt_* fields (gross, cancel rate, rating, grade, penalties, pending) and W:n:mkt_score (0-100, 100 = the seller at loan time; W:n:mkt_worst_score is the lowest major marketplace, mkt_score_parts lists the deductions).`
  - `_SIGNAL_TEXT`에 두 코드 추가(문구는 Step 1 테스트 그대로, crash의 확인 문구도 같은 문장). 옛 코드 항목은 지우지 않는다.

- [ ] **Step 4: 장면 재생 확인**

Run: `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests\mockapis\test_usd_scenes.py tests\mockapis\test_usd_market_flow.py tests\agents`
Expected: PASS. `test_scene_stage_ladder`가 깨지면 아래 순서로 맞춘다. 미리 본 값: linen은 3~8주 반품 18%(기준 2%)로 세 마켓 모두 반품 상한 25점 → 가중 75(drop)이고, 4~8주는 주 마켓 평점도 0.2 이상 떨어져 그 마켓이 60 이하(crash)로 갈 수 있다.
  1. linen이 `[("medium", 3), ...]`처럼 관찰 없이 중간으로 시작하면, 스펙 4.2에서 받아들인 대가다. `STAGE_SCENES["seller-linen-10"]`을 실제 사다리로 고치고 스펙 7장 ② 줄에 결과 주차를 적는다. 해소는 있어야 한다.
  2. 다른 장면(gearstore 8주 해소, dailylook 5주 중간·7주 높음, tint 6주 관찰→해소, 안정 셀러 사건 없음)이 깨지면 `usd_market_score.py` 계수·경계를 조정한다. 경고 남는 기간을 2주로 줄이는 것도 이 단계에서 할 수 있다(스펙 9장). 바꾼 값은 스펙 5.2·5.4 표와 전역 제약에 반영한다.
  3. 평가 고정값(`early_warning_usd.py` CASES)의 주차에 신호가 없어지면 같은 장면의 새 주차로 옮기고 주석을 고친다.

- [ ] **Step 5: 커밋** — `feat(agents): USD 마켓 신호를 마켓 점수로 대체`

---

### Task 4: 감시 그래프 점수 칩 줄

**Files:**
- Modify: `src\console\watch_chart.py:107-117`(칩 목록), 머리말 주석의 "마켓 칩 줄"
- Modify: `src\console\templates\_charts.html:171`(칩), `:176-188`(범례)
- Modify: `src\console\monitoring.py`(새 `PLATFORM_LABELS`)
- Modify: `src\console\locales\en\monitoring.json`
- Test: `tests\console\test_watch_chart.py`, `tests\console\test_usd_views.py`

**Interfaces:**
- Consumes: Task 3 행 필드, 일지 줄 `sources[].signals[].code`.
- Produces: `watch_chart(...)["grades"]` 항목에 키 추가 — `score: int | None`, `tone: "ok" | "warn" | "bad" | None`(점수 없으면 None), `worst: {"platform": str, "score": int} | None`(주요 마켓 최저가 85 이하일 때만), `parts: list[dict]`(최대 3개). 기존 `grade`·`signal`·`fit_w`는 유지(옛 행용).
- `PLATFORM_LABELS = {"coupang": "쿠팡", "naver_smart_store": "네이버 스마트스토어", "own_store": "자사몰", "11st": "11번가"}`, 영어 `Coupang`, `Naver Smart Store`, `Own store`, `11st`.

- [ ] **Step 1: 실패하는 테스트 작성** (`tests\console\test_watch_chart.py`)

```python
def _scored(weeks, lines, n, score, worst, codes, parts=()):
    weeks[n - 1]["row"].update(mkt_score=score, mkt_worst_score=worst, mkt_worst_platform="coupang",
                               mkt_score_parts=list(parts))
    line = next(ln for ln in lines if ln["week"] == n)
    line["sources"][2] = {"source": "market", "state": "signal" if codes else "normal",
                          "signals": [{"code": c} for c in codes]}


def test_market_chip_score_tone_follows_signal():
    weeks, lines = _dailylook()
    parts = [{"platform": "coupang", "metric": m, "points": p, "value": 0, "baseline": 0}
             for m, p in (("cancel", 9.0), ("grade", 6.8), ("rating", 2.0), ("returns", 1.0))]
    _scored(weeks, lines, 4, 93, 83, ["market_score_drop"], parts)
    _scored(weeks, lines, 5, 58, 50, ["market_score_crash"])
    _scored(weeks, lines, 6, 99, 99, [])
    chips = {g["week"]: g for g in _chart(weeks, lines)["grades"]}
    assert (chips[4]["score"], chips[4]["tone"], chips[4]["worst"]) == (93, "warn", {"platform": "coupang", "score": 83})
    assert [p["metric"] for p in chips[4]["parts"]] == ["cancel", "grade", "rating"]
    assert (chips[5]["tone"], chips[6]["tone"], chips[6]["worst"]) == ("bad", "ok", None)


def test_market_chip_falls_back_to_grade_for_old_rows():
    weeks, lines = _dailylook()
    _scored(weeks, lines, 7, 90, 90, [])
    chips = {g["week"]: g for g in _chart(weeks, lines)["grades"]}
    assert (chips[1]["score"], chips[1]["tone"], chips[1]["grade"]) == (None, None, "Gold")
    assert chips[7]["score"] == 90
```

`tests\console\test_usd_views.py`에 추가(기존 `_scene_weeks`·`_journal`·`_usd_page` 사용):

```python
def test_watch_chart_score_chip_tooltip_and_legend():
    weeks = _scene_weeks()
    weeks[1]["row"].update(mkt_score=93, mkt_worst_score=83, mkt_worst_platform="coupang",
                           mkt_platform_scores={"coupang": {"score": 83, "share": 0.85},
                                                "naver_smart_store": {"score": 100, "share": 0.15}},
                           mkt_score_parts=[
        {"platform": "coupang", "metric": "cancel", "points": 9.0, "value": 0.046, "baseline": 0.02}])
    weeks[1]["watch"]["signals"] = [_signal("market_score_drop", "market", "weak", "W:2:mkt_score", "W:2:mkt_worst_score")]
    card = _journal(_usd_page(weeks, "usd-score"))
    assert "Week 2 market score 93 · Coupang 83 (share" in card
    assert "Coupang cancel rate −9.0" in card
    assert "Market score (100 = at loan)</li>" in card
    assert "market-chip tone-warn" in card
```

영어 화면의 한국어 누락은 기존 `tests\console\test_i18n.py`(`test_every_msgid_has_english`, `test_catalog_values_are_english`)가 잡는다.

툴팁 문구는 msgid 단위로 번역한다: 앞부분 `"%(week)s주차 마켓 점수 %(v)s"`, 최저 마켓 `"%(platform)s %(score)s(비중 %(share)s)"` → `"%(platform)s %(score)s (share %(share)s)"`, 감점 `"%(platform)s %(metric)s −%(points)s"`(그대로). 조각 사이는 ` · `. 지표 이름은 `METRIC_LABELS = {"cancel": "취소율", "returns": "반품률", "rating": "평점", "grade": "등급", "penalty": "판매 경고"}`(영어 `cancel rate`, `return rate`, `rating`, `grade`, `seller warning`)를 `console\monitoring.py`에 둔다.

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests\console\test_watch_chart.py tests\console\test_usd_views.py`
Expected: 새 테스트 FAIL.

- [ ] **Step 3: 구현**
  - `watch_chart.py`: 칩 항목에 Interfaces의 키를 더한다. `tone`은 그 주 일지 줄의 market 신호 코드로 정한다(`market_score_crash` → bad, `market_score_drop` → warn, 그 밖 → ok). `worst`는 `mkt_worst_score <= 85`일 때만. `parts`는 `mkt_score_parts` 앞 3개.
  - `_charts.html:171`: `g.score is not none`이면 `<g class="market-chip tone-{{ g.tone }}{% if g.signal %} grade-signal{% endif %}">`에 숫자, 아니면 지금 등급 칩 그대로. 툴팁은 Step 1 아래 msgid 세 개를 ` · `로 잇고, 감점이 없으면 ` · 감점 없음`. 비중은 행 `mkt_platform_scores[platform].share`를 정수 백분율로(필드가 없으면 최저 마켓 조각을 `"%(platform)s %(score)s"`로).
  - 범례에 `마켓 점수(대출 시점 = 100)` 항목 추가(점수 칩 견본 + 문구).
  - CSS는 기존 칩 스타일 파일에 `market-chip`과 `tone-ok`(녹)·`tone-warn`(황)·`tone-bad`(적)을 기존 단계 색 변수로 더한다.
  - 새 msgid 전부 `locales\en\monitoring.json`에 번역 추가: `"%(week)s주차 마켓 점수 %(v)s": "Week %(week)s market score %(v)s"`, 최저 마켓 조각 두 개, 감점 조각, `"감점 없음": "no deductions"`, `"마켓 점수(대출 시점 = 100)": "Market score (100 = at loan)"`, 플랫폼·지표 라벨.

- [ ] **Step 4: 통과 확인** — 같은 명령 PASS, 이어서 `tests\console` 전체 PASS(번역 누락 검사 포함).

- [ ] **Step 5: 커밋** — `feat(console): 감시 그래프 마켓 점수 칩 줄`

---

### Task 5: 원장 표 점수 열과 신호·근거 라벨

**Files:**
- Modify: `src\console\monitoring.py:52-62`(`SIGNAL_LABELS`), `:89-105`(`EVIDENCE_LABELS`)
- Modify: `src\console\templates\loan.html:79-84`(마켓 표 머리·행)
- Modify: `src\console\locales\en\monitoring.json`
- Test: `tests\console\test_usd_views.py`

**Interfaces:**
- Consumes: Task 3 행 필드와 신호 코드.
- Produces: `SIGNAL_LABELS["market_score_drop"] = "마켓 점수 하락"`, `["market_score_crash"] = "마켓 점수 급락"`; `EVIDENCE_LABELS["mkt_score"] = ("마켓 점수", "count")`, `["mkt_worst_score"] = ("주요 마켓 최저 점수", "count")`.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_journal_market_score_signal_chips():
    weeks = [_watch_week(2, _watch("watch", incident_id="in-rp-w02", base_stage="watch",
                                   signals=[_signal("market_score_drop", "market", "weak",
                                                    "W:2:mkt_score", "W:2:mkt_worst_score")]),
                         mkt_score=93, mkt_worst_score=83)]
    card = _journal(_usd_page(weeks, "usd-score-chip"))
    for text in ("Market · Market score drop", "Market score 93", "Lowest major market score 83"):
        assert text in card, text


def test_market_snapshot_score_column():
    weeks = [_watch_week(n, _watch("normal", "normal"), **_market_row(n)) for n in range(1, 3)]
    weeks.append(_watch_week(3, _watch("watch", signals=[_signal("market_score_drop", "market", "weak",
                                                                 "W:3:mkt_score", "W:3:mkt_worst_score")]),
                             **_market_row(3, mkt_score=82, mkt_worst_score=82)))
    page = _usd_page(weeks, "usd-score-col")
    card = re.search(r'<section id="market-snapshot".*?</section>', page, re.S).group(0)
    assert "<th>Score</th>" in card
    rows = re.findall(r"<tr>(.*?)</tr>", card.split("<tbody>")[1], re.S)
    assert '<td class="cell-warn">82</td>' in rows[-1] and "<td>-</td>" in rows[0]
```

`test_market_snapshot_table_rows`의 셀 단언은 열이 하나 늘어도 그대로 통과해야 한다(값 기반 단언).

- [ ] **Step 2: 실패 확인** — `tests\console\test_usd_views.py` 새 2개 FAIL.

- [ ] **Step 3: 구현** — 라벨 사전 추가. `loan.html` 마켓 표 머리 `주차` 다음에 `<th>{{ _("점수") }}</th>`, 행에 `<td{{ cell_warn(hits, "mkt_score", "mkt_worst_score") }}>{{ r.mkt_score if r.mkt_score is number else "-" }}</td>`. 번역: `"마켓 점수 하락": "Market score drop"`, `"마켓 점수 급락": "Market score crash"`, `"마켓 점수": "Market score"`, `"주요 마켓 최저 점수": "Lowest major market score"`, `"점수": "Score"`.

- [ ] **Step 4: 통과 확인** — `tests\console` 전체 PASS.

- [ ] **Step 5: 커밋** — `feat(console): 마켓 점수 원장 열과 신호 라벨`

---

### Task 6: 전체 검증, 배포, 장면 재생 기록

**Files:**
- Modify: `docs\runbook\deploy-history.md`(배포·재생 결과 표)
- Modify: `docs\superpowers\specs\2026-11-27-usd-market-score-design.md`(상태: 구현됨, 7장 결과 주차)

- [ ] **Step 1: 전체 테스트**

Run: `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider`
Expected: 실패 0(직전 기준 7448 passed, 141 skipped에 새 테스트 수만큼 증가).

- [ ] **Step 2: 사용자 배포 승인 받기** — 운영 배포다. 진행 전에 사용자에게 묻는다.

- [ ] **Step 3: 배포 순서** — 계약이 `extra="forbid"`라 새 mockapi 페이로드(`returns_cents`)를 옛 오케스트레이터가 읽으면 실패한다. 그래서 **오케스트레이터 → mockapi → 콘솔** 순서다.
  1. PATH 새로 고침, azd env와 운영 리비전 이미지 digest 비교(드리프트 없음 확인).
  2. `python -m ops window open --acr`
  3. `python -m ops deploy orchestrator`
  4. mockapi·콘솔은 커밋 고정 배포(`git archive <sha>` → `deploy.deploy(subprocess_runner, '<svc>', root=..., tag='main-<sha>')`), 각 digest를 `azd env set SERVICE_<SVC>_IMAGE_NAME ...@<digest>`로 고정하고 ACR 이미지 잠금.
  5. `python -m ops window close` → `python -m ops smoke` 통과.
  Expected: 세 서비스 Healthy, smoke 통과.

- [ ] **Step 4: 장면 재생** — 콘솔에서 gearstore, linen, dailylook, tangerine, snackbox, campgear, tint-17 대출을 각각 재생하고, 단계 사다리와 마켓 점수 칩을 확인한다.
  Expected: Task 3에서 고정한 사다리와 ±1주 안에서 일치. 경보 없음 장면(tint, campgear)은 경보 문서가 없어야 한다.

- [ ] **Step 5: 기록과 태그** — `deploy-history.md`에 배포 리비전·digest·장면 결과 표를 추가하고, 스펙 상태를 "구현됨"으로 바꾼다. 커밋 `docs: USD 마켓 점수 배포·장면 재생 기록`. 태그 `demo-2026-11-27c`와 GitHub release(digest·롤백 절차)를 만든다.
