"""Server-rendered evidence choices. The model selects keys, never labels, values or units."""

import re
from collections.abc import Mapping
from typing import Any

from shared.amount_advice import has_proposal
from shared.money import format_usd
from shared.product_context import product_of

SETTLEMENT_RATE = "cashflow.settlement.pg_reconciliation_ratio"

_OFFER = re.compile(
    r"감액|대체\s*금액|대안(?:\s*금액|으로)|일부만\s*승인|한도(?:만큼|까지)\s*(?:대출|승인)|"
    r"(?:금액|대출|신청액|한도).{0,35}(?:낮추|줄이|축소)|"
    r"\b(?:counteroffer|(?:smaller|lower|reduced) (?:amount|loan)|partial approval|"
    r"reduce (?:it|the (?:loan|amount|request))|(?:would|could|should) (?:offer|lend)|"
    r"(?:offer|lend|approve|suggest|recommend)\s+\d+|lend (?:up to |at )?the (?:policy )?(?:limit|cap))\b", re.I)
_MONEY = re.compile(r"(?:[₩$]|\b(?:krw|usd)\b|\d[\d,.]*\s*(?:[억만천백]*\s*원|million|thousand|[mk]\b)|"
                    r"\d{1,3}(?:,\d{3})+|\d{5,}|[일이삼사오육칠팔구십백천만억]+\s*원|\b(?:won|dollars?)\b)", re.I)
_IMPLICIT = re.compile(r"(?:이면|라면|수준이면).{0,16}(?:적절|충분|진행|적당)|정도가\s*적당|"
                       r"\b(?:would work|would (?:be|make) (?:a |the )?(?:prudent|safer|suitable)|"
                       r"(?:suitable|prudent|recommended|appropriate) loan size)\b", re.I)
_EN_AMOUNT = r"(?:KRW|₩|USD|\$)\s*\d[\d,]*(?:\.\d{1,2})?"
_KO_AMOUNT = r"\d[\d,]*\s*(?:억|천만|백만|만|천)?\s*원"
_DEPOSIT_CHECK = re.compile(
    rf"\b(?:match|compare|reconcile|verify|check)\s+(?:the\s+)?(?:{_EN_AMOUNT}\s+deposit\b|deposit of\s+{_EN_AMOUNT}\b)|"
    rf"(?:{_KO_AMOUNT}(?:의|짜리)?\s*입금|입금액\s*{_KO_AMOUNT})"
    r"(?=[^.!?;\n]{0,40}(?:대조|확인))", re.I)
_VERDICT = re.compile(r"승인|거절|비권고|판단\s*보류|감액|조건부|"
                      r"\b(?:approv\w*|declin\w*|reject\w*|refer the request|defer judgment|"
                      r"(?:wise|unwise|safe) to proceed|go ahead with the loan)\b", re.I)


def counteroffer(text: str, requested: int | None, *, usd: bool = False) -> bool:
    # Field labels describe facts, not imperative verbs ("policy limit KRW …").
    clauses = re.sub(r"\bpolicy (?:limit|cap)\b|(?:실제\s*)?승인액(?:은|는)?", "field", text, flags=re.I)
    return bool(_OFFER.search(text) or has_proposal(clauses, requested, usd=usd) or (_MONEY.search(text) and _IMPLICIT.search(text)))


def reviewed_detail(text: str, requested: int | None) -> bool:
    return bool(text.strip() and not (_VERDICT.search(text) or counteroffer(text, requested)
                                      or _MONEY.search(_DEPOSIT_CHECK.sub("", text))))


