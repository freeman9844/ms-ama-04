"""대출 사후 모니터링 계산(결정적): 기준선, 주간 계열, 통계 탐지기, 근거 카탈로그.

- 기준선은 심사 시점 underwriting 결과(features·monthly)에서 만든다. 원천 거래는 다시 읽지 않는다.
- 주차 n은 흐름 시작일부터 7일 단위다(n=1이 첫 7일).
- 탐지기는 일부러 단순하다(계절 보정 없음). 오탐을 거르는 일은 Early-Warning 에이전트의 몫이다(스펙 §3.3).
- 근거 ID: W:{주차}:{필드}, B:{기준선 이름}, B:index:{MM}, B:cohort:{이름}.
"""

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any

from agents.classify import Category, classify
from agents.features import DAYS_PER_MONTH, OPERATING_OUT
from shared.contracts import Transaction

WEEKS = 13
INFLOW_DROP_RATIO = 0.85
BUFFER_DROP_RATIO = 0.5
VOLATILITY_WEEKS = 4
VOLATILITY_COV = 0.15
TRIGGERS = ("inflow_drop", "buffer_drop", "volatility_spike", "new_debt")
WEEK_FIELDS = (
    "revenue",
    "revenue_ratio",
    "operating_out",
    "debt_service",
    "high_cost_lender",
    "high_cost_count",
    "loan_proceeds",
    "unclassified_in",
    "end_balance",
    "buffer_days",
)
_UNCLASSIFIED = (Category.UNCLASSIFIED_IN, Category.UNCLASSIFIED_OUT)

# 합성 업종 집단 통계(데모용 고정 표). 실제 운영에서는 집단 통계 저장소에서 읽는다.
_DEFAULT_COHORT = {"index": dict.fromkeys(range(1, 13), 1.0), "high_cost_lender_share": 0.05, "median_buffer_days": 40.0}


def _flat(peaks: dict[int, float] | None = None) -> dict[int, float]:
    return {**dict.fromkeys(range(1, 13), 1.0), **(peaks or {})}


# 온라인 셀러 업종 10개. 데모 페르소나(패션의류·식품·뷰티·핸드메이드)는 P8 이전 업종(카페·분식·한식·제과점)의 값을 그대로 쓴다.
PEER_COHORTS: dict[str, dict[str, Any]] = {
    "패션의류": {
        "index": {1: 0.82, 2: 0.84, 3: 0.95, 4: 1.0, 5: 1.02, 6: 1.08, 7: 1.32, 8: 1.35, 9: 1.0, 10: 0.98, 11: 0.97, 12: 1.15},
        "high_cost_lender_share": 0.06,
        "median_buffer_days": 45.0,
    },
    "식품": {"index": _flat({1: 0.9, 2: 0.9}), "high_cost_lender_share": 0.08, "median_buffer_days": 30.0},
    "뷰티": {"index": _flat({12: 1.2}), "high_cost_lender_share": 0.07, "median_buffer_days": 35.0},
    "핸드메이드": {"index": _flat({12: 1.25}), "high_cost_lender_share": 0.05, "median_buffer_days": 40.0},
    "리빙": {"index": _flat({3: 1.1, 9: 1.1}), "high_cost_lender_share": 0.05, "median_buffer_days": 40.0},
    "디지털": {"index": _flat({11: 1.3, 12: 1.2}), "high_cost_lender_share": 0.06, "median_buffer_days": 35.0},
    "반려동물": {"index": _flat(), "high_cost_lender_share": 0.04, "median_buffer_days": 45.0},
    "유아동": {"index": _flat({5: 1.3, 12: 1.2}), "high_cost_lender_share": 0.05, "median_buffer_days": 40.0},
    "스포츠": {"index": _flat({6: 1.15, 7: 1.2, 8: 1.2, 12: 0.85}), "high_cost_lender_share": 0.06, "median_buffer_days": 35.0},
    "도서·문구": {"index": _flat({2: 1.3, 3: 1.3, 8: 1.1}), "high_cost_lender_share": 0.05, "median_buffer_days": 40.0},
}


@dataclass(frozen=True)
class Baseline:
    avg_weekly_revenue: int
    revenue_volatility: float
    cash_buffer_days: float
    operating_daily_krw: int
    tax_bank_match_ratio: float
    seasonal_index: dict[int, float]

    @classmethod
    def from_underwriting(cls, underwriting: Mapping[str, Any]) -> "Baseline":
        f, monthly = underwriting["features"], underwriting["monthly"]
        mean_rev = statistics.fmean(r["revenue"] for r in monthly) if monthly else 0.0
        index = {m: 1.0 for m in range(1, 13)}
        for r in monthly:
            if mean_rev > 0:
                index[int(r["month"][5:7])] = round(r["revenue"] / mean_rev, 3)
        op = statistics.fmean(r["operating_out"] for r in monthly) if monthly else 0.0
        return cls(
            avg_weekly_revenue=int(f["avg_monthly_inflow_krw"] * 7 / DAYS_PER_MONTH),
            revenue_volatility=float(f["revenue_volatility"]),
            cash_buffer_days=float(f["cash_buffer_days"]),
            operating_daily_krw=int(op / DAYS_PER_MONTH),
            tax_bank_match_ratio=float(f["tax_bank_match_ratio"]),
            seasonal_index=index,
        )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["seasonal_index"] = {f"{m:02d}": v for m, v in self.seasonal_index.items()}
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Baseline":
        return cls(**{**d, "seasonal_index": {int(k): float(v) for k, v in d["seasonal_index"].items()}})


