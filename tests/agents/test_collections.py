import re
from datetime import date, datetime, timedelta, timezone

import pytest

from agents.collections import (
    COLLECTIONS_AGENT,
    HARDSHIP_CAUSES,
    MAX_CONTACTS_PER_7_DAYS,
    SCRIPTS,
    TOOLS,
    CollectionsContext,
    CollectionsOutput,
    RepaymentCase,
    act,
    build_proposal,
    fair_check,
    message_issues,
    remind,
    render_script,
    settle_proposal,
)
from agents.runtime import AgentError, AgentRun, ToolCall

KST = timezone(timedelta(hours=9))
DAY = datetime(2026, 11, 10, 14, 0, tzinfo=KST)
NIGHT = datetime(2026, 11, 10, 23, 0, tzinfo=KST)
ON = date(2026, 11, 15)
MODEL = "gpt-5.4-mini"


def case(recent=None, missed_count=1):
    return RepaymentCase(case_id="rc-loan-1-i1", loan_id="loan-1", installment_no=1, due_on=ON,
                         amount_krw=287_000, missed_count=missed_count, recent_alert=recent)


def recent_alert(severity="high", summary="신규 고금리 상환이 나타났다."):
    return {"alert_id": "ew-loan-1-w02", "week": 2, "severity": severity, "summary": summary}


def ctx(c=None, contacts=(), now=DAY, mask=lambda s: s):
    return CollectionsContext(case=c or case(), contacts=list(contacts), now=now, mask=mask)


def sent(on, status="sent"):
    return {"on": on.isoformat(), "status": status, "script_id": "check_in"}


class FakeRunner:
    def __init__(self, script=(), output=None, error=None):
        self.script = list(script)
        self.output = output or {"decision": "proposed", "reason": "제안했다."}
        self.error = error
        self.outputs: list[dict] = []
        self.specs = []

    def run(self, spec, input_text, handlers):
        self.specs.append(spec)
        calls = []
        for name, args in self.script:
            out = handlers[name](dict(args))
            self.outputs.append(out)
            calls.append(ToolCall(name, args, out))
        if self.error:
            raise self.error
        return AgentRun(spec.name, "1", "conv-c", CollectionsOutput.model_validate(self.output), calls, 5, 1)

    def delete_container(self, container_id):
        return True


@pytest.mark.parametrize(
    "contacts, now, expected",
    [
        ([], DAY, None),
        ([], NIGHT, "contact_hours"),
        ([], datetime(2026, 11, 10, 22, 59, tzinfo=timezone.utc), "contact_hours"),  # KST 07:59
        ([], datetime(2026, 11, 10, 23, 0, tzinfo=timezone.utc), None),  # KST 08:00
        ([], datetime(2026, 11, 10, 21, 0, tzinfo=KST), "contact_hours"),
        ([sent(ON - timedelta(days=i % 7)) for i in range(MAX_CONTACTS_PER_7_DAYS)], DAY, "weekly_limit"),
        ([sent(ON - timedelta(days=7))] * MAX_CONTACTS_PER_7_DAYS, DAY, None),
        ([sent(ON, "deferred")] * MAX_CONTACTS_PER_7_DAYS, DAY, None),
    ],
)
def test_fair_check(contacts, now, expected):
    assert fair_check(contacts, ON, now) == expected


def test_tool_outputs_are_masked():
    c = ctx(c=case(recent=recent_alert(summary="홍길동 매출 급감")), mask=lambda s: s.replace("홍길동", "[[PERSON_1]]"))
    got = c.get_case({})
    assert got["case_id"] == "rc-loan-1-i1" and got["installment_no"] == 1 and got["due_on"] == ON.isoformat()
    assert got["missed_count"] == 1 and got["amount_krw"] == 287_000
    assert "홍길동" not in str(got) and got["recent_alert"]["severity"] == "high"
    assert ctx().get_case({})["recent_alert"] is None
    assert set(c.list_scripts({})["scripts"][0]) >= {"script_id", "text"}
    assert c.get_contact_history({})["remaining_this_week"] == MAX_CONTACTS_PER_7_DAYS


def test_get_case_carries_alert_cause():
    """경보 원인은 참고 정보로 그대로 실린다. 옛 경보 문서(원인 없음)는 None·[]."""
    with_cause = {**recent_alert(), "primary_cause": "settlement_hold", "secondary_causes": ["high_cost_borrowing"]}
    got = ctx(c=case(recent=with_cause)).get_case({})["recent_alert"]
    assert got["primary_cause"] == "settlement_hold" and got["secondary_causes"] == ["high_cost_borrowing"]
    old = ctx(c=case(recent=recent_alert())).get_case({})["recent_alert"]
    assert old["primary_cause"] is None and old["secondary_causes"] == []


