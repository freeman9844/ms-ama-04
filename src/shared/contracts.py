"""에이전트·오케스트레이터·서비스 간 버전드 데이터 계약."""

from datetime import date
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, TypeAdapter, model_serializer, model_validator

from shared.money import RequestedUsdCents

SchemaV1 = Literal["1"]
NonNegFloat = Annotated[float, Field(ge=0, allow_inf_nan=False)]
# 상품 대출 기간(개월). 신규 신청 입구에서만 강제한다. 저장된 신청·대출과 진행 중인 오케스트레이션은 이전 범위(1~12)로 읽는다.
MIN_TENOR_MONTHS = 3
MAX_TENOR_MONTHS = 6


class Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class UsdProduct(Contract):
    product_version: Literal["usd-v1"]
    currency: Literal["USD"]
    language: Literal["en"]


USD_V1 = UsdProduct(product_version="usd-v1", currency="USD", language="en")
PlatformCode = Literal["naver_smart_store", "coupang", "own_store", "11st"]
Cents = Annotated[StrictInt, Field(ge=0)]


class ConsentScope(StrEnum):
    ACCOUNT = "account"
    TAX = "tax"
    PG = "pg"
    MARKETPLACE = "marketplace"


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


class UsdConsentGrant(ConsentGrant, UsdProduct):
    pass


TxnTypeCode = Literal["CARD_SETTLE", "PG_SETTLE", "TRANSFER_IN", "TRANSFER_OUT", "AUTO_DEBIT", "TAX"]


class _Transaction(Contract):
    schema_version: SchemaV1 = "1"
    txn_id: str = Field(min_length=1)
    loan_id: str | None = None  # Legacy synthetic post-loan feeds are already loan-scoped.
    scheduled_item_key: str | None = None  # Only synthetic scheduled debits are replaceable by a plan.
    booked_on: date

    @model_serializer(mode="wrap")
    def _omit_absent_repayment_metadata(self, serialize):
        data = serialize(self)
        if self.loan_id is None:
            data.pop("loan_id", None)  # Preserve the wire format of existing account histories.
        if self.scheduled_item_key is None:
            data.pop("scheduled_item_key", None)
        return data


class Transaction(_Transaction):
    """계좌 거래 1건. amount_krw > 0 이면 입금."""

    amount_krw: int
    balance_after_krw: int
    description: str
    txn_type_code: TxnTypeCode

    @model_validator(mode="after")
    def _nonzero(self) -> "Transaction":
        if self.amount_krw == 0:
            raise ValueError("amount_krw는 0일 수 없다")
        return self


class UsdTransaction(_Transaction, UsdProduct):
    platform_code: PlatformCode | None = None
    amount_cents: StrictInt
    balance_after_cents: StrictInt
    description: str
    txn_type_code: TxnTypeCode

    @model_validator(mode="after")
    def _nonzero(self) -> "UsdTransaction":
        if self.amount_cents == 0:
            raise ValueError("Transaction amount must be nonzero")
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
    deduction_krw: int = Field(default=0, ge=0)  # 광고비·물류비 상계(2026-11-08). 옛 JSON은 0.
    # 정산 보류·보류 해제(2026-11-11). 옛 JSON은 0.
    held_krw: int = Field(default=0, ge=0)
    released_krw: int = Field(default=0, ge=0)
    net_krw: int

    @model_validator(mode="after")
    def _net(self) -> "PgSettlement":
        expected = self.gross_krw - self.fee_krw - self.deduction_krw - self.held_krw + self.released_krw
        if self.net_krw != expected:
            raise ValueError("net_krw == gross_krw - fee_krw - deduction_krw - held_krw + released_krw 이어야 한다")
        return self


