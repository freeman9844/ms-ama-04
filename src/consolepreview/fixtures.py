"""미리보기용 합성 데이터와 메모리 저장소. 실제 차주·실제 측정값이 아니다(화면 확인 전용).

신청 11건(상태 골고루), 대출 3건(재생 완료·재생 중·재생 전), 운영 측정 실행 1개를 만든다. 신청의 심사 지표·월별
현금흐름·지출 구성·정책 판정은 데모·부하 셀러 합성 거래에 실제 엔진(agents.features, 정책 규칙 v2)을 돌려 만든다.
원천 데이터(RawStore)는 비워 두어 상세 화면은 가명 토큰 복원 불가 상태로 보인다.
"""

from datetime import date, datetime, timedelta, timezone
from functools import cache
from typing import Any

from agents.features import compute_underwriting
from agents.forecast import summarize
from agents.monitoring import settlement_for
from console.app import ConsoleDeps
from console.auth import CREDIT_OFFICER, Principal
from mockapis.demo import build_demo_cases, build_load_cases
from mockapis.synth import SyntheticCase
from policy.engine import evaluate
from policy.rules import load_rules

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 11, 3, 10, 0, tzinfo=KST)
DAY = NOW.replace(hour=9, minute=0)
RUN_ID = "load-20261103090000"
PRINCIPAL = Principal(user_id="preview-officer", name="미리보기 심사역", roles=frozenset({CREDIT_OFFICER}))
FLOW = ("received", "consent_granted", "data_assembled", "underwritten", "assessed", "awaiting_officer")
OFFSETS = (0, 20, 65, 100, 190, 195)
AS_OF = date(2026, 11, 2)
TENOR = 6
# 신청 번호 → 셀러. 데모 셀러 5곳과 부하 셀러 6곳(상호가 겹치지 않는다).
SELLERS = {1: "load-healthy-01", 2: "seller-mangwon-02", 3: "seller-dailylook-01", 4: "seller-candle-05",
           5: "seller-knitroom-04", 6: "load-mismatch-01", 7: "load-thin-01", 8: "load-healthy-02",
           9: "load-stress-01", 10: "seller-glowlab-03", 11: "load-healthy-03"}


@cache
def _cases() -> dict[str, SyntheticCase]:
    return {**build_demo_cases(AS_OF), **build_load_cases(AS_OF)}


def seller(n: int) -> SyntheticCase:
    return _cases()[SELLERS[n]]


# 합성 정답 라벨 → 의미 분류 에이전트 라벨(의미 분류가 정답을 맞혔다고 가정한 미리보기)
SEMANTIC_LABEL = {"supplier": "supplier", "rent": "rent", "payroll": "payroll", "social_insurance": "tax_or_insurance",
                  "tax": "tax_or_insurance", "advertising": "other_operating", "solution_fee": "other_operating",
                  "shipping": "other_operating", "card_payment": "other_operating",
                  "loan_repayment": "loan_repayment", "high_cost_lender": "high_cost_lender",
                  "owner_transfer": "owner_transfer"}


