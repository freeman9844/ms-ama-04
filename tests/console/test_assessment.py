import pytest

from console import i18n
from console.assessment import SIGNAL_RULES, SIGNALS, assessment_panels
from console.views import detail_view
from policy.rules import DEFAULT_RULES_PATH, load_rules
from shared.contracts import USD_V1

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


def test_signal_thresholds_match_policy_rules():
    rules = load_rules(DEFAULT_RULES_PATH)
    expected = {c.field: ("knockout", c.threshold) for c in rules.knockouts}
    expected |= {c.field: ("refer", c.threshold) for c in rules.refers}
    expected |= {"tax_bank_match_ratio": ("watch", 0.7)}  # v3에서 R101 삭제, 참고 지표로만 표시
    assert SIGNAL_RULES == expected
    ops = {c.field: c.op for c in (*rules.knockouts, *rules.refers)} | {"tax_bank_match_ratio": "lt"}
    assert {field: direction for field, _, direction, _, _ in SIGNALS} == {
        f: "min" if op == "lt" else "max" for f, op in ops.items()}


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
        "주의(참고 지표): 세금·계좌 일치율 0.6 — 신고 매출의 60%가 계좌 입금으로 확인됩니다(참고 지표, 0.7 이상 권장).",
        "R900 산출 한도가 상품 최소 금액 미만", "데이터 부족"]


def test_tax_match_is_watch_not_refer(make_doc):
    s = by_field(assessment_panels(view(make_doc, features={**STRONG, "tax_bank_match_ratio": 0.5})))
    assert s["tax_bank_match_ratio"]["state"] == "watch" and s["tax_bank_match_ratio"]["tone"] == "amber"


@pytest.mark.parametrize("ratio,fetched,records,expected", [
    (0.0, False, 0, "unknown"), (0.0, True, 2, "unknown"),
    (0.6, True, 0, "unknown"), (0.6, False, 0, "unknown"),
    (0.6, True, 2, "watch"), (0.7, True, 2, "pass"),
])
def test_tax_signal_requires_recorded_evidence(key, ratio, fetched, records, expected):
    doc = usd_doc()
    doc["assembly"] = {"sources": {"tax": {"fetched": fetched, "records": records}}}
    doc["underwriting"]["features"]["tax_bank_match_ratio"] = ratio
    with i18n.use("en"):
        panels = assessment_panels(detail_view(doc, None, key, restore=False))
    signal = by_field(panels)["tax_bank_match_ratio"]
    assert signal["state"] == expected
    assert any("of reported revenue is confirmed" in risk for risk in panels["risks"]) == (expected == "watch")
    if expected == "unknown":
        assert signal["value"] is None and signal["bar"] is None and not signal["strong"]
    assert doc["underwriting"]["features"]["tax_bank_match_ratio"] == ratio
    assert doc["policy"]["decision"] == "eligible"


@pytest.mark.parametrize("record", [
    {"consent": {"scopes": ["account", "pg"]}},
    {"assembly": {"sources": {"tax": {"consented": False}}}},
    {"assembly": {"missing_sources": ["tax"]}},
])
def test_uncollected_tax_cannot_create_a_strength_or_new_signals(key, record):
    doc = usd_doc() | record
    p = assessment_panels(detail_view(doc, None, key, restore=False))
    assert by_field(p)["tax_bank_match_ratio"]["state"] == "unknown"
    doc["underwriting"] = {}
    assert assessment_panels(detail_view(doc, None, key, restore=False))["signals"] == []


def test_v3_optional_signals_hidden_when_missing(make_doc):
    fields = set(by_field(assessment_panels(view(make_doc, features=STRONG))))
    assert not fields & {"revenue_trend_ratio", "high_cost_lender_count", "pg_reconciliation_ratio",
                         "pg_top_platform_share", "mkt_days_since_suspension", "mkt_return_rate_90d", "mkt_rating"}


def test_v3_signal_refer_on_low_reconciliation(make_doc):
    p = assessment_panels(view(make_doc, features={**STRONG, "pg_reconciliation_ratio": 0.62, "pg_top_platform_share": 0.56}))
    s = by_field(p)
    assert s["pg_reconciliation_ratio"]["state"] == "refer" and s["pg_top_platform_share"]["state"] == "pass"
    assert any("정산" in r for r in p["risks"])


def test_high_cost_lender_signal_counts_transactions_not_lenders(make_doc):
    s = by_field(assessment_panels(view(make_doc, features={**STRONG, "high_cost_lender_count": 25})))
    assert s["high_cost_lender_count"]["state"] == "refer"
    assert s["high_cost_lender_count"]["text"] == (
        "고비용 대출 상환 25건 — 대부업·고금리 대출 상환 거래가 계좌에서 보입니다(기준 0건).")


