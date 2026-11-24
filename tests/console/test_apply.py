import re
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from console.app import APPLY_WAIT_LIMIT, START_QUEUE, ConsoleDeps, create_app
from console.auth import CREDIT_OFFICER
from journey.models import KST
from tests.console.test_app import NO_ROLE, FakeQueue, FakeStore, principal

NOW = datetime(2026, 11, 4, 10, 0, tzinfo=KST)
CUSTOMER = NO_ROLE
VALID = {
    "applicant_id": "seller-dailylook-01",
    "consent_account": "on",
    "consent_tax": "on",
    "consent_pg": "on",
    "requested_amount_usd": "7,000.00",
    "requested_tenor_months": "6",
}


@pytest.fixture
def env(make_doc, raw, key):
    store = FakeStore([
        make_doc(id="app-cust", application_id="app-cust", source="customer"),
        make_doc(id="app-console", application_id="app-console", source="console"),
    ])
    queue = FakeQueue()
    deps = ConsoleDeps(store=store, raw=raw, queue=queue, pseudonym_key=key,
                       usd_origination_enabled=True, new_application_id=lambda: "app-20261104-c0ffee", clock=lambda: NOW)
    return TestClient(create_app(deps), follow_redirects=False, cookies={"ama_locale_v2": "ko"}), store, queue


def token(client, headers=CUSTOMER):
    page = client.get("/apply?applicant_id=seller-dailylook-01", headers=headers).text
    return re.search(r'name="csrf" value="([0-9a-f]{64})"', page).group(1)


def apply(client, headers=CUSTOMER, **fields):
    form = {"csrf": token(client, headers)} | VALID | fields
    return client.post("/apply", data={k: v for k, v in form.items() if v is not None}, headers=headers)


def test_apply_page_needs_login_but_no_console_role(env):
    client, _, _ = env
    assert client.get("/apply").status_code == 401
    page = client.get("/apply?applicant_id=seller-dailylook-01", headers=CUSTOMER)
    assert page.status_code == 200
    html = page.text
    assert '<input type="hidden" name="applicant_id" value="seller-dailylook-01">' in html
    assert '<select name="applicant_id"' not in html
    for name in ("consent_account", "consent_tax", "consent_pg"):
        assert f'name="{name}"' in html
    assert "운전자금 대출 심사" in html and "90일" in html
    assert "Seongsu Daily Look" in html
    assert '<script src="/static/apply.js"></script>' in html
    assert "사후 모니터링" not in html


def test_apply_page_is_fixed_to_chosen_applicant(env):
    client, _, _ = env
    html = client.get("/apply?applicant_id=seller-candle-05", headers=CUSTOMER).text
    assert '<input type="hidden" name="applicant_id" value="seller-candle-05">' in html
    assert '<li class="n-apply active">' in html and 'href="/borrower/seller-candle-05/grievances"' in html


@pytest.mark.parametrize("path", ["/apply", "/apply?applicant_id=nope"])
def test_apply_without_known_applicant_goes_to_customer_select(env, path):
    client, _, _ = env
    response = client.get(path, headers=CUSTOMER)
    assert response.status_code == 303 and response.headers["location"] == "/borrower"


def test_waiting_status_page_keeps_chosen_applicant_menu(env):
    client, _, _ = env
    html = client.get("/apply/app-not-yet?applicant_id=seller-candle-05", headers=CUSTOMER).text
    assert 'href="/apply?applicant_id=seller-candle-05"' in html and 'href="/borrower/seller-candle-05"' in html
    assert 'url=/apply/app-not-yet?n=1&amp;applicant_id=seller-candle-05' in html
    unknown = client.get("/apply/app-not-yet?applicant_id=nope", headers=CUSTOMER).text
    assert "/borrower/nope" not in unknown and 'href="/apply?applicant_id=nope"' not in unknown and "&amp;applicant_id=nope" not in unknown


def test_unknown_applicant_post_goes_to_customer_select(env):
    client, _, queue = env
    response = apply(client, applicant_id="seller-unknown-99")
    assert response.status_code == 303 and response.headers["location"] == "/borrower"
    assert queue.sent == []


def test_status_page_opens_menu_of_its_applicant_and_points_declined_to_grievances(env):
    client, store, _ = env
    store.docs["app-cust"]["status"] = "declined"
    html = client.get("/apply/app-cust", headers=CUSTOMER).text
    applicant = store.docs["app-cust"]["applicant_id"]
    assert f'<li class="n-status active"><a href="/legacy/borrower/{applicant}">' in html
    assert "이의제기 메뉴" in html and "고객센터" not in html


