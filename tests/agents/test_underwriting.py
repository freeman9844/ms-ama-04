import copy
import json
from dataclasses import replace
from datetime import date

import pytest
from pydantic import ValidationError

from agents.data_assembly import build_pseudonymizer
from agents.features import compute_underwriting
from agents.runtime import AgentError, AgentRun
from agents.underwriting import (
    ANALYST_AGENT,
    ANALYST_INSTRUCTIONS,
    ANALYST_OUTPUT_SCHEMA,
    ANALYST_TOOLS,
    FLAG_ASSESSMENT_UNAVAILABLE,
    FLAG_REVIEW_NOT_PASSED,
    MAX_ROUNDS,
    REVIEWER_AGENT,
    REVIEWER_INSTRUCTIONS,
    REVIEWER_OUTPUT_SCHEMA,
    AnalystDraft,
    UnderwritingContext,
    _reviewer_input,
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
AMOUNT = 3_300_000

GOOD = {
    "recommendation": "approve",
    "summary": "영업현금흐름이 신규 상환액을 충분히 감당한다.",
    "claims": [
        {"text": "DSCR은 4.074로 정책 하한을 크게 웃돈다.", "metric_ids": ["M:dscr"], "txn_ids": []},
        {"text": "현금 완충일수는 23.3일이다.", "metric_ids": ["M:cash_buffer_days"], "txn_ids": []},
    ],
    "counterfactuals": [
        {"metric_id": "M:dscr", "current": 4.074, "target": 1.0, "explanation": "DSCR이 1.0 아래면 거절 사유가 된다."}
    ],
    "officer_checks": [],
}
SUPPORTED = {
    "summary_supported": True,
    "verdicts": [
        {"claim_index": 0, "supported": True, "reason": "M:dscr 값과 같다."},
        {"claim_index": 1, "supported": True, "reason": "M:cash_buffer_days 값과 같다."},
    ],
    "feedback": "",
    "check_verdicts": [],
}


@pytest.fixture(scope="module")
def cases():
    return build_demo_cases(AS_OF)


def context(cases, applicant_id="seller-dailylook-01") -> UnderwritingContext:
    case = cases[applicant_id]
    uw = compute_underwriting(
        f"app-{applicant_id}", case.profile, case.transactions, case.tax_filings,
        as_of=AS_OF, requested_amount_krw=AMOUNT, requested_tenor_months=12,
        pg_settlements=case.pg_settlements,
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
        settlement=uw.pg_ledger,
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
    assert [t["name"] for t in functions] == [
        "get_metrics", "get_monthly_series", "get_settlement_summary", "query_transactions",
    ]
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
    assert out["metrics"]["M:dscr"] == 4.074 and out["metrics"]["P:max_amount_krw"] == AMOUNT
    assert not [k for k in out["metrics"] if k.startswith("S:")]
    assert out["policy"]["decision"] == "eligible" and out["policy"]["reason_codes"] == []
    assert out["classification"] == {"unresolved": 0}
    assert out["flags"] == []


def test_get_metrics_lists_out_of_range_watch_metrics(cases):
    ctx = context(cases, "seller-mangwon-02")
    out = ctx.handlers()["get_metrics"]({})
    ratio = out["metrics"]["M:tax_bank_match_ratio"]
    assert ratio < 0.7 and ctx.policy["decision"] == "eligible"
    assert out["watch"] == [{"metric_id": "M:tax_bank_match_ratio", "observed": ratio}]
    assert context(cases).handlers()["get_metrics"]({})["watch"] == []


def test_missing_tax_filings_are_not_a_watch_signal(cases):
    ctx = context(cases, "seller-mangwon-02")
    no_filings = replace(ctx, features={**ctx.features, "tax_bank_match_ratio": 0.0})
    assert no_filings.handlers()["get_metrics"]({})["watch"] == []


def test_instructions_ask_to_cite_watch_metrics_as_risk():
    assert "watch" in ANALYST_INSTRUCTIONS and "M:tax_bank_match_ratio" in ANALYST_INSTRUCTIONS
    assert "watch" in REVIEWER_INSTRUCTIONS


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


def test_settlement_summary_tool_returns_ids_and_platforms(cases):
    ctx = context(cases)
    out = ctx.handlers()["get_settlement_summary"]({})
    assert out["available"] is True
    assert out["metric_ids"]["M:pg_reconciliation_ratio"] == ctx.features["pg_reconciliation_ratio"]
    assert out["metric_ids"]["M:pg_top_platform_share"] == ctx.features["pg_top_platform_share"]
    assert out["metric_ids"]["M:pg_hhi"] == ctx.signals["pg_hhi"]
    assert out["platforms"][0].keys() == {"platform", "share", "lag_days"}
    assert "unmatched" not in out  # 개별 미입금 금액은 근거 ID가 없어 주지 않는다


def test_settlement_summary_unavailable(cases):
    ctx = replace(context(cases), settlement=None)
    assert ctx.handlers()["get_settlement_summary"]({}) == {"available": False}
    assert any(t.get("name") == "get_settlement_summary" for t in ANALYST_TOOLS)


def test_settlement_summary_exposes_new_metrics_without_details(cases):
    out = context(cases).handlers()["get_settlement_summary"]({})
    assert "M:pg_cash_conversion_days" in out["metric_ids"] and "M:pg_unmatched_count" in out["metric_ids"]
    assert out["platforms"][0].keys() == {"platform", "share", "lag_days"}
    assert {"fee_rate", "offset_rate"} <= out["monthly_take_rate"][0].keys()
    # 개별 미입금·선지급 세부(unmatched[]·advance 객체)는 주지 않는다. 집계 지표 ID(M:pg_advance_*)는 허용한다.
    assert "unmatched" not in out
    assert not any("advance" in item for lst in (out["platforms"], out["monthly_take_rate"]) for item in lst)
    assigned = context(cases, "seller-mugshop-21").handlers()["get_settlement_summary"]({})
    assert "M:pg_advance_share" in assigned["metric_ids"] and "M:pg_advance_suspected_count" in assigned["metric_ids"]


CHECK = {"text": "정산금 미입금 원인과 선정산 계약 여부를 확인한다.", "metric_ids": ["M:pg_reconciliation_ratio"]}


def test_analyst_schema_requires_officer_checks():
    assert "officer_checks" in ANALYST_OUTPUT_SCHEMA["required"]
    item = ANALYST_OUTPUT_SCHEMA["properties"]["officer_checks"]["items"]
    assert item["required"] == ["text", "metric_ids"] and item["additionalProperties"] is False
    assert "officer_checks" in ANALYST_INSTRUCTIONS and "정산·셀러 건전성" in ANALYST_INSTRUCTIONS
    assert "6~8문장" in ANALYST_INSTRUCTIONS
    assert "거래 상대방 이름 대신" in ANALYST_INSTRUCTIONS and "일반 명사" in ANALYST_INSTRUCTIONS


def test_grounded_check_has_no_issues(cases):
    assert check_grounding(draft(officer_checks=[CHECK]), context(cases)) == []


@pytest.mark.parametrize("check, issue", [
    ({"text": "확인", "metric_ids": []}, "officer_checks[0]: 근거 ID가 없다"),
    ({"text": "확인", "metric_ids": ["M:nope"]}, "officer_checks[0]: 알 수 없는 근거 ID"),
    ({"text": "확인", "metric_ids": ["M:dscr"]}, "officer_checks[0]: 정산·셀러 근거(M:pg_*·M:mkt_*·P:R107·P:R108·P:R003·P:R109·P:R110·P:S001)가 없다"),
    ({"text": "입금 비율 55%를 확인한다.", "metric_ids": ["M:pg_reconciliation_ratio"]},
     "officer_checks[0]: 인용 근거와 맞지 않는 수치"),
])
def test_check_grounding_reports_bad_checks(cases, check, issue):
    issues = check_grounding(draft(officer_checks=[check]), context(cases))
    assert any(i.startswith(issue) for i in issues), issues


def test_check_grounding_limits_count_and_requires_settlement(cases):
    assert "officer_checks: 3개를 넘는다" in check_grounding(draft(officer_checks=[CHECK] * 4), context(cases))
    ctx = replace(context(cases), settlement={"available": False})
    assert "officer_checks: 정산 자료가 없는데 확인 사항이 있다" in check_grounding(draft(officer_checks=[CHECK]), ctx)


def seller_only_context(cases):
    ctx = context(cases)
    return replace(
        ctx, features={**ctx.features, "mkt_rating": 4.7},
        settlement={"available": False}, marketplace={"available": True},
    )


def test_officer_check_with_only_seller_evidence_ok_without_pg(cases):
    check = {"text": "평점 하락 원인을 확인한다.", "metric_ids": ["M:mkt_rating"]}
    assert check_grounding(draft(officer_checks=[check]), seller_only_context(cases)) == []


def test_officer_check_without_any_data_still_rejected(cases):
    ctx = replace(context(cases), settlement={"available": False}, marketplace=None)
    assert "officer_checks: 정산 자료가 없는데 확인 사항이 있다" in check_grounding(draft(officer_checks=[CHECK]), ctx)
    ctx = replace(context(cases), settlement=None, marketplace={"available": False})
    assert "officer_checks: 정산 자료가 없는데 확인 사항이 있다" in check_grounding(draft(officer_checks=[CHECK]), ctx)


def test_officer_check_with_seller_evidence_is_not_flagged_as_missing_settlement_evidence(cases):
    check = {"text": "확인", "metric_ids": ["M:dscr"]}
    issues = check_grounding(draft(officer_checks=[check]), seller_only_context(cases))
    assert any("정산·셀러 근거(M:pg_*·M:mkt_*·P:R107·P:R108·P:R003·P:R109·P:R110·P:S001)가 없다" in i for i in issues)


def test_missing_s001_check_is_not_grounding_error(cases):
    ctx = seller_only_context(cases)
    reason = {"code": "S001", "message": "판매정지", "field": "mkt_suspension_days", "observed": 24, "threshold": 18}
    ctx = replace(ctx, policy={**ctx.policy, "reason_codes": [*ctx.policy.get("reason_codes", ()), reason]})
    check = {"text": "평점 하락 원인을 확인한다.", "metric_ids": ["M:mkt_rating"]}
    assert check_grounding(draft(officer_checks=[check]), ctx) == []


def test_instructions_cover_seller_evidence():
    assert "M:mkt_" in ANALYST_INSTRUCTIONS and "P:S001" in ANALYST_INSTRUCTIONS
    assert "정산·셀러 건전성" in ANALYST_INSTRUCTIONS


WITH_CHECK = GOOD | {"officer_checks": [CHECK]}
CHECK_OK = SUPPORTED | {"check_verdicts": [{"check_index": 0, "supported": True, "reason": "근거와 맞다"}]}


def test_reviewer_sees_checks_with_evidence(cases):
    ctx = context(cases)
    payload = json.loads(_reviewer_input(ctx, draft(officer_checks=[CHECK])).split("\n", 1)[1])
    assert payload["officer_checks"] == [{"check_index": 0, "text": CHECK["text"],
                                          "evidence": {"M:pg_reconciliation_ratio": ctx.catalog["M:pg_reconciliation_ratio"]}}]
    assert "check_verdicts" in REVIEWER_OUTPUT_SCHEMA["required"]


def test_reviewer_sees_market_evidence_even_when_analyst_omits_it(cases):
    ctx = seller_only_context(cases)
    ctx = replace(ctx, features={**ctx.features, "mkt_review_count": 0})
    payload = json.loads(_reviewer_input(ctx, draft()).split("\n", 1)[1])
    assert payload["market_evidence"] == {"M:mkt_rating": 4.7, "M:mkt_review_count": 0}
    absent = json.loads(_reviewer_input(context(cases), draft()).split("\n", 1)[1])
    assert absent["market_evidence"] == {}


def test_summary_rejection_alone_returns_reason_to_analyst(cases):
    reason = "정산 예정금을 이미 입금된 현금으로 보았다."
    rejected = SUPPORTED | {"summary_supported": False, "feedback": reason}
    runner = FakeRunner([GOOD, GOOD], [rejected, SUPPORTED | {"summary_supported": True}])
    r = run_assess(context(cases), runner)
    assert (r.assessment.review.passed, r.assessment.review.rounds) == (True, 2)
    assert reason in runner.calls[2][1]


def test_reviewer_can_compare_uncited_reconciliation_with_expected_settlement(cases):
    ctx = context(cases)
    for ratio in (0.9, 1.0):
        changed = replace(ctx, features={**ctx.features, "pg_reconciliation_ratio": ratio})
        payload = json.loads(_reviewer_input(changed, draft()).split("\n", 1)[1])
        assert payload["cash_evidence"]["M:pg_reconciliation_ratio"] == ratio
        assert payload["cash_evidence"]["M:dscr"] == ctx.catalog["M:dscr"]


def test_reviewer_gets_full_seasonal_index_when_claim_cites_index(cases):
    ctx = context(cases)
    month = ctx.monthly[0]["month"]
    claim = {"text": "계절 지수가 가장 낮은 달이다.", "metric_ids": [f"S:{month}:index"], "txn_ids": []}
    payload = json.loads(_reviewer_input(ctx, draft(claims=[claim])).split("\n", 1)[1])
    series = payload["seasonal_index"]
    assert "1.0" in series["basis"]
    assert series["months"] == {r["month"]: ctx.catalog[f"S:{r['month']}:index"] for r in ctx.monthly}
    plain = json.loads(_reviewer_input(ctx, draft()).split("\n", 1)[1])
    assert "seasonal_index" not in plain


def test_reviewer_gets_watch_when_claim_cites_watch_metric(cases):
    ctx = context(cases, "seller-mangwon-02")
    claim = {"text": "세금·계좌 일치율이 낮아 신고 매출 확인이 필요하다.", "metric_ids": ["M:tax_bank_match_ratio"], "txn_ids": []}
    payload = json.loads(_reviewer_input(ctx, draft(claims=[claim])).split("\n", 1)[1])
    assert payload["watch"] == ctx.watch() and payload["watch"]
    assert "watch" not in json.loads(_reviewer_input(ctx, draft()).split("\n", 1)[1])


def test_supported_check_is_kept_with_verdict(cases):
    result = run_assess(context(cases), FakeRunner([WITH_CHECK], [CHECK_OK]))
    assert result.assessment.review.passed and result.assessment.officer_checks[0].supported is True


def test_rejected_or_missing_check_verdict_triggers_revision(cases):
    bad = SUPPORTED | {"check_verdicts": [{"check_index": 0, "supported": False, "reason": "근거 밖 추론"}]}
    runner = FakeRunner([WITH_CHECK] * 3, [bad, SUPPORTED, CHECK_OK])
    result = run_assess(context(cases), runner)
    rounds = result.record["rounds"]
    assert "officer_checks[0]: 검토자 지적 - 근거 밖 추론" in rounds[0]["review_issues"]
    assert "officer_checks[0]: 검토 결과 없음" in rounds[1]["review_issues"]
    assert result.assessment.review.passed and result.assessment.officer_checks[0].supported is True


def test_unsupported_check_prevents_publication_after_max_rounds(cases):
    bad = SUPPORTED | {"check_verdicts": [{"check_index": 0, "supported": False, "reason": "과장"}]}
    result = run_assess(context(cases), FakeRunner([WITH_CHECK] * 3, [bad] * 3))
    assert result.assessment is None
    assert "과장" in str(result.record["rounds"][-1]["review_issues"])


def test_invalid_checks_are_not_published_unreviewed(cases):
    too_many = GOOD | {"officer_checks": [CHECK] * 4}
    result = run_assess(context(cases), FakeRunner([too_many] * MAX_ROUNDS))
    assert result.assessment is None and FLAG_REVIEW_NOT_PASSED in result.flags


def test_check_verdicts_come_from_review_of_final_draft(cases):
    ungrounded = WITH_CHECK | {"claims": [{"text": "DSCR은 9.9다.", "metric_ids": ["M:dscr"], "txn_ids": []}]}
    claim_bad = CHECK_OK | {"verdicts": [{"claim_index": 0, "supported": False, "reason": "x"},
                                         {"claim_index": 1, "supported": True, "reason": "x"}]}
    result = assess(context(cases), runner=FakeRunner([WITH_CHECK, ungrounded], [claim_bad]),
                    analyst_model="a", reviewer_model="r", max_rounds=2)
    assert result.assessment is None


ALL_TOOLS = [
    ("get_metrics", {}),
    ("get_monthly_series", {}),
    ("get_settlement_summary", {}),
    *[("query_transactions", {"category": c, "month": "", "limit": 20}) for c in ("payroll", "owner_transfer", "unclassified_out", "rent")],
]


@pytest.mark.parametrize("applicant_id", ["seller-dailylook-01", "seller-glowlab-03", "seller-candle-05"])
def test_agents_never_see_pii(cases, applicant_id):
    case = cases[applicant_id]
    leaky = copy.deepcopy(GOOD)
    leaky["claims"][0]["text"] = f"{case.profile.representative_name} 대표의 DSCR은 4.074다."
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
        "세금 신고 일치율은 97.2%다.",
        "세금 신고 일치율은 0.972이다.",
    ],
)
def test_percent_and_ratio_forms_match(cases, text):
    d = draft(claims=[{"text": text, "metric_ids": ["M:tax_bank_match_ratio"], "txn_ids": []}])
    assert check_grounding(d, context(cases)) == []


