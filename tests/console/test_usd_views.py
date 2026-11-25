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
    assert "usd-first" in home and "old-0" not in home and "$5000" in home
    assert "Daejeon Bakery" in home
    archive = client.get("/legacy").text
    assert "usd-first" not in archive and "₩4,000,000" in archive
    assert all(f'href="/applications/old-{i}"' in archive for i in range(100))
    assert [x["status"] for x in deps.store.list_status_rows()] == ["awaiting_officer"]


def token(html):
    return re.search(r'name="csrf" value="([^"]+)"', html)[1]


def detail_panels(html):
    tabs = re.findall(r'role="tab" aria-controls="(tab-[a-z]+)"', html)
    assert len(tabs) == 8
    parts = re.split(r'<section id="(tab-[a-z]+)"[^>]*role="tabpanel"[^>]*>', html)
    assert tabs == parts[1::2] == ["tab-" + name for name in
        ("summary", "cashflow", "marketplace", "policy", "opinion", "evidence", "consent", "decision")]
    return dict(zip(tabs, parts[2::2], strict=True))


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_shared_detail_navigation_and_native_values(lang, fake_raw, raw_profile):
    deps = fixtures.build_deps()
    doc = deps.store.docs["preview-usd-bakery"]
    doc.update(requested_amount_cents=499975, status="awaiting_officer", officer_decision=None,
               consent={"consent_id": "c-1"}, sla={"deadline": "2026-11-03T09:00:00Z"},
               timeline=[{"status": "received", "at": "2026-11-03T08:00:00Z"}])
    doc["underwriting"].update(signals={"new_installment_cents": 11151}, marketplace={"available": True,
        "platforms": [{"platform": "coupang", "pending_settlement_cents": 87650, "rating": 4.7}]})
    doc["assessment"].update(claims=[{"text": "Stable receipts.", "txn_ids": ["tx-1"],
        "metric_ids": ["S:2026-10:revenue_cents", "P:max_amount_cents"]}], officer_checks=[
        {"text": "Check delayed deposits.", "metric_ids": ["P:max_amount_cents"], "supported": True}],
        counterfactuals=[{"metric_id": "M:avg_monthly_inflow_cents", "current": 35000.0,
                          "target": 40000.0, "explanation": "More deposits would improve coverage."}])
    deps.raw = fake_raw({("consent-c-1", "profile.json"): dict(raw_profile, **USD, representative_name="Yun Boksun"),
        ("consent-c-1", "transactions.json"): [dict(USD, txn_id="tx-1", booked_on="2026-10-02",
            amount_cents=33151, balance_after_cents=44444, description="Yun Boksun transfer",
            txn_type_code="TRANSFER_IN")]})
    deps.chat = object()  # The GET renders the assistant; it must not invoke it.
    client = TestClient(create_app(deps))
    path = f"/applications/{doc['id']}?lang={lang}"
    html = client.get(path).text
    panes = detail_panels(html)
    assert "2026-11-03T08:00:00Z" in html and "SLA" in html and 'data-default-tab="tab-decision"' in html
    assert ("Timeline" if lang == "en" else "타임라인") in html
    assert "$5000" in panes["tab-summary"] and "$6000" in panes["tab-summary"] and "summary-officer" not in html
    assert "class=\"chart columns\"" in panes["tab-cashflow"]
    for value in ("$16527", "$8000", "$185", "$112", "$297"):
        assert value in panes["tab-cashflow"]
    assert "$877" in panes["tab-marketplace"] and "Coupang" in panes["tab-marketplace"]
    assert "usd-v1" in panes["tab-policy"] and "$6000" in panes["tab-policy"]
    for value in ("Stable receipts.", "Check delayed deposits.", "More deposits would improve coverage.",
                  "$16527", "$6000", "$350", "$400", "+$50", 'href="#tab-evidence"'):
        assert value in panes["tab-opinion"]
    opinion_text = re.sub("<[^>]+>", "", panes["tab-opinion"])
    for cited_id in ("S:2026-10:revenue_cents", "P:max_amount_cents", "M:avg_monthly_inflow_cents"):
        assert cited_id in opinion_text  # On touch screens tooltips alone cannot identify the source.
    assert "Yun Boksun transfer" in panes["tab-evidence"] and "$332" in panes["tab-evidence"]
    assert "restored" in panes["tab-consent"]
    assert 'value="4999.75"' in panes["tab-decision"] and "data-officer-chat" in panes["tab-decision"]
    assert "₩" not in html
    doc.update(status="approved", officer_decision=dict(USD, decision="approve", amount_cents=440050,
               reason="Checked Yun Boksun deposits.", conditions=["Contact Yun Boksun."]))
    decided = detail_panels(client.get(path).text)
    assert "$4401" in decided["tab-summary"] and "Checked Yun Boksun deposits." in decided["tab-decision"]
    deps.demo_principal = Principal("audit", "Auditor", frozenset({AUDITOR}))
    masked = client.get(path).text
    assert "Yun Boksun" not in masked and "Daejeon Bakery" not in masked and "data-decision-form" not in masked
    assert "$332" in detail_panels(masked)["tab-evidence"] and "[[PERSON_" in masked
    deps.raw.blobs.clear()
    deleted = client.get(path).text
    assert "Yun Boksun" not in deleted
    evidence = detail_panels(deleted)["tab-evidence"]
    assert "$332" not in evidence and ("unavailable" if lang == "en" else "자료가 없습니다") in evidence
    doc.update(status="awaiting_officer", officer_decision=None)
    assert "data-decision-form" not in detail_panels(client.get(path).text)["tab-decision"]


