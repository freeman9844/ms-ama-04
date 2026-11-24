import asyncio
import json
import re
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from console.adapters import CosmosApplications
from console.adapters import CosmosMonitoring, CosmosOps, CosmosPrivacy
from console.app import create_app
from console.auth import AUDITOR, Principal
from console.monitoring import load_monitoring_raw, loan_view
from consolepreview import fixtures
from journey.entry import handle_decision, handle_start
from journey.validation import validate_officer_decision
from shared.contracts import USD_V1
from opsmetrics.insights import decision_funnel
from opsjob.azure import CosmosRuns


USD = USD_V1.model_dump()
SELLER = "seller-bakery-20"
DOC = dict(USD, id="usd-first", application_id="usd-first", applicant_id=SELLER,
           requested_amount_cents=500001, requested_tenor_months=6, updated_at="2026-01-01",
           source="customer", status="awaiting_officer",
           policy=dict(USD, decision="eligible", max_amount_cents=600000))


class QueryContainer:
    """Execute the production WHERE/order/limit against mixed local documents."""

    def __init__(self, docs):
        self.docs = docs

    def query_items(self, query, parameters=(), **kwargs):
        db = sqlite3.connect(":memory:")
        for name, fn in (("IS_DEFINED", lambda v: v is not None), ("IS_STRING", lambda v: isinstance(v, str)),
                         ("IS_NUMBER", lambda v: isinstance(v, int)), ("STARTSWITH", lambda x, p: x.startswith(p))):
            db.create_function(name, fn.__code__.co_argcount, fn)
        db.execute("create table records (doc text)")
        db.executemany("insert into records values (?)", [(json.dumps(d),) for d in self.docs])
        tail = query.split(" FROM c", 1)[1]
        tail = re.sub(r"c\.(\w+)", r"json_extract(doc, '$.\1')", tail)
        limit = " LIMIT @limit" if "TOP @limit" in query else ""
        try:
            return [json.loads(v) for v, in db.execute("SELECT doc FROM records" + tail + limit,
                    {p["name"][1:]: p["value"] for p in parameters})]
        finally:
            db.close()


def test_archive_before_limit():
    legacy = [dict(id=f"old-{n}", applicant_id=SELLER, requested_amount_krw=4000000,
                   status="approved", updated_at="2026-11-24") for n in range(100)]
    deps = fixtures.build_deps()
    deps.store = CosmosApplications(QueryContainer([*legacy, DOC]))
    client = TestClient(create_app(deps))
    home = client.get("/").text
    assert "usd-first" in home and "old-0" not in home and "$5,000.01 USD" in home
    assert "Daejeon Bakery" in home
    archive = client.get("/legacy").text
    assert "usd-first" not in archive and "₩4,000,000" in archive
    assert all(f'href="/applications/old-{i}"' in archive for i in range(100))
    assert [x["status"] for x in deps.store.list_status_rows()] == ["awaiting_officer"]


def token(html):
    return re.search(r'name="csrf" value="([^"]+)"', html)[1]


@pytest.mark.parametrize("setting, expected", [(None, 503), ("false", 503), ("yes", 503), ("true", 303)])
def test_usd_origination_gate(monkeypatch, setting, expected):
    from console import adapters

    messages = []
    monkeypatch.setattr(adapters, "_cosmos_database", lambda *a: SimpleNamespace(get_container_client=lambda _: None))
    monkeypatch.setattr(adapters, "BlobRawStore", lambda *a: None)
    monkeypatch.setattr(adapters, "_queue_factory", lambda *a: lambda name: SimpleNamespace(
        send_message=lambda body: messages.append((name, json.loads(body)))))
    env = dict.fromkeys(adapters.REQUIRED_ENV, "offline")
    env.update(PSEUDONYM_KEY="k" * 32, CONSOLE_AUTH_MODE="demo")
    if setting is not None:
        env["USD_ORIGINATION_ENABLED"] = setting
    client = TestClient(create_app(adapters.build_deps(env)), follow_redirects=False)
    for endpoint, page in [("/apply", f"/apply?applicant_id={SELLER}"), ("/applications", "/applications/new")]:
        form = dict(csrf=token(client.get(page).text), applicant_id=SELLER, requested_tenor_months="6",
                    consent_account="on", consent_tax="on", consent_pg="on")
        before = len(messages)
        for fields in [{"requested_amount_krw": "5000000"},
                       {"requested_amount_usd": "5000", "requested_amount_krw": "5000000"}]:
            assert client.post(endpoint, data=form | fields).status_code == 422
        assert len(messages) == before
        assert client.post(endpoint, data=form | {"requested_amount_usd": "5000"}).status_code == expected
    assert len(messages) == (2 if expected == 303 else 0)
    for name, message in messages:
        assert name == "applications-start" and message["requested_amount_cents"] == 500000
        assert "requested_amount_krw" not in message and all(message[k] == v for k, v in USD.items())


