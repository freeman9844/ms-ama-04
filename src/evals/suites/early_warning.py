"""조기경보 suite: 운영 process_week와 같은 주차 처리(규칙 분류, 원장 모니터, 새 트리거만 조사)를 경로별 13주 재생한다.

경로 라벨: 원인 시나리오 4개(매출 부진·정산 보류·반품 급증·비용 증가)=양성, STABLE=음성.
양성 경로는 정답 원인(`flow.cause`)과 원인 시작 주차(`onset_week`)를 라벨로 들고, 첫 경보의 주원인이 정답과
같은지로 원인 정확도를 잰다. 비교 기준선 '탐지기 단독'은 트리거가 하나라도 걸리면 경보로 보고,
그때 탐지기가 처음 지목한 원인 후보(candidates[0])를 원인으로 본다.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from agents.data_assembly import build_pseudonymizer
from agents.early_warning import EwContext, investigate
from agents.escalation import next_stage
from agents.features import compute_underwriting, monthly_installment
from agents.ledger_monitor import ledger_baseline, observe, platform_weeks
from agents.monitoring import WEEKS, Baseline, categorize, settlement_for, week_window
from evals.datasets import AS_OF, REQUESTED_AMOUNT_KRW, REQUESTED_TENOR_MONTHS, EwPath
from evals.metering import Meter
from evals.metrics import binary, rate
from evals.runner import EvalRunner
from evals.suites import budget_left, cost_of, failure

SUITE = "early_warning"
ALERT_WINDOW_WEEKS = 3  # 원인 시작 주차로부터 이 주 안에 올린 경보만 '제때'로 본다(하한은 없다: 원인 시작보다 이른 경보도 '제때'다)


def baseline_for(p: EwPath) -> Baseline:
    case = p.case
    uw = compute_underwriting(
        p.path_id, case.profile, case.transactions, case.tax_filings, as_of=AS_OF,
        requested_amount_krw=REQUESTED_AMOUNT_KRW, requested_tenor_months=REQUESTED_TENOR_MONTHS,
        pg_settlements=case.pg_settlements,
    )
    return Baseline.from_underwriting(
        {
            "features": uw.features.model_dump(mode="json"),
            "monthly": uw.monthly,
            "settlement_profile": settlement_for(case.transactions, {}, AS_OF, case.profile.industry),
        },
        installment_krw=round(monthly_installment(REQUESTED_AMOUNT_KRW, REQUESTED_TENOR_MONTHS)),
    )


def escalation_weeks(weeks: Sequence[Mapping[str, Any]]) -> dict[str, int | None]:
    """경보가 난 주차의 심각도로 운영과 같은 단계 판정(next_stage)을 차례로 적용해 단계별 첫 도달 주차를 잰다.
    발송은 모두 sent로 본다(평가에는 연락 시각·횟수 제한이 없다)."""
    severities: list[str] = []
    escalation: dict[str, Any] = {}
    reached: dict[str, int | None] = {"stage1_week": None, "stage2_week": None}
    for w in weeks:
        if not w.get("severity"):
            continue
        severities.append(w["severity"])
        stage = next_stage(severities, escalation)
        if stage is not None:
            escalation[f"stage{stage}"] = {"status": "sent"}
            reached[f"stage{stage}_week"] = w["week"]
    return reached


def replay(p: EwPath, model: str, *, meter: Meter, runner: EvalRunner, pseudonym_key: bytes) -> dict[str, Any]:
    txns = list(p.flow.transactions)
    start = min(t.booked_on for t in txns)
    mask = build_pseudonymizer(pseudonym_key, p.case.profile, txns).mask
    baseline = baseline_for(p)
    pg = p.flow.pg_flow()
    lb = ledger_baseline(pg, start, p.case.profile.industry, p.case.transactions)
    entities = {**p.case.known_entities, p.case.profile.business_reg_no: "BIZNO"}
    seen: list[str] = []
    weeks: list[dict[str, Any]] = []
    detector_cause: str | None = None
    for week in range(1, WEEKS + 1):
        visible = [t for t in txns if t.booked_on <= week_window(start, week)[1]]
        cats = categorize(visible, {})
        obs = observe(visible, cats, start, week, baseline, pg, lb)
        new = [t for t in obs.triggers if t not in seen]
        if not new:
            continue
        seen.extend(new)
        # 탐지기 단독 기준선: 원인 후보를 처음 내놓은 주차의 첫 후보가 탐지기가 지목한 원인이다.
        if detector_cause is None and obs.candidates:
            detector_cause = obs.candidates[0].cause.value
        ctx = EwContext(
            alert_id=f"{p.path_id}-w{week:02d}", loan_id=p.path_id, week=week, start=start,
            industry=p.case.profile.industry, triggers=obs.triggers, rows=obs.rows, baseline=baseline,
            transactions=visible, categories=cats, mask=mask,
            terms={"amount_krw": REQUESTED_AMOUNT_KRW, "tenor_months": REQUESTED_TENOR_MONTHS},
            candidates=obs.candidates, pg_weeks=platform_weeks(pg, start, 1, week), ledger_baseline=lb,
        )
        with meter.scope(suite=SUITE, config=model, case=f"{p.path_id}-w{week:02d}", entities=entities):
            result = investigate(ctx, runner=runner, model=model)
        alerted = result.decision == "alert" and result.alert is not None
        cause = result.alert.primary_cause if alerted and result.alert else None
        weeks.append({
            "week": week,
            "as_of": ctx.as_of.isoformat(),
            "new_triggers": new,
            "candidates": [c.cause.value for c in obs.candidates],
            "decision": result.decision,  # fallback_alert = 에이전트 오류(모델 판단 아님)
            "rejected_attempts": len(result.record.get("rejected") or []),
            "severity": result.alert.severity.value if alerted and result.alert else None,
            "primary_cause": cause.value if cause else None,
        })
    alerts = [w for w in weeks if w["decision"] == "alert"]
    first, last = (alerts[0], alerts[-1]) if alerts else (None, None)
    default_on = p.flow.default_on
    onset_week = p.onset_week
    return {
        "config": model,
        "case": p.path_id,
        "archetype": p.case.archetype.value,
        "scenario": p.flow.scenario.value,
        "positive": p.positive,
        "status": "error" if any(w["decision"] == "fallback_alert" for w in weeks) else "ok",
        "investigations": len(weeks),
        "detector_alerted": bool(weeks),
        "agent_alerted": bool(alerts),
        "first_alert_week": first["week"] if first else None,
        "true_cause": p.flow.cause.value if p.flow.cause else None,
        "onset_week": onset_week,
        "first_alert_cause": first["primary_cause"] if first else None,
        "last_alert_cause": last["primary_cause"] if last else None,
        "detector_cause": detector_cause,
        "in_window": bool(first and onset_week and first["week"] <= onset_week + ALERT_WINDOW_WEEKS),  # 조기 경보 포함
        "default_on": default_on.isoformat() if default_on else None,
        "lead_days": (default_on - date.fromisoformat(first["as_of"])).days if first and default_on else None,
        "escalation": escalation_weeks(alerts),
        "weeks": weeks,
    }


def _cause_accuracy(rows: list[dict[str, Any]], field: str) -> float | None:
    """정답 원인이 있는 경로 중 그 원인을 맞힌 비율. 원인을 아예 못 고른 경로(None)는 분모에서 뺀다."""
    scored = [r for r in rows if r["true_cause"] and r[field] is not None]
    return rate(sum(r[field] == r["true_cause"] for r in scored), len(scored))


def run(paths: list[EwPath], models: list[str], *, meter: Meter, runner: EvalRunner, pseudonym_key: bytes) -> dict[str, Any]:
    result: dict[str, Any] = {"partial": False, "skipped": 0, "configs": {}, "cases": [], "failures": []}
    detector: dict[str, Any] | None = None
    for model in models:
        rows: list[dict[str, Any]] = []
        for p in paths:
            if not budget_left(meter, result):
                continue
            try:
                row = replay(p, model, meter=meter, runner=runner, pseudonym_key=pseudonym_key)
            except Exception as e:  # noqa: BLE001
                rows.append({"config": model, "case": p.path_id, "status": "error"})
                result["failures"].append(failure("agent_error", SUITE, p.path_id, f"{type(e).__name__}: {e}", model))
                continue
            rows.append(row)
            if row["status"] == "error":
                result["failures"].append(failure("agent_error", SUITE, p.path_id, "조사 에이전트 실패(탐지기 단독 경보로 대체)", model))
            if row["positive"] and not row["in_window"]:
                result["failures"].append(failure(
                    "ew_missed", SUITE, p.path_id,
                    f"원인 시작 {row['onset_week']}주차 + {ALERT_WINDOW_WEEKS}주 안에 경보 없음"
                    f"(첫 경보 {row['first_alert_week']}주차)", model))
            if not row["positive"] and row["agent_alerted"]:
                result["failures"].append(failure(
                    "ew_false_alarm", SUITE, p.path_id, f"정상 경로 {row['first_alert_week']}주차 경보", model))
            if row["positive"] and row["agent_alerted"] and row["first_alert_cause"] != row["true_cause"]:
                result["failures"].append(failure(
                    "ew_wrong_cause", SUITE, p.path_id,
                    f"첫 경보 주원인 {row['first_alert_cause']} ≠ 정답 {row['true_cause']}", model))
        result["cases"].extend(rows)
        ok = [r for r in rows if r["status"] == "ok"]
        leads = [r["lead_days"] for r in ok if r["lead_days"] is not None]
        decisions = Counter(w["decision"] for r in ok for w in r["weeks"])
        calls = meter.model_calls(suite=SUITE, config=model)
        cost = cost_of(calls)
        result["configs"][model] = {
            "paths": len(rows),
            "evaluated": len(ok),
            **binary([r["positive"] for r in ok], [r["agent_alerted"] for r in ok]),
            "investigations": sum(r["investigations"] for r in ok),
            "decisions": dict(decisions),
            "rejected_attempt_rate": rate(sum(w["rejected_attempts"] > 0 for r in ok for w in r["weeks"]),
                                          sum(r["investigations"] for r in ok)),
            "cause_accuracy": _cause_accuracy(ok, "first_alert_cause"),
            "last_cause_accuracy": _cause_accuracy(ok, "last_alert_cause"),
            "in_window_rate": rate(sum(r["in_window"] for r in ok if r["positive"]), sum(r["positive"] for r in ok)),
            "mean_lead_days": round(sum(leads) / len(leads), 1) if leads else None,
            "lead_days": leads,
            "stage1_paths": sum(r["escalation"]["stage1_week"] is not None for r in ok),
            "stage2_paths": sum(r["escalation"]["stage2_week"] is not None for r in ok),
            **cost,
        }
        if detector is None and ok:
            detector = {
                "paths": len(ok),
                **binary([r["positive"] for r in ok], [r["detector_alerted"] for r in ok]),
                "cause_accuracy": _cause_accuracy(ok, "detector_cause"),
            }
    result["detector_only"] = detector
    return result
