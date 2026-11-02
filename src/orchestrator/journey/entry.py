"""큐 트리거 처리. Durable 클라이언트는 get_status/start_new/raise_event만 쓴다."""

import logging
from typing import Any

from pydantic import ValidationError

from journey.models import OfficerMessage, StartRequest
from journey.orchestration import EVENT_OFFICER_DECISION, ORCHESTRATOR

log = logging.getLogger("journey.entry")
RESTARTABLE = ("Failed", "Terminated", "Canceled")


def _status(status: Any) -> str | None:
    runtime_status = getattr(status, "runtime_status", None)
    return getattr(runtime_status, "value", runtime_status)


def should_start(status: str | None) -> bool:
    return status is None or status in RESTARTABLE


async def handle_start(client: Any, body: str) -> str | None:
    try:
        req = StartRequest.model_validate_json(body)
    except ValidationError as e:
        log.warning("잘못된 신청 메시지 폐기: %s", e.errors(include_input=False)[:3])
        return None
    status = _status(await client.get_status(req.application_id))
    if not should_start(status):
        log.info("중복 신청 무시: %s (%s)", req.application_id, status)
        return None
    return await client.start_new(ORCHESTRATOR, req.application_id, req.model_dump(mode="json", exclude_none=True))


async def handle_decision(client: Any, body: str) -> bool:
    try:
        msg = OfficerMessage.model_validate_json(body)
    except ValidationError as e:
        log.warning("잘못된 결정 메시지 폐기: %s", e.errors(include_input=False)[:3])
        return False
    status = _status(await client.get_status(msg.application_id))
    if status != "Running":
        log.warning("실행 중이 아닌 신청의 결정 폐기: %s (%s)", msg.application_id, status)
        return False
    await client.raise_event(msg.application_id, EVENT_OFFICER_DECISION, msg.decision)
    return True
