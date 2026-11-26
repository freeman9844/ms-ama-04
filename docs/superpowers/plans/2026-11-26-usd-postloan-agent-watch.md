# USD 사후 통합 감시·단계형 알람 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** USD 사후 13주 재생이 은행·정산·마켓 세 데이터원의 신호로 사건을 열고 관찰 → 중간 → 높음 → 해소 단계로 알람을 내며, 대출 화면은 "감시 비교" 대신 주마다 한 줄씩 쌓이는 "에이전트 감시 일지"를 보여 준다.

**Architecture:** Mock API가 주간 마켓 스냅샷(`/marketplace/flow`)을 만들고, 오케스트레이터가 USD 사후 동의에 `marketplace` 범위를 더해 `market_flow.json`으로 저장한다. 탐지기(`usd_monitoring.observe_usd`)가 신호마다 데이터원·강도를 붙이고, 순수 상태 기계(`agents\incident.py`)가 사건·기본 단계·호출 여부를 정한다. USD 전용 조사 모듈(`agents\usd_early_warning.py`)이 기본 단계 ±1 판단과 검증·폴백을 맡고, 오케스트레이터는 단계가 오를 때만 경보 문서를 만든다. 같은 주간 처리 함수(`incident.watch_week`)를 오프라인 장면 재생(`evals\usd_scenes.py`)도 써서 장면 순서를 테스트로 고정한다.

**Tech Stack:** Python 3.12, Pydantic v2, FastAPI(Mock API), Jinja2(콘솔), pytest, Azure AI Foundry Agents(게이트웨이 `gw-early-warning`)

**Spec:** `docs\superpowers\specs\2026-11-26-usd-postloan-agent-watch-design.md` (커밋 `8a6eeeb`). 실행자는 스펙과 이 계획을 함께 읽는다.

## Global Constraints

- 모든 경로는 worktree `C:\Users\jungwoonlee\orca\projects\ama-agent-04\.worktrees\agent-watch` 기준이다. 테스트는 거기서 `..\..\.venv\Scripts\python.exe -m pytest <대상> -q`로 돌린다.
- KRW 사후 모니터링은 바꾸지 않는다. KRW 경로(`seen_triggers` 기반 새 트리거 조사)와 기존 KRW 테스트는 그대로 통과해야 한다.
- 승인 근거(`approval_basis`, `basis_breach`) 로직과 승인 근거 카드는 바꾸지 않는다. `basis_breach`는 정산 데이터원의 강한 신호로 분류만 한다.
- 관찰(`watch`)과 해소는 경보 문서·차주 연락·추심 입력·`alert.raised`를 만들지 않는다. USD에서 `low` 심각도는 쓰지 않는다.
- 새 계약 필드는 모두 선택 필드다(옛 경보·대출 문서가 그대로 읽혀야 한다).
- 마켓 흐름은 은행·정산·세무 장부를 바꾸지 않는다: `tests\mockapis\test_usd_scenes.py::test_existing_seller_books_do_not_change`가 그대로 통과해야 한다.
- USD 에이전트 문장(why_now, outlook, adjust_reason, reason)과 USD 폴백 문구는 영어다(`usd_language.issues` 검사). 폴백 문구에는 숫자를 넣지 않는다.
- 에이전트 문장 속 숫자는 인용한 근거 값과 맞아야 한다(지금 검사와 같은 반올림 2% 이내).
- 경보는 대출 회수, 한도 축소, 부도 확정, 금리 변경을 권하지 않는다.
- 한 장면 재생의 조사 호출은 8회 이하다(테스트로 확인).
- 콘솔 새 문구는 한국어 원문 + `src\console\locales\en\monitoring.json` 영어 번역을 함께 둔다(`tests\console\test_i18n.py` 통과). 콘솔은 `console.opinion`을 import하지 않는다.
- 대출 화면에 "감시 비교", "은행만", "N주 먼저 감지" 같은 비교 트랙 문구가 다시 나오면 실패다.
- 배포는 수동 ACR 창 방식(`docs\runbook\deploy-history.md`)만 쓴다. `ops deploy`, `ops window`는 WAF 허용 목록을 덮어쓰므로 쓰지 않는다.
- 커밋 메시지 끝에 다음 두 줄을 붙인다.
  - `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`
  - `Copilot-Session: 05f1ca1b-6073-417d-bfb1-6b546f82cb58`

## Review Focus

1. **안정 셀러의 단주 잡음.** STABLE 셀러가 13주 동안 `orders_dip` 같은 단주 신호 잡음으로 사건을 열면 운영 화면이 소음이 된다. 사건이 하나도 열리지 않아야 한다 → Task 7 `test_stable_sellers_open_no_incident`.
2. **마켓 자료 없음.** Mock 실패나 옛 동의로 마켓 흐름이 없으면 재생은 13주를 끝까지 돌고, 신호는 은행·정산만 쓰며, 화면 마켓 칩은 "자료 없음"이다 → Task 3 `test_market_flow_failure_keeps_replaying`, Task 9 `test_journal_market_missing_shows_no_data_chip`.
3. **같은 주 재시도.** 경보 문서를 쓴 뒤 대출 문서 저장이 실패해 같은 주를 다시 처리하면, 경보·사건 단계·연락이 한 번만 남고 에이전트를 다시 부르지 않아야 한다 → Task 8 `test_usd_watch_retry_does_not_duplicate_alert`.
4. **옛 재생 문서.** `watch` 일지 없이 `bank_only_triggers`만 있는 옛 USD 대출 문서가 화면을 깨뜨리지 않고 일지 카드만 숨겨야 한다 → Task 9 `test_old_replay_hides_journal_and_comparison`.
5. **에이전트 실패·엉뚱한 출력.** 429, 비활성, 검증 실패, 단계와 다른 심각도의 경보를 올린 경우 모두 기본 단계 폴백으로 가고, 첫 높음 주에 1단계 안내가 그대로 나가야 한다 → Task 6 `test_alert_severity_must_match_stage`, Task 8 `test_usd_agents_disabled_still_escalates`.

---

## 파일 구조

| 파일 | 책임 | 작업 |
|---|---|---|
| `src\shared\usd_source.py` | `UsdMarketWeek`, `UsdMarketFlow` 계약 | 1 |
| `src\mockapis\sellerdata\usd_market_flow.py` (새) | 주간 마켓 스냅샷 생성 | 1 |
| `src\mockapis\postloan.py` | 시나리오별 마켓 훅, `MARKET_BLIP`, `UsdPostLoanFlow.market_weeks` | 1 |
| `src\mockapis\app.py`, `src\orchestrator\journey\adapters.py` | `/marketplace/flow`, `"market_flow"` 경로 | 2 |
| `src\orchestrator\journey\models.py`, `monitoring.py` | USD 동의 범위, 마켓 흐름 수집·저장·읽기 | 3, 8 |
| `src\agents\usd_monitoring.py`, `src\agents\monitoring.py` | 마켓 행 필드, 단주 신호, 신호 분류 | 4 |
| `src\agents\incident.py` (새) | 기본 단계, 사건 상태 기계, `watch_week` | 5 |
| `src\shared\contracts.py` | 경보 새 선택 필드 | 6 |
| `src\agents\usd_early_warning.py` (새) | USD 단계 출력, 검증, 폴백, `investigate_usd` | 6 |
| `src\agents\early_warning.py` | `EwContext`·`EwResult` 새 선택 필드, `_CENT_FIELDS` | 4, 6 |
| `src\evals\usd_scenes.py` (새) | 오프라인 장면 재생(테스트·평가 공용) | 7 |
| `src\console\monitoring.py`, `templates\loan.html`, `locales\en\monitoring.json` | 일지 카드, 경보 카드 필드, 마켓 표, 감시 비교 삭제 | 9 |
| `src\evals\suites\early_warning_usd.py` (새), `src\evals\__main__.py`, `src\evals\report.py` | USD 단계 평가 케이스 | 10 |
| `docs\AGENTS.md`, `docs\runbook\deploy-history.md` | 조기경보 설명, 배포·장면 기록 | 10, 11 |

---

### Task 1: 주간 마켓 스냅샷 계약과 생성기

