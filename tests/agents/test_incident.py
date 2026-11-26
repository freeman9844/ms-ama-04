import json
from types import SimpleNamespace

import pytest

from agents.escalation import next_stage
from agents.incident import (
    QUIET_TO_RESOLVE,
    WeekPlan,
    apply_week,
    base_stage,
    new_watch,
    plan_week,
    signal_key,
    watch_week,
)


def sig(code, source, strength="weak"):
    return {"code": code, "source": source, "strength": strength, "metric_ids": []}


MARKET = sig("cancel_rise", "market")
MARKET2 = sig("rating_drop", "market")
SETTLE = sig("orders_dip", "settlement")
BANK_STRONG = sig("due_shortfall", "bank", "strong")


def result(stage, **extra):
    fields = {"agent": "agent", "why_now": "why", "outlook": "out", "adjust_reason": None, **extra}
    return SimpleNamespace(stage=stage, **fields)


def run(watch, week, signals, stage=None, *, high_recheck=False):
    plan = plan_week(watch, signals, high_recheck=high_recheck)
    return plan, apply_week(watch, plan, signals, week=week, replay_id="r1", stage=stage)


def test_base_stage_rules():
    assert base_stage([]) is None
    assert base_stage([MARKET, MARKET2]) == "watch"
    assert base_stage([MARKET, SETTLE]) == "medium"
    assert base_stage([sig("settlement_hold", "settlement", "strong")]) == "medium"
    assert base_stage([sig("account_warning", "market", "strong")]) == "medium"
    assert base_stage([sig("due_shortfall", "bank", "strong")]) == "high"
    assert base_stage([sig("inflow_drop", "bank"), MARKET]) == "medium"


def test_signal_key_is_sorted_source_code():
    assert signal_key([MARKET, BANK_STRONG]) == ["bank:due_shortfall", "market:cancel_rise"]


def test_new_signals_open_incident_and_call():
    watch = new_watch()
    plan, line = run(watch, 3, [MARKET], "watch")
    assert plan == WeekPlan("call", "watch")
    assert watch["open"] == {"id": "in-r1-w03", "opened_week": 3, "stage": "watch",
                             "last_key": ["market:cancel_rise"], "quiet": 0, "alert_id": None}
    assert (line["stage"], line["kind"], line["incident_id"], line["base_stage"]) == ("watch", "call", "in-r1-w03", "watch")
    assert line["stage_up"] is False
    assert watch["stages"] == ["watch"]


def test_same_signals_hold_unless_high_recheck():
    watch = new_watch()
    run(watch, 1, [BANK_STRONG], "high")
    plan, line = run(watch, 2, [BANK_STRONG])
    assert plan.action == "hold"
    assert (line["stage"], line["kind"], line["stage_up"]) == ("high", "hold", False)
    plan, _ = run(watch, 3, [BANK_STRONG], "high", high_recheck=True)
    assert plan.action == "call"

    watch = new_watch()
    run(watch, 1, [MARKET], "medium")
    plan, _ = run(watch, 2, [MARKET], high_recheck=True)
    assert plan.action == "hold"


def test_changed_signal_key_calls_again():
    watch = new_watch()
    run(watch, 1, [MARKET], "watch")
    plan, line = run(watch, 2, [MARKET, SETTLE], "medium")
    assert plan.action == "call"
    assert watch["open"]["last_key"] == ["market:cancel_rise", "settlement:orders_dip"]
    assert line["stage_up"] is True


def test_two_quiet_weeks_resolve_without_call():
    watch = new_watch()
    run(watch, 1, [MARKET], "medium")
    plan, line = run(watch, 2, [])
    assert plan.action == "quiet"
    assert (line["stage"], line["kind"], line["quiet_weeks"]) == ("medium", "quiet", 1)
    assert watch["open"]["quiet"] == 1
    plan, line = run(watch, 3, [])
    assert plan.action == "resolve" and QUIET_TO_RESOLVE == 2
    assert (line["stage"], line["kind"]) == ("resolved", "resolved_quiet")
    assert watch["open"] is None
    assert watch["suppressed"] == []
    assert watch["closed"][0]["close_reason"] == "quiet" and watch["closed"][0]["closed_week"] == 3


def test_signals_in_between_reset_quiet_count():
    watch = new_watch()
    run(watch, 1, [MARKET], "watch")
    run(watch, 2, [])
    run(watch, 3, [MARKET])
    assert watch["open"]["quiet"] == 0
    plan, _ = run(watch, 4, [])
    assert plan.action == "quiet"


def test_agent_resolve_suppresses_until_new_code():
    watch = new_watch()
    run(watch, 1, [MARKET, SETTLE], "medium")
    plan, line = run(watch, 2, [MARKET], "resolve")
    assert plan.action == "call"
    assert (line["stage"], line["kind"], line["stage_up"]) == ("resolved", "resolved_agent", False)
    assert watch["open"] is None
    assert watch["suppressed"] == ["cancel_rise"]
    assert watch["closed"][0]["close_reason"] == "agent"

    plan, line = run(watch, 3, [MARKET])
    assert plan.action == "suppressed"
    assert (line["stage"], line["kind"]) == ("normal", "suppressed")

    plan, line = run(watch, 4, [MARKET, SETTLE], "watch")
    assert plan.action == "call"
    assert watch["suppressed"] == [] and watch["suppress_quiet"] == 0
    assert watch["open"]["id"] == "in-r1-w04"
    assert len(watch["closed"]) == 1


