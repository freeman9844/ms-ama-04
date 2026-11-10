# 정산 대사 해설 보강과 에이전트 정산 확인 사항 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 정산 원장에서 결정적 지표 3종(선정산 추정·현금화 일수·광고비 상계율 추이)을 만들고, 분석가가 정산 근거로 심사역 확인 사항(`officer_checks`)을 내며 검토자가 검증하고, 콘솔 정산 대사 카드와 심사 도우미가 이를 보여 준다.

**Architecture:** 값은 `agents/pg_ledger.ledger_summary` 한 곳에서만 계산하고 `features.py`가 숫자 신호로 넘겨 `build_catalog`가 `M:pg_*` 근거 ID를 자동으로 만든다. `CreditAssessment.officer_checks`(기본 빈 튜플)를 추가하고 분석가 스키마·`check_grounding`·검토자 `check_verdicts`·`_to_assessment`가 claims와 같은 반성 루프로 다룬다. 콘솔은 저장된 값만 읽어 정산 카드(KPI 5개·해설·2색 공제율·추정 원인·확인 사항)를 그린다.

**Tech Stack:** Python 3.12, Pydantic v2, FastAPI + Jinja2(콘솔), pytest, Azure AI Foundry Agents(Responses strict 스키마), Bicep 배포는 변경 없음(`ops` CLI로 이미지 배포).

**Spec:** `docs/superpowers/specs/2026-11-10-settlement-insights-officer-checks-design.md`

## Global Constraints

- 정책 v3·`CashFlowFeatures`·정책 엔진·Mock API·오케스트레이터 코드는 바꾸지 않는다.
- 정산 값은 `pg_ledger`에서만 계산한다. 콘솔·에이전트가 다시 계산하지 않는다.
- LLM으로 가는 정산 정보는 집계만: `unmatched[]`·`advance` 세부는 `get_settlement_summary`·심사역 채팅 스냅샷에 넣지 않는다.
- `OfficerCheck.text` 1~200자, `metric_ids` 1개 이상, `officer_checks` 최대 3개, `schema_version`은 "1" 유지.
- 선정산 짝 조건: 금액 > 0, 플랫폼 정산 입금 아님(`_platform_of(t) is None`), 매칭에 쓰이지 않음, `settled_on − 10일 ≤ booked_on ≤ settled_on`(달력일), `0.75 × net_krw ≤ amount_krw ≤ 0.95 × net_krw`, 가장 이른 것, 한 입금은 한 번만.
- `pg_advance_share`·`pg_reconciliation_ratio` 등 비율은 소수 셋째 자리, `pg_cash_conversion_days`는 소수 첫째 자리, 추이(pp)는 기존 `_trend_pp`(소수 둘째 자리).
- `PLATFORM_LAG_DAYS = {"스마트스토어": 8, "쿠팡": 32, "11번가": 17, "자사몰": 3}`(달력일, `channels.py` 일정에서 유도한 근거를 주석으로 남긴다).
- 정산 계열 근거 = `M:pg_`로 시작하거나 `P:R107:`·`P:R108:`로 시작하는 ID.
- 확인 사항은 승인·거절·금리·한도를 지시하지 않는다. `lag_days`·`fee_rate`·`offset_rate` 숫자는 인용 금지. "선정산" 같은 일반 명사만 쓴다.
- 콘솔: 인라인 스크립트·스타일 금지, 기존 매크로(`_charts.html`)·CSS 클래스 재사용. 옛 문서(새 키 없음)도 오류 없이 그린다.
- 옛 문서 안내 문구: "2026-11-10 이전 심사분은 확인 사항이 없습니다".
- 테스트 명령: `$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python -m pytest <경로> -q -p no:cacheprovider`.
- `ops eval`은 실행하지 않는다(별도 승인 필요).
- 커밋: 메시지를 `Out-File -Encoding utf8NoBOM` 임시 파일로 쓰고 `git commit -F`, 트레일러 `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`·`Copilot-Session: 05f1ca1b-6073-417d-bfb1-6b546f82cb58`.

## Review Focus

1. 마지막 라운드 초안이 grounding에서 막혀 검토를 받지 못하면 각 확인 사항의 `supported`는 이전 라운드 판정이 아니라 None이어야 한다 → Task 4 `test_check_verdicts_come_from_review_of_final_draft`.
2. 계약 검증에 실패해 버려진 확인 사항이 있어도 남은 항목의 `supported`는 원래 `check_index`의 판정이어야 한다(인덱스 밀림 금지) → Task 4 `test_dropped_check_does_not_shift_verdicts`.
3. 한 선지급 입금이 같은 금액·같은 날의 미매칭 정산 두 건과 모두 짝지어질 수 있으면 한 번만 쓴다 → Task 1 `test_advance_deposit_used_once`.
4. 옛 원장(`lag_days`·`fee_rate`·`advance` 없음)과 옛 평가서(`officer_checks` 키 없음)로 상세 화면을 열면 KPI는 "—", 공제율은 단색, 추정 원인은 "—", 확인 사항은 옛 문서 안내가 나온다 → Task 6 `test_settlement_card_legacy_ledger`·`test_ui` 옛 문서 테스트.
5. 정상 데모 셀러에게 선정산 추정이 붙으면 안 된다(오탐) → Task 1 `test_demo_sellers_advance_detection`. 실패하면 임계값을 몰래 바꾸지 말고 멈추고 보고한다.

---

### Task 1: 정산 지표 3종과 신호 연결

**Files:**
- Modify: `src/agents/pg_ledger.py`(상수, `ledger_summary`, docstring)
- Modify: `src/agents/features.py:244-249`(signals)
- Test: `tests/agents/test_pg_ledger.py`, `tests/agents/test_features.py`