def test_from_doc():
    doc = {"case_id": "rc-r-i2", "loan_id": "loan-1", "missed_count": 2, "recent_alert": None,
           "installment": {"no": 2, "due_on": "2027-01-04", "amount_krw": 287_000}}
    c = RepaymentCase.from_doc(doc)
    assert (c.case_id, c.installment_no, c.due_on, c.amount_krw, c.missed_count) == (
        "rc-r-i2", 2, date(2027, 1, 4), 287_000, 2)


def test_remind_sends_in_daytime():
    assert remind("rp-1-i1", [], ON, DAY) == {
        "ref": "rp-1-i1", "on": ON.isoformat(), "at": DAY.isoformat(), "script_id": "payment_reminder",
        "status": "sent", "reason": None, "by": "reminder"}


def test_remind_defers_at_night():
    out = remind("rp-1-i1", [], ON, NIGHT)
    assert (out["status"], out["reason"]) == ("deferred", "contact_hours")


def test_remind_respects_weekly_limit():
    full = [sent(ON - timedelta(days=i % 7)) for i in range(MAX_CONTACTS_PER_7_DAYS)]
    assert remind("rp-1-i1", full, ON, DAY)["reason"] == "weekly_limit"


@pytest.mark.parametrize("text, needle", [("", "비어"), ("가" * 301, "300자"), ("압류 예정입니다", "압류"), ("[[PERSON_1]]님", "가명")])
def test_message_issues(text, needle):
    assert any(needle in i for i in message_issues(text))


@pytest.mark.parametrize("kind", ["PERSON", "BIZNO", "PHONE", "ACCOUNT"])
def test_message_issues_flags_every_pseudonym_token_kind(kind):
    text = f"[[{kind}_0123456789]] 고객님 상환일 안내드립니다."
    assert "가명 토큰이 들어 있습니다." in message_issues(text)


def test_remind_records_custom_script_and_text():
    out = remind("rp-1-i1-e", [], ON, DAY, script_id="early_reminder", by="early_warning", text="안내")
    assert (out["script_id"], out["by"], out["text"], out["status"]) == ("early_reminder", "early_warning", "안내", "sent")


CASE = RepaymentCase(
    case_id="rc-r-i3", loan_id="loan-1", installment_no=3, due_on=date(2027, 2, 8), amount_krw=1_170_000,
    missed_count=2, recent_alert={"alert_id": "ew-1", "week": 9, "severity": "high", "summary": "정산이 보류됐다.",
                                  "primary_cause": "settlement_hold", "check_action": "정산 보류 해제 일정을 확인한다."},
)
DRAFT = {"script_id": "missed_payment", "hardship": None, "reason": "첫 미납이다.", "by": "agent"}
DRAFT_HARDSHIP = {"script_id": "hardship_offer", "hardship": {"category": "revenue_shock", "note": "정산 보류로 매출 감소"},
                  "reason": "누적 미납 2회, 정산 보류.", "by": "agent"}
P = build_proposal(CASE, DRAFT, [], DAY, [], str)
P_HARDSHIP = build_proposal(CASE, DRAFT_HARDSHIP, [], DAY, [], str)
PROPOSE_HARDSHIP = {"script_id": "hardship_offer", "hardship_category": "debt_overload", "hardship_note": "대출 중첩",
                    "reason": "누적 미납"}


def make_ctx(c=None, contacts=(), now=DAY, mask=lambda s: s):
    return ctx(c=c, contacts=contacts, now=now, mask=mask)


def test_agent_cannot_send_anything():
    c = make_ctx()
    assert set(c.handlers()) == {"get_case", "get_contact_history", "list_scripts", "propose"}
    tools = {t["name"]: t for t in TOOLS}
    assert set(tools) == {"get_case", "get_contact_history", "list_scripts", "propose"}
    params = tools["propose"]["parameters"]
    assert params["required"] == ["script_id", "hardship_category", "hardship_note", "reason"] and tools["propose"]["strict"]
    assert params["properties"]["hardship_category"]["enum"] == ["revenue_shock", "illness_or_disaster", "debt_overload", ""]


