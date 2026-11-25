import hashlib
import hmac
import json
import logging
import re

import pytest
from fastapi.testclient import TestClient

from agents.officer_chat import ChatAnswer
from agents.runtime import AgentError, GatewayBlocked, GatewayThrottled
from console.app import ConsoleDeps, create_app
from console.auth import csrf_token
from console.officer_chat import CHAT_SUGGESTIONS
from tests.console.test_app import AUDITOR_H, NO_ROLE, OFFICER, FakeQueue, FakeStore


class FakeChat:
    def __init__(self, error=None):
        self.error, self.calls = error, []

    def ask(self, snapshot, history, question, language="ko", *, mask=None):
        self.mask = mask
        if mask is not None:
            question = mask(question)
            history = [{**t, "text": mask(t["text"])} for t in history]
        self.calls.append((snapshot, history, question))
        self.language = language
        if self.error:
            raise self.error
        return ChatAnswer("정책 판정은 eligible입니다(정책 R101).", 120, 30)


def _client(make_doc, raw, key, chat):
    deps = ConsoleDeps(store=FakeStore([make_doc()]), raw=raw, queue=FakeQueue(), pseudonym_key=key, chat=chat)
    return TestClient(create_app(deps), follow_redirects=False, cookies={"ama_locale_v2": "ko"})


def _token(key, user_id="oid-officer"):
    return csrf_token(hmac.new(key, b"console-csrf", hashlib.sha256).digest(), user_id)


def _post(client, key, body, headers=OFFICER, user_id="oid-officer", path="/applications/app-1/chat"):
    return client.post(path, json=body, headers=headers | {"X-CSRF-Token": _token(key, user_id)})


def test_chat_answers_with_snapshot(make_doc, raw, key):
    chat = FakeChat()
    r = _post(_client(make_doc, raw, key, chat), key, {"question": "  회부 사유는?  ", "history": []})
    assert r.status_code == 200 and r.json() == {"answer": "정책 판정은 eligible입니다(정책 R101)."}
    snapshot, history, question = chat.calls[0]
    assert question == "회부 사유는?" and history == []
    assert snapshot["application"]["application_id"] == "app-1" and snapshot["policy"]["decision"] == "eligible"


def test_chat_answers_in_page_language(make_doc, raw, key):
    chat = FakeChat()
    client = _client(make_doc, raw, key, chat)
    _post(client, key, {"question": "q"})
    assert chat.language == "ko"
    client.cookies.set("ama_locale_v2", "en")
    _post(client, key, {"question": "q"})
    assert chat.language == "en"


def test_chat_sends_no_pii_and_masks_officer_text_with_case_entities(make_doc, raw, key):
    chat = FakeChat()
    history = [{"role": "user", "text": "김대표 이체는?"}, {"role": "assistant", "text": "자료에 없음"}]
    _post(_client(make_doc, raw, key, chat), key, {"question": "빈스카페 김대표의 상환 여력은?", "history": history})
    text = json.dumps(chat.calls[0], ensure_ascii=False)
    assert "빈스카페" not in text and "김대표" not in text and "seller-dailylook-01" not in text
    assert "[[" in chat.calls[0][2] and "[[" in chat.calls[0][1][0]["text"]


def test_chat_refuses_when_raw_cannot_be_read(make_doc, key):
    class Broken:
        def get_json(self, container, name):
            raise OSError("blob down")

    chat = FakeChat()
    r = _post(_client(make_doc, Broken(), key, chat), key, {"question": "빈스카페는?"})
    assert r.status_code == 502 and chat.calls == []


def test_chat_without_raw_uses_pattern_masking(make_doc, key):
    from tests.console.conftest import FakeRaw

    chat = FakeChat()
    r = _post(_client(make_doc, FakeRaw({}), key, chat), key, {"question": "010-1234-5678 번호는?"})
    assert r.status_code == 200 and "010-1234-5678" not in chat.calls[0][2]


def test_chat_trims_history(make_doc, raw, key):
    chat = FakeChat()
    history = [{"role": "user" if i % 2 == 0 else "assistant", "text": "다" * 2001} for i in range(20)]
    _post(_client(make_doc, raw, key, chat), key, {"question": "q", "history": history})
    sent = chat.calls[0][1]
    assert len(sent) == 12 and all(len(m["text"]) == 2000 for m in sent)


