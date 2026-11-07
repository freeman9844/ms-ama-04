"""대출 사후 모니터링 처리(스펙 §3.3). 큐·Service Bus 트리거가 부르며 외부 I/O는 Deps 포트로만 한다.

흐름: monitoring-replay 큐 → handle_replay(모니터링 동의·흐름 수집, 상환 원장, 1주차 메시지 발행)
      → flow.updated(세션=loan_id) → process_week(탐지기 → 새 트리거면 EW 에이전트 → 경보 저장·alert.raised,
        상환 원장 → 안내 발송·납부/미납 판정 → 미납이면 추심 건 저장·repayment.updated)
      → repayment.updated(세션=loan_id) → run_collections(공정 추심 규칙 안에서 연락·재조정 요청)
      → restructure-decisions 큐 → handle_restructure_decision(심사역 결정 기록).
EW 경보는 심사역 검토용이고 차주 연락은 미납(추심 건)에서만 한다. alert.raised는 발행만 하고 구독자가 없다.
주차가 끝나면(미납이면 추심까지) _after_week가 다음 주차로 갈지 정한다. 추심이 재조정 요청을 만들면
재생은 paused로 멈추고, 심사역 결정 뒤 awaiting_resume에서 resume·stop 요청을 기다린다.
Service Bus가 꺼져 있으면(deps.events is None) handle_replay가 같은 처리를 순서대로 직접 한다.
"""

import logging
from datetime import date
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from agents.collections import CollectionsContext, RepaymentCase, act, remind
from agents.data_assembly import build_pseudonymizer
from agents.early_warning import EwContext, investigate
from agents.features import monthly_installment
from agents.monitoring import WEEKS, Baseline, categorize, detect, projection, week_window, weekly_series
from agents.repayment import build_schedule, reminder_on, week_events
from journey.activities import (
    _TRANSACTIONS,
    ENDED_CONSENT,
    Deps,
    _remember_foundry,
    _semantic,
    raw_container,
    schedule_lifecycle,
)
from journey.models import (
    CONSENT_TTL_DAYS,
    MONITORING_PURPOSE,
    MONITORING_SCOPES,
    AlertRaised,
    FlowUpdated,
    REPLAY_HELD,
    RepaymentUpdated,
    ReplayRequest,
    RestructureDecision,
    replay_held,
    replay_in_progress,
)
from shared.contracts import ApplicantProfile, Transaction

log = logging.getLogger("journey.monitoring")
LOANS, ALERTS = "loans", "alerts"
TOPIC_FLOW, TOPIC_ALERT, TOPIC_REPAYMENT = "flow.updated", "alert.raised", "repayment.updated"


class OutOfOrder(RuntimeError):
    """앞 주차가 처리되지 않았다. 메시지를 되돌려 재시도하게 한다."""


def _mask_and_flow(deps: Deps, loan: dict[str, Any]) -> tuple[ApplicantProfile, list[Transaction], Any]:
    mon = loan["monitoring"]
    profile = ApplicantProfile.model_validate(
        deps.raw.get_json(raw_container(loan["underwriting_consent_id"]), "profile.json")
    )
    flow = deps.raw.get_json(raw_container(mon["consent_id"]), "flow.json")
    if flow is None:
        raise RuntimeError(f"사후 흐름 없음: {mon['consent_id']}")
    txns = _TRANSACTIONS.validate_python(flow)
    return profile, txns, build_pseudonymizer(deps.pseudonym_key, profile, txns).mask


