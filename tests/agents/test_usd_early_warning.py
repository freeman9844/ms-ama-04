import json
import re
from datetime import date

from agents.early_warning import EwContext
from agents.runtime import AgentRun
from agents.usd_early_warning import (
    UsdStageOutput,
    check_stage,
    fallback_stage,
    investigate_usd,
    source_of_metric,
    usd_fallback_alert,
)
from shared.contracts import USD_V1, EarlyWarningAlert

ROW = {
    "week": 4, "revenue_cents": 120000, "revenue_vs_expected": 0.6, "end_balance_cents": 40000,
    "order_gross_cents": 80000, "expected_orders_cents": 150000, "held_cents": 0,
    "mkt_rating": 4.3, "mkt_grade": "B", "projected_min_balance_cents": -25000,
}
BANK_STRONG = {"code": "projected_shortfall", "source": "bank", "strength": "strong",
               "metric_ids": ["F:4:projected_min_balance_cents"]}
SETTLE_STRONG = {"code": "settlement_hold", "source": "settlement", "strength": "strong", "metric_ids": ["W:4:held_cents"]}
ORDERS_DIP = {"code": "orders_dip", "source": "settlement", "strength": "weak",
              "metric_ids": ["W:4:order_gross_cents", "W:4:expected_orders_cents"]}
RATING_DROP = {"code": "rating_drop", "source": "market", "strength": "weak", "metric_ids": ["W:4:mkt_rating"]}
GRADE_DOWN = {"code": "grade_down", "source": "market", "strength": "weak", "metric_ids": ["W:4:mkt_grade"]}


def usd_ctx(signals, base, rows=None):
    terms = {**USD_V1.model_dump(), "amount_cents": 500000, "tenor_months": 6}
    return EwContext("alert-1", "loan-1", 4, date(2026, 11, 2), "retail", [], rows or [ROW], {}, [], {}, str, terms,
                     signals=signals, base_stage=base, incident_id="in-r1-w04")


def out(stage, why_now="Projected cash fell below zero after orders dipped.",
        outlook="Cash may stay short over the next few weeks.", adjust_reason="", reason="Evidence supports the stage."):
    return UsdStageOutput(stage=stage, why_now=why_now, outlook=outlook, adjust_reason=adjust_reason, reason=reason)


def accept(ctx, stage):
    ctx.accepted = usd_fallback_alert(ctx, stage)
    return ctx


ALERT_ARGS = {
    "summary": "Projected cash falls below zero before the next payment.",
    "check_action": "Verify pending receipts and upcoming payments.",
    "recommended_action": "officer_review",
    "signals": [{"kind": "projected_shortfall", "text": "Projected balance falls below zero.",
                 "metric_ids": ["F:4:projected_min_balance_cents"], "txn_ids": []}],
    "primary_cause": "",
    "secondary_causes": [],
}


class ScriptedStage:
    def __init__(self, output, alert_args=None):
        self.output = output
        self.alert_args = alert_args
        self.specs = []
        self.alert_reply = None

    def run(self, spec, input_text, handlers):
        self.specs.append((spec, input_text))
        if self.alert_args is not None:
            self.alert_reply = handlers["raise_alert"](json.loads(json.dumps(self.alert_args)))
        return AgentRun(spec.name, "1", "conv-1", spec.output_model.model_validate(self.output), [], 1, 1)

    def delete_container(self, container_id):
        return True


def test_source_of_metric():
    assert source_of_metric("W:4:mkt_rating") == "market"
    assert source_of_metric("F:4:projected_min_balance_cents") == "bank"
    assert source_of_metric("W:4:revenue_vs_expected") == "bank"
    assert source_of_metric("W:4:end_balance_cents") == "bank"
    assert source_of_metric("W:4:held_cents") == "settlement"
    assert source_of_metric("B:cash_buffer_days") is None


def test_check_stage_rejects_high_without_strong_bank():
    ctx = accept(usd_ctx([SETTLE_STRONG, RATING_DROP], "medium"), "high")
    issues = check_stage(out("high", adjust_reason="Market rating fell to 4.3 in W:4:mkt_rating."), ctx)
    assert any("strong bank" in i for i in issues)
    assert check_stage(out("medium"), accept(usd_ctx([SETTLE_STRONG, RATING_DROP], "medium"), "medium")) == []


def test_check_stage_rejects_more_than_one_step():
    ctx = accept(usd_ctx([ORDERS_DIP], "watch"), "high")
    issues = check_stage(out("high", adjust_reason="Market rating fell to 4.3 in W:4:mkt_rating."), ctx)
    assert any("one step" in i for i in issues)


