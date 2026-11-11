from datetime import date, timedelta

import pytest

from agents.classify import Category, classify
from mockapis.demo import build_demo_cases, build_load_cases
from mockapis.postloan import (
    DEFAULT_WINDOW,
    DISBURSE_SHARE,
    INSTALLMENT_KRW,
    LENDER,
    POSTLOAN_DAYS,
    PRE_LOAN_DAYS,
    FlowScenario,
    LoanTerms,
    generate_postloan,
)
from mockapis.postloan_scenarios import SLUMP_LOAN_FROM_DAY, SLUMP_PAYROLL_DELAY_FROM_DAY, hold_platform
from mockapis.sellerdata.personas import DEMO_PERSONAS
from mockapis.sellerdata.receipts import ADVANCE_DESCRIPTION, OFFSET_PLATFORMS
from mockapis.synth import Archetype, TxnCategory, generate_case
from shared.contracts import AlertCause
from shared.repayment import LENDER_CREDIT, LENDER_DEBIT, due_dates

AS_OF = date(2026, 11, 2)
AS_OFS = [date(2026, 11, 2), date(2026, 12, 28), date(2027, 1, 25)]
TERMS = LoanTerms(amount_krw=30_000_000, installment_krw=2_610_000)
WEEKS = 13


@pytest.fixture(scope="module")
def cafe():
    return build_demo_cases(AS_OF)["seller-dailylook-01"]


def _week(flow, day: date) -> int | None:
    """사후 주차 인덱스(0 = 1주차). 대출 전 기준선·기간 밖이면 None."""
    offset = (day - flow.start).days
    return offset // 7 if 0 <= offset < WEEKS * 7 else None


def _weekly(flow, key: str, platform: str | None = None) -> list[int]:
    """주차별 합. key: order_gross·order_returns(주문)·held·released·deduction·settled_gross(원장)."""
    weeks = [0] * WEEKS
    for o in flow.orders:
        week = _week(flow, o.order_on)
        if week is None or key not in ("order_gross", "order_returns") or (platform and o.platform != platform):
            continue
        weeks[week] += o.gross_krw if key == "order_gross" else o.returns_krw
    for s in flow.pg_settlements:
        week = _week(flow, s.settled_on)
        if week is None or key in ("order_gross", "order_returns") or (platform and s.platform != platform):
            continue
        if key in ("deduction", "settled_gross") and s.platform not in OFFSET_PLATFORMS:
            continue
        weeks[week] += {"held": s.held_krw, "released": s.released_krw,
                        "deduction": s.deduction_krw, "settled_gross": s.gross_krw}[key]
    return weeks


def _rates(flow, part: str, whole: str) -> list[float]:
    return [p / w if w else 0.0 for p, w in zip(_weekly(flow, part), _weekly(flow, whole))]


def _new_debt(flow):
    """외부 차입 입금(자체 대출금·선정산 선지급 제외)."""
    return [
        t for t in flow.transactions
        if flow.labels[t.txn_id] is TxnCategory.LOAN_PROCEEDS
        and t.description != LENDER_CREDIT and t.description != ADVANCE_DESCRIPTION
    ]


def test_same_seed_same_flow(cafe):
    a = generate_postloan(cafe, FlowScenario.SALES_SLUMP, AS_OF, 7)
    b = generate_postloan(cafe, FlowScenario.SALES_SLUMP, AS_OF, 7)
    assert a == b


def test_flow_continues_the_underwriting_account(cafe):
    flow = generate_postloan(cafe, FlowScenario.STABLE, AS_OF, 7)
    first = flow.transactions[0]
    assert first.booked_on == AS_OF
    assert first.balance_after_krw == cafe.transactions[-1].balance_after_krw + first.amount_krw
    assert flow.end == AS_OF + timedelta(days=90)
    ids = {t.txn_id for t in flow.transactions}
    assert len(ids) == len(flow.transactions)
    assert not ids & {t.txn_id for t in cafe.transactions}


def test_stable_never_defaults_and_has_no_new_debt(cafe):
    flow = generate_postloan(cafe, FlowScenario.STABLE, AS_OF, 7)
    assert flow.default_on is None and flow.stacking_onset is None
    assert flow.cause is None and flow.secondary_causes == () and flow.onset is None
    assert not {TxnCategory.HIGH_COST_LENDER, TxnCategory.LOAN_PROCEEDS} & set(flow.labels.values())


