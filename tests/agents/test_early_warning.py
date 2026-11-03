import json
from datetime import date

import pytest

from agents.data_assembly import build_pseudonymizer
from agents.early_warning import (
    EW_AGENT,
    EwContext,
    EwOutput,
    investigate,
)
from agents.features import compute_underwriting
from agents.monitoring import Baseline, categorize, detect, weekly_series
from agents.runtime import AgentError, AgentRun, ToolCall
from mockapis.demo import build_demo_cases
from mockapis.postloan import FlowScenario, generate_postloan
from shared.pseudonymize import find_pii

AS_OF = date(2026, 11, 2)
KEY = b"e" * 32
MODEL = "gpt-5.4"


@pytest.fixture(scope="module")
def cafe():
    return build_demo_cases(AS_OF)["demo-healthy-001"]


@pytest.fixture(scope="module")
def baseline(cafe):
    uw = compute_underwriting(
        "app-1", cafe.profile, cafe.transactions, cafe.tax_filings,
        as_of=AS_OF, requested_amount_krw=50_000_000, requested_tenor_months=36,
    )
    return Baseline.from_underwriting({"features": uw.features.model_dump(mode="json"), "monthly": uw.monthly})


@pytest.fixture
def ctx(cafe, baseline):
    return make_ctx(cafe, baseline, week=2)


def make_ctx(cafe, baseline, week):
    flow = generate_postloan(cafe, FlowScenario.STACKING, AS_OF, 7)
    cats = categorize(flow.transactions, {})
    rows = weekly_series(flow.transactions, cats, flow.start, week, baseline)
    return EwContext(
        alert_id=f"ew-loan-1-{week}",
        loan_id="loan-1",
        week=week,
        start=flow.start,
        industry=cafe.profile.industry,
        triggers=detect(rows, baseline),
        rows=rows,
        baseline=baseline,
        transactions=flow.transactions,
        categories=cats,
        mask=build_pseudonymizer(KEY, cafe.profile, flow.transactions).mask,
        terms={"amount_krw": 50_000_000, "tenor_months": 36},
    )


def stacking_alert(ctx, text=None):
    paid = int(ctx.catalog[f"W:{ctx.week}:high_cost_lender"])
    return {
        "severity": "high",
        "summary": "실행 직후 고금리 대부 상환이 시작됐다.",
        "recommended_action": "contact_borrower",
        "signals": [
            {
                "kind": "loan_stacking",
                "text": text or f"{ctx.week}주차 고금리 상환 {paid:,}원이 새로 나타났다.",
                "metric_ids": [f"W:{ctx.week}:high_cost_lender"],
                "txn_ids": [],
            }
        ],
    }


class FakeRunner:
    def __init__(self, script=(), output=None, error=None):
        self.script = list(script)
        self.output = output or {"decision": "no_alert", "reason": "계절성으로 설명된다."}
        self.error = error
        self.outputs: list[dict] = []
        self.specs = []

    def run(self, spec, input_text, handlers):
        self.specs.append((spec, input_text))
        calls = []
        for name, args in self.script:
            out = handlers[name](json.loads(json.dumps(args)))
            json.dumps(out, ensure_ascii=False)
            self.outputs.append(out)
            calls.append(ToolCall(name, args, out))
        if self.error:
            raise self.error
        return AgentRun(spec.name, "1", "conv-1", EwOutput.model_validate(self.output), calls, 10, 1)

    def delete_container(self, container_id):
        return True


def test_ctx_hides_future_weeks(ctx):
    assert ctx.triggers and "new_debt" in ctx.triggers
    series = ctx.get_weekly_series({})
    assert [r["week"] for r in series["weeks"]] == [1, 2]
    assert all(t.booked_on <= ctx.as_of for t in ctx.by_id.values())
    assert ctx.query_transactions({"category": "high_cost_lender", "week": 3}) == {"error": "invalid_week"}
    assert ctx.query_transactions({"category": "nope", "week": 0}) == {"error": "invalid_category"}
    rows = ctx.query_transactions({"category": "high_cost_lender", "week": 0, "limit": 99})
    assert rows["total"] >= 1 and all(r["txn_id"] in ctx.by_id for r in rows["transactions"])


