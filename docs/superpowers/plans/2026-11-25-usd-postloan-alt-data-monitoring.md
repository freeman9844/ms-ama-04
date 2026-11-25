# USD 사후 모니터링 개편 — 승인 근거 감시, 조기 감지 비교, 오탐 제거 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** USD 사후 13주 재생이 매주 "은행만 트랙"과 "전체 트랙(정산·마켓·승인 근거)"을 함께 계산하게 하고, 승인 근거 훼손·조기 감지·오탐 제거 세 장면을 콘솔 대출 화면에서 보여 준다.

**Architecture:** `observe_usd`가 지금 계산식 그대로의 은행만 트리거를 먼저 만들고, 원장이 있으면 계절 보정 기대 주문, 주간 정산 대사율, 승인 근거 검사, 설명 처리를 더해 전체 트리거를 만든다. 실제 경보 흐름(새 트리거 → 조사 에이전트)은 전체 트랙만 쓴다. 승인 근거는 대출 등록 때 대출 문서 최상위에 저장하고, 두 트랙 요약은 순수 함수로 계산해 콘솔 카드가 그린다.

**Tech Stack:** Python 3.12, Pydantic v2, FastAPI, Jinja2, pytest. 배포는 Container Apps(오케스트레이터·모의 API·콘솔).

**Spec:** `docs\superpowers\specs\2026-11-25-usd-postloan-alt-data-monitoring-design.md`

**선행 계획:** `docs\superpowers\plans\2026-11-25-usd-policy-v2-alt-data-scenes.md`가 먼저 머지되어 있어야 한다. 이 계획은 v2의 장면 셀러(`seller-snackbox-22`, `seller-campgear-23`), `UsdSeller.season_percent`·`advance_label`, USD 특성 `pg_reconciliation_ratio`, `UsdPolicyDecision.applied_substitutions`, 테스트 도우미 `case_at`·`underwrite_at`(`tests\mockapis\test_usd_scenes.py`)을 쓴다.

## Global Constraints

- KRW 사후 모니터링(`agents\monitoring.py`의 `observe`, `ledger_monitor.observe`)과 KRW 화면은 바꾸지 않는다.
- 은행만 트랙은 결정적 계산만 한다. 조사 에이전트를 부르지 않는다(LLM 호출 수 불변).
- 은행만 트랙은 전체 트랙과 같은 주차, 같은 거래, 같은 임계값을 쓴다. 지금 `observe_usd`의 `inflow_drop`·`projected_shortfall`·`due_shortfall` 계산식 그대로다.
- `due_shortfall`은 설명 처리하지 않는다. 주문이 줄어든 주는 설명 처리하지 않는다.
- 기존 사후 원인 탐지 테스트 `tests\mockapis\test_usd_sellers.py::test_usd_postloan_cause_reaches_the_detector`는 고치지 않고 통과해야 한다.
- 새 필드가 없는 옛 재생 기록·옛 대출 문서·옛 `pg_flow.json`은 지금처럼 동작하고 보인다(새 카드는 숨김).
- 임계값: S003 대사율 < 0.95, S005 대사율 < 0.90, S004 작년 대비 주문 < 0.75면 훼손. 설명 처리는 정산 대비 입금 ≥ 0.95이고 영업일당 주문 ≥ 기대의 0.75. 장면이 안 나오면 조정할 수 있지만, 바꾼 값과 이유를 이 문서에 적는다.
- 화면 문구는 한국어 원문 + `src\console\locales\en\monitoring.json` 영문. 합성 데이터 문자열은 ASCII만.
- 테스트 실행은 `.venv\Scripts\python.exe -m pytest ... -q`.
- 배포는 좁은 창 방식(`ops deploy`·`ops window` 금지, 공개 창은 `finally`에서 닫음). 운영 재생은 한 번에 한 대출씩, 90초 이상 간격.

## 스펙과 다른 점

- **장면 ③ 셀러:** 스펙의 `seller-surfwear-23` 대신 v2 계획이 정한 가을 성수기 캠핑용품 `seller-campgear-23`(시드 123)을 쓴다. 대출 기간(2026-11-25~2027-02-24)의 계절 배수가 70·30·30%라 은행만 트랙에 입금 급감이 여러 주 뜬다. 겨울 성수기(skiwear)는 대출 기간이 성수기라 장면이 안 나와서 버렸다.
- **정산 예정 순액:** 새 `scheduled_net` 필드를 만들지 않고 기존 주간 `settle_net_cents`(그 주에 지급일이 든 정산 순액)를 3주 합산해 쓴다.

## Review Focus

1. **선정산·보류로 원장 지급 예정이 실제로 오지 않는 대출의 전체 트랙 잔고 전망.** 원장 지급 예정을 그대로 기대 입금에 넣으면 양도된 정산까지 들어올 돈으로 쳐서 잔고 부족을 놓친다. 지급 예정에 최근 대사율을 곱하고 보류 해제분(`released_cents > 0`)은 빼야 한다. Task 2의 `test_scheduled_payouts_are_discounted_by_reconciliation`이 확인한다.
2. **재생 직후 1~2주차처럼 3주 창이 덜 찬 주.** 대사율 창이 대출 전 날짜로 넘어가면 대출 전 은행 거래가 `visible`에 없어서 모든 정산이 미대사로 잡힌다. 창 시작을 `max(start, hi-20)`으로 묶는다. Task 4의 `test_stable_s003_loan_never_breaches`가 1주차부터 확인한다.
3. **작년 주문 기록이 없거나 비어 있는 흐름(옛 `pg_flow.json`, 이력 짧은 셀러).** 계절 보정 없이 지금 기대 주문(대출 전 8주 평균)을 쓰고, S004 지표는 `None`, 근거 상태는 `unknown`이며 트리거는 내지 않아야 한다. Task 4의 `test_missing_prior_year_keeps_flat_expectation`이 확인한다.
4. **옛 재생 기록을 새 콘솔에서 열 때.** 주차 항목에 `bank_only_triggers`가 없으면 `track_summary`가 `None`을 돌려주고 두 카드가 숨는다. 주간 표는 지금과 같다. Task 6의 `test_old_usd_replay_hides_track_cards`가 확인한다.
5. **조사 에이전트를 못 써서 폴백 경보로 `basis_breach`를 올릴 때.** 폴백 신호의 근거 ID가 실제 주차 행에 있는 지표여야 한다(S003·S005는 `recon_ratio`, S004는 `orders_vs_prior_year`). 없으면 grounding 검증에서 떨어진다. Task 3의 `test_fallback_basis_breach_cites_breached_metric`이 확인한다.

