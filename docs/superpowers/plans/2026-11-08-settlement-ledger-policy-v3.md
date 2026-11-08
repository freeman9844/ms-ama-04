# 정산 원장 활용과 정책 v3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 마켓플레이스 PG 정산 원장을 심사·정책·에이전트 의견·상세 화면에 쓰고(대사율·채널 집중·공제율 추이), 정책을 v3(R101 삭제, R105~R108 추가)로 올린다.

**Architecture:** 모의 데이터는 정산 원장(`Payout`→`PgSettlement`)과 계좌 입금을 분리해 만든다(`receipts.py`: 시점 차이·상계·선정산 양도). 결정적 모듈 `agents/pg_ledger.py`가 원장과 계좌 거래로 지표를 내고, `compute_underwriting`이 특성(`CashFlowFeatures` 선택 필드)과 상세(`pg_ledger`)로 돌려준다. 정책 엔진은 값이 None인 규칙을 건너뛴다. 분석 에이전트는 새 도구 `get_settlement_summary`와 `M:pg_*` 근거 ID를 쓴다. 콘솔은 현금흐름 탭에 "정산 대사" 카드를 그린다.

**Tech Stack:** Python 3.12, Pydantic v2, FastAPI(mock·policy), Azure Functions(Durable), Flask+Jinja2 콘솔, pytest.

**Spec:** `docs/superpowers/specs/2026-11-08-settlement-ledger-policy-v3-design.md`

## Global Constraints

- 결정적 코드만 지표·정책을 만든다. LLM은 인용만 한다(스펙 §3.2·§3.5).
- 정산 자료가 없으면(`pg.json` 없음·0건) 정산 지표는 None, 결과는 `{"available": False}`, R107·R108은 적용하지 않는다(스펙 §3.3).
- `PgSettlement.schema_version`은 "1" 유지. `deduction_krw` 기본 0(스펙 §3.1).
- 모의 데이터 난수: 계좌 반영기는 셀러 rng와 **별도** rng(`seed + RECEIPT_SEED_OFFSET`)를 쓴다. 매출·비용·잔고 난수 순서는 바뀌지 않는다.
- 사후 재생(`postloan`)의 `simulate` 호출은 바꾸지 않는다(`receipt_rng=None` → 기존 동작).
- 콘솔 템플릿에는 인라인 style·script 금지(CSP). 파일은 CRLF, UTF-8(BOM 없음). 편집은 Python 바이트 치환(`\r\n`)으로 한다.
- 토큰·구독 ID·IP를 출력하거나 커밋하지 않는다. `memory261105.md`, `ops/reports/load-20261107073503/`는 커밋하지 않는다.
- `ops eval`·`ops resume`은 사용자 승인 없이 실행하지 않는다.

### 스펙과 다른 점(계획에서 확정)

1. **배포 순서:** 스펙 §4·§6은 정책 → mockapi → 오케스트레이터 → 콘솔이다. 그런데 오케스트레이터 데이터 수집(`src/agents/data_assembly.py:37`)이 `TypeAdapter(list[PgSettlement])`로 `pg.json`을 검증하고 계약은 `extra="forbid"`다. 새 mockapi가 `deduction_krw`를 보내면 옛 오케스트레이터가 거부한다. 그래서 **정책 → 오케스트레이터 → mockapi → 콘솔** 순서로 배포한다(새 오케스트레이터는 `deduction_krw` 없는 옛 JSON도 받는다).
2. **월별 공제율 차트:** 스펙 §3.6은 "선"이지만 6개 값을 정확히 읽게 가로 막대(`ch.hbars`, 단위 %)로 그린다.
3. **정책 사유 라벨:** 스펙 §3.6은 "정책 사유 라벨에 R105~R108 추가"이나 콘솔에는 사유 코드 라벨 표가 없고 메시지는 정책 결과(`reason_codes[].message`)에서 온다. v3 YAML 메시지가 곧 라벨이며 옛 신청의 R101은 저장된 메시지로 그대로 보인다. 대신 지표 신호등(`assessment.SIGNAL_RULES`)을 v3에 맞춘다.
4. **세금 일치율 신호:** R101이 없어지므로 신호등의 `tax_bank_match_ratio`는 새 상태 `watch`("주의(참고 지표)", 주황)로 0.7 미만을 표시한다(위험 목록에는 들어가지만 정책 사유는 아님).
5. **새 선택 지표 신호:** 값이 None(옛 신청·정산 자료 없음)이면 신호등에서 숨긴다("값 없음" 줄을 늘리지 않는다).

## Review Focus

1. **시점 차이 vs 정산 주기 추정:** 입금 15%가 1~2영업일 늦어져도 `settlement_for`의 주기(cadence)·기준일 추정이 바뀌지 않아야 한다 → T3 Step 5(주기 변화는 재고정 대상이 아니라 중단·보고 대상).
2. **같은 금액·같은 날 매칭:** 쿠팡 주정산·최종액정산이 같은 날 같은 금액일 때 한 입금이 두 번 짝지어지지 않는다 → T5 `test_same_amount_same_day_matches_once`.
3. **기준일 직전 지급:** 기준일 3영업일 이내 지급은 대상에서 빠져 정상 셀러 대사율이 1.0이다 → T5 `test_recent_settlements_excluded`, T7 임계값 게이트.
4. **옛 문서(`pg_ledger` 없음)·옛 정책 결과(R101):** 상세 화면이 깨지지 않고 "정산 자료 없음"을 보이며 R101 사유는 저장된 메시지로 보인다 → T11 `test_settlement_card_unavailable_for_old_docs`, `test_detail_old_doc_shows_no_settlement`.
5. **배포 순서와 `extra="forbid"`:** 오케스트레이터가 mockapi보다 먼저 나간다 → T14 Step 2.

## 공통 명령

- 테스트: `$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python -m pytest -q -p no:cacheprovider <경로>`
- 전체(인프라 제외, 약 3분): 위 명령에서 경로 없이.
- 커밋: 메시지를 임시 파일에 `Out-File -Encoding utf8NoBOM`으로 쓰고 `git commit -F <파일>`. 끝에 다음 트레일러:

```
Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
Copilot-Session: 05f1ca1b-6073-417d-bfb1-6b546f82cb58
```

---

### Task 1: 계약과 `Payout`의 상계액

**Files:**
- Modify: `src/shared/contracts.py:84-96` (`PgSettlement`)
- Modify: `src/mockapis/sellerdata/channels.py:50-60` (`Payout`)
- Test: `tests/shared/test_contracts.py`, `tests/mockapis/test_sellerdata.py`

**Interfaces:**
- Produces: `PgSettlement.deduction_krw: int`(기본 0, ≥0), 검증식 `net == gross − fee − deduction`; `Payout.deduction_krw: int = 0`(마지막 필드), `Payout.net_krw == gross − fee − deduction`.

- [ ] **Step 1: 실패 테스트 작성** — `tests/shared/test_contracts.py`의 `test_pg_settlement_net_must_equal_gross_minus_fee` 아래에:

```python
def test_pg_settlement_net_subtracts_deduction():
    day = date(2026, 9, 7)
    s = PgSettlement(settled_on=day, platform="쿠팡", gross_krw=1000, fee_krw=68, deduction_krw=30, net_krw=902)
    assert s.deduction_krw == 30
    assert PgSettlement(settled_on=day, platform="쿠팡", gross_krw=1000, fee_krw=68, net_krw=932).deduction_krw == 0
    with pytest.raises(ValidationError):
        PgSettlement(settled_on=day, platform="쿠팡", gross_krw=1000, fee_krw=68, deduction_krw=30, net_krw=932)
    with pytest.raises(ValidationError):
        PgSettlement(settled_on=day, platform="쿠팡", gross_krw=1000, fee_krw=68, deduction_krw=-1, net_krw=933)
```

`tests/mockapis/test_sellerdata.py` 끝에(임포트에 `from mockapis.sellerdata.channels import Payout` 추가):

```python
def test_payout_net_subtracts_deduction():
    p = Payout(date(2026, 4, 6), "쿠팡", "쿠팡(주) 주정산", "PG_SETTLE", 1000, 100, 30)
    assert p.net_krw == 870
    assert Payout(date(2026, 4, 6), "쿠팡", "쿠팡(주) 주정산", "PG_SETTLE", 1000, 100).net_krw == 900
```

- [ ] **Step 2: 실패 확인** — `tests/shared/test_contracts.py tests/mockapis/test_sellerdata.py -k deduction` → FAIL(`deduction_krw` 없음).

- [ ] **Step 3: 구현**

`PgSettlement`:

```python
class PgSettlement(Contract):
    schema_version: SchemaV1 = "1"
    settled_on: date
    platform: str = Field(min_length=1)
    gross_krw: int = Field(ge=0)
    fee_krw: int = Field(ge=0)
    deduction_krw: int = Field(default=0, ge=0)  # 광고비·물류비 상계(2026-11-08). 옛 JSON은 0.
    net_krw: int

    @model_validator(mode="after")
    def _net(self) -> "PgSettlement":
        if self.net_krw != self.gross_krw - self.fee_krw - self.deduction_krw:
            raise ValueError("net_krw == gross_krw - fee_krw - deduction_krw 이어야 한다")
        return self
```

`Payout`:

```python
@dataclass(frozen=True)
class Payout:
    paid_on: date
    channel: str
    description: str
    code: TxnTypeCode
    gross_krw: int
    fee_krw: int
    deduction_krw: int = 0  # 광고비 상계(receipts.bank_receipts가 정한다)

    @property
    def net_krw(self) -> int:
        return self.gross_krw - self.fee_krw - self.deduction_krw
```

- [ ] **Step 4: 통과 확인** — `tests/shared tests/mockapis/test_sellerdata.py` → PASS.
- [ ] **Step 5: 커밋** — `feat(contracts): PG 정산 상계액(deduction_krw)`

---

### Task 2: 계좌 입금 반영기 `receipts.py`

**Files:**
- Create: `src/mockapis/sellerdata/receipts.py`
- Create: `tests/mockapis/test_receipts.py`

**Interfaces:**
- Consumes: `Payout`(T1), `Event`(`mockapis.sellerdata.costs`), `TxnCategory`, `add_business_days`.
- Produces: `bank_receipts(payouts, as_of, rng, *, assigned_from=None) -> Receipts`; `Receipts.payouts: tuple[Payout, ...]`(상계 반영 원장), `Receipts.events: tuple[Event, ...]`(계좌 입금, 정렬 안 됨); 상수 `ADVANCE_DESCRIPTION = "한결선정산 선지급"`, `OFFSET_PLATFORMS`.

- [ ] **Step 1: 실패 테스트 작성** — `tests/mockapis/test_receipts.py`:

```python
import random
from datetime import date, timedelta

from mockapis.sellerdata.categories import TxnCategory
from mockapis.sellerdata.channels import Payout
from mockapis.sellerdata.receipts import (ADVANCE_DESCRIPTION, DELAY_SHARE, OFFSET_RATE, OFFSET_RISE_PP,
                                          bank_receipts)
from shared.krcalendar import add_business_days

AS_OF = date(2026, 11, 1)
START = date(2026, 5, 1)


def _payouts(days: int = 180) -> list[Payout]:
    out = []  # 금액을 날마다 다르게 해 (적요, 실지급액)이 지급 한 건을 가리키게 한다
    for i in range(days):
        d = START + timedelta(days=i)
        out.append(Payout(d, "쿠팡", "쿠팡(주) 주정산", "PG_SETTLE", 1_000_000 + 1000 * i, 110_000))
        out.append(Payout(d, "자사몰", "토스페이먼츠 정산", "CARD_SETTLE", 500_000 + 1000 * i, 17_000))
        if i % 3 == 0:
            out.append(Payout(d, "스마트스토어", "네이버파이낸셜 정산", "PG_SETTLE", 300_000 + 1000 * i, 19_800))
    return out


def test_offset_only_on_marketplaces_and_net_is_deposit():
    r = bank_receipts(_payouts(), AS_OF, random.Random(1))
    for p in r.payouts:
        if p.channel == "자사몰":
            assert p.deduction_krw == 0
        else:
            assert OFFSET_RATE[0] * p.gross_krw - 1 <= p.deduction_krw <= (OFFSET_RATE[1] + OFFSET_RISE_PP[1]) * p.gross_krw + 1
    assert sorted(e.amount_krw for e in r.events) == sorted(p.net_krw for p in r.payouts)
    assert all(e.category is TxnCategory.REVENUE for e in r.events)


def test_some_deposits_arrive_one_or_two_business_days_late():
    payouts = _payouts()
    r = bank_receipts(payouts, AS_OF, random.Random(2))
    by_key = {(p.description, p.net_krw): p.paid_on for p in r.payouts}
    late = 0
    for e in r.events:
        paid = by_key[(e.description, e.amount_krw)]
        assert e.booked_on in (paid, add_business_days(paid, 1), add_business_days(paid, 2))
        late += e.booked_on != paid
    assert 0.5 * DELAY_SHARE < late / len(r.events) < 1.5 * DELAY_SHARE


def test_assigned_top_platform_paid_early_by_advance():
    since = AS_OF - timedelta(days=90)
    r = bank_receipts(_payouts(), AS_OF, random.Random(3), assigned_from=since)
    assigned = [p for p in r.payouts if p.channel == "쿠팡" and p.paid_on >= since]
    advances = [e for e in r.events if e.description == ADVANCE_DESCRIPTION]
    assert assigned and len(advances) == len(assigned)
    assert all(e.code == "TRANSFER_IN" and e.category is TxnCategory.LOAN_PROCEEDS for e in advances)
    assert not [e for e in r.events if e.description == "쿠팡(주) 주정산" and e.booked_on >= since + timedelta(days=7)]
    assert all(since - timedelta(days=7) <= e.booked_on < AS_OF for e in advances)
    net = sum(p.net_krw for p in assigned)
    assert 0.83 * net - len(advances) <= sum(e.amount_krw for e in advances) <= 0.87 * net + len(advances)
    assert len([p for p in r.payouts if p.channel == "쿠팡"]) == 180  # 원장에는 그대로 남는다


def test_deterministic():
    a = bank_receipts(_payouts(), AS_OF, random.Random(9))
    b = bank_receipts(_payouts(), AS_OF, random.Random(9))
    assert a == b
```

- [ ] **Step 2: 실패 확인** — `tests/mockapis/test_receipts.py` → FAIL(모듈 없음).

- [ ] **Step 3: 구현** — `src/mockapis/sellerdata/receipts.py`:

```python
"""정산 원장(Payout) → 계좌 입금 사건. 시점 차이·광고비 상계·선정산 양도를 넣는다(스펙 §3.1).

상계(deduction_krw)는 여기서 원장에 기록하고, 계좌 입금액은 언제나 상계 뒤 실지급액(net_krw)이다.
rng는 셀러 rng와 따로 받는다(호출자가 시드 + RECEIPT_SEED_OFFSET으로 만든다). 그래야 매출·비용 난수 순서가 그대로다.
"""

import random
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta

from mockapis.sellerdata.categories import TxnCategory
from mockapis.sellerdata.channels import Payout
from mockapis.sellerdata.costs import Event
from shared.krcalendar import add_business_days

DELAY_SHARE = 0.15
DELAY_BUSINESS_DAYS = (1, 2)
OFFSET_PLATFORMS = frozenset({"쿠팡", "스마트스토어", "11번가"})
OFFSET_RATE = (0.01, 0.04)
OFFSET_RISE_SHARE = 0.25
OFFSET_RISE_PP = (0.015, 0.025)
OFFSET_RISE_DAYS = 90
ADVANCE_RATIO = (0.83, 0.87)
ADVANCE_LEAD_DAYS = (5, 7)
ADVANCE_DESCRIPTION = "한결선정산 선지급"


@dataclass(frozen=True)
class Receipts:
    payouts: tuple[Payout, ...]
    events: tuple[Event, ...]


def _top_platform(payouts: Sequence[Payout], since: date) -> str | None:
    gross: dict[str, int] = defaultdict(int)
    for p in payouts:
        if p.code == "PG_SETTLE" and p.paid_on >= since:
            gross[p.channel] += p.gross_krw
    return max(sorted(gross), key=gross.__getitem__) if gross else None


def bank_receipts(
    payouts: Sequence[Payout], as_of: date, rng: random.Random, *, assigned_from: date | None = None
) -> Receipts:
    """원장에 상계를 넣고, 지급을 계좌 입금으로 바꾼다. assigned_from부터 최대 플랫폼 지급은 선정산 선지급으로 대체."""
    base = rng.uniform(*OFFSET_RATE)
    rise = rng.uniform(*OFFSET_RISE_PP)
    rising = rng.random() < OFFSET_RISE_SHARE
    rise_from = as_of - timedelta(days=OFFSET_RISE_DAYS)
    ledger: list[Payout] = []
    for p in sorted(payouts, key=lambda p: (p.paid_on, p.channel, p.description)):
        if p.channel in OFFSET_PLATFORMS:
            rate = base + (rise if rising and p.paid_on >= rise_from else 0.0)
            p = replace(p, deduction_krw=min(round(p.gross_krw * rate), p.gross_krw - p.fee_krw))
        ledger.append(p)
    top = _top_platform(ledger, assigned_from) if assigned_from else None
    events: list[Event] = []
    for p in ledger:
        if top is not None and assigned_from is not None and p.channel == top and p.paid_on >= assigned_from:
            amount = round(p.net_krw * rng.uniform(*ADVANCE_RATIO))
            lead = rng.randint(*ADVANCE_LEAD_DAYS)
            if amount > 0:
                events.append(Event(p.paid_on - timedelta(days=lead), amount, ADVANCE_DESCRIPTION, "TRANSFER_IN",
                                    TxnCategory.LOAN_PROCEEDS))
            continue
        booked = p.paid_on
        if rng.random() < DELAY_SHARE:
            booked = add_business_days(booked, rng.randint(*DELAY_BUSINESS_DAYS))
        events.append(Event(booked, p.net_krw, p.description, p.code, TxnCategory.REVENUE))
    return Receipts(tuple(ledger), tuple(events))
```

`Event` 생성자 인자 순서는 `engine.py:72`(`Event(p.paid_on, p.net_krw, p.description, p.code, TxnCategory.REVENUE)`)와 같다. 다르면 `costs.py`의 `Event` 정의에 맞춘다.

- [ ] **Step 4: 통과 확인** — `tests/mockapis/test_receipts.py` → PASS.
- [ ] **Step 5: 커밋** — `feat(mockapis): 계좌 입금 반영기(시점 차이·상계·선정산 양도)`

---

### Task 3: 시뮬레이터 연결과 수치 재고정(정책은 아직 v2)

**Files:**
- Modify: `src/mockapis/sellerdata/engine.py:53-84` (`simulate`)
- Modify: `src/mockapis/synth.py` (`RECEIPT_SEED_OFFSET`, `generate_case`의 `simulate` 호출 ~113)
- Test: `tests/mockapis/test_sellerdata.py`, 그리고 재고정 대상(아래 Step 5)

**Interfaces:**
- Consumes: `bank_receipts`(T2).
- Produces: `simulate(..., receipt_rng: random.Random | None = None, assigned_from: date | None = None)`. `receipt_rng=None`이면 기존과 바이트 단위로 같다. `PgSettlement`이 `deduction_krw`를 싣는다. `synth.RECEIPT_SEED_OFFSET = 5000`.

- [ ] **Step 1: 실패 테스트 작성** — `tests/mockapis/test_sellerdata.py` 끝에:

```python
def test_generated_case_ledger_and_bank_differ_only_by_timing_and_offset():
    from mockapis.synth import Archetype, generate_case

    case = generate_case("app-r", Archetype.HEALTHY_SEASONAL, date(2026, 11, 1), 7)
    assert any(s.deduction_krw > 0 for s in case.pg_settlements)
    for s in case.pg_settlements:
        assert s.net_krw == s.gross_krw - s.fee_krw - s.deduction_krw
    deposits = {(t.amount_krw) for t in case.transactions if t.txn_type_code == "PG_SETTLE"}
    matched = [s for s in case.pg_settlements if s.net_krw in deposits]
    assert len(matched) >= 0.97 * len(case.pg_settlements)  # 기준일 직후로 밀린 몇 건만 빠진다


def test_simulate_without_receipt_rng_is_unchanged():
    from mockapis.sellerdata.engine import simulate

    persona = DEMO_PERSONAS["seller-dailylook-01"]
    a = simulate(persona, date(2026, 1, 1), date(2026, 4, 1), random.Random(5))
    assert all(s.deduction_krw == 0 for s in a.pg_settlements)
    pg_deposits = sorted(e.amount_krw for e in a.events if e.code == "PG_SETTLE")
    assert pg_deposits == sorted(s.net_krw for s in a.pg_settlements)
```

- [ ] **Step 2: 실패 확인** — `tests/mockapis/test_sellerdata.py -k "receipt or ledger_and_bank"` → 첫 테스트 FAIL(상계 없음).

- [ ] **Step 3: 구현** — `engine.py`(임포트 `from mockapis.sellerdata.receipts import bank_receipts`):

