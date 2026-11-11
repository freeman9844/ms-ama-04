import json
from datetime import date

import pytest

from agents.data_assembly import build_pseudonymizer
from agents.early_warning import (
    EW_AGENT,
    INSTRUCTIONS,
    TOOLS,
    EwContext,
    EwOutput,
    fallback_alert,
    investigate,
)
from agents.features import compute_underwriting
from agents.ledger_monitor import ledger_baseline, observe as ledger_observe, platform_weeks
from agents.monitoring import Baseline, categorize, detect, settlement_for, weekly_series
from agents.runtime import AgentError, AgentRun, ToolCall
from mockapis.demo import build_demo_cases
from mockapis.postloan import FlowScenario, LoanTerms, generate_postloan
from shared.contracts import AlertCause
from shared.pseudonymize import find_pii

AS_OF = date(2026, 11, 2)
KEY = b"e" * 32
MODEL = "gpt-5.4"


@pytest.fixture(scope="module")
def cafe():
    return build_demo_cases(AS_OF)["seller-dailylook-01"]


@pytest.fixture(scope="module")
def baseline(cafe):
    uw = compute_underwriting(
        "app-1", cafe.profile, cafe.transactions, cafe.tax_filings,
        as_of=AS_OF, requested_amount_krw=50_000_000, requested_tenor_months=12,
    )
    return Baseline.from_underwriting({"features": uw.features.model_dump(mode="json"), "monthly": uw.monthly})


@pytest.fixture
def ctx(cafe, baseline):
    return make_ctx(cafe, baseline, week=6)


def make_ctx(cafe, baseline, week):
    flow = generate_postloan(cafe, FlowScenario.SALES_SLUMP, AS_OF, 7)
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
    assert [r["week"] for r in series["weeks"]] == [1, 2, 3, 4, 5, 6]
    assert all(t.booked_on <= ctx.as_of for t in ctx.by_id.values())
    assert ctx.query_transactions({"category": "high_cost_lender", "week": 7}) == {"error": "invalid_week"}
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
    assert result.alert.triggers == tuple(ctx.triggers) and result.alert.week == 6
    assert len(result.record["rejected"]) == 1 and result.conversations == ["conv-1"]
    assert runner.specs[0][0].name == EW_AGENT


