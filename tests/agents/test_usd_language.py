import json
import re
from datetime import date, datetime, timezone

import pytest

from agents import collections, notices, underwriting as uw
from agents.early_warning import EwContext, investigate
from agents.officer_facts import counteroffer, evidence_menu, reviewed_detail
from console.officer_chat import build_snapshot
from journey.pretranslate import _by_owner
from shared.contracts import USD_V1
from shared.pseudonymize import Pseudonymizer

META = USD_V1.model_dump()


def english(value):
    assert not re.search("[가-힣₩]", json.dumps(value, ensure_ascii=False))


def test_usd_evidence_not_alternative_offer():
    assert reviewed_detail("Verify the $700.01 deposit", 500000)
    assert counteroffer("Offer $300 instead", 500000)
    assert not reviewed_detail("Verify the $700.01 deposit; offer $300 instead", 500000)


def context():
    return uw.UnderwritingContext(
        "app", {**META, "requested_amount_cents": 500000, "requested_tenor_months": 6,
                "mkt_pending_settlement_cents": 70001, "dscr": 2}, {}, [], [],
        {**META, "decision": "eligible", "max_amount_cents": 500000}, {}, [], {}, str, marketplace={"available": True})


def draft(text):
    return uw.AnalystDraft.model_validate(dict(recommendation="approve", summary="Review the deposits.",
        claims=[dict(text=text, metric_ids=["M:mkt_pending_settlement_cents"], txn_ids=[])],
        counterfactuals=[], officer_checks=[]))


@pytest.mark.parametrize("text,valid", [
    ("Verify the $700.01 deposit.", True), ("Verify the $700.02 deposit.", False),
    ("Verify the $700.01 deposit. Settlement timing remains uncertain.", True),
    ("Verify the $700.01 deposit. The loan file is incomplete.", True),
    ("Verify the $700.01 deposit. That amount is unverified.", True),
    ("Set the loan principal to that amount.", False),
    ("Verify the $700.01 deposit. Settlement takes 99 days.", False),
    ("Verify the KRW 70001 deposit.", False), ("입금을 확인하세요.", False),
    ("Offer $700.01 instead.", False), ("Verify the $2 deposit.", False)])
def test_usd_grounding(text, valid):
    assert (not uw.check_grounding(draft(text), context())) is valid


def test_negative_balance_preserves_sign_separately_from_amount_role():
    from agents.usd_language import issues
    assert not issues("Balance: -$700.01", [-70001])
    assert issues("Balance: -$700.01", [70001])


def test_review_language_is_a_publication_gate():
    from agents.runtime import AgentRun

    class Runner:
        def run(self, spec, text, handlers):
            english(text)
            assert "English" in spec.instructions
            output = draft("Verify the $700.01 deposit.") if handlers else uw.ReviewerOutput.model_validate(
                dict(summary_supported=True, verdicts=[dict(claim_index=0, supported=True, reason="확인")],
                     check_verdicts=[], feedback=""))
            return AgentRun(spec.name, "1", "conversation", output, [], 10, 1, ())

    result = uw.assess(context(), runner=Runner(), analyst_model="a", reviewer_model="r", max_rounds=1)
    assert result.assessment is None
    assert result.record["rounds"][0]["review_issues"]
    english(result.record["error"])


def test_english_output_without_translator():
    consent = dict(META, purpose="Working capital", scopes=["account", "marketplace"],
                   expires_at="2026-12-01T00:00:00+09:00")
    for kind in ("renewal", "deletion"):
        facts = notices.consent_facts(kind, consent, proof={"reason": "expired", "id": "proof-123",
            "deleted_at": consent["expires_at"]})
        note = notices.write_notice(facts, runner=None, model="n")
        english([note.title, note.body])
        assert not notices.check_notice(facts, notices.Draft(title=note.title, body=note.body))
    decision = {"outcome": "rereview", "new_decision": "approve", "amount_cents": 70001, "reason": "사람 원문"}
    facts = notices.grievance_facts(META, decision)
    reply = notices.write_grievance_reply(facts, runner=None, model="g")
    assert "$700.01" in reply.body
    english(reply.body)
    assert decision["reason"] == "사람 원문"
    assert not _by_owner("applications", [{**consent, "application_id": "app"}])
    masked = Pseudonymizer(b"x"*32, {"Daejeon Bakery": "BIZNAME"}).mask("DAEJEON BAKERY received $700.01")
    assert "[[BIZNAME_" in masked and "$700.01" in masked


