# USD 정책 v2 — 대안 데이터 시연 장면 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 새 USD 신청을 `usd-v2` 규정으로 판정해, 은행 기록만 볼 때와 정산·마켓 데이터를 더했을 때 판정이 양방향으로 달라지는 장면(기회 O1~O6, 위험 R1~R3)을 시드 고정 셀러로 재현한다.

**Architecture:** 엔진 구조는 그대로 두고, 규정 파일(`usd-v2.yaml`)에 새 규칙·대체 규칙과 "은행만 판정에서 지울 특성 목록"을 싣는다. 정산 원장이 이미 계산하는 지표 3개를 USD 특성으로 옮기고, USD 셀러 합성기에 기본값이 지금 동작과 같은 레버를 더해 장면 셀러 8곳을 만든다. 마켓 프로필은 별도 모듈이 별도 난수로 파생하므로 은행·정산 장부는 바뀌지 않는다.

**Tech Stack:** Python 3.12, Pydantic v2, FastAPI, Jinja2, pytest. 배포는 Azure Functions(정책), Container Apps(오케스트레이터·모의 API·콘솔).

**Spec:** `docs\superpowers\specs\2026-11-25-usd-policy-alt-data-scenes-design.md`

## Global Constraints

- KRW 상품과 KRW 규정(`2026.11-v1`~`v4`)은 바꾸지 않는다.
- `usd-v1.yaml`과 `usd-v1`의 엔진 동작(은행만 판정 포함)은 바꾸지 않는다. 저장된 `usd-v1` 판정은 콘솔 재계산 추적과 심사 도우미에서 그대로 재현된다.
- 상품 버전 `product_version: usd-v1`은 그대로다. 규정 버전만 `usd-v2`가 된다.
- 부적격 규칙의 대체는 회부만 허용하고, 한도는 `substituted_max_amount_cents` 500000으로 묶는다. R002, R003, R900, R901은 대체할 수 없다.
- 한 규칙은 한 조건이다. 복합 조건 규칙이나 점수표는 만들지 않는다.
- 기존 21개 셀러의 은행 거래, 정산, 세무 신고는 한 바이트도 바뀌지 않는다(Task 1의 장부 지문).
- 장면 셀러의 결정 특성은 임계값에서 10% 이상 떨어뜨린다. 평점은 0.1 이상, 비율 1.0인 대사율은 예외다.
- 영문 규칙 메시지와 합성 데이터 문자열은 ASCII만 쓴다.
- 테스트 실행은 `.venv\Scripts\python.exe -m pytest ... -q`이다(`pyproject.toml`이 `src`와 `src/orchestrator`를 경로에 넣는다).
- 배포는 좁은 창 방식으로 한다. Function App·ACR 공개 창은 `finally`에서 닫고, `ops deploy`·`ops window`는 쓰지 않는다(WAF 허용 목록을 덮어씀).
- 운영 신청은 90초 이상 간격을 둔다(Foundry 429).

## Review Focus

1. **배포 순서 중 옛 소비자가 새 판정을 받는 경우.** 옛 오케스트레이터·콘솔의 `UsdPolicyDecision`은 `rules_version: Literal["usd-v1"]`이고 `extra="forbid"`라서 `usd-v2`나 `applied_substitutions`를 받으면 검증에 실패한다. 계약을 쓰는 쪽(오케스트레이터·콘솔)을 먼저 올리고 정책을 마지막에 올린다. 이 순서는 Task 9에 있다. 새 계약이 `usd-v1` 판정(새 필드 없음)을 그대로 읽는지는 Task 2의 `test_usd_decision_accepts_v1_payload_without_new_fields`가 확인한다.
2. **저장된 `usd-v1` 판정 문서를 새 콘솔에서 열 때.** 이 문서에는 새 pg 특성 키도, `applied_substitutions`도 없다. 추적은 일치해야 하고, 비교 문구는 "현금흐름만 볼 때"로 남아야 한다. Task 4의 `test_v1_decision_keeps_cash_flow_label_and_trace`가 확인한다.
3. **`usd-v2` 신청인데 마켓 API가 장애라 정산·마켓 자료가 없을 때.** 은행만 판정은 None이고, 대체 규칙은 None 조건을 "미충족"으로 본다(예외 없음). 콘솔 비교 줄은 숨는다. Task 2의 `test_v2_without_alt_data_has_no_cash_only_and_no_substitution`이 확인한다.
4. **USD 전용 셀러 ID를 KRW 경로나 모르는 경로로 쓸 때.** 모의 API의 KRW 동의 생성과 KRW 사후 흐름은 계속 거절해야 하고, USD 동의와 사후 흐름은 받아야 한다. Task 8의 `test_usd_only_seller_consent_is_usd_only`가 확인한다.
5. **데모 운영 기준일이 2026-11-25일 때.** 테스트 기준일(2026-11-24)과 하루 차이로 장면이 사라지면 안 된다. Task 7의 장면 테스트가 두 기준일 모두에서 판정과 코드 집합을 확인한다.

---

## 파일 구조

| 파일 | 책임 | Task |
|---|---|---|
| `tests\mockapis\test_usd_scenes.py` (신규) | 현행 판정·장부 지문 고정, v2 기존 셀러 표, 장면 테스트 | 1, 6, 7 |
| `tests\mockapis\usd_books_digest.json` (신규) | 기존 21개 셀러 장부 지문 | 1 |
| `src\shared\contracts.py` | `UsdRulesVersion`, 새 USD 특성 3개, `applied_substitutions` | 2 |
| `src\policy\rules.py`, `src\policy\rulesets\usd-v2.yaml` (신규) | v2 규정, 버전 매핑, `alt_data_features` | 2 |
| `src\policy\engine.py`, `src\policy\app.py` | 규정별 은행만 범위, 현행 규정 선택 | 2 |
| `src\agents\pg_ledger.py`, `src\agents\features.py` | USD pg 특성 승격 | 3 |
| `src\console\officer_chat.py`, `src\console\views.py`, `src\console\templates\detail.html`, `src\console\locales\en\detail.json` | USD 규정 판별, 비교 문구, 값 형식 | 4 |
| `src\shared\sellers.py`, `src\mockapis\sellerdata\usd.py`, `src\mockapis\synth.py` | 셀러 레버 | 5 |
| `src\mockapis\sellerdata\usd_market.py` (신규), `src\shared\usd_source.py`, `src\agents\marketplace.py` | 마켓 다양화 | 6 |
| `src\shared\sellers.py`, `src\mockapis\demo.py` | 장면 셀러 8곳과 카탈로그 | 7 |
| `src\mockapis\app.py`, `src\console\app.py`, `docs\agents.md`, `docs\adr\ADR-003-deterministic-policy.md` | 서비스 연결, 문서 | 8 |

---

### Task 1: 현행 판정과 장부 고정

이후 모든 Task의 안전망이다. 코드는 바꾸지 않는다.

