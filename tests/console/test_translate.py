import hashlib
import hmac
import html
import json
import logging
import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agents.runtime import AgentError, GatewayThrottled
from agents.pretranslate import translation_id
from console import i18n, translate
from console.translate import PageTexts, page_scope
from console.app import create_app
from console.auth import csrf_token
from consolepreview import fixtures
from shared.pseudonymize import tokens
from shared.pseudonymize import tokens
from shared.segments import AGE_BANDS, SELLER_INDUSTRIES
from shared.sellers import DEMO_SELLERS, LOAD_BUSINESS_NAMES
from tests.console.conftest import KEY, PROFILE, FakeRaw
from tests.console.test_i18n import CUSTOMER_PAGES, EN_PAGES, HANGUL

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
    active_sources(deps)
    deps.translator = FakeTranslator()
    return TestClient(create_app(deps), follow_redirects=False), deps


def active_sources(deps):
    deps.raw = FakeRaw()
    deps.raw.blobs[("consent-m-1", "flow.json")] = []
    deps.privacy.get_consent = lambda cid: {"id": cid, "status": "granted"} if cid in {"c-1", "m-1"} else None
    for doc in deps.store.docs.values():
        doc["consent"] = {"consent_id": "c-1"}
    for loan in deps.monitoring.loans.values():
        loan["underwriting_consent_id"] = "c-1"
        if loan.get("monitoring"):
            loan["monitoring"]["consent_id"] = "m-1"


def browser_request(client, selector=lambda t: True):
    page = client.get(DETAIL, headers=EN_HEADERS).text
    grant = json.loads(re.search(r'<script type="application/json" class="mt-grant">(.*?)</script>', page).group(1))
    texts = sorted({t for t in _pending(page) if selector(t)})
    assert texts
    return {"texts": texts, "scope": grant["scope"], "tickets": [grant["tickets"][t] for t in texts]}


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
    f = translate.mt_filters(machine, KEY)
    assert f["mt"]("식품") == "Food"
    assert f["mt"]("ABC 123") == "ABC 123"
    cached = str(f["mt"]("캐시된 문장"))
    assert 'class="mt mt-done"' in cached and "Cached sentence" in cached and 'title="Machine translation' in cached
    assert str(f["mt"]("<b>새 문장</b>")) == \
        '<span class="mt" data-mt="&lt;b&gt;새 문장&lt;/b&gt;">&lt;b&gt;새 문장&lt;/b&gt;</span>'
    assert translate.mt_filters(None, KEY)["mt"]("새 문장") == "새 문장"


def test_mt_ref_keeps_sent_korean():
    machine = translate.MachineTranslation(FakeTranslator())
    f = translate.mt_filters(machine, KEY)
    assert f["mt_ref"]("보낸 문구") == "보낸 문구"  # 한국어 화면
    token = i18n._locale.set("en")
    try:
        assert str(f["mt_ref"]("보낸 문구")) == '보낸 문구<span class="mt-ref" data-mt="보낸 문구"></span>'
    finally:
        i18n._locale.reset(token)


def profile(**overrides):
    return PROFILE.model_copy(update=overrides)


def test_stored_translation_restores_only_visible_values(english):
    page = PageTexts.for_case(KEY, profile(business_name="성수데일리룩"), [])
    masked = page.masked("성수데일리룩 매출 감소")
    page = PageTexts.for_case(KEY, profile(business_name="성수데일리룩"), [],
                              stored={translation_id(masked): masked.replace("매출 감소", "sales fell")})
    with page_scope(page):
        officer = str(translate.mt_filters(None, KEY)["mt"]("성수데일리룩 매출 감소"))
        auditor = str(translate.mt_filters(None, KEY)["mt"](masked))  # 감사자 화면은 원래 토큰
    assert "Seongsu Daily Look sales fell" in officer and 'class="mt mt-done"' in officer
    assert "[[BIZNAME_" in auditor and "Seongsu" not in auditor


