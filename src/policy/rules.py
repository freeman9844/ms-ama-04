"""버전드 YAML 정책 규칙 모델과 로더."""

from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Literal, get_args

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.contracts import MKT_FEATURES, UsdCashFlowFeatures, UsdRulesVersion

RESERVED_CODES = frozenset({"R900", "R901"})
# 상환 능력(R002)과 플랫폼 제재(R003)는 셀러 평판으로 덮지 않는다.
NON_SUBSTITUTABLE = frozenset({"R002", "R003"}) | RESERVED_CODES
DEFAULT_RULES_PATH = Path(__file__).parent / "rulesets" / "v4.yaml"

FeatureName = Literal[
    *MKT_FEATURES,
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
    "pg_cash_conversion_days",
    "pg_advance_suspected_count",
    "pg_offset_rate_trend_pp",
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


class SubCondition(_Model):
    field: FeatureName
    op: Op
    threshold: float


class Substitution(_Model):
    code: str = Field(pattern=r"^S\d{3}$")
    message: str = Field(min_length=1)
    replaces: str = Field(pattern=r"^R\d{3}$")
    outcome: Literal["refer", "pass"]
    when: tuple[SubCondition, ...] = Field(min_length=1)


class LimitBoost(_Model):
    pending_settlement_ratio: float = Field(gt=0, le=1)
    substituted_max_amount_krw: int = Field(gt=0)


class UsdLimits(_Model):
    min_amount_cents: int = Field(gt=0)
    max_amount_cents: int = Field(gt=0)
    limit_unit_cents: int = Field(gt=0)
    inflow_multiplier: float = Field(gt=0)


class UsdBoost(_Model):
    pending_settlement_ratio: float = Field(gt=0, le=1)
    substituted_max_amount_cents: int = Field(gt=0)


class RuleSet(_Model):
    version: str = Field(min_length=1)
    product: ProductLimits
    knockouts: tuple[Condition, ...]
    refers: tuple[Condition, ...]
    rate_bands: tuple[RateBand, ...] = Field(min_length=1)
    substitutions: tuple[Substitution, ...] = ()
    limit_boost: LimitBoost | None = None

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
        self._check_substitutions({c.code for c in self.knockouts}, set(codes))
        return self

    def _check_substitutions(self, knockout_codes: set[str], rule_codes: set[str]) -> None:
        sub_codes = [s.code for s in self.substitutions]
        if len(sub_codes) != len(set(sub_codes)):
            raise ValueError("대체 코드가 중복되었다")
        if any(s.replaces in knockout_codes for s in self.substitutions) and self.limit_boost is None:
            raise ValueError("탈락 규칙을 대체하는 규칙셋에는 limit_boost가 필요하다")
        targets = [s.replaces for s in self.substitutions]
        if len(targets) != len(set(targets)):
            raise ValueError("한 규칙을 두 대체가 겨냥한다")
        for s in self.substitutions:
            if s.replaces not in rule_codes:
                raise ValueError(f"{s.code}: replaces {s.replaces}는 이 규칙셋의 탈락·회부 규칙이 아니다")
            if s.replaces in NON_SUBSTITUTABLE:
                raise ValueError(f"{s.code}: {s.replaces}는 대체할 수 없다")
            if s.replaces in knockout_codes and s.outcome != "refer":
                raise ValueError(f"{s.code}: 탈락 규칙의 대체는 refer만 가능하다")


class UsdRuleSet(RuleSet):
    version: UsdRulesVersion
    product: UsdLimits
    limit_boost: UsdBoost
    # 대안 데이터(PG·마켓) 지표. 있으면 은행 거래만 본 비교 판정(cash_only)이 이 목록을 지운다.
    alt_data_features: tuple[str, ...] | None = None

    @model_validator(mode="after")
    def _alt_data_are_optional_usd_fields(self) -> "UsdRuleSet":
        fields = UsdCashFlowFeatures.model_fields
        for name in self.alt_data_features or ():
            if name not in fields or fields[name].is_required() or fields[name].default is not None:
                raise ValueError(f"alt_data_features: {name}는 UsdCashFlowFeatures의 선택(None 허용) 필드가 아니다")
        return self


def load_rules(path: Path = DEFAULT_RULES_PATH) -> RuleSet:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return (UsdRuleSet if isinstance(data, dict) and is_usd_rules(data.get("version")) else RuleSet).model_validate(data)


@cache
def _rulesets() -> dict[str, RuleSet]:
    rulesets = (load_rules(p) for p in sorted(DEFAULT_RULES_PATH.parent.glob("*.yaml")))
    return {r.version: r for r in rulesets}


# 버전 이름을 그대로 둔 채 내용을 고친 규정과 그 시각(커밋 6ff03ee: v1 상품 한도 300만~3,000만 → 50만~500만).
# 이 시각 전에 접수된 판정은 지금 파일과 다른 규정으로 났을 수 있다.
AMENDED_IN_PLACE: dict[str, datetime] = {"2026.11-v1": datetime.fromisoformat("2026-11-04T01:29:11+09:00")}


def rules_for_version(version: object) -> RuleSet | None:
    """판정에 적힌 규정 버전의 규칙을 돌려준다. 모르는 버전이면 None."""
    return _rulesets().get(version) if isinstance(version, str) else None


# 상품 버전 → 새 신청을 판정하는 현행 규정 버전. 저장된 판정은 자기 rules_version으로 다시 설명한다.
USD_CURRENT_RULES: dict[str, str] = {"usd-v1": "usd-v2"}


def is_usd_rules(version: object) -> bool:
    return isinstance(version, str) and version in get_args(UsdRulesVersion)


def current_usd_rules(product_version: str) -> UsdRuleSet:
    """USD 상품 버전의 현행 규칙. 모르는 상품 버전이면 KeyError."""
    rules = rules_for_version(USD_CURRENT_RULES[product_version])
    assert isinstance(rules, UsdRuleSet)
    return rules
