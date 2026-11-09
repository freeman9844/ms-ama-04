from console import dashboard
from console.app import STATUS_LABELS
from opsmetrics.labels import STEP_LABELS

T = "2026-10-31T09:{:02d}:00+09:00"


def tl(*pairs):
    return [{"status": s, "at": T.format(m) if m is not None else None} for s, m in pairs]


def states(steps):
    return [s["state"] for s in steps]


def test_home_summary_counts_groups_and_recommendations():
    rows = [{"status": s, "recommendation": r} for s, r in [
        ("awaiting_officer", "approve"), ("escalated", "refer"), ("approved", None), ("conditionally_approved", "weird"),
        ("declined", "decline"), ("withdrawn", "conditional"), ("received", None), ("assessed", "approve")]]
    s = dashboard.home_summary(rows)
    assert (s["total"], s["awaiting"], s["escalated"], s["in_progress"], s["approved"], s["declined"]) == (8, 2, 1, 2, 2, 1)
    assert s["approval_rate"] == 2 / 3
    assert [n for _, _, n in s["groups"]] == [2, 2, 1, 1, 2]
    assert s["recommendations"] == [("승인", 2, "green"), ("승인 권고(조건 제안)", 1, "blue"), ("심사역 회부", 1, "purple"),
                                    ("거절", 1, "red"), ("권고 없음", 3, "neutral")]
    assert s["donut"]["total"] == 8 and len(s["rec_bars"]["bars"]) == 5


def test_home_summary_without_decided_rows_has_no_approval_rate():
    s = dashboard.home_summary([])
    assert s["approval_rate"] is None and s["donut"]["arcs"] == [] and s["total"] == 0
    assert dashboard.home_summary([{"status": "received"}])["approval_rate"] is None


def test_journey_steps_awaiting_officer(make_doc):
    steps = dashboard.journey_steps(make_doc())
    assert [s["label"] for s in steps] == ["접수", "동의 완료", "데이터 수집", "심사 지표 산출", "에이전트 평가", "심사역 대기", "결정"]
    assert states(steps) == ["done"] * 5 + ["current", "todo"]
    assert (steps[0]["time"], steps[1]["time"], steps[5]["time"]) == ("09:00:00", "", "09:02:00")
    assert (steps[5]["elapsed_s"], steps[5]["elapsed"]) == (120, "+2분 0초")


def test_journey_steps_escalated_is_warn_and_keeps_first_arrival(make_doc):
    doc = make_doc("escalated", timeline=tl(("received", 0), ("awaiting_officer", 2), ("escalated", 30)))
    steps = dashboard.journey_steps(doc)
    assert steps[5]["state"] == "warn" and steps[5]["note"] == "SLA 초과" and steps[5]["elapsed_s"] == 120
    assert steps[6]["state"] == "todo"


def test_journey_steps_data_incomplete_warns_data_step(make_doc):
    doc = make_doc("data_incomplete", timeline=tl(("received", 0), ("consent_granted", 0), ("data_incomplete", 1)))
    steps = dashboard.journey_steps(doc)
    assert states(steps) == ["done", "done", "warn", "todo", "todo", "todo", "todo"]
    assert steps[2]["note"] == "데이터 부족" and steps[2]["elapsed_s"] == 60


def test_journey_steps_withdrawn_fails_next_step_with_status_label(make_doc):
    doc = make_doc("withdrawn", timeline=tl(("received", 0), ("consent_granted", 0), ("withdrawn", 3)))
    steps = dashboard.journey_steps(doc)
    assert states(steps) == ["done", "done", "failed", "todo", "todo", "todo", "todo"]
    assert steps[2]["label"] == "철회 종료" and steps[2]["time"] == "09:03:00"


def test_journey_steps_failed_without_timeline_entry(make_doc):
    steps = dashboard.journey_steps(make_doc("failed", timeline=tl(("received", 0))))
    assert states(steps)[:3] == ["done", "failed", "todo"] and steps[1]["label"] == "실패" and steps[1]["at"] is None


