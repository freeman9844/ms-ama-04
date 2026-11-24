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
from shared.sellers import DEMO_SELLERS
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