def cohort(industry: str) -> dict[str, Any]:
    return PEER_COHORTS.get(industry, _DEFAULT_COHORT)


def week_window(start: date, week: int) -> tuple[date, date]:
    first = start + timedelta(days=7 * (week - 1))
    return first, first + timedelta(days=6)


def categorize(transactions: Sequence[Transaction], overrides: Mapping[str, str | Category]) -> dict[str, Category]:
    """규칙 분류 + 의미 분류 override(규칙 미분류 거래에만 적용, features와 같은 원칙)."""
    out: dict[str, Category] = {}
    for t in transactions:
        rule = classify(t)
        override = overrides.get(t.txn_id)
        out[t.txn_id] = Category(override) if override and rule in _UNCLASSIFIED else rule
    return out


def weekly_series(
    transactions: Sequence[Transaction],
    categories: Mapping[str, Category],
    start: date,
    upto_week: int,
    baseline: Baseline,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    balance = None
    for week in range(1, upto_week + 1):
        lo, hi = week_window(start, week)
        txns = [t for t in transactions if lo <= t.booked_on <= hi]

        def total(*cats: Category) -> int:
            return sum(t.amount_krw for t in txns if categories[t.txn_id] in cats)

        if txns:
            balance = txns[-1].balance_after_krw
        revenue = total(Category.REVENUE)
        end_balance = balance if balance is not None else 0
        rows.append(
            {
                "week": week,
                "start": lo.isoformat(),
                "end": hi.isoformat(),
                "revenue": revenue,
                "revenue_ratio": round(revenue / baseline.avg_weekly_revenue, 3) if baseline.avg_weekly_revenue else 0.0,
                "operating_out": -total(*OPERATING_OUT),
                "debt_service": -total(Category.LOAN_REPAYMENT, Category.HIGH_COST_LENDER),
                "high_cost_lender": -total(Category.HIGH_COST_LENDER),
                "high_cost_count": sum(1 for t in txns if categories[t.txn_id] is Category.HIGH_COST_LENDER),
                "loan_proceeds": total(Category.LOAN_PROCEEDS),
                "unclassified_in": total(Category.UNCLASSIFIED_IN),
                "end_balance": end_balance,
                "buffer_days": round(max(end_balance, 0) / baseline.operating_daily_krw, 1)
                if baseline.operating_daily_krw
                else 0.0,
            }
        )
    return rows


def detect(rows: Sequence[Mapping[str, Any]], baseline: Baseline) -> list[str]:
    """마지막 주차에 걸린 트리거(TRIGGERS 순서)."""
    if not rows:
        return []
    row = rows[-1]
    out: list[str] = []
    if row["revenue_ratio"] < INFLOW_DROP_RATIO:
        out.append("inflow_drop")
    if row["buffer_days"] < baseline.cash_buffer_days * BUFFER_DROP_RATIO:
        out.append("buffer_drop")
    recent = [r["revenue"] for r in rows[-VOLATILITY_WEEKS:]]
    if len(recent) == VOLATILITY_WEEKS and (mean := statistics.fmean(recent)) > 0:
        if statistics.pstdev(recent) / mean > VOLATILITY_COV:
            out.append("volatility_spike")
    if row["high_cost_count"] > 0 or row["loan_proceeds"] > 0:
        out.append("new_debt")
    return out


def evidence_catalog(rows: Sequence[Mapping[str, Any]], baseline: Baseline, industry: str) -> dict[str, int | float]:
    catalog: dict[str, int | float] = {}
    for row in rows:
        for name in WEEK_FIELDS:
            catalog[f"W:{row['week']}:{name}"] = row[name]
    for name in ("avg_weekly_revenue", "revenue_volatility", "cash_buffer_days", "operating_daily_krw", "tax_bank_match_ratio"):
        catalog[f"B:{name}"] = getattr(baseline, name)
    for m, v in baseline.seasonal_index.items():
        catalog[f"B:index:{m:02d}"] = v
    peer = cohort(industry)
    for m, v in peer["index"].items():
        catalog[f"B:cohort:index:{m:02d}"] = v
    catalog["B:cohort:high_cost_lender_share"] = peer["high_cost_lender_share"]
    catalog["B:cohort:median_buffer_days"] = peer["median_buffer_days"]
    return catalog
