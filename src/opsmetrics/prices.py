"""모델 단가(USD/1M 토큰). 저장소·평가 이미지·console 이미지 모두 `<root>/evals/prices.json`에 있다."""

import json
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "evals" / "prices.json"


def load_prices(path: Path | None = None) -> dict[str, dict[str, Any]]:
    data = json.loads((path or DEFAULT_PATH).read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if isinstance(v, dict)}