**Files:**
- Modify: `src\shared\usd_source.py` (`UsdMarketplace` 아래)
- Create: `src\mockapis\sellerdata\usd_market_flow.py`
- Modify: `src\mockapis\postloan.py:83-205` (`FlowScenario`, `USD_POSTLOAN_SCENARIOS`, `UsdPostLoanFlow`, `generate_usd_postloan`, 새 `_usd_market_hooks`)
- Test: `tests\mockapis\test_usd_market_flow.py` (새)

**Interfaces:**
- Consumes: `UsdMarketplace`(심사 시점 `case.marketplace`), `UsdOrderDay`, `UsdSettlement`, `StorePenalty`, `shared.sellers.USD_PAYOUT_BUSINESS_DAYS`, `shared.krcalendar.add_business_days`, `usd_market.AVERAGE_ORDER_CENTS`·`DEFAULT_AVERAGE_ORDER_CENTS`
- Produces:
  - `UsdMarketWeek(UsdProduct)`: `week_end: date`, `platform_code: PlatformCode`, `orders: int (ge=0)`, `gross_cents: Cents`, `cancel_cents: Cents`, `rating: float (1~5)`, `seller_grade: str`, `pending_settlement_cents: Cents`, `penalties: tuple[StorePenalty, ...] = ()`
  - `UsdMarketFlow(UsdProduct)`: `weeks: tuple[UsdMarketWeek, ...]`
  - `usd_market_flow.GRADES = ("Standard", "Silver", "Gold", "Premium")`
  - `usd_market_flow.MarketHooks` (frozen dataclass): `cancel_mult: Callable[[date], float]`(기본 1.0), `rating_delta: Callable[[date], float]`(기본 0.0), `grade_steps: Callable[[date], int]`(기본 0), `penalties: tuple[StorePenalty, ...] = ()`
  - `build_usd_market_flow(base: UsdMarketplace, start: date, orders: Sequence[UsdOrderDay], settlements: Sequence[UsdSettlement], hooks: MarketHooks, seed: int, industry: str) -> UsdMarketFlow`
  - `FlowScenario.MARKET_BLIP = "market_blip"`, `USD_POSTLOAN_SCENARIOS["seller-tint-17"] = FlowScenario.MARKET_BLIP`
  - `UsdPostLoanFlow.market_weeks: tuple[UsdMarketWeek, ...] = ()`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests\mockapis\test_usd_market_flow.py`. 장면 테스트와 같은 입력을 쓴다: `demo.build_usd_cases(date(2026, 11, 24))`, `postloan.generate_usd_postloan(case, AS_OF, 1001, loan_id="loan-usd", amount_cents=500_000, installment_cents=84_000)`. 도우미:

```python
def week_rows(flow, n, platform=None):  # n주차: week_end == flow.start + 7n - 1
    end = flow.start + timedelta(days=7 * n - 1)
    return [w for w in flow.market_weeks if w.week_end == end and (platform is None or w.platform_code == platform)]

def cancel_rate(w):
    return w.cancel_cents / (w.gross_cents + w.cancel_cents)

def main_platform(case):
    return case.marketplace.platforms[0].platform_code
```

테스트(주력 플랫폼 기준선 `base` = 대출 전 8주 취소율 평균):

```python
@pytest.mark.parametrize("seller", ["seller-gearstore-06", "seller-linen-10", "seller-dailylook-01",
                                    "seller-tangerine-15", "seller-snackbox-22", "seller-tint-17", "seller-campgear-23"])
def test_preloan_weeks_match_underwriting_store(seller):
    # 21주(-7..13) × 플랫폼 수, 대출 전 8주의 rating·seller_grade == case.marketplace의 같은 플랫폼 값

def test_gearstore_cancel_rises_before_hold_and_warns_at_hold():
    # 1주차 취소율 < base*1.8; 2주차 >= base*1.8 and >= base+0.02 and <= 0.08
    # 3주차 주력 행 penalties에 kind == "warning" 하나; 9주차 취소율 < base*1.8

def test_linen_rating_drops_after_spike_then_recovers():
    # 주문액 가중 평점: 4~8주차 <= 기준 - 0.2, 9주차 >= 기준 - 0.1

def test_dailylook_main_grade_downgraded_from_week5():
    # 4주차 주력 등급 == 기준 등급, 5주차부터 GRADES 한 단계 아래(Standard면 그대로)

def test_tint_blip_is_week6_only():
    # 6주차 취소율 >= base*1.8 and >= base+0.02; 5·7주차 < base*1.8; 어느 주에도 penalties 없음

@pytest.mark.parametrize("seller", ["seller-tangerine-15", "seller-snackbox-22", "seller-campgear-23", "seller-bakery-20"])
def test_quiet_scenarios_have_no_market_events(seller):
    # penalties 없음, 주력 등급·평점이 21주 내내 기준값과 같음

def test_pending_grows_during_gearstore_hold():
    # 주력 pending_settlement_cents: 5주차 > 1주차
```

- [ ] **Step 2: 실패를 확인한다**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\mockapis\test_usd_market_flow.py -q`
Expected: FAIL (`market_weeks` 속성 없음 / import 오류)

- [ ] **Step 3: 계약과 생성기를 구현한다**

- `UsdMarketWeek`, `UsdMarketFlow`를 `usd_source.py`에 더한다.
- `build_usd_market_flow` 규칙:
  - 주차 k = -7..13, `week_end = start + 7k - 1`, 그 주 = `[week_end-6, week_end]`.
  - 주력 플랫폼은 `base.platforms[0]`(usd_market이 채널 순서 0번을 주력 이력으로 만든다). 훅은 주력 플랫폼에만 적용한다.
  - `gross_cents` = 그 주 `orders`의 `gross_cents` 합. `orders` = `gross_cents // AVERAGE_ORDER_CENTS[industry]`.
  - 기본 취소율 `r = cancel_90d_cents / gross_90d_cents`(플랫폼별). `cancel_cents = round(gross * r / (1 - r) * jitter * mult)`, `jitter`는 `random.Random(f"market-flow:{seed}")`의 0.85~1.15, `mult = hooks.cancel_mult(week_end)`.
  - `rating` = 플랫폼 심사 평점 + `round(hooks.rating_delta(week_end), 1)`, 1~5로 자른다. `seller_grade` = 심사 등급을 `GRADES`에서 `hooks.grade_steps(week_end)`만큼 내린다(최저 Standard).
  - `pending_settlement_cents` = 그 플랫폼 정산 중 `week_end < settled_on <= add_business_days(week_end, USD_PAYOUT_BUSINESS_DAYS.get(code, 0))`인 `net_cents` 합 + `week_end`까지 보류됐지만 아직 풀리지 않은 `held_cents - released_cents` 누계.
  - `penalties` = `hooks.penalties` 중 `occurred_on`이 그 주에 드는 것(주력 행에만).
- `postloan.py`:
  - `_usd_market_hooks(scenario, seller, start) -> MarketHooks`. 출발 값(장면 순서를 맞추며 Task 7에서 조정 가능):

    | 시나리오 | 훅 |
    |---|---|
    | `SETTLEMENT_HOLD` | `cancel_mult` = 2.5 (day 7 ≤ d < `USD_HOLD_RELEASE_DAY`), 경고 `StorePenalty(platform=주력, occurred_on=start+USD_ONSET_DAY, kind="warning")` |
    | `RETURN_SPIKE` | `rating_delta` = −0.3 (day 21 ≤ d ≤ `USD_RETURN_TO_DAY`) |
    | `SALES_SLUMP` | `grade_steps` = 1 (d ≥ day 28) |
    | `MARKET_BLIP` | `cancel_mult` = 2.5 (day 35 ≤ d ≤ day 41) |
    | 나머지 | `MarketHooks()` |

    상수 이름: `USD_MARKET_LEAD_DAY = 7`, `USD_CANCEL_RISE_MULT = 2.5`, `USD_RATING_DROP = -0.3`, `USD_RATING_FROM_DAY = 21`, `USD_GRADE_DOWN_DAY = 28`, `USD_BLIP_DAYS = (35, 41)`.
  - `_usd_scenario`는 `MARKET_BLIP`에 `{}`를 돌려준다(장부는 STABLE과 같다).
  - `generate_usd_postloan`은 `case.marketplace`가 있으면 대출 전·후 주문과 정산을 합친 목록으로 `build_usd_market_flow(..., seed=seed, industry=seller.industry)`를 불러 `market_weeks`에 넣는다. 없으면 `()`.

