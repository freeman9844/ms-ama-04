"""주장 문장 속 숫자와 인용 근거 값의 대조(결정적). Underwriting·Early-Warning이 같이 쓴다."""

import re
from collections.abc import Sequence

TOLERANCE = 0.02
_STRIP = (
    re.compile(r"\[\[[A-Z]+_[0-9a-f]+\]\]"),
    re.compile(r"[MSPWB]:[A-Za-z0-9_.:-]+"),
    re.compile(r"\d{4}-\d{2}(?:-\d{2})?"),
    re.compile(r"\bR\d{3}\b"),
    re.compile(r"(?<![\d.])(?:19|20)\d{2}\s*년"),
)
_NUMBER = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(억|천만|백만|만|천)?\s*(%|퍼센트)?")
_UNITS = {"억": 1e8, "천만": 1e7, "백만": 1e6, "만": 1e4, "천": 1e3}


def numbers(text: str) -> list[tuple[str, float]]:
    """문장 속 검증 대상 숫자. 근거 ID·날짜·사유 코드·연도와 단위 없는 100 미만 정수는 뺀다."""
    for pattern in _STRIP:
        text = pattern.sub(" ", text)
    out = []
    for m in _NUMBER.finditer(text):
        raw, unit, pct = m.group(1), m.group(2), m.group(3)
        try:
            value = float(raw.replace(",", "")) * _UNITS.get(unit or "", 1)
        except ValueError:
            continue
        if not unit and not pct and "." not in raw and value < 100:
            continue
        out.append((m.group(0).strip(), value))
    return out


def close(a: float, b: float) -> bool:
    return abs(a - b) <= max(abs(b) * TOLERANCE, 0.005)


def matches(value: float, cited: Sequence[float]) -> bool:
    return any(close(value, v) or close(value, v * 100) or close(value, abs(v)) for v in cited)


def ungrounded(text: str, cited: Sequence[float]) -> list[str]:
    return [raw for raw, value in numbers(text) if not matches(value, cited)]