**Interfaces:**
- Produces: `ledger_summary(...)` 결과에 새 키
  - `pg_advance_suspected_count: int`, `pg_advance_share: float | None`, `pg_cash_conversion_days: float | None`, `pg_offset_rate_trend_pp: float | None`
  - `platforms[i]["lag_days"]: int | None`
  - `monthly_take_rate[i]` = `{"month", "rate", "fee_rate", "offset_rate"}`(모두 소수 넷째 자리)
  - `unmatched[i]["advance"]` = `{"booked_on": "YYYY-MM-DD", "amount_krw": int, "lead_days": int}` 또는 None
- Produces: 상수 `PLATFORM_LAG_DAYS: dict[str, int]`, `ADVANCE_WINDOW_DAYS = 10`, `ADVANCE_RATIO = (0.75, 0.95)`
- Produces: `Underwriting.signals`에 `pg_cash_conversion_days`, `pg_offset_rate_trend_pp`, `pg_advance_share`, `pg_advance_suspected_count`, `pg_unmatched_count`(= `unmatched_count`) — 값이 None이 아닐 때만

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/agents/test_pg_ledger.py`, 기존 `_s`·`_t` 헬퍼 재사용, `import pytest` 추가)

```python
def test_advance_pairs_unmatched_settlement_with_earlier_partial_deposit():
    s = [_s(date(2026, 10, 13), "쿠팡", 1000)]
    t = [_t(date(2026, 10, 7), 850, "한결선정산 선지급", code="TRANSFER_IN")]
    r = ledger_summary(s, t, AS_OF)
    assert r["pg_advance_suspected_count"] == 1 and r["pg_advance_share"] == 1.0
    assert r["unmatched"][0]["advance"] == {"booked_on": "2026-10-07", "amount_krw": 850, "lead_days": 6}


@pytest.mark.parametrize("day, amount, desc", [
    (date(2026, 10, 2), 850, "선지급"),           # 11일 전 → 창 밖
    (date(2026, 10, 14), 850, "선지급"),          # 정산일 뒤
    (date(2026, 10, 7), 960, "선지급"),           # 0.96
    (date(2026, 10, 7), 740, "선지급"),           # 0.74
    (date(2026, 10, 7), 850, "쿠팡(주) 주정산"),  # 플랫폼 입금
])
def test_advance_rejects_out_of_window_ratio_or_platform(day, amount, desc):
    r = ledger_summary([_s(date(2026, 10, 13), "쿠팡", 1000)], [_t(day, amount, desc, code="TRANSFER_IN")], AS_OF)
    assert r["pg_advance_suspected_count"] == 0 and r["unmatched"][0]["advance"] is None


def test_advance_deposit_used_once():
    s = [_s(date(2026, 10, 13), "쿠팡", 1000), _s(date(2026, 10, 13), "쿠팡", 1000)]
    r = ledger_summary(s, [_t(date(2026, 10, 8), 850, "선지급", code="TRANSFER_IN")], AS_OF)
    assert r["pg_advance_suspected_count"] == 1 and r["pg_advance_share"] == 0.5


def test_advance_share_none_without_unmatched():
    s = [_s(date(2026, 10, 13), "쿠팡", 1000)]
    r = ledger_summary(s, [_t(date(2026, 10, 13), 1000, "쿠팡(주) 주정산")], AS_OF)
    assert r["pg_advance_suspected_count"] == 0 and r["pg_advance_share"] is None


def test_cash_conversion_days_weighted_and_renormalised():
    def run(rows):
        return ledger_summary(rows, [], AS_OF)
    r = run([_s(date(2026, 10, 13), "쿠팡", 600), _s(date(2026, 10, 13), "스마트스토어", 400)])
    assert r["pg_cash_conversion_days"] == 22.4  # 0.6×32 + 0.4×8
    assert {p["platform"]: p["lag_days"] for p in r["platforms"]} == {"쿠팡": 32, "스마트스토어": 8}
    r = run([_s(date(2026, 10, 13), "쿠팡", 1000), _s(date(2026, 10, 13), "기타몰", 1000)])
    assert r["pg_cash_conversion_days"] == 32.0
    assert next(p for p in r["platforms"] if p["platform"] == "기타몰")["lag_days"] is None
    assert run([_s(date(2026, 10, 13), "기타몰", 1000)])["pg_cash_conversion_days"] is None
```

`test_concentration_take_rate_trend_and_features`에 추가:

```python
    may = r["monthly_take_rate"][0]
    assert (may["fee_rate"], may["offset_rate"], may["rate"]) == (0.08, 0.005, 0.085)
    assert r["pg_offset_rate_trend_pp"] == 1.5  # 상계 0.5~3.0%: (2.0+2.5+3.0)/3 − (0.5+1.0+1.5)/3
```

`tests/agents/test_features.py`에 추가(기존 `cases`·`_underwrite` 사용):

```python
def test_pg_signals_mirror_ledger(cases):
    uw = _underwrite(cases, "seller-dailylook-01")
    assert uw.signals["pg_unmatched_count"] == uw.pg_ledger["unmatched_count"]
    for k in ("pg_cash_conversion_days", "pg_offset_rate_trend_pp", "pg_advance_share", "pg_advance_suspected_count"):
        assert (k in uw.signals) == (uw.pg_ledger[k] is not None)
        if k in uw.signals:
            assert uw.signals[k] == uw.pg_ledger[k]


