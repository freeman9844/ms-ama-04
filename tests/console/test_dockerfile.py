import fnmatch
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "src" / "console" / "Dockerfile"


def _dockerignored(rel: str) -> bool:
    """.dockerignore 규칙(마지막 일치 우선, `!` 예외, 상위 디렉터리 일치 포함)으로 제외 여부를 판단한다."""
    parts = rel.split("/")
    candidates = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
    ignored = False
    for raw in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines():
        pattern = raw.strip()
        if not pattern or pattern.startswith("#"):
            continue
        negate = pattern.startswith("!")
        pattern = pattern.lstrip("!").strip("/")
        if pattern.startswith("**/"):
            tail = pattern.removeprefix("**/")
            hit = any(fnmatch.fnmatchcase(c.rsplit("/", 1)[-1], tail) for c in candidates)
        else:
            hit = any(fnmatch.fnmatchcase(c, pattern) for c in candidates)
        if hit:
            ignored = not negate
    return ignored


def _copy(src: Path, dest: Path) -> None:
    """빌드 컨텍스트(.dockerignore 적용)에 남는 파일만 복사한다."""
    files = [src] if src.is_file() else [f for f in src.rglob("*") if f.is_file()]
    for f in files:
        if _dockerignored(f.relative_to(ROOT).as_posix()):
            continue
        target = dest / f.name if src.is_file() else dest / f.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(f, target)


def test_dockerfile_copies_everything_the_console_imports(tmp_path):
    """Dockerfile의 COPY 목록과 .dockerignore만으로 /app을 재현해 콘솔 모듈을 import한다(Azure SDK는 지연 import)."""
    app = tmp_path / "app"
    for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        m = re.match(r"COPY (src/\S.*) (/app/\S*)$", line)
        if not m:
            continue
        *sources, target = m.group(1).split() + [m.group(2)]
        dest = app / target.removeprefix("/app/").rstrip("/")
        for src in sources:
            _copy(ROOT / src, dest)
    # 편집 설치(.pth)가 저장소 src를 sys.path에 넣으므로, /app 재현본 말고 다른 소스 경로는 지운다.
    code = (
        "import sys, pathlib; "
        f"sys.path[:] = [p for p in sys.path if p == {str(app)!r} or not (pathlib.Path(p) / 'shared').is_dir()]; "
        "import console.app, console.adapters, console.views, console.ops; "
        "assert not [m for m in sys.modules if m.startswith('azure')], 'azure imported eagerly'"
    )
    env = {**os.environ, "PYTHONPATH": str(app)}
    result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (app / "console" / "templates" / "detail.html").exists()
    assert (app / "console" / "static" / "console.css").exists()
    assert not (app / "journey" / "activities.py").exists()
    assert (app / "evals" / "prices.json").exists()
    assert (app / "opsmetrics" / "summary.py").exists()
    assert not (app / "evals" / "runner.py").exists()


def test_dockerignore_keeps_other_images_lean():
    # 콘솔에 필요한 journey 파일만 예외로 두고, 오케스트레이터·ops·테스트는 계속 빌드 컨텍스트에서 뺀다.
    assert _dockerignored("src/orchestrator/function_app.py")
    assert _dockerignored("src/orchestrator/journey/activities.py")
    assert _dockerignored("src/ops/cli.py")
    assert _dockerignored("tests/console/test_app.py")
    assert _dockerignored("src/shared/__pycache__/x.pyc")
    assert not _dockerignored("src/orchestrator/journey/models.py")
    assert not _dockerignored("src/agents/features.py")
