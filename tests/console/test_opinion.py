import json

from console.opinion import evidence_label, evidence_value, opinion_panel


def view(make_doc, claims=(), run=None, steps=None, **assessment):
    doc = make_doc()
    doc["assessment"] = {**doc["assessment"], **assessment}
    doc["assessment_run"] = run
    if steps is not None:
        doc["steps"] = steps
    return {"doc": doc, "policy": doc["policy"], "assessment": doc["assessment"], "claims": list(claims),
            "assessment_run": run}


def test_no_assessment_has_no_panel(make_doc):
    assert opinion_panel({"doc": make_doc(), "assessment": None}) is None


def test_recommendation_against_policy_is_called_out(make_doc):
    same = opinion_panel(view(make_doc, recommendation="approve"))["recommendation"]
    against = opinion_panel(view(make_doc, recommendation="decline"))["recommendation"]
    assert same["aligned"] and same["policy"] == "적격" and same["meaning"]
    assert not against["aligned"] and "엇갈립니다" in against["relation"] and against["tone"] == "red"


def test_claims_table_labels_values_and_evidence_mix(make_doc):
    claims = [
        {"text": "a", "evidence": [("M:dscr", 1.8), ("M:avg_monthly_inflow_krw", 42_000_000)], "txn_ids": ["t2"]},
        {"text": "b", "evidence": [("S:2026-09:revenue", 40_000_000), ("P:R101:threshold", 1.25),
                                   ("M:unknown", None)], "txn_ids": []},
    ]
    p = opinion_panel(view(make_doc, claims))
    first, second = p["claims"]
    assert [(e["label"], e["value"]) for e in first["evidence"]] == [("DSCR", "1.8"), ("월평균 입금", "42,000,000원")]
    assert first["ok"] and first["sources"] == 3
    assert [e["label"] for e in second["evidence"]] == ["2026-09 매출", "정책 R101 기준값", "unknown"]
    assert not second["ok"] and second["evidence"][2]["value"] == "없음"
    assert p["kpi"]["grounded"] == "1/2" and p["kpi"]["grounded_tone"] == "amber"
    assert p["mix"]["legend"] == [("현금흐름 지표", "blue", "2건"), ("월별 흐름", "teal", "1건"),
                                  ("정책 결과", "purple", "1건"), ("근거 거래", "orange", "1건"),
                                  ("확인 안 됨", "red", "1건")]


def test_evidence_label_and_value_formats():
    assert evidence_label("P:max_amount_krw") == "정책 한도"
    assert evidence_label("S:2026-08:index") == "2026-08 매출 지수"
    assert evidence_value("M:cash_buffer_days", 12.345) == "12.3"
    assert evidence_value("M:dscr", "x") == "없음"


def test_structure_marks_reduced_and_low_months(make_doc):
    doc_view = view(make_doc, proposed_structure={
        "amount_krw": 15_000_000, "tenor_months": 12, "schedule": "seasonal", "reduced_payment_months": [2],
        "rationale": "2월 매출이 낮다"})
    doc_view["doc"]["underwriting"]["monthly"] = [
        {"month": "2026-01", "revenue": 50_000_000}, {"month": "2026-02", "revenue": 20_000_000},
        {"month": "2026-03", "revenue": 50_000_000}, {"month": "2026-04", "revenue": 30_000_000}]
    st = opinion_panel(doc_view)["structure"]
    assert dict(st["rows"])["신청액 대비"] == "50%" and dict(st["rows"])["감액 월"] == "2월"
    assert dict(st["rows"])["상환 방식"] == "계절 감액 상환" and dict(st["rows"])["월 원금(균등 기준)"] == "1,250,000원"
    assert [(b["label"], b["value"]) for b in st["bars"]["bars"]] == [("신청액", 3000), ("정책 한도", 2000),
                                                                       ("제안 금액", 1500)]
    bars = [g["bars"][0]["parts"][0] for g in st["season"]["chart"]["groups"]]
    assert [(b["value"], b["color"]) for b in bars] == [(1.33, "blue"), (0.53, "amber"), (1.33, "blue"), (0.8, "blue")]
    assert [c for _, c in st["season"]["legend"]] == ["blue", "amber", "purple"]


