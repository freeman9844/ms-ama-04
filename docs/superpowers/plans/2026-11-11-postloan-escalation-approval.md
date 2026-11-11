# 조기경보 단계별 자동 대응·추심 심사역 승인 구현 플랜

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 조기경보가 "이유 + 확인할 조치" 경보와 높음 경보 단계별 자동 안내(L4)를 내고, 고객이 고른 셀프 상환 플랜이 심사역 승인 뒤 실제 일정·재생에 반영되며, 추심 에이전트는 제안만 하고 심사역이 승인·수정·반려한 문구만 발송되게 한다.

**Architecture:** 단계 판정·템플릿·공정 연락 검사는 LLM 없는 순수 모듈(`agents/escalation.py`, `agents/collections.py`)이 맡고, 플랜 옵션·흐름 덧씌우기는 순수 모듈(`agents/repayment_plan.py`)이 맡는다. 오케스트레이터는 기존 재조정 멈춤·재개 구조를 "대기 항목 목록(holds)"으로 일반화하고, 심사역·고객 결정은 기존 `restructure-decisions` 큐에 `kind` 필드를 더한 메시지로 받는다. 콘솔은 대출 화면(추심 승인·플랜 변경 승인·응답 없이 계속)과 차주 화면(플랜 선택)에 카드를 더한다.

**Tech Stack:** Python 3.12, Pydantic v2, FastAPI + Jinja2(콘솔), Azure Functions(오케스트레이터), Cosmos/Blob 어댑터(테스트는 메모리 대역), pytest.

**Spec:** `docs/superpowers/specs/2026-11-11-postloan-escalation-approval-design.md`

## Global Constraints

- 사용자·고객에게 보이는 문구는 한국어. 콘솔은 CSP 규칙상 인라인 스크립트·스타일 없음.
- Mock API·인프라(큐·토픽) 변경 없음. 결정 메시지는 기존 `restructure-decisions` 큐 하나로 보낸다.
- 공정 연락 규칙: 7일 7회(`MAX_CONTACTS_PER_7_DAYS = 7`), 08:00~21:00 KST(`CONTACT_HOURS = (8, 21)`). 걸리면 `deferred`, 재시도 없음.
- 자동 발송(3일 전 안내, 5일 전 추가 안내, 조기경보 1·2단계)은 **시뮬레이션 날짜 10:00 KST**로 시간대를 검사한다. 심사역이 승인·수정한 추심 문구는 **실제 시각**으로 검사한다.
- 고객 문구: 300자 이하, 금지 표현 없음, `[[PERSON_` 토큰 없음. 자동 템플릿 자리표시자는 날짜·금액·링크만.
- 조기경보는 회수·한도 축소·부도 확정·금리 변경을 하지 않고 그렇게 읽히는 문구도 보내지 않는다.
- 각 조기경보 단계는 재생마다 한 번만 발송(`deferred`여도 쓴 것으로 본다).
- 공개된 주차의 거래·판정은 일정 변경 뒤에도 바뀌지 않는다.
- 옛 문서 호환: `check_action` 없는 경보, `proposal` 없는 추심 건, `part`·`amount_krw` 없는 상환 회차, `holds`·`escalation` 없는 모니터링, `kind` 없는 재조정 결정 메시지.
- 배포 순서: 오케스트레이터 → 콘솔(콘솔이 `kind`를 담아 보내기 때문).
- 테스트 명령(전체, 약 280초): `$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python -m pytest tests/orchestrator tests/agents tests/console tests/evals tests/shared tests/mockapis tests/docs tests/infra -q -p no:cacheprovider` — 기준 4589 passed, 136 skipped.
- 커밋 메시지는 `Out-File -Encoding utf8NoBOM`로 파일에 쓰고 `git commit -F`. 트레일러 `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`, `Copilot-Session: 05f1ca1b-6073-417d-bfb1-6b546f82cb58`. `memory261105.md`·`ops/reports/`는 스테이징 금지.

## Review Focus

1. **결정 메시지 재전달(큐 중복)** — 같은 추심 승인·플랜 승인이 두 번 오면 두 번째는 아무것도 보내거나 바꾸지 않는다(Task 6, Task 7 테스트).
2. **만료된 제안에 고객이 늦게 응답** — 심사역이 "고객 응답 없이 계속"을 누른 뒤 들어온 선택은 버린다(Task 7 오케스트레이터, Task 9 콘솔 409).
3. **옛 문서** — 위 Global Constraints의 옛 문서들이 콘솔 렌더·오케스트레이터 재개에서 깨지지 않는다(Task 6, Task 8 테스트).
4. **밤 시간 데모** — 자동 흐름은 끝까지 가고, 심사역 승인 발송만 `deferred`가 돼도 결정은 끝난 것으로 보고 재개 대기로 간다(Task 6 테스트).
5. **같은 주에 2단계 안내와 미납** — 추심 승인과 고객 응답이 모두 끝나야 재개 대기로 간다(Task 7 테스트).

---

## 파일 구조

| 파일 | 책임 |
|---|---|
| `src/shared/contracts.py` | `EarlyWarningAlert.check_action` |
| `src/agents/early_warning.py` | `raise_alert`의 `check_action`, 지시문 신호 표, 폴백 문구 템플릿, `due_shortfall` 문장 |
| `src/agents/monitoring.py`, `src/agents/forecast.py` | `due_shortfall` 트리거·`F:n:next_due_on`·`F:n:due_balance`, 회차별 금액 전망 |
| `src/agents/collections.py` | 금지 표현·문구 검사, 스크립트 문구 채우기, `propose` 도구, 제안서 조립, 심사역 결정 처리 |
| `src/agents/escalation.py` (새) | 단계 판정, 자동 안내 템플릿, 시뮬레이션 시각 연락 |
| `src/agents/repayment.py` | 회차 키·금액, 5일 전 추가 안내 판정 |
| `src/agents/repayment_plan.py` (새) | 주 플랫폼·지급일 추정, 이동·분할 옵션, 일정 교체, 흐름 덧씌우기 |
| `src/orchestrator/journey/models.py` | `awaiting_customer`, 결정 메시지 종류(판별 유니온) |
| `src/orchestrator/journey/holds.py` (새) | 대기 항목 계산과 멈춤 상태 |
| `src/orchestrator/journey/monitoring.py` | 단계 발송, 제안·플랜 처리, 추심 제안, 결정 분기, 흐름 덧씌우기 |
| `src/console/*` | 대출 화면 카드·라우트, 차주 화면 플랜 섹션·라우트 |
| `src/evals/suites/early_warning.py` | 경로별 단계 도달 주차 기록 |
| 문서 | `docs/AGENTS.md`, `docs/limitations.md`, `demo/demo-script.md`, `demo/fallback.md` |

---

### Task 1: 경보 "이유 + 확인할 조치"

**Files:**
- Modify: `src/shared/contracts.py` (`EarlyWarningAlert`, 약 328행)
- Modify: `src/agents/early_warning.py` (`INSTRUCTIONS`, `raise_alert` 스키마, `check_alert`, `build_alert`, `fallback_alert`)
- Modify: `src/evals/fake.py` (가짜 `raise_alert` 인자에 `check_action`)
- Test: `tests/agents/test_early_warning.py`, `tests/shared/` 계약 테스트가 있으면 그 파일

**Interfaces:**
- Produces: `EarlyWarningAlert.check_action: str | None`(1~200자, 기본 `None`). `raise_alert` 인자 `check_action: str`(빈 문자열이면 `None`). `FALLBACK_TEXT: dict[str, tuple[str, str]]` — 원인값(`AlertCause.value`) 또는 트리거 이름 → (이유, 확인할 조치).

- [ ] **Step 1: 실패 테스트 작성** (`tests/agents/test_early_warning.py`)

