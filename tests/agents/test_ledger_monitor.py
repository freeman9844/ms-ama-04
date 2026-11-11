"""원장 모니터: 대출 전 8주 기준선, 주간 원장 필드, 새 트리거 4개, 원인 후보(스펙 §6)."""

import re
from datetime import date
from functools import lru_cache

import pytest

from agents.features import compute_underwriting
from agents.ledger_monitor import (
    CAUSE_FIELDS,
    CAUSE_OF_TRIGGER,
    LedgerBaseline,
    ledger_baseline,
    ledger_catalog,
    observe,
    platform_weeks,
)
from agents.monitoring import WEEKS, Baseline, categorize, evidence_catalog, settlement_for
from mockapis.demo import POSTLOAN_SCENARIOS, build_demo_cases
from mockapis.postloan import FlowScenario, LoanTerms, generate_postloan
from shared.contracts import EVIDENCE_ID_PATTERN, AlertCause

SCENARIO_SELLERS = {
    "seller-dailylook-01": FlowScenario.SALES_SLUMP,
    "seller-gearstore-06": FlowScenario.SETTLEMENT_HOLD,
    "seller-linen-10": FlowScenario.RETURN_SPIKE,
    "seller-tangerine-15": FlowScenario.COST_SURGE,
}
NEW = {"sales_slump", "settlement_hold", "return_spike", "cost_surge"}
EARLY = {"buffer_drop", "projected_shortfall", "new_debt", "inflow_drop", "volatility_spike"}
TERMS = LoanTerms(amount_krw=5_000_000, installment_krw=852_885)
SEED = 7


@lru_cache(maxsize=8)
def _cases(as_of: date):
    return build_demo_cases(as_of)


@lru_cache(maxsize=256)
def _prepared(seller: str, scenario: FlowScenario, as_of: date):
    case = _cases(as_of)[seller]
    uw = compute_underwriting(
        "app-1", case.profile, case.transactions, case.tax_filings,
        as_of=as_of, requested_amount_krw=TERMS.amount_krw, requested_tenor_months=6,
    )
    base = Baseline.from_underwriting(
        {
            "features": uw.features.model_dump(mode="json"),
            "monthly": uw.monthly,
            "settlement_profile": settlement_for(case.transactions, {}, as_of, case.profile.industry),
        },
        installment_krw=TERMS.installment_krw,
    )
    flow = generate_postloan(case, scenario, as_of, SEED, loan=TERMS)
    pg = flow.pg_flow()
    lb = ledger_baseline(pg, flow.start, case.profile.industry, case.transactions)
    return case, flow, base, pg, lb


@lru_cache(maxsize=256)
def _observe(seller: str, scenario: FlowScenario, as_of: date, week: int = WEEKS):
    case, flow, base, pg, lb = _prepared(seller, scenario, as_of)
    cats = categorize(flow.transactions, {})
    return flow, observe(flow.transactions, cats, flow.start, week, base, pg, lb)


def _triggers_at(obs, week: int) -> list[str]:
    return obs.history[week]


@pytest.mark.parametrize("as_of", [date(2026, 11, 2), date(2026, 12, 28), date(2027, 1, 25)])
@pytest.mark.parametrize("seller", sorted(SCENARIO_SELLERS))
def test_true_cause_fires_first(seller, as_of):
    flow, obs = _observe(seller, SCENARIO_SELLERS[seller], as_of)
    first = {c.cause: c.first_week for c in obs.candidates}
    truth = first[flow.cause]
    onset_week = (flow.onset - flow.start).days // 7 + 1
    assert truth <= onset_week + 3, (seller, truth, onset_week)
    assert obs.candidates[0].cause is flow.cause, (seller, [c.cause for c in obs.candidates])
    early = [w for w in range(1, WEEKS + 1) if EARLY & set(_triggers_at(obs, w))]
    assert not early or truth <= min(early), (seller, truth, early)


@pytest.mark.parametrize("as_of", [date(2026, 9, 14), date(2026, 11, 2), date(2026, 12, 28), date(2027, 1, 25)])
def test_stable_sellers_never_fire_ledger_triggers(as_of):
    for seller in sorted(set(_cases(as_of)) - set(SCENARIO_SELLERS)):
        _, obs = _observe(seller, FlowScenario.STABLE, as_of)
        hits = {(w, t) for w in range(1, WEEKS + 1) for t in _triggers_at(obs, w) if t in NEW}
        assert not hits, (seller, sorted(hits))


def test_own_disbursement_and_prior_advance_are_not_new_debt():
    as_of = date(2026, 11, 2)
    _, _, _, _, lb = _prepared("seller-mugshop-21", FlowScenario.STABLE, as_of)
    _, obs = _observe("seller-mugshop-21", FlowScenario.STABLE, as_of)
    assert lb.prior_advance is True  # 심사 때부터 쓰던 선정산 양도는 기준선에 있는 자금 조달이다
    assert obs.rows[0]["loan_proceeds"] == 0 and obs.rows[0]["advance_in"] > 0
    assert not any("new_debt" in _triggers_at(obs, w) for w in range(1, WEEKS + 1))

    _, hold = _observe("seller-gearstore-06", FlowScenario.SETTLEMENT_HOLD, as_of)
    assert hold.rows[3]["advance_in"] > 0  # 보류 뒤 새로 튼 선정산(4주차)은 차입이 아니다
    assert [r["week"] for r in hold.rows if r["loan_proceeds"] > 0] == [5]  # 브릿지 대부만 차입


