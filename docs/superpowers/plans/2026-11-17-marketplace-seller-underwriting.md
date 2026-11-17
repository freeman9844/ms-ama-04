# 마켓 셀러 데이터 기반 차별화 심사(정책 v4) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 마켓 판매 성과(입점 기간·평점·리뷰·반품·취소·판매정지·정산 예정금)를 새 동의 범위로 받아 결정적 지표로 만들고, 정책 v4가 셀러 탈락·검토 규칙, 대체 규칙, 정산 예정금 한도 보강으로 판정 자체를 바꾸게 한다. 에이전트와 콘솔은 그 근거와 "현금흐름만 볼 때" 비교를 보여 준다.

**Architecture:** Mock API가 같은 합성 셀러 원장에서 `MarketplaceStore`를 결정적으로 만든다(별도 난수 스트림). 오케스트레이터가 동의 범위 `marketplace`로 받아 `agents/marketplace.py`가 `mkt_*` 특성을 계산한다. 정책 엔진은 v4 YAML의 `substitutions`·`limit_boost`를 해석해 대체 추적과 `cash_only` 판정을 함께 낸다. 판정은 결정적 엔진만 하고, LLM은 인용만 한다(ADR-003).

**Tech Stack:** Python 3.12, Pydantic v2, FastAPI(Mock API·정책 서비스), Durable Functions 활동(오케스트레이터), Jinja2 콘솔, pytest.

**Spec:** `docs/superpowers/specs/2026-11-17-marketplace-seller-underwriting-design.md` (승인됨, 커밋 `7b8a47a`)

## Global Constraints

- 정책 버전 문자열은 `"2026.11-v4"`, 파일은 `src/policy/rulesets/v4.yaml`이다. `DEFAULT_RULES_PATH`는 v4를 가리킨다. v1~v3 파일은 그대로 두고 계속 로드돼야 한다.
- 판정·대체 여부는 결정적 엔진만 정한다. LLM 지시문은 판정을 바꾸지 못한다(ADR-003).
- 셀러 자료에 동의하지 않았거나 자료가 없으면 불이익이 없어야 한다. 값이 `None`이면 규칙은 `not_applied`다.
- 탈락(knockout) 대체는 반드시 `outcome: refer`로 끝난다. R002·R003·R900·R901은 대체할 수 없다.
- 대체할 수 없는 탈락이 하나라도 걸리면 어떤 대체도 적용하지 않는다. 사유 코드는 원래대로 남는다.
- 한도: `before = min(requested, max(inflow_cap, pending × 0.8), product_max)`. 탈락을 대체했으면 5,000,000원으로 한 번 더 자른다. 그다음 10만 원 단위 절사, 그다음 R900 순서다.
- 특성에 `mkt_*` 값이 하나도 없으면 정책 응답 JSON에 `cash_only` 키 자체가 없다(옛 오케스트레이터 호환).
- `ReasonCode.code` 패턴은 `^[RS]\d{3}$`이다. 규칙 `Condition.code`는 `^R\d{3}$`, 대체 `code`는 `^S\d{3}$`이다.
- `CashFlowFeatures`는 strict를 유지한다. 새 필드는 모두 선택이고 기본값은 `None`이다.
- 합성은 결정적이다. 덮어쓰기 대상(petfood-07·ceramic-08)이 아닌 모든 데모·평가·부하 셀러의 계좌 거래와 정산 원장은 바이트 단위로 바뀌지 않는다. 사후 시나리오 셀러(dailylook-01·gearstore-06·linen-10·tangerine-15)는 건드리지 않는다.
- 제재(`penalties`)에는 자유 텍스트 사유 필드가 없다. 에이전트 문장에 리뷰 원문이나 제재 사유를 쓰지 않는다.
- 새 한국어 msgid는 모두 EN 로케일(`src/console/locales/en/*.json`) 항목을 단다.
- Python은 `.venv\Scripts\python.exe`를 쓴다. 테스트는 `-m pytest -q -p no:cacheprovider`로 돌린다.
- 커밋 메시지 끝에 다음 트레일러를 단다: `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`, `Copilot-Session: 05f1ca1b-6073-417d-bfb1-6b546f82cb58`.
- 다음은 스테이징하지 않는다: `memory261105.md`, `ops/reports/`, `docs/deck/capstone_render/`, `docs/deck/capstone.build.json`, `.superpowers/`.
- push·배포·`ops eval`은 하지 않는다(사용자 승인 사항).

## Review Focus

1. **동의는 있는데 플랫폼이 없거나 90일 매출이 0인 셀러.** 반품률·취소율은 `None`이고 R109는 `not_applied`여야 한다. 대체 조건 `mkt_return_rate_90d le 0.08`도 값이 없으므로 S001은 적용되지 않아야 한다. 0으로 나누면 안 된다. → T4 `test_zero_gross_rates_are_none`, T6 `test_substitution_needs_all_condition_values`.
2. **옛 문서(v1~v3, `cash_only`·`marketplace` 없음)를 새 콘솔로 열 때.** 정책 탭과 마켓 셀러 탭이 오류 없이 "자료 없음" 안내를 내야 한다. → T8 `test_detail_renders_v3_doc_without_marketplace`.
3. **옛 오케스트레이터가 v4 정책 서비스 응답을 받을 때(배포 순서상 생김).** `mkt_*` 없는 특성이면 응답에 `cash_only` 키가 없어야 하고, `extra="forbid"`인 옛 `PolicyDecision`으로도 파싱돼야 한다. → T6 `test_api_omits_cash_only_without_marketplace_features`.
4. **손상된 `marketplace.json`.** 심사는 계속되고, 플래그 `marketplace_unavailable`가 붙고, `mkt_*`는 모두 `None`이어야 한다. → T4 `test_underwrite_flags_corrupt_marketplace`.
5. **동의 철회·만료 삭제.** 동의별 컨테이너의 `marketplace.json`도 삭제·증명 대상이어야 한다(ADR-009). → T3 `test_deletion_includes_marketplace_json`.

---

## 파일 구조

| 파일 | 책임 | 작업 |
|---|---|---|
| `src/shared/contracts.py` | `ConsentScope.MARKETPLACE`, `StorePlatform`·`StorePenalty`·`MarketplaceStore`, `mkt_*` 특성, `CashOnlyDecision`, `ReasonCode` 패턴, `MKT_FEATURES` | T1 |
| `src/mockapis/sellerdata/engine.py` | `Ledger.pending`, `channel_factor` 훅 | T2 |
| `src/mockapis/sellerdata/marketplace.py` (신규) | `StoreOverride`, 셀러 성과 합성 | T2 |
| `src/mockapis/synth.py`, `src/mockapis/demo.py` | `SyntheticCase.marketplace`, 데모 덮어쓰기 | T2 |
| `src/mockapis/app.py`, `src/agents/data_assembly.py`, `src/orchestrator/journey/{adapters,models}.py`, `src/agents/notices.py` | 수집 경로·동의 범위 | T3 |
| `src/agents/marketplace.py` (신규), `src/agents/features.py`, `src/orchestrator/journey/activities.py` | 요약·특성·적재 | T4 |
| `src/policy/rules.py`, `src/policy/rulesets/v4.yaml` (신규) | 규칙 모델·v4 | T5 |
| `src/policy/engine.py`, `src/policy/app.py` | 대체·한도·`cash_only` | T6 |
| `src/agents/evidence.py`, `src/agents/underwriting.py` | 근거·grounding·지시문 | T7 |
| `src/console/{assessment,dashboard,views,officer_chat,statuses}.py`, `templates/detail.html`, `locales/en/*.json`, `src/consolepreview/fixtures.py` | 화면 | T8 |
| `src/evals/{datasets,suites/underwriting}.py`, `docs/adr/ADR-003-deterministic-policy.md`, `docs/agents.md`, `docs/limitations.md` | 평가·문서 | T9 |

