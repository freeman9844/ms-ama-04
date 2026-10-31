"""현금흐름 특성 계산(결정적). Policy Engine 입력과 심사 근거 신호를 만든다.

- 집계 구간: 기준일(as_of)이 속한 달보다 앞선 '완결 월' 전체.
- DSCR = max(월평균 영업순현금, 0) / (월평균 기존 부채상환 + 신규 대출 월상환액 추정).
- 현금 완충일수 = 최근 30일 거래 후 잔액 평균 / 일평균 영업 지출.
- 세금 일치율 = min(신고 매출, 통장 매출) / max(...), 신고 기간 안의 통장 매출만 합산.
- 의미 분류 override(P4a)는 규칙 미분류 출금에만 적용한다. DSCR·완충일수는 규칙만/의미 반영 중
  작은 값을 쓴다(모델 판단이 정책 지표를 좋게 만들 수 없다). 위험 신호·플래그는 의미 반영 결과를 쓴다.
"""

import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from agents.classify import RULES_VERSION, Category, classify
from shared.contracts import ApplicantProfile, CashFlowFeatures, TaxFiling, Transaction

ANNUAL_RATE = 0.08  # 신규 대출 월상환액 추정용 가정 금리(원리금 균등)
DAYS_PER_MONTH = 30.4
BUFFER_LOOKBACK_DAYS = 30
OPERATING_OUT = (
    Category.TAX,
    Category.SOCIAL_INSURANCE,
    Category.RENT,
    Category.PAYROLL,
    Category.UNCLASSIFIED_OUT,
)
DEBT_SERVICE = (Category.LOAN_REPAYMENT, Category.HIGH_COST_LENDER)
FLAG_HIGH_COST_LENDER = "high_cost_lender_detected"


@dataclass(frozen=True)
class Underwriting:
    features: CashFlowFeatures
    signals: dict[str, int | float | str]
    flags: list[str]
    monthly: list[dict[str, int | str]]
    categories: dict[str, Category]

def monthly_installment(principal: int, tenor_months: int, annual_rate: float = ANNUAL_RATE) -> float:
    r = annual_rate / 12
    return principal * r / (1 - (1 + r) ** -tenor_months)


def _month(d: date) -> tuple[int, int]:
    return (d.year, d.month)


def _tax_match(transactions: Sequence[Transaction], categories: dict[str, Category], filings: Sequence[TaxFiling]) -> float:
    if not filings:
        return 0.0
    declared = sum(f.declared_revenue_krw for f in filings)
    observed = sum(
        t.amount_krw
        for f in filings
        for t in transactions
        if categories[t.txn_id] is Category.REVENUE and f.period_start <= t.booked_on <= f.period_end
    )
    high = max(declared, observed)
    return min(declared, observed) / high if high > 0 else 0.0


