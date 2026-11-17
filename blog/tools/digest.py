"""날짜별 자료 묶음 도구.

Copilot CLI 세션 로그(events.jsonl), 세션 저장소 체크포인트, git 커밋,
배포 이력을 모아 KST 날짜마다 Markdown 한 개(`out/YYYY-MM-DD.md`)로 묶는다.
결과물은 글감 정리용 비공개 자료다.

사용법: python -m blog.tools.digest --sources sources.json --out <dir>

sources.json 형식::

    {
      "sources": [{"label": "A1", "session_id": "...", "repo": "..."}],
      "deploy_history": "docs/runbook/deploy-history.md",
      "session_db": "~/.copilot/session-store.db",
      "session_state": "~/.copilot/session-state"
    }
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sqlite3
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
ASSISTANT_LIMIT = 600
DEPLOY_HEADING = re.compile(r"^## (\d{4}-\d{2}-\d{2})\s+(.*)$")


@dataclass
class Item:
    day: str
    time: str
    source: str
    kind: str
    text: str


def _to_kst(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.strip())
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST)


def kst_date(ts: str) -> str:
    return _to_kst(ts).strftime("%Y-%m-%d")


def _item(ts: str, source: str, kind: str, text: str) -> Item:
    dt = _to_kst(ts)
    return Item(dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M"), source, kind, text)


def _as_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            try:
                parsed = ast.literal_eval(value)
            except (ValueError, SyntaxError):
                return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _compact(value) -> str:
    if isinstance(value, dict):
        return "; ".join(f"{k}={v}" for k, v in value.items())
    return "" if value is None else str(value)


def _is_false(value) -> bool:
    return value is False or (isinstance(value, str) and value.strip().lower() == "false")


def read_events(path: Path, label: str) -> list[Item]:
    """events.jsonl에서 글감이 될 사건만 뽑는다.

    assistant.message는 한 상호작용(같은 interactionId, 없으면 turnId)에서
    내용이 있는 마지막 메시지 하나만 남긴다. 새 user.message가 오거나
    키가 바뀌거나 파일이 끝나면 그때까지의 마지막 답을 내보낸다.
    """
    source = f"session:{label}"
    items: list[Item] = []
    pending: Item | None = None
    pending_key = None

    def flush():
        nonlocal pending, pending_key
        if pending is not None:
            items.append(pending)
        pending, pending_key = None, None

    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            ts = event.get("timestamp")
            if not ts:
                continue
            etype = event.get("type")
            data = _as_dict(event.get("data"))

            if etype == "user.message":
                flush()
                content = (data.get("content") or "").strip()
                if content:
                    items.append(_item(ts, source, "user", content))
            elif etype == "human_response.recorded":
                response = _as_dict(data.get("response"))
                message = _compact(response.get("message")).strip()
                answer = _compact(response.get("content")).strip()
                if message:
                    items.append(_item(ts, source, "ask", message))
                if answer:
                    items.append(_item(ts, source, "answer", answer))
            elif etype == "tool.execution_complete":
                if _is_false(data.get("success")):
                    error = data.get("error")
                    message = _as_dict(error).get("message") or _compact(error)
                    items.append(_item(ts, source, "tool_fail", str(message).strip()))
            elif etype == "assistant.message":
                content = (data.get("content") or "").strip()
                if not content:
                    continue
                key = data.get("interactionId") or data.get("turnId")
                if pending is not None and key != pending_key:
                    flush()
                pending = _item(ts, source, "assistant", content[:ASSISTANT_LIMIT])
                pending_key = key
    flush()
    return items


def read_checkpoints(db: Path, session_id: str, label: str) -> list[Item]:
    con = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT title, overview, created_at FROM checkpoints"
            " WHERE session_id = ? ORDER BY checkpoint_number",
            (session_id,),
        ).fetchall()
    finally:
        con.close()
    source = f"session:{label}"
    return [
        _item(created_at, source, "checkpoint", "\n".join(p for p in (title, overview) if p))
        for title, overview, created_at in rows
        if created_at
    ]


def read_git(repo: Path, label: str) -> list[Item]:
    out = subprocess.run(
        ["git", "-C", str(repo), "log", "--format=%aI%x09%s"],
        check=True,
        capture_output=True,
        encoding="utf-8",
    ).stdout
    source = f"git:{label}"
    items = []
    for line in out.splitlines():
        ts, sep, subject = line.partition("\t")
        if sep:
            items.append(_item(ts, source, "commit", subject))
    return items


def read_deploy_history(md: Path) -> list[Item]:
    items: list[Item] = []
    current: tuple[str, str] | None = None
    body: list[str] = []

    def flush():
        if current is not None:
            day, title = current
            text = "\n".join([title.strip(), *[b for b in body if b.strip()]]).strip()
            items.append(Item(day, "", "deploy-history", "deploy", text))

    for line in Path(md).read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            flush()
            body = []
            match = DEPLOY_HEADING.match(line)
            current = (match.group(1), match.group(2)) if match else None
        elif current is not None:
            body.append(line.rstrip())
    flush()
    return items


def _render_day(day: str, items: list[Item]) -> str:
    counts = Counter(i.kind for i in items)
    lines = [
        f"# {day}",
        "",
        f"사용자 메시지 {counts['user']} · 실패 {counts['tool_fail']} · 커밋 {counts['commit']}",
    ]
    ordered = sorted(items, key=lambda i: i.time)
    sources = list(dict.fromkeys(i.source for i in ordered))
    for source in sources:
        lines += ["", f"### {source}", ""]
        for item in ordered:
            if item.source != source:
                continue
            first, *rest = item.text.splitlines() or [""]
            lines.append(f"- {item.time or '--:--'} [{item.kind}] {first}")
            lines += [f"  {r}" if r else "" for r in rest]
    return "\n".join(lines) + "\n"


def write_digests(items: list[Item], out: Path) -> list[Path]:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    by_day: dict[str, list[Item]] = {}
    for item in items:
        by_day.setdefault(item.day, []).append(item)
    paths = []
    for day in sorted(by_day):
        path = out / f"{day}.md"
        path.write_text(_render_day(day, by_day[day]), encoding="utf-8")
        paths.append(path)
    return paths


def _path(value: str | None) -> Path | None:
    return Path(value).expanduser() if value else None


def _warn(message: str) -> None:
    print(f"건너뜀: {message}", file=sys.stderr)


def collect(config: dict) -> list[Item]:
    items: list[Item] = []
    state = _path(config.get("session_state"))
    db = _path(config.get("session_db"))
    for src in config.get("sources", []):
        label = src["label"]
        session_id = src.get("session_id")
        if session_id and state:
            events = state / session_id / "events.jsonl"
            if events.is_file():
                items += read_events(events, label)
            else:
                _warn(f"{events} 없음")
        if session_id and db:
            if db.is_file():
                items += read_checkpoints(db, session_id, label)
            else:
                _warn(f"{db} 없음")
        repo = _path(src.get("repo"))
        if repo:
            items += read_git(repo, label)
    deploy = _path(config.get("deploy_history"))
    if deploy:
        if deploy.is_file():
            items += read_deploy_history(deploy)
        else:
            _warn(f"{deploy} 없음")
    return items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="세션 로그·git·배포 이력을 날짜별 Markdown으로 묶는다.")
    parser.add_argument("--sources", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    config = json.loads(args.sources.read_text(encoding="utf-8"))
    paths = write_digests(collect(config), args.out)
    print(f"{len(paths)}개 날짜 파일을 {args.out}에 썼다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
