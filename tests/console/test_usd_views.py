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
from console.monitoring import load_monitoring_raw, loan_view, signal_metrics
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


@pytest.mark.parametrize("lang", ["ko", "en"])
def test_auditor_masks_case_display_alias_in_officer_text(lang, make_doc, fake_raw, raw_profile):
    deps = fixtures.build_deps()
    source, alias = "대전밀빵공방", "Daejeon Wheat Bakery"
    decision = {"decision": "approve", "reason": f"Checked {source} and {alias}.",
                "conditions": [f"Contact {alias.upper()}."]}
    doc = make_doc("approved", officer_decision=decision, applicant_id="seller-bakery-20")
    deps.store.docs[doc["id"]] = doc
    deps.raw = fake_raw({("consent-c-1", "profile.json"): dict(raw_profile, applicant_id=doc["applicant_id"],
                                                               business_name=source),
                         ("consent-c-1", "transactions.json"): []})
    client = TestClient(create_app(deps))
    url = f"/applications/{doc['id']}?lang={lang}"
    officer = detail_panels(client.get(url).text)["tab-decision"]
    assert decision["reason"] in officer and decision["conditions"][0] in officer
    deps.demo_principal = Principal("auditor", "Auditor", frozenset({AUDITOR}))
    html = client.get(url).text
    assert source not in html and alias.casefold() not in html.casefold()
    panel = detail_panels(html)["tab-decision"]
    assert "Checked [[BIZNAME_" in panel and "Contact [[BIZNAME_" in panel


@pytest.mark.parametrize("received,stored,codes,verified", [
    ("2026-11-09T09:00:00+09:00", 5_000_000, [], True),
    ("2026-11-03T09:00:00+09:00", 5_000_000, [], False),  # v1 was amended in place.
    ("2026-11-09T09:00:00+09:00", 4_900_000, [], False),
    ("2026-11-09T09:00:00+09:00", 5_000_000, None, False),
])
def test_archived_krw_formula_requires_verified_version(received, stored, codes, verified, make_doc):
    deps = fixtures.build_deps()
    doc = make_doc(received_at=received, requested_amount_krw=8_000_000)
    doc["underwriting"]["features"].update(application_id=doc["id"], requested_amount_krw=8_000_000,
        avg_monthly_inflow_krw=12_000_000, requested_tenor_months=12, months_of_history=12, cash_buffer_days=30,
        revenue_volatility=.2, tax_bank_match_ratio=.95, region="서울", business_age_months=38)
    doc["policy"].update(reason_codes=codes, max_amount_krw=stored)
    deps.store.docs[doc["id"]] = doc
    panes = detail_panels(TestClient(create_app(deps)).get(f"/applications/{doc['id']}?lang=ko").text)
    summary = panes["tab-summary"]
    assert f"한도 {stored:,}원" in summary and f"한도 {stored:,}원" in panes["tab-policy"]
    if verified:
        assert "상품 최대 500만 원" in summary and "산출 한도 5,000,000원" in summary
    else:
        assert "한도 산정 후보" not in summary and "산출 근거를 확인할 수 없습니다" in summary
    if codes is None:
        assert "걸린 사유 코드가 없습니다." not in panes["tab-policy"]
    assert doc["policy"]["max_amount_krw"] == stored and doc["policy"]["reason_codes"] == codes


def test_archived_transaction_changes_current_evidence_not_published_review(make_doc, raw):
    deps = fixtures.build_deps()
    doc = make_doc()
    doc["assessment"]["claims"] = [{"text": "Reviewed transfer.", "txn_ids": ["tx-archived"], "metric_ids": []}]
    doc["assessment"]["officer_checks"] = []
    deps.store.docs[doc["id"]] = doc
    deps.raw = raw
    raw.blobs[("consent-c-1", "transactions.json")][0]["txn_id"] = "tx-archived"
    client = TestClient(create_app(deps))
    url = f"/applications/{doc['id']}?lang=en"
    before = detail_panels(client.get(url).text)
    assert ">1/1<" in before["tab-opinion"] and "All evidence verified" in before["tab-opinion"]
    assert "tx-archived" in before["tab-evidence"]
    raw.blobs.clear()
    after = detail_panels(client.get(url).text)
    assert ">0/1<" in after["tab-opinion"] and "All evidence verified" not in after["tab-opinion"]
    assert "Reviewed transfer." in after["tab-opinion"] and ">Passed<" in after["tab-opinion"]
    assert "Source data for the cited transactions is unavailable." in after["tab-evidence"]


