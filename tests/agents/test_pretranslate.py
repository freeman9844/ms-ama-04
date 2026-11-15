import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from agents.pretranslate import (
    ALLOWLIST,
    BATCH_CHARS,
    BATCH_ITEMS,
    SOURCES,
    batches,
    extract,
    load_case_raw,
    owner_of,
    raw_container,
    tokens_kept,
    translation_doc,
    translation_id,
)

PROFILE = {
    "applicant_id": "ap1",
    "business_name": "성수커피랩",
    "business_reg_no": "123-45-67890",
    "representative_name": "김민수",
    "industry": "식품",
    "region": "서울",
    "business_age_months": 30,
}
TXN = {
    "txn_id": "t1",
    "booked_on": "2026-10-01",
    "amount_krw": -1_000_000,
    "balance_after_krw": 5_000_000,
    "description": "급여 이서연",
    "txn_type_code": "TRANSFER_OUT",
}


class FakeRaw:
    def __init__(self, blobs=None, error=None):
        self.blobs = blobs or {}
        self.error = error
        self.calls = []

    def get_json(self, container, name):
        self.calls.append((container, name))
        if self.error:
            raise self.error
        return self.blobs.get((container, name))


def test_extract_follows_allowlist_and_skips_non_korean():
    doc = {"application_id": "a1", "assessment": {"summary": "매출 안정", "claims": [{"text": "정산 지연"}, {"text": "OK"}],
           "secret": "비허용 경로"}}
    assert extract("applications", doc) == ["매출 안정", "정산 지연"]


def test_extract_dedupes_exact_strings_in_first_seen_order():
    doc = {"assessment": {"summary": "같은 문구", "claims": [{"text": "다른 문구"}, {"text": "같은 문구"}, {"text": "같은 문구 "}],
                          "review": {"issues": ["다른 문구"]}}}
    assert extract("applications", doc) == ["같은 문구", "다른 문구", "같은 문구 "]


def test_extract_handles_nested_lists_and_missing_or_mistyped_nodes():
    doc = {"assessment_run": {"rounds": [{"grounding_issues": ["근거 부족"], "review_issues": ["검토 지적"]}, {}, "x"]},
           "assessment": {"claims": None, "review": "문자열", "summary": 3}, "officer_decision": None}
    assert extract("applications", doc) == ["근거 부족", "검토 지적"]


def test_extract_reads_contact_records_in_alerts():
    doc = {"loan_id": "L", "contacts": [{"text": "상환일이 다가옵니다"}, {"text": None}], "summary": "입금 감소"}
    assert extract("alerts", doc) == ["입금 감소", "상환일이 다가옵니다"]


def test_extract_reads_proposal_checks_detail_and_decision_reason_in_alerts():
    doc = {"loan_id": "L", "proposal": {"checks": [{"id": "approved_script", "ok": True, "detail": "승인된 스크립트입니다."},
                                                    {"id": "contact_hours", "ok": True, "detail": "OK"}, {"id": "x"}],
                                        "decision_reason": "연락 시간이 아니라 반려한다"}}
    assert extract("alerts", doc) == ["승인된 스크립트입니다.", "연락 시간이 아니라 반려한다"]


def test_allowlist_covers_every_source():
    assert set(ALLOWLIST) == set(SOURCES)


def test_owner_by_source():
    assert owner_of("alerts", {"loan_id": "L"}) == "L" and owner_of("grievances", {"application_id": "A"}) == "A"
    assert owner_of("loans", {"loan_id": "L"}) == "L" and owner_of("applications", {"application_id": "A"}) == "A"
    assert owner_of("alerts", {}) is None


def test_tokens_kept_detects_dropped_or_changed_token():
    m = "[[BIZNAME_0123456789]] 매출"
    assert tokens_kept(m, "[[BIZNAME_0123456789]] sales")
    assert not tokens_kept(m, "Sales") and not tokens_kept(m, "[[BIZNAME_0123456780]] sales")
    assert not tokens_kept(m, "[[BIZNAME_0123456789]] [[PERSON_0123456789]] sales")
    assert tokens_kept("매출", "sales")


def test_load_case_raw_application_reads_profile_and_transactions():
    raw = FakeRaw({("consent-c1", "profile.json"): PROFILE, ("consent-c1", "transactions.json"): [TXN]})
    profile, txns = load_case_raw("applications", {"consent": {"consent_id": "c1"}}, raw, lambda c, i: None)
    assert profile.business_name == "성수커피랩" and [t.txn_id for t in txns] == ["t1"]