def test_demo_sellers_advance_detection(cases):
    for applicant_id in cases:
        pg = _underwrite(cases, applicant_id).pg_ledger
        if applicant_id == "seller-mugshop-21":
            assert pg["pg_advance_suspected_count"] >= 1 and pg["pg_advance_share"] >= 0.8
        elif pg.get("available"):
            assert pg["pg_advance_suspected_count"] == 0, applicant_id
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests/agents/test_pg_ledger.py tests/agents/test_features.py -q -p no:cacheprovider`
Expected: 새 테스트가 `KeyError: 'pg_advance_suspected_count'` 등으로 FAIL

- [ ] **Step 3: `ledger_summary` 확장**

기존 매칭 루프 뒤에서 계산한다. 선정산 후보는 `transactions` 중 금액 > 0·`_platform_of(t) is None`·매칭 `used`에 없는 입금을 `(booked_on, txn_id)` 순으로 정렬해 두고, `unmatched`를 `settled_on` 오름차순으로 돌며 조건(Global Constraints)을 만족하는 첫 미사용 입금을 짝짓는다(`advance_used` 집합). `lead_days = (settled_on − booked_on).days`. 짝 dict는 정산 객체 기준으로 보관해 `unmatched[]` 최근 10건 직렬화 때 붙인다. `by_month`를 `[gross, fee, deduction]` 세 칸으로 바꿔 `fee_rate`·`offset_rate`·`rate`(=두 값 합의 원 비율)를 각각 `round(…, 4)`로 내고, `pg_take_rate_trend_pp`는 기존처럼 합계 원 비율로, `pg_offset_rate_trend_pp`는 상계 원 비율 목록으로 `_trend_pp`를 호출한다. 현금화 일수는 `PLATFORM_LAG_DAYS`에 있는 플랫폼의 `gross`만으로 가중 평균한다. 모듈 docstring과 함수 docstring의 키 목록을 갱신한다.

- [ ] **Step 4: features 신호 추가**

`features.py`의 signals 사전 마지막 줄을 새 키까지 포함하도록 바꾸고 `"pg_unmatched_count": pg["unmatched_count"]`(available일 때만)를 넣는다.

- [ ] **Step 5: 통과 확인 + 회귀**

Run: `.\.venv\Scripts\python -m pytest tests/agents tests/mockapis -q -p no:cacheprovider`
Expected: 모두 PASS(`test_decision_invariance` 포함 — 정책 판정 불변). `test_demo_sellers_advance_detection`이 정상 셀러에서 실패하면 멈추고 보고한다.

- [ ] **Step 6: 커밋** — `feat(pg_ledger): 선정산 추정·현금화 일수·광고비 상계율 추이 지표`

---

### Task 2: `OfficerCheck` 계약

**Files:**
- Modify: `src/shared/contracts.py`(`Counterfactual` 아래에 `OfficerCheck`, `CreditAssessment`에 필드)
- Test: `tests/shared/test_contracts.py`

**Interfaces:**
- Produces: `OfficerCheck(text: str[1..200], metric_ids: tuple[EvidenceId, ...] (min 1), supported: bool | None = None)`
- Produces: `CreditAssessment.officer_checks: tuple[OfficerCheck, ...] = Field(default=(), max_length=3)`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_officer_check_needs_evidence_and_bounded_text():
    OfficerCheck(text="선정산 계약서를 확인한다.", metric_ids=("M:pg_advance_share",))
    for bad in ({"text": "x", "metric_ids": ()}, {"text": "", "metric_ids": ("M:pg_hhi",)},
                {"text": "가" * 201, "metric_ids": ("M:pg_hhi",)}, {"text": "x", "metric_ids": ("pg_hhi",)}):
        with pytest.raises(ValidationError):
            OfficerCheck(**bad)


def test_credit_assessment_officer_checks_default_and_limit():
    legacy = _assessment().model_dump(mode="json", exclude={"officer_checks"})  # 기존 헬퍼 _assessment(**kw)
    assert CreditAssessment.model_validate(legacy).officer_checks == ()
    check = OfficerCheck(text="확인", metric_ids=("M:pg_hhi",))
    assert _assessment(officer_checks=(check,) * 3).officer_checks[2].supported is None
    with pytest.raises(ValidationError):
        _assessment(officer_checks=(check,) * 4)
```

- [ ] **Step 2: 실패 확인** — `pytest tests/shared/test_contracts.py` → `ImportError: OfficerCheck`
- [ ] **Step 3: 계약 구현**(스펙 §4.2 그대로)
- [ ] **Step 4: 통과 확인** — `pytest tests/shared -q` PASS
- [ ] **Step 5: 커밋** — `feat(contracts): 심사역 확인 사항 OfficerCheck`

---

### Task 3: 분석가 — 스키마·지시문·도구·grounding

**Files:**
- Modify: `src/agents/underwriting.py`(`ANALYST_INSTRUCTIONS`, `ANALYST_TOOLS`의 `get_settlement_summary` 설명, `ANALYST_OUTPUT_SCHEMA`, `DraftCheck`·`AnalystDraft`, `get_settlement_summary`, `check_grounding`)
- Modify: `src/evals/fake.py`(`_analyst_draft`가 `officer_checks` 키를 항상 내도록 — 이 Task에서는 `[]`)
- Test: `tests/agents/test_underwriting.py`(`GOOD`에 `"officer_checks": []` 추가)

**Interfaces:**
- Consumes: Task 1의 새 `M:pg_*` 근거 ID, Task 2 없음
- Produces: `DraftCheck(_Model)`: `text: str`, `metric_ids: list[str]`; `AnalystDraft.officer_checks: list[DraftCheck]`
- Produces: `SETTLEMENT_EVIDENCE_PREFIXES = ("M:pg_", "P:R107:", "P:R108:")`, `MAX_OFFICER_CHECKS = 3`
- Produces: `check_grounding` 지적 문구(테스트가 고정)
  - `officer_checks[{i}]: 근거 ID가 없다`
  - `officer_checks[{i}]: 알 수 없는 근거 ID [...]`
  - `officer_checks[{i}]: 정산 근거(M:pg_*·P:R107·P:R108)가 없다`
  - `officer_checks[{i}]: 인용 근거와 맞지 않는 수치 [...]`
  - `officer_checks: 3개를 넘는다`
  - `officer_checks: 정산 자료가 없는데 확인 사항이 있다`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
CHECK = {"text": "정산금 미입금 원인과 선정산 계약 여부를 확인한다.", "metric_ids": ["M:pg_reconciliation_ratio"]}