@pytest.mark.parametrize("record", [{}, {"penalties": None}, {"penalties": []}])
def test_usd_penalties_unknown_is_not_explicit_empty(record):
    deps = fixtures.build_deps()
    doc = deps.store.docs["preview-usd-bakery"]
    doc["underwriting"]["marketplace"] = {"available": True, "mkt_days_since_suspension": 15, **record}
    doc["underwriting"]["features"]["mkt_days_since_suspension"] = 15
    panels = detail_panels(TestClient(create_app(deps)).get(f"/applications/{doc['id']}?lang=en").text)
    if record.get("penalties") == []:
        assert "No penalties." in panels["tab-marketplace"]
    else:
        assert "No penalties." not in panels["tab-marketplace"]
        assert "Penalty history is unavailable." in panels["tab-marketplace"]
    assert "15" in panels["tab-cashflow"] and "mkt_days_since_suspension" in panels["tab-cashflow"]


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


def _track_week(n, full, bank, explained, basis=()):
    return {"week": n, "row": {"week": n, "revenue_cents": 404123, "buffer_days": 7}, "triggers": full,
            "bank_only_triggers": bank, "explained": explained, "basis": list(basis)}


def test_usd_loan_shows_basis_card():
    deps = fixtures.build_deps()
    breach = {"code": "S003", "metric": "recon_ratio", "approved": 0.99, "floor": 0.95,
              "value": 0.81, "status": "breach"}
    loan = dict(USD, id="usd-track", application_id="usd-track", applicant_id=SELLER, industry="Food",
                amount_cents=500001, tenor_months=6, decision="approve",
                baseline={"avg_weekly_revenue_cents": 484000, "cash_buffer_days": 12},
                approval_basis=[{"code": "S003", "metric": "recon_ratio", "approved": 0.99, "floor": 0.95}],
                monitoring={"status": "completed", "replay_id": "rp", "processed_week": 4, "weeks": [
                    _track_week(1, [], [], []), _track_week(2, [], [], []),
                    _track_week(3, ["basis_breach"], [], [], [breach]),
                    _track_week(4, [], ["inflow_drop"], [{"trigger": "inflow_drop"}])]})
    alert = {"id": "ew-3", "alert_id": "ew-3", "loan_id": "usd-track", "replay_id": "rp", "week": 3,
             "severity": "high", "recommended_action": "check", "status": "open", "summary": "s",
             "primary_cause": "settlement_assigned", "models": {}}
    deps.monitoring = fixtures.MemoryMonitoring([loan], [alert])
    page = TestClient(create_app(deps)).get("/loans/usd-track?lang=en").text
    for text in ("Approval basis", "Broken (week 3)", "Approved 0.99", "Floor 0.95", "Week 3 0.81",
                 "Reconciliation ratio", "Settlement assignment", "Approval basis broken",
                 "Fast, accurate settlements", "Explained"):
        assert text in page
    assert "settlement_on_time_rate" not in page and "recon_ratio" not in page
    assert "Monitoring comparison" not in page


def test_usd_loan_s004_basis_has_no_approved_value_and_never_renders_none():
    deps = fixtures.build_deps()
    ok = {"code": "S004", "metric": "orders_vs_prior_year", "approved": None, "floor": 0.75, "value": 0.9,
          "status": "ok"}
    loan = dict(USD, id="usd-s004", application_id="usd-s004", applicant_id=SELLER, industry="Food",
                amount_cents=500001, tenor_months=6, decision="approve",
                baseline={"avg_weekly_revenue_cents": 484000, "cash_buffer_days": 12},
                approval_basis=[{"code": "S004", "metric": "orders_vs_prior_year", "approved": None, "floor": 0.75}],
                monitoring={"status": "completed", "replay_id": "rp", "processed_week": 2, "weeks": [
                    _track_week(1, [], [], [], [dict(ok, value=0.95)]), _track_week(2, [], [], [], [ok])]})
    deps.monitoring = fixtures.MemoryMonitoring([loan], [])
    page = TestClient(create_app(deps)).get("/loans/usd-s004?lang=en").text
    for text in ("vs. same period last year", "Orders vs last year", "Floor 0.75", "Week 2 0.9", "Holding"):
        assert text in page
    assert "None" not in page and "Approved" not in page


