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
