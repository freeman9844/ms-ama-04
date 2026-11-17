"""블로그 글의 앞머리·영한 짝·episodes.yml·교훈 제목·Liquid 노출을 검사한다.

사용: python -m blog.tools.check_site blog
문제가 있으면 한 줄에 하나씩 출력하고 종료 코드 1을 돌려준다.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

LANGS = ("en", "ko")
REQUIRED_KEYS = ("layout", "title", "date", "lang", "ref", "days", "permalink", "summary")
EPISODE_KEYS = ("ref", "days", "title_en", "title_ko")
TAKEAWAY = {"en": "## Takeaway", "ko": "## 오늘의 교훈"}

_FRONT = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_REF = re.compile(r"day-\d{2}")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \+0900")
_FILE = re.compile(r"(\d{4}-\d{2}-\d{2})-.+\.(?:md|markdown)")
_RAW = re.compile(r"\{%-?\s*raw\s*-?%\}.*?\{%-?\s*endraw\s*-?%\}", re.DOTALL)
_LIQUID = re.compile(r"\{\{|\{%")
_FENCE = re.compile(r"^\s{0,3}(```|~~~)")


def _split_front_matter(text: str) -> tuple[dict | None, str]:
    m = _FRONT.match(text)
    if not m:
        return None, text
    data = yaml.safe_load(m.group(1))
    return (data if isinstance(data, dict) else None), text[m.end():]


def _count_heading(body: str, heading: str) -> int:
    count, fence = 0, None
    for line in body.splitlines():
        fm = _FENCE.match(line)
        if fm:
            if fence is None:
                fence = fm.group(1)
            elif fm.group(1) == fence:
                fence = None
            continue
        if fence is None and line.rstrip() == heading:
            count += 1
    return count


def _check_post(path: Path, rel: str, problems: list[str]) -> dict | None:
    text = path.read_text(encoding="utf-8-sig")
    try:
        front, body = _split_front_matter(text)
    except yaml.YAMLError as exc:
        problems.append(f"{rel}: front matter is not valid YAML ({exc.__class__.__name__})")
        return None
    if front is None:
        problems.append(f"{rel}: missing front matter")
        return None

    missing = [k for k in REQUIRED_KEYS if front.get(k) in (None, "")]
    for key in missing:
        problems.append(f"{rel}: missing front matter key '{key}'")

    lang, ref = front.get("lang"), front.get("ref")
    if front.get("layout") not in (None, "post"):
        problems.append(f"{rel}: layout must be 'post'")
    if lang is not None and lang not in LANGS:
        problems.append(f"{rel}: lang must be one of {', '.join(LANGS)} (got {lang!r})")
        lang = None
    if lang is not None and path.parent.name != lang:
        problems.append(f"{rel}: lang '{lang}' does not match folder '_posts/{path.parent.name}'")
    if ref is not None and not _REF.fullmatch(str(ref)):
        problems.append(f"{rel}: ref must look like day-NN (got {ref!r})")
    if front.get("date") is not None and not _DATE.fullmatch(str(front["date"])):
        problems.append(f"{rel}: date must be 'YYYY-MM-DD HH:MM:SS +0900'")
    file_m = _FILE.fullmatch(path.name)
    if not file_m:
        problems.append(f"{rel}: file name must be YYYY-MM-DD-<slug>.md")
    elif front.get("date") is not None and not str(front["date"]).startswith(file_m.group(1)):
        problems.append(f"{rel}: date does not match file name date {file_m.group(1)}")
    if lang and ref is not None:
        expected = f"/{lang}/{ref}/"
        if front.get("permalink") != expected:
            problems.append(f"{rel}: permalink must be '{expected}' (got {front.get('permalink')!r})")

    if lang:
        n = _count_heading(body, TAKEAWAY[lang])
        if n != 1:
            problems.append(f"{rel}: heading '{TAKEAWAY[lang]}' must appear exactly once (found {n})")

    stripped = _RAW.sub("", body)
    for line in stripped.splitlines():
        if _LIQUID.search(line):
            problems.append(f"{rel}: liquid tag outside {{% raw %}} block: {line.strip()[:60]}")
            break

    if not lang or ref is None:
        return None
    return {"lang": lang, "ref": str(ref), "name": path.name, "rel": rel}


def _load_episodes(root: Path, problems: list[str]) -> list[str]:
    path = root / "_data" / "episodes.yml"
    if not path.is_file():
        problems.append("_data/episodes.yml: file not found")
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except yaml.YAMLError as exc:
        problems.append(f"_data/episodes.yml: not valid YAML ({exc.__class__.__name__})")
        return []
    if data is None:
        data = []
    if not isinstance(data, list):
        problems.append("_data/episodes.yml: must be a list")
        return []
    refs: list[str] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            problems.append(f"_data/episodes.yml: entry {i} must be a mapping")
            continue
        for key in EPISODE_KEYS:
            if item.get(key) in (None, ""):
                problems.append(f"_data/episodes.yml: entry {i} missing '{key}'")
        if item.get("ref") is not None:
            ref = str(item["ref"])
            if ref in refs:
                problems.append(f"_data/episodes.yml: duplicate ref {ref}")
            refs.append(ref)
    return refs


def check_site(root: Path) -> list[str]:
    root = Path(root)
    problems: list[str] = []
    episode_refs = _load_episodes(root, problems)

    posts: dict[str, dict[str, dict]] = {}
    posts_dir = root / "_posts"
    files = sorted(p for p in posts_dir.rglob("*") if p.is_file() and p.suffix in (".md", ".markdown")) \
        if posts_dir.is_dir() else []
    for path in files:
        rel = path.relative_to(root).as_posix()
        info = _check_post(path, rel, problems)
        if info is None:
            continue
        by_lang = posts.setdefault(info["ref"], {})
        if info["lang"] in by_lang:
            problems.append(f"{rel}: duplicate {info['lang']} post for {info['ref']} "
                            f"(also {by_lang[info['lang']]['rel']})")
            continue
        by_lang[info["lang"]] = info

    for ref in sorted(set(episode_refs) | set(posts)):
        by_lang = posts.get(ref, {})
        for lang in LANGS:
            if lang not in by_lang:
                problems.append(f"{ref}: missing {lang} post")
        if ref not in episode_refs:
            problems.append(f"{ref}: post ref not listed in _data/episodes.yml")
        names = {info["name"] for info in by_lang.values()}
        if len(names) > 1:
            problems.append(f"{ref}: en and ko posts must share the same file name (date and slug)")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="블로그 글 구조 검사")
    parser.add_argument("root", nargs="?", default="blog", help="검사할 폴더 (기본: blog)")
    args = parser.parse_args(argv)

    root = Path(args.root)
    if not root.is_dir():
        print(f"검사할 폴더가 없습니다: {root}", file=sys.stderr)
        return 2
    problems = check_site(root)
    for problem in problems:
        print(problem)
    if problems:
        print(f"구조 문제 {len(problems)}건", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
