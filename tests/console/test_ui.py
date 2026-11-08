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


def test_detail_has_timeline_stepper_and_charts(client):
    html = page(client, "/applications/app-20261103-p01")
    assert '<ol class="stepper"' in html and html.count('class="step step-') >= 7
    assert 'class="chart columns"' in html and 'class="chart marker-bar"' in html
    for text in ("타임라인", "정책 판정", "에이전트 평가", "주장과 근거", "제안 구조", "개선 조건(반사실)", "심사역 결정", "결정 보내기"):
        assert text in html, text
    assert "<details" in html and 'class="timeline"' in html


def test_detail_tabs_and_assessment_panels(client):
    html = page(client, "/applications/app-20261103-p01")
    labels = re.findall(r'role="tab" aria-controls="tab-(\w+)"[^>]*>([^<]+)<', html)
    assert labels == [("summary", "요약"), ("cashflow", "현금흐름"), ("policy", "정책 판정"), ("opinion", "에이전트 의견"),
                      ("evidence", "근거 거래"), ("consent", "동의·데이터"), ("decision", "심사역 결정")]
    assert html.count('role="tabpanel"') == 7 and '<script src="/static/tabs.js" defer></script>' in html
    assert 'data-default-tab="tab-decision"' in html
    assert 'class="chart gauge"' in html and 'class="chart donut"' in html and 'class="signals"' in html
    assert "한도 산정" in html and "상환 여력" in html and "지출 구성" in html and "남는 돈" in html
    assert "<title>순현금흐름 " in html and "순현금흐름(매출−지출)" in html
    assert "<title>2026-10 거래처·물류 " in html and "<title>2026-10 인건비 " in html
    assert 'class="claim-mark ok"' in html and 'class="claim-mark missing"' in html
    for text in ("권고의 의미", "근거 확인 주장", "반성 루프", "분석 에이전트 도구 호출", "모델 사용", "gpt-5.6-sol"):
        assert text in html, text
    assert 'class="claims-table"' in html and 'aria-label="주장 근거 출처 구성"' in html
    assert client.get("/static/tabs.js").status_code == 200


def test_detail_shows_settlement_card(client):
    html = page(client, "/applications/app-20261103-p01")
    assert "정산 대사" in html and "플랫폼별 정산 비중" in html and "월별 공제율(수수료+상계)" in html
    assert "정산 자료 없음" not in html


def test_detail_old_doc_shows_no_settlement():
    deps = build_deps()
    deps.store.docs["app-20261103-p01"]["underwriting"].pop("pg_ledger")
    html = page(TestClient(create_app(deps), follow_redirects=False), "/applications/app-20261103-p01")
    assert "정산 자료 없음" in html and "플랫폼별 정산 비중" not in html


def test_detail_tabs_show_notices_when_empty(client):
    html = page(client, "/applications/app-20261103-p06")
    assert 'data-default-tab="tab-summary"' in html
    for text in ("정책 판정 결과가 아직 없습니다.", "에이전트 평가가 아직 없습니다.", "에이전트 주장이 참조한 거래가 없습니다."):
        assert text in html, text


def test_withdrawn_detail_marks_failed_step(client):
    html = page(client, "/applications/app-20261103-p06")
    assert "step-failed" in html and "철회 종료" in html


def test_loans_list_has_kpis(client):
    html = page(client, "/loans")
    assert html.count('class="card kpi ') == 4
    assert "13주 완주" in html


def test_loan_has_chart_triggers_and_alert_steppers(client):
    html = page(client, "/loans/app-20261103-p03")
    assert html.count('class="card kpi ') == 8  # 상단 4 + 4주 잔고 전망 4
    assert 'class="chart columns"' in html and 'class="line ' in html and 'class="trigger-mark"' in html
    for text in ("주간 흐름과 탐지기", "경보 6주차", "경보 10주차", "사후 13주 재생", "결정 보내기"):
        assert text in html, text
    assert 'class="stepper compact"' in html
    table = html.split("주간 흐름과 탐지기", 1)[1]
    assert "<details" not in table.split("</table>", 1)[0]
    assert "잔고 부족 전망" in table and "2027-02-10 부족" in table


def test_loan_shows_expected_inflow_and_balance_forecast(client):
    html = page(client, "/loans/app-20261103-p03")
    expected = html.split("기대 입금 대비 실제</h2>", 1)[1].split("</section>", 1)[0]
    assert 'class="line stroke-teal"' in expected and 'class="bar-v fill-amber"' in expected
    forecast = html.split("4주 잔고 전망 <small>13주차 말 기준", 1)[1].split("</section>", 1)[0]
    assert 'class="ref stroke-red"' in forecast and 'class="dot fill-red"' in forecast
    assert "2027-02-10" in forecast and "이번 대출 원리금" in forecast and "-1,717,552원" in forecast
    running = page(client, "/loans/app-20261103-p04")
    assert "4주 잔고 전망 <small>4주차 말 기준" in running and "28일 안에 0원 아래로 내려가지 않음" in running


def test_ops_has_gauges_stacked_and_fairness_columns(client):
    html = page(client, "/ops")
    assert html.count('class="chart gauge"') == 2
    assert 'class="chart stacked-bar"' in html
    assert html.count('class="chart columns"') >= 2
    assert 'class="ops-alert"' in html
