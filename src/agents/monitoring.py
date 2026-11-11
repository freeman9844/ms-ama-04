"""대출 사후 모니터링 계산(결정적): 기준선, 주간 계열, 통계 탐지기, 근거 카탈로그.

- 기준선은 심사 시점 underwriting 결과(features·monthly)에서 만든다. 원천 거래는 다시 읽지 않는다.
- 주차 n은 흐름 시작일부터 7일 단위다(n=1이 첫 7일).
- 매출 판단은 정산 프로필의 기대 입금 대비 실제(revenue_vs_expected, 최근 3주 합)로 한다. 정산일이 영업일로 밀려
  주 경계를 넘나드는 잡음과 주문 수의 우연한 흔들림을 3주 합으로 줄이고, 기대의 75% 아래(INFLOW_DROP_RATIO)에서만 트리거한다.
  정상 셀러도 13주 동안 기대 대비 ±8% 수준으로 출렁이므로(합성 엔진 측정) 0.85 같은 좁은 기준은 날짜에 따라 오탐을 낸다.
  프로필이 없는 과거 대출은 기준선 주평균(revenue_ratio)으로 폴백한다.
- 정산 프로필에 예정 출금 일정(forecast.outflow_schedule)이 있으면 주차마다 4주 잔고 전망을 계산해
  projected_min_balance·projected_min_date·shortfall_date를 주차 행에 넣는다(스펙 §12.2). 최저 잔고가 0 아래면 projected_shortfall.
- 탐지기는 결정적 규칙이다. 남는 오탐(명절 이동 등)을 거르는 일은 Early-Warning 에이전트의 몫이다(스펙 §3.3).
- 자체 대출금 입금(LENDER_CREDIT)과 선정산 선지급은 loan_proceeds에서 빼고 advance_in으로 따로 센다(스펙 §5.5).
  원장 트리거 4개와 주간 원장 필드는 `agents/ledger_monitor.py`가 채운다(detect는 기존 5개만 본다).
- 근거 ID: W:{주차}:{필드}, F:{주차}:{전망 필드}, B:{기준선 이름}, B:index:{MM}, B:cohort:{이름}.
"""

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any

from agents.classify import Category, classify
from agents.features import DAYS_PER_MONTH, OPERATING_OUT
from agents.forecast import outflow_schedule, project, summarize
from agents.settlement import expected_payouts, payer_of, settlement_profile
from shared.contracts import Transaction
from shared.repayment import LENDER_CREDIT

WEEKS = 13
INFLOW_DROP_RATIO = 0.75
EXPECTED_WEEKS = 3  # revenue_vs_expected를 재는 주 수(최근 n주 합)
BUFFER_DROP_RATIO = 0.5
VOLATILITY_WEEKS = 4
VOLATILITY_COV = 0.15
TRIGGERS = (
    "inflow_drop",
    "buffer_drop",
    "volatility_spike",
    "new_debt",
    "projected_shortfall",
    # 원장 트리거(agents/ledger_monitor.py가 계산한다, 2026-11-11). detect는 위 5개만 본다.
    "sales_slump",
    "settlement_hold",
    "return_spike",
    "cost_surge",
)
WEEK_FIELDS = (
    "revenue",
    "revenue_ratio",
    "expected_revenue",
    "revenue_vs_expected",
    "operating_out",
    "debt_service",
    "high_cost_lender",
    "high_cost_count",
    "loan_proceeds",
    "unclassified_in",
    "end_balance",
    "buffer_days",
    # 선정산 선지급과 주간 원장 집계(ledger_monitor.observe가 채운다, 2026-11-11)
    "advance_in",
    "order_gross",
    "orders_vs_expected",
    "returns",
    "return_rate",
    "held",
    "deductions",
    "deduction_rate",
    "settle_net",
)
FORECAST_FIELDS = ("projected_min_balance", "projected_min_date", "shortfall_date")
ADVANCE_KEYWORD = "선정산"  # 정산 양도 선지급 입금 적요. 매출도 신규 차입도 아니다(스펙 §5.5)
ASSIGNED_SHARE = 0.4  # 대출 뒤 누적 기대 대비 이만큼도 안 들어온 입금처는 선정산 양도로 보고 기대에서 뺀다
_UNCLASSIFIED = (Category.UNCLASSIFIED_IN, Category.UNCLASSIFIED_OUT)