**Files:**
- Create: `tests\mockapis\test_usd_scenes.py`
- Create: `tests\mockapis\usd_books_digest.json`

**Interfaces:**
- Produces: `V1_TABLE: dict[str, tuple[str, frozenset[str], str, frozenset[str]]]` (셀러 → 최종 판정, 최종 코드, 은행만 판정, 은행만 코드), `case_at(seller, as_of) -> UsdCase` (`demo.build_usd_cases(as_of)`를 기준일별로 캐시), `underwrite_at(seller, as_of, principal=500_000) -> Underwriting` (`test_usd_sellers.underwrite`와 같은 호출, 기준일만 인자), `books_digest(case) -> str`. Task 3, 5, 6, 7이 같은 파일에 테스트를 더한다.

- [ ] **Step 1: 테스트 작성**

```python
AS_OF = date(2026, 11, 24)
E, R, I = "eligible", "refer", "ineligible"
F = frozenset
V1_TABLE = {
    **{s: (E, F(), E, F()) for s in ("seller-dailylook-01", "seller-mangwon-02", "seller-gearstore-06",
       "seller-kimchi-09", "seller-linen-10", "seller-kitchen-12", "seller-tangerine-15",
       "seller-petcare-16", "seller-bakery-20")},
    **{s: (R, F({"R105", "R106"}), R, F({"R105", "R106"})) for s in ("seller-glowlab-03", "seller-skinlab-11",
       "seller-cable-13", "seller-sofa-18")},
    **{s: (R, F({"R107", "S001"}), I, F({"R001", "R104", "R107"})) for s in ("seller-knitroom-04",
       "seller-leather-14", "seller-tint-17")},
    "seller-candle-05": (R, F({"R107"}), R, F({"R107"})),
    "seller-ceramic-08": (R, F({"R107", "R109"}), R, F({"R107"})),
    "seller-petfood-07": (I, F({"R003"}), E, F()),
    "seller-streetfit-19": (I, F({"R001", "R002", "R104", "R110", "R901"}), I, F({"R001", "R002", "R104", "R901"})),
    "seller-mugshop-21": (I, F({"R002", "R106", "R108", "R901"}), I, F({"R002", "R106", "R108", "R901"})),
}

def books_digest(case) -> str:
    """은행 거래·정산·세무 신고의 sha256. 마켓 자료는 넣지 않는다(Task 6이 의도적으로 바꾼다)."""

@pytest.mark.parametrize("seller", sorted(V1_TABLE))
def test_usd_v1_verdicts_are_pinned(seller):
    d = explain(underwrite_at(seller, AS_OF).features, rules_for_version("usd-v1")).decision
    final, codes, cash, cash_codes = V1_TABLE[seller]
    assert (d.decision, {r.code for r in d.reason_codes}) == (final, codes)
    assert (d.cash_only.decision, {r.code for r in d.cash_only.reason_codes}) == (cash, cash_codes)
    assert d.max_amount_cents == (0 if final == I else 500_000)

@pytest.mark.parametrize("seller", sorted(V1_TABLE))
def test_existing_seller_books_do_not_change(seller):
    assert books_digest(case_at(seller, AS_OF)) == DIGESTS[seller]
```

`DIGESTS`는 `usd_books_digest.json`을 읽는다. 셀러 목록은 `V1_TABLE`의 21개와 같다.

- [ ] **Step 2: 지문 파일 생성**

지금 코드로 21개 셀러의 `books_digest`를 계산해 `usd_books_digest.json`에 쓴다. 한 번 쓰고 나면 손으로 고치지 않는다.

- [ ] **Step 3: 실행**

Run: `.venv\Scripts\python.exe -m pytest tests\mockapis\test_usd_scenes.py -q`
Expected: 42 passed. 판정 표가 하나라도 다르면 표를 고치지 말고 원인을 보고한다.

- [ ] **Step 4: 커밋**

```powershell
git add tests\mockapis\test_usd_scenes.py tests\mockapis\usd_books_digest.json
git commit -m "test(usd): pin usd-v1 verdicts and seller books before policy v2"
```

---

### Task 2: `usd-v2` 규정, 계약, 엔진, 정책 API

**Files:**
- Modify: `src\shared\contracts.py:255-259` (`UsdCashFlowFeatures`), `:332-343` (`UsdPolicyDecision`)
- Modify: `src\policy\rules.py:18-31`, `:134-160`
- Create: `src\policy\rulesets\usd-v2.yaml`
- Modify: `src\policy\engine.py:171-205`
- Modify: `src\policy\app.py:34`
- Test: `tests\policy\test_rules.py`, `tests\policy\test_engine.py`, `tests\policy\test_app.py`

**Interfaces:**
- Produces (contracts):
  - `UsdRulesVersion = Literal["usd-v1", "usd-v2"]`
  - `UsdCashFlowFeatures.pg_cash_conversion_days: float | None` (≥ 0), `.pg_advance_suspected_count: int | None` (≥ 0), `.pg_offset_rate_trend_pp: float | None`. 모두 기본 None.
  - `UsdPolicyDecision.rules_version: UsdRulesVersion`, `.applied_substitutions: tuple[str, ...] = ()` (각 원소 `^S\d{3}$`)
- Produces (rules):
  - `FeatureName`에 위 3개 추가
  - `UsdRuleSet.version: UsdRulesVersion`, `UsdRuleSet.alt_data_features: tuple[str, ...] | None = None`. 원소는 `UsdCashFlowFeatures`의 None 허용 필드여야 한다. 아니면 검증 오류.
  - `USD_CURRENT_RULES: dict[str, str] = {"usd-v1": "usd-v2"}` (상품 버전 → 현행 규정 버전)
  - `current_usd_rules(product_version: str) -> UsdRuleSet`. 모르는 상품 버전이면 `KeyError`.
  - `is_usd_rules(version: object) -> bool`. `UsdRulesVersion`의 값이면 True.
- Produces (engine): `_cash_only`는 `rules.alt_data_features`가 있으면 그 목록을 지운다. 없으면 지금 USD 목록을 지운다. 결정에 `applied_substitutions=tuple(t.code for t in applied)`를 싣는다(USD만).
- Produces (테스트 헬퍼, `tests\policy\conftest.py`): `usd_features(**overrides) -> UsdCashFlowFeatures`. 기본값은 `usd-v1`·`usd-v2` 모두에서 적격이다.
  - 기간·상환: `months_of_history=12`, `dscr=2.5`, `cash_buffer_days=25`
  - 매출: `revenue_volatility=0.1`, `tax_bank_match_ratio=1.0`, `revenue_trend_ratio=1.0`
  - 업력·차입: `business_age_months=38`, `high_cost_lender_count=0`
  - 금액·기간: `avg_monthly_inflow_cents=2_000_000`, `requested_amount_cents=500_000`, `requested_tenor_months=6`
  - 프로필: `industry="Food"`, `region="Seoul"`
  - 그 밖의 `pg_*`·`mkt_*`는 None

  `usd_features_payload(**overrides)`는 그 `model_dump(mode="json")`이다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests\policy\test_rules.py
