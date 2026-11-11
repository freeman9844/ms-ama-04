# 사후 13주 재생 현실화와 조기경보 원인 분류 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사후 재생을 원인이 다른 네 셀러 이야기(매출 부진·정산 보류·반품 급증·비용 증가)로 바꾸고, 사후 PG 원장·주문 데이터를 `/pg/flow`로 노출하며, 결정적 탐지기가 원인 후보를 내고 조기경보 에이전트가 근거와 함께 주원인을 고르게 한다.

**Architecture:** Mock은 `sellerdata`에 난수 순서를 바꾸지 않는 훅(반품률·주문 수집·보류·추가 상계·선정산 플랫폼·워밍업 길이)을 더하고, `postloan.py`가 시나리오 계획(`postloan_scenarios.py`)을 조합해 계좌 흐름 + PG 원장 + 주문을 만든다. 에이전트 쪽은 새 모듈 `agents/ledger_monitor.py`가 원장 기준선·주간 원장 필드·새 트리거 4개·원인 후보를 한 곳에서 계산하고(`observe`), 오케스트레이터·평가가 같은 함수를 쓴다. 경보 계약에 `primary_cause`·`secondary_causes`를 더하고 콘솔은 저장된 값만 그린다.

**Tech Stack:** Python 3.12, Pydantic v2, FastAPI(Mock·콘솔) + Jinja2, httpx, pytest, Azure Functions(오케스트레이터), Azure AI Foundry Agents(Responses strict 스키마), `ops` CLI 배포.

**Spec:** `docs/superpowers/specs/2026-11-11-postloan-replay-realism-design.md`

## Global Constraints

- 심사 단계 데이터 불변: `generate_case(...)`의 거래·PG 원장은 이 작업 전후로 같아야 한다(Task 2의 해시 고정 테스트). 기존 훅은 기본값에서 난수 호출 순서·값이 바뀌지 않는다.
- 정책 v3·`CashFlowFeatures`·심사 에이전트·R1xx 규칙은 바꾸지 않는다. `agents/classify.py`에는 사후 전용 규칙 1줄(`"TRANSFER_IN", "대출실행"` → `LOAN_PROCEEDS`)만 추가한다.
- 원인 enum 값(정확히): `sales_slump`, `settlement_hold`, `return_spike`, `cost_surge`, `high_cost_borrowing`. 한국어 라벨: 매출 부진, 정산 보류, 반품 급증, 비용 증가, 고금리 차입.
- 새 트리거 이름(정확히): `sales_slump`, `settlement_hold`, `return_spike`, `cost_surge`. `TRIGGERS` 튜플 **뒤에** 붙인다(기존 순서 유지).
- 시작 임계값: 판매 부진 3주 주문액 < 기대의 0.75, 보류 ≥ 원장 기준 주간 정산의 30% 또는 한 플랫폼 2주 연속 지급 0(그 플랫폼 주문은 기준의 0.75 이상), 반품률 2주 연속 ≥ max(기준×2, 기준+5%p), 공제율 2주 연속 ≥ 기준+4%p. 원장 기준선 = 대출 전 8주(56일).
- 새 트리거 4개는 STABLE 셀러(데모 17개 + 평가 STABLE 경로)에서 발화 0. 임계값을 바꿔야 통과하면 바꾼 값과 이유를 보고서에 적는다(시나리오 탐지 테스트도 함께 통과해야 한다).
- 자체 대출금 입금 적요 `LENDER_CREDIT = "에이엠에이렌딩 대출실행"`(`shared/repayment.py`). 이 입금과 선정산 입금(적요에 "선정산")은 `loan_proceeds`·`new_debt`에 넣지 않는다.
- 옛 데이터 호환: 옛 경보(원인 없음) → 콘솔 "—", 옛 재생(원장·`ledger_baseline` 없음) → 원장 표 대신 "정산 원장 없음", 옛 `PgSettlement` JSON(보류 필드 없음) → 0. `FlowScenario("stacking")`은 `SALES_SLUMP`로 읽힌다.
- Mock 하위 호환: `amount_krw`·`installment_krw`가 없으면 상환액 287,000원·대출금 입금 없음. 둘 중 하나만 오면 422.
- 배포 순서: Mock API → 오케스트레이터 → 콘솔. 배포·푸시·`ops eval`은 사용자 승인 뒤에만.
- 테스트 명령: `$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python -m pytest <경로> -q -p no:cacheprovider`.
- 커밋: 메시지를 `Out-File -Encoding utf8NoBOM` 임시 파일로 쓰고 `git commit -F`, 트레일러 `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`·`Copilot-Session: 05f1ca1b-6073-417d-bfb1-6b546f82cb58`. `memory261105.md`·`ops/reports/load-*`는 커밋하지 않는다.

## 계획 단계 결정(스펙 §11 위임분)

- **비용 증가 탐지는 원장 공제율로만 한다.** 스펙 §6의 "광고·물류 출금 1.5배" 경로는 넣지 않는다: 광고 충전·택배비는 월 1회 출금이라 주 단위 비교가 안 되고, 모니터링 분류(`Category`)에 광고·택배 범주가 없다. 대신 시나리오가 광고비를 플랫폼 상계(공제)로도 올려 원장에서 보이게 한다.
- 매출 부진 시나리오도 6주차부터 상계율을 올려(광고 1.8배의 일부가 플랫폼 광고) 부원인 비용 증가가 데이터에 드러나게 한다.
- 원장 주간 집계는 주간 행(`row`)에 합쳐 저장한다(콘솔 표·근거 ID가 그대로 쓴다). 플랫폼×주 세부는 저장하지 않고 원장 JSON(`pg_flow.json`)에서 도구 호출 때 계산한다.
- 대출 전 8주 원장은 사후 시뮬레이션의 워밍업에서 만든다(사후 전용 `warmup_days = 75 + 56`). 심사 원장(`case.pg_settlements`)을 쓰면 상계 기준율이 다른 난수에서 나와 STABLE에 공제율 오탐이 생긴다.
- 평가 경로 10개: 원인 4개 × (HEALTHY_SEASONAL, STRESSED) + STABLE × (HEALTHY_SEASONAL, TAX_MISMATCH).

## Review Focus

1. 자체 대출금 입금(1일차)과 기존 선정산(mugshop-21)이 1주차 `new_debt`를 일으키면 모든 재생이 "고금리 차입" 경보로 시작한다 → Task 5 `test_own_disbursement_and_prior_advance_are_not_new_debt`.
2. 시나리오 셀러에서 정답 원인 트리거보다 `buffer_drop`·`projected_shortfall`·`new_debt`가 먼저 뜨면 첫 경보 원인이 틀린다 → Task 5 `test_true_cause_fires_first`(데모 4셀러 × as_of 3개).
3. 계절(패션 11월 1.3 → 1월 0.82)·명절이 낀 기간에 STABLE 셀러에게 새 트리거가 뜬다 → Task 5 `test_stable_sellers_never_fire_ledger_triggers`(as_of 4개, 데모 17셀러).
4. PG 동의·`/pg/flow` 호출이 실패해도 재생은 계좌 데이터만으로 끝까지 가고, 원장 도구는 "데이터 없음"을 준다 → Task 7 `test_replay_continues_without_pg_flow`, Task 6 `test_settlement_ledger_tool_without_data`.
5. 옛 경보·옛 재생 문서로 콘솔 상세를 열면 오류 없이 "—"·"정산 원장 없음"이 나온다 → Task 8 `test_legacy_alert_and_replay_render`.

---

### Task 1: 계약 — 원인 enum, 경보 원인 필드, 신호 종류, 원장 보류·주문 계약

**Files:**
- Modify: `src/shared/contracts.py`(PgSettlement 87-100, SignalKind 262, EarlyWarningAlert 287)
- Test: `tests/shared/test_contracts.py`(없으면 기존 계약 테스트 파일에 추가)