def test_new_incident_resolved_on_first_week_is_opened_and_closed():
    watch = new_watch()
    _, line = run(watch, 5, [MARKET], "resolve")
    assert line["kind"] == "resolved_agent" and line["incident_id"] == "in-r1-w05"
    assert watch["open"] is None
    assert watch["closed"][0]["opened_week"] == 5 and watch["closed"][0]["closed_week"] == 5
    assert watch["suppressed"] == ["cancel_rise"]


def test_suppression_lifts_after_two_quiet_weeks():
    watch = new_watch()
    run(watch, 1, [MARKET], "resolve")
    _, line = run(watch, 2, [])
    assert (line["kind"], line["stage"]) == ("normal", "normal")
    assert watch["suppress_quiet"] == 1 and watch["suppressed"] == ["cancel_rise"]
    run(watch, 3, [])
    assert watch["suppressed"] == [] and watch["suppress_quiet"] == 0
    plan, _ = run(watch, 4, [MARKET], "watch")
    assert plan.action == "call"


def test_suppressed_week_resets_quiet_count():
    watch = new_watch()
    run(watch, 1, [MARKET], "resolve")
    run(watch, 2, [])
    run(watch, 3, [MARKET])
    assert watch["suppress_quiet"] == 0
    run(watch, 4, [])
    assert watch["suppressed"] == ["cancel_rise"]


@pytest.mark.parametrize("first, second, expected", [
    ("watch", "watch", False),
    ("watch", "medium", True),
    ("medium", "medium", False),
    ("high", "medium", False),
    ("medium", "high", True),
])
def test_stage_up_only_when_rising_to_medium_or_high(first, second, expected):
    watch = new_watch()
    run(watch, 1, [MARKET], first)
    _, line = run(watch, 2, [MARKET, SETTLE], second)
    assert line["stage_up"] is expected


def test_new_incident_stage_up_only_for_medium_or_high():
    assert run(new_watch(), 1, [MARKET], "watch")[1]["stage_up"] is False
    assert run(new_watch(), 1, [MARKET, SETTLE], "medium")[1]["stage_up"] is True


def test_agent_stage_is_not_forced_to_base():
    watch = new_watch()
    _, line = run(watch, 1, [BANK_STRONG], "medium")
    assert (line["stage"], line["base_stage"]) == ("medium", "high")
    assert watch["open"]["stage"] == "medium"


def test_missing_agent_stage_falls_back_to_base():
    watch = new_watch()
    _, line = run(watch, 1, [BANK_STRONG])
    assert line["stage"] == "high"


def test_watch_week_calls_investigate_once_per_call():
    watch = new_watch()
    calls = []

    def investigate(base):
        calls.append(base)
        return result("medium", why_now="w", outlook="o", adjust_reason="a")

    def week(n, signals, **kw):
        return watch_week(watch, signals, week=n, replay_id="r1", high_recheck=False,
                          explained=kw.get("explained", False), market=True, investigate=investigate)

    line, res = week(1, [])
    assert (line["kind"], res, calls) == ("normal", None, [])
    line, res = week(2, [MARKET, SETTLE])
    assert calls == ["medium"] and res.stage == "medium"
    assert (line["kind"], line["agent"], line["why_now"], line["outlook"], line["adjust_reason"]) == ("call", "agent", "w", "o", "a")
    assert line["market"] is True and line["signals"] == [MARKET, SETTLE]
    line, res = week(3, [MARKET, SETTLE])
    assert (line["kind"], res, line["agent"], line["why_now"]) == ("hold", None, None, None)
    week(4, [])
    assert len(calls) == 1


def test_watch_week_marks_explained_normal_week():
    watch = new_watch()
    line, _ = watch_week(watch, [], week=1, replay_id="r1", high_recheck=False, explained=True,
                         market=False, investigate=lambda base: pytest.fail("no call"))
    assert (line["kind"], line["stage"]) == ("explained", "normal")


def test_watch_week_agent_resolve_closes_incident():
    watch = new_watch()
    watch_week(watch, [MARKET], week=1, replay_id="r1", high_recheck=False, explained=False,
               market=False, investigate=lambda base: result("watch"))
    line, res = watch_week(watch, [MARKET, SETTLE], week=2, replay_id="r1", high_recheck=False,
                           explained=False, market=False, investigate=lambda base: result("resolve", agent="fallback"))
    assert (line["kind"], line["stage"], line["agent"]) == ("resolved_agent", "resolved", "fallback")
    assert watch["open"] is None and sorted(watch["suppressed"]) == ["cancel_rise", "orders_dip"]


def test_stages_feed_next_stage_and_state_is_json():
    watch = new_watch()
    run(watch, 1, [BANK_STRONG], "high")
    assert next_stage(watch["stages"], {}) == 1
    run(watch, 2, [BANK_STRONG], "high", high_recheck=True)
    assert watch["stages"] == ["high", "high"]
    assert next_stage(watch["stages"], {"stage1": {"status": "sent"}}) == 2
    run(watch, 3, [BANK_STRONG], "medium", high_recheck=True)
    run(watch, 4, [BANK_STRONG], "high", high_recheck=True)
    assert next_stage(watch["stages"], {"stage1": {"status": "sent"}}) is None
    assert json.loads(json.dumps(watch)) == watch