---

### Task 1: 계약

**Files:**
- Modify: `src/shared/contracts.py` (`ConsentScope` 20–24행, `CashFlowFeatures` 131–156행, `ReasonCode`·`PolicyDecision` 165–190행)
- Test: `tests/shared/test_contracts.py`

**Interfaces:**
- Produces:
  - `ConsentScope.MARKETPLACE = "marketplace"`
  - `StorePlatform(Contract)`: `platform: str(min 1)`, `opened_on: date`, `rating: float(1.0~5.0)`, `review_count: int≥0`, `orders_90d: int≥0`, `gross_90d_krw: int≥0`, `cancel_90d_krw: int≥0`, `return_90d_krw: int≥0`, `seller_grade: str`, `pending_settlement_krw: int≥0`, `assigned_pending_krw: int≥0`. 검증: `cancel_90d_krw + return_90d_krw ≤ gross_90d_krw`, `assigned_pending_krw ≤ pending_settlement_krw`.
  - `StorePenalty(Contract)`: `platform: str`, `occurred_on: date`, `kind: Literal["suspension", "warning"]`. 다른 필드는 없다.
  - `MarketplaceStore(Contract)`: `schema_version: SchemaV1 = "1"`, `as_of: date`, `platforms: tuple[StorePlatform, ...] = ()`, `penalties: tuple[StorePenalty, ...] = ()`
  - `CashFlowFeatures`에 기본값 `None`인 필드: `mkt_tenure_months: int≥0`, `mkt_rating: float(1~5)`, `mkt_review_count: int≥0`, `mkt_return_rate_90d: float(0~1)`, `mkt_cancel_rate_90d: float(0~1)`, `mkt_days_since_suspension: int≥0`, `mkt_pending_settlement_krw: int≥0`
  - `MKT_FEATURES: tuple[str, ...]`: 위 7개 이름을 위 순서대로 담는다.
  - `CashOnlyDecision(Contract)`: `decision: Decision`, `max_amount_krw: int≥0`, `reason_codes: tuple[ReasonCode, ...]`
  - `PolicyDecision.cash_only: CashOnlyDecision | None = None`
  - `ReasonCode.code` 패턴 `^[RS]\d{3}$`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_marketplace_store_rejects_free_text_penalty_reason():
    with pytest.raises(ValidationError):
        StorePenalty(platform="쿠팡", occurred_on=date(2026, 10, 1), kind="suspension", reason="x")

def test_store_platform_assigned_le_pending():
    with pytest.raises(ValidationError):
        StorePlatform(**PLATFORM | {"pending_settlement_krw": 100, "assigned_pending_krw": 101})

def test_features_mkt_fields_default_none_and_strict():
    f = CashFlowFeatures(**BASE)
    assert all(getattr(f, k) is None for k in MKT_FEATURES) and len(MKT_FEATURES) == 7
    with pytest.raises(ValidationError):
        CashFlowFeatures(**BASE, mkt_rating="4.5")

def test_reason_code_accepts_substitution_code():
    assert ReasonCode(code="S001", message="m").code == "S001"
    with pytest.raises(ValidationError):
        ReasonCode(code="X001", message="m")

def test_policy_decision_cash_only_optional():
    d = PolicyDecision(**ELIGIBLE)
    assert d.cash_only is None
    d2 = PolicyDecision(**ELIGIBLE, cash_only={"decision": "ineligible", "max_amount_krw": 0,
                                               "reason_codes": [{"code": "R001", "message": "m"}]})
    assert d2.cash_only.decision is Decision.INELIGIBLE