def test_usd_v2_loads_with_new_rules_and_one_substitution_per_target():
    r = rules_for_version("usd-v2")
    assert isinstance(r, UsdRuleSet) and r.version == "usd-v2"
    assert {c.code: (c.field, c.op, c.threshold) for c in r.knockouts}["R004"] == ("tax_bank_match_ratio", "lt", 0.5)
    refers = {c.code: (c.field, c.op, c.threshold) for c in r.refers}
    assert refers["R111"] == ("mkt_cancel_rate_90d", "gt", 0.08)
    assert refers["R112"] == ("pg_advance_suspected_count", "gt", 0)
    assert refers["R113"] == ("pg_offset_rate_trend_pp", "gt", 3.0)
    subs = {s.code: (s.replaces, s.outcome) for s in r.substitutions}
    assert subs == {"S001": ("R001", "refer"), "S002": ("R104", "pass"), "S003": ("R103", "pass"),
                    "S004": ("R102", "pass"), "S005": ("R004", "refer")}
    assert r.limit_boost.substituted_max_amount_cents == 500_000
    assert set(r.alt_data_features) == {*(k for k in MKT_FEATURES if k != "mkt_pending_settlement_krw"),
        "mkt_pending_settlement_cents", "pg_reconciliation_ratio", "pg_top_platform_share",
        "pg_cash_conversion_days", "pg_advance_suspected_count", "pg_offset_rate_trend_pp"}
    assert all(c.message.isascii() for c in (*r.knockouts, *r.refers, *r.substitutions))

def test_usd_v1_is_unchanged_and_current_rules_map_to_v2():
    assert rules_for_version("usd-v1").alt_data_features is None
    assert current_usd_rules("usd-v1").version == "usd-v2"
    assert is_usd_rules("usd-v1") and is_usd_rules("usd-v2") and not is_usd_rules("2026.11-v4")

def test_alt_data_features_must_be_optional_usd_fields():
    data = yaml.safe_load(USD_V2_PATH.read_text()) | {"alt_data_features": ["dscr"]}
    with pytest.raises(ValidationError):
        UsdRuleSet.model_validate(data)
```

```python
# tests\policy\test_engine.py — usd_features는 위 conftest 헬퍼
def test_v2_bank_only_strips_settlement_and_market_but_v1_keeps_settlement():
    f = usd_features(cash_buffer_days=6.0, pg_reconciliation_ratio=1.0, pg_cash_conversion_days=6.0,
                     pg_top_platform_share=0.6, mkt_tenure_months=30)
    v2 = explain(f, rules_for_version("usd-v2")).decision
    assert (v2.decision, v2.applied_substitutions) == ("eligible", ("S003",))
    assert (v2.cash_only.decision, {r.code for r in v2.cash_only.reason_codes}) == ("refer", {"R103"})
    v1 = explain(f, rules_for_version("usd-v1")).decision
    assert v1.cash_only.decision == "refer" and v1.applied_substitutions == ()

def test_s005_refers_tax_gap_and_caps_limit():
    f = usd_features(tax_bank_match_ratio=0.4, pg_reconciliation_ratio=1.0, mkt_tenure_months=20,
                     requested_amount_cents=1_000_000)
    d = explain(f, rules_for_version("usd-v2")).decision
    assert (d.decision, {r.code for r in d.reason_codes}, d.max_amount_cents) == ("refer", {"S005"}, 500_000)
    assert d.cash_only.decision == "ineligible" and {r.code for r in d.cash_only.reason_codes} == {"R004"}

def test_v2_without_alt_data_has_no_cash_only_and_no_substitution():
    f = usd_features(cash_buffer_days=6.0)  # pg_*, mkt_* 모두 None
    d = explain(f, rules_for_version("usd-v2")).decision
    assert d.cash_only is None and d.applied_substitutions == ()
    assert {r.code for r in d.reason_codes} == {"R103"}

def test_usd_decision_accepts_v1_payload_without_new_fields():
    stored = {**USD_V1.model_dump(), "application_id": "a", "decision": "eligible", "max_amount_cents": 500000,
              "rate_band": "A", "reason_codes": [], "rules_version": "usd-v1"}
    assert UsdPolicyDecision.model_validate(stored).applied_substitutions == ()
```

```python
# tests\policy\test_app.py
def test_usd_application_is_judged_with_current_usd_rules():
    body = client.post("/v1/decisions", json=usd_features_payload()).json()
    assert body["rules_version"] == "usd-v2" and "applied_substitutions" in body
```

- [ ] **Step 2: 실행해 실패 확인**

Run: `.venv\Scripts\python.exe -m pytest tests\policy -q`
Expected: FAIL (`usd-v2` 없음, `current_usd_rules` import 오류)

- [ ] **Step 3: 구현**

- `usd-v2.yaml`은 `usd-v1.yaml`을 복사한다. 스펙 4.1~4.3 표의 영문 메시지를 그대로 쓰고, `alt_data_features`(위 테스트의 12개)를 더한다. 규칙 순서는 knockouts R001, R002, R003, R004, refers R102~R113, substitutions S001~S005로 한다.
- `load_rules`는 `version`이 `UsdRulesVersion`의 값이면 `UsdRuleSet`으로 읽는다.
- `app.py:34`는 `current_usd_rules(features.product_version)`을 쓴다.
- `decision_payload`는 바꾸지 않는다(빈 `applied_substitutions`도 `[]`로 나간다).

- [ ] **Step 4: 실행해 통과 확인**

Run: `.venv\Scripts\python.exe -m pytest tests\policy tests\mockapis\test_usd_scenes.py tests\mockapis\test_usd_sellers.py -q`
Expected: 모두 PASS. Task 1의 v1 표는 그대로다.

- [ ] **Step 5: 커밋**

```powershell
git add src\shared\contracts.py src\policy tests\policy
git commit -m "feat(policy): usd-v2 rules with alt-data scope and substitutions"
```

---

### Task 3: 정산 지표 3개를 USD 특성으로 옮김

**Files:**
- Modify: `src\agents\pg_ledger.py:203` 근처
- Modify: `src\agents\features.py:22`, `:300-301`
- Test: `tests\agents\test_pg_ledger.py`, `tests\mockapis\test_usd_scenes.py`

**Interfaces:**
- Consumes: Task 2의 `UsdCashFlowFeatures` 새 필드
- Produces: `pg_ledger.USD_PG_FEATURES = ("pg_cash_conversion_days", "pg_advance_suspected_count", "pg_offset_rate_trend_pp")`, `pg_ledger.usd_features_of(summary: Mapping) -> dict[str, float | int | None]` (`features_of` 결과 + 위 3개). KRW `features_of`는 그대로다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests\mockapis\test_usd_scenes.py
def test_usd_underwriting_carries_settlement_features():
    bakery = underwrite_at("seller-bakery-20", AS_OF).features
    assert 12 <= bakery.pg_cash_conversion_days <= 20 and bakery.pg_advance_suspected_count == 0
    assert bakery.pg_offset_rate_trend_pp in (0.0, None)
    assert underwrite_at("seller-mugshop-21", AS_OF).features.pg_advance_suspected_count > 0

# tests\agents\test_pg_ledger.py
def test_usd_features_of_adds_three_keys_and_krw_does_not():
    summary = {"available": True, "pg_reconciliation_ratio": 1.0, "pg_top_platform_share": .5,
               "pg_cash_conversion_days": 6.4, "pg_advance_suspected_count": 0, "pg_offset_rate_trend_pp": 1.2}
    assert set(usd_features_of(summary)) == set(features_of(summary)) | set(USD_PG_FEATURES)
    assert set(features_of(summary)) == {"pg_reconciliation_ratio", "pg_top_platform_share"}
```