def _underwriting(app_id: str, case: SyntheticCase, amount: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """오케스트레이터 underwrite·evaluate_policy와 같은 모양. 지출 구성 라벨만 합성 정답에서 가져온다."""
    labels = {k: SEMANTIC_LABEL[v.value] for k, v in case.labels.items() if v.value in SEMANTIC_LABEL}
    uw = compute_underwriting(app_id, case.profile, case.transactions, case.tax_filings, as_of=AS_OF,
                              requested_amount_krw=amount, requested_tenor_months=TENOR, semantic_labels=labels)
    underwriting = {
        "features": uw.features.model_dump(mode="json"), "signals": uw.signals, "flags": uw.flags,
        "monthly": uw.monthly, "expense_breakdown": uw.expense_breakdown, "expense_monthly": uw.expense_monthly,
        "settlement_profile": settlement_for(case.transactions, {}, AS_OF, case.profile.industry),
        "classification": None, "category_overrides": {},
    }
    return underwriting, evaluate(uw.features, load_rules()).model_dump(mode="json")


def _at(start: datetime, seconds: int) -> str:
    return (start + timedelta(seconds=seconds)).isoformat()


def _timeline(start: datetime, statuses: tuple[str, ...], extra: tuple[tuple[str, int], ...] = ()) -> list[dict]:
    out = [{"status": s, "at": _at(start, OFFSETS[i])} for i, s in enumerate(statuses)]
    out += [{"status": s, "at": _at(start, sec)} for s, sec in extra]
    return out


def _man(krw: float) -> str:
    return f"{round(krw / 10_000):,}"


REC_OF = {"eligible": "approve", "refer": "refer", "ineligible": "decline"}
DECISION_KO = {"eligible": "적격", "refer": "검토 필요", "ineligible": "부적격"}


def _assessment(app_id: str, rec: str, uw: dict[str, Any], policy: dict[str, Any], amount: int) -> dict[str, Any]:
    f, s = uw["features"], uw["signals"]
    limit = policy["max_amount_krw"] or 0
    structure = None
    if rec != "decline" and limit > 0:
        structure = {"amount_krw": min(amount, limit), "tenor_months": TENOR, "schedule": "level",
                     "reduced_payment_months": [],
                     "rationale": f"월 상환액이 월평균 정산 입금 {_man(f['avg_monthly_inflow_krw'])}만 원 대비 작다."}
    return {
        "application_id": app_id,
        "recommendation": rec,
        "summary": (f"최근 {s['months_used']}개월 월평균 정산 입금 {_man(f['avg_monthly_inflow_krw'])}만 원, "
                    f"DSCR {f['dscr']:.2f}, 현금 버퍼 {f['cash_buffer_days']:.0f}일이다. "
                    f"정책 판정은 {DECISION_KO[policy['decision']]}이다."),
        "claims": [
            {"text": f"DSCR {f['dscr']:.2f}", "metric_ids": ["M:dscr"], "txn_ids": []},
            {"text": f"현금 버퍼 {f['cash_buffer_days']:.0f}일", "metric_ids": ["M:cash_buffer_days"], "txn_ids": []},
            {"text": f"대표자 월평균 인출 {_man(s['avg_monthly_owner_draw_krw'])}만 원",
             "metric_ids": ["S:avg_monthly_owner_draw_krw"], "txn_ids": []},
        ],
        "proposed_structure": structure,
        "counterfactuals": [
            {"metric_id": "M:cash_buffer_days", "current": f["cash_buffer_days"], "target": 30.0,
             "explanation": "현금 버퍼가 30일 이상이면 한도 여유가 커진다"},
        ],
        "review": {"passed": True, "rounds": 2, "issues": []},
        "models": {"analyst": "gpt-5.6-sol", "reviewer": "gpt-5.6-luna"},
    }


def _doc(n: int, status: str, timeline: list[dict], *, rec: str | None = "policy", amount: int = 8_000_000,
         **overrides: Any) -> dict[str, Any]:
    """rec="policy"면 정책 판정을 따른 추천(적격→승인, 검토 필요→검토, 부적격→거절)을 쓴다."""
    app_id = f"app-20261103-p{n:02d}"
    case = seller(n)
    doc: dict[str, Any] = {
        "id": app_id, "application_id": app_id, "applicant_id": case.profile.applicant_id,
        "requested_amount_krw": amount, "requested_tenor_months": TENOR, "status": status,
        "received_at": timeline[0]["at"], "updated_at": timeline[-1]["at"], "timeline": timeline,
        "underwriting": None, "policy": None, "assessment": None, "flags": [],
        "sla": {"hours": 24, "deadline": (datetime.fromisoformat(timeline[0]["at"]) + timedelta(hours=24)).isoformat(),
                "escalated": False},
        "decision_attempts": [], "officer_decision": None, "error": None,
    }
    if any(t["status"] == "underwritten" for t in timeline):
        doc["underwriting"], doc["policy"] = _underwriting(app_id, case, amount)
        doc["flags"] = list(doc["underwriting"]["flags"])
    if rec is not None and doc["policy"] is not None:
        rec = REC_OF[doc["policy"]["decision"]] if rec == "policy" else rec
        doc["assessment"] = _assessment(app_id, rec, doc["underwriting"], doc["policy"], amount)
        doc["assessment_run"] = _assessment_run()
        doc["steps"] = [{"step": "assess", "outcome": "ok", "agent": True, "duration_ms": 31_400, "usage": [
            {"model": "gpt-5.6-sol", "input_tokens": 18_200, "output_tokens": 2_900, "calls": 5},
            {"model": "gpt-5.6-luna", "input_tokens": 4_100, "output_tokens": 380, "calls": 1}]}]
    doc.update(overrides)
    return doc


def _assessment_run() -> dict[str, Any]:
    """반성 루프 기록 예시: 1라운드는 근거 검사에서 돌려보내고 2라운드에 검토를 통과한다(도구 인자는 비움)."""
    def calls(*names: str) -> list[dict[str, Any]]:
        return [{"name": n, "arguments": {}, "error": None} for n in names]

    return {"models": {"analyst": "gpt-5.6-sol", "reviewer": "gpt-5.6-luna"}, "rounds": [
        {"round": 1, "analyst": {"total_tokens": 11_800, "tool_calls": calls(
            "get_metrics", "get_monthly_series", "query_transactions", "code_interpreter")},
         "grounding_issues": ["claim 3: 근거 목록에 없는 숫자 4.7"]},
        {"round": 2, "analyst": {"total_tokens": 9_300, "tool_calls": calls("get_metrics")}, "grounding_issues": [],
         "reviewer": {"total_tokens": 4_480, "tool_calls": []}, "review_issues": []},
    ]}


def _start(minutes_ago: int) -> datetime:
    return NOW - timedelta(minutes=minutes_ago)


def applications() -> list[dict[str, Any]]:
    s = [_start(m) for m in (6, 1500, 300, 280, 260, 240, 30, 2, 90, 12, 1800)]
    return [
        _doc(1, "awaiting_officer", _timeline(s[0], FLOW)),
        _doc(2, "escalated", _timeline(s[1], FLOW, (("escalated", 86_400),)),
             sla={"hours": 24, "deadline": _at(s[1], 86_400), "escalated": True}),
        _doc(3, "approved", _timeline(s[2], FLOW, (("approved", 3_600),)), amount=5_000_000,
             officer_decision={"decision": "approve", "amount_krw": 5_000_000, "officer_id": "preview-officer",
                               "conditions": [], "reason": "정책 한도 안, 상환 여력 충분"}),
        _doc(4, "conditionally_approved", _timeline(s[3], FLOW, (("conditionally_approved", 4_000),)),
             amount=4_000_000, rec="conditional",  # P9 이전 조건부 승인 문서도 그대로 보여야 한다
             officer_decision={"decision": "conditional", "amount_krw": 4_000_000, "officer_id": "preview-officer",
                               "conditions": ["월별 매출 보고"], "reason": "비수기 대비 조건 부여"}),
        _doc(5, "declined", _timeline(s[4], FLOW, (("declined", 3_000),)),
             officer_decision={"decision": "decline", "amount_krw": None, "officer_id": "preview-officer",
                               "conditions": [], "reason": "정책 부적격(R001 거래 이력 부족, R104 업력 부족)"}),
        _doc(6, "withdrawn", _timeline(s[5], FLOW[:3], (("withdrawn", 120),)), rec=None),
        _doc(7, "data_incomplete", _timeline(s[6], FLOW[:2], (("data_incomplete", 70),)), rec=None),
        _doc(8, "received", _timeline(s[7], FLOW[:1]), rec=None),
        _doc(9, "failed", _timeline(s[8], FLOW[:4], (("failed", 150),)), rec=None,
             error={"stage": "assess", "message": "모델 응답 시간 초과(합성 예시)"}),
        _doc(10, "assessed", _timeline(s[9], FLOW[:5])),
        _doc(11, "approved", _timeline(s[10], FLOW, (("approved", 2_400),)), amount=3_000_000,
             officer_decision={"decision": "approve", "amount_krw": 3_000_000, "officer_id": "preview-officer",
                               "conditions": [], "reason": "정책 한도 안"}),
    ]


BASELINE = {
    "avg_weekly_revenue": 1_580_000, "revenue_volatility": 0.12, "cash_buffer_days": 30.0,
    "operating_daily_krw": 150_000, "tax_bank_match_ratio": 0.97,
    "seasonal_index": {f"{m:02d}": 1.0 for m in range(1, 13)},
}
WEEK_REVENUE = (1_630_000, 1_570_000, 1_680_000, 1_500_000, 1_450_000, 1_020_000, 1_230_000,
                1_370_000, 1_170_000, 800_000, 930_000, 1_150_000, 1_300_000)
WEEK_BUFFER = (31.0, 30.2, 31.5, 29.0, 27.4, 21.0, 22.3, 23.9, 19.5, 12.8, 11.2, 13.6, 15.9)
WEEK_EXPECTED = (1_600_000, 1_610_000, 1_640_000, 1_580_000, 1_560_000, 1_590_000, 1_620_000,
                 1_650_000, 1_700_000, 1_720_000, 1_690_000, 1_640_000, 1_380_000)  # 13주차는 연말 휴일
TRIGGERS = {6: ["inflow_drop"], 7: ["inflow_drop"], 8: ["inflow_drop"], 9: ["inflow_drop"],
            **{w: ["inflow_drop", "new_debt", "projected_shortfall"] for w in range(10, 14)}}
SCHEDULE = (("rent", 5, 560_000), ("payroll", 10, 900_000), ("social_insurance", 10, 160_000),
            ("installment", 15, 440_000), ("loan_repayment", 25, 167_000), ("owner_transfer", 28, 380_000))
VARIABLE_RATIO = 0.55


def _projection(week: int, end: date, end_balance: int) -> dict[str, Any]:
    """미리보기용 4주 잔고 전망(합성). 실제 계산은 agents.forecast.project가 한다."""
    expected = WEEK_EXPECTED[week - 1]
    attainment = round(min(sum(WEEK_REVENUE[max(week - 4, 0):week]) / sum(WEEK_EXPECTED[max(week - 4, 0):week]), 1.0), 3)
    daily_in = round(expected / 7 * attainment)
    totals = dict.fromkeys(["inflow", *(k for k, _, _ in SCHEDULE), "high_cost_lender", "variable"], 0)
    path, bal = [], end_balance
    for i in range(1, 29):
        day = end + timedelta(days=i)
        moves = {"inflow": daily_in if day.weekday() < 5 else 0, "variable": -round(daily_in * VARIABLE_RATIO)}
        moves |= {k: -amt for k, d, amt in SCHEDULE if day.day == d}
        if week >= 10 and day.weekday() == 4:
            moves["high_cost_lender"] = -250_000
        for k, v in moves.items():
            totals[k] += v
        bal += sum(moves.values())
        path.append({"date": day.isoformat(), "balance": bal})
    return {"start_balance": end_balance, "attainment": attainment, "path": path,
            "totals": {k: v for k, v in totals.items() if v}}


def _row(week: int) -> dict[str, Any]:
    start = datetime(2026, 11, 2) + timedelta(weeks=week - 1)
    revenue = WEEK_REVENUE[week - 1]
    end = (start + timedelta(days=6)).date()
    end_balance = 3_300_000 - week * 100_000 - (700_000 if week >= 10 else 0)
    two = slice(max(week - 2, 0), week)
    return {
        "week": week, "start": start.date().isoformat(), "end": end.isoformat(),
        "revenue": revenue, "revenue_ratio": round(revenue / BASELINE["avg_weekly_revenue"], 3),
        "expected_revenue": WEEK_EXPECTED[week - 1],
        "revenue_vs_expected": round(sum(WEEK_REVENUE[two]) / sum(WEEK_EXPECTED[two]), 3),
        "operating_out": round(revenue * 0.6), "debt_service": 167_000,
        "high_cost_lender": 250_000 if week >= 10 else 0, "high_cost_count": 1 if week >= 10 else 0,
        "loan_proceeds": 0, "unclassified_in": 0, "end_balance": end_balance,
        "buffer_days": WEEK_BUFFER[week - 1],
        **summarize(_projection(week, end, end_balance)),
    }


def _weeks(replay_id: str, count: int) -> list[dict[str, Any]]:
    out, seen = [], set()
    for week in range(1, count + 1):
        triggers = TRIGGERS.get(week, [])
        new = [t for t in triggers if t not in seen]
        seen.update(triggers)
        alert_id = f"ew-{replay_id}-w{week:02d}" if new else None
        row = _row(week)
        out.append({"week": week, "row": row, "triggers": triggers, "new_triggers": new,
                    "classification": None,
                    "projection": _projection(week, date.fromisoformat(row["end"]), row["end_balance"]),
                    "investigation": {"decision": "alert", "reason": "합성 예시", "record": {}} if new else None,
                    "alert_id": alert_id})
    return out


def _loan(app_id: str, applicant: str, industry: str, amount: int, monitoring: dict | None) -> dict[str, Any]:
    return {
        "id": app_id, "loan_id": app_id, "application_id": app_id, "applicant_id": applicant, "industry": industry,
        "underwriting_consent_id": None, "decision": "approve", "amount_krw": amount, "tenor_months": TENOR,
        "approved_at": (NOW - timedelta(days=1)).isoformat(), "baseline": BASELINE, "monitoring": monitoring,
        "updated_at": NOW.isoformat(),
    }


def _monitoring(replay_id: str, status: str, weeks: int, started: datetime) -> dict[str, Any]:
    rows = _weeks(replay_id, weeks)
    return {
        "replay_id": replay_id, "consent_id": None, "flow_start": "2026-11-02", "started_at": started.isoformat(),
        "mode": "inline", "status": status, "processed_week": weeks,
        "seen_triggers": sorted({t for w in rows for t in w["triggers"]}), "overrides": {}, "weeks": rows,
        "alert_ids": [w["alert_id"] for w in rows if w["alert_id"]], "error": None,
    }


SIGNAL_METRIC = {"inflow_drop": "W:{w}:revenue", "new_debt": "W:{w}:high_cost_lender",
                 "buffer_drop": "W:{w}:buffer_days", "projected_shortfall": "F:{w}:projected_min_balance"}


def _alert(loan_id: str, replay_id: str, week: int, severity: str) -> dict[str, Any]:
    alert_id = f"ew-{replay_id}-w{week:02d}"
    return {
        "id": alert_id, "schema_version": "1", "alert_id": alert_id, "loan_id": loan_id, "week": week,
        "as_of": _row(week)["end"], "severity": severity,
        "summary": f"{week}주차 매출이 기준 대비 크게 줄고 현금 버퍼가 감소했다(합성 예시).",
        "signals": [{"kind": t, "text": f"{week}주차 {t} 신호", "metric_ids": [SIGNAL_METRIC[t].format(w=week)],
                     "txn_ids": []} for t in TRIGGERS[week]],
        "recommended_action": "hardship_review" if severity == "high" else "monitor", "triggers": TRIGGERS[week],
        "models": {"investigator": "gpt-5.4"}, "replay_id": replay_id, "status": "open",
        "created_at": NOW.isoformat(), "event_error": None, "collections": None, "restructure": None,
    }


INSTALLMENT = 450_000
DUES = ("2026-12-02", "2027-01-01", "2027-01-31")


def _repayment(replay_id: str) -> dict[str, Any]:
    """납부·납부·미납(3회차) 원장. 3회차는 13주차 추심 건으로 이어진다."""
    statuses = ("paid", "paid", "missed")
    return {"installment_krw": INSTALLMENT, "schedule": [
        {"no": i, "due_on": due, "status": st, "reminded_on": (date.fromisoformat(due) - timedelta(days=3)).isoformat(),
         "reminder_status": "sent", "txn_id": f"t-pay-{i}" if st == "paid" else None,
         "case_id": f"rc-{replay_id}-i{i}" if st == "missed" else None}
        for i, (due, st) in enumerate(zip(DUES, statuses, strict=True), start=1)]}


def _case(loan_id: str, replay_id: str, recent: dict[str, Any]) -> dict[str, Any]:
    case_id = f"rc-{replay_id}-i3"
    return {
        "id": case_id, "kind": "repayment_case", "case_id": case_id, "loan_id": loan_id, "replay_id": replay_id,
        "week": 13, "as_of": DUES[2], "installment": {"no": 3, "due_on": DUES[2], "amount_krw": INSTALLMENT},
        "missed_count": 1,
        "recent_alert": {k: recent[k] for k in ("alert_id", "week", "severity", "summary")},
        "status": "restructure_pending", "created_at": NOW.isoformat(), "event_error": None,
        "collections": {"decision": "routed", "reason": "합성 예시", "at": NOW.isoformat(), "record": {},
                        "contacts": [{"ref": case_id, "on": DUES[2], "at": NOW.isoformat(),
                                      "script_id": "hardship_offer", "status": "sent", "reason": None,
                                      "by": "agent"}]},
        "restructure": {"request_id": f"rs-{case_id}", "category": "debt_overload",
                        "note": "고금리 차입 중첩(합성 예시)", "status": "pending_officer",
                        "created_at": NOW.isoformat()},
    }


def loans() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    def who(n: int) -> tuple[str, str]:
        return seller(n).profile.applicant_id, seller(n).profile.industry

    done = _loan("app-20261103-p03", *who(3), 5_000_000,
                 _monitoring("rp-preview-1", "completed", 13, NOW - timedelta(hours=2)))
    running = _loan("app-20261103-p04", *who(4), 4_000_000,
                    _monitoring("rp-preview-2", "replaying", 4, NOW - timedelta(minutes=2)))
    idle = _loan("app-20261103-p11", *who(11), 3_000_000, None)
    done["repayment"] = _repayment("rp-preview-1")
    done["monitoring"]["case_ids"] = ["rc-rp-preview-1-i3"]
    done["monitoring"]["weeks"][12]["case_id"] = "rc-rp-preview-1-i3"
    alerts = [_alert(done["id"], "rp-preview-1", 6, "medium"), _alert(done["id"], "rp-preview-1", 10, "high")]
    alerts.append(_case(done["id"], "rp-preview-1", alerts[1]))
    return [done, running, idle], alerts


def _projected(i: int, industry: str, rec: str, region: str, age: str, outcomes: tuple[str, str]) -> dict:
    sub = DAY + timedelta(minutes=i)
    usage = [{"model": "gpt-5.6-luna", "input_tokens": 1200, "output_tokens": 150, "calls": 1}]
    return {
        "application_id": f"r-{i:02d}", "applicant_id": f"load-healthy-{i:02d}", "status": "awaiting_officer",
        "submitted_at": sub.isoformat(), "received_at": sub.isoformat(), "source": "load", "run_id": RUN_ID,
        "segment": {"industry": industry, "region": region, "business_age_band": age},
        "steps": [{"step": "classify", "outcome": outcomes[0], "agent": True, "usage": usage},
                  {"step": "assess", "outcome": outcomes[1], "agent": True, "usage": usage}],
        "timeline": [{"status": "awaiting_officer", "at": (sub + timedelta(minutes=3 + i % 5)).isoformat()}],
        "flags": [], "policy": {"decision": "eligible", "reason_codes": []},
        "assessment": {"recommendation": rec}, "officer_decision": None,
    }


def ops_docs() -> list[dict[str, Any]]:
    food = ("approve", "decline", "refer", "decline", "approve", "decline", "refer", "decline", "approve",
            "decline", "refer")
    plan = ([("패션의류", "approve")] * 10 + [("식품", r) for r in food]
            + [("반려동물", "approve"), ("반려동물", "decline"), ("반려동물", "approve")])
    docs = []
    for i, (industry, rec) in enumerate(plan):
        outcomes = ("ok", "degraded" if i % 7 == 3 else ("failed" if i == 20 else "ok"))
        docs.append(_projected(i, industry, rec, "서울" if i % 2 else "부산", "3년+" if i % 3 else "1~3년", outcomes))
    return docs


class MemoryApplications:
    def __init__(self, docs: list[dict[str, Any]]):
        self.docs = {d["id"]: d for d in docs}

    def list_recent(self, limit: int) -> list[dict[str, Any]]:
        rows = [{"id": d["id"], "applicant_id": d["applicant_id"], "status": d["status"],
                 "requested_amount_krw": d["requested_amount_krw"], "updated_at": d["updated_at"],
                 "recommendation": (d.get("assessment") or {}).get("recommendation")} for d in self.docs.values()]
        return sorted(rows, key=lambda r: r["updated_at"], reverse=True)[:limit]

    def get(self, application_id: str) -> dict[str, Any] | None:
        return self.docs.get(application_id)


class NoRaw:
    def get_json(self, container: str, name: str) -> None:
        return None


class RecordingQueue:
    """결정·재생 메시지를 기록만 한다(오케스트레이터 없음)."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    def send(self, queue: str, message: dict[str, Any]) -> None:
        self.sent.append((queue, message))


class MemoryMonitoring:
    def __init__(self, loans: list[dict[str, Any]], alerts: list[dict[str, Any]]):
        self.loans = {d["id"]: d for d in loans}
        self.alerts = alerts

    def list_loans(self, limit: int) -> list[dict[str, Any]]:
        return [{"id": d["id"], "applicant_id": d["applicant_id"], "industry": d["industry"],
                 "amount_krw": d["amount_krw"], "tenor_months": d["tenor_months"],
                 "monitoring_status": (d["monitoring"] or {}).get("status"),
                 "alerts": len(d["monitoring"]["alert_ids"]) if d["monitoring"] else None,
                 "updated_at": d["updated_at"]} for d in self.loans.values()][:limit]

    def get_loan(self, loan_id: str) -> dict[str, Any] | None:
        return self.loans.get(loan_id)

    def list_alerts(self, loan_id: str, replay_id: str) -> list[dict[str, Any]]:
        return [a for a in self.alerts if a["loan_id"] == loan_id and a["replay_id"] == replay_id]


class EmptyPrivacy:
    def consents_for_applicant(self, applicant_id: str) -> list[dict[str, Any]]:
        return []

    def consents_for_application(self, application_id: str) -> list[dict[str, Any]]:
        return []

    def get_consent(self, consent_id: str) -> None:
        return None

    def get_proof(self, consent_id: str) -> None:
        return None

    def list_grievances(self, limit: int) -> list[dict[str, Any]]:
        return []

    def grievances_for_applicant(self, applicant_id: str) -> list[dict[str, Any]]:
        return []

    def get_grievance(self, application_id: str, grievance_id: str) -> None:
        return None


class MemoryOps:
    def __init__(self, runs: dict[str, list[dict[str, Any]]]):
        self.data = runs

    def runs(self) -> list[str]:
        return list(self.data)

    def by_run(self, run_id: str) -> list[dict[str, Any]]:
        return self.data[run_id]

    def recent(self, limit: int) -> list[dict[str, Any]]:
        docs = [d for run in self.data.values() for d in run]
        return sorted(docs, key=lambda d: d.get("received_at") or "", reverse=True)[:limit]


def build_deps() -> ConsoleDeps:
    loan_docs, alerts = loans()
    return ConsoleDeps(
        store=MemoryApplications(applications()), raw=NoRaw(), queue=RecordingQueue(),
        pseudonym_key=b"preview-pseudonym-key-not-secret-0000", demo_principal=PRINCIPAL,
        monitoring=MemoryMonitoring(loan_docs, alerts), clock=lambda: NOW, privacy=EmptyPrivacy(),
        ops=MemoryOps({RUN_ID: ops_docs()}),
    )