def krw_limit(make_doc, features, *, version, requested, saved, decision="eligible", codes=()):
    features = dict(features, requested_amount_krw=requested, requested_tenor_months=12, application_id="app-1",
                    industry="retail", region="서울")
    policy = {"rules_version": version, "decision": decision, "max_amount_krw": saved, "rate_band": "B",
              "reason_codes": [{"code": c} for c in codes]}
    return assessment_panels(view(make_doc, features, policy, requested_amount_krw=requested))["limit"]


def test_limit_bars_highlight_the_binding_minimum(make_doc):
    limit = krw_limit(make_doc, STRONG, version="2026.11-v3", requested=10_000_000, saved=6_100_000)
    assert [(b["label"], b["value"], b["color"]) for b in limit["bars"]["bars"]] == [
        ("신청액", 1000, "grey"), ("월평균 입금×1.5", 619, "blue"), ("상품 최대", 1000, "grey")]
    assert limit["binding"] == "월평균 입금×1.5" and limit["computed"] == 6_100_000


def test_limit_uses_settlement_cap_and_substitution_cap(make_doc):
    v4 = dict(version="2026.11-v4", requested=8_000_000, decision="refer", codes=["S001"])
    features = {**STRONG, "avg_monthly_inflow_krw": 2_000_000, "mkt_pending_settlement_krw": 10_000_000,
                "months_of_history": 4, "mkt_tenure_months": 30, "mkt_rating": 4.7, "mkt_review_count": 500,
                "mkt_return_rate_90d": 0.03}
    limit = krw_limit(make_doc, features, saved=5_000_000, **v4)
    assert [(b["label"], b["value"], b["color"]) for b in limit["bars"]["bars"]] == [
        ("신청액", 800, "grey"), ("정산 예정금×0.8", 800, "grey"), ("상품 최대", 1000, "grey"),
        ("대체 상한", 500, "blue")]
    assert limit["binding"] == "대체 상한" and limit["computed"] == 5_000_000
    # 정산 예정금이 입금 상한보다 작으면 입금 상한이 그대로 두 번째 후보다. v3 판정은 정산 예정금을 보지 않는다.
    small = {**features, "mkt_pending_settlement_krw": 1_000_000}
    limit = krw_limit(make_doc, small, saved=3_000_000, **v4)
    assert limit["bars"]["bars"][1]["label"] == "월평균 입금×1.5" and limit["computed"] == 3_000_000
    limit = krw_limit(make_doc, {**features, "months_of_history": 12}, version="2026.11-v3",
                      requested=8_000_000, saved=3_000_000)
    assert [b["label"] for b in limit["bars"]["bars"]] == ["신청액", "월평균 입금×1.5", "상품 최대"]


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
        ("거래처·물류", 600, "neutral"), ("남는 돈", 400, "green")]
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
    v["assessment"] = {**v["assessment"], "recommendation": "decline"}
    p = assessment_panels(v)
    s = p["summary"]
    assert (s["decision"], s["recommendation"], s["review"]) == ("적격", "전액 승인 비권고", "통과 · 1회")
    assert s["gauge_text"] == "67%" and s["gauge_sub"] == "한도 20,000,000원 / 신청 30,000,000원"
    assert p["claims"] == [{"ok": True, "missing": []}, {"ok": False, "missing": ["S:x"]}, {"ok": False, "missing": []}]
    empty = assessment_panels({"doc": make_doc("received", underwriting=None, assessment=None), "policy": None,
                               "assessment": None, "features": {}, "claims": []})
    assert empty["signals"] == [] and empty["capacity"] is None and empty["limit"] is None
    assert empty["expenses"] is None and empty["summary"]["gauge_text"] == "-"
    assert empty["summary"]["recommendation"] == "의견 없음"