def _usd_page(weeks, loan_id, alerts=(), lang="en"):
    deps = fixtures.build_deps()
    loan = dict(USD, id=loan_id, application_id=loan_id, applicant_id=SELLER, industry="Food",
                amount_cents=500001, tenor_months=6, decision="approve",
                baseline={"avg_weekly_revenue_cents": 484000, "cash_buffer_days": 12},
                monitoring={"status": "completed", "replay_id": "rp", "processed_week": len(weeks), "weeks": weeks})
    deps.monitoring = fixtures.MemoryMonitoring([loan], list(alerts))
    return TestClient(create_app(deps)).get(f"/loans/{loan_id}?lang={lang}").text


def _watch(stage, kind="call", **fields):
    return {"stage": stage, "kind": kind, "incident_id": None, "base_stage": None, "quiet_weeks": 0,
            "stage_up": False, "signals": [], "market": True, "agent": None, "why_now": None, "outlook": None,
            "adjust_reason": None, "alert_id": None, "alert_action": None, **fields}


def _signal(code, source, strength, *metric_ids):
    return {"code": code, "source": source, "strength": strength, "metric_ids": list(metric_ids)}


def _watch_week(n, watch, **row):
    return {"week": n, "row": {"week": n, "revenue_cents": 404123, "buffer_days": 7, **row}, "triggers": [],
            "watch": watch}


def _journal(page):
    match = re.search(r'<section id="watch-journal".*?</section>', page, re.S)
    assert match, "journal card missing"
    return match.group(0)


def _scene_weeks():
    return [
        _watch_week(1, _watch("normal", "normal"), mkt_cancel_rate=0.031),
        _watch_week(2, _watch("watch", incident_id="in-rp-w02", base_stage="watch",
                              signals=[_signal("cancel_rise", "market", "weak", "W:2:mkt_cancel_rate")]),
                    mkt_cancel_rate=0.046),
        _watch_week(3, _watch("medium", incident_id="in-rp-w02", base_stage="medium", stage_up=True, agent="agent",
                              signals=[_signal("settlement_hold", "settlement", "strong", "W:3:held_cents"),
                                       _signal("account_warning", "market", "strong", "W:3:mkt_penalties")],
                              why_now="취소율 상승 뒤 정산 보류가 겹쳤습니다.", outlook="2주 안에 입금 감소",
                              alert_id="ew-3", alert_action="created"),
                    held_cents=380000, mkt_penalties=1),
        _watch_week(4, _watch("medium", "hold", incident_id="in-rp-w02", base_stage="medium",
                              signals=[_signal("settlement_hold", "settlement", "strong", "W:4:held_cents")]),
                    held_cents=380000),
        _watch_week(5, _watch("high", incident_id="in-rp-w02", base_stage="high", stage_up=True, agent="agent",
                              signals=[_signal("projected_shortfall", "bank", "strong",
                                               "F:5:projected_min_balance_cents")],
                              why_now="보류가 이어져 입금이 줄었습니다.", outlook="7/14 상환일 잔고 부족",
                              alert_id="ew-5", alert_action="updated"),
                    projected_min_balance_cents=-48000),
    ]


def test_journal_newest_first_with_source_chips():
    page = _usd_page(_scene_weeks(), "usd-journal")
    card = _journal(page)
    assert "Agent watch journal" in card
    assert re.findall(r'id="journal-w(\d+)"', card) == ["5", "4", "3", "2", "1"]
    assert "Open incident: High · since week 2" in card
    for text in ("Market · Cancel rate up", "Cancel rate 4.6%", "Settlement · Settlement hold",
                 "Held $3800", "Market · Seller warning/suspension", "Account warnings 1", "Bank · Projected shortfall",
                 "Projected min balance -$480", "Why now", "Outlook", "7/14 상환일 잔고 부족",
                 "No change · stage kept", "Cause updated"):
        assert text in card, text
    week1 = card[card.index('id="journal-w1"'):]
    assert week1.count("Normal") >= 4  # stage badge and three source chips
    assert 'href="#ew-5"' in card and 'href="#ew-3"' in card


def test_journal_chips_hide_metric_ids():
    card = _journal(_usd_page(_scene_weeks(), "usd-chipids"))
    visible = re.sub(r"<[^>]+>", " ", card)
    assert "W:" not in visible and "F:" not in visible
    assert 'title="W:3:held_cents"' in card and 'title="F:5:projected_min_balance_cents"' in card


