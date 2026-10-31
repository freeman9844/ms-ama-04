"""신청 여정의 상태·요청·심사역 결정 모델과 동의 상수."""

from datetime import timedelta, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

KST = timezone(timedelta(hours=9), "KST")
APPLICATION_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{2,62}$"
CONSENT_SCOPES = ("account", "tax", "pg")
CONSENT_PURPOSE = "운전자금 대출 심사"
CONSENT_TTL_DAYS = 90


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