def test_counterfactual_rows_show_change(make_doc):
    p = opinion_panel(view(make_doc, counterfactuals=[
        {"metric_id": "M:cash_buffer_days", "current": 20, "target": 30, "explanation": "버퍼"},
        {"metric_id": "M:revenue_volatility", "current": 0.6, "target": 0.45, "explanation": "변동"}]))
    up, down = p["counterfactuals"]
    assert (up["label"], up["change"], up["pct"], up["direction"]) == ("현금 완충일수", "+10", "+50%", "올리기")
    assert (down["change"], down["pct"], down["direction"]) == ("-0.15", "-25%", "낮추기")


RUN = {"models": {"analyst": "gpt-5.6-sol", "reviewer": "gpt-5.4-mini"}, "rounds": [
    {"round": 1, "analyst": {"total_tokens": 900, "tool_calls": [
        {"name": "get_metrics", "arguments": {"secret": "ARG-BODY"}, "error": None},
        {"name": "query_transactions", "arguments": {}, "error": None}]},
     "grounding_issues": ["claim 1: 근거 목록에 없는 숫자 3.2"]},
    {"round": 2, "analyst": {"total_tokens": 800, "tool_calls": [{"name": "get_metrics", "arguments": {}}]},
     "grounding_issues": [], "reviewer": {"total_tokens": 300}, "review_issues": ["감액 월 근거 부족"]},
    {"round": 3, "analyst": {"total_tokens": 700, "tool_calls": []}, "grounding_issues": [],
     "reviewer": {"total_tokens": 200}, "review_issues": []},
]}


def test_reflection_loop_rounds_tools_and_no_arguments(make_doc):
    loop = opinion_panel(view(make_doc, run=RUN))["loop"]
    assert [(r["n"], r["outcome"], r["tone"], r["tools"], r["tokens"], r["reviewer"]) for r in loop["rows"]] == [
        (1, "근거 보완 요청", "amber", 2, 900, "건너뜀(근거 검사 미통과)"),
        (2, "검토 지적", "amber", 1, 1100, "지적 1건"),
        (3, "통과", "green", 0, 900, "지적 0건")]
    assert [(b["label"], b["value"]) for b in loop["tools"]["bars"]] == [("지표 조회", 2), ("거래 조회", 1)]
    assert [[p["value"] for p in g["bars"][0]["parts"]] for g in loop["issues_chart"]["groups"]] == [
        [1, 0], [0, 1], [0, 0]]
    assert "ARG-BODY" not in json.dumps(loop, ensure_ascii=False, default=str)


def test_failed_analyst_round_shows_error(make_doc):
    run = {"rounds": [{"round": 1, "analyst_error": "HTTP 400 bad"}]}
    row = opinion_panel(view(make_doc, run=run))["loop"]["rows"][0]
    assert (row["outcome"], row["tone"]) == ("분석 실패", "red") and ("오류", "HTTP 400 bad") in row["issues"]


def test_usage_uses_last_assess_step_by_model(make_doc):
    steps = [
        {"step": "assess", "outcome": "failed", "duration_ms": 2000, "usage": []},
        {"step": "assess", "outcome": "ok", "duration_ms": 28500, "usage": [
            {"model": "gpt-5.6-sol", "input_tokens": 1000, "output_tokens": 200, "calls": 2},
            {"model": "gpt-5.4-mini", "input_tokens": 300, "output_tokens": 50, "calls": 1},
            {"model": "gpt-5.6-sol", "input_tokens": 500, "output_tokens": 100, "calls": 1}]},
    ]
    p = opinion_panel(view(make_doc, steps=steps, models={"analyst": "gpt-5.6-sol", "reviewer": "gpt-5.4-mini"}))
    assert p["usage"]["rows"] == [
        {"model": "gpt-5.6-sol", "role": "분석", "calls": 3, "input": 1500, "output": 300},
        {"model": "gpt-5.4-mini", "role": "검토", "calls": 1, "input": 300, "output": 50}]
    assert p["kpi"]["tokens"] == "2,150" and p["kpi"]["tokens_sub"] == "에이전트 단계 28.5초"