---

## 파일 구조

| 파일 | 책임 | Task |
|---|---|---|
| `src\mockapis\postloan.py`, `src\shared\usd_source.py`, `src\mockapis\app.py` | 작년 주문, 선정산 양도 시나리오 | 1 |
| `src\agents\pg_ledger.py`, `src\agents\forecast.py` | 주간 대사율, 지급 예정 반영 전망 | 2 |
| `src\shared\contracts.py`, `src\agents\ledger_monitor.py`, `src\agents\monitoring.py`, `src\agents\early_warning.py`, `src\evals\fake.py` | 새 원인·트리거·주차 지표 어휘 | 3 |
| `src\agents\usd_monitoring.py`, `docs\agents.md` | 두 트랙 관측, 승인 근거, 설명 처리, 요약 | 4 |
| `src\orchestrator\journey\activities.py`, `src\orchestrator\journey\monitoring.py` | 승인 근거 저장, 주차 기록 | 5 |
| `src\console\monitoring.py`, `src\console\templates\loan.html`, `src\console\locales\en\monitoring.json` | 비교·승인 근거 카드 | 6 |
| `docs\runbook\deploy-history.md` | 배포 기록 | 7 |

---

### Task 1: 사후 흐름에 작년 주문과 선정산 양도 시나리오 추가

**Files:**
- Modify: `src\mockapis\postloan.py:72-184` (`FlowScenario`, `USD_POSTLOAN_SCENARIOS`, `UsdPostLoanFlow`, `generate_usd_postloan`, `_usd_scenario`)
- Modify: `src\shared\usd_source.py:56-58` (`UsdPgFlow`)
- Modify: `src\mockapis\app.py:191-196` (`/pg/flow` USD 응답)
- Test: `tests\mockapis\test_usd_sellers.py`

**Interfaces:**
- Consumes: v2의 `UsdSeller.advance_label`, `season_percent`, 셀러 `seller-snackbox-22`·`seller-campgear-23`
- Produces:
  - `FlowScenario.ADVANCE_AFTER_LOAN = "advance_after_loan"`
  - `USD_POSTLOAN_ADVANCE_DAY = 14`, `USD_POSTLOAN_ADVANCE_LABEL = "Marketplace payout"`
  - `USD_POSTLOAN_SCENARIOS["seller-snackbox-22"] = FlowScenario.ADVANCE_AFTER_LOAN`
  - `UsdPostLoanFlow.prior_year_orders: tuple[UsdOrderDay, ...] = ()`: 케이스 주문 중 `order_on`이 `[start - 21일 - 364일, end - 364일]`인 것. **날짜는 원래 날짜 그대로**다. 쓰는 쪽이 364일(52주, 요일 일치)을 더해 맞춘다.
  - `UsdPgFlow.prior_year_orders: tuple[UsdOrderDay, ...] = ()`. 기본값이 있어 옛 `pg_flow.json`도 검증된다. `/pg/flow`는 `start`·`end`로 거르지 않고 그대로 싣는다.
  - `_usd_scenario(ADVANCE_AFTER_LOAN, ...)`는 `{"advance_from": start + timedelta(days=USD_POSTLOAN_ADVANCE_DAY)}`를 돌려준다. `generate_usd_postloan`은 이 시나리오일 때만 `seller = replace(seller, advance_label=USD_POSTLOAN_ADVANCE_LABEL)`로 `simulate_usd`를 부른다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_advance_after_loan_diverts_top_platform_payouts():
    case = case_for("seller-snackbox-22")
    flow = postloan.generate_usd_postloan(case, AS_OF, 1001, loan_id="loan-usd", amount_cents=500_000,
                                          installment_cents=84_000)
    onset = flow.start + timedelta(days=14)
    assert flow.scenario is postloan.FlowScenario.ADVANCE_AFTER_LOAN
    naver_paid = [s for s in flow.pg_settlements if s.platform_code == "naver_smart_store" and s.settled_on >= onset]
    assert naver_paid  # 원장에는 그대로 남는다
    assert not any(t.platform_code == "naver_smart_store" and t.booked_on >= onset for t in flow.transactions)
    adv = [t for t in flow.transactions if t.description == "Marketplace payout"]
    assert adv and all(t.platform_code is None and t.booked_on >= onset - timedelta(days=5) for t in adv)

def test_prior_year_orders_cover_loan_period_shifted_one_year():
    case = case_for("seller-campgear-23")
    flow = postloan.generate_usd_postloan(case, AS_OF, 1001, loan_id="loan-usd", amount_cents=500_000,
                                          installment_cents=84_000)
    days = {o.order_on for o in flow.prior_year_orders}
    assert min(days) >= flow.start - timedelta(days=21 + 364) and max(days) <= flow.end - timedelta(days=364)
    dec = sum(o.gross_cents for o in flow.prior_year_orders if o.order_on.month == 12)
    nov = sum(o.gross_cents for o in flow.prior_year_orders if o.order_on.month == 11)
    assert dec < nov  # 가을 성수기 → 12월 비수기가 작년 기록에 보인다

def test_old_pg_flow_payload_without_prior_year_still_validates():
    flow = UsdPgFlow(**USD_V1.model_dump(), settlements=(), orders=())
    assert flow.prior_year_orders == ()