- [ ] **Step 4: 통과를 확인한다**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\mockapis\test_usd_market_flow.py tests\mockapis\test_usd_scenes.py tests\mockapis\test_postloan.py -q`
Expected: PASS (특히 `test_existing_seller_books_do_not_change` 그대로 통과)

- [ ] **Step 5: 커밋**

```bash
git add src/shared/usd_source.py src/mockapis/sellerdata/usd_market_flow.py src/mockapis/postloan.py tests/mockapis/test_usd_market_flow.py
git commit -m "feat(mockapis): USD 주간 마켓 스냅샷과 시나리오별 마켓 움직임"
```

---

### Task 2: `/marketplace/flow` 엔드포인트

**Files:**
- Modify: `src\mockapis\app.py` (`/pg/flow` 옆)
- Modify: `src\orchestrator\journey\adapters.py:36-44` (`SOURCE_PATHS`)
- Test: `tests\mockapis\test_app.py`

**Interfaces:**
- Consumes: Task 1 `UsdPostLoanFlow.market_weeks`, `UsdMarketFlow`; 기존 `authorize`, `product_flow`
- Produces: `GET /marketplace/flow` (인자는 `/pg/flow`와 같다) → `UsdMarketFlow`; `SOURCE_PATHS["market_flow"] = "/marketplace/flow"`

- [ ] **Step 1: 실패하는 테스트를 쓴다** (같은 파일의 `/pg/flow` 테스트가 토큰·대출 조건을 만드는 방식을 그대로 따른다)

```python
def test_marketplace_flow_requires_marketplace_scope(...):
    # account·pg 범위 토큰 → 403
def test_marketplace_flow_returns_usd_weeks(...):
    # marketplace 범위 토큰 + USD 대출 조건 → 200, currency == "USD", weeks 21 × 플랫폼 수
    # start=<flow.start>를 주면 week_end >= start인 13주 × 플랫폼 수만
def test_marketplace_flow_krw_case_is_404(...):
    # KRW 셀러 → 404 "no_flow"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\mockapis\test_app.py -q -k marketplace_flow`
Expected: FAIL (404 Not Found 경로 없음)

- [ ] **Step 3: 구현한다**

`get_marketplace_flow`는 `authorize(authorization, ConsentScope.MARKETPLACE, "/marketplace/flow")`, KRW 케이스는 `_error(404, "no_flow")`, USD는 `product_flow(...)`로 흐름을 얻어 `start <= week_end <= end`로 거른 `UsdMarketFlow(**USD_V1.model_dump(), weeks=...)`를 돌려준다.

- [ ] **Step 4: 통과를 확인한다**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\mockapis\test_app.py -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add src/mockapis/app.py src/orchestrator/journey/adapters.py tests/mockapis/test_app.py
git commit -m "feat(mockapis): /marketplace/flow 주간 마켓 스냅샷 엔드포인트"
```

---

### Task 3: USD 사후 동의에 마켓 범위, 마켓 흐름 수집·저장

**Files:**
- Modify: `src\orchestrator\journey\models.py:28`
- Modify: `src\orchestrator\journey\monitoring.py:148-165` (`_collect_pg_flow` 옆), `:233-290` (`start_replay`)
- Test: `tests\orchestrator\test_monitoring_handlers.py`

**Interfaces:**
- Consumes: Task 2 `"market_flow"` 소스, `UsdMarketFlow`
- Produces:
  - `models.USD_MONITORING_SCOPES = ("account", "pg", "marketplace")`
  - `_collect_market_flow(deps, token, terms, consent_id, loan_id) -> UsdMarketFlow | None`
  - `_market(deps, mon) -> UsdMarketFlow | None` (저장된 `market_flow.json`을 읽는다. 없거나 형식이 틀리면 `log.warning` 후 None)
  - USD `monitoring["market"]`: `"ok"` 또는 `"missing"` (KRW 문서에는 키가 없다)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`usd_registration(run_journey)` 도우미에 `seller="seller-dailylook-01"` 인자를 더해(기본값 유지) 쓴다.

```python
def test_usd_monitoring_consent_includes_marketplace(run_journey):
    # USD 재생 시작 → 모니터링 동의 문서 scopes == ["account", "pg", "marketplace"]
    # raw 컨테이너에 market_flow.json, loan["monitoring"]["market"] == "ok"

def test_krw_monitoring_consent_scopes_unchanged(mock_app):
    # KRW 재생 시작 → scopes == ["account", "pg"], "market" not in monitoring, market_flow.json 없음

def test_market_flow_failure_keeps_replaying(run_journey, caplog):
    # fetch("market_flow")만 {"weeks": [{"week_end": "<sentinel>"}]} 를 돌려주는 래퍼 mock
    # → monitoring["market"] == "missing", 재생 13주 완료, caplog.text에 sentinel 없음
```

- [ ] **Step 2: 실패를 확인한다**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\orchestrator\test_monitoring_handlers.py -q -k "marketplace or scopes_unchanged or market_flow_failure"`
Expected: FAIL

- [ ] **Step 3: 구현한다**

- `start_replay`: `scopes = list(USD_MONITORING_SCOPES if product else MONITORING_SCOPES)`.
- `_collect_market_flow`: `_collect_pg_flow`와 같은 흐름이되 **어떤 예외든** `_drop_if_consent_ended(...)` 후 `log.warning("Optional market flow unavailable: %s %s", loan_id, type(e).__name__)`, None을 돌려준다(예외를 다시 던지지 않는다). 성공하면 `put_consent_raw(deps, consent_id, "market_flow.json", raw)`.
- USD 분기에서 pg 수집 뒤 불러 `monitoring.update(market="ok" if market else "missing")`.

- [ ] **Step 4: 통과를 확인한다**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\orchestrator\test_monitoring_handlers.py -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add src/orchestrator/journey/models.py src/orchestrator/journey/monitoring.py tests/orchestrator/test_monitoring_handlers.py
git commit -m "feat(orchestrator): USD 사후 동의에 marketplace 범위와 마켓 흐름 저장"
```

---

### Task 4: 데이터원·강도가 붙은 신호와 마켓 행 필드

**Files:**
- Modify: `src\agents\usd_monitoring.py` (`UsdObservation`, `_ledger_week`, `observe_usd`, 새 상수·함수)
- Modify: `src\agents\monitoring.py:53-83` (`WEEK_FIELDS`), `src\agents\early_warning.py:78-80` (`_CENT_FIELDS`)
- Test: `tests\mockapis\test_usd_scenes.py`

**Interfaces:**
- Consumes: Task 1 `UsdMarketFlow`, `GRADES`; 기존 `RETURN_SPIKE_MULT`, `RETURN_SPIKE_PP`, `COST_SURGE_PP`
- Produces:
  - `SIGNALS: dict[str, tuple[str, str]]` 코드 → (source, strength), 스펙 §5.3 표 그대로. 은행: `inflow_drop` 약, `projected_shortfall`·`due_shortfall` 강. 정산: `orders_dip`·`returns_rising`·`deductions_rising`·`sales_slump`·`return_spike`·`cost_surge` 약, `settlement_hold`·`basis_breach` 강. 마켓: `cancel_rise`·`rating_drop`·`grade_down` 약, `cancel_spike`·`account_warning` 강.
  - 상수: `ORDERS_DIP_RATIO = 0.80`, `CANCEL_RISE_MULT = 1.8`, `CANCEL_RISE_PP = 0.02`, `CANCEL_SPIKE_RATE = 0.08`, `RATING_DROP = 0.2`
  - 신호 dict 모양: `{"code": str, "source": "bank"|"settlement"|"market", "strength": "weak"|"strong", "metric_ids": list[str]}`
  - `UsdObservation`에 마지막 필드 `signals: tuple[dict, ...] = ()` 추가
  - `observe_usd(loan, visible, pg, week, dues, market: UsdMarketFlow | None = None) -> UsdObservation`
  - 마켓이 있으면 행 필드: `mkt_gross_cents`, `mkt_cancel_rate`(주력, 4자리), `mkt_rating`(주문액 가중, 2자리), `mkt_grade`(주력), `mkt_penalties`(그 주 새 경고·정지 수), `mkt_pending_cents`. `WEEK_FIELDS`에 `mkt_gross`, `mkt_cancel_rate`, `mkt_rating`, `mkt_grade`, `mkt_penalties`, `mkt_pending`, `_CENT_FIELDS`에 `mkt_gross`, `mkt_pending`을 더한다.
  - 근거 ID: `inflow_drop`→`W:n:revenue_vs_expected`, `projected_shortfall`→`F:n:projected_min_balance_cents`, `due_shortfall`→`F:n:due_balance_cents`, `orders_dip`→`W:n:order_gross_cents`+`W:n:expected_orders_cents`, `returns_rising`·`return_spike`→`W:n:return_rate`, `deductions_rising`·`cost_surge`→`W:n:deduction_rate`, `sales_slump`→`W:n:orders_vs_expected`, `settlement_hold`→`W:n:held_cents`, `basis_breach`→첫 위반 근거의 지표(`W:n:recon_ratio` 또는 `W:n:orders_vs_prior_year`), `cancel_rise`·`cancel_spike`→`W:n:mkt_cancel_rate`, `rating_drop`→`W:n:mkt_rating`, `grade_down`→`W:n:mkt_grade`, `account_warning`→`W:n:mkt_penalties`.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`test_usd_scenes.py`의 `replay()`에 `market=True` 인자를 더해 `UsdMarketFlow(**USD_V1.model_dump(), weeks=flow.market_weeks)`를 `observe_usd`에 넘긴다. 테스트:

```python
def codes(o, source=None):
    return [s["code"] for s in o.signals if source is None or s["source"] == source]

