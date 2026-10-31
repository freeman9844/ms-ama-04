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
    requested_tenor_months: int = Field(ge=1, le=60)
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
