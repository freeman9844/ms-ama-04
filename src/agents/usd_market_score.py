"""Deterministic USD marketplace score: one 0-100 score per market and a share-weighted seller score.

Each market starts at 100 at loan time and loses points against its own pre-loan weeks (`week_end < start`) for a
higher cancel or return rate, a lower rating, a lower seller grade and recent post-loan penalties. Improvement earns
nothing. Each metric deduction is capped, then the market's total is capped at 100. The seller score weights each
market by its share of pre-loan gross; markets without pre-loan sales are left out."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from shared.usd_source import GRADES, UsdMarketWeek

SCORE_DROP = 85  # weighted score or worst major market at or below this: market_score_drop (weak)
SCORE_CRASH = 60  # same at or below this: market_score_crash (strong), replaces the drop
MAJOR_SHARE = 0.20  # a market with at least this share of pre-loan gross counts for the worst market

CANCEL_PER_PP = 6  # points per percentage point of cancel rate above the baseline
CANCEL_CAP = 40
RETURNS_PER_PP = 2
RETURNS_CAP = 25
RATING_PER_STAR = 50  # 5 points per 0.1 under the baseline average
RATING_CAP = 25
GRADE_PER_STEP = 15
GRADE_CAP = 30
PENALTY_POINTS = {"warning": 20, "suspension": 40}
PENALTY_CAP = 40
PENALTY_DAYS = 21  # a penalty counts in its own week and the three weeks after
METRICS = ("cancel", "returns", "rating", "grade", "penalty")


@dataclass(frozen=True)
class MarketScore:
    score: int  # share-weighted seller score
    platforms: dict[str, dict[str, float | int]]  # code -> {"score": int, "share": float (3 decimals)}
    worst_platform: str  # lowest-scoring major market; ties go to the larger share
    worst_score: int
    parts: list[dict[str, Any]]  # {"platform", "metric", "points", "value", "baseline"}, points descending


def _rate(cents: int, gross: int) -> float | None:
    return cents / gross if gross else None


def _deductions(market: list[UsdMarketWeek], start: date, hi: date) -> list[tuple[str, float, Any, Any]]:
    """(metric, deduction, value, baseline) for one market's snapshots, deductions above zero only."""
    pre = sorted((w for w in market if w.week_end < start), key=lambda w: w.week_end)
    now = next((w for w in market if w.week_end == hi), None)
    out: list[tuple[str, float, Any, Any]] = []
    if now is not None:
        for metric, field, per_pp, cap in (("cancel", "cancel_cents", CANCEL_PER_PP, CANCEL_CAP),
                                           ("returns", "returns_cents", RETURNS_PER_PP, RETURNS_CAP)):
            value = _rate(getattr(now, field), now.gross_cents)
            base_rates = [r for w in pre if (r := _rate(getattr(w, field), w.gross_cents)) is not None]
            if value is not None and base_rates:
                base = sum(base_rates) / len(base_rates)
                out.append((metric, min(cap, max(0.0, value - base) * 100 * per_pp), round(value, 4), round(base, 4)))
        base_rating = sum(w.rating for w in pre) / len(pre)
        out.append(("rating", min(RATING_CAP, max(0.0, base_rating - now.rating) * RATING_PER_STAR),
                    round(now.rating, 2), round(base_rating, 2)))
        base_grade = pre[-1].seller_grade
        if base_grade in GRADES and now.seller_grade in GRADES:
            steps = max(0, GRADES.index(base_grade) - GRADES.index(now.seller_grade))
            out.append(("grade", min(GRADE_CAP, steps * GRADE_PER_STEP), now.seller_grade, base_grade))
    recent = [p for w in market if hi - timedelta(days=PENALTY_DAYS) <= w.week_end <= hi and w.week_end >= start
              for p in w.penalties]
    out.append(("penalty", min(PENALTY_CAP, sum(PENALTY_POINTS[p.kind] for p in recent)), len(recent), 0))
    return [(m, round(d, 1), v, b) for m, d, v, b in out if round(d, 1) > 0]


def market_score(weeks: Sequence[UsdMarketWeek], start: date, hi: date) -> MarketScore | None:
    """Scores the week ending `hi` against the pre-loan weeks. None without a snapshot for `hi` or before the loan.
    A market with pre-loan sales but no snapshot this week keeps its share and is judged on penalties alone."""
    if not any(w.week_end == hi for w in weeks) or not any(w.week_end < start for w in weeks):
        return None
    by_platform: dict[str, list[UsdMarketWeek]] = defaultdict(list)
    for w in weeks:
        by_platform[w.platform_code].append(w)
    pre_gross = {code: sum(w.gross_cents for w in ws if w.week_end < start) for code, ws in by_platform.items()}
    total = sum(g for g in pre_gross.values() if g > 0)
    if not total:
        return None
    shares = {code: g / total for code, g in pre_gross.items() if g > 0}
    order = sorted(shares, key=lambda c: (-shares[c], c))

    platforms: dict[str, dict[str, float | int]] = {}
    exact: dict[str, float] = {}
    parts: list[dict[str, Any]] = []
    for code in order:
        found = _deductions(by_platform[code], start, hi)
        exact[code] = 100 - min(100, sum(d for _, d, _, _ in found))
        platforms[code] = {"score": round(exact[code]), "share": round(shares[code], 3)}
        for metric, deduction, value, baseline in sorted(found, key=lambda f: METRICS.index(f[0])):
            points = round(deduction * shares[code], 1)
            if points:
                parts.append({"platform": code, "metric": metric, "points": points, "value": value,
                              "baseline": baseline})
    parts.sort(key=lambda p: -p["points"])  # stable: ties keep share order, then metric order

    major = [c for c in order if shares[c] >= MAJOR_SHARE] or order[:1]
    worst = min(major, key=lambda c: (platforms[c]["score"], -shares[c], c))
    return MarketScore(score=round(sum(shares[c] * exact[c] for c in order)), platforms=platforms,
                       worst_platform=worst, worst_score=int(platforms[worst]["score"]), parts=parts)