def test_gearstore_market_signal_comes_first():
    obs, _ = replay("seller-gearstore-06")
    assert codes(obs[1]) == ["cancel_rise"]                       # 2주차: 마켓 약 신호만
    assert {"account_warning", "settlement_hold"} <= set(codes(obs[2]))
    assert all(s["strength"] == "strong" for s in obs[2].signals if s["code"] in ("account_warning", "settlement_hold"))

def test_single_week_signal_dropped_when_persistent_fires():
    obs, _ = replay("seller-linen-10")
    fired = [o for o in obs if "return_spike" in codes(o)]
    assert fired and all("returns_rising" not in codes(o) for o in fired)

def test_explained_inflow_is_not_a_signal():
    obs, _ = replay("seller-campgear-23")
    assert any(o.explained for o in obs) and all("inflow_drop" not in codes(o) for o in obs if o.explained)

def test_no_market_means_no_market_fields_or_signals():
    obs, _ = replay("seller-gearstore-06", market=False)
    assert all(not any(k.startswith("mkt_") for k in o.rows[-1]) and not codes(o, "market") for o in obs)

def test_signal_metric_ids_exist_in_rows():
    # 모든 장면 셀러, 모든 주: 각 metric_id의 필드가 그 주 행(W:) 또는 전망(F:) 필드로 존재
```

- [ ] **Step 2: 실패를 확인한다**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\mockapis\test_usd_scenes.py -q`
Expected: FAIL (`signals` 속성 없음)

- [ ] **Step 3: 구현한다**

- `_ledger_week`는 `(triggers, weekly)`를 돌려준다. `weekly`는 이번 주만 본 `orders_dip`(`order_gross_cents < ORDERS_DIP_RATIO * expected_orders_cents`), `returns_rising`(이번 주 `return_rate >= spike`), `deductions_rising`(이번 주 `deduction_rate >= base_deductions + COST_SURGE_PP`).
- 새 `_market_week(row, market, start, hi) -> list[str]`: 기준선은 `week_end < start`인 8주(주력 취소율 평균, 가중 평점 평균, 주력 등급). `cancel_spike`가 서면 `cancel_rise`는 넣지 않는다. 등급 비교는 `GRADES` 인덱스.
- 새 `signal_list(codes, row, week) -> tuple[dict, ...]`: `SIGNALS` 순서로 정렬, 근거 ID는 위 표.
- `observe_usd`: 기존 `triggers`·`candidates` 계산은 그대로 둔다. 신호 코드 = 은행(`triggers` 중 은행 코드) + 정산(`triggers` 중 정산 코드 + 대응 지속 신호가 없는 `weekly`) + 마켓. `pg is None` 조기 반환 경로도 은행·마켓 신호를 채운다.

- [ ] **Step 4: 통과를 확인한다**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\mockapis\test_usd_scenes.py tests\agents -q`
Expected: PASS (기존 장면·트랙 테스트 포함)

- [ ] **Step 5: 커밋**

```bash
git add src/agents/usd_monitoring.py src/agents/monitoring.py src/agents/early_warning.py tests/mockapis/test_usd_scenes.py
git commit -m "feat(agents): USD 신호에 데이터원·강도와 마켓·단주 신호 추가"
```

---

### Task 5: 사건 상태 기계 (`agents\incident.py`)

**Files:**
- Create: `src\agents\incident.py`
- Test: `tests\agents\test_incident.py` (새)

**Interfaces:**
- Consumes: Task 4 신호 dict 모양. 조사 결과는 덕 타이핑으로 `stage`, `agent`, `why_now`, `outlook`, `adjust_reason` 속성만 읽는다(Task 6 `EwResult`).
- Produces:
  - `STAGES = ("watch", "medium", "high")`, `QUIET_TO_RESOLVE = 2`
  - `new_watch() -> dict`: `{"open": None, "closed": [], "suppressed": [], "suppress_quiet": 0, "stages": []}`
  - `base_stage(signals) -> str | None`: 신호 없음 None, 은행 강 → `high`, 약 신호 데이터원 2개 이상 또는 정산·마켓 강 → `medium`, 그 밖 → `watch`
  - `signal_key(signals) -> list[str]`: 정렬된 `"source:code"`
  - `WeekPlan` (frozen dataclass): `action: Literal["normal", "suppressed", "quiet", "resolve", "call", "hold"]`, `base: str | None`
  - `plan_week(watch, signals, *, high_recheck: bool) -> WeekPlan`
  - `apply_week(watch, plan, signals, *, week, replay_id, stage=None) -> dict` (`stage`는 `call`일 때 `"watch"|"medium"|"high"|"resolve"`)
  - `watch_week(watch, signals, *, week, replay_id, high_recheck, explained: bool, market: bool, investigate: Callable[[str], Any]) -> tuple[dict, Any | None]` — 일지 줄과 조사 결과(호출 안 했으면 None)
  - 사건 dict: `{"id": f"in-{replay_id}-w{week:02d}", "opened_week", "stage", "last_key", "quiet", "alert_id": None}`; 닫히면 `closed_week`, `close_reason`("quiet"|"agent")를 붙여 `closed`로 옮긴다.
  - 일지 줄(이후 Task 8·9가 그대로 쓴다):

    ```
    {"stage": "normal"|"watch"|"medium"|"high"|"resolved",
     "kind": "normal"|"explained"|"suppressed"|"quiet"|"resolved_quiet"|"resolved_agent"|"call"|"hold",
     "incident_id", "base_stage", "quiet_weeks": int, "stage_up": bool,
     "signals": [신호 dict], "market": bool,
     "agent": "agent"|"fallback"|"stored"|None, "why_now", "outlook", "adjust_reason",
     "alert_id": None, "alert_action": None}
    ```

**상태 전이 (plan_week → apply_week):**

| 상황 | action | 일지 stage | 비고 |
|---|---|---|---|
| 열린 사건 없음, 억제 없음, 신호 없음 | `normal` | normal | `explained`면 kind `explained` |
| 열린 사건 없음, 억제 중, 신호가 모두 억제 코드 | `suppressed` | normal | `suppress_quiet = 0` |
| 열린 사건 없음, 억제 중, 신호 없음 | `normal` | normal | `suppress_quiet += 1`, 2가 되면 억제 해제 |
| 열린 사건 없음, 신호 있음(억제 밖 코드 포함) | `call` | 결정 단계 | 억제 해제, 사건 열기 |
| 열린 사건, 신호 없음 | `quiet` 또는 `resolve` | 사건 단계 / resolved | `quiet += 1`, 2면 `resolved_quiet`로 닫기(억제 없음) |
| 열린 사건, 신호 키가 `last_key`와 다름 | `call` | 결정 단계 | `quiet = 0` |
| 열린 사건, 같은 키, 단계 high, `high_recheck` | `call` | 결정 단계 | |
| 그 밖 | `hold` | 사건 단계 | kind `hold` |
| `call` 결과 `resolve` | — | resolved | `resolved_agent`, 그 사건 신호 코드를 `suppressed`로 |

`stage_up`은 결정 단계가 `medium`·`high`이고 이전 단계(새 사건이면 없음)보다 높을 때만 True다. 매주 일지 stage를 `watch["stages"]`에 덧붙인다(Task 8의 `next_stage` 입력).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def sig(code, source, strength="weak"):
    return {"code": code, "source": source, "strength": strength, "metric_ids": []}

def test_base_stage_rules():
    assert base_stage([]) is None
    assert base_stage([sig("cancel_rise", "market"), sig("rating_drop", "market")]) == "watch"
    assert base_stage([sig("cancel_rise", "market"), sig("orders_dip", "settlement")]) == "medium"
    assert base_stage([sig("settlement_hold", "settlement", "strong")]) == "medium"
    assert base_stage([sig("account_warning", "market", "strong")]) == "medium"
    assert base_stage([sig("due_shortfall", "bank", "strong")]) == "high"

def test_new_signals_open_incident_and_call()
def test_same_signals_hold_unless_high_recheck()
def test_two_quiet_weeks_resolve_without_call()         # 1주 quiet(단계 유지) → 2주 resolved_quiet, open None, suppressed == []
def test_agent_resolve_suppresses_until_new_code()      # 같은 코드 → suppressed, 새 코드 → call
def test_suppression_lifts_after_two_quiet_weeks()
def test_stage_up_only_when_rising_to_medium_or_high()  # 새 사건 watch False, watch→medium True, medium→medium False, high→medium False
def test_watch_week_calls_investigate_once_per_call(): # hold·quiet·normal 주에는 investigate가 불리지 않는다
def test_stages_feed_next_stage():
    # stages [... "high"] → escalation.next_stage(stages, {}) == 1
    # stages [..., "high", "high"], {"stage1": {...}} → 2; [..., "high", "medium", "high"] → None
```

