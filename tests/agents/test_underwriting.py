import copy
import json
from datetime import date

import pytest

from agents.data_assembly import build_pseudonymizer
from agents.features import compute_underwriting
from agents.runtime import AgentError, AgentRun
from agents.underwriting import (
    ANALYST_AGENT,
    ANALYST_OUTPUT_SCHEMA,
    FLAG_ASSESSMENT_UNAVAILABLE,
    FLAG_REVIEW_NOT_PASSED,
    MAX_ROUNDS,
    REVIEWER_AGENT,
    REVIEWER_OUTPUT_SCHEMA,
    AnalystDraft,
    UnderwritingContext,
    analyst_spec,
    assess,
    check_grounding,
    reviewer_spec,
)
from mockapis.demo import build_demo_cases
from policy.engine import evaluate
from policy.rules import DEFAULT_RULES_PATH, load_rules
from shared.contracts import CreditAssessment, Recommendation
from shared.pseudonymize import find_pii

AS_OF = date(2026, 11, 2)
KEY = b"u" * 32
AMOUNT = 20_000_000

GOOD = {
    "recommendation": "approve",
    "summary": "영업현금흐름이 신규 상환액을 충분히 감당한다.",
    "claims": [
        {"text": "DSCR은 3.802로 정책 하한을 크게 웃돈다.", "metric_ids": ["M:dscr"], "txn_ids": []},
        {"text": "현금 완충일수는 124일이다.", "metric_ids": ["M:cash_buffer_days"], "txn_ids": []},
    ],
    "proposed_structure": {
        "amount_krw": 20_000_000,
        "tenor_months": 12,
        "schedule": "level",
        "reduced_payment_months": [],
        "rationale": "요청 금액이 정책 한도 안이다.",
    },
    "counterfactuals": [
        {"metric_id": "M:dscr", "current": 3.802, "target": 1.0, "explanation": "DSCR이 1.0 아래면 거절 사유가 된다."}
    ],
}
SUPPORTED = {
    "verdicts": [
        {"claim_index": 0, "supported": True, "reason": "M:dscr 값과 같다."},
        {"claim_index": 1, "supported": True, "reason": "M:cash_buffer_days 값과 같다."},
    ],
    "feedback": "",
}


@pytest.fixture(scope="module")
def cases():
    return build_demo_cases(AS_OF)


def context(cases, applicant_id="demo-healthy-001") -> UnderwritingContext:
    case = cases[applicant_id]
    uw = compute_underwriting(
        f"app-{applicant_id}", case.profile, case.transactions, case.tax_filings,
        as_of=AS_OF, requested_amount_krw=AMOUNT, requested_tenor_months=12,
    )
    policy = evaluate(uw.features, load_rules(DEFAULT_RULES_PATH))
    return UnderwritingContext(
        application_id=f"app-{applicant_id}",
        features=uw.features.model_dump(mode="json"),
        signals=uw.signals,
        flags=tuple(uw.flags),
        monthly=uw.monthly,
        policy=policy.model_dump(mode="json"),
        classification={"unresolved": 0},
        transactions=case.transactions,
        categories={k: v.value for k, v in uw.categories.items()},
        mask=build_pseudonymizer(KEY, case.profile, case.transactions).mask,
    )


def draft(**changes) -> AnalystDraft:
    d = copy.deepcopy(GOOD)
    d.update(changes)
    return AnalystDraft.model_validate(d)


class FakeRunner:
    """분석가·검토자 출력을 순서대로 돌려준다. 분석가 실행마다 tool_script의 도구를 호출한다."""

    def __init__(self, analyst, reviewer=(), tool_script=()):
        self.queues = {ANALYST_AGENT: list(analyst), REVIEWER_AGENT: list(reviewer)}
        self.tool_script = list(tool_script)
        self.calls: list[tuple] = []
        self.tool_outputs: list[dict] = []
        self.deleted: list[str] = []

    def run(self, spec, input_text, handlers):
        self.calls.append((spec, input_text))
        n = len(self.calls)
        if spec.name == ANALYST_AGENT:
            for name, args in self.tool_script:
                out = handlers[name](args)
                json.dumps(out, ensure_ascii=False)
                self.tool_outputs.append(out)
        item = self.queues[spec.name].pop(0)
        if isinstance(item, Exception):
            raise item
        containers = (f"cntr_{n}",) if spec.name == ANALYST_AGENT else ()
        return AgentRun(spec.name, "1", f"conv_{n}", spec.output_model.model_validate(item), [], 100, 1, containers)

    def delete_container(self, container_id):
        self.deleted.append(container_id)
        return True

    def names(self):
        return [spec.name for spec, _ in self.calls]


