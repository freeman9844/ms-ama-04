"""metrics.json → 한국어 보고서(markdown). 모든 숫자는 합성 데이터 기준이다."""
from __future__ import annotations

from typing import Any

from opsmetrics.labels import AXIS_LABELS, STEP_LABELS


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def _num(x: float | None, nd: int = 1) -> str:
    return "—" if x is None else f"{x:.{nd}f}"


def _usd(x: float | None) -> str:
    return "—" if x is None else f"${x:,.4f}"


def _verdict(met: bool, insufficient: bool) -> str:
    return "표본 부족" if insufficient else ("충족" if met else "미달")


def _slo(s: dict[str, Any]) -> list[str]:
    lat, ag = s["latency"], s["agent_success"]
    lines = [
        "## 1. SLO", "",
        "| 지표 | 목표 | 측정 | 판정 |", "|---|---|---|---|",
        f"| 접수 → 심사역 대기열 p95 | ≤ {_num(lat['target_min'], 0)}분 | {_num(lat['p95_min'])}분 "
        f"(p50 {_num(lat['p50_min'])}, 최대 {_num(lat['max_min'])}, n={lat['n']}) | "
        f"{_verdict(lat['met'], lat['insufficient'])} |",
        f"| 에이전트 단계 성공률 | ≥ {_pct(ag['target'])} | {_pct(ag['rate'])} (ok {ag['ok']}/{ag['total']}, "
        f"degraded {ag['degraded']}, failed {ag['failed']}) | {_verdict(ag['met'], ag['total'] == 0)} |",
        "",
        f"미도달 {lat['unreached']}건, 접수 시각 대체 {lat['received_only']}건. p95는 nearest-rank입니다. "
        "표본이 작아 신뢰 구간이 넓고, 용량 시험이 아닙니다. degraded(안전망 경로)는 성공으로 세지 않습니다.",
        "", "| 단계 | ok | degraded | failed |", "|---|---|---|---|",
    ]
    for step, c in sorted(ag["by_step"].items()):
        lines.append(f"| {STEP_LABELS.get(step, step)} | {c['ok']} | {c['degraded']} | {c['failed']} |")
    return lines


def _cost(s: dict[str, Any]) -> list[str]:
    cost, fixed = s["ai_cost"], s["fixed"]
    lines = ["", "## 2. 비용", "", "| 모델 | 호출 | 입력 토큰 | 출력 토큰 | 비용 |", "|---|---|---|---|---|"]
    for model, m in sorted(cost["by_model"].items()):
        lines.append(f"| {model} | {m['calls']} | {m['input_tokens']:,} | {m['output_tokens']:,} | {_usd(m['cost_usd'])} |")
    lines += [
        "",
        f"AI 비용 합계 {_usd(cost['total_usd'])}, 신청당 {_usd(cost['per_application_usd'])} "
        f"(캐시 할인 미반영 상한, 단가 미확인 호출 {cost['unpriced_calls']}건).",
        f"고정비(월): {_usd(fixed['monthly_usd'])} — {fixed['source']}.",
        "", "| 월 물량 | AI/건 | 고정비/건 | 합계/건 | 수작업 대비 | 대출 1건(USD 6,000) 대비 |",
        "|---|---|---|---|---|---|",
    ]
    for r in s["unit_economics"]:
        lines.append(f"| {r['volume']:,} | {_usd(r['ai_usd'])} | {_usd(r['fixed_usd'])} | {_usd(r['total_usd'])} | "
                     f"{_pct(r['vs_manual'])} | {_pct(r['vs_ticket'])} |")
    lines.append("")
    lines.append("수작업 처리 원가 $3,500/건 가정: Mills & McCarthy, HBS Working Paper 15-004 "
                 "(소상공인 대출 1건 처리 원가 $3,000~4,000의 중간값).")
    return lines


def _fairness(s: dict[str, Any]) -> list[str]:
    f = s["fairness"]
    lines = ["", "## 3. 공정성(추천 approve율)", "",
             f"임계값 {f['threshold_pp']:g}pp, 값별 평가 표본 < {f['min_samples']}이면 격차 계산에서 제외합니다."]
    for axis, a in f["axes"].items():
        lines += ["", f"### {AXIS_LABELS.get(axis, axis)}", "",
                  "| 값 | 신청 | 평가 | approve율 | eligible율 | 비고 |", "|---|---|---|---|---|---|"]
        for value, v in a["values"].items():
            lines.append(f"| {value} | {v['n']} | {v['assessed']} | {_pct(v['approve_rate'])} | "
                         f"{_pct(v['eligible_rate'])} | {'표본 부족' if v['insufficient'] else ''} |")
        note = "규칙 R104(업력)로 설명되는 차이 — 경보 대상 아님" if a["policy_explained"] else (
            "**경보: 임계값 초과**" if a["alert"] else "임계값 이내")
        lines += ["", f"격차 {a['gap_pp']:g}pp(eligible {a['eligible_gap_pp']:g}pp), "
                      f"원형 통제 최대 {a['controlled']['max_pp']:g}pp. {note}."]
    return lines


def _faults(faults: list[dict[str, Any]]) -> list[str]:
    if not faults:
        return []
    lines = ["", "## 4. 장애 시나리오"]
    for f in faults:
        reverted = {True: "확인", False: "실패"}.get(f["reverted"], "기록 없음")
        lines += ["", f"### {f['scenario'] or f['run_id']} (run {f['run_id']}, 원복 {reverted})", "",
                  "| 신청 | 상태 | 플래그 | 단계 |", "|---|---|---|---|"]
        for a in f["applications"]:
            steps = ", ".join(f"{s['step']}:{s['outcome']}" for s in a["steps"])
            lines.append(f"| {a['application_id']} | {a['status']} | {', '.join(a['flags']) or '—'} | {steps} |")
        if f["dependencies_error"]:
            lines += ["", f"의존성 조회 실패: {f['dependencies_error']}"]
        elif f["dependencies"] is not None:
            lines += ["", "| 신청 | 대상 | 역할 | 호출 | 실패 |", "|---|---|---|---|---|"]
            for app, rows in f["dependencies"].items():
                for r in rows or [{"target": "—", "role": "—", "total": 0, "failed": 0}]:
                    lines.append(f"| {app} | {r['target']} | {r['role']} | {r['total']} | {r['failed']} |")
    return lines


def render(data: dict[str, Any]) -> str:
    s = data["summary"]
    lines = [f"# 운영 지표 보고서 — {data['run_id']}", "",
             f"> {s['basis']}. 신청 {s['applications']}건, 생성 {data['generated_at']}.", ""]
    load = data.get("load") or {}
    if load.get("unreached"):
        ids = ", ".join(f"{u['application_id']}({u['status']})" for u in load["unreached"])
        lines += [f"미도달 {len(load['unreached'])}건: {ids}", ""]
    if load.get("aborted"):
        lines += [f"예산 가드로 제출 중단: 예상 {_usd((load.get('guard') or {}).get('projected_usd'))}", ""]
    lines += _slo(s) + _cost(s) + _fairness(s) + _faults(data.get("faults") or [])
    return "\n".join(lines) + "\n"
