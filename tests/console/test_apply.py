import re
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from console.app import START_QUEUE, ConsoleDeps, create_app
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
    "requested_amount_krw": "7,000,000",
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
                       new_application_id=lambda: "app-20261104-c0ffee", clock=lambda: NOW)
    return TestClient(create_app(deps), follow_redirects=False), store, queue


def token(client, headers=CUSTOMER):
    page = client.get("/apply", headers=headers).text
    return re.search(r'name="csrf" value="([0-9a-f]{64})"', page).group(1)


def apply(client, headers=CUSTOMER, **fields):
    form = {"csrf": token(client, headers)} | VALID | fields
    return client.post("/apply", data={k: v for k, v in form.items() if v is not None}, headers=headers)


def test_apply_page_needs_login_but_no_console_role(env):
    client, _, _ = env
    assert client.get("/apply").status_code == 401
    page = client.get("/apply", headers=CUSTOMER)
    assert page.status_code == 200
    html = page.text
    for name in ("consent_account", "consent_tax", "consent_pg"):
        assert f'name="{name}"' in html
    assert "운전자금 대출 심사" in html and "90일" in html
    assert "성수데일리룩 · 스마트스토어·쿠팡·자사몰 · 패션의류" in html
    assert '<script src="/static/apply.js"></script>' in html
    assert "사후 모니터링" not in html


def test_valid_application_is_enqueued_as_customer(env):
    client, _, queue = env
    response = apply(client)
    assert response.status_code == 303
    assert response.headers["location"] == "/apply/app-20261104-c0ffee"
    [(name, message)] = queue.sent
    assert name == START_QUEUE
    assert message["source"] == "customer"
    assert message["applicant_id"] == "seller-dailylook-01"
    assert message["requested_amount_krw"] == 7_000_000
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
    ({"requested_amount_krw": "499999"}, "amount_invalid"),
    ({"requested_amount_krw": "10000001"}, "amount_invalid"),
    ({"requested_amount_krw": "-5000000"}, "amount_invalid"),
    ({"requested_amount_krw": "오백만"}, "amount_invalid"),
    ({"requested_tenor_months": "2"}, "tenor_invalid"),
    ({"requested_tenor_months": "7"}, "tenor_invalid"),
    ({"requested_tenor_months": "12"}, "tenor_invalid"),
    ({"applicant_id": "seller-unknown-99"}, "applicant_invalid"),
])
def test_out_of_range_input_is_rejected_and_form_kept(env, fields, code):
    client, _, queue = env
    response = apply(client, **fields)
    assert response.status_code == 422
    assert f'data-code="{code}"' in response.text
    assert 'name="consent_tax" class="consent" checked' in response.text
    assert queue.sent == []


def test_boundary_amounts_are_accepted(env):
    client, _, queue = env
    assert apply(client, requested_amount_krw="500000").status_code == 303
    assert apply(client, requested_amount_krw="10,000,000", requested_tenor_months="3").status_code == 303
    assert [m["requested_amount_krw"] for _, m in queue.sent] == [500_000, 10_000_000]
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
    assert '<meta http-equiv="refresh" content="3">' in html


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


def test_status_page_does_not_expose_console_applications(env):
    client, _, _ = env
    assert client.get("/apply/app-console", headers=CUSTOMER).status_code == 404


def test_console_list_marks_customer_applications(env):
    client, _, _ = env
    rows = client.get("/applications/rows", headers=principal(CREDIT_OFFICER)).text
    assert rows.count("고객 신청") == 1
