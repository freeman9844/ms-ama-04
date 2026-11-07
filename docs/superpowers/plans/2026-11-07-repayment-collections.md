# 상환 상태 기반 추심 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 추심을 조기경보(EW) 경보가 아니라 상환 상태(30일 주기 상환일의 납부·미납)로 시작하게 바꾸고, 상환일 3일 전 안내를 결정론으로 보낸다.

**Architecture:** 상환일 규칙을 `src/shared/repayment.py`에 두고 Mock 사후 흐름(출금 생성)과 오케스트레이터(상환 원장)가 같이 쓴다. 오케스트레이터는 재생 시작 때 대출 문서에 `repayment` 원장을 만들고, 주차마다 안내·납부·미납을 판정한다. 미납이면 `rc-` 추심 건 문서를 만들고 `repayment.updated`(Service Bus) 또는 인라인으로 추심 에이전트를 부른다. EW 경보는 심사역 검토용으로만 남고 차주 연락을 하지 않는다.

**Tech Stack:** Python 3.12, Pydantic v2, Azure Functions(Service Bus trigger), Cosmos DB, Flask+Jinja2 콘솔, Bicep, pytest.

**Spec:** `docs/superpowers/specs/2026-11-07-repayment-collections-design.md`

## Global Constraints

- 상환 주기 30일, 안내는 상환일 3일 전, 출금 적요 `에이엠에이렌딩 대출상환`(스펙 §3.1).
- 상환일: 시작일+30k일을 다음 영업일로 밀되 재생 범위(시작일+days-1)를 넘으면 앞 영업일로 당긴다(스펙 §3.1).
- 납부 판정은 상환일 당일 같은 적요의 음수 출금 거래로만 한다(스펙 §3.3).
- 안내(`payment_reminder`)는 LLM 없이 결정론으로 보내고 `fair_check`(연락 시간·주간 한도)를 지킨다(스펙 §3.5).
- EW 경보만으로는 차주에게 연락하지 않는다. 추심 에이전트는 미납 건에서만 돈다(스펙 §2).
- 추심 건 ID `rc-{replay_id}-i{no}`, 재조정 요청 ID `rs-{case_id}`, 메시지 ID `repayment.updated:{case_id}`, 세션 ID = loan_id(스펙 §3.7).
- 콘솔 템플릿에는 인라인 style·script 금지(CSP). 파일은 CRLF, UTF-8(BOM 없음).
- 토큰·구독 ID·IP를 출력하거나 커밋하지 않는다. `memory261105.md`, `ops/reports/load-20261107073503/`는 커밋하지 않는다.

### 스펙과 다른 점(계획에서 확정)

1. **`due_dates(start, days)`**: 스펙은 `(start, end)`로 썼으나 호출자가 모두 `days`(=`POSTLOAN_DAYS`/`WEEKS*7`)를 가지고 있어 `(start, days)`로 한다. 범위 끝은 `start + days - 1`.
2. **13주차 미납(스펙 §3.6)**: `process_week`는 13주차에서 `status="completed"`를 먼저 쓰므로 `_after_week`가 `"done"`을 돌려 멈추지 않는다. 재생은 완료되고 재조정 요청은 `pending_officer`로 남아 결정할 수 있다. `handle_restructure_decision`은 결정을 기록하고 `_promote_if_decided`는 `paused`가 아니라 아무것도 하지 않으므로 상태는 `completed`로 남는다. 추가 코드가 필요 없다.
3. **데모 B 장면 영향**: 데모 셀러(`seller-dailylook-01`, STACKING)는 마지막 상환일(13주차)에만 미납이 확정되므로 재생이 중간에 멈추지 않는다. B는 "재생 완료 + 13주차 미납 추심 건에 재조정 요청 대기"가 된다. 재개 카드는 나오지 않는다(앞 주차 미납이면 재개는 여전히 동작). 데모 대본·대체 자료를 T7에서 고친다.

## Review Focus

1. **밤 시간 안내**: 안내일 처리 시각이 연락 가능 시간 밖이면 보내지 않고 `deferred`+`contact_hours`로 남는다 → T3 `test_remind_defers_at_night`.
2. **`repayment.updated` 재전달**: 같은 메시지가 두 번 와도 추심이 다시 연락하지 않고 다음 주차를 두 번 발행하지 않는다 → T4 `test_redelivered_repayment_updated_is_noop`.
3. **배포 전 멈춘 대출(`pause_alert_id`)**: 옛 문서도 결정 뒤 재개 대기로 올라간다 → T4 `test_legacy_pause_alert_id_still_promotes`.
4. **완료 뒤 13주차 결정**: 재생 완료 뒤에도 재조정 결정이 기록되고 상태는 `completed`로 남는다 → T4 `test_week13_case_decidable_after_completion`.
5. **재생 다시 시작·안내 재시도**: 새 재생은 원장을 새로 만들고, 같은 주차 재시도는 안내를 두 번 보내지 않는다 → T4 `test_new_replay_rebuilds_ledger`, `test_reminder_not_duplicated_on_retry`.

## 공통 명령

- 테스트: `$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python -m pytest -q -p no:cacheprovider <경로>`
- 전체(인프라 포함, 약 5분): 위 명령에 `$env:RUN_INFRA_TESTS='1';`를 앞에 붙이고 경로 없이.
- 커밋: 메시지를 임시 파일에 쓰고 `git commit -F <파일>`. 끝에 다음 트레일러:

```
Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
Copilot-Session: 05f1ca1b-6073-417d-bfb1-6b546f82cb58
```

---

### Task 1: 상환일 공유 규칙과 Mock 출금

**Files:**
- Create: `src/shared/repayment.py`
- Create: `tests/shared/test_repayment.py`
- Modify: `src/mockapis/postloan.py` (`INSTALLMENT_DAY` 33 삭제, `PostLoanFlow` 54, `_installments` 74, 마지막 루프 ~196)
- Test: `tests/mockapis/test_postloan.py`

**Interfaces:**
- Produces: `shared.repayment.LENDER_DEBIT: str`, `CYCLE_DAYS = 30`, `REMINDER_DAYS = 3`, `due_dates(start: date, days: int) -> list[date]`; `PostLoanFlow.missed_installments: tuple[date, ...]`(마지막 필드, 기본 `()`).

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/shared/test_repayment.py`

```python
from datetime import date, timedelta

import pytest

from shared.krcalendar import is_business_day
from shared.repayment import due_dates

START = date(2026, 11, 2)


def test_due_dates_for_demo_start():
    assert due_dates(START, 91) == [date(2026, 12, 2), date(2027, 1, 4), date(2027, 1, 29)]


def test_due_dates_short_ranges():
    assert due_dates(START, 30) == []
    assert due_dates(START, 31) == [date(2026, 12, 2)]


@pytest.mark.parametrize("start", [date(2026, 1, 5) + timedelta(weeks=k) for k in range(52)])
def test_three_business_dues_inside_range(start):
    dues = due_dates(start, 91)
    assert len(dues) == 3 and dues == sorted(set(dues))
    assert all(is_business_day(d) and start < d <= start + timedelta(days=90) for d in dues)
```

`tests/shared/`가 없으면 기존 `tests/agents/`처럼 `__init__.py` 여부를 확인해 같은 방식으로 만든다.

- [ ] **Step 2: 실패 확인** — `pytest tests/shared/test_repayment.py` → `ModuleNotFoundError: shared.repayment`

- [ ] **Step 3: 구현** — `src/shared/repayment.py`

```python
"""상환 스케줄 공유 규칙(스펙 §3.1). Mock 사후 흐름과 오케스트레이터 원장이 같은 상환일을 쓴다."""
from datetime import date, timedelta

from shared.krcalendar import next_business_day, previous_business_day

LENDER_DEBIT = "에이엠에이렌딩 대출상환"
CYCLE_DAYS = 30
REMINDER_DAYS = 3


def due_dates(start: date, days: int) -> list[date]:
    """start부터 days일 범위 안의 상환일. 휴일이면 다음 영업일, 범위를 넘으면 앞 영업일."""
    last = start + timedelta(days=days - 1)
    out: list[date] = []
    k = 1
    while (d := start + timedelta(days=CYCLE_DAYS * k)) <= last:
        due = next_business_day(d)
        out.append(due if due <= last else previous_business_day(d))
        k += 1
    return out
```

- [ ] **Step 4: 통과 확인** — `pytest tests/shared/test_repayment.py` → PASS

- [ ] **Step 5: Mock 출금 테스트 작성** — `tests/mockapis/test_postloan.py` 끝에 추가(기존 stacking 테스트의 as_of 4개 값을 그대로 쓴다)

```python
from shared.repayment import LENDER_DEBIT, due_dates


