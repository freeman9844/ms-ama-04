"""활동 스팬·단계 로그(`journey.otel` 로거 → AppTraces). 속성에 원문 PII를 넣지 않는다.

텔레메트리 실패는 여정을 막지 않는다: 아래 함수는 스스로 예외를 삼킨다.
"""

import logging
from contextlib import contextmanager
from typing import Any

from shared.otel import configure_monitor

LOGGER = "journey.otel"
log = logging.getLogger(LOGGER)


def setup() -> bool:
    log.setLevel(logging.INFO)
    return configure_monitor(LOGGER, instrument_httpx=True)


@contextmanager
def span(name: str, attributes: dict[str, Any]):
    """활동 스팬. 스팬 생성·시작·종료 실패는 삼키고, 본문 예외는 그대로 전파한다."""
    try:
        from opentelemetry import trace

        attrs = {k: v for k, v in attributes.items() if isinstance(v, (str, int, float, bool))}
        cm = trace.get_tracer("journey").start_as_current_span(name, attributes=attrs)
    except Exception:  # noqa: BLE001
        cm = None
    sp = None
    if cm is not None:
        try:
            sp = cm.__enter__()
        except Exception:  # noqa: BLE001
            cm = None
    try:
        yield sp
    except BaseException as e:
        if cm is not None:
            try:
                cm.__exit__(type(e), e, e.__traceback__)
            except Exception:  # noqa: BLE001
                pass
        raise
    if cm is not None:
        try:
            cm.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass


def set_attrs(span: Any, attributes: dict[str, Any]) -> None:
    try:
        if span is not None and hasattr(span, "set_attribute"):
            for k, v in attributes.items():
                span.set_attribute(k, v)
    except Exception:  # noqa: BLE001
        pass


def record_step(record: dict[str, Any], *, application_id: str | None, tags: dict[str, Any]) -> None:
    try:
        usage = record.get("usage") or []
        log.info(
            "journey.step",
            extra={
                "application_id": application_id,
                "step": record.get("step"),
                "outcome": record.get("outcome"),
                "duration_ms": record.get("duration_ms"),
                "agent": bool(record.get("agent")),
                "source": tags.get("source"),
                "run_id": tags.get("run_id"),
                "input_tokens": sum(int(u.get("input_tokens") or 0) for u in usage),
                "output_tokens": sum(int(u.get("output_tokens") or 0) for u in usage),
            },
        )
    except Exception:  # noqa: BLE001
        pass


def circuit_changed(state: str) -> None:
    # 서킷 잠금 안에서 불린다: 막히지 않아야 하고 브레이커를 다시 부르면 안 된다.
    try:
        log.warning("policy.circuit", extra={"state": state})
        from opentelemetry import trace

        trace.get_current_span().add_event("policy.circuit", {"state": state})
    except Exception:  # noqa: BLE001
        pass