class PgOrderDay(Contract):
    """플랫폼·일자별 주문 총액과 반품액(사후 원장, 2026-11-11)."""

    schema_version: SchemaV1 = "1"
    order_on: date
    platform: str = Field(min_length=1)
    gross_krw: int = Field(ge=0)
    returns_krw: int = Field(ge=0)

    @model_validator(mode="after")
    def _returns(self) -> "PgOrderDay":
        if self.returns_krw > self.gross_krw:
            raise ValueError("returns_krw는 gross_krw 이하여야 한다")
        return self


class PgFlow(Contract):
    """사후 PG 원장(정산)과 주문 묶음. Mock `/pg/flow` 응답."""

    schema_version: SchemaV1 = "1"
    settlements: tuple[PgSettlement, ...] = ()
    orders: tuple[PgOrderDay, ...] = ()


class StorePlatform(Contract):
    """마켓 플랫폼 1곳의 셀러 성과 요약(최근 90일)."""

    platform: str = Field(min_length=1)
    opened_on: date
    rating: float = Field(ge=1.0, le=5.0, allow_inf_nan=False)
    review_count: int = Field(ge=0)
    orders_90d: int = Field(ge=0)
    gross_90d_krw: int = Field(ge=0)
    cancel_90d_krw: int = Field(ge=0)
    return_90d_krw: int = Field(ge=0)
    seller_grade: str
    pending_settlement_krw: int = Field(ge=0)
    assigned_pending_krw: int = Field(ge=0)

    @model_validator(mode="after")
    def _amounts(self) -> "StorePlatform":
        if self.cancel_90d_krw + self.return_90d_krw > self.gross_90d_krw:
            raise ValueError("cancel_90d_krw + return_90d_krw는 gross_90d_krw 이하여야 한다")
        if self.assigned_pending_krw > self.pending_settlement_krw:
            raise ValueError("assigned_pending_krw는 pending_settlement_krw 이하여야 한다")
        return self


class StorePenalty(Contract):
    """플랫폼 제재 이력. 자유 서술 사유는 받지 않는다."""

    platform: str
    occurred_on: date
    kind: Literal["suspension", "warning"]


class MarketplaceStore(Contract):
    schema_version: SchemaV1 = "1"
    as_of: date
    platforms: tuple[StorePlatform, ...] = ()
    penalties: tuple[StorePenalty, ...] = ()


class _CommonFeatures(Contract):
    """Underwriting이 산출해 Policy Engine에 넘기는 특성 벡터.

    LLM이 만든 JSON이 들어올 수 있으므로 strict로 받는다(true→1.0 같은 암묵 변환을 막아 fail-closed).
    """

    model_config = ConfigDict(strict=True)

    schema_version: SchemaV1 = "1"
    application_id: str = Field(min_length=1)
    months_of_history: int = Field(ge=0)
    dscr: NonNegFloat
    cash_buffer_days: NonNegFloat
    revenue_volatility: NonNegFloat
    tax_bank_match_ratio: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    requested_tenor_months: int = Field(ge=1, le=12)
    industry: str
    region: str
    business_age_months: int = Field(ge=0)
    # 정책 v3(2026-11-08) 선택 지표. 옛 문서·자료 없음은 None이며 해당 규칙은 적용하지 않는다.
    revenue_trend_ratio: NonNegFloat | None = None
    high_cost_lender_count: int | None = Field(default=None, ge=0)
    pg_reconciliation_ratio: Annotated[float, Field(ge=0, le=1)] | None = None
    pg_top_platform_share: Annotated[float, Field(ge=0, le=1)] | None = None
    # 정책 v4 마켓 셀러 성과 지표. 마켓 동의·자료가 없으면 None이다.
    mkt_tenure_months: int | None = Field(default=None, ge=0)
    mkt_rating: float | None = Field(default=None, ge=1, le=5, allow_inf_nan=False)
    mkt_review_count: int | None = Field(default=None, ge=0)
    mkt_return_rate_90d: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    mkt_cancel_rate_90d: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    mkt_days_since_suspension: int | None = Field(default=None, ge=0)