- [ ] **Step 2: 실행해 실패 확인**

Run: `.venv\Scripts\python.exe -m pytest tests\agents\test_pg_ledger.py tests\mockapis\test_usd_scenes.py -q`
Expected: FAIL (`usd_features_of` 없음)

- [ ] **Step 3: 구현.** `features.py`의 `features_of(pg)` 자리에서 USD면 `usd_features_of(pg)`를 쓴다.

- [ ] **Step 4: 실행해 통과 확인.** 같은 명령에 `tests\agents\test_features.py tests\agents\test_underwriting.py`를 더한다. Expected: PASS. v1 표는 그대로다(v1은 새 특성을 읽지 않는다).

- [ ] **Step 5: 커밋**

```powershell
git add src\agents tests\agents\test_pg_ledger.py tests\mockapis\test_usd_scenes.py
git commit -m "feat(agents): promote USD settlement ledger metrics to policy features"
```

---

### Task 4: 콘솔과 심사 도우미의 `usd-v2` 대응

**Files:**
- Modify: `src\console\officer_chat.py:80,92,98,103,135-143`
- Modify: `src\console\views.py:185-191`
- Modify: `src\console\templates\detail.html:11-13`, `:224-226`
- Modify: `src\console\locales\en\detail.json`
- Test: `tests\console\test_usd_views.py`, `tests\console\test_officer_chat.py`

**Interfaces:**
- Consumes: Task 2의 `is_usd_rules`, `UsdRuleSet.alt_data_features`
- Produces:
  - `officer_chat`의 `rules.version == "usd-v1"` 5곳을 `isinstance(rules, UsdRuleSet)`로 바꾼다. `verified_policy`의 `version != product.product_version`은 `not is_usd_rules(version)`으로 바꾼다.
  - `views._policy_compare(policy, unit)`의 반환에 `"bank_tax": bool`을 더한다. 판정 규정에 `alt_data_features`가 있으면 True다.
  - `views.reason_text(field: str | None, value: Any) -> str | None`: 아래 네 필드에만 단위를 붙인다. 다른 필드는 None을 돌려 지금 매크로 동작을 쓴다.
    - `pg_cash_conversion_days` → `_("%(v)s일")` (en `"%(v)s days"`), 소수 1자리
    - `pg_advance_suspected_count` → `_("%(v)s건")` (en `"%(v)s"`), 정수
    - `pg_offset_rate_trend_pp` → `"%(v)s%p"`, 소수 1자리
    - `mkt_cancel_rate_90d` → 백분율 소수 1자리 + `%`
  - 비교 문구 키: `"은행·세무 기록만 볼 때"` (en `"Bank and tax records only"`), `"정산·마켓 데이터 반영"` (en `"With settlement and marketplace data"`). `bank_tax`가 False면 지금 키("현금흐름만 볼 때", "셀러 데이터 반영")를 쓴다.

- [ ] **Step 1: 실패하는 테스트 작성** (`tests\console\test_usd_views.py`, 기존 `preview-usd-bakery` 픽스처와 `lang` 픽스처 사용)

```python
@pytest.mark.parametrize("lang", ["ko", "en"])
def test_v2_decision_shows_alt_data_compare_and_unit_values(lang):
    deps = fixtures.build_deps()
    doc = deps.store.docs["preview-usd-bakery"]
    doc["policy"] |= {"rules_version": "usd-v2", "decision": "refer",
        "cash_only": {**USD_V1.model_dump(), "decision": "eligible", "max_amount_cents": 500000, "reason_codes": []},
        "reason_codes": [
            RuleCheck("R113", "m", "refer", "pg_offset_rate_trend_pp", "gt", 5.94, 3.0, "hit").reason().model_dump(),
            RuleCheck("R111", "m", "refer", "mkt_cancel_rate_90d", "gt", .13, .08, "hit").reason().model_dump(),
            RuleCheck("R112", "m", "refer", "pg_advance_suspected_count", "gt", 3, 0, "hit").reason().model_dump()]}
    policy = detail_panels(page_for(deps, lang))["tab-policy"]
    assert {"ko": "은행·세무 기록만 볼 때", "en": "Bank and tax records only"}[lang] in policy
    assert ("R113", "5.9%p", "3.0%p") in reasons(policy) and ("R111", "13.0%", "8.0%") in reasons(policy)
    assert ("R112", "3건", "0건") in reasons(policy) if lang == "ko" else ("R112", "3", "0") in reasons(policy)

def test_v1_decision_keeps_cash_flow_label_and_trace():
    deps = fixtures.build_deps()
    doc = deps.store.docs["preview-usd-bakery"]  # rules_version usd-v1, 새 pg 키·applied_substitutions 없음
    doc["policy"].setdefault("cash_only", {**USD_V1.model_dump(), "decision": "eligible",
                                           "max_amount_cents": doc["policy"]["max_amount_cents"], "reason_codes": []})
    policy = detail_panels(TestClient(create_app(deps)).get("/applications/preview-usd-bakery").text)["tab-policy"]
    assert "현금흐름만 볼 때" in policy and "은행·세무 기록만 볼 때" not in policy
```

`page_for(deps, lang)`(문서를 바꾼 `deps`로 앱을 만들어 `lang` 쿠키로 상세 페이지를 받는다)와 `reasons(policy)`는 이 파일의 기존 패턴(`TestClient(create_app(deps)).get(...)`, `test_policy_reason_displays_whole_cents_without_rounding_other_values`의 정규식)을 묶은 지역 헬퍼다.

`tests\console\test_officer_chat.py`에는 `usd-v2` 판정 문서로 `verified_policy`가 None이 아니고, 추적의 `substitutions`에 S003이 있는 테스트를 더한다. 문서의 특성은 Task 2의 S003 테스트 값을 쓴다.

- [ ] **Step 2: 실행해 실패 확인**

Run: `.venv\Scripts\python.exe -m pytest tests\console\test_usd_views.py tests\console\test_officer_chat.py -q`
Expected: 새 테스트 FAIL