```

`PLATFORM`·`BASE`·`ELIGIBLE`은 파일 안 기존 픽스처 양식을 따라 만든다.

- [ ] **Step 2: 실패 확인**

Run: `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/shared/test_contracts.py`
Expected: ImportError 또는 새 테스트 FAIL

- [ ] **Step 3: 위 Interfaces대로 구현**

- [ ] **Step 4: 통과 확인**

Run: `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/shared tests/mockapis/test_consent.py tests/policy`
Expected: 모두 PASS

- [ ] **Step 5: 커밋** `feat(contracts): 마켓 셀러 성과 계약과 mkt 특성 필드`

---

### Task 2: 셀러 성과 합성

**Files:**
- Modify: `src/mockapis/sellerdata/engine.py` (`Ledger` 34–38행, `daily_sales` 41–50행, `simulate` 55–115행)
- Create: `src/mockapis/sellerdata/marketplace.py`
- Modify: `src/mockapis/synth.py` (`SyntheticCase` 58행, `generate_case` 99–186행), `src/mockapis/demo.py` (`build_demo_cases` 42행)
- Test: `tests/mockapis/test_marketplace_synth.py` (신규), `tests/mockapis/test_decision_invariance.py`

**Interfaces:**
- Consumes: T1의 `MarketplaceStore`·`StorePlatform`·`StorePenalty`
- Produces:
  - `Ledger.pending: tuple[Payout, ...] = ()`: `payouts()`가 만든 지급 중 `paid_on >= end`인 것(계좌 반영 전, `net_krw = gross − fee`).
  - `daily_sales(..., channel_factor: Callable[[str, date], float] = _one_channel)`, `simulate(..., channel_factor=_one_channel)`. 채널별 금액에 곱만 한다. 난수 호출 순서는 그대로다.
  - `@dataclass(frozen=True) StoreOverride`: `rating: float | None = None`, `suspended_days_ago: int | None = None`, `suspension_days: int = 7`, `return_rate_90d: float | None = None`
  - `MARKETPLACE_SEED_OFFSET = 7000`
  - `marketplace_hooks(override: StoreOverride | None, persona: Persona, as_of: date) -> dict[str, Callable]`: `simulate`에 넘길 `return_rate`·`channel_factor` 키워드 인자. 덮어쓰기가 없으면 빈 dict.
  - `synthesize_store(persona, archetype, as_of, business_age_months, ledger: Ledger, assigned_from: date | None, rng: random.Random, override: StoreOverride | None = None) -> MarketplaceStore`
  - `SyntheticCase.marketplace: MarketplaceStore | None = None` (마지막 필드)
  - `generate_case(..., store_override: StoreOverride | None = None)`
  - `demo.DEMO_STORE_OVERRIDES: dict[str, StoreOverride]`

합성 규칙(스펙 4.1·8.1):
- 플랫폼은 페르소나 채널 중 `CHANNELS[c].code == "PG_SETTLE"`인 것만이다. 주력 플랫폼은 가중치가 가장 큰 PG 채널이다.
- 아키타입 기본값은 스펙 4.1 표를 그대로 쓴다.
  - 입점 기간(개월):
    - HEALTHY_SEASONAL·TAX_MISMATCH: `max(업력, 24)`
    - STRESSED·ASSIGNED: 업력
    - THIN_FILE: 24
  - 주력 플랫폼이 이 기간에 입점한다. 나머지 플랫폼은 그 뒤 `rng`로 정한 달에 입점한다.
- 평점은 `rng.uniform(범위)`를 소수 1자리로 반올림한다. `override.rating`이 있으면 모든 플랫폼 평점을 그 값으로 쓴다.
- 리뷰 수는 `rng.randint(범위)`다. 취소액은 `round(gross_90d × rng.uniform(취소율 범위))`다.
- 90일 수치(`orders_90d`·`gross_90d_krw`·`return_90d_krw`)는 `ledger.orders` 중 `as_of − 90일 ≤ day < as_of`인 것을 채널별로 더한다. `orders_90d`는 주문액이 0보다 큰 날의 수다.
- `pending_settlement_krw`는 그 채널 `ledger.pending`의 `net_krw` 합이다.
- `assigned_pending_krw`는 `assigned_from`이 있을 때만 채운다. 양도 플랫폼(`ledger.pg_settlements` 중 `settled_on ≥ assigned_from`인 것의 gross가 가장 큰 플랫폼, `receipts._top_platform`과 같은 규칙)의 pending 전액이다.
- 제재:
  - STRESSED: 주력 플랫폼에 `warning` 1건, `occurred_on = as_of − rng.randint(20, 80)일`.
  - `override.suspended_days_ago`가 있으면 주력 플랫폼에 `suspension` 1건, `occurred_on = as_of − suspended_days_ago`.
- `marketplace_hooks`:
  - `suspended_days_ago`가 있으면 주력 플랫폼 주문액이 `[as_of − suspended_days_ago, + suspension_days)` 동안 0인 `channel_factor`를 만든다.
  - `return_rate_90d`가 있으면 `as_of − 90일` 이후 반품률이 그 값인 `return_rate`를 만든다.
- `generate_case`는 `simulate` 뒤에 `random.Random(seed + MARKETPLACE_SEED_OFFSET)`로 `synthesize_store`를 부른다. 기존 `rng` 호출 순서를 바꾸지 않는다.
- `DEMO_STORE_OVERRIDES`:
  - `seller-streetfit-19`: `StoreOverride(rating=3.8)`
  - `seller-petfood-07`: `StoreOverride(suspended_days_ago=40)`
  - `seller-ceramic-08`: `StoreOverride(return_rate_90d=0.18)`

- [ ] **Step 1: 특성화(fingerprint) 테스트를 먼저 쓰고 현재 코드에서 통과시키기**

`tests/mockapis/test_marketplace_synth.py`에 `_fingerprint(case) -> str`를 둔다. `(transactions, pg_settlements)`를 `model_dump(mode="json")`한 뒤 `json.dumps(sort_keys=True)`로 직렬화하고 sha256을 낸다. 지금 코드로 다음 대상의 값을 계산해 `FINGERPRINTS` 상수에 고정한다.
- `build_demo_cases(date(2026, 11, 17))`의 21개 셀러
- `underwriting_cases()`
- `build_load_cases(date(2026, 11, 17))` 중 앞 4개

```python
@pytest.mark.parametrize("case_id", sorted(FINGERPRINTS))
def test_account_data_unchanged_except_overrides(case_id, all_cases):
    assert _fingerprint(all_cases[case_id]) == FINGERPRINTS[case_id]
