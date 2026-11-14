import ast
import importlib
import pkgutil
import re
from html import escape
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import console
from console import i18n
from console.app import create_app
from consolepreview import fixtures
from shared.sellers import DEMO_SELLERS, LOAD_BUSINESS_NAMES

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "src" / "console"
TEMPLATES = sorted((CONSOLE / "templates").glob("*.html"))
HANGUL = re.compile(r"[가-힣]")
JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", re.S)
HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
LITERAL = re.compile(r"""(["'])((?:(?!\1).)*?[가-힣](?:(?!\1).)*?)\1""", re.S)
TEMPLATE_MSGID = re.compile(r"""(?<![\w.])_\(\s*(["'])((?:(?!\1).)*)\1""", re.S)
TEMPLATE_PMSGID = re.compile(r"""pgettext\(\s*"(\w+)",\s*"([^"]*)"\s*""")
PLACEHOLDER = re.compile(r"%\(\w+\)s|%%")
# 언어 이름은 각 언어로 적는다.
UNTRANSLATED_TEMPLATES = {"_lang.html"}
EN = i18n.CATALOGS["en"]


@pytest.fixture
def preview():
    deps = fixtures.build_deps()
    return TestClient(create_app(deps), follow_redirects=False), deps


def test_default_locale_is_korean(preview):
    client, _ = preview
    html = client.get("/").text
    assert '<html lang="ko">' in html and "사후 모니터링" in html


def test_lang_query_switches_and_sets_cookie(preview):
    client, _ = preview
    r = client.get("/loans?lang=en")
    assert '<html lang="en">' in r.text and "Post-loan monitoring" in r.text
    assert "lang=en" in r.headers["set-cookie"]
    assert '<html lang="en">' in client.get("/").text
    assert '<html lang="ko">' in client.get("/?lang=ko").text


def test_accept_language_picks_english_without_cookie(preview):
    client, _ = preview
    assert '<html lang="en">' in client.get("/", headers={"Accept-Language": "en-US,en;q=0.9"}).text
    assert '<html lang="ko">' in client.get("/", headers={"Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"}).text


def test_switch_link_keeps_path_and_drops_watch_params(preview):
    client, _ = preview
    html = client.get("/loans/app-20261103-p03?since=abc&n=2&lang=en").text
    assert 'href="/loans/app-20261103-p03?lang=ko"' in html


def test_krw_follows_locale(preview):
    client, _ = preview
    assert "원" in client.get("/loans").text
    assert "₩" in client.get("/loans?lang=en").text


def test_button_context_differs_from_status(preview):
    client, _ = preview
    html = client.get("/applications/app-20261103-p01?lang=en").text
    assert "> Approve</label>" in html
    i18n._locale.set("en")
    try:
        assert i18n.gettext("승인") == "Approved" and i18n.pgettext("button", "승인") == "Approve"
    finally:
        i18n._locale.set(i18n.DEFAULT)
    assert i18n.pgettext("button", "승인") == "승인"


def test_catalog_placeholders_match():
    bad = {k: v for k, v in EN.items() if sorted(PLACEHOLDER.findall(k)) != sorted(PLACEHOLDER.findall(v))}
    assert bad == {}


def test_catalog_values_are_english():
    assert [k for k, v in EN.items() if not v.strip() or HANGUL.search(v)] == []


def test_conflicting_duplicates_are_rejected(tmp_path):
    (tmp_path / "en").mkdir()
    (tmp_path / "en" / "a.json").write_text('{"신청": "Applications"}', encoding="utf-8")
    (tmp_path / "en" / "b.json").write_text('{"신청": "Apply"}', encoding="utf-8")
    with pytest.raises(ValueError):
        i18n.load_catalog("en", tmp_path)


def _template_msgids() -> set[str]:
    texts = [path.read_text(encoding="utf-8") for path in TEMPLATES]
    return ({m.group(2) for t in texts for m in TEMPLATE_MSGID.finditer(t)}
            | {f"{m.group(1)}|{m.group(2)}" for t in texts for m in TEMPLATE_PMSGID.finditer(t)})


def _python_msgids() -> set[str]:
    found = set()
    for path in CONSOLE.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                    and ((isinstance(node.func, ast.Name) and node.func.id in {"_", "gettext"})
                         or (isinstance(node.func, ast.Attribute) and node.func.attr in {"_", "gettext"}))):
                found.add(node.args[0].value)
    return found


def test_every_msgid_has_english():
    missing = sorted(m for m in _template_msgids() | _python_msgids() if HANGUL.search(m) and m not in EN)
    assert missing == []


def _label_values() -> set[str]:
    values = set()
    for info in pkgutil.iter_modules(console.__path__):
        if info.name == "main":  # 시작 시 환경 변수를 읽는다
            continue
        module = importlib.import_module(f"console.{info.name}")
        for name, obj in vars(module).items():
            if name.isupper() and name.endswith(("_LABELS", "_ERRORS")) and isinstance(obj, dict):
                for value in obj.values():
                    for text in value if isinstance(value, tuple) else (value,):
                        if isinstance(text, str) and HANGUL.search(text):
                            values.add(text)
    return values


def test_every_label_has_english():
    assert sorted(v for v in _label_values() if v not in EN) == []


@pytest.mark.parametrize("path", [p for p in TEMPLATES if p.name not in UNTRANSLATED_TEMPLATES], ids=lambda p: p.name)
def test_template_korean_goes_through_gettext(path):
    text = HTML_COMMENT.sub(" ", path.read_text(encoding="utf-8"))
    loose = [line.strip() for line in JINJA.sub(" ", text).splitlines() if HANGUL.search(line)]
    unwrapped = []
    for block in JINJA.finditer(text):
        if block.group(0).startswith("{#"):
            continue
        for lit in LITERAL.finditer(block.group(0)):
            before = block.group(0)[:lit.start()].rstrip()
            if not (before.endswith("_(") or re.search(r'pgettext\(\s*"\w+",$', before)):
                unwrapped.append(lit.group(0))
    assert loose == [] and unwrapped == []


def _data_strings(value, out: set[str]) -> set[str]:
    if isinstance(value, str):
        if HANGUL.search(value):
            out.add(value)
    elif isinstance(value, dict):
        for v in value.values():
            _data_strings(v, out)
    elif isinstance(value, list | tuple):
        for v in value:
            _data_strings(v, out)
    return out


EN_PAGES = [
    "/", "/applications/new", "/applications/app-20261103-p01", "/applications/app-20261103-p06",
    "/loans", "/loans/app-20261103-p03", "/loans/app-20261103-p04", "/loans/app-20261103-p11",
    "/ops", "/grievances", "/borrower", "/apply",
]


@pytest.mark.parametrize("page", EN_PAGES)
def test_english_page_has_no_korean_ui_text(preview, page):
    client, deps = preview
    data: set[str] = set()
    for source in (deps.store.docs, deps.monitoring.loans, deps.monitoring.alerts, deps.ops.data):
        _data_strings(source, data)
    _data_strings([vars(s) for s in DEMO_SELLERS], data)
    _data_strings(LOAD_BUSINESS_NAMES, data)
    data.update({"한국어", deps.demo_principal.name})
    html = client.get(page, headers={"Cookie": "lang=en"}).text
    html = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
    for text in sorted(data, key=len, reverse=True):
        html = html.replace(escape(text, quote=False), " ").replace(escape(text), " ").replace(text, " ")
    leftover = sorted({line.strip() for line in re.sub(r"<[^>]+>", "\n", html).splitlines() if HANGUL.search(line)})
    assert leftover == []
