"""화면 집계(순수 함수, P7·P9): 홈 KPI·분포, 신청 7단계 진행, 사후 모니터링 요약, 운영 지표 게이지·패널.

저장된 값만 쓴다. 홈 KPI는 목록 쿼리 결과(최근 LIST_LIMIT건)로만 계산한다.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from agents.forecast import OUTFLOW_LABELS
from console import charts
from console.monitoring import SEVERITY_LABELS, TRIGGER_LABELS
from console.statuses import FLAG_LABELS, STATUS_LABELS, TERMINAL
from opsmetrics.labels import STEP_LABELS

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
POLICY_DECISIONS = {"eligible": ("적격", "green"), "refer": ("심사역 회부", "amber"), "ineligible": ("부적격", "red")}
OFFICER_DECISIONS = {"approve": ("승인", "green"), "decline": ("거절", "red")}
FUNNEL_NONE = {"policy": "판정 없음", "recommendation": "권고 없음", "officer": "미결정"}
MODEL_COLORS = ("blue", "purple", "teal", "orange", "pink")
KST_OFFSET = timezone(timedelta(hours=9))
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
FORECAST_CHART = {"width": 960, "height": 260}
SHORT_OF_EXPECTED = 0.85  # 한 주 단위 주의 색. 탐지(inflow_drop)는 3주 합 0.75로 더 넓게 본다


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


def gateway_blocks(doc: Mapping[str, Any]) -> list[str]:
    """AI 게이트웨이 콘텐츠 차단이 있었던 단계 이름(처음 나온 순서, 중복 없음)."""
    labels: list[str] = []
    for step in doc.get("steps") or []:
        if any(u.get("gateway") == "content_blocked" for u in step.get("usage") or [] if isinstance(u, Mapping)):
            label = STEP_LABELS.get(step.get("step"), str(step.get("step")))
            if label not in labels:
                labels.append(label)
    return labels


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
        "gateway_blocks": gateway_blocks(doc),
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
        **_expected_vs_actual(weeks),
        "forecast": _forecast(weeks),
    }


def _expected_vs_actual(weeks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """주차별 실제 입금 막대와 기대 입금 선(같은 축)."""
    rows = [w.get("row") or {} for w in weeks]
    expected = [r.get("expected_revenue") for r in rows]
    if not any(charts.is_num(v) for v in expected):
        return {"expected_chart": None, "expected_line": None}
    groups = []
    for w, r, exp in zip(weeks, rows, expected, strict=True):
        rev = r.get("revenue")
        short = charts.is_num(exp) and charts.is_num(rev) and rev < exp * SHORT_OF_EXPECTED
        tip = f"{w.get('week')}주차 실제 {krw_text(rev)} · 기대 {krw_text(exp)}"
        groups.append((f"{w.get('week')}주", [[("revenue", rev, "amber" if short else "blue", tip)]]))
    top = max([v for v in expected + [r.get("revenue") for r in rows] if charts.is_num(v)], default=0)
    chart = charts.columns(groups, vmax=top or None, tick_fmt=man, **LOAN_CHART)
    return {"expected_chart": chart, "expected_line": charts.line(expected, vmax=chart["vmax"], **LOAN_CHART)}


def _forecast(weeks: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """가장 최근 주차의 4주 잔고 전망. 전망이 없는(정산 프로필이 없던) 대출은 None."""
    latest = next((w for w in reversed(weeks) if w.get("projection")), None)
    if latest is None:
        return None
    p, r = latest["projection"], latest.get("row") or {}
    totals = p.get("totals") or {}
    return {
        "week": latest.get("week"),
        "start_balance": p.get("start_balance"),
        "attainment_pct": round(float(p.get("attainment") or 0) * 100, 1),
        "min_balance": r.get("projected_min_balance"),
        "min_date": r.get("projected_min_date"),
        "shortfall_date": r.get("shortfall_date"),
        "chart": charts.signed_line([(d["date"][5:], d["balance"]) for d in p.get("path") or []],
                                    tick_fmt=man, **FORECAST_CHART),
        "totals": [(label, totals[k]) for k, label in {"inflow": "기대 입금", **OUTFLOW_LABELS}.items() if k in totals],
    }


def _pct_text(v: float) -> str:
    return f"{v:.0f}%"


def _funnel_parts(kind: str, counts: Mapping[str, int]) -> list[tuple[str, Any, str]]:
    labels = {"policy": POLICY_DECISIONS, "officer": OFFICER_DECISIONS,
              "recommendation": {k: (label, color) for k, label, color in RECOMMENDATIONS}}[kind]
    parts = []
    for key, n in counts.items():
        label, color = (FUNNEL_NONE[kind], "neutral") if key == "none" else labels.get(key, (key, "grey"))
        parts.append((label, n, color))
    return parts


def _seconds(ms: Any) -> float | None:
    return round(ms / 1000, 1) if charts.is_num(ms) else None


def _sec_text(v: float) -> str:
    return f"{v:,.0f}초" if v >= 10 else f"{v:,.1f}초"


def _bucket_label(iso: str, index: int) -> str:
    d = _dt(iso)
    return d.astimezone(KST_OFFSET).strftime("%H:%M") if d and index % 6 == 0 else ""


def ops_insights(s: Mapping[str, Any], step_labels: Mapping[str, str]) -> dict[str, Any]:
    """P9 §8 운영 패널 기하: 결정 퍼널, 단계별 처리 시간, AI 비용, 사유 코드·플래그 빈도, 10분 처리량."""
    funnel = s.get("funnel") or {}
    funnel_rows = charts.stacked([(title, _funnel_parts(kind, funnel.get(kind) or {}))
                                  for kind, title in (("policy", "정책 판정"), ("recommendation", "에이전트 권고"),
                                                      ("officer", "심사역 결정"))])
    lat_groups = []
    for step, r in (s.get("step_latency") or {}).items():
        name, p50, p95 = step_labels.get(step, step), _seconds(r.get("p50_ms")), _seconds(r.get("p95_ms"))
        lat_groups.append((name, [[("p50", p50, "blue", f"{name} p50 {p50}초 · {r.get('n')}회")],
                                  [("p95", p95, "purple", f"{name} p95 {p95}초 · {r.get('n')}회")]]))
    cost = s.get("ai_cost") or {}
    priced = [(m, round(v["cost_usd"], 4)) for m, v in (cost.get("by_model") or {}).items()
              if charts.is_num(v.get("cost_usd"))]
    model_donut = charts.donut([(m, c, MODEL_COLORS[i % len(MODEL_COLORS)]) for i, (m, c) in enumerate(priced)])
    model_donut["total"] = round(model_donut["total"], 4)
    step_cost = charts.hbars([(step_labels.get(k, k), round(v.get("cost_usd") or 0.0, 4), "purple")
                              for k, v in (cost.get("by_step") or {}).items()], width=480, label_w=120)
    reasons = charts.hbars([(r["key"], r["count"], "amber" if r["key"].startswith("R1") else "red")
                            for r in s.get("reason_codes") or []], width=480, label_w=72)
    flags = charts.hbars([(FLAG_LABELS.get(f["key"], f["key"]), f["count"], "orange") for f in s.get("flags") or []],
                         width=560, label_w=232)
    groups = []
    for i, b in enumerate((s.get("throughput") or {}).get("buckets") or []):
        at = _bucket_label(b["start"], 0)
        groups.append((_bucket_label(b["start"], i), [[("접수", b["received"], "blue", f"{at} 접수 {b['received']}건")],
                                                      [("심사역 대기 도달", b["reached"], "green",
                                                        f"{at} 심사역 대기 도달 {b['reached']}건")]]))
    throughput = charts.columns(groups, width=720, height=220) if groups else None
    return {
        "funnel": funnel_rows,
        "step_latency": charts.columns(lat_groups, width=640, height=220, tick_fmt=_sec_text) if lat_groups else None,
        "model_cost": model_donut,
        "model_colors": [(m, MODEL_COLORS[i % len(MODEL_COLORS)], f"${c:,.4f}") for i, (m, c) in enumerate(priced)],
        "step_cost": step_cost,
        "reasons": reasons,
        "flags": flags,
        "throughput": throughput,
    }


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
        **ops_insights(s, step_labels),
    }