def start_replay(deps: Deps, loan_id: str) -> dict[str, Any] | None:
    loan = deps.store.read(LOANS, loan_id, loan_id)
    if loan is None:
        log.warning("모니터링 대상 대출 없음: %s", loan_id)
        return None
    if replay_in_progress(loan.get("monitoring"), deps.clock()):
        log.warning("재생 진행 중이라 새 재생 요청 무시: %s", loan_id)
        return None
    if replay_held(loan.get("monitoring")):
        log.warning("재조정으로 멈춘 재생이 있어 새 재생 요청 무시: %s", loan_id)
        return None
    ended = _underwriting_consent_ended(deps, loan)
    if ended:
        log.warning("심사 동의가 끝나 재생할 수 없음: %s (%s)", loan_id, ended)
        return None
    # 동의·흐름 조회보다 먼저 대출 문서를 etag로 선점한다. 더블 클릭으로 큐 메시지가 두 번 오면 둘 다 위 확인을 통과하므로
    # (아직 아무도 문서에 쓰지 않았다) 선점에 진 쪽은 동의를 만들지 않고 물러난다.
    now, replay_id = deps.clock().isoformat(), f"rp-{uuid4().hex[:10]}"
    loan["monitoring"] = {
        "replay_id": replay_id,
        "consent_id": None,
        "flow_start": None,
        "started_at": now,
        "mode": "servicebus" if deps.events is not None else "inline",
        "status": "replaying",
        "processed_week": 0,
        "seen_triggers": [],
        "overrides": {},
        "weeks": [],
        "alert_ids": [],
        "error": None,
        "paused_week": None,
        "pause_case_id": None,
        "case_ids": [],
        "resumed_at": [],
        "stopped_at": None,
    }
    loan["updated_at"] = now
    if not deps.store.replace(LOANS, loan, loan.get("_etag")):
        log.warning("동시 재생 요청 무시: %s", loan_id)
        return None
    try:
        scopes = list(MONITORING_SCOPES)
        grant = deps.mock.create_consent(loan["applicant_id"], scopes, MONITORING_PURPOSE, CONSENT_TTL_DAYS)
        consent_id = grant["consent_id"]
        schedule_lifecycle(
            deps,
            {
                "id": consent_id,
                "consent_id": consent_id,
                "application_id": loan_id,
                "applicant_id": loan["applicant_id"],
                "scopes": scopes,
                "purpose": MONITORING_PURPOSE,
                "granted_at": now,
                "expires_at": grant["expires_at"],
                "status": "granted",
                "raw_container": raw_container(consent_id),
                "foundry_conversations": [],
            },
        )
        flow = deps.mock.fetch("flow", grant["token"])
        txns = _TRANSACTIONS.validate_python(flow)
        if not txns:
            raise RuntimeError(f"사후 흐름이 비어 있다: {loan_id}")
        deps.raw.put_json(raw_container(consent_id), "flow.json", flow)
        flow_start = min(t.booked_on for t in txns)
        loan["monitoring"].update(consent_id=consent_id, flow_start=flow_start.isoformat())
        # 재생마다 원장을 새로 만든다(이전 재생의 납부·미납 판정을 지운다).
        loan["repayment"] = build_schedule(
            flow_start, WEEKS * 7, round(monthly_installment(loan["amount_krw"], loan["tenor_months"]))
        )
        deps.store.upsert(LOANS, loan)
    except Exception as e:
        # 선점한 문서가 replaying으로 남으면 30분(REPLAY_GUARD) 동안 콘솔·큐가 막힌다. 실패를 기록하고 원래 예외를 다시 던진다
        # (이 단계는 LLM을 쓰지 않아 큐 재시도가 싸고, 일시 오류는 재시도로 복구된다).
        log.exception("재생 시작 실패: %s %s", loan_id, replay_id)
        loan["monitoring"].update(status="failed", error=f"{type(e).__name__}: {e}"[:300])
        loan["updated_at"] = deps.clock().isoformat()
        deps.store.upsert(LOANS, loan)
        raise
    return {"loan_id": loan_id, "replay_id": replay_id, "mode": loan["monitoring"]["mode"]}

def handle_replay(deps: Deps, body: str) -> dict[str, Any] | None:
    try:
        req = ReplayRequest.model_validate_json(body)
    except ValidationError as e:
        log.warning("잘못된 재생 요청 폐기: %s", e.errors(include_input=False)[:3])
        return None
    if req.action == "stop":
        return stop_replay(deps, req.loan_id, req.replay_id)  # type: ignore[arg-type]
    if req.action == "resume":
        resumed = resume_replay(deps, req.loan_id, req.replay_id)  # type: ignore[arg-type]
        if resumed is not None:
            _run_weeks(deps, req.loan_id, resumed["replay_id"], resumed["from_week"])
        return resumed
    started = start_replay(deps, req.loan_id)
    if started is not None:
        _run_weeks(deps, req.loan_id, started["replay_id"], 1)
    return started


def _held_loan(deps: Deps, loan_id: str, replay_id: str, status: str) -> dict[str, Any] | None:
    loan = deps.store.read(LOANS, loan_id, loan_id)
    mon = (loan or {}).get("monitoring") or {}
    if mon.get("replay_id") != replay_id or mon.get("status") != status:
        log.warning("%s 상태의 재생이 아니라 요청 무시: %s %s (%s)", status, loan_id, replay_id, mon.get("status"))
        return None
    return loan