- [ ] **Step 3: 구현.** `reason_value` 매크로는 `reason_text`(Jinja 전역 또는 필터로 등록)가 값을 주면 그것을 쓰고, 아니면 지금 식을 쓴다. 문자열 키를 추가하면 `tests\console\test_i18n.py`와 `test_pretranslate_coverage.py`가 요구하는 대로 en 로케일에 넣는다.

- [ ] **Step 4: 실행해 통과 확인**

Run: `.venv\Scripts\python.exe -m pytest tests\console -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```powershell
git add src\console tests\console
git commit -m "feat(console): show usd-v2 bank-and-tax comparison and unit-aware reason values"
```

---

### Task 5: USD 셀러 레버

기본값은 지금 동작과 같다. 난수 소비 순서를 바꾸지 않는다(배수는 `rng.randint` 뒤에 곱한다).

**Files:**
- Modify: `src\shared\sellers.py:49-64` (`UsdSeller`)
- Modify: `src\mockapis\sellerdata\usd.py` (`simulate_usd`)
- Modify: `src\mockapis\synth.py:95-136` (`generate_usd_case`)
- Test: `tests\mockapis\test_usd_scenes.py`

**Interfaces:**
- Produces (`UsdSeller` 새 필드, 모두 기본값 = 지금 동작):
  - `season_percent: tuple[int, ...] | None = None`: 1~12월 매출 배수(%). 길이가 12가 아니면 `ValueError`. 일 매출과 일 재고 매입에 곱한다. 급여·인출 기준은 기본 주 매출을 그대로 쓴다.
  - `owner_floor_percent: int = 200`: 금요일 사주 인출 후 남기는 잔고(주 매출 대비 %)
  - `tax_declared_percent: int = 100`: 세무 신고액 = 월 정산 순액 × 이 비율 // 100
  - `growth_days: int = 0`, `growth_percent: int = 100`: 기준일 `growth_days`일 전부터 일 매출과 재고 매입에 곱한다
  - `offset_rise_days: int = 0`, `offset_rise_rate: Decimal = Decimal(0)`: 기준일 `offset_rise_days`일 전 이후 지급분의 상계율
  - `advance_label: str | None = None`: 선정산 입금 적요. None이면 `ADVANCE_DESCRIPTION`
  - `market_months: int | None = None`, `cancel_rate: Decimal | None = None`: Task 6이 쓴다
- Produces (`simulate_usd` 새 키워드): `growth_from: date | None = None`. `generate_usd_case`는 `growth_days`가 있으면 `as_of - timedelta(days=growth_days)`를 넘긴다. `offset_rise_days`가 있으면 `deduction_rate=lambda day: rate if day >= as_of - timedelta(days=offset_rise_days) else Decimal(0)`을 넘긴다.
- 사후 흐름(`postloan.generate_usd_postloan`)에서는 계절 배수가 `simulate_usd` 안에서 자동 적용된다. 성장 셀러는 사후 시작일부터 `growth_from=start`를 받는다. 이 연결은 Task 8에서 한다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
BASE = USD_SELLERS["seller-bakery-20"]

def books(seller, **kw):
    return simulate_usd(seller, "k", date(2026, 1, 1), date(2026, 3, 31), 7, 3 * seller.weekly_sales_cents, **kw)

def test_default_levers_keep_books_identical():
    assert books(replace(BASE)) == books(BASE)

def test_season_scales_sales_by_month():
    flat = books(BASE); peaked = books(replace(BASE, season_percent=(200,) + (100,) * 11))
    jan = lambda b: sum(o.gross_cents for o in b.orders if o.order_on.month == 1)
    feb = lambda b: sum(o.gross_cents for o in b.orders if o.order_on.month == 2)
    assert jan(peaked) >= 1.95 * jan(flat) and feb(peaked) == feb(flat)
    with pytest.raises(ValueError):
        replace(BASE, season_percent=(100,) * 11)

def test_owner_floor_lowers_friday_balance():
    low = books(replace(BASE, owner_floor_percent=50))
    fridays = [t.balance_after_cents for t in low.transactions if t.description == "Owner withdrawal"]
    assert fridays and max(fridays) == BASE.weekly_sales_cents // 2

def test_advance_label_books_advances_as_revenue_without_platform():
    b = simulate_usd(replace(BASE, advance_label="Marketplace payout"), "k", date(2026, 1, 1), date(2026, 3, 31),
                     7, 0, advance_from=date(2026, 2, 1))
    adv = [t for t in b.transactions if t.description == "Marketplace payout"]
    assert adv and all(t.platform_code is None and classify(t) is Category.REVENUE for t in adv)

def test_tax_declared_percent_and_offset_rise_in_generated_case():
    # replace()한 셀러를 USD_SELLERS에 monkeypatch로 넣고 generate_usd_case를 부른다.
    # tax_declared_percent=40 → 모든 신고액 == 정산 순액 × 40 // 100
    # offset_rise_days=90, offset_rise_rate=Decimal(".08") → 기준일 90일 전 이후 지급분의 deduction/gross ≈ .08, 그 전은 0
```

- [ ] **Step 2: 실행해 실패 확인**

Run: `.venv\Scripts\python.exe -m pytest tests\mockapis\test_usd_scenes.py -q`
Expected: 새 테스트 FAIL(필드 없음)

- [ ] **Step 3: 구현.** 필드와 키워드를 더하고 `generate_usd_case`에서 연결한다.

- [ ] **Step 4: 실행해 통과 확인**

Run: `.venv\Scripts\python.exe -m pytest tests\mockapis -q`
Expected: PASS. Task 1의 장부 지문과 v1 표가 그대로다.

- [ ] **Step 5: 커밋**

```powershell
git add src\shared\sellers.py src\mockapis tests\mockapis\test_usd_scenes.py
git commit -m "feat(mockapis): opt-in USD seller levers for alt-data scenes"
```

---

### Task 6: 마켓 판매 성과 다양화와 기존 셀러의 v2 표

**Files:**
- Create: `src\mockapis\sellerdata\usd_market.py`
- Modify: `src\mockapis\synth.py:112-120` (하드코딩 마켓을 새 함수 호출로 바꿈)
- Modify: `src\shared\usd_source.py:51-60`
- Modify: `src\agents\marketplace.py:39-55`
- Test: `tests\mockapis\test_usd_scenes.py`, `tests\agents\test_marketplace.py`

**Interfaces:**
- Consumes: Task 5의 `market_months`, `cancel_rate`, `risk`
- Produces:
  - `UsdStorePlatform.orders_90d: int = 0` (≥ 0), `.seller_grade: str | None = None`, `.assigned_pending_cents: Cents = 0`. 기본값은 옛 문서 호환용이다.
  - `usd_market.build_usd_market(seller: UsdSeller, as_of: date, seed: int, orders: Sequence[UsdOrderDay], pending: Sequence[UsdSettlement], penalties: tuple[StorePenalty, ...]) -> UsdMarketplace`. 난수는 `random.Random(f"market:{seed}")`처럼 장부와 다른 스트림을 쓴다.
  - `store_summary`의 USD 플랫폼 행에 `orders_90d`, `seller_grade`, `assigned_pending_cents`를 넣는다. `mkt_pending_settlement_cents`는 KRW처럼 양도분을 뺀다.