class CashFlowFeatures(_CommonFeatures):
    avg_monthly_inflow_krw: int = Field(ge=0)
    requested_amount_krw: int = Field(gt=0)
    mkt_pending_settlement_krw: int | None = Field(default=None, ge=0)


class UsdCashFlowFeatures(_CommonFeatures, UsdProduct):
    avg_monthly_inflow_cents: Cents
    requested_amount_cents: RequestedUsdCents
    requested_tenor_months: int = Field(ge=MIN_TENOR_MONTHS, le=MAX_TENOR_MONTHS)
    mkt_pending_settlement_cents: Cents | None = None


MKT_FEATURES: tuple[str, ...] = (
    "mkt_tenure_months",
    "mkt_rating",
    "mkt_review_count",
    "mkt_return_rate_90d",
    "mkt_cancel_rate_90d",
    "mkt_days_since_suspension",
    "mkt_pending_settlement_krw",
)


class Decision(StrEnum):
    ELIGIBLE = "eligible"
    REFER = "refer"
    INELIGIBLE = "ineligible"


class ReasonCode(Contract):
    code: str = Field(pattern=r"^[RS]\d{3}$")
    message: str
    field: str | None = None
    observed: float | None = None
    threshold: float | None = None


class CashOnlyDecision(Contract):
    """마켓 자료를 빼고 현금흐름만으로 낸 비교 판정."""

    decision: Decision
    max_amount_krw: int = Field(ge=0)
    reason_codes: tuple[ReasonCode, ...]


class PolicyDecision(Contract):
    schema_version: SchemaV1 = "1"
    application_id: str = Field(min_length=1)
    decision: Decision
    max_amount_krw: int = Field(ge=0)
    rate_band: str | None
    reason_codes: tuple[ReasonCode, ...]
    rules_version: str = Field(min_length=1)
    cash_only: CashOnlyDecision | None = None

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


class UsdCashOnlyDecision(UsdProduct):
    decision: Decision
    max_amount_cents: Cents
    reason_codes: tuple[ReasonCode, ...]

    @model_validator(mode="after")
    def _valid_limit(self):
        if (self.decision is Decision.INELIGIBLE) != (self.max_amount_cents == 0):
            raise ValueError("Only an ineligible decision has a zero limit")
        if self.decision is not Decision.ELIGIBLE and not self.reason_codes:
            raise ValueError("Referral and ineligibility require reason codes")
        return self


class UsdPolicyDecision(UsdCashOnlyDecision):
    schema_version: SchemaV1 = "1"
    application_id: str = Field(min_length=1)
    rate_band: str | None
    rules_version: Literal["usd-v1"]
    cash_only: UsdCashOnlyDecision | None = None

    @model_validator(mode="after")
    def _valid_band(self):
        if (self.decision is Decision.INELIGIBLE) != (self.rate_band is None):
            raise ValueError("Only an ineligible decision has no rate band")
        return self


class Recommendation(StrEnum):
    APPROVE = "approve"
    DECLINE = "decline"
    REFER = "refer"


# M: 계산 지표, S: 월별 계열, P: 정책 판정 값, W: 사후 주간 계열, B: 사후 모니터링 기준선·업종 집단.
# 예) M:dscr, S:2026-07:revenue, P:R108:threshold, W:3:revenue, B:index:01
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


class OfficerCheck(Contract):
    """심사역이 결정 전에 확인할 사항 1개. 근거 지표 ID가 하나 이상 있어야 하며, 승인·거절·금리·한도를 지시하지 않는다."""

    text: str = Field(min_length=1, max_length=200)
    metric_ids: tuple[EvidenceId, ...] = Field(min_length=1)
    supported: bool | None = None


class ReviewOutcome(Contract):
    passed: bool
    rounds: int = Field(ge=1, le=3)
    issues: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _issues(self) -> "ReviewOutcome":
        if not self.passed and not self.issues:
            raise ValueError("검토 미통과에는 사유가 필요하다")
        return self