**Interfaces:**
- Produces: `AlertCause(StrEnum)` = `SALES_SLUMP="sales_slump"`, `SETTLEMENT_HOLD="settlement_hold"`, `RETURN_SPIKE="return_spike"`, `COST_SURGE="cost_surge"`, `HIGH_COST_BORROWING="high_cost_borrowing"`
- Produces: `EarlyWarningAlert.primary_cause: AlertCause | None = None`, `secondary_causes: tuple[AlertCause, ...] = ()`
- Produces: `SignalKind.SETTLEMENT_HOLD="settlement_hold"`, `RETURN_SPIKE="return_spike"`, `COST_SURGE="cost_surge"`
- Produces: `PgSettlement.held_krw: int = Field(default=0, ge=0)`, `released_krw: int = Field(default=0, ge=0)`, 불변식 `net == gross - fee - deduction - held + released`
- Produces: `PgOrderDay(Contract)`: `schema_version`, `order_on: date`, `platform: str(min 1)`, `gross_krw: int ≥0`, `returns_krw: int ≥0`, 검증 `returns_krw <= gross_krw`
- Produces: `PgFlow(Contract)`: `schema_version`, `settlements: tuple[PgSettlement, ...] = ()`, `orders: tuple[PgOrderDay, ...] = ()`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_alert_cause_values():
    assert [c.value for c in AlertCause] == [
        "sales_slump", "settlement_hold", "return_spike", "cost_surge", "high_cost_borrowing"]


def test_old_alert_json_has_no_cause(alert_kwargs):  # 기존 경보 생성 인자 픽스처 재사용 또는 새로 작성
    a = EarlyWarningAlert(**alert_kwargs)
    assert a.primary_cause is None and a.secondary_causes == ()


def test_secondary_causes_rules(alert_kwargs):
    ok = EarlyWarningAlert(**alert_kwargs, primary_cause="settlement_hold", secondary_causes=("high_cost_borrowing",))
    assert ok.secondary_causes == (AlertCause.HIGH_COST_BORROWING,)
    for bad in (
        {"primary_cause": "settlement_hold", "secondary_causes": ("settlement_hold",)},  # 주원인 중복
        {"primary_cause": None, "secondary_causes": ("cost_surge",)},  # 주원인 없이 부원인
        {"primary_cause": "sales_slump", "secondary_causes": ("cost_surge", "return_spike", "settlement_hold")},  # 3개
    ):
        with pytest.raises(ValidationError):
            EarlyWarningAlert(**alert_kwargs, **bad)


def test_pg_settlement_hold_and_release():
    held = PgSettlement(settled_on=date(2026, 11, 20), platform="쿠팡", gross_krw=1000, fee_krw=100,
                        deduction_krw=30, held_krw=870, net_krw=0)
    rel = PgSettlement(settled_on=date(2026, 12, 4), platform="쿠팡", gross_krw=0, fee_krw=0,
                       released_krw=870, net_krw=870)
    assert held.net_krw == 0 and rel.net_krw == 870
    old = PgSettlement.model_validate({"settled_on": "2026-11-20", "platform": "쿠팡", "gross_krw": 1000,
                                       "fee_krw": 100, "net_krw": 900})
    assert old.held_krw == old.released_krw == 0
    with pytest.raises(ValidationError):
        PgSettlement(settled_on=date(2026, 11, 20), platform="쿠팡", gross_krw=1000, fee_krw=100, held_krw=900, net_krw=900)


def test_pg_flow_round_trip():
    flow = PgFlow(orders=(PgOrderDay(order_on=date(2026, 11, 2), platform="쿠팡", gross_krw=500, returns_krw=20),))
    assert PgFlow.model_validate_json(flow.model_dump_json()) == flow
    with pytest.raises(ValidationError):
        PgOrderDay(order_on=date(2026, 11, 2), platform="쿠팡", gross_krw=10, returns_krw=11)
```

- [ ] **Step 2: 실패 확인** — `pytest tests/shared -q` → ImportError(`AlertCause`, `PgFlow`).
- [ ] **Step 3: 구현** — 위 Interfaces 그대로. 부원인 검증은 `EarlyWarningAlert`의 기존 `model_validator`에 더한다(최대 2개, 주원인과 중복 금지, 주원인 None이면 부원인 빈 튜플).
- [ ] **Step 4: 통과 확인** — `pytest tests/shared tests/docs -q` 전부 PASS(문서 계약 스냅샷 테스트가 깨지면 스냅샷에 새 필드를 반영).
- [ ] **Step 5: 커밋** — `feat(contracts): 경보 원인·원장 보류·주문 계약`

---

### Task 2: sellerdata 훅 — 난수 순서를 지키는 반품률·주문 수집·보류·추가 상계·선정산 플랫폼·워밍업

**Files:**
- Modify: `src/mockapis/sellerdata/channels.py`(Payout, payouts)
- Modify: `src/mockapis/sellerdata/receipts.py`(bank_receipts)
- Modify: `src/mockapis/sellerdata/engine.py`(Ledger, simulate)
- Test: `tests/mockapis/test_sellerdata_hooks.py`(신규)

**Interfaces:**
- Consumes: Task 1 `PgSettlement.held_krw/released_krw`
- Produces (`channels.py`):
  - `OrderDay` frozen dataclass: `day: date, channel: str, gross_krw: int, returns_krw: int`
  - `Payout`에 `held_krw: int = 0`, `released_krw: int = 0`; `net_krw = gross - fee - deduction - held + released`
  - `payouts(channel, industry, daily_sales, rng, *, return_rate: Callable[[date, float], float] = _same_rate, orders: list[OrderDay] | None = None)` — `kept = sales * (1 - return_rate(day, channel.return_rate) * rng.uniform(0.6, 1.4))`. 기본 `_same_rate(day, base) -> base`. `orders`가 있으면 일별 `OrderDay(day, channel.name, int(sales), int(sales) - int(kept))`를 덧붙인다(난수 호출 없음).
- Produces (`receipts.py`): `ReceiptHooks` frozen dataclass
  - `extra_offset: Callable[[date], float] = lambda d: 0.0` — `OFFSET_PLATFORMS` 상계율에 더한다
  - `hold: Callable[[Payout], bool] = lambda p: False` — 참이면 그 지급은 `held_krw = 상계 뒤 net`, net 0
  - `release_on: date | None = None` — 보류가 있었으면 그날 플랫폼별 해제 행 1개(`gross=fee=deduction=0`, `released_krw = 누적 보류`, 적요 `f"{payer} 보류 해제"`)
  - `advance_platform: str | None = None` — 있으면 `_top_platform` 대신 이 플랫폼을 선정산 양도
  - `bank_receipts(payouts, as_of, rng, *, assigned_from=None, hooks: ReceiptHooks = ReceiptHooks())`. net 0인 지급은 계좌 입금 사건을 만들지 않는다.
- Produces (`engine.py`):
  - `Ledger`에 `orders: tuple[OrderDay, ...] = ()`(워밍업 포함 전체)
  - `simulate(..., return_rate=_same_rate, receipt_hooks: ReceiptHooks | None = None, warmup_days: int = WARMUP_DAYS, ledger_from: date | None = None)` — `pg_settlements`는 `paid_on >= (ledger_from or start)`인 행(보류·해제 필드 포함)

- [ ] **Step 1: 심사 데이터 고정 테스트 작성(변경 전 코드로 값 채움)**

```python
GOLDEN = {  # Task 2 변경 전 코드로 한 번 계산해 붙여 넣는다
    "seller-dailylook-01": "<sha256>", "seller-gearstore-06": "<sha256>", "seller-mugshop-21": "<sha256>",
}