- [ ] **Step 2: 실패를 확인한다**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\agents\test_incident.py -q`
Expected: FAIL (모듈 없음)

- [ ] **Step 3: 구현한다** — 순수 함수만. I/O·시계·로그 없음. `watch_week`는 `plan_week` → (`call`이면 `investigate(plan.base)`) → `apply_week(stage=result.stage)` → 일지에 `signals`, `market`, 결과 필드를 채운다.

- [ ] **Step 4: 통과를 확인한다**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\agents\test_incident.py tests\agents\test_escalation.py -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add src/agents/incident.py tests/agents/test_incident.py
git commit -m "feat(agents): USD 사건 상태 기계와 기본 단계 규칙"
```

---

### Task 6: USD 단계 조사 에이전트·검증·폴백

**Files:**
- Modify: `src\shared\contracts.py:503-543` (`AlertSignal`, `EarlyWarningAlert`)
- Modify: `src\agents\early_warning.py` (`EwContext` 새 필드, `get_baseline` USD 추가 항목, `EwResult` 새 필드)
- Create: `src\agents\usd_early_warning.py`
- Test: `tests\agents\test_usd_early_warning.py` (새)

**Interfaces:**
- Consumes: Task 4 신호 dict, Task 5 `base_stage`; 기존 `EwContext`, `check_alert`, `build_alert`, `_Session`, `usd_language`, `_USD_TRIGGERS`
- Produces:
  - 계약: `AlertSignal.source: Literal["bank", "settlement", "market"] | None = None`; `EarlyWarningAlert.incident_id: str | None = None`; `why_now`, `outlook`, `adjust_reason`: `str | None = Field(default=None, min_length=1, max_length=200)`
  - `EwContext` 새 선택 필드: `signals: Sequence[Mapping[str, Any]] = ()`, `base_stage: str | None = None`, `journal: Sequence[Mapping[str, Any]] = ()`(최근 6줄), `incident_id: str | None = None`. USD `get_baseline`은 `signals`, `base_stage`, `journal`을 함께 돌려준다. 마켓 지표는 `get_weekly_series` 행의 `mkt_*` 필드로 준다(별도 도구 없음).
  - `EwResult` 새 선택 필드: `stage: str | None = None`, `agent: str | None = None`, `why_now`, `outlook`, `adjust_reason`: `str | None = None`
  - `UsdStageOutput(BaseModel, extra="forbid")`: `stage: Literal["watch", "medium", "high", "resolve"]`, `why_now: str`, `outlook: str`, `adjust_reason: str = ""`, `reason: str`
  - `usd_ew_spec(model) -> AgentSpec` (이름 `EW_AGENT`, 도구 `TOOLS`, 출력 `UsdStageOutput`, 영어 지시문 `USD_STAGE_INSTRUCTIONS`)
  - `source_of_metric(metric_id) -> str | None`: `W:n:mkt_*` → market; `F:*`, `W:n:revenue*`, `W:n:end_balance_cents`, `W:n:buffer_days` → bank; 그 밖 `W:n:*` → settlement; `B:*` → None
  - `check_stage(out, ctx) -> list[str]`
  - `usd_fallback_alert(ctx, stage) -> EarlyWarningAlert`
  - `fallback_stage(ctx, reason) -> EwResult`
  - `investigate_usd(ctx, *, runner, model, checkpoint=None) -> EwResult`

**검증 규칙 (`check_stage`, 수준 resolve=-1, watch=0, medium=1, high=2):**
- `|수준(stage) - 수준(base)| <= 1`
- `high`는 은행 강 신호가 있을 때만
- 강 신호가 하나라도 있으면 `watch`, `resolve` 불가
- 약 신호가 한 데이터원에만 있으면 `medium`까지
- `stage != base`면 `adjust_reason`이 있어야 하고, 카탈로그에 있는 근거 ID를 하나 이상 인용하며, 그중 하나는 가장 강한 신호들의 데이터원과 **다른** 데이터원(`source_of_metric`)이어야 한다
- `why_now`, `outlook`, `adjust_reason`, `reason`은 `usd_language.issues`(영어·숫자 근거) 통과
- `stage`가 `medium`·`high`면 `ctx.accepted`가 있고 그 `severity == stage`; `watch`·`resolve`면 `ctx.accepted`가 없어야 한다

**폴백:** 단계 = `ctx.base_stage`. `medium`·`high`면 `usd_fallback_alert`(신호는 `ctx.signals[:6]`에서 만든다: kind는 `orders_dip`→`inflow_drop`, `returns_rising`→`return_spike`, `deductions_rising`→`cost_surge`, 마켓 코드→`other`, 그 밖은 `_TRIGGER_TEXT`의 kind; 문장은 숫자 없는 영어 고정 문장; `source` 채움; 원인은 `fallback_alert`와 같은 후보 규칙). `why_now`·`outlook`은 숫자 없는 고정 문장(첫 신호의 데이터원별 `WHY_NOW_FALLBACK`, 단계별 `OUTLOOK_FALLBACK`). `agent="fallback"`, `decision`은 경보가 있으면 `"fallback_alert"`, 없으면 `"fallback_stage"`.

**성공 결과:** 경보에 `incident_id`, `why_now`, `outlook`, `adjust_reason`, 신호별 `source`, `models={"investigator": model}`을 채운다. `decision = stage`, `agent="agent"`.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

도우미 `usd_ctx(signals, base, rows=None)`는 USD 최소 `EwContext`를 만든다(`terms = {**USD_V1.model_dump(), "amount_cents": 500000, "tenor_months": 6}`, 거래 없음, 행에 `revenue_cents`, `mkt_rating`, `held_cents` 등). 가짜 러너 `ScriptedStage(output, alert_args=None)`는 `alert_args`가 있으면 `handlers["raise_alert"]`를 부른 뒤 `UsdStageOutput`을 돌려준다.