def test_load_case_raw_grievance_reads_application_first():
    raw = FakeRaw({("consent-c1", "profile.json"): PROFILE, ("consent-c1", "transactions.json"): [TXN]})
    reads = []

    def read(container, ident):
        reads.append((container, ident))
        return {"consent": {"consent_id": "c1"}}

    profile, _ = load_case_raw("grievances", {"application_id": "A1"}, raw, read)
    assert reads == [("applications", "A1")] and profile.applicant_id == "ap1"


def test_load_case_raw_alert_reads_loan_then_flow():
    raw = FakeRaw({("consent-u1", "profile.json"): PROFILE, ("consent-m1", "flow.json"): [TXN]})
    loan = {"underwriting_consent_id": "u1", "monitoring": {"consent_id": "m1"}}
    reads = []

    def read(container, ident):
        reads.append((container, ident))
        return loan

    profile, txns = load_case_raw("alerts", {"loan_id": "L1"}, raw, read)
    assert reads == [("loans", "L1")] and profile.business_name == "성수커피랩" and len(txns) == 1
    assert raw.calls == [("consent-u1", "profile.json"), ("consent-m1", "flow.json")]


def test_load_case_raw_loan_reads_profile_and_flow_directly():
    raw = FakeRaw({("consent-u1", "profile.json"): PROFILE, ("consent-m1", "flow.json"): [TXN]})
    loan = {"underwriting_consent_id": "u1", "monitoring": {"consent_id": "m1"}}
    assert load_case_raw("loans", loan, raw, lambda c, i: pytest.fail("읽으면 안 된다")) is not None


@pytest.mark.parametrize(
    ("source", "doc", "linked"),
    [
        ("applications", {"consent": {"consent_id": "c1"}}, None),
        ("applications", {"consent": None}, None),
        ("grievances", {"application_id": "A1"}, None),
        ("loans", {"underwriting_consent_id": "u1", "monitoring": None}, None),
        ("alerts", {"loan_id": "L1"}, None),
    ],
)
def test_load_case_raw_returns_none_when_blob_or_doc_missing(source, doc, linked):
    raw = FakeRaw({("consent-c1", "transactions.json"): [TXN], ("consent-u1", "profile.json"): PROFILE})
    assert load_case_raw(source, doc, raw, lambda c, i: linked) is None


def test_load_case_raw_returns_none_when_flow_blob_missing():
    raw = FakeRaw({("consent-u1", "profile.json"): PROFILE})
    loan = {"underwriting_consent_id": "u1", "monitoring": {"consent_id": "m1"}}
    assert load_case_raw("loans", loan, raw, lambda c, i: None) is None


def test_load_case_raw_propagates_store_errors():
    raw = FakeRaw(error=OSError("blob down"))
    with pytest.raises(OSError):
        load_case_raw("applications", {"consent": {"consent_id": "c1"}}, raw, lambda c, i: None)


def test_raw_container_matches_console():
    from console.views import raw_container as console_rc

    assert raw_container("c1") == console_rc("c1")


def test_translation_id_is_sha256_of_masked():
    assert translation_id("[[BIZNAME_0123456789]] 매출") == hashlib.sha256("[[BIZNAME_0123456789]] 매출".encode()).hexdigest()


def test_translation_doc_shape():
    at = datetime(2026, 11, 15, 10, 0, tzinfo=timezone(timedelta(hours=9)))
    assert translation_doc("A1", "applications", "매출", "Sales", "gpt-6-luna", at) == {
        "id": translation_id("매출"),
        "owner_id": "A1",
        "source": "applications",
        "en": "Sales",
        "model": "gpt-6-luna",
        "translated_at": "2026-11-15T10:00:00+09:00",
    }


def test_batches_split_by_items_and_chars():
    assert [len(b) for b in batches([f"t{i}" for i in range(BATCH_ITEMS + 1)])] == [BATCH_ITEMS, 1]
    assert [len(b) for b in batches(["가" * (BATCH_CHARS // 2 + 1)] * 2)] == [1, 1]
    assert [len(b) for b in batches(["가" * BATCH_CHARS, "나"])] == [1, 1]
    assert [len(b) for b in batches(["가" * (BATCH_CHARS - 1), "나"])] == [2]
    assert list(batches([])) == []
