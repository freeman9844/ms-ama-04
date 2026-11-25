from datetime import date, datetime, timedelta, timezone
from functools import cache

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agents import features, pg_ledger
from agents.classify import Category, classify
from mockapis import demo, postloan
from mockapis.app import create_app as mock_app
from policy.app import create_app
from policy.engine import explain
from policy.rules import load_rules, rules_for_version
from shared.contracts import Transaction, UsdPolicyDecision, UsdTransaction
from shared.sellers import DEMO_SELLERS, USD_SELLERS
from shared.usd_source import UsdSettlement

AS_OF = date(2026, 11, 24)
BAKERY = "seller-bakery-20"
DAILY = "seller-dailylook-01"


@cache
def cases():
    return demo.build_usd_cases(AS_OF)


def case_for(seller):
    return cases()[seller]


def test_usd_catalog_keeps_demo_identities_and_channels():
    codes = {"쿠팡": "coupang", "스마트스토어": "naver_smart_store", "자사몰": "own_store", "11번가": "11st"}
    for seller in DEMO_SELLERS:
        case = case_for(seller.applicant_id)
        assert all(value.isascii() for value in (case.profile.business_name, case.profile.representative_name,
                                                  case.profile.region, case.profile.industry))
        assert {s.platform_code for s in case.pg_settlements} == {codes[channel] for channel in seller.channels}


def underwrite(case, principal=500_000):
    return features.compute_usd_underwriting(
        "app-demo", case.profile, case.transactions, case.tax_filings, as_of=AS_OF,
        requested_amount_cents=principal, requested_tenor_months=6,
        pg_settlements=case.pg_settlements, marketplace=case.marketplace,
    )


@pytest.mark.parametrize("seller,outcome,codes", [
    (BAKERY, "eligible", set()), ("seller-petfood-07", "ineligible", {"R003"}),
    ("seller-knitroom-04", "refer", {"R107", "S001"}), (DAILY, "eligible", set()),
])
def test_usd_seller_paths(seller, outcome, codes):
    case = case_for(seller)
    assert case.profile.business_name.isascii() and case.profile.region.isascii()
    assert all(isinstance(t, UsdTransaction) and t.description.isascii() for t in case.transactions)
    balance = case.opening_balance_cents
    for t in case.transactions:
        balance += t.amount_cents
        assert t.balance_after_cents == balance
    uw = underwrite(case, principal=600_099)
    assert uw.features.pg_reconciliation_ratio == 1
    month = uw.monthly[-1]
    assert month.get("revenue_cents") == sum(t.amount_cents for t in case.transactions
                                              if t.booked_on.isoformat().startswith(month["month"])
                                              and t.platform_code)
    assert "unclassified_cents" in uw.expense_monthly[-1]
    decision = explain(uw.features, rules_for_version("usd-v1")).decision
    assert (decision.decision, {r.code for r in decision.reason_codes}) == (outcome, codes)
    assert decision.max_amount_cents == {"eligible": 600_000, "refer": 500_000, "ineligible": 0}[outcome]
    assert all(r.message.isascii() for r in decision.reason_codes)
    assert "_krw" not in str(uw) and "_krw" not in decision.model_dump_json()


@cache
def krw_cases():
    return demo.build_demo_cases(AS_OF)


@pytest.mark.parametrize("seller", [a for a, *_ in demo.DEMO_APPLICANTS])
def test_usd_policy_outcome_mirrors_the_krw_archetype(seller):
    """Same seller, same v4 rules: the USD books must carry the KRW risk archetype into the policy decision.

    KRW reasons must all reappear in USD; USD may add a borderline one (cable-13's R106 depends on the KRW as-of day).
    """
    k = krw_cases()[seller]
    krw = explain(features.compute_underwriting(
        "app-krw", k.profile, k.transactions, k.tax_filings, as_of=AS_OF, requested_amount_krw=5_000_000,
        requested_tenor_months=6, pg_settlements=k.pg_settlements, marketplace=k.marketplace,
    ).features, rules_for_version("2026.11-v4")).decision
    usd = explain(underwrite(case_for(seller)).features, rules_for_version("usd-v1")).decision
    assert usd.decision == krw.decision
    assert {r.code for r in krw.reason_codes} <= {r.code for r in usd.reason_codes}


