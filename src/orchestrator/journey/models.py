"""신청 여정의 상태·요청·심사역 결정 모델과 동의 상수."""

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

KST = timezone(timedelta(hours=9), "KST")
APPLICATION_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{2,62}$"
CONSENT_SCOPES = ("account", "tax", "pg")
CONSENT_PURPOSE = "운전자금 대출 심사"
CONSENT_TTL_DAYS = 90
MONITORING_SCOPES = ("account",)
MONITORING_PURPOSE = "대출 사후 모니터링"
LOAN_ID_PATTERN = APPLICATION_ID_PATTERN
# 진행 중인 재생을 새 재생이 덮어쓰지 못하게 막는 시간. 멈춘 재생은 이 시간이 지나면 다시 시작할 수 있다.
REPLAY_GUARD = timedelta(minutes=30)


def replay_in_progress(monitoring: Mapping[str, Any] | None, now: datetime) -> bool:
    """같은 대출의 재생이 진행 중인가. 동시 재생은 대출 문서(monitoring)를 서로 덮어쓴다."""
    if not monitoring or monitoring.get("status") != "replaying":
        return False
    try:
        started = datetime.fromisoformat(monitoring["started_at"])
    except (KeyError, TypeError, ValueError):
        return False
    return now - started < REPLAY_GUARD


class Status(StrEnum):
    RECEIVED = "received"
    CONSENT_GRANTED = "consent_granted"
    DATA_ASSEMBLED = "data_assembled"
    DATA_INCOMPLETE = "data_incomplete"
    UNDERWRITTEN = "underwritten"
    ASSESSED = "assessed"
    AWAITING_OFFICER = "awaiting_officer"
    ESCALATED = "escalated"
    APPROVED = "approved"
    CONDITIONALLY_APPROVED = "conditionally_approved"
    DECLINED = "declined"
    CLOSED_NO_DECISION = "closed_no_decision"
    FAILED = "failed"


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_id: str = Field(pattern=APPLICATION_ID_PATTERN)
    applicant_id: str = Field(min_length=1, max_length=64)
    requested_amount_krw: int = Field(gt=0)
    requested_tenor_months: int = Field(ge=1, le=60)
    sla_hours: float = Field(default=24, gt=0, le=720)


class OfficerDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "conditional", "decline"]
    officer_id: str = Field(min_length=1)
    amount_krw: int | None = Field(default=None, gt=0)
    conditions: list[str] = Field(default_factory=list)
    reason: str = ""


class OfficerMessage(BaseModel):
    """officer-decisions 큐 메시지."""

    model_config = ConfigDict(extra="forbid")

    application_id: str = Field(pattern=APPLICATION_ID_PATTERN)
    decision: dict[str, Any]


class ReplayRequest(BaseModel):
    """monitoring-replay 큐 메시지. 승인된 대출의 사후 흐름 13주를 재생한다."""

    model_config = ConfigDict(extra="forbid")

    loan_id: str = Field(pattern=LOAN_ID_PATTERN)


class FlowUpdated(BaseModel):
    """flow.updated 토픽 메시지(세션 = loan_id). 스모크 메시지는 type이 달라 무시된다."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["flow.updated"]
    loan_id: str = Field(pattern=LOAN_ID_PATTERN)
    replay_id: str = Field(min_length=1, max_length=64)
    week: int = Field(ge=1, le=53)


class AlertRaised(BaseModel):
    """alert.raised 토픽 메시지(세션 = loan_id)."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["alert.raised"]
    loan_id: str = Field(pattern=LOAN_ID_PATTERN)
    alert_id: str = Field(min_length=1, max_length=128)


class RestructureDecision(BaseModel):
    """restructure-decisions 큐 메시지. 재조정 상담 요청에 대한 심사역 결정."""

    model_config = ConfigDict(extra="forbid")

    loan_id: str = Field(pattern=LOAN_ID_PATTERN)
    alert_id: str = Field(min_length=1, max_length=128)
    decision: Literal["approve", "decline"]
    officer_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(default="", max_length=500)