def _digest(case) -> str:
    rows = [(t.booked_on.isoformat(), t.amount_krw, t.balance_after_krw, t.description, t.txn_type_code) for t in case.transactions]
    rows += [(s.settled_on.isoformat(), s.platform, s.gross_krw, s.fee_krw, s.deduction_krw, s.net_krw) for s in case.pg_settlements]
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()


@pytest.mark.parametrize("seller", sorted(GOLDEN))
def test_underwriting_data_unchanged(seller):
    assert _digest(build_demo_cases(date(2026, 11, 2))[seller]) == GOLDEN[seller]
```

- [ ] **Step 2: 값 채우기** — 현재 코드로 세 해시를 계산해 `GOLDEN`에 넣고 PASS 확인.
- [ ] **Step 3: 훅 테스트 작성**

```python
def test_orders_collector_matches_returns(): ...
    # payouts(..., orders=buf) 결과의 gross 합 == 기본 호출 gross 합, buf의 gross - returns 합 ≈ 지급 gross 합(±채널 수 원)
def test_return_rate_hook_raises_returns():
    # return_rate=lambda d, b: 0.22 → 주 단위 반품률 0.15~0.30, 같은 rng 시드로 기본 호출과 rng.random() 다음 값이 같다
def test_hold_and_release():
    # hold=쿠팡·특정 구간 → 해당 행 net 0·held>0, 계좌 사건 없음; release_on 행 released == 보류 합, 계좌 입금 1건
def test_extra_offset_and_advance_platform():
    # extra_offset=0.08 → 쿠팡 deduction/gross 가 기본 대비 +0.08±0.001; advance_platform="스마트스토어" → 선정산 입금 적요 ADVANCE_DESCRIPTION, 스마트스토어 정산 입금 없음
def test_simulate_warmup_and_ledger_from():
    # warmup_days=131, ledger_from=start-56 → pg_settlements 최소 settled_on >= start-56, ledger.orders 최소 day == start-131
```
(각 테스트는 위 주석의 단언을 그대로 코드로 쓴다.)

- [ ] **Step 4: 실패 확인** — `pytest tests/mockapis/test_sellerdata_hooks.py -q` → 훅 테스트 FAIL, 고정 테스트 PASS.
- [ ] **Step 5: 구현** — Interfaces대로. `bank_receipts`는 기존 난수 호출(base·rise·rising·선지급 비율·리드·지연)을 같은 순서로 유지하고, 보류·해제 행은 난수를 쓰지 않는다. 해제 행은 지연 난수를 쓰지 않고 `release_on`에 바로 입금.
- [ ] **Step 6: 통과 확인** — `pytest tests/mockapis -q` 전부 PASS(고정 해시 포함).
- [ ] **Step 7: 커밋** — `feat(mockapis): 정산 원장 훅(반품·보류·상계·주문) 추가`

---

### Task 3: 사후 시나리오 엔진 — 4개 원인, 원장·주문, 상환액·대출금 입금

**Files:**
- Create: `src/mockapis/postloan_scenarios.py`(시나리오별 계획)
- Modify: `src/mockapis/postloan.py`, `src/shared/repayment.py`(`LENDER_CREDIT`)
- Test: `tests/mockapis/test_postloan.py`(갱신), `tests/agents/test_monitoring.py`(STACKING 시점을 고정한 테스트 6개 갱신)

**Interfaces:**
- Consumes: Task 2 훅, Task 1 `PgFlow`·`PgOrderDay`·`AlertCause`
- Produces (`postloan.py`):
  - `FlowScenario` = `STABLE="stable"`, `SALES_SLUMP="sales_slump"`, `SETTLEMENT_HOLD="settlement_hold"`, `RETURN_SPIKE="return_spike"`, `COST_SURGE="cost_surge"`; `_missing_`가 `"stacking"` → `SALES_SLUMP`
  - `LoanTerms` frozen dataclass: `amount_krw: int`, `installment_krw: int`
  - `PRE_LOAN_DAYS = 56`, `INSTALLMENT_KRW = 287_000`(하위 호환 기본값), `LENDER = "에이엠에이렌딩"`
  - `PostLoanFlow` 추가 필드(기본값 있음): `cause: AlertCause | None`, `secondary_causes: tuple[AlertCause, ...]`, `onset: date | None`, `pg_settlements: tuple[PgSettlement, ...]`, `orders: tuple[PgOrderDay, ...]`(둘 다 `start-56` ~ `end`); 메서드 `pg_flow() -> PgFlow`. `stacking_onset`은 "첫 외부 고금리 차입일"(없으면 None)로 뜻을 유지
  - `generate_postloan(case, scenario, start, seed, days=POSTLOAN_DAYS, *, loan: LoanTerms | None = None) -> PostLoanFlow`
- Produces (`shared/repayment.py`): `LENDER_CREDIT = "에이엠에이렌딩 대출실행"`
- Produces (`postloan_scenarios.py`): `scenario_plan(scenario, case, start, days) -> ScenarioPlan` (필드: `sales_factor`, `return_rate`, `hooks: ReceiptHooks`, `assigned_from`, `extra: list[Event]`, `adjust: Callable[[list[Event]], list[Event]]`, `cause`, `secondary`, `onset`, `force_missed: Callable[[list[date]], set[date]]`, `calibrate_default: bool`); `hold_platform(persona) -> str` (쿠팡 있으면 쿠팡, 없으면 오픈마켓 중 비중 최대)

**공통 동작:** 항상 `receipt_rng = random.Random(seed + RECEIPT_SEED_OFFSET)`, `warmup_days = WARMUP_DAYS + PRE_LOAN_DAYS`, `ledger_from = start - 56`. `case.archetype is ASSIGNED`면 `assigned_from = start`(기존 선정산 계속). `loan`이 있으면 1일차 `LENDER_CREDIT` 입금(`TRANSFER_IN`, `TxnCategory.LOAN_PROCEEDS`) + 1~5일차에 그 90%를 `"{공급처} 재고 매입"`/운영비 `TRANSFER_OUT`(SUPPLIER)로 나눠 지출, 상환액 = `loan.installment_krw`. 없으면 287,000원·입금 없음.

**시나리오(일 = 시작일 기준, 주차 n = 일 7(n-1)~7n-1):**

| 시나리오 | 원인 변화 | 결과 | 정답 |
|---|---|---|---|
| SALES_SLUMP | `sales_factor` 7일 1.0 → 34일 0.60 선형, 이후 0.60 | 35일~ 광고·카드 ×1.8, `extra_offset` +0.05; 35일 첫 대부(14일 간격, 금요일 상환); 56일~ 급여 20일·4대보험 미납; `_capex_plan`으로 부도 70~80일; 마지막 상환 강제 미납 | sales_slump / (cost_surge, high_cost_borrowing) / onset 7일 |
| SETTLEMENT_HOLD | `hold`: 지급일 14~41일의 `hold_platform` 지급; `release_on = next_business_day(start+44)`; 21일 `advance_platform`(보류 플랫폼 외 비중 최대 오픈마켓) | 28일 대부 1회(잔고가 0 아래로 안 가는 금액, 100만 원 단위 올림, 최소 150만), 해제 다음 영업일 원금+이자 일시 상환; 첫 상환일 강제 미납 | settlement_hold / (high_cost_borrowing,) / onset 14일 |
| RETURN_SPIKE | `return_rate`: 14~55일 0.225, 56일~ 0.08 | 14~55일 매주 월요일 `f"{courier} 반품회수"` AUTO_DEBIT(SHIPPING, 그 주 반품액 × 0.06); 차입 없음 | return_spike / () / onset 14일 |
| COST_SURGE | `sales_factor` 14일~ 1.05; `extra_offset` 14일~ +0.08; 광고·카드 ×2, 택배비 ×1.3(14일~) | 45일 대부 1회 100만 원, 금요일 상환 | cost_surge / (high_cost_borrowing,) / onset 14일 |
| STABLE | 없음 | 없음 | None / () / None |

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/mockapis/test_postloan.py`에 추가, 헬퍼 `_weekly(flow, key)`가 주차별 주문·반품·보류·공제 합을 낸다)

