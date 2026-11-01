from datetime import date, datetime, timedelta, timezone

import pytest

from agents.collections import (
    COLLECTIONS_AGENT,
    MAX_CONTACTS_PER_7_DAYS,
    SCRIPTS,
    CollectionsContext,
    CollectionsOutput,
    act,
    fair_check,
)
from agents.runtime import AgentError, AgentRun, ToolCall
from shared.contracts import AlertSignal, EarlyWarningAlert

KST = timezone(timedelta(hours=9))
DAY = datetime(2026, 11, 10, 14, 0, tzinfo=KST)
NIGHT = datetime(2026, 11, 10, 22, 30, tzinfo=KST)
ON = date(2026, 11, 15)
MODEL = "gpt-5.4-mini"


def alert(severity="high", action="contact_borrower", summary="신규 고금리 상환이 나타났다."):
    return EarlyWarningAlert(
        alert_id="ew-loan-1-2",
        loan_id="loan-1",
        week=2,
        as_of=ON,
        severity=severity,
        summary=summary,
        signals=(AlertSignal(kind="loan_stacking", text="고금리 상환", metric_ids=("W:2:high_cost_lender",), txn_ids=()),),
        recommended_action=action,
        triggers=("new_debt",),
        models={"investigator": "gpt-5.4"},
    )


def ctx(a=None, contacts=(), now=DAY, mask=lambda s: s):
    return CollectionsContext(alert=a or alert(), contacts=list(contacts), now=now, mask=mask)


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


def test_night_send_is_deferred_not_sent():
    c = ctx(now=NIGHT)
    assert c.send_script({"script_id": "check_in"}) == {"status": "deferred", "reason": "contact_hours"}


def test_route_hardship_masks_and_bounds_note():
    c = ctx(mask=lambda s: s.replace("홍길동", "[[PERSON_1]]"))
    assert c.route_hardship({"category": "none", "note": "x"}) == {"error": "invalid_category"}
    out = c.route_hardship({"category": "debt_overload", "note": "홍길동 " + "가" * 500})
    assert out == {"status": "pending_officer", "request_id": "rs-ew-loan-1-2"}
    assert c.restructure["note"].startswith("[[PERSON_1]]") and len(c.restructure["note"]) == 300
    assert c.route_hardship({"category": "revenue_shock", "note": ""})["error"] == "already_routed"


def test_tool_outputs_are_masked():
    c = ctx(a=alert(summary="홍길동 매출 급감"), mask=lambda s: s.replace("홍길동", "[[PERSON_1]]"))
    assert "홍길동" not in str(c.get_alert({})) and set(c.list_scripts({})["scripts"][0]) >= {"script_id", "text"}
    assert c.get_contact_history({})["remaining_this_week"] == MAX_CONTACTS_PER_7_DAYS


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


@pytest.mark.parametrize("action", ["monitor", "officer_review"])
def test_no_automatic_contact_without_contact_action(action):
    runner = FakeRunner()
    result = act(ctx(a=alert(severity="medium", action=action)), runner=runner, model=MODEL)
    assert result.decision == "none" and result.contacts == [] and runner.specs == []


@pytest.mark.parametrize(
    "severity, script, decision",
    [("high", "hardship_offer", "contacted"), ("medium", "check_in", "contacted"), ("low", None, "none")],
)
def test_fallback_by_severity(severity, script, decision):
    result = act(ctx(a=alert(severity=severity)), runner=None, model=MODEL)
    assert result.decision == decision and result.restructure is None
    assert [c["script_id"] for c in result.contacts] == ([script] if script else [])
    assert all(c["by"] == "fallback" for c in result.contacts)


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
