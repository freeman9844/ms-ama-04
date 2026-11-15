"""허용 목록(agents.pretranslate.ALLOWLIST)이 영문 화면의 데이터 문구를 모두 덮는지 본다.

미리보기 픽스처의 모든 문서를 실제 사전 번역 경로(handle_changes)로 번역해 저장한 뒤 영문 화면을 그려,
브라우저 번역을 기다리는 data-mt 문구가 남지 않아야 한다.
"""

import html
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
from shared.pseudonymize import Pseudonymizer
from tests.console.test_i18n import EN_PAGES

PAGES = [*EN_PAGES, "/borrower/seller-dailylook-01"]
EN_HEADERS = {"Cookie": "lang=en"}

# 콘솔 코드가 조립해 저장 문서에 그대로 없는 문구(문구 → 이유). 문서 경로에서 온 문구는 ALLOWLIST에 더한다.
COMPOSED: dict[str, str] = {}


class FixtureStore:
    """픽스처 문서(읽기)와 translations(쓰기)를 한 저장소처럼 보이게 한다."""

    def __init__(self, deps):
        self.docs = {
            "applications": {d["id"]: d for d in deps.store.docs.values()},
            "loans": dict(deps.monitoring.loans),
        }
        self.translations: dict[tuple[str, str], dict] = {}

    def read(self, container, item_id, partition_key):
        if container == TRANSLATIONS:
            doc = self.translations.get(item_id)
            return deepcopy(doc) if doc and doc["owner_id"] == partition_key else None
        return self.docs.get(container, {}).get(item_id)

    def upsert(self, container, document):
        assert container == TRANSLATIONS
        self.translations[(document["owner_id"], document["id"])] = deepcopy(document)


class FakeTranslator:
    def translate(self, texts):
        return TranslationResult([f"EN({t})" for t in texts], 1, 1)


def _fixture_documents(deps):
    alerts = deps.monitoring.alerts
    return {
        "applications": list(deps.store.docs.values()),
        "loans": list(deps.monitoring.loans.values()),
        "alerts": alerts,
        "grievances": [],
    }


@pytest.fixture(scope="module")
def stored():
    deps = fixtures.build_deps()
    store = FixtureStore(deps)
    pre = Deps(
        store=store, raw=deps.raw, mock=None, policy=None, events=None, pseudonym_key=deps.pseudonym_key,
        clock=lambda: datetime(2026, 11, 15, 10, 0, tzinfo=KST), translator=FakeTranslator(),
        translate_model="gpt-test",
    )
    documents = _fixture_documents(deps)
    assert set(documents) == set(SOURCES)
    items: dict[str, dict[str, str]] = {}
    for source, docs in documents.items():
        report = handle_changes(pre, source, docs)
        assert report["failed"] == report["rejected"] == 0, (source, report)
    for doc in store.translations.values():
        items.setdefault(doc["owner_id"], {})[doc["id"]] = doc["en"]
    assert items and all(owner_of(s, d) for s, docs in documents.items() for d in docs)
    return deps, items


def _pending(page_html):
    return {html.unescape(t) for t in re.findall(r'data-mt="([^"]*)"', page_html)}


@pytest.mark.parametrize("page", ["/applications/app-20261103-p01", "/loans/app-20261103-p03"])
def test_pages_wait_for_browser_translation_without_stored_ones(stored, page):
    deps, _ = stored
    deps.translations = fixtures.InMemoryTranslations()
    deps.translator = FakeTranslator()
    r = TestClient(create_app(deps), follow_redirects=False).get(page, headers=EN_HEADERS)
    assert _pending(r.text)


@pytest.mark.parametrize("page", PAGES)
def test_allowlist_covers_every_english_page(stored, page):
    deps, items = stored
    deps.translations = fixtures.InMemoryTranslations(items)
    deps.translator = FakeTranslator()
    r = TestClient(create_app(deps), follow_redirects=False).get(page, headers=EN_HEADERS)
    assert r.status_code == 200
    expected = {Pseudonymizer(deps.pseudonym_key, case_entities(None, ())).mask(t) for t in COMPOSED}
    left = _pending(r.text) - expected
    assert not left, f"{page}: 사전 번역이 덮지 못한 문구(마스킹) {sorted(left)}"