```python
TERMS = LoanTerms(amount_krw=30_000_000, installment_krw=2_610_000)
AS_OFS = [date(2026, 11, 2), date(2026, 12, 28), date(2027, 1, 25)]

@pytest.mark.parametrize("as_of", AS_OFS)
def test_sales_slump_orders_fall_before_borrowing(as_of):
    case = build_demo_cases(as_of)["seller-dailylook-01"]
    slump = generate_postloan(case, FlowScenario.SALES_SLUMP, as_of, 7, loan=TERMS)
    base = generate_postloan(case, FlowScenario.STABLE, as_of, 7, loan=TERMS)
    ratio = sum(_weekly(slump, "order_gross")[2:5]) / sum(_weekly(base, "order_gross")[2:5])  # 3~5주차
    assert ratio <= 0.70
    assert (slump.stacking_onset - slump.start).days >= 35 > (slump.onset - slump.start).days
    assert slump.cause is AlertCause.SALES_SLUMP and slump.missed_installments[-1] == due_dates(slump.start, 91)[-1]

def test_settlement_hold_ledger_shape():  # gearstore, as_of 3개 parametrize
    # 3~6주차 보류 플랫폼 지급: held>0·net==0; 7주차 해제 행 released == 보류 합; 그 플랫폼 주문은 STABLE 대비 0.9 이상
    # 4주차에 ADVANCE_DESCRIPTION 입금 처음 등장, 5주차 대부 입금 1건, missed == (첫 상환일,), default_on is None

def test_return_spike_rates():  # linen, as_of 3개
    # 주 반품률: 3~8주차 0.18~0.27, 1~2주차·10~13주차 ≤ 0.10; LOAN_PROCEEDS(자체 입금 제외)·HIGH_COST_LENDER 없음; default_on None

def test_cost_surge_deductions():  # tangerine, as_of 3개
    # 3주차~ 공제율(상계 플랫폼 deduction/gross) ≥ 1~2주차 평균 + 0.06; 쿠팡 공제율 0.09~0.13; 7주차 대부 1건; default_on None

def test_installment_and_disbursement_follow_terms():
    # loan=TERMS: 상환 출금 == -2_610_000, 1일차 LENDER_CREDIT == 30_000_000, 1~5일차 출금 합 ≈ 0.9 × 30_000_000(±1만)
    # loan=None: 상환 == -INSTALLMENT_KRW, LENDER_CREDIT 없음

def test_pg_flow_covers_pre_loan_and_post_loan():
    # flow.pg_flow(): orders 최소 order_on == start-56, 최대 == end; 모든 PgSettlement 불변식 통과; STABLE은 held·released 0

def test_stacking_value_reads_as_sales_slump():
    assert FlowScenario("stacking") is FlowScenario.SALES_SLUMP

@pytest.mark.parametrize("as_of", [date(2026, 11, 2), date(2026, 5, 4), date(2027, 2, 1), date(2026, 8, 3)])
@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("seller", [None, *DEMO_PERSONAS])
def test_sales_slump_defaults_in_window(as_of, seed, seller):  # 기존 stacking 부도 테스트 갱신
    # loan=None 그대로: default_on 이 DEFAULT_WINDOW 안, 첫 외부 차입일 >= 35일
```

기존 테스트 갱신: `STACKING_ONSET_DAY` import 제거, STACKING → SALES_SLUMP, "마지막 상환 미납"·"대부 숨김"·"급여 지연" 테스트는 새 일정(35일·56일)으로. `tests/agents/test_monitoring.py`의 `test_categorize_applies_overrides_only_to_unclassified`, `test_weekly_series_shapes_stacking`, `test_detector_trips_on_buffer_then_new_debt`, `test_stacking_inflow_drop_shows_up_within_two_months`, `test_stacking_sellers_warn_before_default`, `test_stacking_projected_shortfall_comes_before_default`는 SALES_SLUMP 기준(첫 대부 6주차 이후, `new_debt`는 6주차 이후, 전망 부족이 부도보다 먼저)으로 고친다.

- [ ] **Step 2: 실패 확인** — `pytest tests/mockapis/test_postloan.py -q` → ImportError/FAIL.
- [ ] **Step 3: 구현** — `postloan_scenarios.py`에 표의 계획, `postloan.py`는 계획 조합·자체 대출 이벤트·미납 처리(`force_missed` ∪ 잔고 부족)·원장/주문을 `PgSettlement`/`PgOrderDay`로 변환(`start-56` 이후). SETTLEMENT_HOLD 대부 금액은 대부 없이 계산한 잔고 최저치로 정한다(SALES_SLUMP의 `_capex_plan`처럼 한 번 미리 계산).
- [ ] **Step 4: 통과 확인** — `pytest tests/mockapis tests/agents/test_monitoring.py -q` PASS.
- [ ] **Step 5: 커밋** — `feat(mockapis): 원인별 사후 시나리오와 사후 정산 원장`

---

### Task 4: Mock 데모 매핑과 API — `/pg/flow`, 상환액 인자, 요청 시 생성 캐시

**Files:**
- Modify: `src/mockapis/demo.py`(STACKING_APPLICANTS → `POSTLOAN_SCENARIOS`, `build_postloan_flows` → `FlowStore`), `src/mockapis/main.py`, `src/mockapis/app.py`
- Test: `tests/mockapis/test_demo.py`, `tests/mockapis/test_app.py`

**Interfaces:**
- Consumes: Task 3 `generate_postloan(..., loan=)`, `LoanTerms`, `PostLoanFlow.pg_flow()`
- Produces (`demo.py`):
  - `POSTLOAN_SCENARIOS: dict[str, FlowScenario]` = dailylook-01 SALES_SLUMP, gearstore-06 SETTLEMENT_HOLD, linen-10 RETURN_SPIKE, tangerine-15 COST_SURGE(나머지 STABLE)
  - `class FlowStore`: `__init__(cases: Mapping[str, SyntheticCase], as_of: date, maxsize: int = 256)`, `flow(applicant_id: str, loan: LoanTerms | None) -> PostLoanFlow | None`(`(applicant_id, loan)` 키 LRU, 시작일 `next_business_day(as_of)`, 시드 `seed + POSTLOAN_SEED_OFFSET`)
- Produces (`app.py`): `create_app(..., flows: Mapping[str, PostLoanFlow] | FlowStore | None = None, ...)` — Mapping이면 loan 인자를 무시하고 그대로 쓴다(기존 테스트 호환)
  - `GET /mydata/flow?start&end&amount_krw&installment_krw` (ACCOUNT) → `list[Transaction]`
  - `GET /pg/flow?start&end&amount_krw&installment_krw` (PG) → `PgFlow`(settlements는 `settled_on`, orders는 `order_on`으로 구간 필터)
  - `amount_krw`·`installment_krw`는 둘 다 있거나 둘 다 없어야 한다(아니면 422 `loan_terms`), 둘 다 > 0, `installment_krw <= amount_krw`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_scenario_map_covers_four_sellers():
    assert {k: v.value for k, v in POSTLOAN_SCENARIOS.items()} == {
        "seller-dailylook-01": "sales_slump", "seller-gearstore-06": "settlement_hold",
        "seller-linen-10": "return_spike", "seller-tangerine-15": "cost_surge"}

def test_flow_store_caches_by_terms(demo_cases):
    store = FlowStore(demo_cases, AS_OF)
    t = LoanTerms(30_000_000, 2_610_000)
    assert store.flow("seller-gearstore-06", t) is store.flow("seller-gearstore-06", t)
    assert store.flow("seller-gearstore-06", None) != store.flow("seller-gearstore-06", t)
    assert store.flow("nobody", None) is None

