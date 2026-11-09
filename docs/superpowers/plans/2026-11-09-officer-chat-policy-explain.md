# 심사 도우미 정책 판정 설명 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**목표:** 심사 도우미 스냅샷에 적용 규정 원문(`policy.rules`)과 이 건의 계산 과정(`policy.trace`)을 넣어 "구간 기준·한도 계산·규칙 미적용 이유" 질문에 출처를 밝혀 답하게 한다.

**아키텍처:** 정책 엔진에 `explain()`을 추가하고 `evaluate()`는 그 결과의 판정을 돌려준다(한 코드 경로). 콘솔 이미지가 `policy`의 순수 모듈(`__init__`·`rules`·`engine`)과 `rulesets/`를 함께 담고, 채팅 스냅샷을 만들 때 `rules_version`으로 규정을 읽어 저장된 특성으로 다시 계산한다. 재계산이 저장된 판정과 다르면 계산 과정을 싣지 않는다.

**기술 스택:** Python 3.12, pydantic 2, PyYAML 6.0.3, FastAPI 콘솔, Foundry 프롬프트 에이전트(`gpt-6-luna`), Azure Container Apps.

**Spec:** `docs/superpowers/specs/2026-11-09-officer-chat-policy-explain-design.md`

## Global Constraints

- 기존 `evaluate()` 결과와 `/v1/decisions` 응답은 바뀌지 않는다(기존 `tests/policy` 그대로 통과).
- 콘솔에서 정책 코드는 설명용으로만 쓴다. 판정을 저장하거나 바꾸지 않는다.
- 콘솔 이미지에 `src/policy/app.py`·`main.py`는 복사하지 않는다.
- `requirements-console.txt`에 `PyYAML==6.0.3`(`requirements-api.txt`와 같은 버전).
- 출처 표기: 규정 원문은 "(정책 규정 <버전>)", 이 건의 계산은 "(정책 계산)".
- 구간별 실제 금리·기준값을 정한 이유는 계속 "자료에 없음".
- 가명화 원칙 유지: 규정과 계산 과정에는 신청 ID 말고 식별자가 없다.
- `_fit`은 `policy`를 줄이지 않는다. 스냅샷 상한 `SNAPSHOT_MAX_CHARS = 24_000` 그대로.
- 문서·주석·지시문은 한국어.

## Review Focus

1. **v1·v2로 판정된 옛 신청:** 선택 지표(`revenue_trend_ratio` 등)가 없어도 trace가 만들어지고 판정과 일치해야 한다 → Task 1 `test_explain_matches_evaluate_across_versions`, Task 2 `test_snapshot_trace_for_v1_decision`.
2. **저장 특성이 계약과 다름(키 누락·옛 스키마):** 채팅이 깨지지 않고 `trace=null`, `trace_note`=특성 문구 → Task 2 `test_snapshot_trace_null_when_features_invalid`.
3. **모르는/빈 `rules_version`:** `rules=null`, `trace=null`, 버전 문구 → Task 2 `test_snapshot_unknown_version`.
4. **재계산 불일치(판정 뒤 특성이 바뀐 경우 등):** 틀린 근거를 주지 않는다 → Task 2 `test_snapshot_trace_null_on_mismatch`.
5. **스냅샷이 상한을 넘을 때:** 주장·월 데이터는 줄어도 `policy.rules`·`trace`는 그대로 → Task 2 `test_fit_never_trims_policy`.

---

### Task 1: 엔진 `explain()`과 규정 버전 조회

**Files:**
- Modify: `src/policy/engine.py`
- Modify: `src/policy/rules.py`
- Test: `tests/policy/test_engine.py`, `tests/policy/test_rules.py`