```python
def simulate(
    persona: Persona,
    start: date,
    end: date,
    rng: random.Random,
    *,
    sales_factor: Callable[[date], float] = _one,
    payroll_day: Callable[[int, int], int] = _ten,
    owner_draw_factor: Callable[[date], float] = _one,
    extra: Iterable[Event] = (),
    receipt_rng: random.Random | None = None,
    assigned_from: date | None = None,
) -> Ledger:
    """[start, end) 기간의 거래 사건. 같은 날은 입금 먼저, 출금 나중.

    receipt_rng가 있으면 정산 원장과 계좌 입금을 분리한다(receipts.bank_receipts: 시점 차이·상계·양도).
    없으면 원장 그대로 입금한다(사후 재생).
    """
    by_channel = daily_sales(persona, start - timedelta(days=WARMUP_DAYS), end, rng, sales_factor)
    paid = [p for channel, sales in by_channel.items() for p in payouts(CHANNELS[channel], persona.industry, sales, rng)
            if start <= p.paid_on < end]
    if receipt_rng is None:
        ledger = paid
        events = [Event(p.paid_on, p.net_krw, p.description, p.code, TxnCategory.REVENUE) for p in paid]
    else:
        r = bank_receipts(paid, end, receipt_rng, assigned_from=assigned_from)
        ledger = list(r.payouts)
        events = [e for e in r.events if e.booked_on < end]
    settlements = [
        PgSettlement(settled_on=p.paid_on, platform=p.channel, gross_krw=p.gross_krw, fee_krw=p.fee_krw,
                     deduction_krw=p.deduction_krw, net_krw=p.net_krw)
        for p in ledger if p.code == "PG_SETTLE"
    ]
    total: dict[date, int] = {}
    for sales in by_channel.values():
        for d, v in sales.items():
            total[d] = total.get(d, 0) + v
    events += CostSchedule(persona, start, end, total, rng, payroll_day, owner_draw_factor).build()
    events += [e for e in extra if start <= e.booked_on < end]
    events.sort(key=lambda e: (e.booked_on, e.amount_krw < 0))
    return Ledger(tuple(events), tuple(sorted(settlements, key=lambda s: s.settled_on)), total)
```

리스트 내포는 채널 순서대로 `payouts(..., rng)`를 불러 기존 rng 소비 순서와 같다.

`synth.py`: 상수 `RECEIPT_SEED_OFFSET = 5000  # 계좌 반영기 전용 rng(기존 매출·비용 난수 순서를 보존)`을 `OPENING_BALANCE_RATIO` 근처에 두고, `generate_case`의 `simulate(...)` 호출 인자 끝에 `receipt_rng=random.Random(seed + RECEIPT_SEED_OFFSET),`를 추가한다(양도는 T4).

- [ ] **Step 4: 대상 테스트 통과** — `tests/mockapis/test_sellerdata.py tests/mockapis/test_receipts.py` → PASS.

- [ ] **Step 5: 전체 실행과 재고정** — 경로 없이 전체 실행. 실패를 분류한다.
  - **재고정 허용(수치 드리프트):** 입금액이 1~4% 줄고 일부 입금일이 1~2영업일 밀려 생기는 고정 수치. 예: `tests/mockapis/test_demo.py` `DIGESTS`, `tests/agents/test_underwriting.py`의 `M:dscr` 4.106 같은 근거 값, `tests/agents/test_features.py` 보정값, `tests/orchestrator/test_activities.py:226` `max_amount_krw == 3_300_000`, 콘솔 미리보기·평가 데이터셋의 고정 수치. 새 값으로 바꾸되 변화율이 금액·비율 기준 6% 이내여야 한다.
  - **재고정 금지(중단·보고):** `tests/mockapis/test_decision_invariance.py`의 판정·사유·금리 구간 변화(정책 v2 그대로라 바뀌면 안 된다), 정산 주기(`cadence`)·기준일 추정 변화(`tests/agents/test_settlement.py` 등), 조기경보 경보 여부 변화. 하나라도 있으면 원인을 적고 멈춘 뒤 보고한다.
  - 재고정한 테스트마다 옛 값 → 새 값을 커밋 메시지 본문에 적는다.

- [ ] **Step 6: 커밋** — `feat(mockapis): 정산 원장과 계좌 입금 분리, 수치 재고정`

---

### Task 4: ASSIGNED 원형과 데모 셀러 mugshop-21

**Files:**
- Modify: `src/mockapis/synth.py` (`Archetype`, `ARCHETYPE_PERSONAS`, `ASSIGNED_DAYS`, `generate_case`)
- Modify: `src/shared/sellers.py:20-41` (`DEMO_SELLERS`)
- Modify: `src/mockapis/sellerdata/personas.py:50-96` (`DEMO_PERSONAS`)
- Modify: `src/mockapis/demo.py:15-37` (`DEMO_APPLICANTS`)
- Modify: `src/agents/classify.py:26-38` (`_RULES`)
- Test: `tests/mockapis/test_demo.py`, `tests/mockapis/test_sellerdata.py:106`, `tests/agents/test_classify.py`

**Interfaces:**
- Produces: `Archetype.ASSIGNED = "assigned"`; 데모 셀러 `seller-mugshop-21`(시드 121); 분류 규칙 `("TRANSFER_IN", "선정산", Category.LOAN_PROCEEDS)`(`RULES_VERSION`은 `classify-v1` 유지).

- [ ] **Step 1: 실패 테스트 작성**
  - `tests/mockapis/test_demo.py` `test_build_demo_cases`의 기대 맵 끝에 `"seller-mugshop-21": Archetype.ASSIGNED,`.
  - `tests/mockapis/test_sellerdata.py:106` `== 20` → `== 21`.
  - `tests/mockapis/test_receipts.py` 끝에:

```python
def test_assigned_case_has_advances_classified_as_loan_proceeds():
    from agents.classify import Category, classify
    from mockapis.synth import Archetype, generate_case

    case = generate_case("app-a", Archetype.ASSIGNED, AS_OF, 121)
    advances = [t for t in case.transactions if t.description == ADVANCE_DESCRIPTION]
    assert advances and all(t.booked_on >= AS_OF - timedelta(days=97) for t in advances)
    assert {classify(t) for t in advances} == {Category.LOAN_PROCEEDS}
    assert {case.labels[t.txn_id] for t in advances} == {TxnCategory.LOAN_PROCEEDS}
```

  - `tests/agents/test_classify.py`에 `TRANSFER_IN` "한결선정산 선지급" → `LOAN_PROCEEDS` 한 줄(기존 파라미터 표가 있으면 거기에 추가).

- [ ] **Step 2: 실패 확인** — `tests/mockapis/test_demo.py tests/mockapis/test_sellerdata.py tests/mockapis/test_receipts.py tests/agents/test_classify.py` → FAIL.

- [ ] **Step 3: 구현**
  - `synth.py`: `Archetype`에 `ASSIGNED = "assigned"`. `ASSIGNED_DAYS = 90  # 기준일 90일 전부터 최대 플랫폼 정산을 선정산업체에 양도`. `ARCHETYPE_PERSONAS[Archetype.ASSIGNED] = DEMO_PERSONAS["seller-mugshop-21"]`. `simulate` 호출에 `assigned_from=as_of - timedelta(days=ASSIGNED_DAYS) if archetype is Archetype.ASSIGNED else None,`.
  - `sellers.py` 끝: `DemoSeller("seller-mugshop-21", "연남머그상점", ("자사몰", "쿠팡", "스마트스토어"), "리빙"),`
  - `personas.py` 끝:

```python
    "seller-mugshop-21": _demo("seller-mugshop-21", "변재민", ("추하윤", "편서율"),
        ("(주)한양도자기 OEM", "(주)에어캡포장"), "CJ대한통운", "서울 마포구", (0.2, 0.45, 0.35), 24_000_000),
```

  - `demo.py` 끝: `("seller-mugshop-21", Archetype.ASSIGNED, 121, False),`
  - `classify.py` `_RULES` 끝(`대출금` 다음): `("TRANSFER_IN", "선정산", Category.LOAN_PROCEEDS),`
  - 원형 전수 맵 확인: `rg -n "Archetype\.THIN_FILE" src tests`로 원형마다 값을 두는 dict가 더 있는지 보고, 있으면 ASSIGNED 값을 넣는다(평가 `COUNTERFACTUAL_SWAP`처럼 선택적 맵은 그대로 둔다). `LOAD_ARCHETYPE`·`LOAD_GRID`는 바꾸지 않는다.

- [ ] **Step 4: 통과 확인** — Step 2 경로 + `tests/evals tests/console` → PASS. 평가 데이터셋은 `ARCHETYPES = tuple(Archetype)`이라 분류 앱(시드×5)·심사 사례(+2)가 늘어난다. 건수를 고정한 평가 테스트는 새 건수로 바꾼다. 상호 중복 테스트(`test_demo_sellers_have_unique_names_apart_from_load_sellers`)가 실패하면 상호를 바꾼다.
- [ ] **Step 5: 커밋** — `feat(mockapis): 선정산 양도 원형(ASSIGNED)과 데모 셀러 mugshop-21`

---

### Task 5: 결정적 정산 대사 모듈 `pg_ledger.py`

**Files:**
- Create: `src/agents/pg_ledger.py`
- Create: `tests/agents/test_pg_ledger.py`

**Interfaces:**
- Consumes: `PgSettlement`(T1), `Transaction`(`shared.contracts`), `add_business_days`(`shared.krcalendar`).
- Produces: `ledger_summary(settlements, transactions, as_of) -> dict`(키는 아래 Step 3 docstring), `features_of(summary) -> dict[str, float | None]`(`pg_reconciliation_ratio`, `pg_top_platform_share`), `FLAG_PG_LEDGER_UNAVAILABLE = "pg_ledger_unavailable"`, `MATCH_BUSINESS_DAYS = 3`.

- [ ] **Step 1: 실패 테스트 작성** — `tests/agents/test_pg_ledger.py`(기준일 2026-11-01, 10/9 한글날 휴일. 거래 생성 도우미는 `tests/agents`의 기존 `_txn` 도우미가 있으면 그것을 쓰고 없으면 아래처럼 만든다):