def test_pg_flow_endpoint_requires_pg_scope(client_with_store): ...      # ACCOUNT 토큰 → 403, PG 토큰 → 200·settlements·orders
def test_flow_endpoints_pass_terms(client_with_store): ...              # amount/installment 주면 상환 출금 == -installment, /pg/flow orders 최소일 == start-56
def test_half_terms_rejected(client_with_store): ...                    # amount_krw만 → 422
def test_dict_flows_still_work(): ...                                   # 기존 dict 전달 create_app이 그대로 동작
```

- [ ] **Step 2: 실패 확인** — `pytest tests/mockapis/test_demo.py tests/mockapis/test_app.py -q` FAIL.
- [ ] **Step 3: 구현** — main.py는 `flows=FlowStore(_cases, _as_of)`(시작 시 사전 생성 없음). 스펙의 "사전 생성 캐시"는 요청 시 생성 + LRU로 대신한다(결정론 동일, 시작 시간 단축).
- [ ] **Step 4: 통과 확인** — `pytest tests/mockapis -q` PASS.
- [ ] **Step 5: 커밋** — `feat(mockapis): /pg/flow와 상환액 인자, 시나리오 매핑`

---

### Task 5: 원장 모니터 — 기준선·주간 원장 필드·새 트리거·원인 후보

**Files:**
- Create: `src/agents/ledger_monitor.py`
- Modify: `src/agents/monitoring.py`(TRIGGERS·WEEK_FIELDS, `weekly_series`의 `loan_proceeds`·`advance_in`), `src/agents/classify.py`(규칙 1줄)
- Test: `tests/agents/test_ledger_monitor.py`(신규), `tests/agents/test_monitoring.py`

**Interfaces:**
- Consumes: Task 1 `PgFlow`·`AlertCause`, Task 3 시나리오(테스트용), `shared.repayment.LENDER_CREDIT`
- Produces (`monitoring.py`):
  - `TRIGGERS` 끝에 `"sales_slump", "settlement_hold", "return_spike", "cost_surge"`
  - `WEEK_FIELDS` 끝에 `"advance_in", "order_gross", "orders_vs_expected", "returns", "return_rate", "held", "deductions", "deduction_rate", "settle_net"`
  - `weekly_series` 행에 `advance_in`(적요에 "선정산"인 입금 합); `loan_proceeds`는 `LENDER_CREDIT`·선정산 입금을 뺀다. `detect`는 그대로(기존 5개만)
- Produces (`ledger_monitor.py`):
  - 상수 `PRE_LOAN_DAYS = 56`, `SLUMP_RATIO = 0.75`, `SLUMP_WEEKS = 3`, `HOLD_SHARE = 0.30`, `HOLD_ZERO_WEEKS = 2`, `HOLD_ORDER_FLOOR = 0.75`, `HOLD_MIN_PLATFORM_SHARE = 0.10`, `RETURN_SPIKE_MULT = 2.0`, `RETURN_SPIKE_PP = 0.05`, `COST_SURGE_PP = 0.04`, `PERSIST_WEEKS = 2`
  - `CAUSE_OF_TRIGGER: dict[str, AlertCause]` = sales_slump·settlement_hold·return_spike·cost_surge → 같은 이름, `new_debt` → `HIGH_COST_BORROWING`
  - `CAUSE_FIELDS: dict[AlertCause, tuple[str, ...]]` = SALES_SLUMP `("order_gross", "orders_vs_expected")`, SETTLEMENT_HOLD `("held", "advance_in")`, RETURN_SPIKE `("returns", "return_rate")`, COST_SURGE `("deductions", "deduction_rate")`, HIGH_COST_BORROWING `("high_cost_lender", "high_cost_count", "loan_proceeds")`
  - `LedgerBaseline` frozen dataclass: `weekly_orders: int`(계절 제거 주간 주문), `return_rate: float`, `deduction_rate: float`, `weekly_settle_net: int`, `platform_weekly_net: dict[str, int]`, `platform_weekly_orders: dict[str, int]`, `prior_advance: bool`; `to_dict()`/`from_dict()`
  - `ledger_baseline(pg: PgFlow, start: date, industry: str, prior_txns: Sequence[Transaction]) -> LedgerBaseline | None` — `[start-56, start)` 주문·정산. 계절 제거: 주문 합 / Σ(그날 월의 `cohort(industry)["index"]`) × 7. 주문이 없으면 None. `prior_advance` = 대출 전 90일 계좌에 "선정산" 입금이 있었는가
  - `ledger_catalog(lb: LedgerBaseline | None) -> dict[str, float | int]` — `B:ledger:weekly_orders`, `B:ledger:return_rate`, `B:ledger:deduction_rate`, `B:ledger:weekly_settle_net`
  - `CauseCandidate` frozen dataclass: `cause: AlertCause`, `first_week: int`, `evidence_ids: tuple[str, ...]`, `consequence: bool`; `to_dict()`
  - `Observation` frozen dataclass: `rows: list[dict]`, `triggers: list[str]`(TRIGGERS 순), `candidates: list[CauseCandidate]`(`first_week`, TRIGGERS 순 정렬)
  - `observe(transactions, categories, start, week, baseline, pg: PgFlow | None, lb: LedgerBaseline | None) -> Observation` — `weekly_series` 행에 원장 필드를 더하고(원장·기준선 둘 다 있을 때만) 1~week 각 시점의 트리거로 후보를 만든다. 근거 ID는 첫 발화 주차의 `W:{n}:{CAUSE_FIELDS[cause] 중 행에 있는 것}`. `consequence` = 다른 원인 후보가 이 후보보다 먼저(또는 같은 주) 발화했는가(`HIGH_COST_BORROWING`에만 참이 될 수 있다)
  - `platform_weeks(pg: PgFlow | None, start: date, from_week: int, to_week: int) -> list[dict] | None` — 플랫폼×주 `{"week","platform","order_gross","returns","held","released","deductions","settle_net"}`, pg가 None이면 None

**주간 원장 필드:** `order_gross`·`returns`(주문일 기준), `return_rate = returns/order_gross`(소수 넷째, 주문 0이면 0.0), `held`·`deductions`·`settle_net`(지급일 기준, settle_net은 해제 포함), `deduction_rate = deductions/정산 gross`(소수 넷째), `orders_vs_expected` = 최근 3주(모자라면 있는 주) 주문 합 / 같은 주들의 기대 주문 합(`lb.weekly_orders × 그 주 날짜들의 월 지수 평균`, 소수 셋째).

**트리거(마지막 행 기준):** `sales_slump`: week ≥ 3 이고 `orders_vs_expected < 0.75`. `settlement_hold`: `held ≥ 0.30 × lb.weekly_settle_net`, 또는 기준 비중 ≥ 10%인 플랫폼이 최근 2주 지급 0이고 그 2주 주문 ≥ 0.75 × 기준. `return_spike`: 최근 2주 모두 `return_rate ≥ max(2×기준, 기준+0.05)`. `cost_surge`: 최근 2주 모두 `deduction_rate ≥ 기준+0.04`.

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/agents/test_ledger_monitor.py`; 헬퍼 `_observe(seller, scenario, as_of, week=13)`가 데모 케이스·`compute_underwriting` 기준선·`TERMS`·`ledger_baseline`로 `observe`를 돌린다)

