import json
import logging
from copy import deepcopy
from datetime import datetime

import pytest

from agents.pretranslate import BATCH_ITEMS, translation_id
from agents.runtime import GatewayThrottled
from agents.translator import TranslationResult
from journey.activities import Deps
from journey.models import KST
from journey.pretranslate import TRANSLATIONS, handle_changes

KEY = b"k" * 32
NOW = datetime(2026, 11, 15, 10, 0, tzinfo=KST)
APP = "app-1"
PROFILE = {
    "applicant_id": "ap1",
    "business_name": "가나상회",
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


class MemoryStore:
    def __init__(self):
        self.docs = {}

    def upsert(self, container, document):
        self.docs[(container, document["id"])] = deepcopy(document)

    def read(self, container, item_id, partition_key):
        doc = self.docs.get((container, item_id))
        owner = doc and (doc.get("owner_id") or doc.get("application_id") or doc.get("loan_id") or doc.get("consent_id"))
        return deepcopy(doc) if doc and owner == partition_key else None


class MemoryRaw:
    def __init__(self, blobs=None, error=None):
        self.blobs, self.error = blobs or {}, error

    def get_json(self, container, name):
        if self.error:
            raise self.error
        return self.blobs.get((container, name))


class FakeTranslator:
    def __init__(self, drop_tokens=False, throttle_first=False):
        self.calls, self.drop_tokens, self.throttle_first = [], drop_tokens, throttle_first

    def translate(self, texts):
        self.calls.append(list(texts))
        if self.throttle_first and len(self.calls) == 1:
            raise GatewayThrottled("rate_limited: 429")
        out = [f"EN({t})" for t in texts]
        if self.drop_tokens:
            out = [t.replace("[[", "").replace("]]", "") for t in out]
        return TranslationResult(out, 10, 5)


def make_deps(translator, raw=None, store=None):
    raw = raw or MemoryRaw({(f"consent-{cid}", name): data for cid in ("c1", "c2") for name, data in
                           (("profile.json", PROFILE), ("transactions.json", [TXN]))})
    store = store or MemoryStore()
    store.upsert("applications", app_doc())
    store.upsert("applications", {"id": "app-2", "application_id": "app-2", "consent": {"consent_id": "c2"}})
    for cid in ("c1", "c2", "u1", "m1"):
        store.upsert("consents", {"id": cid, "consent_id": cid, "status": "granted"})
    return Deps(
        store=store, raw=raw, mock=None, policy=None, events=None,
        pseudonym_key=KEY, clock=lambda: NOW, translator=translator, translate_model="gpt-test",
    )


def app_doc(**assessment):
    return {"id": APP, "application_id": APP, "consent": {"consent_id": "c1"},
            "assessment": {"schema_version": "2", "recommendation": "refer", "review": {"passed": True}, **assessment}}


def test_translates_masked_text_once_and_stores_by_owner():
    deps = make_deps(FakeTranslator())
    doc = app_doc(summary="가나상회 대표 010-1234-5678 확인")
    report = handle_changes(deps, "applications", [doc])
    sent = deps.translator.calls[0]
    assert "가나상회" not in sent[0] and "010-1234-5678" not in sent[0]
    assert "[[BIZNAME_" in sent[0] and "[[PHONE_" in sent[0]
    stored = deps.store.read(TRANSLATIONS, translation_id(sent[0]), APP)
    assert stored["en"].startswith("EN(") and stored["source"] == "applications" and stored["model"] == "gpt-test"
    assert "가나상회" not in json.dumps(stored, ensure_ascii=False) and stored["translated_at"].endswith("+09:00")
    assert report == {"owners": 1, "texts": 1, "translated": 1, "skipped": 0, "rejected": 0, "failed": 0,
                      "input_tokens": 10, "output_tokens": 5}
    again = handle_changes(deps, "applications", [doc])
    assert again["skipped"] == report["translated"] and again["translated"] == 0
    assert len(deps.translator.calls) == 1


def test_dropped_token_is_not_stored():
    deps = make_deps(FakeTranslator(drop_tokens=True))
    report = handle_changes(deps, "applications", [app_doc(summary="가나상회 매출 안정")])
    assert report["rejected"] == 1 and report["translated"] == 0
    assert not any(c == TRANSLATIONS for c, _ in deps.store.docs)


def test_empty_translation_is_rejected():
    translator = FakeTranslator()
    translator.translate = lambda texts: TranslationResult([""] * len(texts), 1, 1)
    deps = make_deps(translator)
    assert handle_changes(deps, "applications", [app_doc(summary="매출 안정")])["rejected"] == 1
    assert not any(c == TRANSLATIONS for c, _ in deps.store.docs)


def test_throttled_batch_is_skipped_and_others_continue():
    deps = make_deps(FakeTranslator(throttle_first=True))
    claims = [{"text": f"주장 {i}"} for i in range(BATCH_ITEMS + 1)]
    report = handle_changes(deps, "applications", [app_doc(claims=claims)])
    assert report["failed"] == BATCH_ITEMS and report["translated"] == 1
    assert len(deps.translator.calls) == 2 and sum(c == TRANSLATIONS for c, _ in deps.store.docs) == 1


def test_raw_error_skips_owner_without_calling_model():
    deps = make_deps(FakeTranslator(), raw=MemoryRaw(error=OSError("blob down")))
    report = handle_changes(deps, "applications", [app_doc(summary="가나상회 매출", claims=[{"text": "가나상회 매출"}, {"text": "다른"}])])
    assert report["failed"] == 2 and report["translated"] == 0
    assert deps.translator.calls == [] and not any(c == TRANSLATIONS for c, _ in deps.store.docs)


def test_missing_raw_never_sends_free_text():
    deps = make_deps(FakeTranslator(), raw=MemoryRaw())
    report = handle_changes(deps, "applications", [app_doc(summary="가나상회 대표 010-1234-5678")])
    assert deps.translator.calls == [] and report["translated"] == 0


@pytest.mark.parametrize("status", ["expired", "revoked"])
def test_stale_feed_event_after_deletion_skips_even_if_source_blobs_remain(status):
    deps = make_deps(FakeTranslator())
    deps.store.upsert("consents", {"id": "c1", "consent_id": "c1", "status": status, "deletion_proof_id": "c1"})
    report = handle_changes(deps, "applications", [app_doc(summary="가나상회 매출")])
    assert not deps.translator.calls and not report["translated"]


def test_stale_alert_from_deleted_replay_does_not_block_current_replay():
    raw = MemoryRaw({("consent-u1", "profile.json"): PROFILE, ("consent-m1", "flow.json"): [TXN]})
    deps = make_deps(FakeTranslator(), raw)
    deps.store.upsert("loans", {"id": APP, "loan_id": APP, "underwriting_consent_id": "u1",
                                "monitoring": {"replay_id": "current", "consent_id": "m1"}})
    alerts = [{"loan_id": APP, "replay_id": "deleted", "summary": "옛 경보"},
              {"loan_id": APP, "replay_id": "current", "summary": "현재 경보"}]
    handle_changes(deps, "alerts", alerts)
    assert deps.translator.calls == [["현재 경보"]]


def test_alert_uses_monitoring_raw_owner_is_loan():
    raw = MemoryRaw({("consent-u1", "profile.json"): PROFILE, ("consent-m1", "flow.json"): [TXN]})
    store = MemoryStore()
    store.upsert("loans", {"id": "L1", "loan_id": "L1", "underwriting_consent_id": "u1", "monitoring": {"consent_id": "m1", "replay_id": "r1"}})
    deps = make_deps(FakeTranslator(), raw=raw, store=store)
    alert = {"id": "a1", "loan_id": "L1", "replay_id": "r1", "summary": "가나상회 입금 감소", "signals": [{"text": "입금 감소"}]}
    report = handle_changes(deps, "alerts", [alert])
    assert report["owners"] == 1 and report["translated"] == 2
    sent = deps.translator.calls[0]
    assert "[[BIZNAME_" in sent[0] and "가나상회" not in "".join(sent)
    assert all(store.docs[k]["owner_id"] == "L1" for k in store.docs if k[0] == TRANSLATIONS)


def test_owners_are_grouped_and_documents_without_owner_ignored():
    deps = make_deps(FakeTranslator())
    other = {**app_doc(summary="매출 안정"), "id": "app-2", "application_id": "app-2", "consent": {"consent_id": "c2"}}
    docs = [app_doc(summary="매출 안정"), app_doc(summary="다른 문구"), other, {"assessment": {"summary": "주인 없음"}}]
    report = handle_changes(deps, "applications", docs)
    assert report["owners"] == 2 and report["texts"] == 3 and report["translated"] == 3


def test_nothing_to_translate_skips_raw_load(caplog):
    class CountingRaw(MemoryRaw):
        reads = 0

        def get_json(self, container, name):
            type(self).reads += 1
            return super().get_json(container, name)

    store = MemoryStore()
    store.upsert("loans", {"id": "L1", "loan_id": "L1", "underwriting_consent_id": "u1", "monitoring": {"consent_id": "m1"}})
    deps = make_deps(FakeTranslator(), raw=CountingRaw(error=OSError("blob down")), store=store)
    caplog.set_level(logging.DEBUG)
    report = handle_changes(deps, "loans", [{"id": "L1", "loan_id": "L1", "underwriting_consent_id": "u1", "monitoring": {"consent_id": "m1"}, "status": "active"}])
    assert report["owners"] == 1 and report["texts"] == 0 and report["failed"] == 0
    assert CountingRaw.reads == 0 and deps.translator.calls == []
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_without_translator_reports_zero():
    deps = make_deps(None)
    report = handle_changes(deps, "applications", [app_doc(summary="매출 안정")])
    assert set(report.values()) == {0}


def test_store_error_never_raises():
    deps = make_deps(FakeTranslator())
    deps.store.upsert = lambda *a: (_ for _ in ()).throw(RuntimeError("cosmos down"))
    report = handle_changes(deps, "applications", [app_doc(summary="매출 안정")])
    assert report["failed"] == 1 and report["translated"] == 0


def test_invalid_raw_blob_logs_no_profile_values(caplog):
    bad = {**PROFILE, "business_age_months": "삼십개월"}
    raw = MemoryRaw({("consent-c1", "profile.json"): bad, ("consent-c1", "transactions.json"): [TXN]})
    deps = make_deps(FakeTranslator(), raw=raw)
    caplog.set_level(logging.DEBUG)
    report = handle_changes(deps, "applications", [app_doc(summary="매출 안정")])
    assert report["failed"] == 1 and deps.translator.calls == []
    logged = "\n".join(r.getMessage() + (r.exc_text or "") + str(r.exc_info) for r in caplog.records)
    assert "ValidationError" in logged
    for text in ("가나상회", "김민수", "123-45-67890", "삼십개월", "급여 이서연", "매출 안정"):
        assert text not in logged


def test_owner_level_failure_logs_type_only(caplog):
    deps = make_deps(FakeTranslator())
    deps.store.read = lambda *a: (_ for _ in ()).throw(RuntimeError("가나상회 대표 김민수"))
    caplog.set_level(logging.DEBUG)
    handle_changes(deps, "applications", [app_doc(summary="매출 안정")])
    logged = "\n".join(r.getMessage() + (r.exc_text or "") + str(r.exc_info) for r in caplog.records)
    assert "가나상회" not in logged and "김민수" not in logged


def test_logs_have_counts_not_text(caplog):
    deps = make_deps(FakeTranslator(throttle_first=True))
    caplog.set_level(logging.DEBUG)
    handle_changes(deps, "applications", [app_doc(summary="가나상회 대표 010-1234-5678 확인", claims=[{"text": "별개 문구"}])])
    handle_changes(make_deps(FakeTranslator(), raw=MemoryRaw(error=OSError("x"))), "applications", [app_doc(summary="가나상회 매출")])
    logged = "\n".join(r.getMessage() + (r.exc_text or "") for r in caplog.records)
    assert "pretranslate source=applications owners=1" in logged
    for text in ("가나상회", "010-1234-5678", "[[BIZNAME_", "별개 문구", "EN("):
        assert text not in logged