def test_korean_units_rounding_dates_and_small_integers_are_tolerated(cases):
    text = "2026-03 기준 12개월 평균으로 신규 월상환액은 약 29만원이고 R101 사유는 없다."
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
    t = next(t for t in ctx.transactions if t.amount_krw < -400_000)
    text = f"{t.booked_on:%Y-%m-%d}에 {-t.amount_krw:,}원이 나갔다."
    d = draft(claims=[{"text": text, "metric_ids": [], "txn_ids": [t.txn_id]}])
    assert check_grounding(d, ctx) == []


def test_ungrounded_summary_numbers_are_reported(cases):
    bad = draft(summary="DSCR은 4.5로 충분하다. 월 유입은 1,234만원이다.")
    issues = [i for i in check_grounding(bad, context(cases)) if i.startswith("summary")]
    assert len(issues) == 1 and "4.5" in issues[0] and "1,234만" in issues[0]


def test_summary_can_state_factual_amounts(cases):
    ctx = context(cases)
    d = draft(summary="신청액은 330만 원으로 정책 한도와 같다. 신청 전액 승인을 권고한다.")
    assert check_grounding(d, ctx) == []


def test_assessing_the_exact_requested_amount_is_not_an_alternative_offer(cases):
    ctx = context(cases)
    ctx = replace(ctx, features={**ctx.features, "requested_amount_krw": 5_000_000},
                  policy={**ctx.policy, "max_amount_krw": 5_000_000})
    text = "신청액 500만 원을 승인할 근거가 부족하다."
    assert check_grounding(draft(summary=text, recommendation="refer"), ctx) == []
    assert check_grounding(draft(summary=f"{text} 따라서 0원을 제안한다.", recommendation="refer"), ctx)
    assert check_grounding(draft(summary="신청액 0원을 승인할 근거가 있다."), ctx)