def test_journey_steps_closed_without_decision_fails_decision_step(make_doc):
    doc = make_doc("closed_no_decision", timeline=tl(("received", 0), ("awaiting_officer", 2), ("closed_no_decision", 9)))
    steps = dashboard.journey_steps(doc)
    assert states(steps) == ["done"] * 6 + ["failed"] and steps[6]["label"] == "미결 종료"


def test_journey_steps_declined_and_escalated_then_approved(make_doc):
    declined = dashboard.journey_steps(make_doc("declined", timeline=tl(("received", 0), ("awaiting_officer", 2),
                                                                        ("declined", 5))))
    assert states(declined) == ["done"] * 7 and declined[6]["label"] == "거절" and declined[6]["elapsed_s"] == 180
    approved = dashboard.journey_steps(make_doc("approved", timeline=tl(
        ("received", 0), ("awaiting_officer", 2), ("escalated", 30), ("approved", 31))))
    assert approved[5]["state"] == "warn" and approved[6]["state"] == "done" and approved[6]["label"] == "승인"


def test_journey_steps_ignore_unknown_status_and_mixed_timezones(make_doc):
    doc = make_doc(timeline=[{"status": "mystery", "at": T.format(0)}, {"status": "received", "at": "2026-10-31T09:00:00"},
                             {"status": "awaiting_officer", "at": T.format(2)}])
    steps = dashboard.journey_steps(doc)
    assert states(steps) == ["done"] * 5 + ["current", "todo"] and steps[5]["elapsed_s"] is None


def test_detail_summary_monthly_markers_counterfactuals_and_review(make_doc):
    doc = make_doc()
    assessment = {**doc["assessment"], "counterfactuals": [
        {"metric_id": "M:dscr", "current": 0.9, "target": 1.25, "explanation": "x"},
        {"metric_id": "M:note", "current": "낮음", "target": "보통", "explanation": "y"}]}
    policy = {**doc["policy"], "reason_codes": doc["policy"]["reason_codes"] + [
        {"code": "R109", "message": "범주형", "observed": None, "threshold": "A"}]}
    s = dashboard.detail_summary({"doc": doc, "policy": policy, "assessment": assessment})
    assert [s_["state"] for s_ in s["steps"]][5] == "current"
    group = s["monthly"]["groups"][0]
    assert group["label"] == "26-09" and [len(b["parts"]) for b in group["bars"]] == [1, 3]
    assert group["bars"][0]["parts"][0]["tip"] == "2026-09 매출 40,000,000원"
    assert [(p["value"], p["negative"]) for p in s["monthly_net"]["points"]] == [(8_000_000, False)]
    assert [label for label, _ in s["monthly_legend"]] == ["매출", "운영비", "상환", "대표자 인출", "순현금흐름(매출−지출)"]
    assert [m["code"] for m in s["markers"]] == ["R101"] and s["markers"][0]["bar"] == {"fill": 80.0, "mark": 55.56}
    assert s["counterfactuals"][0]["bar"]["bars"][1]["w"] == 204.0 and s["counterfactuals"][1]["bar"] is None
    assert [(r["label"], r["state"], r["note"]) for r in s["review"]] == [
        ("분석 에이전트", "done", "gpt-5.4"), ("검토 에이전트", "done", "1회")]