def test_chat_auth_and_csrf(make_doc, raw, key):
    client = _client(make_doc, raw, key, FakeChat())
    body = {"question": "q"}
    assert client.post("/applications/app-1/chat", json=body).status_code == 401
    assert client.post("/applications/app-1/chat", json=body, headers=NO_ROLE).status_code == 403
    assert client.post("/applications/app-1/chat", json=body, headers=OFFICER).status_code == 403
    assert client.post("/applications/app-1/chat", json=body,
                       headers=OFFICER | {"X-CSRF-Token": "0" * 64}).status_code == 403
    assert _post(client, key, body, headers=AUDITOR_H, user_id="oid-auditor").status_code == 200


@pytest.mark.parametrize("question", ["", "   ", "가" * 501, None])
def test_chat_rejects_bad_question(make_doc, raw, key, question):
    r = _post(_client(make_doc, raw, key, FakeChat()), key, {"question": question})
    assert r.status_code == 422 and r.json() == {"error": "질문은 1~500자로 입력해 주세요."}


def test_chat_not_found_and_disabled(make_doc, raw, key):
    r = _post(_client(make_doc, raw, key, FakeChat()), key, {"question": "q"}, path="/applications/nope/chat")
    assert r.status_code == 404 and r.json() == {"error": "신청을 찾을 수 없습니다."}
    r = _post(_client(make_doc, raw, key, None), key, {"question": "q"})
    assert r.status_code == 503 and r.json() == {"error": "채팅을 쓸 수 없습니다."}


def test_usd_chat_disabled_error_is_english_with_korean_locale(raw, key):
    from shared.contracts import USD_V1
    doc = dict(USD_V1.model_dump(), id="app-1", application_id="app-1", requested_amount_cents=500000)
    response = _post(_client(lambda: doc, raw, key, None), key, {"question": "Can I chat?"})
    assert response.status_code == 503 and response.json()["error"].isascii()


def test_usd_missing_mask_sources_do_not_send_free_text(key):
    from shared.contracts import USD_V1
    from tests.console.conftest import FakeRaw
    doc = dict(USD_V1.model_dump(), id="app-1", application_id="app-1", consent={"consent_id": "missing"})
    chat = FakeChat()
    response = _post(_client(lambda: doc, FakeRaw({}), key, chat), key,
                     {"question": "Ask Jamie Park.", "history": [{"role": "user", "text": "Jamie Park"}]})
    assert response.status_code == 502 and not chat.calls


@pytest.mark.parametrize("partial", ["policy", "cash_only", "features", "monthly"])
def test_usd_chat_rejects_display_only_partial_evidence(fake_raw, raw_profile, key, partial):
    from shared.contracts import USD_V1
    from tests.console.test_assessment import usd_doc

    usd = USD_V1.model_dump()
    doc = usd_doc()
    doc.update(id="usd-1", consent={"consent_id": "c-1"})
    doc["underwriting"]["features"]["mkt_rating"] = 4.8
    doc["policy"].update(application_id="usd-1", cash_only=dict(usd, decision="eligible", max_amount_cents=410000,
                                                                 reason_codes=[]))
    doc["underwriting"].update(signals={"new_installment_cents": 101}, monthly=[
        {"month": "2026-01", "revenue_cents": 1000, "operating_out_cents": 100, "debt_service_cents": 0,
         "owner_draw_cents": 0}])
    raw = fake_raw({("consent-c-1", "profile.json"): {**raw_profile, **usd}, ("consent-c-1", "transactions.json"): []})
    chat = FakeChat()
    client = _client(lambda: doc, raw, key, chat)
    def ask():
        return _post(client, key, {"question": "Explain my policy limit."}, path="/applications/usd-1/chat")
    assert ask().status_code == 200 and len(chat.calls) == 1
    if partial == "policy":
        del doc["policy"]["language"]
    elif partial == "cash_only":
        del doc["policy"]["cash_only"]["language"]
    elif partial == "features":
        del doc["underwriting"]["features"]["avg_monthly_inflow_cents"]
    else:
        del doc["underwriting"]["monthly"][0]["debt_service_cents"]
    assert ask().status_code == 502 and len(chat.calls) == 1


