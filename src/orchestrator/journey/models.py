"""신청 여정의 상태·요청·심사역 결정 모델과 동의 상수."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta, timezone
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    TypeAdapter,
    field_validator,
    model_validator,
)

KST = timezone(timedelta(hours=9), "KST")
APPLICATION_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{2,62}$"
CONSENT_SCOPES = ("account", "tax", "pg")
CONSENT_PURPOSE = "운전자금 대출 심사"
CONSENT_TTL_DAYS = 90
MONITORING_SCOPES = ("account", "pg")
MONITORING_PURPOSE = "대출 사후 모니터링"
LOAN_ID_PATTERN = APPLICATION_ID_PATTERN
# 진행 중인 재생을 새 재생이 덮어쓰지 못하게 막는 시간. 멈춘 재생은 이 시간이 지나면 다시 시작할 수 있다.
REPLAY_GUARD = timedelta(minutes=30)
CONSENT_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$"
GRIEVANCE_ID_PATTERN = r"^grv-[a-z0-9]{6,32}$"
RENEWAL_NOTICE_DAYS = 7
# Durable timer는 한 번에 최대 6일까지만 건다(긴 대기는 나눠서 반복).
MAX_TIMER = timedelta(days=6)
WITHDRAWN_EVENT = "consent_withdrawn"
GRIEVANCE_TEXT_MAX = 1000


def lifecycle_instance(consent_id: str) -> str:
    return f"consent-{consent_id}"


def renewal_at(expires_at: datetime) -> datetime:
    return expires_at - timedelta(days=RENEWAL_NOTICE_DAYS)


def next_wake(now: datetime, target: datetime) -> datetime:
    """target까지 남은 대기를 MAX_TIMER 단위로 자른 다음 깨어날 시각."""
    return max(now, min(target, now + MAX_TIMER))


# 대기 항목(추심 승인·고객 응답·플랜 변경 승인·재조정 결정)으로 멈춘 재생. 새 재생(start)이 이 재생을 덮지 않게 막는다.
# paused·awaiting_customer는 대기 항목이 끝나기를, awaiting_resume은 resume·stop을 기다린다.
REPLAY_HELD = frozenset({"paused", "awaiting_resume", "awaiting_customer"})


def replay_held(monitoring: Mapping[str, Any] | None) -> bool:
    return bool(monitoring) and monitoring.get("status") in REPLAY_HELD  # type: ignore[union-attr]


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
    WITHDRAWN = "withdrawn"
    FAILED = "failed"


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_id: str = Field(pattern=APPLICATION_ID_PATTERN)
    applicant_id: str = Field(min_length=1, max_length=64)
    requested_amount_krw: int = Field(gt=0, le=10_000_000)
    requested_tenor_months: int = Field(ge=1, le=12)
    sla_hours: float = Field(default=24, gt=0, le=720)
    submitted_at: datetime | None = None
    source: Literal["console", "customer", "load", "fault"] | None = None
    run_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._-]{1,64}$")

    @field_validator("submitted_at")
    @classmethod
    def _utc(cls, v: datetime | None) -> datetime | None:
        return v.replace(tzinfo=UTC) if v and v.tzinfo is None else v


class OfficerDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "conditional", "decline"]
    officer_id: str = Field(min_length=1)
    amount_krw: int | None = Field(default=None, gt=0)
    conditions: list[str] = Field(default_factory=list)
    reason: str = ""
    # 정책 판정이 거절이거나 결과가 없는 건을 심사역이 확인 후 승인할 때만 True(감사 기록용).
    policy_override: bool = False


class OfficerMessage(BaseModel):
    """officer-decisions 큐 메시지."""

    model_config = ConfigDict(extra="forbid")

    application_id: str = Field(pattern=APPLICATION_ID_PATTERN)
    decision: dict[str, Any]


class ReplayRequest(BaseModel):
    """monitoring-replay 큐 메시지. start는 13주 재생을 새로 시작하고, resume·stop은 재조정 결정으로
    멈춘(awaiting_resume) 재생을 이어 가거나 끝낸다. resume·stop은 멈춘 재생의 replay_id가 있어야 한다."""

    model_config = ConfigDict(extra="forbid")

    loan_id: str = Field(pattern=LOAN_ID_PATTERN)
    action: Literal["start", "resume", "stop"] = "start"
    replay_id: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def _replay_id_for_paused(self) -> "ReplayRequest":
        if self.action != "start" and self.replay_id is None:
            raise ValueError("resume·stop에는 replay_id가 필요하다")
        return self


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


class RepaymentUpdated(BaseModel):
    """repayment.updated 토픽 메시지(세션 = loan_id). 상환일 미납으로 추심 건이 열렸다."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["repayment.updated"]
    loan_id: str = Field(pattern=LOAN_ID_PATTERN)
    case_id: str = Field(min_length=1, max_length=128)