def _consent_ended(deps: Deps, cid: str | None) -> str | None:
    consent = deps.store.read("consents", cid, cid) if cid else None
    status = (consent or {}).get("status")
    return status if status in ENDED_CONSENT else None


def _underwriting_consent_ended(deps: Deps, loan: dict[str, Any]) -> str | None:
    """사후 탐지는 심사 동의로 받은 프로필(가명화 키)이 필요하다. 동의가 끝나면 원천 데이터가 없다."""
    return _consent_ended(deps, loan["underwriting_consent_id"])


def _resume_blocker(deps: Deps, loan: dict[str, Any]) -> str | None:
    """재개하면 남은 주차가 읽을 원천(심사 프로필·사후 flow.json)이 동의 종료로 지워졌는지."""
    if ended := _underwriting_consent_ended(deps, loan):
        return f"심사 동의가 끝나({ended}) 재생을 이어갈 수 없습니다."
    if ended := _consent_ended(deps, loan["monitoring"].get("consent_id")):
        return f"사후 동의가 끝나({ended}) 재생을 이어갈 수 없습니다."
    return None


def resume_replay(deps: Deps, loan_id: str, replay_id: str) -> dict[str, Any] | None:
    """재조정 결정 뒤 멈춘 재생을 paused_week 다음 주차부터 잇는다. 30분 가드도 다시 시작한다."""
    loan = _held_loan(deps, loan_id, replay_id, "awaiting_resume")
    if loan is None:
        return None
    mon, now = loan["monitoring"], deps.clock().isoformat()
    blocker = _resume_blocker(deps, loan)
    if blocker:
        # 재개를 받아 주면 주차 처리가 계속 실패해 '재생 중'에 멈춘다. 이유를 남기고 재생을 끝낸다.
        log.warning("재생을 이어갈 수 없음: %s (%s)", loan_id, blocker)
        mon.update(status="stopped", stopped_at=now, error=blocker)
        loan["updated_at"] = now
        if not deps.store.replace(LOANS, loan, loan.get("_etag")):
            log.warning("동시 요청으로 재생 종료 기록 실패: %s", loan_id)
        return None
    mon.update(status="replaying", started_at=now, resumed_at=[*mon.get("resumed_at", []), now])
    loan["updated_at"] = now
    if not deps.store.replace(LOANS, loan, loan.get("_etag")):
        log.warning("동시 재개 요청 무시: %s", loan_id)
        return None
    return {"loan_id": loan_id, "replay_id": replay_id, "mode": mon["mode"], "from_week": mon["paused_week"] + 1}


def stop_replay(deps: Deps, loan_id: str, replay_id: str) -> dict[str, Any] | None:
    loan = _held_loan(deps, loan_id, replay_id, "awaiting_resume")
    if loan is None:
        return None
    now = deps.clock().isoformat()
    loan["monitoring"].update(status="stopped", stopped_at=now)
    loan["updated_at"] = now
    if not deps.store.replace(LOANS, loan, loan.get("_etag")):
        log.warning("동시 종료 요청 무시: %s", loan_id)
        return None
    return {"loan_id": loan_id, "replay_id": replay_id, "status": "stopped"}


def _publish_week(deps: Deps, loan_id: str, replay_id: str, week: int) -> None:
    message = FlowUpdated(type=TOPIC_FLOW, loan_id=loan_id, replay_id=replay_id, week=week)
    deps.events.publish(  # type: ignore[union-attr]
        TOPIC_FLOW, message.model_dump(mode="json"), f"{TOPIC_FLOW}:{loan_id}:{replay_id}:{week}", session_id=loan_id
    )