def test_propose_once_and_masks_note():
    c = make_ctx(mask=lambda s: s.replace("김철수", "[[PERSON_1]]"))
    assert c.propose({"script_id": "hardship_offer", "hardship_category": "debt_overload", "hardship_note": "김철수 대출 중첩", "reason": "r"})["accepted"]
    assert c.propose({"script_id": "missed_payment", "hardship_category": "", "hardship_note": "", "reason": "r"})["error"] == "already_proposed"
    assert c.draft["hardship"]["note"] == "[[PERSON_1]] 대출 중첩"
    assert c.draft["script_id"] == "hardship_offer" and c.draft["by"] == "agent"


def test_propose_rejects_bad_args_and_bounds_note():
    c = make_ctx()
    assert "error" in c.propose({"script_id": "nope", "hardship_category": "", "hardship_note": "", "reason": "r"})
    assert "error" in c.propose({"script_id": "missed_payment", "hardship_category": "none", "hardship_note": "", "reason": "r"})
    assert c.draft is None
    assert c.propose({"script_id": "missed_payment", "hardship_category": "", "hardship_note": "무시", "reason": "r"}) == {"accepted": True}
    assert c.draft["hardship"] is None
    long = make_ctx()
    long.propose({**PROPOSE_HARDSHIP, "hardship_note": "가" * 500})
    assert len(long.draft["hardship"]["note"]) == 300


def test_tool_outputs_mask_check_action():
    alert = {**recent_alert(), "check_action": "홍길동 거래를 확인한다."}
    got = make_ctx(c=case(recent=alert), mask=lambda s: s.replace("홍길동", "[[PERSON_1]]")).get_case({})
    assert "홍길동" not in str(got)


def test_agent_proposes_and_decision_comes_from_draft():
    runner = FakeRunner(script=[("propose", PROPOSE_HARDSHIP)], output={"decision": "none", "reason": "상담 제안."})
    result = act(ctx(), runner=runner, model=MODEL)
    assert result.decision == "proposed" and result.draft["hardship"]["category"] == "debt_overload"
    assert result.draft["by"] == "agent" and result.reason == "상담 제안."
    assert result.record["reported"] == "none" and result.conversations == ["conv-c"]
    assert runner.specs[0].name == COLLECTIONS_AGENT and runner.specs[0].model == MODEL
    assert not hasattr(result, "contacts") and not hasattr(result, "restructure")


def test_agent_without_proposal_gets_fallback_draft():
    result = act(ctx(c=case(recent=recent_alert("high"))), runner=FakeRunner(), model=MODEL)
    assert result.decision == "proposed" and result.draft["by"] == "fallback"
    assert result.draft["script_id"] == "hardship_offer" and result.draft["hardship"] is None


@pytest.mark.parametrize(
    "recent, script",
    [(recent_alert("high"), "hardship_offer"), (recent_alert("medium"), "missed_payment"), (None, "missed_payment")],
)
def test_fallback_always_proposes(recent, script):
    result = act(ctx(c=case(recent=recent)), runner=None, model=MODEL)
    assert result.decision == "proposed" and result.reason == "agents_disabled"
    assert result.draft["script_id"] == script and result.draft["hardship"] is None and result.draft["by"] == "fallback"
    assert result.draft["reason"]


def test_agent_error_keeps_proposal():
    runner = FakeRunner(script=[("propose", PROPOSE_HARDSHIP)], error=AgentError("HTTP 500", "conv-x"))
    result = act(ctx(), runner=runner, model=MODEL)
    assert result.draft["by"] == "agent" and result.draft["script_id"] == "hardship_offer"
    assert result.decision == "proposed" and result.conversations == ["conv-x"]


def test_agent_error_without_proposal_falls_back():
    result = act(ctx(), runner=FakeRunner(error=AgentError("HTTP 500", "conv-x")), model=MODEL)
    assert result.draft["by"] == "fallback" and result.conversations == ["conv-x"]


def test_agent_prompt_names_the_missed_installment():
    runner = FakeRunner(script=[("propose", {**PROPOSE_HARDSHIP, "hardship_category": ""})])
    seen = []
    orig = runner.run
    runner.run = lambda spec, text, handlers: (seen.append(text), orig(spec, text, handlers))[1]
    act(ctx(), runner=runner, model=MODEL)
    assert seen == ["미납 건 rc-loan-1-i1(1회차)를 처리하라."]
    assert "get_case" in str(runner.specs[0].tools) and "send_script" not in str(runner.specs[0].tools)