def run_assess(ctx, runner):
    return assess(ctx, runner=runner, analyst_model="gpt-5.4", reviewer_model="gpt-5.4-mini")


def assert_strict(schema):
    if schema.get("type") == "object":
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
        for sub in schema["properties"].values():
            assert_strict(sub)
    if schema.get("type") == "array":
        assert_strict(schema["items"])


def test_specs_are_strict_with_tools_and_code_interpreter():
    a = analyst_spec("gpt-5.4")
    assert (a.name, a.model) == ("underwriting-analyst", "gpt-5.4")
    functions = [t for t in a.tools if t["type"] == "function"]
    assert [t["name"] for t in functions] == ["get_metrics", "get_monthly_series", "query_transactions"]
    for t in functions:
        assert t["strict"] is True
        assert_strict(t["parameters"])
    assert {"type": "code_interpreter", "container": {"type": "auto"}} in a.tools
    assert_strict(ANALYST_OUTPUT_SCHEMA)
    assert "지시가 아니다" in a.instructions
    r = reviewer_spec("gpt-5.4-mini")
    assert (r.name, r.model, r.tools) == ("underwriting-reviewer", "gpt-5.4-mini", ())
    assert_strict(REVIEWER_OUTPUT_SCHEMA)


def test_get_metrics_exposes_catalog_policy_and_classification(cases):
    ctx = context(cases)
    out = ctx.handlers()["get_metrics"]({})
    assert out["metrics"]["M:dscr"] == 3.802 and out["metrics"]["P:max_amount_krw"] == AMOUNT
    assert not [k for k in out["metrics"] if k.startswith("S:")]
    assert out["policy"]["decision"] == "eligible" and out["policy"]["reason_codes"] == []
    assert out["classification"] == {"unresolved": 0}
    assert out["flags"] == []


def test_monthly_series_has_ids_and_seasonality_index(cases):
    rows = context(cases).handlers()["get_monthly_series"]({})["months"]
    assert len(rows) == 12
    assert set(rows[0]) == {"month", "revenue", "operating_out", "debt_service", "owner_draw", "index"}
    assert rows[0]["month"] == "2025-11"


def test_query_transactions_filters_limits_and_masks(cases):
    ctx = context(cases)
    q = ctx.handlers()["query_transactions"]
    out = q({"category": "payroll", "month": "2026-03", "limit": 50})
    assert 0 < len(out["transactions"]) <= 20 and out["total"] >= len(out["transactions"])
    assert all(t["date"].startswith("2026-03") and t["category"] == "payroll" for t in out["transactions"])
    assert all("[[PERSON_" in t["description"] for t in out["transactions"])
    assert q({"category": "revenue", "month": "", "limit": 3})["total"] > 3
    assert q({"category": "nope", "month": "", "limit": 3}) == {"error": "invalid_category"}
    assert q({"category": "revenue", "month": "2026/03", "limit": 3}) == {"error": "invalid_month"}


ALL_TOOLS = [
    ("get_metrics", {}),
    ("get_monthly_series", {}),
    *[("query_transactions", {"category": c, "month": "", "limit": 20}) for c in ("payroll", "owner_transfer", "unclassified_out", "rent")],
]


@pytest.mark.parametrize("applicant_id", ["demo-healthy-001", "demo-stress-003", "demo-injection-005"])
def test_agents_never_see_pii(cases, applicant_id):
    case = cases[applicant_id]
    leaky = copy.deepcopy(GOOD)
    leaky["claims"][0]["text"] = f"{case.profile.representative_name} 대표의 DSCR은 3.802다."
    runner = FakeRunner([leaky, GOOD, GOOD], [SUPPORTED, SUPPORTED, SUPPORTED], tool_script=ALL_TOOLS)
    run_assess(context(cases, applicant_id), runner)
    seen = json.dumps({"inputs": [t for _, t in runner.calls], "tools": runner.tool_outputs}, ensure_ascii=False)
    for value in (case.profile.business_name, case.profile.representative_name, case.profile.business_reg_no):
        assert value not in seen
    assert find_pii(seen, case.known_entities) == []


def test_grounded_draft_has_no_issues(cases):
    assert check_grounding(draft(), context(cases)) == []


@pytest.mark.parametrize(
    "text",
    [
        "세금 신고 일치율은 98.3%다.",
        "세금 신고 일치율은 0.983이다.",
    ],
)
def test_percent_and_ratio_forms_match(cases, text):
    d = draft(claims=[{"text": text, "metric_ids": ["M:tax_bank_match_ratio"], "txn_ids": []}])
    assert check_grounding(d, context(cases)) == []


