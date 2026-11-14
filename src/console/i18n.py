"""화면 문구 국문/영문 전환.

한국어 원문이 곧 메시지 키다. 영문은 locales/en/*.json(원문 → 영문)에서 찾고, 없으면 원문을 그대로 쓴다.
언어는 요청마다 ?lang= → lang 쿠키 → Accept-Language 순으로 정하고 ContextVar에 담는다.
값을 끼워 넣는 문구는 Jinja newstyle gettext와 같은 %(name)s 형식을 쓴다(리터럴 %는 %%).
LLM 출력·고객 발송 문구·저장된 데이터는 번역하지 않는다.
"""

import json
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

LOCALES = ("ko", "en")
DEFAULT = "ko"
COOKIE = "lang"
COOKIE_MAX_AGE = 365 * 24 * 3600
LOCALE_DIR = Path(__file__).parent / "locales"

_locale: ContextVar[str] = ContextVar("console_locale", default=DEFAULT)
_request_path: ContextVar[tuple[str, tuple[tuple[str, str], ...]]] = ContextVar("console_path", default=("/", ()))


def load_catalog(lang: str, directory: Path = LOCALE_DIR) -> dict[str, str]:
    merged: dict[str, str] = {}
    for path in sorted((directory / lang).glob("*.json")):
        for source, text in json.loads(path.read_text(encoding="utf-8")).items():
            if source in merged and merged[source] != text:
                raise ValueError(f"{path.name}: {source!r} 번역이 다른 파일과 다릅니다")
            merged[source] = text
    return merged


CATALOGS: dict[str, dict[str, str]] = {"en": load_catalog("en")}


def current() -> str:
    return _locale.get()


def gettext(message: str, **params: Any) -> str:
    lang = current()
    text = message if lang == DEFAULT else CATALOGS.get(lang, {}).get(message, message)
    return text % params if params else text


_ = gettext


def pgettext(context: str, message: str, **params: Any) -> str:
    """같은 한국어가 문맥마다 다른 영어가 될 때(예: 버튼 '승인'=Approve, 상태 '승인'=Approved). 키는 'context|원문'."""
    lang = current()
    text = message if lang == DEFAULT else CATALOGS.get(lang, {}).get(f"{context}|{message}", message)
    return text % params if params else text


def label(labels: dict[str, str], key: str | None, default: str | None = None) -> str:
    """상태 코드 → 표시명. 사전에 없으면 default(없으면 코드 자체, 빈 값은 '-')."""
    text = labels.get(key or "")
    if text is None:
        text = default if default is not None else (key or "-")
    return gettext(text)


def negotiate(query: str | None, cookie: str | None, accept_language: str) -> str:
    for candidate in (query, cookie):
        if candidate in LOCALES:
            return candidate
    for part in accept_language.split(","):
        tag = part.split(";")[0].strip().lower()
        primary = tag.split("-")[0]
        if primary in LOCALES:
            return primary
    return DEFAULT


def lang_url(lang: str) -> str:
    """지금 화면을 다른 언어로 여는 URL. 큐 반영 확인용 since/n은 뺀다."""
    path, query = _request_path.get()
    kept = [(k, v) for k, v in query if k not in {"lang", "since", "n"}]
    return f"{path}?{urlencode([*kept, ('lang', lang)])}"


class LocaleMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        query = request.query_params.get("lang")
        lang = negotiate(query, request.cookies.get(COOKIE), request.headers.get("accept-language", ""))

        async def send_with_cookie(message: Message) -> None:
            if message["type"] == "http.response.start" and query in LOCALES:
                MutableHeaders(scope=message).append(
                    "set-cookie", f"{COOKIE}={lang}; Path=/; Max-Age={COOKIE_MAX_AGE}; SameSite=Lax")
            await send(message)

        locale_token = _locale.set(lang)
        path_token = _request_path.set((request.url.path, tuple(request.query_params.multi_items())))
        try:
            await self.app(scope, receive, send_with_cookie)
        finally:
            _request_path.reset(path_token)
            _locale.reset(locale_token)
