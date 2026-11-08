"""결정론적 정책 평가. LLM은 관여하지 않는다."""

import operator

from policy.rules import Condition, RuleSet
from shared.contracts import CashFlowFeatures, Decision, PolicyDecision, ReasonCode

_OPS = {"lt": operator.lt, "le": operator.le, "gt": operator.gt, "ge": operator.ge}
_LIMIT_UNIT_KRW = 100_000


def _check(cond: Condition, features: CashFlowFeatures) -> ReasonCode | None:
    value = getattr(features, cond.field)
    if value is None:  # 선택 지표가 없으면 규칙을 적용하지 않는다(정책 v3)
        return None
    observed = float(value)
    if not _OPS[cond.op](observed, cond.threshold):
        return None
    return ReasonCode(
        code=cond.code,
        message=cond.message,
        field=cond.field,
        observed=observed,
        threshold=cond.threshold,
    )


def evaluate(features: CashFlowFeatures, rules: RuleSet) -> PolicyDecision:
    knockouts = [r for c in rules.knockouts if (r := _check(c, features)) is not None]
    refers = [r for c in rules.refers if (r := _check(c, features)) is not None]

    product = rules.product
    limit = min(
        features.requested_amount_krw,
        int(features.avg_monthly_inflow_krw * product.inflow_multiplier),
        product.max_amount_krw,
    )
    limit -= limit % _LIMIT_UNIT_KRW
    if limit < product.min_amount_krw:
        knockouts.append(
            ReasonCode(
                code="R900",
                message="산출 한도가 상품 최소 금액 미만",
                field="max_amount_krw",
                observed=float(limit),
                threshold=float(product.min_amount_krw),
            )
        )

    band = next((b.band for b in rules.rate_bands if features.dscr >= b.min_dscr), None)
    if band is None:
        knockouts.append(
            ReasonCode(
                code="R901",
                message="해당하는 금리 밴드 없음",
                field="dscr",
                observed=features.dscr,
                threshold=rules.rate_bands[-1].min_dscr,
            )
        )

    if knockouts:
        return PolicyDecision(
            application_id=features.application_id,
            decision=Decision.INELIGIBLE,
            max_amount_krw=0,
            rate_band=None,
            reason_codes=tuple(knockouts + refers),
            rules_version=rules.version,
        )
    return PolicyDecision(
        application_id=features.application_id,
        decision=Decision.REFER if refers else Decision.ELIGIBLE,
        max_amount_krw=limit,
        rate_band=band,
        reason_codes=tuple(refers),
        rules_version=rules.version,
    )