```python
def test_check_action_is_grounded_like_summary(ctx):  # 기존 픽스처 사용
    args = valid_args(ctx) | {"check_action": "정산 상태를 확인하고 1,234,567원 부족분을 점검하세요."}
    assert any(i.startswith("check_action:") for i in check_alert(args, ctx))

def test_check_action_is_stored_on_alert(ctx):
    args = valid_args(ctx) | {"check_action": "플랫폼 정산 상태와 다른 계좌의 가용자금 확인이 필요합니다."}
    assert build_alert(args, ctx, {}).check_action == "플랫폼 정산 상태와 다른 계좌의 가용자금 확인이 필요합니다."

def test_fallback_uses_cause_template_without_numbers(ctx_with_settlement_hold):
    a = fallback_alert(ctx_with_settlement_hold, "agents_disabled")
    assert a.summary == "정산 지급이 보류되어 다음 상환일 자금이 부족할 수 있습니다."
    assert a.check_action == "플랫폼 정산 상태와 다른 계좌의 가용자금 확인이 필요합니다."
    assert not re.search(r"\d", a.summary + a.check_action)

def test_raise_alert_schema_requires_check_action():
    tool = next(t for t in TOOLS if t["name"] == "raise_alert")
    assert "check_action" in tool["parameters"]["required"]

def test_old_alert_without_check_action_validates():
    assert EarlyWarningAlert.model_validate(OLD_ALERT_DOC).check_action is None
```

- [ ] **Step 2: 실패 확인** — `.\.venv\Scripts\python -m pytest tests/agents/test_early_warning.py -q -p no:cacheprovider` → 새 테스트 FAIL.

- [ ] **Step 3: 구현**
  - 계약: `check_action: str | None = Field(default=None, min_length=1, max_length=200)`.
  - `check_alert`: `summary`와 같은 방식으로 `ungrounded(check_action, catalog 숫자)`면 `"check_action: 근거 카탈로그에 없는 수치 …"` issue.
  - `FALLBACK_TEXT` 항목(원인 우선, 원인이 없으면 첫 트리거, 둘 다 없으면 `"default"`). 정산 보류 문구는 위 테스트 그대로. 나머지 의미: 매출 부진 → 판매대금 입금 감소로 상환재원이 줄 수 있음 / 최근 주문·정산 추이 확인. 반품 급증 → 예상 정산액이 줄 수 있음 / 반품·환불 사유와 정산 차감 확인. 비용 증가 → 매출이 유지돼도 가용현금이 줄 수 있음 / 광고비·매입비 지출 계획 확인. 고금리 차입 → 고금리 상환 부담으로 상환 여력이 줄 수 있음 / 신규 차입 내역과 상환 일정 확인. `due_shortfall`(Task 2에서 트리거 추가) → 다음 상환일 예상 잔고가 상환액보다 적음 / 상환일 전 입금 예정과 다른 계좌 가용자금 확인. `default` → 탐지기 이상이 확인됨 / 심사역 확인 필요. 숫자 금지.
  - 폴백의 `summary`는 이유 문장, 기존 "조사 에이전트를 쓸 수 없어…" 사유는 `EwResult.reason`에 이미 있으니 summary에서 뺀다.
  - `INSTRUCTIONS` 5번에 신호 표(판매대금 입금 감소/반품·환불 증가/광고비·매입비 증가/예상 현금잔액 < 예정 상환액)와 예시 "정산일이 7일 지연되어 다음 상환일에 현금 부족이 예상됩니다." / "정산 상태와 다른 계좌의 가용자금 확인이 필요합니다."를 넣고, summary=이유, check_action=확인할 조치라고 적는다. 경보는 회수·한도 축소·부도 확정을 권하지 않는다는 문장도 넣는다.
  - `src/evals/fake.py` 가짜 `raise_alert` 인자에 숫자 없는 `check_action`.

- [ ] **Step 4: 통과 확인** — 같은 명령 + `tests/evals -q` → PASS.

- [ ] **Step 5: 커밋** — `feat: 조기경보 경보에 확인할 조치 문장 추가`

---

### Task 2: 다음 상환일 예상 잔고(`due_shortfall`)

**Files:**
- Modify: `src/agents/forecast.py` (`project`)
- Modify: `src/agents/monitoring.py` (`TRIGGERS`, `FORECAST_FIELDS`, `projection`, 새 `due_check`)
- Modify: `src/agents/early_warning.py` (`_TRIGGER_TEXT`)
- Modify: `src/orchestrator/journey/monitoring.py` (`process_week`, `_due_list`)
- Modify: `src/console/monitoring.py` (`TRIGGER_LABELS["due_shortfall"] = "상환일 잔고 부족"`)
- Test: `tests/agents/test_monitoring.py`, `tests/orchestrator/test_monitoring_handlers.py`

**Interfaces:**
- Consumes: Task 4의 `item_amount(item, installment_krw) -> int`는 아직 없으므로 이 Task에서는 `item.get("amount_krw") or installment_krw`로 쓴다(Task 4가 같은 의미의 함수를 만든다).
- Produces:
  - `project(..., installment_due: Sequence[date] | Mapping[date, int] | None)` — Mapping이면 날짜별 금액을 뺀다.
  - `projection(rows, transactions, categories, baseline, installment_due=...)` 같은 타입 통과.
  - `due_check(projection: Mapping | None, schedule: Sequence[Mapping], installment_krw: int, after: date) -> dict[str, Any]` → `{"next_due_on": str}` 또는 `{"next_due_on": str, "due_balance": int}` 또는 `{}`.
  - 트리거 이름 `"due_shortfall"`, 근거 ID `F:{week}:next_due_on`(날짜 문자열), `F:{week}:due_balance`(정수).
  - 오케스트레이터 `_due_list(loan) -> dict[date, int] | None`(상태 `scheduled` 회차만, 같은 날 금액 합).

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/agents/test_monitoring.py
def test_due_check_reports_pre_debit_balance():
    proj = {"path": [{"date": "2026-12-01", "balance": 900_000}, {"date": "2026-12-02", "balance": -270_000}]}
    sched = [{"no": 1, "due_on": "2026-12-02", "status": "scheduled"}]
    assert due_check(proj, sched, 1_170_000, date(2026, 11, 29)) == {"next_due_on": "2026-12-02", "due_balance": 900_000}

def test_due_check_outside_horizon_has_no_balance():
    proj = {"path": [{"date": "2026-12-01", "balance": 5}]}
    sched = [{"no": 1, "due_on": "2027-01-01", "status": "scheduled"}]
    assert due_check(proj, sched, 1, date(2026, 11, 29)) == {"next_due_on": "2027-01-01"}

def test_due_check_skips_paid_and_past_items():
    sched = [{"no": 1, "due_on": "2026-11-02", "status": "paid"}, {"no": 2, "due_on": "2026-11-20", "status": "scheduled"}]
    assert due_check(None, sched, 1, date(2026, 11, 29)) == {}

def test_project_uses_per_date_amounts():
    # 같은 입력으로 Mapping {d: 500_000}과 [d](installment 1_000_000)를 비교해 totals["installment"]가 -500_000 vs -1_000_000
    ...

def test_due_balance_is_in_evidence_catalog():
    rows = [ROW | {"next_due_on": "2026-12-02", "due_balance": 900_000}]
    cat = evidence_catalog(rows, BASELINE, "fashion")
    assert cat["F:1:due_balance"] == 900_000 and cat["F:1:next_due_on"] == "2026-12-02"
```

```python
# tests/orchestrator/test_monitoring_handlers.py
def test_due_shortfall_trigger_fires_when_due_balance_below_installment(mock_app, monkeypatch):
    # agents.monitoring.due_check를 {"next_due_on": FIRST_DUE.isoformat(), "due_balance": 1}로 바꿔 끼운다
    ...
    entry = loan(deps)["monitoring"]["weeks"][0]
    assert "due_shortfall" in entry["triggers"] and entry["row"]["due_balance"] == 1