def test_tool_outputs_are_masked(cafe, baseline):
    ctx = make_ctx(cafe, baseline, week=13)
    outs = [ctx.get_weekly_series({}), ctx.get_baseline({}), ctx.get_peer_cohort({})]
    outs += [ctx.query_transactions({"category": c, "week": 0}) for c in ("payroll", "unclassified_in", "revenue")]
    assert find_pii(json.dumps(outs, ensure_ascii=False)) == []


def test_rejected_then_accepted_first_wins(ctx):
    runner = FakeRunner(
        script=[
            ("raise_alert", stacking_alert(ctx, text="고금리 상환 999,999,999원이 나타났다.")),
            ("raise_alert", stacking_alert(ctx)),
            ("raise_alert", stacking_alert(ctx)),
        ],
        output={"decision": "alert", "reason": "대출 중첩 신호다."},
    )
    result = investigate(ctx, runner=runner, model=MODEL)
    first, second, third = runner.outputs
    assert first["accepted"] is False and "999999999" in " ".join(first["issues"]).replace(",", "")
    assert second == {"accepted": True, "alert_id": ctx.alert_id}
    assert third["accepted"] is False
    assert result.decision == "alert" and result.alert.severity == "high"
    assert result.alert.models == {"investigator": MODEL}
    assert result.alert.triggers == tuple(ctx.triggers) and result.alert.week == 2
    assert len(result.record["rejected"]) == 1 and result.conversations == ["conv-1"]
    assert runner.specs[0][0].name == EW_AGENT


@pytest.mark.parametrize(
    "change, needle",
    [
        ({"signals": []}, "signals"),
        ({"signals": [{"kind": "loan_stacking", "text": "근거", "metric_ids": ["W:9:revenue"], "txn_ids": []}]}, "W:9:revenue"),
        ({"signals": [{"kind": "loan_stacking", "text": "근거", "metric_ids": [], "txn_ids": []}]}, "근거 ID"),
        ({"severity": "high", "recommended_action": "monitor"}, "형식 오류"),
        ({"summary": "잔고가 999,999,999원 줄었다."}, "summary"),
    ],
)
def test_raise_alert_rejects(ctx, change, needle):
    out = ctx.raise_alert({**stacking_alert(ctx), **change})
    assert out["accepted"] is False and needle in " ".join(out["issues"])
    assert ctx.accepted is None


def test_evidence_ids_in_txn_ids_are_moved(ctx):
    args = stacking_alert(ctx)
    args["signals"][0]["txn_ids"], args["signals"][0]["metric_ids"] = args["signals"][0]["metric_ids"], []
    assert ctx.raise_alert(args)["accepted"] is True
    assert ctx.accepted.signals[0].metric_ids == (f"W:{ctx.week}:high_cost_lender",)


def test_no_alert(ctx):
    runner = FakeRunner(script=[("get_baseline", {}), ("get_peer_cohort", {})])
    result = investigate(ctx, runner=runner, model=MODEL)
    assert result.alert is None and result.decision == "no_alert"
    assert result.reason == "계절성으로 설명된다." and result.record["model"] == MODEL


def test_agent_error_falls_back_to_detector_alert(ctx):
    runner = FakeRunner(error=AgentError("HTTP 500", "conv-x"))
    result = investigate(ctx, runner=runner, model=MODEL)
    assert result.decision == "fallback_alert" and result.conversations == ["conv-x"]
    alert = result.alert
    assert alert.severity == "medium" and alert.recommended_action == "officer_review"
    assert alert.models == {"investigator": "detector_only"}
    assert {s.kind for s in alert.signals} >= {"loan_stacking"}


def test_error_after_accepted_alert_keeps_it(ctx):
    runner = FakeRunner(script=[("raise_alert", stacking_alert(ctx))], error=AgentError("timeout", "conv-y"))
    result = investigate(ctx, runner=runner, model=MODEL)
    assert result.decision == "alert" and result.alert.severity == "high"


def test_runner_none_uses_fallback(ctx):
    result = investigate(ctx, runner=None, model=MODEL)
    assert result.decision == "fallback_alert" and result.record == {"error": "agents_disabled"}
