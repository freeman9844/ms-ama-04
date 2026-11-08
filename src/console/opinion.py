"""신청 상세 '에이전트 의견' 탭 계산. 순수 함수이고 그리기는 `_charts.html` 매크로가 한다.

입력은 `views.detail_view` 결과다(주장 근거 값·가명 처리는 이미 끝남). 반성 루프 기록(`assessment_run`)과
에이전트 단계 사용량(`steps`의 assess)은 신청 문서에서 읽는다. 도구 인자 본문은 보여주지 않고 개수만 센다.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from console import charts
from console.assessment import RECOMMENDATION_TONES
from console.dashboard import POLICY_DECISIONS, RECOMMENDATION_LABELS, krw_text

RECOMMENDATION_MEANINGS = {
    "approve": "정책 판정과 현금흐름 근거가 모두 대출을 지지합니다. 제안 구조대로 승인할 것을 권합니다.",
    "conditional": "승인할 만하지만 제안 구조(금액·기간·감액 월)를 조건으로 두길 권합니다. "
                   "콘솔 결정에는 조건부 승인이 없으므로 승인 금액과 사유로 반영합니다.",
    "refer": "근거가 엇갈려 에이전트가 판단을 미뤘습니다. 근거 거래와 지표를 직접 확인해 주세요.",
    "decline": "정책 판정이나 현금흐름 근거가 대출을 지지하지 않습니다.",
}
# 정책 판정별로 같은 방향인 권고. 정책 회부는 어떤 권고든 심사역 판단이 필요하다.
ALIGNED = {"eligible": {"approve", "conditional"}, "refer": {"approve", "conditional", "refer", "decline"},
           "ineligible": {"decline", "refer"}}
METRIC_LABELS = {
    "dscr": "DSCR", "cash_buffer_days": "현금 완충일수", "revenue_volatility": "매출 변동성",
    "tax_bank_match_ratio": "세금·계좌 일치율", "business_age_months": "업력(개월)",
    "months_of_history": "거래 이력(개월)", "avg_monthly_inflow_krw": "월평균 입금",
    "requested_amount_krw": "신청액", "requested_tenor_months": "신청 기간(개월)", "months_used": "사용 개월",
    "avg_monthly_noi_krw": "월평균 순영업이익", "avg_monthly_debt_service_krw": "월평균 기존 상환",
    "new_installment_krw": "이번 대출 월 상환액", "avg_monthly_owner_draw_krw": "월평균 대표자 인출",
    "dscr_after_owner_draw": "대표자 인출 후 DSCR", "unclassified_count": "미분류 거래 수",
    "high_cost_lender_count": "고금리 대부 거래 수", "loan_proceeds_count": "대출금 입금 수",
    "max_amount_krw": "정책 한도",
}
MONTHLY_LABELS = {"revenue": "매출", "operating_out": "운영비", "debt_service": "상환", "owner_draw": "대표자 인출",
                  "index": "매출 지수"}
KRW_MONTHLY = frozenset({"revenue", "operating_out", "debt_service", "owner_draw"})
# 근거 출처: (키, 이름, 색). 표·도넛·범례가 같은 순서를 쓴다.
SOURCES = (("M", "현금흐름 지표", "blue"), ("S", "월별 흐름", "teal"), ("P", "정책 결과", "purple"),
           ("txn", "근거 거래", "orange"), ("missing", "확인 안 됨", "red"))
TOOL_LABELS = {"get_metrics": "지표 조회", "get_monthly_series": "월별 흐름 조회",
               "get_settlement_summary": "정산 요약 조회",
               "query_transactions": "거래 조회", "code_interpreter": "코드 계산"}
SCHEDULE_LABELS = {"level": "균등 상환", "seasonal": "계절 감액 상환"}
INDEX_CHART = {"width": 480, "height": 220}
ISSUES_CHART = {"width": 480, "height": 200}
LOW_INDEX = 0.8  # 감액 월이 없어도 평균의 80% 미만인 달은 주의 색으로 보인다.


def _num_text(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:,.3g}" if abs(v) < 1000 else f"{v:,.0f}"


def evidence_label(metric_id: str) -> str:
    """근거 ID → 사람이 읽는 이름. 모르는 형식은 ID 그대로."""
    parts = metric_id.split(":")
    if parts[0] == "M" and len(parts) == 2:
        return METRIC_LABELS.get(parts[1], parts[1])
    if parts[0] == "S" and len(parts) == 3:
        return f"{parts[1]} {MONTHLY_LABELS.get(parts[2], parts[2])}"
    if parts[0] == "P" and len(parts) == 2:
        return METRIC_LABELS.get(parts[1], parts[1])
    if parts[0] == "P" and len(parts) == 3:
        return f"정책 {parts[1]} {'관측값' if parts[2] == 'observed' else '기준값'}"
    return metric_id


def evidence_value(metric_id: str, value: Any) -> str:
    if not charts.is_num(value):
        return "없음"
    field = metric_id.split(":")[-1]
    if field.endswith("_krw") or field in KRW_MONTHLY:
        return krw_text(value)
    return _num_text(value)


def _source(metric_id: str) -> str:
    head = metric_id.split(":", 1)[0]
    return head if head in ("M", "S", "P") else "M"


def _recommendation(assessment: Mapping[str, Any], policy: Mapping[str, Any]) -> dict[str, Any]:
    rec = assessment.get("recommendation") or ""
    decision = policy.get("decision")
    decision_label, decision_tone = POLICY_DECISIONS.get(decision, (decision or "판정 없음", "neutral"))
    aligned = rec in ALIGNED.get(decision, set())
    if not decision:
        relation = "정책 판정이 없어 권고만 참고합니다."
    elif aligned:
        relation = "정책 판정과 에이전트 권고가 같은 방향입니다."
    else:
        relation = "정책 판정과 에이전트 권고가 엇갈립니다. 근거 표와 근거 거래를 먼저 확인하세요."
    return {
        "label": RECOMMENDATION_LABELS.get(rec, rec or "권고 없음"), "tone": RECOMMENDATION_TONES.get(rec, "neutral"),
        "meaning": RECOMMENDATION_MEANINGS.get(rec, ""), "policy": decision_label, "policy_tone": decision_tone,
        "aligned": aligned, "relation": relation,
    }


def _claims(claims: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    counts = {key: 0 for key, _, _ in SOURCES}
    rows = []
    for i, c in enumerate(claims, start=1):
        evidence = []
        for metric_id, value in c.get("evidence") or ():
            missing = value is None
            counts["missing" if missing else _source(metric_id)] += 1
            evidence.append({"id": metric_id, "label": evidence_label(metric_id),
                             "value": evidence_value(metric_id, value), "missing": missing})
        txn_ids = list(c.get("txn_ids") or ())
        counts["txn"] += len(txn_ids)
        rows.append({"n": i, "text": c.get("text", ""), "evidence": evidence, "txn_ids": txn_ids,
                     "ok": not any(e["missing"] for e in evidence), "sources": len(evidence) + len(txn_ids)})
    return rows, counts


def _evidence_mix(counts: Mapping[str, int]) -> dict[str, Any] | None:
    segments = [(label, counts[key], color) for key, label, color in SOURCES]
    if not any(v for _, v, _ in segments):
        return None
    return {"donut": charts.donut(segments), "legend": [(label, color, f"{v}건") for label, v, color in segments if v]}


def _season(monthly: Sequence[Mapping[str, Any]], reduced: Sequence[int]) -> dict[str, Any] | None:
    revenue = [m.get("revenue") for m in monthly]
    nums = [v for v in revenue if charts.is_num(v)]
    mean = sum(nums) / len(nums) if nums else 0
    if mean <= 0:
        return None
    groups = []
    for m, rev in zip(monthly, revenue, strict=True):
        month = str(m.get("month") or "")
        idx = round(rev / mean, 2) if charts.is_num(rev) else None
        cal = int(month[5:7]) if len(month) == 7 and month[5:7].isdigit() else None
        is_reduced = cal in reduced
        color = "amber" if is_reduced else ("red" if idx is not None and idx < LOW_INDEX else "blue")
        tip = f"{month} 매출 지수 {idx if idx is not None else '-'}" + (" · 감액 월" if is_reduced else "")
        groups.append((month[2:] if len(month) == 7 else month, [[("index", idx, color, tip)]]))
    chart = charts.columns(groups, refs=[("평균", 1.0, "purple")], **INDEX_CHART)
    used = {bar[0][2] for _, bars in groups for bar in bars}
    legend = [(label, color) for label, color in (("평소 달", "blue"), (f"평균의 {LOW_INDEX:.0%} 미만", "red"),
                                                  ("제안된 감액 월", "amber")) if color in used]
    return {"chart": chart, "legend": [*legend, ("평균 = 1.0(선)", "purple")]}


def _structure(assessment: Mapping[str, Any], doc: Mapping[str, Any], policy: Mapping[str, Any],
               monthly: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    st = assessment.get("proposed_structure")
    if not isinstance(st, Mapping):
        return None
    amount, tenor = st.get("amount_krw"), st.get("tenor_months")
    requested, limit = doc.get("requested_amount_krw"), policy.get("max_amount_krw")
    reduced = [m for m in st.get("reduced_payment_months") or () if isinstance(m, int)]
    principal = amount / tenor if charts.is_num(amount) and charts.is_num(tenor) and tenor > 0 else None
    bars = [(label, round(v / 10_000), color) for label, v, color in
            (("신청액", requested, "grey"), ("정책 한도", limit, "purple"), ("제안 금액", amount, "blue"))
            if charts.is_num(v)]
    rows = [
        ("제안 금액", krw_text(amount)),
        ("신청액 대비", f"{amount / requested:.0%}" if charts.is_num(amount) and charts.is_num(requested)
         and requested > 0 else "-"),
        ("기간", f"{tenor}개월" if charts.is_num(tenor) else "-"),
        ("상환 방식", SCHEDULE_LABELS.get(st.get("schedule"), st.get("schedule") or "-")),
        ("감액 월", ", ".join(f"{m}월" for m in reduced) or "없음"),
        ("월 원금(균등 기준)", krw_text(round(principal)) if principal is not None else "-"),
    ]
    return {"rows": rows, "bars": charts.hbars(bars, width=420, label_w=80) if bars else None,
            "rationale": st.get("rationale") or "", "season": _season(monthly, reduced)}


def _counterfactuals(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for cf in items:
        metric_id, cur, tgt = str(cf.get("metric_id") or ""), cf.get("current"), cf.get("target")
        delta = pct = None
        if charts.is_num(cur) and charts.is_num(tgt):
            delta = tgt - cur
            pct = delta / abs(cur) * 100 if cur else None
        rows.append({
            "label": evidence_label(metric_id), "id": metric_id,
            "current": evidence_value(metric_id, cur), "target": evidence_value(metric_id, tgt),
            "change": ("+" if delta and delta > 0 else "") + evidence_value(metric_id, delta) if delta is not None
            else "-",
            "pct": f"{pct:+.0f}%" if pct is not None else "-",
            "direction": "올리기" if delta and delta > 0 else "낮추기" if delta and delta < 0 else "유지",
            "explanation": cf.get("explanation") or "",
        })
    return rows


def _round(entry: Mapping[str, Any]) -> dict[str, Any]:
    analyst = entry.get("analyst") if isinstance(entry.get("analyst"), Mapping) else {}
    reviewer = entry.get("reviewer") if isinstance(entry.get("reviewer"), Mapping) else {}
    grounding = list(entry.get("grounding_issues") or [])
    review = list(entry.get("review_issues") or [])
    if entry.get("analyst_error"):
        outcome, tone = "분석 실패", "red"
    elif grounding:
        outcome, tone = "근거 보완 요청", "amber"
    elif entry.get("reviewer_error"):
        outcome, tone = "검토 실패", "red"
    elif review:
        outcome, tone = "검토 지적", "amber"
    elif "reviewer" in entry:
        outcome, tone = "통과", "green"
    else:
        outcome, tone = "-", "neutral"
    reviewer_text = ("건너뜀(근거 검사 미통과)" if grounding else "실패" if entry.get("reviewer_error")
                     else f"지적 {len(review)}건" if "reviewer" in entry else "-")
    return {
        "n": entry.get("round"), "tools": len(analyst.get("tool_calls") or ()),
        "tokens": int(analyst.get("total_tokens") or 0) + int(reviewer.get("total_tokens") or 0),
        "grounding": len(grounding), "review": len(review), "reviewer": reviewer_text,
        "outcome": outcome, "tone": tone,
        "issues": [("근거 검사", i) for i in grounding] + [("검토", i) for i in review]
        + ([("오류", str(entry["analyst_error"]))] if entry.get("analyst_error") else [])
        + ([("오류", str(entry["reviewer_error"]))] if entry.get("reviewer_error") else []),
    }


def _loop(run: Mapping[str, Any] | None) -> dict[str, Any] | None:
    rounds = [r for r in (run or {}).get("rounds") or () if isinstance(r, Mapping)]
    if not rounds:
        return None
    tools: dict[str, int] = {}
    for r in rounds:
        for call in ((r.get("analyst") or {}).get("tool_calls") or ()):
            name = str((call or {}).get("name") or "")
            tools[name] = tools.get(name, 0) + 1
    tool_bars = [(TOOL_LABELS.get(n, n), c, "blue") for n, c in sorted(tools.items(), key=lambda kv: -kv[1])]
    rows = [_round(r) for r in rounds]
    groups = [(f"{r['n']}라운드", [[("grounding", r["grounding"], "amber", f"{r['n']}라운드 근거 검사 {r['grounding']}건"),
                                     ("review", r["review"], "red", f"{r['n']}라운드 검토 지적 {r['review']}건")]])
              for r in rows]
    return {
        "rows": rows,
        "tools": charts.hbars(tool_bars, width=420, label_w=110) if tool_bars else None,
        "issues_chart": charts.columns(groups, **ISSUES_CHART) if any(r["grounding"] or r["review"] for r in rows)
        else None,
    }


def _usage(doc: Mapping[str, Any], models: Mapping[str, str]) -> dict[str, Any] | None:
    step = next((s for s in reversed(doc.get("steps") or []) if isinstance(s, Mapping) and s.get("step") == "assess"),
                None)
    if step is None:
        return None
    role_of = {m: r for r, m in models.items()}
    by_model: dict[str, dict[str, int]] = {}
    for u in step.get("usage") or ():
        m = str(u.get("model") or "")
        agg = by_model.setdefault(m, {"calls": 0, "input": 0, "output": 0})
        agg["calls"] += int(u.get("calls") or 0)
        agg["input"] += int(u.get("input_tokens") or 0)
        agg["output"] += int(u.get("output_tokens") or 0)
    roles = {"analyst": "분석", "reviewer": "검토"}
    rows = [{"model": m, "role": roles.get(role_of.get(m, ""), "-"), **v} for m, v in by_model.items()]
    colors = ("blue", "purple", "teal", "orange")
    bars = [(r["model"], r["input"] + r["output"], colors[i % len(colors)]) for i, r in enumerate(rows)]
    ms = step.get("duration_ms")
    return {
        "rows": rows, "bars": charts.hbars(bars, width=420, label_w=110) if any(b[1] for b in bars) else None,
        "seconds": round(ms / 1000, 1) if charts.is_num(ms) else None,
        "tokens": sum(r["input"] + r["output"] for r in rows),
    }


def opinion_panel(view: Mapping[str, Any]) -> dict[str, Any] | None:
    assessment = view.get("assessment")
    if not assessment:
        return None
    doc = view["doc"]
    policy = view.get("policy") or {}
    monthly = [m for m in (doc.get("underwriting") or {}).get("monthly") or [] if isinstance(m, Mapping)]
    claims, counts = _claims(view.get("claims") or ())
    review = assessment.get("review") or {}
    usage = _usage(doc, assessment.get("models") or {})
    ok = sum(1 for c in claims if c["ok"])
    return {
        "recommendation": _recommendation(assessment, policy),
        "kpi": {
            "grounded": f"{ok}/{len(claims)}", "grounded_tone": "green" if claims and ok == len(claims) else "amber",
            "review": ("통과" if review.get("passed") else "미통과") if review else "-",
            "review_sub": f"{review['rounds']}라운드" if charts.is_num(review.get("rounds")) else "",
            "review_tone": "green" if review.get("passed") else ("red" if review else "neutral"),
            "tokens": f"{usage['tokens']:,}" if usage else "-",
            "tokens_sub": f"에이전트 단계 {usage['seconds']}초" if usage and usage["seconds"] is not None else "",
        },
        "claims": claims,
        "mix": _evidence_mix(counts),
        "structure": _structure(assessment, doc, policy, monthly),
        "counterfactuals": _counterfactuals(assessment.get("counterfactuals") or ()),
        "loop": _loop(view.get("assessment_run")),
        "usage": usage,
        "models": assessment.get("models") or {},
    }