@pytest.mark.parametrize("as_of", [date(2026, 11, 2), date(2026, 3, 30), date(2026, 6, 15), date(2026, 12, 28)])
@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("persona", [None, *DEMO_PERSONAS])
@pytest.mark.parametrize("scenario", list(PostLoanScenario))
def test_installments_follow_shared_due_dates(as_of, seed, persona, scenario):
    case = generate_case(f"x{seed}", Archetype.HEALTHY_SEASONAL, as_of, seed, persona=persona)
    flow = build_postloan_flow(case, scenario, as_of)
    paid = [t for t in flow.transactions if t.description == LENDER_DEBIT]
    assert all(t.amount_krw == -INSTALLMENT_KRW and t.balance_after_krw >= 0 for t in paid)
    assert sorted([t.booked_on for t in paid] + list(flow.missed_installments)) == due_dates(as_of, POSTLOAN_DAYS)
    if scenario is PostLoanScenario.STABLE:
        assert flow.missed_installments == ()
    if scenario is PostLoanScenario.STACKING:
        assert flow.missed_installments[-1] == due_dates(as_of, POSTLOAN_DAYS)[-1]
```

이름(`build_postloan_flow`, `PostLoanScenario`, `DEMO_PERSONAS`, `generate_case`, `Archetype`)은 파일 상단의 기존 import·기존 stacking 테스트가 쓰는 이름과 맞춘다. 기존 as_of 값이 다르면 기존 테스트의 4개 값을 복사한다.

- [ ] **Step 6: 실패 확인** — `AttributeError: missed_installments`

- [ ] **Step 7: Mock 구현** — `src/mockapis/postloan.py`
  - `from shared.repayment import LENDER_DEBIT, due_dates` 추가, `INSTALLMENT_DAY` 삭제.
  - `PostLoanFlow` 마지막에 `missed_installments: tuple[date, ...] = ()`.
  - `_installments`:

```python
def _installments(start: date, days: int) -> list[Event]:
    return [
        Event(d, -INSTALLMENT_KRW, LENDER_DEBIT, "AUTO_DEBIT", TxnCategory.LOAN_REPAYMENT)
        for d in due_dates(start, days)
    ]
```

  - 호출부: `extra=[*_installments(start, days), ...]`.
  - 마지막 잔고 루프: 루프 앞에 `dues = due_dates(start, days)`, `missed: list[date] = []`. 각 이벤트에서

```python
        if e.description == LENDER_DEBIT and e.amount_krw < 0 and (
            balance < -e.amount_krw or (stacking and e.booked_on == dues[-1])
        ):
            missed.append(e.booked_on)
            continue
```

  를 잔고 반영 전에 두고, `txn_id=f"{prefix}{len(transactions):05d}"`로 바꿔 건너뛴 이벤트가 번호를 비우지 않게 한다(미납이 없으면 이전과 같은 번호). 반환에 `missed_installments=tuple(missed)`. `stacking`은 이 함수가 이미 쓰는 시나리오 판정 변수 이름을 그대로 쓴다.

- [ ] **Step 8: 통과·회귀 확인**

Run: `pytest tests/shared tests/mockapis tests/agents/test_monitoring.py tests/agents/test_early_warning.py tests/evals`
Expected: PASS. 특히 `test_postloan.py` 기본 연체 테스트(46행, 70~90일)와 `tests/agents/test_monitoring.py:145,156-157`(첫 경보·부족이 `default_on`보다 앞섬)이 그대로 통과해야 한다.

- [ ] **Step 9: 커밋** — `feat(repayment): 30일 주기 상환일 공유 규칙과 Mock 출금·미납`

---

### Task 2: 상환 원장 함수와 전망의 상환일

**Files:**
- Create: `src/agents/repayment.py`
- Create: `tests/agents/test_repayment.py`
- Modify: `src/agents/forecast.py:124-149`, `src/agents/monitoring.py:160-172`
- Test: `tests/agents/test_forecast.py`

**Interfaces:**
- Consumes: `shared.repayment.{LENDER_DEBIT, REMINDER_DAYS, due_dates}`
- Produces:
  - `build_schedule(start: date, days: int, installment_krw: int) -> dict`
  - `reminder_on(due: date) -> date`
  - `RepaymentWeek(reminders: tuple[int, ...], paid: tuple[tuple[int, str], ...], missed: tuple[int, ...])`
  - `week_events(schedule: list[dict], txns: Sequence[Transaction], lo: date, hi: date) -> RepaymentWeek`
  - `forecast.project(..., installment_due: Sequence[date] | None = None)`, `monitoring.projection(..., installment_due=None)`

- [ ] **Step 1: 실패하는 테스트** — `tests/agents/test_repayment.py`

```python
from datetime import date

from agents.repayment import RepaymentWeek, build_schedule, reminder_on, week_events
from shared.models import Transaction  # test_forecast.py가 쓰는 Transaction import 경로와 맞춘다
from shared.repayment import LENDER_DEBIT

START = date(2026, 11, 2)


def _debit(on, desc=LENDER_DEBIT, amount=-287_000, txn_id="t1"):
    return Transaction(txn_id=txn_id, booked_on=on, amount_krw=amount, balance_after_krw=0,
                       description=desc, txn_type_code="AUTO_DEBIT")


def test_build_schedule():
    led = build_schedule(START, 91, 287_000)
    assert led["installment_krw"] == 287_000
    assert [s["due_on"] for s in led["schedule"]] == ["2026-12-02", "2027-01-04", "2027-01-29"]
    assert led["schedule"][0] == {"no": 1, "due_on": "2026-12-02", "status": "scheduled", "reminded_on": None,
                                  "reminder_status": None, "txn_id": None, "case_id": None}


def test_reminder_on():
    assert reminder_on(date(2026, 12, 2)) == date(2026, 11, 29)


def test_week_events_reminder_paid_missed():
    sched = build_schedule(START, 91, 287_000)["schedule"]
    # 4주차(11/23~11/29): 1회차 안내만
    assert week_events(sched, [], date(2026, 11, 23), date(2026, 11, 29)) == RepaymentWeek((1,), (), ())
    # 5주차: 당일 출금이 있으면 납부
    w5 = week_events(sched, [_debit(date(2026, 12, 2))], date(2026, 11, 30), date(2026, 12, 6))
    assert w5 == RepaymentWeek((), ((1, "t1"),), ())
    # 다른 날짜·다른 적요·양수는 납부가 아니다
    others = [_debit(date(2026, 12, 1)), _debit(date(2026, 12, 2), desc="다른 상환"),
              _debit(date(2026, 12, 2), amount=287_000)]
    assert week_events(sched, others, date(2026, 11, 30), date(2026, 12, 6)) == RepaymentWeek((), (), (1,))


def test_week_events_skips_done_items():
    sched = build_schedule(START, 91, 287_000)["schedule"]
    sched[0].update(status="paid", reminded_on="2026-11-29")
    assert week_events(sched, [], date(2026, 11, 23), date(2026, 12, 6)) == RepaymentWeek((), (), ())
```

`Transaction`의 실제 import 경로는 `tests/agents/test_forecast.py` 상단을 보고 맞춘다.

- [ ] **Step 2: 실패 확인** — `ModuleNotFoundError: agents.repayment`

- [ ] **Step 3: 구현** — `src/agents/repayment.py`

```python
"""상환 원장(스펙 §3.3): 재생 시작 때 스케줄을 만들고 주차마다 안내·납부·미납을 판정한다."""
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from shared.repayment import LENDER_DEBIT, REMINDER_DAYS, due_dates


def build_schedule(start: date, days: int, installment_krw: int) -> dict[str, Any]:
    return {
        "installment_krw": installment_krw,
        "schedule": [
            {"no": i, "due_on": d.isoformat(), "status": "scheduled", "reminded_on": None,
             "reminder_status": None, "txn_id": None, "case_id": None}
            for i, d in enumerate(due_dates(start, days), start=1)
        ],
    }


def reminder_on(due: date) -> date:
    return due - timedelta(days=REMINDER_DAYS)


@dataclass(frozen=True)
class RepaymentWeek:
    reminders: tuple[int, ...]
    paid: tuple[tuple[int, str], ...]
    missed: tuple[int, ...]