파생 규칙(제 판단, 스펙 10장이 구현자에게 맡긴 부분):

| 항목 | 규칙 |
|---|---|
| 주력 채널 입점 개월 | `market_months`가 있으면 그 값. 없으면 `business_age_months - rng(0..6)`을 20~60으로 자른 값 |
| 다른 채널 입점 개월 | 주력 × rng(45..85)%. 자사몰은 × rng(25..50)%. 최소 6개월 |
| 평점 | 주력은 `seller.rating`. 다른 채널은 `seller.rating + rng.choice((-0.2, -0.1, 0, 0.1))`, 4.9를 넘지 않게(원래 평점이 4.9 이하일 때) |
| 취소율 | `cancel_rate`가 있으면 그 값. 없으면 업종 기본(Apparel .025, Electronics .02, Beauty .015, Home and living .012, Food .01, Pet supplies .01, Handmade .008, 기타 .012) × rng(70..130)% |
| 90일 총 주문액 | 정산 대상 매출 + 취소액. 취소액 = 매출 × r / (1 − r). 장부에는 더하지 않는다 |
| 객단가 | Food 2800, Apparel 4500, Beauty 3200, Handmade 5500, Home and living 6000, Electronics 8500, Pet supplies 4000, 기타 4000 센트 |
| 90일 주문 수 | 90일 총 주문액 // 객단가 |
| 리뷰 수 | 주문 수 × 입점 개월 / 3 × rng(3..8)% |
| 등급 | Premium(입점 ≥ 36, 평점 ≥ 4.7, 90일 총 주문액 ≥ $40,000), Gold(≥ 24, ≥ 4.5), Silver(≥ 12), 그 밖 Standard |
| 선정산 양도 | `risk == "assigned"`인 셀러의 주력 채널만 정산 예정금 × 85%. 나머지 0 |

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_existing_sellers_have_diverse_marketplaces():
    tenures, all_cancel = set(), []
    for seller in V1_TABLE:
        ps = case_at(seller, AS_OF).marketplace.platforms
        if len(ps) > 1:
            assert len({p.opened_on for p in ps}) > 1
            assert len({(p.rating, p.review_count) for p in ps}) > 1
        assert all(p.orders_90d > 0 and p.seller_grade for p in ps)
        assigned = [p for p in ps if p.assigned_pending_cents > 0]
        assert len(assigned) == (1 if seller == "seller-mugshop-21" else 0)
        tenures.add(min(p.opened_on for p in ps))
        summary = store_summary(case_at(seller, AS_OF).marketplace, AS_OF)
        all_cancel.append(summary["mkt_cancel_rate_90d"])
    assert len(tenures) >= 8 and all(0 < c <= 0.04 for c in all_cancel)

V2_FINAL_EXTRA = {"seller-mugshop-21": {"R112"}}

@pytest.mark.parametrize("seller", sorted(V1_TABLE))
def test_existing_sellers_under_usd_v2(seller):
    d = explain(underwrite_at(seller, AS_OF).features, rules_for_version("usd-v2")).decision
    final, codes, cash, cash_codes = V1_TABLE[seller]
    assert (d.decision, {r.code for r in d.reason_codes}) == (final, codes | V2_FINAL_EXTRA.get(seller, set()))
    bank_codes = cash_codes - {"R107", "R108"}
    bank = I if bank_codes & {"R001", "R002", "R003", "R004", "R901"} else R if bank_codes else E
    assert (d.cash_only.decision, {r.code for r in d.cash_only.reason_codes}) == (bank, bank_codes)