**Interfaces:**
- Produces (`policy.engine`, 모두 `@dataclass(frozen=True)`):
  - `RuleCheck(code: str, message: str, kind: Literal["knockout", "refer", "engine"], field: str, op: str, observed: float | None, threshold: float, result: Literal["hit", "pass", "not_applied"])`
  - `LimitTrace(requested_amount_krw: int, avg_monthly_inflow_krw: int, inflow_multiplier: float, inflow_cap_krw: int, product_max_krw: int, product_min_krw: int, before_rounding_krw: int, unit_krw: int, limit_krw: int)`
  - `BandTrace(dscr: float, bands: tuple[tuple[str, float], ...], band: str | None)`
  - `Explanation(rules_version: str, checks: tuple[RuleCheck, ...], limit: LimitTrace, band: BandTrace, decision: PolicyDecision)`
  - `explain(features: CashFlowFeatures, rules: RuleSet) -> Explanation`
  - `evaluate(features, rules) -> PolicyDecision` = `explain(features, rules).decision`
- Produces (`policy.rules`): `rules_for_version(version: object) -> RuleSet | None`

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/policy/test_engine.py`에 추가)

```python
@pytest.mark.parametrize("name", ["v1.yaml", "v2.yaml", "v3.yaml"])
@pytest.mark.parametrize("kw", [{}, {"business_age_months": 11}, {"months_of_history": 5}, {"dscr": 0.99}])
def test_explain_matches_evaluate_across_versions(make_features, name, kw):
    rules = load_rules(RULESETS / name)
    f = make_features(**kw)
    e = explain(f, rules)
    assert e.decision == evaluate(f, rules)
    hits = [c.code for c in e.checks if c.result == "hit"]
    assert sorted(hits) == sorted(r.code for r in e.decision.reason_codes)

def test_explain_checks_cover_every_rule_plus_engine_codes(make_features, rules):
    e = explain(make_features(), rules)
    assert [c.code for c in e.checks] == [c.code for c in (*rules.knockouts, *rules.refers)] + ["R900", "R901"]
    assert {c.kind for c in e.checks if c.code in ("R900", "R901")} == {"engine"}

def test_explain_missing_optional_metric_is_not_applied(make_features, rules):
    e = explain(make_features(revenue_trend_ratio=None), rules)
    r106 = next(c for c in e.checks if c.field == "revenue_trend_ratio")
    assert r106.result == "not_applied" and r106.observed is None

def test_explain_limit_and_band_trace(make_features, rules):
    e = explain(make_features(requested_amount_krw=9_000_000, avg_monthly_inflow_krw=3_456_789), rules)
    lim = e.limit
    assert lim.inflow_cap_krw == int(3_456_789 * rules.product.inflow_multiplier)
    assert lim.before_rounding_krw == min(9_000_000, lim.inflow_cap_krw, rules.product.max_amount_krw)
    assert lim.unit_krw == 100_000 and lim.limit_krw == lim.before_rounding_krw - lim.before_rounding_krw % 100_000
    assert e.band.bands == tuple((b.band, b.min_dscr) for b in rules.rate_bands) and e.band.band == "B"

def test_explain_r900_r901_hit(make_features, rules):
    e = explain(make_features(avg_monthly_inflow_krw=0, dscr=0.5), rules)
    by = {c.code: c for c in e.checks}
    assert by["R900"].result == "hit" and by["R900"].threshold == rules.product.min_amount_krw
    assert by["R901"].result == "hit" and by["R901"].threshold == rules.rate_bands[-1].min_dscr
