import json
from io import StringIO

import pytest

from opsmetrics.pilot import main, summarize


HEADER = "application_id,outcome,hands_on_minutes,labor_krw_per_hour,nonlabor_krw\n"


def report(rows, fixed="0", header=HEADER):
    return summarize(StringIO(header + rows), fixed_krw=fixed, basis="hypothetical")


def test_cli_counts_time_and_cost_of_all_completed_cases(tmp_path, capsys):
    path = tmp_path / "synthetic.csv"
    path.write_text(
        HEADER + "case-a,funded,30,12000,500\ncase-b,declined,15,24000,300\n"
        "case-c,withdrawn,5,6000,200\n",
        encoding="utf-8-sig",
    )
    main([str(path), "--basis", "hypothetical", "--fixed-krw", "1500"])
    result = json.loads(capsys.readouterr().out)
    assert result == {
        "basis": "hypothetical", "applications": 3, "funded_loans": 1,
        "hands_on_minutes": "50.00", "minutes_per_application": "16.67",
        "minutes_per_funded_loan": "50.00", "labor_krw": "12500.00",
        "nonlabor_krw": "1000.00", "fixed_krw": "1500.00", "total_krw": "15000.00",
        "krw_per_application": "5000.00", "krw_per_funded_loan": "15000.00",
    }


def test_no_funded_loans_keeps_unfunded_cost():
    result = report("a,withdrawn,2,10,7\nb,declined,2,10,1\n", fixed="1")
    assert result["total_krw"] == "9.67"  # aggregate before rounding
    assert result["krw_per_application"] == "4.83"
    assert result["krw_per_funded_loan"] is None
    assert result["minutes_per_funded_loan"] is None


def test_empty_period_retains_fixed_cost_but_no_unit_cost():
    result = summarize(StringIO(HEADER), fixed_krw="200", basis="observed")
    assert result["basis"] == "observed"
    assert result["total_krw"] == "200.00"
    assert result["krw_per_application"] is None
    assert result["minutes_per_application"] is None


@pytest.mark.parametrize(("rows", "message"), [
    ("a,funded,1,1,1\n a ,withdrawn,0,0,0\n", "duplicate application_id"),
    (",declined,0,0,0\n", "missing application_id"),
    ("a,funded,,1,0\n", "missing hands_on_minutes"),
    ("a,funded,1,1\n", "column count"),
    ("a,pending,1,1,1\n", "outcome"),
    ("a,funded,-1,1,1\n", "hands_on_minutes"),
    ("a,funded,1,inf,1\n", "labor_krw_per_hour"),
    ("a,funded,1,1,NaN\n", "nonlabor_krw"),
    ("a,funded,1,unknown,1\n", "labor_krw_per_hour"),
])
def test_invalid_case_is_rejected_not_dropped(rows, message):
    with pytest.raises(ValueError, match=message):
        report(rows)


@pytest.mark.parametrize("header", [
    "", HEADER.replace("nonlabor_krw", "hands_on_minutes"),
    HEADER.replace("nonlabor_krw", "name"), HEADER.rstrip() + ",name\n",
])
def test_only_minimized_schema_is_accepted(header):
    with pytest.raises(ValueError, match="header"):
        report("", header=header)


@pytest.mark.parametrize("fixed", ["", "-1", "NaN", "inf", "unknown"])
def test_invalid_period_cost_is_not_imputed(fixed):
    with pytest.raises(ValueError, match="fixed_krw"):
        report("", fixed=fixed)
