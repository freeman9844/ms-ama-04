"""영문 화면의 데이터 문구 번역.

화면 문구(_())와 달리 데이터(셀러 상호·정책 사유·에이전트 의견·차주 문구)는 두 단계로 옮긴다.
1) 카탈로그(locales/en/*.json)에 원문이 그대로 있으면 그 영문을 쓴다(상호·채널·업종·지역·정책 규칙·고정 안내 문구).
2) 없으면 번역 에이전트(agents.translator)로 기계 번역한다. 페이지는 원문으로 먼저 그리고, 브라우저(static/mt.js)가
   /i18n/translate로 한 번에 받아 바꾼다. 결과는 프로세스 메모리에 캐시해 다음 렌더부터는 서버가 바로 그린다.
원문이 근거·감사 기준이다. 기계 번역은 항상 표시(mt-done, 원문 툴팁)하고, 차주에게 가는 문구는 원문을 지우지 않는다.
"""

import contextlib
import contextvars
import hashlib
import threading
from collections import OrderedDict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import Any

from markupsafe import Markup

from agents.data_assembly import case_entities
from agents.pretranslate import BATCH_CHARS, BATCH_ITEMS, batches, tokens_kept, translation_id  # noqa: F401 - 배치 상수 재수출
from console import i18n
from shared.pseudonymize import Pseudonymizer, tokens

MAX_ITEMS = 80
MAX_CHARS = 2000
MAX_TOTAL_CHARS = 30000  # 한글 3바이트 기준으로 본문 상한(app.MAX_CHAT_BYTES 128KB) 안에 들어간다
CACHE_SIZE = 5000


def has_korean(text: Any) -> bool:
    return isinstance(text, str) and any("가" <= ch <= "힣" for ch in text)


def catalog(text: str) -> str | None:
    """원문 그대로(앞뒤 공백 무시) 카탈로그에 있으면 영문."""
    return i18n.CATALOGS.get(i18n.current(), {}).get(text.strip()) if i18n.current() != i18n.DEFAULT else None


def data(text: Any) -> Any:
    """결정론 번역만: 카탈로그에 있으면 영문, 없으면 원문. 차트 라벨처럼 지연 번역을 붙일 수 없는 곳에 쓴다."""
    if not has_korean(text):
        return text
    return catalog(text) or text


def data_label(text: Any) -> Any:
    """'상호 · 채널·채널 · 업종'처럼 가운뎃점으로 이어 붙인 데이터 라벨을 조각마다 옮긴다."""
    if not has_korean(text) or i18n.current() == i18n.DEFAULT:
        return text
    return " · ".join(catalog(part) or "·".join(str(data(p)) for p in part.split("·")) for part in text.split(" · "))


