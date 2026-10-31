"""uvicorn 진입점: `uvicorn policy.main:app`"""

import os
from pathlib import Path

from policy.app import create_app
from policy.rules import DEFAULT_RULES_PATH, load_rules

app = create_app(load_rules(Path(os.environ.get("POLICY_RULES_PATH", DEFAULT_RULES_PATH))))
