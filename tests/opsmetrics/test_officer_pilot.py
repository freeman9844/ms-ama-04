from io import StringIO

import pytest

HEADER = "case_id,evidence_version,policy_result,reviewer,arm,hands_on_minutes,labor_krw_per_hour,nonlabor_krw,requests,errors,decision,override_reason\n"
A = "a,v1,refer,r1,no_memo,30,20000,0,2,1,refer,\n"
B = "a,v1,refer,r2,memo,18,20000,150,1,0,eligible,verified_evidence\n"


def test_only_complete_matched_reviews_enter_cost_and_quality_comparison():
    from opsmetrics import officer_pilot

    r = officer_pilot.compare(StringIO(HEADER + A + B + A.replace("a,v1", "b,v1")), basis="hypothetical")
    assert (r["cases"], r["complete_pairs"], r["incomplete_cases"]) == (2, 1, 1)
    assert r["basis"] == "hypothetical"
    assert r["reviewer_assignment"] == "confounded"
    assert r["memo"]["minutes_per_application"] == "18.00"
    assert r["no_memo"]["krw_per_application"] == "10000.00"
    assert r["memo"]["krw_per_application"] == "6150.00"
    assert (r["memo"]["overrides"], r["memo"]["requests"], r["no_memo"]["errors"]) == (1, 1, 1)
    assert r["memo"]["override_reasons"] == {"verified_evidence": 1}
    empty = officer_pilot.compare(StringIO(HEADER + A), basis="observed")
    assert empty["memo"]["krw_per_application"] is None
    assert empty["memo"]["errors"] is None and empty["no_memo"]["overrides"] is None
    assert empty["reviewer_assignment"] == "unobserved"
    other_a = A.replace("a,v1", "b,v1").replace("r1", "r2")
    other_b = B.replace("a,v1", "b,v1").replace("r2", "r1")
    assert officer_pilot.compare(StringIO(HEADER + A + B + other_a + other_b), basis="observed")["reviewer_assignment"] == "balanced"


@pytest.mark.parametrize("row", [B.replace("v1", "v2"), B.replace("r2", "r1"), A, B.replace(",verified_evidence", ",")])
def test_rejects_unmatched_inputs_duplicate_exposure_and_unexplained_override(row):
    from opsmetrics import officer_pilot

    with pytest.raises(ValueError):
        officer_pilot.compare(StringIO(HEADER + A + row), basis="observed")