def test_usd_exact_match_and_currency_isolation():
    case = case_for(BAKERY)
    s = next(s for s in case.pg_settlements if s.settled_on.month == 10 and s.settled_on.year == AS_OF.year)
    t = next(t for t in case.transactions if t.amount_cents == s.net_cents and t.platform_code == s.platform_code
             and t.booked_on == s.settled_on)
    legacy = Transaction(txn_id=t.txn_id, booked_on=t.booked_on, amount_krw=t.amount_cents,
                         balance_after_krw=t.amount_cents, description="쿠팡(주) 주정산", txn_type_code="PG_SETTLE")
    def summary(*txns):
        return pg_ledger.usd_ledger_summary([s], txns, AS_OF)
    assert summary(t.model_copy(update={"description": "Settlement"}))["unmatched_count"] == 0
    assert summary(t.model_copy(update={"platform_code": None}))["unmatched_count"] == 0
    assert summary(t.model_copy(update={"amount_cents": t.amount_cents + 1}), legacy)["unmatched_count"] == 1
    assert classify(t) == Category.REVENUE
    assert classify(t.model_copy(update={"txn_type_code": "TRANSFER_IN", "description": "Payout"})) == Category.REVENUE


def test_refunds_reduce_settleable_sales_not_advertising_deductions():
    case = case_for(BAKERY)
    from shared.krcalendar import add_business_days
    order = next(o for o in case.orders if o.platform_code == "own_store" and o.returns_cents)
    payout = next(s for s in case.pg_settlements if s.platform_code == order.platform_code
                  and s.settled_on == add_business_days(order.order_on, 2))
    assert payout.gross_cents == order.gross_cents - order.returns_cents


def test_actual_policy_endpoint_selects_by_contract():
    f = underwrite(case_for(BAKERY)).features.model_dump(mode="json")
    client = TestClient(create_app(load_rules()))
    d = client.post("/v1/decisions", json=f)
    assert d.status_code == 200 and d.json()["max_amount_cents"] == 500_000
    for bad in [dict(f, currency="KRW"), dict(f, requested_amount_krw=500000),
                dict(f, requested_amount_cents=9999), dict(f, requested_amount_cents=1000001)]:
        assert client.post("/v1/decisions", json=bad).status_code == 422
    with pytest.raises(ValidationError):
        UsdPolicyDecision.model_validate(d.json() | {"decision": "ineligible"})


def test_usd_rules_cannot_be_configured_as_the_legacy_policy():
    with pytest.raises(ValueError, match="legacy"):
        create_app(rules_for_version("usd-v1"))


def test_usd_flow_rejects_a_gap_that_would_drop_pending_receipts():
    case = case_for(BAKERY)
    delayed = date(2026, 12, 1)
    assert sum(s.net_cents for s in case.pending if AS_OF <= s.settled_on < delayed) == 335750
    with pytest.raises(ValueError, match="contiguous"):
        postloan.generate_usd_postloan(case, delayed, 1120, loan_id="after-gap",
                                       amount_cents=500000, installment_cents=84000)


def test_zero_gross_settlement_has_no_concentration_evidence():
    s = UsdSettlement(product_version="usd-v1", currency="USD", language="en", platform_code="coupang",
                      settled_on=date(2026, 10, 15), gross_cents=0, fee_cents=0, net_cents=0)
    summary = pg_ledger.usd_ledger_summary([s], [], AS_OF)
    assert summary["settlement_count"] == 1
    assert summary["pg_top_platform_share"] is None and summary["pg_hhi"] is None
    assert summary["platforms"][0]["share"] is None