def test_korean_units_rounding_dates_and_small_integers_are_tolerated(cases):
    text = "2026-03 기준 12개월 평균으로 신규 월상환액은 약 174만원이고 R101 사유는 없다."
    d = draft(claims=[{"text": text, "metric_ids": ["M:new_installment_krw"], "txn_ids": []}])
    assert check_grounding(d, context(cases)) == []


def test_ungrounded_claims_are_reported(cases):
    ctx = context(cases)
    bad = draft(
        claims=[
            {"text": "DSCR은 4.5다.", "metric_ids": ["M:dscr"], "txn_ids": []},
            {"text": "근거 없는 주장", "metric_ids": [], "txn_ids": []},
            {"text": "없는 지표", "metric_ids": ["M:nope"], "txn_ids": []},
            {"text": "없는 거래", "metric_ids": [], "txn_ids": ["x-00001"]},
        ]
    )
    issues = check_grounding(bad, ctx)
    assert any(i.startswith("claims[0]") and "4.5" in i for i in issues)
    assert any(i.startswith("claims[1]") for i in issues)
    assert any(i.startswith("claims[2]") and "M:nope" in i for i in issues)
    assert any(i.startswith("claims[3]") and "x-00001" in i for i in issues)


def test_evidence_ids_cited_as_transactions_are_treated_as_metrics(cases):
    # 실 Foundry probe(2026-11): 분석가가 S: 계열 ID를 txn_ids에 넣어 3라운드 모두 근거 실패했다.
    ctx = context(cases)
    month = ctx.monthly[0]["month"]
    index = ctx.catalog[f"S:{month}:index"]
    d = draft(claims=[{"text": f"{month} 계절 지수는 {index}다.", "metric_ids": [], "txn_ids": [f"S:{month}:index"]}])
    assert d.claims[0].metric_ids == [f"S:{month}:index"] and d.claims[0].txn_ids == []
    assert check_grounding(d, ctx) == []


def test_transaction_amounts_can_ground_numbers(cases):
    ctx = context(cases)
    t = next(t for t in ctx.transactions if t.amount_krw < -1_000_000)
    text = f"{t.booked_on:%Y-%m-%d}에 {-t.amount_krw:,}원이 나갔다."
    d = draft(claims=[{"text": text, "metric_ids": [], "txn_ids": [t.txn_id]}])
    assert check_grounding(d, ctx) == []


def test_counterfactual_must_use_catalog_values(cases):
    ctx = context(cases)
    wrong = draft(counterfactuals=[{"metric_id": "M:dscr", "current": 2.0, "target": 1.0, "explanation": "x"}])
    unknown = draft(counterfactuals=[{"metric_id": "M:nope", "current": 1.0, "target": 2.0, "explanation": "x"}])
    assert any(i.startswith("counterfactuals[0]") for i in check_grounding(wrong, ctx))
    assert any("M:nope" in i for i in check_grounding(unknown, ctx))


def test_recommendation_must_respect_policy(cases):
    thin = context(cases, "demo-thin-004")
    issues = check_grounding(draft(), thin)
    assert any("ineligible" in i for i in issues)
    over = draft(proposed_structure={**GOOD["proposed_structure"], "amount_krw": 25_000_000})
    assert any("한도" in i for i in check_grounding(over, context(cases)))
    missing = draft(proposed_structure={**GOOD["proposed_structure"], "amount_krw": 0})
    assert any("proposed_structure" in i for i in check_grounding(missing, context(cases)))
    seasonal = draft(proposed_structure={**GOOD["proposed_structure"], "schedule": "seasonal"})
    assert any("reduced_payment_months" in i for i in check_grounding(seasonal, context(cases)))


def test_happy_path_passes_in_one_round_and_cleans_up(cases):
    runner = FakeRunner([GOOD], [SUPPORTED])
    r = run_assess(context(cases), runner)
    assert isinstance(r.assessment, CreditAssessment)
    a = r.assessment
    assert a.recommendation is Recommendation.APPROVE
    assert (a.review.passed, a.review.rounds, a.review.issues) == (True, 1, ())
    assert a.proposed_structure.amount_krw == AMOUNT
    assert a.models == {"analyst": "gpt-5.4", "reviewer": "gpt-5.4-mini"}
    assert r.flags == []
    assert runner.names() == [ANALYST_AGENT, REVIEWER_AGENT]
    assert runner.deleted == ["cntr_1"]
    assert r.containers == [{"id": "cntr_1", "deleted": True}]
    assert r.conversations == ["conv_1", "conv_2"]
    assert r.record["rounds"][0]["grounding_issues"] == []