def test_proposal_has_exact_text_evidence_and_checks():
    p = build_proposal(CASE, DRAFT_HARDSHIP, [], DAY, [{"script_id": "balance_warning", "on": "2026-11-28", "status": "sent"}], str)
    assert p["text"] == render_script("hardship_offer", CASE) and "1,170,000원" in p["text"]
    assert [c["id"] for c in p["checks"]] == ["approved_script", "contact_hours", "weekly_limit", "banned_words", "pii_tokens", "hardship_grounds"]
    assert p["evidence"]["escalation"][0]["script_id"] == "balance_warning"
    assert "[[" not in p["text"] and "{" not in p["text"]


def test_proposal_shape_and_evidence():
    week = [sent(CASE.due_on - timedelta(days=1)), sent(CASE.due_on, "deferred"), sent(CASE.due_on - timedelta(days=8))]
    p = build_proposal(CASE, DRAFT, week, DAY, [], str)
    assert p["status"] == "pending_officer" and p["script_id"] == "missed_payment" and p["hardship"] is None
    assert p["reason"] == "첫 미납이다." and p["by"] == "agent" and p["created_at"] == DAY.isoformat()
    assert "3회차" in p["text"] and "2027-02-08" in p["text"]
    ev = p["evidence"]
    assert (ev["installment_no"], ev["due_on"], ev["amount_krw"], ev["missed_count"], ev["contacts_7d"]) == (
        3, "2027-02-08", 1_170_000, 2, 1)
    assert ev["recent_alert"] == {"severity": "high", "primary_cause": "settlement_hold", "summary": "정산이 보류됐다.",
                                  "check_action": "정산 보류 해제 일정을 확인한다."}
    assert [c["id"] for c in p["checks"]] == ["approved_script", "contact_hours", "weekly_limit", "banned_words", "pii_tokens"]
    assert all(c["ok"] and c["detail"] for c in p["checks"])


def test_proposal_checks_flag_failures():
    p = build_proposal(case(), {**DRAFT_HARDSHIP}, [sent(ON)] * MAX_CONTACTS_PER_7_DAYS, NIGHT, [], str)
    checks = {c["id"]: c["ok"] for c in p["checks"]}
    assert checks == {"approved_script": True, "contact_hours": False, "weekly_limit": False, "banned_words": True,
                      "pii_tokens": True, "hardship_grounds": False}
    assert p["evidence"]["recent_alert"] is None


def test_proposal_masks_evidence_and_old_alert_without_check_action():
    alert = {"severity": "high", "summary": "홍길동 정산 보류"}
    c = RepaymentCase("rc-1", "l", 1, ON, 100_000, 1, alert)
    p = build_proposal(c, DRAFT, [], DAY, [], lambda s: s.replace("홍길동", "[[PERSON_1]]"))
    assert p["evidence"]["recent_alert"]["summary"] == "[[PERSON_1]] 정산 보류"
    assert p["evidence"]["recent_alert"]["check_action"] is None and p["evidence"]["recent_alert"]["primary_cause"] is None


@pytest.mark.parametrize("missed, cause, ok, detail", [
    (2, None, True, "누적 미납 2회"), (1, "settlement_hold", True, "정산 보류"), (1, "none", False, "근거 없음")])
def test_hardship_grounds(missed, cause, ok, detail):
    c = RepaymentCase("rc-1", "l", 1, ON, 100_000, missed, {"severity": "high", "primary_cause": cause,
                                                             "signals": ["loan_stacking"]})
    p = build_proposal(c, DRAFT_HARDSHIP, [], DAY, [], str)
    assert p["checks"][-1]["id"] == "hardship_grounds" and p["checks"][-1]["ok"] is ok
    assert detail in p["checks"][-1]["detail"]


def test_instructions_ground_restructure_like_the_check():
    from agents.collections import INSTRUCTIONS
    assert "loan_stacking" not in INSTRUCTIONS and all(c in INSTRUCTIONS for c in HARDSHIP_CAUSES)


def test_hardship_causes():
    assert HARDSHIP_CAUSES == {"sales_slump", "settlement_hold", "return_spike", "high_cost_borrowing"}


def test_approve_sends_exact_text_with_officer():
    s = settle_proposal(P, "approve", text=None, include_hardship=None, reason="", contacts=[], now=DAY, officer_id="o-1", case=CASE)
    assert s.contact["text"] == P["text"] and s.contact["by"] == "officer" and s.contact["officer_id"] == "o-1"
    assert s.proposal["status"] == "approved" and s.errors == []
    assert s.contact == {"ref": "rc-r-i3", "on": "2027-02-08", "at": DAY.isoformat(), "script_id": "missed_payment",
                         "text": P["text"], "status": "sent", "reason": None, "by": "officer", "officer_id": "o-1"}
    assert s.restructure is None
    assert s.proposal["officer_id"] == "o-1" and s.proposal["decided_at"] == DAY.isoformat()
    assert "sent_text" not in s.proposal and P["status"] == "pending_officer"