@pytest.mark.parametrize("as_of", AS_OFS)
def test_sales_slump_orders_fall_before_borrowing(as_of):
    case = build_demo_cases(as_of)["seller-dailylook-01"]
    slump = generate_postloan(case, FlowScenario.SALES_SLUMP, as_of, 7, loan=TERMS)
    base = generate_postloan(case, FlowScenario.STABLE, as_of, 7, loan=TERMS)
    ratio = sum(_weekly(slump, "order_gross")[2:5]) / sum(_weekly(base, "order_gross")[2:5])  # 3~5주차
    assert ratio <= 0.70
    assert (slump.stacking_onset - slump.start).days >= SLUMP_LOAN_FROM_DAY > (slump.onset - slump.start).days
    assert slump.cause is AlertCause.SALES_SLUMP
    assert slump.secondary_causes == (AlertCause.COST_SURGE, AlertCause.HIGH_COST_BORROWING)
    assert slump.missed_installments[-1] == due_dates(slump.start, POSTLOAN_DAYS)[-1]


@pytest.mark.parametrize("as_of", AS_OFS)
def test_settlement_hold_ledger_shape(as_of):
    case = build_demo_cases(as_of)["seller-gearstore-06"]
    flow = generate_postloan(case, FlowScenario.SETTLEMENT_HOLD, as_of, 7, loan=TERMS)
    base = generate_postloan(case, FlowScenario.STABLE, as_of, 7, loan=TERMS)
    platform = hold_platform(case.persona)
    held = _weekly(flow, "held")
    # 3~6주차 보류. 공휴일이 긴 주에는 그 플랫폼 지급 자체가 없어(정산 공백) 한 주가 비기도 한다.
    assert {w for w, krw in enumerate(held) if krw} <= {2, 3, 4, 5}
    assert sum(1 for w in range(2, 6) if held[w]) >= 3
    on_hold = [s for s in flow.pg_settlements if s.held_krw > 0]
    assert on_hold and all(s.platform == platform and s.net_krw == 0 for s in on_hold)
    assert _weekly(flow, "released")[6] == sum(held)  # 7주차 해제
    assert sum(_weekly(flow, "released")) == sum(held)

    orders = sum(_weekly(flow, "order_gross", platform))
    assert orders >= 0.9 * sum(_weekly(base, "order_gross", platform))  # 원인은 정산이지 주문이 아니다
    advances = [t for t in flow.transactions if t.description == ADVANCE_DESCRIPTION]
    assert advances and _week(flow, min(t.booked_on for t in advances)) == 3  # 4주차
    (bridge,) = _new_debt(flow)
    assert _week(flow, bridge.booked_on) == 4  # 5주차
    assert flow.cause is AlertCause.SETTLEMENT_HOLD and flow.onset == as_of + timedelta(days=14)
    assert flow.missed_installments == (due_dates(as_of, POSTLOAN_DAYS)[0],)
    assert flow.default_on is None


@pytest.mark.parametrize("as_of", AS_OFS)
def test_return_spike_rates(as_of):
    case = build_demo_cases(as_of)["seller-linen-10"]
    flow = generate_postloan(case, FlowScenario.RETURN_SPIKE, as_of, 7, loan=TERMS)
    rates = _rates(flow, "order_returns", "order_gross")
    assert all(0.18 <= rates[w] <= 0.27 for w in range(2, 8))  # 3~8주차
    assert all(rates[w] <= 0.10 for w in [0, 1, *range(9, WEEKS)])
    assert not _new_debt(flow)
    assert TxnCategory.HIGH_COST_LENDER not in set(flow.labels.values())
    assert flow.cause is AlertCause.RETURN_SPIKE and flow.secondary_causes == ()
    assert flow.default_on is None
    pickups = [t for t in flow.transactions if t.description == f"{case.persona.courier} 반품회수"]
    assert pickups and all(t.booked_on.weekday() == 0 for t in pickups)


