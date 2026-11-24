"""P7 화면 구성 고정: 차트·스테퍼·KPI가 나오고, 데모 대본 문구와 CSP 제약이 유지된다."""

import re
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import console.app as console_app
from agents.features import compute_underwriting
from console.app import create_app
from consolepreview.fixtures import build_deps
from mockapis.demo import build_demo_cases
from policy.engine import decision_payload, evaluate
from policy.rules import load_rules, rules_for_version
from shared.contracts import CashFlowFeatures

TEMPLATES = Path(console_app.__file__).parent / "templates"


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(build_deps()), follow_redirects=False, cookies={"ama_locale_v2": "ko"})


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
    ("/ops", '<li class="n-ops active">'),
    ("/apply?applicant_id=seller-dailylook-01", '<li class="n-apply active">'),
    ("/borrower", '<li class="n-status active">'),
])
def test_sidebar_marks_current_screen(client, path, item):
    html = page(client, path)
    assert '<aside class="sidebar">' in html and item in html
    assert html.count(" active\">") == 1


def test_home_has_kpis_donut_and_scope_note(client):
    html = page(client, "/")
    assert html.count('class="card kpi ') == 4
    assert 'class="chart donut"' in html and 'class="recommendation-bars"' in html
    assert "모든 신청을 집계합니다" in html and "최신 20건 · 배포 smoke·부하 측정·장애 주입 신청 제외" in html
    assert ">새 신청<" in html
    assert 'hx-get="/applications/rows"' in html


def test_detail_has_timeline_stepper_and_charts(client):
    html = page(client, "/applications/app-20261103-p01")
    assert '<ol class="stepper"' in html and html.count('class="step step-') >= 7
    assert 'class="chart columns"' in html and 'class="chart marker-bar"' in html
    assert "제안 구조" not in html
    for text in ("타임라인", "정책 판정", "에이전트 평가", "주장과 근거", "개선 조건(반사실)", "심사역 결정", "결정 보내기"):
        assert text in html, text
    assert "<details" in html and 'class="timeline"' in html


@pytest.mark.parametrize("version,passed,rec,ko,en", [
    ("2", True, "approve", "전액 승인 권고", "Recommend full approval"),
    ("2", True, "refer", "판단 보류", "Defer judgment"),
    ("2", True, "decline", "전액 승인 비권고", "Do not recommend full approval"),
    (None, True, "approve", "과거 의견", "Historical opinion"),
    ("1", True, "conditional", "과거 의견", "Historical opinion"),
    ("2", False, "approve", "의견 없음", "No opinion"),
])
def test_full_request_detail_and_list(version, passed, rec, ko, en):
    deps = build_deps()
    doc = deps.store.docs["app-20261103-p01"]
    a = doc["assessment"]
    a.pop("proposed_structure", None)
    if version is not None:
        a["schema_version"] = version
    else:
        a.pop("schema_version", None)
    a.update(recommendation=rec, review={"passed": passed, "rounds": 1})
    a["summary"] = "검토 근거" if version == "2" and passed else "300만 원 감액은 권고하지 않습니다"
    doc["assessment_run"]["rounds"][0]["grounding_issues"] = ["300만 원 제안"]
    client = TestClient(create_app(deps), cookies={"ama_locale_v2": "ko"})
    for lang, label in (("ko", ko), ("en", en)):
        for path in ("/applications/app-20261103-p01", "/legacy/applications/rows", "/legacy"):
            html = page(client, f"{path}?lang={lang}")
            assert label in html
            assert "300만 원 감액" not in html and "300만 원 제안" not in html
    assert doc["assessment_run"]["rounds"][0]["grounding_issues"] == ["300만 원 제안"]


def test_failed_assessment_does_not_display_last_rejected_draft():
    deps = build_deps()
    doc = deps.store.docs["app-20261103-p09"]
    doc["error"] = {"stage": "assess_credit", "message": "review rejected: 311만 원 제안"}
    html = page(TestClient(create_app(deps), cookies={"ama_locale_v2": "ko"}), "/applications/app-20261103-p09")
    assert "의견 없음" in html and "311만 원" not in html


def test_detail_tabs_and_assessment_panels(client):
    html = page(client, "/applications/app-20261103-p01")
    labels = re.findall(r'role="tab" aria-controls="tab-(\w+)"[^>]*>([^<]+)<', html)
    assert labels == [("summary", "요약"), ("cashflow", "현금흐름"), ("marketplace", "마켓 셀러"), ("policy", "정책 판정"),
                      ("opinion", "에이전트 의견"), ("evidence", "근거 거래"), ("consent", "동의·데이터"),
                      ("decision", "심사역 결정")]
    assert html.count('role="tabpanel"') == 8 and '<script src="/static/tabs.js" defer></script>' in html
    assert 'data-default-tab="tab-decision"' in html
    assert 'class="chart gauge"' in html and 'class="chart donut"' in html and 'class="signals"' in html
    assert "한도 산정" in html and "상환 여력" in html and "지출 구성" in html and "남는 돈" in html
    assert "<title>순현금흐름 " in html and "순현금흐름(매출−지출)" in html
    assert "<title>2026-10 거래처·물류 " in html and "<title>2026-10 인건비 " in html
    assert 'class="claim-mark ok"' in html and 'class="claim-mark missing"' not in html
    for text in ("권고의 의미", "근거 확인 주장", "반성 루프", "분석 에이전트 도구 호출", "모델 사용", "gpt-5.6-sol"):
        assert text in html, text
    assert 'class="claims-table"' in html and 'aria-label="주장 근거 출처 구성"' in html
    assert client.get("/static/tabs.js").status_code == 200
    js = client.get("/static/tabs.js").text
    assert "sessionStorage" in js and "data-tab-reset" in js


