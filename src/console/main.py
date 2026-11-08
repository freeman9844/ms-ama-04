"""uvicorn 진입점: uvicorn console.main:app"""

import logging
import sys

from console.adapters import build_deps
from console.app import create_app

# 채팅 메타데이터(신청 ID·결과·지연·토큰)만 stdout으로 남긴다. 다른 로거 수준은 바꾸지 않는다.
_chat_log = logging.getLogger("console.chat")
_chat_log.setLevel(logging.INFO)
_chat_log.addHandler(logging.StreamHandler(sys.stdout))

app = create_app(build_deps())