def test_detail_summary_splits_expenses_into_groups_with_net_line(make_doc):
    doc = make_doc()
    uw = doc["underwriting"]
    uw["monthly"].append({"month": "2026-10", "revenue": 1_000_000, "operating_out": 2_000_000, "debt_service": 0,
                          "owner_draw": 0})
    zero = dict.fromkeys(("supplier", "rent", "payroll", "tax_insurance", "other_operating", "debt_service",
                          "owner_draw", "unclassified"), 0)
    uw["expense_monthly"] = [
        {**zero, "month": "2026-09", "supplier": 20_000_000, "rent": 3_000_000, "payroll": 5_000_000,
         "tax_insurance": 1_000_000, "other_operating": 500_000, "debt_service": 1_000_000, "owner_draw": 1_000_000,
         "unclassified": 500_000},
        {**zero, "month": "2026-10", "supplier": 2_000_000}]
    s = dashboard.detail_summary({"doc": doc, "policy": None, "assessment": None})
    bars = s["monthly"]["groups"][0]["bars"]
    assert [p["color"] for p in bars[1]["parts"]] == ["neutral", "purple", "amber", "teal", "orange", "red", "pink", "grey"]
    assert bars[1]["parts"][0]["tip"] == "2026-09 거래처·물류 20,000,000원"
    assert [(p["value"], p["negative"]) for p in s["monthly_net"]["points"]] == [(8_000_000, False), (-1_000_000, True)]
    assert s["monthly_net"]["vmax"] == s["monthly"]["vmax"]
    assert [label for label, _ in s["monthly_legend"]] == [
        "매출", "거래처·물류", "임대료", "인건비", "세금·4대보험", "기타 운영비", "기존 상환", "대표자 인출", "미분류",
        "순현금흐름(매출−지출)"]


def test_detail_summary_without_underwriting_or_assessment(make_doc):
    s = dashboard.detail_summary({"doc": make_doc("received", underwriting=None, assessment=None), "policy": None,
                                  "assessment": None})
    assert s["monthly"] is None and s["markers"] == [] and s["counterfactuals"] == [] and s["review"] is None
    assert s["monthly_net"] is None


def test_loans_summary_counts():
    rows = [{"monitoring_status": "completed", "alerts": 2}, {"monitoring_status": "replaying", "alerts": 0},
            {"monitoring_status": None, "alerts": None}, {"monitoring_status": "failed", "alerts": True}]
    assert dashboard.loans_summary(rows) == {"total": 4, "replaying": 1, "completed": 1, "not_started": 1, "alerts": 2}


def test_alert_steps_branches():
    full = dashboard.alert_steps({"week": 1, "severity": "high", "collections": {"decision": "routed"},
                                  "restructure": {"status": "pending_officer"}})
    assert [(s["label"], s["state"], s["note"]) for s in full] == [
        ("탐지", "done", "1주차"), ("조기경보 조사", "done", "높음"), ("추심 에이전트", "done", "routed"),
        ("재조정 결정", "current", "심사역 대기")]
    # 미납 추심 뒤 경보는 심사역 검토용이다(차주 연락 없음). 위 두 경우는 옛 경보 문서.
    review = dashboard.alert_steps({"week": 4, "severity": "medium", "collections": None, "restructure": None})
    assert [(s["label"], s["state"], s["note"]) for s in review] == [
        ("탐지", "done", "4주차"), ("조기경보 조사", "done", "중간"), ("심사역 검토", "done", "연락 없음")]
    done = dashboard.alert_steps({"week": 4, "severity": "low", "collections": {"decision": "x"},
                                  "restructure": {"status": "declined"}})
    assert (done[-1]["state"], done[-1]["note"]) == ("done", "거절")