```

`/pg/flow` 응답에 `prior_year_orders`가 실리는지는 이 파일의 기존 `/pg/flow` USD 테스트에 단언 한 줄을 더해 확인한다. v2 Task 8의 `test_approved_scene_sellers_generate_stable_postloan`은 snackbox도 상환 거래와 13주 길이를 그대로 만족해야 한다(이름의 "stable"은 snackbox에 한해 시나리오가 다르다는 주석을 단다).

- [ ] **Step 2: 실행해 실패 확인**

Run: `.venv\Scripts\python.exe -m pytest tests\mockapis\test_usd_sellers.py -q`
Expected: 새 테스트 FAIL(`ADVANCE_AFTER_LOAN`·`prior_year_orders` 없음)

- [ ] **Step 3: 구현.** 위 Interfaces대로 더한다. `FlowScenario`는 KRW도 쓰므로 KRW 쪽이 값을 전수 매핑하는 곳이 있는지 `grep FlowScenario`로 보고, 있으면 USD 전용 값으로 건너뛰게 한다.

- [ ] **Step 4: 실행해 통과 확인**

Run: `.venv\Scripts\python.exe -m pytest tests\mockapis -q`
Expected: PASS. `test_usd_postloan_cause_reaches_the_detector` 그대로 통과.

- [ ] **Step 5: 커밋**

```powershell
git add src\mockapis src\shared\usd_source.py tests\mockapis\test_usd_sellers.py
git commit -m "feat(mockapis): prior-year orders and post-loan advance assignment scenario"
```

---

### Task 2: 주간 정산 대사율과 지급 예정 반영 전망

**Files:**
- Modify: `src\agents\pg_ledger.py:117-145` (대사 매칭 추출, USD 입금 변환 추출)
- Modify: `src\agents\forecast.py:191-214` (`project_usd`)
- Test: `tests\agents\test_pg_ledger.py`, `tests\agents\test_forecast.py`

**Interfaces:**
- Produces:
  - `pg_ledger.weekly_reconciliation(settlements: Sequence[UsdSettlement], transactions: Sequence[UsdTransaction], lo: date, hi: date) -> tuple[float | None, int]`: (대사율, 미대사 지급 정산 순액 센트).
    - 대상 정산: `net_cents > 0`, `lo <= settled_on`, `add_business_days(settled_on, MATCH_BUSINESS_DAYS) <= hi`.
    - 매칭: `_summary`와 같은 규칙(같은 플랫폼, 같은 금액, `settled_on <= booked_on <= add_business_days(settled_on, 3)`, 입금 하나는 한 번만). 이 매칭 루프를 `_match(rows, deposits) -> list[_Payout]`(미대사 목록)로 뽑아 `_summary`와 같이 쓴다. USD 입금 변환(`usd_ledger_summary` 안의 `platform`)도 `_usd_deposits(transactions)`로 뽑는다.
    - 대사율 = `round(1 - 미대사 합 / 대상 합, 3)`, 대상 합이 0이면 `None`.
  - `forecast.project_usd(visible, start, hi, dues, scheduled: Mapping[str, tuple[date, Mapping[date, int]]] | None = None)`:
    - `scheduled`는 플랫폼 → (알려진 마지막 날, 날짜별 지급 예정 순액).
    - `None`이면 지금과 한 글자도 다르지 않게 동작한다.
    - 있으면 그 플랫폼의 `PG_SETTLE` 입금(`platform_code`가 키에 있는 거래)을 최근 28일 평균(`ordinary`)에서 빼고, 플랫폼별 최근 28일 영업일 평균을 따로 구한다. 전망 영업일 `d`마다 그 플랫폼은 `d <= 알려진 마지막 날`이면 `scheduled`의 그날 금액, 아니면 플랫폼별 평균을 더한다.
  - `usd_monitoring`이 `scheduled`를 만드는 규칙(Task 4에서 씀): 플랫폼 `p`의 알려진 마지막 날 = `add_business_days(hi, USD_PAYOUT_BUSINESS_DAYS[p])`(`shared.sellers`), 금액 = `hi < settled_on <= 그날`이고 `net_cents > 0`이며 `released_cents == 0`인 정산 순액 × (최근 대사율, `None`이면 1.0)을 내림한 값.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests\agents\test_pg_ledger.py
def test_weekly_reconciliation_counts_only_judgeable_payouts():
    # 정산 3건: (월, naver, 1000) 입금 일치 / (화, naver, 2000) 입금 없음 / (hi 하루 전, coupang, 500) 판단 보류
    ratio, unpaid = weekly_reconciliation(settlements, deposits, lo, hi)
    assert (ratio, unpaid) == (round(1 - 2000 / 3000, 3), 2000)
    assert weekly_reconciliation((), deposits, lo, hi) == (None, 0)

def test_usd_ledger_summary_unchanged_after_extraction():
    # 이 파일의 기존 USD 요약 테스트가 그대로 통과하는 것으로 확인한다(새 단언 없음)

# tests\agents\test_forecast.py
def test_project_usd_without_schedule_is_unchanged():
    assert project_usd(visible, start, hi, dues) == project_usd(visible, start, hi, dues, None)

def test_scheduled_payouts_replace_trailing_average_until_known_day():
    # 최근 4주 naver 입금이 0(보류)인데 원장에 다음 3영업일 지급 예정 3000이 있으면
    # 그 날짜의 before_payment_cents가 3000만큼 높고, 알려진 날 이후는 플랫폼 평균(0)을 쓴다
    ...

def test_scheduled_payouts_are_discounted_by_reconciliation():
    # Task 4의 _scheduled(pg, hi, recon=0.4)가 지급 예정 1000을 400으로 넣고, released_cents>0 정산은 넣지 않는다
```

마지막 테스트는 Task 4의 `_scheduled`를 쓰므로 Task 4 Step 1에서 `tests\agents\test_forecast.py`에 둔다. 여기서는 앞의 넷만 쓴다.

- [ ] **Step 2: 실행해 실패 확인**

Run: `.venv\Scripts\python.exe -m pytest tests\agents\test_pg_ledger.py tests\agents\test_forecast.py -q`
Expected: 새 테스트 FAIL

- [ ] **Step 3: 구현.** 위 Interfaces대로.

- [ ] **Step 4: 실행해 통과 확인**

Run: `.venv\Scripts\python.exe -m pytest tests\agents -q`
Expected: PASS(기존 원장 요약 테스트 포함)

- [ ] **Step 5: 커밋**

```powershell
git add src\agents\pg_ledger.py src\agents\forecast.py tests\agents
git commit -m "feat(agents): weekly settlement reconciliation and scheduled-payout projection"
```

---

### Task 3: 새 원인·트리거·주차 지표 어휘

