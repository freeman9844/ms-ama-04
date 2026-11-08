"""정산 프로필과 기대 입금(결정적, 스펙 §12.1).

- 심사 때 매출 입금을 입금처별로 묶어 월평균·정산 주기·회당 평균을 잰다.
- 완전한 달의 절반 넘게 지급이 없는 입금처(일회성 입금)는 뺀다.
- 주기는 완전한 달의 월간 지급 횟수 중앙값으로 정한다: 15회 이상 매 영업일, 3~6회 주 1회(일요일 마감 뒤 L영업일),
  2회 월 2회, 1회 월 1회. 월 단위 기준일은 지급일(일자) 묶음의 최솟값이다(공휴일이면 다음 영업일로 밀린 것).
- 기대 입금은 지급 예정일마다 회당 평균 × 그날 계절 지수다. 지급 예정일은 영업일 달력으로 세므로
  공휴일이 낀 주는 기대가 낮다. 계절 지수는 자기 이력의 월별 정산 입금 지수(21일 이상 본 달은 한 달치로 환산)와
  업종 코호트 지수를 반씩 섞은 값이고(한 해 관찰 하나는 잡음이 크다), 이력에 없는 달은 코호트 지수다. 월 지수를 매달 15일 값으로 보고 그 사이는 선형 보간한다(11월 행사처럼
  달 안에서 오르내리는 흐름을 계단 대신 비탈로 따라간다).
- 명절 이동(예: 추석 2025년 10월 → 2026년 9월)은 반영하지 않는다. 그 주의 오탐은 조기경보 에이전트가 거른다.
"""

import calendar
import statistics
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from agents.classify import Category
from shared.contracts import Transaction
from shared.krcalendar import add_business_days, is_business_day, month_add, next_business_day

# (입금처, 적요에 모두 들어 있어야 하는 키워드). 앞에서부터 처음 맞는 것.
PAYERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("네이버파이낸셜", ("네이버파이낸셜",)),
    ("쿠팡 주정산", ("쿠팡", "주정산")),
    ("쿠팡 최종액정산", ("쿠팡", "최종액")),
    ("11번가", ("11번가",)),
    ("토스페이먼츠", ("토스페이먼츠",)),
)
OTHER = "기타"
CADENCES = ("business_daily", "weekly", "semimonthly", "monthly")
CADENCE_LABELS = {"business_daily": "매 영업일", "weekly": "주 1회", "semimonthly": "월 2회", "monthly": "월 1회"}
_ANCHOR_GAP_DAYS = 6
_MIN_MONTH_DAYS = 21
_WEEKLY_LAGS_BD = range(1, 26)
OWN_INDEX_WEIGHT = 0.5  # 한 해 관찰은 잡음이 커서 업종 코호트와 반씩 섞는다(신뢰도 가중)
COHORT_CASH_LAG_SHARE = 0.3  # 주문→입금 평균 약 열흘: 입금 달 지수에 앞 달 주문 지수가 30% 섞인다