def test_alert_followups_link_missed_payment_contacts_to_cited_alert():
    case = {"case_id": "rc-1", "installment": {"no": 3}, "recent_alert": {"alert_id": "ew-12"},
            "collections": {"decision": "routed", "contacts": [
                {"at": "2026-11-09T16:13:21", "script_id": "hardship_offer", "status": "sent"}]},
            "restructure": {"status": "pending_officer"}}
    waiting = {"case_id": "rc-2", "installment": {"no": 4}, "recent_alert": {"alert_id": "ew-12"}}
    other = {"case_id": "rc-0", "installment": {"no": 1}, "recent_alert": None}
    links = dashboard.alert_followups([case, waiting, other])
    assert set(links) == {"ew-12"}
    assert links["ew-12"][0] == {"case_id": "rc-1", "no": 3, "decision": "routed", "restructure": "pending_officer",
                                 "contacts": [{"at": "2026-11-09T16:13:21", "script_id": "hardship_offer",
                                               "status": "sent"}]}
    assert links["ew-12"][1]["contacts"] == [] and links["ew-12"][1]["decision"] is None
    steps = dashboard.alert_steps({"week": 12, "severity": "high", "collections": None, "restructure": None},
                                  links["ew-12"][:1])
    assert [(s["label"], s["state"], s["note"]) for s in steps] == [
        ("탐지", "done", "12주차"), ("조기경보 조사", "done", "높음"),
        ("미납 추심 연락", "done", "3회차 · hardship_offer"), ("재조정 결정", "current", "심사역 대기")]
    pending = dashboard.alert_steps({"week": 12, "severity": "high"}, links["ew-12"][1:])
    assert (pending[-1]["label"], pending[-1]["state"], pending[-1]["note"]) == ("미납 추심 연락", "current", "4회차")


def week(n, buffer, triggers=(), new=()):
    row = {"start": f"2026-09-0{n}", "end": f"2026-09-0{n + 6}", "revenue": 8_000_000 + n, "buffer_days": buffer}
    return {"week": n, "row": row, "triggers": list(triggers), "new_triggers": list(new)}


def test_loan_summary_progress_severity_buffer_and_chart():
    view = {
        "loan": {"baseline": {"avg_weekly_revenue": 9_000_000, "cash_buffer_days": 30.0}},
        "monitoring": {"processed_week": 3},
        "weeks": [week(1, 20.0), week(2, 12.5, ["new_debt"], ["new_debt"]), week(3, None, ["new_debt"])],
        "alerts": [{"alert_id": "ew-1", "week": 2, "severity": "high"}, {"alert_id": "ew-2", "severity": "medium"}],
    }
    s = dashboard.loan_summary(view)
    assert (s["processed"], s["progress_pct"], s["alerts"]) == (3, 23.1, 2)
    assert s["severity"] == {"high": 1, "medium": 1, "low": 0}
    assert (s["min_buffer"], s["min_buffer_week"], s["baseline_buffer"]) == (12.5, 2, 30.0)
    colors = [g["bars"][0]["parts"][0]["color"] for g in s["chart"]["groups"]]
    assert colors == ["blue", "red", "red"] and s["chart"]["refs"][0]["label"] == "기준 주매출"
    assert "신규 차입" in s["chart"]["groups"][1]["bars"][0]["parts"][0]["tip"]
    none_tip = s["chart"]["groups"][2]["bars"][0]["parts"][0]["tip"]
    assert "None" not in none_tip and "· 버퍼 -일" in none_tip
    top = s["chart"]["groups"][1]["bars"][0]["top"]
    assert [(m["week"], m["y"]) for m in s["markers"]] == [(2, round(top - 6, 2))]
    assert len(s["line"]["segments"]) == 1 and len(s["line"]["points"]) == 2
    assert s["line"]["x"] == s["chart"]["x1"] and s["line"]["points"][0]["x"] == s["chart"]["groups"][0]["cx"]
    assert set(s["alert_steps"]) == {"ew-1", "ew-2"}
    assert s["case_steps"] == {} and s["repayment"] is None


def test_case_steps():
    c = {"installment": {"no": 3, "due_on": "2027-01-29"}, "collections": {"decision": "routed"},
         "restructure": {"status": "pending_officer"}}
    steps = dashboard.case_steps(c)
    assert [(s["label"], s["state"], s["note"]) for s in steps] == [
        ("미납", "done", "3회차 2027-01-29"), ("추심 에이전트", "done", "routed"), ("재조정 결정", "current", "심사역 대기")]
    waiting = dashboard.case_steps({**c, "collections": None, "restructure": None})
    assert [s["state"] for s in waiting] == ["done", "current"]


