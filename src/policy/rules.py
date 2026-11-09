"""버전드 YAML 정책 규칙 모델과 로더."""

from functools import cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

RESERVED_CODES = frozenset({"R900", "R901"})
DEFAULT_RULES_PATH = Path(__file__).parent / "rulesets" / "v3.yaml"

FeatureName = Literal[
    "months_of_history",
    "dscr",
    "cash_buffer_days",
    "revenue_volatility",
    "tax_bank_match_ratio",
    "business_age_months",
    "high_cost_lender_count",
    "revenue_trend_ratio",
    "pg_top_platform_share",
    "pg_reconciliation_ratio",
]
Op = Literal["lt", "le", "gt", "ge"]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Condition(_Model):
    code: str = Field(pattern=r"^R\d{3}$")
    message: str = Field(min_length=1)
    field: FeatureName
    op: Op
    threshold: float


class ProductLimits(_Model):
    min_amount_krw: int = Field(gt=0)
    max_amount_krw: int = Field(gt=0)
    inflow_multiplier: float = Field(gt=0)

    @model_validator(mode="after")
    def _min_le_max(self) -> "ProductLimits":
        if self.min_amount_krw > self.max_amount_krw:
            raise ValueError("min_amount_krw <= max_amount_krw 이어야 한다")
        return self


class RateBand(_Model):
    band: str = Field(min_length=1)
    min_dscr: float = Field(ge=0)


class RuleSet(_Model):
    version: str = Field(min_length=1)
    product: ProductLimits
    knockouts: tuple[Condition, ...]
    refers: tuple[Condition, ...]
    rate_bands: tuple[RateBand, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistency(self) -> "RuleSet":
        codes = [c.code for c in (*self.knockouts, *self.refers)]
        if len(codes) != len(set(codes)):
            raise ValueError("규칙 코드가 중복되었다")
        if RESERVED_CODES & set(codes):
            raise ValueError("R900/R901은 엔진 예약 코드다")
        mins = [b.min_dscr for b in self.rate_bands]
        if any(a <= b for a, b in zip(mins, mins[1:])):
            raise ValueError("rate_bands는 min_dscr 기준 엄격한 내림차순이어야 한다")
        return self


def load_rules(path: Path = DEFAULT_RULES_PATH) -> RuleSet:
    with open(path, encoding="utf-8") as f:
        return RuleSet.model_validate(yaml.safe_load(f))


@cache
def _rulesets() -> dict[str, RuleSet]:
    rulesets = (load_rules(p) for p in sorted(DEFAULT_RULES_PATH.parent.glob("*.yaml")))
    return {r.version: r for r in rulesets}


def rules_for_version(version: object) -> RuleSet | None:
    """판정에 적힌 규정 버전의 규칙을 돌려준다. 모르는 버전이면 None."""
    return _rulesets().get(version) if isinstance(version, str) else None