```python
from datetime import date

from agents.pg_ledger import features_of, ledger_summary
from shared.contracts import PgSettlement, Transaction

AS_OF = date(2026, 11, 1)


def _s(day, platform, gross, fee=0, ded=0):
    return PgSettlement(settled_on=day, platform=platform, gross_krw=gross, fee_krw=fee, deduction_krw=ded,
                        net_krw=gross - fee - ded)


def _t(day, amount, desc, code="PG_SETTLE", i=[0]):
    i[0] += 1
    return Transaction(txn_id=f"t{i[0]}", booked_on=day, amount_krw=amount, balance_after_krw=0, description=desc,
                       txn_type_code=code)


def test_matches_within_three_business_days_and_reports_unpaid():
    s = [_s(date(2026, 10, 13), "쿠팡", 1000, 100, 20), _s(date(2026, 10, 14), "쿠팡", 2000, 200),
         _s(date(2026, 10, 15), "스마트스토어", 500, 30)]
    t = [_t(date(2026, 10, 15), 880, "쿠팡(주) 주정산"),            # +2영업일 → 짝
         _t(date(2026, 10, 20), 1800, "쿠팡(주) 주정산"),           # +4영업일 → 미매칭
         _t(date(2026, 10, 15), 470, "네이버파이낸셜 정산")]
    r = ledger_summary(s, t, AS_OF)
    assert r["available"] and r["settlement_count"] == 3
    assert r["pg_reconciliation_ratio"] == round((880 + 470) / (880 + 1800 + 470), 3)
    assert r["unmatched_count"] == 1 and r["unmatched"][0]["net_krw"] == 1800
    coupang = next(p for p in r["platforms"] if p["platform"] == "쿠팡")
    assert coupang["unpaid_krw"] == 1800 and r["platforms"][0]["platform"] == "쿠팡"


def test_same_amount_same_day_matches_once():
    s = [_s(date(2026, 10, 13), "쿠팡", 1000), _s(date(2026, 10, 13), "쿠팡", 1000)]
    t = [_t(date(2026, 10, 13), 1000, "쿠팡(주) 주정산")]
    r = ledger_summary(s, t, AS_OF)
    assert r["pg_reconciliation_ratio"] == 0.5 and r["unmatched_count"] == 1


def test_recent_settlements_excluded():
    s = [_s(date(2026, 10, 13), "쿠팡", 1000), _s(date(2026, 10, 29), "쿠팡", 5000)]  # 10/29 + 3영업일 ≥ 기준일
    t = [_t(date(2026, 10, 13), 1000, "쿠팡(주) 주정산")]
    r = ledger_summary(s, t, AS_OF)
    assert r["settlement_count"] == 1 and r["pg_reconciliation_ratio"] == 1.0


def test_concentration_take_rate_trend_and_features():
    s = [_s(date(2026, m, 10), "쿠팡", 1000, 100, 10 * (m - 4)) for m in range(5, 11)]
    s += [_s(date(2026, m, 10), "스마트스토어", 1000, 60) for m in range(5, 11)]
    t = [_t(x.settled_on, x.net_krw, "쿠팡(주) 주정산" if x.platform == "쿠팡" else "네이버파이낸셜 정산") for x in s]
    r = ledger_summary(s, t, AS_OF)
    assert r["pg_top_platform_share"] == 0.5 and r["pg_hhi"] == 0.5
    assert [m["month"] for m in r["monthly_take_rate"]] == ["2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]
    assert r["pg_take_rate_trend_pp"] == round((r["monthly_take_rate"][-1]["rate"] - r["monthly_take_rate"][0]["rate"]) * 100, 2) > 0
    assert features_of(r) == {"pg_reconciliation_ratio": 1.0, "pg_top_platform_share": 0.5}


def test_unavailable_without_settlements():
    assert ledger_summary([], [], AS_OF) == {"available": False}
    assert features_of({"available": False}) == {"pg_reconciliation_ratio": None, "pg_top_platform_share": None}
```

`Transaction` 필드는 `src/shared/contracts.py:53`(txn_id, booked_on, amount_krw, balance_after_krw, description, txn_type_code)이다.

- [ ] **Step 2: 실패 확인** — `tests/agents/test_pg_ledger.py` → FAIL(모듈 없음).

- [ ] **Step 3: 구현** — `src/agents/pg_ledger.py`:

```python
"""정산 원장 ↔ 계좌 입금 대사, 채널 집중, 공제율 추이(스펙 §3.2). 결정적 코드만 지표를 만든다.

대상 기간은 기준일 직전 6개 완결 월. 기준일 직전 3영업일 안의 지급은 아직 입금 전일 수 있어 뺀다.
매칭은 같은 플랫폼·같은 금액·지급일부터 3영업일 안의 가장 이른 미사용 입금(한 입금은 한 번만).
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date

from shared.contracts import PgSettlement, Transaction
from shared.krcalendar import add_business_days

MATCH_BUSINESS_DAYS = 3
WINDOW_MONTHS = 6
UNMATCHED_LIMIT = 10
FLAG_PG_LEDGER_UNAVAILABLE = "pg_ledger_unavailable"
PAYER_PLATFORM = {
    "네이버파이낸셜": "스마트스토어",
    "쿠팡(주) 주정산": "쿠팡",
    "쿠팡(주) 최종액정산": "쿠팡",
    "11번가": "11번가",
    "토스페이먼츠": "자사몰",
}


def _month_start(d: date, back: int = 0) -> date:
    m = d.year * 12 + d.month - 1 - back
    return date(m // 12, m % 12 + 1, 1)


def _platform_of(t: Transaction) -> str | None:
    return next((p for payer, p in PAYER_PLATFORM.items() if payer in t.description), None)


def ledger_summary(settlements: Sequence[PgSettlement], transactions: Sequence[Transaction], as_of: date) -> dict:
    """키: available, period{from,to}, settlement_count, pg_reconciliation_ratio, pg_top_platform_share, pg_hhi,
    pg_take_rate_trend_pp, platforms[{platform,gross_krw,share,unpaid_krw}], monthly_take_rate[{month,rate}],
    unmatched_count, unmatched[{settled_on,platform,net_krw}](최근 10건)."""
    start, end = _month_start(as_of, WINDOW_MONTHS), _month_start(as_of)
    rows = sorted(
        (s for s in settlements
         if start <= s.settled_on < end and add_business_days(s.settled_on, MATCH_BUSINESS_DAYS) < as_of),
        key=lambda s: (s.settled_on, s.platform, s.net_krw),
    )
    if not rows:
        return {"available": False}
    deposits = sorted((t for t in transactions if t.amount_krw > 0 and _platform_of(t)),
                      key=lambda t: (t.booked_on, t.txn_id))
    used: set[str] = set()
    unmatched: list[PgSettlement] = []
    for s in rows:
        last = add_business_days(s.settled_on, MATCH_BUSINESS_DAYS)
        hit = next((t for t in deposits if t.txn_id not in used and _platform_of(t) == s.platform
                    and s.settled_on <= t.booked_on <= last and t.amount_krw == s.net_krw), None)
        if hit is None:
            unmatched.append(s)
        else:
            used.add(hit.txn_id)
    target = sum(s.net_krw for s in rows)
    gross: dict[str, int] = defaultdict(int)
    unpaid: dict[str, int] = defaultdict(int)
    by_month: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for s in rows:
        gross[s.platform] += s.gross_krw
        m = by_month[s.settled_on.strftime("%Y-%m")]
        m[0] += s.gross_krw
        m[1] += s.fee_krw + s.deduction_krw
    for s in unmatched:
        unpaid[s.platform] += s.net_krw
    total = sum(gross.values())
    shares = {p: g / total for p, g in gross.items()} if total else {}
    monthly = [{"month": k, "rate": round(v[1] / v[0], 4)} for k, v in sorted(by_month.items()) if v[0] > 0]
    return {
        "available": True,
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "settlement_count": len(rows),
        "pg_reconciliation_ratio": round(1 - sum(s.net_krw for s in unmatched) / target, 3) if target > 0 else None,
        "pg_top_platform_share": round(max(shares.values()), 3) if shares else None,
        "pg_hhi": round(sum(v * v for v in shares.values()), 3) if shares else None,
        "pg_take_rate_trend_pp": (round((monthly[-1]["rate"] - monthly[0]["rate"]) * 100, 2)
                                  if len(monthly) >= WINDOW_MONTHS else None),
        "platforms": [{"platform": p, "gross_krw": g, "share": round(shares[p], 3), "unpaid_krw": unpaid[p]}
                      for p, g in sorted(gross.items(), key=lambda kv: (-kv[1], kv[0]))],
        "monthly_take_rate": monthly,
        "unmatched_count": len(unmatched),
        "unmatched": [{"settled_on": s.settled_on.isoformat(), "platform": s.platform, "net_krw": s.net_krw}
                      for s in sorted(unmatched, key=lambda s: s.settled_on, reverse=True)[:UNMATCHED_LIMIT]],
    }


def features_of(summary: Mapping) -> dict[str, float | None]:
    return {"pg_reconciliation_ratio": summary.get("pg_reconciliation_ratio"),
            "pg_top_platform_share": summary.get("pg_top_platform_share")}
```

`PAYER_PLATFORM` 키는 `src/mockapis/sellerdata/channels.py`의 정산 적요(`description`)와 `src/agents/settlement.py`의 `PAYERS`에 실제로 있는 문자열이어야 한다. 구현 전에 두 파일을 읽어 맞춘다(쿠팡 적요가 다르면 키를 바꾼다). 자사몰(`CARD_SETTLE`)은 원장에 없으므로 매칭되지 않아도 무해하다.

- [ ] **Step 4: 통과 확인** — `tests/agents/test_pg_ledger.py` → PASS.
- [ ] **Step 5: 커밋** — `feat(agents): 정산 원장 대사·채널 집중·공제율 추이(pg_ledger)`

---

### Task 6: 심사 특성 확장(매출 추세·고비용 대출·정산 지표)

**Files:**
- Modify: `src/shared/contracts.py:99-119` (`CashFlowFeatures`)
- Modify: `src/agents/features.py` (`Underwriting`, `_compute`, `compute_underwriting`, `_conservative`, 새 `_revenue_trend`)
- Test: `tests/agents/test_features.py`, `tests/shared/test_contracts.py`

**Interfaces:**
- Consumes: `ledger_summary`, `features_of`(T5).
- Produces: `CashFlowFeatures`의 선택 필드 `revenue_trend_ratio: NonNegFloat | None = None`, `high_cost_lender_count: int | None = None`(≥0), `pg_reconciliation_ratio`·`pg_top_platform_share`: 0~1 `| None = None`. `compute_underwriting(..., pg_settlements: Sequence[PgSettlement] | None = None)`. `Underwriting.pg_ledger: dict`(기본 `{"available": False}`). `signals`에 `pg_hhi`·`pg_take_rate_trend_pp`(None이 아닐 때만).

- [ ] **Step 1: 실패 테스트 작성** — `tests/agents/test_features.py`. 기존 `_underwrite(cases, applicant_id, transactions=None)`에 키워드 `pg=True`를 더해 `pg_settlements=case.pg_settlements if pg else None`을 넘긴다(기존 호출은 그대로 정산 포함). 임포트에 `from datetime import timedelta`. 그리고:

```python
def test_revenue_trend_lower_for_stressed_and_none_for_short_history(cases):
    healthy = _underwrite(cases, "seller-dailylook-01").features.revenue_trend_ratio
    stressed = _underwrite(cases, "seller-glowlab-03").features.revenue_trend_ratio
    assert healthy is not None and stressed is not None and stressed < 0.8 <= healthy
    recent = [t for t in cases["seller-dailylook-01"].transactions if t.booked_on >= AS_OF - timedelta(days=100)]
    assert _underwrite(cases, "seller-dailylook-01", transactions=recent).features.revenue_trend_ratio is None


def test_high_cost_lender_count_matches_signal(cases):
    uw = _underwrite(cases, "seller-glowlab-03")
    assert uw.features.high_cost_lender_count == uw.signals["high_cost_lender_count"] > 0
    assert _underwrite(cases, "seller-dailylook-01").features.high_cost_lender_count == 0


def test_pg_features_and_ledger_detail(cases):
    uw = _underwrite(cases, "seller-dailylook-01")
    assert uw.pg_ledger["available"] is True
    assert uw.features.pg_reconciliation_ratio == uw.pg_ledger["pg_reconciliation_ratio"] > 0.9
    assert uw.features.pg_top_platform_share == uw.pg_ledger["pg_top_platform_share"]
    assert uw.signals["pg_hhi"] == uw.pg_ledger["pg_hhi"]


def test_pg_features_none_without_settlements(cases):
    uw = _underwrite(cases, "seller-dailylook-01", pg=False)
    assert uw.features.pg_reconciliation_ratio is None and uw.features.pg_top_platform_share is None
    assert uw.pg_ledger == {"available": False} and "pg_hhi" not in uw.signals
```