def test_signed_consent_selects_source_and_flow_cache():
    from mockapis.synth import Archetype, generate_case
    legacy = {BAKERY: generate_case(BAKERY, Archetype.HEALTHY_SEASONAL, AS_OF, 120)}
    now = datetime(2026, 11, 24, tzinfo=timezone.utc)
    client = TestClient(mock_app(legacy, bytes(32), lambda: now, flows=demo.FlowStore(legacy, AS_OF)))
    req = dict(applicant_id=BAKERY, purpose="test", scopes=["account", "tax", "pg", "marketplace"])
    meta = dict(product_version="usd-v1", language="en", currency="USD")
    def auth(body):
        res = client.post("/consents", json=body)
        assert res.status_code == 201
        return {"Authorization": "Bearer " + res.json()["token"]}
    usd, krw = auth(req | meta), auth(req)
    for path in ("/mydata/profile", "/mydata/transactions", "/pg/settlements", "/tax/filings", "/marketplace/store"):
        raw = client.get(path, headers=usd)
        assert raw.status_code == 200 and "_krw" not in raw.text
        record = raw.json()[0] if isinstance(raw.json(), list) else raw.json()
        assert record["currency"] == "USD"
    flow = {"loan_id": "usd-loan", "amount_cents": 500000, "installment_cents": 84000}
    assert client.get("/mydata/flow", params=flow, headers=usd).json()[0]["amount_cents"] == 500000
    assert client.get("/pg/flow", params=flow, headers=usd).json()["orders"][0]["gross_cents"] > 0
    assert "prior_year_orders" in client.get("/pg/flow", params=flow, headers=usd).json()
    assert client.get("/mydata/flow", params=flow, headers=krw).status_code == 422
    assert client.get("/pg/flow", params={"amount_krw": 500000, "installment_krw": 84000}, headers=usd).status_code == 422
    assert "amount_krw" in client.get("/mydata/transactions", headers=krw).json()[0]


@pytest.mark.parametrize("seller,slump", [(BAKERY, False), (DAILY, True)])
def test_thirteen_week_flow_is_native_and_seeded(seller, slump):
    case = case_for(seller)
    kwargs = dict(start=AS_OF, seed=1001, loan_id="loan-usd", amount_cents=500_000, installment_cents=84_000)
    flow = postloan.generate_usd_postloan(case, **kwargs)
    assert flow == postloan.generate_usd_postloan(case, **kwargs)
    assert flow.end == AS_OF + timedelta(days=90)
    assert any(t.loan_id == "loan-usd" and t.amount_cents == 500_000 for t in flow.transactions)
    early = sum(o.gross_cents for o in flow.orders if AS_OF <= o.order_on < AS_OF + timedelta(weeks=3))
    late = sum(o.gross_cents for o in flow.orders if flow.end - timedelta(weeks=3) < o.order_on <= flow.end)
    assert (late < early / 2) == slump
    if not slump:
        assert sum(t.amount_cents == -84_000 for t in flow.transactions) == 3


@pytest.mark.parametrize("seller,cause", [
    (BAKERY, None), (DAILY, "sales_slump"), ("seller-gearstore-06", "settlement_hold"),
    ("seller-linen-10", "return_spike"), ("seller-tangerine-15", "cost_surge")])
def test_usd_postloan_cause_reaches_the_detector(seller, cause):
    from agents.usd_monitoring import observe_usd
    from shared.contracts import USD_V1
    from shared.usd_source import UsdPgFlow
    case = case_for(seller)
    flow = postloan.generate_usd_postloan(case, AS_OF, 1001, loan_id="loan-usd", amount_cents=500_000,
                                          installment_cents=84_000)
    assert flow.scenario.value == (cause or "stable")
    pg = UsdPgFlow(**USD_V1.model_dump(), settlements=flow.pg_settlements, orders=flow.orders)
    mon = {"flow_start": flow.start.isoformat(), "weeks": []}
    loan = {"monitoring": mon, "baseline": {"avg_weekly_revenue_cents": USD_SELLERS[seller].weekly_sales_cents}}
    causes = set()
    for week in range(1, 14):
        hi = flow.start + timedelta(days=7 * week - 1)
        visible = [t for t in flow.transactions if t.booked_on <= hi]
        rows, triggers, candidates, *_ = observe_usd(loan, visible, pg, week, {})
        mon["weeks"].append({"week": week, "row": rows[-1], "triggers": triggers})
        causes |= {c.cause.value for c in candidates}
        assert all(e.split(":")[2] in rows[c.first_week - 1] for c in candidates for e in c.evidence_ids)
    assert causes == ({cause} if cause else set())
    if cause == "settlement_hold":
        assert sum(s.released_cents for s in flow.pg_settlements) == sum(s.held_cents for s in flow.pg_settlements) > 0