```python
SCENARIO_SELLERS = {"seller-dailylook-01": FlowScenario.SALES_SLUMP, "seller-gearstore-06": FlowScenario.SETTLEMENT_HOLD,
                    "seller-linen-10": FlowScenario.RETURN_SPIKE, "seller-tangerine-15": FlowScenario.COST_SURGE}
NEW = {"sales_slump", "settlement_hold", "return_spike", "cost_surge"}
EARLY = {"buffer_drop", "projected_shortfall", "new_debt", "inflow_drop", "volatility_spike"}

@pytest.mark.parametrize("as_of", [date(2026, 11, 2), date(2026, 12, 28), date(2027, 1, 25)])
@pytest.mark.parametrize("seller", sorted(SCENARIO_SELLERS))
def test_true_cause_fires_first(seller, as_of):
    flow, obs = _observe(seller, SCENARIO_SELLERS[seller], as_of)
    first = {c.cause: c.first_week for c in obs.candidates}
    truth = first[flow.cause]
    onset_week = (flow.onset - flow.start).days // 7 + 1
    assert truth <= onset_week + 3
    assert obs.candidates[0].cause is flow.cause
    early = [w for w in range(1, 14) if EARLY & set(_triggers_at(obs, w))]
    assert not early or truth <= min(early)

@pytest.mark.parametrize("as_of", [date(2026, 9, 14), date(2026, 11, 2), date(2026, 12, 28), date(2027, 1, 25)])
def test_stable_sellers_never_fire_ledger_triggers(as_of):
    for seller in set(build_demo_cases(as_of)) - set(SCENARIO_SELLERS):
        _, obs = _observe(seller, FlowScenario.STABLE, as_of)
        hits = {(w, t) for w in range(1, 14) for t in _triggers_at(obs, w) if t in NEW}
        assert not hits, (seller, sorted(hits))

def test_own_disbursement_and_prior_advance_are_not_new_debt():
    # mugshop-21(ASSIGNED) STABLE + TERMS: 1주차 loan_proceeds == 0, advance_in > 0, lb.prior_advance is True, 13주 내내 new_debt 없음
    # gearstore SETTLEMENT_HOLD: 4주차 advance_in > 0 이지만 loan_proceeds는 5주차 대부만

def test_borrowing_marked_as_consequence():
    # gearstore: HIGH_COST_BORROWING 후보 consequence True, SETTLEMENT_HOLD 후보 consequence False

def test_no_ledger_means_no_ledger_fields_or_triggers():
    # observe(..., pg=None, lb=None): 행에 order_gross 없음, 트리거·후보에 NEW 없음, platform_weeks(None, ...) is None

def test_ledger_ids_are_evidence_ids():
    # evidence_catalog(rows) ∪ ledger_catalog(lb)의 모든 키가 EVIDENCE_ID_PATTERN에 맞고 W:5:held 등이 들어 있다
    # LedgerBaseline.from_dict(lb.to_dict()) == lb
```

`_triggers_at(obs, w)`는 테스트 헬퍼로 `observe(..., week=w).triggers`를 돈다(또는 `Observation`이 주차별 트리거 이력 `history: dict[int, list[str]]`를 함께 내면 그것을 쓴다 — 구현 선택).

- [ ] **Step 2: 실패 확인** — `pytest tests/agents/test_ledger_monitor.py -q` → ImportError.
- [ ] **Step 3: 구현** — Interfaces대로. `classify.py` 규칙 표에 `("TRANSFER_IN", "대출실행", Category.LOAN_PROCEEDS)` 추가.
- [ ] **Step 4: 통과 확인** — `pytest tests/agents -q` PASS. `test_true_cause_fires_first`가 시나리오 수치 때문에 실패하면 Task 3 시나리오 수치(시작값)를 조정하고 Task 3 테스트도 다시 통과시킨다. 임계값을 바꿔야 하면 Global Constraints 규칙대로 보고.
- [ ] **Step 5: 커밋** — `feat(agents): 원장 모니터(원장 기준선·새 트리거·원인 후보)`

---

### Task 6: 조기경보 에이전트 — 원장 도구, 원인 인자, 원인 근거 검증, 대체 경보; 추심 한 줄

**Files:**
- Modify: `src/agents/early_warning.py`, `src/agents/collections.py:60-61`
- Test: `tests/agents/test_early_warning.py`

**Interfaces:**
- Consumes: Task 5 `CauseCandidate`, `CAUSE_FIELDS`, `LedgerBaseline`, `ledger_catalog`, `platform_weeks` 결과 형식; Task 1 `AlertCause`
- Produces:
  - `EwContext` 새 필드(기본값): `candidates: Sequence[CauseCandidate] = ()`, `pg_weeks: Sequence[Mapping[str, Any]] | None = None`(None = 원장 없음), `ledger_baseline: LedgerBaseline | None = None`. `catalog`에 `ledger_catalog` 병합
  - 도구 `get_settlement_ledger(from_week: int, to_week: int)` → 원장 있으면 `{"available": True, "weeks": [...]}`(구간 1 ≤ from ≤ to ≤ 현재 주차, 아니면 `{"error": "invalid_week"}`), 없으면 `{"available": False, "weeks": [], "note": "정산 원장 데이터 없음"}`
  - `get_baseline` 결과에 `"cause_candidates": [c.to_dict() ...]`
  - `raise_alert` 인자 `primary_cause`(enum AlertCause 값), `secondary_causes`(array of enum)
  - `_TRIGGER_TEXT` 추가: `sales_slump` → `("inflow_drop", "W:{week}:orders_vs_expected", "최근 3주 주문액이 정상 수준보다 낮다")`, `settlement_hold` → `("settlement_hold", "W:{week}:held", "정산 보류액이 있다")`, `return_spike` → `("return_spike", "W:{week}:return_rate", "반품률이 기준선보다 크게 높다")`, `cost_surge` → `("cost_surge", "W:{week}:deduction_rate", "정산 공제율이 기준선보다 높다")`

**check_alert 추가 규칙:** 후보가 있으면 `primary_cause` 필수이고 후보 원인 중 하나, 후보가 없으면 `primary_cause`는 비어 있어야 한다. `secondary_causes` ⊆ 후보, 주원인 제외, 최대 2개. 주원인이 있으면 신호 하나 이상이 `W:{n}:{CAUSE_FIELDS[주원인] 중 하나}`를 `metric_ids`에 인용해야 한다. 위반은 한국어 issue로 돌려 기존 재시도 경로를 탄다.

**fallback_alert:** `primary_cause = candidates[0].cause`(없으면 None), `secondary_causes` = 이어지는 서로 다른 후보 최대 2개.

**INSTRUCTIONS 추가(정확한 문구):** 1단계 도구 묶음에 `get_settlement_ledger`를 넣고, 새 단계로 "get_baseline의 cause_candidates가 원인 후보다. 차입은 대개 결과다. 차입보다 앞선 주문·정산·반품·비용 변화가 있으면 그것을 주원인으로 고른다. primary_cause는 후보 중 하나여야 하고, 그 원인의 지표(매출 부진: W:n:order_gross·orders_vs_expected, 정산 보류: W:n:held·advance_in, 반품 급증: W:n:returns·return_rate, 비용 증가: W:n:deductions·deduction_rate, 고금리 차입: W:n:high_cost_lender·high_cost_count·loan_proceeds)를 신호에 인용한다. 후보가 없으면 primary_cause를 비운다."

**collections.py:** 61행 `loan_stacking·inflow_drop` → `loan_stacking·inflow_drop·settlement_hold·return_spike`, 60행 문장에 "정산 보류·반품 급증"을 더한다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_raise_alert_requires_candidate_cause(ctx_with_candidates):  # 후보 = [SETTLEMENT_HOLD w3, HIGH_COST_BORROWING w5]
    r = ctx_with_candidates.raise_alert({**ARGS, "primary_cause": "sales_slump"})
    assert r["accepted"] is False and any("후보" in i for i in r["issues"])

def test_primary_cause_must_cite_its_metric(ctx_with_candidates):
    r = ctx_with_candidates.raise_alert({**ARGS_CITING_ONLY_LOAN, "primary_cause": "settlement_hold"})
    assert r["accepted"] is False
    r = ctx_with_candidates.raise_alert({**ARGS_CITING_HELD, "primary_cause": "settlement_hold",
                                         "secondary_causes": ["high_cost_borrowing"]})
    assert r["accepted"] is True
    assert ctx_with_candidates.accepted.primary_cause is AlertCause.SETTLEMENT_HOLD

