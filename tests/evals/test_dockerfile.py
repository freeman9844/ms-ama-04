import re
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[2] / "src" / "evals" / "Dockerfile"


def test_default_report_dir_is_writable_by_runtime_user():
    # Job은 비루트(USER)로 WORKDIR 아래 eval/reports/<실행 이름>에 리포트를 쓴다. WORKDIR(/app)은 root 소유라
    # 미리 만들고 런타임 사용자에게 넘겨야 한다(첫 실제 실행이 모든 suite를 끝낸 뒤 PermissionError로 실패).
    lines = [l.strip() for l in DOCKERFILE.read_text(encoding="utf-8").splitlines()]
    user_at = next(i for i, l in enumerate(lines) if l.startswith("USER "))
    uid = lines[user_at].split()[1]
    workdir = next(l.split()[1] for l in lines if l.startswith("WORKDIR "))
    reports = f"{workdir.rstrip('/')}/eval/reports"
    prepared = [l for l in lines[:user_at] if l.startswith("RUN ")
                and re.search(rf"mkdir -p {re.escape(reports)}\b", l)
                and re.search(rf"chown -R {uid}\S* {re.escape(workdir.rstrip('/'))}/eval\b", l)]
    assert prepared, f"{reports}를 만들고 {uid}에게 넘기는 RUN이 USER 앞에 없다"
