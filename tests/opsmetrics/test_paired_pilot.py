from dataclasses import replace
from datetime import date

import pytest

from mockapis.demo import build_demo_cases


@pytest.fixture(scope="module")
def sellers():
    return build_demo_cases(date(2026, 11, 1))


def test_suspension_changes_policy_not_an_officer_decision(sellers):
    from opsmetrics.paired_pilot import compare

    seller = "seller-petfood-07"
    report = compare({seller: sellers[seller]})
    row, = report["pairs"]
    bank, market = row["bank"], row["bank_marketplace"]
    assert bank["decision"] != "ineligible"
    assert market["decision"] == "ineligible" and "R003" in market["reason_codes"]
    assert row["decision_changed"] and row["limit_delta_krw"] == -bank["limit_krw"] < 0
    for arm in (bank, market):
        assert arm["human_override"] is None and arm["hands_on_minutes"] is None
    assert report["decision_changed_pairs"] == report["marketplace_observed_pairs"] == 1


def test_missing_marketplace_is_unexposed_and_pg_tax_cannot_affect_either_arm(sellers):
    from opsmetrics.paired_pilot import compare

    seller = "seller-mugshop-21"
    case = sellers[seller]
    without_other_sources = replace(case, tax_filings=(object(),), pg_settlements=(object(),))
    assert compare({seller: case}) == compare({seller: without_other_sources})
    report = compare({seller: replace(case, marketplace=None)})
    row, = report["pairs"]
    assert report["marketplace_observed_pairs"] == 0
    assert not row["bank_marketplace"]["marketplace_available"]
    assert row["bank"] == row["bank_marketplace"]
    assert row["limit_delta_krw"] == 0 and not row["decision_changed"]


def test_console_cohort_separates_policy_changes_from_new_review_reasons():
    from opsmetrics.paired_pilot import compare

    cases = build_demo_cases(date(2026, 11, 20))
    expected = {
        "seller-bakery-20": (("eligible", 5_000_000, set()), ("eligible", 5_000_000, set())),
        "seller-knitroom-04": (("ineligible", 0, {"R001", "R104", "R107"}), ("refer", 5_000_000, {"S001", "R107"})),
        "seller-petfood-07": (("eligible", 5_000_000, set()), ("ineligible", 0, {"R003"})),
        "seller-ceramic-08": (("refer", 5_000_000, {"R107"}), ("refer", 5_000_000, {"R107", "R109"})),
    }
    report = compare({k: cases[k] for k in expected}, console_cohort=True)
    for row in report["pairs"]:
        a, b = row["bank"], row["bank_marketplace"]
        assert tuple((arm["decision"], arm["limit_krw"], set(arm["reason_codes"])) for arm in (a, b)) == expected[row["case_id"]]
    assert report["decision_changed_pairs"] == 2 and report["reasons_only_pairs"] == 1