@pytest.mark.parametrize("as_of", AS_OFS)
def test_cost_surge_deductions(as_of):
    case = build_demo_cases(as_of)["seller-tangerine-15"]
    flow = generate_postloan(case, FlowScenario.COST_SURGE, as_of, 7, loan=TERMS)
    rates = _rates(flow, "deduction", "settled_gross")
    before = sum(rates[:2]) / 2
    assert all(rates[w] >= before + 0.06 for w in range(2, WEEKS))
    coupang = [s for s in flow.pg_settlements
               if s.platform == "쿠팡" and s.gross_krw and (s.settled_on - flow.start).days >= 14]
    assert coupang and all(0.09 <= s.deduction_krw / s.gross_krw <= 0.13 for s in coupang)
    (loan,) = _new_debt(flow)
    assert _week(flow, loan.booked_on) == 6  # 7주차
    assert flow.cause is AlertCause.COST_SURGE and flow.default_on is None


def test_installment_and_disbursement_follow_terms(cafe):
    flow = generate_postloan(cafe, FlowScenario.STABLE, AS_OF, 7, loan=TERMS)
    paid = [t for t in flow.transactions if t.description == LENDER_DEBIT]
    assert paid and all(t.amount_krw == -TERMS.installment_krw for t in paid)
    (credit,) = [t for t in flow.transactions if t.description == LENDER_CREDIT]
    assert credit.booked_on == AS_OF and credit.amount_krw == TERMS.amount_krw
    assert credit.description.startswith(LENDER)
    spend = [t for t in flow.transactions
             if t.description in (f"{cafe.persona.suppliers[0]} 재고 매입", "운영비 이체")]
    assert {(t.booked_on - AS_OF).days for t in spend} == {0, 1, 2, 3, 4}
    assert sum(t.amount_krw for t in spend) == pytest.approx(-DISBURSE_SHARE * TERMS.amount_krw, abs=10_000)

    plain = generate_postloan(cafe, FlowScenario.STABLE, AS_OF, 7)
    assert all(t.amount_krw == -INSTALLMENT_KRW for t in plain.transactions if t.description == LENDER_DEBIT)
    assert not [t for t in plain.transactions if t.description == LENDER_CREDIT]


@pytest.mark.parametrize("scenario", list(FlowScenario))
def test_pg_flow_covers_pre_loan_and_post_loan(cafe, scenario):
    flow = generate_postloan(cafe, scenario, AS_OF, 7, loan=TERMS)
    pg = flow.pg_flow()
    assert min(o.order_on for o in pg.orders) == AS_OF - timedelta(days=PRE_LOAN_DAYS)
    assert max(o.order_on for o in pg.orders) == flow.end
    assert all(AS_OF - timedelta(days=PRE_LOAN_DAYS) <= s.settled_on <= flow.end for s in pg.settlements)
    assert all(
        s.net_krw == s.gross_krw - s.fee_krw - s.deduction_krw - s.held_krw + s.released_krw for s in pg.settlements
    )
    if scenario is not FlowScenario.SETTLEMENT_HOLD:
        assert not [s for s in pg.settlements if s.held_krw or s.released_krw]


def test_stacking_value_reads_as_sales_slump():
    assert FlowScenario("stacking") is FlowScenario.SALES_SLUMP


@pytest.mark.parametrize("as_of", [date(2026, 11, 2), date(2026, 5, 4), date(2027, 2, 1), date(2026, 8, 3)])
@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("seller", [None, *DEMO_PERSONAS])
def test_sales_slump_defaults_in_window(as_of, seed, seller):
    # 11월 시작은 행사·연말 매출이 잔고를 받쳐 준다. 규모·계절과 무관하게 부도 시점이 같은 범위에 와야 한다.
    persona = DEMO_PERSONAS[seller] if seller else None
    case = generate_case(f"x{seed}", Archetype.HEALTHY_SEASONAL, as_of, seed, persona=persona)
    flow = generate_postloan(case, FlowScenario.SALES_SLUMP, as_of, seed)
    assert flow.default_on is not None
    assert DEFAULT_WINDOW[0] <= (flow.default_on - as_of).days <= DEFAULT_WINDOW[1]
    assert (flow.stacking_onset - as_of).days >= SLUMP_LOAN_FROM_DAY