class TranslationCache:
    def __init__(self, size: int = CACHE_SIZE) -> None:
        self._size = size
        self._items: OrderedDict[str, str] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def key(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def get(self, text: str) -> str | None:
        k = self.key(text)
        with self._lock:
            value = self._items.get(k)
            if value is not None:
                self._items.move_to_end(k)
            return value

    def put(self, text: str, value: str) -> None:
        k = self.key(text)
        with self._lock:
            self._items[k] = value
            self._items.move_to_end(k)
            while len(self._items) > self._size:
                self._items.popitem(last=False)


class MachineTranslation:
    """agent.translate(texts) -> 결과(.translations, .input_tokens, .output_tokens)를 감싼 캐시."""

    def __init__(self, agent: Any, cache: TranslationCache | None = None) -> None:
        self._agent = agent
        self.cache = cache or TranslationCache()

    def lookup(self, masked: str) -> str | None:
        """키는 가명처리한 문구(번역 에이전트에 보낸 것과 같다)."""
        return self.cache.get(masked)

    def translate(self, texts: Sequence[str]) -> tuple[list[str], int, int]:
        """가명처리한 문구 순서대로 영문. 카탈로그·캐시에 없는 한국어만 에이전트로 보낸다. (번역, 입력 토큰, 출력 토큰).

        토큰이 빠지거나 바뀐 결과는 캐시하지 않고 그 항목에 ""를 돌려준다(호출자는 번역 없음으로 본다).
        """
        missing = list(dict.fromkeys(t for t in texts if has_korean(t) and not catalog(t) and self.cache.get(t) is None))
        tokens_in = tokens_out = 0
        rejected: set[str] = set()
        for batch in batches(missing):
            result = self._agent.translate(batch)
            tokens_in += result.input_tokens
            tokens_out += result.output_tokens
            for src, dst in zip(batch, result.translations, strict=True):
                if not dst:
                    continue
                if tokens_kept(src, dst):
                    self.cache.put(src, dst)
                else:
                    rejected.add(src)
        out = [
            t if not has_korean(t) else (catalog(t) or self.cache.get(t) or ("" if t in rejected else t)) for t in texts
        ]
        return out, tokens_in, tokens_out


def validate_texts(body: Any) -> list[str] | None:
    texts = body.get("texts") if isinstance(body, dict) else None
    if not isinstance(texts, list) or not texts or len(texts) > MAX_ITEMS:
        return None
    if not all(isinstance(t, str) and 0 < len(t) <= MAX_CHARS for t in texts):
        return None
    if sum(len(t) for t in texts) > MAX_TOTAL_CHARS:
        return None
    return texts


class PageTexts:
    """한 화면의 문구를 가명처리하고 저장된 영문 번역을 찾는다.

    masker는 그 화면이 다루는 신청의 엔티티를 아는 가명처리기다. 영문 표시는 한국어 화면에서 평문으로 보이던 값만
    복원한다(마스킹으로 새로 생긴 토큰). 이미 토큰으로 보이던 문구(감사자 화면)는 토큰 그대로 둔다.
    """

    def __init__(self, masker: Pseudonymizer, stored: Mapping[str, str] | None = None) -> None:
        self._masker = masker
        self._stored = stored or {}
        self._tokens: dict[str, str] = {}

    @classmethod
    def for_case(cls, key: bytes, profile: Any, transactions: Sequence[Any], stored: Mapping[str, str] | None = None):
        return cls(Pseudonymizer(key, case_entities(profile, transactions)), stored)

    @classmethod
    def merged(cls, key: bytes, cases: Iterable[tuple[Any, Sequence[Any]]], stored: Mapping[str, str] | None = None):
        """여러 신청이 한 화면에 섞일 때: 엔티티의 합집합."""
        entities: dict[str, str] = {}
        for profile, transactions in cases:
            entities.update(case_entities(profile, transactions))
        return cls(Pseudonymizer(key, entities), stored)

    def masked(self, text: str) -> str:
        return self._masker.mask(text)

    def _fresh(self, text: str, masked: str) -> dict[str, str]:
        """text에는 없고 마스킹으로 새로 생긴 토큰 → 원래 값."""
        mapping = self._masker.mapping
        return {t: mapping[t] for t in tokens(masked) - tokens(text) if t in mapping}

    def show(self, text: str, en: str) -> str:
        fresh = self._fresh(text, self.masked(text))
        for token, value in fresh.items():
            en = en.replace(token, catalog(value) or value)
        return en

    def stored_en(self, text: str) -> str | None:
        masked = self.masked(text)
        en = self._stored.get(translation_id(masked))
        if not en or not tokens_kept(masked, en):
            return None
        return self.show(text, en)

    def pending(self, text: str) -> str:
        masked = self.masked(text)
        self._tokens.update(self._fresh(text, masked))
        return masked

    def token_map(self) -> dict[str, str]:
        return dict(self._tokens)


_page: contextvars.ContextVar[PageTexts | None] = contextvars.ContextVar("console_page_texts", default=None)


def current_page() -> PageTexts | None:
    return _page.get()


@contextlib.contextmanager
def page_scope(page: PageTexts) -> Iterator[None]:
    token = _page.set(page)
    try:
        yield
    finally:
        _page.reset(token)


def _cached_en(text: str, page: PageTexts, translator: MachineTranslation | None) -> str | None:
    """저장 번역 → 이 프로세스의 기계 번역 캐시(키는 마스킹 문구) 순."""
    stored = page.stored_en(text)
    if stored:
        return stored
    cached = translator.lookup(page.masked(text)) if translator else None
    return page.show(text, cached) if cached else None


def _mark(text: str, mode: str, translator: MachineTranslation | None, key: bytes) -> Markup | str:
    if i18n.current() == i18n.DEFAULT or not has_korean(text):
        return text
    fixed = catalog(text)
    if mode == "ref":
        # 차주에게 가는 문구: 원문(실제 발송 문구)을 두고 영문을 아래에 덧붙인다.
        if fixed:
            return Markup('{}<span class="mt-ref">{}</span>').format(text, fixed)
        page = current_page() or PageTexts(Pseudonymizer(key))
        cached = _cached_en(text, page, translator)
        if cached:
            return Markup('{}<span class="mt-ref mt-done">{}</span>').format(text, cached)
        if translator:
            return Markup('{}<span class="mt-ref" data-mt="{}"></span>').format(text, page.pending(text))
        return text
    if fixed:
        return fixed
    page = current_page() or PageTexts(Pseudonymizer(key))
    cached = _cached_en(text, page, translator)
    if cached:
        return Markup('<span class="mt mt-done" title="{}">{}</span>').format(i18n.gettext("기계 번역 · 원문: %(t)s", t=text), cached)
    if translator is None:
        return text
    return Markup('<span class="mt" data-mt="{}">{}</span>').format(page.pending(text), text)


def mt_filters(translator: MachineTranslation | None, key: bytes) -> dict[str, Any]:
    return {
        "mt": lambda text: _mark(text, "inline", translator, key) if isinstance(text, str) else text,
        "mt_ref": lambda text: _mark(text, "ref", translator, key) if isinstance(text, str) else text,
        "dt": data,
        "dl": data_label,
    }