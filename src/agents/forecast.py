"""대출 사후 4주 잔고 전망(결정적, 스펙 §12.2).

- 심사 때 거래의 반복 출금에서 예정 출금 일정(outflow_schedule)을 뽑아 정산 프로필 옆에 둔다:
  임대료·급여·4대보험·원천세·기존 대출 원리금·대표자 인출은 최근 완전한 3개월 중 2개월 넘게 나온 달별 출금(금액·일자 중앙값),
  부가세는 1·4·7·10월 25일 납부(지난 1년 납부액 중앙값)다.
- 나머지 운영비(매입·택배·광고·카드대금 등)는 매출에 비례하므로 심사 때 매출 대비 비율(variable_ratio)로 둔다.
- 전망 = 주 마지막 날 잔고 + 다음 28일 기대 입금 × 달성률 − 예정 출금 − 비례 운영비 − 이번 대출 원리금
  − 최근 7일 고금리 상환(매주 같은 요일 반복 가정). 달성률은 최근 4주 실제/기대 입금(1 이하)이다.
- 예정 출금 일정이 없는 과거 문서는 전망하지 않는다(트리거·근거 없음).
"""

import calendar
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from agents.classify import Category
from agents.settlement import expected_payouts
from shared.contracts import Transaction
from shared.krcalendar import month_add, next_business_day

HORIZON_DAYS = 28
ATTAINMENT_WEEKS = 4
INSTALLMENT_DAY = 15  # 이번 대출 상환일(매월 15일, 공휴일이면 다음 영업일)
WINDOW_MONTHS = 3
MIN_MONTHS = 2
VAT_MONTHS = (1, 4, 7, 10)
VAT_DAY = 25
MONTHLY = (
    Category.RENT,
    Category.PAYROLL,
    Category.SOCIAL_INSURANCE,
    Category.LOAN_REPAYMENT,
    Category.OWNER_TRANSFER,
)
VARIABLE = (Category.UNCLASSIFIED_OUT,)
OUTFLOW_LABELS = {
    "rent": "임대료",
    "payroll": "급여",
    "social_insurance": "4대보험",
    "loan_repayment": "기존 대출 원리금",
    "owner_transfer": "대표자 인출",
    "withholding": "원천세",
    "vat": "부가세",
    "installment": "이번 대출 원리금",
    "high_cost_lender": "고금리 상환",
    "variable": "매출 비례 운영비",
}


def _is_vat(t: Transaction) -> bool:
    return t.booked_on.month in VAT_MONTHS and t.booked_on.day >= VAT_DAY - 5


def _recent_months(as_of: date, n: int) -> list[tuple[int, int]]:
    return [month_add(as_of.year, as_of.month, -k) for k in range(n, 0, -1)]


def outflow_schedule(
    transactions: Sequence[Transaction], categories: Mapping[str, Category], as_of: date
) -> dict[str, Any] | None:
    """심사 시점 거래의 반복 출금 일정. 최근 3개월 중 완전한 달(첫 거래 달 다음부터)이 MIN_MONTHS보다 적으면 None."""
    first = min((t.booked_on for t in transactions), default=None)
    if first is None:
        return None
    window = [m for m in _recent_months(as_of, WINDOW_MONTHS) if m > (first.year, first.month)]
    if len(window) < MIN_MONTHS:
        return None
    lo = date(*window[0], 1)
    hi = date(as_of.year, as_of.month, 1)
    seen = [t for t in transactions if lo <= t.booked_on < hi and t.amount_krw < 0]

    def items(key: str, txns: Sequence[Transaction]) -> dict[str, Any] | None:
        by_month: dict[tuple[int, int], list[Transaction]] = defaultdict(list)
        for t in txns:
            by_month[(t.booked_on.year, t.booked_on.month)].append(t)
        if len(by_month) < MIN_MONTHS:
            return None
        return {
            "key": key,
            "day": int(statistics.median(t.booked_on.day for ts in by_month.values() for t in ts)),
            "amount_krw": int(statistics.median(-sum(t.amount_krw for t in ts) for ts in by_month.values())),
        }

    out: list[dict[str, Any]] = []
    for cat in MONTHLY:
        if item := items(cat.value, [t for t in seen if categories[t.txn_id] is cat]):
            out.append(item)
    taxes = [t for t in seen if categories[t.txn_id] is Category.TAX and not _is_vat(t)]
    if item := items("withholding", [t for t in taxes if t.booked_on.day <= 15]):
        out.append(item)
    year_ago = date(as_of.year - 1, as_of.month, 1)
    vat = [-t.amount_krw for t in transactions
           if categories[t.txn_id] is Category.TAX and year_ago <= t.booked_on < hi and t.amount_krw < 0 and _is_vat(t)]
    if vat:
        out.append({"key": "vat", "day": VAT_DAY, "months": list(VAT_MONTHS), "amount_krw": int(statistics.median(vat))})
    revenue = sum(t.amount_krw for t in transactions
                  if lo <= t.booked_on < hi and categories[t.txn_id] is Category.REVENUE)
    variable = -sum(t.amount_krw for t in seen if categories[t.txn_id] in VARIABLE)
    return {"items": out, "variable_ratio": round(variable / revenue, 4) if revenue > 0 else 0.0}


