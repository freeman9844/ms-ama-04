"""동의 수명주기: 만료 타이머, 갱신 안내, 삭제와 삭제 증명.

동의마다 consent_lifecycle 오케스트레이션(인스턴스 consent-{id})이 만료까지 기다린다.
철회·지금 만료·갱신 안내는 consent-commands 큐 명령이 외부 이벤트로 전달한다.
끝나면 원천 데이터(Blob), 목업 동의 레지스트리, Foundry 대화·컨테이너를 지우고
삭제 증명(deletion_proofs, id = consent_id)을 남긴다. 신청 문서(판단 기록)는 남긴다.
"""

import hashlib
import json
import logging
from collections.abc import Callable
from functools import partial
from typing import Any

from pydantic import ValidationError

from agents.notices import RETAINED, consent_facts, write_notice
from journey.activities import ENDED_CONSENT, TOPIC_CONSENT, Deps, raw_container, update_doc
from journey.entry import _status, should_start
from journey.models import (
    WITHDRAWN_EVENT,
    ConsentCommand,
    LifecycleStart,
    lifecycle_instance,
    next_wake,
    renewal_at,
)
from journey.orchestration import Steps, _json, _now
from shared.deletion import proof_is_current

log = logging.getLogger("journey.consent")
LIFECYCLE_ORCHESTRATOR = "consent_lifecycle"
PROOFS_CONTAINER = "deletion_proofs"
COMMANDS = ("revoke", "expire_now", "remind_now")
ACTIVE = ("Running", "Pending")
# 끝낸 동작 → (삭제 사유, 계기)
_END = {"revoke": ("revoked", "revoke"), "expire_now": ("expired", "expire_now"), "timer": ("expired", "timer")}
_EVENTS = {"expired": "consent.expired", "revoked": "consent.revoked"}


class IncompleteDeletion(RuntimeError):
    """일부 항목을 지우지 못했거나 삭제를 확인하지 못했다. 증명은 미완료로 남고 재시도한다."""


def _item(kind: str, target: str, outcome: str, verified: bool, detail: str | None = None) -> dict[str, Any]:
    return {"kind": kind, "target": target, "outcome": outcome, "verified": verified, "detail": detail}


def _error(e: Exception) -> str:
    return f"{type(e).__name__}: {e}"[:200]


def _delete_raw(deps: Deps, container: str, *, fence_writers: bool = False) -> dict[str, Any]:
    try:
        outcome = "deleted" if deps.raw.delete_container(container) else "absent"
        # 삭제 중인 컨테이너는 잠시 빈 채로 보이므로 없음(None)과 빈 목록 모두 삭제 확인이다.
        remaining = deps.raw.manifest(container)
        return _item("raw_container", container, outcome, remaining is None if fence_writers else not remaining)
    except Exception as e:  # noqa: BLE001 - 항목 실패는 증명에 남기고 재시도한다
        return _item("raw_container", container, "failed", False, _error(e))


def _revoke_registry(deps: Deps, consent_id: str) -> dict[str, Any]:
    try:
        outcome = deps.mock.revoke(consent_id)
        return _item("consent_registry", consent_id, outcome, outcome in ("revoked", "absent"))
    except Exception as e:  # noqa: BLE001
        return _item("consent_registry", consent_id, "failed", False, _error(e))


def start_raw_repair(deps: Deps, cid: str, container: str) -> None:
    def mark(doc: dict) -> None:
        doc["raw_repair_generation"] = doc.get("raw_repair_generation", 0) + 1
        doc.update(raw_repair_pending=True, deletion_complete=False)

    consent = update_doc(deps.store, "consents", cid, cid, mark)
    if consent:
        try:
            _repair_raw(deps, consent, container)
        except Exception as exc:  # The sweep owns the persisted repair; do not restart servicing.
            log.warning("Raw deletion checkpoint deferred: %s (%s)", cid, type(exc).__name__)


