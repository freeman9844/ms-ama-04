"""허용 목록(agents.pretranslate.ALLOWLIST)이 영문 화면의 데이터 문구를 모두 덮는지 본다.

문서를 실제 사전 번역 경로(handle_changes)로 번역해 저장한 뒤 영문 화면을 그려, 브라우저 번역을 기다리는
data-mt 문구가 남지 않아야 한다. 두 벌로 본다.
- 미리보기 픽스처의 모든 문서(유효 동의·원천 Blob을 추가).
- 합성 사례: 픽스처에 없는 이의·연락 기록·추심 제안·플랜 변경 결정과 원천 Blob(상호·대표자 이름)이 있는 신청·대출.
"""

import html
import json
import re
from copy import deepcopy
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from agents.data_assembly import case_entities
from agents.pretranslate import SOURCES, owner_of
from agents.translator import TranslationResult
from console.app import create_app
from consolepreview import fixtures
from journey.activities import Deps
from journey.models import KST
from journey.pretranslate import TRANSLATIONS, handle_changes
from shared.contracts import ApplicantProfile, Transaction
from shared.pseudonymize import Pseudonymizer
from tests.console.test_i18n import CUSTOMER_PAGES, EN_PAGES

PAGES = [*EN_PAGES, *CUSTOMER_PAGES]
EN_HEADERS = {"Cookie": "ama_locale_v2=en"}

# 콘솔 코드가 조립해 저장 문서에 그대로 없는 문구(문구 → 이유). 문서 경로에서 온 문구는 ALLOWLIST에 더한다.
COMPOSED: dict[str, str] = {
    "동의 만료 예정 안내": "consents 컨테이너 문서의 notices[] 제목(사전 번역 소스 아님). 브라우저 번역에 맡긴다",
    "2027년 1월 30일에 만료됩니다.": "consents 컨테이너 문서의 notices[] 본문(사전 번역 소스 아님). 브라우저 번역에 맡긴다",
}

APP, APPLICANT, REPLAY = "app-syn-1", "syn-applicant-1", "rp-syn-1"
BIZ, REP = "다온상점", "박서준"
PROFILE = {
    "applicant_id": APPLICANT, "business_name": BIZ, "business_reg_no": "321-54-98765",
    "representative_name": REP, "industry": "식품", "region": "서울", "business_age_months": 30,
}
FLOW = [{"txn_id": "t1", "booked_on": "2026-11-09", "amount_krw": 1_200_000, "balance_after_krw": 3_000_000,
         "description": "정산 입금", "txn_type_code": "PG_SETTLE"}]
BLOBS = {
    ("consent-c-syn", "profile.json"): PROFILE,
    ("consent-c-syn", "transactions.json"): FLOW,
    ("consent-m-syn", "flow.json"): FLOW,
}
SYN_PAGES = [
    f"/applications/{APP}", f"/loans/{APP}", f"/grievances/{APP}/grv-syn-pending", f"/grievances/{APP}/grv-syn-done",
    f"/legacy/borrower/{APPLICANT}/notices", f"/legacy/borrower/{APPLICANT}/consents", f"/legacy/borrower/{APPLICANT}/grievances",
]


class FixtureStore:
    """문서(읽기)와 translations(쓰기)를 한 저장소처럼 보이게 한다. 번역은 실제처럼 (owner, id)로 구분한다."""

    def __init__(self, applications, loans):
        self.docs = {"applications": applications, "loans": loans}
        self.translations: dict[tuple[str, str], dict] = {}

    def read(self, container, item_id, partition_key):
        if container == TRANSLATIONS:
            doc = self.translations.get((partition_key, item_id))
            return deepcopy(doc) if doc else None
        return self.docs.get(container, {}).get(item_id)

    def upsert(self, container, document):
        assert container == TRANSLATIONS
        self.translations[(document["owner_id"], document["id"])] = deepcopy(document)


class MemoryRaw:
    def __init__(self, blobs):
        self.blobs = blobs

    def get_json(self, container, name):
        return self.blobs.get((container, name))