# 합성 업종 집단 통계(데모용 고정 표). 실제 운영에서는 집단 통계 저장소에서 읽는다.
_DEFAULT_COHORT = {"index": dict.fromkeys(range(1, 13), 1.0), "high_cost_lender_share": 0.05, "median_buffer_days": 40.0}


def _flat(peaks: dict[int, float] | None = None) -> dict[int, float]:
    return {**dict.fromkeys(range(1, 13), 1.0), **(peaks or {})}


def _months(*values: float) -> dict[int, float]:
    return dict(zip(range(1, 13), values, strict=True))


# 온라인 셀러 업종 10개. 월 지수는 업종 집단의 월별 주문 지수(11월 쇼핑 행사 포함)다.
PEER_COHORTS: dict[str, dict[str, Any]] = {
    "패션의류": {
        "index": _months(0.82, 0.86, 1.12, 1.15, 1.02, 0.9, 0.84, 0.86, 1.1, 1.18, 1.3, 1.08),
        "high_cost_lender_share": 0.06,
        "median_buffer_days": 45.0,
    },
    "식품": {"index": _months(1.0, 1.0, 0.95, 0.96, 1.0, 0.95, 0.98, 1.0, 1.0, 0.98, 1.3, 1.06),
           "high_cost_lender_share": 0.08, "median_buffer_days": 30.0},
    "뷰티": {"index": _months(0.9, 0.92, 1.0, 1.02, 1.2, 0.98, 0.92, 0.92, 0.98, 1.0, 1.456, 1.04),
           "high_cost_lender_share": 0.07, "median_buffer_days": 35.0},
    "핸드메이드": {"index": _months(0.85, 0.95, 0.95, 0.92, 1.08, 0.88, 0.85, 0.86, 1.0, 1.08, 1.495, 1.3),
              "high_cost_lender_share": 0.05, "median_buffer_days": 40.0},
    "리빙": {"index": _months(0.92, 0.95, 1.08, 1.06, 1.0, 0.95, 0.92, 0.94, 1.02, 1.02, 1.3, 1.12),
           "high_cost_lender_share": 0.05, "median_buffer_days": 40.0},
    "디지털": {"index": _months(0.95, 1.05, 1.05, 0.95, 0.98, 0.94, 0.98, 0.98, 0.98, 1.0, 1.365, 1.08),
            "high_cost_lender_share": 0.06, "median_buffer_days": 35.0},
    "반려동물": {"index": _months(1.0, 0.96, 1.0, 1.0, 1.02, 0.98, 1.0, 1.0, 1.0, 1.0, 1.326, 1.02),
             "high_cost_lender_share": 0.04, "median_buffer_days": 45.0},
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
    settlement: dict[str, Any] | None = None
    installment_krw: int = 0  # 이번 대출 월 원리금(4주 전망용). 이전 문서에는 없다

    @classmethod
    def from_underwriting(cls, underwriting: Mapping[str, Any], installment_krw: int = 0) -> "Baseline":
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
            settlement=underwriting.get("settlement_profile"),
            installment_krw=installment_krw,
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


def settlement_for(
    transactions: Sequence[Transaction], overrides: Mapping[str, str | Category], as_of: date, industry: str
) -> dict[str, Any] | None:
    """심사 시점 거래로 만든 정산 프로필(doc["underwriting"]["settlement_profile"]). 이력에 없는 달은 업종 코호트 지수.
    예정 출금 일정(outflows)을 함께 넣는다."""
    cats = categorize(transactions, overrides)
    profile = settlement_profile(transactions, cats, as_of, cohort(industry)["index"])
    if profile is not None:
        profile["outflows"] = outflow_schedule(transactions, cats, as_of)
    return profile


def projection(
    rows: Sequence[Mapping[str, Any]],
    transactions: Sequence[Transaction],
    categories: Mapping[str, Category],
    baseline: Baseline,
    installment_due: Sequence[date] | None = None,
) -> dict[str, Any] | None:
    """마지막 주차 행 다음 4주 잔고 전망. 그 주 고금리 상환은 매주 같은 요일에 이어진다고 본다."""
    if not rows:
        return None
    lo, hi = date.fromisoformat(rows[-1]["start"]), date.fromisoformat(rows[-1]["end"])
    high_cost = [t for t in transactions
                 if lo <= t.booked_on <= hi and categories[t.txn_id] is Category.HIGH_COST_LENDER]
    return project(baseline.settlement, rows, high_cost, baseline.installment_krw, installment_due)


def _assigned(advance_seen: bool, due_total: Mapping[str, int], paid_total: Mapping[str, int]) -> set[str]:
    """선정산사에 양도해 기대 달력에서 빼야 할 입금처. 양도한 플랫폼 정산은 선지급사를 거쳐
    85% 안팎으로 일찍 들어와 그 입금처 이름으로는 사실상 한 푼도 찍히지 않는다. 대출 전부터 양도한 셀러는
    심사 프로필에 그 입금처가 그대로 남아 있어, 빼지 않으면 매주 매출이 급감한 것처럼 보인다.
    대출 뒤 누적 기대의 ASSIGNED_SHARE도 못 들어온 입금처만 뺀다 — 대출 뒤 새로 양도한 셀러(SETTLEMENT_HOLD)는
    앞 주차 입금이 누적에 남아 한참 뒤에야 빠지고, 양도 직후 매출 급감 트리거는 그대로 울린다."""
    if not advance_seen:
        return set()
    return {p for p, due in due_total.items() if due > 0 and paid_total.get(p, 0) < due * ASSIGNED_SHARE}


def weekly_series(
    transactions: Sequence[Transaction],
    categories: Mapping[str, Category],
    start: date,
    upto_week: int,
    baseline: Baseline,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    balance = None
    window: list[tuple[int, int]] = []  # 최근 EXPECTED_WEEKS주의 (실제, 기대) 입금
    due_total: dict[str, int] = {}  # 대출 뒤 입금처별 누적 기대 입금
    paid_total: dict[str, int] = {}  # 대출 뒤 입금처별 누적 실제 입금
    advance_seen = False
    for week in range(1, upto_week + 1):
        lo, hi = week_window(start, week)
        txns = [t for t in transactions if lo <= t.booked_on <= hi]

        def total(*cats: Category) -> int:
            return sum(t.amount_krw for t in txns if categories[t.txn_id] in cats)

        if txns:
            balance = txns[-1].balance_after_krw
        revenue = total(Category.REVENUE)
        # 자체 대출금 입금과 선정산 선지급은 신규 차입이 아니다(스펙 §5.5). 선지급은 따로 센다.
        advance_in = sum(t.amount_krw for t in txns if t.amount_krw > 0 and ADVANCE_KEYWORD in t.description)
        advance_seen = advance_seen or advance_in > 0
        loan_proceeds = sum(
            t.amount_krw for t in txns
            if categories[t.txn_id] is Category.LOAN_PROCEEDS
            and t.description != LENDER_CREDIT and ADVANCE_KEYWORD not in t.description
        )
        end_balance = balance if balance is not None else 0
        revenue_ratio = round(revenue / baseline.avg_weekly_revenue, 3) if baseline.avg_weekly_revenue else 0.0
        if baseline.settlement:
            payouts = expected_payouts(baseline.settlement, lo, hi)
            for _, payer, amount in payouts:
                due_total[payer] = due_total.get(payer, 0) + amount
            for t in txns:
                if categories[t.txn_id] is Category.REVENUE and t.amount_krw > 0:
                    payer = payer_of(t.description)
                    paid_total[payer] = paid_total.get(payer, 0) + t.amount_krw
            expected = sum(a for _, p, a in payouts if p not in _assigned(advance_seen, due_total, paid_total))
            window = [*window, (revenue, expected)][-EXPECTED_WEEKS:]
            total_expected = sum(e for _, e in window)
            vs_expected = round(sum(r for r, _ in window) / total_expected, 3) if total_expected else 0.0
        else:
            expected, vs_expected = baseline.avg_weekly_revenue, revenue_ratio
        rows.append(
            {
                "week": week,
                "start": lo.isoformat(),
                "end": hi.isoformat(),
                "revenue": revenue,
                "revenue_ratio": revenue_ratio,
                "expected_revenue": expected,
                "revenue_vs_expected": vs_expected,
                "operating_out": -total(*OPERATING_OUT),
                "debt_service": -total(Category.LOAN_REPAYMENT, Category.HIGH_COST_LENDER),
                "high_cost_lender": -total(Category.HIGH_COST_LENDER),
                "high_cost_count": sum(1 for t in txns if categories[t.txn_id] is Category.HIGH_COST_LENDER),
                "loan_proceeds": loan_proceeds,
                "advance_in": advance_in,
                "unclassified_in": total(Category.UNCLASSIFIED_IN),
                "end_balance": end_balance,
                "buffer_days": round(max(end_balance, 0) / baseline.operating_daily_krw, 1)
                if baseline.operating_daily_krw
                else 0.0,
            }
        )
        rows[-1].update(summarize(projection(rows, txns, categories, baseline)))
    return rows


def detect(rows: Sequence[Mapping[str, Any]], baseline: Baseline) -> list[str]:
    """마지막 주차에 걸린 트리거(TRIGGERS 순서)."""
    if not rows:
        return []
    row = rows[-1]
    out: list[str] = []
    # 첫 주는 한 주치만 있어 잡음이 크다(정산 시차로 대출 전 매출이 들어오는지에 좌우된다).
    if row["revenue_vs_expected"] < INFLOW_DROP_RATIO and (len(rows) > 1 or not baseline.settlement):
        out.append("inflow_drop")
    if row["buffer_days"] < baseline.cash_buffer_days * BUFFER_DROP_RATIO:
        out.append("buffer_drop")
    # 기대 입금이 있으면 기대 대비 비율의 흔들림을 본다(계절·정산 주기로 생기는 흔들림은 빼고).
    # 기대 대비 비율은 EXPECTED_WEEKS주가 다 찬 값만 쓴다(앞 주차의 짧은 합은 흔들림이 커 보인다).
    full = rows[EXPECTED_WEEKS - 1 :] if baseline.settlement else rows
    recent = [r["revenue_vs_expected"] if baseline.settlement else r["revenue"] for r in full[-VOLATILITY_WEEKS:]]
    if len(recent) == VOLATILITY_WEEKS and (mean := statistics.fmean(recent)) > 0:
        if statistics.pstdev(recent) / mean > VOLATILITY_COV:
            out.append("volatility_spike")
    if row["high_cost_count"] > 0 or row["loan_proceeds"] > 0:
        out.append("new_debt")
    if (low := row.get("projected_min_balance")) is not None and low < 0:
        out.append("projected_shortfall")
    return out


def evidence_catalog(
    rows: Sequence[Mapping[str, Any]], baseline: Baseline, industry: str
) -> dict[str, int | float | str]:
    """근거 ID → 값. 전망 날짜(F:n:projected_min_date·shortfall_date)만 ISO 날짜 문자열이고 나머지는 수치다."""
    catalog: dict[str, int | float | str] = {}
    for row in rows:
        for name in WEEK_FIELDS:
            if name in row:  # 기대 입금 이전에 저장된 주차 행에는 없다
                catalog[f"W:{row['week']}:{name}"] = row[name]
        for name in FORECAST_FIELDS:
            if row.get(name) is not None:
                catalog[f"F:{row['week']}:{name}"] = row[name]
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