@pytest.mark.parametrize(("page", "endpoint", "source"), [
    ("/applications/new", "/applications", "console"), (f"/apply?applicant_id={SELLER}", "/apply", "customer")])
def test_usd_form_to_real_start_consumer(page, endpoint, source):
    deps = fixtures.build_deps()
    client = TestClient(create_app(deps), follow_redirects=False)
    html = client.get(page).text
    assert 'name="requested_amount_usd"' in html and 'value="5000.00"' in html
    assert "New USD applications are not open yet." in html
    form = dict(csrf=token(html), applicant_id=SELLER, requested_amount_usd="5,000.01", requested_tenor_months="6",
                consent_account="on", consent_tax="on", consent_pg="on")
    assert client.post(endpoint, data=form).status_code == 503 and not deps.queue.sent
    deps.usd_origination_enabled = True
    assert client.post(endpoint, data={**form, "requested_amount_usd": "5000.001"}).status_code == 422
    assert client.post(endpoint, data=form).status_code == 303
    queue, msg = deps.queue.sent[-1]
    durable = AsyncMock()
    durable.get_status.return_value = None
    asyncio.run(handle_start(durable, json.dumps(msg)))
    payload = durable.start_new.await_args.args[-1]
    assert queue == "applications-start" and payload["requested_amount_cents"] == 500001
    assert payload["source"] == source and all(payload[k] == v for k, v in USD.items())
    assert "requested_amount_krw" not in payload


@pytest.mark.parametrize("enabled", [False, True])
def test_usd_officer_detail_to_real_decision_consumer(enabled):
    deps = fixtures.build_deps()
    deps.usd_origination_enabled = enabled
    deps.store.docs[DOC["id"]] = DOC
    client = TestClient(create_app(deps))
    html = client.get("/applications/usd-first").text
    assert "$5,000.01 USD" in html and "$6,000.00 USD" in html and 'name="amount_usd"' in html
    assert "Daejeon Bakery" in html and "₩" not in html
    result = client.post("/applications/usd-first/decision", data=dict(csrf=token(html), decision="approve",
                                                                       amount_usd="5000.01", reason="Reviewed"))
    assert result.status_code == 202
    durable = AsyncMock()
    durable.get_status.return_value = SimpleNamespace(runtime_status="Running")
    queue, msg = deps.queue.sent[-1]
    assert queue == "officer-decisions" and asyncio.run(handle_decision(durable, json.dumps(msg)))
    decision, error = validate_officer_decision(durable.raise_event.await_args.args[-1], DOC["policy"], usd=True)
    assert not error and decision.amount_cents == 500001

    old = dict(id="pending-old", application_id="pending-old", applicant_id=SELLER, status="awaiting_officer",
               requested_amount_krw=5000000, policy=dict(decision="eligible", max_amount_krw=5000000))
    deps.store.docs[old["id"]] = old
    html = client.get("/applications/pending-old").text
    assert "₩5,000,000" in html and 'name="amount_krw"' in html
    result = client.post("/applications/pending-old/decision", data=dict(csrf=token(html), decision="approve",
                         amount_krw="4500000", reason="Historical request"))
    assert result.status_code == 202
    queue, msg = deps.queue.sent[-1]
    assert queue == "officer-decisions" and asyncio.run(handle_decision(durable, json.dumps(msg)))
    payload = durable.raise_event.await_args.args[-1]
    assert "currency" not in payload and "amount_cents" not in payload
    decision, error = validate_officer_decision(payload, old["policy"])
    assert not error and decision.amount_krw == 4500000


