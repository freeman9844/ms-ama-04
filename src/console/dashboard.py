"""화면 집계(순수 함수, P7·P9): 홈 KPI·분포, 신청 7단계 진행, 사후 모니터링 요약, 운영 지표 게이지·패널.

저장된 값만 쓴다. 홈 KPI는 목록 쿼리 결과(최근 LIST_LIMIT건)로만 계산한다.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from agents.features import EXPENSE_GROUPS
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
# 지출 세부 그룹 색. 월별 현금흐름의 매출(파랑)·순현금흐름(초록)과 겹치지 않게 거래처·물류는 짙은 회색이다.
EXPENSE_COLORS = {
    "supplier": "neutral", "rent": "purple", "payroll": "amber", "tax_insurance": "teal", "other_operating": "orange",
    "debt_service": "red", "owner_draw": "pink", "unclassified": "grey",
}
NET_LABEL = "순현금흐름(매출−지출)"
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


RECONCILIATION_WARN = 0.90
TOP_SHARE_WARN = 0.80
# 표준 정산 주기 문구(mockapis/sellerdata/channels.py 정산 일정 근거는 agents/pg_ledger.py의 PLATFORM_LAG_DAYS 주석과 같다).
PLATFORM_CYCLES = {
    "스마트스토어": "구매확정 후 1영업일",
    "쿠팡": "주정산 70% + 최종액 30%(익익월 1일)",
    "11번가": "월 2회(25일·익월 10일)",
    "자사몰": "PG 2영업일",
}
# "지표 읽는 법" 접기 패널 문구. (제목, 설명) 순서는 카드에 보이는 순서와 다르다(스펙 §4.5 스케치).
SETTLEMENT_GUIDE: tuple[tuple[str, str], ...] = (
    ("정산금 계좌 입금", "정산 원장 실지급액 중 지급일부터 3영업일 안에 같은 금액이 이 계좌로 들어온 비율입니다. "
     "낮으면 매출이 이 계좌로 들어오지 않고 있다는 뜻입니다(기준 90% 이상, R108)."),
    ("월별 공제율", "그 달 정산 총액 중 플랫폼이 떼어 간 몫(수수료 + 광고비 상계)입니다. "
     "막대가 오르면 마진이 줄고 있다는 뜻입니다."),
    ("현금화 일수", "플랫폼별 표준 정산 주기를 매출 비중으로 가중 평균한 값입니다. "
     "실제 건별 지연이 아니라 추정치입니다."),
    ("선정산 추정", "계좌에 들어오지 않은 정산 중 금액·기간 조건이 맞는 선지급성 입금과 짝지어진 건수입니다. "
     "정산채권 양도 여부를 확인하는 단서입니다."),
    ("채널 집중도(HHI)", "플랫폼별 매출 비중 제곱의 합입니다. "
     "1에 가까울수록 한 채널에 쏠려 있다는 뜻이며, 참고 지표로 정책 기준은 없습니다. "
     "정책 기준(80% 이하, R107)은 최대 채널 비중 KPI에 적용됩니다."),
)


def _unmatched_cause(u: Mapping[str, Any]) -> str:
    if "advance" not in u:
        return "—"
    advance = u["advance"]
    if not isinstance(advance, Mapping):
        return "원인 미상 — 지연·다른 계좌·상계 확인"
    lead, amount, net = advance.get("lead_days"), advance.get("amount_krw"), u.get("net_krw")
    if not (charts.is_num(lead) and charts.is_num(amount) and charts.is_num(net) and net):
        return "원인 미상 — 지연·다른 계좌·상계 확인"
    return f"선지급 추정({lead}일 전, 정산액의 {round(amount / net * 100)}%)"


def settlement_card(pg: Mapping[str, Any] | None) -> dict[str, Any]:
    """정산 원장 대사 카드. 옛 문서(pg_ledger 없음)·자료 없음은 available=False."""
    if not isinstance(pg, Mapping) or not pg.get("available"):
        return {"available": False}
    recon, share, trend = pg.get("pg_reconciliation_ratio"), pg.get("pg_top_platform_share"), pg.get("pg_take_rate_trend_pp")
    cash_days, advance_n, advance_share = (pg.get("pg_cash_conversion_days"), pg.get("pg_advance_suspected_count"),
                                           pg.get("pg_advance_share"))
    pct = lambda v: f"{round(v * 100)}%" if charts.is_num(v) else "—"  # noqa: E731
    advance_sub = ("미입금 중 선지급 짝" if not charts.is_num(advance_share)
                  else f"미입금액의 {round(advance_share * 100)}%")
    kpis = [
        {"label": "정산금 계좌 입금", "value": pct(recon), "sub": "기준 90% 이상",
         "tone": "red" if charts.is_num(recon) and recon < RECONCILIATION_WARN else "green"},
        {"label": "최대 채널 비중", "value": pct(share), "sub": "기준 80% 이하",
         "tone": "amber" if charts.is_num(share) and share > TOP_SHARE_WARN else "blue"},
        {"label": "현금화 일수", "value": f"{cash_days:.1f}일" if charts.is_num(cash_days) else "—",
         "sub": "표준 정산 주기 기준 추정", "tone": "blue" if charts.is_num(cash_days) else "neutral"},
        {"label": "공제율 변화(6개월)", "value": f"{trend:+.1f}%p" if charts.is_num(trend) else "—",
         "sub": "수수료+광고비 상계", "tone": "amber" if charts.is_num(trend) and trend >= 1.0 else "blue"},
        {"label": "선정산 추정", "value": f"{advance_n}건" if charts.is_num(advance_n) else "—",
         "sub": advance_sub if charts.is_num(advance_n) else "",
         "tone": ("red" if advance_n >= 1 else "green") if charts.is_num(advance_n) else "neutral"},
    ]
    platforms_list = list(pg.get("platforms") or ())
    platforms = charts.hbars([(p["platform"], round(p["share"] * 100, 1), "blue") for p in platforms_list],
                             width=320, label_w=88)
    cycles = [{"platform": p["platform"], "cycle": PLATFORM_CYCLES.get(p["platform"], "—"),
               "lag_days": p.get("lag_days")} for p in platforms_list]
    monthly = list(pg.get("monthly_take_rate") or ())
    split = bool(monthly) and all(charts.is_num(m.get("fee_rate")) and charts.is_num(m.get("offset_rate"))
                                  for m in monthly)
    if split:
        groups = []
        for m in monthly:
            fee_pct, offset_pct = round(m["fee_rate"] * 100, 2), round(m["offset_rate"] * 100, 2)
            month = m["month"][2:]
            groups.append((month, [[("수수료", fee_pct, "purple", f"{month} 수수료 {fee_pct}%"),
                                    ("광고비 상계", offset_pct, "orange", f"{month} 광고비 상계 {offset_pct}%")]]))
        take = charts.columns(groups, width=320, height=200, tick_fmt=lambda v: f"{v:.1f}%")
        take_rate_legend = [("수수료", "purple"), ("광고비 상계", "orange")]
    else:
        take = charts.hbars([(m["month"][2:], round(m["rate"] * 100, 2), "purple") for m in monthly],
                            width=320, label_w=56)
        take_rate_legend = []
    unmatched = [{**u, "cause": _unmatched_cause(u)} for u in pg.get("unmatched") or ()]
    period = pg.get("period") or {}
    return {"available": True, "kpis": kpis, "guide": SETTLEMENT_GUIDE, "platforms": platforms, "cycles": cycles,
            "take_rate_kind": "split" if split else "single", "take_rate": take, "take_rate_legend": take_rate_legend,
            "unmatched": unmatched, "unmatched_count": pg.get("unmatched_count", 0),
            "note": f"{period.get('from', '')} ~ {period.get('to', '')} 정산 {pg.get('settlement_count', 0)}건"}


def detail_summary(view: Mapping[str, Any]) -> dict[str, Any]:
    doc = view["doc"]
    uw = doc.get("underwriting") or {}
    monthly = [m for m in uw.get("monthly") or [] if isinstance(m, Mapping)]
    # 세부 그룹(expense_monthly)은 2026-11-08 이후 심사분에만 있다. 한 달이라도 없으면 3구분으로 그린다.
    by_month = {r.get("month"): r for r in uw.get("expense_monthly") or [] if isinstance(r, Mapping)}
    detailed = bool(monthly) and all(m.get("month") in by_month for m in monthly)
    spec = [(g, label, EXPENSE_COLORS[g]) for g, label in EXPENSE_GROUPS] if detailed else EXPENSES
    groups, net = [], []
    for m in monthly:
        month = str(m.get("month") or "")
        src = by_month[m.get("month")] if detailed else m
        revenue = [("revenue", m.get("revenue"), "blue", f"{month} 매출 {krw_text(m.get('revenue'))}")]
        expenses = [(k, src.get(k), color, f"{month} {label} {krw_text(src.get(k))}") for k, label, color in spec]
        groups.append((month[2:] if len(month) == 7 else month, [revenue, expenses]))
        rev = m.get("revenue")
        net.append(rev - sum(v for _, v, _, _ in expenses if charts.is_num(v)) if charts.is_num(rev) else None)
    monthly_chart = charts.columns(groups, tick_fmt=man) if groups else None
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
        "monthly": monthly_chart,
        "monthly_net": charts.line(net, vmax=monthly_chart["vmax"], tick_fmt=man) if monthly_chart else None,
        "monthly_legend": [("매출", "blue")] + [(label, color) for _, label, color in spec] + [(NET_LABEL, "green")],
        "markers": markers,
        "counterfactuals": counterfactuals,
        "review": review,
        "gateway_blocks": gateway_blocks(doc),
        "settlement": settlement_card(uw.get("pg_ledger")),
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


def alert_followups(cases: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """미납 추심 건을 그 건이 근거로 본 최근 경보(recent_alert)에 묶는다. 경보 카드에 그 경보로 나간 연락을 보인다."""
    links: dict[str, list[dict[str, Any]]] = {}
    for c in cases:
        alert_id = (c.get("recent_alert") or {}).get("alert_id")
        if not alert_id:
            continue
        collections = c.get("collections") or {}
        links.setdefault(alert_id, []).append({
            "case_id": c.get("case_id"),
            "no": (c.get("installment") or {}).get("no"),
            "decision": collections.get("decision"),
            "restructure": (c.get("restructure") or {}).get("status"),
            "contacts": [{k: x.get(k) for k in ("at", "script_id", "status")} for x in collections.get("contacts") or ()],
        })
    return links


def _restructure_step(status: str | None) -> dict[str, Any]:
    return _step("restructure", "재조정 결정", "current" if status == "pending_officer" else "done",
                 RESTRUCTURE_NOTES.get(status, status or ""))


def alert_steps(alert: Mapping[str, Any], followups: Sequence[Mapping[str, Any]] = ()) -> list[dict[str, Any]]:
    collections = alert.get("collections") or {}
    head = [
        _step("detect", "탐지", "done", f"{alert.get('week')}주차"),
        _step("investigate", "조기경보 조사", "done", SEVERITY_LABELS.get(alert.get("severity") or "", "")),
    ]
    if not collections and not alert.get("restructure"):
        # 조기경보는 심사역 검토용이다. 차주 연락은 상환 원장의 미납 추심 건이 맡고, 그 건이 이 경보를 근거로 보면 잇는다.
        if not followups:
            return [*head, _step("review", "심사역 검토", "done", "연락 없음")]
        f = followups[-1]
        scripts = ", ".join(x["script_id"] for x in f["contacts"] if x.get("script_id"))
        steps = [*head, _step("followup", "미납 추심 연락", "done" if f["decision"] else "current",
                              f"{f['no']}회차" + (f" · {scripts}" if scripts else ""))]
        if f["restructure"]:
            steps.append(_restructure_step(f["restructure"]))
        return steps
    steps = [*head, _step("collections", "추심 에이전트", "done" if collections else "current", collections.get("decision") or "")]
    restructure = alert.get("restructure")
    if restructure:
        steps.append(_restructure_step(restructure.get("status")))
    return steps


def case_steps(case: Mapping[str, Any]) -> list[dict[str, Any]]:
    inst = case.get("installment") or {}
    collections = case.get("collections") or {}
    steps = [
        _step("missed", "미납", "done", f"{inst.get('no')}회차 {inst.get('due_on')}"),
        _step("collections", "추심 에이전트", "done" if collections else "current", collections.get("decision") or ""),
    ]
    restructure = case.get("restructure")
    if restructure:
        st = restructure.get("status")
        steps.append(_step("restructure", "재조정 결정", "current" if st == "pending_officer" else "done",
                           RESTRUCTURE_NOTES.get(st, st or "")))
    return steps


def _repayment(loan: Mapping[str, Any]) -> dict[str, int] | None:
    schedule = (loan.get("repayment") or {}).get("schedule")
    if not schedule:
        return None
    statuses = [s.get("status") for s in schedule]
    return {"paid": statuses.count("paid"), "total": len(statuses), "missed": statuses.count("missed")}


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
    followups = alert_followups(view.get("cases") or ())
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
        "alert_followups": followups,
        "alert_steps": {a.get("alert_id"): alert_steps(a, followups.get(a.get("alert_id"), ())) for a in alerts},
        "case_steps": {c.get("case_id"): case_steps(c) for c in view.get("cases") or ()},
        "repayment": _repayment(loan),
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