def _compute(
    application_id: str,
    profile: ApplicantProfile,
    transactions: Sequence[Transaction],
    tax_filings: Sequence[TaxFiling],
    categories: dict[str, Category],
    *,
    as_of: date,
    requested_amount_krw: int,
    requested_tenor_months: int,
) -> Underwriting:
    current = _month(as_of)
    months = sorted({_month(t.booked_on) for t in transactions if _month(t.booked_on) < current})
    totals: dict[tuple[int, int], dict[Category, int]] = defaultdict(lambda: defaultdict(int))
    for t in transactions:
        if _month(t.booked_on) < current:
            totals[_month(t.booked_on)][categories[t.txn_id]] += t.amount_krw

    n = len(months)
    new_installment = monthly_installment(requested_amount_krw, requested_tenor_months)

    def monthly_avg(cats: Sequence[Category]) -> float:
        return sum(totals[m][c] for m in months for c in cats) / n if n else 0.0

    revenue = [totals[m][Category.REVENUE] for m in months]
    noi = monthly_avg((Category.REVENUE, *OPERATING_OUT))
    debt = -monthly_avg(DEBT_SERVICE)
    owner = -monthly_avg((Category.OWNER_TRANSFER,))
    dscr = max(noi, 0.0) / (debt + new_installment) if n else 0.0
    mean_rev = statistics.fmean(revenue) if n else 0.0
    volatility = statistics.pstdev(revenue) / mean_rev if mean_rev > 0 else 0.0

    recent = [
        t.balance_after_krw
        for t in transactions
        if as_of - timedelta(days=BUFFER_LOOKBACK_DAYS) <= t.booked_on < as_of
    ]
    operating_daily = -monthly_avg(OPERATING_OUT) / DAYS_PER_MONTH
    buffer = max(statistics.fmean(recent) / operating_daily, 0.0) if recent and operating_daily > 0 else 0.0
    match = _tax_match(transactions, categories, tax_filings) if n else 0.0

    features = CashFlowFeatures(
        application_id=application_id,
        months_of_history=n,
        avg_monthly_inflow_krw=int(sum(revenue) / n) if n else 0,
        dscr=round(float(dscr), 3),
        cash_buffer_days=round(float(buffer), 1),
        revenue_volatility=round(float(volatility), 3),
        tax_bank_match_ratio=round(float(match), 3),
        requested_amount_krw=requested_amount_krw,
        requested_tenor_months=requested_tenor_months,
        industry=profile.industry,
        region=profile.region,
        business_age_months=profile.business_age_months,
    )
    counts = defaultdict(int)
    for c in categories.values():
        counts[c] += 1
    signals: dict[str, int | float | str] = {
        "classification_rules": RULES_VERSION,
        "months_used": n,
        "avg_monthly_noi_krw": int(noi),
        "avg_monthly_debt_service_krw": int(debt),
        "new_installment_krw": int(new_installment),
        "avg_monthly_owner_draw_krw": int(owner),
        "dscr_after_owner_draw": round(max(noi - owner, 0.0) / (debt + new_installment), 3) if n else 0.0,
        "unclassified_count": counts[Category.UNCLASSIFIED_IN] + counts[Category.UNCLASSIFIED_OUT],
        "high_cost_lender_count": counts[Category.HIGH_COST_LENDER],
        "loan_proceeds_count": counts[Category.LOAN_PROCEEDS],
    }
    flags = [FLAG_HIGH_COST_LENDER] if counts[Category.HIGH_COST_LENDER] else []
    monthly: list[dict[str, int | str]] = [
        {
            "month": f"{y:04d}-{mo:02d}",
            "revenue": totals[(y, mo)][Category.REVENUE],
            "operating_out": -sum(totals[(y, mo)][c] for c in OPERATING_OUT),
            "debt_service": -sum(totals[(y, mo)][c] for c in DEBT_SERVICE),
            "owner_draw": -totals[(y, mo)][Category.OWNER_TRANSFER],
        }
        for y, mo in months
    ]
    return Underwriting(features=features, signals=signals, flags=flags, monthly=monthly, categories=categories)


def compute_underwriting(
    application_id: str,
    profile: ApplicantProfile,
    transactions: Sequence[Transaction],
    tax_filings: Sequence[TaxFiling],
    *,
    as_of: date,
    requested_amount_krw: int,
    requested_tenor_months: int,
    overrides: Mapping[str, Category] | None = None,
) -> Underwriting:
    rules = {t.txn_id: classify(t) for t in transactions}
    args = (application_id, profile, transactions, tax_filings)
    kw = {"as_of": as_of, "requested_amount_krw": requested_amount_krw, "requested_tenor_months": requested_tenor_months}
    base = _compute(*args, rules, **kw)
    applied = {
        k: v
        for k, v in (overrides or {}).items()
        if rules.get(k) in (Category.UNCLASSIFIED_IN, Category.UNCLASSIFIED_OUT)
    }
    adjusted = _compute(*args, rules | applied, **kw) if applied else base
    features = _conservative(base.features, adjusted.features)
    signals = {
        **adjusted.signals,
        "semantic_overrides": len(applied),
        "dscr_rules_only": base.features.dscr,
        "dscr_semantic": adjusted.features.dscr,
    }
    return Underwriting(
        features=features,
        signals=signals,
        flags=adjusted.flags,
        monthly=adjusted.monthly,
        categories=adjusted.categories,
    )


def _conservative(base: CashFlowFeatures, adjusted: CashFlowFeatures) -> CashFlowFeatures:
    return base.model_copy(
        update={
            "dscr": min(base.dscr, adjusted.dscr),
            "cash_buffer_days": min(base.cash_buffer_days, adjusted.cash_buffer_days),
        }
    )