**Files:**
- Modify: `src\shared\contracts.py:463-493` (`SignalKind`, `AlertCause`)
- Modify: `src\agents\ledger_monitor.py:50-56` (`CAUSE_FIELDS`)
- Modify: `src\agents\monitoring.py:53-76` (`WEEK_FIELDS`)
- Modify: `src\agents\early_warning.py:49-125, 500-525` (트리거 표, `_CENT_FIELDS`, `FALLBACK_TEXT`, 지시문, `fallback_alert`)
- Modify: `src\evals\fake.py:25-31` (`_EW_SIGNAL_KIND`)
- Test: `tests\agents\test_early_warning.py`

**Interfaces:**
- Produces:
  - `SignalKind.SETTLEMENT_ASSIGNED = AlertCause.SETTLEMENT_ASSIGNED = "settlement_assigned"`
  - `CAUSE_FIELDS[AlertCause.SETTLEMENT_ASSIGNED] = ("recon_ratio", "unreconciled_payout")`
  - `WEEK_FIELDS`에 추가: `"settle_coverage", "expected_orders", "orders_per_day_vs_expected", "orders_vs_prior_year", "recon_ratio", "unreconciled_payout"`. KRW 행에는 이 키가 없어 카탈로그에 안 생긴다.
  - `_CENT_FIELDS`에 `expected_orders`, `unreconciled_payout` 추가(USD 행 키는 `expected_orders_cents`, `unreconciled_payout_cents`).
  - 트리거 `basis_breach`:
    - `_TRIGGER_TEXT["basis_breach"] = ("settlement_assigned", "W:{week}:recon_ratio", "대체 규칙으로 승인한 근거가 깨졌다")`
    - `_USD_TRIGGERS["basis_breach"] = ("An approval basis from alternative data no longer holds.", "Review the approval basis against recent settlements and orders.")`
    - `_USD_TRIGGERS["settlement_assigned"] = ("Platform payouts are no longer reaching this account.", "Ask whether payouts were sold to an early-payout lender.")`
    - `FALLBACK_TEXT["settlement_assigned"] = ("플랫폼 정산금이 이 계좌로 들어오지 않아 승인 근거(빠른 정산)가 깨졌습니다.", "선정산 업체 양도 여부와 정산 수령 계좌 확인이 필요합니다.")`, `FALLBACK_TEXT["basis_breach"]`도 같은 문구.
  - `fallback_alert`의 `basis_breach` 신호는 `ctx.rows[-1].get("basis_breached")`의 첫 코드로 고른다: S003·S005 → (`settlement_assigned`, `recon_ratio`), S004 → (`inflow_drop`, `orders_vs_prior_year`).
  - 지시문(`INSTRUCTIONS`): 4번 원인별 지표 목록에 "정산 양도: W:n:recon_ratio·unreconciled_payout"을, 3번 뒤에 "basis_breach는 심사 때 대체 규칙(S003 빠른 정산, S004 계절 매출 등)으로 승인한 근거가 깨졌다는 뜻이다. 근거 코드는 W:n:basis_breached에 있다"를 더한다. USD 지시문이 따로 있으면 같은 내용을 영문으로 넣는다.
  - `_EW_SIGNAL_KIND["settlement_assigned"] = "settlement_assigned"`
  - `collections.HARDSHIP_CAUSES`는 바꾸지 않는다(양도는 재무 곤란 사유가 아니다).

- [ ] **Step 1: 실패하는 테스트 작성**

```python
@pytest.mark.parametrize("codes,metric,kind", [
    (["S003"], "recon_ratio", "settlement_assigned"), (["S004"], "orders_vs_prior_year", "inflow_drop")])
def test_fallback_basis_breach_cites_breached_metric(codes, metric, kind):
    row = {**usd_week_row(week=3), "recon_ratio": 0.81, "orders_vs_prior_year": 0.6, "basis_breached": codes}
    ctx = usd_ctx(rows=[row], triggers=["basis_breach"])  # 이 파일의 USD EwContext 도우미 구성 그대로
    alert = fallback_alert(ctx, "agent_unavailable")
    sig = next(s for s in alert.signals if s.kind.value == kind)
    assert sig.metric_ids == (f"W:3:{metric}",) and f"W:3:{metric}" in ctx.catalog

def test_usd_catalog_includes_new_week_fields():
    row = {**usd_week_row(week=2), "expected_orders_cents": 1000, "unreconciled_payout_cents": 0, "recon_ratio": 1.0}
    ctx = usd_ctx(rows=[row])
    assert {"W:2:expected_orders_cents", "W:2:unreconciled_payout_cents", "W:2:recon_ratio"} <= ctx.catalog.keys()
```

`usd_week_row`·`usd_ctx`는 이 파일에 이미 있는 USD 컨텍스트 구성을 도우미로 뽑은 것이다. 없으면 기존 USD 테스트 한 개의 구성을 그대로 함수로 만든다.

- [ ] **Step 2: 실행해 실패 확인**

Run: `.venv\Scripts\python.exe -m pytest tests\agents\test_early_warning.py -q`
Expected: 새 테스트 FAIL(`KeyError: 'basis_breach'` 등)

- [ ] **Step 3: 구현.** 위 Interfaces대로.

- [ ] **Step 4: 실행해 통과 확인**

Run: `.venv\Scripts\python.exe -m pytest tests\agents tests\evals -q`
Expected: PASS. KRW 조기경보 테스트도 그대로다.

- [ ] **Step 5: 커밋**

```powershell
git add src\shared\contracts.py src\agents src\evals\fake.py tests\agents
git commit -m "feat(agents): settlement_assigned cause and basis_breach trigger vocabulary"
```

---

### Task 4: `observe_usd` 두 트랙, 승인 근거, 설명 처리, 장면 고정

**Files:**
- Modify: `src\agents\usd_monitoring.py` (전체)
- Modify: `docs\agents.md` (사후 모니터링 절)
- Test: `tests\mockapis\test_usd_scenes.py`(장면), `tests\agents\test_forecast.py`(Task 2에서 미룬 테스트)

