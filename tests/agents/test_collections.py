from datetime import date, datetime, timedelta, timezone

import pytest

from agents.collections import (
    COLLECTIONS_AGENT,
    MAX_CONTACTS_PER_7_DAYS,
    SCRIPTS,
    CollectionsContext,
    CollectionsOutput,
    RepaymentCase,
    act,
    fair_check,
    remind,
)
from agents.runtime import AgentError, AgentRun, ToolCall

KST = timezone(timedelta(hours=9))
DAY = datetime(2026, 11, 10, 14, 0, tzinfo=KST)
NIGHT = datetime(2026, 11, 10, 22, 30, tzinfo=KST)
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
        self.output = output or {"decision": "contacted", "reason": "연락했다."}
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


def test_send_once_then_already_sent():
    c = ctx()
    assert c.send_script({"script_id": "check_in"}) == {"status": "sent", "reason": None}
    assert c.send_script({"script_id": "hardship_offer"})["error"] == "already_sent"
    assert c.send_script({"script_id": "nope"})["error"] == "unknown_script"
    assert len(c.added) == 1 and c.added[0]["on"] == ON.isoformat() and c.added[0]["by"] == "agent"
    assert c.added[0]["ref"] == "rc-loan-1-i1"


def test_night_send_is_deferred_not_sent():
    c = ctx(now=NIGHT)
    assert c.send_script({"script_id": "check_in"}) == {"status": "deferred", "reason": "contact_hours"}


def test_route_hardship_masks_and_bounds_note():
    c = ctx(mask=lambda s: s.replace("홍길동", "[[PERSON_1]]"))
    assert c.route_hardship({"category": "none", "note": "x"}) == {"error": "invalid_category"}
    out = c.route_hardship({"category": "debt_overload", "note": "홍길동 " + "가" * 500})
    assert out == {"status": "pending_officer", "request_id": "rs-rc-loan-1-i1"}
    assert c.restructure["note"].startswith("[[PERSON_1]]") and len(c.restructure["note"]) == 300
    assert c.route_hardship({"category": "revenue_shock", "note": ""})["error"] == "already_routed"


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


def test_agent_routes_and_decision_comes_from_tools():
    runner = FakeRunner(
        script=[("send_script", {"script_id": "hardship_offer"}), ("route_hardship", {"category": "debt_overload", "note": "중첩"})],
        output={"decision": "none", "reason": "상담으로 넘겼다."},
    )
    result = act(ctx(), runner=runner, model=MODEL)
    assert result.decision == "routed" and result.restructure["category"] == "debt_overload"
    assert [c["script_id"] for c in result.contacts] == ["hardship_offer"]
    assert result.record["reported"] == "none" and result.conversations == ["conv-c"]
    assert runner.specs[0].name == COLLECTIONS_AGENT and runner.specs[0].model == MODEL


@pytest.mark.parametrize(
    "recent, script",
    [(recent_alert("high"), "hardship_offer"), (recent_alert("medium"), "missed_payment"), (None, "missed_payment")],
)
def test_fallback_always_contacts_on_missed_payment(recent, script):
    result = act(ctx(c=case(recent=recent)), runner=None, model=MODEL)
    assert result.decision == "contacted" and result.restructure is None
    assert [c["script_id"] for c in result.contacts] == [script]
    assert all(c["by"] == "fallback" and c["ref"] == "rc-loan-1-i1" for c in result.contacts)


def test_agent_prompt_names_the_missed_installment():
    runner = FakeRunner(script=[("send_script", {"script_id": "missed_payment"})])
    seen = []
    orig = runner.run
    runner.run = lambda spec, text, handlers: (seen.append(text), orig(spec, text, handlers))[1]
    act(ctx(), runner=runner, model=MODEL)
    assert seen == ["미납 건 rc-loan-1-i1(1회차)를 처리하라."]
    assert "get_case" in str(runner.specs[0].tools) and "get_alert" not in str(runner.specs[0].tools)


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


def test_agent_error_keeps_tool_effects_and_does_not_double_send():
    runner = FakeRunner(script=[("send_script", {"script_id": "check_in"})], error=AgentError("HTTP 500", "conv-x"))
    result = act(ctx(), runner=runner, model=MODEL)
    assert [c["script_id"] for c in result.contacts] == ["check_in"]
    assert result.decision == "contacted" and result.conversations == ["conv-x"]


def test_fallback_respects_fair_rules_at_night():
    result = act(ctx(now=NIGHT), runner=None, model=MODEL)
    assert result.decision == "deferred" and result.contacts[0]["reason"] == "contact_hours"


def test_scripts_have_no_pii_placeholders():
    assert all("{" not in s["text"] for s in SCRIPTS.values())
