"""uvicorn 진입점: uvicorn console.main:app"""

from console.adapters import build_deps
from console.app import create_app

app = create_app(build_deps())