class FakeTranslator:
    def translate(self, texts):
        return TranslationResult([f"EN({t})" for t in texts], 1, 1)


def _pretranslate(deps, documents, raw):
    """문서를 소스별로 handle_changes에 넣는다. (저장소, 소스별 보고)."""
    store = FixtureStore({d["id"]: d for d in documents["applications"]}, {d["id"]: d for d in documents["loans"]})
    ids = {d["consent"]["consent_id"] for d in documents["applications"]}
    ids.update(d["monitoring"]["consent_id"] for d in documents["loans"] if d.get("monitoring"))
    store.docs["consents"] = {cid: deps.privacy.get_consent(cid) for cid in ids}
    pre = Deps(
        store=store, raw=raw, mock=None, policy=None, events=None, pseudonym_key=deps.pseudonym_key,
        clock=lambda: datetime(2026, 11, 15, 10, 0, tzinfo=KST), translator=FakeTranslator(),
        translate_model="gpt-test",
    )
    reports = {source: handle_changes(pre, source, documents[source]) for source in SOURCES}
    assert all(r["failed"] == r["rejected"] == 0 for r in reports.values()), reports
    return store, pre, reports


def _items(store):
    items: dict[str, dict[str, str]] = {}
    for doc in store.translations.values():
        items.setdefault(doc["owner_id"], {})[doc["id"]] = doc["en"]
    return items


def _pending(page_html):
    return {html.unescape(t) for t in re.findall(r'data-mt="([^"]*)"', page_html)}


def _render(deps, items, page):
    deps.translations = fixtures.InMemoryTranslations(items)
    deps.translator = FakeTranslator()
    r = TestClient(create_app(deps), follow_redirects=False).get(page, headers=EN_HEADERS)
    assert r.status_code == 200, (page, r.status_code)
    return r.text


def _left(deps, page_html):
    masker = Pseudonymizer(deps.pseudonym_key, case_entities(None, ()))
    return _pending(page_html) - {masker.mask(t) for t in COMPOSED}


def _preview_documents(deps):
    return {
        "applications": list(deps.store.docs.values()),
        "loans": list(deps.monitoring.loans.values()),
        "alerts": deps.monitoring.alerts,
        "grievances": [],
    }


@pytest.fixture(scope="module")
def preview_world():
    from tests.console.test_translate import active_sources

    deps = fixtures.build_deps()
    active_sources(deps)
    documents = _preview_documents(deps)
    store, _, _ = _pretranslate(deps, documents, deps.raw)
    assert all(owner_of(s, d) for s, docs in documents.items() for d in docs)
    return deps, _items(store)


@pytest.mark.parametrize("page", ["/applications/app-20261103-p01", "/loans/app-20261103-p03"])
def test_pages_wait_for_browser_translation_without_stored_ones(preview_world, page):
    deps, _ = preview_world
    assert _pending(_render(deps, {}, page))


@pytest.mark.parametrize("page", PAGES)
def test_allowlist_covers_every_preview_page(preview_world, page):
    deps, items = preview_world
    left = _left(deps, _render(deps, items, page))
    assert not left, f"{page}: 사전 번역이 덮지 못한 문구(마스킹) {sorted(left)}"


def test_second_pass_skips_stored_translations(preview_world):
    deps, _ = preview_world
    documents = _preview_documents(deps)
    store, pre, first = _pretranslate(deps, documents, deps.raw)
    assert sum(r["translated"] for r in first.values()) > 0
    again = {source: handle_changes(pre, source, documents[source]) for source in SOURCES}
    assert sum(r["translated"] for r in again.values()) == 0
    assert sum(r["skipped"] for r in again.values()) == sum(r["translated"] for r in first.values())


# ── 합성 사례 ──


def _swap(doc):
    text = json.dumps(doc, ensure_ascii=False).replace("app-20261103-p03", APP).replace("rp-preview-1", REPLAY)
    return json.loads(text)