def _run_weeks(deps: Deps, loan_id: str, replay_id: str, first_week: int) -> None:
    """Service Bus면 첫 주차만 발행하고 이후는 주차·추심 핸들러가 _after_week로 잇는다."""
    if deps.events is not None:
        _publish_week(deps, loan_id, replay_id, first_week)
        return
    # Service Bus가 꺼진 동안: 같은 판단(_after_week)으로 주차 순서대로 직접 한다(데모 비용 절감 모드).
    try:
        for week in range(first_week, WEEKS + 1):
            entry = process_week(deps, loan_id, replay_id, week)
            case_id = (entry or {}).get("case_id")
            if case_id:
                run_collections(deps, loan_id, case_id)
            if _after_week(deps, loan_id, replay_id, week, case_id) != "next":
                break
    except Exception as e:  # noqa: BLE001 - 큐 재시도는 13주를 처음부터 다시 돌린다(LLM 비용). 실패를 기록하고 콘솔에서 다시 시작한다
        log.exception("인라인 재생 실패: %s %s", loan_id, replay_id)
        loan = deps.store.read(LOANS, loan_id, loan_id)
        if loan and (loan.get("monitoring") or {}).get("replay_id") == replay_id:
            loan["monitoring"].update(status="failed", error=f"{type(e).__name__}: {e}"[:300])
            loan["updated_at"] = deps.clock().isoformat()
            deps.store.upsert(LOANS, loan)


WRITE_ATTEMPTS = 3  # 동시 수정(etag 불일치) 때 다시 읽고 판단하는 횟수


def _after_week(deps: Deps, loan_id: str, replay_id: str, week: int, case_id: str | None) -> str:
    """주차 처리(미납이면 추심까지) 뒤 "next"|"paused"|"done"을 정한다. 재조정 요청이 생기면 멈춘다.

    지난 주차·지난 재생의 중복 메시지는 processed_week가 이미 앞서 있어 "done"이 된다(다음 주차를 두 번 내지 않는다).
    """
    for _ in range(WRITE_ATTEMPTS):
        loan = deps.store.read(LOANS, loan_id, loan_id)
        mon = (loan or {}).get("monitoring")
        if not mon or mon["replay_id"] != replay_id or mon["processed_week"] != week:
            return "done"
        if mon["status"] in REPLAY_HELD:
            return "paused"
        if mon["status"] != "replaying":
            return "done"
        case = deps.store.read(ALERTS, case_id, loan_id) if case_id else None
        if ((case or {}).get("restructure") or {}).get("status") != "pending_officer":
            return "next"
        mon.update(status="paused", paused_week=week, pause_case_id=case_id)
        loan["updated_at"] = deps.clock().isoformat()  # type: ignore[index]
        if deps.store.replace(LOANS, loan, loan.get("_etag")):  # type: ignore[union-attr]
            # 요청을 읽은 뒤 멈춤을 쓰기 전에 결정이 끝났으면 결정 쪽은 멈춤을 못 봤다. 여기서 재개 대기로 올린다.
            _promote_if_decided(deps, loan_id, case_id)  # type: ignore[arg-type]
            return "paused"
    log.warning("동시 수정이 이어져 멈춤을 기록하지 못함: %s 주차 %s", loan_id, week)
    return "done"


def _promote_if_decided(deps: Deps, loan_id: str, case_id: str) -> None:
    """이 요청 때문에 멈춘 재생이고 심사역 결정이 끝났으면 재개 대기로 올린다.

    결정 처리와 주차 처리는 각자 자기 쓰기를 마친 뒤 이 함수를 부른다. 어느 순서로 겹쳐도 나중 쪽이 앞쪽의 쓰기를 본다.
    """
    for _ in range(WRITE_ATTEMPTS):
        loan = deps.store.read(LOANS, loan_id, loan_id)
        mon = (loan or {}).get("monitoring") or {}
        # pause_alert_id: 미납 추심 배포 전에 멈춘 대출 문서의 키
        if mon.get("status") != "paused" or (mon.get("pause_case_id") or mon.get("pause_alert_id")) != case_id:
            return
        case = deps.store.read(ALERTS, case_id, loan_id)
        if ((case or {}).get("restructure") or {}).get("status") not in ("approved", "declined"):
            return
        mon["status"] = "awaiting_resume"
        loan["updated_at"] = deps.clock().isoformat()  # type: ignore[index]
        if deps.store.replace(LOANS, loan, loan.get("_etag")):  # type: ignore[union-attr]
            return
    log.warning("동시 수정이 이어져 재개 대기로 올리지 못함: %s", loan_id)


def _continue(deps: Deps, loan_id: str, replay_id: str, week: int, case_id: str | None) -> None:
    if _after_week(deps, loan_id, replay_id, week, case_id) == "next":
        _publish_week(deps, loan_id, replay_id, week + 1)


