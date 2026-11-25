import hashlib
import json
from datetime import date
from functools import lru_cache
from pathlib import Path

import pytest

from agents import features
from mockapis import demo
from policy.engine import explain
from policy.rules import rules_for_version

AS_OF = date(2026, 11, 24)
E, R, I = "eligible", "refer", "ineligible"
F = frozenset
V1_TABLE = {
    **{s: (E, F(), E, F()) for s in ("seller-dailylook-01", "seller-mangwon-02", "seller-gearstore-06",
       "seller-kimchi-09", "seller-linen-10", "seller-kitchen-12", "seller-tangerine-15",
       "seller-petcare-16", "seller-bakery-20")},
    **{s: (R, F({"R105", "R106"}), R, F({"R105", "R106"})) for s in ("seller-glowlab-03", "seller-skinlab-11",
       "seller-cable-13", "seller-sofa-18")},
    **{s: (R, F({"R107", "S001"}), I, F({"R001", "R104", "R107"})) for s in ("seller-knitroom-04",
       "seller-leather-14", "seller-tint-17")},
    "seller-candle-05": (R, F({"R107"}), R, F({"R107"})),
    "seller-ceramic-08": (R, F({"R107", "R109"}), R, F({"R107"})),
    "seller-petfood-07": (I, F({"R003"}), E, F()),
    "seller-streetfit-19": (I, F({"R001", "R002", "R104", "R110", "R901"}), I, F({"R001", "R002", "R104", "R901"})),
    "seller-mugshop-21": (I, F({"R002", "R106", "R108", "R901"}), I, F({"R002", "R106", "R108", "R901"})),
}
DIGESTS = json.loads((Path(__file__).parent / "usd_books_digest.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def _cases(as_of):
    return demo.build_usd_cases(as_of)


def case_at(seller, as_of):
    return _cases(as_of)[seller]


def underwrite_at(seller, as_of, principal=500_000):
    case = case_at(seller, as_of)
    return features.compute_usd_underwriting(
        "app-demo", case.profile, case.transactions, case.tax_filings, as_of=as_of,
        requested_amount_cents=principal, requested_tenor_months=6,
        pg_settlements=case.pg_settlements, marketplace=case.marketplace,
    )


def books_digest(case) -> str:
    """은행 거래·정산·세무 신고의 sha256. 마켓 자료는 넣지 않는다(Task 6이 의도적으로 바꾼다)."""
    books = {name: [row.model_dump(mode="json") for row in getattr(case, name)]
             for name in ("transactions", "pg_settlements", "tax_filings")}
    return hashlib.sha256(json.dumps(books, sort_keys=True).encode()).hexdigest()


@pytest.mark.parametrize("seller", sorted(V1_TABLE))
def test_usd_v1_verdicts_are_pinned(seller):
    d = explain(underwrite_at(seller, AS_OF).features, rules_for_version("usd-v1")).decision
    final, codes, cash, cash_codes = V1_TABLE[seller]
    assert (d.decision, {r.code for r in d.reason_codes}) == (final, codes)
    assert (d.cash_only.decision, {r.code for r in d.cash_only.reason_codes}) == (cash, cash_codes)
    assert d.max_amount_cents == (0 if final == I else 500_000)


@pytest.mark.parametrize("seller", sorted(V1_TABLE))
def test_existing_seller_books_do_not_change(seller):
    assert books_digest(case_at(seller, AS_OF)) == DIGESTS[seller]


def test_usd_underwriting_carries_settlement_features():
    bakery = underwrite_at("seller-bakery-20", AS_OF).features
    assert 12 <= bakery.pg_cash_conversion_days <= 20 and bakery.pg_advance_suspected_count == 0
    assert bakery.pg_offset_rate_trend_pp in (0.0, None)
    assert underwrite_at("seller-mugshop-21", AS_OF).features.pg_advance_suspected_count > 0