def test_collection_fallback_is_a_checked_proposal_only():
    case = collections.RepaymentCase.from_doc(dict(META, case_id="case", loan_id="loan",
        installment={"no": 1, "amount_cents": 70001, "due_on": "2026-11-24"}, missed_count=1))
    ctx = collections.CollectionsContext(case, [], datetime.now(timezone.utc), str)
    assert ctx.get_case({})["amount_cents"] == 70001
    assert not ctx.propose(dict(script_id="missed_payment", reason="한글")).get("accepted")
    result = collections.act(ctx, runner=None, model="c")
    proposal = collections.build_proposal(case, result.draft, [], ctx.now, [], str)
    english(proposal)
    assert proposal["status"] == "pending_officer" and "$700.01" in proposal["text"]
    assert collections.message_issues("We will take legal action.")


def test_detector_only_usd_alert():
    ctx = EwContext("alert", "loan", 1, date(2026, 11, 24), "retail", ["inflow_drop"],
        [{"week": 1, "revenue_vs_expected": 0.5}], {}, [], {}, str, dict(META, amount_cents=500000))
    alert = investigate(ctx, runner=None, model="e").alert.model_dump(mode="json")
    english(alert)
    assert alert["currency"] == "USD" and alert["severity"] == "medium"
    assert alert["signals"][0]["metric_ids"] == ["W:1:revenue_vs_expected"]


def test_snapshot_to_evidence_uses_native_amounts():
    doc = dict(META, requested_amount_cents=500000, officer_decision={"decision": "approve", "amount_cents": 490001})
    snapshot = build_snapshot({"doc": doc})
    menu = evidence_menu(snapshot, "en")
    assert "$5,000.00" in menu["application.requested_amount_cents"]
    assert "$4,900.01" in menu["officer_decision.amount_cents"]
    assert "_krw" not in json.dumps(snapshot)


def test_grievance_snapshot_keeps_product_and_original_reason():
    from journey.grievance import _snapshot
    original = {"decision": "decline", "reason": "감사 원문"}
    record = _snapshot(dict(META, officer_decision=original, policy=dict(META, decision="ineligible", max_amount_cents=0)))
    reply = notices.write_grievance_reply(notices.grievance_facts(record, dict(outcome="uphold", reason="원문")),
                                          runner=None, model="g")
    english(reply.body)
    assert record["policy"]["max_amount_cents"] == 0 and record["officer_decision"] == original


def test_application_and_loan_raw_maskers_load_native_sources():
    from agents.pretranslate import load_case_raw
    from console.views import load_raw
    from mockapis.synth import generate_usd_case
    from types import SimpleNamespace
    case = generate_usd_case("seller-bakery-20", date(2026, 11, 24), 120)
    files = {"profile.json": case.profile.model_dump(mode="json"),
             "transactions.json": [case.transactions[0].model_dump(mode="json")]}
    files["flow.json"] = files["transactions.json"]
    raw = SimpleNamespace(get_json=lambda container, key: files[key])
    doc = dict(META, consent={"consent_id": "c"})
    assert load_raw(raw, doc).transactions[0].amount_cents == case.transactions[0].amount_cents
    for source, doc in (("applications", doc), ("loans", dict(META, underwriting_consent_id="c", monitoring={"consent_id": "m"}))):
        profile, txns = load_case_raw(source, doc, raw, None)
        assert profile == case.profile and txns == [case.transactions[0]]


@pytest.mark.parametrize("bad", ["We could extend a $700.01 loan instead.", "A smaller facility of $700.01 is advisable.",
                                 "Use €700.01.", "Verify the $700.01 million deposit."])
def test_no_alternative_credit_or_scale_alias(bad):
    assert uw.check_grounding(draft(bad), context())


def test_alert_rejects_a_korean_signal_and_wrong_unit_without_publishing():
    ctx = EwContext("alert", "loan", 1, date(2026, 11, 24), "retail", ["settlement_hold"],
        [{"week": 1, "held_cents": 70001}], {}, [], {}, str, dict(META, amount_cents=500000))
    args = dict(severity="medium", recommended_action="officer_review", summary="Settlement funds are on hold.",
                check_action="Verify pending deposits.", signals=[dict(kind="settlement_hold", text="Verify $700.01.",
                metric_ids=["W:1:held_cents"], txn_ids=[])])
    for text in ("보류 중", "Verify KRW 70001.", "Verify $700.02."):
        args["signals"][0]["text"] = text
        assert not ctx.raise_alert(args)["accepted"] and ctx.accepted is None
    args["signals"][0]["text"] = "Verify $700.01."
    assert ctx.raise_alert(args)["accepted"]


