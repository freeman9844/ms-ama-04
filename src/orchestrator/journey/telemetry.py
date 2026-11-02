"""활동 스팬·단계 로그. OTel이 꺼져 있으면 아무것도 하지 않는다(T5에서 구현)."""

from contextlib import nullcontext
from typing import Any


def span(name: str, attributes: dict[str, Any]):
    return nullcontext()


def set_attrs(span: Any, attributes: dict[str, Any]) -> None:
    return None


def record_step(record: dict[str, Any], *, application_id: str | None, tags: dict[str, Any]) -> None:
    return None