def test_analyst_schema_requires_officer_checks():
    assert "officer_checks" in ANALYST_OUTPUT_SCHEMA["required"]
    item = ANALYST_OUTPUT_SCHEMA["properties"]["officer_checks"]["items"]
    assert item["required"] == ["text", "metric_ids"] and item["additionalProperties"] is False
    assert "officer_checks" in ANALYST_INSTRUCTIONS and "정산 건전성" in ANALYST_INSTRUCTIONS
    assert "6~8문장" in ANALYST_INSTRUCTIONS


def test_settlement_summary_exposes_new_metrics_without_details(cases):
    out = context(cases).handlers()["get_settlement_summary"]({})
    assert "M:pg_cash_conversion_days" in out["metric_ids"] and "M:pg_unmatched_count" in out["metric_ids"]
    assert out["platforms"][0].keys() == {"platform", "share", "lag_days"}
    assert {"fee_rate", "offset_rate"} <= out["monthly_take_rate"][0].keys()
    assert "unmatched" not in out and "advance" not in json.dumps(out)


def test_grounded_check_has_no_issues(cases):
    assert check_grounding(draft(officer_checks=[CHECK]), context(cases)) == []


@pytest.mark.parametrize("check, issue", [
    ({"text": "확인", "metric_ids": []}, "officer_checks[0]: 근거 ID가 없다"),
    ({"text": "확인", "metric_ids": ["M:nope"]}, "officer_checks[0]: 알 수 없는 근거 ID"),
    ({"text": "확인", "metric_ids": ["M:dscr"]}, "officer_checks[0]: 정산 근거(M:pg_*·P:R107·P:R108)가 없다"),
    ({"text": "입금 비율 55%를 확인한다.", "metric_ids": ["M:pg_reconciliation_ratio"]},
     "officer_checks[0]: 인용 근거와 맞지 않는 수치"),
])
def test_check_grounding_reports_bad_checks(cases, check, issue):
    issues = check_grounding(draft(officer_checks=[check]), context(cases))
    assert any(i.startswith(issue) for i in issues), issues


def test_check_grounding_limits_count_and_requires_settlement(cases):
    assert "officer_checks: 3개를 넘는다" in check_grounding(draft(officer_checks=[CHECK] * 4), context(cases))
    ctx = replace(context(cases), settlement={"available": False})
    assert "officer_checks: 정산 자료가 없는데 확인 사항이 있다" in check_grounding(draft(officer_checks=[CHECK]), ctx)
```

기존 `test_settlement_summary_tool_returns_ids_and_platforms`의 `platforms` 키 단언을 `{"platform", "share", "lag_days"}`로 바꾼다.

- [ ] **Step 2: 실패 확인** — `pytest tests/agents/test_underwriting.py -q` → `AnalystDraft` 검증 오류·KeyError로 FAIL
- [ ] **Step 3: 스키마·모델 구현** — `ANALYST_OUTPUT_SCHEMA.properties.officer_checks` = 배열, 항목 `{text: string, metric_ids: string[] (description "M:pg_*·P:R107:·P:R108: 근거 ID")}`, 최상위 `required`에 추가. `DraftCheck`·`AnalystDraft.officer_checks`.
- [ ] **Step 4: 지시문 개정**(스펙 §4.3 그대로)
  - 1단계 정산 문장: "get_settlement_summary로 정산금 계좌 입금 비율·채널 집중·공제율 추이·현금화 일수·광고비 상계율 추이·선정산 추정을 확인한다. 인용은 M:pg_* ID 값만 쓰고, 미입금 개별 금액과 플랫폼별 지연 일수·월별 수수료율·상계율 숫자는 문장에 쓰지 않는다."
  - 새 10단계: officer_checks 0~3개 규칙 전문 + 예시 2개("미입금 정산 대부분이 정산일 전 입금과 짝지어지므로 선정산 계약서와 양도 범위를 확인한다." / "정산금 계좌 입금 비율이 기준에 못 미치므로 정산계좌 변경 여부를 확인한다."). "선정산" 일반 명사 사용.
  - 9단계 summary: "6~8문장", (2) 위험 요인 뒤에 "(3) 정산 건전성: 정산금 입금·현금화 일수·상계율·선정산 추정 중 두드러진 점. 정산 자료가 없으면 '정산 자료 없음' 한 문장." 이후 번호를 (4)~(6)으로 민다.
  - 도구 설명에 "현금화 일수는 표준 정산 주기 기준 추정"을 덧붙인다.
- [ ] **Step 5: `get_settlement_summary` 확장** — `ids`에 `M:pg_cash_conversion_days`, `M:pg_offset_rate_trend_pp`, `M:pg_advance_share`, `M:pg_advance_suspected_count`, `M:pg_unmatched_count` 추가(카탈로그에 있는 것만), `platforms`는 `("platform", "share", "lag_days")`, `monthly_take_rate`는 원소 그대로.
- [ ] **Step 6: `check_grounding` 확장** — claims 루프 뒤에 위 지적 문구 규칙. 숫자 검사는 `ungrounded(c.text, [인용 근거 값])`. 정산 자료 판단은 `(ctx.settlement or {}).get("available")`.
- [ ] **Step 7: fake 갱신** — `src/evals/fake.py` `_analyst_draft` 반환 dict에 `"officer_checks": []`.
- [ ] **Step 8: 통과 확인** — `pytest tests/agents tests/evals -q` PASS
- [ ] **Step 9: 커밋** — `feat(underwriting): 분석가 정산 확인 사항 스키마·지시문·grounding`

---

### Task 4: 검토자 — `check_verdicts`와 평가서 변환

**Files:**
- Modify: `src/agents/underwriting.py`(`REVIEWER_INSTRUCTIONS`, `REVIEWER_OUTPUT_SCHEMA`, `CheckVerdict`·`ReviewerOutput`, `_reviewer_input`, `_review_issues`, `assess`, `_to_assessment`)
- Modify: `src/evals/fake.py`(`_review`가 `check_verdicts` 출력)
- Test: `tests/agents/test_underwriting.py`(`SUPPORTED`에 `"check_verdicts": []` 추가)

**Interfaces:**
- Consumes: Task 2 `OfficerCheck`, Task 3 `AnalystDraft.officer_checks`
- Produces: `CheckVerdict(_Model)`: `check_index: int`, `supported: bool`, `reason: str`; `ReviewerOutput.check_verdicts: list[CheckVerdict]`
- Produces: `_to_assessment(draft, ctx, review, models, verdicts: Mapping[int, bool] | None = None) -> CreditAssessment | None` — `verdicts`는 최종 초안을 검토한 판정(check_index → supported). 최종 초안이 검토를 받지 못했으면 None.
- Produces: 지적 문구 `officer_checks[{i}]: 검토 결과 없음`, `officer_checks[{i}]: 검토자 지적 - {reason}`

- [ ] **Step 1: 실패하는 테스트 작성**(기존 `FakeRunner`·`run_assess` 사용)

```python
WITH_CHECK = GOOD | {"officer_checks": [CHECK]}
CHECK_OK = SUPPORTED | {"check_verdicts": [{"check_index": 0, "supported": True, "reason": "근거와 맞다"}]}


