import hashlib
import json
import random
from datetime import date, timedelta

import pytest

from evals.datasets import underwriting_cases
from mockapis.demo import DEMO_STORE_OVERRIDES, build_demo_cases, build_load_cases
from mockapis.sellerdata.channels import CHANNELS, OrderDay, Payout
from mockapis.sellerdata.engine import Ledger, simulate
from mockapis.sellerdata.marketplace import StoreOverride, marketplace_hooks, synthesize_store
from mockapis.sellerdata.personas import DEMO_PERSONAS, Persona
from mockapis.synth import Archetype, generate_case

AS_OF = date(2026, 11, 17)

# 마켓 셀러 합성(2026-11-17)을 넣기 전 현재 코드에서 잰 계좌·정산 데이터 지문. 합성은 별도 난수 스트림이라
# 기존 데이터를 바꾸지 않는다. 덮어쓰기(스펙 8.1)가 걸린 두 셀러만 예외다.
FINGERPRINTS = {
    "seller-dailylook-01": "c916abdf97a3fa45",
    "seller-mangwon-02": "5388f97e62721c8b",
    "seller-glowlab-03": "be2544e3f7491179",
    "seller-knitroom-04": "5d966fa758199943",
    "seller-candle-05": "49033d1e9ff68e9e",
    "seller-gearstore-06": "07555525924a3701",
    "seller-petfood-07": "b4c9cc0f667ef53b",  # 2026-11-17 판매정지 40일 전부터 7일간 주력 플랫폼 주문 0(스펙 8.1)
    "seller-ceramic-08": "ff34510fc5cd7e7c",  # 2026-11-17 최근 90일 반품률 18%로 상향(스펙 8.1)
    "seller-kimchi-09": "4b526edcf70e9a0c",
    "seller-linen-10": "083aeb24d9064e84",
    "seller-skinlab-11": "f0b3c8aa593893e5",
    "seller-kitchen-12": "aca9b4b7d633312c",
    "seller-cable-13": "9c9b2bed4fa5f738",
    "seller-leather-14": "a88d3560541c9485",
    "seller-tangerine-15": "706a58b528727acd",
    "seller-petcare-16": "8423f9934a80cbbe",
    "seller-tint-17": "48a7d1cc40253534",
    "seller-sofa-18": "5a9a2a0e87bfe915",
    "seller-streetfit-19": "83f2b69a73389771",
    "seller-bakery-20": "e9f7eadd91fee445",
    "seller-mugshop-21": "793d9de71a4b54a4",
    "ev-uhealn": "313b5abdd62f02c7",
    "ev-uheali": "9b22c2071bcc697c",
    "ev-utax_n": "619b7dc863c20133",
    "ev-utax_i": "8280fb8fa0bc49c6",
    "ev-ustren": "364f5c0ea55c2ecd",
    "ev-ustrei": "f7f2257d5668cb15",
    "ev-uthinn": "f97e89f2e4e22a11",
    "ev-uthini": "95fb231df0e78fae",
    "ev-uassin": "78cfa7eb294e4cdb",
    "ev-uassii": "b5fd8ef1400f2142",
    "load-healthy-01": "c08b61af34321f58",
    "load-mismatch-01": "dde5b17ba9feb5ae",
    "load-stress-01": "ea49cd7fce7f4158",
    "load-thin-01": "25fa426e5ce2e9e0",
}


def _fingerprint(case) -> str:
    body = {
        "transactions": [t.model_dump(mode="json") for t in case.transactions],
        "pg_settlements": [p.model_dump(mode="json") for p in case.pg_settlements],
    }
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


@pytest.fixture(scope="module")
def demo():
    return build_demo_cases(AS_OF)


@pytest.fixture(scope="module")
def all_cases(demo):
    cases = dict(demo)
    cases.update({u.case_id: u.case for u in underwriting_cases()})
    cases.update(dict(list(build_load_cases(AS_OF).items())[:4]))
    return cases


@pytest.mark.parametrize("case_id", sorted(FINGERPRINTS))
def test_account_data_unchanged_except_overrides(case_id, all_cases):
    assert _fingerprint(all_cases[case_id]) == FINGERPRINTS[case_id]


def _store(case_id, demo):
    return demo[case_id].marketplace


def test_store_is_deterministic():
    persona = DEMO_PERSONAS["seller-glowlab-03"]
    a = generate_case("x", Archetype.STRESSED, AS_OF, 103, persona=persona)
    b = generate_case("x", Archetype.STRESSED, AS_OF, 103, persona=persona)
    assert a.marketplace is not None
    assert a.marketplace == b.marketplace


def test_thin_file_tenure_24_months(demo):
    store = _store("seller-knitroom-04", demo)
    assert min(p.opened_on for p in store.platforms) == date(2024, 11, 17)