def test_summary_officer_decision_only_for_approve_or_decline(make_doc):
    approved = view(make_doc, STRONG, officer_decision={"decision": "approve", "amount_krw": 5_000_000, "officer_id": "o1",
                                                       "conditions": [], "reason": "", "policy_override": True,
                                                       "at": "2026-11-09T05:00:00+00:00"})
    o = assessment_panels(approved)["officer"]
    assert (o["label"], o["tone"], o["requested"], o["approved"], o["override"]) == ("승인", "green", 30_000_000,
                                                                                      5_000_000, True)
    assert o["at"] == "2026-11-09T05:00:00+00:00"
    legacy = view(make_doc, STRONG, officer_decision={"decision": "conditional", "amount_krw": 4_000_000, "officer_id": "o1"})
    assert assessment_panels(legacy)["officer"]["label"] == "승인"
    declined = view(make_doc, STRONG, officer_decision={"decision": "decline", "amount_krw": None, "officer_id": "o1"})
    o = assessment_panels(declined)["officer"]
    assert (o["label"], o["tone"], o["approved"], o["override"]) == ("거절", "red", None, False)
    assert assessment_panels(view(make_doc, STRONG))["officer"] is None
    assert assessment_panels(view(make_doc, STRONG, officer_decision={"decision": "refer"}))["officer"] is None


def usd_doc():
    usd = USD_V1.model_dump()
    features = dict(usd, application_id="usd-1", dscr=1.8, cash_buffer_days=45, revenue_volatility=0.2,
                    tax_bank_match_ratio=0.95, months_of_history=12, business_age_months=30, industry="retail",
                    region="Seoul", avg_monthly_inflow_cents=274169, requested_amount_cents=500051,
                    requested_tenor_months=6)
    return dict(usd, application_id="usd-1", requested_amount_cents=500051, requested_tenor_months=6,
                underwriting={"features": features}, policy=dict(usd, rules_version="usd-v1", decision="eligible",
                    max_amount_cents=410000, rate_band="B", reason_codes=[]))


def test_usd_verified_limit_candidates_and_actual_approval(key):
    doc = usd_doc()
    doc["assessment"] = dict(USD_V1.model_dump(), schema_version="2", review={"passed": True},
                             recommendation="approve", summary="Supported by verified deposits.")
    with i18n.use("en"):
        p = assessment_panels(detail_view(doc, None, key, restore=False))
    assert p["officer"] is None
    assert p["summary"]["gauge_sub"] == "Limit $4100 / requested $5001"
    limit = p["limit"]
    assert [b["value"] for b in limit["bars"]["bars"]] == [500051, 411253, 1000000]
    assert limit["binding"] == "Avg. monthly deposits × 1.5"
    assert (limit["computed"], limit["policy_limit"], limit["unit_cents"]) == (410000, 410000, 10000)
    doc["officer_decision"] = dict(USD_V1.model_dump(), decision="approve", amount_cents=399951)
    officer = assessment_panels(detail_view(doc, None, key, restore=False))["officer"]
    assert (officer["requested"], officer["approved"]) == (500051, 399951)


def test_usd_knockout_zero_is_not_missing_or_the_formula_amount(key):
    doc = usd_doc()
    doc["underwriting"]["features"]["mkt_days_since_suspension"] = 0
    doc["policy"].update(decision="ineligible", max_amount_cents=0, rate_band=None,
                         reason_codes=[{"code": "R003", "field": "mkt_days_since_suspension",
                                        "message": "Selling suspension in the last 90 days"}],
                         cash_only=dict(USD_V1.model_dump(), decision="eligible", max_amount_cents=410000))
    v = detail_view(doc, None, key, restore=False)
    p = assessment_panels(v)
    assert p["summary"]["gauge_text"] == "0%" and "$0" in p["summary"]["gauge_sub"]
    assert (p["limit"]["policy_limit"], p["limit"]["computed"]) == (0, 410000)
    assert v["policy_compare"] == {"cash": {"decision": "eligible", "max": 410000},
                                   "seller": {"decision": "ineligible", "max": 0}, "bank_tax": False}
    assert by_field(p)["mkt_days_since_suspension"]["state"] == "knockout"


@pytest.mark.parametrize(("has_market", "cash_decision", "cash_limit"), [
    (True, "refer", 410000), (True, "eligible", 410001), (False, "eligible", 410000),
])
def test_usd_unverified_cash_only_keeps_stored_comparison_but_hides_formula(key, has_market, cash_decision, cash_limit):
    doc = usd_doc()
    if has_market:
        doc["underwriting"]["features"]["mkt_rating"] = 4.8
    doc["policy"]["cash_only"] = dict(USD_V1.model_dump(), decision=cash_decision, max_amount_cents=cash_limit)
    v = detail_view(doc, None, key, restore=False)
    assert v["policy_compare"]["cash"] == {"decision": cash_decision, "max": cash_limit}
    assert v["policy_compare"]["seller"] == {"decision": "eligible", "max": 410000}
    p = assessment_panels(v)
    assert p["limit"] is None and p["limit_note"]


