"""화면 집계(순수 함수, P7): 홈 KPI·분포, 신청 7단계 진행, 사후 모니터링 요약, 운영 지표 게이지.

저장된 값만 쓴다. 홈 KPI는 목록 쿼리 결과(최근 LIST_LIMIT건)로만 계산한다.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from console import charts
from console.monitoring import SEVERITY_LABELS, TRIGGER_LABELS
from console.statuses import STATUS_LABELS, TERMINAL

AWAITING = frozenset({"awaiting_officer", "escalated"})
APPROVED = frozenset({"approved", "conditionally_approved"})
DECIDED = APPROVED | {"declined"}
CLOSED = frozenset({"withdrawn", "closed_no_decision", "failed"})
GROUPS = (
    ("approved", "승인·조건부", "green"),
    ("awaiting", "심사역 대기", "amber"),
    ("declined", "거절", "red"),
    ("closed", "기타 종료", "neutral"),
    ("progress", "진행 중", "blue"),
)
RECOMMENDATIONS = (
    ("approve", "승인", "green"),
    ("conditional", "승인 권고(조건 제안)", "blue"),
    ("refer", "심사역 회부", "purple"),
    ("decline", "거절", "red"),
    ("none", "권고 없음", "neutral"),
)
RECOMMENDATION_LABELS = {key: label for key, label, _ in RECOMMENDATIONS}
STEPS = (
    ("received", "접수"),
    ("consent", "동의 완료"),
    ("data", "데이터 수집"),
    ("underwrite", "심사 지표 산출"),
    ("assess", "에이전트 평가"),
    ("officer", "심사역 대기"),
    ("decision", "결정"),
)
STEP_OF = {
    "received": 0,
    "consent_granted": 1,
    "data_assembled": 2,
    "data_incomplete": 2,
    "underwritten": 3,
    "assessed": 4,
    "awaiting_officer": 5,
    "escalated": 5,
    "approved": 6,
    "conditionally_approved": 6,
    "declined": 6,
}
STEP_NOTES = {"data_incomplete": "데이터 부족", "escalated": "SLA 초과"}
EXPENSES = (("operating_out", "운영비", "amber"), ("debt_service", "상환", "red"), ("owner_draw", "대표자 인출", "purple"))
RESTRUCTURE_NOTES = {"pending_officer": "심사역 대기", "approved": "승인", "declined": "거절"}
REPLAY_WEEKS = 13
LOAN_CHART = {"width": 960, "height": 300}  # 13주 막대와 버퍼 선이 같은 칸을 쓰도록 크기를 맞춘다.


def _dt(iso: Any) -> datetime | None:
    if not isinstance(iso, str):
        return None
    try:
        return datetime.fromisoformat(iso)
    except ValueError:
        return None


def clock(iso: Any) -> str:
    d = _dt(iso)
    return d.strftime("%H:%M:%S") if d else ""


def elapsed_text(seconds: int | None) -> str:
    if seconds is None:
        return ""
    if seconds < 60:
        return f"+{seconds}초"
    if seconds < 3600:
        return f"+{seconds // 60}분 {seconds % 60}초"
    return f"+{seconds // 3600}시간 {seconds % 3600 // 60}분"


def krw_text(v: Any) -> str:
    return f"{v:,.0f}원" if charts.is_num(v) else "-"


def man(v: float) -> str:
    """금액 축 눈금(만 원 단위)."""
    return f"{v / 10_000:,.0f}만" if abs(v) >= 10_000 else f"{v:,.0f}"


def _int(v: Any) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def _step(key: str, label: str, state: str, note: str = "") -> dict[str, Any]:
    return {"key": key, "label": label, "state": state, "at": None, "time": "", "elapsed": "", "elapsed_s": None,
            "note": note}


def _group(status: Any) -> str:
    if status in APPROVED:
        return "approved"
    if status in AWAITING:
        return "awaiting"
    if status == "declined":
        return "declined"
    if status in TERMINAL:
        return "closed"
    return "progress"


def home_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = dict.fromkeys((k for k, _, _ in GROUPS), 0)
    recs = dict.fromkeys((k for k, _, _ in RECOMMENDATIONS), 0)
    escalated = 0
    for r in rows:
        counts[_group(r.get("status"))] += 1
        escalated += r.get("status") == "escalated"
        rec = r.get("recommendation")
        recs[rec if rec in recs else "none"] += 1
    decided = counts["approved"] + counts["declined"]
    groups = [(label, color, counts[k]) for k, label, color in GROUPS]
    recommendations = [(label, recs[k], color) for k, label, color in RECOMMENDATIONS]
    return {
        "total": len(rows),
        "awaiting": counts["awaiting"],
        "escalated": escalated,
        "in_progress": counts["progress"],
        "approved": counts["approved"],
        "declined": counts["declined"],
        "approval_rate": counts["approved"] / decided if decided else None,
        "groups": groups,
        "donut": charts.donut([(label, n, color) for label, color, n in groups]),
        "recommendations": recommendations,
        "rec_bars": charts.hbars(recommendations),
    }


def journey_steps(doc: Mapping[str, Any]) -> list[dict[str, Any]]:
    """7단계 진행. 단계마다 처음 도달한 시각과 직전 시각이 있는 단계로부터 걸린 초."""
    status = doc.get("status")
    timeline = [t for t in doc.get("timeline") or [] if isinstance(t, Mapping)]
    if status and all(t.get("status") != status for t in timeline):
        timeline.append({"status": status, "at": None})
    first_at: dict[int, Any] = {}
    notes: dict[int, str] = {}
    last, closed_at = -1, None
    for t in timeline:
        s = t.get("status")
        if s in CLOSED:
            closed_at = closed_at or t.get("at")
            continue
        i = STEP_OF.get(s)
        if i is None:
            continue
        first_at.setdefault(i, t.get("at"))
        last = max(last, i)
        if s in STEP_NOTES:
            notes[i] = STEP_NOTES[s]
    fail_at = min(last + 1, len(STEPS) - 1) if status in CLOSED else None
    finished = status in DECIDED or fail_at is not None
    steps, prev = [], None
    for i, (key, label) in enumerate(STEPS):
        at = first_at.get(i)
        if fail_at is not None and i == fail_at:
            state, label, at = "failed", STATUS_LABELS.get(status, status), closed_at
        elif fail_at is not None and i > fail_at:
            state = "todo"
        elif i < last or (i == last and finished):
            state = "done"
        elif i == last:
            state = "current"
        else:
            state = "todo"
        if i == len(STEPS) - 1 and status in DECIDED:
            label = STATUS_LABELS[status]
        note = notes.get(i, "")
        if note and state in ("done", "current"):
            state = "warn"
        elapsed = None
        a, b = _dt(at), _dt(prev)
        if a and b:
            try:
                elapsed = int((a - b).total_seconds())
            except TypeError:
                elapsed = None
            if elapsed is not None and elapsed < 0:
                elapsed = None
        if a:
            prev = at
        steps.append({"key": key, "label": label, "state": state, "at": at, "time": clock(at),
                      "elapsed": elapsed_text(elapsed), "elapsed_s": elapsed, "note": note})
    return steps


def detail_summary(view: Mapping[str, Any]) -> dict[str, Any]:
    doc = view["doc"]
    monthly = [m for m in (doc.get("underwriting") or {}).get("monthly") or [] if isinstance(m, Mapping)]
    groups = []
    for m in monthly:
        month = str(m.get("month") or "")
        revenue = [("revenue", m.get("revenue"), "blue", f"{month} 매출 {krw_text(m.get('revenue'))}")]
        expenses = [(k, m.get(k), color, f"{month} {label} {krw_text(m.get(k))}") for k, label, color in EXPENSES]
        groups.append((month[2:] if len(month) == 7 else month, [revenue, expenses]))
    markers = []
    for r in (view.get("policy") or {}).get("reason_codes") or []:
        bar = charts.marker_bar(r.get("observed"), r.get("threshold"))
        if bar:
            markers.append({"code": r.get("code"), "message": r.get("message"), "observed": r.get("observed"),
                            "threshold": r.get("threshold"), "bar": bar})
    assessment = view.get("assessment") or {}
    counterfactuals = []
    for cf in assessment.get("counterfactuals") or []:
        cur, tgt = cf.get("current"), cf.get("target")
        comparable = charts.is_num(cur) and charts.is_num(tgt) and cur >= 0 and tgt >= 0
        bar = charts.hbars([("현재", cur, "neutral"), ("목표", tgt, "blue")], width=300, label_w=48)
        counterfactuals.append({**cf, "bar": bar if comparable else None})
    review = None
    if assessment.get("review"):
        rv = assessment["review"]
        rounds = rv.get("rounds")
        review = [
            _step("analyst", "분석 에이전트", "done", (assessment.get("models") or {}).get("analyst") or ""),
            _step("reviewer", "검토 에이전트", "done" if rv.get("passed") else "failed",
                  f"{rounds}회" if rounds is not None else ""),
        ]
    return {
        "steps": journey_steps(doc),
        "monthly": charts.columns(groups, tick_fmt=man) if groups else None,
        "monthly_legend": [("매출", "blue")] + [(label, color) for _, label, color in EXPENSES],
        "markers": markers,
        "counterfactuals": counterfactuals,
        "review": review,
    }


def loans_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    statuses = [r.get("monitoring_status") for r in rows]
    return {
        "total": len(rows),
        "replaying": statuses.count("replaying"),
        "completed": statuses.count("completed"),
        "not_started": statuses.count(None),
        "alerts": sum(_int(r.get("alerts")) for r in rows),
    }


def alert_steps(alert: Mapping[str, Any]) -> list[dict[str, Any]]:
    collections = alert.get("collections") or {}
    steps = [
        _step("detect", "탐지", "done", f"{alert.get('week')}주차"),
        _step("investigate", "조기경보 조사", "done", SEVERITY_LABELS.get(alert.get("severity") or "", "")),
        _step("collections", "추심 에이전트", "done" if collections else "current", collections.get("decision") or ""),
    ]
    restructure = alert.get("restructure")
    if restructure:
        st = restructure.get("status")
        steps.append(_step("restructure", "재조정 결정", "current" if st == "pending_officer" else "done",
                           RESTRUCTURE_NOTES.get(st, st or "")))
    return steps


def loan_summary(view: Mapping[str, Any]) -> dict[str, Any]:
    loan, mon, weeks, alerts = view["loan"], view["monitoring"] or {}, view["weeks"], view["alerts"]
    baseline = loan.get("baseline") or {}
    processed = _int(mon.get("processed_week"))
    severity = dict.fromkeys(("high", "medium", "low"), 0)
    for a in alerts:
        if a.get("severity") in severity:
            severity[a["severity"]] += 1
    buffers = [(w.get("week"), (w.get("row") or {}).get("buffer_days")) for w in weeks]
    numeric = [(wk, b) for wk, b in buffers if charts.is_num(b)]
    min_week, min_buffer = min(numeric, key=lambda x: x[1]) if numeric else (None, None)
    groups = []
    for w in weeks:
        r = w.get("row") or {}
        triggers = w.get("triggers") or []
        buffer = r.get("buffer_days")
        tip = (f"{w.get('week')}주차 {r.get('start', '')}~{r.get('end', '')} · 매출 {krw_text(r.get('revenue'))}"
               f" · 버퍼 {buffer if charts.is_num(buffer) else '-'}일")
        if triggers:
            tip += " · " + ", ".join(TRIGGER_LABELS.get(t, t) for t in triggers)
        groups.append((f"{w.get('week')}주", [[("revenue", r.get("revenue"), "red" if triggers else "blue", tip)]]))
    base_rev = baseline.get("avg_weekly_revenue")
    refs = [("기준 주매출", base_rev, "neutral")] if charts.is_num(base_rev) else []
    chart = charts.columns(groups, refs=refs, tick_fmt=man, **LOAN_CHART) if groups else None
    markers = []
    if chart:
        for w, g in zip(weeks, chart["groups"], strict=True):
            if w.get("new_triggers"):
                names = ", ".join(TRIGGER_LABELS.get(t, t) for t in w["new_triggers"])
                markers.append({"x": g["cx"], "y": round(g["bars"][0]["top"] - 6, 2), "week": w.get("week"),
                                "tip": f"{w.get('week')}주차 새 트리거: {names}"})
    return {
        "processed": processed,
        "weeks_total": REPLAY_WEEKS,
        "progress_pct": round(min(max(processed / REPLAY_WEEKS, 0.0), 1.0) * 100, 1),
        "severity": severity,
        "alerts": len(alerts),
        "min_buffer": min_buffer,
        "min_buffer_week": min_week,
        "baseline_buffer": baseline.get("cash_buffer_days"),
        "chart": chart,
        "line": charts.line([b for _, b in buffers], **LOAN_CHART) if groups else None,
        "markers": markers,
        "legend": [("매출", "blue"), ("트리거 주", "red"), ("기준 주매출", "neutral"), ("현금 버퍼(일, 오른쪽 축)", "purple")],
        "alert_steps": {a.get("alert_id"): alert_steps(a) for a in alerts},
    }


def _pct_text(v: float) -> str:
    return f"{v:.0f}%"


def ops_summary(s: Mapping[str, Any], step_labels: Mapping[str, str]) -> dict[str, Any]:
    lat = s.get("latency") or {}
    target = lat.get("target_min")
    latency = charts.gauge(lat.get("p95_min"), target * 2 if charts.is_num(target) else None)
    success_src = s.get("agent_success") or {}
    success = charts.gauge(success_src.get("rate"), 1.0)
    steps = charts.stacked([
        (step_labels.get(name, name), [("ok", c.get("ok"), "green"), ("degraded", c.get("degraded"), "amber"),
                                       ("failed", c.get("failed"), "red")])
        for name, c in sorted((success_src.get("by_step") or {}).items())
    ])
    fairness = {}
    for axis, a in ((s.get("fairness") or {}).get("axes") or {}).items():
        values = a.get("values") or {}
        groups, sufficient = [], []
        for value, r in values.items():
            rate = r.get("approve_rate")
            pct = rate * 100 if charts.is_num(rate) else None
            if pct is None:
                tip = f"{value} 추천 없음"
            else:
                tip = f"{value} 추천 approve율 {pct:.1f}% · 추천 완료 {r.get('assessed')}건"
                if r.get("insufficient"):
                    tip += " · 표본 부족"
                else:
                    sufficient.append(pct)
            groups.append((value, [[("approve_rate", pct, "grey" if r.get("insufficient") else "blue", tip)]]))
        tone = "red" if a.get("alert") else "neutral"
        refs = [("최대", max(sufficient), tone), ("최소", min(sufficient), tone)] if len(sufficient) >= 2 else []
        fairness[axis] = (charts.columns(groups, height=200, refs=refs, vmax=100, tick_fmt=_pct_text)
                          if groups else None)
    return {
        "latency": latency,
        "latency_tone": "neutral" if latency["frac"] is None else ("green" if lat.get("met") else "red"),
        "success": success,
        "success_tone": "neutral" if success["frac"] is None else ("green" if success_src.get("met") else "red"),
        "steps": steps,
        "fairness": fairness,
    }