```

`tests\agents\test_marketplace.py`에는 USD 플랫폼 행에 세 키가 있고, `mkt_pending_settlement_cents == 예정금 합 - 양도 합`인지 보는 테스트를 더한다.

- [ ] **Step 2: 실행해 실패 확인**

Run: `.venv\Scripts\python.exe -m pytest tests\mockapis\test_usd_scenes.py tests\agents\test_marketplace.py -q`
Expected: 새 테스트 FAIL

- [ ] **Step 3: 구현.** `generate_usd_case`는 `build_usd_market`을 부른다. 판매정지 제재는 지금처럼 첫 플랫폼에 둔다.

- [ ] **Step 4: 실행해 통과 확인**

Run: `.venv\Scripts\python.exe -m pytest tests\mockapis tests\agents -q`
Expected: PASS. v1 표, 장부 지문, KRW 원형 대응, 셀러 경로가 모두 그대로다. v1 표가 깨지면(예: knitroom 평점 가중평균 < 4.5, 리뷰 < 100) 파생 규칙의 난수 범위를 좁혀 맞추고, 표는 고치지 않는다. 바꾼 범위는 이 계획 표에 적는다.

- [ ] **Step 5: 커밋**

```powershell
git add src\mockapis src\shared\usd_source.py src\agents\marketplace.py tests
git commit -m "feat(mockapis): diversify USD marketplace performance per platform"
```

---

### Task 7: 장면 셀러 8곳과 장면 테스트

**Files:**
- Modify: `src\shared\sellers.py` (`USD_SELLERS`에 추가, `USD_ONLY_SELLER_IDS`)
- Modify: `src\mockapis\demo.py:62-64` (`build_usd_cases`), FlowStore 시드
- Test: `tests\mockapis\test_usd_scenes.py`

**Interfaces:**
- Produces:
  - `shared.sellers.USD_ONLY_SELLER_IDS: tuple[str, ...]` (아래 8개, 표 순서)
  - `mockapis.demo.USD_ONLY_APPLICANTS: tuple[tuple[str, int], ...]` (ID, 시드 122~129)
  - `mockapis.demo.SCENE_REQUEST_CENTS: dict[str, int]`. popup만 1_000_000이고 나머지는 기본 500_000이다. 시나리오 실행기가 쓴다.
  - `build_usd_cases`는 기존 21개와 USD 전용 8개를 모두 만든다. `FlowStore._seeds`에도 넣는다.

시작 값(제 판단). 장면 테스트가 기준이며, 수치는 맞을 때까지 조정할 수 있다. 아래 표는 조정을 마친 최종 값이다(Task 7 구현 시 `owner_floor_percent` 50→40, campgear `season_percent`를 키워 10% 여유 확보. Fix round 1: vitamin 채널을 `_GENERAL`→`_TWO_MARKETS`로 바꿔 선정산 대상(쿠팡) 비중을 55%로 올렸다. 정산 대사율이 약 0.805로 R108 기준 0.9에서 10% 이상 떨어진다).

| ID (시드) | 상호 / 업종 | 채널 | 주 매출(센트) | 레버 | 장면 |
|---|---|---|---|---|---|
| `seller-snackbox-22` (122) | Mapo Snack Box / Food | naver 60, own 40 | 300_000 | `owner_floor_percent=40` | O1 |
| `seller-campgear-23` (123) | Gapyeong Camp Gear / Sports | `_GENERAL` | 380_000 | `season_percent=(20,20,50,100,120,90,60,60,200,280,250,60)`, `market_months=40` | O2 |
| `seller-teahouse-24` (124) | Boseong Tea House / Food | `_GENERAL` | 300_000 | `tax_declared_percent=40` | O3 |
| `seller-phonecase-25` (125) | Sinchon Phone Case / Electronics | `_TWO_MARKETS` | 200_000 | `history_months=4`, `business_age_months=8`, `market_months=7`, `rating=4.3` | O5 |
| `seller-toyland-26` (126) | Bucheon Toy Land / Toys | `_GENERAL` | 350_000 | `cancel_rate=Decimal(".13")` | R1 |
| `seller-vitamin-27` (127) | Ilsan Vitamin House / Health | `_TWO_MARKETS` | 360_000 | `risk="assigned"`, `advance_label="Marketplace payout"` | R2 |
| `seller-golfgear-28` (128) | Yongin Golf Gear / Sports | `_RETAIL` | 520_000 | `offset_rise_days=90`, `offset_rise_rate=Decimal(".08")` | R3 |
| `seller-popup-29` (129) | Seongsu Popup Goods / Character goods | coupang 70, naver 30 | 120_000 | `inventory_percent=30`, `growth_days=40`, `growth_percent=300` | O6 |

스펙 예시와 다른 점과 이유:
- **O2를 여름(surfwear)에서 가을 성수기 캠핑용품(campgear)으로 바꿨다.** 여름 성수기 셀러는 11월 기준일에 최근 60일 매출이 앞 180일보다 크게 낮아 R106(대체 불가 대상 아님, S004가 덮지 않음)에도 걸린다. 가을 성수기 셀러는 9~11월 매출이 높아 R102만 남는다(기준일 2026-11-24에 `revenue_volatility` 약 0.60).
  - 겨울 성수기(skiwear)도 검토했지만 버렸다. 대출 기간(2026-11-25~2027-02-24)이 스키 성수기라 사후 모니터링 장면 ③(비수기 오경보)을 만들 수 없다. 캠핑용품은 12~2월이 비수기(60/20/20)라 은행만 보면 입금 급감 경보가 여러 주 뜬다. 사후 모니터링 계획이 이 셀러를 장면 ③에 쓴다.
- **O1은 네이버 단일 집중을 피했다.** 네이버 85%면 R107(> 0.8)에 걸려 장면이 흐려진다. 네이버 60, 자사몰 40이면 현금화 약 5.4일, 최대 비중 0.6이다.
- **O6은 성장을 기준일 40일 전부터만 준다.** 완결 월(10월까지)의 변동성과 평균 입금은 거의 그대로 두고, 현재 정산 예정금만 키운다. 쿠팡 70%(R107 여유)와 낮은 재고율(DSCR 여유, 요청 $10,000·6개월)도 같은 이유다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
SCENES = {  # seller: (은행만 판정, 은행만 코드, 최종 판정, 최종 코드)
    "seller-snackbox-22": (R, F({"R103"}), E, F()),
    "seller-campgear-23": (R, F({"R102"}), E, F()),
    "seller-teahouse-24": (I, F({"R004"}), R, F({"S005"})),
    "seller-phonecase-25": (I, F({"R001", "R104"}), I, F({"R001", "R104"})),
    "seller-toyland-26": (E, F(), R, F({"R111"})),
    "seller-vitamin-27": (E, F(), R, None),  # None: {"R112"} ⊆ 코드 ⊆ {"R112", "R108"}. 첫 실행 결과로 고정한다
    "seller-golfgear-28": (E, F(), R, F({"R113"})),
    "seller-popup-29": (E, F(), E, F()),
}

@pytest.mark.parametrize("as_of", [AS_OF, date(2026, 11, 25)])
@pytest.mark.parametrize("seller", sorted(SCENES))
def test_scene_verdicts(seller, as_of):
    d = explain(underwrite_at(seller, as_of, SCENE_REQUEST_CENTS.get(seller, 500_000)).features,
                rules_for_version("usd-v2")).decision
    bank, bank_codes, final, codes = SCENES[seller]
    assert (d.cash_only.decision, {r.code for r in d.cash_only.reason_codes}) == (bank, bank_codes)
    assert d.decision == final and {r.code for r in d.reason_codes} == codes

def test_scene_limits_and_substitutions():
    by = {s: explain(underwrite_at(s, AS_OF, SCENE_REQUEST_CENTS.get(s, 500_000)).features,
                     rules_for_version("usd-v2")).decision for s in SCENES}
    assert by["seller-snackbox-22"].applied_substitutions == ("S003",)
    assert by["seller-campgear-23"].applied_substitutions == ("S004",)
    assert by["seller-teahouse-24"].max_amount_cents == 500_000
    assert "S001" not in by["seller-phonecase-25"].applied_substitutions
    popup = by["seller-popup-29"]
    assert popup.max_amount_cents >= popup.cash_only.max_amount_cents + 100_000

MARGINS = {  # seller: [(특성, 연산, 임계값)] — 결정 특성만, 10% 여유
    "seller-snackbox-22": [("cash_buffer_days", "lt", 10), ("pg_cash_conversion_days", "le", 10)],
    "seller-campgear-23": [("revenue_volatility", "gt", .5), ("mkt_tenure_months", "ge", 24),
                          ("mkt_review_count", "ge", 300), ("revenue_trend_ratio", "ge", .8)],
    "seller-teahouse-24": [("tax_bank_match_ratio", "lt", .5)],
    "seller-toyland-26": [("mkt_cancel_rate_90d", "gt", .08)],
    "seller-golfgear-28": [("pg_offset_rate_trend_pp", "gt", 3.0)],
    "seller-popup-29": [("revenue_volatility", "le", .5), ("pg_top_platform_share", "le", .8), ("dscr", "ge", 1.0)],
}

@pytest.mark.parametrize("seller", sorted(MARGINS))
def test_scene_features_keep_ten_percent_margin(seller):
    f = underwrite_at(seller, AS_OF, SCENE_REQUEST_CENTS.get(seller, 500_000)).features
    for field, op, t in MARGINS[seller]:
        v = getattr(f, field)
        ok = v <= t / 1.1 if op in ("lt", "le") else v >= t * 1.1
        assert ok, (field, v, t)
```

`test_usd_policy_outcome_mirrors_the_krw_archetype`(`tests\mockapis\test_usd_sellers.py`)는 `demo.DEMO_APPLICANTS`만 돌므로 USD 전용 셀러를 건드리지 않는다. 이것도 확인한다.