@pytest.mark.parametrize(
    "change, needle",
    [
        ({"signals": []}, "signals"),
        ({"signals": [{"kind": "loan_stacking", "text": "근거", "metric_ids": ["W:9:revenue"], "txn_ids": []}]}, "W:9:revenue"),
        ({"signals": [{"kind": "loan_stacking", "text": "근거", "metric_ids": [], "txn_ids": []}]}, "근거 ID"),
        ({"severity": "high", "recommended_action": "monitor"}, "형식 오류"),
        ({"summary": "잔고가 999,999,999,999원 줄었다."}, "summary"),
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


@pytest.fixture(scope="module")
def shortfall_ctx(cafe, baseline):
    """4주 잔고 전망이 처음 0원 아래로 내려간 주차의 맥락."""
    fb = Baseline.from_dict(
        {**baseline.to_dict(), "installment_krw": 287_000,
         "settlement": settlement_for(cafe.transactions, {}, AS_OF, cafe.profile.industry)}
    )
    for week in range(1, 14):
        ctx = make_ctx(cafe, fb, week)
        if "projected_shortfall" in ctx.triggers:
            return ctx
    raise AssertionError("SALES_SLUMP 13주 안에 잔고 부족 전망이 없다")


def test_alert_can_cite_projected_balance(shortfall_ctx):
    ctx, w = shortfall_ctx, shortfall_ctx.week
    low = int(ctx.catalog[f"F:{w}:projected_min_balance"])
    assert low < 0 and ctx.catalog[f"F:{w}:shortfall_date"] == ctx.rows[-1]["shortfall_date"]
    signal = {"kind": "projected_shortfall", "metric_ids": [f"F:{w}:projected_min_balance"], "txn_ids": []}
    ok = {**stacking_alert(ctx), "signals": [{**signal, "text": f"4주 안에 잔고가 {low:,}원까지 내려간다."}]}
    bad = {**stacking_alert(ctx), "signals": [{**signal, "text": "4주 안에 잔고가 -123,456,789원까지 내려간다."}]}
    assert ctx.raise_alert(bad)["accepted"] is False
    assert ctx.raise_alert(ok)["accepted"] is True
    ctx.accepted = None


def test_fallback_cites_projected_balance(shortfall_ctx):
    alert = investigate(shortfall_ctx, runner=None, model=MODEL).alert
    signal = next(s for s in alert.signals if s.kind == "projected_shortfall")
    assert signal.metric_ids == (f"F:{shortfall_ctx.week}:projected_min_balance",)


def test_runner_none_uses_fallback(ctx):
    result = investigate(ctx, runner=None, model=MODEL)
    assert result.decision == "fallback_alert" and result.record == {"error": "agents_disabled"}


# --- 원인 분류·정산 원장 도구(Task 6) ---

GEARSTORE_WEEK = 5
GEARSTORE_TERMS = LoanTerms(amount_krw=5_000_000, installment_krw=852_885)


@pytest.fixture(scope="module")
def gearstore():
    return build_demo_cases(AS_OF)["seller-gearstore-06"]


@pytest.fixture(scope="module")
def gearstore_prepared(gearstore):
    """gearstore SETTLEMENT_HOLD 흐름을 5주차까지 observe로 돌린 재료. 후보 = [SETTLEMENT_HOLD w3, HIGH_COST_BORROWING w5]."""
    uw = compute_underwriting(
        "app-2", gearstore.profile, gearstore.transactions, gearstore.tax_filings,
        as_of=AS_OF, requested_amount_krw=GEARSTORE_TERMS.amount_krw, requested_tenor_months=6,
    )
    base = Baseline.from_underwriting(
        {"features": uw.features.model_dump(mode="json"), "monthly": uw.monthly},
        installment_krw=GEARSTORE_TERMS.installment_krw,
    )
    flow = generate_postloan(gearstore, FlowScenario.SETTLEMENT_HOLD, AS_OF, 7, loan=GEARSTORE_TERMS)
    pg = flow.pg_flow()
    lb = ledger_baseline(pg, flow.start, gearstore.profile.industry, gearstore.transactions)
    cats = categorize(flow.transactions, {})
    obs = ledger_observe(flow.transactions, cats, flow.start, GEARSTORE_WEEK, base, pg, lb)
    pg_weeks = platform_weeks(pg, flow.start, 1, GEARSTORE_WEEK)
    return flow, base, cats, obs, pg_weeks, lb


@pytest.fixture
def ctx_with_candidates(gearstore, gearstore_prepared):
    flow, base, cats, obs, pg_weeks, lb = gearstore_prepared
    return EwContext(
        alert_id=f"ew-loan-2-{GEARSTORE_WEEK}",
        loan_id="loan-2",
        week=GEARSTORE_WEEK,
        start=flow.start,
        industry=gearstore.profile.industry,
        triggers=obs.triggers,
        rows=obs.rows,
        baseline=base,
        transactions=flow.transactions,
        categories=cats,
        mask=build_pseudonymizer(KEY, gearstore.profile, flow.transactions).mask,
        terms={"amount_krw": GEARSTORE_TERMS.amount_krw, "tenor_months": 6},
        candidates=obs.candidates,
        pg_weeks=pg_weeks,
        ledger_baseline=lb,
    )


@pytest.fixture(scope="module")
def stable_prepared(cafe, baseline):
    """STABLE 셀러 흐름, pg=None. 원인 후보 없음."""
    flow = generate_postloan(cafe, FlowScenario.STABLE, AS_OF, 7)
    cats = categorize(flow.transactions, {})
    obs = ledger_observe(flow.transactions, cats, flow.start, 6, baseline, None, None)
    return flow, cats, obs


@pytest.fixture
def ctx_without_candidates(cafe, baseline, stable_prepared):
    flow, cats, obs = stable_prepared
    return EwContext(
        alert_id="ew-loan-3-6",
        loan_id="loan-3",
        week=6,
        start=flow.start,
        industry=cafe.profile.industry,
        triggers=obs.triggers,
        rows=obs.rows,
        baseline=baseline,
        transactions=flow.transactions,
        categories=cats,
        mask=build_pseudonymizer(KEY, cafe.profile, flow.transactions).mask,
        terms={"amount_krw": 50_000_000, "tenor_months": 36},
        candidates=obs.candidates,
        pg_weeks=None,
        ledger_baseline=None,
    )


def no_cause_alert(ctx, **overrides):
    """원인 주장이 없는 기본 유효 경보(근거: 그 주 말 잔고). 원장 유무와 무관하게 항상 존재하는 필드를 쓴다."""
    week = ctx.week
    value = int(ctx.catalog[f"W:{week}:end_balance"])
    args = {
        "severity": "medium",
        "summary": "이번 주 잔고를 확인했다.",
        "recommended_action": "monitor",
        "signals": [
            {
                "kind": "other",
                "text": f"{week}주차 말 잔고는 {value:,}원이다.",
                "metric_ids": [f"W:{week}:end_balance"],
                "txn_ids": [],
            }
        ],
    }
    args.update(overrides)
    return args


def loan_proceeds_alert(ctx, **overrides):
    """대출금 입금만 인용하는 신호(정산 보류 근거는 없다)."""
    week = ctx.week
    value = int(ctx.catalog[f"W:{week}:loan_proceeds"])
    args = {
        "severity": "medium",
        "summary": "이번 주 대출금 입금이 있었다.",
        "recommended_action": "monitor",
        "signals": [
            {
                "kind": "loan_stacking",
                "text": f"{week}주차에 대출금 {value:,}원이 입금됐다.",
                "metric_ids": [f"W:{week}:loan_proceeds"],
                "txn_ids": [],
            }
        ],
    }
    args.update(overrides)
    return args


def held_alert(ctx, week=3, **overrides):
    """정산 보류액을 인용하는 신호."""
    value = int(ctx.catalog[f"W:{week}:held"])
    args = {
        "severity": "medium",
        "summary": "정산 보류액이 발생했다.",
        "recommended_action": "monitor",
        "signals": [
            {
                "kind": "settlement_hold",
                "text": f"{week}주차 정산 보류액이 {value:,}원이다.",
                "metric_ids": [f"W:{week}:held"],
                "txn_ids": [],
            }
        ],
    }
    args.update(overrides)
    return args


def test_raise_alert_requires_candidate_cause(ctx_with_candidates):
    """후보 = [SETTLEMENT_HOLD w3, HIGH_COST_BORROWING w5]. sales_slump은 후보가 아니다."""
    r = ctx_with_candidates.raise_alert({**no_cause_alert(ctx_with_candidates), "primary_cause": "sales_slump"})
    assert r["accepted"] is False and any("후보" in i for i in r["issues"])
    assert ctx_with_candidates.accepted is None


def test_primary_cause_must_cite_its_metric(ctx_with_candidates):
    bad = {**loan_proceeds_alert(ctx_with_candidates), "primary_cause": "settlement_hold"}
    r = ctx_with_candidates.raise_alert(bad)
    assert r["accepted"] is False and any("지표" in i for i in r["issues"])

    ok = {
        **held_alert(ctx_with_candidates),
        "primary_cause": "settlement_hold",
        "secondary_causes": ["high_cost_borrowing"],
    }
    r = ctx_with_candidates.raise_alert(ok)
    assert r["accepted"] is True
    assert ctx_with_candidates.accepted.primary_cause is AlertCause.SETTLEMENT_HOLD
    assert ctx_with_candidates.accepted.secondary_causes == (AlertCause.HIGH_COST_BORROWING,)


def test_secondary_cause_must_be_candidate_and_exclude_primary(ctx_with_candidates):
    args = {**held_alert(ctx_with_candidates), "primary_cause": "settlement_hold", "secondary_causes": ["sales_slump"]}
    r = ctx_with_candidates.raise_alert(args)
    assert r["accepted"] is False and any("후보" in i for i in r["issues"])

    args = {**held_alert(ctx_with_candidates), "primary_cause": "settlement_hold", "secondary_causes": ["settlement_hold"]}
    r = ctx_with_candidates.raise_alert(args)
    assert r["accepted"] is False and any("겹칠 수 없다" in i for i in r["issues"])


def test_no_candidates_means_no_primary(ctx_without_candidates):
    args = no_cause_alert(ctx_without_candidates)
    assert ctx_without_candidates.raise_alert({**args, "primary_cause": "sales_slump"})["accepted"] is False
    assert ctx_without_candidates.raise_alert(args)["accepted"] is True
    assert ctx_without_candidates.accepted.primary_cause is None


def test_fallback_uses_earliest_candidate(ctx_with_candidates):
    a = fallback_alert(ctx_with_candidates, "timeout")
    assert a.primary_cause is AlertCause.SETTLEMENT_HOLD
    assert a.secondary_causes == (AlertCause.HIGH_COST_BORROWING,)


def test_settlement_ledger_tool(ctx_with_candidates):
    out = ctx_with_candidates.get_settlement_ledger({"from_week": 3, "to_week": 4})
    assert out["available"] and {w["week"] for w in out["weeks"]} == {3, 4}
    assert ctx_with_candidates.get_settlement_ledger({"from_week": 0, "to_week": 99}) == {"error": "invalid_week"}


def test_settlement_ledger_tool_without_data(ctx_without_candidates):
    out = ctx_without_candidates.get_settlement_ledger({"from_week": 1, "to_week": 1})
    assert out["available"] is False


def test_get_baseline_lists_cause_candidates(ctx_with_candidates):
    out = ctx_with_candidates.get_baseline({})
    assert out["cause_candidates"] == [c.to_dict() for c in ctx_with_candidates.candidates]


def test_tool_schema_lists_causes():
    raise_alert = next(t for t in TOOLS if t["name"] == "raise_alert")
    assert "primary_cause" in json.dumps(raise_alert) and "get_settlement_ledger" in {t["name"] for t in TOOLS}
    assert "차입은 대개 결과다" in INSTRUCTIONS