@pytest.mark.parametrize("error, status, message", [
    (GatewayThrottled("rate_limited", "c"), 429, "요청이 많습니다. 잠시 후 다시 질문해 주세요."),
    (GatewayBlocked("content_blocked", "c"), 422, "이 질문에는 답할 수 없습니다."),
    (AgentError("응답 상태 failed", "c"), 502, "답변을 받지 못했습니다."),
    (RuntimeError("token"), 502, "답변을 받지 못했습니다."),
])
def test_chat_maps_agent_errors(make_doc, raw, key, error, status, message):
    r = _post(_client(make_doc, raw, key, FakeChat(error)), key, {"question": "q"})
    assert r.status_code == status and r.json() == {"error": message}


def test_chat_rejects_bad_bodies(make_doc, raw, key):
    client = _client(make_doc, raw, key, FakeChat())
    headers = OFFICER | {"X-CSRF-Token": _token(key)}
    assert client.post("/applications/app-1/chat", data={"question": "q"}, headers=headers).status_code == 415
    r = client.post("/applications/app-1/chat", content=b"{bad", headers=headers | {"Content-Type": "application/json"})
    assert r.status_code == 400 and r.json() == {"error": "요청 형식이 올바르지 않습니다."}
    r = client.post("/applications/app-1/chat", content=b"[1]", headers=headers | {"Content-Type": "application/json"})
    assert r.status_code == 400
    big = json.dumps({"question": "q", "history": [{"role": "user", "text": "x" * 140_000}]}).encode()
    r = client.post("/applications/app-1/chat", content=big, headers=headers | {"Content-Type": "application/json"})
    assert r.status_code == 413


def test_chat_log_has_metadata_only(make_doc, raw, key, caplog):
    caplog.set_level(logging.INFO, logger="console.chat")
    _post(_client(make_doc, raw, key, FakeChat()), key, {"question": "비밀 질문 문장"})
    _post(_client(make_doc, raw, key, FakeChat(GatewayThrottled("rate_limited", "c"))), key, {"question": "두번째 질문"})
    text = caplog.text
    assert "application_id=app-1" in text and "result=ok" in text and "result=throttled" in text
    assert "input_tokens=120" in text
    assert "비밀 질문" not in text and "두번째 질문" not in text and "eligible입니다" not in text


def test_decision_tab_renders_chat_panel(make_doc, raw, key):
    page = _client(make_doc, raw, key, FakeChat()).get("/applications/app-1", headers=OFFICER).text
    assert 'class="decision-layout"' in page and "data-officer-chat" in page
    assert 'data-url="/applications/app-1/chat"' in page
    assert re.search(r'data-csrf="([0-9a-f]{64})"', page).group(1) == _token(key)
    assert '<script src="/static/officer_chat.js" defer></script>' in page
    for q in CHAT_SUGGESTIONS:
        assert f">{q}</button>" in page
    assert 'maxlength="500"' in page
    assert 'name="decision"' in page  # 결정 폼은 그대로


def test_chat_panel_disabled_notice(make_doc, raw, key):
    page = _client(make_doc, raw, key, None).get("/applications/app-1", headers=OFFICER).text
    assert "채팅을 쓸 수 없습니다." in page and "officer_chat.js" not in page


def test_chat_panel_hidden_without_policy(make_doc, raw, key):
    deps = ConsoleDeps(store=FakeStore([make_doc(status="assessing", policy=None, assessment=None)]), raw=raw,
                       queue=FakeQueue(), pseudonym_key=key, chat=FakeChat())
    page = TestClient(create_app(deps), cookies={"ama_locale_v2": "ko"}).get("/applications/app-1", headers=OFFICER).text
    assert "심사 도우미" not in page


def test_chat_script_is_static_and_keeps_history_in_memory(make_doc, raw, key):
    r = _client(make_doc, raw, key, FakeChat()).get("/static/officer_chat.js")
    assert r.status_code == 200
    assert "X-CSRF-Token" in r.text and "localStorage" not in r.text and "sessionStorage" not in r.text