@pytest.mark.parametrize("lang", ["en", "ko"])
@pytest.mark.parametrize("app_id", ["preview-usd-bakery", "app-20261103-p01"])
def test_last_review_attempt_shows_outcomes_without_rejected_material(lang, app_id):
    deps = fixtures.build_deps()
    doc = deps.store.docs[app_id]
    doc["assessment_run"] = {"rounds": [
        {"round": 1, "grounding_issues": ["PRIVATE-GROUND", "PRIVATE-GROUND-2"],
         "analyst": {"tool_calls": [{"name": "PRIVATE-TOOL", "arguments": {"q": "PRIVATE-ARG"}}]}},
        {"round": 2, "grounding_issues": [], "reviewer": {}, "review_issues": ["PRIVATE-REVIEW"]},
        {"round": 3, "grounding_issues": [], "reviewer": {}, "review_issues": [],
         "contract_issues": ["PRIVATE-CONTRACT"]}]}
    doc["assessment"].update(summary="PRIVATE-DRAFT", review={"passed": False})
    doc.update(status="failed", error={"stage": "assess_credit", "message": "PRIVATE-ERROR"})
    client = TestClient(create_app(deps))
    path = f"/applications/{app_id}?lang={lang}"
    html = client.get(path).text
    assert "PRIVATE-" not in html
    opinion = detail_panels(html)["tab-opinion"]
    assert ("No opinion" if lang == "en" else "의견 없음") in opinion
    rows = re.findall(r"<tr><td>([123])</td>(.*?)</tr>", opinion, re.S)
    assert [n for n, _ in rows] == ["1", "2", "3"]
    labels = ("Evidence fix requested", "Review issues", "Validation failed") if lang == "en" else (
        "근거 보완 요청", "검토 지적", "형식 검증 실패")
    assert all(label in row for label, (_, row) in zip(labels, rows, strict=True))
    assert ("Problems: 2" if lang == "en" else "문제 2건") in rows[0][1]
    assert ("Issues: 1" if lang == "en" else "지적 1건") in rows[1][1]
    doc["assessment"].update(summary="Reviewed summary.", review={"passed": True, "rounds": 3})
    doc["assessment_run"]["rounds"][-1].pop("contract_issues")
    doc.update(status="awaiting_officer", error=None)
    html = client.get(path).text
    assert "PRIVATE-" not in html
    opinion = detail_panels(html)["tab-opinion"]
    assert "Reviewed summary." in opinion
    assert re.search(r'<tr><td>3</td>.*?badge-green[^>]*>(Passed|통과)<', opinion, re.S)