def test_approve_with_hardship_creates_restructure_request():
    s = settle_proposal(P_HARDSHIP, "approve", text=None, include_hardship=None, reason="", contacts=[], now=DAY, officer_id="o", case=CASE)
    assert s.restructure == {"request_id": "rs-rc-r-i3", "category": "revenue_shock", "note": "정산 보류로 매출 감소",
                             "status": "pending_officer", "created_at": DAY.isoformat()}


def test_modify_with_banned_word_sends_nothing():
    s = settle_proposal(P, "modify", text="압류 예정입니다", include_hardship=None, reason="", contacts=[], now=DAY, officer_id="o", case=CASE)
    assert s.contact is None and s.errors and s.proposal["status"] == "pending_officer"
    assert s.restructure is None


def test_modify_can_drop_hardship():
    s = settle_proposal(P_HARDSHIP, "modify", text="고객님, 안내드립니다.", include_hardship=False, reason="", contacts=[], now=DAY, officer_id="o", case=CASE)
    assert s.restructure is None and s.contact["text"] == "고객님, 안내드립니다."
    assert s.proposal["status"] == "modified" and s.proposal["sent_text"] == "고객님, 안내드립니다."


def test_modify_keeps_hardship_by_default_and_cannot_add_one():
    kept = settle_proposal(P_HARDSHIP, "modify", text="고객님, 안내드립니다.", include_hardship=None, reason="", contacts=[], now=DAY, officer_id="o", case=CASE)
    assert kept.restructure["category"] == "revenue_shock"
    added = settle_proposal(P, "modify", text="고객님, 안내드립니다.", include_hardship=True, reason="", contacts=[], now=DAY, officer_id="o", case=CASE)
    assert added.errors and added.contact is None and added.proposal["status"] == "pending_officer"
    empty = settle_proposal(P, "modify", text=None, include_hardship=None, reason="", contacts=[], now=DAY, officer_id="o", case=CASE)
    assert empty.errors and empty.contact is None


def test_reject_requires_reason():
    assert settle_proposal(P, "reject", text=None, include_hardship=None, reason="", contacts=[], now=DAY, officer_id="o", case=CASE).errors
    s = settle_proposal(P, "reject", text=None, include_hardship=None, reason="이미 입금 확인", contacts=[], now=DAY, officer_id="o", case=CASE)
    assert s.contact is None and s.proposal["status"] == "rejected"
    assert s.restructure is None and s.proposal["decision_reason"] == "이미 입금 확인"


def test_settled_proposal_cannot_be_decided_again():
    done = settle_proposal(P, "approve", text=None, include_hardship=None, reason="", contacts=[], now=DAY, officer_id="o", case=CASE)
    again = settle_proposal(done.proposal, "approve", text=None, include_hardship=None, reason="", contacts=[], now=DAY, officer_id="o", case=CASE)
    assert again.errors and again.contact is None and again.proposal["status"] == "approved"


def test_officer_send_at_night_is_deferred():
    s = settle_proposal(P, "approve", text=None, include_hardship=None, reason="", contacts=[], now=NIGHT, officer_id="o", case=CASE)
    assert s.contact["status"] == "deferred" and s.contact["reason"] == "contact_hours"


def test_officer_send_respects_weekly_limit():
    full = [sent(CASE.due_on - timedelta(days=i % 7)) for i in range(MAX_CONTACTS_PER_7_DAYS)]
    s = settle_proposal(P, "approve", text=None, include_hardship=None, reason="", contacts=full, now=DAY, officer_id="o", case=CASE)
    assert s.contact["reason"] == "weekly_limit" and s.proposal["status"] == "approved"


def test_scripts_render_without_placeholders_or_banned_words():
    for sid in SCRIPTS:
        assert message_issues(render_script(sid, CASE)) == []
        assert "{" not in render_script(sid, CASE)
    assert {f for s in SCRIPTS.values() for f in re.findall(r"{(\w+)}", s["text"])} <= {"no", "due_on", "amount"}
    for sid in ("missed_payment", "hardship_offer"):
        text = render_script(sid, CASE)
        assert "3회차" in text and "2027-02-08" in text and "1,170,000원" in text