def test_no_candidates_means_no_primary(ctx_without_candidates):
    assert ctx_without_candidates.raise_alert({**ARGS, "primary_cause": "sales_slump"})["accepted"] is False
    assert ctx_without_candidates.raise_alert(ARGS)["accepted"] is True

def test_fallback_uses_earliest_candidate(ctx_with_candidates):
    a = fallback_alert(ctx_with_candidates, "timeout")
    assert a.primary_cause is AlertCause.SETTLEMENT_HOLD and a.secondary_causes == (AlertCause.HIGH_COST_BORROWING,)

def test_settlement_ledger_tool(ctx_with_candidates):
    out = ctx_with_candidates.get_settlement_ledger({"from_week": 3, "to_week": 4})
    assert out["available"] and {w["week"] for w in out["weeks"]} == {3, 4}
    assert ctx_with_candidates.get_settlement_ledger({"from_week": 0, "to_week": 99}) == {"error": "invalid_week"}

def test_settlement_ledger_tool_without_data(ctx_without_candidates):  # pg_weeks=None
    assert ctx_without_candidates.get_settlement_ledger({"from_week": 1, "to_week": 1})["available"] is False

def test_tool_schema_lists_causes():
    raise_alert = next(t for t in TOOLS if t["name"] == "raise_alert")
    assert "primary_cause" in json.dumps(raise_alert) and "get_settlement_ledger" in {t["name"] for t in TOOLS}
    assert "차입은 대개 결과다" in INSTRUCTIONS
```

픽스처는 gearstore SETTLEMENT_HOLD 흐름을 Task 5 `observe`로 5주차까지 돌려 만든다(`ctx_without_candidates`는 STABLE·pg=None).

- [ ] **Step 2: 실패 확인** — `pytest tests/agents/test_early_warning.py -q` FAIL.
- [ ] **Step 3: 구현** — Interfaces·규칙대로. `_fn` 스키마는 기존 `query_transactions` 선택 인자와 같은 방식으로 선언(strict 요구사항이 다르면 기존 패턴을 따른다).
- [ ] **Step 4: 통과 확인** — `pytest tests/agents -q` PASS.
- [ ] **Step 5: 커밋** — `feat(agents): 조기경보 원인 분류와 정산 원장 도구`

---

### Task 7: 오케스트레이터 — PG 동의·상환액 전달·원장 저장·원장 기준선·observe 연결

**Files:**
- Modify: `src/orchestrator/journey/models.py:15`, `journey/activities.py:63`, `journey/adapters.py:35-75`, `journey/monitoring.py`(`start_replay` 66-150, `process_week` 326-400)
- Test: `tests/orchestrator/test_monitoring_handlers.py`, `tests/agents/test_data_assembly.py:37`(가짜 fetch 서명)

**Interfaces:**
- Consumes: Task 4 API(`/pg/flow`, `amount_krw`·`installment_krw`), Task 5 `observe`·`ledger_baseline`·`platform_weeks`·`LedgerBaseline`, Task 6 `EwContext` 새 필드
- Produces:
  - `MONITORING_SCOPES = ("account", "pg")`
  - `MockApi.fetch(source: str, token: str, params: Mapping[str, str | int] | None = None) -> Any`; `HttpMockApi.fetch`는 `params`를 httpx에 넘긴다. `SOURCE_PATHS["pg_flow"] = "/pg/flow"`
  - `start_replay`: `terms = {"amount_krw": loan["amount_krw"], "installment_krw": round(monthly_installment(...))}`(원장 `build_schedule`과 같은 값)로 `flow`·`pg_flow`를 가져온다. `pg_flow` 실패(`SourceError` 포함 모든 예외)는 경고 로그 후 None. 있으면 raw `pg_flow.json` 저장. `mon["ledger_baseline"] = ledger_baseline(...).to_dict()` 또는 None(`prior_txns` = 심사 raw `transactions.json`, 없으면 빈 목록). `mon["pg"] = "ok" | "missing"`
  - `process_week`: `observe(...)`로 rows·triggers·candidates를 얻고, `entry["candidates"] = [c.to_dict() ...]`, `EwContext(..., candidates=, pg_weeks=platform_weeks(pg, start, 1, week), ledger_baseline=)`. 원장 JSON이 없거나 `ledger_baseline`이 없으면 pg·lb = None

- [ ] **Step 1: 실패하는 테스트 작성** (기존 가짜 Mock의 `fetch(kind, token)` → `fetch(kind, token, params=None)`로 바꾸고 호출 기록을 남긴다)

```python
def test_replay_passes_terms_and_pg_scope(deps_with_fake_mock):
    start_replay(deps, LOAN_ID)
    assert fake_mock.consent_scopes[-1] == ["account", "pg"]
    calls = {c.source: c.params for c in fake_mock.calls}
    expected = round(monthly_installment(LOAN["amount_krw"], LOAN["tenor_months"]))
    assert calls["flow"] == calls["pg_flow"] == {"amount_krw": LOAN["amount_krw"], "installment_krw": expected}
    loan = deps.store.read(LOANS, LOAN_ID, LOAN_ID)
    assert loan["monitoring"]["ledger_baseline"]["weekly_orders"] > 0 and loan["monitoring"]["pg"] == "ok"
    assert all(s["amount_krw"] == expected for s in loan["repayment"]["schedule"])  # 키 이름은 기존 원장 형식을 따른다

def test_replay_continues_without_pg_flow(deps_with_fake_mock):
    fake_mock.fail_on = {"pg_flow"}
    start_replay(deps, LOAN_ID)
    for week in range(1, 14):
        process_week(deps, LOAN_ID, replay_id, week)
    mon = deps.store.read(LOANS, LOAN_ID, LOAN_ID)["monitoring"]
    assert mon["pg"] == "missing" and mon["ledger_baseline"] is None and mon["status"] == "completed"
    assert all("order_gross" not in w["row"] for w in mon["weeks"])

def test_week_entry_records_candidates_and_alert_cause(deps_with_fake_mock):  # gearstore SETTLEMENT_HOLD, 가짜 에이전트 runner=None → fallback
    # 첫 경보 문서 primary_cause == "settlement_hold", entry["candidates"][0]["cause"] == "settlement_hold"