def test_retry_without_a_returned_record_does_not_show_previous_attempt():
    deps = fixtures.build_deps()
    doc = deps.store.docs["preview-usd-bakery"]
    doc.update(status="failed", assessment=None, assessment_run={"error": "The opinion did not pass.",
        "rounds": [{"round": 1, "grounding_issues": ["PRIVATE-REJECTION"]}]},
        steps=[{"step": "assess", "outcome": "failed", "detail": "The"}])
    client = TestClient(create_app(deps))
    path = f"/applications/{doc['id']}?lang=en"
    assert "Evidence fix requested" in detail_panels(client.get(path).text)["tab-opinion"]
    # A thrown retry records the exception type in steps without replacing assessment_run.
    doc["steps"].append({"step": "assess", "outcome": "failed", "detail": "RuntimeError"})
    html = client.get(path).text
    assert "No opinion" in detail_panels(html)["tab-opinion"]
    assert "Evidence fix requested" not in html and "PRIVATE-REJECTION" not in html


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_monthly_expense_readouts_use_average_not_sum_and_handle_missing_installment(lang):
    deps = fixtures.build_deps()
    doc = deps.store.docs["preview-usd-bakery"]
    doc["underwriting"]["monthly"] = [dict(month=m, revenue_cents=10000, operating_out_cents=cost,
        debt_service_cents=0, owner_draw_cents=0) for m, cost in (("2026-09", 12050), ("2026-10", 12249))]
    html = TestClient(create_app(deps)).get(f"/applications/{doc['id']}?lang={lang}").text
    cash = detail_panels(html)["tab-cashflow"]
    donut = re.search(r'<svg class="chart donut".*?</svg>', cash, re.S)[0]
    assert re.search(r"<title>[^<]+\$121 \(100(?:\.0)?%\)</title>", donut) and ">$121</text>" in donut
    assert "$21" in cash and "0/0" not in cash and "₩" not in cash
    assert ("Insufficient data" if lang == "en" else "자료 부족") in cash
    assert ("could not be verified" if lang == "en" else "산출 근거를 확인할 수 없습니다") in html


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_verified_limit_from_policy_tab_and_exact_amount_on_error(lang):
    deps = fixtures.build_deps()
    doc = deps.store.docs["preview-usd-bakery"]
    doc.update(status="escalated", officer_decision=None, requested_amount_cents=500051,
               sla={"deadline": "2026-11-02T00:00:00Z", "escalated": True})
    doc["underwriting"]["features"].update(application_id=doc["id"], dscr=1.8, cash_buffer_days=45,
        revenue_volatility=0.2, tax_bank_match_ratio=.95, months_of_history=12, business_age_months=30,
        industry="retail", region="Seoul", avg_monthly_inflow_cents=274169, requested_amount_cents=500051,
        requested_tenor_months=6)
    doc["policy"].update(max_amount_cents=410000, rate_band="B", reason_codes=[])
    doc["assessment"]["recommendation"] = "decline"
    client = TestClient(create_app(deps))
    path = f"/applications/{doc['id']}"
    html = client.get(path + f"?lang={lang}").text
    panes = detail_panels(html)
    assert 'href="#tab-summary"' in panes["tab-policy"] and "$4100" in panes["tab-policy"]
    for value in ("$4100", "$4113", "$10000", "$100", "$5001"):
        assert value in panes["tab-summary"]
    assert ("request exceeds" if lang == "en" else "신청액이 정책 한도를 초과") in panes["tab-opinion"]
    assert "SLA" in html and "2026-11-02 09:00" in html and 'value="5000.51"' in panes["tab-decision"]
    resp = client.post(path + "/decision", data=dict(csrf=token(html), decision="approve",
                                                     amount_usd="4150.37", reason="Reviewed"))
    assert resp.status_code == 422 and not deps.queue.sent
    assert 'data-default-tab="tab-decision" data-tab-reset' in resp.text
    assert 'value="4150.37"' in detail_panels(resp.text)["tab-decision"]


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_in_progress_usd_without_evidence_refreshes_and_keeps_monthly_empty_state(lang):
    deps = fixtures.build_deps()
    deps.store.docs[DOC["id"]] = dict(DOC, policy=None, status="received")
    html = TestClient(create_app(deps)).get(f"/applications/usd-first?lang={lang}").text
    panes = detail_panels(html)
    assert 'http-equiv="refresh"' in html and 'data-default-tab="tab-summary"' in html
    assert ("No monthly data" if lang == "en" else "월별 자료가 없습니다") in panes["tab-cashflow"]
    assert "data-decision-form" not in panes["tab-decision"] and "data-officer-chat" not in panes["tab-decision"]


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_unreadable_raw_data_does_not_block_or_disclose_detail(lang, fake_raw):
    deps = fixtures.build_deps()
    doc = deps.store.docs["preview-usd-bakery"]
    doc["consent"] = {"consent_id": "unreadable"}
    deps.raw = fake_raw({("consent-unreadable", "profile.json"): {"private": "PRIVATE-SOURCE"},
                         ("consent-unreadable", "transactions.json"): []})
    client = TestClient(create_app(deps), raise_server_exceptions=False)
    resp = client.get(f"/applications/{doc['id']}?lang={lang}")
    assert resp.status_code == 200 and "PRIVATE-SOURCE" not in resp.text
    assert "pii restored" not in detail_panels(resp.text)["tab-consent"]