def test_valid_application_is_enqueued_as_customer(env):
    client, _, queue = env
    response = apply(client)
    assert response.status_code == 303
    assert response.headers["location"] == "/apply/app-20261104-c0ffee?applicant_id=seller-dailylook-01"
    [(name, message)] = queue.sent
    assert name == START_QUEUE
    assert message["source"] == "customer"
    assert message["applicant_id"] == "seller-dailylook-01"
    assert message["requested_amount_cents"] == 700000
    assert message["requested_tenor_months"] == 6


@pytest.mark.parametrize("missing", ["consent_account", "consent_tax", "consent_pg"])
def test_every_consent_is_required(env, missing):
    client, _, queue = env
    response = apply(client, **{missing: None})
    assert response.status_code == 422
    assert "데이터 3종에 모두 동의해야 신청할 수 있습니다." in response.text
    assert 'data-code="consent_required"' in response.text
    assert queue.sent == []


def test_consent_value_other_than_on_is_not_consent(env):
    client, _, queue = env
    assert apply(client, consent_pg="off").status_code == 422
    assert queue.sent == []


@pytest.mark.parametrize(("fields", "code"), [
    ({"requested_amount_usd": "99.99"}, "amount_invalid"),
    ({"requested_amount_usd": "10000.01"}, "amount_invalid"),
    ({"requested_amount_usd": "-5000000"}, "amount_invalid"),
    ({"requested_amount_usd": "오백만"}, "amount_invalid"),
    ({"requested_tenor_months": "2"}, "tenor_invalid"),
    ({"requested_tenor_months": "7"}, "tenor_invalid"),
    ({"requested_tenor_months": "12"}, "tenor_invalid"),
])
def test_out_of_range_input_is_rejected_and_form_kept(env, fields, code):
    client, _, queue = env
    response = apply(client, **fields)
    assert response.status_code == 422
    assert f'data-code="{code}"' in response.text
    assert 'name="consent_tax" class="consent" checked' in response.text
    assert 'name="applicant_id" value="seller-dailylook-01"' in response.text
    assert 'href="/borrower/seller-dailylook-01"' in response.text
    assert queue.sent == []


def test_boundary_amounts_are_accepted(env):
    client, _, queue = env
    assert apply(client, requested_amount_usd="100").status_code == 303
    assert apply(client, requested_amount_usd="10,000", requested_tenor_months="3").status_code == 303
    assert [m["requested_amount_cents"] for _, m in queue.sent] == [10000, 1000000]
    assert [m["requested_tenor_months"] for _, m in queue.sent] == [6, 3]


def test_apply_requires_csrf(env):
    client, _, queue = env
    form = VALID | {"csrf": "0" * 64}
    assert client.post("/apply", data=form, headers=CUSTOMER).status_code == 403
    assert queue.sent == []


def test_status_before_document_exists_is_pending_with_refresh(env):
    client, _, _ = env
    html = client.get("/apply/app-20261104-c0ffee", headers=CUSTOMER).text
    assert "접수 대기" in html
    assert '<meta http-equiv="refresh" content="3;url=/apply/app-20261104-c0ffee?n=1">' in html


def test_status_of_unknown_application_stops_refreshing_after_limit(env):
    client, _, _ = env
    path = f"/apply/app-20261104-c0ffee?n={APPLY_WAIT_LIMIT}"
    html = client.get(path, headers=CUSTOMER).text
    assert 'http-equiv="refresh"' not in html and "3초마다" not in html
    assert "자동 새로 고침을 멈췄습니다" in html


@pytest.mark.parametrize(("status", "label", "refresh"), [
    ("received", "심사 중", True),
    ("data_incomplete", "심사 중", True),
    ("assessed", "심사 중", True),
    ("awaiting_officer", "심사역 검토 중", True),
    ("escalated", "심사역 검토 중", True),
    ("declined", "거절", False),
    ("withdrawn", "동의 철회로 종료", False),
    ("closed_no_decision", "처리 종료", False),
    ("failed", "처리 종료", False),
])
def test_status_mapping_hides_internal_evidence(env, status, label, refresh):
    client, store, _ = env
    store.docs["app-cust"]["status"] = status
    html = client.get("/apply/app-cust", headers=CUSTOMER).text
    assert f">{label}</p>" in html
    assert ('http-equiv="refresh"' in html) is refresh
    for internal in ("R101", "DSCR", "dscr", "계절성", "approve", "개인이체"):
        assert internal not in html