def test_counterfactual_must_use_catalog_values(cases):
    ctx = context(cases)
    wrong = draft(counterfactuals=[{"metric_id": "M:dscr", "current": 2.0, "target": 1.0, "explanation": "x"}])
    unknown = draft(counterfactuals=[{"metric_id": "M:nope", "current": 1.0, "target": 2.0, "explanation": "x"}])
    assert any(i.startswith("counterfactuals[0]") for i in check_grounding(wrong, ctx))
    assert any("M:nope" in i for i in check_grounding(unknown, ctx))


@pytest.mark.parametrize("decision, cap, allowed", [
    ("eligible", AMOUNT, {"approve", "refer", "decline"}),
    ("refer", AMOUNT, {"refer"}),
    ("eligible", AMOUNT - 1, {"decline"}),
    ("refer", AMOUNT - 1, {"decline"}),
    ("ineligible", 0, {"decline"}),
])
def test_full_request_policy_bounds(cases, decision, cap, allowed):
    ctx = context(cases)
    ctx = replace(ctx, policy={**ctx.policy, "decision": decision, "max_amount_krw": cap})
    for rec in ("approve", "refer", "decline"):
        issues = check_grounding(draft(recommendation=rec), ctx)
        assert bool(issues) == (rec not in allowed), (rec, issues)