def test_sparse_settlement_alone_still_shows_known_rows_without_inventing_rates():
    deps = fixtures.build_deps()
    doc = deps.store.docs["preview-usd-bakery"]
    doc["underwriting"] = {"pg_ledger": {"available": True, "platforms": [{"share": .9}],
        "monthly_take_rate": [{"month": "2026-08"}, {"month": "2026-09", "rate": 0}],
        "unmatched": [{"settled_on": "2026-10-01", "platform": "coupang", "net_cents": 0}]},
        "marketplace": {"available": True, "platforms": [{"platform": "coupang", "rating": 4.76}]}}
    resp = TestClient(create_app(deps), raise_server_exceptions=False).get(f"/applications/{doc['id']}")
    assert resp.status_code == 200
    panels = detail_panels(resp.text)
    assert "2026-10-01 Coupang" in panels["tab-cashflow"] and "$0" in panels["tab-cashflow"]
    assert "26-09 0%" in panels["tab-cashflow"] and "26-08 0%" not in panels["tab-cashflow"]
    assert "4.76" in panels["tab-marketplace"]


def test_rounded_display_preserves_editable_principal():
    deps = fixtures.build_deps()
    deps.store.docs[DOC["id"]] = dict(DOC, requested_amount_cents=499975)
    html = TestClient(create_app(deps)).get("/applications/usd-first").text
    assert re.search(r"<dt>Requested amount</dt>\s*<dd>\$5000 / 6 months</dd>", html)
    assert 'name="amount_usd" inputmode="decimal" value="4999.75"' in html


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
    assert 'name="requested_amount_usd"' in html and 'value="5000"' in html
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
    assert "$5000" in html and "$6000" in html and 'name="amount_usd"' in html
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
    assert all(s in html for s in ["Daejeon Bakery", "$5000", "$4041", "$0", "$833"])
    assert "₩" not in html


def test_borrower_portfolios_find_applications_before_consent_exists():
    legacy = dict(id="old-case", application_id="old-case", applicant_id=SELLER, requested_amount_krw=5000000,
                  status="approved", source="customer", officer_decision={"amount_krw": 4500000})
    deps = fixtures.build_deps()
    deps.store = CosmosApplications(QueryContainer([DOC, legacy]))
    client = TestClient(create_app(deps))
    path = f"/borrower/{SELLER}"
    usd = client.get(path + "?lang=ko").text
    assert "$5000" in usd and "usd-first" in usd and "old-case" not in usd
    archive = client.get("/legacy" + path + "?lang=en").text
    assert "₩4,500,000" in archive and "old-case" in archive and "usd-first" not in archive


def test_offline_preview_presents_named_seller_and_original_human_and_agent_records():
    client = TestClient(create_app(fixtures.build_deps()))
    home = client.get("/").text
    assert "Daejeon Bakery" in home
    detail = client.get("/applications/preview-usd-bakery").text
    assert "$5000" in detail and "Verified settlement receipts." in detail
    assert "Settlement receipts support the requested amount." in detail
    assert "preview-usd-bakery" not in client.get("/legacy").text
    assert "$5000" in client.get("/apply/preview-usd-bakery").text


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
    for value in ["R107", "Sales depend on one channel.", "Naver Smart Store", "$123", "Coupang",
                  "$457", "90.0%"]:
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
    policy = detail_panels(page)["tab-policy"]
    reasons = re.findall(r"<tr><td>(R\d+)</td><td>.*?</td><td>(.*?)</td><td>(.*?)</td></tr>", policy)
    assert reasons == [("R900", "$0", "$100"), ("R107", "0.95", "0.85")]


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