def test_loan_summary_repayment_and_cases():
    schedule = [{"status": "paid"}, {"status": "paid"}, {"status": "missed"}]
    case = {"case_id": "rc-1", "installment": {"no": 3, "due_on": "2027-01-29"}, "collections": None, "restructure": None}
    s = dashboard.loan_summary({"loan": {"baseline": {}, "repayment": {"schedule": schedule}}, "monitoring": {},
                                "weeks": [], "alerts": [], "cases": [case]})
    assert s["repayment"] == {"paid": 2, "total": 3, "missed": 1} and list(s["case_steps"]) == ["rc-1"]


def test_loan_summary_before_replay():
    s = dashboard.loan_summary({"loan": {"baseline": {}}, "monitoring": {}, "weeks": [], "alerts": []})
    assert s["chart"] is None and s["line"] is None and s["min_buffer"] is None and s["progress_pct"] == 0.0
    assert s["expected_chart"] is None and s["forecast"] is None


def test_loan_summary_expected_and_forecast_charts():
    w1, w2, w3 = week(1, 20.0), week(2, 15.0), week(3, 9.0, ["projected_shortfall"])
    w1["row"]["expected_revenue"], w2["row"]["expected_revenue"] = 8_000_000, 12_000_000
    path = [{"date": f"2026-09-{d:02d}", "balance": 1_000_000 - d * 100_000} for d in range(10, 38 - 10)]
    w3["row"].update(projected_min_balance=-700_000, projected_min_date="2026-09-27", shortfall_date="2026-09-21")
    w3["projection"] = {"start_balance": 1_000_000, "attainment": 0.62, "path": path,
                        "totals": {"inflow": 5_000_000, "rent": -1_000_000, "variable": -2_000_000, "installment": -287_000}}
    view = {"loan": {"baseline": {}}, "monitoring": {"processed_week": 3}, "weeks": [w1, w2, w3], "alerts": []}
    s = dashboard.loan_summary(view)
    e, line = s["expected_chart"], s["expected_line"]
    assert e["vmax"] == 12_000_000 and line["vmax"] == e["vmax"]
    assert [g["bars"][0]["parts"][0]["color"] for g in e["groups"]] == ["blue", "amber", "blue"]  # 기대의 85% 미만
    assert [p["i"] for p in line["points"]] == [0, 1] and line["points"][1]["x"] == e["groups"][1]["cx"]
    f = s["forecast"]
    assert (f["week"], f["attainment_pct"], f["shortfall_date"], f["min_balance"]) == (3, 62.0, "2026-09-21", -700_000)
    assert f["chart"]["points"][0]["label"] == "09-10" and f["chart"]["low"]["value"] == path[-1]["balance"]
    assert f["totals"] == [("기대 입금", 5_000_000), ("임대료", -1_000_000), ("이번 대출 원리금", -287_000),
                           ("매출 비례 운영비", -2_000_000)]


def test_loan_summary_forecast_uses_latest_projection():
    w1, w2 = week(1, 20.0), week(2, 15.0)
    w1["projection"] = {"start_balance": 1, "attainment": 1.0, "path": [{"date": "2026-09-08", "balance": 5}], "totals": {}}
    s = dashboard.loan_summary({"loan": {"baseline": {}}, "monitoring": {}, "weeks": [w1, w2], "alerts": []})
    assert s["forecast"]["week"] == 1 and s["expected_chart"] is None


