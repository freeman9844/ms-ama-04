"""Native English USD source contracts; never deserialize legacy currency as cents."""

from datetime import date

from pydantic import Field, model_validator

from shared.contracts import ApplicantProfile, Cents, PlatformCode, StorePenalty, UsdProduct


class UsdProfile(ApplicantProfile, UsdProduct):
    pass


class UsdTaxFiling(UsdProduct):
    period_start: date
    period_end: date
    declared_revenue_cents: Cents


class UsdSettlement(UsdProduct):
    settled_on: date
    platform_code: PlatformCode
    gross_cents: Cents
    fee_cents: Cents
    deduction_cents: Cents = 0
    held_cents: Cents = 0
    released_cents: Cents = 0
    net_cents: Cents

    @model_validator(mode="after")
    def _net(self):
        if self.net_cents != (self.gross_cents - self.fee_cents - self.deduction_cents
                              - self.held_cents + self.released_cents):
            raise ValueError("Settlement gross less fees, deductions and holds plus releases must equal net")
        return self


class UsdOrderDay(UsdProduct):
    order_on: date
    platform_code: PlatformCode
    gross_cents: Cents
    returns_cents: Cents


class UsdStorePlatform(UsdProduct):
    platform_code: PlatformCode
    opened_on: date
    rating: float = Field(ge=1, le=5)
    review_count: int = Field(ge=0)
    gross_90d_cents: Cents
    return_90d_cents: Cents
    cancel_90d_cents: Cents
    pending_settlement_cents: Cents


class UsdPgFlow(UsdProduct):
    settlements: tuple[UsdSettlement, ...]
    orders: tuple[UsdOrderDay, ...]


class UsdMarketplace(UsdProduct):
    as_of: date
    platforms: tuple[UsdStorePlatform, ...]
    penalties: tuple[StorePenalty, ...]
