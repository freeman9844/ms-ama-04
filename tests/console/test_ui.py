"""P7 화면 구성 고정: 차트·스테퍼·KPI가 나오고, 데모 대본 문구와 CSP 제약이 유지된다."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import console.app as console_app
from console.app import create_app
from consolepreview.fixtures import build_deps

TEMPLATES = Path(console_app.__file__).parent / "templates"


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(build_deps()), follow_redirects=False)


def page(client, path):
    resp = client.get(path)
    assert resp.status_code == 200, path
    return resp.text


def test_templates_have_no_inline_style_or_script():
    for path in TEMPLATES.glob("*.html"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\sstyle\s*=", text), path.name
        assert not re.search(r"<style", text), path.name
        for tag in re.findall(r"<script\b[^>]*>", text):
            assert "src=" in tag, path.name


def test_csp_is_unchanged(client):
    assert console_app.CSP == (
        "default-src 'self'; script-src 'self' https://unpkg.com; style-src 'self'; "
        "img-src 'self' data:; frame-ancestors 'none'"
    )
    assert client.get("/").headers["content-security-policy"] == console_app.CSP


@pytest.mark.parametrize(("path", "item"), [
    ("/", '<li class="n-home active"><a href="/">신청</a></li>'),
    ("/loans", '<li class="n-loans active">'),
    ("/grievances", '<li class="n-grievances active">'),
    ("/borrower", '<li class="n-borrower active">'),
    ("/ops", '<li class="n-ops active">'),
])
def test_sidebar_marks_current_screen(client, path, item):
    html = page(client, path)
    assert '<aside class="sidebar">' in html and item in html
    assert html.count(" active\">") == 1


def test_home_has_kpis_donut_and_scope_note(client):
    html = page(client, "/")
    assert html.count('class="card kpi ') == 4
    assert 'class="chart donut"' in html and 'class="chart hbars"' in html
    assert "최근 50건 기준" in html
    assert ">새 신청<" in html
    assert 'hx-get="/applications/rows"' in html