def test_same_seller_loan_grievance_consent_and_run_scopes():
    legacy = dict(id="legacy", applicant_id=SELLER, requested_amount_krw=5000000, amount_krw=4000000,
                  updated_at="2026-11-01", received_at="2026-11-01", run_id="shared")
    usd = dict(DOC, amount_cents=500001, received_at="2026-01-01", run_id="shared",
               officer_decision=dict(USD, decision="approve", amount_cents=500001),
               assessment=dict(USD, schema_version="2", review={"passed": True}, recommendation="approve"))
    container = QueryContainer([legacy, usd])
    malformed = {"id": "not-an-established-contract", "applicant_id": SELLER, "submitted_at": "2099", "granted_at": "2099"}
    def records(key):
        return QueryContainer([dict(row, application_id=row["id"], **{key: row["id"]}) for row in (legacy, usd)] + [malformed])
    monitoring, ops = CosmosMonitoring(container, None), CosmosOps(container)
    privacy = CosmosPrivacy(records("consent_id"), None, records("grievance_id"))
    for archive, expected in [(True, "legacy"), (False, "usd-first")]:
        for rows in (monitoring.list_loans(1, legacy=archive), privacy.list_grievances(1, legacy=archive),
                     privacy.consents_for_applicant(SELLER, legacy=archive),
                     privacy.grievances_for_applicant(SELLER, legacy=archive)):
            assert [row["id"] for row in rows] == [expected]
        assert len(ops.by_run("shared", legacy=archive)) == len(ops.recent(1, legacy=archive)) == 1
    rows = ops.by_run("shared")
    assert rows[0]["approved_amount_cents"] == rows[0]["requested_amount_cents"] == 500001
    assert decision_funnel(rows)["agreement"]["rate"] == 1
    legacy_job = CosmosRuns.__new__(CosmosRuns)
    legacy_job._container = container
    assert [d["id"] for d in legacy_job.by_run("shared")] == ["legacy"]


def test_usd_loan_repayment_and_chart_are_native():
    deps = fixtures.build_deps()
    loan = dict(USD, id="usd-loan", application_id="usd-loan", applicant_id=SELLER, industry="Food",
                amount_cents=500001, tenor_months=6, decision="approve",
                baseline={"avg_weekly_revenue_cents": 484000, "cash_buffer_days": 12},
                monitoring={"status": "completed", "processed_week": 1, "weeks": [
                    {"week": 1, "row": {"week": 1, "revenue_cents": 404123, "buffer_days": 7}}]},
                repayment={"installment_cents": 83333, "schedule": [
                    {"no": 1, "amount_cents": 83333, "paid_cents": 1, "balance_cents": 83332,
                     "due_on": "2026-11-24", "status": "partial"}]})
    deps.monitoring.loans["usd-loan"] = loan
    html = TestClient(create_app(deps)).get("/loans/usd-loan").text
    assert all(s in html for s in ["Daejeon Bakery", "$5,000.01 USD", "$4,041.23 USD", "$0.01 USD", "$833.32 USD"])
    assert "₩" not in html


def test_borrower_portfolios_find_applications_before_consent_exists():
    legacy = dict(id="old-case", application_id="old-case", applicant_id=SELLER, requested_amount_krw=5000000,
                  status="approved", source="customer", officer_decision={"amount_krw": 4500000})
    deps = fixtures.build_deps()
    deps.store = CosmosApplications(QueryContainer([DOC, legacy]))
    client = TestClient(create_app(deps))
    path = f"/borrower/{SELLER}"
    usd = client.get(path + "?lang=ko").text
    assert "$5,000.01 USD" in usd and "usd-first" in usd and "old-case" not in usd
    archive = client.get("/legacy" + path + "?lang=en").text
    assert "₩4,500,000" in archive and "old-case" in archive and "usd-first" not in archive


def test_offline_preview_presents_named_seller_and_original_human_and_agent_records():
    client = TestClient(create_app(fixtures.build_deps()))
    home = client.get("/").text
    assert "Daejeon Bakery" in home
    detail = client.get("/applications/preview-usd-bakery").text
    assert "$5,000.00 USD" in detail and "Verified settlement receipts." in detail
    assert "Settlement receipts support the requested amount." in detail
    assert "preview-usd-bakery" not in client.get("/legacy").text
    assert "$5,000.00 USD" in client.get("/apply/preview-usd-bakery").text