def week_events(schedule: list[dict[str, Any]], txns: Sequence[Any], lo: date, hi: date) -> RepaymentWeek:
    reminders, paid, missed = [], [], []
    for item in schedule:
        due = date.fromisoformat(item["due_on"])
        if item["reminded_on"] is None and lo <= reminder_on(due) <= hi:
            reminders.append(item["no"])
        if item["status"] != "scheduled" or not lo <= due <= hi:
            continue
        hit = next((t for t in txns if t.booked_on == due and t.description == LENDER_DEBIT and t.amount_krw < 0), None)
        if hit is None:
            missed.append(item["no"])
        else:
            paid.append((item["no"], hit.txn_id))
    return RepaymentWeek(tuple(reminders), tuple(paid), tuple(missed))
```

`test_week_events_skips_done_items`에서 1회차는 `reminded_on`이 있고 `paid`라 둘 다 건너뛴다.

- [ ] **Step 4: 통과 확인** — `pytest tests/agents/test_repayment.py`

- [ ] **Step 5: 전망 테스트** — `tests/agents/test_forecast.py`에 추가

```python
def test_projection_uses_given_installment_dates():
    schedule = {"items": [], "variable_ratio": 0.0}
    inside = project(_profile(schedule), [_row(1_000_000)], [], 287_000, installment_due=[date(2026, 11, 20)])
    assert inside["totals"]["installment"] == -287_000
    outside = project(_profile(schedule), [_row(1_000_000)], [], 287_000, installment_due=[date(2027, 3, 2)])
    assert "installment" not in outside["totals"]
```

`outside`에서 키가 0으로 남는 구현이면 `outside["totals"].get("installment", 0) == 0`으로 쓴다(기존 `totals` 구성 방식을 보고 정한다).

- [ ] **Step 6: 실패 확인** — `TypeError: unexpected keyword 'installment_due'`

- [ ] **Step 7: 구현**
  - `forecast.project(settlement, rows, high_cost, installment_krw, installment_due: Sequence[date] | None = None)`. 147~149행의 상환일 계산을

```python
    dates = (
        [d for d in installment_due if lo <= d <= hi]
        if installment_due is not None
        else _due(INSTALLMENT_DAY, lo, hi)
    )
```

  로 바꾼다. `forecast.INSTALLMENT_DAY`는 원장이 없는 옛 대출용 대체값으로 남긴다.
  - `agents.monitoring.projection(rows, transactions, categories, baseline, installment_due=None)`이 `project(..., installment_due=installment_due)`로 넘긴다.

- [ ] **Step 8: 통과 확인** — `pytest tests/agents`

- [ ] **Step 9: 커밋** — `feat(repayment): 상환 원장 판정과 전망의 실제 상환일`

---

### Task 3: 추심 에이전트를 미납 건 기준으로

**Files:**
- Modify: `src/agents/collections.py` (전체: 25 `AUTO_ACTIONS`, 28 `SCRIPTS`, 48 `FALLBACK_SCRIPT`, 50 `INSTRUCTIONS`, ~66 `TOOLS`, ~136 `CollectionsContext`, ~215~245 `_fallback`/`act`)
- Test: `tests/agents/test_collections.py`(재작성)

**Interfaces:**
- Produces:
  - `RepaymentCase(case_id: str, loan_id: str, installment_no: int, due_on: date, amount_krw: int, missed_count: int, recent_alert: dict | None)`, `RepaymentCase.from_doc(doc: dict) -> RepaymentCase`
  - `CollectionsContext(case: RepaymentCase, contacts: list[dict], now: datetime, mask: Callable)`
  - 도구 `get_case`(기존 `get_alert` 대체), 스크립트 `missed_payment`·`payment_reminder`
  - `remind(ref: str, contacts: list[dict], on: date, now: datetime) -> dict` → `{"ref","on","at","script_id":"payment_reminder","status","reason","by":"reminder"}`
  - `act(ctx, runner, model)`는 그대로(반환 `CollectionsResult`의 `decision, reason, contacts, record, restructure, conversations`).

- [ ] **Step 1: 테스트 재작성** — `tests/agents/test_collections.py`. 기존 파일의 `ON`, `NOW`(낮), 밤 시각, `mask`, 스텁 runner 도우미는 그대로 두고 경보 빌더를 바꾼다.

```python
from agents.collections import CollectionsContext, RepaymentCase, act, remind


def case(recent=None, missed_count=1):
    return RepaymentCase(case_id="rc-loan-1-i1", loan_id="loan-1", installment_no=1, due_on=ON,
                         amount_krw=287_000, missed_count=missed_count, recent_alert=recent)


def ctx(c=None, contacts=None, now=NOW):
    return CollectionsContext(case=c or case(), contacts=contacts or [], now=now, mask=mask)


def test_from_doc():
    doc = {"case_id": "rc-r-i2", "loan_id": "loan-1", "installment": {"no": 2, "due_on": "2027-01-04",
           "amount_krw": 287_000}, "missed_count": 2, "recent_alert": None}
    c = RepaymentCase.from_doc(doc)
    assert (c.installment_no, c.due_on, c.missed_count) == (2, date(2027, 1, 4), 2)


def test_get_case_masks_recent_alert_summary():
    c = case(recent={"alert_id": "ew-1", "week": 3, "severity": "high", "summary": "홍길동 대표 고금리 대출"})
    out = ctx(c).handlers["get_case"]({})
    assert out["case_id"] == "rc-loan-1-i1" and out["installment_no"] == 1
    assert "홍길동" not in out["recent_alert"]["summary"]


def test_route_hardship_request_id():
    out = ctx().handlers["route_hardship"]({"reason": "debt_overload", "summary": "s"})
    assert out["request_id"] == "rs-rc-loan-1-i1"


@pytest.mark.parametrize("severity,script", [("high", "hardship_offer"), ("medium", "missed_payment"), (None, "missed_payment")])
def test_fallback_script_by_recent_alert(severity, script):
    recent = None if severity is None else {"alert_id": "ew-1", "week": 3, "severity": severity, "summary": "s"}
    result = act(ctx(case(recent=recent)), runner=None, model="m")
    assert [c["script_id"] for c in result.contacts] == [script]
    assert result.contacts[0]["by"] == "fallback" and result.contacts[0]["ref"] == "rc-loan-1-i1"


def test_remind_sends_in_daytime():
    out = remind("r-i1", [], ON, NOW)
    assert out["status"] == "sent" and out["script_id"] == "payment_reminder" and out["by"] == "reminder"


def test_remind_defers_at_night():
    out = remind("r-i1", [], ON, NIGHT)
    assert out["status"] == "deferred" and out["reason"] == "contact_hours"


def test_remind_respects_weekly_limit():
    full = [{"status": "sent", "at": NOW.isoformat()} for _ in range(WEEKLY_LIMIT)]
    assert remind("r-i1", full, ON, NOW)["reason"] == "weekly_limit"
```

`act(..., runner=None)`이 대체 경로를 타는 방식, 밤 시각 상수 이름, 주간 한도 상수 이름은 기존 테스트가 쓰는 것을 그대로 쓴다. 기존 `send_script` 공정 추심 테스트는 `ctx()`만 바꿔 유지하고 `test_no_automatic_contact_without_contact_action`은 지운다.

- [ ] **Step 2: 실패 확인** — `ImportError: RepaymentCase`

- [ ] **Step 3: 구현** — `src/agents/collections.py`
  - 모듈 docstring: "미납 추심 에이전트. 상환일 출금이 없을 때만 돈다. EW 경보는 참고 정보(`recent_alert`)일 뿐 연락 사유가 아니다. 상환 안내는 `remind`가 LLM 없이 보낸다."
  - `EarlyWarningAlert` import, `AUTO_ACTIONS`, `FALLBACK_SCRIPT`, act 게이트(경보 액션이 연락이 아니면 건너뛰기) 삭제.
  - `SCRIPTS`에 추가:

```python
    "missed_payment": {
        "purpose": "미납 안내",
        "text": "고객님, 이번 상환일에 자동이체가 처리되지 않아 안내드립니다. 납부 방법이나 일정 조정이 필요하시면 회신 주시면 함께 방법을 찾겠습니다.",
    },
```

  (`payment_reminder`가 없으면 `"purpose": "상환 안내"`, `"text": "고객님, 3일 뒤 대출 상환일입니다. 결제 계좌 잔액을 확인해 주세요."`로 추가.)
  - 새 데이터 클래스:

```python
@dataclass(frozen=True)
class RepaymentCase:
    case_id: str
    loan_id: str
    installment_no: int
    due_on: date
    amount_krw: int
    missed_count: int
    recent_alert: dict[str, Any] | None

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> "RepaymentCase":
        inst = doc["installment"]
        return cls(doc["case_id"], doc["loan_id"], inst["no"], date.fromisoformat(inst["due_on"]),
                   inst["amount_krw"], doc["missed_count"], doc.get("recent_alert"))