def _due(day: int, lo: date, hi: date, months: Sequence[int] | None = None) -> list[date]:
    out = []
    y, m = month_add(lo.year, lo.month, -1)
    while date(y, m, 1) <= hi:
        if months is None or m in months:
            d = next_business_day(date(y, m, min(day, calendar.monthrange(y, m)[1])))
            if lo <= d <= hi:
                out.append(d)
        y, m = month_add(y, m, 1)
    return out


def attainment(rows: Sequence[Mapping[str, Any]]) -> float:
    recent = rows[-ATTAINMENT_WEEKS:]
    expected = sum(r.get("expected_revenue") or 0 for r in recent)
    actual = sum(r.get("revenue") or 0 for r in recent)
    return round(min(actual / expected, 1.0), 3) if expected > 0 else 1.0


def project(
    settlement: Mapping[str, Any] | None,
    rows: Sequence[Mapping[str, Any]],
    high_cost: Sequence[Transaction],
    installment_krw: int,
) -> dict[str, Any] | None:
    """마지막 주차 다음 날부터 HORIZON_DAYS일의 일말 전망 잔고(path)와 항목별 합계(totals, OUTFLOW_LABELS 키 + inflow).
    정산 프로필·예정 출금이 없으면 None."""
    if not settlement or not settlement.get("outflows") or not rows:
        return None
    schedule = settlement["outflows"]
    last = rows[-1]
    day0 = date.fromisoformat(last["end"])
    lo, hi = day0 + timedelta(days=1), day0 + timedelta(days=HORIZON_DAYS)
    rate = attainment(rows)
    flows: dict[date, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    inflow = 0
    for d, _, amount in expected_payouts(settlement, lo, hi):
        flows[d]["inflow"] += round(amount * rate)
        inflow += round(amount * rate)
    for item in schedule["items"]:
        for d in _due(item["day"], lo, hi, item.get("months")):
            flows[d][item["key"]] -= item["amount_krw"]
    if installment_krw:
        for d in _due(INSTALLMENT_DAY, lo, hi):
            flows[d]["installment"] -= installment_krw
    for t in high_cost:
        d = t.booked_on + timedelta(days=7)
        while d <= hi:
            if d >= lo:
                flows[d]["high_cost_lender"] += t.amount_krw
            d += timedelta(days=7)
    daily_variable = round(schedule["variable_ratio"] * inflow / HORIZON_DAYS)
    balance = last["end_balance"]
    path = []
    totals: dict[str, int] = defaultdict(int)
    for i in range(HORIZON_DAYS):
        d = lo + timedelta(days=i)
        flows[d]["variable"] -= daily_variable
        for key, amount in flows[d].items():
            totals[key] += amount
        balance += sum(flows[d].values())
        path.append({"date": d.isoformat(), "balance": balance})
    return {"start_balance": last["end_balance"], "attainment": rate, "path": path, "totals": dict(totals)}


def summarize(projection: Mapping[str, Any] | None) -> dict[str, Any]:
    """주차 행에 넣을 전망 요약: 최저 잔고와 그날, 처음 0 아래로 내려가는 날(없으면 None)."""
    if not projection or not projection["path"]:
        return {}
    path = projection["path"]
    low = min(path, key=lambda p: p["balance"])
    short = next((p["date"] for p in path if p["balance"] < 0), None)
    return {"projected_min_balance": low["balance"], "projected_min_date": low["date"], "shortfall_date": short}