class SyntheticPrivacy(fixtures.EmptyPrivacy):
    def __init__(self, consents, grievances):
        self.consents, self.grievances = consents, grievances

    def consents_for_applicant(self, applicant_id, *, legacy: bool = False):
        return [c for c in self.consents if c["applicant_id"] == applicant_id]

    def consents_for_application(self, application_id):
        return [c for c in self.consents if c["application_id"] == application_id]

    def get_consent(self, consent_id):
        return next((c for c in self.consents if c["id"] == consent_id), None)

    def list_grievances(self, limit, *, legacy: bool = False):
        return self.grievances[:limit]

    def grievances_for_applicant(self, applicant_id, *, legacy: bool = False):
        return [g for g in self.grievances if g["applicant_id"] == applicant_id]

    def get_grievance(self, application_id, grievance_id):
        return next((g for g in self.grievances if g["grievance_id"] == grievance_id), None)


def _application(base, mask):
    doc = deepcopy(base)
    doc.update(
        id=APP, application_id=APP, applicant_id=APPLICANT, status="approved",
        consent={"consent_id": "c-syn", "scopes": ["account"], "expires_at": "2027-01-30T09:00:00+09:00"},
        officer_decision={"decision": "approve", "officer_id": "oid-officer", "amount_krw": 5_000_000,
                          "conditions": [], "reason": f"{REP} 대표 면담 결과를 반영했다",
                          "at": "2026-11-04T10:00:00+09:00"},
    )
    a = doc["assessment"]
    a["application_id"] = APP
    a["summary"] = f"{BIZ}(대표 {REP})은 계절성이 있다"
    a["claims"][0]["text"] = f"대표자 {REP} 월평균 인출 120만 원"
    a["review"]["issues"] = [f"{BIZ} 매출 근거가 부족하다"]
    return doc


def _loan_and_alerts(mask):
    loans, alerts = fixtures.loans()
    loan, alerts = _swap(loans[0]), [_swap(a) for a in alerts]
    loan.update(applicant_id=APPLICANT, underwriting_consent_id="c-syn")
    loan["monitoring"]["consent_id"] = "m-syn"
    loan["repayment"]["plan_change"] = {
        "change_id": f"pc-{REPLAY}", "option": "move", "replaces": ["i3"],
        "items": [{"no": 3, "due_on": "2027-02-05", "amount_krw": 450_000}],
        "status": "declined", "requested_at": "2026-11-05T09:00:00+09:00", "officer_id": "oid-officer",
        "decided_at": "2026-11-05T10:00:00+09:00", "reason": f"{REP} 대표의 매출 회복 전이라 반려한다",
    }
    medium, high, case = alerts
    medium.update(
        summary=mask(f"{BIZ} 매출이 기준 대비 줄었다"),
        collections={"decision": "routed", "reason": mask(f"{BIZ} 상환 상담이 필요하다"), "contacts": []},
    )
    medium["signals"][0]["text"] = mask(f"{REP} 대표 계좌 입금이 줄었다")
    high["summary"] = mask(f"{BIZ} 정산 보류로 현금 버퍼가 줄었다")
    loan["monitoring"]["escalation"] = {"stage1": {"alert_id": high["alert_id"], "on": "2027-01-10", "status": "sent",
                                                   "reason": None}}
    high["check_action"] = mask(f"{REP} 대표에게 정산 보류 사유를 확인한다")
    high["restructure"] = {
        "request_id": f"rs-{high['id']}", "category": "debt_overload", "note": mask(f"{BIZ} 고금리 차입 중첩"),
        "status": "approved", "created_at": "2026-11-05T09:00:00+09:00", "officer_id": "oid-officer",
        "decided_at": "2026-11-05T10:00:00+09:00", "reason": f"{REP} 대표 사정을 반영해 승인한다",
    }
    case["recent_alert"] = {**case["recent_alert"], "summary": high["summary"], "check_action": high["check_action"]}
    sent = f"{REP} 대표님, 상환 일정 상담을 도와드리겠습니다."
    case["proposal"] = {
        "status": "modified", "script_id": "hardship_offer", "text": "대표님, 상환 일정 상담을 도와드리겠습니다.",
        "hardship": {"category": "debt_overload", "note": mask(f"{BIZ} 고금리 차입 중첩")},
        "reason": mask(f"{BIZ} 정산 보류가 이어져 상담 문구를 제안한다"), "by": "agent",
        "evidence": {
            "installment_no": 3, "due_on": "2027-01-31", "amount_krw": 450_000, "missed_count": 1,
            "recent_alert": {"severity": "high", "primary_cause": "settlement_hold", "summary": high["summary"],
                             "check_action": high["check_action"]},
            "contacts_7d": 1, "escalation": [],
        },
        "checks": [{"id": "approved_script", "ok": True, "detail": "승인된 스크립트입니다."},
                   {"id": "contact_hours", "ok": False, "detail": "연락 가능 시간(08:00~21:00)이 아닙니다."}],
        "created_at": "2027-01-31T10:00:00+09:00", "officer_id": "oid-officer",
        "decided_at": "2027-01-31T11:00:00+09:00", "decision_reason": f"{REP} 대표와 통화한 뒤 문구를 고쳤다",
        "sent_text": sent,
        "contact": {"ref": case["case_id"], "on": "2027-01-31", "at": "2027-01-31T11:00:00+09:00",
                    "script_id": "hardship_offer", "status": "sent", "reason": None, "by": "officer",
                    "officer_id": "oid-officer", "text": sent},
    }
    case["restructure"] |= {"officer_id": "oid-officer", "decided_at": "2027-02-01T10:00:00+09:00", "status": "approved",
                            "note": mask(f"{BIZ} 고금리 차입 중첩"), "reason": f"{REP} 대표와 합의해 승인한다"}
    contacts = {
        "id": f"contacts-{REPLAY}", "loan_id": APP, "replay_id": REPLAY,
        "contacts": [
            {"ref": f"{REPLAY}-s1", "on": "2027-01-10", "at": "2027-01-10T10:00:00+09:00",
             "script_id": "balance_warning", "status": "sent", "reason": None, "by": "early_warning",
             "text": f"{BIZ} 대표님, 다음 상환일 전 잔고가 부족할 수 있습니다."},
            {"ref": case["case_id"], "on": "2027-01-31", "at": "2027-01-31T11:00:00+09:00",
             "script_id": "hardship_offer", "status": "sent", "reason": None, "by": "officer",
             "officer_id": "oid-officer", "text": sent},
        ],
    }
    return loan, [medium, high, case], contacts