**Interfaces:**
- Consumes: Task 1의 `UsdPgFlow.prior_year_orders`, Task 2의 `weekly_reconciliation`·`project_usd(scheduled=)`, Task 3의 어휘
- Produces:
  - `class UsdObservation(NamedTuple)`: `rows, triggers, candidates, cats, proj, bank_only: list[str], explained: list[dict], basis: list[dict]`. 앞 다섯 필드 순서는 지금 반환 튜플과 같다(기존 테스트의 `rows, triggers, candidates, *_` 풀기가 그대로 된다).
  - `observe_usd(loan, visible, pg, week, dues) -> UsdObservation`. 승인 근거는 `loan.get("approval_basis") or []`.
  - `approval_basis(substitutions: Sequence[str], features: Mapping[str, Any]) -> list[dict]`: 항목 `{"code", "metric", "approved", "floor"}`.

    | code | metric | approved | floor |
    |---|---|---|---|
    | S003 | `recon_ratio` | `features["pg_reconciliation_ratio"]` | 0.95 |
    | S005 | `recon_ratio` | `features["pg_reconciliation_ratio"]` | 0.90 |
    | S004 | `orders_vs_prior_year` | `None` | 0.75 |

    다른 코드(S001, S002)는 건너뛴다.
  - `track_summary(weeks: Sequence[Mapping]) -> dict | None`: 어느 주에도 `bank_only_triggers` 키가 없으면 `None`. 아니면 `{"bank_first_week", "full_first_week", "lead_weeks", "explained_weeks", "bank_only_weeks"}`.
    - `lead_weeks` = 두 첫 주차가 모두 있으면 `bank - full`, 아니면 `None`.
    - `bank_only_weeks` = 은행만 트리거는 있고 전체 트리거는 없는 주 수.
    - `explained_weeks` = `explained`가 비지 않은 주 수.
  - `_scheduled(pg, hi, recon) -> dict[str, tuple[date, dict[date, int]]]` (Task 2 Interfaces의 규칙)
  - 상수: `EXPLAIN_COVERAGE = 0.95`, `EXPLAIN_ORDERS = 0.75`, `SEASON_BAND = (0.8, 1.25)`, `PRIOR_YEAR_DAYS = 364`

**주차 계산(원장이 있을 때, 제 판단).** `recent`는 지금처럼 최근 3주 행, `span` = `[recent 첫 주 시작, hi]`.

1. **은행만 트랙:** 지금 계산식 그대로. 전망은 `project_usd(visible, start, hi, dues)`.
2. **계절 보정 기대 주문:** `py(d)` = `prior_year_orders` 중 `order_on + 364일 == d`인 주문 합.
   - `pre3` = `[start-21, start-1]` 실제 주문 합, `py_pre3` = 같은 구간 `py` 합, `py_loan` = `[start, week_window(start, 13)[1]]`의 `py` 합 ÷ 13.
   - `seasonal` = `pre3 > 0 and py_pre3 > 0 and not SEASON_BAND[0] <= py_loan / (py_pre3 / 3) <= SEASON_BAND[1]`.
   - 주 기대 `expected_orders_cents` = `seasonal`이면 `([lo, hi]의 py 합) * pre3 // py_pre3`, 아니면 지금 `weekly`.
   - `orders_vs_expected` = `round(Σ order_gross / Σ expected_orders, 3)`(recent 합). 계절 보정이 없으면 지금 값과 같다. `sales_slump`는 이 값으로 지금 조건 그대로 판단한다.
   - `orders_per_day_vs_expected` = (Σ 실제 주문 ÷ `span` 영업일 수) ÷ (Σ 기대 ÷ 기준 영업일 수). 기준 영업일 수는 `seasonal`이면 `span`을 364일 당긴 구간의 영업일 수, 아니면 `[start-56, start-1]` 영업일 수 × (span 일수 ÷ 56)이다. 영업일은 `shared.krcalendar.is_business_day`.
   - `orders_vs_prior_year` = `span`의 실제 주문 합 ÷ `span`의 `py` 합. `py` 합이 0이거나 작년 주문이 없으면 `None`.
3. **대사율:** `recon_ratio, unreconciled_payout_cents = weekly_reconciliation(pg.settlements, visible, max(start, hi - 20일), hi)`.
4. **정산 대비 입금:** `settle_coverage` = recent의 `revenue_cents` 합 ÷ recent의 `settle_net_cents` 합(합이 0이면 `None`).
5. **승인 근거:** 항목마다 `value = row[metric]`, `status` = `None`이면 `"unknown"`, `value < floor`면 `"breach"`, 아니면 `"ok"`. `basis`는 `{**항목, "value", "status"}` 목록. `row["basis_breached"]` = 훼손 코드 목록(비면 키를 넣지 않는다). 훼손이 하나라도 있으면 `basis_breach`.
6. **전체 트랙 전망:** `project_usd(visible, start, hi, dues, scheduled=_scheduled(pg, hi, recon_ratio))`. 행의 `projected_*`·`shortfall_date`·`next_due_on`·`due_balance_cents`와 반환 `proj`는 전체 전망 값이다. `due_shortfall`은 **은행만 전망**으로 판단해 두 트랙에 같게 넣는다.
7. **설명 처리:**
   - 은행만에 `inflow_drop`이 있고 `settle_coverage >= EXPLAIN_COVERAGE`이고 `orders_per_day_vs_expected >= EXPLAIN_ORDERS`면 전체 트랙에서 빼고 `{"trigger": "inflow_drop", "reason": "scheduled_settlements", "metric_ids": ["W:{w}:revenue_vs_expected", "W:{w}:settle_coverage", "W:{w}:orders_per_day_vs_expected"]}`를 기록한다.
   - 은행만에 `projected_shortfall`이 있고 전체 전망 최저 잔고가 0 이상이면 `{"trigger": "projected_shortfall", "reason": "scheduled_payouts", "metric_ids": ["F:{w}:projected_min_balance_cents"]}`.
8. **전체 트리거 순서:** `inflow_drop`(설명 안 됐을 때), `sales_slump`, `settlement_hold`, `return_spike`, `cost_surge`, `basis_breach`, `projected_shortfall`, `due_shortfall`.
9. **원인 후보:** `_candidates`에서 `basis_breach`는 그 주 행의 `basis_breached`로 고른다. S003·S005가 있으면 같은 주 트리거에 `settlement_hold`가 있을 때 `SETTLEMENT_HOLD`, 아니면 `SETTLEMENT_ASSIGNED`. S004면 `SALES_SLUMP`. `_CENT_EVIDENCE`에 `unreconciled_payout`을 더한다.

원장이 없으면(`pg is None`) 전체 트랙 = 은행만, `explained`·`basis`는 빈 목록이다.