def process_week(deps: Deps, loan_id: str, replay_id: str, week: int) -> dict[str, Any] | None:
    loan = deps.store.read(LOANS, loan_id, loan_id)
    mon = (loan or {}).get("monitoring")
    if not mon or mon["replay_id"] != replay_id:
        log.info("지난 재생의 주차 메시지 무시: %s %s", loan_id, replay_id)
        return None
    if mon["status"] != "replaying":
        log.info("재생 중이 아니라 주차 메시지 무시: %s %s (%s)", loan_id, replay_id, mon["status"])
        return None
    if week <= mon["processed_week"]:
        return None
    if week != mon["processed_week"] + 1:
        raise OutOfOrder(f"{loan_id} {week}주차 앞 주차 미처리({mon['processed_week']})")

    profile, txns, mask = _mask_and_flow(deps, loan)
    start = date.fromisoformat(mon["flow_start"])
    lo, hi = week_window(start, week)
    visible = [t for t in txns if t.booked_on <= hi]
    classification, overrides, _ = _semantic(
        deps, profile, [t for t in visible if t.booked_on >= lo], mon["consent_id"]
    )
    mon["overrides"].update({k: v.value for k, v in overrides.items()})
    baseline = Baseline.from_dict(loan["baseline"])
    cats = categorize(visible, mon["overrides"])
    rows = weekly_series(visible, cats, start, week, baseline)
    triggers = detect(rows, baseline)
    new = [t for t in triggers if t not in mon["seen_triggers"]]
    entry: dict[str, Any] = {
        "week": week,
        "row": rows[-1],
        "triggers": triggers,
        "new_triggers": new,
        "classification": (classification or {}).get("metrics") or (classification or {}).get("error"),
        "projection": projection(rows, visible, cats, baseline, installment_due=_due_list(loan)),
        "investigation": None,
        "alert_id": None,
        "repayment": None,
        "case_id": None,
    }
    if new:
        alert_id = f"ew-{replay_id}-w{week:02d}"
        ctx = EwContext(
            alert_id=alert_id,
            loan_id=loan_id,
            week=week,
            start=start,
            industry=loan["industry"],
            triggers=triggers,
            rows=rows,
            baseline=baseline,
            transactions=visible,
            categories=cats,
            mask=mask,
            terms={"amount_krw": loan["amount_krw"], "tenor_months": loan["tenor_months"]},
        )
        result = investigate(ctx, runner=deps.agents, model=deps.ew_model)
        _remember_foundry(deps, mon["consent_id"], result.conversations, [])
        mon["seen_triggers"].extend(new)
        entry["investigation"] = {"decision": result.decision, "reason": result.reason, "record": result.record}
        if result.alert is not None:
            deps.store.upsert(
                ALERTS,
                {
                    "id": alert_id,
                    **result.alert.model_dump(mode="json"),
                    "replay_id": replay_id,
                    "status": "open",
                    "created_at": deps.clock().isoformat(),
                    "collections": None,
                    "restructure": None,
                    "event_error": None,
                },
            )
            entry["alert_id"] = alert_id
            mon["alert_ids"].append(alert_id)
    _repayment_week(deps, loan_id, replay_id, loan, visible, lo, hi, week, entry)
    mon["processed_week"] = week
    mon["weeks"].append(entry)
    if week == WEEKS:
        mon["status"] = "completed"
    loan["updated_at"] = deps.clock().isoformat()
    deps.store.upsert(LOANS, loan)
    if deps.events is not None:
        if entry["alert_id"]:
            _publish_alert(deps, loan_id, entry["alert_id"])
        if entry["case_id"]:
            _publish_case(deps, loan_id, entry["case_id"])
    return entry


def _due_list(loan: dict[str, Any]) -> list[date] | None:
    led = loan.get("repayment")
    return [date.fromisoformat(s["due_on"]) for s in led["schedule"]] if led else None