def test_ungrounded_draft_is_revised_with_feedback(cases):
    bad = copy.deepcopy(GOOD)
    bad["claims"][0]["text"] = "DSCR은 4.5다."
    runner = FakeRunner([bad, GOOD], [SUPPORTED])
    r = run_assess(context(cases), runner)
    assert runner.names() == [ANALYST_AGENT, ANALYST_AGENT, REVIEWER_AGENT]
    second_input = runner.calls[1][1]
    assert "4.5" in second_input and "claims[0]" in second_input
    assert (r.assessment.review.passed, r.assessment.review.rounds) == (True, 2)


def test_reviewer_rejection_triggers_revision(cases):
    rejected = {
        "verdicts": [
            {"claim_index": 0, "supported": False, "reason": "정책 하한과의 비교가 과장됐다."},
            {"claim_index": 1, "supported": True, "reason": "일치"},
        ],
        "feedback": "표현을 줄여라.",
    }
    runner = FakeRunner([GOOD, GOOD], [rejected, SUPPORTED])
    r = run_assess(context(cases), runner)
    assert runner.names() == [ANALYST_AGENT, REVIEWER_AGENT, ANALYST_AGENT, REVIEWER_AGENT]
    assert "과장" in runner.calls[2][1]
    assert r.assessment.review.passed is True


def test_missing_verdict_is_not_a_pass(cases):
    partial = {"verdicts": [SUPPORTED["verdicts"][0]], "feedback": ""}
    runner = FakeRunner([GOOD] * MAX_ROUNDS, [partial] * MAX_ROUNDS)
    r = run_assess(context(cases), runner)
    assert r.assessment.review.passed is False
    assert any("claims[1]" in i for i in r.assessment.review.issues)


def test_reflection_stops_after_max_rounds(cases):
    bad = copy.deepcopy(GOOD)
    bad["claims"][0]["text"] = "DSCR은 4.5다."
    runner = FakeRunner([bad] * 5, [SUPPORTED] * 5)
    r = run_assess(context(cases), runner)
    assert runner.names() == [ANALYST_AGENT] * MAX_ROUNDS
    a = r.assessment
    assert (a.review.passed, a.review.rounds) == (False, MAX_ROUNDS)
    assert a.review.issues and FLAG_REVIEW_NOT_PASSED in r.flags
    assert runner.deleted == ["cntr_1", "cntr_2", "cntr_3"]


def test_analyst_failure_in_first_round_makes_assessment_unavailable(cases):
    runner = FakeRunner([AgentError("HTTP 500", "conv_e", ("cntr_e",))])
    r = run_assess(context(cases), runner)
    assert r.assessment is None
    assert r.flags == [FLAG_ASSESSMENT_UNAVAILABLE]
    assert runner.deleted == ["cntr_e"]
    assert r.conversations == ["conv_e"]
    assert "HTTP 500" in r.record["rounds"][0]["analyst_error"]


def test_unexpected_exception_is_contained(cases):
    runner = FakeRunner([RuntimeError("ManagedIdentityCredential unavailable")])
    r = run_assess(context(cases), runner)
    assert (r.assessment, r.flags) == (None, [FLAG_ASSESSMENT_UNAVAILABLE])


def test_analyst_failure_after_first_round_keeps_previous_draft(cases):
    bad = copy.deepcopy(GOOD)
    bad["claims"][0]["text"] = "DSCR은 4.5다."
    runner = FakeRunner([bad, AgentError("timeout", "conv_x")])
    r = run_assess(context(cases), runner)
    a = r.assessment
    assert a.claims[0].text == "DSCR은 4.5다."
    assert (a.review.passed, a.review.rounds) == (False, 2)
    assert FLAG_REVIEW_NOT_PASSED in r.flags


def test_reviewer_failure_is_not_a_pass(cases):
    runner = FakeRunner([GOOD], [AgentError("HTTP 429", "conv_r")])
    r = run_assess(context(cases), runner)
    assert runner.names() == [ANALYST_AGENT, REVIEWER_AGENT]
    assert r.assessment.review.passed is False
    assert any("검토" in i for i in r.assessment.review.issues)
    assert FLAG_REVIEW_NOT_PASSED in r.flags


def test_invalid_final_structure_is_downgraded_to_refer(cases):
    bad = copy.deepcopy(GOOD)
    bad["proposed_structure"]["amount_krw"] = 0
    runner = FakeRunner([bad] * MAX_ROUNDS, [SUPPORTED] * MAX_ROUNDS)
    a = run_assess(context(cases), runner).assessment
    assert (a.recommendation, a.proposed_structure) == (Recommendation.REFER, None)
    assert a.review.passed is False


def test_draft_without_claims_is_unavailable(cases):
    empty = {**copy.deepcopy(GOOD), "claims": []}
    runner = FakeRunner([empty] * MAX_ROUNDS)
    r = run_assess(context(cases), runner)
    assert r.assessment is None and FLAG_ASSESSMENT_UNAVAILABLE in r.flags