def test_unrecognized_policy_never_publishes_an_opinion(cases):
    ctx = context(cases)
    ctx = replace(ctx, policy={**ctx.policy, "decision": "unknown"})
    assert run_assess(ctx, FakeRunner([GOOD] * MAX_ROUNDS, [SUPPORTED])).assessment is None


@pytest.mark.parametrize("changes", [
    {"recommendation": "conditional"},
    {"proposed_structure": None},
])
def test_generation_rejects_retired_contract_fields(changes):
    with pytest.raises(ValidationError):
        draft(**changes)


@pytest.mark.parametrize("changes, field", [
    ({"summary": "0원 제안"}, "summary"),
    ({"summary": "Recommend lending up to the policy limit instead."}, "summary"),
    ({"summary": "제안 금액은 0원이다."}, "summary"),
    ({"summary": "Recommend a six-month repayment schedule instead."}, "summary"),
    ({"summary": "6개월 균등 상환을 제안한다."}, "summary"),
    ({"summary": "The suggested loan amount is ₩3,300,000."}, "summary"),
    ({"claims": [{"text": "330만원으로 감액을 권고한다.", "metric_ids": ["P:max_amount_krw"], "txn_ids": []}]}, "claims[0]"),
    ({"officer_checks": [{**CHECK, "text": "Offer KRW 0 instead."}]}, "officer_checks[0]"),
    ({"counterfactuals": [{**GOOD["counterfactuals"][0], "explanation": "Recommend a loan of 3 million won."}]},
     "counterfactuals[0]"),
    ({"counterfactuals": [{"metric_id": "M:requested_amount_krw", "current": AMOUNT, "target": 2000000,
                           "explanation": "Reapply on this basis."}]}, "counterfactuals[0]"),
])
def test_alternative_amounts_cannot_hide_in_grounded_text(cases, changes, field):
    assert any(i.startswith(field) for i in check_grounding(draft(**changes), context(cases)))