def _repayment_week(
    deps: Deps,
    loan_id: str,
    replay_id: str,
    loan: dict[str, Any],
    visible: list[Transaction],
    lo: date,
    hi: date,
    week: int,
    entry: dict[str, Any],
) -> None:
    """이번 주 상환 안내 발송, 상환일 납부·미납 판정. 미납이면 추심 건을 연다."""
    led = loan.get("repayment")
    if not led:
        return
    ev = week_events(led["schedule"], visible, lo, hi)
    by_no = {s["no"]: s for s in led["schedule"]}
    if ev.reminders:
        cid = f"contacts-{replay_id}"
        contacts = deps.store.read(ALERTS, cid, loan_id) or {
            "id": cid, "loan_id": loan_id, "replay_id": replay_id, "contacts": []
        }
        for no in ev.reminders:
            item, ref = by_no[no], f"{replay_id}-i{no}"
            sent = next((c for c in contacts["contacts"] if c.get("ref") == ref and c.get("by") == "reminder"), None)
            if sent is None:
                sent = remind(ref, contacts["contacts"], reminder_on(date.fromisoformat(item["due_on"])), deps.clock())
                contacts["contacts"].append(sent)
            item.update(reminded_on=sent["on"], reminder_status=sent["status"])
        deps.store.upsert(ALERTS, contacts)  # 대출 문서보다 먼저: 재시도 때 같은 ref면 다시 보내지 않는다
    for no, txn_id in ev.paid:
        by_no[no].update(status="paid", txn_id=txn_id)
    mon = loan["monitoring"]
    for no in ev.missed:  # 30일 주기라 한 주에 최대 하나
        item, case_id = by_no[no], f"rc-{replay_id}-i{no}"
        item.update(status="missed", case_id=case_id)
        recent = None
        if mon["alert_ids"]:
            a = deps.store.read(ALERTS, mon["alert_ids"][-1], loan_id)
            if a:
                recent = {
                    "alert_id": a.get("alert_id") or a.get("id"),
                    "week": a.get("week"),
                    "severity": a.get("severity"),
                    "summary": a.get("summary"),
                }
        deps.store.upsert(
            ALERTS,
            {
                "id": case_id,
                "kind": "repayment_case",
                "case_id": case_id,
                "loan_id": loan_id,
                "replay_id": replay_id,
                "week": week,
                "as_of": item["due_on"],
                "installment": {"no": no, "due_on": item["due_on"], "amount_krw": led["installment_krw"]},
                "missed_count": sum(s["status"] == "missed" for s in led["schedule"]),
                "recent_alert": recent,
                "status": "open",
                "created_at": deps.clock().isoformat(),
                "collections": None,
                "restructure": None,
                "event_error": None,
            },
        )
        mon.setdefault("case_ids", []).append(case_id)
        entry["case_id"] = case_id
    entry["repayment"] = {"reminders": list(ev.reminders), "paid": [n for n, _ in ev.paid], "missed": list(ev.missed)}


def _publish_alert(deps: Deps, loan_id: str, alert_id: str) -> None:
    message = AlertRaised(type=TOPIC_ALERT, loan_id=loan_id, alert_id=alert_id).model_dump(mode="json")
    try:
        deps.events.publish(TOPIC_ALERT, message, f"{TOPIC_ALERT}:{alert_id}", session_id=loan_id)  # type: ignore[union-attr]
    except Exception as e:  # noqa: BLE001 - 주차 처리는 끝났다. 경보는 콘솔에 남고 발행 실패를 기록한다
        _mark_publish_failed(deps, loan_id, alert_id, e)


def _publish_case(deps: Deps, loan_id: str, case_id: str) -> None:
    message = RepaymentUpdated(type=TOPIC_REPAYMENT, loan_id=loan_id, case_id=case_id).model_dump(mode="json")
    try:
        deps.events.publish(TOPIC_REPAYMENT, message, f"{TOPIC_REPAYMENT}:{case_id}", session_id=loan_id)  # type: ignore[union-attr]
    except Exception as e:  # noqa: BLE001 - 추심 건은 남고 발행 실패를 기록한다. 주차 핸들러가 직접 잇는다
        _mark_publish_failed(deps, loan_id, case_id, e)


def _mark_publish_failed(deps: Deps, loan_id: str, doc_id: str, e: Exception) -> None:
    doc = deps.store.read(ALERTS, doc_id, loan_id)
    if doc is not None:
        doc["event_error"] = f"{type(e).__name__}: {e}"[:300]
        deps.store.upsert(ALERTS, doc)


def _publish_failed(deps: Deps, loan_id: str, doc_id: str) -> bool:
    return bool((deps.store.read(ALERTS, doc_id, loan_id) or {}).get("event_error"))


