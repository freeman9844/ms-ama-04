"""uvicorn 진입점: `uvicorn policy.main:app`"""

import os
from pathlib import Path

from shared.otel import configure_monitor

configure_monitor("policy.otel")

from policy.app import create_app  # noqa: E402 - OTel 설정 뒤에 FastAPI를 만든다
from policy.rules import DEFAULT_RULES_PATH, load_rules  # noqa: E402

app = create_app(
    load_rules(Path(os.environ.get("POLICY_RULES_PATH", DEFAULT_RULES_PATH))),
    fault=os.environ.get("FAULT_MODE", "none"),
)