def test_reviewer_sees_full_request_and_counterfactual_text(cases):
    payload = json.loads(_reviewer_input(context(cases), draft()).split("\n", 1)[1])
    assert payload["requested_amount_krw"] == AMOUNT
    assert payload["counterfactuals"][0]["explanation"] == GOOD["counterfactuals"][0]["explanation"]
    assert "proposed_structure" not in payload


def test_happy_path_passes_in_one_round_and_cleans_up(cases):
    runner = FakeRunner([GOOD], [SUPPORTED])
    r = run_assess(context(cases), runner)
    assert isinstance(r.assessment, CreditAssessment)
    a = r.assessment
    assert a.recommendation is Recommendation.APPROVE
    assert (a.review.passed, a.review.rounds, a.review.issues) == (True, 1, ())
    assert a.schema_version == "2" and "proposed_structure" not in a.model_dump()
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


def test_invalid_publication_contract_does_not_silently_truncate_reviewed_text(cases):
    bad = copy.deepcopy(GOOD)
    bad["claims"][0]["text"] = "가" * 401
    r = run_assess(context(cases), FakeRunner([bad, GOOD], [SUPPORTED, SUPPORTED]))
    assert r.assessment.review.rounds == 2 and r.assessment.claims[0].text == GOOD["claims"][0]["text"]