def payer_of(description: str) -> str:
    for name, keys in PAYERS:
        if all(k in description for k in keys):
            return name
    return OTHER


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _complete_months(first: date, as_of: date) -> list[str]:
    """첫 거래 달과 기준일 달을 뺀, 처음부터 끝까지 관찰한 달."""
    out = []
    y, m = month_add(first.year, first.month, 1)
    while (y, m) < (as_of.year, as_of.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = month_add(y, m, 1)
    return out


def _monthly_revenue(revenue: Sequence[Transaction], first: date, as_of: date) -> dict[str, float]:
    """달별 정산 입금. 21일 이상 관찰한 달은 한 달치로 환산해 넣는다(대출 시작 달은 대개 이력 첫 달에 걸쳐 있다)."""
    seen: Counter[str] = Counter()
    d = first
    while d < as_of:
        seen[_month_key(d)] += 1
        d += timedelta(days=1)
    totals: Counter[str] = Counter()
    for t in revenue:
        totals[_month_key(t.booked_on)] += t.amount_krw
    out = {}
    for key, days in seen.items():
        y, m = int(key[:4]), int(key[5:7])
        if days >= _MIN_MONTH_DAYS:
            out[key] = totals[key] * calendar.monthrange(y, m)[1] / days
    return out


def _anchors(days: Sequence[int]) -> list[int]:
    """지급 일자 묶음의 최솟값(10~13일과 25~28일 → [10, 25])."""
    out: list[int] = []
    prev = None
    for d in sorted(set(days)):
        if prev is None or d - prev > _ANCHOR_GAP_DAYS:
            out.append(d)
        prev = d
    return out


def _sundays(lo: date, hi: date) -> list[date]:
    d = lo + timedelta(days=(6 - lo.weekday()) % 7)
    out = []
    while d <= hi:
        out.append(d)
        d += timedelta(days=7)
    return out


def _weekly_lag(paid_days: Sequence[date]) -> int:
    """일요일 마감 뒤 몇 영업일에 주는지: 이력 지급일과 가장 많이 겹치는 L(같으면 작은 L)."""
    paid = set(paid_days)
    sundays = _sundays(paid_days[0] - timedelta(days=45), paid_days[-1])
    return max(_WEEKLY_LAGS_BD, key=lambda lag: (len(paid & {add_business_days(s, lag) for s in sundays}), -lag))


def _cadence(per_month: float) -> str:
    if per_month >= 15:
        return "business_daily"
    if per_month >= 3:
        return "weekly"
    if per_month >= 2:
        return "semimonthly"
    return "monthly"


def settlement_profile(
    transactions: Sequence[Transaction],
    categories: Mapping[str, Category],
    as_of: date,
    cohort_index: Mapping[int, float],
) -> dict[str, Any] | None:
    revenue = [t for t in transactions if categories.get(t.txn_id) is Category.REVENUE and t.amount_krw > 0]
    if not revenue:
        return None
    months = _complete_months(min(t.booked_on for t in transactions), as_of)
    by_payer: dict[str, list[Transaction]] = defaultdict(list)
    for t in revenue:
        by_payer[payer_of(t.description)].append(t)
    channels = []
    for payer, txns in sorted(by_payer.items(), key=lambda kv: -sum(t.amount_krw for t in kv[1])):
        paid_days = sorted({t.booked_on for t in txns})
        in_months = [d for d in paid_days if _month_key(d) in months]
        if months:
            counts = Counter(_month_key(d) for d in in_months)
            per_month = statistics.median(counts.get(m, 0) for m in months)
            monthly_avg = sum(t.amount_krw for t in txns if _month_key(t.booked_on) in months) / len(months)
        else:
            span = max((paid_days[-1] - paid_days[0]).days, 1) / 30.4
            per_month = len(paid_days) / span
            monthly_avg = sum(t.amount_krw for t in txns) / span
        if per_month == 0 or len(paid_days) < 2:
            continue  # 일회성 입금(완전한 달의 절반 넘게 비어 있음)은 정산 채널로 보지 않는다
        cadence = _cadence(per_month)
        if cadence == "weekly":
            anchors = [_weekly_lag(paid_days)]
        elif cadence in ("semimonthly", "monthly"):
            anchors = _anchors([d.day for d in paid_days])
        else:
            anchors = []
        channels.append(
            {
                "payer": payer,
                "cadence": cadence,
                "anchors": anchors,
                "payouts_per_month": round(per_month, 1),
                "monthly_avg_krw": int(monthly_avg),
                "per_payout_krw": int(sum(t.amount_krw for t in txns) / len(txns)),
            }
        )
    totals = _monthly_revenue(revenue, min(t.booked_on for t in transactions), as_of)
    return {"channels": channels, **_seasonal_index(totals, cohort_index)}


def _seasonal_index(totals: Mapping[str, float], cohort_index: Mapping[int, float]) -> dict[str, Any]:
    """자기 지수(한 해 관찰)와 코호트 지수를 반씩 섞는다. 코호트는 주문 달 기준이라 입금 달 기준으로 옮기고
    (정산 지연만큼 앞 달을 섞는다) 자기 이력 달 평균이 1이 되게 맞춘다."""
    order = {m: float(cohort_index.get(m, 1.0)) for m in range(1, 13)}
    cohort = {m: (1 - COHORT_CASH_LAG_SHARE) * order[m] + COHORT_CASH_LAG_SHARE * order[(m - 2) % 12 + 1]
              for m in range(1, 13)}
    mean = statistics.fmean(totals.values()) if totals else 0.0
    own = {int(k[5:7]): t / mean for k, t in totals.items()} if mean > 0 else {}
    scale = statistics.fmean(cohort[m] for m in own) if own else 1.0
    index = {
        f"{m:02d}": round(OWN_INDEX_WEIGHT * own[m] + (1 - OWN_INDEX_WEIGHT) * cohort[m] / scale
                          if m in own else cohort[m] / scale, 3)
        for m in range(1, 13)
    }
    return {"seasonal_index": index, "own_months": [f"{m:02d}" for m in sorted(own)]}


def _nominal_dates(channel: Mapping[str, Any], lo: date, hi: date) -> list[date]:
    cadence = channel["cadence"]
    if cadence == "business_daily":
        return [d for d in (lo + timedelta(days=i) for i in range((hi - lo).days + 1)) if is_business_day(d)]
    # 앞 기간의 지급일이 공휴일로 밀려 들어올 수 있게 2주 앞부터 본다.
    first = lo - timedelta(days=14)
    if cadence == "weekly":
        (lag,) = channel["anchors"]
        return [add_business_days(s, lag) for s in _sundays(lo - timedelta(days=45), hi)]
    out = []
    y, m = first.year, first.month
    while date(y, m, 1) <= hi:
        last = calendar.monthrange(y, m)[1]
        out += [date(y, m, min(a, last)) for a in channel["anchors"]]
        y, m = month_add(y, m, 1)
    return out


def seasonal_factor(profile: Mapping[str, Any], d: date) -> float:
    """월 지수를 매달 15일 값으로 두고 이웃 달과 선형 보간한 그날의 지수."""
    index = profile["seasonal_index"]
    mid = date(d.year, d.month, 15)
    y, m = month_add(d.year, d.month, 1 if d >= mid else -1)
    other = date(y, m, 15)
    a, b = (mid, other) if d >= mid else (other, mid)
    frac = (d - a).days / (b - a).days
    return index[f"{a.month:02d}"] + (index[f"{b.month:02d}"] - index[f"{a.month:02d}"]) * frac


def expected_payouts(profile: Mapping[str, Any], lo: date, hi: date) -> list[tuple[date, str, int]]:
    """[lo, hi] 안의 지급 예정 (지급일, 입금처, 기대 금액). 지급일은 다음 영업일로 민다."""
    out = []
    for ch in profile["channels"]:
        for nominal in _nominal_dates(ch, lo, hi):
            paid = next_business_day(nominal)
            if lo <= paid <= hi:
                out.append((paid, ch["payer"], round(ch["per_payout_krw"] * seasonal_factor(profile, paid))))
    return sorted(out)


def expected_revenue(profile: Mapping[str, Any], lo: date, hi: date) -> int:
    return sum(a for _, _, a in expected_payouts(profile, lo, hi))