def test_stored_translation_with_missing_token_is_ignored(english):
    page = PageTexts.for_case(KEY, profile(business_name="성수데일리룩"), [])
    masked = page.masked("성수데일리룩 매출 감소")
    page = PageTexts.for_case(KEY, profile(business_name="성수데일리룩"), [], stored={translation_id(masked): "sales fell"})
    with page_scope(page):
        out = str(translate.mt_filters(translate.MachineTranslation(FakeTranslator()), KEY)["mt"]("성수데일리룩 매출 감소"))
    assert 'class="mt"' in out and f'data-mt="{masked}"' in out and "sales fell" not in out


def test_pending_carries_masked_text_and_token_map(english):
    page = PageTexts.for_case(KEY, PROFILE, [])
    machine = translate.MachineTranslation(FakeTranslator())
    with page_scope(page):
        out = str(translate.mt_filters(machine, KEY)["mt"]("연락처 010-1234-5678"))
    token = next(iter(tokens(out)))
    assert token.startswith("[[PHONE_") and "010-1234-5678" not in out.split(">")[0]
    assert page.token_map() == {token: "010-1234-5678"}
    assert 'class="mt" data-mt="연락처 [[PHONE_' in out


def test_pending_without_page_masks_patterns_only(english):
    out = str(translate.mt_filters(translate.MachineTranslation(FakeTranslator()), KEY)["mt"]("연락처 010-1234-5678"))
    assert 'data-mt="연락처 [[PHONE_' in out


def test_machine_translation_rejects_token_loss():
    class Lossy(FakeTranslator):
        def translate(self, texts):
            return SimpleNamespace(translations=[t.split("]]")[-1] for t in texts], input_tokens=1, output_tokens=1)

    machine = translate.MachineTranslation(Lossy())
    masked = "연락처 [[PHONE_0123456789]] 입니다"
    out, _, _ = machine.translate([masked, "일반 문장"])
    assert out[0] == "" and machine.lookup(masked) is None


@pytest.mark.parametrize("page", [*EN_PAGES, *CUSTOMER_PAGES])
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
    assert re.search(r'<script src="/static/mt\.js\?v=[0-9a-f]{8,}" defer></script>', en) and f'data-mt-csrf="{_token(deps)}"' in en
    ko = client.get("/applications/app-20261103-p01").text
    assert "mt.js" not in ko and "data-mt=" not in ko


def test_translate_endpoint_fills_cache_for_next_render(mt, caplog):
    client, deps = mt
    page = "/applications/app-20261103-p01"
    payload = browser_request(client)
    texts = payload["texts"]
    with caplog.at_level(logging.INFO, logger="console.translate"):
        r = _post(client, deps, payload)
    assert r.status_code == 200
    assert r.json()["translations"] == [f"EN({t})" for t in texts]
    assert deps.translator.calls == [texts]  # 카탈로그·한글 없는 문구는 보내지 않는다
    assert "result=ok" in caplog.text and texts[0] not in caplog.text
    again = client.get(page, headers={"Cookie": "lang=en"}).text
    assert 'data-mt="' not in again and 'class="mt mt-done"' in again
    _post(client, deps, payload)
    assert len(deps.translator.calls) == 1


def test_translate_endpoint_masks_pii_before_model(mt):
    client, deps = mt
    deps.store.docs["app-20261103-p01"]["assessment"]["summary"] = "연락처 010-1234-5678 확인"
    r = _post(client, deps, browser_request(client, lambda t: "[[PHONE_" in t))
    assert r.status_code == 200 and "010-1234-5678" not in json.dumps(deps.translator.calls, ensure_ascii=False)
    assert "[[PHONE_" in r.json()["translations"][0]


def test_translate_endpoint_blanks_token_loss(mt):
    client, deps = mt

    class Lossy(FakeTranslator):
        def translate(self, texts):
            return SimpleNamespace(translations=[t.split("]]")[-1] for t in texts], input_tokens=1, output_tokens=1)

    deps.translator = Lossy()
    client2 = TestClient(create_app(deps), follow_redirects=False)
    deps.store.docs["app-20261103-p01"]["assessment"]["summary"] = "연락처 010-1234-5678 확인"
    r = _post(client2, deps, browser_request(client2, lambda t: "[[PHONE_" in t))
    assert r.status_code == 200 and r.json()["translations"] == [""]


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
    active_sources(deps)
    deps.translator = FakeTranslator(error)
    client = TestClient(create_app(deps))
    assert _post(client, deps, browser_request(client)).status_code == status


