"""uvicorn 진입점: `uvicorn mockapis.main:app`"""

import os
from datetime import datetime

from mockapis.app import create_app
from mockapis.demo import KST, build_demo_cases, build_postloan_flows, load_secret, resolve_as_of

_as_of = resolve_as_of()
_cases = build_demo_cases(_as_of)
app = create_app(
    _cases,
    load_secret(),
    lambda: datetime.now(KST),
    flows=build_postloan_flows(_cases, _as_of),
    fault=os.environ.get("FAULT_MODE", "none"),
)