def test_grievance_submission_cannot_target_other_portfolio_for_same_seller():
    deps = fixtures.build_deps()
    deps.store.docs[DOC["id"]] = dict(DOC, status="approved", officer_decision={"amount_cents": 500001})
    deps.store.docs["old"] = dict(id="old", application_id="old", applicant_id=SELLER, requested_amount_krw=5000000,
                                  status="approved", officer_decision={"amount_krw": 5000000})
    client = TestClient(create_app(deps))
    path = f"/borrower/{SELLER}/grievances"
    form = {"csrf": token(client.get(path).text), "application_id": DOC["id"], "text": "Please review."}
    assert client.post("/legacy" + path, data=form).status_code == 404
    assert client.post(path, data=form).status_code == 202
    assert deps.queue.sent[-1][1]["currency"] == "USD"
    assert client.post(path, data={**form, "application_id": "old"}).status_code == 404
    assert client.post("/legacy" + path, data={**form, "application_id": "old"}).status_code == 202
    assert "currency" not in deps.queue.sent[-1][1]


def test_usd_review_renders_stored_policy_seller_and_settlement_evidence():
    deps = fixtures.build_deps()
    doc = deps.store.docs["preview-usd-bakery"]
    doc["policy"]["reason_codes"] = [{"code": "R107", "message": "Sales depend on one channel.",
                                     "field": "pg_top_platform_share", "observed": 0.95, "threshold": 0.85}]
    doc["underwriting"]["marketplace"] = {"available": True, "platforms": [
        {"platform": "naver_smart_store", "rating": 4.8, "review_count": 12, "pending_settlement_cents": 12345}]}
    doc["underwriting"]["pg_ledger"] = {"available": True, "pg_reconciliation_ratio": .9, "unmatched": [
        {"settled_on": "2026-11-03", "platform": "coupang", "net_cents": 45678}]}
    page = TestClient(create_app(deps)).get("/applications/preview-usd-bakery").text
    for value in ["R107", "Sales depend on one channel.", "Naver Smart Store", "$123.45 USD", "Coupang",
                  "$456.78 USD", "90.0%"]:
        assert value in page


def test_auditor_visibility_and_csrf_do_not_change_with_currency():
    deps = fixtures.build_deps()
    deps.store.docs[DOC["id"]] = DOC
    deps.demo_principal = Principal("audit", "Auditor", frozenset({AUDITOR}))
    client = TestClient(create_app(deps))
    for path in ("/", "/applications/usd-first"):
        html = client.get(path).text
        assert "seller-bakery-20" in html and "Daejeon Bakery" not in html and 'name="amount_usd"' not in html
    assert client.get("/applications/new").status_code == 403
    assert client.post("/applications/usd-first/decision", data={"amount_usd": "5000"}).status_code == 403
    assert not deps.queue.sent


def test_usd_monitoring_reads_native_blobs_and_masks_referenced_transactions(fake_raw, raw_profile, key):
    loan = dict(USD, id="loan", amount_cents=500000, underwriting_consent_id="uw",
                monitoring={"consent_id": "mon"})
    txn = dict(USD, txn_id="tx-1", booked_on="2026-11-01", amount_cents=46701, balance_after_cents=57802,
               description="Yun Boksun transfer", txn_type_code="TRANSFER_IN")
    store = fake_raw({("consent-uw", "profile.json"): dict(raw_profile, **USD, representative_name="Yun Boksun"),
                      ("consent-mon", "flow.json"): [txn]})
    raw = load_monitoring_raw(store, loan)
    alert = {"alert_id": "alert", "signals": [{"kind": "inflow_drop", "text": "Check receipts.", "txn_ids": ["tx-1"]}]}
    for restore in (False, True):
        shown = loan_view(loan, [alert], raw, key, restore=restore)["transactions"][0]
        assert shown["amount_cents"] == 46701 and "amount_krw" not in shown
        assert ("Yun Boksun" in shown["description"]) is restore


