"""에이전트·오케스트레이터·서비스 간 버전드 데이터 계약."""

from datetime import date
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

SchemaV1 = Literal["1"]
NonNegFloat = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ConsentScope(StrEnum):
    ACCOUNT = "account"
    TAX = "tax"
    PG = "pg"


class ConsentGrant(Contract):
    schema_version: SchemaV1 = "1"
    consent_id: str = Field(min_length=1)
    applicant_id: str = Field(min_length=1)
    scopes: frozenset[ConsentScope] = Field(min_length=1)
    purpose: str = Field(min_length=1)
    granted_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def _expiry_after_grant(self) -> "ConsentGrant":
        if self.expires_at <= self.granted_at:
            raise ValueError("expires_at은 granted_at 이후여야 한다")
        return self


class ApplicantProfile(Contract):
    schema_version: SchemaV1 = "1"
    applicant_id: str = Field(min_length=1)
    business_name: str = Field(min_length=1)
    business_reg_no: str = Field(pattern=r"^\d{3}-\d{2}-\d{5}$")
    representative_name: str = Field(min_length=1)
    industry: str
    region: str
    business_age_months: int = Field(ge=0)


TxnTypeCode = Literal["CARD_SETTLE", "PG_SETTLE", "TRANSFER_IN", "TRANSFER_OUT", "AUTO_DEBIT", "TAX"]


class Transaction(Contract):
    """계좌 거래 1건. amount_krw > 0 이면 입금."""

    schema_version: SchemaV1 = "1"
    txn_id: str = Field(min_length=1)
    booked_on: date
    amount_krw: int
    balance_after_krw: int
    description: str
    txn_type_code: TxnTypeCode

    @model_validator(mode="after")
    def _nonzero(self) -> "Transaction":
        if self.amount_krw == 0:
            raise ValueError("amount_krw는 0일 수 없다")
        return self


class TaxFiling(Contract):
    schema_version: SchemaV1 = "1"
    period_start: date
    period_end: date
    declared_revenue_krw: int = Field(ge=0)

    @model_validator(mode="after")
    def _period_order(self) -> "TaxFiling":
        if self.period_start >= self.period_end:
            raise ValueError("period_start < period_end 이어야 한다")
        return self


class PgSettlement(Contract):
    schema_version: SchemaV1 = "1"
    settled_on: date
    platform: str = Field(min_length=1)
    gross_krw: int = Field(ge=0)
    fee_krw: int = Field(ge=0)
    net_krw: int

    @model_validator(mode="after")
    def _net(self) -> "PgSettlement":
        if self.net_krw != self.gross_krw - self.fee_krw:
            raise ValueError("net_krw == gross_krw - fee_krw 이어야 한다")
        return self


class CashFlowFeatures(Contract):
    """Underwriting이 산출해 Policy Engine에 넘기는 특성 벡터.

    LLM이 만든 JSON이 들어올 수 있으므로 strict로 받는다(true→1.0 같은 암묵 변환을 막아 fail-closed).
    """

    model_config = ConfigDict(strict=True)

    schema_version: SchemaV1 = "1"
    application_id: str = Field(min_length=1)
    months_of_history: int = Field(ge=0)
    avg_monthly_inflow_krw: int = Field(ge=0)
    dscr: NonNegFloat
    cash_buffer_days: NonNegFloat
    revenue_volatility: NonNegFloat
    tax_bank_match_ratio: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    requested_amount_krw: int = Field(gt=0)
    requested_tenor_months: int = Field(ge=1, le=12)
    industry: str
    region: str
    business_age_months: int = Field(ge=0)


class Decision(StrEnum):
    ELIGIBLE = "eligible"
    REFER = "refer"
    INELIGIBLE = "ineligible"


class ReasonCode(Contract):
    code: str = Field(pattern=r"^R\d{3}$")
    message: str
    field: str | None = None
    observed: float | None = None
    threshold: float | None = None


class PolicyDecision(Contract):
    schema_version: SchemaV1 = "1"
    application_id: str = Field(min_length=1)
    decision: Decision
    max_amount_krw: int = Field(ge=0)
    rate_band: str | None
    reason_codes: tuple[ReasonCode, ...]
    rules_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def _invariants(self) -> "PolicyDecision":
        if self.decision is Decision.INELIGIBLE:
            if self.max_amount_krw != 0 or self.rate_band is not None:
                raise ValueError("INELIGIBLE은 한도 0, 밴드 없음이어야 한다")
        else:
            if self.max_amount_krw <= 0 or self.rate_band is None:
                raise ValueError("ELIGIBLE/REFER는 한도>0, 밴드가 있어야 한다")
        if self.decision is not Decision.ELIGIBLE and not self.reason_codes:
            raise ValueError("ELIGIBLE이 아닌 결정은 사유 코드가 필요하다")
        return self


class Recommendation(StrEnum):
    APPROVE = "approve"
    CONDITIONAL = "conditional"
    DECLINE = "decline"
    REFER = "refer"