def _repair_raw(deps: Deps, consent: dict, container: str) -> bool:
    """The consent marker survives worker loss, including between consent and proof writes."""
    cid, generation = consent["id"], consent["raw_repair_generation"]

    def invalidate(proof: dict) -> bool | None:
        if proof.get("raw_repair_generation", 0) >= generation:
            return False
        proof.update(complete=False, raw_repair_generation=generation)
        raw = next(i for i in proof["items"] if i["kind"] == "raw_container")
        raw.update(verified=False, outcome="pending", detail="late_write")

    update_doc(deps.store, PROOFS_CONTAINER, cid, cid, invalidate)
    proof = deps.store.read(PROOFS_CONTAINER, cid, cid)
    if not proof or proof.get("raw_repair_generation") != generation:
        return False
    raw = next(i for i in proof["items"] if i["kind"] == "raw_container")
    needs_verification = not raw["verified"]
    if needs_verification:
        raw = _delete_raw(deps, container, fence_writers=bool(consent.get("raw_write_count")))
        proof["items"] = [raw if i["kind"] == "raw_container" else i for i in proof["items"]]
    complete = all(i["verified"] for i in proof["items"])
    if needs_verification or proof["complete"] != complete:
        proof["complete"] = complete
        proof["attempts"] = proof.get("attempts", 0) + 1
        proof["deleted_at"] = deps.clock().isoformat()
        if proof["complete"] and not proof.get("event_published"):
            _publish_ended(deps, proof)
        if not deps.store.replace(PROOFS_CONTAINER, proof, proof["_etag"]):
            return False

    def finish(doc: dict) -> bool | None:
        if doc.get("raw_repair_generation") != generation or not doc.get("raw_repair_pending"):
            return False
        outcome = raw["outcome"] if raw["verified"] else "failed"
        history = doc.setdefault("late_cleanup", [])
        last = history[-1] if history else {}
        if (last.get("generation"), last.get("outcome")) != (generation, outcome):
            history.append({"kind": "raw_container", "target": container, "outcome": outcome,
                            "generation": generation, "at": deps.clock().isoformat()})
        if raw["verified"]:
            doc.update(raw_repair_pending=False, deletion_complete=proof["complete"])
            if proof["complete"] or not doc.get("deletion_request"):
                _finish_writes(doc)

    update_doc(deps.store, "consents", cid, cid, finish)
    return raw["verified"]


def _finish_writes(doc: dict) -> None:
    if doc.get("deletion_request") and not any(n["kind"] == "deletion" for n in doc.get("notices", [])):
        doc["deletion_notice_pending"] = True
    doc["raw_write_count"] = 0


def sweep_raw_repairs(deps: Deps) -> None:
    for cid in deps.store.pending_raw_repairs():
        ref = hashlib.sha256(cid.encode()).hexdigest()[:12]
        try:
            consent = deps.store.read("consents", cid, cid)
            if not consent or consent.get("status") not in ENDED_CONSENT:
                continue
            if consent.get("raw_write_count") and consent.get("deletion_request"):
                delete_consent_data(deps, consent["deletion_request"])
                send_consent_notice(deps, {"consent_id": cid, "kind": "deletion"})
            elif consent.get("raw_repair_pending"):
                if not _repair_raw(deps, consent, consent.get("raw_container") or raw_container(cid)):
                    log.warning("raw_repair_deferred ref=%s reason=unverified", ref)
            elif consent.get("deletion_notice_pending"):
                send_consent_notice(deps, {"consent_id": cid, "kind": "deletion"})
        except Exception:  # Per-item retry: never log raw SDK errors or block the rest of the scan.
            log.warning("raw_repair_deferred ref=%s reason=checkpoint", ref)


def _delete_conversation(deps: Deps, conversation_id: str) -> dict[str, Any]:
    if deps.agents is None:
        return _item("conversation", conversation_id, "failed", False, "agents_disabled")
    runner: Any = deps.agents
    try:
        outcome = str(runner.delete_conversation(conversation_id))
    except Exception:  # noqa: BLE001
        outcome = "failed"
    try:
        exists = runner.conversation_exists(conversation_id)
    except Exception:  # noqa: BLE001
        exists = None
    detail = "verify_failed" if exists is None else None
    return _item("conversation", conversation_id, outcome, outcome in ("deleted", "absent") and exists is False, detail)


def _delete_container(deps: Deps, container: dict[str, Any]) -> dict[str, Any]:
    if container.get("deleted"):
        return _item("container", container["id"], "deleted", True, "deleted_after_run")
    if deps.agents is None:
        return _item("container", container["id"], "failed", False, "agents_disabled")
    try:
        deleted = bool(deps.agents.delete_container(container["id"]))
    except Exception:  # noqa: BLE001
        deleted = False
    return _item("container", container["id"], "deleted" if deleted else "failed", deleted)


def _proof_summary(proof: dict[str, Any]) -> dict[str, Any]:
    return {"proof_id": proof["id"], "complete": proof["complete"], "reason": proof["reason"]}