def test_journal_unknown_metric_falls_back_to_id():
    weeks = [_watch_week(2, _watch("watch", incident_id="in-rp-w02", base_stage="watch",
                                   signals=[_signal("cancel_rise", "market", "weak", "W:2:mystery_metric")]),
                         mystery_metric=7)]
    assert "W:2:mystery_metric = 7" in _journal(_usd_page(weeks, "usd-mystery"))


def test_journal_chip_units():
    weeks = [_watch_week(2, _watch("watch", incident_id="in-rp-w02", base_stage="watch",
                                   signals=[_signal("rating_drop", "market", "weak", "W:2:mkt_rating",
                                                    "W:2:mkt_grade", "W:2:orders_vs_expected")]),
                         mkt_rating=4.25, mkt_grade="B", orders_vs_expected=0.7)]
    card = _journal(_usd_page(weeks, "usd-units"))
    for text in ("Rating 4.25", "Market grade B", "Orders vs expected 70.0%"):
        assert text in card, text


def test_signal_metrics_by_week():
    assert signal_metrics(_scene_weeks()) == {1: set(), 2: {"mkt_cancel_rate"},
                                              3: {"held_cents", "mkt_penalties"}, 4: {"held_cents"},
                                              5: {"projected_min_balance_cents"}}


def test_journal_market_missing_shows_no_data_chip():
    weeks = [_watch_week(1, _watch("normal", "normal", market=False)),
             _watch_week(2, _watch("normal", "normal", market=True), mkt_cancel_rate=0.031),
             _watch_week(3, _watch("normal", "normal", market=True))]  # 마켓 흐름은 있으나 이 주 스냅샷 행이 없다
    card = _journal(_usd_page(weeks, "usd-nomarket"))
    week1 = card[card.index('id="journal-w1"'):]
    assert "Market · No data" in week1 and "Bank · Normal" in week1 and "Settlement · Normal" in week1
    assert "No data" not in card[card.index('id="journal-w2"'):card.index('id="journal-w1"')]
    week3 = card[card.index('id="journal-w3"'):card.index('id="journal-w2"')]
    assert "Market · No data" in week3 and "Market · Normal" not in week3


def test_journal_labels_for_every_kind():
    kinds = {"explained": "Normal · bank inflow dip explained by the payout schedule",
             "suppressed": "Normal · resolved signal continues",
             "quiet": "No signals for 1 week(s)", "resolved_quiet": "Resolved · no signals for 2 weeks in a row",
             "resolved_agent": "Resolved · agent decision"}
    weeks = [_watch_week(n, _watch("resolved" if kind.startswith("resolved") else "normal", kind, quiet_weeks=1))
             for n, kind in enumerate(kinds, 1)]
    card = _journal(_usd_page(weeks, "usd-kinds"))
    for text in kinds.values():
        assert text in card, text


def test_fallback_line_label():
    weeks = [_watch_week(1, _watch("medium", agent="fallback", incident_id="in-rp-w01", alert_id="ew-1",
                                   alert_action="created"))]
    page = _usd_page(weeks, "usd-fallback")
    assert "Agent unavailable · rule stage" in _journal(page)
    assert "에이전트 미응답 · 규칙 단계" in _journal(_usd_page(weeks, "usd-fallback", lang="ko"))


def test_old_replay_hides_journal_and_comparison():
    weeks = [_track_week(1, [], [], []), _track_week(2, [], ["inflow_drop"], [{"trigger": "inflow_drop"}]),
             _track_week(3, ["basis_breach"], [], [])]
    loan = dict(USD, id="usd-oldtrack", application_id="usd-oldtrack", applicant_id=SELLER, industry="Food",
                amount_cents=500001, tenor_months=6, decision="approve",
                baseline={"avg_weekly_revenue_cents": 484000, "cash_buffer_days": 12},
                monitoring={"status": "completed", "replay_id": "rp", "processed_week": 3, "weeks": weeks})
    assert loan_view(loan, [], None, b"k", restore=False)["journal"] is None
    for lang in ("ko", "en"):
        page = _usd_page(weeks, "usd-oldtrack", lang=lang)
        for text in ("감시 비교", "Monitoring comparison", "에이전트 감시 일지", "Agent watch journal"):
            assert text not in page