```

Run: `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/mockapis/test_marketplace_synth.py`
Expected: PASS (구현 전 기준선)

- [ ] **Step 2: 합성 동작 테스트 작성**

```python
def test_store_is_deterministic(): ...  # generate_case 두 번 → marketplace 동일
def test_thin_file_tenure_24_months(demo):  # knitroom-04: 가장 이른 opened_on이 as_of 기준 24개월 전
def test_healthy_defaults(demo):  # dailylook-01: 모든 rating 4.5~4.9, penalties == ()
def test_stressed_has_one_warning(demo):  # glowlab-03: [p.kind for p in penalties] == ["warning"]
def test_gross_90d_matches_orders(demo):  # 각 플랫폼 gross_90d_krw == 해당 채널 90일 OrderDay gross 합
def test_assigned_pending(demo):  # mugshop-21: 0 < assigned_pending_krw ≤ pending_settlement_krw인 플랫폼이 정확히 1개
def test_streetfit_rating_override(demo):  # 모든 rating == 3.8
def test_petfood_suspension(demo):  # suspension 1건, occurred_on == as_of - 40일, 그 주 7일간 주력 플랫폼 주문 0
def test_ceramic_return_rate(demo):  # Σreturn_90d / Σgross_90d 가 0.15 초과 0.22 미만
```

`demo` 픽스처는 `build_demo_cases(date(2026, 11, 17))`다.

- [ ] **Step 3: 실패 확인** — 새 테스트 FAIL, fingerprint는 PASS

- [ ] **Step 4: 위 Interfaces대로 구현한다. 그다음 petfood-07·ceramic-08의 fingerprint만 갱신하고, 갱신 사유를 테스트 주석으로 남긴다.**

- [ ] **Step 5: 통과 확인과 기존 판정 불변 확인**

Run: `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/mockapis`
Expected: PASS. `test_decision_invariance.py`에서 petfood-07·ceramic-08 판정이 바뀌면 `DEMO_EXPECTED`를 실측값으로 고치고 주석 "2026-11-17 셀러 덮어쓰기(스펙 8.1)"을 단다. 그 밖의 셀러가 바뀌면 멈추고 보고한다.

- [ ] **Step 6: 커밋** `feat(mockapis): 마켓 셀러 성과 결정적 합성과 데모 덮어쓰기`

---

### Task 3: 수집 경로와 동의 범위

**Files:**
- Modify:
  - `src/mockapis/app.py`
  - `src/agents/data_assembly.py` (`SOURCES` 23행, `SOURCE_SCOPE` 24행, `_ADAPTERS` 33–37행, `_summarize` 176행)
  - `src/orchestrator/journey/adapters.py` (`SOURCE_PATHS` 36행)
  - `src/orchestrator/journey/models.py:22`
  - `src/agents/notices.py:25`
- Test:
  - `tests/mockapis/test_app.py`
  - `tests/agents/test_data_assembly.py`
  - `tests/orchestrator/test_consent_lifecycle.py`
  - `tests/agents/test_notices.py`

**Interfaces:**
- Consumes: T1 `ConsentScope.MARKETPLACE`, `MarketplaceStore`; T2 `SyntheticCase.marketplace`
- Produces:
  - `GET /marketplace/store`: `authorize(..., ConsentScope.MARKETPLACE, "/marketplace/store").marketplace`를 그대로 돌려준다. `None`이면 `MarketplaceStore(as_of=clock().date())`를 돌려준다.
  - `SOURCES = ("profile", "transactions", "tax", "pg", "marketplace")`, `SOURCE_SCOPE["marketplace"] = "marketplace"`. `REQUIRED`는 바꾸지 않는다.
  - `_ADAPTERS["marketplace"] = TypeAdapter(MarketplaceStore)`. `_summarize("marketplace", store)`는 `{"platforms": len, "penalties": len, "as_of": iso}`를 낸다.
  - `SOURCE_PATHS["marketplace"] = "/marketplace/store"`
  - `CONSENT_SCOPES = ("account", "tax", "pg", "marketplace")`
  - `SCOPE_LABELS["marketplace"] = "마켓 판매 성과"`
  - 원천 파일 이름은 기존 규칙대로 `marketplace.json`이다(`activities.py:235`).

- [ ] **Step 1: 실패하는 테스트 작성**
  - `test_marketplace_store_endpoint`: 범위 `marketplace`로 받은 동의 토큰이면 200이다. 응답이 `MarketplaceStore`로 파싱되고 `platforms`가 비어 있지 않다.
  - `test_marketplace_scope_enforced_403`: `("account",)` 토큰이면 403이고 `detail.code == "scope"`다.
  - `test_assembly_fetches_marketplace_when_consented` / `test_assembly_marketplace_not_missing_without_consent`: 동의가 없으면 `missing_sources`에 들어가지 않는다.
  - `test_deletion_includes_marketplace_json`: 545행 `AsyncDeleteContainer` 양식으로 `["marketplace.json", "pg.json"]`을 둔다. 삭제 증명에 `marketplace.json`이 들어간다.
  - `test_scope_label_marketplace`: `SCOPE_LABELS["marketplace"] == "마켓 판매 성과"`

- [ ] **Step 2: 실패 확인** (`tests/mockapis/test_app.py tests/agents/test_data_assembly.py tests/orchestrator/test_consent_lifecycle.py tests/agents/test_notices.py`)

- [ ] **Step 3: 구현.** 콘솔·신청 화면에 범위 목록이 하드코딩돼 있는지 `grep -n '"pg"' src/console src/orchestrator`로 찾는다. 있으면 `marketplace`를 더한다.

- [ ] **Step 4: 통과 확인** — 위 파일들과 `tests/orchestrator` 전체를 돌려 PASS

- [ ] **Step 5: 커밋** `feat: marketplace 동의 범위와 /marketplace/store 수집 경로`

---

### Task 4: 셀러 지표 계산과 적재

**Files:**
- Create: `src/agents/marketplace.py`
- Modify: `src/agents/features.py` (`Underwriting` 70–78행, `compute_underwriting` 217–262행), `src/orchestrator/journey/activities.py` (`_load_pg` 312행 옆, `underwrite` 347–375행)
- Test: `tests/agents/test_marketplace.py` (신규), `tests/agents/test_features.py`, `tests/orchestrator/test_activities.py`

**Interfaces:**
- Consumes: T1 `MarketplaceStore`, `MKT_FEATURES`
- Produces:
  - `FLAG_MARKETPLACE_UNAVAILABLE = "marketplace_unavailable"`
  - `store_summary(store: MarketplaceStore | None, as_of: date) -> dict`. 자료가 없거나 플랫폼이 0개면 `{"available": False}`를 낸다. 그 밖에는 다음 키를 낸다:
    - `available: True`, `as_of`
    - `platforms`: 행마다 `platform`, `opened_on`, `rating`, `review_count`, `orders_90d`, `gross_90d_krw`, `return_rate_90d`, `cancel_rate_90d`, `seller_grade`, `pending_settlement_krw`, `assigned_pending_krw`
    - `penalties`: 행마다 `platform`, `occurred_on`, `kind`
    - `MKT_FEATURES` 7개 키
  - `features_of(summary: Mapping) -> dict[str, int | float | None]`: 항상 `MKT_FEATURES` 7개 키를 낸다. 자료가 없으면 모두 `None`이다.
  - `compute_underwriting(..., marketplace: MarketplaceStore | None = None)`. 결과에 `Underwriting.marketplace: dict`(기본 `{"available": False}`)를 담는다. `features`에는 `features_of(...)`를 `model_copy(update=...)`로 넣는다. `_conservative`는 바꾸지 않는다.
  - `activities._load_marketplace(deps, consent_id) -> tuple[MarketplaceStore | None, list[str]]`. 파일이 없으면 `(None, [])`, 손상되면 `(None, [FLAG_MARKETPLACE_UNAVAILABLE])`이다.
  - `underwrite()`의 반환 dict에 `"marketplace": result.marketplace`를 넣고, 플래그도 합친다.

지표 계산(스펙 4.2):
- `mkt_tenure_months`: 가장 이른 `opened_on`부터 `as_of`까지 개월 수다. `(y2 − y1)·12 + (m2 − m1)`에서 `as_of.day < opened_on.day`면 1을 뺀다.
- `mkt_rating`: `Σ(rating·reviews) / Σreviews`를 소수 2자리로 반올림한다. 리뷰 합이 0이면 단순 평균을 쓴다.
- `mkt_return_rate_90d`·`mkt_cancel_rate_90d`: 각각 `Σreturn / Σgross`, `Σcancel / Σgross`를 소수 4자리로 반올림한다. `Σgross == 0`이면 `None`이다.
- `mkt_days_since_suspension`: `kind == "suspension"`이고 `occurred_on ≤ as_of`인 것 중 가장 최근 것까지의 일수다. 없으면 `None`이다. `warning`은 세지 않는다.
- `mkt_pending_settlement_krw`: `max(0, Σ(pending − assigned))`

- [ ] **Step 1: 실패하는 테스트 작성**
  - `test_weighted_rating`: 플랫폼 (4.0, 100), (5.0, 300)이면 `mkt_rating == 4.75`
  - `test_zero_gross_rates_are_none`
  - `test_tenure_floors_partial_month`: `opened_on=2024-11-18`, `as_of=2026-11-17`이면 23
  - `test_warning_not_suspension`
  - `test_pending_net_of_assigned_floor_zero`
  - `test_unavailable_all_none`
  - `test_compute_underwriting_sets_mkt_features` / `test_compute_underwriting_without_marketplace_keeps_none`
  - `test_underwrite_flags_corrupt_marketplace`: 깨진 JSON이면 플래그가 붙고, `features`의 `mkt_*`가 모두 `None`이고, `marketplace.available is False`다.
  - `test_underwrite_missing_marketplace_no_flag`

- [ ] **Step 2: 실패 확인** (`tests/agents/test_marketplace.py tests/agents/test_features.py tests/orchestrator/test_activities.py`)

- [ ] **Step 3: 구현.** 모양은 `agents/pg_ledger.py`의 `ledger_summary`·`features_of`를 따른다.

- [ ] **Step 4: 통과 확인** — 같은 명령, PASS

- [ ] **Step 5: 커밋** `feat(agents): 마켓 셀러 지표 계산과 심사 적재`

---

### Task 5: 규칙 모델과 v4 규칙셋

**Files:**
- Modify: `src/policy/rules.py`
- Create: `src/policy/rulesets/v4.yaml`
- Test: `tests/policy/test_rules.py`, `tests/policy/conftest.py`, `tests/policy/test_engine.py`, `tests/policy/test_app.py`

**Interfaces:**
- Produces:
  - `FeatureName`에 `MKT_FEATURES` 7개를 더한다.
  - `SubCondition(_Model)`: `field: FeatureName`, `op: Op`, `threshold: float`
  - `Substitution(_Model)`: `code: str(^S\d{3}$)`, `message: str(min 1)`, `replaces: str(^R\d{3}$)`, `outcome: Literal["refer", "pass"]`, `when: tuple[SubCondition, ...](min 1)`
  - `LimitBoost(_Model)`: `pending_settlement_ratio: float(0 < x ≤ 1)`, `substituted_max_amount_krw: int(> 0)`
  - `RuleSet.substitutions: tuple[Substitution, ...] = ()`, `RuleSet.limit_boost: LimitBoost | None = None`
  - `NON_SUBSTITUTABLE = frozenset({"R002", "R003"}) | RESERVED_CODES`
  - `RuleSet._consistency`가 추가로 거부하는 경우:
    - S 코드 중복
    - 한 규칙을 두 대체가 겨냥함
    - `replaces`가 이 규칙셋의 knockout·refer 코드가 아님
    - `replaces`가 `NON_SUBSTITUTABLE`에 속함
    - knockout을 겨냥한 대체의 `outcome != "refer"`
  - `DEFAULT_RULES_PATH = .../rulesets/v4.yaml`
  - `v4.yaml`: v3 내용 전체에 스펙 4.3 YAML 블록(R003, R109, R110, S001, S002, `limit_boost`)을 더하고 `version: "2026.11-v4"`로 한다. 규칙 순서는 knockout R001·R002·R003, refer는 v3 순서 뒤에 R109·R110을 둔다.

- [ ] **Step 1: 기존 v3 고정 테스트를 v3 파일로 묶는다.** `tests/policy/conftest.py`에 `rules_v3` 픽스처(`load_rules(RULESETS / "v3.yaml")`)를 추가한다. `test_engine.py`의 `test_v3_*`와 `rules_version == "2026.11-v3"` 단언은 `rules_v3`를 쓰게 한다. `test_rules.py:9`의 버전 목록에 `"2026.11-v4"`를 더한다. `test_engine.py:17`의 parametrize에 `"v4.yaml"`을 더한다.

- [ ] **Step 2: 실패하는 테스트 작성**

```python
def test_default_is_v4():
    r = load_rules()
    assert r.version == "2026.11-v4"
    assert [s.code for s in r.substitutions] == ["S001", "S002"]
    assert r.limit_boost.pending_settlement_ratio == 0.8
    assert r.limit_boost.substituted_max_amount_krw == 5_000_000

