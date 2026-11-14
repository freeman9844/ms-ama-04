import hashlib
import hmac
import logging
import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agents.runtime import AgentError, GatewayThrottled
from console import i18n, translate
from console.app import create_app
from console.auth import csrf_token
from consolepreview import fixtures
from shared.segments import AGE_BANDS, SELLER_INDUSTRIES
from shared.sellers import DEMO_SELLERS, LOAD_BUSINESS_NAMES
from tests.console.test_i18n import EN_PAGES, HANGUL

EN = i18n.CATALOGS["en"]


class FakeTranslator:
    def __init__(self, error=None):
        self.error, self.calls = error, []

    def translate(self, texts):
        self.calls.append(list(texts))
        if self.error:
            raise self.error
        return SimpleNamespace(translations=[f"EN({t})" for t in texts], input_tokens=50, output_tokens=20)


@pytest.fixture
def mt():
    deps = fixtures.build_deps()
    deps.translator = FakeTranslator()
    return TestClient(create_app(deps), follow_redirects=False), deps


def _token(deps):
    key = hmac.new(deps.pseudonym_key, b"console-csrf", hashlib.sha256).digest()
    return csrf_token(key, deps.demo_principal.user_id)


def _post(client, deps, body, token=None):
    return client.post("/i18n/translate", json=body,
                       headers={"X-CSRF-Token": token or _token(deps), "Cookie": "lang=en"})


@pytest.fixture
def english():
    token = i18n._locale.set("en")
    yield
    i18n._locale.reset(token)


def test_every_seller_vocabulary_has_english():
    vocab = ({s.business_name for s in DEMO_SELLERS} | {c for s in DEMO_SELLERS for c in s.channels}
             | {n for names in LOAD_BUSINESS_NAMES.values() for n in names} | set(SELLER_INDUSTRIES) | set(AGE_BANDS))
    assert sorted(v for v in vocab if v not in EN) == []


def test_data_label_translates_each_part(english):
    assert translate.data_label("성수데일리룩 · 스마트스토어·쿠팡 · 도서·문구") == \
        "Seongsu Daily Look · Smart Store·Coupang · Books & stationery"
    assert translate.data("모르는 값") == "모르는 값"


def test_mt_filter_marks_by_source(english):
    machine = translate.MachineTranslation(FakeTranslator())
    machine.cache.put("캐시된 문장", "Cached sentence")
    f = translate.mt_filters(machine)
    assert f["mt"]("식품") == "Food"
    assert f["mt"]("ABC 123") == "ABC 123"
    cached = str(f["mt"]("캐시된 문장"))
    assert 'class="mt mt-done"' in cached and "Cached sentence" in cached and 'title="Machine translation' in cached
    assert str(f["mt"]("<b>새 문장</b>")) == \
        '<span class="mt" data-mt="&lt;b&gt;새 문장&lt;/b&gt;">&lt;b&gt;새 문장&lt;/b&gt;</span>'
    assert translate.mt_filters(None)["mt"]("새 문장") == "새 문장"


def test_mt_ref_keeps_sent_korean():
    machine = translate.MachineTranslation(FakeTranslator())
    f = translate.mt_filters(machine)
    assert f["mt_ref"]("보낸 문구") == "보낸 문구"  # 한국어 화면
    token = i18n._locale.set("en")
    try:
        assert str(f["mt_ref"]("보낸 문구")) == '보낸 문구<span class="mt-ref" data-mt="보낸 문구"></span>'
    finally:
        i18n._locale.reset(token)


@pytest.mark.parametrize("page", [*EN_PAGES, "/borrower/seller-dailylook-01"])
def test_english_pages_leave_only_pending_machine_translation(mt, page):
    client, deps = mt
    r = client.get(page, headers={"Cookie": "lang=en"})
    assert r.status_code == 200
    html = re.sub(r"<script.*?</script>|<style.*?</style>", " ", r.text, flags=re.S)
    html = re.sub(r'<span class="mt" data-mt="[^"]*">.*?</span>', " ", html, flags=re.S)
    html = re.sub(r'[^<>]*<span class="mt-ref" data-mt="[^"]*"></span>', " ", html)  # 원문 + 영문 참고
    html = re.sub(r'(title|data-mt|aria-label)="[^"]*"', "", html)
    html = html.replace(deps.demo_principal.name, " ").replace("한국어", " ")
    leftover = sorted({line.strip() for line in re.sub(r"<[^>]+>", "\n", html).splitlines() if HANGUL.search(line)})
    assert leftover == []
    assert deps.translator.calls == []  # 렌더는 번역을 기다리지 않는다


def test_english_page_loads_mt_script_with_csrf(mt):
    client, deps = mt
    en = client.get("/applications/app-20261103-p01", headers={"Cookie": "lang=en"}).text
    assert '<script src="/static/mt.js" defer></script>' in en and f'data-mt-csrf="{_token(deps)}"' in en
    ko = client.get("/applications/app-20261103-p01").text
    assert "mt.js" not in ko and "data-mt=" not in ko


def test_translate_endpoint_fills_cache_for_next_render(mt, caplog):
    client, deps = mt
    page = "/applications/app-20261103-p01"
    pending = re.findall(r'<span class="mt" data-mt="([^"]*)">', client.get(page, headers={"Cookie": "lang=en"}).text)
    assert pending
    texts = sorted({t.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&#34;", '"')
                    .replace("&#39;", "'") for t in pending})
    with caplog.at_level(logging.INFO, logger="console.translate"):
        r = _post(client, deps, {"texts": [*texts, "식품", "OK"]})
    assert r.status_code == 200
    assert r.json()["translations"] == [*(f"EN({t})" for t in texts), "Food", "OK"]
    assert deps.translator.calls == [texts]  # 카탈로그·한글 없는 문구는 보내지 않는다
    assert "result=ok" in caplog.text and texts[0] not in caplog.text
    again = client.get(page, headers={"Cookie": "lang=en"}).text
    assert 'data-mt="' not in again and 'class="mt mt-done"' in again
    _post(client, deps, {"texts": texts})
    assert len(deps.translator.calls) == 1


def test_translate_endpoint_guards(mt):
    client, deps = mt
    assert _post(client, deps, {"texts": ["가"]}, token="bad").status_code == 403
    assert _post(client, deps, {"texts": []}).status_code == 400
    assert _post(client, deps, {"texts": ["가" * (translate.MAX_CHARS + 1)]}).status_code == 400
    assert _post(client, deps, {"texts": ["가"] * (translate.MAX_ITEMS + 1)}).status_code == 400
    assert _post(client, deps, {"texts": [1]}).status_code == 400


@pytest.mark.parametrize(("error", "status"), [(GatewayThrottled("rate_limited", "c"), 429), (AgentError("x"), 502)])
def test_translate_endpoint_errors(error, status):
    deps = fixtures.build_deps()
    deps.translator = FakeTranslator(error)
    client = TestClient(create_app(deps))
    assert _post(client, deps, {"texts": ["새 문장"]}).status_code == status


def test_translate_endpoint_disabled_without_translator():
    deps = fixtures.build_deps()
    client = TestClient(create_app(deps))
    assert _post(client, deps, {"texts": ["새 문장"]}).status_code == 503
    assert "mt.js" not in client.get("/", headers={"Cookie": "lang=en"}).text