```

`RULESETS = Path(policy.rules.__file__).parent / "rulesets"`. `tests/policy/test_rules.py`에 추가:

```python
def test_rules_for_version_reads_every_ruleset():
    for v in ("2026.11-v1", "2026.11-v2", "2026.11-v3"):
        assert rules_for_version(v).version == v
    assert rules_for_version("2026.10-v0") is None and rules_for_version(None) is None
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests\policy -q`
Expected: `ImportError: cannot import name 'explain'` 등으로 FAIL

- [ ] **Step 3: 구현**
  - `engine.py`: `_check`를 `RuleCheck`를 돌려주는 함수로 바꾸고(값 None → `not_applied`, 연산 참 → `hit`, 아니면 `pass`), `explain()`이 규칙 순서(knockouts → refers → R900 → R901)로 `checks`를 만든다. R900은 `field="max_amount_krw"`, `op="lt"`, observed=한도, threshold=상품 최소. R901은 `field="dscr"`, `op="lt"`, threshold=마지막 구간 `min_dscr`, 메시지는 기존 문구 그대로. `decision`의 `reason_codes` 순서는 기존과 같다: hit한 knockouts → R900 → R901 → hit한 refers. `ReasonCode`의 `observed`는 기존처럼 `float`.
  - `rules.py`: `rulesets/*.yaml`을 모두 `load_rules`로 읽어 `{version: RuleSet}`을 만드는 `functools.cache` 함수 위에 `rules_for_version`을 둔다. 문자열이 아니거나 없는 버전이면 `None`.

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests\policy tests\mockapis tests\evals tests\console\test_assessment.py -q`
Expected: 모두 PASS(기존 테스트 포함)

- [ ] **Step 5: 커밋** — `feat(policy): explain()과 규정 버전 조회 추가`

---

### Task 2: 채팅 스냅샷에 규정·계산 과정 추가

**Files:**
- Modify: `src/console/officer_chat.py`
- Test: `tests/console/test_officer_chat.py`

**Interfaces:**
- Consumes: Task 1의 `explain`, `rules_for_version`, `Explanation`; `agents.features.ANNUAL_RATE`; `shared.contracts.CashFlowFeatures`.
- Produces: 스냅샷 `policy` 키에 `rules`, `trace`, `trace_note` 추가(항상 존재, 값은 dict 또는 `None`). `cashflow.capacity.assumed_annual_rate: float`. 모듈 상수 `TRACE_NOTES: dict[str, str]`.

`policy.rules` 형태:

```python
{"version": str, "product": {"min_amount_krw", "max_amount_krw", "inflow_multiplier", "limit_unit_krw"},
 "knockouts": [{"code", "message", "field", "op", "threshold"}], "refers": [...같은 키...],
 "rate_bands": [{"band", "min_dscr"}]}
```

`policy.trace` 형태: `dataclasses.asdict(explanation)`에서 `decision`과 `rules_version`을 뺀 `{"checks": [...], "limit": {...}, "band": {...}}`(`bands`는 `[{"band", "min_dscr"}]` 목록으로 바꾼다).

`TRACE_NOTES`(문구 고정):

```python
TRACE_NOTES = {
    "unknown_version": "적용 규정 버전을 콘솔에서 찾을 수 없어 계산 과정을 만들지 않았습니다.",
    "invalid_features": "저장된 특성 값이 불완전해 계산 과정을 만들지 않았습니다.",
    "mismatch": "다시 계산한 결과가 저장된 판정과 달라 계산 과정을 싣지 않았습니다.",
}
```

- [ ] **Step 1: 실패하는 테스트 작성**

테스트 헬퍼: `CashFlowFeatures(...)`(tests/policy/conftest.py와 같은 기본값, `application_id="app-1"`)로 특성을 만들고 `evaluate(f, rules_for_version(v))`로 판정을 만든 뒤 `make_doc(underwriting=uw | {"features": f.model_dump(mode="json")}, policy=d.model_dump(mode="json"))`로 문서를 만든다.

```python
def test_snapshot_policy_rules_and_trace(make_doc, key):          # v3, refer 1건(business_age_months=11)
    p = build_snapshot(_view(...))["policy"]
    assert p["rules"]["version"] == "2026.11-v3" and p["rules"]["product"]["limit_unit_krw"] == 100_000
    assert [b["band"] for b in p["rules"]["rate_bands"]] == ["A", "B", "C", "D"]
    assert p["trace_note"] is None
    assert {c["code"] for c in p["trace"]["checks"] if c["result"] == "hit"} == {"R104"}
    assert p["trace"]["band"]["band"] == p["rate_band"] and p["trace"]["limit"]["limit_krw"] == p["max_amount_krw"]

def test_snapshot_trace_for_v1_decision(make_doc, key):            # v1 판정, 선택 지표 없음
    assert build_snapshot(_view(...))["policy"]["trace"] is not None

def test_snapshot_unknown_version(make_doc, key):                  # rules_version="2026.10-v0"
    p = ...; assert p["rules"] is None and p["trace"] is None and p["trace_note"] == TRACE_NOTES["unknown_version"]

def test_snapshot_trace_null_when_features_invalid(make_doc, key):  # conftest 기본 문서(특성 3개뿐, v1)
    p = ...; assert p["rules"] is not None and p["trace"] is None and p["trace_note"] == TRACE_NOTES["invalid_features"]

def test_snapshot_trace_null_on_mismatch(make_doc, key):           # 저장 판정의 max_amount_krw를 100_000 줄임
    p = ...; assert p["trace"] is None and p["trace_note"] == TRACE_NOTES["mismatch"]

def test_snapshot_capacity_states_assumed_rate(make_doc, key):
    assert build_snapshot(_view(make_doc, key))["cashflow"]["capacity"]["assumed_annual_rate"] == ANNUAL_RATE

def test_fit_never_trims_policy(make_doc, key):                     # 주장 텍스트를 키워 상한 초과
    snap = build_snapshot(...); assert snap["truncated"] is True
    assert snap["policy"] == build_snapshot(<같은 문서, 주장 없음>)["policy"]
```

기존 `test_snapshot_keeps_whitelisted_sections`와 `test_snapshot_capacity_matches_detail_panel`의 `capacity == {...}` 비교에 `"assumed_annual_rate": ANNUAL_RATE`를 더한다.

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests\console\test_officer_chat.py -q`
Expected: `KeyError: 'rules'`, `ImportError: TRACE_NOTES` 등으로 FAIL

- [ ] **Step 3: 구현** — `_policy(policy, features)`로 시그니처를 바꾼다.
  - 규정: `rules_for_version(policy.get("rules_version"))`. 없으면 `rules=None`, `trace=None`, `trace_note=TRACE_NOTES["unknown_version"]`.
  - 특성: `CashFlowFeatures.model_validate_json(json.dumps(features))`. `pydantic.ValidationError`(그리고 `TypeError`·`ValueError`)면 `invalid_features`.
  - 일치 검사: 재계산 판정의 `decision.value`·`max_amount_krw`·`rate_band`·사유 코드 집합이 저장 판정과 모두 같아야 trace를 싣는다. 다르면 `mismatch`.
  - `build_snapshot`은 `_policy(view.get("policy"), features)`를 부르고 `capacity`에 `assumed_annual_rate=ANNUAL_RATE`를 더한다. `_fit`은 바꾸지 않는다(정책을 건드리지 않음을 테스트가 고정).

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests\console -q`
Expected: 모두 PASS

- [ ] **Step 5: 커밋** — `feat(console): 채팅 스냅샷에 정책 규정·계산 과정 추가`

---

### Task 3: 지시문·콘솔 이미지·문서

**Files:**
- Modify: `src/agents/officer_chat.py` (`INSTRUCTIONS`)
- Modify: `src/console/Dockerfile`, `requirements-console.txt`
- Modify: `src/console/assessment.py:1-4` (모듈 설명), `docs/agents.md`(심사역 결정 채팅 절)
- Test: `tests/agents/test_officer_chat.py`, `tests/console/test_dockerfile.py`

**Interfaces:**
- Consumes: Task 2 스냅샷 키 `policy.rules`·`policy.trace`·`policy.trace_note`·`cashflow.capacity.assumed_annual_rate`.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_instructions_explain_policy_sources():
    for phrase in ("(정책 규정", "(정책 계산)", "trace_note", "not_applied", "assumed_annual_rate", "구간별 실제 금리"):
        assert phrase in INSTRUCTIONS
```

`test_dockerfile_copies_everything_the_console_imports`에 추가:

```python
    assert (app / "policy" / "engine.py").exists() and (app / "policy" / "rulesets" / "v3.yaml").exists()
    assert not (app / "policy" / "app.py").exists() and not (app / "policy" / "main.py").exists()
```

새 테스트:

```python
def test_console_pins_same_pyyaml_as_policy_api():
    pin = lambda f: [l for l in (ROOT / f).read_text(encoding="utf-8").splitlines() if l.startswith("PyYAML==")]
    assert pin("requirements-console.txt") == pin("requirements-api.txt") == ["PyYAML==6.0.3"]
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests\agents\test_officer_chat.py tests\console\test_dockerfile.py -q`
Expected: 새 테스트 3개 FAIL(이미지 재현본에서 `import console.app`이 `policy` 없음으로 실패할 수도 있음)

- [ ] **Step 3: 구현**
  - `INSTRUCTIONS`: 출처 줄을 `(현금흐름), (정산 대사), (정책 R106처럼 규칙 코드), (정책 규정 2026.11-v3처럼 규정 버전), (정책 계산), (에이전트 의견)`으로 바꾸고 다음 세 줄을 더한다.
    - `- snapshot.policy.rules는 적용된 규정 원문, policy.trace는 이 건의 규칙별 실제값·기준값·결과(hit 걸림, pass 통과, not_applied 값이 없어 적용 안 함), 한도 산식, 금리 구간 결정이다. 규정·한도·구간 질문은 이것으로 답한다.`
    - `- policy.trace가 null이면 trace_note를 그대로 알리고 계산 과정을 추측하지 않는다.`
    - `- 금리 구간은 DSCR로 정한 등급이며 구간별 실제 금리와 기준값을 정한 이유는 자료에 없다. 월 상환액은 cashflow.capacity.assumed_annual_rate(연 가정 금리)로 계산한 값임을 밝힌다.`
  - `Dockerfile`: `COPY src/agents ...` 다음에 `COPY src/policy/__init__.py src/policy/rules.py src/policy/engine.py /app/policy/`와 `COPY src/policy/rulesets /app/policy/rulesets`. 상단 주석에 정책 순수 모듈만 복사한다는 한 줄.
  - `requirements-console.txt`: `PyYAML==6.0.3` 추가.
  - `assessment.py` 설명: "신호등 기준값은 상수로 두고 테스트가 `load_rules()`와 같은지 고정한다. 정책 패키지는 채팅 설명(`console.officer_chat`)에서만 쓴다."는 취지로 고친다.
  - `docs/agents.md` 심사역 결정 채팅 절 입력 항목에 `policy.rules`(규정 원문)·`policy.trace`(계산 과정, 재계산이 저장 판정과 다르면 싣지 않음)·가정 금리를, 가드레일에 두 출처 표기와 "구간별 금리는 자료에 없음"을 적는다.

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests\agents tests\console tests\policy tests\docs -q`
Expected: 모두 PASS

- [ ] **Step 5: 커밋** — `feat(console): 심사 도우미 정책 설명 지시문·이미지 반영`

---

### Task 4: 배포와 라이브 확인

**Files:**
- Modify: `docs/runbook/deploy-history.md`

- [ ] **Step 1: 전체 테스트** — `.\.venv\Scripts\python -m pytest -q` → 실패 0.
- [ ] **Step 2: 배포 창 열기** — `.\.venv\Scripts\python -m ops window open --acr` → 성공.
- [ ] **Step 3: 콘솔 배포** — `.\.venv\Scripts\python -m ops deploy console --acr` → 새 이미지 태그 기록. 스모크가 시간 초과면 `agw-amalend`의 `operationalState`를 보고 `Stopped`면 `az network application-gateway start` 후 `ops smoke`.
- [ ] **Step 4: 콘솔 IP 확인** — provision이 `allowOperatorOnly`를 `CONSOLE_IP`만 남기므로, 브라우저 IP(167.220.232.89 등)가 빠졌으면 다시 더한다. `/`·`/loans` 200.
- [ ] **Step 5: 라이브 4문항** — 세션 파일 `officer_chat_live.py`의 질문을 다음으로 바꿔 실행: "금리 구간 기준은?", "한도는 어떻게 계산됐나?", "R102는 왜 안 걸렸나?", "구간별 금리는?". 앞의 세 답은 "(정책 규정"·"(정책 계산)" 출처와 규정과 맞는 수치, 마지막은 "자료에 없음". 콘솔 로그에 질문 원문이 없는지 확인.
- [ ] **Step 6: 배포 창 닫기** — `.\.venv\Scripts\python -m ops window close`.
- [ ] **Step 7: 배포 기록** — 이미지 태그·라이브 결과를 `docs/runbook/deploy-history.md`에 한 항목으로 적는다.
- [ ] **Step 8: 커밋·푸시** — `docs: 심사 도우미 정책 설명 배포 기록`, `origin main`으로 푸시(spec 커밋 포함).