@pytest.mark.parametrize("target", ["R002", "R003", "R900", "R901"])
def test_rejects_non_substitutable(target): ...  # v4 dict의 S001.replaces를 target으로 → ValidationError

def test_rejects_knockout_substitution_with_pass(): ...  # S001.outcome = "pass" → ValidationError
def test_rejects_unknown_replaces(): ...                 # replaces "R999" → ValidationError
def test_v1_to_v3_load_without_substitutions(): ...      # substitutions == (), limit_boost is None
```

- [ ] **Step 3: 실패 확인** (`tests/policy`)

- [ ] **Step 4: 구현.** `tests/policy/test_app.py:19,27`의 버전 단언은 `"2026.11-v4"`로 고친다.

- [ ] **Step 5: 통과 확인** — `tests/policy`가 PASS여야 한다. `tests/console/test_assessment.py::test_signal_thresholds_and_limits_match_policy_rules`는 v4 규칙(R003·R109·R110)을 아직 모르므로 T8까지 FAIL이 정상이다. 이 테스트 하나만 예외로 두고 리뷰에서도 알린다.

- [ ] **Step 6: 커밋** `feat(policy): 대체·한도 보강 규칙 모델과 v4 규칙셋`

---

### Task 6: 엔진 — 대체, 한도, cash_only

**Files:**
- Modify: `src/policy/engine.py`, `src/policy/app.py`
- Test: `tests/policy/test_engine.py`, `tests/policy/test_app.py`, `tests/mockapis/test_policy_v4_sellers.py` (신규)

**Interfaces:**
- Consumes: T1 `CashOnlyDecision`, `MKT_FEATURES`; T5 `Substitution`, `LimitBoost`, `NON_SUBSTITUTABLE`
- Produces:
  - `RuleCheck.kind`에 `"substitution"`, `RuleCheck.result`에 `"substituted"`를 더한다.
  - `@dataclass(frozen=True) SubstitutionTrace`: `code`, `message`, `replaces`, `outcome`, `target_hit: bool`, `conditions: tuple[RuleCheck, ...]`, `conditions_met: bool`, `blocked_by: tuple[str, ...]`, `applied: bool`.
    - `conditions`의 각 항목은 `kind="substitution"`이다. `result`는 `_check`와 같은 뜻이다: `hit` = 조건 참, `pass` = 거짓, `not_applied` = 값 없음.
  - `LimitTrace`에 다음 필드를 더한다(기본값 있음):
    - `pending_settlement_ratio: float | None = None`
    - `settlement_cap_krw: int = 0`
    - `substituted_cap_krw: int | None = None`
  - `Explanation.substitutions: tuple[SubstitutionTrace, ...] = ()`
  - `decision_payload(decision: PolicyDecision) -> dict`: `model_dump(mode="json")`에서 `cash_only`가 `None`이면 그 키를 지운다. 다른 `None`(`rate_band` 등)은 지우지 않는다.
  - 정책 API `POST /v1/decisions`는 `decision_payload(evaluate(...))`를 돌려준다.
  - 대체 사유 코드: S001이 적용되면 `ReasonCode(code="S001", message=s.message, field=첫 조건 field, observed=그 관측값, threshold=그 기준값)`이다. 그래서 근거 `P:S001:observed`·`P:S001:threshold`가 생긴다. `outcome: pass`(S002)는 사유를 남기지 않으므로 `P:S002` 근거는 생기지 않는다(스펙 4.4의 `P:S002`는 이 이유로 제외하며, ADR에 적는다).

`explain()` 알고리즘(스펙 4.3·8.3):

```text
1. knockouts, refers = 기존 _check
2. r901 = 금리 구간 검사(기존과 같음, 한도와 무관)
3. blocking = [c.code for c in knockouts + [r901] if c.result == "hit" and c.code in NON_SUBSTITUTABLE]
4. 각 substitution s:
     target = replaces 규칙의 check
     conds = [_check(cond, "substitution")]
     met = 모든 conds.result == "hit"
     tentative = target.result == "hit" and met and not blocking
5. 한도: inflow_cap, settlement_cap = int(pending × ratio)(값 없거나 boost 없으면 0)
     before = min(requested, max(inflow_cap, settlement_cap), product_max)
     tentative 중 replaces가 knockout인 것이 있으면 before = min(before, substituted_max)
     limit = 10만 원 절사
   r900 = 기존과 같음
