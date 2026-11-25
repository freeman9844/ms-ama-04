"""심사 근거 카탈로그. 분석 에이전트가 인용할 수 있는 숫자에 안정적인 ID를 붙인다.

- M:{name}      특성(CashFlowFeatures)과 결정적 신호(signals)의 숫자 값
- S:{YYYY-MM}:{field}  월별 현금흐름(revenue, operating_out, debt_service, owner_draw, index)
- P:{name}      정책 결과(max_amount_krw, {reason code}:observed|threshold)
index = 그 달 매출 / 월평균 매출(계절성 판단용, 결정적으로 계산).
grounding 검사는 이 카탈로그에 있는 ID·값만 인정한다.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from shared.product_context import product_of, validate_usd_evidence

MONTHLY_FIELDS = ("revenue", "operating_out", "debt_service", "owner_draw")


def _numeric(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def build_catalog(
    features: Mapping[str, Any],
    signals: Mapping[str, Any],
    monthly: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
    *,
    partial_usd: bool = False,
) -> dict[str, int | float]:
    catalog: dict[str, int | float] = {}
    for source in (features, signals):
        catalog.update({f"M:{k}": v for k, v in source.items() if _numeric(v)})
    if partial_usd:
        usd = True  # Only the read view inherits the application product; agent inputs stay strict.
    else:
        product = product_of(features)
        if product != product_of(policy):
            raise ValueError("Policy and feature products differ")
        usd = bool(product)
    if usd:
        validate_usd_evidence((features, signals, monthly, policy))
    suffix = "_cents" if usd else ""
    revenue = f"revenue{suffix}"
    if partial_usd and any(not _numeric(r.get(revenue)) for r in monthly):
        mean = None
    else:
        mean = sum(r[revenue] for r in monthly) / len(monthly) if monthly else 0
    for row in monthly:
        if partial_usd and not row.get("month"):
            continue
        for name in MONTHLY_FIELDS:
            name += suffix
            if not partial_usd or _numeric(row.get(name)):
                catalog[f"S:{row['month']}:{name}"] = row[name]
        if mean is not None:
            catalog[f"S:{row['month']}:index"] = round(row[revenue] / mean, 3) if mean > 0 else 0.0
    limit = "max_amount_cents" if usd else "max_amount_krw"
    if not partial_usd or _numeric(policy.get(limit)):
        catalog[f"P:{limit}"] = policy[limit]
    reasons = policy.get("reason_codes", ())
    for reason in (reasons or ()) if partial_usd else reasons:
        for name in ("observed", "threshold"):
            if _numeric(reason.get(name)):
                catalog[f"P:{reason['code']}:{name}"] = reason[name]
    return catalog
