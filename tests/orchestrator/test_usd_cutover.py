"""Offline cutover rehearsal: real HTTP and queue contracts, no cloud or deliveries."""
import asyncio
import json
from dataclasses import replace
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from agents.runtime import AgentRun
from agents.underwriting import ANALYST_AGENT, REVIEWER_AGENT
from console.app import create_app as console_app
from consolepreview.fixtures import build_deps
from journey.activities import ACTIVITIES
from journey.adapters import HttpPolicyApi
from journey.entry import handle_decision, handle_start
from journey.metering import STEPS, run_metered
from journey.monitoring import handle_replay
from mockapis.app import create_app as mock_app
from mockapis.demo import FlowStore, build_demo_cases
from policy.app import create_app as policy_app
from policy.rules import load_rules
from tests.console.test_usd_views import token
from tests.orchestrator.conftest import FakeContext, drive
from tests.orchestrator.test_monitoring_handlers import NOW, SECRET, Outbox, make_deps, pump


class ScriptedReview:
    def run(self, spec, text, handlers):
        if spec.name == ANALYST_AGENT:
            evidence = handlers["get_metrics"]({})
            key = "M:pg_reconciliation_ratio"
            value = evidence["metrics"][key]
            outcome = evidence["policy"]["decision"]
            output = dict(recommendation={"eligible": "approve", "ineligible": "decline", "refer": "refer"}[outcome],
                          summary="Review the settlement record.", counterfactuals=[], officer_checks=[],
                          claims=[dict(text=f"Settlement reconciliation ratio: {value}.", metric_ids=[key], txn_ids=[])])
        else:
            assert spec.name == REVIEWER_AGENT
            output = dict(summary_supported=True, verdicts=[dict(claim_index=0, supported=True, reason="Verified.")],
                          check_verdicts=[], feedback="")
        return AgentRun(spec.name, "1", "offline-" + spec.name, spec.output_model.model_validate(output), [], 1, 1)


@pytest.mark.parametrize("seller,policy,codes,decision", [
    ("seller-bakery-20", "eligible", [], "approve"),
    ("seller-petfood-07", "ineligible", ["R003"], "decline"),
    ("seller-knitroom-04", "refer", ["R107", "S001"], None),
    ("seller-dailylook-01", "eligible", [], "approve"),
])
def test_four_seller_cutover(seller, policy, codes, decision):
    now = NOW.replace(day=24)
    sellers = build_demo_cases(now.date())
    outbox = Outbox()
    worker = make_deps(mock_app(sellers, SECRET, lambda: now, flows=FlowStore(sellers, now.date())), outbox)
    worker = replace(worker, policy=HttpPolicyApi(client=TestClient(policy_app(load_rules()))), clock=lambda: now)
    app_id = "rehearsal-" + seller
    ui = build_deps()
    ui.clock, ui.new_application_id = lambda: now, lambda: app_id
    ui.raw, ui.usd_origination_enabled = worker.raw, True
    client = TestClient(console_app(ui), follow_redirects=False)
    archive = "/applications/app-20261103-p03"
    assert "₩5,000,000" in client.get(archive).text
    legacy_rows = ui.store.list_status_rows(legacy=True)
    csrf = token(client.get("/apply", params={"applicant_id": seller}).text)
    form = dict(applicant_id=seller, csrf=csrf, requested_amount_usd="5000.00", requested_tenor_months="6",
                consent_account="on", consent_tax="on", consent_pg="on")
    assert client.post("/apply", data=form).status_code == 303
    (queue, start), = ui.queue.sent
    assert queue == "applications-start" and start["application_id"] == app_id
    ui.usd_origination_enabled = False
    assert client.post("/apply", data=form).status_code == 503 and len(ui.queue.sent) == 1
    durable = AsyncMock()
    durable.get_status.return_value = None
    durable.start_new.return_value = app_id
    assert asyncio.run(handle_start(durable, json.dumps(start))) == app_id
    durable.get_status.return_value = SimpleNamespace(runtime_status="Running")
    assert asyncio.run(handle_start(durable, json.dumps(start))) is None

    def officer_event():
        doc = worker.store.read("applications", app_id, app_id)
        assert doc["policy"], doc["flags"]
        assert doc["status"] == "awaiting_officer" and doc["policy"]["decision"] == policy
        assert sorted(r["code"] for r in doc["policy"]["reason_codes"]) == codes
        assert doc["assessment"]["review"]["passed"] and doc["assessment"]["claims"][0]["text"].isascii()
        assert doc["requested_amount_cents"] == 500000 and doc["requested_tenor_months"] == 6
        transactions = worker.raw.get_json("consent-" + doc["consent"]["consent_id"], "transactions.json")
        assert transactions and all("amount_cents" in t and t["description"].isascii() for t in transactions)
        ui.store.docs[app_id] = doc
        detail = client.get("/applications/" + app_id).text
        assert "$5,000.00 USD" in detail and "Settlement reconciliation ratio" in detail
        if decision is None:
            return None
        response = client.post(f"/applications/{app_id}/decision", data=dict(csrf=token(detail),
                               decision=decision, amount_usd="5000" if decision == "approve" else "", reason="Reviewed."))
        assert response.status_code == 202
        queue, msg = ui.queue.sent[-1]
        assert queue == "officer-decisions" and asyncio.run(handle_decision(durable, json.dumps(msg)))
        return ("event", durable.raise_event.await_args.args[-1])

    review = replace(worker, agents=ScriptedReview())
    acts = {k: partial(run_metered, k, f, review if k == "assess_credit" else worker)
            if k in STEPS else partial(f, worker) for k, f in ACTIVITIES.items()}
    drive(FakeContext(durable.start_new.await_args.args[-1], acts, start=now), on_wait=officer_event)
    doc = worker.store.read("applications", app_id, app_id)
    ui.store.docs[app_id] = doc
    assert doc["status"] == {None: "awaiting_officer", "approve": "approved", "decline": "declined"}[decision]
    assert app_id in client.get("/").text and app_id not in client.get("/legacy").text
    assert ui.store.list_status_rows(legacy=True) == legacy_rows and "₩5,000,000" in client.get(archive).text
    assert "$5,000.00 USD" in client.get("/apply/" + app_id).text
    loan = worker.store.read("loans", app_id, app_id)
    if decision != "approve":
        assert not loan and not (doc.get("officer_decision") or {}).get("amount_cents")
        return
    assert loan["amount_cents"] == doc["officer_decision"]["amount_cents"] == 500000
    ui.monitoring.loans[app_id] = loan
    assert client.post(f"/loans/{app_id}/replay", data={"csrf": csrf}).status_code == 202
    queue, msg = ui.queue.sent[-1]
    assert queue == "monitoring-replay" and handle_replay(worker, json.dumps(msg))
    pump(worker, outbox)
    loan = worker.store.read("loans", app_id, app_id)
    schedule = loan["repayment"]["schedule"]
    assert [i["amount_cents"] for i in schedule] == [83333] * 5 + [83335]
    bakery = seller == "seller-bakery-20"
    assert sum(i["paid_cents"] for i in schedule) == (249999 if bakery else 166666)
    assert loan["monitoring"]["processed_week"] == 13
    assert loan["monitoring"]["status"] == ("completed" if bakery else "paused")
    if not bakery:
        assert loan["monitoring"]["holds"] == ["collections_proposal"]
