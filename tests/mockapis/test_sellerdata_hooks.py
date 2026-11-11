"""sellerdata 훅: 난수 순서를 지키는 반품률·주문 수집·보류·추가 상계·선정산 플랫폼·워밍업(Task 2, 2026-11-11).

Step 1~2: 심사 데이터 고정 테스트. GOLDEN은 이 작업(훅 추가) 전 코드로 build_demo_cases(date(2026, 11, 2))를
한 번 돌려 계산했다(HEAD bb4a9a7). 이 작업이 끝난 뒤에도 같은 값이 나와야 한다(생성 로직 불변 증거).
"""

import hashlib
import json
import random
from collections import defaultdict
from datetime import date, timedelta

import pytest

from mockapis.demo import build_demo_cases
from mockapis.sellerdata.channels import COUPANG, ELEVENST, OWNMALL, SMARTSTORE, OrderDay, Payout, payouts
from mockapis.sellerdata.engine import simulate
from mockapis.sellerdata.personas import DEMO_PERSONAS
from mockapis.sellerdata.receipts import ADVANCE_DESCRIPTION, ReceiptHooks, bank_receipts

GOLDEN = {
    "seller-dailylook-01": "45ec98c05ee8c2453d69b934f64f2cb476cc4667b4cf8f82a6a938ae43cad177",
    "seller-gearstore-06": "6aba69c4939b1e8d626385a128d7f5026ba3fcc8f5e330708883cb8b1e564d20",
    "seller-mugshop-21": "7ebb1a638ba888714f19e69428373866f790d183403e98e9e35736787c048d10",
}


def _digest(case) -> str:
    rows = [(t.booked_on.isoformat(), t.amount_krw, t.balance_after_krw, t.description, t.txn_type_code)
            for t in case.transactions]
    rows += [(s.settled_on.isoformat(), s.platform, s.gross_krw, s.fee_krw, s.deduction_krw, s.net_krw)
             for s in case.pg_settlements]
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()


@pytest.mark.parametrize("seller", sorted(GOLDEN))
def test_underwriting_data_unchanged(seller):
    case = build_demo_cases(date(2026, 11, 2))[seller]
    assert all(s.held_krw == s.released_krw == 0 for s in case.pg_settlements)
    assert _digest(case) == GOLDEN[seller]


# --- Step 3: 훅 테스트 ---

def _sales(start: date, days: int, amount: int = 1_000_000) -> dict[date, int]:
    return {start + timedelta(days=i): amount for i in range(days)}


def test_orders_collector_matches_returns():
    """orders 버퍼는 반환값을 바꾸지 않고(gross 합 동일), 일별 반품 집계가 지급 gross 합과 거의 같다."""
    sales = _sales(date(2026, 4, 6), 60)
    channels = (COUPANG, SMARTSTORE, ELEVENST, OWNMALL)
    gross_without_orders = 0
    gross_with_orders = 0
    buf: list[OrderDay] = []
    for channel in channels:
        out_a = payouts(channel, "식품", sales, random.Random(5))
        out_b = payouts(channel, "식품", sales, random.Random(5), orders=buf)
        assert out_a == out_b
        gross_without_orders += sum(p.gross_krw for p in out_a)
        gross_with_orders += sum(p.gross_krw for p in out_b)
    assert gross_without_orders == gross_with_orders
    buf_net = sum(o.gross_krw - o.returns_krw for o in buf)
    # 지급 쪽은 지급일 묶음 단위로 한 번만 int()하고 orders는 날짜별로 int()하므로 절삭 오차가 쌓인다.
    # 오차 상한은 (채널 수 x 테스트 일수) 미만이어야 한다(각 날짜 항이 잃는 값은 1 미만).
    assert abs(buf_net - gross_with_orders) < len(channels) * len(sales)


def test_return_rate_hook_raises_returns():
    """return_rate로 반품률을 고정하면 주 단위 반품률이 그 범위에 들고, 기본 호출과 다음 rng 값이 같다."""
    sales = _sales(date(2026, 4, 6), 14)
    buf: list[OrderDay] = []
    rng_hook = random.Random(11)
    payouts(COUPANG, "식품", sales, rng_hook, return_rate=lambda d, b: 0.22, orders=buf)
    weekly: dict[int, list[OrderDay]] = defaultdict(list)
    for o in buf:
        weekly[o.day.isocalendar()[1]].append(o)
    for days in weekly.values():
        gross = sum(o.gross_krw for o in days)
        returns = sum(o.returns_krw for o in days)
        assert 0.15 * gross <= returns <= 0.30 * gross

    rng_default = random.Random(11)
    payouts(COUPANG, "식품", sales, rng_default)
    assert rng_hook.random() == rng_default.random()