def test_approved_status_shows_officer_amount(env):
    client, store, _ = env
    store.docs["app-cust"].update(status="approved", officer_decision={"decision": "approve", "amount_krw": 6_000_000})
    html = client.get("/apply/app-cust", headers=CUSTOMER).text
    assert ">승인</p>" in html and "6,000,000원" in html


@pytest.mark.parametrize(("status", "current", "done"), [
    (None, "접수", 0),
    ("received", "데이터 심사", 1),
    ("assessed", "데이터 심사", 1),
    ("awaiting_officer", "심사역 검토", 2),
    ("approved", "결과", 3),
    ("declined", "결과", 3),
])
def test_progress_steps_follow_status(env, status, current, done):
    client, store, _ = env
    path = "/apply/app-20261104-c0ffee"
    if status:
        store.docs["app-cust"]["status"] = status
        path = "/apply/app-cust"
    html = client.get(path, headers=CUSTOMER).text
    assert f'aria-current="step">{current}</li>' in html
    assert html.count('class="step done"') == done
    assert html.count('aria-current="step"') == 1


@pytest.mark.parametrize(("status", "history", "stage", "done"), [
    ("failed", ["received"], "접수", 0),
    ("failed", ["received", "consent_granted"], "데이터 심사", 1),
    ("withdrawn", ["received", "consent_granted", "awaiting_officer"], "심사역 검토", 2),
    ("closed_no_decision", ["awaiting_officer", "escalated"], "심사역 검토", 2),
    ("failed", [], None, 0),
])
def test_stopped_application_never_completes_unreached_stages(env, status, history, stage, done):
    client, store, _ = env
    store.docs["app-cust"].update(status=status, timeline=[{"status": s} for s in [*history, status]])
    html = client.get("/apply/app-cust", headers=CUSTOMER).text
    assert html.count('class="step done"') == done
    if stage:
        assert f'aria-current="step">{stage} · 처리 중단</li>' in html
    else:
        assert 'aria-current="step"' not in html


@pytest.mark.parametrize("status", ["received", "declined"])
def test_hero_shows_requested_amount_and_tenor(env, status):
    client, store, _ = env
    store.docs["app-cust"]["status"] = status
    html = client.get("/apply/app-cust", headers=CUSTOMER).text
    assert 'class="card hero-card"' in html
    assert '<p class="hero-amount">30,000,000원</p>' in html and "12개월" in html
    assert "신청 금액" in html.split('class="card hero-card">')[1].split("</section>")[0]


@pytest.mark.parametrize("status", ["approved", "conditionally_approved"])
def test_missing_decision_amount_is_not_replaced_with_request(env, status):
    client, store, _ = env
    store.docs["app-cust"].update(status=status, officer_decision={})
    html = client.get("/apply/app-cust", headers=CUSTOMER).text
    assert "승인 금액 미확인" in html and 'class="hero-amount"' not in html


def test_hero_shows_officer_amount_when_approved(env):
    client, store, _ = env
    store.docs["app-cust"].update(status="approved", officer_decision={"decision": "approve", "amount_krw": 6_000_000})
    html = client.get("/apply/app-cust", headers=CUSTOMER).text
    assert '<p class="hero-amount">6,000,000원</p>' in html and "step now tone-good" in html


def test_waiting_hero_has_no_amount(env):
    client, _, _ = env
    html = client.get("/apply/app-20261104-c0ffee", headers=CUSTOMER).text
    assert 'class="card hero-card"' in html and 'class="hero-amount"' not in html


def test_progress_steps_in_english(env):
    client, store, _ = env
    store.docs["app-cust"]["status"] = "awaiting_officer"
    html = client.get("/apply/app-cust", headers={**CUSTOMER, "Cookie": "ama_locale_v2=en"}).text
    assert 'aria-current="step">Officer review</li>' in html
    for korean in ("데이터 심사", "심사역 검토", "접수"):
        assert f">{korean}</li>" not in html


def test_status_page_does_not_expose_console_applications(env):
    client, _, _ = env
    assert client.get("/apply/app-console", headers=CUSTOMER).status_code == 404


def test_console_list_marks_customer_applications(env):
    client, _, _ = env
    rows = client.get("/applications/rows", headers=principal(CREDIT_OFFICER)).text
    assert rows.count("고객 신청") == 1