class RestructureDecision(BaseModel):
    """restructure-decisions 큐 메시지. 재조정 상담 요청에 대한 심사역 결정(kind가 없는 옛 메시지도 이것이다)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["restructure"] = "restructure"
    loan_id: str = Field(pattern=LOAN_ID_PATTERN)
    alert_id: str = Field(min_length=1, max_length=128)
    decision: Literal["approve", "decline"]
    officer_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(default="", max_length=500)


class CollectionsDecision(BaseModel):
    """추심 제안에 대한 심사역 결정(승인·수정·반려). 수정이면 text·include_hardship을 쓴다."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["collections"]
    loan_id: str = Field(pattern=LOAN_ID_PATTERN)
    case_id: str = Field(min_length=1, max_length=128)
    decision: Literal["approve", "modify", "reject"]
    officer_id: str = Field(min_length=1, max_length=64)
    text: str | None = Field(default=None, max_length=300)
    include_hardship: bool | None = None
    reason: str = Field(default="", max_length=500)


class PlanChoice(BaseModel):
    """셀프 상환 플랜 제안에 대한 고객 선택."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["plan_choice"]
    loan_id: str = Field(pattern=LOAN_ID_PATTERN)
    offer_id: str = Field(min_length=1, max_length=128)
    option: Literal["move", "split", "keep"]


class PlanDecision(BaseModel):
    """고객이 고른 플랜 변경 요청에 대한 심사역 결정."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["plan_change"]
    loan_id: str = Field(pattern=LOAN_ID_PATTERN)
    change_id: str = Field(min_length=1, max_length=128)
    decision: Literal["approve", "decline"]
    officer_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(default="", max_length=500)


class OfferExpire(BaseModel):
    """심사역의 "고객 응답 없이 계속": 열린 플랜 제안을 닫는다."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["offer_expire"]
    loan_id: str = Field(pattern=LOAN_ID_PATTERN)
    offer_id: str = Field(min_length=1, max_length=128)
    officer_id: str = Field(min_length=1, max_length=64)


def _message_kind(value: Any) -> str:
    if isinstance(value, Mapping):
        return value.get("kind", "restructure")
    return getattr(value, "kind", "restructure")


# restructure-decisions 큐 메시지(kind로 가른다. kind가 없으면 옛 재조정 결정).
OFFICER_MESSAGE: TypeAdapter[
    RestructureDecision | CollectionsDecision | PlanChoice | PlanDecision | OfferExpire
] = TypeAdapter(
    Annotated[
        Annotated[RestructureDecision, Tag("restructure")]
        | Annotated[CollectionsDecision, Tag("collections")]
        | Annotated[PlanChoice, Tag("plan_choice")]
        | Annotated[PlanDecision, Tag("plan_change")]
        | Annotated[OfferExpire, Tag("offer_expire")],
        Discriminator(_message_kind),
    ]
)


class LifecycleStart(BaseModel):
    """consent-lifecycle 큐 메시지이자 consent_lifecycle 오케스트레이션 입력.

    initial은 수명주기 인스턴스가 없는 동의(P5b 이전 동의)에 명령이 왔을 때 바로 처리할 동작이다.
    """

    model_config = ConfigDict(extra="forbid")

    consent_id: str = Field(pattern=CONSENT_ID_PATTERN)
    expires_at: AwareDatetime
    initial: Literal["revoke", "expire_now", "remind_now"] | None = None
    requested_by: str = Field(default="system", min_length=1, max_length=64)


class ConsentCommand(BaseModel):
    """consent-commands 큐 메시지. 차주 철회와 데모 버튼(지금 만료, 갱신 안내)."""

    model_config = ConfigDict(extra="forbid")

    consent_id: str = Field(pattern=CONSENT_ID_PATTERN)
    action: Literal["revoke", "expire_now", "remind_now"]
    requested_by: str = Field(min_length=1, max_length=64)
    application_id: str | None = Field(default=None, pattern=APPLICATION_ID_PATTERN)


class GrievanceSubmission(BaseModel):
    """grievances 큐 메시지(차주 이의제기)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    grievance_id: str = Field(pattern=GRIEVANCE_ID_PATTERN)
    application_id: str = Field(pattern=APPLICATION_ID_PATTERN)
    applicant_id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=GRIEVANCE_TEXT_MAX)


class GrievanceDecision(BaseModel):
    """grievance-decisions 큐 메시지. uphold는 원결정 유지, rereview는 재심사 결과를 기록한다."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    grievance_id: str = Field(pattern=GRIEVANCE_ID_PATTERN)
    application_id: str = Field(pattern=APPLICATION_ID_PATTERN)
    outcome: Literal["uphold", "rereview"]
    officer_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=500)
    new_decision: Literal["approve", "conditional", "decline"] | None = None
    amount_krw: int | None = Field(default=None, gt=0)
    conditions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _outcome(self) -> "GrievanceDecision":
        if self.outcome == "rereview" and self.new_decision is None:
            raise ValueError("재심사에는 새 결정이 필요하다")
        if self.outcome == "uphold" and (self.new_decision or self.amount_krw or self.conditions):
            raise ValueError("원결정 유지에는 새 결정을 둘 수 없다")
        return self