- [ ] **Step 1: 실패하는 테스트 작성** (`tests\mockapis\test_usd_scenes.py`에 추가)

```python
def replay(seller, *, basis=None, ledger=True, prior_year=True):
    """v2 판정으로 등록한 것처럼 대출을 만들고 13주를 관측한다. 반환: (주차별 UsdObservation, 주차 항목)."""
    uw = underwrite_at(seller, AS_OF)
    stored = {"features": uw.features.model_dump(mode="json"), "monthly": uw.monthly}
    decision = explain(uw.features, rules_for_version("usd-v2")).decision
    flow = postloan.generate_usd_postloan(case_at(seller, AS_OF), AS_OF, 1001,
                                          loan_id="loan-usd", amount_cents=500_000, installment_cents=84_000)
    pg = UsdPgFlow(**USD_V1.model_dump(), settlements=flow.pg_settlements, orders=flow.orders,
                   prior_year_orders=flow.prior_year_orders if prior_year else ())
    mon = {"flow_start": flow.start.isoformat(), "weeks": []}
    loan = {"monitoring": mon, "baseline": usd_baseline(stored),
            "approval_basis": approval_basis(decision.applied_substitutions, stored["features"])
            if basis is None else basis}
    out = []
    for week in range(1, 14):
        hi = flow.start + timedelta(days=7 * week - 1)
        obs = observe_usd(loan, [t for t in flow.transactions if t.booked_on <= hi], pg if ledger else None, week, {})
        mon["weeks"].append({"week": week, "row": obs.rows[-1], "triggers": obs.triggers,
                             "bank_only_triggers": obs.bank_only, "explained": obs.explained})
        out.append(obs)
    return out, mon["weeks"]

def test_scene1_assignment_breaks_s003_basis():
    obs, weeks = replay("seller-snackbox-22")
    s = track_summary(weeks)
    assert s["bank_first_week"] is None  # 은행만: 미감지
    assert s["full_first_week"] in (3, 4) and "basis_breach" in obs[s["full_first_week"] - 1].triggers
    assert obs[s["full_first_week"] - 1].candidates[0].cause is AlertCause.SETTLEMENT_ASSIGNED

def test_stable_s003_loan_never_breaches(monkeypatch):
    monkeypatch.setitem(postloan.USD_POSTLOAN_SCENARIOS, "seller-snackbox-22", postloan.FlowScenario.STABLE)
    obs, _ = replay("seller-snackbox-22")
    assert all(b["status"] != "breach" for o in obs for b in o.basis)
    assert all(o.basis for o in obs)  # S003 근거가 1주차부터 계산된다(Review Focus 2)

SCENE2 = {"seller-dailylook-01": (None, None), "seller-gearstore-06": (None, None), "seller-linen-10": (None, None)}
# (은행만 첫 주차, 전체 첫 주차). Step 4에서 측정값으로 채운다.

@pytest.mark.parametrize("seller", sorted(SCENE2))
def test_scene2_full_track_detects_first(seller):
    s = track_summary(replay(seller)[1])
    assert s["full_first_week"] is not None
    assert s["bank_first_week"] is None or s["full_first_week"] < s["bank_first_week"]
    assert (s["bank_first_week"], s["full_first_week"]) == SCENE2[seller]

def test_scene3_seasonal_drop_is_explained():
    obs, weeks = replay("seller-campgear-23")
    s = track_summary(weeks)
    assert s["full_first_week"] is None and s["bank_only_weeks"] >= 3 and s["explained_weeks"] >= 3
    assert all(b["status"] != "breach" for o in obs for b in o.basis)

@pytest.mark.parametrize("seller", ["seller-campgear-23", "seller-dailylook-01"])
def test_bank_only_track_equals_detector_without_ledger(seller):
    _, weeks = replay(seller)
    no_ledger, _ = replay(seller, ledger=False)
    assert [w["bank_only_triggers"] for w in weeks] == [o.triggers for o in no_ledger]
    assert all(o.explained == [] and o.basis == [] for o in no_ledger)

def test_order_slump_is_never_explained():
    obs, _ = replay("seller-dailylook-01")
    assert not any(e["trigger"] == "inflow_drop" for o in obs for e in o.explained
                   if o.rows[-1]["orders_per_day_vs_expected"] < .75)

def test_missing_prior_year_keeps_flat_expectation():
    obs, _ = replay("seller-campgear-23", prior_year=False)
    assert len({o.rows[-1]["expected_orders_cents"] for o in obs}) == 1
    assert all(o.rows[-1]["orders_vs_prior_year"] is None for o in obs)
    assert all(b["status"] == "unknown" for o in obs for b in o.basis if b["code"] == "S004")
    assert not any("basis_breach" in o.triggers for o in obs)

def test_no_substitution_loan_has_empty_basis():
    assert approval_basis((), {"pg_reconciliation_ratio": .99}) == []
    assert all(o.basis == [] for o in replay("seller-bakery-20")[0])
```

`tests\agents\test_forecast.py`에 Task 2에서 미룬 `test_scheduled_payouts_are_discounted_by_reconciliation`을 더한다.

- [ ] **Step 2: 실행해 실패 확인**

Run: `.venv\Scripts\python.exe -m pytest tests\mockapis\test_usd_scenes.py tests\agents\test_forecast.py -q`
Expected: 새 테스트 FAIL(`UsdObservation`·`approval_basis`·`track_summary` 없음)

- [ ] **Step 3: 구현.** 위 "주차 계산" 순서대로 `usd_monitoring.py`를 고친다.

- [ ] **Step 4: 장면 측정과 고정.**
  - `SCENE2`를 비워 둔 채 실행해 세 셀러의 (은행만, 전체) 첫 주차를 출력하고, 그 값으로 `SCENE2`를 채운다. 전체가 먼저가 아닌 셀러가 있으면 임계값을 바꾸지 말고 멈춰서 보고한다(장면 ②는 새 데이터를 만들지 않는다).
  - 장면 ①③이 안 나오면 조정 순서: ③은 campgear의 12~2월 `season_percent`나 `owner_floor_percent`(v2 장면 판정이 그대로인 범위), ①은 `USD_POSTLOAN_ADVANCE_DAY`. 그래도 안 되면 Global Constraints의 임계값. 바꾼 값과 이유를 이 문서 "스펙과 다른 점"에 적는다.