def evidence_menu(snapshot: Mapping[str, Any], language: str) -> dict[str, str]:
    app = snapshot.get("application") or {}
    product = product_of(app)
    unit = "cents" if product else "krw"
    en = bool(product) or language == "en"
    missing = "not recorded" if en else "기록 없음"
    facts: dict[str, str] = {}

    def amount(value: Any) -> str:
        if product:
            return format_usd(value) if type(value) is int else missing
        if type(value) not in (int, float):
            return missing
        return f"KRW {value:,}" if en else f"{value:,}원"

    def money(path: str, ko: str, english: str, value: Any) -> None:
        facts[path] = f"{english if en else ko}: {amount(value)}"

    policy = snapshot.get("policy") or {}
    officer = snapshot.get("officer_decision") or {}
    cf = snapshot.get("cashflow") or {}
    money(f"application.requested_amount_{unit}", "신청액", "Requested amount", app.get(f"requested_amount_{unit}"))
    money(f"policy.max_amount_{unit}", "정책 한도", "Policy limit", policy.get(f"max_amount_{unit}"))
    money(f"officer_decision.amount_{unit}", "승인액", "Officer-approved amount",
          officer.get(f"amount_{unit}") if officer.get("decision") in ("approve", "conditional") else None)
    money(f"cashflow.features.avg_monthly_inflow_{unit}", "월평균 입금", "Average monthly inflow",
          (cf.get("features") or {}).get(f"avg_monthly_inflow_{unit}"))

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
            is_money = field.endswith(f"_{unit}") or (not product and
                path.startswith("cashflow.monthly.") and field in ("revenue", "operating_out", "debt_service", "owner_draw"))
            facts[path] = f"{label}: {amount(value) if is_money else value}"

    walk("policy", "Policy" if en else "정책", policy)
    walk("cashflow", "Cash flow" if en else "현금흐름", cf)
    settlement = cf.get("settlement") or {}
    ratio = settlement.get("pg_reconciliation_ratio") if settlement.get("available") is True else None
    percent = f"{ratio * 100:g}%" if type(ratio) in (int, float) and 0 <= ratio <= 1 else missing
    facts[SETTLEMENT_RATE] = f"{'Settlement reconciliation rate' if en else '정산 대사율'}: {percent}"
    if "cashflow.features.pg_reconciliation_ratio" in facts:
        facts["cashflow.features.pg_reconciliation_ratio"] = facts[SETTLEMENT_RATE]
    capacity = cf.get("capacity") or {}
    if "short_months" in capacity:
        rows = {r.get("month"): r for r in cf.get("monthly") or ()}
        reasons = []
        for index, month in enumerate(capacity["short_months"]):
            row = rows.get(month, {})
            if all(type(row.get(k + ("_cents" if product else ""))) in (int, float) for k in ("revenue", "operating_out")):
                reason = ("Revenue less operating outflows does not cover existing debt service and the new installment."
                          if en else "매출에서 운영비를 빼면 기존 상환액과 신규 월 상환액을 충당하지 못합니다.")
            else:
                reason = "The revenue or expense record is incomplete." if en else "매출 또는 운영비 기록이 불완전합니다."
            reasons.append(f"{month}: {reason}")
            facts[f"cashflow.capacity.short_months.{index}"] = reasons[-1]
        if reasons:
            facts["cashflow.capacity.short_months"] = "\n".join(reasons)
        elif rows:
            facts["cashflow.capacity.short_months"] = ("No recorded month has a shortfall." if en
                                                       else "기록된 달 중 상환 여력이 부족한 달은 없습니다.")
    walk("officer_decision.decision", "Officer decision" if en else "심사역 결정", officer.get("decision"))
    walk("application.requested_tenor_months", "Requested term (months)" if en else "신청 기간(개월)",
         app.get("requested_tenor_months"))

    opinion = snapshot.get("agent_opinion") or {}
    if (opinion.get("review_passed") is True and opinion.get("state") != "legacy"
            and opinion.get("recommendation") in ("approve", "refer", "decline")):
        for group, ko, english in (("claims", "검토 근거", "Reviewed evidence"),
                                   ("officer_checks", "다음 확인 사항", "Next check")):
            for index, item in enumerate(opinion.get(group) or ()):
                evidence = item.get("evidence") or {}
                refs = [f"{key}: {amount(v) if key.endswith('_' + unit) else v}"
                        for key, v in evidence.items() if type(v) in (int, float)]
                refs.extend(t for t in item.get("txn_ids") or ()
                            if isinstance(t, str) and re.fullmatch(r"[a-zA-Z0-9_:.~-]{1,128}", t))
                text = item.get("text") or ""
                if refs:
                    detail = text if reviewed_detail(text, app.get(f"requested_amount_{unit}")) else ""
                    evidence_text = ", ".join(refs)
                    facts[f"agent_opinion.{group}.{index}"] = (
                        f"{english if en else ko}: {detail} ({evidence_text})" if detail
                        else f"{english if en else ko}: {evidence_text}")
    return facts
