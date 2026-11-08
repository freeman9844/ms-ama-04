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
