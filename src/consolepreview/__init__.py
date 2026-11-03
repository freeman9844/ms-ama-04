"""로컬 미리보기(P7): 합성 고정 데이터로 심사 콘솔을 띄운다. Azure 자원·Entra 로그인 없이 화면만 확인한다."""

import sys
from pathlib import Path

_ORCHESTRATOR = str(Path(__file__).resolve().parents[1] / "orchestrator")
if _ORCHESTRATOR not in sys.path:
    sys.path.insert(0, _ORCHESTRATOR)