```

- [ ] **Step 2: 실패 확인** — `pytest tests/agents/test_monitoring.py tests/orchestrator/test_monitoring_handlers.py -k "due_" -q` → FAIL.

- [ ] **Step 3: 구현**
  - `due_check`: `after`보다 뒤인 첫 `scheduled` 회차(정렬: `due_on`). `due_balance` = 전망 path에서 그 날짜 잔고 + 그 회차 금액(전망이 이미 상환액을 뺐으므로 "출금 직전"으로 되돌림). path에 날짜가 없으면 `due_balance` 생략.
  - `TRIGGERS` 끝에 `"due_shortfall"`, `FORECAST_FIELDS`에 `"next_due_on"`, `"due_balance"`.
  - `_TRIGGER_TEXT["due_shortfall"] = ("projected_shortfall", "F:{week}:due_balance", "다음 상환일 출금 직전 예상 잔고가 상환액보다 적다")`.
  - `process_week`: `entry["projection"]` 계산 뒤 `fields = due_check(entry["projection"], loan["repayment"]["schedule"], installment, hi)`를 `rows[-1]`에 넣고, `due_balance < 그 회차 금액`이면 `triggers`에 `"due_shortfall"` 추가. 그다음 `new`를 계산한다(순서 변경). 상환 원장이 없으면 건너뛴다.
  - `_due_list`: `scheduled` 회차만, `{date: 금액}`.

- [ ] **Step 4: 통과 확인** — 같은 명령 → PASS. `tests/console -q`도 PASS.

- [ ] **Step 5: 커밋** — `feat: 다음 상환일 예상 잔고 트리거 due_shortfall`

---

### Task 3: 연락 정책과 단계 판정 모듈

**Files:**
- Modify: `src/agents/collections.py` (`BANNED_PHRASES`, `MESSAGE_MAX`, `message_issues`, `remind` 확장)
- Create: `src/agents/escalation.py`
- Test: `tests/agents/test_escalation.py`(새), `tests/agents/test_collections.py`

**Interfaces:**
- Produces (collections):
  - `BANNED_PHRASES: tuple[str, ...]` — 최소 `"법적 조치", "압류", "소송", "고소", "형사", "신용불량", "가족", "직장", "회수", "한도 축소", "부도", "금리 인상", "즉시 상환", "강제"`.
  - `MESSAGE_MAX = 300`.
  - `message_issues(text: str) -> list[str]` — 빈 문자열, 300자 초과, 금지 표현(어느 것인지), `[[PERSON_` 포함을 한국어 사유로.
  - `remind(ref, contacts, on, now, *, script_id="payment_reminder", by="reminder", text=None) -> dict` — `text`가 있으면 기록에 싣는다.
- Produces (escalation):
  - `SIM_HOUR = 10`, `sim_now(on: date) -> datetime`(KST).
  - `TEMPLATES: dict[str, str]` 키 `balance_warning`, `balance_warning_plain`, `early_reminder`, `self_plan_offer`. 자리표시자는 `{due_on}`, `{amount}`, `{balance}`, `{link}`만.
  - `next_stage(severities: Sequence[str], escalation: Mapping[str, Any]) -> int | None` — `severities`는 이 재생 경보의 심각도(이번 것 포함, 순서대로). `escalation`은 `{"stage1": {...}|None, "stage2": {...}|None}`.
  - `stage_message(stage: int, *, due_on: date, amount: int, balance: int | None, link: str) -> tuple[str, str]` → `(script_id, text)`. 1단계는 `balance`가 있으면 `balance_warning`, 없으면 `balance_warning_plain`(script_id는 둘 다 `"balance_warning"`). 2단계는 `self_plan_offer`.
  - `plan_link(applicant_id: str) -> str` = `f"/borrower/{applicant_id}#self-plan"`.
  - `auto_contact(ref: str, script_id: str, text: str, contacts: Sequence[Mapping], on: date) -> dict` → `{ref, on, at: sim_now(on), script_id, text, status, reason, by: "early_warning"}`.

- [ ] **Step 1: 실패 테스트 작성** (`tests/agents/test_escalation.py`)

```python
@pytest.mark.parametrize("sev, esc, expected", [
    (["high"], {}, 1),
    (["medium", "high"], {}, 1),
    (["high", "high"], {"stage1": {"status": "sent"}}, 2),
    (["high", "medium", "high"], {"stage1": {"status": "sent"}}, None),
    (["high", "high", "high"], {"stage1": {}, "stage2": {"status": "deferred"}}, None),
    (["high", "high"], {"stage1": {"status": "deferred"}}, 2),
    (["medium"], {}, None),
])
def test_next_stage(sev, esc, expected):
    assert next_stage(sev, esc) == expected

def test_templates_pass_message_policy():
    for stage, bal in ((1, 900_000), (1, None), (2, None)):
        _, text = stage_message(stage, due_on=date(2026, 12, 2), amount=1_170_000, balance=bal, link=plan_link("seller-x"))
        assert message_issues(text) == []
        assert "2026-12-02" in text

def test_self_plan_offer_has_link_and_options():
    _, text = stage_message(2, due_on=date(2026, 12, 2), amount=1, balance=None, link="/borrower/s#self-plan")
    assert "/borrower/s#self-plan" in text and "나눠" in text and "정산" in text

def test_auto_contact_uses_simulated_10am_even_at_night():
    c = auto_contact("rp-1-s1", "balance_warning", "t", [], date(2026, 12, 1))
    assert c["status"] == "sent" and c["at"].startswith("2026-12-01T10:00") and c["by"] == "early_warning"

def test_auto_contact_respects_weekly_limit():
    full = [{"on": "2026-12-01", "status": "sent"}] * 7
    assert auto_contact("r", "balance_warning", "t", full, date(2026, 12, 1))["reason"] == "weekly_limit"
```

```python
# tests/agents/test_collections.py
@pytest.mark.parametrize("text, needle", [("", "비어"), ("가" * 301, "300자"), ("압류 예정입니다", "압류"), ("[[PERSON_1]]님", "가명")])
def test_message_issues(text, needle):
    assert any(needle in i for i in message_issues(text))
```

- [ ] **Step 2: 실패 확인** — `pytest tests/agents/test_escalation.py tests/agents/test_collections.py -q` → FAIL(import 오류).

- [ ] **Step 3: 구현** — `next_stage`: 마지막이 `high`가 아니면 `None`. `stage1`이 없으면 1. `stage2`가 없고 끝의 두 개가 모두 `high`면 2. 그 밖은 `None`. `escalation` 값이 dict이면(내용 무관) 그 단계는 쓴 것. 템플릿 문장은 스펙 §5 의미를 지키고(1단계: 상환일 예상 잔고 부족 안내 + 미리 입금 확인 + 어려우면 회신, 2단계: 정산 지급일 뒤로 상환일 이동 또는 다음 회차를 두 번에 나눠 납부 가능 + 링크에서 선택) 금지 표현 없이 쓴다. 금액은 `f"{amount:,}원"`.

- [ ] **Step 4: 통과 확인** — 같은 명령 → PASS.

- [ ] **Step 5: 커밋** — `feat: 연락 문구 정책 검사와 조기경보 단계 판정 모듈`

---

### Task 4: 상환 회차 키·5일 전 안내·셀프 플랜 계산·흐름 덧씌우기

**Files:**
- Modify: `src/agents/repayment.py`
- Create: `src/agents/repayment_plan.py`
- Test: `tests/agents/test_repayment.py`, `tests/agents/test_repayment_plan.py`(새)

**Interfaces:**
- Produces (repayment):
  - `EARLY_REMINDER_DAYS = 5`, `early_reminder_on(due: date) -> date`.
  - `item_key(item: Mapping) -> str` — `part`가 없으면 `f"i{no}"`, 있으면 `f"i{no}p{part}"`.
  - `item_amount(item: Mapping, installment_krw: int) -> int` — `item.get("amount_krw") or installment_krw`.
  - `RepaymentWeek(reminders: tuple[str, ...], paid: tuple[tuple[str, str], ...], missed: tuple[str, ...], early_reminders: tuple[str, ...] = ())` — **회차 번호 대신 `item_key`**.
  - `week_events(schedule, txns, lo, hi, early_from: date | None = None) -> RepaymentWeek` — `early_from`이 있으면 `scheduled`이고 `early_reminded_on`이 없으며 `early_from <= early_reminder_on(due)`이고 `lo <= early_reminder_on(due) <= hi`인 회차.
- Produces (repayment_plan):
  - `main_platform(lb: LedgerBaseline | None) -> str | None` — `platform_weekly_net` 최대.
  - `payout_days(pg: PgFlow | None, platform: str | None, upto: date) -> list[date]` — 그 플랫폼 `net_krw > 0` 정산일(중복 없음, 정렬, `upto` 이하).
  - `next_payout(days: Sequence[date], after: date) -> date | None`.
  - `plan_options(schedule: Sequence[dict], installment_krw: int, *, after: date, flow_end: date, payouts: Sequence[date]) -> list[dict]` → 각 `{"option": "move"|"split"|"keep", "items": [...], "replaces": [key, ...]}`. `items` 원소는 `{no, part, due_on, amount_krw, from_due_on}`.
  - `apply_change(schedule: Sequence[dict], change: Mapping) -> list[dict]`.
  - `apply_plan(txns: Sequence[Transaction], history: Sequence[Mapping]) -> list[Transaction]` — `history` 원소 `{change_id, applied_from: str, before: [{key, due_on, amount_krw}], after: [{key, due_on, amount_krw}]}`.

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/agents/test_repayment_plan.py  (2026-12 공휴일 없음, 금요일 주간 지급)
FRI = [date(2026, 11, 6), date(2026, 11, 13), date(2026, 11, 20), date(2026, 11, 27)]
SCHED = [{"no": 1, "due_on": "2026-11-02", "status": "paid"}, {"no": 2, "due_on": "2026-12-02", "status": "scheduled"},
         {"no": 3, "due_on": "2027-01-01", "status": "scheduled"}]

def test_move_goes_to_business_day_after_next_payout():
    move = opt(plan_options(SCHED, 1_170_001, after=date(2026, 11, 29), flow_end=date(2026, 12, 31), payouts=FRI), "move")
    assert [(i["no"], i["due_on"], i["from_due_on"]) for i in move["items"]] == [(2, "2026-12-07", "2026-12-02")]
    assert move["replaces"] == ["i2"]  # 3회차는 flow_end 밖이라 그대로

def test_move_is_capped_at_ten_days():
    monthly = [date(2026, 10, 25), date(2026, 11, 25)]
    move = opt(plan_options(SCHED, 1, after=date(2026, 11, 29), flow_end=date(2027, 1, 31), payouts=monthly), "move")
    assert move["items"][0]["due_on"] == "2026-12-11"

def test_move_absent_without_payout_history():
    assert [o["option"] for o in plan_options(SCHED, 1, after=date(2026, 11, 29), flow_end=date(2026, 12, 31), payouts=[])] == ["split", "keep"]

def test_split_halves_next_installment_keeping_total():
    split = opt(plan_options(SCHED, 1_170_001, after=date(2026, 11, 29), flow_end=date(2026, 12, 31), payouts=FRI), "split")
    assert [(i["part"], i["due_on"], i["amount_krw"]) for i in split["items"]] == [(1, "2026-12-02", 585_000), (2, "2026-12-17", 585_001)]
    assert split["replaces"] == ["i2"]

def test_apply_change_replaces_items_and_resets_reminders():
    ...  # 새 회차는 status="scheduled", reminded_on·early_reminded_on None, due_on 순 정렬

def test_apply_plan_moves_debit_and_recomputes_later_balances():
    ...  # 원래 상환일 출금이 빠지고 새 날짜(그날 마지막)에 LENDER_DEBIT -금액, 이후 balance_after 재계산

def test_apply_plan_misses_when_balance_is_short():
    ...  # 새 날짜 직전 잔고 < 금액이면 출금을 넣지 않는다

def test_apply_plan_never_touches_txns_before_applied_from():
    ...  # applied_from 이전 거래는 원본과 같음

def test_apply_plan_without_history_is_identity():
    assert apply_plan(TXNS, []) == list(TXNS)
```

```python
# tests/agents/test_repayment.py
def test_week_events_returns_item_keys_for_split_parts(): ...      # "i2p1" 납부, "i2p2" 미납
def test_early_reminders_only_after_stage_one(): ...               # early_from=None이면 (), 있으면 due-5가 이번 주인 키
```

- [ ] **Step 2: 실패 확인** — `pytest tests/agents/test_repayment.py tests/agents/test_repayment_plan.py -q` → FAIL.

- [ ] **Step 3: 구현**
  - `next_payout`: 지급일이 2개 미만이면 `None`. 최근 9개 지급일 간격의 중앙값(최소 1일)을 주기로, 마지막 지급일에서 주기를 더해 `after`보다 뒤인 첫 날.
  - 이동: 대상은 `status == "scheduled"`이고 `due_on > after`인 회차. 새 날짜 = `add_business_days(next_payout(payouts, due), 1)`; `due + 10일`을 넘으면 `previous_business_day(due + 10일)`; 결과가 `due` 이하이거나 `flow_end` 밖이면 옮기지 않는다. 옮긴 회차가 없으면 옵션을 내지 않는다.
  - 분할: 대상 중 첫 회차. 1부 = 원래 날짜 `installment // 2`, 2부 = `next_business_day(due + 15일)`에 나머지. 2부가 `flow_end` 밖이면 옵션을 내지 않는다.
  - 유지: 항상 마지막, `items=[]`, `replaces=[]`.
  - `apply_plan` 알고리즘(변경마다 차례로):

```text
start = applied_from; 앞 잔고 = start 이전 마지막 거래 balance_after (없으면 첫 거래 balance_after - amount)
before 날짜들에서 description == LENDER_DEBIT 이고 amount < 0 인 원래 출금을 뺀다(start 이후만)
start 이후 날짜(남은 거래 날짜 ∪ after 날짜)를 순서대로:
    그날 원래 거래를 원래 순서로 잔고에 더해 balance_after 재설정
    그날 after 항목마다: 잔고 >= 금액이면 Transaction(txn_id=f"{change_id}-{key}", booked_on=그날,
        amount_krw=-금액, balance_after_krw=잔고-금액, description=LENDER_DEBIT, txn_type_code=뺀 원래 출금의 코드
        (없으면 흐름 안 아무 LENDER_DEBIT 코드))를 그날 맨 뒤에 넣는다. 모자라면 넣지 않는다.
```

  - `build_schedule`은 그대로(옛 형태). `week_events`는 `item_key`로 돌려준다.

- [ ] **Step 4: 통과 확인** — 같은 명령 → PASS.

- [ ] **Step 5: 커밋** — `feat: 셀프 상환 플랜 계산과 흐름 덧씌우기`

> 오케스트레이터의 `week_events` 호출부는 Task 6에서 키 기반으로 바꾼다. 이 Task 끝에 `tests/orchestrator`가 깨지면 Task 6 전까지 허용하지 말고, `_repayment_week`의 `by_no` 맵을 `item_key` 맵으로만 바꾸는 최소 수정(문서 ID `rc-{replay_id}-{key}`, 연락 ref `{replay_id}-{key}`, 금액 `item_amount`)을 이 Task에 포함한다. 옛 회차 키 `i{no}`는 지금 ID와 같다.

---

### Task 5: 추심 에이전트 `propose`와 제안서

**Files:**
- Modify: `src/agents/collections.py`
- Test: `tests/agents/test_collections.py`

**Interfaces:**
- Consumes: Task 3 `message_issues`, `fair_check`.
- Produces:
  - `SCRIPTS[id]["text"]`가 `{no}`, `{due_on}`, `{amount}` 자리표시자를 가질 수 있다(`missed_payment`, `hardship_offer`는 회차·금액·상환일을 넣는다). `render_script(script_id: str, case: RepaymentCase) -> str`.
  - 도구: `get_case`, `get_contact_history`, `list_scripts`, `propose`. `propose` 인자(strict, 모두 필수): `script_id`(enum SCRIPTS), `hardship_category`(enum: 재조정 카테고리 + `""`), `hardship_note: str`, `reason: str`. 한 번만 `{"accepted": true}`; 두 번째는 `{"error": "already_proposed"}`; 잘못된 script·category는 `{"error": ...}`.
  - `CollectionsContext.draft: dict | None` — `{script_id, hardship: {category, note}|None, reason, by}`. `note`는 `mask` 후 `NOTE_MAX`로 자른다.
  - `OUTPUT_SCHEMA` decision enum `["proposed", "none"]`; `CollectionsResult(decision, reason, draft, record, conversations)`.
  - 폴백: 최근 경보 `high`면 `hardship_offer`, 아니면 `missed_payment`, 재조정 없음, `by="fallback"`.
  - `HARDSHIP_CAUSES = {"sales_slump", "settlement_hold", "return_spike", "high_cost_borrowing"}`.
  - `build_proposal(case: RepaymentCase, draft: Mapping, contacts: Sequence[Mapping], now: datetime, escalation: Sequence[Mapping], mask: Callable[[str], str]) -> dict` →
    `{status: "pending_officer", script_id, text, hardship, reason, by, evidence: {installment_no, due_on, amount_krw, missed_count, recent_alert: {severity, primary_cause, summary, check_action}|None, contacts_7d, escalation: [{script_id, on, status}]}, checks: [{id, ok, detail}], created_at}`. check id 순서: `approved_script`, `contact_hours`, `weekly_limit`, `banned_words`, `pii_tokens`, 그리고 재조정이 있으면 `hardship_grounds`.
  - `settle_proposal(proposal: Mapping, decision: str, *, text: str | None, include_hardship: bool | None, reason: str, contacts: Sequence[Mapping], now: datetime, officer_id: str, case: RepaymentCase) -> Settled`;
    `Settled(proposal: dict, contact: dict | None, restructure: dict | None, errors: list[str])`. 승인: 제안 `text` 그대로. 수정: 새 `text`가 `message_issues`를 통과해야 하고, `include_hardship`이 `False`면 재조정 제외(`True`인데 제안에 재조정이 없으면 오류). 반려: `reason` 필수, 발송 없음. 연락 기록 `{ref: case_id, on: due_on, at: now, script_id, text, status, reason, by: "officer", officer_id}`, 상태는 `fair_check(contacts, due_on, now)`(실제 시각). 재조정 요청 형태는 지금과 같다(`rs-{case_id}`, `pending_officer`). 결과 proposal에 `status`(`approved`|`modified`|`rejected`), `officer_id`, `decided_at`, `decision_reason`, 수정이면 `sent_text`.

- [ ] **Step 1: 실패 테스트 작성** (기존 `send_script`·`route_hardship` 테스트는 `propose` 기준으로 바꾼다)

```python
def test_agent_cannot_send_anything():
    ctx = make_ctx()
    assert set(ctx.handlers()) == {"get_case", "get_contact_history", "list_scripts", "propose"}

def test_propose_once_and_masks_note():
    ctx = make_ctx(mask=lambda s: s.replace("김철수", "[[PERSON_1]]"))
    assert ctx.propose({"script_id": "hardship_offer", "hardship_category": "debt_overload", "hardship_note": "김철수 대출 중첩", "reason": "r"})["accepted"]
    assert ctx.propose({"script_id": "missed_payment", "hardship_category": "", "hardship_note": "", "reason": "r"})["error"] == "already_proposed"
    assert ctx.draft["hardship"]["note"] == "[[PERSON_1]] 대출 중첩"

def test_proposal_has_exact_text_evidence_and_checks():
    p = build_proposal(CASE, DRAFT_HARDSHIP, [], DAY, [{"script_id": "balance_warning", "on": "2026-11-28", "status": "sent"}], str)
    assert p["text"] == render_script("hardship_offer", CASE) and "1,170,000원" in p["text"]
    assert [c["id"] for c in p["checks"]] == ["approved_script", "contact_hours", "weekly_limit", "banned_words", "pii_tokens", "hardship_grounds"]
    assert p["evidence"]["escalation"][0]["script_id"] == "balance_warning"
    assert "[[" not in p["text"] and "{" not in p["text"]

def test_approve_sends_exact_text_with_officer():
    s = settle_proposal(P, "approve", text=None, include_hardship=None, reason="", contacts=[], now=DAY, officer_id="o-1", case=CASE)
    assert s.contact["text"] == P["text"] and s.contact["by"] == "officer" and s.contact["officer_id"] == "o-1"
    assert s.proposal["status"] == "approved" and s.errors == []

def test_modify_with_banned_word_sends_nothing():
    s = settle_proposal(P, "modify", text="압류 예정입니다", include_hardship=None, reason="", contacts=[], now=DAY, officer_id="o", case=CASE)
    assert s.contact is None and s.errors and s.proposal["status"] == "pending_officer"

def test_modify_can_drop_hardship():
    s = settle_proposal(P_HARDSHIP, "modify", text="고객님, 안내드립니다.", include_hardship=False, reason="", contacts=[], now=DAY, officer_id="o", case=CASE)
    assert s.restructure is None and s.contact["text"] == "고객님, 안내드립니다."

def test_reject_requires_reason():
    assert settle_proposal(P, "reject", text=None, include_hardship=None, reason="", contacts=[], now=DAY, officer_id="o", case=CASE).errors
    s = settle_proposal(P, "reject", text=None, include_hardship=None, reason="이미 입금 확인", contacts=[], now=DAY, officer_id="o", case=CASE)
    assert s.contact is None and s.proposal["status"] == "rejected"

def test_officer_send_at_night_is_deferred():
    s = settle_proposal(P, "approve", text=None, include_hardship=None, reason="", contacts=[], now=NIGHT, officer_id="o", case=CASE)
    assert s.contact["status"] == "deferred" and s.contact["reason"] == "contact_hours"

def test_scripts_render_without_placeholders_or_banned_words():
    for sid in SCRIPTS:
        assert message_issues(render_script(sid, CASE)) == []
```

- [ ] **Step 2: 실패 확인** — `pytest tests/agents/test_collections.py -q` → FAIL.

- [ ] **Step 3: 구현** — `INSTRUCTIONS`의 2~4번을 "보낼 스크립트와 재조정 상담 여부를 `propose`로 제안한다. 발송은 심사역이 승인해야 나간다"로 바꾸고 판단 규칙(첫 미납 `missed_payment`, 누적 2회 이상 또는 원인이 `HARDSHIP_CAUSES`면 `hardship_offer` + 재조정)은 유지한다. 모듈 docstring도 맞춘다. `act`의 decision은 draft 유무로 정한다(`proposed`/`none`). draft가 없으면(에이전트가 propose를 안 함) 폴백 draft를 만든다.

- [ ] **Step 4: 통과 확인** — `pytest tests/agents -q` → PASS(오케스트레이터는 Task 6에서 고친다).

- [ ] **Step 5: 커밋** — `feat: 추심 에이전트를 제안 전용으로 바꾸고 제안서·심사역 결정 처리 추가`

---

### Task 6: 오케스트레이터 대기 항목과 추심 승인 흐름

**Files:**
- Modify: `src/orchestrator/journey/models.py`
- Create: `src/orchestrator/journey/holds.py`
- Modify: `src/orchestrator/journey/monitoring.py` (`_after_week`, `_promote_if_decided` → `_refresh_hold`, `run_collections`, `_repayment_week`, `handle_restructure_decision`)
- Test: `tests/orchestrator/test_monitoring_handlers.py`, `tests/orchestrator/test_holds.py`(새)

**Interfaces:**
- Consumes: Task 4 `item_key`, `item_amount`, 키 기반 `week_events`; Task 5 `act`, `build_proposal`, `settle_proposal`, `RepaymentCase`.
- Produces:
  - `REPLAY_HELD = frozenset({"paused", "awaiting_resume", "awaiting_customer"})`.
  - 메시지 모델(모두 `extra="forbid"`): `RestructureDecision`에 `kind: Literal["restructure"] = "restructure"`; `CollectionsDecision(kind: Literal["collections"], loan_id, case_id, decision: Literal["approve","modify","reject"], officer_id, text: str | None = None (≤300), include_hardship: bool | None = None, reason: str = "" (≤500))`; `PlanChoice(kind: Literal["plan_choice"], loan_id, offer_id, option: Literal["move","split","keep"])`; `PlanDecision(kind: Literal["plan_change"], loan_id, change_id, decision: Literal["approve","decline"], officer_id, reason: str = "" (≤500))`; `OfferExpire(kind: Literal["offer_expire"], loan_id, offer_id, officer_id)`.
  - `OFFICER_MESSAGE: TypeAdapter` — 콜러블 `Discriminator`(dict의 `kind`, 없으면 `"restructure"`)와 `Tag`로 묶은 유니온.
  - `holds.py`: `HOLD_ORDER = ("collections_proposal", "customer_response", "plan_change", "restructure")`, `OFFICER_HOLDS = frozenset({"collections_proposal", "plan_change", "restructure"})`, `pending_holds(loan: Mapping, case: Mapping | None) -> list[str]`(HOLD_ORDER 순서), `held_status(holds: Sequence[str]) -> str`(`paused` | `awaiting_customer` | `awaiting_resume`).
    - `collections_proposal`: `case.proposal.status == "pending_officer"`; `restructure`: `case.restructure.status == "pending_officer"`; `customer_response`: `loan.repayment.offer.status == "open"`; `plan_change`: `loan.repayment.plan_change.status == "pending_officer"`.
  - 모니터링 문서 새 필드: `holds: list[str]`(멈출 때·갱신 때 기록).
  - 추심 건 문서: `proposal`(Task 5 형태), `status` `proposal_pending` → `contacted`|`deferred`|`rejected`|`restructure_pending`. `collections`는 `{decision: "proposed", reason, contacts: [], record, at}`. `recent_alert`에 `check_action` 추가.

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/orchestrator/test_holds.py
def test_pending_holds_order_and_status():
    loan = {"repayment": {"offer": {"status": "open"}, "plan_change": {"status": "pending_officer"}}}
    case = {"proposal": {"status": "pending_officer"}, "restructure": {"status": "pending_officer"}}
    assert pending_holds(loan, case) == list(HOLD_ORDER)
    assert held_status(["customer_response"]) == "awaiting_customer"
    assert held_status(["customer_response", "plan_change"]) == "paused"
    assert held_status([]) == "awaiting_resume"
    assert pending_holds({}, None) == []
```

```python
# tests/orchestrator/test_monitoring_handlers.py — ScriptedAgents의 Collections 분기는
# handlers["propose"]({"script_id": "hardship_offer", "hardship_category": "debt_overload", "hardship_note": "대출 중첩", "reason": "누적 미납"})
# 로 바꾸고 output decision="proposed". resume_until_done은 holds를 순서대로 풀도록 고친다(추심 승인 → 재조정 결정 → 재개).

def test_missed_payment_creates_proposal_and_pauses_without_contact(mock_app):
    deps = missing_first_due(make_deps(mock_app)); approved_loan(deps); replay(deps)
    case, mon = cases(deps)[0], loan(deps)["monitoring"]
    assert case["status"] == "proposal_pending" and case["proposal"]["text"]
    assert mon["status"] == "paused" and mon["holds"] == ["collections_proposal"]
    assert not [c for c in contacts(deps, mon["replay_id"]) if c["by"] in ("agent", "fallback", "officer")]

def test_approve_sends_exact_text_and_awaits_resume(mock_app): ...
    # decide_collections(deps, case_id, "approve") → 연락 1건(by=officer, text==proposal.text), 상태 awaiting_resume

def test_duplicate_collections_decision_sends_once(mock_app): ...   # 같은 메시지 두 번 → 두 번째 False, 연락 1건

def test_modify_with_banned_text_is_dropped(mock_app): ...          # False, 연락 0, proposal pending 유지, 재생 paused

def test_reject_sends_nothing_and_awaits_resume(mock_app): ...

def test_approve_with_hardship_waits_for_restructure(mock_app, ):    # ScriptedAgents 사용
    ...  # 승인 뒤 holds == ["restructure"], status paused → 재조정 승인 → awaiting_resume

def test_officer_approval_at_night_is_deferred_but_decided(mock_app): ...  # clock 23:00 → status deferred, 재생 awaiting_resume

def test_legacy_restructure_message_without_kind_still_decides(mock_app): ...
```

  기존 테스트 갱신: `test_repayment_updated_runs_collections_once`(연락 대신 제안 1회), `test_inline_replay_pauses_on_restructure_request_and_resumes`, `test_restructure_decision_requires_pending_request`, `test_week13_case_is_decidable_after_completion`, `test_decision_landing_between_pause_check_and_pause_write_still_awaits_resume`, `test_restructure_decision_for_other_alert_does_not_unpause`, `test_legacy_pause_alert_id_still_promotes`, `test_reminder_at_night_is_deferred`(→ 자동 안내는 시뮬레이션 10시라 밤에도 `sent`로 기대값 변경, 이름 `test_reminder_uses_simulated_10am`). 미납이면 이제 항상 멈춘다는 점을 반영한다.

- [ ] **Step 2: 실패 확인** — `pytest tests/orchestrator -q` → FAIL.

- [ ] **Step 3: 구현**
  - `_repayment_week`: Task 4 키 기반(4번 Task에서 이미 했으면 유지). 3일 전 안내는 `remind(..., sim_now(reminder_on(due)))`. `recent_alert`에 `check_action`.
  - `run_collections`: `act` → `build_proposal(case, result.draft, contacts, deps.clock(), escalation_sends, mask)`(`escalation_sends` = 연락 문서에서 `by == "early_warning"`). 연락 문서는 쓰지 않는다. 이미 `collections`가 있으면 아무것도 하지 않는다(지금과 같다).
  - `_after_week`: `holds = pending_holds(loan, case)`가 비면 `"next"`. 아니면 `status=held_status(holds)`, `paused_week`, `pause_case_id`, `holds`를 쓰고 `_refresh_hold` 호출 뒤 `"paused"`.
  - `_refresh_hold(deps, loan_id)`: 상태가 `paused`/`awaiting_customer`이면 `pause_case_id or pause_alert_id`의 문서로 holds를 다시 계산해 상태·holds를 갱신(비면 `awaiting_resume`). etag 재시도 `WRITE_ATTEMPTS`.
  - `handle_restructure_decision`: `OFFICER_MESSAGE.validate_json(body)`로 받고 `kind`로 분기. `restructure`는 지금 로직 + `_refresh_hold`. `collections`: 문서 `proposal.status == "pending_officer"`가 아니면 `False`. `settle_proposal`에 오류가 있으면 로그 후 `False`(문서 변경 없음). 아니면 추심 건 문서(제안·상태·재조정)를 먼저 쓰고 연락을 연락 문서에 더한 뒤 `_refresh_hold`. `plan_choice`·`plan_change`·`offer_expire`는 Task 7에서 채운다(여기선 `False`).

- [ ] **Step 4: 통과 확인** — `pytest tests/orchestrator tests/agents -q` → PASS.

- [ ] **Step 5: 커밋** — `feat: 추심 제안 심사역 승인과 재생 대기 항목 일반화`

---

### Task 7: 오케스트레이터 단계 발송·셀프 플랜·흐름 반영

**Files:**
- Modify: `src/orchestrator/journey/monitoring.py` (`process_week`, `_repayment_week`, `_mask_and_flow`, 결정 분기)
- Test: `tests/orchestrator/test_monitoring_handlers.py`

**Interfaces:**
- Consumes: Task 3 `next_stage`, `stage_message`, `auto_contact`, `plan_link`; Task 4 `plan_options`, `apply_change`, `apply_plan`, `main_platform`, `payout_days`, `week_events(..., early_from=)`, `early_reminder_on`; Task 6 `holds`, 메시지 모델.
- Produces (문서 필드):
  - `monitoring.alert_severities: list[str]`, `monitoring.escalation: {"stage1": {alert_id, on, status, reason}|None, "stage2": {...}|None}`.
  - 연락 ref: 1단계 `"{replay_id}-s1"`, 2단계 `"{replay_id}-s2"`, 5일 전 안내 `"{replay_id}-{key}-e"`(`by="early_reminder"`, `script_id="early_reminder"`, `text` 포함).
  - 회차 필드 `early_reminded_on`, `early_reminder_status`.
  - `repayment.offer = {offer_id: f"of-{replay_id}", week, sent_on, status: "open"|"chosen"|"kept"|"expired", options, choice, responded_at}`.
  - `repayment.plan_change = {change_id: f"pc-{replay_id}", option, items, replaces, status: "pending_officer"|"approved"|"declined", requested_at, officer_id, decided_at, reason}`.
  - `repayment.plan_history = [{change_id, option, applied_from, before, after, officer_id, decided_at}]`.
  - `_mask_and_flow`는 가명화기를 **원본** 흐름으로 만들고 `apply_plan(원본, plan_history)`를 돌려준다.

- [ ] **Step 1: 실패 테스트 작성** — 경보마다 `high`를 올리는 `HighAgents`(ScriptedAgents를 상속, `severity` 고정 `"high"`, 트리거가 새로 생기는 주마다 경보)를 추가한다.

```python
def test_first_high_alert_sends_balance_warning_and_early_reminders(mock_app): ...
    # 첫 high 경보 주차에 by=early_warning, script_id=balance_warning, status=sent 1건
    # 그 뒤 회차마다 ref "...-e" 5일 전 안내가 생기고, 3일 전 안내도 그대로 있다

def test_consecutive_high_alerts_open_offer_and_wait_for_customer(mock_app): ...
    # self_plan_offer 1건(text에 plan_link), offer.status open, options에 keep 포함, status awaiting_customer, holds ["customer_response"]

def test_stage_messages_are_sent_once_per_replay(mock_app): ...      # 이후 high가 더 나와도 s1·s2 각 1건

def test_auto_sends_ignore_real_night_clock(mock_app): ...            # clock 23:00에도 s1 sent

def test_deferred_stage_two_opens_no_offer(mock_app): ...             # 7일 7회를 채운 연락 문서를 미리 넣어 → s2 deferred, offer 없음, 재생 계속

def test_plan_choice_then_approval_changes_schedule_and_replay(mock_app): ...
    # PlanChoice(move) → plan_change pending, status paused, holds ["plan_change"]
    # PlanDecision(approve) → 일정 교체, plan_history 1건, awaiting_resume
    # resume → 옮긴 날짜에 출금 거래(txn_id "pc-...-i2")로 납부 또는 잔고 부족이면 미납, 원래 날짜에는 판정 없음
    # 공개된 주차(weeks[*].row) 값은 승인 전과 같다

def test_keep_choice_needs_no_officer(mock_app): ...                  # offer kept, plan_change 없음, awaiting_resume

def test_plan_decline_keeps_schedule(mock_app): ...                   # 사유 필수(빈 사유면 False), 일정 그대로

def test_offer_expire_then_late_choice_is_dropped(mock_app): ...      # OfferExpire → expired, awaiting_resume; 뒤늦은 PlanChoice → False

def test_duplicate_plan_decision_applies_once(mock_app): ...          # plan_history 1건

def test_stage_two_and_miss_same_week_need_both_cleared(mock_app): ...
    # holds ["collections_proposal", "customer_response"] → 추심 승인 뒤 awaiting_customer → 고객 keep 뒤 awaiting_resume
```

- [ ] **Step 2: 실패 확인** — `pytest tests/orchestrator -q` → 새 테스트 FAIL.

- [ ] **Step 3: 구현**
  - `process_week`에서 경보를 저장한 뒤 `mon["alert_severities"].append(severity)` → `stage = next_stage(...)`. 다음 `scheduled` 회차가 없으면 발송하지 않고 단계도 쓰지 않는다. 있으면 `stage_message`(금액 = 그 회차 금액, `balance` = `rows[-1].get("due_balance")`), `auto_contact(ref, ..., on=hi)`를 연락 문서에 더하고 `mon["escalation"]["stageN"]` 기록.
  - 2단계가 `sent`이면: `pg, lb = _ledger(...)`; `payouts = payout_days(pg, main_platform(lb), hi)`; `flow_end = flow_start + WEEKS*7 - 1일`; `options = plan_options(schedule, installment, after=hi, flow_end=flow_end, payouts=payouts)`. `keep` 말고 옵션이 하나라도 있으면 `offer`를 연다.
  - `_repayment_week`: `early_from = escalation.stage1.on`(있을 때)으로 `week_events`를 부르고 5일 전 안내를 `remind(ref, contacts, early_reminder_on(due), sim_now(...), script_id="early_reminder", by="early_reminder", text=TEMPLATES 채움)`으로 보낸다. 같은 ref가 이미 있으면 다시 보내지 않는다(3일 전 안내와 같은 방식).
  - `_after_week`는 Task 6 그대로(이번 주에 열린 offer가 `customer_response`로 잡힌다). 미납이 없는 주도 `pending_holds(loan, None)`을 본다.
  - 결정 분기: `plan_choice` — `offer.status == "open"`이고 `offer_id`가 같아야 함. `keep`이면 `status="kept"`. 그 밖이면 `chosen` + `plan_change` 생성. `plan_change` — `status == "pending_officer"`이고 `change_id`가 같아야 함; 반려는 사유 필수. 승인이면 `apply_change` 결과로 일정을 바꾸고 `plan_history`에 `applied_from = flow_start + paused_week*7일`과 before/after(키·날짜·금액)를 남긴다. `offer_expire` — `offer.status == "open"`이어야 함 → `expired`. 모두 대출 문서를 etag로 쓰고 `_refresh_hold`.

- [ ] **Step 4: 통과 확인** — `pytest tests/orchestrator tests/agents -q` → PASS.

- [ ] **Step 5: 커밋** — `feat: 조기경보 단계 자동 안내와 셀프 상환 플랜 반영`

---

### Task 8: 콘솔 대출 화면

**Files:**
- Modify: `src/console/app.py` (새 라우트 3개, `loan_page`에 연락 문서 전달)
- Modify: `src/console/adapters.py`, `src/console/monitoring.py` (`MonitoringStore.get_contacts`, 라벨, `loan_view`)
- Modify: `src/console/dashboard.py` (`case_steps`에 "심사역 승인" 단계)
- Modify: `src/console/templates/loan.html`
- Test: `tests/console/test_monitoring.py`, `tests/console/test_monitoring_view.py`

**Interfaces:**
- Consumes: Task 6 메시지 모델(`CollectionsDecision`, `PlanDecision`, `OfferExpire`, `RestructureDecision`), Task 5 `message_issues`, Task 3 템플릿 의미.
- Produces:
  - `MonitoringStore.get_contacts(loan_id: str, replay_id: str) -> dict | None`(`contacts-{replay_id}` 점 읽기). 테스트 대역에도 추가.
  - `MONITORING_LABELS`: `paused` → "심사역 결정 대기", `awaiting_customer` → "고객 응답 대기". `HOLD_LABELS = {"collections_proposal": "추심 승인 대기", "customer_response": "고객 응답 대기", "plan_change": "플랜 변경 승인 대기", "restructure": "재조정 결정 대기"}`. `CHECK_LABELS`(정책 확인 id → 한국어).
  - `loan_view` 결과: 경보마다 `auto_contacts`(그 경보의 `escalation` 단계 발송), 추심 건마다 `proposal_decidable`, 대출 수준 `offer`, `plan_change`, `plan_decidable`.
  - 라우트: `POST /loans/{loan_id}/cases/{case_id}/proposal`(폼: `decision`, `text`, `include_hardship`, `reason`), `POST /loans/{loan_id}/plan/{change_id}`(폼: `decision`, `reason`), `POST /loans/{loan_id}/offer/expire`. 모두 `decider` + `checked_form`, 심사역 ID는 로그인 주체, 큐는 `RESTRUCTURE_QUEUE`, 응답은 `loan_sent`. 재조정 결정 메시지는 `kind`를 포함해 보낸다.

- [ ] **Step 1: 실패 테스트 작성**

```python
def test_alert_card_shows_reason_and_check_action(client_with_alert): ...      # "이유", "확인할 조치", check_action 문장
def test_old_alert_without_check_action_renders(client_with_old_alert): ...
def test_proposal_card_shows_actions_evidence_checks_and_exact_text(client_with_proposal):
    html = get_loan(...)
    for s in ("제안 조치와 이유", "판단 근거", "정책 확인", "고객에게 보낼 문구", PROPOSAL["text"]): assert s in html
def test_proposal_approve_queues_collections_decision(...):   # 메시지 kind=collections, officer_id=로그인 사용자, decision=approve
def test_proposal_modify_with_banned_text_is_422_and_not_queued(...):
def test_proposal_reject_requires_reason(...):               # 422, 큐 0
def test_proposal_not_pending_is_409(...):
def test_plan_change_card_and_decline_requires_reason(...):
def test_offer_expire_queues_message_only_when_open(...):    # open 아니면 409
def test_pause_banner_lists_holds_in_order(...):             # "추심 승인 대기"가 "고객 응답 대기"보다 먼저
def test_case_without_proposal_renders_as_before(...):
```

- [ ] **Step 2: 실패 확인** — `pytest tests/console -q` → FAIL.

- [ ] **Step 3: 구현** — 템플릿:
  - 경보 카드: `summary`를 "이유", `check_action`을 "확인할 조치"로 나란히(`check_action`이 없으면 지금처럼 요약만). "이 경보로 자동 발송된 안내" 목록(단계, 스크립트, 날짜, 상태). 기존 "경보만으로는 연락하지 않고…" 안내 문구는 단계 자동 안내 설명으로 바꾼다.
  - 추심 건 카드: `proposal`이 있으면 스펙 §8 배치(제안 조치와 이유 / 판단 근거 / 정책 확인 ✔·✖ / 고객 문구 전체) + 결정 폼(승인·수정·반려 라디오, 수정 문구 textarea(기본값 제안 문구, 300자), 재조정 포함 체크박스(제안에 재조정이 있을 때만), 사유). 결정 뒤에는 결과(상태, 심사역, 보낸 문구)를 보인다. `proposal`이 없으면 지금 `collections_block`.
  - 플랜 카드: 제안(옵션 표, 상태)과 변경 요청(바뀔 일정 전→후, 승인·반려 폼), `offer.status == "open"`이면 "고객 응답 없이 계속" 버튼.
  - 멈춤 배너: `m.holds`를 `HOLD_LABELS` 순서대로, 각 항목 앵커 링크. `holds`가 없는 옛 문서는 지금 문구.
  - 상환 원장 표: 금액은 회차 금액, 분할은 "2-1회차"처럼, "5일 전 안내" 열 추가. 설명 문단을 새 규칙(3일 전 + 높음 경보 뒤 5일 전, 시뮬레이션 10시 기준, 미납이면 추심 제안 → 심사역 승인)으로 바꾼다.
  - 인라인 스크립트·스타일 없음(기존 `decision.js` 재사용).

- [ ] **Step 4: 통과 확인** — `pytest tests/console -q` → PASS.

- [ ] **Step 5: 커밋** — `feat: 콘솔 추심 승인·플랜 변경 승인 카드와 대기 배너`

---

### Task 9: 콘솔 차주 화면 셀프 플랜

**Files:**
- Modify: `src/console/app.py` (`borrower_page`, 새 라우트)
- Modify: `src/console/templates/borrower.html`
- Test: `tests/console/` 차주 화면 테스트 파일(기존 borrower 테스트가 있는 곳)

**Interfaces:**
- Consumes: Task 6 `PlanChoice`, Task 8 `get_contacts`, `monitoring_store().get_loan`.
- Produces: `POST /borrower/{applicant_id}/plan`(폼: `loan_id`, `offer_id`, `option`). 대출의 `applicant_id`가 경로와 다르면 404, `offer.status != "open"`이거나 `offer_id`가 다르면 409, 옵션이 제안에 없으면 422.

- [ ] **Step 1: 실패 테스트 작성**

```python
def test_borrower_sees_open_offer_with_option_schedules(...):   # id="self-plan" 섹션, 옵션별 바뀔 날짜·금액, 선택 버튼
def test_borrower_choice_queues_plan_choice(...):                # kind=plan_choice, option=move
def test_borrower_choice_on_expired_offer_is_409(...):
def test_borrower_choice_unknown_option_is_422(...):
def test_expired_offer_is_shown_as_expired(...):
def test_borrower_sees_received_notices(...):                    # 연락 문서의 by=early_warning·early_reminder·officer 문구
```

- [ ] **Step 2: 실패 확인** — 해당 테스트 → FAIL.

- [ ] **Step 3: 구현** — `borrower_page`가 신청별로 대출 문서와 연락 문서를 읽어 `plans=[{loan_id, offer, plan_change, notices}]`를 넘긴다. 섹션 제목 "상환 플랜 제안", 앵커 `id="self-plan"`. 상태 문구: open → 고르기, chosen → "심사역 확인 중", kept → "지금 일정 유지", expired → "만료", plan_change 결과(승인·반려). 받은 안내는 날짜·문구·상태.

- [ ] **Step 4: 통과 확인** — `pytest tests/console -q` → PASS.

- [ ] **Step 5: 커밋** — `feat: 차주 화면 셀프 상환 플랜 선택`

---

### Task 10: 평가에 단계 도달 기록

**Files:**
- Modify: `src/evals/suites/early_warning.py`
- Test: `tests/evals/test_early_warning_suite.py`

**Interfaces:**
- Consumes: Task 3 `next_stage`.
- Produces: `escalation_weeks(weeks: Sequence[Mapping]) -> dict[str, int | None]` → `{"stage1_week", "stage2_week"}`(경보가 난 주차의 `severity`로 `next_stage`를 차례로 적용, 발송은 모두 `sent`로 가정). 경로 행에 `escalation`, 설정 지표에 `stage1_paths`, `stage2_paths`.

- [ ] **Step 1: 실패 테스트 작성**

```python
def test_escalation_weeks():
    weeks = [{"week": 3, "severity": "high"}, {"week": 5, "severity": "medium"}, {"week": 7, "severity": "high"}, {"week": 9, "severity": "high"}]
    assert early_warning.escalation_weeks(weeks) == {"stage1_week": 3, "stage2_week": 9}

def test_fake_run_reports_stage_counts():
    out, model = _run(datasets.ew_paths())
    assert {"stage1_paths", "stage2_paths"} <= set(out["configs"][model])
```

- [ ] **Step 2~4:** 실패 확인 → 구현 → `pytest tests/evals -q` PASS.

- [ ] **Step 5: 커밋** — `feat: 조기경보 평가에 단계 도달 주차 기록`

---

### Task 11: 문서

**Files:**
- Modify: `docs/AGENTS.md`(조기경보 약 75행·추심 약 87행 절 — 수정 전 `docs/AGENTS.md`와 그 지시를 읽는다), `docs/limitations.md`, `demo/demo-script.md`, `demo/fallback.md`
- Test: `tests/docs`

- [ ] **Step 1:** `AGENTS.md` — 조기경보: 이유 + 확인할 조치, 신호 표, `due_shortfall`, 단계 표(1: 잔고 부족 알림 + 5일 전 추가 안내, 2: 이동·분할·링크 한 메시지 → 고객 응답 대기), L4 범위(회수·한도 축소·부도 확정 없음), 시뮬레이션 10시 기준. 추심: 제안만(`propose`), 제안서 4요소, 승인·수정·반려, 실제 시각 검사, 큐 `kind`.
- [ ] **Step 2:** `limitations.md` — 추심을 원문 L3보다 보수적으로 승인제로 둔 이유, 분할 이자 미반영, 급여일 → 주 정산 플랫폼 지급일, 강제 미납이 옮긴 날짜로 이어지지 않음(잔고 규칙만), 자동 발송 시뮬레이션 시각 기준, 실제 문자 발송 없음.
- [ ] **Step 3:** `demo-script.md` — 원인 셀러 재생 → 1단계 안내 → 2단계 안내로 고객 응답 대기 → 차주 화면에서 플랜 선택 → 심사역 승인 → 재개 → 바뀐 일정 판정 → 미납이면 추심 제안 승인·수정 장면. 어느 셀러가 2단계에 닿는지는 배포 뒤 실측값으로 채운다(자리 표시를 남기지 말고, 실측 전이면 그 문단을 이 Task에서 쓰지 않는다).
- [ ] **Step 4:** `fallback.md` — 고객 응답이 없을 때 "고객 응답 없이 계속", 밤 시간 심사역 발송 `deferred`, 에이전트 장애 시 폴백 제안도 승인 필요.
- [ ] **Step 5:** `pytest tests/docs -q` PASS → 커밋 `docs: 조기경보 단계 대응·추심 승인 문서화`

---

## 마무리(플랜 밖, 사용자 승인 필요)
- 전체 테스트 실행 → 오케스트레이터 → 콘솔 배포 → 원인 셀러 실측(2단계 도달 셀러 확인, `demo-script.md` 보완) → `docs/runbook/deploy-history.md` 추가 → 푸시.
