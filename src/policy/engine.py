"""결정론적 정책 평가. LLM은 관여하지 않는다.

`explain()`이 규칙별 결과·대체·한도 산식·구간 결정을 만들고, `evaluate()`는 그 판정만 돌려준다(한 코드 경로).
"""

import operator
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Literal

from policy.rules import NON_SUBSTITUTABLE, Condition, RuleSet, SubCondition
from policy.rules import UsdRuleSet
from shared.contracts import USD_V1, UsdCashFlowFeatures, UsdCashOnlyDecision, UsdPolicyDecision
from shared.contracts import MKT_FEATURES, CashFlowFeatures, CashOnlyDecision, Decision, PolicyDecision, ReasonCode

_OPS = {"lt": operator.lt, "le": operator.le, "gt": operator.gt, "ge": operator.ge}
LIMIT_UNIT_KRW = 100_000


@dataclass(frozen=True)
class RuleCheck:
    code: str
    message: str
    kind: Literal["knockout", "refer", "engine", "substitution"]
    field: str
    op: str
    observed: float | None
    threshold: float
    result: Literal["hit", "pass", "not_applied", "substituted"]

    def reason(self) -> ReasonCode:
        return ReasonCode(code=self.code, message=self.message, field=self.field,
                          observed=self.observed, threshold=self.threshold)


@dataclass(frozen=True)
class SubstitutionTrace:
    code: str
    message: str
    replaces: str
    outcome: Literal["refer", "pass"]
    target_hit: bool
    conditions: tuple[RuleCheck, ...]
    conditions_met: bool
    blocked_by: tuple[str, ...]
    applied: bool

    def reason(self) -> ReasonCode:
        first = self.conditions[0]
        return ReasonCode(code=self.code, message=self.message, field=first.field,
                          observed=first.observed, threshold=first.threshold)


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
    pending_settlement_ratio: float | None = None
    settlement_cap_krw: int = 0
    substituted_cap_krw: int | None = None


@dataclass(frozen=True)
class BandTrace:
    dscr: float
    bands: tuple[tuple[str, float], ...]
    band: str | None


@dataclass(frozen=True)
class UsdLimitTrace:
    requested_amount_cents: int
    avg_monthly_inflow_cents: int
    inflow_multiplier: float
    inflow_cap_cents: int
    product_max_cents: int
    product_min_cents: int
    before_rounding_cents: int
    unit_cents: int
    limit_cents: int
    pending_settlement_ratio: float
    settlement_cap_cents: int
    substituted_cap_cents: int | None


@dataclass(frozen=True)
class Explanation:
    rules_version: str
    checks: tuple[RuleCheck, ...]
    limit: LimitTrace | UsdLimitTrace
    band: BandTrace
    decision: PolicyDecision | UsdPolicyDecision
    substitutions: tuple[SubstitutionTrace, ...] = ()


def _check(cond: Condition | SubCondition, kind: Literal["knockout", "refer", "substitution"],
           features: CashFlowFeatures, code: str | None = None, message: str | None = None) -> RuleCheck:
    value = getattr(features, cond.field)
    # 선택 지표가 없으면 규칙을 적용하지 않는다(정책 v3)
    observed = None if value is None else float(value)
    if observed is None:
        result = "not_applied"
    else:
        result = "hit" if _OPS[cond.op](observed, cond.threshold) else "pass"
    return RuleCheck(code or cond.code, message or cond.message, kind, cond.field, cond.op, observed,
                     cond.threshold, result)


