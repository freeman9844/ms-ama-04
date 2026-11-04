from console import assessment
from console.assessment import SIGNAL_RULES, SIGNALS, assessment_panels
from policy.rules import DEFAULT_RULES_PATH, load_rules

STRONG = {"dscr": 1.8, "cash_buffer_days": 45.0, "revenue_volatility": 0.2, "tax_bank_match_ratio": 0.95,
          "business_age_months": 30, "months_of_history": 12, "avg_monthly_inflow_krw": 4_123_456}


def view(make_doc, features=None, policy="keep", claims=(), **doc_overrides):
    doc = make_doc(**doc_overrides)
    if features is not None:
        doc["underwriting"] = {**doc["underwriting"], "features": features}
    pol = doc["policy"] if policy == "keep" else policy
    return {"doc": doc, "policy": pol, "assessment": doc.get("assessment"),
            "features": (doc.get("underwriting") or {}).get("features") or {}, "claims": list(claims)}


def by_field(panels):
    return {s["field"]: s for s in panels["signals"]}


def test_signal_thresholds_and_limits_match_policy_rules():
    rules = load_rules(DEFAULT_RULES_PATH)
    expected = {c.field: ("knockout", c.threshold) for c in rules.knockouts}
    expected |= {c.field: ("refer", c.threshold) for c in rules.refers}
    assert SIGNAL_RULES == expected
    ops = {c.field: c.op for c in (*rules.knockouts, *rules.refers)}
    assert {field: direction for field, _, direction, _, _ in SIGNALS} == {
        f: "min" if op == "lt" else "max" for f, op in ops.items()}
    assert assessment.PRODUCT_MAX_KRW == rules.product.max_amount_krw
    assert assessment.INFLOW_MULTIPLIER == rules.product.inflow_multiplier


def test_traffic_lights_colour_strength_and_plain_text(make_doc):
    features = {**STRONG, "dscr": 0.9, "cash_buffer_days": 8.0, "revenue_volatility": 0.4}
    s = by_field(assessment_panels(view(make_doc, features)))
    assert [(f, s[f]["tone"], s[f]["strong"]) for f in s] == [
        ("dscr", "red", False), ("cash_buffer_days", "amber", False), ("revenue_volatility", "green", False),
        ("tax_bank_match_ratio", "green", True), ("business_age_months", "green", True),
        ("months_of_history", "green", True)]
    assert s["dscr"]["text"] == "DSCR 0.9 — 벌어들이는 돈이 매달 갚을 돈의 0.9배입니다(기준 1.0 이상)."
    assert s["revenue_volatility"]["text"] == "매출 변동성 0.4 — 월 매출이 평균에서 약 40% 흔들립니다(기준 0.5 이하)."
    assert s["dscr"]["bar"] == {"fill": 72.0, "mark": 80.0}


def test_missing_feature_is_grey_and_not_strength(make_doc):
    s = by_field(assessment_panels(view(make_doc, {"dscr": 2.0})))
    assert s["cash_buffer_days"]["state"] == "unknown" and s["cash_buffer_days"]["bar"] is None
    assert s["dscr"]["strong"] and not s["months_of_history"]["strong"]


def test_strengths_and_risks_cover_violations_reason_codes_and_flags(make_doc):
    features = {**STRONG, "tax_bank_match_ratio": 0.6}
    policy = {"decision": "ineligible", "max_amount_krw": 0, "reason_codes": [
        {"code": "R101", "message": "신고매출과 계좌매출 불일치", "field": "tax_bank_match_ratio"},
        {"code": "R900", "message": "산출 한도가 상품 최소 금액 미만", "field": "max_amount_krw"}]}
    p = assessment_panels(view(make_doc, features, policy, flags=["data_incomplete"]))
    assert len(p["strengths"]) == 5
    assert p["risks"] == [
        "주의(회부 기준): 세금·계좌 일치율 0.6 — 신고 매출의 60%가 계좌 입금으로 확인됩니다(기준 0.7 이상).",
        "R900 산출 한도가 상품 최소 금액 미만", "데이터 부족"]


