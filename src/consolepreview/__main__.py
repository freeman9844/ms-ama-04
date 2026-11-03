"""`python -m consolepreview [--port 8765]`: 127.0.0.1에서만 연다."""

import argparse

import uvicorn

from console.app import create_app
from consolepreview.fixtures import build_deps


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m consolepreview", description="심사 콘솔 로컬 미리보기(합성 데이터)")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    print(f"미리보기: http://127.0.0.1:{args.port}/  (합성 데이터, 결정·재생 버튼은 기록만 하고 처리하지 않음)")
    uvicorn.run(create_app(build_deps()), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
