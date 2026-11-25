"""정산 원장 ↔ 계좌 입금 대사, 채널 집중, 공제율 추이, 선정산 추정(스펙 §3.2). 결정적 코드만 지표를 만든다.

대상 기간은 기준일 직전 6개 완결 월. 기준일 직전 3영업일 안의 지급은 아직 입금 전일 수 있어 뺀다.
매칭은 같은 플랫폼·같은 금액·지급일부터 3영업일 안의 가장 이른 미사용 입금(한 입금은 한 번만).
미매칭 정산은 플랫폼 입금이 아닌 미사용 입금 중 금액 비율·기간 조건(ADVANCE_RATIO·ADVANCE_WINDOW_DAYS)을
만족하는 가장 이른 것과 짝지어 선정산 양도를 추정한다(한 입금은 한 번만).
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import NamedTuple

from shared.contracts import USD_V1, PgSettlement, Transaction, UsdTransaction
from shared.sellers import USD_CHANNEL_NAMES
from shared.usd_source import UsdSettlement
from shared.krcalendar import add_business_days

MATCH_BUSINESS_DAYS = 3
WINDOW_MONTHS = 6
UNMATCHED_LIMIT = 10
FLAG_PG_LEDGER_UNAVAILABLE = "pg_ledger_unavailable"
PAYER_PLATFORM = {
    "네이버파이낸셜": "스마트스토어",
    "쿠팡(주) 주정산": "쿠팡",
    "쿠팡(주) 최종액정산": "쿠팡",
    "11번가": "11번가",
    "토스페이먼츠": "자사몰",
}
# mockapis/sellerdata/channels.py 정산 일정에서 유도한 평균 캘린더일(달력일).
# 스마트스토어: 구매확정(주문 후 5~9일, 평균 7일) + 다음 영업일(+1) ≈ 8.
# 쿠팡: 주정산(일요일 마감 +15영업일, 70%)과 최종액정산(마감 익익월 첫 영업일, 30%)의 가중 평균 ≈ 32.
# 11번가: 1~15일분→25일(평균 기산일 대비 lag 약 17), 16일~말일분→익월10일(비슷한 lag)의 평균 ≈ 17.
# 자사몰: 카드 결제 D+2영업일 ≈ 3.
PLATFORM_LAG_DAYS: dict[str, int] = {
    "스마트스토어": 8,
    "쿠팡": 32,
    "11번가": 17,
    "자사몰": 3,
}
ADVANCE_WINDOW_DAYS = 10  # 선정산 후보 입금은 정산일 이전 이 일수(달력일) 안이어야 한다.
ADVANCE_RATIO = (0.75, 0.95)  # 선정산 후보 입금액은 실지급액의 이 비율 범위 안이어야 한다.


def _month_start(d: date, back: int = 0) -> date:
    m = d.year * 12 + d.month - 1 - back
    return date(m // 12, m % 12 + 1, 1)


def _platform_of(t: Transaction) -> str | None:
    return next((p for payer, p in PAYER_PLATFORM.items() if payer in t.description), None)


def _trend_pp(rates: Sequence[float]) -> float | None:
    """최근 3개월 평균 − 이전 3개월 평균(%p, 소수 둘째 자리). 원 비율로 계산하고 마지막에만 반올림한다."""
    if len(rates) < WINDOW_MONTHS:
        return None
    half = WINDOW_MONTHS // 2
    value = round((sum(rates[-half:]) / half - sum(rates[-WINDOW_MONTHS:-half]) / half) * 100, 2)
    return value + 0.0  # -0.0을 0.0으로 정규화한다.


class _Payout(NamedTuple):
    settled_on: date
    platform: str
    net: int
    gross: int
    fee: int
    deduction: int


class _Deposit(NamedTuple):
    txn_id: str
    booked_on: date
    platform: str | None
    amount: int


def _advance_pairs(unmatched: Sequence[_Payout], candidates: Sequence[_Deposit], unit: str) -> dict[int, dict]:
    """미매칭 정산을 settled_on 오름차순으로 돌며 조건을 만족하는 가장 이른 미사용 입금과 짝짓는다(한 입금은 한 번만).

    반환은 정산 객체(id(s))를 키로 하는 매칭 결과 dict다.
    """
    used: set[str] = set()
    lo_ratio, hi_ratio = ADVANCE_RATIO
    pairs: dict[int, dict] = {}
    for s in sorted(unmatched, key=lambda s: s.settled_on):
        earliest = s.settled_on - timedelta(days=ADVANCE_WINDOW_DAYS)
        hit = next(
            (t for t in candidates
             if t.txn_id not in used and earliest <= t.booked_on <= s.settled_on
             and lo_ratio * s.net <= t.amount <= hi_ratio * s.net),
            None,
        )
        if hit is not None:
            used.add(hit.txn_id)
            pairs[id(s)] = {
                "booked_on": hit.booked_on.isoformat(),
                f"amount_{unit}": hit.amount,
                "lead_days": (s.settled_on - hit.booked_on).days,
            }
    return pairs


def ledger_summary(settlements: Sequence[PgSettlement], transactions: Sequence[Transaction], as_of: date) -> dict:
    """키: available, period{from,to}, settlement_count, pg_reconciliation_ratio, pg_top_platform_share, pg_hhi,
    pg_take_rate_trend_pp, pg_offset_rate_trend_pp, pg_cash_conversion_days, pg_advance_suspected_count,
    pg_advance_share, platforms[{platform,gross_krw,share,unpaid_krw,lag_days}],
    monthly_take_rate[{month,rate,fee_rate,offset_rate}], unmatched_count,
    unmatched[{settled_on,platform,net_krw,advance}](최근 10건, advance는 {booked_on,amount_krw,lead_days} 또는 None)."""
    rows = [_Payout(s.settled_on, s.platform, s.net_krw, s.gross_krw, s.fee_krw, s.deduction_krw) for s in settlements]
    deposits = [_Deposit(t.txn_id, t.booked_on, _platform_of(t), t.amount_krw) for t in transactions if t.amount_krw > 0]
    return _summary(rows, deposits, as_of, "krw", PLATFORM_LAG_DAYS)


def usd_ledger_summary(settlements: Sequence[UsdSettlement], transactions: Sequence[Transaction | UsdTransaction],
                       as_of: date) -> dict:
    rows = [_Payout(s.settled_on, s.platform_code, s.net_cents, s.gross_cents, s.fee_cents, s.deduction_cents)
            for s in settlements]
    def platform(t):
        return t.platform_code or next((code for code, label in USD_CHANNEL_NAMES.items()
                                        if label.casefold() in t.description.casefold()), None)
    deposits = [_Deposit(t.txn_id, t.booked_on, platform(t), t.amount_cents) for t in transactions
                if isinstance(t, UsdTransaction) and t.amount_cents > 0]
    return {**USD_V1.model_dump(), **_summary(rows, deposits, as_of, "cents",
                                              {"naver_smart_store": 7, "coupang": 28, "own_store": 3, "11st": 17})}


def _summary(settlements, transactions, as_of, unit, lag_days):
    start, end = _month_start(as_of, WINDOW_MONTHS), _month_start(as_of)
    rows = sorted(
        (s for s in settlements
         if start <= s.settled_on < end and add_business_days(s.settled_on, MATCH_BUSINESS_DAYS) < as_of),
        key=lambda s: (s.settled_on, s.platform, s.net),
    )
    if not rows:
        return {"available": False}
    deposits = sorted((t for t in transactions if t.platform),
                      key=lambda t: (t.booked_on, t.txn_id))
    used: set[str] = set()
    unmatched: list[_Payout] = []
    for s in rows:
        last = add_business_days(s.settled_on, MATCH_BUSINESS_DAYS)
        hit = next((t for t in deposits if t.txn_id not in used and t.platform == s.platform
                    and s.settled_on <= t.booked_on <= last and t.amount == s.net), None)
        if hit is None:
            unmatched.append(s)
        else:
            used.add(hit.txn_id)
    advance_candidates = sorted(
        (t for t in transactions if t.platform is None and t.txn_id not in used),
        key=lambda t: (t.booked_on, t.txn_id),
    )
    advance_pairs = _advance_pairs(unmatched, advance_candidates, unit)
    unmatched_total = sum(s.net for s in unmatched)
    target = sum(s.net for s in rows)
    gross: dict[str, int] = defaultdict(int)
    unpaid: dict[str, int] = defaultdict(int)
    by_month: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for s in rows:
        gross[s.platform] += s.gross
        m = by_month[s.settled_on.strftime("%Y-%m")]
        m[0] += s.gross
        m[1] += s.fee
        m[2] += s.deduction
    for s in unmatched:
        unpaid[s.platform] += s.net
    total = sum(gross.values())
    shares = {p: g / total for p, g in gross.items()} if total else {}
    monthly_raw = [(k, v[1] / v[0], v[2] / v[0]) for k, v in sorted(by_month.items()) if v[0] > 0]
    known_gross = sum(g for p, g in gross.items() if p in lag_days)
    cash_conversion_days = (
        round(sum(g * lag_days[p] for p, g in gross.items() if p in lag_days) / known_gross, 1)
        if known_gross > 0 else None
    )
    return {
        "available": True,
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "settlement_count": len(rows),
        "pg_reconciliation_ratio": round(1 - unmatched_total / target, 3) if target > 0 else None,
        "pg_top_platform_share": round(max(shares.values()), 3) if shares else None,
        "pg_hhi": round(sum(v * v for v in shares.values()), 3) if shares else None,
        "pg_take_rate_trend_pp": _trend_pp([fee + off for _, fee, off in monthly_raw]),
        "pg_offset_rate_trend_pp": _trend_pp([off for _, _, off in monthly_raw]),
        "pg_cash_conversion_days": cash_conversion_days,
        # R112 (> 0) is a synthetic-demo threshold: one unmatched settlement with a platform-less
        # deposit counts as a hit.
        "pg_advance_suspected_count": len(advance_pairs),
        "pg_advance_share": (
            round(sum(s.net for s in unmatched if id(s) in advance_pairs) / unmatched_total, 3)
            if unmatched_total else None
        ),
        "platforms": [{"platform": p, f"gross_{unit}": g, "share": round(shares[p], 3) if total else None,
                       f"unpaid_{unit}": unpaid[p], "lag_days": lag_days.get(p)}
                      for p, g in sorted(gross.items(), key=lambda kv: (-kv[1], kv[0]))],
        "monthly_take_rate": [{"month": k, "rate": round(fee + off, 4), "fee_rate": round(fee, 4),
                               "offset_rate": round(off, 4)} for k, fee, off in monthly_raw],
        "unmatched_count": len(unmatched),
        "unmatched": [{"settled_on": s.settled_on.isoformat(), "platform": s.platform, f"net_{unit}": s.net,
                      "advance": advance_pairs.get(id(s))}
                      for s in sorted(unmatched, key=lambda s: s.settled_on, reverse=True)[:UNMATCHED_LIMIT]],
    }


USD_PG_FEATURES: tuple[str, ...] = ("pg_cash_conversion_days", "pg_advance_suspected_count",
                                    "pg_offset_rate_trend_pp")


def features_of(summary: Mapping) -> dict[str, float | None]:
    return {"pg_reconciliation_ratio": summary.get("pg_reconciliation_ratio"),
            "pg_top_platform_share": summary.get("pg_top_platform_share")}


def usd_features_of(summary: Mapping) -> dict[str, float | int | None]:
    return {**features_of(summary), **{k: summary.get(k) for k in USD_PG_FEATURES}}