def test_reviewer_sees_checks_with_evidence(cases):
    ctx = context(cases)
    payload = json.loads(_reviewer_input(ctx, draft(officer_checks=[CHECK])).split("\n", 1)[1])
    assert payload["officer_checks"] == [{"check_index": 0, "text": CHECK["text"],
                                          "evidence": {"M:pg_reconciliation_ratio": ctx.catalog["M:pg_reconciliation_ratio"]}}]
    assert "check_verdicts" in REVIEWER_OUTPUT_SCHEMA["required"]


def test_supported_check_is_kept_with_verdict(cases):
    result = run_assess(context(cases), FakeRunner([WITH_CHECK], [CHECK_OK]))
    assert result.assessment.review.passed and result.assessment.officer_checks[0].supported is True


def test_rejected_or_missing_check_verdict_triggers_revision(cases):
    bad = SUPPORTED | {"check_verdicts": [{"check_index": 0, "supported": False, "reason": "근거 밖 추론"}]}
    runner = FakeRunner([WITH_CHECK] * 3, [bad, SUPPORTED, CHECK_OK])
    result = run_assess(context(cases), runner)
    rounds = result.record["rounds"]
    assert "officer_checks[0]: 검토자 지적 - 근거 밖 추론" in rounds[0]["review_issues"]
    assert "officer_checks[0]: 검토 결과 없음" in rounds[1]["review_issues"]
    assert result.assessment.review.passed and result.assessment.officer_checks[0].supported is True


def test_unsupported_check_is_shown_not_hidden_after_max_rounds(cases):
    bad = SUPPORTED | {"check_verdicts": [{"check_index": 0, "supported": False, "reason": "과장"}]}
    result = run_assess(context(cases), FakeRunner([WITH_CHECK] * 3, [bad] * 3))
    assert not result.assessment.review.passed and result.assessment.officer_checks[0].supported is False


def test_check_verdicts_come_from_review_of_final_draft(cases):
    ungrounded = WITH_CHECK | {"claims": [{"text": "DSCR은 9.9다.", "metric_ids": ["M:dscr"], "txn_ids": []}]}
    claim_bad = CHECK_OK | {"verdicts": [{"claim_index": 0, "supported": False, "reason": "x"},
                                         {"claim_index": 1, "supported": True, "reason": "x"}]}
    result = assess(context(cases), runner=FakeRunner([WITH_CHECK, ungrounded], [claim_bad]),
                    analyst_model="a", reviewer_model="r", max_rounds=2)
    assert result.assessment.officer_checks[0].supported is None


def test_dropped_check_does_not_shift_verdicts(cases):
    checks = [{"text": "확인", "metric_ids": ["bad id"]}, CHECK]  # 0번은 EvidenceId 패턴 위반으로 버려진다
    a = _to_assessment(draft(officer_checks=checks), context(cases),
                       ReviewOutcome(passed=True, rounds=1), {}, {0: False, 1: True})
    assert [(c.text, c.supported) for c in a.officer_checks] == [(CHECK["text"], True)]
```

- [ ] **Step 2: 실패 확인** — `pytest tests/agents/test_underwriting.py -q` FAIL
- [ ] **Step 3: 스키마·모델·지시문** — `check_verdicts` 배열(항목 `check_index`·`supported`·`reason`, strict)과 `required` 추가. `REVIEWER_INSTRUCTIONS`에 스펙 §4.3 문장과 "모든 check_index에 대해 판정을 하나씩 낸다. 확인 사항이 없으면 빈 배열." 추가.
- [ ] **Step 4: `_reviewer_input`·`_review_issues`** — payload에 `officer_checks: [{check_index, text, evidence}]`(evidence는 카탈로그에 있는 근거 값). `_review_issues`는 claims와 같은 방식(`{v.check_index: v}` 사전, 범위 밖 인덱스는 자연히 무시)으로 `officer_checks[i]` 지적을 claims 지적 뒤, `검토 의견` 앞에 붙인다.
- [ ] **Step 5: `assess`** — 라운드마다 `final_verdicts: dict[int, bool] | None`을 둔다. 분석가가 새 초안을 내면 None으로 되돌리고, 검토자가 그 초안을 검토하면 `{v.check_index: v.supported for v in review.output.check_verdicts}`로 채운다. `_to_assessment(..., final_verdicts)`로 넘긴다.
- [ ] **Step 6: `_to_assessment`** — `enumerate(draft.officer_checks)`로 원래 인덱스를 유지하며 `OfficerCheck(text=c.text[:200], metric_ids=tuple(c.metric_ids), supported=(verdicts or {}).get(i))`; `ValidationError`면 버린다. 3개를 넘으면 앞 3개만. `CreditAssessment(officer_checks=...)`.
- [ ] **Step 7: fake 검토자** — `src/evals/fake.py` `_review`가 payload의 `officer_checks`(없으면 [])마다 `{"check_index", "supported": True, "reason": "근거와 일치"}`를 `check_verdicts`로 낸다.
- [ ] **Step 8: 통과 확인** — `pytest tests/agents tests/evals -q` PASS
- [ ] **Step 9: 커밋** — `feat(underwriting): 검토자 확인 사항 판정과 평가서 반영`

---

### Task 5: 평가 지표 `settlement_check_rate`

**Files:**
- Modify: `src/evals/fake.py`(정산 근거가 있으면 확인 사항 1개)
- Modify: `src/evals/suites/underwriting.py`(행 지표·config 집계·실패 유형)
- Modify: `src/evals/report.py`(조치 문구, 표 열)
- Test: `tests/evals/test_eval_run.py`

**Interfaces:**
- Consumes: Task 4 `CreditAssessment.officer_checks`
- Produces: 행 키 `settlement_check_detected: bool`(ASSIGNED만), config 키 `settlement_check_rate`, 실패 유형 `settlement_check_missed`, 상수 `SETTLEMENT_CHECK_EVIDENCE = ("M:pg_advance_share", "M:pg_reconciliation_ratio", "P:R108")`(기존 `_cites`의 접두 일치 그대로)

- [ ] **Step 1: 실패하는 테스트 작성** — 기존 정산 테스트 3개 옆에:

```python
def test_settlement_check_detected_for_assigned_cases():
    out, model = _assigned_run()
    rows = [r for r in out["cases"] if r["archetype"] == "assigned"]
    assert rows and all(r["settlement_check_detected"] for r in rows)
    assert all("settlement_check_detected" not in r for r in out["cases"] if r["archetype"] != "assigned")
    assert out["configs"][model]["settlement_check_rate"] == 1.0