def test_check_stage_rejects_watch_or_resolve_with_strong_signal():
    for stage in ("watch", "resolve"):
        ctx = usd_ctx([SETTLE_STRONG], "medium")
        issues = check_stage(out(stage, adjust_reason="Market rating held at 4.3 in W:4:mkt_rating."), ctx)
        assert any("strong signal" in i for i in issues)


def test_adjusted_stage_needs_reason_citing_other_source():
    ctx = accept(usd_ctx([BANK_STRONG, RATING_DROP], "high"), "medium")
    ok = "Market rating held at 4.3 in W:4:mkt_rating, so the bank change looks temporary."
    assert check_stage(out("medium", adjust_reason=ok), ctx) == []
    same_source = "The forecast in F:4:projected_min_balance_cents looks temporary."
    assert any("other data source" in i for i in check_stage(out("medium", adjust_reason=same_source), ctx))
    assert any("adjust_reason" in i for i in check_stage(out("medium", adjust_reason=""), ctx))


def test_ungrounded_number_in_why_now_rejected():
    ctx = accept(usd_ctx([SETTLE_STRONG, RATING_DROP], "medium"), "medium")
    assert check_stage(out("medium", why_now="Orders fell 37% before receipts moved."), ctx)
    assert check_stage(out("medium", why_now="Orders fell before receipts moved in week 4."), ctx) == []


def test_alert_severity_must_match_stage():
    ctx = usd_ctx([BANK_STRONG, RATING_DROP], "high")
    runner = ScriptedStage(out("high").model_dump(), {**ALERT_ARGS, "severity": "medium"})
    result = investigate_usd(ctx, runner=runner, model="gpt")
    assert runner.alert_reply["accepted"] is True
    assert result.agent == "fallback" and result.stage == "high" and result.decision == "fallback_alert"
    assert result.alert.severity.value == "high" and result.conversations == ["conv-1"]


def test_agent_high_alert_gets_stage_fields():
    ctx = usd_ctx([BANK_STRONG, RATING_DROP], "high")
    runner = ScriptedStage(out("high").model_dump(), {**ALERT_ARGS, "severity": "high"})
    result = investigate_usd(ctx, runner=runner, model="gpt")
    assert (result.agent, result.stage, result.decision) == ("agent", "high", "high")
    alert = result.alert
    assert alert.incident_id == "in-r1-w04" and alert.why_now == result.why_now and alert.outlook == result.outlook
    assert alert.models == {"investigator": "gpt"} and [s.source for s in alert.signals] == ["bank"]
    assert result.adjust_reason is None
    assert runner.specs[0][0].output_model is UsdStageOutput


def test_fallback_uses_base_stage_and_number_free_text():
    for signals, base in (([BANK_STRONG], "high"), ([ORDERS_DIP, RATING_DROP], "medium"), ([ORDERS_DIP], "watch")):
        result = investigate_usd(usd_ctx(signals, base), runner=None, model="gpt")
        assert result.stage == base and result.agent == "fallback"
        assert result.why_now and result.outlook and not re.search(r"\d", result.why_now + result.outlook)
        if result.alert:
            assert not re.search(r"\d", result.alert.summary + "".join(s.text for s in result.alert.signals))
    assert fallback_stage(usd_ctx([ORDERS_DIP], "watch"), "agent_error").decision == "fallback_stage"


def test_market_only_medium_fallback_alert_has_signals():
    ctx = usd_ctx([ORDERS_DIP, GRADE_DOWN], "medium")
    result = fallback_stage(ctx, "agents_disabled")
    alert = result.alert
    assert result.decision == "fallback_alert" and alert.severity.value == "medium"
    assert [(s.kind.value, s.source) for s in alert.signals] == [("inflow_drop", "settlement"), ("other", "market")]
    assert alert.incident_id == "in-r1-w04" and alert.why_now == result.why_now


def test_watch_result_has_no_alert():
    ctx = usd_ctx([ORDERS_DIP], "watch")
    result = investigate_usd(ctx, runner=ScriptedStage(out("watch").model_dump()), model="gpt")
    assert result.alert is None and (result.stage, result.agent, result.decision) == ("watch", "agent", "watch")


def test_old_alert_documents_still_validate():
    doc = {
        "alert_id": "a", "loan_id": "l", "week": 3, "as_of": "2026-11-20", "severity": "medium", "summary": "s",
        "signals": [{"kind": "inflow_drop", "text": "t", "metric_ids": ["W:3:revenue_vs_expected"], "txn_ids": []}],
        "recommended_action": "officer_review", "triggers": ["inflow_drop"], "models": {"investigator": "agent"},
    }
    alert = EarlyWarningAlert.model_validate(doc)
    assert alert.incident_id is None and alert.why_now is None and alert.signals[0].source is None
