"""결정론적 정책 평가. LLM은 관여하지 않는다.

`explain()`이 규칙별 결과·한도 산식·구간 결정을 만들고, `evaluate()`는 그 판정만 돌려준다(한 코드 경로).
"""

import operator
from dataclasses import dataclass
from typing import Literal

from policy.rules import Condition, RuleSet
from shared.contracts import CashFlowFeatures, Decision, PolicyDecision, ReasonCode

_OPS = {"lt": operator.lt, "le": operator.le, "gt": operator.gt, "ge": operator.ge}
LIMIT_UNIT_KRW = 100_000


@dataclass(frozen=True)
class RuleCheck:
    code: str
    message: str
    kind: Literal["knockout", "refer", "engine"]
    field: str
    op: str
    observed: float | None
    threshold: float
    result: Literal["hit", "pass", "not_applied"]

    def reason(self) -> ReasonCode:
        return ReasonCode(code=self.code, message=self.message, field=self.field,
                          observed=self.observed, threshold=self.threshold)


@dataclass(frozen=True)
class LimitTrace:
    requested_amount_krw: int
    avg_monthly_inflow_krw: int
    inflow_multiplier: float
    inflow_cap_krw: int
    product_max_krw: int
    product_min_krw: int
    before_rounding_krw: int
    unit_krw: int
    limit_krw: int


@dataclass(frozen=True)
class BandTrace:
    dscr: float
    bands: tuple[tuple[str, float], ...]
    band: str | None


@dataclass(frozen=True)
class Explanation:
    rules_version: str
    checks: tuple[RuleCheck, ...]
    limit: LimitTrace
    band: BandTrace
    decision: PolicyDecision


def _check(cond: Condition, kind: Literal["knockout", "refer"], features: CashFlowFeatures) -> RuleCheck:
    value = getattr(features, cond.field)
    # 선택 지표가 없으면 규칙을 적용하지 않는다(정책 v3)
    observed = None if value is None else float(value)
    if observed is None:
        result = "not_applied"
    else:
        result = "hit" if _OPS[cond.op](observed, cond.threshold) else "pass"
    return RuleCheck(cond.code, cond.message, kind, cond.field, cond.op, observed, cond.threshold, result)


def explain(features: CashFlowFeatures, rules: RuleSet) -> Explanation:
    knockouts = [_check(c, "knockout", features) for c in rules.knockouts]
    refers = [_check(c, "refer", features) for c in rules.refers]

    product = rules.product
    inflow_cap = int(features.avg_monthly_inflow_krw * product.inflow_multiplier)
    before = min(features.requested_amount_krw, inflow_cap, product.max_amount_krw)
    limit = before - before % LIMIT_UNIT_KRW
    limit_trace = LimitTrace(
        requested_amount_krw=features.requested_amount_krw,
        avg_monthly_inflow_krw=features.avg_monthly_inflow_krw,
        inflow_multiplier=product.inflow_multiplier,
        inflow_cap_krw=inflow_cap,
        product_max_krw=product.max_amount_krw,
        product_min_krw=product.min_amount_krw,
        before_rounding_krw=before,
        unit_krw=LIMIT_UNIT_KRW,
        limit_krw=limit,
    )
    r900 = RuleCheck("R900", "산출 한도가 상품 최소 금액 미만", "engine", "max_amount_krw", "lt", float(limit),
                     float(product.min_amount_krw), "hit" if limit < product.min_amount_krw else "pass")

    band = next((b.band for b in rules.rate_bands if features.dscr >= b.min_dscr), None)
    band_trace = BandTrace(features.dscr, tuple((b.band, b.min_dscr) for b in rules.rate_bands), band)
    r901 = RuleCheck("R901", "해당하는 금리 밴드 없음", "engine", "dscr", "lt", features.dscr,
                     rules.rate_bands[-1].min_dscr, "hit" if band is None else "pass")

    checks = (*knockouts, *refers, r900, r901)
    ko_hits = [c.reason() for c in (*knockouts, r900, r901) if c.result == "hit"]
    refer_hits = [c.reason() for c in refers if c.result == "hit"]
    if ko_hits:
        decision = PolicyDecision(
            application_id=features.application_id,
            decision=Decision.INELIGIBLE,
            max_amount_krw=0,
            rate_band=None,
            reason_codes=tuple(ko_hits + refer_hits),
            rules_version=rules.version,
        )
    else:
        decision = PolicyDecision(
            application_id=features.application_id,
            decision=Decision.REFER if refer_hits else Decision.ELIGIBLE,
            max_amount_krw=limit,
            rate_band=band,
            reason_codes=tuple(refer_hits),
            rules_version=rules.version,
        )
    return Explanation(rules.version, checks, limit_trace, band_trace, decision)


def evaluate(features: CashFlowFeatures, rules: RuleSet) -> PolicyDecision:
    return explain(features, rules).decision
