"""Azure Monitor OpenTelemetry 초기화. SDK는 지연 import하고, 실패해도 서비스는 계속 돈다."""

import logging
import os
from collections.abc import Mapping

log = logging.getLogger(__name__)


def configure_monitor(logger_name: str, env: Mapping[str, str] = os.environ, instrument_httpx: bool = False) -> bool:
    conn = env.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not conn:
        return False
    try:
        from azure.identity import ManagedIdentityCredential
        from azure.monitor.opentelemetry import configure_azure_monitor

        configure_azure_monitor(
            connection_string=conn,
            credential=ManagedIdentityCredential(client_id=env.get("AZURE_CLIENT_ID")),
            logger_name=logger_name,
            enable_live_metrics=False,
        )
        if instrument_httpx:
            from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

            HTTPXClientInstrumentor().instrument()
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("OTel 초기화 실패: %s", type(e).__name__)
        return False