```

  - `CollectionsContext`: 필드 `alert` → `case`. `_entry`는 `"ref": self.case.case_id`, 연락일 `on=self.case.due_on`; `send`는 `fair_check(..., self.case.due_on, self.now)`; `get_contact_history`도 `case.due_on`; `route_hardship`의 `request_id=f"rs-{self.case.case_id}"`.
  - 도구 `get_case`(TOOLS·handlers에서 `get_alert` 대체):

```python
    def _get_case(self, _: dict[str, Any]) -> dict[str, Any]:
        c = self.case
        recent = None
        if c.recent_alert:
            recent = {**c.recent_alert, "summary": self.mask(c.recent_alert.get("summary") or "")}
        return {"case_id": c.case_id, "installment_no": c.installment_no, "due_on": c.due_on.isoformat(),
                "amount_krw": c.amount_krw, "missed_count": c.missed_count, "recent_alert": recent}
```

  TOOLS 스키마 설명: "미납 추심 건(회차·상환일·금액·누적 미납 횟수·최근 조기경보 요약)을 읽는다."
  - `INSTRUCTIONS`:

```python
INSTRUCTIONS = """너는 소상공인 대출의 미납 추심 담당이다. 상환일 자동이체가 처리되지 않은 건만 받는다.
1. get_case로 미납 회차·누적 미납·최근 조기경보를 읽고 get_contact_history로 연락 이력을 본다.
2. list_scripts에서 골라 send_script로 한 번 연락한다. 첫 미납은 missed_payment가 기본이다.
3. 누적 미납이 2회 이상이거나 최근 조기경보가 고금리 대출 누적·매출 급감을 가리키면 hardship_offer를 보내고 route_hardship으로 재조정 상담을 요청한다.
4. 조기경보만으로는 재조정 사유가 아니다. 미납 사실과 함께 볼 때만 근거로 쓴다.
5. 연락 시간·주간 한도를 어기는 발송은 도구가 막는다. 막히면 다시 시도하지 말고 이유를 남긴다.
6. 위협·압박 표현, 제3자 언급, 개인정보 노출을 하지 않는다."""
```

  기존 공정 추심 문장이 더 있으면 6번 뒤에 그대로 둔다.
  - act 프롬프트: `f"미납 건 {ctx.case.case_id}({ctx.case.installment_no}회차)를 처리하라."`
  - `_fallback`:

```python
def _fallback(ctx: CollectionsContext) -> list[dict[str, Any]]:
    high = (ctx.case.recent_alert or {}).get("severity") == "high"
    ctx.send("hardship_offer" if high else "missed_payment", by="fallback")
    return ctx.sent
```

  (기존 `_fallback`의 반환·기록 방식에 맞추고 "항상 보낸다"만 지킨다.)
  - `remind`:

```python
def remind(ref: str, contacts: list[dict[str, Any]], on: date, now: datetime) -> dict[str, Any]:
    """상환일 3일 전 안내. LLM 없이 공정 추심 규칙만 적용한다."""
    reason = fair_check(contacts, on, now)
    return {"ref": ref, "on": on.isoformat(), "at": now.isoformat(), "script_id": "payment_reminder",
            "status": "sent" if reason is None else "deferred", "reason": reason, "by": "reminder"}
```

  `fair_check`의 실제 인자 순서·반환(사유 문자열 또는 None)을 확인해 맞춘다.

- [ ] **Step 4: 통과 확인** — `pytest tests/agents/test_collections.py`

- [ ] **Step 5: 커밋** — `refactor(collections): 추심 에이전트를 미납 건 기준으로, 상환 안내 추가`

---

### Task 4: 오케스트레이터 상환 원장·추심 건·`repayment.updated`

**Files:**
- Modify: `src/orchestrator/journey/models.py` (`AlertRaised` 145 아래)
- Modify: `src/orchestrator/journey/monitoring.py` (`start_replay` 84~103, `_run_weeks`, `_after_week`, `_promote_if_decided`, `_continue`, `process_week`, `_publish_alert`, `handle_flow_update`, `run_collections`, `handle_alert_raised` 삭제, 새 `handle_repayment_updated`)
- Test: `tests/orchestrator/test_monitoring_handlers.py`(대폭 수정)

**Interfaces:**
- Consumes: T2 `build_schedule`, `week_events`, `reminder_on`; T3 `RepaymentCase`, `CollectionsContext(case=...)`, `remind`; `agents.features.monthly_installment(amount_krw, tenor_months)`.
- Produces: `TOPIC_REPAYMENT = "repayment.updated"`, `RepaymentUpdated(type, loan_id, case_id)`, `handle_repayment_updated(deps, body) -> dict | None`, `run_collections(deps, loan_id, case_id)`, 대출 문서 `repayment` 원장, `monitoring.pause_case_id`·`case_ids`, `ALERTS` 컨테이너의 `rc-` 문서(아래 형식), 주차 기록의 `repayment`·`case_id`.

추심 건 문서(`ALERTS` 컨테이너, 파티션 loan_id):

```python
{
    "id": case_id, "kind": "repayment_case", "case_id": case_id, "loan_id": loan_id, "replay_id": replay_id,
    "week": week, "as_of": due_on,
    "installment": {"no": no, "due_on": due_on, "amount_krw": installment_krw},
    "missed_count": missed_count, "recent_alert": recent_alert,  # {alert_id, week, severity, summary} | None
    "status": "open", "created_at": now, "collections": None, "restructure": None, "event_error": None,
}
```

- [ ] **Step 1: 테스트 도우미 수정** — `tests/orchestrator/test_monitoring_handlers.py`

```python
from shared.repayment import LENDER_DEBIT, due_dates
from orchestrator.journey.monitoring import TOPIC_REPAYMENT, handle_repayment_updated


class DropDebits:
    """특정 상환일 출금을 흐름에서 빼 미납을 만든다(FlowDown처럼 나머지는 위임)."""

    def __init__(self, inner, dates):
        self.inner, self.dates = inner, set(dates)

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def fetch(self, kind, token):
        out = self.inner.fetch(kind, token)
        if kind == "flow":
            out = {**out, "transactions": [t for t in out["transactions"]
                   if not (t["description"] == LENDER_DEBIT and t["booked_on"] in self.dates)]}
        return out


def cases(deps):
    return [d for d in deps.store.items(ALERTS) if d["id"].startswith("rc-")]
```

`fetch`의 실제 반환 모양(dict 키 이름)과 `MemoryStore`의 전체 조회 메서드 이름은 `FlowDown`·`alerts(deps)` 도우미를 보고 맞춘다.
- `pump`가 `TOPIC_FLOW`와 `TOPIC_REPAYMENT`를 처리(`TOPIC_ALERT`는 구독자가 없으니 버린다).
- `paused_inline`: `approved_loan` 뒤 `deps = replace(deps, mock=DropDebits(deps.mock, {due_dates(AS_OF, WEEKS * 7)[0].isoformat()}))`, 재생 뒤 `flow_start == AS_OF.isoformat()`를 확인하고 5주차 멈춤, `pause_case_id == f"rc-{replay_id}-i1"`을 기대한다. 이후 모든 호출에 바뀐 deps를 쓴다.
- `resume_until_done`·경쟁 테스트·다른 경보 테스트는 `pause_alert_id` → `pause_case_id`(다른 경보 테스트는 `"rc-elsewhere"`).

- [ ] **Step 2: 새 테스트 작성**

```python
def test_inline_replay_without_agents_runs_collections_only_on_missed():
    deps = make_deps(agents=None)
    loan_id, replay_id = replay(deps)  # 기존 인라인 재생 도우미
    doc = loan(deps)
    sched = doc["repayment"]["schedule"]
    assert sched[-1]["status"] == "missed"
    assert all(a["collections"] is None for a in alerts(deps))
    [*_, last] = cases(deps)
    assert {c["script_id"] for c in last["collections"]["contacts"]} <= {"missed_payment", "hardship_offer"}
    assert all(c["by"] == "fallback" for c in last["collections"]["contacts"])
    contacts = deps.store.read(ALERTS, f"contacts-{replay_id}", loan_id)["contacts"]
    assert sum(c["by"] == "reminder" for c in contacts) == 3
    week5 = doc["monitoring"]["weeks"][4]
    assert week5["projection"]["totals"].get("installment", 0) == 0  # 5주차 상환일 이후 전망 창에는 상환일이 없다