- [ ] **Step 5: 통과 확인**

Run: `.venv\Scripts\python.exe -m pytest tests\mockapis tests\agents -q`
Expected: PASS. `test_usd_postloan_cause_reaches_the_detector` 수정 없이 통과.

- [ ] **Step 6: 문서.** `docs\agents.md` 사후 모니터링 절에 두 트랙, 승인 근거 표(S003·S005·S004와 하한), 설명 처리 두 조건, `due_shortfall` 예외를 짧게 더한다.

- [ ] **Step 7: 커밋**

```powershell
git add src\agents\usd_monitoring.py tests docs\agents.md
git commit -m "feat(agents): dual-track USD monitoring with approval basis and explained drops"
```

---

### Task 5: 오케스트레이터 — 승인 근거 저장과 주차 기록

**Files:**
- Modify: `src\orchestrator\journey\activities.py:571-588` (`register_monitoring` USD 경로)
- Modify: `src\orchestrator\journey\monitoring.py:550-571` (`process_week` USD 경로와 `entry`)
- Test: `tests\orchestrator\test_monitoring_handlers.py`

**Interfaces:**
- Consumes: Task 4의 `UsdObservation`, `approval_basis`
- Produces:
  - 대출 문서 최상위 `approval_basis: list[dict]` = `approval_basis((application.get("policy") or {}).get("applied_substitutions") or (), uw["features"])`. `monitoring`이나 `baseline` 안에 두지 않는다(`start_replay`가 `monitoring`을 덮어쓰고, 콘솔이 `baseline`을 `B:` 근거로 올린다). KRW 대출에는 키를 넣지 않는다.
  - USD 주차 항목에 `bank_only_triggers: list[str]`, `explained: list[dict]`, `basis: list[dict]` 추가. KRW 항목은 그대로다.
  - 조사 에이전트에는 지금처럼 전체 트랙 `triggers`·`new_triggers`만 간다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_register_usd_loan_stores_approval_basis(...):
    # 이 파일 265·717행 USD 등록 테스트의 구성을 그대로 쓰고, 신청 문서 policy에
    # applied_substitutions ["S003"]과 underwriting features pg_reconciliation_ratio .99를 넣는다
    loan = deps.store.read("loans", loan_id, loan_id)
    assert loan["approval_basis"] == [{"code": "S003", "metric": "recon_ratio", "approved": .99, "floor": .95}]
    assert "approval_basis" not in loan["baseline"]

def test_register_v1_usd_loan_has_empty_basis(...):
    # policy에 applied_substitutions가 없는 저장 판정(usd-v1) → []

def test_usd_week_entry_records_both_tracks(...):
    # 이 파일 850행 USD process_week 테스트 구성 그대로 1주차를 처리한 뒤
    entry = loan["monitoring"]["weeks"][0]
    assert {"bank_only_triggers", "explained", "basis"} <= entry.keys()

def test_replay_restart_keeps_approval_basis(...):
    # start_replay를 두 번 불러도 loan["approval_basis"]가 남는다
```

- [ ] **Step 2: 실행해 실패 확인**

Run: `.venv\Scripts\python.exe -m pytest tests\orchestrator\test_monitoring_handlers.py -q`
Expected: 새 테스트 FAIL

- [ ] **Step 3: 구현.** `process_week`는 `obs = observe_usd(...)`로 받고 `rows, triggers, candidates, cats, proj = obs[:5]`로 지금 흐름을 유지한 뒤 `entry`에 세 필드를 더한다.

- [ ] **Step 4: 실행해 통과 확인**

Run: `.venv\Scripts\python.exe -m pytest tests\orchestrator -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```powershell
git add src\orchestrator tests\orchestrator
git commit -m "feat(orchestrator): store USD approval basis and record both monitoring tracks"
```

---

### Task 6: 콘솔 — 감시 비교 카드와 승인 근거 카드

**Files:**
- Modify: `src\console\monitoring.py:37-48, 110-116, 143-237` (`TRIGGER_LABELS`, `CAUSE_LABELS`, `loan_view`)
- Modify: `src\console\templates\loan.html:160-185` (카드 두 개, 주간 표 설명 표시)
- Modify: `src\console\locales\en\monitoring.json`
- Test: `tests\console\test_usd_views.py`

**Interfaces:**
- Consumes: Task 4의 `track_summary`, Task 5의 주차 필드와 `loan["approval_basis"]`
- Produces (`loan_view` 반환에 추가):
  - `track`: `track_summary(weeks)`가 `None`이면 `None`. 아니면 요약 + `timeline`: 주차별 `{"week", "bank": "trigger"|"bank_only"|"", "full": "trigger"|"explained"|""}`.
    - 은행만 줄: 은행만 트리거가 있고 전체 트리거가 없으면 `bank_only`(○), 둘 다 있으면 `trigger`(●).
    - 전체 줄: 전체 트리거가 있으면 `trigger`(●), 없고 `explained`가 있으면 `explained`(✓).
  - `basis_cards`: `loan.get("approval_basis")`가 비면 `[]`. 아니면 항목마다 `{"code", "metric", "approved", "floor", "latest", "breach_week"}`. `latest`는 마지막 주의 값, `breach_week`는 처음 `breach`인 주차(없으면 `None`).
- 라벨:
  - `TRIGGER_LABELS["basis_breach"] = "승인 근거 훼손"`
  - `CAUSE_LABELS["settlement_assigned"] = "정산 양도"`
  - 카드 제목 "감시 비교", "승인 근거"
  - 안내 "은행만 트랙은 탐지기 기준, 실제 경보는 정산·마켓 트랙과 조사 에이전트 판단"
  - 요약 "정산·마켓 데이터가 %(n)s주 먼저 감지", "은행만: 미감지", "설명된 입금 감소 %(n)s주"
  - 범례 "● 트리거 ○ 은행만 트리거 ✓ 정산 예정표로 설명됨"
  - 근거 이름 S003 "빠르고 정확한 정산", S005 "정산 원장으로 매출 실재 확인", S004 "계절 매출 패턴"
  - 상태 "유지", "훼손(%(n)s주차)", "판단 불가"