def test_reviewer_rejection_triggers_revision(cases):
    rejected = {
        "summary_supported": True,
        "verdicts": [
            {"claim_index": 0, "supported": False, "reason": "정책 하한과의 비교가 과장됐다."},
            {"claim_index": 1, "supported": True, "reason": "일치"},
        ],
        "feedback": "표현을 줄여라.",
        "check_verdicts": [],
    }
    runner = FakeRunner([GOOD, GOOD], [rejected, SUPPORTED])
    r = run_assess(context(cases), runner)
    assert runner.names() == [ANALYST_AGENT, REVIEWER_AGENT, ANALYST_AGENT, REVIEWER_AGENT]
    assert "과장" in runner.calls[2][1]
    assert r.assessment.review.passed is True


def test_missing_verdict_is_not_a_pass(cases):
    partial = SUPPORTED | {"verdicts": [SUPPORTED["verdicts"][0]]}
    runner = FakeRunner([GOOD] * MAX_ROUNDS, [partial] * MAX_ROUNDS)
    r = run_assess(context(cases), runner)
    assert r.assessment is None
    assert any("claims[1]" in i for i in r.record["rounds"][-1]["review_issues"])


def test_conflicting_verdicts_do_not_publish_rejected_claim(cases):
    duplicate = SUPPORTED | {"verdicts": [
        {"claim_index": 0, "supported": False, "reason": "감액 제안"}, *SUPPORTED["verdicts"],
    ]}
    r = run_assess(context(cases), FakeRunner([GOOD] * MAX_ROUNDS, [duplicate] * MAX_ROUNDS))
    assert r.assessment is None