def handle_flow_update(deps: Deps, body: str) -> dict[str, Any] | None:
    try:
        msg = FlowUpdated.model_validate_json(body)
    except ValidationError:
        log.info("flow.updated 형식이 아닌 메시지 무시(스모크 등)")
        return None
    entry = process_week(deps, msg.loan_id, msg.replay_id, msg.week)
    # 재전달(이미 처리한 주차)이어도 다음 주차 발행이 빠졌을 수 있어 저장된 주차 기록으로 다시 판단한다.
    last = entry or _week_entry(deps, msg.loan_id, msg.replay_id, msg.week)
    if last is None:
        return entry
    case_id = last.get("case_id")
    if case_id and not _publish_failed(deps, msg.loan_id, case_id):
        return entry  # repayment.updated → run_collections 뒤 handle_repayment_updated가 잇는다
    if case_id:
        run_collections(deps, msg.loan_id, case_id)  # 발행 실패: 여기서 추심을 돌리고 잇는다
    _continue(deps, msg.loan_id, msg.replay_id, msg.week, case_id)
    return entry


def _week_entry(deps: Deps, loan_id: str, replay_id: str, week: int) -> dict[str, Any] | None:
    mon = ((deps.store.read(LOANS, loan_id, loan_id) or {}).get("monitoring")) or {}
    if mon.get("replay_id") != replay_id or mon.get("processed_week") != week or not mon.get("weeks"):
        return None
    return mon["weeks"][-1]


def run_collections(deps: Deps, loan_id: str, case_id: str) -> dict[str, Any] | None:
    doc = deps.store.read(ALERTS, case_id, loan_id)
    loan = deps.store.read(LOANS, loan_id, loan_id)
    if doc is None or loan is None or doc.get("kind") != "repayment_case" or doc.get("collections") is not None:
        return None
    contacts_id = f"contacts-{doc['replay_id']}"
    contacts = deps.store.read(ALERTS, contacts_id, loan_id) or {
        "id": contacts_id,
        "loan_id": loan_id,
        "replay_id": doc["replay_id"],
        "contacts": [],
    }
    _, _, mask = _mask_and_flow(deps, loan)
    ctx = CollectionsContext(case=RepaymentCase.from_doc(doc), contacts=contacts["contacts"], now=deps.clock(), mask=mask)
    result = act(ctx, runner=deps.agents, model=deps.collections_model)
    _remember_foundry(deps, loan["monitoring"]["consent_id"], result.conversations, [])
    doc["collections"] = {
        "decision": result.decision,
        "reason": result.reason,
        "contacts": result.contacts,
        "record": result.record,
        "at": deps.clock().isoformat(),
    }
    doc["restructure"] = result.restructure
    if result.restructure is not None:
        doc["status"] = "restructure_pending"
    # 추심 건 문서를 먼저 쓴다: 재시도 시 collections가 있으면 다시 연락하지 않는다(중복 발송 방지).
    deps.store.upsert(ALERTS, doc)
    contacts["contacts"].extend(result.contacts)
    deps.store.upsert(ALERTS, contacts)
    return doc["collections"]


def handle_repayment_updated(deps: Deps, body: str) -> dict[str, Any] | None:
    try:
        msg = RepaymentUpdated.model_validate_json(body)
    except ValidationError:
        log.info("repayment.updated 형식이 아닌 메시지 무시")
        return None
    result = run_collections(deps, msg.loan_id, msg.case_id)
    doc = deps.store.read(ALERTS, msg.case_id, msg.loan_id)
    if doc is not None and doc.get("kind") == "repayment_case" and deps.events is not None:
        _continue(deps, msg.loan_id, doc["replay_id"], doc["week"], msg.case_id)
    return result


def handle_restructure_decision(deps: Deps, body: str) -> bool:
    try:
        msg = RestructureDecision.model_validate_json(body)
    except ValidationError as e:
        log.warning("잘못된 재조정 결정 폐기: %s", e.errors(include_input=False)[:3])
        return False
    doc = deps.store.read(ALERTS, msg.alert_id, msg.loan_id)
    request = (doc or {}).get("restructure")
    if not request or request.get("status") != "pending_officer":
        log.warning("대기 중인 재조정 요청 없음: %s", msg.alert_id)
        return False
    status = "approved" if msg.decision == "approve" else "declined"
    request.update(
        status=status, officer_id=msg.officer_id, reason=msg.reason, decided_at=deps.clock().isoformat()
    )
    doc["status"] = f"restructure_{status}"  # type: ignore[index]
    deps.store.upsert(ALERTS, doc)  # type: ignore[arg-type]
    _promote_if_decided(deps, msg.loan_id, msg.alert_id)
    return True