@pytest.mark.parametrize("as_of", [date(2026, 11, 2), date(2026, 5, 4), date(2027, 2, 1), date(2026, 8, 3)])
@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("seller", [None, *DEMO_PERSONAS])
@pytest.mark.parametrize("scenario", list(FlowScenario))
def test_installments_follow_shared_due_dates(as_of, seed, seller, scenario):
    persona = DEMO_PERSONAS[seller] if seller else None
    case = generate_case(f"x{seed}", Archetype.HEALTHY_SEASONAL, as_of, seed, persona=persona)
    flow = generate_postloan(case, scenario, as_of, seed)
    dues = due_dates(as_of, POSTLOAN_DAYS)
    paid = [t for t in flow.transactions if t.description == LENDER_DEBIT]
    assert all(t.amount_krw == -INSTALLMENT_KRW and t.balance_after_krw >= 0 for t in paid)
    assert sorted([t.booked_on for t in paid] + list(flow.missed_installments)) == dues
    forced = {
        FlowScenario.SALES_SLUMP: (dues[-1],),
        FlowScenario.SETTLEMENT_HOLD: (dues[0],),
    }.get(scenario, ())
    assert flow.missed_installments == forced


def test_sales_slump_hides_some_lenders_from_the_rules(cafe):
    flow = generate_postloan(cafe, FlowScenario.SALES_SLUMP, AS_OF, 7)
    hidden = {
        t.description
        for t in flow.transactions
        if flow.labels[t.txn_id] in (TxnCategory.HIGH_COST_LENDER, TxnCategory.LOAN_PROCEEDS)
        and classify(t) in (Category.UNCLASSIFIED_IN, Category.UNCLASSIFIED_OUT)
    }
    assert {"머니무브캐피탈", "머니무브 빠른자금"} <= hidden


def test_sales_slump_delays_payroll(cafe):
    flow = generate_postloan(cafe, FlowScenario.SALES_SLUMP, AS_OF, 7)
    late_from = AS_OF + timedelta(days=SLUMP_PAYROLL_DELAY_FROM_DAY)
    payroll = [t for t in flow.transactions if flow.labels[t.txn_id] is TxnCategory.PAYROLL]
    assert {t.booked_on.day for t in payroll if t.booked_on >= late_from} <= {18, 19, 20}
    assert any(t.booked_on >= late_from for t in payroll)


def test_sales_slump_story_on_the_seller_engine(cafe):
    flow = generate_postloan(cafe, FlowScenario.SALES_SLUMP, AS_OF, 7)
    by_desc = lambda d: [t for t in flow.transactions if t.description == d]  # noqa: E731
    (capex,) = by_desc("시즌 재고 대량 선매입")
    assert capex.booked_on == AS_OF + timedelta(days=2) and capex.amount_krw < -cafe.persona.monthly_sales_krw / 4
    dues = by_desc("시즌 재고 외상 잔금")
    # 매주 같은 금액이고, 계절 매출이 받쳐 주면 목표 범위 안 한 주만 잔금을 몰아 낸다.
    assert len(dues) >= 8 and len({t.amount_krw for t in dues}) <= 2
    # 3주차부터 매주 수요일(공휴일이면 다음 영업일)
    assert dues[0].booked_on >= AS_OF + timedelta(days=14) and all(t.booked_on.weekday() in (2, 3, 4) for t in dues)
    unpaid_from = AS_OF + timedelta(days=SLUMP_PAYROLL_DELAY_FROM_DAY)
    insurance = [t for t in flow.transactions if flow.labels[t.txn_id] is TxnCategory.SOCIAL_INSURANCE]
    assert insurance and all(t.booked_on < unpaid_from for t in insurance)


def test_load_sellers_keep_their_own_persona_after_the_loan():
    cases = build_load_cases(AS_OF)
    small = min(cases.values(), key=lambda c: c.persona.monthly_sales_krw)
    big = max(cases.values(), key=lambda c: c.persona.monthly_sales_krw)

    def weekly(case):
        flow = generate_postloan(case, FlowScenario.STABLE, AS_OF, 1)
        return sum(t.amount_krw for t in flow.transactions if t.amount_krw > 0) / 13
    sales_ratio = big.persona.monthly_sales_krw / small.persona.monthly_sales_krw
    assert sales_ratio > 1.3
    assert weekly(big) / weekly(small) == pytest.approx(sales_ratio, rel=0.25)