def test_ops_summary_gauges_steps_and_fairness():
    seg = {"assessed": 5, "insufficient": False}
    industry_values = {
        "카페": {**seg, "approve_rate": 1.0}, "한식": {**seg, "approve_rate": 0.0},
        "편의점": {"assessed": 2, "insufficient": True, "approve_rate": 0.5},
        "기타": {"assessed": 0, "insufficient": True, "approve_rate": None}}
    s = {
        "latency": {"p95_min": 8.0, "target_min": 10.0, "met": True},
        "agent_success": {"rate": 0.95, "met": False, "by_step": {"underwrite": {"ok": 3, "degraded": 1, "failed": 0},
                                                                  "assess": {"ok": 0, "degraded": 0, "failed": 0}}},
        "fairness": {"axes": {"industry": {"values": industry_values, "alert": False},
                              "region": {"values": industry_values, "alert": True}}},
    }
    u = dashboard.ops_summary(s, STEP_LABELS)
    assert (u["latency"]["pct"], u["latency_tone"], u["success"]["pct"], u["success_tone"]) == (40.0, "green", 95.0, "red")
    assert [r["label"] for r in u["steps"]] == ["신용 평가", "심사 지표 산출"] and u["steps"][0]["empty"]
    chart = u["fairness"]["industry"]
    assert [r["text"] for r in chart["refs"]] == ["100%", "0%"] and chart["vmax"] == 100.0
    assert [r["color"] for r in chart["refs"]] == ["neutral", "neutral"]
    assert [r["color"] for r in u["fairness"]["region"]["refs"]] == ["red", "red"]
    parts = [g["bars"][0]["parts"][0] for g in chart["groups"]]
    assert [p["color"] for p in parts] == ["blue", "blue", "grey", "grey"]
    assert parts[2]["tip"].endswith("표본 부족") and parts[3]["tip"] == "기타 추천 없음"


def test_ops_summary_without_samples_is_neutral():
    u = dashboard.ops_summary({"latency": {"p95_min": None, "target_min": 10.0, "met": False},
                               "agent_success": {"rate": None, "met": False, "by_step": {}},
                               "fairness": {"axes": {}}}, STEP_LABELS)
    assert (u["latency_tone"], u["success_tone"], u["steps"], u["fairness"]) == ("neutral", "neutral", [], {})
    assert all(r["empty"] for r in u["funnel"]) and u["step_latency"] is None and u["throughput"] is None
    assert (u["model_cost"]["arcs"], u["step_cost"]["bars"], u["reasons"]["bars"], u["flags"]["bars"]) == ([], [], [], [])


