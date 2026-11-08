"""정산 원장 ↔ 계좌 입금 대사, 채널 집중, 공제율 추이(스펙 §3.2). 결정적 코드만 지표를 만든다.

대상 기간은 기준일 직전 6개 완결 월. 기준일 직전 3영업일 안의 지급은 아직 입금 전일 수 있어 뺀다.
매칭은 같은 플랫폼·같은 금액·지급일부터 3영업일 안의 가장 이른 미사용 입금(한 입금은 한 번만).
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date

from shared.contracts import PgSettlement, Transaction
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


def _month_start(d: date, back: int = 0) -> date:
    m = d.year * 12 + d.month - 1 - back
    return date(m // 12, m % 12 + 1, 1)


def _platform_of(t: Transaction) -> str | None:
    return next((p for payer, p in PAYER_PLATFORM.items() if payer in t.description), None)


def ledger_summary(settlements: Sequence[PgSettlement], transactions: Sequence[Transaction], as_of: date) -> dict:
    """키: available, period{from,to}, settlement_count, pg_reconciliation_ratio, pg_top_platform_share, pg_hhi,
    pg_take_rate_trend_pp, platforms[{platform,gross_krw,share,unpaid_krw}], monthly_take_rate[{month,rate}],
    unmatched_count, unmatched[{settled_on,platform,net_krw}](최근 10건)."""
    start, end = _month_start(as_of, WINDOW_MONTHS), _month_start(as_of)
    rows = sorted(
        (s for s in settlements
         if start <= s.settled_on < end and add_business_days(s.settled_on, MATCH_BUSINESS_DAYS) < as_of),
        key=lambda s: (s.settled_on, s.platform, s.net_krw),
    )
    if not rows:
        return {"available": False}
    deposits = sorted((t for t in transactions if t.amount_krw > 0 and _platform_of(t)),
                      key=lambda t: (t.booked_on, t.txn_id))
    used: set[str] = set()
    unmatched: list[PgSettlement] = []
    for s in rows:
        last = add_business_days(s.settled_on, MATCH_BUSINESS_DAYS)
        hit = next((t for t in deposits if t.txn_id not in used and _platform_of(t) == s.platform
                    and s.settled_on <= t.booked_on <= last and t.amount_krw == s.net_krw), None)
        if hit is None:
            unmatched.append(s)
        else:
            used.add(hit.txn_id)
    target = sum(s.net_krw for s in rows)
    gross: dict[str, int] = defaultdict(int)
    unpaid: dict[str, int] = defaultdict(int)
    by_month: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for s in rows:
        gross[s.platform] += s.gross_krw
        m = by_month[s.settled_on.strftime("%Y-%m")]
        m[0] += s.gross_krw
        m[1] += s.fee_krw + s.deduction_krw
    for s in unmatched:
        unpaid[s.platform] += s.net_krw
    total = sum(gross.values())
    shares = {p: g / total for p, g in gross.items()} if total else {}
    monthly = [{"month": k, "rate": round(v[1] / v[0], 4)} for k, v in sorted(by_month.items()) if v[0] > 0]
    return {
        "available": True,
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "settlement_count": len(rows),
        "pg_reconciliation_ratio": round(1 - sum(s.net_krw for s in unmatched) / target, 3) if target > 0 else None,
        "pg_top_platform_share": round(max(shares.values()), 3) if shares else None,
        "pg_hhi": round(sum(v * v for v in shares.values()), 3) if shares else None,
        "pg_take_rate_trend_pp": (round((monthly[-1]["rate"] - monthly[0]["rate"]) * 100, 2)
                                  if len(monthly) >= WINDOW_MONTHS else None),
        "platforms": [{"platform": p, "gross_krw": g, "share": round(shares[p], 3), "unpaid_krw": unpaid[p]}
                      for p, g in sorted(gross.items(), key=lambda kv: (-kv[1], kv[0]))],
        "monthly_take_rate": monthly,
        "unmatched_count": len(unmatched),
        "unmatched": [{"settled_on": s.settled_on.isoformat(), "platform": s.platform, "net_krw": s.net_krw}
                      for s in sorted(unmatched, key=lambda s: s.settled_on, reverse=True)[:UNMATCHED_LIMIT]],
    }


def features_of(summary: Mapping) -> dict[str, float | None]:
    return {"pg_reconciliation_ratio": summary.get("pg_reconciliation_ratio"),
            "pg_top_platform_share": summary.get("pg_top_platform_share")}
