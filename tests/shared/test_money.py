import pytest
from pydantic import ValidationError

from console import customer
from journey import validation
from journey.models import StartRequest
from shared import contracts


USD = {"product_version": "usd-v1", "currency": "USD", "language": "en"}
START = {"application_id": "app-1", "applicant_id": "seller", "requested_tenor_months": 6}


def test_usd_input_bounds():
    from shared.money import parse_usd_cents

    assert parse_usd_cents("5,000.01") == 500001
    assert customer.parse_requested_usd_cents("100.00") == 10000
    assert customer.parse_requested_usd_cents("10000.00") == 1000000
    for text in ("99.99", "10000.01", "1.001", "100.001", "-100", "1,00.00", "1e3"):
        with pytest.raises(ValueError):
            customer.parse_requested_usd_cents(text)


def test_ledger_minor_units_are_not_limited_to_product_amounts():
    from shared.money import format_usd, parse_usd_cents

    assert parse_usd_cents("0.01") == 1
    assert format_usd(-1) == "-$0.01"
    assert format_usd(1000001) == "$10,000.01"
    with pytest.raises((TypeError, ValueError)):
        format_usd(1.5)
    txn = contracts.UsdTransaction.model_validate({
        **USD, "txn_id": "debit", "booked_on": "2026-11-24", "amount_cents": -1,
        "balance_after_cents": 0, "description": "Repayment", "txn_type_code": "AUTO_DEBIT",
    })
    assert txn.model_dump()["amount_cents"] == -1
    for extra in ({"amount_cents": 0}, {"amount_cents": 1.5}, {"amount_krw": -1}):
        with pytest.raises(ValidationError):
            contracts.UsdTransaction.model_validate(txn.model_dump() | extra)


def test_product_version_drives_currency():
    legacy = {**START, "requested_amount_krw": 500000}
    new = {**START, **USD, "requested_amount_cents": 500001}
    assert isinstance(validation.parse_start_request(legacy), StartRequest)
    assert validation.parse_start_request(new).model_dump(exclude_defaults=True) == new
    for field in USD:
        with pytest.raises(ValidationError):
            validation.parse_start_request({k: v for k, v in new.items() if k != field})
    for extra in ({"requested_amount_krw": 500001}, {"product_version": "future"},
                  {"currency": "KRW"}, {"language": "ko"}, {"requested_amount_cents": 9999},
                  {"requested_amount_cents": 1000001}, {"requested_amount_cents": "500001"},
                  {"requested_tenor_months": 2}, {"requested_tenor_months": 7}):
        with pytest.raises(ValidationError):
            validation.parse_start_request(new | extra)
    with pytest.raises(ValidationError):
        validation.parse_start_request(legacy | USD)


def test_legacy_form_does_not_accept_mixed_currency_input():
    form = {**START, "requested_tenor_months": "6", "requested_amount_krw": "500000"}
    for extra in (USD, {"requested_amount_cents": "500000"}):
        assert customer.validate_request(form | extra, ("seller",)) == "amount_invalid"