def _grievances():
    record = {
        "status": "approved",
        "policy": {"decision": "eligible", "max_amount_krw": 20_000_000, "rate_band": "B", "reason_codes": [],
                   "rules_version": "2026.11-v1"},
        "assessment": {"summary": f"{BIZ}은 계절성이 있다", "counterfactuals": []},
        "officer_decision": {"decision": "approve", "officer_id": "oid-officer", "amount_krw": 5_000_000,
                             "conditions": [], "reason": f"{REP} 대표 면담 결과를 반영했다"},
    }
    base = {"application_id": APP, "applicant_id": APPLICANT, "submitted_at": "2026-11-06T11:00:00+09:00",
            "record": record, "decision_attempts": [], "resolved_at": None}
    pending = {"id": "grv-syn-pending", "grievance_id": "grv-syn-pending", "status": "pending_officer",
               "text": f"{REP}입니다. 한도가 너무 낮으니 010-1234-5678로 연락 주세요", "decision": None, "reply": None, **base}
    done = {"id": "grv-syn-done", "grievance_id": "grv-syn-done", "status": "resolved_upheld",
            "text": f"{BIZ}의 매출을 다시 봐 주세요",
            "decision": {"outcome": "uphold", "officer_id": "oid-manager", "reason": f"{BIZ} 한도는 정책 한도다",
                         "at": "2026-11-07T12:00:00+09:00"},
            "reply": {"title": "이의제기 처리 결과", "body": f"{REP} 님, 원결정을 유지합니다.", "source": "agent",
                      "at": "2026-11-07T12:00:00+09:00"},
            **base, "resolved_at": "2026-11-07T12:00:00+09:00"}
    return [pending, done]