def test_usd_loan_page_has_no_comparison_copy():
    for lang in ("ko", "en"):
        page = _usd_page(_scene_weeks(), "usd-nocompare", lang=lang)
        for text in ("은행만", "Bank only", "bank-only", "감시 비교", "Monitoring comparison", "먼저 감지",
                     "detected 1 week(s) earlier"):
            assert text not in page, text


def test_alert_card_shows_why_now_outlook_and_source():
    new = {"id": "ew-3", "alert_id": "ew-3", "loan_id": "usd-alert", "replay_id": "rp", "week": 3,
           "severity": "high", "recommended_action": "check", "status": "open", "summary": "s",
           "primary_cause": "settlement_hold", "models": {}, "incident_id": "in-rp-w02",
           "why_now": "정산 보류가 2주째 이어졌습니다.", "outlook": "7/14 상환일 잔고 부족",
           "adjust_reason": "마켓 근거로 한 단계 낮춤",
           "signals": [{"kind": "settlement_hold", "text": "보류", "metric_ids": ["W:3:held_cents"],
                        "source": "settlement"}]}
    old = dict(new, id="ew-2", alert_id="ew-2", week=2, signals=[{"kind": "inflow_drop", "text": "감소",
                                                                  "metric_ids": ["W:2:revenue_cents"]}])
    for key in ("incident_id", "why_now", "outlook", "adjust_reason"):
        del old[key]
    weeks = [_watch_week(2, _watch("normal", "normal")), _watch_week(3, _watch("normal", "normal"),
                                                                     held_cents=380000)]
    page = _usd_page(weeks, "usd-alert", [old, new])
    cards = {m.group(1): m.group(0) for m in re.finditer(r'<section id="(ew-\d)".*?</section>', page, re.S)}
    assert "Why now" not in cards["ew-2"] and "Outlook" not in cards["ew-2"] and "Adjustment reason" not in cards["ew-2"]
    assert '<span class="chip">Settlement</span>' not in cards["ew-2"]
    for text in ("Why now", "Outlook", "Adjustment reason", "7/14 상환일 잔고 부족"):
        assert text in cards["ew-3"]
    source_chip = cards["ew-3"].index('<span class="chip">Settlement</span>')
    assert source_chip < cards["ew-3"].index("W:3:held_cents")


def _market_row(n, **fields):
    return {"mkt_gross_cents": 1200000 + n, "mkt_cancel_rate": 0.02, "mkt_rating": 4.6, "mkt_grade": "A",
            "mkt_penalties": 0, "mkt_pending_cents": 90000, **fields}


def test_market_snapshot_table_rows():
    weeks = [_watch_week(n, _watch("normal", "normal"), **_market_row(n)) for n in range(1, 13)]
    weeks.append(_watch_week(13, _watch("watch"), **_market_row(13, mkt_cancel_rate=0.046, mkt_rating=4.25,
                                                                mkt_grade="B", mkt_penalties=2)))
    page = _usd_page(weeks, "usd-market")
    card = re.search(r'<section id="market-snapshot".*?</section>', page, re.S).group(0)
    assert "Weekly market snapshot" in card
    rows = re.findall(r"<tr>(.*?)</tr>", card.split("<tbody>")[1], re.S)
    assert len(rows) == 13
    assert all(f"<td>{cell}</td>" in rows[-1] for cell in ("13", "4.6%", "4.25", "B", "2", "$900"))
    assert "2.0%" in rows[0] and "No marketplace data" not in page


def test_market_snapshot_note_without_market_data():
    page = _usd_page([_watch_week(n, _watch("normal", "normal")) for n in (1, 2)], "usd-nomkt")
    assert "No marketplace data" in page and 'id="market-snapshot"' not in page
    assert "마켓 자료 없음" in _usd_page([_watch_week(1, _watch("normal", "normal"))], "usd-nomkt", lang="ko")
def test_old_usd_replay_hides_track_cards():
    deps = fixtures.build_deps()
    loan = dict(USD, id="usd-old", application_id="usd-old", applicant_id=SELLER, industry="Food",
                amount_cents=500001, tenor_months=6, decision="approve",
                baseline={"avg_weekly_revenue_cents": 484000, "cash_buffer_days": 12},
                monitoring={"status": "completed", "processed_week": 1, "weeks": [
                    {"week": 1, "row": {"week": 1, "revenue_cents": 404123, "buffer_days": 7},
                     "triggers": ["inflow_drop"], "new_triggers": ["inflow_drop"]}]})
    deps.monitoring.loans["usd-old"] = loan
    page = TestClient(create_app(deps)).get("/loans/usd-old?lang=en").text
    assert all(s not in page for s in ("Monitoring comparison", "Approval basis", "Explained"))
    assert page.count('class="chip') >= 1


