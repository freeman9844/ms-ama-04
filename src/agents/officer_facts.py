"""Server-rendered evidence choices. The model selects keys, never labels, values or units."""

from collections.abc import Mapping
from typing import Any


def evidence_menu(snapshot: Mapping[str, Any], language: str) -> dict[str, str]:
    en = language == "en"
    missing = "not recorded" if en else "기록 없음"
    facts: dict[str, str] = {}

    def amount(value: Any) -> str:
        if type(value) not in (int, float):
            return missing
        return f"KRW {value:,}" if en else f"{value:,}원"

    def money(path: str, ko: str, english: str, value: Any) -> None:
        facts[path] = f"{english if en else ko}: {amount(value)}"

    app = snapshot.get("application") or {}
    policy = snapshot.get("policy") or {}
    officer = snapshot.get("officer_decision") or {}
    cf = snapshot.get("cashflow") or {}
    money("application.requested_amount_krw", "신청액", "Requested amount", app.get("requested_amount_krw"))
    money("policy.max_amount_krw", "정책 한도", "Policy limit", policy.get("max_amount_krw"))
    money("officer_decision.amount_krw", "승인액", "Officer-approved amount",
          officer.get("amount_krw") if officer.get("decision") in ("approve", "conditional") else None)
    money("cashflow.features.avg_monthly_inflow_krw", "월평균 입금", "Average monthly inflow",
          (cf.get("features") or {}).get("avg_monthly_inflow_krw"))

    # These branches are the console's allowlisted, factual snapshot, not agent prose.
    # Keep a row's rule code or month when displaying one of its members.
    def walk(path: str, label: str, value: Any) -> None:
        if path in facts or value is None:
            return
        if isinstance(value, Mapping):
            for key, child in value.items():
                walk(f"{path}.{key}", f"{label} · {key}", child)
        elif isinstance(value, (tuple, list)):
            for index, child in enumerate(value):
                row = (child.get("code") or child.get("month")) if isinstance(child, Mapping) else None
                walk(f"{path}.{index}", f"{label} [{row or index}]", child)
        elif isinstance(value, (str, bool, int, float)):
            field = path.rsplit(".", 1)[-1]
            is_krw = field.endswith("_krw") or (
                path.startswith("cashflow.monthly.") and field in ("revenue", "operating_out", "debt_service", "owner_draw"))
            facts[path] = f"{label}: {amount(value) if is_krw else value}"

    walk("policy", "Policy" if en else "정책", policy)
    walk("cashflow", "Cash flow" if en else "현금흐름", cf)
    walk("officer_decision.decision", "Officer decision" if en else "심사역 결정", officer.get("decision"))
    walk("application.requested_tenor_months", "Requested term (months)" if en else "신청 기간(개월)",
         app.get("requested_tenor_months"))

    opinion = snapshot.get("agent_opinion") or {}
    if opinion.get("recommendation") in ("approve", "refer", "decline"):
        for group, ko, english in (("claims", "검토 근거", "Reviewed evidence"),
                                   ("officer_checks", "다음 확인 사항", "Next check")):
            for index, item in enumerate(opinion.get(group) or ()):
                evidence = item.get("evidence") or {}
                if evidence:
                    values = ", ".join(f"{key}: {amount(v) if key.endswith('_krw') else v}"
                                       for key, v in evidence.items() if type(v) in (int, float))
                    if values:
                        facts[f"agent_opinion.{group}.{index}"] = f"{english if en else ko}: {values}"
    return facts