def test_settlement_check_missed_when_checks_dropped(monkeypatch):
    out, model = _assigned_run(monkeypatch, strip=True)
    assert out["configs"][model]["settlement_check_rate"] == 0.0
    assert [f for f in out["failures"] if f["type"] == "settlement_check_missed"]
```

`_assigned_run`의 `strip` 분기에 `out["officer_checks"] = []`를 추가한다. 리포트 테스트에 `"정산 확인 사항" in report.render_markdown(summary)`와 옛 결과(`del config["settlement_check_rate"]`) → `—` 단언을 추가한다.

- [ ] **Step 2: 실패 확인** — `pytest tests/evals -q` FAIL
- [ ] **Step 3: fake** — `_analyst_draft`: `"M:pg_reconciliation_ratio" in metrics`이면 `officer_checks = [{"text": "정산금 계좌 미입금 원인과 선정산 계약 여부를 확인한다.", "metric_ids": ["M:pg_reconciliation_ratio"]}]`, 아니면 `[]`.
- [ ] **Step 4: suite** — ASSIGNED 행에 `settlement_check_detected = bool(a) and _cites([c.model_dump() for c in a.officer_checks], SETTLEMENT_CHECK_EVIDENCE)`(최종 평가서 기준, 초안 아님); config에 `settlement_check_rate`; 실패 `failure("settlement_check_missed", SUITE, c.case_id, "정산 근거 확인 사항(선정산·입금 비율)을 내지 않음", model)`.
- [ ] **Step 5: report** — `FAILURE_ACTIONS["settlement_check_missed"] = "분석가 지시문의 officer_checks 단계와 get_settlement_summary의 선정산 지표 노출을 점검한다"`; 표 헤더 "정산 차이 탐지" 뒤에 "정산 확인 사항" 열(값 없으면 "—").
- [ ] **Step 6: 통과 확인** — `pytest tests/evals -q` PASS
- [ ] **Step 7: 커밋** — `feat(evals): 정산 확인 사항 탐지율`

---

### Task 6: 콘솔 정산 대사 카드

**Files:**
- Modify: `src/console/views.py:86-93`(`detail_view`에 `officer_checks`)
- Modify: `src/console/dashboard.py:228-252`(`settlement_card` 확장, 상수)
- Modify: `src/console/opinion.py`(`METRIC_LABELS` 5개, `_officer_checks`, `opinion_panel["officer_checks"]`)
- Modify: `src/console/templates/detail.html:97-114`
- Modify: `src/console/static/console.css`(`.grid-5`만 필요 시 추가, 반응형 규칙은 `.grid-4`와 같게)
- Test: `tests/console/test_dashboard.py`, `tests/console/test_opinion.py`, `tests/console/test_ui.py`

**Interfaces:**
- Consumes: Task 1 원장 키, Task 4 평가서 `officer_checks[{text, metric_ids, supported}]`
- Produces: `detail_view(...)["officer_checks"]`: `list[{"text", "evidence": [(id, value|None)], "supported"}] | None` — 평가서가 없거나 `officer_checks` 키가 없으면 None
- Produces: `settlement_card(pg) -> dict` 새 키 `guide: tuple[tuple[str, str], ...]`, `cycles: list[{"platform", "cycle", "lag_days"}]`, `take_rate_kind: "split"|"single"`, `take_rate`(split이면 `charts.columns` 결과, single이면 기존 `hbars`), `take_rate_legend`, `unmatched[i]["cause"]: str`
- Produces: `opinion_panel(view)["officer_checks"] = {"state": "legacy"|"empty"|"listed", "items": [{"n", "text", "evidence": [{"label", "value", "missing"}], "status": "ok"|"failed"|"unknown", "status_text"}], "verified": int, "total": int}`
- Produces: 상수 `PLATFORM_CYCLES = {"스마트스토어": "구매확정 후 1영업일", "쿠팡": "주정산 70% + 최종액 30%(익익월 1일)", "11번가": "월 2회(25일·익월 10일)", "자사몰": "PG 2영업일"}`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/console/test_dashboard.py` — `LEDGER`에 새 키(`pg_cash_conversion_days: 21.4`, `pg_advance_suspected_count: 1`, `pg_advance_share: 0.92`, `platforms[].lag_days`, `monthly_take_rate[]`에 `fee_rate`·`offset_rate`, `unmatched[0]["advance"] = {"booked_on": "2026-10-21", "amount_krw": 435_000, "lead_days": 6}`)를 넣은 `LEDGER_V2`를 만들고:

```python
def test_settlement_card_new_kpis_guide_and_split_take_rate():
    card = dashboard.settlement_card(LEDGER_V2)
    assert [k["label"] for k in card["kpis"]] == ["정산금 계좌 입금", "최대 채널 비중", "현금화 일수", "공제율 변화(6개월)", "선정산 추정"]
    assert card["kpis"][2]["value"] == "21.4일" and (card["kpis"][4]["value"], card["kpis"][4]["tone"]) == ("1건", "red")
    assert [t for t, _ in card["guide"]] == ["정산금 계좌 입금", "월별 공제율", "현금화 일수", "선정산 추정", "채널 집중도(HHI)"]
    assert card["take_rate_kind"] == "split" and card["cycles"][0] == {"platform": "쿠팡", "cycle": dashboard.PLATFORM_CYCLES["쿠팡"], "lag_days": 32}
    assert card["unmatched"][0]["cause"] == "선지급 추정(6일 전, 정산액의 85%)"


def test_settlement_card_legacy_ledger():
    card = dashboard.settlement_card(LEDGER)
    assert card["kpis"][2]["value"] == "—" and card["kpis"][4]["value"] == "—"
    assert card["take_rate_kind"] == "single" and card["unmatched"][0]["cause"] == "—"
```

`advance`가 None인 행의 원인은 `"원인 미상 — 지연·다른 계좌·상계 확인"`(테스트에 한 줄 추가).

`tests/console/test_opinion.py`:

```python
def test_officer_checks_states(make_doc, key):
    view = detail_view(make_doc(), None, key, restore=False)          # 평가서에 officer_checks 키 없음
    assert opinion_panel(view)["officer_checks"]["state"] == "legacy"
    doc = make_doc()
    doc["assessment"]["officer_checks"] = [
        {"text": "선정산 계약서를 확인한다.", "metric_ids": ["M:pg_reconciliation_ratio"], "supported": True},
        {"text": "정산계좌 변경을 확인한다.", "metric_ids": ["P:R108:threshold"], "supported": None}]
    panel = opinion_panel(detail_view(doc, None, key, restore=False))["officer_checks"]
    assert panel["state"] == "listed" and (panel["verified"], panel["total"]) == (1, 2)
    assert [i["status_text"] for i in panel["items"]] == ["검토 통과", "검토 결과 없음"]
    assert panel["items"][0]["evidence"][0]["label"] == "정산금 계좌 입금 비율"
    doc["assessment"]["officer_checks"] = []
    assert opinion_panel(detail_view(doc, None, key, restore=False))["officer_checks"]["state"] == "empty"
```

(`make_doc` 픽스처의 언더라이팅에 `pg_reconciliation_ratio`가 없으면 근거 값은 `missing=True`로 나와도 된다 — 단언은 label만.)

`tests/console/test_ui.py` — 기존 `test_detail_shows_settlement_card`에 `"지표 읽는 법"`, `"현금화 일수"`, `"선정산 추정"` 단언 추가. 새 테스트: `deps.store.docs["app-20261103-p01"]["assessment"]["officer_checks"] = [{"text": "선정산 계약서를 확인한다.", "metric_ids": ["M:pg_reconciliation_ratio"], "supported": True}]` 후 `"에이전트 확인 사항"`, `"검토자 검증 1/1"`, `"선정산 계약서를 확인한다."` 단언. 키를 지운 문서는 `"2026-11-10 이전 심사분은 확인 사항이 없습니다"`.

- [ ] **Step 2: 실패 확인** — `pytest tests/console -q` FAIL
- [ ] **Step 3: `views.detail_view`** — `assessment`가 있고 `"officer_checks" in assessment`이면 `[{"text", "evidence": [(m, catalog.get(m)) ...], "supported": c.get("supported")}]`, 아니면 None. 반환 dict에 `"officer_checks"`.
- [ ] **Step 4: `settlement_card`** — KPI 순서는 테스트 그대로. 현금화 일수 부제 "표준 정산 주기 기준 추정", tone blue(None이면 neutral). 선정산 추정 값 `f"{n}건"`, 부제 `f"미입금의 {round(share*100)}%"`(share 없으면 "미입금 중 선지급 짝"), n ≥ 1이면 red 아니면 green, 키 없으면 "—"·neutral. split 공제율은 `charts.columns([(m["month"][2:], [[("수수료", fee%, "purple", tip), ("광고비 상계", offset%, "orange", tip)]]) ...], width=320, height=200, tick_fmt=lambda v: f"{v:.1f}%")`, 한 달이라도 `fee_rate`가 없으면 single. `guide`는 스펙 §4.5 문구를 다섯 항목 상수 `SETTLEMENT_GUIDE`로 둔다. 원인 문구: `f"선지급 추정({lead}일 전, 정산액의 {round(amount/net*100)}%)"`, advance None → 원인 미상 문구, 키 없음 → "—".
- [ ] **Step 5: `opinion.py`** — `METRIC_LABELS`에 `pg_cash_conversion_days: "현금화 일수"`, `pg_offset_rate_trend_pp: "광고비 상계율 변화(%p)"`, `pg_advance_share: "선지급 추정 비중"`, `pg_advance_suspected_count: "선정산 추정 건수"`, `pg_unmatched_count: "미입금 정산 건수"`. `_officer_checks(view)`: 평가서는 있는데 `view["officer_checks"] is None` → legacy, `[]` → empty, 그 밖 → listed(`status`: True→ok "검토 통과", False→failed "검토 미통과", None→unknown "검토 결과 없음"; evidence는 `_claims`와 같은 `evidence_label`·`evidence_value`).
- [ ] **Step 6: 템플릿** — 카드 제목 아래 해설 문단(스펙 §4.5 스케치 문구), KPI 5개(`grid-5` 또는 `grid-4` 두 줄 — `grid-5`를 추가하면 CSS 반응형 규칙도), `<details><summary>지표 읽는 법</summary>` + `table.kv`, 플랫폼 막대 아래 `cycles` 표("표준 정산 주기"), 공제율은 `take_rate_kind`에 따라 `ch.columns` + `ch.legend(take_rate_legend)` 또는 기존 `ch.hbars`, 미입금 표에 "추정 원인" 열, 그 아래 `{% if op %}` 확인 사항 블록(제목 "에이전트 확인 사항 <small>검토자 검증 n/m</small>", 항목은 `claim-mark ok|missing` 표시 + 근거 라벨·값; legacy·empty 안내 문구). 정산 자료 없음 분기는 그대로.
- [ ] **Step 7: 통과 확인** — `pytest tests/console tests/docs -q` PASS
- [ ] **Step 8: 커밋** — `feat(console): 정산 대사 해설·새 지표·에이전트 확인 사항`

