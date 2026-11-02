"""Azure Monitor OpenTelemetry 초기화. SDK는 지연 import하고, 실패해도 서비스는 계속 돈다."""

import logging
import os
from collections.abc import Mapping
from typing import Any

log = logging.getLogger(__name__)


# ASGI 내부 http send/receive 스팬은 요청마다 의존성 행을 늘릴 뿐이라 내보내지 않는다(spec §5.2).
# 설치된 fastapi 계측은 이를 환경 변수로 받지 않고 instrument_app(exclude_spans=...)로만 받는다.
FASTAPI_EXCLUDE_SPANS = ["receive", "send"]


def configure_monitor(
    logger_name: str,
    env: Mapping[str, str] = os.environ,
    instrument_httpx: bool = False,
    fastapi_auto: bool = True,
) -> bool:
    """fastapi_auto=False면 FastAPI 자동 계측을 끈다. 앱을 만든 뒤 instrument_fastapi(app)를 부른다."""
    conn = env.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not conn:
        return False
    try:
        from azure.identity import ManagedIdentityCredential
        from azure.monitor.opentelemetry import configure_azure_monitor

        options: dict[str, Any] = {}
        if not fastapi_auto:
            options["instrumentation_options"] = {"fastapi": {"enabled": False}}
        configure_azure_monitor(
            connection_string=conn,
            credential=ManagedIdentityCredential(client_id=env.get("AZURE_CLIENT_ID")),
            logger_name=logger_name,
            enable_live_metrics=False,
            **options,
        )
        if instrument_httpx:
            from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

            HTTPXClientInstrumentor().instrument()
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("OTel 초기화 실패: %s", type(e).__name__)
        return False


def instrument_fastapi(app: Any, tracer_provider: Any = None) -> bool:
    """FastAPI 앱 계측(send/receive 스팬 제외). 제외 URL은 OTEL_PYTHON_FASTAPI_EXCLUDED_URLS를 따른다."""
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, tracer_provider=tracer_provider, exclude_spans=FASTAPI_EXCLUDE_SPANS)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("FastAPI 계측 실패: %s", type(e).__name__)
        return False