def test_usd_forecast_shows_recorded_due_day_cashflows_not_krw_assumptions():
    deps = fixtures.build_deps()
    loan = dict(USD, id="risk", application_id="risk", applicant_id=SELLER, amount_cents=500000,
                tenor_months=6, decision="approve", baseline={"avg_weekly_revenue_cents": 28200,
                "cash_buffer_days": 12}, monitoring={"processed_week": 1, "weeks": [{"week": 1,
                "row": {"week": 1, "expected_revenue_cents": 28200, "revenue_cents": 14500,
                        "revenue_vs_expected": None, "projected_min_balance_cents": -7200,
                        "projected_min_date": "2026-11-11", "shortfall_date": "2026-11-11"},
                "projection": {"start_balance_cents": 31100, "path": [
                    {"date": "2026-11-10", "balance_cents": 13000, "before_payment_cents": 13000, "installment_cents": 0},
                    {"date": "2026-11-11", "balance_cents": -7200, "before_payment_cents": 0, "installment_cents": 7200},
                    {"date": "2026-11-12", "balance_cents": 7000, "before_payment_cents": 10000, "installment_cents": 3000}]}}]})
    deps.monitoring.loans["risk"] = loan
    client = TestClient(create_app(deps))
    html = client.get("/loans/risk?lang=en").text
    forecast = html.split("4-week balance forecast", 1)[1].split("</section>", 1)[0]
    assert all(s in forecast for s in ("$311", "-$72", "11-10 $130", "11-12 $70"))
    assert re.search(r"2026-11-11</td>\s*<td>\$0</td>\s*<td>\$72</td>", forecast)
    assert re.search(r"2026-11-12</td>\s*<td>\$100</td>\s*<td>\$30</td>", forecast)
    assert all(s not in forecast for s in ("Last 4 weeks attainment", "28-day total", "₩", ">0%</"))
    assert "Actual vs expected inflow by week" in html and "$145" in html and "$282" in html
    assert "Cash buffer (days, right axis)" not in html and "<td>None</td>" not in html
    week = loan["monitoring"]["weeks"][0]
    del week["row"]["shortfall_date"]
    del week["projection"]["path"][1]["before_payment_cents"]
    sparse = client.get("/loans/risk?lang=en").text.split("4-week balance forecast", 1)[1].split("</section>", 1)[0]
    assert re.search(r"2026-11-11</td>\s*<td>-</td>\s*<td>\$72</td>", sparse)
    assert not re.search(r'Expected shortfall date</div>\s*<div class="kpi-value">None', sparse)


def test_usd_forecast_keeps_undated_payment_off_the_timeline():
    deps = fixtures.build_deps()
    path = [{"date": "2026-11-10", "balance_cents": 13000},
            {"balance_cents": -7200, "before_payment_cents": 0, "installment_cents": 7200}]
    deps.monitoring.loans["risk"] = dict(
        USD, id="risk", application_id="risk", applicant_id=SELLER, amount_cents=500000, tenor_months=6,
        decision="approve", baseline={"avg_weekly_revenue_cents": 28200, "cash_buffer_days": 12},
        monitoring={"processed_week": 1, "weeks": [{"week": 1, "row": {"week": 1}, "projection": {"path": path}}]})
    response = TestClient(create_app(deps)).get("/loans/risk?lang=en")
    assert response.status_code == 200
    forecast = response.text.split("4-week balance forecast", 1)[1].split("</section>", 1)[0]
    assert re.findall(r"<circle[^>]*><title>(.*?)</title></circle>", forecast) == ["11-10 $130"]
    assert "<td>—</td><td>$0</td><td>$72</td>" in forecast


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


def page_for(deps, lang):
    return TestClient(create_app(deps)).get("/applications/preview-usd-bakery", params={"lang": lang}).text


def reasons(policy):
    return re.findall(r"<tr><td>(R\d+)</td><td>.*?</td><td>(.*?)</td><td>(.*?)</td></tr>", policy)


