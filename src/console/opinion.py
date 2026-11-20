"""신청 상세 '에이전트 의견' 탭 계산. 순수 함수이고 그리기는 `_charts.html` 매크로가 한다.

입력은 `views.detail_view` 결과다(주장 근거 값·가명 처리는 이미 끝남). 반성 루프 기록(`assessment_run`)과
에이전트 단계 사용량(`steps`의 assess)은 신청 문서에서 읽는다. 도구 인자 본문은 보여주지 않고 개수만 센다.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from console import charts, i18n
from console.assessment import RECOMMENDATION_TONES
from console.dashboard import POLICY_DECISIONS, RECOMMENDATION_LABELS
from console.i18n import _

RECOMMENDATION_MEANINGS = {
    "approve": "현재 근거는 신청액 전액 승인을 지지합니다. 최종 결정은 심사역이 합니다.",
    "refer": "자료가 부족하거나 상충합니다. 근거를 확인한 뒤 신청액 전액을 재심사하세요.",
    "decline": "현재 정책 또는 확인된 근거가 신청액 전액 승인을 지지하지 않습니다.",
}
ALIGNED = {"eligible": {"approve"}, "refer": {"refer"}, "ineligible": {"decline"}}
METRIC_LABELS = {
    "dscr": "DSCR", "cash_buffer_days": "현금 완충일수", "revenue_volatility": "매출 변동성",
    "tax_bank_match_ratio": "세금·계좌 일치율", "business_age_months": "업력(개월)",
    "months_of_history": "거래 이력(개월)", "avg_monthly_inflow_krw": "월평균 입금",
    "requested_amount_krw": "신청액", "requested_tenor_months": "신청 기간(개월)", "months_used": "사용 개월",
    "avg_monthly_noi_krw": "월평균 순영업이익", "avg_monthly_debt_service_krw": "월평균 기존 상환",
    "new_installment_krw": "이번 대출 월 상환액", "avg_monthly_owner_draw_krw": "월평균 대표자 인출",
    "dscr_after_owner_draw": "대표자 인출 후 DSCR", "unclassified_count": "미분류 거래 수",
    "high_cost_lender_count": "고비용 대출 상환 거래 수", "loan_proceeds_count": "대출금 입금 수",
    "max_amount_krw": "정책 한도",
    "pg_reconciliation_ratio": "정산금 계좌 입금 비율", "pg_top_platform_share": "최대 채널 비중",
    "pg_hhi": "채널 집중도(HHI)", "pg_take_rate_trend_pp": "공제율 변화(%p)",
    "pg_cash_conversion_days": "현금화 일수", "pg_offset_rate_trend_pp": "광고비 상계율 변화(%p)",
    "pg_advance_share": "선지급 추정 비중", "pg_advance_suspected_count": "선정산 추정 건수",
    "pg_unmatched_count": "미입금 정산 건수",
    "revenue_trend_ratio": "최근 매출 추세",
}
MONTHLY_LABELS = {"revenue": "매출", "operating_out": "운영비", "debt_service": "상환", "owner_draw": "대표자 인출",
                  "index": "매출 지수"}
KRW_MONTHLY = frozenset({"revenue", "operating_out", "debt_service", "owner_draw"})
# 근거 출처: (키, 이름, 색). 표·도넛·범례가 같은 순서를 쓴다. 이름은 화면에 낼 때 번역한다.
SOURCES = (("M", "현금흐름 지표", "blue"), ("S", "월별 흐름", "teal"), ("P", "정책 결과", "purple"),
           ("txn", "근거 거래", "orange"), ("missing", "확인 안 됨", "red"))
TOOL_LABELS = {"get_metrics": "지표 조회", "get_monthly_series": "월별 흐름 조회",
               "get_settlement_summary": "정산 원장 대사 조회",
               "query_transactions": "거래 조회", "code_interpreter": "코드 계산"}
ISSUES_CHART = {"width": 480, "height": 200}


def _num_text(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:,.3g}" if abs(v) < 1000 else f"{v:,.0f}"


def evidence_label(metric_id: str) -> str:
    """근거 ID → 사람이 읽는 이름. 모르는 형식은 ID 그대로."""
    parts = metric_id.split(":")
    if parts[0] == "M" and len(parts) == 2:
        return i18n.label(METRIC_LABELS, parts[1])
    if parts[0] == "S" and len(parts) == 3:
        return f"{parts[1]} {i18n.label(MONTHLY_LABELS, parts[2])}"
    if parts[0] == "P" and len(parts) == 2:
        return i18n.label(METRIC_LABELS, parts[1])
    if parts[0] == "P" and len(parts) == 3:
        if parts[2] == "observed":
            return _("정책 %(code)s 관측값", code=parts[1])
        return _("정책 %(code)s 기준값", code=parts[1])
    return metric_id


def evidence_value(metric_id: str, value: Any) -> str:
    if not charts.is_num(value):
        return _("없음")
    field = metric_id.split(":")[-1]
    if field.endswith("_krw") or field in KRW_MONTHLY:
        return charts.won(value)
    return _num_text(value)


def _source(metric_id: str) -> str:
    head = metric_id.split(":", 1)[0]
    return head if head in ("M", "S", "P") else "M"


def _recommendation(assessment: Mapping[str, Any], policy: Mapping[str, Any], requested: Any) -> dict[str, Any]:
    rec = assessment.get("recommendation") or ""
    decision = policy.get("decision")
    decision_label, decision_tone = POLICY_DECISIONS.get(decision, (decision or "판정 없음", "neutral"))
    aligned = rec in ALIGNED.get(decision, set())
    limit = policy.get("max_amount_krw")
    if decision != "ineligible" and charts.is_num(requested) and charts.is_num(limit) and requested > limit:
        aligned = rec == "decline"
        relation = _("신청액이 정책 한도를 초과하므로 전액 승인이 불가능합니다.")
    elif not decision:
        relation = _("정책 판정이 없어 권고만 참고합니다.")
    elif aligned:
        relation = _("정책 판정과 에이전트 권고가 같은 방향입니다.")
    elif decision == "eligible":
        relation = _("정책상 적격은 전액 승인을 뜻하지 않습니다. 권고의 근거와 확인 사항을 살펴보세요.")
    else:
        relation = _("정책 판정과 에이전트 권고가 엇갈립니다. 근거 표와 근거 거래를 먼저 확인하세요.")
    return {
        "label": i18n.label(RECOMMENDATION_LABELS, rec, "의견 없음"),
        "tone": RECOMMENDATION_TONES.get(rec, "neutral"),
        "meaning": _(RECOMMENDATION_MEANINGS[rec]) if rec in RECOMMENDATION_MEANINGS else "",
        "policy": _(decision_label), "policy_tone": decision_tone,
        "aligned": aligned, "relation": relation,
    }


def _claims(claims: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    counts = {key: 0 for key, _label, _color in SOURCES}
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
    segments = [(_(label), counts[key], color) for key, label, color in SOURCES]
    if not any(v for _label, v, _color in segments):
        return None
    return {"donut": charts.donut(segments),
            "legend": [(label, color, _("%(n)s건", n=v)) for label, v, color in segments if v]}


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
            "direction": _("올리기") if delta and delta > 0 else _("낮추기") if delta and delta < 0 else _("유지"),
            "explanation": cf.get("explanation") or "",
        })
    return rows


def _round(entry: Mapping[str, Any]) -> dict[str, Any]:
    analyst = entry.get("analyst") if isinstance(entry.get("analyst"), Mapping) else {}
    reviewer = entry.get("reviewer") if isinstance(entry.get("reviewer"), Mapping) else {}
    grounding = list(entry.get("grounding_issues") or [])
    review = list(entry.get("review_issues") or [])
    if entry.get("analyst_error"):
        outcome, tone = _("분석 실패"), "red"
    elif grounding:
        outcome, tone = _("근거 보완 요청"), "amber"
    elif entry.get("reviewer_error"):
        outcome, tone = _("검토 실패"), "red"
    elif review:
        outcome, tone = _("검토 지적"), "amber"
    elif "reviewer" in entry:
        outcome, tone = _("통과"), "green"
    else:
        outcome, tone = "-", "neutral"
    reviewer_text = (_("건너뜀(근거 검사 미통과)") if grounding else _("실패") if entry.get("reviewer_error")
                     else _("지적 %(n)s건", n=len(review)) if "reviewer" in entry else "-")
    return {
        "n": entry.get("round"), "tools": len(analyst.get("tool_calls") or ()),
        "tokens": int(analyst.get("total_tokens") or 0) + int(reviewer.get("total_tokens") or 0),
        "grounding": len(grounding), "review": len(review), "reviewer": reviewer_text,
        "outcome": outcome, "tone": tone,
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
    tool_bars = [(i18n.label(TOOL_LABELS, n), c, "blue") for n, c in sorted(tools.items(), key=lambda kv: -kv[1])]
    rows = [_round(r) for r in rounds]
    groups = [(_("%(n)s라운드", n=r["n"]),
               [[("grounding", r["grounding"], "amber",
                  _("%(n)s라운드 근거 검사 %(count)s건", n=r["n"], count=r["grounding"])),
                 ("review", r["review"], "red", _("%(n)s라운드 검토 지적 %(count)s건", n=r["n"], count=r["review"]))]])
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
    roles = {"analyst": _("분석"), "reviewer": _("검토")}
    rows = [{"model": m, "role": roles.get(role_of.get(m, ""), "-"), **v} for m, v in by_model.items()]
    colors = ("blue", "purple", "teal", "orange")
    bars = [(r["model"], r["input"] + r["output"], colors[i % len(colors)]) for i, r in enumerate(rows)]
    ms = step.get("duration_ms")
    return {
        "rows": rows, "bars": charts.hbars(bars, width=420, label_w=110) if any(b[1] for b in bars) else None,
        "seconds": round(ms / 1000, 1) if charts.is_num(ms) else None,
        "tokens": sum(r["input"] + r["output"] for r in rows),
    }


OFFICER_CHECK_STATUS = {True: ("ok", "검토 통과"), False: ("failed", "검토 미통과")}  # 문구는 화면에 낼 때 번역


def _officer_checks(view: Mapping[str, Any]) -> dict[str, Any]:
    """검토된 현행 의견의 확인 사항(생략과 빈 배열은 모두 확인 사항 없음)."""
    checks = view.get("officer_checks")
    if not checks:
        return {"state": "empty", "items": [], "verified": 0, "total": 0}
    items = []
    for i, c in enumerate(checks, start=1):
        status, status_text = OFFICER_CHECK_STATUS.get(c.get("supported"), ("unknown", "검토 결과 없음"))
        status_text = _(status_text)
        evidence = [{"label": evidence_label(m), "value": evidence_value(m, val), "missing": val is None}
                    for m, val in c.get("evidence") or ()]
        items.append({"n": i, "text": c.get("text", ""), "evidence": evidence, "status": status,
                      "status_text": status_text})
    verified = sum(1 for i in items if i["status"] == "ok")
    return {"state": "listed", "items": items, "verified": verified, "total": len(items)}


def opinion_panel(view: Mapping[str, Any], run: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    assessment = view.get("assessment")
    if not assessment:
        return None
    doc = view["doc"]
    policy = view.get("policy") or {}
    claims, counts = _claims(view.get("claims") or ())
    review = assessment.get("review") or {}
    usage = _usage(doc, assessment.get("models") or {})
    ok = sum(1 for c in claims if c["ok"])
    return {
        "recommendation": _recommendation(assessment, policy, doc.get("requested_amount_krw")),
        "kpi": {
            "grounded": f"{ok}/{len(claims)}", "grounded_tone": "green" if claims and ok == len(claims) else "amber",
            "review": (_("통과") if review.get("passed") else _("미통과")) if review else "-",
            "review_sub": _("%(n)s라운드", n=review["rounds"]) if charts.is_num(review.get("rounds")) else "",
            "review_tone": "green" if review.get("passed") else ("red" if review else "neutral"),
            "tokens": f"{usage['tokens']:,}" if usage else "-",
            "tokens_sub": _("에이전트 단계 %(s)s초", s=usage["seconds"]) if usage and usage["seconds"] is not None
            else "",
        },
        "claims": claims,
        "mix": _evidence_mix(counts),
        "counterfactuals": _counterfactuals(assessment.get("counterfactuals") or ()),
        "loop": _loop(run),
        "usage": usage,
        "models": assessment.get("models") or {},
        "officer_checks": _officer_checks(view),
    }