def test_healthy_defaults(demo):
    store = _store("seller-dailylook-01", demo)
    assert store.platforms and all(4.5 <= p.rating <= 4.9 for p in store.platforms)
    assert all(300 <= p.review_count <= 3000 for p in store.platforms)
    assert store.penalties == ()


def test_stressed_has_one_warning(demo):
    store = _store("seller-glowlab-03", demo)
    assert [p.kind for p in store.penalties] == ["warning"]
    assert 20 <= (AS_OF - store.penalties[0].occurred_on).days <= 80


def test_only_pg_channels_become_platforms(demo):
    for case_id, case in demo.items():
        pg = {c for c, _ in case.persona.channels if CHANNELS[c].code == "PG_SETTLE"}
        assert {p.platform for p in case.marketplace.platforms} == pg, case_id


def test_90d_figures_and_pending_come_from_ledger():
    persona = Persona("상호", "대표", ("a", "b"), ("c", "d"), "택배", "패션의류",
                      "서울", (("스마트스토어", 0.7), ("쿠팡", 0.3)), 10_000_000)
    orders = (
        OrderDay(AS_OF - timedelta(days=91), "스마트스토어", 999, 99),  # 90일 밖
        OrderDay(AS_OF - timedelta(days=90), "스마트스토어", 1000, 100),
        OrderDay(AS_OF - timedelta(days=5), "스마트스토어", 2000, 300),
        OrderDay(AS_OF - timedelta(days=4), "스마트스토어", 0, 0),  # 주문 없는 날은 세지 않는다
        OrderDay(AS_OF - timedelta(days=3), "쿠팡", 5000, 0),
    )
    pending = (Payout(AS_OF, "스마트스토어", "정산", "PG_SETTLE", 1000, 66),
               Payout(AS_OF + timedelta(days=3), "스마트스토어", "정산", "PG_SETTLE", 2000, 132))
    ledger = Ledger((), (), {}, orders, pending)
    store = synthesize_store(persona, Archetype.HEALTHY_SEASONAL, AS_OF, 30, ledger, None, random.Random(1))
    by = {p.platform: p for p in store.platforms}
    smart, coupang = by["스마트스토어"], by["쿠팡"]
    assert (smart.orders_90d, smart.gross_90d_krw, smart.return_90d_krw) == (2, 3000, 400)
    assert (coupang.orders_90d, coupang.gross_90d_krw, coupang.return_90d_krw) == (1, 5000, 0)
    assert (smart.pending_settlement_krw, coupang.pending_settlement_krw) == (2802, 0)
    assert smart.assigned_pending_krw == coupang.assigned_pending_krw == 0
    assert all(p.cancel_90d_krw + p.return_90d_krw <= p.gross_90d_krw for p in store.platforms)


def test_assigned_pending(demo):
    store = _store("seller-mugshop-21", demo)
    assigned = [p for p in store.platforms if p.assigned_pending_krw > 0]
    assert len(assigned) == 1
    assert 0 < assigned[0].assigned_pending_krw <= assigned[0].pending_settlement_krw


def test_streetfit_rating_override(demo):
    assert {p.rating for p in _store("seller-streetfit-19", demo).platforms} == {3.8}


def test_petfood_suspension(demo):
    case = demo["seller-petfood-07"]
    store = case.marketplace
    primary = max(((c, w) for c, w in case.persona.channels if CHANNELS[c].code == "PG_SETTLE"),
                  key=lambda cw: cw[1])[0]
    assert [(p.platform, p.kind, p.occurred_on) for p in store.penalties] == [
        (primary, "suspension", AS_OF - timedelta(days=40))
    ]
    hooks = marketplace_hooks(DEMO_STORE_OVERRIDES["seller-petfood-07"], case.persona, AS_OF)
    first = date(2026, 5, 1)
    ledger = simulate(case.persona, first, AS_OF, random.Random(7), **hooks)
    suspended = {AS_OF - timedelta(days=40 - i) for i in range(7)}
    primary_orders = {o.day: o.gross_krw for o in ledger.orders if o.channel == primary and o.day >= first}
    assert all(primary_orders[d] == 0 for d in suspended)
    assert all(v > 0 for d, v in primary_orders.items() if d not in suspended)


def test_ceramic_return_rate(demo):
    platforms = _store("seller-ceramic-08", demo).platforms
    ratio = sum(p.return_90d_krw for p in platforms) / sum(p.gross_90d_krw for p in platforms)
    assert 0.15 < ratio < 0.22


def test_override_hooks_are_empty_without_account_overrides(demo):
    persona = demo["seller-streetfit-19"].persona
    assert marketplace_hooks(None, persona, AS_OF) == {}
    assert marketplace_hooks(StoreOverride(rating=3.8), persona, AS_OF) == {}