def test_usd_referred_limit_uses_verified_settlement_and_substitution_caps(key):
    doc = usd_doc()
    doc["underwriting"]["features"].update(months_of_history=3, mkt_rating=4.6, mkt_return_rate_90d=0.02,
                                           mkt_review_count=130, mkt_tenure_months=20, mkt_pending_settlement_cents=702347)
    doc["policy"].update(decision="refer", max_amount_cents=500000, reason_codes=[{"code": "S001"}])
    p = assessment_panels(detail_view(doc, None, key, restore=False))
    assert p["limit"] is not None
    assert [(b["value"], b["color"]) for b in p["limit"]["bars"]["bars"]] == [
        (500051, "grey"), (561877, "grey"), (1000000, "grey"), (500000, "blue")]
    assert (p["limit"]["binding"], p["limit"]["computed"]) == ("대체 상한", 500000)


@pytest.mark.parametrize("override", [
    {"max_amount_cents": 410001}, {"rate_band": "A"}, {"decision": "refer"},
    {"reason_codes": [{"code": "R001"}]}, {"rules_version": "unknown"}, {"rules_version": "2026.11-v4"},
])
def test_usd_unverified_policy_omits_formula_but_keeps_stored_limit(key, override):
    doc = usd_doc()
    doc["policy"].update(override)
    p = assessment_panels(detail_view(doc, None, key, restore=False))
    assert p["limit"] is None and p.get("limit_note")
    assert "$4100" in p["summary"]["gauge_sub"]


def test_usd_partial_features_and_policy_have_no_invented_limit(key):
    doc = usd_doc()
    del doc["policy"]["max_amount_cents"]
    doc["underwriting"]["features"] = {**USD_V1.model_dump(), "dscr": 1.8}
    doc["policy"]["cash_only"] = {"decision": "ineligible", "max_amount_cents": 0}
    v = detail_view(doc, None, key, restore=False)
    p = assessment_panels(v)
    assert p["summary"]["gauge_text"] == "-" and "$0" not in p["summary"]["gauge_sub"]
    assert p["limit"] is None and p.get("limit_note")
    assert v["policy_compare"]["cash"]["max"] == 0 and v["policy_compare"]["seller"]["max"] is None
    assert by_field(p)["cash_buffer_days"]["state"] == "unknown"


def test_usd_sparse_evidence_inherits_product_but_cannot_verify_a_formula(key):
    doc = usd_doc()
    doc["underwriting"]["features"] = {"dscr": 0.95}
    doc["policy"] = {"decision": "ineligible", "max_amount_cents": 0, "rules_version": "usd-v1"}
    p = assessment_panels(detail_view(doc, None, key, restore=False))
    assert by_field(p)["dscr"]["value"] == 0.95 and p["summary"]["gauge_text"] == "0%"
    assert p["limit"] is None and p["limit_note"]
    doc["underwriting"]["features"] = {"requested_amount_krw": 500051}
    doc["policy"] = {"decision": "ineligible", "max_amount_krw": 0}
    with pytest.raises(ValueError):
        detail_view(doc, None, key, restore=False)


def test_usd_formula_does_not_describe_a_different_recorded_request(key):
    doc = usd_doc()
    doc["requested_amount_cents"] = 499949  # Inflow still binds: the final policy limit happens to agree.
    p = assessment_panels(detail_view(doc, None, key, restore=False))
    assert p["limit"] is None and p["limit_note"]
    assert "$4999" in p["summary"]["gauge_sub"]


@pytest.mark.parametrize("partial", [{}, {"reason_codes": None}])
def test_usd_missing_reason_list_is_not_a_verified_empty_list(key, partial):
    doc = usd_doc()
    del doc["policy"]["reason_codes"]
    doc["policy"].update(partial)
    p = assessment_panels(detail_view(doc, None, key, restore=False))
    assert p["limit"] is None and p["limit_note"]


def test_usd_capacity_axis_includes_repayments_when_all_months_are_short(key):
    doc = usd_doc()
    doc["underwriting"].update(signals={"new_installment_cents": 2100}, monthly=[
        {"month": "2026-01", "revenue_cents": 550, "operating_out_cents": 250, "debt_service_cents": 100},
        {"month": "2026-02", "revenue_cents": 250, "operating_out_cents": 250, "debt_service_cents": 0}])
    cap = assessment_panels(detail_view(doc, None, key, restore=False))["capacity"]
    assert cap["chart"]["vmax"] == cap["line"]["vmax"] == 2200
    assert cap["short_months"] == 2 and cap["line"]["points"][0]["y"] < cap["line"]["points"][1]["y"]


