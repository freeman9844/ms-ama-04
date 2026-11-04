from collections import Counter, defaultdict
from datetime import date, timedelta

from agents.classify import Category, classify
from mockapis.sellerdata.personas import DEMO_PERSONAS
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
    balance = case.opening_balance_krw
    for t in case.transactions:
        balance += t.amount_krw
        assert t.balance_after_krw == balance


def test_window_and_chronological():
    case = _case()
    dates = [t.booked_on for t in case.transactions]
    assert dates == sorted(dates)
    assert date(2025, 10, 1) <= dates[0] < date(2025, 10, 4)
    assert dates[-1] < AS_OF
    assert len({t.txn_id for t in case.transactions}) == len(case.transactions)
    assert set(case.labels) == {t.txn_id for t in case.transactions}


def test_fashion_seasonality_spring_over_summer():
    rev = _revenue_by_month(_case())
    assert rev[4] > 1.2 * rev[8]


def test_monthly_volume_and_irregular_amounts():
    for applicant_id, persona in DEMO_PERSONAS.items():
        case = generate_case(applicant_id, Archetype.HEALTHY_SEASONAL, AS_OF, 3, persona=persona)
        per_month = Counter((t.booked_on.year, t.booked_on.month) for t in case.transactions)
        assert all(40 <= n <= 100 for n in per_month.values()), (applicant_id, per_month)
    case = _case()
    per_month = Counter((t.booked_on.year, t.booked_on.month) for t in case.transactions)
    assert 60 <= sum(per_month.values()) / len(per_month) <= 85
    irregular = sum(t.amount_krw % 1000 != 0 for t in case.transactions)
    assert irregular / len(case.transactions) > 0.8


def test_descriptions_keep_classify_v1_keywords():
    case = _case()
    rules = {t.txn_id: classify(t) for t in case.transactions}
    by_truth = defaultdict(set)
    for t in case.transactions:
        by_truth[case.labels[t.txn_id]].add(rules[t.txn_id])
    assert by_truth[TxnCategory.REVENUE] == {Category.REVENUE}
    assert by_truth[TxnCategory.RENT] == {Category.RENT}
    assert by_truth[TxnCategory.PAYROLL] == {Category.PAYROLL}
    assert by_truth[TxnCategory.LOAN_REPAYMENT] == {Category.LOAN_REPAYMENT}
    assert by_truth[TxnCategory.OWNER_TRANSFER] == {Category.OWNER_TRANSFER}
    assert by_truth[TxnCategory.TAX] == {Category.TAX}
    # 건강보험만 규칙이 잡고, 연금·고용산재는 의미 분류기로 간다.
    assert by_truth[TxnCategory.SOCIAL_INSURANCE] == {Category.SOCIAL_INSURANCE, Category.UNCLASSIFIED_OUT}
    for cat in (TxnCategory.SUPPLIER, TxnCategory.SHIPPING, TxnCategory.ADVERTISING, TxnCategory.CARD_PAYMENT,
                TxnCategory.SOLUTION_FEE):
        assert by_truth[cat] == {Category.UNCLASSIFIED_OUT}, cat


def test_tax_schedule_vat_and_income_tax():
    case = _case()
    taxes = [t for t in case.transactions if case.labels[t.txn_id] == TxnCategory.TAX]
    vat_months = {(t.booked_on.year, t.booked_on.month) for t in taxes if t.description == "부가가치세 국세청"}
    assert vat_months == {(2025, 10), (2026, 1), (2026, 4), (2026, 7)}  # 2026-10은 기준일(10/1) 뒤
    income = [t for t in taxes if t.description == "종합소득세 국세청"]
    assert [t.booked_on for t in income] == [date(2026, 6, 1)]  # 5/31 일요일 → 다음 영업일


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
    assert late_payroll_days and all(17 <= d <= 20 for d in late_payroll_days)


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


def test_inject_prompt_rewrites_one_toss_settlement():
    injected = _case(inject_prompt=True)
    hits = [t for t in injected.transactions if t.description == INJECTION_TEXT]
    assert len(hits) == 1
    assert hits[0].txn_type_code == "CARD_SETTLE" and hits[0].booked_on >= date(2025, 10, 4)
    assert all(t.description != INJECTION_TEXT for t in _case().transactions)
    assert len(injected.transactions) == len(_case().transactions)


def test_pg_transactions_match_settlements():
    case = _case()
    pg_txn_total = sum(t.amount_krw for t in case.transactions if t.txn_type_code == "PG_SETTLE")
    assert pg_txn_total == sum(s.net_krw for s in case.pg_settlements)
    assert {s.platform for s in case.pg_settlements} == {"스마트스토어", "쿠팡"}


def test_case_carries_persona_and_known_entities():
    persona = DEMO_PERSONAS["seller-candle-05"]
    case = generate_case("c5", Archetype.HEALTHY_SEASONAL, AS_OF, 5, persona=persona)
    assert case.persona == persona and case.profile.business_name == "연남캔들웍스"
    assert case.known_entities[case.profile.representative_name] == "PERSON"
    assert case.known_entities[case.profile.business_name] == "BIZNAME"