def test_detail_shows_settlement_card(client):
    html = page(client, "/applications/app-20261103-p01")
    assert "정산 대사" in html and "플랫폼별 정산 비중" in html and "월별 공제율(수수료+상계)" in html
    assert "정산 자료 없음" not in html
    assert "지표 읽는 법" in html and "현금화 일수" in html and "선정산 추정" in html


def test_detail_shows_officer_checks(client):
    deps = build_deps()
    deps.store.docs["app-20261103-p01"]["assessment"]["officer_checks"] = [
        {"text": "선정산 계약서를 확인한다.", "metric_ids": ["M:pg_reconciliation_ratio"], "supported": True}]
    html = page(TestClient(create_app(deps), follow_redirects=False, cookies={"ama_locale_v2": "ko"}), "/applications/app-20261103-p01")
    assert "에이전트 확인 사항" in html and "검토자 검증 1/1" in html and "선정산 계약서를 확인한다." in html


def test_preview_detail_has_next_checks(client):
    html = page(client, "/applications/app-20261103-p01")
    assert "정산 명세와 계좌 입금을 대조하세요." in html


def test_detail_old_doc_shows_no_settlement():
    deps = build_deps()
    deps.store.docs["app-20261103-p01"]["underwriting"].pop("pg_ledger")
    html = page(TestClient(create_app(deps), follow_redirects=False, cookies={"ama_locale_v2": "ko"}), "/applications/app-20261103-p01")
    assert "정산 자료 없음" in html and "플랫폼별 정산 비중" not in html


DEMO_AS_OF = date(2026, 11, 17)
DEMO_APP = "app-20261103-p01"


@pytest.fixture(scope="module")
def demo_cases():
    return build_demo_cases(DEMO_AS_OF)


def _seller_page(demo_cases, applicant_id, amount=5_000_000):
    """데모 셀러를 v4 엔진으로 심사한 문서(오케스트레이터 적재 모양)로 미리보기 신청 하나를 바꿔 그린다."""
    case = demo_cases[applicant_id]
    uw = compute_underwriting(DEMO_APP, case.profile, case.transactions, case.tax_filings, as_of=DEMO_AS_OF,
                              requested_amount_krw=amount, requested_tenor_months=6,
                              pg_settlements=case.pg_settlements, marketplace=case.marketplace)
    deps = build_deps()
    doc = deps.store.docs[DEMO_APP]
    doc["requested_amount_krw"], doc["requested_tenor_months"] = amount, 6
    doc["underwriting"] |= {"features": uw.features.model_dump(mode="json"), "marketplace": uw.marketplace}
    doc["policy"] = decision_payload(evaluate(uw.features, load_rules()))
    return page(TestClient(create_app(deps), follow_redirects=False, cookies={"ama_locale_v2": "ko"}), f"/applications/{DEMO_APP}")


@pytest.mark.parametrize("cash_only", ["missing", None])
def test_detail_renders_v3_doc_without_marketplace(cash_only):
    deps = build_deps()
    doc = deps.store.docs[DEMO_APP]
    doc["underwriting"].pop("marketplace", None)
    features = {k: v for k, v in doc["underwriting"]["features"].items() if not k.startswith("mkt_")}
    doc["underwriting"]["features"] = features
    doc["policy"] = evaluate(CashFlowFeatures(**features), rules_for_version("2026.11-v3")).model_dump(mode="json")
    if cash_only == "missing":
        doc["policy"].pop("cash_only")
    html = page(TestClient(create_app(deps), follow_redirects=False, cookies={"ama_locale_v2": "ko"}), f"/applications/{DEMO_APP}")
    assert "마켓 판매 성과 자료 없음" in html and "현금흐름만 볼 때" not in html and "셀러 데이터 대체" not in html


def test_detail_shows_substitution_and_compare(demo_cases):
    html = _seller_page(demo_cases, "seller-knitroom-04")
    assert "R001 대체됨(S001)" in html and "R104 대체됨(S002)" in html
    assert "현금흐름만 볼 때" in html and "셀러 데이터 반영" in html
    panel = html.split('id="tab-marketplace"')[1].split('id="tab-policy"')[0]
    assert "<th>플랫폼</th>" in panel and "<td>스마트스토어</td>" in panel and "마켓 판매 성과 자료 없음" not in panel
    assert "대체 상한" in html and "후보 중 가장 작은 값을 10만 원 단위로 내림" in html


def test_detail_shows_blocked_substitution(demo_cases):
    html = _seller_page(demo_cases, "seller-tint-17")
    assert "조건 충족, R002로 미적용" in html and "대체됨(S001)" not in html


def test_detail_shows_suspension_penalty(demo_cases):
    html = _seller_page(demo_cases, "seller-petfood-07")
    panel = html.split('id="tab-marketplace"')[1].split('id="tab-policy"')[0]
    assert "판매정지" in panel and "R003" in html and "현금흐름만 볼 때" in html


def test_detail_tabs_show_notices_when_empty(client):
    html = page(client, "/applications/app-20261103-p06")
    assert 'data-default-tab="tab-summary"' in html
    for text in ("정책 판정 결과가 아직 없습니다.", "의견 없음", "에이전트 주장이 참조한 거래가 없습니다."):
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
    html = page(client, "/legacy/ops")
    assert html.count('class="chart gauge"') == 2
    assert 'class="chart stacked-bar"' in html
    assert html.count('class="chart columns"') >= 2
    assert 'class="ops-alert"' in html


@pytest.mark.parametrize("path", ["/", "/borrower"])
def test_brand_shows_logo_mark(client, path):
    html = page(client, path)
    brand = html.split('class="brand"', 1)[1].split("</a>", 1)[0]
    assert '<svg class="logo-mark" aria-hidden="true"' in brand and "AMA Lending" in brand