def test_usd_partial_monthly_rows_keep_only_supported_opinion_evidence(key):
    doc = usd_doc()
    doc["underwriting"]["monthly"] = [{"month": "2026-01", "revenue_cents": 12345}, {"month": "2026-02"}]
    ids = ["S:2026-01:revenue_cents", "S:2026-01:index", "S:2026-02:revenue_cents"]
    doc["assessment"] = dict(USD_V1.model_dump(), schema_version="2", review={"passed": True},
                             recommendation="refer", claims=[{"text": "Check January receipts.", "metric_ids": ids}])
    v = detail_view(doc, None, key, restore=False)
    assert v["claims"][0]["evidence"] == list(zip(ids, [12345, None, None], strict=True))


def test_usd_capacity_compares_exact_cents_only_for_known_months(key):
    doc = usd_doc()
    doc["underwriting"].update(signals={"new_installment_cents": 101}, monthly=[
        {"month": "2026-01", "revenue_cents": 624, "operating_out_cents": 423, "debt_service_cents": 100},
        {"month": "2026-02", "revenue_cents": 623, "operating_out_cents": 423, "debt_service_cents": 100},
        {"month": "2026-03", "revenue_cents": 395, "operating_out_cents": 100},
        {"month": "2026-04", "revenue_cents": 400, "debt_service_cents": 0}])
    cap = assessment_panels(detail_view(doc, None, key, restore=False))["capacity"]
    bars = [g["bars"][0]["parts"][0] for g in cap["chart"]["groups"]]
    assert [(b["color"], b["value"]) for b in bars] == [
        ("green", 201), ("red", 200), ("grey", 295), ("grey", None)]
    assert "$2" in bars[1]["tip"] and "$" in cap["chart"]["ticks"][1]["text"]
    assert [(p["i"], p["value"]) for p in cap["line"]["points"]] == [(0, 201), (1, 201), (3, 101)]
    assert (cap["short_months"], cap["months"]) == (1, 2)
    del doc["underwriting"]["signals"]["new_installment_cents"]
    cap = assessment_panels(detail_view(doc, None, key, restore=False))["capacity"]
    assert cap["installment"] is None and not cap["line"]["points"] and cap["months"] == 0


def test_usd_expense_donut_preserves_small_amounts_and_rejects_incomplete_costs(key):
    doc = usd_doc()
    rows = [{"group": g, "monthly_avg_cents": 133 if g == "rent" else 1 if g == "debt_service" else 0}
            for g in ("supplier", "rent", "payroll", "tax_insurance", "other_operating",
                      "debt_service", "owner_draw", "unclassified")]
    doc["underwriting"].update(expense_breakdown=rows)
    def expenses():
        return assessment_panels(detail_view(doc, None, key, restore=False))["expenses"]
    ex = expenses()
    assert [a["value"] for a in ex["donut"]["arcs"]] == [133, 1, 274035]
    assert ex["leftover"] == 274035 and ex["legend"][1][2] == "$0"
    del rows[0]["monthly_avg_cents"]
    assert expenses() is None
    rows.pop(0)
    assert expenses() is None


@pytest.mark.parametrize("missing_breakdown_inflow", [False, True])
def test_usd_monthly_expense_fallback_rounds_only_the_readout(key, missing_breakdown_inflow):
    doc = usd_doc()
    if missing_breakdown_inflow:
        del doc["underwriting"]["features"]["avg_monthly_inflow_cents"]
        doc["underwriting"]["expense_breakdown"] = [{"group": g, "monthly_avg_cents": 725} for g in (
            "supplier", "rent", "payroll", "tax_insurance", "other_operating", "debt_service", "owner_draw", "unclassified")]
    doc["underwriting"]["monthly"] = [
        {"month": "2026-01", "revenue_cents": 1000, "operating_out_cents": 149, "debt_service_cents": 0, "owner_draw_cents": 0},
        {"month": "2026-02", "revenue_cents": 1000, "operating_out_cents": 150, "debt_service_cents": 0, "owner_draw_cents": 0}]
    ex = assessment_panels(detail_view(doc, None, key, restore=False))["expenses"]
    # Use 299 / 2 months. $1.495 must not round twice to $2.
    assert [a["value"] for a in ex["donut"]["arcs"]] == [299, 1701] and ex["divisor"] == 2
    assert ex["legend"][0][2] == "$1"
    del doc["underwriting"]["monthly"][0]["owner_draw_cents"]
    assert assessment_panels(detail_view(doc, None, key, restore=False))["expenses"] is None