def _consents():
    return [{
        "id": "c-syn", "consent_id": "c-syn", "application_id": APP, "applicant_id": APPLICANT, "scopes": ["account"],
        "purpose": "운전자금 대출 심사", "granted_at": "2026-11-01T09:00:00+09:00",
        "expires_at": "2027-01-30T09:00:00+09:00", "status": "granted",
        "notices": [{"kind": "renewal", "at": "2027-01-23T09:00:00+09:00", "title": "동의 만료 예정 안내",
                     "body": "2027년 1월 30일에 만료됩니다.", "source": "agent", "issues": []}],
    }]


def _synthetic_world():
    deps = fixtures.build_deps()
    deps.clock = lambda: datetime(2027, 2, 1, 10, tzinfo=KST)
    mask = Pseudonymizer(deps.pseudonym_key, case_entities(
        ApplicantProfile.model_validate(PROFILE), [Transaction.model_validate(t) for t in FLOW])).mask
    application = _application(deps.store.docs["app-20261103-p01"], mask)
    loan, alerts, contacts = _loan_and_alerts(mask)
    grievances = _grievances()
    deps.store.docs[APP] = application
    deps.monitoring.loans[APP] = loan
    deps.monitoring.alerts.extend(alerts)
    deps.monitoring.get_contacts = lambda loan_id, replay_id: contacts if (loan_id, replay_id) == (APP, REPLAY) else None
    deps.privacy = SyntheticPrivacy(_consents() + [_consents()[0] | {"id": "m-syn", "consent_id": "m-syn", "notices": []}], grievances)
    deps.raw = MemoryRaw(BLOBS)
    documents = {"applications": [application], "loans": [loan], "alerts": [*alerts, contacts],
                 "grievances": grievances}
    return deps, documents


@pytest.fixture(scope="module")
def synthetic_world():
    deps, documents = _synthetic_world()
    store, _, _ = _pretranslate(deps, documents, deps.raw)
    return deps, documents, _items(store)


@pytest.mark.parametrize("page", SYN_PAGES)
def test_allowlist_covers_grievance_contact_proposal_and_entity_texts(synthetic_world, page):
    deps, _, items = synthetic_world
    left = _left(deps, _render(deps, items, page))
    assert not left, f"{page}: 사전 번역이 덮지 못한 문구(마스킹) {sorted(left)}"


def test_loan_page_shows_stored_english_for_checks_decision_reason_check_action_and_auto_contact(synthetic_world):
    deps, _, items = synthetic_world
    page = html.unescape(_render(deps, items, f"/loans/{APP}"))
    for ko in (
        "승인된 스크립트입니다.", "연락 가능 시간(08:00~21:00)이 아닙니다.", f"{REP} 대표와 통화한 뒤 문구를 고쳤다",
        f"{REP} 대표에게 정산 보류 사유를 확인한다",
    ):
        assert f"EN({ko})" in page, ko
    assert f"EN({BIZ} 대표님, 다음 상환일 전 잔고가 부족할 수 있습니다.)" in page


@pytest.mark.parametrize("page", SYN_PAGES)
def test_synthetic_pages_wait_for_browser_translation_without_stored_ones(synthetic_world, page):
    deps, _, _ = synthetic_world
    assert _pending(_render(deps, {}, page))


@pytest.mark.parametrize("page", [f"/applications/{APP}", f"/loans/{APP}", f"/legacy/borrower/{APPLICANT}/notices"])
def test_entity_masking_must_agree_between_trigger_and_console(synthetic_world, page):
    # 트리거의 원천이 없으면 번역하지 않는다. 유효한 원천을 읽은 화면에서만 즉시 번역할 수 있다.
    deps, documents, _ = synthetic_world
    store, _, _ = _pretranslate(deps, documents, MemoryRaw({}))
    left = _left(deps, _render(deps, _items(store), page))
    assert left


def test_composed_entries_are_really_pending(synthetic_world):
    deps, _, items = synthetic_world
    pending = set().union(*(_pending(_render(deps, items, page)) for page in SYN_PAGES))
    masker = Pseudonymizer(deps.pseudonym_key, case_entities(None, ()))
    assert {masker.mask(t) for t in COMPOSED} <= pending