"""uvicorn 진입점: `uvicorn policy.main:app`"""

import os
from pathlib import Path

from shared import otel

# FastAPI 자동 계측은 send/receive 스팬을 끌 수 없어 끄고, 앱을 만든 뒤 직접 계측한다.
OTEL_ON = otel.configure_monitor("policy.otel", fastapi_auto=False)

from policy.app import create_app  # noqa: E402 - OTel 설정 뒤에 FastAPI를 만든다
from policy.rules import DEFAULT_RULES_PATH, load_rules  # noqa: E402

app = create_app(
    load_rules(Path(os.environ.get("POLICY_RULES_PATH", DEFAULT_RULES_PATH))),
    fault=os.environ.get("FAULT_MODE", "none"),
)

if OTEL_ON:
    otel.instrument_fastapi(app)