def _consent_client():
    from mockapis.synth import Archetype, generate_case
    legacy = {BAKERY: generate_case(BAKERY, Archetype.HEALTHY_SEASONAL, AS_OF, 120)}
    now = datetime(2026, 11, 24, tzinfo=timezone.utc)
    return TestClient(mock_app(legacy, bytes(32), lambda: now, flows=demo.FlowStore(legacy, AS_OF)))


def test_usd_only_seller_consent_is_usd_only():
    client = _consent_client()
    req = dict(purpose="test", scopes=["account", "tax", "pg", "marketplace"])
    meta = dict(product_version="usd-v1", language="en", currency="USD")
    def post(seller, usd):
        return client.post("/consents", json=req | {"applicant_id": seller} | (meta if usd else {}))
    assert post("seller-snackbox-22", True).status_code == 201
    assert post("seller-snackbox-22", False).status_code in (400, 404, 422)
    assert post("seller-unknown-99", True).status_code in (400, 404, 422)


def test_advance_after_loan_diverts_top_platform_payouts():
    case = case_for("seller-snackbox-22")
    flow = postloan.generate_usd_postloan(case, AS_OF, 1001, loan_id="loan-usd", amount_cents=500_000,
                                          installment_cents=84_000)
    onset = flow.start + timedelta(days=14)
    assert flow.scenario is postloan.FlowScenario.ADVANCE_AFTER_LOAN
    naver_paid = [s for s in flow.pg_settlements if s.platform_code == "naver_smart_store" and s.settled_on >= onset]
    assert naver_paid  # the ledger keeps them
    assert not any(t.platform_code == "naver_smart_store" and t.booked_on >= onset for t in flow.transactions)
    adv = [t for t in flow.transactions if t.description == "Marketplace payout"]
    assert adv and all(t.platform_code is None and t.booked_on >= onset - timedelta(days=5) for t in adv)


def test_prior_year_orders_cover_loan_period_shifted_one_year():
    case = case_for("seller-campgear-23")
    flow = postloan.generate_usd_postloan(case, AS_OF, 1001, loan_id="loan-usd", amount_cents=500_000,
                                          installment_cents=84_000)
    days = {o.order_on for o in flow.prior_year_orders}
    assert min(days) >= flow.start - timedelta(days=21 + 364) and max(days) <= flow.end - timedelta(days=364)
    dec = sum(o.gross_cents for o in flow.prior_year_orders if o.order_on.month == 12)
    nov = sum(o.gross_cents for o in flow.prior_year_orders if o.order_on.month == 11)
    assert dec < nov  # autumn peak, December off-season shows in last year's record


def test_old_pg_flow_payload_without_prior_year_still_validates():
    from shared.contracts import USD_V1
    from shared.usd_source import UsdPgFlow
    flow = UsdPgFlow(**USD_V1.model_dump(), settlements=(), orders=())
    assert flow.prior_year_orders == ()


@pytest.mark.parametrize("seller", ["seller-snackbox-22", "seller-campgear-23", "seller-popup-29"])
def test_approved_scene_sellers_generate_stable_postloan(seller):
    # "stable" here means the flow is complete, not that the cause is STABLE: snackbox runs ADVANCE_AFTER_LOAN.
    flow = demo.FlowStore(demo.build_demo_cases(AS_OF), AS_OF).usd_flow(seller, "loan", 500_000, 83_333, 6)
    assert flow is not None and len({t.booked_on.isocalendar()[1] for t in flow.transactions}) >= 13
    assert any(t.description == "AMALend loan repayment" for t in flow.transactions)