def test_translate_endpoint_disabled_without_translator():
    deps = fixtures.build_deps()
    client = TestClient(create_app(deps))
    assert _post(client, deps, {"texts": ["새 문장"]}).status_code == 503
    assert "mt.js" not in client.get("/", headers={"Cookie": "lang=en"}).text


EN_HEADERS = {"Cookie": "lang=en"}
DETAIL = "/applications/app-20261103-p01"


def _pending(text):
    return [html.unescape(t) for t in re.findall(r'data-mt="([^"]*)"', text)]


def test_unsigned_post_cannot_translate_free_text(mt):
    client, deps = mt
    r = _post(client, deps, {"texts": ["삭제 뒤의 자유 문구"]})
    assert r.status_code == 403 and not deps.translator.calls


@pytest.mark.parametrize("change", ["revoked", "raw_missing", "unknown_consent"])
def test_queued_browser_translation_rechecks_deleted_case(mt, change):
    client, deps = mt
    payload = browser_request(client)
    if change == "raw_missing":
        deps.raw.blobs.clear()
    else:
        deps.privacy.get_consent = lambda _: {"status": "revoked"} if change == "revoked" else None
    assert "data-mt=" not in client.get(DETAIL, headers=EN_HEADERS).text
    r = _post(client, deps, payload)
    assert r.status_code == 200 and r.json()["translations"] == [""] * len(payload["texts"]) and not deps.translator.calls


def test_browser_ticket_cannot_authorize_different_text_or_scope(mt):
    client, deps = mt
    payload = browser_request(client)
    assert _post(client, deps, payload | {"texts": ["다른 신청 문구"] * len(payload["texts"])}).status_code == 403
    assert _post(client, deps, payload | {"scope": [["other", "profile.json"]]}).status_code == 403
    assert deps.translator.calls == []


def test_english_detail_uses_stored_translation_without_browser_call(mt):
    client, deps = mt
    pending = _pending(client.get(DETAIL, headers=EN_HEADERS).text)
    assert pending
    deps.translations = fixtures.InMemoryTranslations(
        {"app-20261103-p01": {translation_id(t): f"EN({t})" for t in pending}})
    again = TestClient(create_app(deps)).get(DETAIL, headers=EN_HEADERS).text
    assert "data-mt=" not in again and deps.translator.calls == []
    assert deps.translations.calls == ["app-20261103-p01"]


@pytest.mark.parametrize("page", [DETAIL, "/loans/app-20261103-p03", *CUSTOMER_PAGES])
def test_korean_page_does_not_read_translations(mt, page):
    client, deps = mt
    deps.translations = fixtures.InMemoryTranslations()
    r = client.get(page)
    assert r.status_code == 200 and deps.translations.calls == [] and "mt-tokens" not in r.text


def test_loan_page_reads_its_own_owner(mt):
    client, deps = mt
    deps.translations = fixtures.InMemoryTranslations()
    assert client.get("/loans/app-20261103-p03", headers=EN_HEADERS).status_code == 200
    assert deps.translations.calls == ["app-20261103-p03"]


def test_translation_read_failure_falls_back_to_pending(mt, caplog):
    client, deps = mt

    class Broken:
        def for_owner(self, owner):
            raise RuntimeError("cosmos-secret-detail")

    deps.translations = Broken()
    with caplog.at_level(logging.WARNING, logger="console.translate"):
        r = client.get(DETAIL, headers=EN_HEADERS)
    assert r.status_code == 200 and "data-mt=" in r.text
    assert "app-20261103-p01" in caplog.text and "cosmos-secret-detail" not in caplog.text