def test_ops_insights_funnel_latency_cost_reasons_flags_throughput():
    s = {
        "funnel": {"policy": {"eligible": 3, "refer": 1, "none": 1}, "recommendation": {"approve": 2, "odd": 1},
                   "officer": {"approve": 1, "none": 4}},
        "step_latency": {"assess": {"n": 4, "p50_ms": 2_340, "p95_ms": 12_000}},
        "ai_cost": {"by_model": {"gpt-5.4": {"cost_usd": 0.0123456}, "decision-1": {"cost_usd": None},
                                 "gpt-5.4-mini": {"cost_usd": 0.001}},
                    "by_step": {"assess": {"cost_usd": 0.0133456}}},
        "reason_codes": [{"key": "R101", "count": 3}, {"key": "R002", "count": 1}],
        "flags": [{"key": "agent_fallback", "count": 2}, {"key": "new_flag", "count": 1}],
        "throughput": {"buckets": [{"start": f"2026-11-04T01:{m:02d}:00+00:00", "received": m // 10, "reached": 1}
                                   for m in (0, 10)]},
    }
    u = dashboard.ops_insights(s, STEP_LABELS)
    assert [r["label"] for r in u["funnel"]] == ["정책 판정", "에이전트 권고", "심사역 결정"]
    assert [(p["part"], p["color"]) for p in u["funnel"][0]["parts"]] == [
        ("적격", "green"), ("심사역 회부", "amber"), ("판정 없음", "neutral")]
    assert [(p["part"], p["color"]) for p in u["funnel"][1]["parts"]] == [("승인", "green"), ("odd", "grey")]
    assert [p["part"] for p in u["funnel"][2]["parts"]] == ["승인", "미결정"]
    group = u["step_latency"]["groups"][0]
    assert group["label"] == "신용 평가" and [b["parts"][0]["value"] for b in group["bars"]] == [2.3, 12.0]
    assert u["step_latency"]["ticks"][-1]["text"] == "12초"
    assert [(a["label"], a["value"]) for a in u["model_cost"]["arcs"]] == [("gpt-5.4", 0.0123), ("gpt-5.4-mini", 0.001)]
    assert u["model_cost"]["total"] == 0.0133 and u["model_colors"][0] == ("gpt-5.4", "blue", "$0.0123")
    assert [(b["label"], b["value"]) for b in u["step_cost"]["bars"]] == [("신용 평가", 0.0133)]
    assert [(b["label"], b["color"]) for b in u["reasons"]["bars"]] == [("R101", "amber"), ("R002", "red")]
    assert [b["label"] for b in u["flags"]["bars"]] == ["에이전트 대체 경로 사용", "new_flag"]
    assert [g["label"] for g in u["throughput"]["groups"]] == ["10:00", ""]
    assert u["throughput"]["groups"][1]["bars"][1]["parts"][0]["tip"] == "10:10 심사역 대기 도달 1건"


def test_formatters():
    assert dashboard.elapsed_text(None) == "" and dashboard.elapsed_text(5) == "+5초"
    assert dashboard.elapsed_text(3725) == "+1시간 2분"
    assert dashboard.man(40_000_000) == "4,000만" and dashboard.man(0) == "0"
    assert dashboard.krw_text(1_000) == "1,000원" and dashboard.krw_text(None) == "-"
    assert dashboard.clock("bad") == "" and dashboard.clock(None) == ""


def test_status_labels_are_shared_with_app():
    from console import statuses
    assert STATUS_LABELS is statuses.STATUS_LABELS


def test_gateway_blocks_lists_blocked_steps_in_order():
    doc = {"steps": [
        {"step": "assemble", "usage": [{"model": "m", "gateway": "content_blocked"}]},
        {"step": "classify", "usage": [{"model": "m", "gateway": "rate_limited"}]},
        {"step": "assemble", "usage": [{"model": "m", "gateway": "content_blocked"}]},
        {"step": "consent", "usage": []},
    ]}
    assert dashboard.gateway_blocks(doc) == ["데이터 수집"]
    assert dashboard.gateway_blocks({}) == []


LEDGER = {"available": True, "period": {"from": "2026-05-01", "to": "2026-11-01"}, "settlement_count": 120,
          "pg_reconciliation_ratio": 0.62, "pg_top_platform_share": 0.56, "pg_hhi": 0.45, "pg_take_rate_trend_pp": 1.8,
          "platforms": [{"platform": "쿠팡", "gross_krw": 60_000_000, "share": 0.56, "unpaid_krw": 21_000_000},
                        {"platform": "스마트스토어", "gross_krw": 47_000_000, "share": 0.44, "unpaid_krw": 0}],
          "monthly_take_rate": [{"month": "2026-05", "rate": 0.081}, {"month": "2026-10", "rate": 0.099}],
          "unmatched_count": 30, "unmatched": [{"settled_on": "2026-10-27", "platform": "쿠팡", "net_krw": 512_000}]}


def test_settlement_card_warns_low_reconciliation():
    card = dashboard.settlement_card(LEDGER)
    assert card["available"] and card["kpis"][0]["tone"] == "red" and card["kpis"][0]["value"] == "62%"
    assert [b["label"] for b in card["platforms"]["bars"]][:1] == ["쿠팡"]
    assert card["unmatched"][0]["net_krw"] == 512_000 and card["unmatched_count"] == 30


def test_settlement_card_unavailable_for_old_docs():
    assert dashboard.settlement_card(None) == {"available": False}
    assert dashboard.settlement_card({"available": False}) == {"available": False}