def explain(features: CashFlowFeatures | UsdCashFlowFeatures, rules: RuleSet) -> Explanation:
    usd = isinstance(features, UsdCashFlowFeatures)
    if usd != isinstance(rules, UsdRuleSet):
        raise ValueError("Feature and ruleset products do not match")
    knockouts = [_check(c, "knockout", features) for c in rules.knockouts]
    refers = [_check(c, "refer", features) for c in rules.refers]
    by_code = {c.code: c for c in (*knockouts, *refers)}
    knockout_codes = {c.code for c in knockouts}

    band = next((b.band for b in rules.rate_bands if features.dscr >= b.min_dscr), None)
    band_trace = BandTrace(features.dscr, tuple((b.band, b.min_dscr) for b in rules.rate_bands), band)
    r901 = RuleCheck("R901", "No applicable rate band" if usd else "해당하는 금리 밴드 없음", "engine", "dscr", "lt", features.dscr,
                     rules.rate_bands[-1].min_dscr, "hit" if band is None else "pass")

    # 대체할 수 없는 탈락이 하나라도 걸리면 어떤 대체도 적용하지 않는다(스펙 8.3)
    blocking = [c.code for c in (*knockouts, r901) if c.result == "hit" and c.code in NON_SUBSTITUTABLE]
    candidates = []
    for s in rules.substitutions:
        conds = tuple(_check(c, "substitution", features, s.code, s.message) for c in s.when)
        target_hit = by_code[s.replaces].result == "hit"
        met = all(c.result == "hit" for c in conds)
        candidates.append((s, conds, target_hit, met))
    tentative = [s for s, _, target_hit, met in candidates if target_hit and met and not blocking]

    product = rules.product
    boost = rules.limit_boost
    if usd:
        requested, avg = features.requested_amount_cents, features.avg_monthly_inflow_cents
        pending = features.mkt_pending_settlement_cents
        low, high, unit = product.min_amount_cents, product.max_amount_cents, product.limit_unit_cents
    else:
        requested, avg = features.requested_amount_krw, features.avg_monthly_inflow_krw
        pending = features.mkt_pending_settlement_krw
        low, high, unit = product.min_amount_krw, product.max_amount_krw, LIMIT_UNIT_KRW
    def cap(amount, ratio):
        return int(amount * (Decimal(str(ratio)) if usd else ratio))
    inflow_cap = cap(avg, product.inflow_multiplier)
    settlement_cap = cap(pending, boost.pending_settlement_ratio) if boost and pending is not None else 0
    before = min(requested, max(inflow_cap, settlement_cap), high)
    substituted_cap = None
    if boost and any(s.replaces in knockout_codes for s in tentative):
        substituted_cap = boost.substituted_max_amount_cents if usd else boost.substituted_max_amount_krw
        before = min(before, substituted_cap)
    limit = before - before % unit
    limit_trace = (UsdLimitTrace if usd else LimitTrace)(
        requested, avg, product.inflow_multiplier, inflow_cap, high, low, before, unit, limit,
        boost.pending_settlement_ratio if boost else None, settlement_cap, substituted_cap)
    amount_key = "max_amount_cents" if usd else "max_amount_krw"
    r900 = RuleCheck("R900", "Limit below product minimum" if usd else "산출 한도가 상품 최소 금액 미만",
                     "engine", amount_key, "lt", float(limit), float(low), "hit" if limit < low else "pass")
    if r900.result == "hit":
        # 한도 trace는 그대로 둔다. R900이면 어차피 탈락이다.
        blocking.append("R900")

    traces = tuple(
        SubstitutionTrace(s.code, s.message, s.replaces, s.outcome, target_hit, conds, met,
                          tuple(blocking) if target_hit and met else (),
                          target_hit and met and not blocking)
        for s, conds, target_hit, met in candidates
    )
    applied = [t for t in traces if t.applied]
    substituted = {t.replaces for t in applied}
    knockouts = [replace(c, result="substituted") if c.code in substituted else c for c in knockouts]
    refers = [replace(c, result="substituted") if c.code in substituted else c for c in refers]

    checks = (*knockouts, *refers, r900, r901)
    ko_hits = [c.reason() for c in (*knockouts, r900, r901) if c.result == "hit"]
    refer_hits = [t.reason() for t in applied if t.outcome == "refer"]
    refer_hits += [c.reason() for c in refers if c.result == "hit"]
    cash_only = _cash_only(features, rules)
    decision = (UsdPolicyDecision if usd else PolicyDecision)(
        application_id=features.application_id,
        decision=Decision.INELIGIBLE if ko_hits else Decision.REFER if refer_hits else Decision.ELIGIBLE,
        rate_band=None if ko_hits else band, reason_codes=tuple(ko_hits + refer_hits),
        rules_version=rules.version, cash_only=cash_only, **{amount_key: 0 if ko_hits else limit},
        **(USD_V1.model_dump() if usd else {}))
    return Explanation(rules.version, checks, limit_trace, band_trace, decision, traces)


def _cash_only(features: CashFlowFeatures | UsdCashFlowFeatures, rules: RuleSet) -> CashOnlyDecision | UsdCashOnlyDecision | None:
    """마켓 자료를 모두 지운 같은 신청의 판정. 마켓 자료가 없으면 비교할 것이 없다."""
    keys = (*MKT_FEATURES[:-1], "mkt_pending_settlement_cents") if isinstance(features, UsdCashFlowFeatures) else MKT_FEATURES
    if all(getattr(features, k) is None for k in keys):
        return None
    cash = explain(features.model_copy(update=dict.fromkeys(keys)), rules).decision
    if isinstance(cash, UsdPolicyDecision):
        return UsdCashOnlyDecision(**USD_V1.model_dump(), decision=cash.decision, max_amount_cents=cash.max_amount_cents,
                                   reason_codes=cash.reason_codes)
    return CashOnlyDecision(decision=cash.decision, max_amount_krw=cash.max_amount_krw,
                            reason_codes=cash.reason_codes)


def evaluate(features: CashFlowFeatures | UsdCashFlowFeatures, rules: RuleSet) -> PolicyDecision | UsdPolicyDecision:
    return explain(features, rules).decision


def decision_payload(decision: PolicyDecision | UsdPolicyDecision) -> dict:
    """정책 API 응답. 마켓 자료가 없으면 `cash_only` 키를 빼 v4 이전 응답과 같은 모양을 유지한다(스펙 8.5)."""
    payload = decision.model_dump(mode="json")
    if payload["cash_only"] is None:
        del payload["cash_only"]
    return payload
