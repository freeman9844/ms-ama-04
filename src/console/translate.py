"""영문 화면의 데이터 문구 번역.

화면 문구(_())와 달리 데이터(셀러 상호·정책 사유·에이전트 의견·차주 문구)는 두 단계로 옮긴다.
1) 카탈로그(locales/en/*.json)에 원문이 그대로 있으면 그 영문을 쓴다(상호·채널·업종·지역·정책 규칙·고정 안내 문구).
2) 없으면 번역 에이전트(agents.translator)로 기계 번역한다. 페이지는 원문으로 먼저 그리고, 브라우저(static/mt.js)가
   /i18n/translate로 한 번에 받아 바꾼다. 결과는 프로세스 메모리에 캐시해 다음 렌더부터는 서버가 바로 그린다.
원문이 근거·감사 기준이다. 기계 번역은 항상 표시(mt-done, 원문 툴팁)하고, 차주에게 가는 문구는 원문을 지우지 않는다.
"""

import hashlib
import threading
from collections import OrderedDict
from collections.abc import Iterable, Sequence
from typing import Any

from markupsafe import Markup

from console import i18n

MAX_ITEMS = 80
MAX_CHARS = 2000
MAX_TOTAL_CHARS = 30000  # 한글 3바이트 기준으로 본문 상한(app.MAX_CHAT_BYTES 128KB) 안에 들어간다
BATCH_ITEMS = 25
BATCH_CHARS = 8000
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


def _batches(texts: Sequence[str]) -> Iterable[list[str]]:
    batch: list[str] = []
    chars = 0
    for t in texts:
        if batch and (len(batch) >= BATCH_ITEMS or chars + len(t) > BATCH_CHARS):
            yield batch
            batch, chars = [], 0
        batch.append(t)
        chars += len(t)
    if batch:
        yield batch


class MachineTranslation:
    """agent.translate(texts) -> 결과(.translations, .input_tokens, .output_tokens)를 감싼 캐시."""

    def __init__(self, agent: Any, cache: TranslationCache | None = None) -> None:
        self._agent = agent
        self.cache = cache or TranslationCache()

    def lookup(self, text: str) -> str | None:
        return self.cache.get(text)

    def translate(self, texts: Sequence[str]) -> tuple[list[str], int, int]:
        """원문 순서대로 영문. 카탈로그·캐시에 없는 한국어만 에이전트로 보낸다. (번역, 입력 토큰, 출력 토큰)."""
        missing = list(dict.fromkeys(t for t in texts if has_korean(t) and not catalog(t) and self.cache.get(t) is None))
        tokens_in = tokens_out = 0
        for batch in _batches(missing):
            result = self._agent.translate(batch)
            tokens_in += result.input_tokens
            tokens_out += result.output_tokens
            for src, dst in zip(batch, result.translations, strict=True):
                if dst:
                    self.cache.put(src, dst)
        out = [t if not has_korean(t) else (catalog(t) or self.cache.get(t) or t) for t in texts]
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


def _mark(text: str, mode: str, translator: MachineTranslation | None) -> Markup | str:
    if i18n.current() == i18n.DEFAULT or not has_korean(text):
        return text
    fixed = catalog(text)
    if mode == "ref":
        # 차주에게 가는 문구: 원문(실제 발송 문구)을 두고 영문을 아래에 덧붙인다.
        if fixed:
            return Markup('{}<span class="mt-ref">{}</span>').format(text, fixed)
        cached = translator.lookup(text) if translator else None
        if cached:
            return Markup('{}<span class="mt-ref mt-done">{}</span>').format(text, cached)
        if translator:
            return Markup('{}<span class="mt-ref" data-mt="{}"></span>').format(text, text)
        return text
    if fixed:
        return fixed
    if translator is None:
        return text
    cached = translator.lookup(text)
    if cached:
        return Markup('<span class="mt mt-done" title="{}">{}</span>').format(i18n.gettext("기계 번역 · 원문: %(t)s", t=text), cached)
    return Markup('<span class="mt" data-mt="{}">{}</span>').format(text, text)


def mt_filters(translator: MachineTranslation | None) -> dict[str, Any]:
    return {
        "mt": lambda text: _mark(text, "inline", translator) if isinstance(text, str) else text,
        "mt_ref": lambda text: _mark(text, "ref", translator) if isinstance(text, str) else text,
        "dt": data,
        "dl": data_label,
    }