6. r900이 hit이면 blocking에 "R900"을 넣고 tentative를 모두 취소한다(한도 trace는 그대로 둔다. 어차피 ineligible).
7. applied 대상 check의 result를 "substituted"로 바꾼다. 사유에서 빼고, outcome refer면 S 사유를 refer 사유에 더한다.
8. 판정은 기존 규칙으로 정한다. 끝으로 features에 MKT_FEATURES 값이 하나라도 있으면
   cash = explain(features.model_copy(update={k: None for k in MKT_FEATURES}), rules).decision
   decision.cash_only = CashOnlyDecision(cash.decision, cash.max_amount_krw, cash.reason_codes)
```

- [ ] **Step 1: 실패하는 엔진 단위 테스트 작성** (`rules`는 v4, `make_features`에 mkt 값을 더한다)

```python
SELLER = dict(mkt_tenure_months=24, mkt_rating=4.7, mkt_review_count=500, mkt_return_rate_90d=0.03,
              mkt_cancel_rate_90d=0.02, mkt_pending_settlement_krw=2_000_000)

def test_s001_turns_r001_knockout_into_refer(make_features, rules):
    e = explain(make_features(months_of_history=4, business_age_months=8, requested_amount_krw=8_000_000,
                              avg_monthly_inflow_krw=6_000_000, **SELLER), rules)
    d = e.decision
    assert d.decision is Decision.REFER and {r.code for r in d.reason_codes} == {"S001"}
    assert next(c for c in e.checks if c.code == "R001").result == "substituted"
    assert next(c for c in e.checks if c.code == "R104").result == "substituted"
    assert e.limit.substituted_cap_krw == 5_000_000 and d.max_amount_krw == 5_000_000
    assert d.cash_only.decision is Decision.INELIGIBLE
    assert {r.code for r in d.cash_only.reason_codes} == {"R001", "R104"}

def test_r002_blocks_substitution(make_features, rules):
    e = explain(make_features(months_of_history=4, dscr=0.9, **SELLER), rules)
    s = next(t for t in e.substitutions if t.code == "S001")
    assert s.conditions_met and not s.applied and s.blocked_by == ("R002", "R901")
    assert {"R001", "R002"} <= {r.code for r in e.decision.reason_codes}

def test_substitution_needs_all_condition_values(make_features, rules):
    d = evaluate(make_features(months_of_history=4, **(SELLER | {"mkt_return_rate_90d": None})), rules)
    assert d.decision is Decision.INELIGIBLE and "R001" in {r.code for r in d.reason_codes}

def test_s002_waives_r104_without_reason(make_features, rules):
    d = evaluate(make_features(business_age_months=8, **SELLER), rules)
    assert d.decision is Decision.ELIGIBLE and d.reason_codes == ()

def test_r003_recent_suspension_knockout(make_features, rules):
    d = evaluate(make_features(**SELLER, mkt_days_since_suspension=40), rules)
    assert d.decision is Decision.INELIGIBLE and [r.code for r in d.reason_codes] == ["R003"]
    assert d.cash_only.decision is Decision.ELIGIBLE

@pytest.mark.parametrize("field,value,code", [("mkt_return_rate_90d", 0.18, "R109"), ("mkt_rating", 3.8, "R110")])
def test_v4_seller_refers(make_features, rules, field, value, code): ...

def test_settlement_cap_raises_limit(make_features, rules):
    e = explain(make_features(avg_monthly_inflow_krw=2_000_000, requested_amount_krw=5_000_000,
                              mkt_pending_settlement_krw=5_000_000), rules)
    assert e.limit.inflow_cap_krw == 3_000_000 and e.limit.settlement_cap_krw == 4_000_000
    assert e.decision.max_amount_krw == 4_000_000

def test_no_mkt_values_no_cash_only(make_features, rules):
    assert evaluate(make_features(), rules).cash_only is None
```

`test_policy/test_app.py`:
- `test_api_omits_cash_only_without_marketplace_features`: 응답 JSON에 `"cash_only"` 키가 없다. 또 T1 이전 `PolicyDecision` 정의를 흉내 낸 `extra="forbid"` 모델(이 테스트 안에서 `cash_only` 없이 정의)로 `model_validate`가 성공한다.
- `test_api_includes_cash_only_with_marketplace_features`

- [ ] **Step 2: 데모 셀러 v4 결과 테스트 작성** — `tests/mockapis/test_policy_v4_sellers.py`

기준일은 `date(2026, 11, 17)`, 신청은 5,000,000원·6개월이다. `compute_underwriting(..., pg_settlements=case.pg_settlements, marketplace=case.marketplace)`로 특성을 만들고 `explain(features, load_rules())`를 부른다.

```python
V4_EXPECTED = {  # (decision, reason code 집합, cash_only decision)
    "seller-knitroom-04": ("refer", {"S001", "R107"}, "ineligible"),
    "seller-leather-14": ("refer", {"S001", "R107"}, "ineligible"),
    "seller-petfood-07": ("ineligible", {"R003"}, "eligible"),
    "seller-ceramic-08": ("refer", {"R107", "R109"}, "refer"),
}

def test_v4_demo_outcomes(...):  # 위 표와 같고 knitroom·leather max_amount_krw ≤ 5_000_000
def test_tint_streetfit_stay_ineligible(...):
    # tint-17: ineligible, {"R001", "R002"} ⊆ codes, S001 conditions_met and blocked_by에 "R002"
    # streetfit-19: ineligible, {"R001", "R002", "R110"} ⊆ codes
def test_other_sellers_match_cash_only(...):
    # 위 6곳을 뺀 15곳: decision == cash_only.decision이고 reason code 집합이 같다
