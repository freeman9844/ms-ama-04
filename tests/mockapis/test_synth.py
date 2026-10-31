from collections import defaultdict
from datetime import date, timedelta
from statistics import mean

from mockapis.synth import INJECTION_TEXT, Archetype, TxnCategory, generate_case

AS_OF = date(2026, 10, 1)


def _case(arch=Archetype.HEALTHY_SEASONAL, seed=7, **kw):
    return generate_case("a1", arch, AS_OF, seed, **kw)


def _revenue_by_month(case):
    out = defaultdict(int)
    for t in case.transactions:
        if case.labels[t.txn_id] == TxnCategory.REVENUE:
            out[t.booked_on.month] += t.amount_krw
    return out


def _observed_revenue(case, start, end):
    return sum(
        t.amount_krw
        for t in case.transactions
        if case.labels[t.txn_id] == TxnCategory.REVENUE and start <= t.booked_on <= end
    )


def test_deterministic_for_same_seed():
    a, b = _case(), _case()
    assert a.transactions == b.transactions
    assert a.tax_filings == b.tax_filings
    assert a.profile == b.profile
    assert _case(seed=8).transactions != a.transactions


def test_balance_chain():
    case = _case()
    balance = 15_000_000
    for t in case.transactions:
        balance += t.amount_krw
        assert t.balance_after_krw == balance


def test_window_and_chronological():
    case = _case()
    dates = [t.booked_on for t in case.transactions]
    assert dates == sorted(dates)
    assert dates[0] == date(2025, 10, 1)
    assert dates[-1] < AS_OF
    assert len({t.txn_id for t in case.transactions}) == len(case.transactions)
    assert set(case.labels) == {t.txn_id for t in case.transactions}


def test_seasonality_summer_peak():
    rev = _revenue_by_month(_case())
    summer = mean([rev[7], rev[8]])
    others = mean([rev[m] for m in (3, 4, 5, 6, 9, 10, 11)])
    assert summer > 1.2 * others


def test_tax_ratio_by_archetype():
    mismatch = _case(Archetype.TAX_MISMATCH)
    assert len(mismatch.tax_filings) == 1
    f = mismatch.tax_filings[0]
    assert (f.period_start, f.period_end) == (date(2026, 1, 1), date(2026, 6, 30))
    assert f.declared_revenue_krw / _observed_revenue(mismatch, f.period_start, f.period_end) < 0.7

    healthy = _case()
    h = healthy.tax_filings[0]
    ratio = h.declared_revenue_krw / _observed_revenue(healthy, h.period_start, h.period_end)
    assert 0.95 <= ratio <= 1.05


def test_stressed_high_cost_lending_after_onset():
    case = _case(Archetype.STRESSED)
    onset = AS_OF - timedelta(days=60)
    assert case.stress_onset == onset
    hc = [t for t in case.transactions if case.labels[t.txn_id] == TxnCategory.HIGH_COST_LENDER]
    assert len(hc) >= 4
    assert all(t.booked_on >= onset for t in hc)
    assert any(case.labels[t.txn_id] == TxnCategory.LOAN_PROCEEDS for t in case.transactions)
    late_payroll_days = {
        t.booked_on.day
        for t in case.transactions
        if case.labels[t.txn_id] == TxnCategory.PAYROLL and t.booked_on >= onset
    }
    assert late_payroll_days == {20}


def test_healthy_has_no_stress_signals():
    case = _case()
    assert case.stress_onset is None
    cats = set(case.labels.values())
    assert TxnCategory.HIGH_COST_LENDER not in cats
    assert TxnCategory.LOAN_PROCEEDS not in cats


def test_thin_file():
    case = _case(Archetype.THIN_FILE)
    assert case.transactions[0].booked_on >= date(2026, 6, 1)
    assert case.tax_filings == ()
    assert case.profile.business_age_months < 12


def test_inject_prompt_adds_description():
    injected = _case(inject_prompt=True)
    hits = [t for t in injected.transactions if t.description == INJECTION_TEXT]
    assert len(hits) == 1
    assert hits[0].booked_on == date(2025, 10, 4)
    assert all(t.description != INJECTION_TEXT for t in _case().transactions)


def test_pg_transactions_match_settlements():
    case = _case()
    pg_txn_total = sum(t.amount_krw for t in case.transactions if t.txn_type_code == "PG_SETTLE")
    assert pg_txn_total == sum(s.net_krw for s in case.pg_settlements)
    assert len(case.pg_settlements) > 0


def test_known_entities_cover_profile_names():
    case = _case()
    assert case.known_entities[case.profile.representative_name] == "PERSON"
    assert case.known_entities[case.profile.business_name] == "BIZNAME"
