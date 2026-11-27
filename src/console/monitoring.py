"""콘솔의 사후 모니터링 화면 데이터(P5a). 경보 문장의 가명 토큰 복원과 근거 값 조회를 한다.

오케스트레이터는 심사 동의의 프로필 + 모니터링 동의의 사후 흐름(flow.json)으로 가명처리기를 만든다.
복원도 같은 입력으로 해야 같은 토큰 매핑이 나온다.
"""

import re
from collections.abc import Mapping
from typing import Any, Protocol

from agents.collections import RepaymentCase, SCRIPTS, usd_text_choices
from agents.ledger_monitor import LedgerBaseline, ledger_catalog
from agents.monitoring import Baseline, evidence_catalog
from agents.pretranslate import load_case_raw
from agents.repayment import item_amount, item_key
from console import i18n
from console.i18n import _
from console.views import RawData, RawStore, _map_strings, _text_functions
from shared.product_context import product_of

SEVERITY_LABELS = {"low": "낮음", "medium": "중간", "high": "높음"}
ACTION_LABELS = {
    "monitor": "관찰",
    "contact_borrower": "차주 연락",
    "hardship_review": "상환 곤란 검토",
    "officer_review": "심사역 확인",
}
MONITORING_LABELS = {
    "replaying": "재생 중",
    "paused": "심사역 결정 대기",
    "awaiting_customer": "고객 응답 대기",
    "awaiting_resume": "재개 확인 대기",
    "stopped": "재생 종료",
    "completed": "재생 완료",
    "failed": "재생 실패",
}
TRIGGER_LABELS = {
    "inflow_drop": "매출 급감",
    "buffer_drop": "현금 버퍼 감소",
    "volatility_spike": "변동성 급증",
    "new_debt": "신규 차입",
    "projected_shortfall": "잔고 부족 전망",
    "sales_slump": "주문 감소",
    "settlement_hold": "정산 보류",
    "return_spike": "반품 급증",
    "cost_surge": "공제 증가",
    "due_shortfall": "상환일 잔고 부족",
    "basis_breach": "승인 근거 훼손",
}
REPAYMENT_LABELS = {"scheduled": "예정", "paid": "납부", "partial": "부분납", "missed": "미납"}
# USD 감시 일지(단계형 조기경보).
STAGE_LABELS = {"normal": "정상", "watch": "관찰", "medium": "중간", "high": "높음", "resolved": "해소"}
SOURCE_LABELS = {"bank": "은행", "settlement": "정산", "market": "마켓"}
SIGNAL_LABELS = TRIGGER_LABELS | {
    "orders_dip": "주문 일시 감소",
    "returns_rising": "반품률 상승",
    "deductions_rising": "공제율 상승",
    "cancel_rise": "취소율 상승",
    "cancel_spike": "취소율 급등",
    "rating_drop": "평점 하락",
    "grade_down": "등급 강등",
    "account_warning": "판매 경고·정지",
}
# 일지 줄의 설명. 키는 incident.py의 kind(explained·suppressed·hold·quiet·resolved_quiet·resolved_agent)와
# 줄의 에이전트(fallback)·경보 처리(updated). normal·call은 신호와 판단이 줄을 채워 따로 설명하지 않는다.
JOURNAL_LABELS = {
    "explained": "정상 · 은행 입금 감소는 정산 예정표로 설명됨",
    "suppressed": "정상 · 해소된 신호가 이어짐",
    "hold": "변화 없음 · 단계 유지",
    "quiet": "신호 없음 %(n)s주째",
    "resolved_quiet": "해소 · 2주 연속 신호 없음",
    "resolved_agent": "해소 · 에이전트 판단",
    "fallback": "에이전트 미응답 · 규칙 단계",
    "updated": "원인 갱신",
}
# 승인 근거 코드(USD 정산·마켓 신호) → 승인 당시 근거 설명.
BASIS_LABELS = {
    "S003": "빠르고 정확한 정산",
    "S005": "정산 원장으로 매출 실재 확인",
    "S004": "계절 매출 패턴",
}
# 승인 근거 지표 id(usd_monitoring.BASIS_METRICS) → 표시 이름.
BASIS_METRIC_LABELS = {
    "recon_ratio": "정산 대사율",
    "orders_vs_prior_year": "작년 대비 주문",
}
# 감시 일지 증거 칩: 지표 이름(근거 ID의 마지막 조각) → (라벨, 단위). 단위는 pct·usd·text·rating·count.
# USD 신호가 인용하는 지표 전부다(usd_monitoring._EVIDENCE·BASIS_METRICS). 표에 없는 지표는 ID와 값 그대로 보인다.
EVIDENCE_LABELS = {
    "orders_vs_expected": ("정산 주문 기대 대비", "pct"),
    "revenue_vs_expected": ("은행 매출 기대 대비", "pct"),
    "projected_min_balance_cents": ("예상 최저 잔액", "usd"),
    "due_balance_cents": ("상환일 잔액", "usd"),
    "order_gross_cents": ("주문액", "usd"),
    "expected_orders_cents": ("기대 주문액", "usd"),
    "held_cents": ("보류액", "usd"),
    "mkt_grade": ("마켓 등급", "text"),
    "mkt_cancel_rate": ("취소율", "pct"),
    "return_rate": ("반품률", "pct"),
    "deduction_rate": ("공제율", "pct"),
    "mkt_rating": ("평점", "rating"),
    "mkt_penalties": ("계정 경고", "count"),
    "recon_ratio": (BASIS_METRIC_LABELS["recon_ratio"], "pct"),
    "orders_vs_prior_year": (BASIS_METRIC_LABELS["orders_vs_prior_year"], "pct"),
}
# 재생을 멈춘 대기 항목(2026-11-11). 이 순서대로 멈춤 배너에 보인다(journey.holds.HOLD_ORDER와 같은 순서).
HOLD_LABELS = {
    "collections_proposal": "추심 승인 대기",
    "customer_response": "고객 응답 대기",
    "plan_change": "플랜 변경 승인 대기",
    "restructure": "재조정 결정 대기",
}
# 추심 제안서의 정책 확인 항목(agents.collections.build_proposal의 checks[].id).
CHECK_LABELS = {
    "approved_script": "승인 스크립트",
    "contact_hours": "연락 시간",
    "weekly_limit": "7일 7회",
    "banned_words": "금지 표현 없음",
    "pii_tokens": "가명 토큰 없음",
    "hardship_grounds": "재조정 근거",
}
SCRIPT_LABELS = {k: v["purpose"] for k, v in SCRIPTS.items()} | {
    "balance_warning": "잔고 부족 예상 알림",
    "early_reminder": "5일 전 추가 안내",
    "self_plan_offer": "상환 플랜 제안",
}
CONTACT_STATUS_LABELS = {"sent": "발송", "deferred": "보류", "skipped": "건너뜀"}
CONTACT_REASON_LABELS = {"contact_hours": "연락 시간 밖", "weekly_limit": "7일 7회 초과", "no_options": "고를 옵션 없음"}
PROPOSAL_LABELS = {
    "paid": "완납으로 종료",
    "superseded": "잔액 변경으로 종료",
    "pending_officer": "심사역 승인 대기",
    "approved": "승인",
    "modified": "수정 후 승인",
    "rejected": "반려",
}
PLAN_OPTION_LABELS = {"move": "상환일 이동", "split": "분할납부", "keep": "지금 일정 유지"}
OFFER_LABELS = {"open": "고객 응답 대기", "chosen": "고객 선택", "kept": "유지 선택", "expired": "만료(고객 응답 없이 계속)"}
PLAN_CHANGE_LABELS = {"pending_officer": "심사역 승인 대기", "approved": "승인", "declined": "반려"}
# 차주 화면의 셀프 플랜 상태(borrower_plan의 status). 심사역 ID·결정 사유는 차주에게 보이지 않는다.
BORROWER_PLAN_LABELS = {
    "open": "플랜을 골라 주세요",
    "reviewing": "심사역 확인 중",
    "approved": "승인(상환 일정 변경)",
    "declined": "반려(지금 일정대로 상환)",
    "kept": "지금 일정 유지",
    "expired": "만료",
}
# 차주 화면 "받은 안내"에 싣는 연락(3일 전 기본 안내·조기경보 단계 안내·5일 전 추가 안내·심사역 승인 추심 문구).
# 기본 안내는 문구가 기록되지 않아 스크립트 라벨로 보인다.
BORROWER_NOTICE_BY = ("reminder", "early_warning", "early_reminder", "officer")
# 템플릿 필터 이름 → 라벨(없는 값은 원문 그대로).
MONITORING_FILTERS = {
    "hold_label": HOLD_LABELS,
    "check_label": CHECK_LABELS,
    "script_label": SCRIPT_LABELS,
    "contact_status_label": CONTACT_STATUS_LABELS,
    "contact_reason_label": CONTACT_REASON_LABELS,
    "proposal_label": PROPOSAL_LABELS,
    "plan_option_label": PLAN_OPTION_LABELS,
    "offer_label": OFFER_LABELS,
    "plan_change_label": PLAN_CHANGE_LABELS,
    "borrower_plan_label": BORROWER_PLAN_LABELS,
    "basis_label": BASIS_LABELS,
    "basis_metric_label": BASIS_METRIC_LABELS,
    "source_label": SOURCE_LABELS,
    "signal_label": SIGNAL_LABELS,
}
# 조기경보 원인 라벨(2026-11-11). 경보 문서 primary_cause·secondary_causes 값(str)을 그대로 키로 쓴다.
CAUSE_LABELS = {
    "sales_slump": "매출 부진",
    "settlement_hold": "정산 보류",
    "return_spike": "반품 급증",
    "cost_surge": "비용 증가",
    "high_cost_borrowing": "고금리 차입",
    "settlement_assigned": "정산 양도",
}
NO_CAUSE_LABEL = "—"
NO_LEDGER_LABEL = "정산 원장 없음"
# collections·restructure는 미납 추심 전환 전에 경보 문서에 붙던 필드다(옛 문서 표시용).
_SHOWN_FIELDS = ("summary", "check_action", "signals", "collections", "restructure", "why_now", "outlook", "adjust_reason")
_JOURNAL_TEXT = ("why_now", "outlook", "adjust_reason")
_CASE_FIELDS = ("recent_alert", "collections", "restructure")
# 제안서에서 가명 토큰을 복원할 필드. 고객 문구(text·sent_text)는 토큰이 없어 그대로 보인다.
_PROPOSAL_FIELDS = ("reason", "hardship", "evidence")
_ITEM_KEY = re.compile(r"i(\d+)(?:p(\d+))?")
_LEDGER_ROW_FIELDS = ("week", "order_gross", "returns", "held", "deductions", "settle_net")