```python
def test_check_stage_rejects_high_without_strong_bank()
def test_check_stage_rejects_more_than_one_step()             # base watch → high 거절
def test_check_stage_rejects_watch_or_resolve_with_strong_signal()
def test_adjusted_stage_needs_reason_citing_other_source()   # base high(은행 강) → medium: W:n:mkt_rating 인용 통과, F:n:... 만 인용 거절, 빈 이유 거절
def test_ungrounded_number_in_why_now_rejected()
def test_alert_severity_must_match_stage()                   # stage high인데 medium 경보 → fallback, stage == base
def test_fallback_uses_base_stage_and_number_free_text()     # runner None → stage == base, why_now·outlook에 숫자 없음, agent == "fallback"
def test_market_only_medium_fallback_alert_has_signals()     # triggers 없음 + orders_dip·grade_down → 경보 signals 2개, source 채움
def test_watch_result_has_no_alert()
def test_old_alert_documents_still_validate()                # 새 필드 없는 옛 경보 dict → EarlyWarningAlert.model_validate 통과
```

- [ ] **Step 2: 실패를 확인한다**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\agents\test_usd_early_warning.py -q`
Expected: FAIL (모듈 없음)

- [ ] **Step 3: 구현한다**

`USD_STAGE_INSTRUCTIONS`(영어)에 담을 것: 세 데이터원, 기본 단계 표와 ±1 규칙(검증 규칙 그대로), medium·high면 `raise_alert`를 `severity=stage`로 먼저 부를 것, `why_now`(어느 데이터원이 먼저 움직였고 무엇이 뒤따랐나), `outlook`(다음 1~3주, 숫자는 근거 값만), 높음 유지 주에는 짧게 쓸 것, 회수·한도·금리 권고 금지, 가명 토큰 복원 금지. 기존 `investigate`의 USD 분기는 건드리지 않는다(오케스트레이터가 더는 부르지 않는다).

- [ ] **Step 4: 통과를 확인한다**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\agents tests\shared -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add src/shared/contracts.py src/agents/early_warning.py src/agents/usd_early_warning.py tests/agents/test_usd_early_warning.py
git commit -m "feat(agents): USD 단계형 조기경보 출력·검증·폴백"
```

---

### Task 7: 오프라인 장면 재생과 장면 순서 고정

**Files:**
- Create: `src\evals\usd_scenes.py`
- Modify: `tests\mockapis\test_usd_scenes.py` (트랙 비교 테스트를 단계 장면 테스트로 교체)
- 필요 시 Modify: Task 1 훅 상수, Task 4 임계값 상수, 스펙 §5.2·§5.3 표

**Interfaces:**
- Consumes: Task 1 `generate_usd_postloan`(`market_weeks`), Task 4 `observe_usd(..., market=)`, Task 5 `new_watch`, `watch_week`, Task 6 `investigate_usd`, `fallback_stage`; `escalation.next_stage`
- Produces:
  - `scene_replay(seller, *, decide: Callable[[EwContext], EwResult] | None = None, market: bool = True, as_of: date = date(2026, 11, 24)) -> list[dict]` — 13주 일지 줄 목록(각 줄에 `"week"`와 `"called": bool` 추가). 기본 `decide`는 `fallback_stage(ctx, "agents_disabled")`.
  - `scene_context(seller, week, *, as_of=...) -> EwContext` — 그 주가 `call`이 되는 장면의 조사 문맥(앞 주들은 폴백으로 재생). 그 주가 `call`이 아니면 `ValueError`.
  - 높음 재확인: 2단계는 `next_stage(watch["stages"], escalation)`가 2를 돌려준 주에 보낸 것으로 보고, 그 뒤 `high_recheck=False`.
  - 대출 조건은 기존 장면 하네스와 같다: `loan_id="loan-usd"`, `amount_cents=500_000`, `installment_cents=84_000`, `tenor_months=6`, seed 1001.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def ladder(lines):  # normal을 빼고 연속 중복을 없앤 (stage, 첫 주) 목록
    ...

SCENES = {  # 스펙 §7, 주차는 ±1 허용
    "seller-gearstore-06": [("watch", 2), ("medium", 3), ("high", 5), ("resolved", 9)],
    "seller-linen-10": [("watch", 3), ("medium", 4), ("resolved", 10)],
    "seller-dailylook-01": [("watch", 4), ("medium", 5), ("high", 6)],
    "seller-snackbox-22": [("medium", 3)],
    "seller-tint-17": [("watch", 6), ("resolved", 8)],
}

@pytest.mark.parametrize("seller", sorted(SCENES))
def test_scene_stage_ladder(seller):
    got = ladder(scene_replay(seller))
    assert [s for s, _ in got] == [s for s, _ in SCENES[seller]]   # 순서는 정확히
    assert all(abs(w - e) <= 1 for (_, w), (_, e) in zip(got, SCENES[seller]))

def test_gearstore_sources_spread_market_then_settlement_then_bank():
    # 데이터원별 첫 신호 주: market < settlement < bank

def test_campgear_explained_and_no_incident():
    lines = scene_replay("seller-campgear-23")
    assert all(l["stage"] == "normal" for l in lines) and sum(l["kind"] == "explained" for l in lines) >= 3

def test_tint_blip_never_alerts():
    assert all(l["stage"] in ("normal", "watch", "resolved") for l in scene_replay("seller-tint-17"))

def test_stable_sellers_open_no_incident():
    # USD_SELLERS 중 사후 시나리오가 STABLE이고 심사 마켓 자료가 있는 셀러 전부: 모든 줄 stage == "normal"

@pytest.mark.parametrize("seller", sorted(SCENES) + ["seller-tangerine-15", "seller-campgear-23"])
def test_scene_agent_calls_at_most_eight(seller):
    assert sum(l["called"] for l in scene_replay(seller)) <= 8

def test_tangerine_agent_keeps_medium():
    # decide = investigate_usd + 가짜 러너: base가 high면 stage "medium" + adjust_reason에 그 주 W:n:mkt_rating 인용,
    # 아니면 base 그대로(medium·high면 raise_alert(severity=stage) 먼저)
    lines = scene_replay("seller-tangerine-15", decide=...)
    assert [s for s, _ in ladder(lines)][:2] == ["watch", "medium"] and "high" not in {l["stage"] for l in lines}
    assert all(l["agent"] == "agent" for l in lines if l["called"])   # 낮춘 판단이 검증을 통과했다

def test_market_missing_still_detects_settlement_and_bank():
    lines = scene_replay("seller-gearstore-06", market=False)
    assert all(not l["market"] for l in lines) and "high" in {l["stage"] for l in lines}
```

기존 `track_summary`를 쓰는 `test_scene1_*`, `test_scene2_*`, `test_scene3_*`, `test_track_summary_*`, `test_explained_weeks_*`는 지운다(장면 1의 "S003 근거 훼손 + 정산 양도 원인" 확인은 `obs` 트리거 기준으로 다시 쓴다). `test_bank_only_track_equals_detector_without_ledger`는 남긴다.

- [ ] **Step 2: 실패를 확인한다**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\mockapis\test_usd_scenes.py -q`
Expected: FAIL (모듈 없음)

- [ ] **Step 3: `src\evals\usd_scenes.py`를 구현한다** — `test_usd_scenes.py`의 `underwrite_at`·`replay` 준비 과정(심사, `usd_baseline`, `approval_basis`, 흐름 생성)을 그대로 옮기고, 매주 `observe_usd` → `watch_week(investigate=lambda base: decide(ctx))`를 돈다. 문맥 `EwContext`는 Task 8 오케스트레이터와 같은 필드로 만든다(`mask`는 항등 함수).

