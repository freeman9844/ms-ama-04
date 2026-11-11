"""uvicorn 진입점: `uvicorn mockapis.main:app`"""

import os
from datetime import datetime

from mockapis.app import create_app
from mockapis.demo import KST, FlowStore, build_demo_cases, build_load_cases, load_secret, resolve_as_of

_as_of = resolve_as_of()
_demo = build_demo_cases(_as_of)
_cases = {**_demo, **build_load_cases(_as_of)}
app = create_app(
    _cases,
    load_secret(),
    lambda: datetime.now(KST),
    flows=FlowStore(_cases, _as_of),  # 부하 신청도 승인되면 사후 재생할 수 있다(요청 시 생성)
    fault=os.environ.get("FAULT_MODE", "none"),
)
