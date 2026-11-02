"""문서 허브 검사: 필수 문서, 상대 링크, 저장소 경로, 경로 표기."""
import re
from pathlib import Path
from urllib.parse import unquote

import pytest

ROOT = Path(__file__).resolve().parents[2]
REQUIRED_DOCS = [
    "docs/runbook/setup.md",
    "docs/runbook/deploy.md",
    "docs/runbook/operate.md",
    "docs/runbook/troubleshooting.md",
    "docs/runbook/deploy-history.md",
    "docs/architecture.md",
    "docs/agents.md",
    "docs/adr/README.md",
    "docs/adr/ADR-001-orchestration.md",
    "docs/adr/ADR-002-model-deployment.md",
    "docs/adr/ADR-003-deterministic-policy.md",
    "docs/adr/ADR-004-private-network-deploy-window.md",
    "docs/adr/ADR-005-classification-model.md",
    "docs/adr/ADR-006-two-container-apps-environments.md",
    "docs/adr/ADR-007-demo-auth-mode.md",
    "docs/adr/ADR-008-vnet-jobs-for-eval-ops.md",
    "docs/adr/ADR-009-deletion-vs-explainability.md",
]
ADR_SECTIONS = ("## 상태", "## 맥락", "## 결정", "## 검토한 대안", "## 결과", "## 재검토 조건", "## 근거")
FENCE = re.compile(r"```.*?```", re.S)
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
REPO_PATH = re.compile(r"`((?:src|infra|ops/reports|eval/reports|tests|docs)/[^`\s]+)`")
BACKSLASH_PATH = re.compile(r"`(?:src|infra|ops|eval|tests|docs)\\")
PLACEHOLDER = re.compile(r"[<>*…{}]")


def hub_docs() -> list[Path]:
    docs = (ROOT / "docs").rglob("*.md")
    return [ROOT / "README.md", *sorted(p for p in docs if "superpowers" not in p.relative_to(ROOT / "docs").parts)]


def _text(doc: Path) -> str:
    return FENCE.sub("", doc.read_text(encoding="utf-8"))


def _id(doc: Path) -> str:
    return doc.relative_to(ROOT).as_posix()


def links(doc: Path) -> list[str]:
    out = []
    for target in LINK.findall(_text(doc)):
        if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith("#"):
            continue
        out.append(unquote(target.split("#", 1)[0]))
    return out


@pytest.mark.parametrize("rel", REQUIRED_DOCS)
def test_required_doc_exists(rel):
    assert (ROOT / rel).is_file()


@pytest.mark.parametrize("doc", hub_docs(), ids=_id)
def test_relative_links_resolve(doc):
    broken = [t for t in links(doc) if not (doc.parent / t).resolve().exists()]
    assert broken == []


@pytest.mark.parametrize("doc", hub_docs(), ids=_id)
def test_repo_paths_exist(doc):
    missing = []
    for raw in REPO_PATH.findall(_text(doc)):
        path = re.sub(r":\d+(?:-\d+)?$", "", raw.split("#", 1)[0]).rstrip("/.,")
        if not PLACEHOLDER.search(path) and not (ROOT / path).exists():
            missing.append(raw)
    assert missing == []


@pytest.mark.parametrize("doc", hub_docs(), ids=_id)
def test_repo_paths_use_forward_slashes(doc):
    assert BACKSLASH_PATH.findall(_text(doc)) == []


@pytest.mark.parametrize("adr", sorted((ROOT / "docs" / "adr").glob("ADR-*.md")), ids=_id)
def test_adr_has_required_sections(adr):
    text = adr.read_text(encoding="utf-8")
    assert [s for s in ADR_SECTIONS if s not in text] == []


def test_adr_index_links_every_adr():
    index = ROOT / "docs" / "adr" / "README.md"
    linked = {(index.parent / t).resolve() for t in links(index)}
    assert {p.resolve() for p in (ROOT / "docs" / "adr").glob("ADR-*.md")} <= linked