- [ ] **Step 4: 장면을 맞춘다**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\mockapis\test_usd_scenes.py -q`
Expected: PASS. 순서가 어긋나면 Task 1 훅 날짜·크기와 Task 4 임계값만 조정한다(테스트의 순서 기대값은 바꾸지 않는다). 임계값을 바꾸면 스펙 §5.2·§5.3 표도 같은 커밋에서 고친다. 주차가 ±1을 넘게 어긋나 맞출 수 없으면 멈추고 보고한다.

- [ ] **Step 5: 전체 회귀**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\mockapis tests\agents -q`
Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git add src/evals/usd_scenes.py tests/mockapis/test_usd_scenes.py src/mockapis src/agents docs/superpowers/specs
git commit -m "test: USD 감시 장면 단계 순서 고정과 오프라인 장면 재생"
```

---

### Task 8: 오케스트레이터 USD 주간 처리를 사건·단계로 연결

**Files:**
- Modify: `src\orchestrator\journey\monitoring.py` (`start_replay` 초기 문서, `process_week` USD 분기 `:553-650`, `_escalate` `:657-735`)
- Test: `tests\orchestrator\test_monitoring_handlers.py`

**Interfaces:**
- Consumes: Task 3 `_market`, Task 4 `observe_usd(..., market=)`, Task 5 `new_watch`, `watch_week`, Task 6 `investigate_usd`, `EwResult` 새 필드
- Produces:
  - USD `monitoring["watch"]`(Task 5 모양), 재생 시작마다 `new_watch()`로 초기화. KRW는 `None`.
  - USD 주차 항목 `entry["watch"]` = 일지 줄(Task 5 모양, `alert_id`·`alert_action` 채움)
  - `_escalate(deps, loan_id, replay_id, loan, alert_id, history: list[str], row, hi, week, pg, lb)` — `severity` 대신 단계 이력. KRW 호출부는 `alert_severities`에 덧붙인 뒤 그 목록을 넘긴다.
  - 새 `_investigate_usd_week(deps, loan, ..., base) -> EwResult`

**USD 주간 처리 순서:**
1. `market = _market(deps, mon)`; `obs = observe_usd(loan, visible, pg, week, due_amounts, market=market)`.
2. `high_recheck = not isinstance(mon["escalation"].get("stage2"), dict)`.
3. `journal, result = watch_week(mon["watch"], obs.signals, week=..., replay_id=..., high_recheck=..., explained=bool(obs.explained), market=market is not None, investigate=...)`.
4. `investigate(base)`: 이번 주 ID `ew-{replay_id}-w{week:02d}` 경보 문서가 이미 있으면(재시도) 에이전트를 부르지 않고 `EwResult(stage=저장 severity, agent="stored", why_now·outlook·adjust_reason=저장값, alert=저장 경보)`. 없으면 `EwContext(..., signals=obs.signals, base_stage=base, journal=최근 6주 entry["watch"], incident_id=열린 사건 id)`로 `run_consent_agent(deps, mon["consent_id"], investigate_usd, ctx, model=deps.ew_model)`.
5. `journal["stage_up"]`이고 `result.alert`가 있으면: 경보 문서 upsert(지금 필드 + `incident_id`), 열린 사건 `alert_id` 기록, `mon["alert_ids"]`·`entry["alert_id"]`, `alert_action="created"`.
6. 아니고 `call`이었으며 열린 사건에 `alert_id`가 있고 결과 경보의 `{primary_cause} ∪ secondary_causes`가 저장 문서와 다르면: 저장 문서의 `primary_cause`, `secondary_causes`, `why_now`, `outlook`만 같은 ID로 갱신, `alert_action="updated"`, `entry["alert_id"]`는 비워 둔다(`alert.raised` 재발행 없음).
7. `journal["stage"] == "high"`면 `_escalate(..., alert_id=열린 사건 alert_id, history=mon["watch"]["stages"], ...)`.
8. `entry["investigation"]`은 호출한 주에만 채운다. `entry["watch"] = journal`. 기존 `bank_only_triggers`, `explained`, `basis` 기록은 그대로 둔다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

가짜 러너 `StageAgents`: EW는 `handlers["get_baseline"]({})["base_stage"]`를 그대로 단계로 쓰고, medium·high면 `raise_alert(severity=stage, ...)`(영어 문장, 첫 원인 후보·신호 근거 인용)를 먼저 부른다. 호출 수를 센다. Collections는 `EnglishHigh`와 같이 오프라인 폴백.

```python
def test_usd_watch_journal_and_alerts(run_journey):           # gearstore
    # 모든 주 entry["watch"] 있음; 단계 사다리에 watch → medium → high 순서
    # 경보 문서 수 == stage_up 주 수; TOPIC_ALERT 메시지 수 == 경보 문서 수
    # 관찰 주에는 경보·연락 없음; 1단계 연락 ref f"{rid}-s1"의 on == 첫 high 주 끝날
    # EW 호출 수 <= 8

def test_usd_cause_update_keeps_alert_id_and_does_not_republish(run_journey):   # linen
    # 같은 경보 ID의 primary_cause가 나중 주에 "return_spike"로 바뀜, alert_action "updated" 줄 존재
    # TOPIC_ALERT 메시지 수는 갱신 전후 같음

def test_usd_agents_disabled_still_escalates(run_journey):    # dailylook, agents=None
    # 모든 호출 줄 agent == "fallback"; 첫 high 주에 1단계 연락 발송

def test_usd_watch_retry_does_not_duplicate_alert(run_journey):
    # 첫 stage_up 주에 LOANS 저장을 한 번 실패시킨 뒤 같은 주 재처리
    # → 경보 문서 1개, 그 주 EW 호출 추가 없음(agent == "stored"), watch["stages"]에 그 주 1번

def test_replay_restart_resets_watch(run_journey):
    # 재생 두 번 시작 → monitoring["watch"] == new_watch()
```

`usd_registration(run_journey, seller=...)`를 쓴다. 기존 `EnglishHigh`는 `UsdStageOutput`을 돌려주도록 고친다: `check_stage`가 받아 주는 가장 높은 단계(은행 강 신호가 있으면 high, 아니면 base)와 그 단계의 `raise_alert`. `test_usd_officer_to_servicing`의 기존 단언(2단계 제안, 플랜 변경 등)은 그대로 둔다. dailylook 장면이 더는 2주 연속 높음에 닿지 않아 그 단언이 깨지면 단언을 느슨하게 하지 말고 멈춰서 보고한다.

- [ ] **Step 2: 실패를 확인한다**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\orchestrator\test_monitoring_handlers.py -q -k "usd_watch or cause_update or agents_disabled or restart_resets"`
Expected: FAIL

- [ ] **Step 3: 구현한다** (위 처리 순서, KRW 분기는 `_escalate` 인자 변경 외에 손대지 않는다)

- [ ] **Step 4: 통과를 확인한다**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\orchestrator tests\agents -q`
Expected: PASS (KRW 테스트 포함)

- [ ] **Step 5: 커밋**

```bash
git add src/orchestrator/journey/monitoring.py tests/orchestrator/test_monitoring_handlers.py
git commit -m "feat(orchestrator): USD 주간 처리를 사건·단계형 경보와 에스컬레이션에 연결"
```

---

### Task 9: 대출 화면 — 에이전트 감시 일지, 경보 카드 필드, 주간 마켓 스냅샷

**Files:**
- Modify: `src\console\monitoring.py` (라벨 사전, `MONITORING_FILTERS`, `loan_view`, `_track_view` 삭제, 새 `_journal_view`)
- Modify: `src\agents\usd_monitoring.py` (`track_summary` 삭제)
- Modify: `src\console\templates\loan.html:165-181` (감시 비교 블록 → 일지 카드), `:223-` (정산 표 아래 마켓 표), `:368-401` (경보 카드)
- Modify: `src\console\locales\en\monitoring.json` (`"감시 비교"` 삭제, 새 문구 추가)
- Test: `tests\console\test_usd_views.py`

**Interfaces:**
- Consumes: Task 8 `entry["watch"]`, 행의 `mkt_*` 필드, 경보 `why_now`·`outlook`·`adjust_reason`·`signals[].source`
- Produces:
  - `STAGE_LABELS = {"normal": "정상", "watch": "관찰", "medium": "중간", "high": "높음", "resolved": "해소"}`
  - `SOURCE_LABELS = {"bank": "은행", "settlement": "정산", "market": "마켓"}`
  - `SIGNAL_LABELS = TRIGGER_LABELS | {"orders_dip": "주문 일시 감소", "returns_rising": "반품률 상승", "deductions_rising": "공제율 상승", "cancel_rise": "취소율 상승", "cancel_spike": "취소율 급등", "rating_drop": "평점 하락", "grade_down": "등급 강등", "account_warning": "판매 경고·정지"}`
  - `JOURNAL_LABELS = {"explained": "정상 · 은행 입금 감소는 정산 예정표로 설명됨", "suppressed": "정상 · 해소된 신호가 이어짐", "hold": "변화 없음 · 단계 유지", "quiet": "신호 없음 %(n)s주째", "resolved_quiet": "해소 · 2주 연속 신호 없음", "resolved_agent": "해소 · 에이전트 판단", "fallback": "에이전트 미응답 · 규칙 단계", "updated": "원인 갱신"}`
  - 필터: `stage_label`, `source_label`, `signal_label`
  - `_journal_view(weeks, catalog) -> dict | None`: `watch` 줄이 하나도 없으면 None. `{"lines": 최신 주부터 [{week, stage, kind, quiet_weeks, agent, why_now, outlook, adjust_reason, alert_id, alert_action, sources: [{"source", "state": "signal"|"normal"|"missing", "signals": [{"code", "strength", "evidence": [(id, value)]}]}]}], "open": {"stage", "since"} | None}`. 데이터원 순서는 은행·정산·마켓, 마켓 자료가 없으면 `state="missing"`.
  - `loan_view` 반환: `"journal"`, `"market_rows"`(USD 행 중 `mkt_cancel_rate`가 있는 것), `"market_note"`(USD이고 주차가 있는데 `market_rows`가 없으면 `_("마켓 자료 없음")`). `"track"` 키 삭제. 경보 `signals[]`에 `"source"` 추가.

화면(스펙 §5.6 스케치): 일지 카드 제목 "에이전트 감시 일지", 오른쪽에 "열린 사건: %(stage)s · %(week)s주차부터". 한 줄 = 주차, 단계 배지(기존 칩·severity 스타일 재사용), 데이터원 칩(신호 없으면 "정상", 자료 없으면 "자료 없음"), 왜 지금·다음 예상, 조정 이유가 있으면 "조정 이유", `agent == "fallback"`이면 "에이전트 미응답 · 규칙 단계", 경보 링크(`#alert-<id>`)와 `alert_action` 라벨. 경보 카드에 "왜 지금", "다음 예상", "조정 이유" 줄과 근거 칩 앞 출처 칩. 마켓 표 열: 주차, 주문액, 취소율, 평점, 등급, 경고, 정산 대기.

