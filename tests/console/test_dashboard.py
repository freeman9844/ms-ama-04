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
    assert s["recommendations"] == [("승인", 2, "green"), ("조건부", 1, "blue"), ("심사역 회부", 1, "purple"),
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
    assert [m["code"] for m in s["markers"]] == ["R101"] and s["markers"][0]["bar"] == {"fill": 80.0, "mark": 55.56}
    assert s["counterfactuals"][0]["bar"]["bars"][1]["w"] == 204.0 and s["counterfactuals"][1]["bar"] is None
    assert [(r["label"], r["state"], r["note"]) for r in s["review"]] == [
        ("분석 에이전트", "done", "gpt-5.4"), ("검토 에이전트", "done", "1회")]


def test_detail_summary_without_underwriting_or_assessment(make_doc):
    s = dashboard.detail_summary({"doc": make_doc("received", underwriting=None, assessment=None), "policy": None,
                                  "assessment": None})
    assert s["monthly"] is None and s["markers"] == [] and s["counterfactuals"] == [] and s["review"] is None


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
    waiting = dashboard.alert_steps({"week": 4, "severity": "medium", "collections": None, "restructure": None})
    assert [s["state"] for s in waiting] == ["done", "done", "current"]
    done = dashboard.alert_steps({"week": 4, "severity": "low", "collections": {"decision": "x"},
                                  "restructure": {"status": "declined"}})
    assert (done[-1]["state"], done[-1]["note"]) == ("done", "거절")


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
    top = s["chart"]["groups"][1]["bars"][0]["top"]
    assert [(m["week"], m["y"]) for m in s["markers"]] == [(2, round(top - 6, 2))]
    assert len(s["line"]["segments"]) == 1 and len(s["line"]["points"]) == 2
    assert s["line"]["x"] == s["chart"]["x1"] and s["line"]["points"][0]["x"] == s["chart"]["groups"][0]["cx"]
    assert set(s["alert_steps"]) == {"ew-1", "ew-2"}


def test_loan_summary_before_replay():
    s = dashboard.loan_summary({"loan": {"baseline": {}}, "monitoring": {}, "weeks": [], "alerts": []})
    assert s["chart"] is None and s["line"] is None and s["min_buffer"] is None and s["progress_pct"] == 0.0


def test_ops_summary_gauges_steps_and_fairness():
    seg = {"assessed": 5, "insufficient": False}
    s = {
        "latency": {"p95_min": 8.0, "target_min": 10.0, "met": True},
        "agent_success": {"rate": 0.95, "met": False, "by_step": {"underwrite": {"ok": 3, "degraded": 1, "failed": 0},
                                                                  "assess": {"ok": 0, "degraded": 0, "failed": 0}}},
        "fairness": {"axes": {"industry": {"values": {
            "카페": {**seg, "approve_rate": 1.0}, "한식": {**seg, "approve_rate": 0.0},
            "편의점": {"assessed": 2, "insufficient": True, "approve_rate": 0.5},
            "기타": {"assessed": 0, "insufficient": True, "approve_rate": None}}}}},
    }
    u = dashboard.ops_summary(s, STEP_LABELS)
    assert (u["latency"]["pct"], u["latency_tone"], u["success"]["pct"], u["success_tone"]) == (40.0, "green", 95.0, "red")
    assert [r["label"] for r in u["steps"]] == ["신용 평가", "심사 지표 산출"] and u["steps"][0]["empty"]
    chart = u["fairness"]["industry"]
    assert [r["text"] for r in chart["refs"]] == ["100%", "0%"] and chart["vmax"] == 100.0
    parts = [g["bars"][0]["parts"][0] for g in chart["groups"]]
    assert [p["color"] for p in parts] == ["blue", "blue", "grey", "grey"]
    assert parts[2]["tip"].endswith("표본 부족") and parts[3]["tip"] == "기타 추천 없음"


def test_ops_summary_without_samples_is_neutral():
    u = dashboard.ops_summary({"latency": {"p95_min": None, "target_min": 10.0, "met": False},
                               "agent_success": {"rate": None, "met": False, "by_step": {}},
                               "fairness": {"axes": {}}}, STEP_LABELS)
    assert (u["latency_tone"], u["success_tone"], u["steps"], u["fairness"]) == ("neutral", "neutral", [], {})


def test_formatters():
    assert dashboard.elapsed_text(None) == "" and dashboard.elapsed_text(5) == "+5초"
    assert dashboard.elapsed_text(3725) == "+1시간 2분"
    assert dashboard.man(40_000_000) == "4,000만" and dashboard.man(0) == "0"
    assert dashboard.krw_text(1_000) == "1,000원" and dashboard.krw_text(None) == "-"
    assert dashboard.clock("bad") == "" and dashboard.clock(None) == ""


def test_status_labels_are_shared_with_app():
    from console import statuses
    assert STATUS_LABELS is statuses.STATUS_LABELS