---

### Task 7: 심사 도우미 스냅샷

**Files:**
- Modify: `src/console/officer_chat.py:25-26`(`SETTLEMENT_KEYS`), `_opinion`, `_fit`
- Modify: `src/agents/officer_chat.py:20`(`INSTRUCTIONS` 한 줄)
- Test: `tests/console/test_officer_chat.py`

**Interfaces:**
- Consumes: Task 6 `view["officer_checks"]`
- Produces: `snapshot["cashflow"]["settlement"]`에 `pg_cash_conversion_days`, `pg_offset_rate_trend_pp`, `pg_advance_share`, `pg_advance_suspected_count`; `snapshot["agent_opinion"]["officer_checks"] = [{"text", "evidence": {id: value}, "supported"}]`(없으면 `[]`)

- [ ] **Step 1: 실패하는 테스트 작성** — `test_snapshot_settlement_subset`의 기대 키 튜플을 `SETTLEMENT_KEYS`로 바꾸고 `pg`에 새 4개 값을 넣어 단언, `"unmatched" not in settlement`. 새 테스트: 평가서에 확인 사항 1개를 넣은 문서 → `snap["agent_opinion"]["officer_checks"] == [{"text": ..., "evidence": {"M:pg_reconciliation_ratio": <catalog 값 또는 None>}, "supported": True}]`; 키 없는 옛 문서 → `[]`. `_fit`이 크기 초과 시 `officer_checks`도 뒤에서부터 줄이는지 기존 truncation 테스트 패턴으로 한 줄.
- [ ] **Step 2: 실패 확인** — `pytest tests/console/test_officer_chat.py -q` FAIL
- [ ] **Step 3: 구현** — `SETTLEMENT_KEYS`에 4개 추가, `_opinion`에 `officer_checks`, `_fit`의 줄이는 키 순서를 `("claims", "counterfactuals", "officer_checks")`로 한다(확인 사항은 마지막에 줄인다). 에이전트 지시문에 "agent_opinion.officer_checks가 있으면 정산 관련 질문에 그 확인 사항과 근거 값으로 답한다." 한 줄.
- [ ] **Step 4: 통과 확인** — `pytest tests/console tests/agents/test_officer_chat.py -q` PASS
- [ ] **Step 5: 커밋** — `feat(officer-chat): 정산 새 지표와 확인 사항을 스냅샷에 포함`

---

### Task 8: 문서

**Files:**
- Modify: `docs/agents.md`(심사 섹션: officer_checks·검토자 check_verdicts·summary 6~8문장 / 정산 원장 대사: 새 지표 3종과 선정산 추정 규칙)
- Modify: `docs/architecture.md`(정산 원장 데이터 흐름 문단 ~187행: 새 지표·확인 사항 경로, LLM에는 집계만)
- Modify: `docs/demo/demo-script.md`(mugshop-21 정산 카드에서 선정산 추정·확인 사항 보여 주기)

- [ ] **Step 1: 세 문서 갱신**(값은 Global Constraints 그대로, 표 안 `|`는 `\|`로)
- [ ] **Step 2: 문서 테스트** — `pytest tests/docs -q` PASS
- [ ] **Step 3: 커밋** — `docs: 정산 확인 사항과 새 정산 지표`

---

### Task 9: 배포·실환경 검증

**Files:**
- Modify: `docs/runbook/deploy-history.md`

- [ ] **Step 1: 전체 테스트** — `pytest tests/agents tests/console tests/docs tests/evals tests/shared tests/mockapis -q -p no:cacheprovider` 모두 PASS
- [ ] **Step 2: 배포** — `ops window open --acr` → `ops deploy orchestrator` → `ops deploy console` → `ops smoke`(az JSON 오류면 한 번 재실행). 오케스트레이터 403이면 배포 머신 아웃바운드 IP를 메인 사이트 접근 제한에 추가.
- [ ] **Step 3: 실환경 확인** — 콘솔에서 `seller-mugshop-21`과 정상 셀러 1명을 새로 신청. 확인 기준:
  - mugshop-21: 선정산 추정 ≥ 1건(빨강), 미입금 표 "선지급 추정(…)" 원인, 에이전트 확인 사항 1개 이상(정산 근거), 검토자 검증 표시
  - 정상 셀러: 선정산 추정 0건, 현금화 일수 숫자, 2색 공제율
  - 이전 신청 1건: 새 KPI "—"와 옛 문서 안내
  - 심사 도우미에 "정산에서 확인할 점은?" → 확인 사항 근거로 답변
- [ ] **Step 4: 창 닫기** — `ops window close`(WAF가 `CONSOLE_IP`로 재설정되므로 운영자 IP 예외를 다시 확인)
- [ ] **Step 5: 배포 이력 기록·커밋·푸시** — `docs/runbook/deploy-history.md`에 날짜·이미지·검증 결과(신청 ID는 그대로, IP·구독 ID는 가림) 추가, 커밋 후 `origin/main` 푸시.