```

(마지막 줄 기대값은 1회차 상환일 2026-12-02가 5주차 안에 있고 전망은 다음 주부터 4주이므로 2회차 2027-01-04가 창 밖임에 근거한다. 실제 창 길이가 다르면 `doc["repayment"]` 상환일과 창을 비교해 기대값을 계산한다.) 기존 "부족 신호가 연체보다 먼저" 단언은 유지한다.

```python
def test_service_bus_pause_on_missed_then_resume():
    deps, loan_id, replay_id = paused_inline(service_bus=True, agents=ScriptedAgents())
    mon = loan(deps)["monitoring"]
    assert (mon["status"], mon["paused_week"], mon["pause_case_id"]) == ("paused", 5, f"rc-{replay_id}-i1")
    decide_restructure(deps, loan_id, f"rc-{replay_id}-i1", "approve")
    assert loan(deps)["monitoring"]["status"] == "awaiting_resume"
    resume_until_done(deps, loan_id, replay_id)
    mon = loan(deps)["monitoring"]
    assert mon["status"] == "completed" and mon["weeks"][5]["week"] == 6


def test_redelivered_repayment_updated_is_noop():
    deps, loan_id, replay_id = paused_inline(service_bus=True, agents=ScriptedAgents())
    case_id = f"rc-{replay_id}-i1"
    before = (deps.store.read(ALERTS, f"contacts-{replay_id}", loan_id)["contacts"], len(deps.events.sent))
    body = json.dumps({"type": TOPIC_REPAYMENT, "loan_id": loan_id, "case_id": case_id})
    assert handle_repayment_updated(deps, body) is None
    after = (deps.store.read(ALERTS, f"contacts-{replay_id}", loan_id)["contacts"], len(deps.events.sent))
    assert after == before and loan(deps)["monitoring"]["status"] == "paused"


def test_repayment_updated_runs_collections_once():
    deps, loan_id, replay_id = paused_inline(service_bus=True, agents=ScriptedAgents())
    contacts = deps.store.read(ALERTS, f"contacts-{replay_id}", loan_id)["contacts"]
    assert [c["by"] for c in contacts] == ["reminder", "agent"]
    assert len(foundry_conversations(deps)) == expected_conversations  # EW 조사 수 + 추심 1


@pytest.mark.parametrize("topic", [TOPIC_REPAYMENT, TOPIC_ALERT])
def test_publish_failure_still_completes(topic):
    deps = make_deps(service_bus=True, outbox=Outbox(fail_topics={topic}))
    loan_id, replay_id = replay(deps)
    pump(deps)
    assert loan(deps)["monitoring"]["status"] == "completed"
    docs = cases(deps) if topic == TOPIC_REPAYMENT else alerts(deps)
    assert docs and all(d["event_error"] for d in docs)


def test_legacy_pause_alert_id_still_promotes():
    deps, loan_id, replay_id = paused_inline(service_bus=True, agents=ScriptedAgents())
    doc = loan(deps)
    case_id = doc["monitoring"].pop("pause_case_id")
    doc["monitoring"]["pause_alert_id"] = case_id
    deps.store.upsert(LOANS, doc)
    decide_restructure(deps, loan_id, case_id, "decline")
    assert loan(deps)["monitoring"]["status"] == "awaiting_resume"


def test_week13_case_decidable_after_completion():
    deps = make_deps(service_bus=True, agents=ScriptedAgents())
    loan_id, replay_id = replay(deps)  # 데모 STACKING 셀러: 마지막 상환일 미납
    resume_until_done(deps, loan_id, replay_id)
    last = cases(deps)[-1]
    assert last["week"] == 13 and last["restructure"]["status"] == "pending_officer"
    assert loan(deps)["monitoring"]["status"] == "completed"
    decide_restructure(deps, loan_id, last["case_id"], "approve")
    assert deps.store.read(ALERTS, last["case_id"], loan_id)["restructure"]["status"] == "approved"
    assert loan(deps)["monitoring"]["status"] == "completed"


def test_new_replay_rebuilds_ledger():
    deps = make_deps(agents=None)
    loan_id, first = replay(deps)
    expire_replay(deps, loan_id)  # 기존 재생 종료 도우미
    _, second = replay(deps, loan_id=loan_id)
    sched = loan(deps)["repayment"]["schedule"]
    assert second != first and all(s["case_id"] is None or s["case_id"].startswith(f"rc-{second}") for s in sched)


def test_reminder_not_duplicated_on_retry():
    deps = make_deps(agents=None)
    loan_id, replay_id = replay(deps, until_week=4)  # 4주차까지 처리(도우미가 없으면 process_week를 직접 부른다)
    doc = loan(deps)
    doc["monitoring"]["processed_week"] = 3
    doc["monitoring"]["weeks"].pop()
    doc["repayment"]["schedule"][0]["reminded_on"] = None  # 연락 기록은 썼으나 원장 쓰기 전에 실패한 상황
    deps.store.upsert(LOANS, doc)
    process_week(deps, loan_id, replay_id, 4)
    contacts = deps.store.read(ALERTS, f"contacts-{replay_id}", loan_id)["contacts"]
    assert sum(c["by"] == "reminder" and c["ref"] == f"{replay_id}-i1" for c in contacts) == 1


def test_bad_repayment_updated_message_ignored():
    deps = make_deps()
    assert handle_repayment_updated(deps, "{}") is None