def _payouts(days: int = 180, start: date = date(2026, 5, 1)):
    out = []  # 금액을 날마다 다르게 해 (적요, 실지급액)이 지급 한 건을 가리키게 한다
    for i in range(days):
        d = start + timedelta(days=i)
        out.append(Payout(d, "쿠팡", "쿠팡(주) 주정산", "PG_SETTLE", 1_000_000 + 1000 * i, 110_000))
        out.append(Payout(d, "자사몰", "토스페이먼츠 정산", "CARD_SETTLE", 500_000 + 1000 * i, 17_000))
        if i % 3 == 0:
            out.append(Payout(d, "스마트스토어", "네이버파이낸셜 정산", "PG_SETTLE", 300_000 + 1000 * i, 19_800))
    return out


AS_OF = date(2026, 11, 1)


def test_hold_and_release():
    """보류 구간의 쿠팡 지급은 net 0·held>0이 되어 계좌 사건이 없고, 해제일에 누적 보류가 한 번에 들어온다."""
    payout_list = _payouts()
    hold_start, hold_end = date(2026, 6, 1), date(2026, 6, 15)
    release_on = date(2026, 7, 1)
    hooks = ReceiptHooks(hold=lambda p: p.channel == "쿠팡" and hold_start <= p.paid_on < hold_end,
                         release_on=release_on)
    r = bank_receipts(payout_list, AS_OF, random.Random(4), hooks=hooks)

    held_rows = [p for p in r.payouts if p.channel == "쿠팡" and hold_start <= p.paid_on < hold_end]
    assert held_rows and all(p.net_krw == 0 and p.held_krw > 0 for p in held_rows)

    release_rows = [p for p in r.payouts if "보류 해제" in p.description]
    assert len(release_rows) == 1
    assert release_rows[0].paid_on == release_on
    assert release_rows[0].released_krw == sum(p.held_krw for p in held_rows)
    assert release_rows[0].net_krw == release_rows[0].released_krw

    # 보류된 지급 수만큼 계좌 사건이 줄고, 해제 입금 1건이 더해진다.
    assert len(r.events) == len(payout_list) - len(held_rows) + 1
    release_events = [e for e in r.events if e.booked_on == release_on and "보류 해제" in e.description]
    assert len(release_events) == 1
    assert release_events[0].amount_krw == sum(p.held_krw for p in held_rows)


def test_extra_offset_and_advance_platform():
    """extra_offset은 상계율에 그대로 더해지고, advance_platform은 _top_platform 대신 양도 대상을 고른다."""
    payout_list = _payouts()
    base = bank_receipts(payout_list, AS_OF, random.Random(6))
    hooked = bank_receipts(payout_list, AS_OF, random.Random(6), hooks=ReceiptHooks(extra_offset=lambda d: 0.08))
    base_by_key = {(p.paid_on, p.channel, p.description): p for p in base.payouts}
    checked = 0
    for p in hooked.payouts:
        if p.channel != "쿠팡":
            continue
        b = base_by_key[(p.paid_on, p.channel, p.description)]
        assert abs((p.deduction_krw / p.gross_krw) - (b.deduction_krw / b.gross_krw) - 0.08) <= 0.001
        checked += 1
    assert checked > 0

    since = AS_OF - timedelta(days=90)
    r = bank_receipts(payout_list, AS_OF, random.Random(7), assigned_from=since,
                      hooks=ReceiptHooks(advance_platform="스마트스토어"))
    assigned_payouts = [p for p in r.payouts if p.channel == "스마트스토어" and p.paid_on >= since]
    not_assigned = [p for p in r.payouts if p.channel == "스마트스토어" and p.paid_on < since]
    advances = [e for e in r.events if e.description == ADVANCE_DESCRIPTION]
    assert assigned_payouts and len(advances) == len(assigned_payouts)
    normal_smartstore_events = [e for e in r.events if e.description == "네이버파이낸셜 정산"]
    assert len(normal_smartstore_events) == len(not_assigned)  # 양도 대상 지급은 정산 입금을 만들지 않는다


def test_simulate_warmup_and_ledger_from():
    """warmup_days·ledger_from이 사후 전용 긴 워밍업을 만들고, pg_settlements·orders가 그 범위를 반영한다."""
    persona = DEMO_PERSONAS["seller-dailylook-01"]
    start = date(2026, 1, 1)
    end = date(2026, 4, 1)
    ledger_from = start - timedelta(days=56)
    ledger = simulate(persona, start, end, random.Random(5), warmup_days=131, ledger_from=ledger_from)
    assert ledger.pg_settlements
    assert min(s.settled_on for s in ledger.pg_settlements) >= ledger_from
    assert min(s.settled_on for s in ledger.pg_settlements) < start  # 기본(start) 범위를 벗어난 것도 있다
    assert ledger.orders
    assert min(o.day for o in ledger.orders) == start - timedelta(days=131)