def _case_client(make_doc, raw, key, role, stored=None):
    from console.app import ConsoleDeps
    from tests.console.test_app import FakeQueue, FakeStore, principal

    deps = ConsoleDeps(store=FakeStore([make_doc()]), raw=raw, queue=FakeQueue(), pseudonym_key=key,
                       translator=FakeTranslator(), translations=fixtures.InMemoryTranslations(stored))
    deps.privacy = fixtures.EmptyPrivacy()
    deps.privacy.get_consent = lambda cid: {"status": "granted"}
    return TestClient(create_app(deps)), deps, {**EN_HEADERS, **principal(role)}


def test_auditor_sees_tokens_in_stored_translation(make_doc, raw, key, mask):
    from console.auth import AUDITOR, CREDIT_OFFICER

    summary = mask("빈스카페(대표 김대표)는 계절성이 있다")
    stored = {"app-1": {translation_id(summary): summary.replace("는 계절성이 있다", " is seasonal")}}
    client, _, headers = _case_client(make_doc, raw, key, AUDITOR, stored)
    page = client.get("/applications/app-1", headers=headers).text
    assert "is seasonal" in page and "[[BIZNAME_" in page and "빈스카페" not in page and "김대표" not in page
    client, _, headers = _case_client(make_doc, raw, key, CREDIT_OFFICER, stored)
    officer = client.get("/applications/app-1", headers=headers).text
    assert "빈스카페(대표 김대표) is seasonal" in officer
    assert "[[BIZNAME_" not in officer


def test_token_map_script_lists_only_pending_tokens(make_doc, raw, key):
    from console.auth import CREDIT_OFFICER

    client, _, headers = _case_client(make_doc, raw, key, CREDIT_OFFICER)
    page = client.get("/applications/app-1", headers=headers).text
    found = re.findall(r'<script type="application/json" class="mt-tokens">(.*?)</script>', page, flags=re.S)
    assert len(found) == 1 and page.index("mt-tokens") < page.rindex("</body>")
    token_map = json.loads(found[0])
    pending = "".join(_pending(page))
    assert token_map and set(token_map) <= tokens(pending)
    assert "김대표" in token_map.values() or "빈스카페" in token_map.values()


def test_detail_masking_matches_orchestrator(make_doc, raw, key):
    from agents.data_assembly import case_entities
    from agents.pretranslate import load_case_raw
    from console.auth import CREDIT_OFFICER
    from shared.pseudonymize import Pseudonymizer

    doc = make_doc()
    profile, txns = load_case_raw("applications", doc, raw, lambda source, owner: None)
    expected = Pseudonymizer(key, case_entities(profile, txns))
    client, _, headers = _case_client(make_doc, raw, key, CREDIT_OFFICER)
    pending = _pending(client.get("/applications/app-1", headers=headers).text)
    text = "빈스카페(대표 김대표)는 계절성이 있다"
    assert expected.mask(text) in pending and "빈스카페" not in expected.mask(text)
    assert all(expected.mask(p) == p for p in pending)  # 화면이 보낸 문구는 이미 같은 마스킹 결과다


def test_unreadable_source_disables_pending_translation(make_doc, raw, key):
    from console.auth import CREDIT_OFFICER

    client, deps, headers = _case_client(make_doc, raw, key, CREDIT_OFFICER)
    page = PageTexts(translate.Pseudonymizer(key), {}, allow_pending=False)
    with page_scope(page), i18n.use("en"):
        machine = translate.MachineTranslation(FakeTranslator())
        filters = translate.mt_filters(machine, key)
        assert filters["mt"]("김대표 개인이체") == "김대표 개인이체"
        assert filters["mt_ref"]("김대표 개인이체") == "김대표 개인이체"
    assert page.token_map() == {}


def test_token_script_escapes_markup_characters():
    from console.app import _token_script

    out = _token_script({"[[PERSON_x]]": "</script><b>&"})
    assert "</script><b>" not in out.removeprefix('<script type="application/json" class="mt-tokens">')
    assert json.loads(out.split(">", 1)[1].rsplit("</script>", 1)[0]) == {"[[PERSON_x]]": "</script><b>&"}