@pytest.mark.parametrize("lang", ["ko", "en"])
def test_v2_decision_shows_alt_data_compare_and_unit_values(lang):
    from policy.engine import RuleCheck

    deps = fixtures.build_deps()
    doc = deps.store.docs["preview-usd-bakery"]
    doc["policy"] |= {"rules_version": "usd-v2", "decision": "refer",
        "cash_only": {**USD_V1.model_dump(), "decision": "eligible", "max_amount_cents": 500000, "reason_codes": []},
        "reason_codes": [
            RuleCheck("R113", "m", "refer", "pg_offset_rate_trend_pp", "gt", 5.94, 3.0, "hit").reason().model_dump(),
            RuleCheck("R111", "m", "refer", "mkt_cancel_rate_90d", "gt", .13, .08, "hit").reason().model_dump(),
            RuleCheck("R112", "m", "refer", "pg_advance_suspected_count", "gt", 3, 0, "hit").reason().model_dump()]}
    policy = detail_panels(page_for(deps, lang))["tab-policy"]
    assert {"ko": "은행·세무 기록만 볼 때", "en": "Bank and tax records only"}[lang] in policy
    assert {"ko": "정산·마켓 데이터 반영", "en": "With settlement and marketplace data"}[lang] in policy
    count = {"ko": ("3건", "0건"), "en": ("3", "0")}[lang]
    assert reasons(policy) == [("R113", "5.9%p", "3.0%p"), ("R111", "13.0%", "8.0%"), ("R112", *count)]


def test_reason_text_formats_ratio_and_day_features_only_for_usd():
    from console.views import reason_text

    assert reason_text("pg_reconciliation_ratio", 0.857, usd=True) == "85.7%"
    assert reason_text("mkt_return_rate_90d", 0.18, usd=True) == "18.0%"
    assert reason_text("tax_bank_match_ratio", 0.4, usd=True) == "40.0%"
    assert reason_text("mkt_days_since_suspension", 30, usd=True) == "30일"
    assert reason_text("mkt_rating", 3.9, usd=True) is None
    assert reason_text("pg_reconciliation_ratio", 0.857) is None and reason_text("mkt_days_since_suspension", 30) is None


def test_usd_page_shows_r108_as_percent_but_krw_page_keeps_raw_ratio():
    from policy.engine import RuleCheck

    usd = fixtures.build_deps()
    usd.store.docs["preview-usd-bakery"]["policy"]["reason_codes"] = [
        RuleCheck("R108", "m", "refer", "pg_reconciliation_ratio", "lt", .857, .9, "hit").reason().model_dump()]
    shown = detail_panels(TestClient(create_app(usd)).get("/applications/preview-usd-bakery").text)["tab-policy"]
    assert reasons(shown) == [("R108", "85.7%", "90.0%")]

    krw = fixtures.build_deps()
    doc = krw.store.docs["app-20261103-p03"]
    doc["policy"]["reason_codes"] = [
        RuleCheck("R101", "m", "refer", "tax_bank_match_ratio", "lt", .7, .9, "hit").reason().model_dump()]
    shown = detail_panels(TestClient(create_app(krw)).get("/applications/" + doc["id"]).text)["tab-policy"]
    assert reasons(shown) == [("R101", "0.7", "0.9")] and "70.0%" not in shown


def test_v1_decision_keeps_cash_flow_label_and_trace():
    deps = fixtures.build_deps()
    doc = deps.store.docs["preview-usd-bakery"]  # rules_version usd-v1, 새 pg 키·applied_substitutions 없음
    doc["policy"].setdefault("cash_only", {**USD_V1.model_dump(), "decision": "eligible",
                                           "max_amount_cents": doc["policy"]["max_amount_cents"], "reason_codes": []})
    policy = detail_panels(page_for(deps, "ko"))["tab-policy"]
    assert "현금흐름만 볼 때" in policy and "셀러 데이터 반영" in policy
    assert "은행·세무 기록만 볼 때" not in policy and "정산·마켓 데이터 반영" not in policy
    assert "판정이 달라진 이유" not in policy


def _v2_bakery(**overrides):
    from policy.engine import evaluate
    from policy.rules import rules_for_version
    from tests.policy.conftest import usd_features

    f = usd_features(**overrides)
    deps = fixtures.build_deps()
    doc = deps.store.docs["preview-usd-bakery"]
    doc["requested_amount_cents"] = f.requested_amount_cents
    doc["underwriting"]["features"] = f.model_dump(mode="json")
    doc["policy"] = evaluate(f, rules_for_version("usd-v2")).model_dump(mode="json")
    return deps