`tests/shared/test_contracts.py`(기존 도우미 `_features(**kw)`, :43):

```python
def test_cash_flow_features_v3_fields_optional_and_bounded():
    f = _features()
    assert (f.revenue_trend_ratio, f.high_cost_lender_count, f.pg_reconciliation_ratio, f.pg_top_platform_share) == (None,) * 4
    with pytest.raises(ValidationError):
        f.model_validate(f.model_dump() | {"pg_top_platform_share": 1.2})
    with pytest.raises(ValidationError):
        f.model_validate(f.model_dump() | {"high_cost_lender_count": -1})
```

- [ ] **Step 2: 실패 확인** — `tests/agents/test_features.py tests/shared/test_contracts.py` → FAIL.

- [ ] **Step 3: 구현**
  - `contracts.py` `CashFlowFeatures` 끝에:

```python
    # 정책 v3(2026-11-08) 선택 지표. 옛 문서·자료 없음은 None이며 해당 규칙은 적용하지 않는다.
    revenue_trend_ratio: NonNegFloat | None = None
    high_cost_lender_count: int | None = Field(default=None, ge=0)
    pg_reconciliation_ratio: Annotated[float, Field(ge=0, le=1)] | None = None
    pg_top_platform_share: Annotated[float, Field(ge=0, le=1)] | None = None
```

  - `features.py`:

```python
TREND_RECENT_DAYS = 60
TREND_PRIOR_DAYS = 180
TREND_MIN_PRIOR_DAYS = 60


def _revenue_trend(txns: Sequence[Transaction], categories: Mapping[str, Category], as_of: date) -> float | None:
    """최근 60일 일평균 매출 ÷ 그 앞(최대 180일, 최소 60일) 일평균 매출."""
    if not txns:
        return None
    split = as_of - timedelta(days=TREND_RECENT_DAYS)
    prior_from = max(split - timedelta(days=TREND_PRIOR_DAYS), min(t.booked_on for t in txns))
    prior_days = (split - prior_from).days
    if prior_days < TREND_MIN_PRIOR_DAYS:
        return None
    rev = [t for t in txns if categories.get(t.txn_id) is Category.REVENUE and t.amount_krw > 0]
    recent = sum(t.amount_krw for t in rev if split <= t.booked_on < as_of) / TREND_RECENT_DAYS
    prior = sum(t.amount_krw for t in rev if prior_from <= t.booked_on < split) / prior_days
    return round(recent / prior, 3) if prior > 0 else None
```

  `_compute`(`features.py:98`)에서 `counts` 계산(현재 :153)을 `CashFlowFeatures(...)` 생성 앞으로 옮기고 `revenue_trend_ratio=_revenue_trend(transactions, categories, as_of)`, `high_cost_lender_count=counts[Category.HIGH_COST_LENDER]`를 넘긴다(`categories`는 `_compute`의 분류 매핑 인자 이름). `_conservative`:

```python
def _min_opt(a: float | None, b: float | None) -> float | None:
    return min((v for v in (a, b) if v is not None), default=None)


def _conservative(base: CashFlowFeatures, adjusted: CashFlowFeatures) -> CashFlowFeatures:
    hc = [v for v in (base.high_cost_lender_count, adjusted.high_cost_lender_count) if v is not None]
    return base.model_copy(
        update={
            "dscr": min(base.dscr, adjusted.dscr),
            "cash_buffer_days": min(base.cash_buffer_days, adjusted.cash_buffer_days),
            "revenue_trend_ratio": _min_opt(base.revenue_trend_ratio, adjusted.revenue_trend_ratio),
            "high_cost_lender_count": max(hc) if hc else None,
        }
    )
```

  `compute_underwriting`: 키워드 `pg_settlements: Sequence[PgSettlement] | None = None`를 `semantic_labels` 뒤에 추가하고, `features = _conservative(...)` 다음에:

```python
    pg = ledger_summary(pg_settlements or (), transactions, as_of)
    features = features.model_copy(update=features_of(pg))
```

  `signals` dict 끝에 `**{k: pg[k] for k in ("pg_hhi", "pg_take_rate_trend_pp") if pg.get(k) is not None},`, `Underwriting(...)`에 `pg_ledger=pg,`. `Underwriting`(`features.py:64`, frozen dataclass) 끝 필드로 `pg_ledger: dict = field(default_factory=lambda: {"available": False})`. 임포트: `from agents.pg_ledger import features_of, ledger_summary`, `PgSettlement`.

- [ ] **Step 4: 통과 확인** — `tests/agents tests/shared` → PASS. 근거 카탈로그(`evidence.build_catalog`)는 숫자가 아닌 값을 건너뛰므로 None 필드는 근거 ID가 생기지 않는다(확인만).
- [ ] **Step 5: 커밋** — `feat(agents): 매출 추세·고비용 대출 수·정산 지표 특성`

---

### Task 7: 정책 v3 임계값 게이트(영구 테스트)

**Files:**
- Create: `tests/mockapis/test_policy_v3_thresholds.py`

**Interfaces:**
- Consumes: `build_demo_cases`(`mockapis.demo`), `datasets.underwriting_cases`(`evals.datasets`), `compute_underwriting`(T6).
- Produces: 없음(분포 출력과 단언만). 이 테스트가 v3 임계값(0.9·0.8)의 근거다.

- [ ] **Step 1: 테스트 작성**

```python
"""정책 v3 임계값이 모의 데이터 분포에서 정상 셀러와 위험 원형을 가르는지 확인한다(스펙 §5)."""

from datetime import date

import pytest

from agents.features import compute_underwriting
from evals.datasets import AS_OF as EVAL_AS_OF
from evals.datasets import underwriting_cases
from mockapis.demo import build_demo_cases
from mockapis.synth import Archetype

AS_OFS = (date(2026, 10, 1), date(2026, 11, 2), date(2026, 11, 9))
HEALTHY = {Archetype.HEALTHY_SEASONAL, Archetype.TAX_MISMATCH}


def _cases():
    for as_of in AS_OFS:
        for applicant_id, case in build_demo_cases(as_of).items():
            yield applicant_id, case, as_of
    for c in underwriting_cases():
        yield c.case_id, c.case, EVAL_AS_OF


@pytest.fixture(scope="module")
def dist():
    out: dict[Archetype, list] = {}
    for applicant_id, case, as_of in _cases():
        uw = compute_underwriting(f"app-{applicant_id}", case.profile, case.transactions, case.tax_filings,
                                  as_of=as_of, requested_amount_krw=3_300_000, requested_tenor_months=12,
                                  pg_settlements=case.pg_settlements)
        out.setdefault(case.archetype, []).append(uw.features)
    for a, fs in out.items():
        print(a.value, "recon", sorted(f.pg_reconciliation_ratio for f in fs if f.pg_reconciliation_ratio is not None),
              "trend", sorted(f.revenue_trend_ratio for f in fs if f.revenue_trend_ratio is not None))
    return out


def test_healthy_reconciliation_above_threshold(dist):
    vals = [f.pg_reconciliation_ratio for a in HEALTHY for f in dist.get(a, []) if f.pg_reconciliation_ratio is not None]
    assert vals and min(vals) > 0.90


def test_assigned_reconciliation_below_threshold(dist):
    vals = [f.pg_reconciliation_ratio for f in dist[Archetype.ASSIGNED]]
    assert vals and max(vals) < 0.90


def test_stressed_revenue_trend_below_threshold(dist):
    vals = [f.revenue_trend_ratio for f in dist[Archetype.STRESSED] if f.revenue_trend_ratio is not None]
    assert vals and max(vals) < 0.80
```

`build_demo_cases(as_of)`는 `{applicant_id: SyntheticCase}`, `underwriting_cases()`는 `UwCase(case_id, case, injected)` 목록이며 `SyntheticCase.archetype`이 원형이다.

- [ ] **Step 2: 실행** — `tests/mockapis/test_policy_v3_thresholds.py -s` → PASS 기대. 출력 분포를 커밋 메시지 본문에 붙인다.
- [ ] **Step 3: 실패하면 중단·보고** — 임계값을 테스트에 맞춰 바꾸지 말고, 어느 원형·사례가 경계를 넘었는지와 분포를 사용자에게 보고한다(정책 임계값은 스펙 결정 사항).
- [ ] **Step 4: 커밋** — `test(policy): v3 임계값 분포 게이트`

---

### Task 8: 정책 v3

**Files:**
- Create: `src/policy/rulesets/v3.yaml`
- Modify: `src/policy/rules.py:10-19` (`DEFAULT_RULES_PATH`, `FeatureName`)
- Modify: `src/policy/engine.py:12` (`_check`)
- Test: `tests/policy/test_engine.py`, `tests/policy/test_rules.py`, `tests/policy/test_app.py`, `tests/mockapis/test_decision_invariance.py`, `tests/agents/test_features.py:64-79`

**Interfaces:**
- Consumes: `CashFlowFeatures` v3 선택 필드(T6).
- Produces: 규칙 버전 `"2026.11-v3"`; 회부 R102~R104 + R105 `high_cost_lender_count gt 0`, R106 `revenue_trend_ratio lt 0.8`, R107 `pg_top_platform_share gt 0.8`, R108 `pg_reconciliation_ratio lt 0.9`; 값이 None인 규칙은 건너뛴다.

- [ ] **Step 1: 실패 테스트 작성** — `tests/policy/test_engine.py`(conftest 픽스처 `make_features(**kw)`, `rules` 사용; `rules`는 `DEFAULT_RULES_PATH`를 로드한다):

```python
def test_v3_skips_rules_with_missing_values(make_features, rules):
    d = evaluate(make_features(), rules)  # 새 선택 필드 모두 None
    assert rules.version == "2026.11-v3" and d.decision is Decision.ELIGIBLE and d.reason_codes == ()


@pytest.mark.parametrize(("field", "value", "code"), [
    ("high_cost_lender_count", 1, "R105"),
    ("revenue_trend_ratio", 0.79, "R106"),
    ("pg_top_platform_share", 0.81, "R107"),
    ("pg_reconciliation_ratio", 0.89, "R108"),
])
def test_v3_refer_rules(make_features, rules, field, value, code):
    d = evaluate(make_features(**{field: value}), rules)
    assert d.decision is Decision.REFER and _codes(d) == [code]


def test_v3_has_no_tax_mismatch_rule(make_features, rules):
    assert "R101" not in _codes(evaluate(make_features(tax_bank_match_ratio=0.0), rules))
```