```

`replay`·`expire_replay`·`decide_restructure`·`foundry_conversations`의 실제 시그니처는 파일의 기존 도우미를 따른다. 도우미가 없는 인자(`until_week`, `loan_id`)가 필요하면 그 도우미에 선택 인자로 추가한다. 기존 재조정 결정 테스트 중 "대기 요청이 있어야 결정"은 경보 대신 추심 건 문서로 만든다. 데모 셀러는 2회차도 잔고 규칙으로 미납일 수 있으므로 마지막 회차 미납만 단언하고, 중간 멈춤은 `resume_until_done`이 처리한다.

- [ ] **Step 3: 실패 확인** — `ImportError: TOPIC_REPAYMENT`

- [ ] **Step 4: 모델** — `models.py`

```python
class RepaymentUpdated(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["repayment.updated"]
    loan_id: str = Field(pattern=LOAN_ID_PATTERN)
    case_id: str = Field(min_length=1, max_length=128)
```

`AlertRaised`는 그대로 둔다(발행은 계속, 구독자 없음).

- [ ] **Step 5: `start_replay`** — monitoring dict에서 `pause_alert_id` 대신 `"pause_case_id": None`, `"case_ids": []` 추가. `flow_start`를 정한 뒤

```python
    loan["repayment"] = build_schedule(
        flow_start, WEEKS * 7, round(monthly_installment(loan["amount_krw"], loan["tenor_months"]))
    )
```

(`flow_start`가 문자열 변수면 `date.fromisoformat`로 넘긴다.)

- [ ] **Step 6: `process_week`**
  - 전망: `"projection": projection(rows, visible, cats, baseline, installment_due=_due_list(loan))`

```python
def _due_list(loan: dict[str, Any]) -> list[date] | None:
    led = loan.get("repayment")
    return [date.fromisoformat(s["due_on"]) for s in led["schedule"]] if led else None
```

  - 엔트리에 `"repayment": None, "case_id": None`.
  - EW 블록 뒤, `mon["processed_week"] = week` 앞에 `_repayment_week(deps, loan_id, replay_id, loan, visible, lo, hi, week, entry)`.

```python
def _repayment_week(deps, loan_id, replay_id, loan, visible, lo, hi, week, entry) -> None:
    led = loan.get("repayment")
    if not led:
        return
    ev = week_events(led["schedule"], visible, lo, hi)
    by_no = {s["no"]: s for s in led["schedule"]}
    if ev.reminders:
        cid = f"contacts-{replay_id}"
        contacts = deps.store.read(ALERTS, cid, loan_id) or {
            "id": cid, "loan_id": loan_id, "replay_id": replay_id, "contacts": []}
        for no in ev.reminders:
            item, ref = by_no[no], f"{replay_id}-i{no}"
            sent = next((c for c in contacts["contacts"] if c["ref"] == ref and c["by"] == "reminder"), None)
            if sent is None:
                sent = remind(ref, contacts["contacts"], reminder_on(date.fromisoformat(item["due_on"])), deps.clock())
                contacts["contacts"].append(sent)
            item.update(reminded_on=sent["on"], reminder_status=sent["status"])
        deps.store.upsert(ALERTS, contacts)  # 대출 문서보다 먼저: 재시도 때 같은 ref면 다시 보내지 않는다
    for no, txn_id in ev.paid:
        by_no[no].update(status="paid", txn_id=txn_id)
    for no in ev.missed:  # 30일 주기라 한 주에 최대 하나
        item, case_id = by_no[no], f"rc-{replay_id}-i{no}"
        item.update(status="missed", case_id=case_id)
        mon = loan["monitoring"]
        recent = None
        if mon["alert_ids"]:
            a = deps.store.read(ALERTS, mon["alert_ids"][-1], loan_id) or {}
            recent = {k: a.get(k) for k in ("alert_id", "week", "severity", "summary")} if a else None
        deps.store.upsert(ALERTS, {
            "id": case_id, "kind": "repayment_case", "case_id": case_id, "loan_id": loan_id,
            "replay_id": replay_id, "week": week, "as_of": item["due_on"],
            "installment": {"no": no, "due_on": item["due_on"], "amount_krw": led["installment_krw"]},
            "missed_count": sum(s["status"] == "missed" for s in led["schedule"]),
            "recent_alert": recent, "status": "open", "created_at": deps.clock().isoformat(),
            "collections": None, "restructure": None, "event_error": None,
        })
        mon["case_ids"].append(case_id)
        entry["case_id"] = case_id
    entry["repayment"] = {"reminders": list(ev.reminders), "paid": [n for n, _ in ev.paid], "missed": list(ev.missed)}
```

  `recent`의 `alert_id` 키는 경보 문서의 실제 키(`id` 또는 `alert_id`)를 확인해 `a.get("alert_id") or a.get("id")`로 채운다.
  - 대출 upsert 뒤:

```python
    if deps.events is not None:
        if entry["alert_id"]:
            _publish_alert(deps, loan_id, entry["alert_id"])
        if entry["case_id"]:
            _publish_case(deps, loan_id, entry["case_id"])
```

- [ ] **Step 7: 발행과 핸들러**

```python
def _publish_case(deps: Deps, loan_id: str, case_id: str) -> None:
    message = RepaymentUpdated(type=TOPIC_REPAYMENT, loan_id=loan_id, case_id=case_id).model_dump(mode="json")
    try:
        deps.events.publish(TOPIC_REPAYMENT, message, f"{TOPIC_REPAYMENT}:{case_id}", session_id=loan_id)  # type: ignore[union-attr]
    except Exception as e:  # noqa: BLE001 - 추심 건은 남고 발행 실패를 기록한다. 주차 핸들러가 직접 잇는다
        _mark_publish_failed(deps, loan_id, case_id, e)
```

  `_publish_alert`의 except 블록도 `_mark_publish_failed(deps, loan_id, alert_id, e)`로 바꾸고:

```python
def _mark_publish_failed(deps, loan_id, doc_id, e) -> None:
    doc = deps.store.read(ALERTS, doc_id, loan_id)
    if doc is not None:
        doc["event_error"] = f"{type(e).__name__}: {e}"[:300]
        deps.store.upsert(ALERTS, doc)


def _publish_failed(deps, loan_id, doc_id) -> bool:
    return bool((deps.store.read(ALERTS, doc_id, loan_id) or {}).get("event_error"))
```

  `_alert_publish_failed`는 삭제.
  - `handle_flow_update`:

```python
    case_id = last.get("case_id")
    if case_id and not _publish_failed(deps, msg.loan_id, case_id):
        return entry  # repayment.updated → run_collections 뒤 handle_repayment_updated가 잇는다
    if case_id:
        run_collections(deps, msg.loan_id, case_id)  # 발행 실패: 여기서 추심을 돌리고 잇는다
    _continue(deps, msg.loan_id, msg.replay_id, msg.week, case_id)
    return entry
```

  - `_run_weeks` 인라인: `case_id = (entry or {}).get("case_id")`; 있으면 `run_collections(deps, loan_id, case_id)`; `_after_week(..., case_id)`.
  - `_after_week`·`_promote_if_decided`·`_continue`: 매개변수 `alert_id` → `case_id`, 멈춤 기록은 `pause_case_id=case_id`. 승격 확인:

```python
        if mon.get("status") != "paused" or (mon.get("pause_case_id") or mon.get("pause_alert_id")) != case_id:
            return
```

  - `run_collections(deps, loan_id, case_id)`: `if doc is None or loan is None or doc.get("kind") != "repayment_case" or doc.get("collections") is not None: return None`; `ctx = CollectionsContext(case=RepaymentCase.from_doc(doc), contacts=contacts["contacts"], now=deps.clock(), mask=mask)`; 나머지(추심 건 먼저 쓰고 연락 문서) 그대로.
  - `handle_alert_raised` 삭제, 새 핸들러:

```python
def handle_repayment_updated(deps: Deps, body: str) -> dict[str, Any] | None:
    try:
        msg = RepaymentUpdated.model_validate_json(body)
    except ValidationError:
        log.info("repayment.updated 형식이 아닌 메시지 무시")
        return None
    result = run_collections(deps, msg.loan_id, msg.case_id)
    doc = deps.store.read(ALERTS, msg.case_id, msg.loan_id)
    if doc is not None and deps.events is not None:
        _continue(deps, msg.loan_id, doc["replay_id"], doc["week"], msg.case_id)
    return result
```

  재전달 때 `run_collections`는 `collections`가 있어 None이고, `_continue`는 `processed_week`가 같아도 상태가 `paused`라 `"paused"`를 돌려 발행하지 않는다(이미 다음 주차로 갔다면 `"done"`).
  - 사용하지 않게 된 `EarlyWarningAlert`·`_ALERT_FIELDS` import 정리, 모듈 docstring에 "미납 → `repayment.updated` → 추심, EW 경보는 심사역 검토용" 반영. `handle_restructure_decision`은 `msg.alert_id`로 문서를 찾으므로 추심 건 ID도 그대로 받는다(필드 이름은 바꾸지 않는다).

- [ ] **Step 8: 통과 확인** — `pytest tests/orchestrator tests/agents`

- [ ] **Step 9: 커밋** — `feat(orchestrator): 상환 원장과 미납 추심 건, repayment.updated`

---

### Task 5: Service Bus 토픽과 Functions 트리거

**Files:**
- Modify: `infra/resources.bicep:407-412`, `src/orchestrator/function_app.py` (15~22 import, 160~168 트리거)
- Test: `tests/orchestrator/test_function_app.py:143`, `tests/infra/test_messaging.py` (20, 34, 44, 53~56)

**Interfaces:**
- Consumes: T4 `TOPIC_REPAYMENT`, `handle_repayment_updated`.
- Produces: 토픽 `repayment.updated`, 구독 `collections`(규칙 `subject-label`, 레이블 `repayment.updated`), 함수 `repayment_updated`.

- [ ] **Step 1: 테스트 수정**
  - `test_function_app.py:143` 기대값 `{"flow_updated": ("flow.updated", "early-warning"), "repayment_updated": ("repayment.updated", "collections")}`.
  - `test_messaging.py`: 20행 토픽 목록에 `repayment.updated` 추가; 34·44행 `alert.raised/collections` → `repayment.updated/collections`; 53~56행 규칙 단언을 `repayment.updated/collections/subject-label`, 레이블 `repayment.updated`로.

- [ ] **Step 2: 실패 확인** — `$env:RUN_INFRA_TESTS='1'; pytest tests/orchestrator/test_function_app.py tests/infra/test_messaging.py` → FAIL

- [ ] **Step 3: 구현**
  - Bicep 407행: `['consent', 'flow.updated', 'alert.raised', 'repayment.updated', 'grievance']`; 411행 구독 `{ topic: 'repayment.updated', name: 'collections' }`(기존 `alert.raised` 구독 대체).
  - `function_app.py`:

```python
@app.service_bus_topic_trigger(
    arg_name="msg", topic_name=TOPIC_REPAYMENT, subscription_name="collections",
    connection="SERVICEBUS", is_sessions_enabled=True,
)
def repayment_updated(msg: func.ServiceBusMessage) -> None:
    handle_repayment_updated(_deps(), msg.get_body().decode("utf-8"))
```

  데코레이터 인자·본문은 기존 `alert_raised` 트리거를 그대로 복사하고 이름·토픽·핸들러만 바꾼다. import도 `TOPIC_ALERT, handle_alert_raised` → `TOPIC_REPAYMENT, handle_repayment_updated`(다른 곳에서 `TOPIC_ALERT`를 안 쓰면 삭제).

- [ ] **Step 4: 통과 확인** — 같은 명령 → PASS. `az bicep build --file infra/main.bicep`(기존에 쓰는 진입 파일)로 컴파일 확인.

- [ ] **Step 5: 커밋** — `feat(infra): repayment.updated 토픽과 추심 구독·트리거`

---

### Task 6: 콘솔 — 상환 원장·추심 건 카드

**Files:**
- Modify: `src/console/adapters.py:58`, `src/console/monitoring.py` (`loan_view` 58~110), `src/console/app.py` (필터 ~172, 재조정 라우트 ~453), `src/console/dashboard.py` (273~335), `src/console/templates/loan.html`, `src/console/static/console.css` (88~94), `src/consolepreview/fixtures.py` (286~325)
- Test: `tests/console/test_monitoring.py`, `tests/console/test_dashboard.py`

**Interfaces:**
- Consumes: T4 대출 문서 `repayment`, `monitoring.pause_case_id`, `rc-` 문서, 주차 기록 `case_id`.
- Produces: `loan_view(...)["cases"]`(주차순, 각 항목에 `decidable`), `REPAYMENT_LABELS`, Jinja 필터 `repayment_label`, `dashboard.case_steps(case) -> list[dict]`, `loan_summary(...)["case_steps"]`, `["repayment"] = {"paid", "total", "missed"} | None`.

- [ ] **Step 1: 테스트 수정** — `tests/console/test_monitoring.py`
  - `make_alert(mask)`에서 `collections`·`restructure` 제거, `status="open"`.
  - 새 빌더:

```python
def make_case(mask, restructure_status="pending_officer"):
    return {
        "id": "rc-rp-1-i1", "kind": "repayment_case", "case_id": "rc-rp-1-i1", "loan_id": LOAN, "replay_id": "rp-1",
        "week": 5, "as_of": "2026-12-02", "installment": {"no": 1, "due_on": "2026-12-02", "amount_krw": 287_000},
        "missed_count": 1, "recent_alert": {"alert_id": "ew-rp-1-w03", "week": 3, "severity": "high", "summary": "고금리 대출 누적"},
        "status": "restructure_pending", "created_at": NOW,
        "collections": {"decision": "contact", "reason": "미납", "record": None, "at": NOW,
                        "contacts": [{"ref": "rc-rp-1-i1", "on": "2026-12-02", "at": NOW, "script_id": "hardship_offer",
                                      "status": "sent", "reason": None, "by": "agent"}]},
        "restructure": {"request_id": "rs-rc-rp-1-i1", "reason": "debt_overload", "summary": "s", "status": restructure_status},
        "event_error": None,
    }
```

  (`restructure` 필드는 기존 `make_alert`가 쓰던 모양을 옮긴다.) `setup` 기본값 `[make_alert(mask), make_case(mask)]`; 대출 문서에 `repayment` 원장(납부·납부·미납) 추가; 재조정 경로 `/loans/{LOAN}/alerts/rc-rp-1-i1/restructure`; `held_loan`은 `pause_case_id`; awaiting/approved 변형은 `make_case(mask, "approved")`; watch 테스트는 `store.alerts`를 승인된 추심 건으로 바꾼다.
  - 새 단언:

```python
def test_loan_page_shows_ledger_and_case(client):
    html = client.get(f"/loans/{LOAN}").get_data(as_text=True)
    assert "상환 원장" in html and "미납" in html and 'id="rc-rp-1-i1"' in html
    assert "심사역 검토 대상 · 차주 연락 없음" in html


def test_legacy_alert_with_restructure_still_decidable(client, store, mask):
    legacy = {**make_alert(mask), "collections": make_case(mask)["collections"], "restructure": make_case(mask)["restructure"]}
    store.alerts = [legacy]
    r = client.post(f"/loans/{LOAN}/alerts/{legacy['id']}/restructure", data={"decision": "approve", "reason": "r", "csrf": csrf(client)})
    assert r.status_code in (302, 303)
```

  픽스처 이름(`client`, `store`, `csrf`)은 파일의 기존 것과 맞춘다.
  - `tests/console/test_dashboard.py`: 121행 `test_alert_steps_branches`를 "탐지·조기경보 조사·심사역 검토(연락 없음) 모두 done"으로 바꾸고 추가:

```python
def test_case_steps():
    c = {"installment": {"no": 3, "due_on": "2027-01-29"}, "collections": {"decision": "contact"},
         "restructure": {"status": "pending_officer"}}
    steps = case_steps(c)
    assert [s["label"] for s in steps] == ["미납", "추심 에이전트", "재조정 결정"]
    assert steps[0]["note"] == "3회차 2027-01-29"
    assert case_steps({**c, "collections": None, "restructure": None})[1]["state"] == "current"
```

  `loan_summary` 테스트에 `case_steps` 키와 `repayment == {"paid": 2, "total": 3, "missed": 1}` 단언 추가. 단계 dict 키(`label`, `state`, `note`)는 기존 `alert_steps` 반환 형식을 따른다.

- [ ] **Step 2: 실패 확인** — `pytest tests/console` → FAIL

- [ ] **Step 3: 구현**
  - `adapters.py:58`: `ALERTS_QUERY = "SELECT * FROM c WHERE c.replay_id = @replay AND (STARTSWITH(c.id, 'ew-') OR STARTSWITH(c.id, 'rc-'))"`.
  - `console/monitoring.py`: 

```python
REPAYMENT_LABELS = {"scheduled": "예정", "paid": "납부", "missed": "미납"}
```

  `loan_view`에서 문서를 `kind == "repayment_case"`면 `cases`, 아니면 기존 경보 처리로 나눈다. 추심 건은 `recent_alert`·`collections`·`restructure`를 기존 경보와 같은 `_map_strings(..., model_text)`로 거르고 `decidable = (restructure or {}).get("status") == "pending_officer"`. `cases`는 `week` 순으로 정렬해 반환 dict에 `"cases"`로 넣는다. `_SHOWN_FIELDS`의 `collections`·`restructure`는 옛 경보 문서 때문에 남긴다.
  - `app.py`: `app.add_template_filter(lambda s: REPAYMENT_LABELS.get(s, s), "repayment_label")`(기존 필터 등록 방식을 따른다). 재조정 라우트는 대상 문서를 `view["alerts"] + view["cases"]`에서 `d.get("id") == alert_id`로 찾는다.
  - `dashboard.py`:

```python
def alert_steps(alert):
    return [
        {"label": "탐지", "state": "done", "note": None},
        {"label": "조기경보 조사", "state": "done", "note": None},
        {"label": "심사역 검토", "state": "done", "note": "연락 없음"},
    ]


def case_steps(case):
    inst, col, rs = case["installment"], case.get("collections"), case.get("restructure")
    steps = [
        {"label": "미납", "state": "done", "note": f"{inst['no']}회차 {inst['due_on']}"},
        {"label": "추심 에이전트", "state": "done" if col else "current", "note": (col or {}).get("decision")},
    ]
    if rs:
        steps.append({"label": "재조정 결정", "state": "done" if rs["status"] != "pending_officer" else "current",
                      "note": RESTRUCTURE_NOTES.get(rs["status"])})
    return steps
```

  `loan_summary`에 추가:

```python
    led = view["loan"].get("repayment")
    summary["case_steps"] = {c["case_id"]: case_steps(c) for c in view.get("cases", [])}
    summary["repayment"] = None if not led else {
        "paid": sum(s["status"] == "paid" for s in led["schedule"]),
        "total": len(led["schedule"]),
        "missed": sum(s["status"] == "missed" for s in led["schedule"]),
    }
```

  - `console.css` 88~94 근처: `.r-paid { color: var(--good); }`, `.r-missed { color: var(--bad); font-weight: 600; }`, `.r-scheduled { color: var(--muted); }`(변수 이름은 기존 상태 색 클래스에 맞춘다).
  - `loan.html`:
    - 멈춤 카드 문구: `{{ m.paused_week }}주차 미납 추심 건에서 추심 에이전트가 재조정 상담을 요청했습니다. 아래 추심 건에서 재조정을 승인하거나 거절하면 [이어서 재생]·[여기서 종료]를 고를 수 있습니다.`
    - 대출 KPI 부제: `{% if ui.repayment %} · 상환 {{ ui.repayment.paid }}/{{ ui.repayment.total }}회{% if ui.repayment.missed %} · <span class="r-missed">미납 {{ ui.repayment.missed }}</span>{% endif %}{% endif %}`
    - 주간 표 `조사` 칸 끝에 `{% if w.case_id %} · <a href="#{{ w.case_id }}">미납</a>{% endif %}`.
    - 주간 표 다음에 원장 카드:

```html
{% if l.repayment %}
<section class="card">
  <h2>상환 원장</h2>
  <p class="muted">상환일 당일 `에이엠에이렌딩 대출상환` 출금이 있으면 납부로 봅니다. 안내는 상환일 3일 전에 자동 발송합니다.</p>
  <table>
    <thead><tr><th>회차</th><th>상환일</th><th>금액</th><th>상태</th><th>안내일</th><th>출금 거래</th><th>추심 건</th></tr></thead>
    <tbody>
    {% for s in l.repayment.schedule %}
      <tr>
        <td>{{ s.no }}</td><td>{{ s.due_on }}</td><td>{{ l.repayment.installment_krw | krw }}</td>
        <td class="r-{{ s.status }}">{{ s.status | repayment_label }}</td>
        <td>{{ s.reminded_on or "-" }}{% if s.reminder_status == "deferred" %} (보류){% endif %}</td>
        <td>{{ s.txn_id or "-" }}</td>
        <td>{% if s.case_id %}<a href="#{{ s.case_id }}">{{ s.case_id }}</a>{% else %}-{% endif %}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
</section>
{% endif %}
```

  (`<p>` 안의 백틱은 `<code>`로 쓴다. 금액 필터 이름은 템플릿의 기존 것을 쓴다.)
    - 기존 경보 카드의 추심 블록·재조정 폼을 매크로 `collections_block(col)`, `restructure_block(doc, decidable, user, csrf, replay_blocked)`로 옮기고, 경보 카드는 `collections`가 없으면 `<p class="muted">심사역 검토 대상 · 차주 연락 없음</p>`, 있으면(옛 문서) 매크로를 부른다.
    - 추심 건 카드(`{% for c in cases %}`): `<section class="card" id="{{ c.case_id }}">`, 제목 `{{ c.installment.no }}회차 미납 · {{ c.installment.due_on }} · {{ c.installment.amount_krw | krw }}`, 누적 미납, 최근 경보 링크 `<a href="#{{ c.recent_alert.alert_id }}">`, `ui.case_steps[c.case_id]` 스테퍼(경보 스테퍼와 같은 마크업), `collections_block(c.collections)`, `restructure_block(c, c.decidable, user, csrf, replay_blocked)`. 폼 액션은 `/loans/{{ l.id }}/alerts/{{ c.case_id }}/restructure`.
  - `consolepreview/fixtures.py`: `_alert`에서 `collections`·`restructure`를 빼고 `status="open"`; 추심 건 `_case`(`rc-rp-preview-1-i3`, 13주차, `pending_officer`)를 경보 목록에 추가; `done` 대출에 `repayment` 원장(납부·납부·미납, 3회차 `case_id="rc-rp-preview-1-i3"`).

- [ ] **Step 4: 통과 확인** — `pytest tests/console tests/consolepreview`(미리보기 테스트가 있으면 함께)

- [ ] **Step 5: 커밋** — `feat(console): 상환 원장과 미납 추심 건 카드`

---

### Task 7: 문서

**Files:**
- Modify: `docs/agents.md` (16, 66~73), `docs/architecture.md` (102, 186~207), `docs/limitations.md` (66, 73, 114 근처), `docs/runbook/deploy.md:186`, `docs/runbook/troubleshooting.md:31`, `docs/security/threat-model.md` (87, 101), `docs/demo/demo-script.md` (61~70), `docs/demo/fallback.md` (9, 14, 15, 29, 40)
- Test: 문서 테스트(`tests/docs` 또는 기존 문서 검사 테스트)

- [ ] **Step 1: 내용 수정**
  - `docs/agents.md` 16행 추심 행: 도구 `get_case`, `get_contact_history`, `list_scripts`, `send_script`, `route_hardship`; 대체 동작 "최근 경보 high면 `hardship_offer`, 아니면 `missed_payment`". 66~73행 추심 절: "상환일 출금이 없을 때(미납)만 돈다. 상환 안내(`payment_reminder`)는 상환일 3일 전 LLM 없이 보낸다. EW 경보만으로는 차주에게 연락하지 않는다."
  - `docs/architecture.md` 102행 토픽 목록에 `repayment.updated` 추가. Mermaid(186~199): `repay["repayment.updated"]` 노드를 주차 처리(원장)에서 받아 `col`(추심)로 잇고, `alert`→`col` 화살표를 지운다(`alert`는 콘솔 검토로). 207행 설명: "미납이 확정되면 `repayment.updated`가 추심 에이전트를 부른다. `alert.raised`는 발행하지만 현재 구독자가 없다."
  - `docs/limitations.md`: 66·73행 근처 추심 설명을 미납 기준으로 고치고 추가:
    - 납부 대조는 상환일 당일 같은 적요 출금만 본다(부분 납부·지연 납부 미반영).
    - Mock STACKING 시나리오는 마지막 상환일을 시나리오상 미납으로 만든다.
    - 13주차 미납은 재생을 멈추지 않는다(재생 완료 뒤 재조정 결정만 가능).
    - `alert.raised`는 현재 구독자가 없다.
    - 114행의 `alert.raised/collections`를 "당시 구독(현재는 `repayment.updated/collections`)"으로 표기.
  - `docs/runbook/deploy.md:186`: `--topic-name repayment.updated`.
  - `docs/runbook/troubleshooting.md:31`: "추심은 미납 주차에만 돌며, 미납 주차에서 멈춰 있으면 `repayment.updated/collections` 구독의 dead-letter도 봅니다."
  - `docs/security/threat-model.md` 87·101행: `src/agents/collections.py:70` → `grep -n "^TOOLS" src/agents/collections.py`로 찾은 새 줄 번호.
  - `docs/demo/demo-script.md` 61~70·`docs/demo/fallback.md`: B를 "재생 완료 + 13주차 미납 추심 건의 재조정 요청 대기"로 바꾼다. 확인 항목: 상태 `재생 완료`, `상환 원장`(납부 2·미납 1 또는 납부 1·미납 2), 마지막 추심 건에 `재조정 상담 요청`(`pending_officer`)과 `재조정 결정` 폼, EW 경보 카드는 "차주 연락 없음". 재개 장면은 "앞 주차 미납이면 재생이 멈추고 결정 뒤 재개한다"고 말로 설명. 예시 대출 ID 칸은 T8 실측 뒤 채운다(그 전까지 기존 행에 "배포 전 기록" 표기). `docs/demo/rehearsal.md:136`은 과거 기록이라 그대로 둔다.

- [ ] **Step 2: 문서 테스트** — `pytest tests -k "doc or readme"` → PASS(백틱 안 경로가 존재해야 한다).

- [ ] **Step 3: 커밋** — `docs: 미납 기준 추심과 repayment.updated 반영`

---

### Task 8: 배포와 Azure 검증

**Files:**
- Modify: `docs/runbook/deploy-history.md`(297, 304~308 갱신, 새 항목), `docs/demo/fallback.md`(B 예시 ID)

- [ ] **Step 1: 전체 테스트** — `$env:RUN_INFRA_TESTS='1'; $env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python -m pytest -q -p no:cacheprovider` → PASS

- [ ] **Step 2: 배포 전 확인** — Cosmos에서 `monitoring.status`가 `paused`/`awaiting_resume`이고 `pause_alert_id`가 있는 대출 수를 센다(옛 문서는 T4의 대체 키로 계속 동작). 결과만 숫자로 기록한다.

- [ ] **Step 3: 배포**

```powershell
python -m ops window open --acr
python -m ops deploy mockapi
python -m ops deploy orchestrator
python -m ops deploy console
python -m ops smoke
```

Service Bus는 일시 정지(인라인 모드) 상태이므로 Bicep 토픽 변경은 다음 `ops resume` 때 반영된다. `ops resume`(Premium 월 약 $677)은 사용자 승인 없이는 하지 않는다.

- [ ] **Step 4: 인라인 재생 검증** — `seller-dailylook-01`로 새 신청 → 승인 → `사후 13주 재생`. 기대:
  - 상환 원장 3회차 중 마지막 `미납`(2회차는 잔고에 따라 납부·미납), 안내 3건(`by="reminder"`).
  - 미납 주차에 추심 건 카드와 추심 에이전트 연락 기록, EW 경보 카드는 "차주 연락 없음".
  - 13주차 미납이면 상태 `재생 완료`, 재조정 결정 폼 동작, 결정 뒤에도 `재생 완료`.
  - 앞 주차 미납이 있으면 그 주차에서 `재조정 대기` → 결정 → 재개가 동작.

- [ ] **Step 5: 창 닫기** — `python -m ops window close`

- [ ] **Step 6: 기록과 커밋** — `deploy-history.md`에 날짜·이미지 태그·검증 결과(대출 ID, 원장 결과, 소요 시간)를 쓰고 `fallback.md` B 예시 ID를 채운다. 커밋 `docs(runbook): 미납 추심 배포 기록`. push는 사용자 승인 뒤.

- [ ] **Step 7(선택): EW 평가** — `python -m ops eval --suite early_warning`은 비용이 들므로 사용자에게 먼저 묻는다.