def _publish_ended(deps: Deps, proof: dict[str, Any]) -> None:
    if deps.events is None:
        proof["event_error"] = "servicebus_disabled"
        return
    kind = _EVENTS[proof["reason"]]
    message = {
        "type": kind,
        "consent_id": proof["consent_id"],
        "application_id": proof["application_id"],
        "proof_id": proof["id"],
        "deleted_at": proof["deleted_at"],
    }
    try:
        deps.events.publish(TOPIC_CONSENT, message, f"{kind}:{proof['consent_id']}")
        proof["event_published"], proof["event_error"] = True, None
    except Exception as e:  # noqa: BLE001 - 알림 실패가 삭제를 무효로 하지 않는다
        proof["event_error"] = _error(e)


def delete_consent_data(deps: Deps, payload: dict[str, Any]) -> dict[str, Any]:
    """동의로 모은 데이터를 지우고 삭제 증명을 남긴다. 완료된 증명이 있으면 그대로 돌려준다."""
    consent_id = payload["consent_id"]
    existing = deps.store.read(PROOFS_CONTAINER, consent_id, consent_id)
    current = deps.store.read("consents", consent_id, consent_id)
    if existing and current and current.get("raw_repair_pending"):
        if not _repair_raw(deps, current, current.get("raw_container") or raw_container(consent_id)):
            raise IncompleteDeletion(f"Raw repair pending: {consent_id}")
        existing = deps.store.read(PROOFS_CONTAINER, consent_id, consent_id)
        current = deps.store.read("consents", consent_id, consent_id)
    if proof_is_current(current, existing):
        return _proof_summary(existing)
    now = deps.clock().isoformat()

    def mark(consent: dict[str, Any]) -> None:
        # 먼저 끝난 상태로 표시해야 늦게 끝난 에이전트 실행이 남긴 대화도 바로 지워진다.
        if consent.get("status") not in ENDED_CONSENT:
            consent["status"], consent["ended_at"] = payload["reason"], now
        consent["deletion_proof_id"] = consent_id
        if consent.get("raw_write_count"):
            consent.setdefault("deletion_request", {k: payload[k] for k in ("consent_id", "reason", "trigger", "requested_by")})

    consent = update_doc(deps.store, "consents", consent_id, consent_id, mark)
    if consent is None:
        raise LookupError(f"동의 문서 없음: {consent_id}")
    container = consent.get("raw_container") or raw_container(consent_id)
    steps: list[tuple[str, str, Callable[[], dict[str, Any]]]] = [
        ("consent_registry", consent_id, lambda: _revoke_registry(deps, consent_id))
    ]
    steps += [("conversation", c, partial(_delete_conversation, deps, c)) for c in consent.get("foundry_conversations", [])]
    steps += [("container", c["id"], partial(_delete_container, deps, c)) for c in consent.get("foundry_containers", [])]
    if existing is None:
        # Both the lifecycle and the sweep can start here. Only the first manifest survives.
        manifest = deps.raw.manifest(container) or []
        application_id = consent.get("application_id")
        pending = [_item("raw_container", container, "pending", False)]
        pending.extend(_item(kind, target, "pending", False) for kind, target, _ in steps)
        deps.store.create_if_absent(PROOFS_CONTAINER, {
            "id": consent_id, "consent_id": consent_id, "application_id": application_id,
            "applicant_id": consent.get("applicant_id"), "reason": consent["status"],
            "trigger": payload["trigger"], "requested_by": payload["requested_by"], "deleted_at": now,
            "items": pending, "manifest": manifest,
            "manifest_sha256": hashlib.sha256(json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            "retained": ([{"kind": "application_record", "target": f"applications/{application_id}", "note": RETAINED}]
                         if application_id else []),
            "complete": False, "attempts": 0, "raw_repair_generation": consent.get("raw_repair_generation", 0),
            "event_published": False, "event_error": None,
        })
        existing = deps.store.read(PROOFS_CONTAINER, consent_id, consent_id)
        if not existing:
            raise IncompleteDeletion(f"Deletion checkpoint unavailable: {consent_id}")
        if proof_is_current(deps.store.read("consents", consent_id, consent_id), existing):
            return _proof_summary(existing)
    if existing.get("raw_repair_generation", 0) > consent.get("raw_repair_generation", 0):
        raise IncompleteDeletion(f"Deletion generation changed: {consent_id}")
    done = {(i["kind"], i["target"]): i for i in existing["items"] if i["verified"]}
    items = [_delete_raw(deps, container, fence_writers=bool(consent.get("raw_write_count")))]
    items += [done.get((kind, target)) or run() for kind, target, run in steps]
    proof = dict(existing, items=items, complete=all(i["verified"] for i in items), deleted_at=now,
                 attempts=existing.get("attempts", 0) + 1, raw_repair_generation=consent.get("raw_repair_generation", 0))
    if proof["complete"] and not proof.get("event_published"):
        _publish_ended(deps, proof)
    if not deps.store.replace(PROOFS_CONTAINER, proof, existing["_etag"]):
        raise IncompleteDeletion(f"Deletion proof changed: {consent_id}")

    def finish(doc: dict[str, Any]) -> bool | None:
        if doc.get("raw_repair_generation", 0) != proof["raw_repair_generation"]:
            return False
        if proof["complete"]:
            _finish_writes(doc)
        doc["deletion_complete"] = proof["complete"] and not doc.get("raw_repair_pending")

    final = update_doc(deps.store, "consents", consent_id, consent_id, finish)
    if final and (final.get("raw_repair_pending") or proof["complete"] and not proof_is_current(final, proof)):
        raise IncompleteDeletion(f"Raw repair pending: {consent_id}")
    if not proof["complete"]:
        failed = [f"{i['kind']}:{i['target']}" for i in items if not i["verified"]]
        raise IncompleteDeletion(f"삭제 미완료 {consent_id}: {', '.join(failed)}")
    return _proof_summary(proof)


def _notice_summary(entry: dict[str, Any]) -> dict[str, Any]:
    return {"kind": entry["kind"], "source": entry["source"], "title": entry["title"]}


def send_consent_notice(deps: Deps, payload: dict[str, Any]) -> dict[str, Any]:
    """갱신 예정·삭제 완료 안내문을 써서 동의 문서 notices에 남긴다(발송은 시뮬레이션)."""
    consent_id, kind = payload["consent_id"], payload["kind"]
    consent = deps.store.read("consents", consent_id, consent_id)
    if consent is None:
        raise LookupError(f"동의 문서 없음: {consent_id}")

    def sent_deletion(doc: dict[str, Any]) -> dict[str, Any] | None:
        return next((n for n in doc.get("notices", []) if n["kind"] == "deletion"), None)

    proof = prior = None
    if kind == "deletion":
        proof = deps.store.read(PROOFS_CONTAINER, consent_id, consent_id)
        if not proof_is_current(consent, proof):
            raise LookupError(f"완료된 삭제 증명 없음: {consent_id}")
        if (prior := sent_deletion(consent)) is not None and not consent.get("deletion_notice_pending"):
            return _notice_summary(prior)
    if prior is not None:
        entry = prior
    else:
        notice = write_notice(consent_facts(kind, consent, proof=proof), runner=deps.agents, model=deps.notice_model)
        entry = {"kind": kind, "at": deps.clock().isoformat(), **notice.as_dict()}

    def append(doc: dict[str, Any]) -> bool | None:
        if kind == "deletion" and not proof_is_current(doc, proof):
            raise LookupError(f"완료된 삭제 증명 없음: {consent_id}")
        if kind == "deletion":
            pending = doc.pop("deletion_notice_pending", False)
            if sent_deletion(doc) is not None:
                return bool(pending)
        doc.setdefault("notices", []).append(entry)
        return None

    update_doc(deps.store, "consents", consent_id, consent_id, append)
    return _notice_summary(entry)


CONSENT_ACTIVITIES: dict[str, Callable[[Deps, Any], Any]] = {
    f.__name__: f for f in (delete_consent_data, send_consent_notice)
}


def consent_lifecycle(ctx: Any, retry: Any) -> Steps:
    """만료(또는 철회·지금 만료)까지 기다린 뒤 삭제한다. 갱신 시점(만료 7일 전)에 안내를 한 번 보낸다."""
    start = LifecycleStart.model_validate(_json(ctx.get_input()))
    consent_id, expires = start.consent_id, start.expires_at
    renewal = renewal_at(expires)
    action, by = start.initial, start.requested_by
    reminded = False
    # 외부 이벤트 태스크는 완료될 때까지 재사용한다(entry 오케스트레이션과 같은 이유).
    events = {name: ctx.wait_for_external_event(name) for name in COMMANDS}
    ctx.set_custom_status("waiting")
    while True:
        if action == "remind_now":
            try:
                yield ctx.call_activity("send_consent_notice", {"consent_id": consent_id, "kind": "renewal"})
            except Exception:  # noqa: BLE001 - 안내 실패가 만료 처리를 막지 않는다
                log.warning("갱신 안내 실패: %s", consent_id)
            reminded, action = True, None
            ctx.set_custom_status("reminded")
        if action is not None:
            break
        now = _now(ctx)
        if now >= expires:
            action, by = "timer", "system"
            break
        if not reminded and now >= renewal:
            action = "remind_now"
            continue
        timer = ctx.create_timer(next_wake(now, expires if reminded else renewal))
        winner = yield ctx.task_any([*events.values(), timer])
        if winner is timer:
            continue
        timer.cancel()
        action = next(name for name, task in events.items() if task is winner)
        payload = _json(winner.result)
        by = str(payload.get("requested_by") or "unknown")[:64] if isinstance(payload, dict) else "unknown"
        if action == "remind_now":
            events[action] = ctx.wait_for_external_event(action)

    reason, trigger = _END[action]
    ctx.set_custom_status("deleting")
    try:
        result = yield ctx.call_activity_with_retry(
            "delete_consent_data",
            retry,
            {"consent_id": consent_id, "reason": reason, "trigger": trigger, "requested_by": by},
        )
    except Exception as e:  # noqa: BLE001 - 증명은 미완료로 남아 있다. 명령으로 다시 시작할 수 있다
        ctx.set_custom_status("deletion_failed")
        return {"consent_id": consent_id, "status": "deletion_failed", "reason": reason, "error": str(e)[:300]}
    reason = result.get("reason", reason)
    notice = "sent"
    try:
        yield ctx.call_activity("send_consent_notice", {"consent_id": consent_id, "kind": "deletion"})
    except Exception:  # noqa: BLE001
        notice = "failed"
    ctx.set_custom_status("deleted")
    return {"consent_id": consent_id, "status": "deleted", "reason": reason, "notice": notice}


async def handle_lifecycle_start(client: Any, body: str) -> str | None:
    try:
        msg = LifecycleStart.model_validate_json(body)
    except ValidationError as e:
        log.warning("잘못된 수명주기 메시지 폐기: %s", e.errors(include_input=False)[:3])
        return None
    instance = lifecycle_instance(msg.consent_id)
    status = _status(await client.get_status(instance))
    if not should_start(status):
        log.info("중복 수명주기 시작 무시: %s (%s)", instance, status)
        return None
    return await client.start_new(LIFECYCLE_ORCHESTRATOR, instance, msg.model_dump(mode="json"))


async def handle_consent_command(client: Any, body: str, store: Any) -> str:
    """invalid | raised | started(수명주기가 없던 동의) | retried(삭제 미완료로 끝난 수명주기) | unknown | ignored."""
    try:
        cmd = ConsentCommand.model_validate_json(body)
    except ValidationError as e:
        log.warning("잘못된 동의 명령 폐기: %s", e.errors(include_input=False)[:3])
        return "invalid"
    instance = lifecycle_instance(cmd.consent_id)
    status = _status(await client.get_status(instance))
    if status in ACTIVE:
        await client.raise_event(instance, cmd.action, {"requested_by": cmd.requested_by})
        outcome = "raised"
    elif should_start(status):
        consent = store.read("consents", cmd.consent_id, cmd.consent_id)
        if consent is None:
            log.warning("알 수 없는 동의 명령 폐기: %s", cmd.consent_id)
            return "unknown"
        start = LifecycleStart(
            consent_id=cmd.consent_id,
            expires_at=consent["expires_at"],
            initial=cmd.action,
            requested_by=cmd.requested_by,
        )
        await client.start_new(LIFECYCLE_ORCHESTRATOR, instance, start.model_dump(mode="json"))
        outcome = "started"
    else:
        consent = store.read("consents", cmd.consent_id, cmd.consent_id)
        if consent and consent.get("status") in ENDED_CONSENT and not consent.get("deletion_complete"):
            # 삭제가 끝내 실패해 수명주기가 끝났다. 끝난 사유 그대로 다시 시작해 미완료 항목을 다시 지운다.
            start = LifecycleStart(
                consent_id=cmd.consent_id,
                expires_at=consent["expires_at"],
                initial="revoke" if consent["status"] == "revoked" else "expire_now",
                requested_by=cmd.requested_by,
            )
            await client.start_new(LIFECYCLE_ORCHESTRATOR, instance, start.model_dump(mode="json"))
            return "retried"
        log.info("끝난 동의 수명주기 명령 무시: %s (%s)", instance, status)
        return "ignored"
    if cmd.action == "revoke" and cmd.application_id:
        if _status(await client.get_status(cmd.application_id)) in ACTIVE:
            await client.raise_event(
                cmd.application_id,
                WITHDRAWN_EVENT,
                {"consent_id": cmd.consent_id, "requested_by": cmd.requested_by},
            )
    return outcome