# M: 계산 지표, S: 월별 계열, P: 정책 판정 값, W: 사후 주간 계열, B: 사후 모니터링 기준선·업종 집단.
# 예) M:dscr, S:2026-07:revenue, P:R101:threshold, W:3:revenue, B:index:01
EVIDENCE_ID_PATTERN = r"^[MSPWBF]:[A-Za-z0-9_.:-]+$"
EvidenceId = Annotated[str, Field(pattern=EVIDENCE_ID_PATTERN)]


class Claim(Contract):
    """분석가 주장 1개. 근거(지표 ID 또는 거래 ID)가 하나 이상 있어야 한다."""

    text: str = Field(min_length=1, max_length=400)
    metric_ids: tuple[EvidenceId, ...] = ()
    txn_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _grounded(self) -> "Claim":
        if not self.metric_ids and not self.txn_ids:
            raise ValueError("주장에는 근거 지표 ID나 거래 ID가 하나 이상 필요하다")
        return self


class ProposedStructure(Contract):
    amount_krw: int = Field(gt=0)
    tenor_months: int = Field(ge=1, le=12)
    schedule: Literal["level", "seasonal"]
    reduced_payment_months: tuple[Annotated[int, Field(ge=1, le=12)], ...] = ()
    rationale: str

    @model_validator(mode="after")
    def _schedule(self) -> "ProposedStructure":
        if (self.schedule == "seasonal") != bool(self.reduced_payment_months):
            raise ValueError("seasonal이면 감액 월이 있어야 하고, level이면 없어야 한다")
        return self


class Counterfactual(Contract):
    metric_id: EvidenceId
    current: float
    target: float
    explanation: str = Field(min_length=1)


class ReviewOutcome(Contract):
    passed: bool
    rounds: int = Field(ge=1, le=3)
    issues: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _issues(self) -> "ReviewOutcome":
        if not self.passed and not self.issues:
            raise ValueError("검토 미통과에는 사유가 필요하다")
        return self


class CreditAssessment(Contract):
    """Underwriting 에이전트 산출물(참고용 추천). 판정은 Policy Engine과 심사역이 한다."""

    schema_version: SchemaV1 = "1"
    application_id: str = Field(min_length=1)
    recommendation: Recommendation
    summary: str = Field(min_length=1)
    claims: tuple[Claim, ...] = Field(min_length=1)
    proposed_structure: ProposedStructure | None
    counterfactuals: tuple[Counterfactual, ...] = ()
    review: ReviewOutcome
    models: dict[str, str]

    @model_validator(mode="after")
    def _structure(self) -> "CreditAssessment":
        if self.recommendation in (Recommendation.APPROVE, Recommendation.CONDITIONAL) and not self.proposed_structure:
            raise ValueError("승인·조건부 추천에는 제안 구조가 필요하다")
        return self


class AlertSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SignalKind(StrEnum):
    LOAN_STACKING = "loan_stacking"
    INFLOW_DROP = "inflow_drop"
    BUFFER_DROP = "buffer_drop"
    VOLATILITY_SPIKE = "volatility_spike"
    PROJECTED_SHORTFALL = "projected_shortfall"
    PAYROLL_DELAY = "payroll_delay"
    SUPPLIER_LOSS = "supplier_loss"
    TAX_ARREARS = "tax_arrears"
    OTHER = "other"


class RecommendedAction(StrEnum):
    MONITOR = "monitor"
    CONTACT_BORROWER = "contact_borrower"
    HARDSHIP_REVIEW = "hardship_review"
    OFFICER_REVIEW = "officer_review"


class AlertSignal(Claim):
    """조기경보 신호 1개. Claim처럼 근거(W:·B: 지표 ID 또는 거래 ID)가 하나 이상 있어야 한다."""

    kind: SignalKind


class EarlyWarningAlert(Contract):
    """Early-Warning 에이전트 산출물(스펙 §3.3-4). alert.raised로 발행되어 콘솔과 Collections에 전달된다."""

    schema_version: SchemaV1 = "1"
    alert_id: str = Field(min_length=1)
    loan_id: str = Field(min_length=1)
    week: int = Field(ge=1, le=53)
    as_of: date
    severity: AlertSeverity
    summary: str = Field(min_length=1, max_length=600)
    signals: tuple[AlertSignal, ...] = Field(min_length=1)
    recommended_action: RecommendedAction
    triggers: tuple[str, ...]
    models: dict[str, str]

    @model_validator(mode="after")
    def _action(self) -> "EarlyWarningAlert":
        if self.severity is AlertSeverity.HIGH and self.recommended_action is RecommendedAction.MONITOR:
            raise ValueError("high 경보는 관찰(monitor)만 권고할 수 없다")
        return self


class HardshipCategory(StrEnum):
    REVENUE_SHOCK = "revenue_shock"
    ILLNESS_OR_DISASTER = "illness_or_disaster"
    DEBT_OVERLOAD = "debt_overload"
    NONE = "none"