`rules` 픽스처(`tests/policy/conftest.py:31`)가 v2 경로를 직접 쓰면 `DEFAULT_RULES_PATH`로 바꾼다. 파일 맨 위에 `import pytest`를 더한다. 기존 `test_healthy_is_eligible`의 `rules_version == "2026.11-v2"` → `"2026.11-v3"`.

- [ ] **Step 2: 실패 확인** — `tests/policy/test_engine.py` → FAIL.

- [ ] **Step 3: 구현**
  - `v3.yaml`:

```yaml
# 현금흐름 기반 온라인 셀러 소액 운전자금 대출 정책 v3 (데모용 수치, 50만~1,000만 원)
# v2 대비: R101(신고매출·계좌매출 불일치) 삭제 — 간이·면세 사업자와 신고 주기 차이로 오탐이 많다(ADR-003).
# R105~R108 추가. 값이 없는 지표(정산 자료 없음 등)의 규칙은 적용하지 않는다.
version: "2026.11-v3"
product:
  min_amount_krw: 500000
  max_amount_krw: 10000000
  inflow_multiplier: 1.5
knockouts:
  - {code: R001, message: "거래 이력 6개월 미만", field: months_of_history, op: lt, threshold: 6}
  - {code: R002, message: "DSCR 1.0 미만", field: dscr, op: lt, threshold: 1.0}
refers:
  - {code: R102, message: "매출 변동성 과다", field: revenue_volatility, op: gt, threshold: 0.5}
  - {code: R103, message: "현금 완충일수 부족", field: cash_buffer_days, op: lt, threshold: 10}
  - {code: R104, message: "업력 12개월 미만", field: business_age_months, op: lt, threshold: 12}
  - {code: R105, message: "고비용 대부·P2P 상환 발견", field: high_cost_lender_count, op: gt, threshold: 0}
  - {code: R106, message: "최근 60일 매출 급감", field: revenue_trend_ratio, op: lt, threshold: 0.8}
  - {code: R107, message: "판매 채널 편중", field: pg_top_platform_share, op: gt, threshold: 0.8}
  - {code: R108, message: "정산–입금 대사 불일치", field: pg_reconciliation_ratio, op: lt, threshold: 0.9}
rate_bands:
  - {band: A, min_dscr: 2.0}
  - {band: B, min_dscr: 1.5}
  - {band: C, min_dscr: 1.2}
  - {band: D, min_dscr: 1.0}
```

  - `rules.py`: `DEFAULT_RULES_PATH = ... / "v3.yaml"`, `FeatureName`에 `"high_cost_lender_count"`, `"revenue_trend_ratio"`, `"pg_top_platform_share"`, `"pg_reconciliation_ratio"` 추가(v2.yaml은 그대로 둔다 — 옛 결과 재현용).
  - `engine.py`:

```python
def _check(cond: Condition, features: CashFlowFeatures) -> ReasonCode | None:
    value = getattr(features, cond.field)
    if value is None:  # 선택 지표가 없으면 규칙을 적용하지 않는다(정책 v3)
        return None
    observed = float(value)
```

- [ ] **Step 4: 통과 확인** — `tests/policy` → `rules_version`·R101을 고정한 테스트는 v3 기대값으로 바꾼다(`rg -n "2026.11-v2|R101" tests src`로 전부 찾는다. `src`에서 v2 문자열을 하드코딩한 곳이 있으면 `DEFAULT_RULES_PATH`·로드된 `version`을 쓰게 바꾼다).

- [ ] **Step 5: 판정 불변 테스트 재고정** — `tests/mockapis/test_decision_invariance.py`의 `_decision`이 `pg_settlements=case.pg_settlements`를 넘기게 바꾸고, 판정을 다시 계산해 아래 규칙으로만 바뀌었는지 확인한 뒤 `DEMO_EXPECTED`·`LOAD_EXPECTED`를 고친다(`seller-mugshop-21` 추가):
  - R101 삭제: TAX_MISMATCH(mangwon-02·kimchi-09·kitchen-12·petcare-16, 부하 `mismatch`) → 적격(다른 규칙이 없으면).
  - THIN(knitroom-04·leather-14·tint-17·streetfit-19, 부하 `thin`) → R001 + R104(+ 단일 PG 채널이면 R107), 부적격.
  - STRESSED(glowlab-03, 부하 `stress`) → R105(+ R106) 회부.
  - 단일 PG 채널 정상 셀러(candle-05·ceramic-08 등) → R107 회부.
  - ASSIGNED(mugshop-21) → R108(+ R106 가능) 회부.
  - `_with_young_business`는 그대로 쓴다.
  - 이 규칙으로 설명되지 않는 변화(예: 정상 셀러 R108·R106, 금리 구간 변화)가 있으면 재고정하지 말고 멈춘 뒤 보고한다.
  - `tests/agents/test_features.py`의 `test_policy_outcomes_for_demo_applicants`도 같은 규칙으로 고친다(mangwon-02 → `eligible, []`, knitroom-04 → `["R001", "R104", "R107"]` 또는 실제 값, glowlab-03 → R105 포함 회부). 회부 셀러는 한도·금리 단언(`decision != "ineligible"`)이 유지되는지 본다.

- [ ] **Step 6: 전체 실행** — 경로 없이 전체 → PASS(콘솔·평가의 v2 고정 문자열도 이 단계에서 고친다. 평가 지표 의미 변경은 T12에서 한다 — 여기서는 R101 기대값만 바꾸고 `tax_mismatch_detection_rate`는 T12 전까지 건드리지 않는다. 실패하면 T12 Step 범위를 앞당겨 같은 커밋에 넣는다).
- [ ] **Step 7: 커밋** — `feat(policy): 정책 v3(R101 삭제, R105~R108)` — 본문에 데모 셀러 판정 변화 표.

---

### Task 9: 오케스트레이터 연결

**Files:**
- Modify: `src/orchestrator/journey/activities.py:294-385` (`_load_pg` 신설, `underwrite`, `assess_credit`)
- Test: `tests/orchestrator/test_activities.py`

**Interfaces:**
- Consumes: `compute_underwriting(..., pg_settlements=)`·`Underwriting.pg_ledger`(T6), `FLAG_PG_LEDGER_UNAVAILABLE`(T5), `UnderwritingContext.settlement`(T10 — 이 태스크에서 필드만 먼저 넣는다).
- Produces: `underwrite` 결과에 `"pg_ledger"`; `pg.json` 손상 시 `flags`에 `"pg_ledger_unavailable"`. `assess_credit`이 `settlement=uw.get("pg_ledger")`를 넘긴다(옛 결과는 None).

- [ ] **Step 1: 실패 테스트 작성** — `tests/orchestrator/test_activities.py`의 `test_underwrite_flags_high_cost_lender` 아래:

```python
def test_underwrite_returns_pg_ledger(deps):
    consent, _ = assembled(deps)
    result = underwrite(deps, underwrite_input(consent["consent_id"]))
    assert result["pg_ledger"]["available"] is True
    assert result["features"]["pg_reconciliation_ratio"] == result["pg_ledger"]["pg_reconciliation_ratio"]


def test_underwrite_without_pg_json_has_no_ledger(deps):
    consent, _ = assembled(deps)
    deps.raw.blobs.pop((f"consent-{consent['consent_id']}", "pg.json"))
    result = underwrite(deps, underwrite_input(consent["consent_id"]))
    assert result["pg_ledger"] == {"available": False} and "pg_ledger_unavailable" not in result["flags"]
    assert result["features"]["pg_reconciliation_ratio"] is None


def test_underwrite_with_corrupt_pg_json_flags(deps):
    consent, _ = assembled(deps)
    deps.raw.blobs[(f"consent-{consent['consent_id']}", "pg.json")] = [{"platform": ""}]
    result = underwrite(deps, underwrite_input(consent["consent_id"]))
    assert result["pg_ledger"] == {"available": False} and "pg_ledger_unavailable" in result["flags"]
```

가짜 저장소(`test_activities.py:70`)는 JSON 객체를 그대로 저장한다. 기존 `assess_credit` 테스트에 `settlement` 전달 단언을 한 줄 더한다(가짜 러너가 컨텍스트를 기록하지 않으면 `monkeypatch.setattr(activities, "UnderwritingContext", spy)`로 키워드 인자를 잡는다).

- [ ] **Step 2: 실패 확인** — `tests/orchestrator/test_activities.py -k "pg"` → FAIL.

- [ ] **Step 3: 구현** — `activities.py`(임포트 `from agents.pg_ledger import FLAG_PG_LEDGER_UNAVAILABLE`, `PgSettlement`; `_PG = TypeAdapter(list[PgSettlement])`를 `_FILINGS` 옆에):

```python
def _load_pg(deps: Deps, consent_id: str) -> tuple[list[PgSettlement], list[str]]:
    """정산 원장. 없으면 빈 목록, 손상이면 빈 목록 + 플래그(심사는 계속한다)."""
    raw = deps.raw.get_json(raw_container(consent_id), "pg.json")
    if raw is None:
        return [], []
    try:
        return _PG.validate_python(raw), []
    except ValueError:
        return [], [FLAG_PG_LEDGER_UNAVAILABLE]
```

  `underwrite`: `pg, pg_flags = _load_pg(deps, payload["consent_id"])`; `compute_underwriting(..., pg_settlements=pg)`; 반환 `"flags": [*result.flags, *pg_flags]`, `"pg_ledger": result.pg_ledger`. `get_json`이 손상된 JSON에서 예외를 내면 그것도 `ValueError`로 잡히는지 확인한다(`json.JSONDecodeError`는 `ValueError`의 하위 클래스). `assess_credit`: `UnderwritingContext(..., settlement=uw.get("pg_ledger"),)`.

- [ ] **Step 4: 통과 확인** — `tests/orchestrator` → PASS(T10 전이므로 `UnderwritingContext.settlement` 필드를 이 태스크에서 함께 추가한다: `settlement: Mapping[str, Any] | None = None`을 `mask` 다음, `catalog` 앞에).
- [ ] **Step 5: 커밋** — `feat(orchestrator): 정산 원장 대사 결과를 심사에 연결`

---

### Task 10: 분석 에이전트 도구 `get_settlement_summary`

**Files:**
- Modify: `src/agents/underwriting.py:46-60`(지시문), `:81-95`(`ANALYST_TOOLS`), `:252-325`(`UnderwritingContext`)
- Test: `tests/agents/test_underwriting.py`

**Interfaces:**
- Consumes: `UnderwritingContext.settlement`(T9), 카탈로그의 `M:pg_reconciliation_ratio`·`M:pg_top_platform_share`·`M:pg_hhi`·`M:pg_take_rate_trend_pp`(T6 → `build_catalog`).
- Produces: 도구 `get_settlement_summary`(인자 없음). 정산 자료가 없으면 `{"available": false}`.

- [ ] **Step 1: 실패 테스트 작성** — `tests/agents/test_underwriting.py`의 `context(cases, applicant_id)` 도우미(:66)를 고친다: `compute_underwriting(..., pg_settlements=case.pg_settlements)`, `UnderwritingContext(..., settlement=uw.pg_ledger)`. 임포트 `from dataclasses import replace`. 그리고:

