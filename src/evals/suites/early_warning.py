"""조기경보 suite: 운영 process_week와 같은 주차 처리(규칙 분류, 탐지기, 새 트리거만 조사)를 경로별 13주 재생한다.

경로 라벨: STACKING=양성(대출 돌려막기 끝에 연체), STABLE=음성(계절 변동만 있음).
비교 기준선 '탐지기 단독'은 트리거가 하나라도 걸리면 경보로 본다.
"""

from collections import Counter
from datetime import date
from typing import Any

from agents.data_assembly import build_pseudonymizer
from agents.early_warning import EwContext, investigate
from agents.features import compute_underwriting
from agents.monitoring import WEEKS, Baseline, categorize, detect, week_window, weekly_series
from evals.datasets import AS_OF, REQUESTED_AMOUNT_KRW, REQUESTED_TENOR_MONTHS, EwPath
from evals.metering import Meter
from evals.metrics import binary, rate
from evals.runner import EvalRunner
from evals.suites import budget_left, cost_of, failure

SUITE = "early_warning"


def baseline_for(p: EwPath) -> Baseline:
    case = p.case
    uw = compute_underwriting(
        p.path_id, case.profile, case.transactions, case.tax_filings, as_of=AS_OF,
        requested_amount_krw=REQUESTED_AMOUNT_KRW, requested_tenor_months=REQUESTED_TENOR_MONTHS,
    )
    return Baseline.from_underwriting({"features": uw.features.model_dump(mode="json"), "monthly": uw.monthly})


def replay(p: EwPath, model: str, *, meter: Meter, runner: EvalRunner, pseudonym_key: bytes) -> dict[str, Any]:
    txns = list(p.flow.transactions)
    start = min(t.booked_on for t in txns)
    mask = build_pseudonymizer(pseudonym_key, p.case.profile, txns).mask
    baseline = baseline_for(p)
    entities = {**p.case.known_entities, p.case.profile.business_reg_no: "BIZNO"}
    seen: list[str] = []
    weeks: list[dict[str, Any]] = []
    for week in range(1, WEEKS + 1):
        visible = [t for t in txns if t.booked_on <= week_window(start, week)[1]]
        cats = categorize(visible, {})
        rows = weekly_series(visible, cats, start, week, baseline)
        triggers = detect(rows, baseline)
        new = [t for t in triggers if t not in seen]
        if not new:
            continue
        seen.extend(new)
        ctx = EwContext(
            alert_id=f"{p.path_id}-w{week:02d}", loan_id=p.path_id, week=week, start=start,
            industry=p.case.profile.industry, triggers=triggers, rows=rows, baseline=baseline,
            transactions=visible, categories=cats, mask=mask,
            terms={"amount_krw": REQUESTED_AMOUNT_KRW, "tenor_months": REQUESTED_TENOR_MONTHS},
        )
        with meter.scope(suite=SUITE, config=model, case=f"{p.path_id}-w{week:02d}", entities=entities):
            result = investigate(ctx, runner=runner, model=model)
        weeks.append({
            "week": week,
            "as_of": ctx.as_of.isoformat(),
            "new_triggers": new,
            "decision": result.decision,  # fallback_alert = 에이전트 오류(모델 판단 아님)
            "rejected_attempts": len(result.record.get("rejected") or []),
            "severity": result.alert.severity.value if result.alert and result.decision == "alert" else None,
        })
    alerts = [w for w in weeks if w["decision"] == "alert"]
    first = alerts[0] if alerts else None
    default_on = p.flow.default_on
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
        "default_on": default_on.isoformat() if default_on else None,
        "lead_days": (default_on - date.fromisoformat(first["as_of"])).days if first and default_on else None,
        "weeks": weeks,
    }


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
            if row["positive"] and not row["agent_alerted"]:
                result["failures"].append(failure("ew_missed", SUITE, p.path_id, f"연체({row['default_on']}) 전 경보 없음", model))
            if not row["positive"] and row["agent_alerted"]:
                result["failures"].append(failure(
                    "ew_false_alarm", SUITE, p.path_id, f"정상 경로 {row['first_alert_week']}주차 경보", model))
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
            "mean_lead_days": round(sum(leads) / len(leads), 1) if leads else None,
            "lead_days": leads,
            **cost,
        }
        if detector is None and ok:
            detector = {"paths": len(ok), **binary([r["positive"] for r in ok], [r["detector_alerted"] for r in ok])}
    result["detector_only"] = detector
    return result
