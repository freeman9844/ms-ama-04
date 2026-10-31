"""LLM 전송 전 PII 가명처리(HMAC 토큰)와 복원.

한글은 정규식에서 단어 문자(\\w)이므로 '\\b' 대신 숫자 lookaround를 쓴다.
"""

import hashlib
import hmac
import re
from collections.abc import Callable, Mapping

_TOKEN_RE = re.compile(r"\[\[[A-Z]+_[0-9a-f]{10}\]\]")

# 순서가 중요하다: BIZNO(3-2-5)와 PHONE(3-4-4)이 ACCOUNT 패턴에도 걸리므로 먼저 처리한다.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("BIZNO", re.compile(r"(?<!\d)\d{3}-\d{2}-\d{5}(?!\d)")),
    ("PHONE", re.compile(r"(?<!\d)01[016789]-?\d{3,4}-?\d{4}(?!\d)")),
    ("ACCOUNT", re.compile(r"(?<!\d)\d{3,6}-\d{2,6}-\d{2,8}(?!\d)")),
)


def _entity_regex(known_entities: Mapping[str, str]) -> re.Pattern[str] | None:
    if not known_entities:
        return None
    values = sorted(known_entities, key=len, reverse=True)
    return re.compile("|".join(re.escape(v) for v in values))


def _sub_outside_tokens(text: str, pattern: re.Pattern[str], repl: Callable[[re.Match[str]], str]) -> str:
    """기존 토큰 내부는 건드리지 않고 나머지 구간에만 치환을 적용한다."""
    out: list[str] = []
    pos = 0
    for tok in _TOKEN_RE.finditer(text):
        out.append(pattern.sub(repl, text[pos : tok.start()]))
        out.append(tok.group(0))
        pos = tok.end()
    out.append(pattern.sub(repl, text[pos:]))
    return "".join(out)


def _strip_tokens(text: str) -> str:
    return _TOKEN_RE.sub(" ", text)


class Pseudonymizer:
    def __init__(self, key: bytes, known_entities: Mapping[str, str] | None = None) -> None:
        if len(key) < 32:
            raise ValueError("가명처리 키는 32바이트 이상이어야 한다")
        self._key = key
        self._entities = dict(known_entities or {})
        self._entity_re = _entity_regex(self._entities)
        self._mapping: dict[str, str] = {}

    def _token(self, kind: str, value: str) -> str:
        digest = hmac.new(self._key, f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()[:10]
        token = f"[[{kind}_{digest}]]"
        self._mapping[token] = value
        return token

    def mask(self, text: str) -> str:
        if self._entity_re is not None:
            text = _sub_outside_tokens(
                text, self._entity_re, lambda m: self._token(self._entities[m.group(0)], m.group(0))
            )
        for kind, pattern in _PATTERNS:
            text = _sub_outside_tokens(text, pattern, lambda m, k=kind: self._token(k, m.group(0)))
        return text

    def restore(self, text: str) -> str:
        return _TOKEN_RE.sub(lambda m: self._mapping.get(m.group(0), m.group(0)), text)

    @property
    def mapping(self) -> dict[str, str]:
        return dict(self._mapping)


def find_pii(text: str, known_entities: Mapping[str, str] | None = None) -> list[tuple[str, str]]:
    """유출 검사용: 토큰 바깥에 남아 있는 PII를 (KIND, 값) 목록으로 반환한다."""
    plain = _strip_tokens(text)
    found: list[tuple[str, str]] = []
    entities = dict(known_entities or {})
    entity_re = _entity_regex(entities)
    if entity_re is not None:
        found.extend((entities[m.group(0)], m.group(0)) for m in entity_re.finditer(plain))
        plain = entity_re.sub(" ", plain)
    # 마스킹과 같은 우선순위로, 앞 패턴에 잡힌 구간은 지워 중복 보고를 막는다.
    for kind, pattern in _PATTERNS:
        found.extend((kind, m.group(0)) for m in pattern.finditer(plain))
        plain = pattern.sub(" ", plain)
    return found