def test_product_specific_agent_versions_do_not_poison_the_legacy_cache():
    from dataclasses import replace
    import httpx
    from agents.runtime import FoundryAgents
    versions = []

    def register(request):
        versions.append(json.loads(request.content)["definition"]["instructions"])
        return httpx.Response(200, json={"version": str(len(versions))})

    with httpx.Client(transport=httpx.MockTransport(register)) as client:
        runner = FoundryAgents("https://example.invalid", lambda: "test", client=client)
        korean = uw.analyst_spec("a")
        american = replace(korean, instructions="English USD")
        assert [runner.ensure_agent(s) for s in (korean, american, korean)] == ["1", "2", "1"]


def test_persisted_usd_collection_activity_loads_the_correct_mask_and_case():
    from types import SimpleNamespace
    from journey.monitoring import run_collections
    from mockapis.synth import generate_usd_case
    case = generate_usd_case("seller-bakery-20", date(2026, 11, 24), 120)
    loan = dict(META, id="loan", applicant_id=case.profile.applicant_id,
                underwriting_consent_id="c", monitoring={"consent_id": "m"})
    doc = dict(META, case_id="case", loan_id="loan", kind="repayment_case", replay_id="r",
               installment={"no": 1, "amount_cents": 70001, "due_on": "2026-11-24"}, missed_count=1)
    raw = {"profile.json": case.profile.model_dump(mode="json"), "flow.json": [case.transactions[0].model_dump(mode="json")]}
    stored = {"case": doc, "loan": loan}
    deps = SimpleNamespace(store=SimpleNamespace(read=lambda _, key, pk: stored.get(key), replace=lambda *_: True,
                                                 prior_contact_slots=lambda borrower, lo, hi: []),
        raw=SimpleNamespace(get_json=lambda _, key: raw[key]), clock=lambda: datetime.now(timezone.utc),
        pseudonym_key=b"x"*32, agents=None, collections_model="c")
    run_collections(deps, "loan", "case")
    assert doc["proposal"]["status"] == "pending_officer" and "$700.01" in doc["proposal"]["text"]
    assert not doc["collections"]["contacts"]


def test_reviewed_activity_uses_native_tools_and_masks_source_names():
    from types import SimpleNamespace
    from agents.runtime import AgentRun
    from mockapis.synth import generate_usd_case
    from journey.activities import assess_credit
    case = generate_usd_case("seller-bakery-20", date(2026, 11, 24), 120)
    txn = case.transactions[0].model_copy(update={"description": "Payroll Jamie Park", "amount_cents": 70001,
                                                  "txn_type_code": "PG_SETTLE"})
    profile = case.profile.model_dump(mode="json")
    source = {"profile.json": profile, "transactions.json": [txn.model_dump(mode="json")]}
    seen = []

    class Runner:
        def run(self, spec, text, handlers):
            seen.append((spec.definition(), text))
            if handlers:
                rows = handlers["query_transactions"]({"category": "revenue", "limit": 1})
                seen.append(("transactions", rows))
                output = draft("Verify the $700.01 deposit.")
                output.claims[0].txn_ids = [txn.txn_id]
                output.claims[0].metric_ids = []
            else:
                output = uw.ReviewerOutput.model_validate(dict(summary_supported=True, check_verdicts=[], feedback="",
                    verdicts=[dict(claim_index=0, supported=True, reason="The deposit matches the transaction.")]))
            return AgentRun(spec.name, "1", "", output, [], 10, 1, ())

    ctx = context()
    deps = SimpleNamespace(raw=SimpleNamespace(get_json=lambda _, k: source[k]), agents=Runner(), analyst_model="a",
                           reviewer_model="r", pseudonym_key=b"x"*32)
    res = assess_credit(deps, dict(application_id="app", consent_id="c", policy=ctx.policy,
        underwriting={**META, "features": ctx.features, "signals": {}, "monthly": [], "category_overrides": {txn.txn_id: "revenue"}}))
    assert res["assessment"]["currency"] == "USD" and res["assessment"]["review"]["passed"]
    english(seen)
    packet = json.dumps(seen)
    assert "Jamie Park" not in packet and "[[PERSON_" in packet and "amount_cents" in packet


