import pytest

from agents.evidence import build_catalog
from console.assessment import assessment_panels
from console.views import DECIDABLE, detail_view, load_raw, raw_container
from journey.activities import raw_container as journey_raw_container
from shared.contracts import UsdProduct
from tests.console.test_assessment import usd_doc


def test_raw_container_matches_orchestrator():
    assert raw_container("c-1") == journey_raw_container("c-1") == "consent-c-1"


def test_load_raw_reads_profile_and_transactions(raw, make_doc):
    data = load_raw(raw, make_doc())
    assert data.profile.business_name == "빈스카페"
    assert [t.txn_id for t in data.transactions] == ["t1", "t2", "t3"]
    assert ("consent-c-1", "profile.json") in raw.reads


def test_load_raw_is_none_without_consent_or_blobs(make_doc, fake_raw):
    assert load_raw(fake_raw(), make_doc(consent=None)) is None
    assert load_raw(fake_raw({}), make_doc()) is None


def test_restore_shows_real_names_in_assessment_and_transactions(raw, make_doc, key):
    view = detail_view(make_doc(), load_raw(raw, make_doc()), key, restore=True)
    assert view["pii"] == "restored"
    assert view["assessment"]["summary"] == "빈스카페(대표 김대표)는 계절성이 있다"
    assert view["claims"][0]["text"] == "김대표 개인이체가 월 100만원이다"
    assert view["transactions"] == [
        {"txn_id": "t2", "booked_on": "2026-09-02", "amount_krw": -1_000_000, "description": "김대표 개인이체"}
    ]


def test_rejected_drafts_are_audit_only_even_with_restore(raw, make_doc, key):
    run = {"rounds": [{"round": 1, "review_issues": ["감액 대출 311만 원"]}]}
    doc = make_doc(assessment_run=run)
    for restore in (True, False):
        assert "311만 원" not in str(detail_view(doc, load_raw(raw, doc), key, restore=restore))
    assert doc["assessment_run"] == run


def test_without_restore_everything_stays_pseudonymized(raw, make_doc, key):
    view = detail_view(make_doc(), load_raw(raw, make_doc()), key, restore=False)
    assert view["pii"] == "masked"
    assert "김대표" not in view["assessment"]["summary"] and "[[PERSON_" in view["assessment"]["summary"]
    assert "빈스카페" not in view["assessment"]["summary"]
    assert view["transactions"][0]["description"].startswith("[[PERSON_")
    assert "김대표" not in str(view)


def test_restore_without_raw_data_keeps_tokens(make_doc, key):
    view = detail_view(make_doc(), None, key, restore=True)
    assert view["pii"] == "unavailable"
    assert "[[PERSON_" in view["assessment"]["summary"]
    assert view["transactions"] == []


def test_claim_evidence_values_come_from_catalog(raw, make_doc, key):
    view = detail_view(make_doc(), load_raw(raw, make_doc()), key, restore=False)
    assert view["claims"][0]["evidence"] == [("M:dscr", 1.8), ("M:unknown", None)]
    assert view["claims"][1]["evidence"] == [("S:2026-09:index", 1.0), ("P:R101:threshold", 1.25)]
    assert view["claims"][0]["txn_ids"] == ["t2"]


def test_early_stage_document_has_empty_sections(make_doc, key):
    doc = make_doc(status="received", underwriting=None, policy=None, assessment=None, consent=None)
    view = detail_view(doc, None, key, restore=True)
    assert view["assessment"] is None
    assert view["claims"] == [] and view["transactions"] == []
    assert view["decidable"] is False


@pytest.mark.parametrize(("tags", "limit", "label"), [
    ({"currency": "USD"}, 0, "$0"), ({"product_version": "usd-v1", "language": "en"}, 410000, "$4100"),
])
def test_partial_policy_tags_are_only_accepted_for_usd_display(key, tags, limit, label):
    doc = usd_doc()
    doc["policy"] = {k: v for k, v in doc["policy"].items() if k not in UsdProduct.model_fields}
    doc["policy"].update(tags, max_amount_cents=limit)
    v = detail_view(doc, None, key, restore=False)
    assert v["max_amount_cents"] == limit and v["policy"] == doc["policy"]
    p = assessment_panels(v)
    assert label in p["summary"]["gauge_sub"] and p["limit"] is None and p["limit_note"]
    assert {k for k in UsdProduct.model_fields if k in doc["policy"]} == tags.keys()
    with pytest.raises(ValueError):
        build_catalog(doc["underwriting"]["features"], {}, [], doc["policy"])


@pytest.mark.parametrize("corruption", [{"currency": "KRW"}, {"currency": "USD", "max_amount_krw": 0}])
def test_partial_policy_rejects_explicit_krw_even_without_underwriting(key, corruption):
    doc = usd_doc()
    doc.pop("underwriting")
    doc["policy"] = {"max_amount_cents": 0, **corruption}
    with pytest.raises(ValueError):
        detail_view(doc, None, key, restore=False)


def test_decidable_only_while_waiting_for_officer(make_doc, key):
    assert DECIDABLE == {"awaiting_officer", "escalated"}
    for status, expected in (("awaiting_officer", True), ("escalated", True), ("approved", False)):
        assert detail_view(make_doc(status=status), None, key, restore=False)["decidable"] is expected
    assert detail_view(make_doc(), None, key, restore=False)["max_amount_krw"] == 20_000_000