class _Assessment(Contract):
    application_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    claims: tuple[Claim, ...] = Field(min_length=1)
    counterfactuals: tuple[Counterfactual, ...] = ()
    officer_checks: tuple[OfficerCheck, ...] = Field(default=(), max_length=3)
    review: ReviewOutcome
    models: dict[str, str]


class CreditAssessment(_Assessment):
    """신규 의견은 신청액 전액만 평가한다. 최종 결정권은 심사역에게 있다."""

    schema_version: Literal["2"]
    recommendation: Recommendation

    @model_validator(mode="after")
    def _reviewed(self) -> "CreditAssessment":
        if not self.review.passed:
            raise ValueError("검토를 통과해야 전액 의견을 발행할 수 있다")
        return self


class UsdCreditAssessment(CreditAssessment, UsdProduct):
    pass


class LegacyCreditAssessment(_Assessment):
    """과거 감사 자료 읽기 전용. 기존 금액과 조건을 전액 권고로 해석하지 않는다."""

    schema_version: SchemaV1 = "1"
    recommendation: Literal["approve", "conditional", "refer", "decline"]
    proposed_structure: ProposedStructure | None

    @model_validator(mode="after")
    def _structure(self) -> "LegacyCreditAssessment":
        if self.recommendation in ("approve", "conditional") and not self.proposed_structure:
            raise ValueError("승인·조건부 추천에는 제안 구조가 필요하다")
        return self


stored_credit_assessment = TypeAdapter(CreditAssessment | LegacyCreditAssessment)


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
    SETTLEMENT_HOLD = "settlement_hold"
    RETURN_SPIKE = "return_spike"
    COST_SURGE = "cost_surge"
    PAYROLL_DELAY = "payroll_delay"
    SUPPLIER_LOSS = "supplier_loss"
    TAX_ARREARS = "tax_arrears"
    OTHER = "other"


class RecommendedAction(StrEnum):
    MONITOR = "monitor"
    CONTACT_BORROWER = "contact_borrower"
    HARDSHIP_REVIEW = "hardship_review"
    OFFICER_REVIEW = "officer_review"


class AlertCause(StrEnum):
    """조기경보 원인(2026-11-11). 라벨: 매출 부진, 정산 보류, 반품 급증, 비용 증가, 고금리 차입."""

    SALES_SLUMP = "sales_slump"
    SETTLEMENT_HOLD = "settlement_hold"
    RETURN_SPIKE = "return_spike"
    COST_SURGE = "cost_surge"
    HIGH_COST_BORROWING = "high_cost_borrowing"


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
    # 원인 분류(2026-11-11). 옛 경보는 원인 없음.
    primary_cause: AlertCause | None = None
    secondary_causes: tuple[AlertCause, ...] = Field(default=(), max_length=2)
    # 확인할 조치 문장(2026-11-11). summary가 이유다. 옛 경보는 None.
    check_action: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def _action(self) -> "EarlyWarningAlert":
        if self.severity is AlertSeverity.HIGH and self.recommended_action is RecommendedAction.MONITOR:
            raise ValueError("high 경보는 관찰(monitor)만 권고할 수 없다")
        if self.primary_cause is None and self.secondary_causes:
            raise ValueError("주원인 없이 부원인을 둘 수 없다")
        if self.primary_cause in self.secondary_causes:
            raise ValueError("부원인은 주원인과 겹칠 수 없다")
        if len(set(self.secondary_causes)) != len(self.secondary_causes):
            raise ValueError("부원인은 중복될 수 없다")
        return self


class UsdEarlyWarningAlert(EarlyWarningAlert, UsdProduct):
    pass


class HardshipCategory(StrEnum):
    REVENUE_SHOCK = "revenue_shock"
    ILLNESS_OR_DISASTER = "illness_or_disaster"
    DEBT_OVERLOAD = "debt_overload"
    NONE = "none"
