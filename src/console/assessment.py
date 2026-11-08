"""신청 상세 '에이전트 평가' 패널 계산(P9 §5). 순수 함수이고 그리기는 `_charts.html` 매크로가 한다.

콘솔은 정책 패키지를 가져오지 않으므로 신호등 기준값을 상수로 둔다. 테스트가 `load_rules()`와 같은지 고정한다.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from console import charts
from console.dashboard import EXPENSE_COLORS, EXPENSES, POLICY_DECISIONS, RECOMMENDATION_LABELS, krw_text, man
from console.statuses import FLAG_LABELS

PRODUCT_MAX_KRW = 10_000_000
INFLOW_MULTIPLIER = 1.5
LIMIT_UNIT_KRW = 100_000
CAPACITY_CHART = {"width": 480, "height": 240}  # 반 폭 카드에서 글자가 너무 작아지지 않게 한다.

# 지표 → (기준 종류, 정책 임계값). "min"은 임계값 미만이 위반, "max"는 초과가 위반이다.
SIGNAL_RULES: dict[str, tuple[str, float]] = {
    "months_of_history": ("knockout", 6),
    "dscr": ("knockout", 1.0),
    "tax_bank_match_ratio": ("refer", 0.7),
    "revenue_volatility": ("refer", 0.5),
    "cash_buffer_days": ("refer", 10),
    "business_age_months": ("refer", 12),
}
# (지표, 이름, 방향, 강점 기준, 쉬운 설명). 설명의 {v}·{pct}·{t}는 관측값·백분율·정책 기준으로 채운다.
SIGNALS: tuple[tuple[str, str, str, float, str], ...] = (
    ("dscr", "DSCR", "min", 1.5, "DSCR {v} — 벌어들이는 돈이 매달 갚을 돈의 {v}배입니다(기준 {t} 이상)."),
    ("cash_buffer_days", "현금 완충일수", "min", 30,
     "현금 완충일수 {v}일 — 매출이 끊겨도 통장 잔고로 운영비를 {v}일 버팁니다(기준 {t}일 이상)."),
    ("revenue_volatility", "매출 변동성", "max", 0.3,
     "매출 변동성 {v} — 월 매출이 평균에서 약 {pct}% 흔들립니다(기준 {t} 이하)."),
    ("tax_bank_match_ratio", "세금·계좌 일치율", "min", 0.9,
     "세금·계좌 일치율 {v} — 신고 매출의 {pct}%가 계좌 입금으로 확인됩니다(기준 {t} 이상)."),
    ("business_age_months", "업력", "min", 24, "업력 {v}개월 — 사업을 {v}개월째 하고 있습니다(기준 {t}개월 이상)."),
    ("months_of_history", "거래 이력", "min", 12,
     "거래 이력 {v}개월 — 심사에 쓴 계좌 거래가 {v}개월치입니다(기준 {t}개월 이상)."),
)
SIGNAL_TONES = {"pass": "green", "refer": "amber", "knockout": "red", "unknown": "grey"}
SIGNAL_STATE_LABELS = {"pass": "통과", "refer": "주의(회부 기준)", "knockout": "위반(거절 기준)", "unknown": "값 없음"}
RECOMMENDATION_TONES = {"approve": "green", "conditional": "blue", "refer": "purple", "decline": "red"}
LEFTOVER = ("leftover", "남는 돈", "green")


def _num_text(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


def _signal(field: str, name: str, direction: str, strong: float, text: str, features: Mapping[str, Any]) -> dict:
    kind, threshold = SIGNAL_RULES[field]
    v = features.get(field)
    if not charts.is_num(v):
        return {"field": field, "name": name, "value": None, "state": "unknown", "tone": "grey", "strong": False,
                "label": SIGNAL_STATE_LABELS["unknown"], "text": f"{name} — 값이 없습니다.", "bar": None,
                "threshold": threshold}
    violated = v < threshold if direction == "min" else v > threshold
    state = kind if violated else "pass"
    return {
        "field": field, "name": name, "value": v, "state": state, "tone": SIGNAL_TONES[state],
        "label": SIGNAL_STATE_LABELS[state], "threshold": threshold,
        "strong": not violated and (v >= strong if direction == "min" else v <= strong),
        "text": text.format(v=_num_text(v), pct=round(v * 100), t=threshold),
        "bar": charts.marker_bar(v, threshold),
    }


def _summary(view: Mapping[str, Any], doc: Mapping[str, Any]) -> dict[str, Any]:
    policy, assessment = view.get("policy") or {}, view.get("assessment") or {}
    decision = policy.get("decision")
    decision_label, decision_tone = POLICY_DECISIONS.get(decision, (decision or "판정 없음", "neutral"))
    rec = assessment.get("recommendation")
    review = assessment.get("review") or {}
    review_text = "-"
    if review:
        review_text = ("통과" if review.get("passed") else "미통과") + (
            f" · {review['rounds']}회" if charts.is_num(review.get("rounds")) else "")
    requested, limit = doc.get("requested_amount_krw"), policy.get("max_amount_krw")
    gauge = charts.gauge(limit, requested)
    return {
        "decision": decision_label, "decision_tone": decision_tone,
        "recommendation": RECOMMENDATION_LABELS.get(rec, rec) if rec else "권고 없음",
        "recommendation_tone": RECOMMENDATION_TONES.get(rec or "", "neutral"),
        "review": review_text, "review_tone": "green" if review.get("passed") else ("red" if review else "neutral"),
        "gauge": gauge,
        "gauge_text": f"{gauge['pct']:.0f}%" if gauge["frac"] is not None else "-",
        "gauge_sub": f"한도 {krw_text(limit)} / 신청 {krw_text(requested)}",
    }


def _capacity(monthly: Sequence[Mapping[str, Any]], installment: Any) -> dict[str, Any] | None:
    if not monthly:
        return None
    new = installment if charts.is_num(installment) else 0
    groups, obligations = [], []
    for m in monthly:
        month = str(m.get("month") or "")
        rev, opex, debt = m.get("revenue"), m.get("operating_out"), m.get("debt_service")
        cap = (rev - opex) if charts.is_num(rev) and charts.is_num(opex) else None
        owed = (debt if charts.is_num(debt) else 0) + new
        short = cap is None or cap < owed
        tip = f"{month} 매출−운영비 {krw_text(cap)} · 갚을 돈 {krw_text(owed)}"
        groups.append((month[2:] if len(month) == 7 else month, [[("capacity", cap, "red" if short else "green", tip)]]))
        obligations.append(owed)
    chart = charts.columns(groups, tick_fmt=man, **CAPACITY_CHART)
    return {
        "chart": chart, "line": charts.line(obligations, vmax=chart["vmax"], tick_fmt=man, **CAPACITY_CHART),
        "short_months": sum(1 for _, bars in groups if bars[0][0][2] == "red"), "months": len(groups),
        "installment": new,
        "legend": [("매출 − 운영비(여유)", "green"), ("갚을 돈보다 적은 달", "red"),
                   ("기존 상환 + 이번 대출 월 상환액(선)", "purple")],
    }


def _limit(doc: Mapping[str, Any], features: Mapping[str, Any], policy: Mapping[str, Any]) -> dict[str, Any] | None:
    requested, inflow = doc.get("requested_amount_krw"), features.get("avg_monthly_inflow_krw")
    if not (charts.is_num(requested) and charts.is_num(inflow)):
        return None
    candidates = [("신청액", requested), (f"월평균 입금×{INFLOW_MULTIPLIER:g}", int(inflow * INFLOW_MULTIPLIER)),
                  ("상품 최대", PRODUCT_MAX_KRW)]
    low = min(v for _, v in candidates)
    computed = low - low % LIMIT_UNIT_KRW
    items = [(label, round(v / 10_000), "blue" if v == low else "grey") for label, v in candidates]
    return {
        "bars": charts.hbars(items, width=420, label_w=120),
        "binding": next(label for label, v in candidates if v == low),
        "computed": computed,
        "policy_limit": policy.get("max_amount_krw"),
    }


def _expenses(uw: Mapping[str, Any], features: Mapping[str, Any]) -> dict[str, Any] | None:
    breakdown = uw.get("expense_breakdown")
    if breakdown:
        parts = [(g.get("label") or g.get("group"), g.get("monthly_avg_krw"), EXPENSE_COLORS.get(g.get("group"), "grey"))
                 for g in breakdown if isinstance(g, Mapping)]
        revenue, source = features.get("avg_monthly_inflow_krw"), "breakdown"
    else:
        monthly = [m for m in uw.get("monthly") or [] if isinstance(m, Mapping)]
        if not monthly:
            return None

        def avg(key: str) -> float:
            vals = [m[key] for m in monthly if charts.is_num(m.get(key))]
            return sum(vals) / len(vals) if vals else 0.0

        parts = [(label, avg(key), color) for key, label, color in EXPENSES]
        revenue, source = avg("revenue"), "monthly"
    spent = sum(v for _, v, _ in parts if charts.is_num(v) and v > 0)
    leftover = revenue - spent if charts.is_num(revenue) else 0
    segments = [(label, round(v / 10_000), color) for label, v, color in parts if charts.is_num(v)]
    segments.append((LEFTOVER[1], round(max(leftover, 0) / 10_000), LEFTOVER[2]))
    donut = charts.donut(segments)
    return {
        "donut": donut, "source": source, "leftover": leftover,
        "legend": [(label, color, f"{v:,}만") for label, v, color in segments if v > 0],
    }


def _claims(claims: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for c in claims:
        missing = [m for m, val in c.get("evidence") or () if val is None]
        out.append({"ok": not missing, "missing": missing})
    return out


def _strengths_risks(signals: Sequence[Mapping[str, Any]], policy: Mapping[str, Any],
                     flags: Sequence[str]) -> tuple[list[str], list[str]]:
    strengths = [s["text"] for s in signals if s["strong"]]
    risks = [f"{s['label']}: {s['text']}" for s in signals if s["state"] in ("knockout", "refer")]
    covered = {s["field"] for s in signals if s["state"] in ("knockout", "refer")}
    for r in policy.get("reason_codes") or []:
        if r.get("field") not in covered:
            risks.append(f"{r.get('code')} {r.get('message')}")
    risks.extend(FLAG_LABELS.get(f, f) for f in dict.fromkeys(flags))
    return strengths, risks


def assessment_panels(view: Mapping[str, Any]) -> dict[str, Any]:
    doc = view["doc"]
    uw = doc.get("underwriting") or {}
    features = view.get("features") or uw.get("features") or {}
    policy = view.get("policy") or {}
    monthly = [m for m in uw.get("monthly") or [] if isinstance(m, Mapping)]
    signals = [_signal(*s, features) for s in SIGNALS] if features else []
    flags = [*(doc.get("flags") or ()), *(uw.get("flags") or ())]
    strengths, risks = _strengths_risks(signals, policy, flags)
    return {
        "summary": _summary(view, doc),
        "signals": signals,
        "capacity": _capacity(monthly, (uw.get("signals") or {}).get("new_installment_krw")),
        "limit": _limit(doc, features, policy),
        "expenses": _expenses(uw, features),
        "claims": _claims(view.get("claims") or ()),
        "strengths": strengths,
        "risks": risks,
    }