- 영문(`monitoring.json`): 트리거 "Approval basis broken", 원인 "Settlement assignment", 카드 "Monitoring comparison", "Approval basis", "Bank-only track uses detector rules; actual alerts use the settlement and marketplace track and the investigation agent", "Alt data detected %(n)s week(s) earlier", "Bank only: not detected", "Explained inflow drops: %(n)s week(s)", "● trigger ○ bank-only trigger ✓ explained by settlement schedule", "Fast, accurate settlements", "Ledger proves sales", "Seasonal sales pattern", "Holding", "Broken (week %(n)s)", "Not available", 칩 "Explained"
- 배치: 스펙 6장 스케치처럼 "주간 흐름과 탐지기" 카드(`loan.html:164`) 바로 위. 주간 표의 트리거 칸 뒤에 `explained`가 있으면 `<span class="chip ok">설명됨</span>`을 붙인다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_usd_loan_shows_track_comparison_and_basis():
    # 480행 test_usd_loan_repayment_and_chart_are_native의 인라인 USD 대출 구성에 아래를 더한다
    loan["approval_basis"] = [{"code": "S003", "metric": "recon_ratio", "approved": .99, "floor": .95}]
    weeks = [week(1, [], [], []), week(2, [], [], []),  # week(n, full, bank, explained, basis=())
             week(3, ["basis_breach"], [], [], basis=[{"code": "S003", "value": .81, "status": "breach"}]),
             week(4, [], ["inflow_drop"], [{"trigger": "inflow_drop"}])]
    page = client.get(f"/loans/{loan_id}?lang=en").text
    assert "Monitoring comparison" in page and "Alt data detected 1 week(s) earlier" in page  # 전체 3주차, 은행만 4주차
    assert "Approval basis" in page and "Broken (week 3)" in page and "0.99" in page and "0.81" in page
    assert "Settlement assignment" in page  # 원인 라벨 영문

def test_old_usd_replay_hides_track_cards():
    # 같은 인라인 USD 대출 구성, approval_basis 없음, 주차 항목에 bank_only_triggers·explained 키 없음
    loan["monitoring"]["weeks"] = [{"week": 1, "row": row, "triggers": []},
                                   {"week": 2, "row": row, "triggers": ["inflow_drop"]}]
    page = client.get(f"/loans/{loan_id}?lang=en").text
    assert "Monitoring comparison" not in page and "Approval basis" not in page and "Explained" not in page
    assert page.count('class="chip') >= 1  # 기존 트리거 칩은 그대로 보인다
```

`week(n, full, bank, explained, basis=())`는 이 테스트 파일 안의 작은 도우미다(`triggers=full`, `bank_only_triggers=bank`, `row`는 기존 테스트의 USD 행). 영문 문구는 위 라벨 목록의 영문과 같아야 한다.

- [ ] **Step 2: 실행해 실패 확인**

Run: `.venv\Scripts\python.exe -m pytest tests\console\test_usd_views.py -q`
Expected: 새 테스트 FAIL

- [ ] **Step 3: 구현.** 위 Interfaces대로. 한국어 화면(`lang` 없음)도 한 번 열어 깨지지 않는지 기존 `tests\console\test_monitoring_view.py`로 확인한다.

- [ ] **Step 4: 실행해 통과 확인**

Run: `.venv\Scripts\python.exe -m pytest tests\console -q`
Expected: PASS

- [ ] **Step 5: 전체 테스트**

Run: `.venv\Scripts\python.exe -m pytest -q -x`
Expected: PASS(기존 실패가 있으면 `main`에서도 같은지 확인하고 보고한다)

- [ ] **Step 6: 커밋**

```powershell
git add src\console tests\console
git commit -m "feat(console): monitoring track comparison and approval basis cards"
```

---

### Task 7: 배포와 운영 장면 재생

**Files:**
- Modify(세션 폴더, 저장소 밖): 장면 실행 스크립트(v2 Task 9에서 확장한 것)에 재생·대기 단계 추가
- Modify: `docs\runbook\deploy-history.md`

- [ ] **Step 1: PR.** 브랜치를 push하고 PR을 연다. CI가 통과해야 한다.

- [ ] **Step 2: 좁은 창으로 배포.** 순서: 오케스트레이터 → 모의 API → 콘솔. 각 공개 창은 `finally`에서 닫는다.
  - 오케스트레이터가 먼저인 이유: 새 `UsdPgFlow`(`prior_year_orders` 기본값 있음)는 옛 모의 API 응답도 읽는다. 반대로 옛 오케스트레이터의 `UsdPgFlow`가 `extra="forbid"`면 새 모의 API 응답을 거절한다.
  - 콘솔이 마지막인 이유: 새 콘솔은 옛 기록도 그리지만(Review Focus 4), 옛 콘솔은 `basis_breach` 라벨이 없다.

- [ ] **Step 3: 장면 대출 준비.**
  - v2 Task 9에서 이미 등록된 snackbox·campgear 대출에는 `approval_basis`가 없다. 두 셀러로 **새 신청**을 내고(90초 이상 간격) 심사역 승인까지 한다. 등록된 대출 문서에 `approval_basis`(snackbox: S003, campgear: S004)가 있는지 확인한다.
  - 장면 ②는 dailylook·gearstore·linen의 기존 승인 대출을 다시 재생한다(근거 빈 목록이라 재등록 불필요). 재생 재실행이 막혀 있으면 같은 셀러로 새 신청을 낸다.

- [ ] **Step 4: 재생.** 한 번에 한 대출씩 13주 재생을 돌리고, 끝나면 다음 대출로 넘어간다(조사 에이전트 429 방지). 대출 화면에서 확인한다.
  - ①: 은행만 미감지, 3~4주차 승인 근거 훼손, 원인 "정산 양도"
  - ②: 세 대출 모두 "N주 먼저 감지" 또는 "은행만: 미감지"
  - ③: 전체 트랙 트리거 없음, 설명된 입금 감소 3주 이상

  Task 4의 고정값과 다르면 운영 기준일(2026-11-25) 차이인지 먼저 보고, 원인과 함께 보고한다.

- [ ] **Step 5: 기록과 보고.** `deploy-history.md`에 커밋, 배포 순서, 장면별 대출 ID와 두 트랙 첫 주차를 적고 커밋한다. 사용자에게 장면별 대출 화면 링크와 조정한 값(있으면)을 보고한다.
