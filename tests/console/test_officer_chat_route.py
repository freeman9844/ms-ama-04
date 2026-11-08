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

    def ask(self, snapshot, history, question):
        self.calls.append((snapshot, history, question))
        if self.error:
            raise self.error
        return ChatAnswer("정책 판정은 eligible입니다(정책 R101).", 120, 30)


def _client(make_doc, raw, key, chat):
    deps = ConsoleDeps(store=FakeStore([make_doc()]), raw=raw, queue=FakeQueue(), pseudonym_key=key, chat=chat)
    return TestClient(create_app(deps), follow_redirects=False)


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


def test_chat_never_reads_raw_and_sends_no_pii(make_doc, raw, key):
    chat = FakeChat()
    _post(_client(make_doc, raw, key, chat), key, {"question": "요약해 줘"})
    assert raw.reads == []
    text = json.dumps(chat.calls[0][0], ensure_ascii=False)
    assert "빈스카페" not in text and "김대표" not in text and "seller-dailylook-01" not in text


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
    page = TestClient(create_app(deps)).get("/applications/app-1", headers=OFFICER).text
    assert "심사 도우미" not in page


def test_chat_script_is_static_and_keeps_history_in_memory(make_doc, raw, key):
    r = _client(make_doc, raw, key, FakeChat()).get("/static/officer_chat.js")
    assert r.status_code == 200
    assert "X-CSRF-Token" in r.text and "localStorage" not in r.text and "sessionStorage" not in r.text