@pytest.mark.parametrize("lang", ["ko", "en"])
def test_v2_change_explains_substituted_reason_with_conditions(lang):
    deps = _v2_bakery(cash_buffer_days=6.0, pg_reconciliation_ratio=1.0, pg_cash_conversion_days=6.0,
                      pg_top_platform_share=0.6, mkt_tenure_months=30)
    policy = detail_panels(page_for(deps, lang))["tab-policy"]
    ko = lang == "ko"
    assert ("판정이 달라진 이유" if ko else "Why the decision changed") in policy
    assert ("약점을 보완해 판정이 나아졌습니다(심사역 회부 → 적격)" if ko
            else "offset weaknesses in the bank and tax records, so the decision improved (Refer to officer → Eligible)") in policy
    assert ("S003 조건을 충족해 이 사유를 지웠습니다." if ko else "S003 conditions are met, so this reason no longer applies.") in policy
    assert ("현금 완충일수 6.0일 · 기준 10.0일 미만" if ko else "Cash buffer days 6.0 days · Threshold below 10.0 days") in policy
    sub = policy.split("<h4>")[-1]
    assert "Fast, fully reconciled settlements offset a thin cash buffer" in sub and "Insufficient cash buffer" in sub
    assert ("정산금이 빠짐없이 계좌로 들어오고" if ko else "settlements reach the bank account in full") in sub
    assert ("✓ 정산금 계좌 입금 비율 100.0% (기준 98.0% 이상)" if ko
            else "✓ Settlement deposit ratio 100.0% (Threshold 98.0% or more)") in sub
    assert ("✓ 현금화 일수 6.0일 (기준 10.0일 이하)" if ko else "✓ Days to cash 6.0 days (Threshold 10.0 days or less)") in sub


def test_v2_change_names_new_marketplace_risk():
    deps = _v2_bakery(mkt_cancel_rate_90d=0.13)
    policy = detail_panels(page_for(deps, "en"))["tab-policy"]
    assert "revealed risks that the bank and tax records did not show, so the decision tightened (Eligible → Refer to officer)" in policy
    assert "<strong>R111</strong> Order cancellations spiked in the last 90 days" in policy
    assert "A new risk found in settlement and marketplace data" in policy
    assert "Marketplace cancel rate 13.0% · Threshold above 8.0%" in policy


def test_v2_change_explains_settlement_limit():
    deps = _v2_bakery(avg_monthly_inflow_cents=200_000, requested_amount_cents=800_000,
                      mkt_pending_settlement_cents=1_000_000)
    policy = detail_panels(page_for(deps, "en"))["tab-policy"]
    assert "The decision is unchanged (Eligible), and settlement and marketplace data raised the limit ($3000 → $8000)" in policy
    assert "average monthly deposits $2000 × 1.5 = $3000, but 80% of net expected settlements $10000 is $8000" in policy


def test_v2_change_skips_settlement_note_when_request_sets_the_limit():
    deps = _v2_bakery(months_of_history=4, mkt_tenure_months=40, mkt_rating=4.8, mkt_review_count=500,
                      mkt_return_rate_90d=0.02, avg_monthly_inflow_cents=400_000, requested_amount_cents=200_000,
                      mkt_pending_settlement_cents=2_000_000)
    policy = detail_panels(page_for(deps, "en"))["tab-policy"]
    assert "S001 conditions are met, so the case goes to officer review instead of being declined." in policy
    assert "expected settlements set the limit" not in policy
    assert "A decline reason was substituted with seller data, so the limit is capped at $5000." in policy



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


def test_usd_only_seller_is_listed_and_accepted_by_apply():
    client = TestClient(create_app(fixtures.build_deps()))
    assert "Mapo Snack Box" in client.get("/borrower").text
    assert client.get("/apply?applicant_id=seller-snackbox-22", follow_redirects=False).status_code == 200


def test_usd_only_sellers_are_in_usd_picker_with_name_initial_but_not_in_legacy_picker():
    client = TestClient(create_app(fixtures.build_deps()))
    usd = client.get("/borrower").text
    assert re.search(r'avatar av-\d" aria-hidden="true">M</span> <span class="seller-card-label">Mapo Snack Box', usd)
    legacy = client.get("/legacy/borrower").text
    assert "seller-snackbox-22" not in legacy and "seller-popup-29" not in legacy
    assert "/legacy/borrower/seller-dailylook-01" in legacy