def test_bad_counterfactual_cent_does_not_pass_legacy_tolerance():
    candidate = draft("Verify the $700.01 deposit.")
    candidate.counterfactuals = [uw.DraftCounterfactual(metric_id="M:mkt_pending_settlement_cents",
        current=70002, target=80000, explanation="Verify future deposits.")]
    assert uw.check_grounding(candidate, context())


def test_alert_and_chat_reject_mixed_evidence_before_model():
    from dataclasses import replace
    ctx = context()
    with pytest.raises(ValueError):
        replace(ctx, signals={"new_installment_krw": 70001})
    with pytest.raises(ValueError):
        build_snapshot({"doc": dict(META, requested_amount_cents=70001),
                        "policy": dict(max_amount_krw=70001, decision="refer")})


def test_notice_model_cannot_add_a_fee_or_unrecorded_promise():
    from agents.runtime import AgentRun
    facts = notices.grievance_facts(META, {"outcome": "uphold", "reason": "original"})
    class Runner:
        def run(self, spec, text, handlers):
            return AgentRun(spec.name, "1", "", notices.Draft(title="Review", body="The decision was upheld. "
                "We guarantee approval next time and a $7.999 fee."), [], 1, 1, ())
    reply = notices.write_grievance_reply(facts, runner=Runner(), model="g")
    assert reply.source == "template" and "fee" not in reply.body


def test_english_officer_edit_cannot_turn_collection_into_new_credit():
    case = collections.RepaymentCase.from_doc(dict(META, case_id="c", loan_id="l",
        installment={"no": 1, "amount_cents": 70001, "due_on": "2026-11-24"}, missed_count=1))
    proposal = {"status": "pending_officer", "script_id": "missed_payment"}
    for text in ("Offer $300 instead", "We will sue if you do not pay.", "Your balance is $700.02."):
        result = collections.settle_proposal(proposal, "modify", text=text, include_hardship=False, reason="audit",
            contacts=[], now=datetime.now(timezone.utc), officer_id="o", case=case)
        assert result.contact is None and result.errors


def test_notice_does_not_persist_exception_body():
    class Broken:
        def run(self, *args):
            raise RuntimeError("비공개 applicant details")

    consent = dict(META, purpose="Working capital", scopes=["account"], expires_at="2026-12-01T00:00:00+09:00")
    note = notices.write_notice(notices.consent_facts("renewal", consent), runner=Broken(), model="n")
    assert "applicant details" not in str(note.as_dict()) and note.source == "template"


def test_alert_rejected_tool_arguments_are_not_a_stored_system_narrative():
    from agents.runtime import AgentRun, ToolCall
    ctx = EwContext("alert", "loan", 1, date(2026, 11, 24), "retail", ["inflow_drop"],
        [{"week": 1, "revenue_vs_expected": 0.5}], {}, [], {}, str, dict(META, amount_cents=500000))

    class Runner:
        def run(self, spec, text, handlers):
            args = {"summary": "Unverified private text", "signals": []}
            rejected = handlers["raise_alert"](args)
            return AgentRun(spec.name, "1", "", spec.output_model(decision="no_alert", reason="No verified alert."),
                            [ToolCall("raise_alert", args, rejected)], 10, 1)

    result = investigate(ctx, runner=Runner(), model="e")
    assert result.decision == "fallback_alert"
    assert "Unverified private text" not in str(result.record)
    english(result.record)


def test_native_alert_tool_refuses_legacy_settlement_rows():
    with pytest.raises(ValueError):
        EwContext("a", "l", 1, date(2026, 11, 24), "retail", ["settlement_hold"],
            [{"week": 1, "held_cents": 70001}], {}, [], {}, str, dict(META, amount_cents=500000),
            pg_weeks=[{"week": 1, "platforms": {"coupang": {"held_krw": 70001}}}])


def test_usd_chat_has_no_opinion_when_review_is_missing():
    from agents.officer_chat import OfficerChat
    snapshot = {"application": dict(META, requested_amount_cents=500000),
                "agent_opinion": {"recommendation": "approve", "review_passed": False}}
    answer = OfficerChat(None, "c").ask(snapshot, [], "What is the recommendation?")
    assert "no current reviewed opinion" in answer.answer