class MonitoringStore(Protocol):
    def list_loans(self, limit: int, *, legacy: bool = False) -> list[dict[str, Any]]: ...

    def get_loan(self, loan_id: str) -> dict[str, Any] | None: ...

    def list_alerts(self, loan_id: str, replay_id: str) -> list[dict[str, Any]]: ...

    def get_contacts(self, loan_id: str, replay_id: str) -> dict[str, Any] | None: ...


def load_monitoring_raw(store: RawStore, loan: Mapping[str, Any]) -> RawData | None:
    raw = load_case_raw("loans", loan, store, lambda *_: None)
    return RawData(*raw) if raw else None


def loan_view(
    loan: Mapping[str, Any],
    alerts: list[dict[str, Any]],
    raw: RawData | None,
    key: bytes,
    *,
    restore: bool,
    contacts: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    pii, model_text, raw_text = _text_functions(raw, key, restore)
    mon = loan.get("monitoring") or {}
    weeks = mon.get("weeks") or []
    rows = [w["row"] for w in weeks]
    usd = bool(product_of(loan))
    if usd:
        catalog = {f"{'F' if k.startswith(('projected_', 'due_balance', 'shortfall_date', 'next_due_on')) else 'W'}:{r['week']}:{k}": v
                   for r in rows for k, v in r.items()}
        catalog.update({f"B:{k}": v for k, v in (loan.get("baseline") or {}).items()})
    else:
        catalog = evidence_catalog(rows, Baseline.from_dict(loan["baseline"]), loan.get("industry", "")) if rows else {}
    lb = mon.get("ledger_baseline")
    catalog |= ledger_catalog(LedgerBaseline.from_dict(lb) if lb else None)
    ledger_rows = ([r for r in rows if "order_gross_cents" in r] if usd else
                   [{k: r[k] for k in _LEDGER_ROW_FIELDS} for r in rows if "order_gross" in r])
    ledger_note = _(NO_LEDGER_LABEL) if weeks and not ledger_rows else None
    journal = _journal_view(weeks, catalog)
    for line in journal["lines"] if journal else ():
        line.update(_map_strings({k: line[k] for k in _JOURNAL_TEXT}, model_text))
    market_rows = [r for r in rows if "mkt_cancel_rate" in r] if usd else []
    shown: list[dict[str, Any]] = []
    cases: list[dict[str, Any]] = []
    wanted: dict[str, None] = {}
    for doc in sorted(alerts, key=lambda d: d.get("week", 0)):
        if doc.get("kind") == "repayment_case":
            text = _map_strings({k: doc.get(k) for k in _CASE_FIELDS}, model_text)
            proposal = doc.get("proposal")
            if proposal:
                proposal = {**proposal, **_map_strings({k: proposal.get(k) for k in _PROPOSAL_FIELDS}, model_text)}
                if _pending(proposal) and product_of(doc):
                    proposal["text_choices"] = usd_text_choices(proposal.get("script_id", ""), "modify", RepaymentCase.from_doc(doc))
            cases.append(
                {
                    **doc,
                    **text,
                    "proposal": proposal,
                    "decidable": _pending(text["restructure"]),
                    "proposal_decidable": _pending(proposal),
                }
            )
            continue
        text = _map_strings({k: doc.get(k) for k in _SHOWN_FIELDS}, model_text)
        signals = []
        for s in text["signals"] or ():
            signals.append(
                {
                    "kind": s["kind"],
                    "text": s["text"],
                    "evidence": [(m, catalog.get(m)) for m in s.get("metric_ids", ())],
                    "txn_ids": list(s.get("txn_ids", ())),
                    "source": s.get("source"),
                }
            )
            for t in s.get("txn_ids", ()):
                wanted.setdefault(t, None)
        restructure = text["restructure"]
        shown.append(
            {
                **doc,
                **text,
                "signals": signals,
                "decidable": _pending(restructure),
                "cause_label": i18n.label(CAUSE_LABELS, doc.get("primary_cause"), NO_CAUSE_LABEL),
                "secondary_labels": [i18n.label(CAUSE_LABELS, c) for c in doc.get("secondary_causes") or ()],
                "auto_contacts": _auto_contacts(mon, doc.get("alert_id"), contacts),
            }
        )
    transactions = [
        {
            "txn_id": t.txn_id,
            "booked_on": t.booked_on.isoformat(),
            **({"amount_cents": t.amount_cents} if usd else {"amount_krw": t.amount_krw}),
            "description": raw_text(t.description),
        }
        for t in (raw.transactions if raw else ())
        if t.txn_id in wanted
    ]
    return {
        "loan": loan,
        "monitoring": mon,
        "weeks": weeks,
        "alerts": shown,
        "cases": cases,
        "transactions": transactions,
        "pii": pii,
        "ledger_rows": ledger_rows,
        "ledger_note": ledger_note,
        "journal": journal,
        "market_rows": market_rows,
        "market_note": _("마켓 자료 없음") if usd and weeks and not market_rows else None,
        "basis_cards": _basis_cards(loan, weeks),
        **({"signal_hits": signal_metrics(weeks)} if usd else {}),
        **_plan_view(loan.get("repayment") or {}),
        "holds": sorted(mon.get("holds") or (), key=lambda h: list(HOLD_LABELS).index(h) if h in HOLD_LABELS else 99),
    }


def _journal_view(weeks: list[dict[str, Any]], catalog: Mapping[str, Any]) -> dict[str, Any] | None:
    """USD 감시 일지: 주별 일지 줄(최신 주가 먼저)과 열린 사건. 일지 줄이 하나도 없는 옛·KRW 재생은 None."""
    entries = [(w["week"], w["watch"], w.get("row") or {}) for w in weeks if w.get("watch")]
    if not entries:
        return None
    lines: list[dict[str, Any]] = []
    incident: dict[str, Any] | None = None
    for week, watch, row in entries:
        if watch.get("stage") == "resolved":
            incident = None
        elif watch.get("incident_id"):
            if incident is None or incident["id"] != watch["incident_id"]:
                incident = {"id": watch["incident_id"], "since": week}
            incident["stage"] = watch["stage"]
        lines.append({
            "week": week,
            "stage": watch["stage"],
            "kind": watch.get("kind"),
            "quiet_weeks": watch.get("quiet_weeks") or 0,
            "agent": watch.get("agent"),
            "why_now": watch.get("why_now"),
            "outlook": watch.get("outlook"),
            "adjust_reason": watch.get("adjust_reason"),
            "alert_id": watch.get("alert_id"),
            "alert_action": watch.get("alert_action"),
            "notes": _journal_notes(watch),
            "sources": _journal_sources(watch, row, catalog),
        })
    return {
        "lines": lines[::-1],
        "open": {"stage": incident["stage"], "since": incident["since"]} if incident else None,
    }


def _journal_notes(watch: Mapping[str, Any]) -> list[str]:
    keys = [watch.get("kind"), "fallback" if watch.get("agent") == "fallback" else None,
            "updated" if watch.get("alert_action") == "updated" else None]
    return [_(JOURNAL_LABELS[k], n=watch.get("quiet_weeks") or 0) for k in keys if k in JOURNAL_LABELS]


def _journal_sources(watch: Mapping[str, Any], row: Mapping[str, Any], catalog: Mapping[str, Any]) -> list[dict[str, Any]]:
    # 마켓 흐름이 있어도 그 주 스냅샷 행(mkt_*)이 없으면 자료 없음이다. 정상으로 보이지 않게 한다.
    market_missing = not watch.get("market") or "mkt_cancel_rate" not in row
    out = []
    for source in SOURCE_LABELS:
        signals = [
            {"code": s["code"], "strength": s.get("strength"),
             "evidence": [_evidence(m, catalog.get(m)) for m in s.get("metric_ids", ())]}
            for s in watch.get("signals") or () if s.get("source") == source
        ]
        state = "signal" if signals else "missing" if source == "market" and market_missing else "normal"
        out.append({"source": source, "state": state, "signals": signals})
    return out


def _evidence(metric_id: str, value: Any) -> dict[str, Any]:
    metric = metric_id.split(":")[-1]
    label, unit = EVIDENCE_LABELS.get(metric, (None, None))
    return {"id": metric_id, "metric": metric, "label": label, "unit": unit, "value": value}


def signal_metrics(weeks: list[dict[str, Any]]) -> dict[int, set[str]]:
    """주차 → 그 주 일지 신호가 인용한 지표 이름(근거 ID의 마지막 조각). 신호·일지가 없는 주는 빈 집합."""
    return {w["week"]: {m.split(":")[-1] for s in (w.get("watch") or {}).get("signals") or ()
                        for m in s.get("metric_ids", ())}
            for w in weeks}


def stage_label(stage: str | None) -> str:
    text = STAGE_LABELS.get(stage or "", stage or "-")
    # "관찰"은 조치 라벨(Monitor)과 영어가 달라 문맥을 둔다.
    return i18n.pgettext("stage", text) if stage == "watch" else _(text)


def _basis_cards(loan: Mapping[str, Any], weeks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """승인 근거별 승인값·하한과 최근 값. 근거가 기록되지 않은 대출은 빈 목록."""
    cards = []
    for item in loan.get("approval_basis") or ():
        code = item["code"]
        entries = [(w["week"], e) for w in weeks for e in w.get("basis") or () if e.get("code") == code]
        last = entries[-1][1] if entries else None
        cards.append({
            "code": code,
            "metric": item.get("metric"),
            "approved": item.get("approved"),
            "floor": item.get("floor"),
            "latest": last.get("value") if last else None,
            "latest_week": entries[-1][0] if entries else None,
            "status": last.get("status") if last else None,
            "breach_week": next((n for n, e in entries if e.get("status") == "breach"), None),
        })
    return cards


def installment_label(item: Mapping[str, Any]) -> str:
    """회차 표시: 분할 회차는 "2-1회차"처럼."""
    part = item.get("part")
    return _("%(no)s-%(part)s회차", no=item["no"], part=part) if part else _("%(no)s회차", no=item["no"])


def _key_label(key: str) -> str:
    m = _ITEM_KEY.fullmatch(key)
    if not m:
        return key
    return installment_label({"no": m.group(1), "part": m.group(2)})


def _auto_contacts(
    mon: Mapping[str, Any], alert_id: str | None, contacts: Mapping[str, Any] | None
) -> list[dict[str, Any]]:
    """이 경보로 나간 단계 자동 안내(조기경보 1·2단계). 문구는 연락 기록 문서에서 ref로 찾는다."""
    escalation = mon.get("escalation") or {}
    by_ref = {c.get("ref"): c for c in (contacts or {}).get("contacts") or () if c.get("by") == "early_warning"}
    out = []
    for stage in (1, 2):
        rec = escalation.get(f"stage{stage}")
        if not rec or not alert_id or rec.get("alert_id") != alert_id:
            continue
        sent = by_ref.get(f"{mon.get('replay_id')}-s{stage}") if rec.get("status") != "skipped" else None
        out.append(
            {
                "stage": stage,
                "on": rec.get("on"),
                "status": rec.get("status"),
                "reason": rec.get("reason"),
                "script_id": (sent or {}).get("script_id") or ("balance_warning" if stage == 1 else "self_plan_offer"),
                "text": (sent or {}).get("text"),
            }
        )
    return out


def _plan_view(led: Mapping[str, Any]) -> dict[str, Any]:
    """플랜 제안·변경 요청·변경 이력(2026-11-11). 옛 문서는 셋 다 없다."""
    usd = "installment_cents" in led
    amount = "amount_cents" if usd else "amount_krw"
    installment = led.get("installment_cents" if usd else "installment_krw") or 0
    by_key = {item_key(s): s for s in led.get("schedule") or ()}
    change = led.get("plan_change")
    if change:
        before = [
            {"label": _key_label(k), "due_on": s.get("due_on") if s else None,
             amount: item_amount(s, installment) if s else None}
            for k in change.get("replaces") or ()
            for s in (by_key.get(k),)
        ]
        after = [{"label": installment_label(i), "due_on": i["due_on"], amount: i[amount]}
                 for i in change.get("items") or ()]
        change = {**change, "before": before, "after": after}
    history = [
        {
            **h,
            "before": [{**b, "label": _key_label(b["key"])} for b in h.get("before") or ()],
            "after": [{**a, "label": _key_label(a["key"])} for a in h.get("after") or ()],
        }
        for h in led.get("plan_history") or ()
    ]
    return {
        "offer": led.get("offer"),
        "plan_change": change,
        "plan_decidable": _pending(change),
        "plan_history": history,
    }


def _pending(request: Mapping[str, Any] | None) -> bool:
    """재조정 요청·추심 제안·플랜 변경 요청이 심사역 결정을 기다리는지."""
    return bool(request) and request.get("status") == "pending_officer"


def borrower_plan(loan: Mapping[str, Any], contacts: Mapping[str, Any] | None, today: str) -> dict[str, Any]:
    """차주 화면의 대출별 셀프 플랜 제안·플랜 변경 결과·받은 안내(2026-11-11)."""
    led = loan.get("repayment") or {}
    offer, change = led.get("offer"), led.get("plan_change")
    notices = sorted(
        (
            {"loan_id": loan["id"], "on": c.get("on") or "", "script_id": c.get("script_id"), "text": c.get("text"),
             "status": c.get("status"), "reason": c.get("reason")}
            for c in (contacts or {}).get("contacts") or ()
            if c.get("by") in BORROWER_NOTICE_BY and c.get("status") == "sent" and c.get("on") and c["on"] <= today
        ),
        key=lambda n: n["on"],
    )
    return {"loan_id": loan["id"], "offer": offer, "plan_change": change, "notices": notices,
            "status": _borrower_plan_status(offer, change)}


def _borrower_plan_status(offer: Mapping[str, Any] | None, change: Mapping[str, Any] | None) -> str | None:
    if not offer:
        return None
    status = offer.get("status")
    if status != "chosen":
        return status
    if change and change.get("status") in ("approved", "declined"):
        return change["status"]
    return "reviewing"
