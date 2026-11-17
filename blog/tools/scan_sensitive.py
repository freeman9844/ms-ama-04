"""공개 블로그 파일에서 민감 정보(ID·IP·경로·비밀값·개인 목록)를 찾는 검사기.

사용: python -m blog.tools.scan_sensitive blog [--denylist <path>]
걸린 항목이 있으면 `path:line rule excerpt`를 출력하고 종료 코드 1을 돌려준다.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

SCAN_SUFFIXES = {".md", ".html", ".yml"}
DENYLIST_ENV = "BLOG_DENYLIST"

_GUID = re.compile(
    r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}(?![0-9A-Fa-f])"
)
# 앞뒤로 숫자나 `.숫자`가 이어지면(버전 문자열 등) IP로 보지 않는다.
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?!\d|\.\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_USER_PATH = re.compile(r"(?i:[A-Z]:\\{1,2}Users\\{1,2})|/Users/|/home/")
_SECRET = re.compile(
    r"AccountKey="
    r"|SharedAccessSignature"
    r"|sig="
    r"|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{22,}"
    r"|eyJ[A-Za-z0-9_-]{20,}\."
    r"|Bearer [A-Za-z0-9._-]{20,}"
)

_DOC_IP_PREFIXES = ("192.0.2.", "198.51.100.", "203.0.113.")
_ALLOWED_IPS = {"0.0.0.0", "127.0.0.1"}


def _ipv4_ok(value: str) -> bool:
    """True면 걸지 않는다(형식 밖이거나 허용 대역)."""
    if any(int(octet) > 255 for octet in value.split(".")):
        return True
    return value in _ALLOWED_IPS or value.startswith(_DOC_IP_PREFIXES)


def _email_ok(value: str) -> bool:
    v = value.lower()
    return (
        v == "noreply@github.com"
        or v.endswith("@users.noreply.github.com")
        or v.endswith("@example.com")
    )


_RULES: list[tuple[str, re.Pattern, object]] = [
    ("guid", _GUID, None),
    ("ipv4", _IPV4, _ipv4_ok),
    ("email", _EMAIL, _email_ok),
    ("user_path", _USER_PATH, None),
    ("secret", _SECRET, None),
]


@dataclass
class Finding:
    path: str
    line: int
    rule: str
    excerpt: str


def _redact(value: str) -> str:
    # 앞 4자까지만 보이되, 짧은 값은 절반 이하만 보여 값 전체가 출력되지 않게 한다.
    return value[: min(4, len(value) // 2)] + "…"


def scan_text(text: str, path: str = "", deny: Iterable[re.Pattern] = ()) -> list[Finding]:
    deny = list(deny)
    findings: list[Finding] = []
    for lineno, raw in enumerate(text.split("\n"), start=1):
        line = raw.rstrip("\r")
        hits: list[tuple[int, str, str]] = []
        for rule, pattern, allowed in _RULES:
            for m in pattern.finditer(line):
                if allowed is not None and allowed(m.group(0)):
                    continue
                hits.append((m.start(), rule, m.group(0)))
        for pattern in deny:
            for m in pattern.finditer(line):
                if m.group(0):
                    hits.append((m.start(), "deny", m.group(0)))
        hits.sort(key=lambda h: h[0])
        findings.extend(Finding(path, lineno, rule, _redact(value)) for _, rule, value in hits)
    return findings


def load_denylist(path: Path | None) -> list[re.Pattern]:
    """한 줄에 하나. `#`은 주석, `re:` 접두사는 정규식, 나머지는 대소문자 무시 리터럴."""
    if path is None:
        return []
    patterns: list[re.Pattern] = []
    for raw in Path(path).read_text(encoding="utf-8-sig").splitlines():
        entry = raw.strip()
        if not entry or entry.startswith("#"):
            continue
        if entry.startswith("re:"):
            patterns.append(re.compile(entry[3:]))
        else:
            patterns.append(re.compile(re.escape(entry), re.IGNORECASE))
    return patterns


def scan_tree(root: Path, deny: Iterable[re.Pattern]) -> list[Finding]:
    root = Path(root)
    deny = list(deny)
    findings: list[Finding] = []
    for file in sorted(root.rglob("*")):
        if not file.is_file() or file.suffix.lower() not in SCAN_SUFFIXES:
            continue
        rel = file.relative_to(root)
        if rel.parts and rel.parts[0] == "tools":
            continue
        text = file.read_text(encoding="utf-8", errors="replace")
        findings.extend(scan_text(text, path=file.as_posix(), deny=deny))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="공개 블로그 파일의 민감 정보 검사")
    parser.add_argument("root", nargs="?", default="blog", help="검사할 폴더 (기본: blog)")
    parser.add_argument(
        "--denylist",
        default=None,
        help=f"개인 금지어 목록 파일 (없으면 환경변수 {DENYLIST_ENV})",
    )
    args = parser.parse_args(argv)

    root = Path(args.root)
    if not root.is_dir():
        print(f"검사할 폴더가 없습니다: {root}", file=sys.stderr)
        return 2

    deny_path = args.denylist or os.environ.get(DENYLIST_ENV) or None
    try:
        deny = load_denylist(Path(deny_path) if deny_path else None)
    except OSError as exc:
        print(f"금지어 목록을 읽을 수 없습니다: {exc.filename}", file=sys.stderr)
        return 2
    except re.error as exc:
        print(f"금지어 목록의 정규식 오류: {exc}", file=sys.stderr)
        return 2

    findings = scan_tree(root, deny)
    for f in findings:
        print(f"{f.path}:{f.line} {f.rule} {f.excerpt}")
    if findings:
        print(f"민감 정보 의심 {len(findings)}건", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