def test_officer_portfolio_navigation_keeps_archive_context_on_direct_links():
    client = TestClient(create_app(fixtures.build_deps()))
    for prefix, unit in (("", "USD"), ("/legacy", "KRW")):
        for path in ("/loans", "/ops", "/grievances"):
            page = client.get(prefix + path).text
            assert unit in re.search(r"<h1>(.*?)</h1>", page)[1]
    for path, crumb in (("/applications/app-20261103-p03", "/legacy"), ("/loans/app-20261103-p03", "/legacy/loans")):
        page = client.get(path).text
        assert f'class="crumb"><a href="{crumb}"' in page and 'href="/legacy/ops"' in page


def test_archive_officer_reason_remains_visible_beside_reference_translation():
    deps = fixtures.build_deps()
    doc = deps.store.docs["app-20261103-p03"]
    doc["officer_decision"]["reason"] = "승인"
    page = TestClient(create_app(deps)).get("/applications/" + doc["id"]).text
    assert '승인<span class="mt-ref">' in page


def test_policy_reason_displays_whole_cents_without_rounding_other_values():
    from policy.engine import RuleCheck

    deps = fixtures.build_deps()
    doc = deps.store.docs["preview-usd-bakery"]
    doc["policy"]["reason_codes"] = [
        RuleCheck("R900", "Limit below product minimum", "engine", "max_amount_cents", "lt",
                  0.0, 10000.0, "hit").reason().model_dump(),
        RuleCheck("R107", "Check ratio", "refer", "pg_top_platform_share", "gt",
                  .95, .85, "hit").reason().model_dump(),
    ]
    page = TestClient(create_app(deps)).get("/applications/" + doc["id"]).text
    reasons = re.findall(r"<p>(R\d+.*?)</p>", page, re.S)
    assert "$0.00 USD" in reasons[0] and "$100.00 USD" in reasons[0]
    assert "0.95" in reasons[1] and "0.85" in reasons[1] and "$" not in reasons[1]


@pytest.mark.parametrize("usd", [True, False], ids=["usd", "legacy"])
def test_servicing_decisions_keep_the_original_officer_reason(usd):
    from tests.console.test_monitoring import BASELINE, ROW

    deps = fixtures.build_deps()
    amount = "amount_cents" if usd else "amount_krw"
    src = dict(status="approved", officer_id="officer", decided_at="2026-10-01", reason="승인")
    baseline = {"avg_weekly_revenue_cents": 484000, "cash_buffer_days": 12} if usd else BASELINE
    week = {"week": 1, "revenue_cents": 404123, "buffer_days": 7} if usd else ROW
    loan = dict(USD if usd else {}, id="loan", application_id="loan", applicant_id=SELLER, baseline=baseline,
                monitoring={"status": "completed", "replay_id": "rp", "weeks": [{"week": 1, "row": week}]},
                repayment={"plan_change": dict(src, option="move", officer_id="plan-officer")}, **{amount: 500000})
    case = dict(id="rc-1", case_id="rc-1", kind="repayment_case", replay_id="rp", loan_id="loan", week=1,
                installment={"no": 1, "due_on": "2026-09-30", amount: 10000}, missed_count=1,
                proposal=dict(src, decision_reason="승인", officer_id="collection-officer"),
                restructure=dict(src, officer_id="restructure-officer"))
    deps.monitoring = fixtures.MemoryMonitoring([loan], [case])
    page = TestClient(create_app(deps)).get("/loans/loan").text
    for actor in ("plan-officer", "collection-officer", "restructure-officer"):
        suffix = "</p>" if usd else '<span class="mt-ref">Approved</span>'
        assert f"{actor} · 2026-10-01 · 승인{suffix}" in page


def test_usd_consent_management_available_during_monitoring_outage(monkeypatch):
    deps = fixtures.build_deps()
    def unavailable(_):
        raise OSError("monitoring unavailable")
    monkeypatch.setattr(deps.monitoring, "get_loan", unavailable)
    consent = dict(USD, consent_id="consent-bakery", applicant_id=SELLER, application_id="preview-usd-bakery",
                   purpose="Working capital", status="granted", scopes=["account"], expires_at="2027-01-01")
    deps.privacy = CosmosPrivacy(QueryContainer([dict(consent, id="consent-bakery")]), None, QueryContainer([]))
    response = TestClient(create_app(deps), raise_server_exceptions=False).get(f"/borrower/{SELLER}/consents")
    assert response.status_code == 200 and f"/borrower/{SELLER}/consents/consent-bakery/revoke" in response.text