- [ ] **Step 1: 실패하는 테스트를 쓴다** (`test_usd_views.py`의 기존 USD 대출 렌더링 도우미를 쓴다)

```python
def test_journal_newest_first_with_source_chips()
def test_journal_market_missing_shows_no_data_chip()
def test_old_replay_hides_journal_and_comparison():
    # weeks에 bank_only_triggers만 있고 watch 없음 → journal None,
    # 렌더링 HTML(ko·en)에 "감시 비교", "Monitoring comparison", "에이전트 감시 일지" 모두 없음, 예외 없음
def test_usd_loan_page_has_no_comparison_copy()   # 새 재생 문서 렌더링에 "은행만", "bank-only" 없음
def test_alert_card_shows_why_now_outlook_and_source()
def test_market_snapshot_table_rows()             # 13행, 취소율·평점·등급 값 표시; 마켓 없으면 "마켓 자료 없음"
def test_fallback_line_label()                    # agent "fallback" 줄에 "에이전트 미응답 · 규칙 단계"
```

`test_track_view_*` 두 테스트는 지운다.

- [ ] **Step 2: 실패를 확인한다**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\console\test_usd_views.py -q`
Expected: FAIL

- [ ] **Step 3: 구현한다** (위 Interfaces와 화면 설명대로. 새 한국어 문구는 모두 en 번역 추가)

- [ ] **Step 4: 통과를 확인한다**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\console -q`
Expected: PASS (`test_i18n`, KRW 화면 테스트 포함)

- [ ] **Step 5: 커밋**

```bash
git add src/console src/agents/usd_monitoring.py tests/console tests/mockapis/test_usd_scenes.py
git commit -m "feat(console): USD 에이전트 감시 일지와 주간 마켓 스냅샷, 감시 비교 삭제"
```

---

### Task 10: USD 단계 평가 케이스와 에이전트 문서

**Files:**
- Create: `src\evals\suites\early_warning_usd.py`
- Modify: `src\evals\__main__.py:23-70` (`SUITES`, 실행 람다), `src\evals\report.py` (요약 절)
- Modify: `docs\AGENTS.md:99-139` (조기경보 절)
- Test: `tests\evals\test_early_warning_usd_suite.py` (새)

**Interfaces:**
- Consumes: Task 7 `scene_context`, Task 6 `investigate_usd`; 기존 `Meter`, `EvalRunner`, `budget_left`, `cost_of`, `failure`
- Produces:
  - `SUITE = "early_warning_usd"`
  - `CASES: tuple[tuple[str, int, frozenset[str]], ...]` — (셀러, 주차, 허용 단계). `("seller-tint-17", 6, {"watch"})`, `("seller-snackbox-22", 3, {"medium"})`(높음 검증 규칙: high를 고르면 거절돼야 한다), `("seller-linen-10", 4, {"medium"})`, gearstore 첫 high 주 `{"high"}`, tangerine 첫 base-high 주 `{"medium", "high"}`. 주차는 Task 7에서 확정된 값을 쓴다.
  - `run(models, *, meter, runner, pseudonym_key) -> dict`: 모델별 `valid_rate`(`agent == "agent"` 비율), `stage_match`(허용 단계 안 비율), 케이스별 `{seller, week, base, stage, agent}`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_base_stage_runner_scores_full_marks()      # 가짜 러너가 base 단계를 그대로 → valid_rate == 1.0, stage_match == 1.0
def test_high_on_snackbox_is_rejected_and_falls_back()  # 가짜 러너가 늘 high → snackbox 케이스 agent == "fallback", stage == "medium"
def test_suite_registered()                          # "early_warning_usd" in evals.__main__.SUITES
```

- [ ] **Step 2: 실패를 확인한다**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\evals\test_early_warning_usd_suite.py -q`
Expected: FAIL

- [ ] **Step 3: 구현한다** — 기존 `early_warning` suite의 예산·비용 처리 방식을 따른다. `docs\AGENTS.md` 조기경보 절에 USD 단계형 알람(세 데이터원 신호, 기본 단계 표, ±1 조정과 검증, 폴백, 단계가 오를 때만 경보, 원인 갱신, 1·2단계 기준)을 짧게 더한다.

- [ ] **Step 4: 통과를 확인한다**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\evals -q`
Expected: PASS (기존 `test_early_warning_suite.py` 포함)

- [ ] **Step 5: 전체 테스트**

Run: `..\..\.venv\Scripts\python.exe -m pytest -q`
Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git add src/evals tests/evals docs/AGENTS.md
git commit -m "feat(evals): USD 단계 판단 평가 케이스와 조기경보 문서"
```

---

### Task 11: 배포와 운영 장면 기록

이 작업은 운영 환경을 바꾸므로 컨트롤러(메인 세션)가 직접 한다. 구현 서브에이전트에 맡기지 않는다.

**Files:**
- Modify: `docs\runbook\deploy-history.md`

- [ ] **Step 1: 브랜치 리뷰와 PR** — 전체 브랜치 코드 리뷰를 마친 뒤 `gh pr create`. 출력에 오류가 보여도 `gh pr list --head feat/usd-postloan-agent-watch`로 생성 여부를 확인한다.

- [ ] **Step 2: 배포** — `deploy-history.md`의 수동 ACR 창 절차로 오케스트레이터, Mock API, 콘솔 이미지를 빌드하고(ACR 열기 → `az acr build` → `finally`에서 ACR 닫기) `az containerapp update`로 다이제스트 고정 배포. `ops deploy`·`ops window` 금지. App Gateway가 00:50 KST 전후 자동 중지되면 먼저 켠다.
  Expected: 세 앱 새 리비전 Running, `/healthz` 200.

- [ ] **Step 3: 동의 화면 확인** — 고객 콘솔에서 USD 모니터링 동의에 마켓 항목이 보이는지 확인한다.

- [ ] **Step 4: 장면 재생** — 기존 대출 snackbox `app-20261125-5ea829`, dailylook `app-20261124-803ef5`, gearstore `app-20261124-d96249`, linen `app-20261125-baae81`, campgear `app-20261125-a64c65`를 다시 재생한다. tangerine-15와 tint-17은 새로 신청·승인한 뒤 재생한다.
  Expected: 스펙 §7 순서(주차 ±1). 에이전트 조정은 일지의 조정 이유로 확인한다.

- [ ] **Step 5: 기록과 커밋** — 대출별 일지 사다리(단계·주차), 경보 수, 1·2단계 발송 주, 조사 호출 수, 리비전·다이제스트를 `deploy-history.md` 표로 남긴다.

```bash
git add docs/runbook/deploy-history.md
git commit -m "docs(runbook): USD 감시 일지 배포와 장면 기록"
```