```

- [ ] **Step 2: 실패 확인** — `pytest tests/orchestrator/test_monitoring_handlers.py -q` FAIL.
- [ ] **Step 3: 구현** — Interfaces대로. 가짜 fetch 서명을 쓰는 다른 테스트(`tests/agents/test_data_assembly.py:37` 등)도 `params=None`을 받게 고친다.
- [ ] **Step 4: 통과 확인** — `pytest tests/orchestrator tests/agents -q` PASS.
- [ ] **Step 5: 커밋** — `feat(orchestrator): 재생에 PG 원장·상환액·원인 후보 연결`

---

### Task 8: 콘솔 — 원인 칩, 주간 정산 원장 표, 라벨, 미리보기 픽스처

**Files:**
- Modify: `src/console/monitoring.py`(라벨·`loan_view`), `src/console/templates/loan.html`(경보 카드 170-183, 주간 표 98-120 근처), `src/consolepreview/fixtures.py`
- Test: `tests/console/test_monitoring_view.py`(기존 콘솔 모니터링 테스트 파일에 추가)

**Interfaces:**
- Consumes: 경보 문서 `primary_cause`·`secondary_causes`, 주간 행 원장 필드, `mon["ledger_baseline"]`, Task 5 `ledger_catalog`
- Produces:
  - `CAUSE_LABELS = {"sales_slump": "매출 부진", "settlement_hold": "정산 보류", "return_spike": "반품 급증", "cost_surge": "비용 증가", "high_cost_borrowing": "고금리 차입"}`
  - `TRIGGER_LABELS` 추가: `sales_slump` 주문 감소, `settlement_hold` 정산 보류, `return_spike` 반품 급증, `cost_surge` 공제 증가
  - `loan_view` 경보 항목에 `cause_label: str`(없으면 "—"), `secondary_labels: list[str]`; 반환에 `ledger_rows: list[dict]`(원장 필드가 있는 주차만 `{"week","order_gross","returns","held","deductions","settle_net"}`), `ledger_note: str | None`(원장 행이 없으면 "정산 원장 없음"). `catalog`에 `ledger_catalog(LedgerBaseline.from_dict(mon["ledger_baseline"]))` 병합(있을 때만)
  - 템플릿: 경보 카드 머리 `주원인: {{ cause_label }}`, 부원인 있으면 `부원인: …`; 모니터링 탭에 "주간 정산(PG 원장)" 표(주차·주문액·반품·보류·공제·지급, 금액은 기존 금액 필터). 인라인 스크립트·스타일 금지, 기존 CSS 클래스 재사용

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_alert_cause_chip(loan_with_ledger, alert_with_cause):
    v = loan_view(loan_with_ledger, [alert_with_cause], None, KEY, restore=False)
    assert v["alerts"][0]["cause_label"] == "정산 보류" and v["alerts"][0]["secondary_labels"] == ["고금리 차입"]
    assert v["ledger_rows"][2]["held"] > 0 and v["ledger_note"] is None

def test_legacy_alert_and_replay_render(client, legacy_loan, legacy_alert):  # 원장 필드·ledger_baseline·primary_cause 없음
    html = client.get(f"/loans/{legacy_loan['id']}").text
    assert "주원인: —" in html and "정산 원장 없음" in html

def test_ledger_table_renders(client, loan_with_ledger):
    html = client.get(f"/loans/{loan_with_ledger['id']}").text
    assert "주간 정산(PG 원장)" in html and "보류" in html
```

- [ ] **Step 2: 실패 확인** — `pytest tests/console -q` FAIL.
- [ ] **Step 3: 구현** — 콘솔은 값을 다시 계산하지 않는다(행·경보 문서 값만). 미리보기 픽스처 한 경보에 `primary_cause: "settlement_hold"`, 주간 행에 원장 필드를 넣는다.
- [ ] **Step 4: 통과 확인** — `pytest tests/console -q` PASS.
- [ ] **Step 5: 커밋** — `feat(console): 경보 원인 칩과 주간 정산 원장 표`

---

### Task 9: 평가 — 원인 경로·원인 정확도·`ew_wrong_cause`

**Files:**
- Modify: `src/evals/datasets.py`(`EwPath`, `ew_paths`), `src/evals/suites/early_warning.py`, `src/evals/fake.py:147`, `src/evals/report.py`(STACKING 참조)
- Test: `tests/evals/test_early_warning_suite.py`(기존 평가 테스트 파일), `tests/evals/test_datasets.py`

**Interfaces:**
- Consumes: Task 3 `generate_postloan(..., loan=)`, `PostLoanFlow.cause/onset/pg_flow()`, Task 5 `observe`·`ledger_baseline`, Task 6 `EwContext` 새 필드
- Produces:
  - `EW_CAUSE_SCENARIOS = (SALES_SLUMP, SETTLEMENT_HOLD, RETURN_SPIKE, COST_SURGE)`; `ew_paths()` 10개: 원인 4 × (HEALTHY_SEASONAL, STRESSED) + STABLE × (HEALTHY_SEASONAL, TAX_MISMATCH). 경로 ID `ev-e{archetype[:4]}{s|h|r|c|b}`. 흐름은 `loan=LoanTerms(REQUESTED_AMOUNT_KRW, round(monthly_installment(REQUESTED_AMOUNT_KRW, REQUESTED_TENOR_MONTHS)))`
  - `EwPath.positive` = `scenario is not STABLE`; `EwPath.onset_week: int | None`
  - `replay()` 결과에 `first_alert_cause`, `last_alert_cause`, `detector_cause`(첫 트리거 주차의 `candidates[0].cause`), `in_window`(첫 경보 주차 ≤ onset_week + 3)
  - `run()`: 실패 코드 `ew_missed`(양성 경로가 창 안 경보 없음), `ew_false_alarm`(그대로), `ew_wrong_cause`(첫 경보 주원인 ≠ 정답); 지표 `cause_accuracy`(첫 경보), `last_cause_accuracy`, `lead_days`는 `default_on` 있는 경로만, `detector_only.cause_accuracy`
  - `fake.py`: 가짜 에이전트가 `get_baseline`의 `cause_candidates[0]`을 `primary_cause`로, 그 원인 지표 ID를 신호에 인용

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_ew_paths_cover_causes():
    paths = ew_paths()
    assert len(paths) == 10 and len({p.path_id for p in paths}) == 10
    assert sorted(p.flow.scenario.value for p in paths if p.positive) == sorted(
        [s.value for s in EW_CAUSE_SCENARIOS] * 2)
    assert all(p.flow.cause is not None and p.onset_week for p in paths if p.positive)

def test_fake_run_scores_causes(fake_runner):
    out = run(ew_paths(), ["fake"], meter=..., runner=fake_runner, pseudonym_key=b"k")
    m = out["configs"]["fake"]
    assert m["cause_accuracy"] == 1.0 and out["detector_only"]["cause_accuracy"] == 1.0
    assert not [f for f in out["failures"] if f["code"] in ("ew_missed", "ew_false_alarm", "ew_wrong_cause")]

def test_wrong_cause_is_reported(fake_runner_wrong_cause):  # 두 번째 후보를 고르는 가짜
    out = run(ew_paths()[:1], ["fake"], ...)
    assert any(f["code"] == "ew_wrong_cause" for f in out["failures"])
```

- [ ] **Step 2: 실패 확인** — `pytest tests/evals -q` FAIL.
- [ ] **Step 3: 구현** — Interfaces대로. 실패 코드 목록·리포트 표에 `ew_wrong_cause`와 원인 정확도 열을 더한다.
- [ ] **Step 4: 통과 확인** — `pytest tests/evals -q` PASS. `ops eval`은 실행하지 않는다.
- [ ] **Step 5: 커밋** — `feat(evals): 조기경보 원인 정확도와 원인 경로`

---

### Task 10: 문서·전체 검증·배포

**Files:**
- Modify: `docs/limitations.md:39-46`(재생 설명), `docs/agents.md`(조기경보 절: 원인 후보·원장 도구·원인 근거 규칙), `docs/runbook/deploy-history.md`

- [ ] **Step 1: 문서 갱신** — 재생 4셀러 이야기·원장 노출·상환액 일치·대출금 입금·비용 증가 탐지 범위(공제율만), 옛 경보·재생 표시 규칙.
- [ ] **Step 2: 전체 테스트** — `pytest tests/orchestrator tests/agents tests/console tests/evals tests/shared tests/mockapis tests/docs tests/infra -q` 전부 PASS.
- [ ] **Step 3: 커밋** — `docs: 사후 재생 현실화와 원인 분류 문서`
- [ ] **Step 4: (사용자 승인 뒤) 배포** — `.\.venv\Scripts\python -m ops window open --acr` → `deploy mockapi` → `deploy orchestrator` → `deploy console` → `smoke` → `window close` → 운영자 IP WAF 규칙 재확인.
- [ ] **Step 5: 라이브 확인** — 네 셀러 대출로 새 재생을 돌려 첫 경보 원인 칩(매출 부진·정산 보류·반품 급증·비용 증가)과 정산 표 확인, STABLE 한 셀러는 새 트리거 없음 확인.
- [ ] **Step 6: deploy-history 기록·커밋, (사용자 승인 뒤) 푸시**