```

- [ ] **Step 3: 실패 확인** (`tests/policy tests/mockapis/test_policy_v4_sellers.py`)

- [ ] **Step 4: 위 알고리즘대로 구현**

- [ ] **Step 5: 통과 확인.** 같은 명령에 `tests/mockapis/test_decision_invariance.py`를 더한다(mkt 없음 → v3와 같은 판정). PASS여야 한다. 데모 결과가 표와 다르면 합성 기본값(T2)을 고치지 말고 멈춰서 실측을 보고한다.

- [ ] **Step 6: 커밋** `feat(policy): v4 대체·정산 예정금 한도·현금흐름만 판정`

---

### Task 7: 에이전트 근거와 grounding

**Files:**
- Modify: `src/agents/underwriting.py` (`SETTLEMENT_EVIDENCE_PREFIXES` 42행, `ANALYST_INSTRUCTIONS` 50–83행, `UnderwritingContext` 330–345행, `check_grounding` officer_checks 부분 459–478행), `src/orchestrator/journey/activities.py` (`assess_credit`)
- Test: `tests/agents/test_underwriting.py`, `tests/agents/test_evidence.py`

**Interfaces:**
- Consumes: T4 `underwrite()` 반환의 `"marketplace"`, T6 S 사유 코드
- Produces:
  - `UnderwritingContext.marketplace: Mapping[str, Any] | None = None`. `assess_credit`은 `marketplace=uw.get("marketplace")`를 넘긴다.
  - `OFFICER_CHECK_PREFIXES = ("M:pg_", "P:R107:", "P:R108:", "M:mkt_", "P:R003:", "P:R109:", "P:R110:", "P:S001:", "P:S002:")`. 기존 이름 `SETTLEMENT_EVIDENCE_PREFIXES`를 대체하며, 쓰는 곳은 모두 바꾼다.
  - grounding 규칙 두 가지를 바꾼다:
    - 확인 사항 근거가 위 접두어 중 하나도 없으면 오류다. 메시지: `정산·셀러 근거(M:pg_*·M:mkt_*·P:R107·P:R108·P:R003·P:R109·P:R110·P:S001)가 없다`
    - "자료 없는데 확인 사항" 오류는 정산과 셀러 자료가 **둘 다** `available`이 아닐 때만 낸다.
  - `build_catalog`은 바꾸지 않는다(`M:mkt_*`는 features에서, `P:S001:*`는 사유 코드에서 자동으로 들어온다).
  - `ANALYST_INSTRUCTIONS` 변경:
    - 1번: 셀러 지표 `M:mkt_*`(입점 개월·평점·리뷰 수·반품률·취소율·판매정지 경과일·정산 예정금)를 `get_metrics`에서 확인하라는 문장을 넣는다. 리뷰 원문·제재 사유는 문장에 쓰지 않는다는 문장도 넣는다.
    - 9번 (3): "정산·셀러 건전성"으로 바꾸고 셀러 지표를 포함한다.
    - 10번: 확인 사항 근거에 `M:mkt_*`·`P:R003`·`P:R109`·`P:R110`·`P:S001`을 허용한다. "정책 사유에 S001이 있으면 확인 사항 하나 이상에 P:S001 근거를 단다"는 지시만 넣고, 검증하지 않는다(스펙 8.2).

- [ ] **Step 1: 실패하는 테스트 작성**
  - `test_officer_check_with_only_seller_evidence_ok_without_pg`: 정산 `available=False`, marketplace `available=True`, 근거 `["M:mkt_rating"]`이면 오류가 없다.
  - `test_officer_check_without_any_data_still_rejected`: 둘 다 없으면 기존 오류를 유지한다.
  - `test_catalog_includes_mkt_and_s001`: features에 `mkt_rating=4.7`, 정책 사유에 S001(observed 24, threshold 18)이 있으면 카탈로그에 `M:mkt_rating`, `P:S001:observed`, `P:S001:threshold`가 있다.
  - `test_missing_s001_check_is_not_grounding_error`: S001 사유가 있는데 확인 사항에 `P:S001`이 없어도 grounding 오류가 없다.

- [ ] **Step 2: 실패 확인** (`tests/agents/test_underwriting.py tests/agents/test_evidence.py`)

- [ ] **Step 3: 구현**

- [ ] **Step 4: 통과 확인** — 위 두 파일과 `tests/orchestrator/test_activities.py`, PASS

- [ ] **Step 5: 커밋** `feat(agents): 셀러 지표 근거 인용과 확인 사항 grounding 확장`

---

### Task 8: 콘솔

**Files:**
- Modify:
  - `src/console/assessment.py` (`SIGNAL_RULES`·`SIGNALS`·`OPTIONAL_SIGNALS` 21–55행, `_limit` 160–175행)
  - `src/console/dashboard.py` (`settlement_card` 옆, 378행 ui dict)
  - `src/console/officer_chat.py` (`_rules`·`_trace`·`_policy` 77–130행)
  - `src/console/views.py`
  - `src/console/statuses.py:29`
  - `src/console/templates/detail.html` (탭 41행, 한도 카드 79–85행, 정책 탭 194–215행)
  - `src/console/locales/en/*.json`
  - `src/consolepreview/fixtures.py:54`
- Test: `tests/console/test_assessment.py`, `test_views.py`, `test_officer_chat.py`, `test_ui.py`, `test_i18n.py`, `test_pretranslate_coverage.py`, `test_preview.py`, `test_dockerfile.py`

**Interfaces:**
- Consumes: T4 `uw["marketplace"]` 요약; T6 `Explanation.substitutions`·`LimitTrace` 새 필드·`decision_payload`; 정책 dict의 `cash_only`
- Produces:
  - `SIGNAL_RULES`에 다음을 더하고, 셋 모두 `OPTIONAL_SIGNALS`에 넣는다:
    - `"mkt_days_since_suspension": ("knockout", 90)`
    - `"mkt_return_rate_90d": ("refer", 0.15)`
    - `"mkt_rating": ("refer", 4.0)`
  - `SIGNALS`에 세 행을 더한다. 방향은 lt→`min`, gt→`max`다. 강점 기준은 판매정지 경과 365일, 반품률 0.05, 평점 4.5로 한다. 설명 문구는 기존 행 말투를 따른다.
  - 상수:
    - `PENDING_SETTLEMENT_RATIO = 0.8`
    - `SUBSTITUTED_MAX_KRW = 5_000_000`
    - `KNOCKOUT_SUBSTITUTIONS = frozenset({"S001"})`
    - `BOOST_VERSIONS = frozenset({"2026.11-v4"})`
  - `_limit`: `policy.rules_version ∈ BOOST_VERSIONS`이고 `mkt_pending_settlement_krw`가 수치일 때 두 번째 후보를 바꾼다. 값은 `max(입금×1.5, 정산 예정금×0.8)`이고, 라벨은 더 큰 쪽 이름(`월평균 입금×1.5` 또는 `정산 예정금×0.8`)이다. 사유 코드에 `KNOCKOUT_SUBSTITUTIONS`가 있으면 후보 `("대체 상한", 5_000_000)`을 더한다. 카드 부제 "세 값 중"은 "후보 중"으로 바꾼다.
  - `dashboard.marketplace_card(summary: Any) -> dict`: 옛 문서·자료 없음이면 `{"available": False}`다. 있으면 다음 키를 낸다:
    - `available`
    - `platforms`: 행마다 표시용 값. 비율은 %로 쓴다.
    - `penalties`: `kind` 라벨은 `{"suspension": "판매정지", "warning": "경고"}`로 바꾼다.
    - `features`: `mkt_*` 값
  - ui dict 키 `"marketplace": marketplace_card(uw.get("marketplace"))`
  - `officer_chat.policy_trace(policy, features, received_at=None) -> dict | None`: 공개 함수이고 `_policy`를 그대로 감싼다.
    - `_rules`가 `substitutions`·`limit_boost`를 싣는다.
    - `_trace`가 `"substitutions": [asdict(s) for s in e.substitutions]`를 싣는다.
  - `views.detail_view`가 ui에 넣는 값:
    - `policy_compare`: `cash_only`가 있을 때만 `{"cash": {decision, max}, "seller": {decision, max}}`
    - `substitutions`: trace의 대체 중 `target_hit`인 것만. 행마다 `code`, `replaces`, `state`, `blocked_by`를 둔다. `state`는 `applied`, `blocked`(`conditions_met`이고 막힘), `unmet` 중 하나다.
  - 탭: `("marketplace", _("마켓 셀러"))`를 `cashflow` 바로 뒤에 둔다.
  - 문구(한국어 msgid. EN 항목 필수):
    - `현금흐름만 볼 때`, `셀러 데이터 반영`
    - `%(rule)s 대체됨(%(code)s)`, `조건 충족, %(rule)s로 미적용`, `조건 미충족`
    - `정산 예정금×%(r)s`, `대체 상한`, `후보 중 가장 작은 값을 10만 원 단위로 내림`
    - `마켓 셀러`, `마켓 판매 성과 자료 없음 — 동의하지 않았거나 2026-11-17 이전 심사분입니다.`
    - `판매정지`, `경고`, 표 머리말(`플랫폼`, `입점일`, `평점`, `리뷰 수`, `90일 주문일`, `반품률`, `취소율`, `등급`, `정산 예정금`, `선정산 양도`)
  - `FLAG_LABELS["marketplace_unavailable"] = "마켓 판매 성과 손상(셀러 지표 제외)"`
  - `consolepreview/fixtures.py`: `compute_underwriting(..., marketplace=case.marketplace)`를 부르고, 문서 `underwriting["marketplace"] = uw.marketplace`, `policy = decision_payload(evaluate(...))`로 저장한다.

- [ ] **Step 1: 실패하는 테스트 작성**
  - `test_signal_thresholds_and_limits_match_policy_rules`(기존): v4 기준으로 그대로 통과해야 한다. 같은 테스트에 단언을 더한다: `PENDING_SETTLEMENT_RATIO == rules.limit_boost.pending_settlement_ratio`, `SUBSTITUTED_MAX_KRW == rules.limit_boost.substituted_max_amount_krw`, `KNOCKOUT_SUBSTITUTIONS == {s.code for s in rules.substitutions if s.replaces in {c.code for c in rules.knockouts}}`.
  - `test_limit_uses_settlement_cap_and_substitution_cap`: v4 정책, 사유 S001, 입금 2,000,000, 정산 예정금 5,000,000, 신청 8,000,000이면 binding이 `대체 상한`이고 computed가 5,000,000이다.
  - `test_detail_renders_v3_doc_without_marketplace`: v3 문서(`marketplace`·`cash_only` 없음)로 상세 페이지가 200이고, "마켓 판매 성과 자료 없음"이 보이고, "현금흐름만 볼 때"는 보이지 않는다.
  - `test_detail_shows_substitution_and_compare`: knitroom 양식 v4 문서면 `R001 대체됨(S001)`, `현금흐름만 볼 때`가 보이고, 마켓 셀러 탭 표에 플랫폼 행이 있다.
  - `test_detail_shows_blocked_substitution`: tint 양식 문서면 `조건 충족, R002로 미적용`이 보인다.
  - `test_snapshot_includes_substitutions` (`test_officer_chat.py`): v4 문서의 `rules`에 `substitutions`·`limit_boost`가 있고, `trace.substitutions`가 비어 있지 않다.
  - `test_dockerfile.py:72`에 `v4.yaml` 존재 단언을 더한다.

- [ ] **Step 2: 실패 확인** (`tests/console`)

- [ ] **Step 3: 구현.** 템플릿 구조:
  - 정책 탭 카드 맨 위 판정 줄 다음에 비교 줄을 둔다.
  - 사유 표 아래에 "셀러 데이터 대체" 소표(코드·대상 규칙·상태)를 둔다.
  - 마켓 셀러 탭은 카드 하나(표 + 제재 목록 + 정산 예정금 합)다.

- [ ] **Step 4: 통과 확인** — `tests/console tests/consolepreview`가 있으면 함께 돌린다. PASS여야 한다. 미리보기로 화면을 확인한다(`python -m consolepreview` 등 저장소의 기존 미리보기 명령). knitroom-04·tint-17·petfood-07 상세의 정책 탭과 마켓 셀러 탭이 깨지지 않는지 본다.

- [ ] **Step 5: 커밋** `feat(console): 마켓 셀러 탭과 정책 대체·현금흐름만 비교 표시`

---

### Task 9: 평가 suite, 문서, 전체 회귀

**Files:**
- Modify: `src/evals/datasets.py` (`underwriting_cases` 184행), `src/evals/suites/underwriting.py` (`build_context` 39–58행, `run_case` row 118–124행)
- Modify: `docs/adr/ADR-003-deterministic-policy.md`, `docs/agents.md`, `docs/limitations.md`
- Test: `tests/evals/test_eval_run.py`

**Interfaces:**
- Consumes: T2 `StoreOverride`, T4 `marketplace` 인자, T7 `UnderwritingContext.marketplace`
- Produces:
  - `build_context`가 `compute_underwriting(..., marketplace=case.marketplace)`를 부르고 `UnderwritingContext(..., marketplace=uw.marketplace)`를 만든다.
  - `underwriting_cases()` 끝에 판매정지 케이스를 더한다: `UwCase("ev-uhealsusp", generate_case("ev-uhealsusp", Archetype.HEALTHY_SEASONAL, AS_OF, UW_SEED, store_override=StoreOverride(suspended_days_ago=40)), False)`
  - `SELLER_EVIDENCE = ("M:mkt_", "P:S001", "P:R003", "P:R109", "P:R110")`. `run_case`는 THIN_FILE이거나 `case_id == "ev-uhealsusp"`인 케이스에 `row["seller_evidence_cited"] = any(_cites(d.get("claims", []), SELLER_EVIDENCE) for d in drafts)`를 넣는다.

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/evals/test_eval_run.py`)
  - `test_underwriting_cases_include_suspension_case`: `ev-uhealsusp`가 있고 offline 정책 판정이 `ineligible`, 사유에 R003이 있다.
  - `test_thin_file_eval_case_substituted`: `ev-uthinn` offline 판정 사유에 S001이 있고 `cash_only.decision == "ineligible"`이다. 판정이 다르면 멈추고 실측을 보고한다.
  - `test_seller_evidence_row`: 기존 fake runner 양식으로 THIN_FILE 케이스를 돌린다. `row["seller_evidence_cited"]`가 bool이다.

- [ ] **Step 2: 실패 확인 → 구현 → 통과 확인** (`tests/evals`)

- [ ] **Step 3: 문서**
  - ADR-003에 `## 2026-11-17 개정: 정책 v4` 절을 더한다. 담을 내용:
    - 대체 원칙: 탈락 대체는 refer까지, 막는 규칙, 한도 순서
    - 대체 불가 규칙과 이유
    - 임계값 근거(합성 분포 기반 데모 값)
    - T6 `V4_EXPECTED`와 tint·streetfit 실측 판정 변화 표
    - petfood·ceramic의 v3 판정 변화(있었다면)
    - `P:S002` 근거가 없는 이유
    - 배포 순서(정책 → mockapi → 오케스트레이터 → 콘솔)와 `cash_only` 키 생략 호환
  - `docs/agents.md`: "정산 원장 대사" 절 뒤에 "마켓 셀러 지표" 절을 둔다(지표 7개 정의, 동의 범위, 근거 ID, 확인 사항 근거 확장).
  - `docs/limitations.md`: 합성 데이터 기반 임계값, 평점·리뷰 조작 가능성, 신규 셀러·플랫폼 간 공정성 한계, 실제 마켓 API 미연동.
  - 표 셀 안의 `|`는 `\|`로 이스케이프한다.

- [ ] **Step 4: 전체 회귀**

Run: `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests` (약 7분)
Expected: 실패 0. 직전 기준선(5258 passed, 140 skipped)보다 통과 수가 늘어야 한다.

- [ ] **Step 5: 커밋** `feat(evals,docs): v4 셀러 심사 평가 케이스와 ADR-003 개정`