def test_limit_bars_highlight_the_binding_minimum(make_doc):
    p = assessment_panels(view(make_doc, STRONG, requested_amount_krw=10_000_000))
    limit = p["limit"]
    assert [(b["label"], b["value"], b["color"]) for b in limit["bars"]["bars"]] == [
        ("신청액", 1000, "grey"), ("월평균 입금×1.5", 619, "blue"), ("상품 최대", 1000, "grey")]
    assert limit["binding"] == "월평균 입금×1.5" and limit["computed"] == 6_100_000


def test_capacity_marks_months_short_of_debt_plus_new_installment(make_doc):
    doc_view = view(make_doc, STRONG)
    doc_view["doc"]["underwriting"]["signals"] = {"new_installment_krw": 900_000}
    doc_view["doc"]["underwriting"]["monthly"] = [
        {"month": "2026-08", "revenue": 5_000_000, "operating_out": 3_000_000, "debt_service": 500_000},
        {"month": "2026-09", "revenue": 3_000_000, "operating_out": 2_500_000, "debt_service": 500_000}]
    cap = assessment_panels(doc_view)["capacity"]
    assert [g["bars"][0]["parts"][0]["color"] for g in cap["chart"]["groups"]] == ["green", "red"]
    assert cap["short_months"] == 1 and [pt["value"] for pt in cap["line"]["points"]] == [1_400_000, 1_400_000]
    assert cap["line"]["vmax"] == cap["chart"]["vmax"]


def test_expense_donut_adds_leftover_and_falls_back_to_monthly(make_doc):
    doc_view = view(make_doc, {**STRONG, "avg_monthly_inflow_krw": 10_000_000})
    doc_view["doc"]["underwriting"]["expense_breakdown"] = [
        {"group": "supplier", "label": "거래처·물류", "monthly_avg_krw": 6_000_000},
        {"group": "unclassified", "label": "미분류", "monthly_avg_krw": 0}]
    ex = assessment_panels(doc_view)["expenses"]
    assert ex["source"] == "breakdown" and ex["leftover"] == 4_000_000
    assert [(a["label"], a["value"], a["color"]) for a in ex["donut"]["arcs"]] == [
        ("거래처·물류", 600, "blue"), ("남는 돈", 400, "green")]
    fallback = assessment_panels(view(make_doc, STRONG))["expenses"]
    assert fallback["source"] == "monthly" and fallback["leftover"] == 8_000_000
    assert [a["label"] for a in fallback["donut"]["arcs"]] == ["운영비", "상환", "대표자 인출", "남는 돈"]


def test_overspending_has_no_leftover_slice(make_doc):
    doc_view = view(make_doc, {**STRONG, "avg_monthly_inflow_krw": 1_000_000})
    doc_view["doc"]["underwriting"]["expense_breakdown"] = [
        {"group": "rent", "label": "임대료", "monthly_avg_krw": 1_500_000}]
    ex = assessment_panels(doc_view)["expenses"]
    assert ex["leftover"] == -500_000 and [a["label"] for a in ex["donut"]["arcs"]] == ["임대료"]


def test_summary_kpis_claim_checks_and_empty_document(make_doc):
    claims = [{"evidence": [("M:dscr", 1.8)]}, {"evidence": [("M:dscr", 1.8), ("S:x", None)]}, {"evidence": []}]
    v = view(make_doc, STRONG, claims=claims)
    v["assessment"] = {**v["assessment"], "recommendation": "conditional"}
    p = assessment_panels(v)
    s = p["summary"]
    assert (s["decision"], s["recommendation"], s["review"]) == ("적격", "승인 권고(조건 제안)", "통과 · 1회")
    assert s["gauge_text"] == "67%" and s["gauge_sub"] == "한도 20,000,000원 / 신청 30,000,000원"
    assert p["claims"] == [{"ok": True, "missing": []}, {"ok": False, "missing": ["S:x"]}, {"ok": True, "missing": []}]
    empty = assessment_panels({"doc": make_doc("received", underwriting=None, assessment=None), "policy": None,
                               "assessment": None, "features": {}, "claims": []})
    assert empty["signals"] == [] and empty["capacity"] is None and empty["limit"] is None
    assert empty["expenses"] is None and empty["summary"]["gauge_text"] == "-"
    assert empty["summary"]["recommendation"] == "권고 없음"