def test_reflection_stops_after_max_rounds(cases):
    bad = copy.deepcopy(GOOD)
    bad["claims"][0]["text"] = "DSCR은 4.5다."
    runner = FakeRunner([bad] * 5, [SUPPORTED] * 5)
    r = run_assess(context(cases), runner)
    assert runner.names() == [ANALYST_AGENT] * MAX_ROUNDS
    assert r.assessment is None and len(r.record["rounds"]) == MAX_ROUNDS
    assert FLAG_REVIEW_NOT_PASSED in r.flags
    assert r.record.get("error")  # journey reports a generic reason, not the rejected text
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


def test_analyst_failure_after_first_round_does_not_publish_previous_draft(cases):
    bad = copy.deepcopy(GOOD)
    bad["claims"][0]["text"] = "DSCR은 4.5다."
    runner = FakeRunner([bad, AgentError("timeout", "conv_x")])
    r = run_assess(context(cases), runner)
    assert r.assessment is None
    assert len(r.record["rounds"]) == 2
    assert FLAG_REVIEW_NOT_PASSED in r.flags


def test_reviewer_failure_is_not_a_pass(cases):
    runner = FakeRunner([GOOD], [AgentError("HTTP 429", "conv_r")])
    r = run_assess(context(cases), runner)
    assert runner.names() == [ANALYST_AGENT, REVIEWER_AGENT]
    assert r.assessment is None
    assert "HTTP 429" in r.record["rounds"][-1]["reviewer_error"]
    assert FLAG_REVIEW_NOT_PASSED in r.flags


def test_hard_policy_failure_precedes_evidence_referral_on_retry(cases):
    ctx = context(cases)
    ctx = replace(ctx, policy={**ctx.policy, "decision": "refer", "max_amount_krw": AMOUNT - 1})
    runner = FakeRunner([GOOD | {"recommendation": "refer"}, GOOD | {"recommendation": "decline"}], [SUPPORTED])
    a = run_assess(ctx, runner).assessment
    assert a.recommendation == "decline" and a.review.rounds == 2


def test_draft_without_claims_is_unavailable(cases):
    empty = {**copy.deepcopy(GOOD), "claims": []}
    runner = FakeRunner([empty] * MAX_ROUNDS)
    r = run_assess(context(cases), runner)
    assert r.assessment is None and FLAG_ASSESSMENT_UNAVAILABLE in r.flags