- [ ] **Step 2: 실행해 실패 확인**

Run: `.venv\Scripts\python.exe -m pytest tests\mockapis\test_usd_scenes.py -q`
Expected: FAIL (`KeyError: seller-snackbox-22`)

- [ ] **Step 3: 셀러를 추가하고, 장면 테스트가 통과할 때까지 수치를 조정한다.**

판정 기대값은 고치지 않는다. 바꾼 수치는 위 시작 값 표에 반영한다. vitamin의 최종 코드 집합은 첫 통과 결과로 `None`을 바꿔 고정한다. 스펙 5.2대로, O6을 R102·R106·R107·R002 없이 한도 확대로 만들 수 없으면 O6 셀러와 테스트를 빼고 Task 9 보고에 적는다.

- [ ] **Step 4: 실행해 통과 확인**

Run: `.venv\Scripts\python.exe -m pytest tests\mockapis tests\policy -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```powershell
git add src\shared\sellers.py src\mockapis\demo.py tests\mockapis\test_usd_scenes.py
git commit -m "feat(mockapis): USD-only scene sellers for alt-data policy demo"
```

---

### Task 8: 서비스 연결, 사후 흐름, 문서

**Files:**
- Modify: `src\mockapis\demo.py:110-122` (`FlowStore._usd_case`)
- Modify: `src\mockapis\app.py:130` (`create_consent`)
- Modify: `src\mockapis\postloan.py:135-161` (`generate_usd_postloan`: 성장 셀러에 `growth_from=start`)
- Modify: `src\console\app.py:130`
- Modify: `src\shared\sellers.py:125-127` (`seller_label`)
- Modify: `docs\agents.md`, `docs\adr\ADR-003-deterministic-policy.md`
- Test: `tests\mockapis\test_usd_sellers.py`, `tests\console\test_usd_views.py`

**Interfaces:**
- Consumes: Task 7의 `USD_ONLY_SELLER_IDS`, `USD_ONLY_APPLICANTS`
- Produces:
  - `FlowStore.usd_case(id)`는 `id in USD_SELLERS and id in self._seeds`면 케이스를 만든다(KRW 케이스 여부와 무관).
  - `create_consent`는 USD 요청이면 `flows.usd_case`만 확인하고, KRW 요청이면 지금처럼 `cases`만 확인한다.
  - 콘솔 `DEMO_APPLICANTS = (*(s.applicant_id for s in DEMO_SELLERS), *USD_ONLY_SELLER_IDS)`
  - `seller_label(id)`: KRW 데모 셀러가 아니면 `USD_SELLERS[id].business_name`, 그것도 아니면 ID

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests\mockapis\test_usd_sellers.py
def test_usd_only_seller_consent_is_usd_only():
    client = TestClient(mock_app(...))  # 이 파일의 기존 mock_app 픽스처 구성 그대로
    assert usd_consent(client, "seller-snackbox-22").status_code == 201
    assert krw_consent(client, "seller-snackbox-22").status_code in (400, 404, 422)
    assert usd_consent(client, "seller-unknown-99").status_code in (400, 404, 422)

@pytest.mark.parametrize("seller", ["seller-snackbox-22", "seller-campgear-23", "seller-popup-29"])
def test_approved_scene_sellers_generate_stable_postloan(seller):
    flow = demo.FlowStore(demo.build_demo_cases(AS_OF), AS_OF).usd_flow(seller, "loan", 500_000, 87_000, 6)
    assert flow is not None and len({t.booked_on.isocalendar()[1] for t in flow.transactions}) >= 13
    assert any(t.description == "AMALend loan repayment" for t in flow.transactions)

# tests\console\test_usd_views.py
def test_usd_only_seller_is_listed_and_accepted_by_apply():
    page = client.get("/borrower").text
    assert "Mapo Snack Box" in page
    assert client.get("/apply?applicant_id=seller-snackbox-22").status_code == 200
```

기존 사후 원인 탐지 테스트(dailylook·gearstore·linen·tangerine)는 그대로 둔다.

- [ ] **Step 2: 실행해 실패 확인**

Run: `.venv\Scripts\python.exe -m pytest tests\mockapis\test_usd_sellers.py tests\console\test_usd_views.py -q`
Expected: 새 테스트 FAIL

- [ ] **Step 3: 구현과 문서.** `docs\agents.md`의 정책 판정 설명과 ADR-003의 `cash_only` 항목에 다음 세 가지를 짧게 더한다.
  - `usd-v2` 규칙
  - 상품 → 현행 규정 매핑(`USD_CURRENT_RULES`)
  - 규정별 은행만 범위(`alt_data_features`)와 비교 문구

- [ ] **Step 4: 전체 테스트**

Run: `.venv\Scripts\python.exe -m pytest -q -x`
Expected: PASS(기존 실패가 있으면 `main`에서도 같은지 확인하고 보고한다)

- [ ] **Step 5: 커밋**

```powershell
git add src tests docs
git commit -m "feat: wire USD-only scene sellers through mock API, console and post-loan"
```

---

### Task 9: 배포와 운영 장면 실행

**Files:**
- Modify(세션 폴더, 저장소 밖): `files\scenario-pr6.py` → 장면 셀러와 `SCENE_REQUEST_CENTS`를 받도록 확장
- Modify: `docs\runbook\deploy-history.md` (배포 기록 한 절)

- [ ] **Step 1: PR.** 브랜치를 push하고 PR을 연다. CI가 통과해야 한다.

- [ ] **Step 2: 좁은 창으로 배포.** 순서는 이렇다. 각 공개 창은 `finally`에서 닫는다.
  1. 오케스트레이터(계약 소비자)
  2. 콘솔
  3. 모의 API(새 셀러)
  4. 정책 Function App(이때부터 `usd-v2` 판정)

  정책을 마지막에 올리는 이유는 Review Focus 1이다.

- [ ] **Step 3: 운영 확인.** 정책 `/healthz`가 응답하는지 본다. 콘솔에서 기존 `usd-v1` 판정 하나를 열어 추적과 "Cash flow only" 문구가 그대로인지 본다.

- [ ] **Step 4: 장면 신청.**
  - O1, O2, O3, O5, O6, R1, R2, R3 순서로, 90초 이상 간격을 두고 신청한다.
  - 판정이 장면 표와 같은지 확인한다.
  - 승인되는 장면(O1, O2, O6)은 심사역 승인 후 사후 13주까지 돌린다.

- [ ] **Step 5: 기록과 보고.** `deploy-history.md`에 커밋, 배포 순서, 장면별 신청 ID·판정을 적고 커밋한다. 사용자에게는 다음을 보고한다.
  - 장면별 콘솔 링크
  - 바꾼 수치와 이유(O2 가을 캠핑용품 전환 포함)
  - O6 포함 여부
  - v2에서 은행만 판정이 바뀐 기존 셀러: candle과 ceramic이 R107 제외로 적격이 된다