def test_borrowing_marked_as_consequence():
    _, obs = _observe("seller-gearstore-06", FlowScenario.SETTLEMENT_HOLD, date(2026, 11, 2))
    by_cause = {c.cause: c for c in obs.candidates}
    assert by_cause[AlertCause.HIGH_COST_BORROWING].consequence is True
    assert by_cause[AlertCause.SETTLEMENT_HOLD].consequence is False
    assert by_cause[AlertCause.SETTLEMENT_HOLD].first_week <= by_cause[AlertCause.HIGH_COST_BORROWING].first_week


def test_no_ledger_means_no_ledger_fields_or_triggers():
    as_of = date(2026, 11, 2)
    _, flow, base, _, _ = _prepared("seller-dailylook-01", FlowScenario.SALES_SLUMP, as_of)
    cats = categorize(flow.transactions, {})
    obs = observe(flow.transactions, cats, flow.start, WEEKS, base, None, None)
    assert all("order_gross" not in r for r in obs.rows)
    assert not NEW & set(obs.triggers)
    assert not NEW & {t for w in range(1, WEEKS + 1) for t in obs.history[w]}
    assert all(c.cause is AlertCause.HIGH_COST_BORROWING for c in obs.candidates)
    assert platform_weeks(None, flow.start, 1, WEEKS) is None
    assert ledger_catalog(None) == {}


def test_ledger_ids_are_evidence_ids():
    as_of = date(2026, 11, 2)
    case, flow, base, pg, lb = _prepared("seller-gearstore-06", FlowScenario.SETTLEMENT_HOLD, as_of)
    _, obs = _observe("seller-gearstore-06", FlowScenario.SETTLEMENT_HOLD, as_of)
    catalog = {**evidence_catalog(obs.rows, base, case.profile.industry), **ledger_catalog(lb)}
    assert all(re.fullmatch(EVIDENCE_ID_PATTERN, k) for k in catalog)
    assert catalog["W:5:held"] == obs.rows[4]["held"]
    assert catalog["B:ledger:weekly_orders"] == lb.weekly_orders
    assert set(ledger_catalog(lb)) == {
        "B:ledger:weekly_orders", "B:ledger:return_rate", "B:ledger:deduction_rate", "B:ledger:weekly_settle_net",
    }
    assert LedgerBaseline.from_dict(lb.to_dict()) == lb
    hold = next(c for c in obs.candidates if c.cause is AlertCause.SETTLEMENT_HOLD)
    assert hold.evidence_ids and all(i in catalog for i in hold.evidence_ids)
    assert all(i.split(":")[2] in CAUSE_FIELDS[AlertCause.SETTLEMENT_HOLD] for i in hold.evidence_ids)


def test_ledger_baseline_needs_orders():
    as_of = date(2026, 11, 2)
    _, flow, _, pg, lb = _prepared("seller-dailylook-01", FlowScenario.STABLE, as_of)
    assert ledger_baseline(pg.model_copy(update={"orders": ()}), flow.start, "패션의류", ()) is None
    assert lb.weekly_orders > 0 and 0 < lb.return_rate < 0.2 and 0 <= lb.deduction_rate < 0.2
    assert lb.weekly_settle_net > 0 and lb.platform_weekly_net and lb.platform_weekly_orders
    assert lb.prior_advance is False


def test_platform_weeks_split_ledger_by_platform():
    as_of = date(2026, 11, 2)
    _, flow, _, pg, _ = _prepared("seller-gearstore-06", FlowScenario.SETTLEMENT_HOLD, as_of)
    _, obs = _observe("seller-gearstore-06", FlowScenario.SETTLEMENT_HOLD, as_of)
    rows = platform_weeks(pg, flow.start, 3, 5)
    assert {r["week"] for r in rows} == {3, 4, 5}
    assert all(set(r) == {"week", "platform", "order_gross", "returns", "held", "released", "deductions",
                          "settle_net"} for r in rows)
    for week in (3, 4, 5):
        assert sum(r["held"] for r in rows if r["week"] == week) == obs.rows[week - 1]["held"]
    assert sum(r["held"] for r in rows if r["platform"] == "쿠팡") > 0


def test_cause_of_trigger_maps_new_triggers_and_new_debt():
    assert CAUSE_OF_TRIGGER == {
        "sales_slump": AlertCause.SALES_SLUMP,
        "settlement_hold": AlertCause.SETTLEMENT_HOLD,
        "return_spike": AlertCause.RETURN_SPIKE,
        "cost_surge": AlertCause.COST_SURGE,
        "new_debt": AlertCause.HIGH_COST_BORROWING,
    }