```python
def test_settlement_summary_tool_returns_ids_and_platforms(cases):
    ctx = context(cases)
    out = ctx.handlers()["get_settlement_summary"]({})
    assert out["available"] is True
    assert out["metric_ids"]["M:pg_reconciliation_ratio"] == ctx.features["pg_reconciliation_ratio"]
    assert out["metric_ids"]["M:pg_top_platform_share"] == ctx.features["pg_top_platform_share"]
    assert out["metric_ids"]["M:pg_hhi"] == ctx.signals["pg_hhi"]
    assert out["platforms"][0].keys() == {"platform", "share"}
    assert "unmatched" not in out  # 개별 미입금 금액은 근거 ID가 없어 주지 않는다


def test_settlement_summary_unavailable(cases):
    ctx = replace(context(cases), settlement=None)
    assert ctx.handlers()["get_settlement_summary"]({}) == {"available": False}
    assert any(t.get("name") == "get_settlement_summary" for t in ANALYST_TOOLS)
```

`replace`는 `init=False` 필드(`catalog`, `by_id`)를 `__post_init__`으로 다시 만든다.

- [ ] **Step 2: 실패 확인** — `tests/agents/test_underwriting.py -k settlement` → FAIL.

- [ ] **Step 3: 구현**
  - `ANALYST_TOOLS`의 `get_monthly_series` 다음:

```python
    _fn("get_settlement_summary",
        "마켓플레이스 정산 원장 대사 결과(정산금 계좌 입금 비율, 채널 집중도, 공제율 추이, 플랫폼별 비중)를 돌려준다.", {}),
```

  - `UnderwritingContext`:

```python
    def get_settlement_summary(self, _: dict[str, Any]) -> dict[str, Any]:
        s = self.settlement or {}
        if not s.get("available"):
            return {"available": False}
        ids = ("M:pg_reconciliation_ratio", "M:pg_top_platform_share", "M:pg_hhi", "M:pg_take_rate_trend_pp")
        return {
            "available": True,
            "period": s.get("period"),
            "settlement_count": s.get("settlement_count"),
            "metric_ids": {k: self.catalog[k] for k in ids if k in self.catalog},
            "platforms": [{k: p.get(k) for k in ("platform", "share")} for p in s.get("platforms", ())],
            "monthly_take_rate": list(s.get("monthly_take_rate", ())),
            "unmatched_count": s.get("unmatched_count"),
        }
```

  `handlers()`에 `"get_settlement_summary": self.get_settlement_summary,`. 플랫폼별 원화 금액은 카탈로그에 없으므로 비중만 준다(근거 없는 숫자 인용 방지).
  - 지시문 1번 끝에 한 줄: `get_settlement_summary로 정산금 계좌 입금 비율·채널 집중·공제율 추이를 확인한다. 인용은 M:pg_* ID 값만 쓰고 미입금 개별 금액은 쓰지 않는다.` 정책 사유 R107·R108이 있으면 해당 지표를 counterfactual로 쓸 수 있음을 6번에 덧붙인다(`P:R108:threshold` 예시).

- [ ] **Step 4: 통과 확인** — `tests/agents` → PASS. 지시문 문자열을 고정한 테스트가 있으면 함께 고친다.
- [ ] **Step 5: 커밋** — `feat(agents): 분석 에이전트 정산 요약 도구`

---

### Task 11: 콘솔 — 정산 대사 카드와 v3 신호등

**Files:**
- Modify: `src/console/dashboard.py:227-276` (`settlement_card` 신설, `detail_summary`에 `"settlement"`)
- Modify: `src/console/templates/detail.html:84` 다음(월별 현금흐름 카드 뒤)
- Modify: `src/console/assessment.py:19-66, 168-195` (`SIGNAL_RULES`, `SIGNALS`, `_signal`, `_strengths_risks`)
- Modify: `src/console/opinion.py`(`METRIC_LABELS`·`TOOL_LABELS`), `src/console/statuses.py`(`FLAG_LABELS`)
- Modify: `src/consolepreview/fixtures.py`(정산 원장 전달, 문서에 `pg_ledger`)
- Test: `tests/console/test_dashboard.py`, `tests/console/test_assessment.py`, `tests/console/test_ui.py:74-90`

**Interfaces:**
- Consumes: 문서 `underwriting.pg_ledger`(T9), 특성 v3 필드(T6).
- Produces: `settlement_card(pg) -> dict | None`(`available`, `kpis`, `platforms`(hbars), `take_rate`(hbars), `unmatched`, `note`), `RECONCILIATION_WARN = 0.90`; 신호 상태 `watch`.

- [ ] **Step 1: 실패 테스트 작성**
  - `tests/console/test_dashboard.py`:

```python
LEDGER = {"available": True, "period": {"from": "2026-05-01", "to": "2026-11-01"}, "settlement_count": 120,
          "pg_reconciliation_ratio": 0.62, "pg_top_platform_share": 0.56, "pg_hhi": 0.45, "pg_take_rate_trend_pp": 1.8,
          "platforms": [{"platform": "쿠팡", "gross_krw": 60_000_000, "share": 0.56, "unpaid_krw": 21_000_000},
                        {"platform": "스마트스토어", "gross_krw": 47_000_000, "share": 0.44, "unpaid_krw": 0}],
          "monthly_take_rate": [{"month": "2026-05", "rate": 0.081}, {"month": "2026-10", "rate": 0.099}],
          "unmatched_count": 30, "unmatched": [{"settled_on": "2026-10-27", "platform": "쿠팡", "net_krw": 512_000}]}


def test_settlement_card_warns_low_reconciliation():
    card = settlement_card(LEDGER)
    assert card["available"] and card["kpis"][0]["tone"] == "red" and card["kpis"][0]["value"] == "62%"
    assert [b["label"] for b in card["platforms"]["bars"]][:1] == ["쿠팡"]
    assert card["unmatched"][0]["net_krw"] == 512_000 and card["unmatched_count"] == 30


def test_settlement_card_unavailable_for_old_docs():
    assert settlement_card(None) == {"available": False}
    assert settlement_card({"available": False}) == {"available": False}
```

  `charts.hbars`는 `{"width","height","bars":[{"label","value",...}]}`를 돌려준다(`src/console/charts.py:57`).
  - `tests/console/test_assessment.py`(기존 `view(make_doc, features=...)`, `by_field`, `STRONG` 사용):

```python
def test_tax_match_is_watch_not_refer(make_doc):
    s = by_field(assessment_panels(view(make_doc, features={**STRONG, "tax_bank_match_ratio": 0.5})))
    assert s["tax_bank_match_ratio"]["state"] == "watch" and s["tax_bank_match_ratio"]["tone"] == "amber"


def test_v3_optional_signals_hidden_when_missing(make_doc):
    fields = set(by_field(assessment_panels(view(make_doc, features=STRONG))))
    assert not fields & {"revenue_trend_ratio", "high_cost_lender_count", "pg_reconciliation_ratio", "pg_top_platform_share"}


def test_v3_signal_refer_on_low_reconciliation(make_doc):
    p = assessment_panels(view(make_doc, features={**STRONG, "pg_reconciliation_ratio": 0.62, "pg_top_platform_share": 0.56}))
    s = by_field(p)
    assert s["pg_reconciliation_ratio"]["state"] == "refer" and s["pg_top_platform_share"]["state"] == "pass"
    assert any("정산" in r for r in p["risks"])
```

  기존 `test_signal_thresholds_and_limits_match_policy_rules`는 정책 v3와 맞추되 참고 지표를 더한다: `expected |= {"tax_bank_match_ratio": ("watch", 0.7)}`, 방향 비교의 `ops`에도 `"tax_bank_match_ratio": "lt"`를 더한다.
  - `tests/console/test_ui.py`(상세 화면 렌더 테스트, :74-90 부근): 미리보기 상세(`seller-mugshop-21` 또는 기존 상세 신청)에 "정산 대사"가 보이고, 옛 문서(`pg_ledger` 없음)는 "정산 자료 없음"이 보이는 단언 두 개(`test_detail_shows_settlement_card`, `test_detail_old_doc_shows_no_settlement`).

- [ ] **Step 2: 실패 확인** — `tests/console` → FAIL.

- [ ] **Step 3: 구현**
  - `dashboard.py`:

```python
RECONCILIATION_WARN = 0.90
TOP_SHARE_WARN = 0.80


def settlement_card(pg: Mapping[str, Any] | None) -> dict[str, Any]:
    """정산 원장 대사 카드. 옛 문서(pg_ledger 없음)·자료 없음은 available=False."""
    if not isinstance(pg, Mapping) or not pg.get("available"):
        return {"available": False}
    recon, share, trend = pg.get("pg_reconciliation_ratio"), pg.get("pg_top_platform_share"), pg.get("pg_take_rate_trend_pp")
    pct = lambda v: f"{round(v * 100)}%" if charts.is_num(v) else "—"  # noqa: E731
    kpis = [
        {"label": "정산금 계좌 입금", "value": pct(recon), "sub": "기준 90% 이상",
         "tone": "red" if charts.is_num(recon) and recon < RECONCILIATION_WARN else "green"},
        {"label": "최대 채널 비중", "value": pct(share), "sub": "기준 80% 이하",
         "tone": "amber" if charts.is_num(share) and share > TOP_SHARE_WARN else "blue"},
        {"label": "공제율 변화(6개월)", "value": f"{trend:+.1f}%p" if charts.is_num(trend) else "—",
         "sub": "수수료+광고비 상계", "tone": "amber" if charts.is_num(trend) and trend >= 1.0 else "blue"},
    ]
    platforms = charts.hbars([(p["platform"], round(p["share"] * 100, 1), "blue") for p in pg.get("platforms") or ()],
                             width=320, label_w=88)
    take = charts.hbars([(m["month"][2:], round(m["rate"] * 100, 2), "purple") for m in pg.get("monthly_take_rate") or ()],
                        width=320, label_w=56)
    period = pg.get("period") or {}
    return {"available": True, "kpis": kpis, "platforms": platforms, "take_rate": take,
            "unmatched": list(pg.get("unmatched") or ()), "unmatched_count": pg.get("unmatched_count", 0),
            "note": f"{period.get('from', '')} ~ {period.get('to', '')} 정산 {pg.get('settlement_count', 0)}건"}
```

  `detail_summary` 반환에 `"settlement": settlement_card(uw.get("pg_ledger")),`.
  - `detail.html` 월별 현금흐름 `</section>{% endif %}` 바로 뒤에:

```html
<section class="card">
<h3>정산 대사 <small>마켓플레이스 정산 원장과 계좌 입금 비교</small></h3>
{% if ui.settlement.available %}
<div class="grid-4">
{% for k in ui.settlement.kpis %}  {{ ch.kpi(k.label, k.value, k.sub, k.tone) }}
{% endfor %}</div>
<div class="grid-2">
<div>{{ ch.hbars(ui.settlement.platforms, "플랫폼별 정산 비중", "%") }}</div>
<div>{{ ch.hbars(ui.settlement.take_rate, "월별 공제율(수수료+상계)", "%") }}</div>
</div>
{% if ui.settlement.unmatched %}
<details><summary>계좌에 들어오지 않은 정산 {{ ui.settlement.unmatched_count }}건(최근 10건)</summary>
<table class="kv">{% for u in ui.settlement.unmatched %}<tr><th>{{ u.settled_on }} {{ u.platform }}</th><td>{{ u.net_krw|krw }}</td></tr>{% endfor %}</table>
</details>
{% endif %}
<p class="note">{{ ui.settlement.note }} · 지급일부터 3영업일 안의 같은 금액 입금을 짝지었습니다.</p>
{% else %}<p class="note">정산 자료 없음 — 정산 원장이 없거나 2026-11-08 이전 심사분입니다.</p>{% endif %}
</section>
```

  인라인 style·script 없이 기존 클래스만 쓴다(`grid-4`, `grid-2`, `kv`, `note`, `kpi-red` 확인됨).
  - `assessment.py`:

```python
SIGNAL_RULES: dict[str, tuple[str, float]] = {
    "months_of_history": ("knockout", 6),
    "dscr": ("knockout", 1.0),
    "revenue_volatility": ("refer", 0.5),
    "cash_buffer_days": ("refer", 10),
    "business_age_months": ("refer", 12),
    "high_cost_lender_count": ("refer", 0),
    "revenue_trend_ratio": ("refer", 0.8),
    "pg_top_platform_share": ("refer", 0.8),
    "pg_reconciliation_ratio": ("refer", 0.9),
    "tax_bank_match_ratio": ("watch", 0.7),  # 정책 v3에서 R101 삭제 — 참고 지표로만 표시
}
OPTIONAL_SIGNALS = frozenset({"high_cost_lender_count", "revenue_trend_ratio", "pg_top_platform_share",
                              "pg_reconciliation_ratio"})
```

  `SIGNALS`에 추가(기존 다섯 줄 뒤, `tax_bank_match_ratio` 앞):

```python
    ("revenue_trend_ratio", "최근 매출 추세", "min", 1.0,
     "최근 매출 추세 {v} — 최근 60일 하루 매출이 그 전보다 {pct}% 수준입니다(기준 {t} 이상)."),
    ("high_cost_lender_count", "고비용 대출 상환", "max", -1,
     "고비용 대출 상환 {v}곳 — 대부업·고금리 대출 상환이 계좌에서 보입니다(기준 {t}곳)."),
    ("pg_reconciliation_ratio", "정산금 계좌 입금", "min", 0.98,
     "정산금 계좌 입금 {v} — 플랫폼 정산금의 {pct}%가 이 계좌로 들어왔습니다(기준 {t} 이상)."),
    ("pg_top_platform_share", "최대 채널 비중", "max", 0.6,
     "최대 채널 비중 {v} — 정산 매출의 {pct}%가 한 플랫폼에서 나옵니다(기준 {t} 이하)."),
```

  `tax_bank_match_ratio` 설명은 "(참고 지표, {t} 이상 권장)"으로 바꾼다. `SIGNAL_TONES["watch"] = "amber"`, `SIGNAL_STATE_LABELS["watch"] = "주의(참고 지표)"`. `assessment_panels`: `signals = [_signal(*s, features) for s in SIGNALS if s[0] not in OPTIONAL_SIGNALS or charts.is_num(features.get(s[0]))]`. `_strengths_risks`의 위험·`covered` 상태 집합을 `("knockout", "refer", "watch")`로 넓힌다. 지표 신호등 카드 제목 설명(`detail.html`의 "초록 통과 · 주황 회부 기준 위반 · 빨강 거절 기준 위반")에 "· 주황(참고) 참고 지표 주의"를 덧붙인다.
  - `opinion.py` `METRIC_LABELS`에 `pg_reconciliation_ratio`: "정산금 계좌 입금 비율", `pg_top_platform_share`: "최대 채널 비중", `pg_hhi`: "채널 집중도(HHI)", `pg_take_rate_trend_pp`: "공제율 변화(%p)", `revenue_trend_ratio`: "최근 매출 추세", `high_cost_lender_count`: "고비용 대출 상환처 수". `TOOL_LABELS`에 `get_settlement_summary`: "정산 원장 대사 조회". `statuses.FLAG_LABELS`에 `pg_ledger_unavailable`: "정산 원장 손상(정산 지표 제외)".
  - `consolepreview/fixtures.py`: 심사 결과를 만드는 `compute_underwriting` 호출에 `pg_settlements=case.pg_settlements`, 문서 `underwriting`에 `"pg_ledger": uw.pg_ledger`. 모듈 docstring의 정책 "v2" → "v3".

- [ ] **Step 4: 통과 확인** — `tests/console tests/consolepreview` → PASS. 로컬 미리보기(`.\.venv\Scripts\python -m consolepreview`)로 `seller-mugshop-21` 상세의 현금흐름 탭을 열어 카드가 깨지지 않는지 눈으로 확인한다.
- [ ] **Step 5: 커밋** — `feat(console): 정산 대사 카드와 정책 v3 신호등`

---

### Task 12: 평가 — 정산 차이 탐지

**Files:**
- Modify: `src/evals/suites/underwriting.py`(근거 상수, 지표, 실패 사유, `build_context`·`judge_context`)
- Modify: `src/evals/fake.py:100-106`, `src/evals/report.py:20,117`
- Test: `tests/evals/`(해당 스위트·리포트 테스트)

**Interfaces:**
- Consumes: `underwrite` 결과 `pg_ledger`(T9), `get_settlement_summary`(T10).
- Produces: `TAX_EVIDENCE = ("M:tax_bank_match_ratio",)`; `SETTLEMENT_EVIDENCE = ("M:pg_reconciliation_ratio", "P:R108")`; 지표 `settlement_gap_detection_rate`(ASSIGNED 사례 중 의견이 `SETTLEMENT_EVIDENCE` 중 하나를 인용한 비율); 실패 사유 `settlement_gap_missed`; 리포트 열 "정산 차이 탐지".

- [ ] **Step 1: 실패 테스트 작성** — 기존 `tax_mismatch_detection_rate` 테스트를 본떠 `settlement_gap_detection_rate` 테스트를 추가한다: ASSIGNED 사례에서 가짜 러너(`fake.py`)가 `M:pg_reconciliation_ratio`를 인용하면 1.0, 인용을 지운 초안이면 0.0이고 실패 사유 `settlement_gap_missed`가 붙는다. 리포트 렌더 테스트에 "정산 차이 탐지" 열과 옛 결과(지표 키 없음)에서 "—" 표시 단언.

- [ ] **Step 2: 실패 확인** — `tests/evals` → FAIL.

- [ ] **Step 3: 구현**
  - `TAX_EVIDENCE`에서 `"P:R101"`을 뺀다(세금 불일치 탐지는 참고 지표 인용으로만 판단). `SETTLEMENT_EVIDENCE` 추가, ASSIGNED 사례 판정 로직을 TAX_MISMATCH 판정 옆에 같은 형태로 둔다.
  - `build_context`·`judge_context`: `compute_underwriting(..., pg_settlements=case.pg_settlements)`, `UnderwritingContext(..., settlement=uw.pg_ledger)`.
  - `fake.py`: 정책 사유에 R108이 있으면 `M:pg_reconciliation_ratio`를 인용한 주장을 하나 더 만든다(기존 R101 분기를 대체).
  - `report.py`: 열 "정산 차이 탐지" — `metrics.get("settlement_gap_detection_rate")`, 없으면 "—". R101을 언급한 설명 문구를 지운다.

- [ ] **Step 4: 통과 확인** — `tests/evals` → PASS. `ops eval`은 실행하지 않는다(사용자 승인 필요).
- [ ] **Step 5: 커밋** — `feat(evals): 정산 차이 탐지 지표`

---

### Task 13: 문서

**Files:**
- Modify: `docs/adr/ADR-003-deterministic-policy.md`, `docs/agents.md`, `docs/architecture.md`, `docs/demo/demo-script.md:53`
- Modify: `src/orchestrator/journey/validation.py:15`, `src/shared/contracts.py:166`(R101·v2를 언급한 주석·문자열)

- [ ] **Step 1: 갱신**
  - ADR-003: "2026-11-08 개정" 절 — R101 삭제 이유(간이·면세·신고 주기로 오탐), R105~R108과 임계값 근거(T7 분포), 값 없음 규칙 미적용.
  - `docs/agents.md`: 분석 도구 표에 `get_settlement_summary` 행, 정산 원장 대사 절(대상 기간·3영업일 매칭·인용 규칙).
  - `docs/architecture.md`: 데이터 흐름에 `pg.json` → `pg_ledger` → 정책·에이전트·콘솔.
  - `docs/demo/demo-script.md:53`: R101 장면을 mugshop-21 R108(정산 양도 의심) 장면으로 바꾼다.
  - `rg -n "20개|R101|2026.11-v2" docs src`로 남은 언급을 고친다(옛 계획·스펙 문서(`docs/superpowers/`)는 기록이므로 고치지 않는다).
- [ ] **Step 2: 확인** — 경로 없이 전체 테스트 → PASS(문서 링크 테스트 포함).
- [ ] **Step 3: 커밋** — `docs: 정책 v3와 정산 원장 대사`

---

### Task 14: 배포와 실환경 확인

**Files:** 없음(운영)

- [ ] **Step 1: 운영 창 열기** — `ops window open --acr`(SCM 403이면 `func-gaz7yh2zic7zc`에 `operator2` 규칙(우선순위 101)을 추가한 뒤 재시도). IP·구독 ID를 출력하지 않는다.
- [ ] **Step 2: 배포(순서 고정)** — 정책 → 오케스트레이터 → mockapi → 콘솔. 각 배포 뒤 상태 확인. 오케스트레이터가 mockapi보다 먼저여야 한다(`extra="forbid"`, 스펙과 다른 점 1).
- [ ] **Step 3: 스모크** — `ops smoke` → 통과.
- [ ] **Step 4: 실환경 확인** — `files/t8_live.py`(세션 폴더)를 고쳐 `seller-mugshop-21`과 `seller-dailylook-01` 신청을 넣고 완료까지 기다린다. 기대: mugshop-21 → 회부, 사유 R108 포함, 상세 문서 `underwriting.pg_ledger.available == true`, 의견 근거에 `M:pg_reconciliation_ratio`; dailylook-01 → 적격, 대사율 0.9 초과. 콘솔 상세에서 정산 대사 카드를 확인한다.
- [ ] **Step 5: 운영 창 닫기와 푸시** — `ops window close`; `git -c credential.helper= -c "http.extraHeader=Authorization: Basic <토큰 base64>" push origin master:main`(토큰은 `gh auth token --user freeman9844`에서 얻고 출력하지 않는다).
- [ ] **Step 6: 보고** — 판정 변화 표, 실환경 결과, 남은 일(옛 `gpt-5.4-mini` 배포 삭제 여부, 품질 재측정)을 사용자에게 보고한다.