def test_grievance_can_explain_recorded_policy_evidence_not_borrower_text():
    app = {**META, "officer_decision": {"decision": "decline"},
           "policy": {**META, "reason_codes": [{"code": "R002", "message": "DSCR below 1.0",
                                               "field": "dscr", "observed": 0.8, "threshold": 1.0}]}}
    facts = notices.grievance_facts(app, {"outcome": "uphold", "reason": "감사 원문"})
    note = notices.write_grievance_reply(facts, runner=None, model="g")
    assert "DSCR below 1.0" in note.body and not notices.check_grievance_reply(
        facts, notices.Draft(title=note.title, body=note.body))


@pytest.mark.parametrize("text", ["Verify the CAD $700.01 deposit.", "Verify the A$700.01 deposit."])
def test_non_us_dollar_is_not_usd_evidence(text):
    assert uw.check_grounding(draft(text), context())


def test_requested_dollars_cannot_be_replaced_by_another_cited_amount():
    candidate = draft("Approve the requested $700.01 in full.")
    assert uw.check_grounding(candidate, context())
    candidate.claims[0].metric_ids = ["M:requested_amount_cents", "P:max_amount_cents"]
    candidate.claims[0].text = "Policy limit: $5,000.00. Approve the requested $5,000.00 in full."
    assert not uw.check_grounding(candidate, context())


@pytest.mark.parametrize("text", ["Approval of $700.01 is appropriate.", "Extend credit of $700.01.", "Extend credit of $ 700.01.",
                                  "The principal should be $700.01.", "Verify the $700.01 deposit. Offer that amount.",
                                  "Verify the $700.01 deposit. Disburse that sum."])
def test_cited_deposit_cannot_be_reassigned_as_principal(text):
    assert uw.check_grounding(draft(text), context())


@pytest.mark.parametrize("clause", ["Set the loan principal to that amount.", "Size the facility accordingly.",
                                   "The principal equals the preceding figure."])
def test_usd_evidence_cannot_be_repurposed_in_either_agent(clause):
    text = f"Verify the $700.01 deposit. {clause}"
    case = collections.RepaymentCase.from_doc(dict(META, case_id="c", loan_id="l",
        installment={"no": 1, "amount_cents": 70001, "due_on": "2026-11-24"}, missed_count=1))
    ctx = collections.CollectionsContext(case, [], datetime.now(timezone.utc), str)
    assert ctx.propose(dict(script_id="missed_payment", reason=text)).get("error")
    assert ctx.draft is None
    assert uw.check_grounding(draft(text), context())


def test_usd_propose_distinguishes_a_balance_from_a_new_credit_amount():
    case = collections.RepaymentCase.from_doc(dict(META, case_id="c", loan_id="l",
        installment={"no": 1, "amount_cents": 70001, "due_on": "2026-11-24"}, missed_count=1))
    ctx = collections.CollectionsContext(case, [], datetime.now(timezone.utc), str)
    assert ctx.propose(dict(script_id="missed_payment", reason="Extend credit of $700.01.")).get("error")
    assert ctx.propose(dict(script_id="missed_payment", reason="Make a counteroffer at half the balance.")).get("error")
    assert ctx.propose(dict(script_id="missed_payment", reason="The outstanding balance is $700.01."))["accepted"]


def test_upheld_approval_renders_original_amount_not_policy_limit():
    app = dict(META, officer_decision=dict(decision="approve", amount_cents=70001),
               policy=dict(META, decision="eligible", max_amount_cents=90000))
    facts = notices.grievance_facts(app, dict(outcome="uphold", reason="Recorded reason"))
    note = notices.write_grievance_reply(facts, runner=None, model="g")
    assert "$700.01" in note.body and "$900.00" not in note.body and "upheld" in note.body


@pytest.mark.parametrize("text", ["Your balance is CHF 1.", "Your balance is $700.01 trillion.",
                                  "Your balance is seven million pesos.", "Your balance is 1."])
def test_usd_final_contact_cannot_invent_money(text):
    case = collections.RepaymentCase.from_doc(dict(META, case_id="c", loan_id="l",
        installment={"no": 1, "amount_cents": 70001, "due_on": "2026-11-24"}, missed_count=1))
    proposal = {"status": "pending_officer", "script_id": "missed_payment"}
    for edited, accepted in ((text, False), ("Your balance is $700.01.", True)):
        result = collections.settle_proposal(proposal, "modify", text=edited, include_hardship=False, reason="audit",
            contacts=[], now=datetime(2026,11,24,3,tzinfo=timezone.utc), officer_id="o", case=case)
        assert (result.contact is not None) == accepted
