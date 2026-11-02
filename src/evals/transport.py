"""리포트 전달: 콘솔 로그 조각(EVALCHUNK)과 Blob 업로드.

Job 컨테이너의 파일은 실행이 끝나면 사라진다. 운영자 PC는 Blob 데이터 평면에 닿지 못할 수 있으므로(공용 접근 차단)
결과 파일을 gzip+base64 조각으로 stdout에 남기고, ops eval이 Log Analytics에서 모아 복원한다. Blob은 보관용 사본이다.
형식: EVALCHUNK <run_id> <file> <seq>/<total> <sha256(gzip)> <base64 조각>
"""

import base64
import gzip
import hashlib
from collections import defaultdict
from collections.abc import Callable, Iterable
from pathlib import Path

import httpx

TAG = "EVALCHUNK"
PIECE = 6000
STORAGE_SCOPE = "https://storage.azure.com/.default"
BLOB_API_VERSION = "2023-11-03"


class TransportError(RuntimeError):
    pass


def chunks(run_id: str, name: str, data: bytes, piece: int = PIECE) -> list[str]:
    packed = gzip.compress(data, mtime=0)
    digest = hashlib.sha256(packed).hexdigest()
    text = base64.b64encode(packed).decode()
    parts = [text[i:i + piece] for i in range(0, len(text), piece)] or [""]
    return [f"{TAG} {run_id} {name} {i}/{len(parts)} {digest} {p}" for i, p in enumerate(parts, 1)]


def emit(run_id: str, paths: Iterable[Path], write: Callable[[str], None] = print) -> int:
    n = 0
    for path in paths:
        for line in chunks(run_id, path.name, path.read_bytes()):
            write(line)
            n += 1
    return n


def reassemble(lines: Iterable[str], run_id: str) -> dict[str, bytes]:
    """로그 줄(순서·중복 무관)에서 파일을 복원한다. 조각이 빠졌거나 해시가 다르면 TransportError."""
    pieces: dict[str, dict[int, str]] = defaultdict(dict)
    meta: dict[str, tuple[int, str]] = {}
    for line in lines:
        at = line.find(TAG + " ")
        if at < 0:
            continue
        fields = line[at:].strip().split(" ")
        if len(fields) not in (5, 6) or fields[1] != run_id:
            continue
        _, _, name, pos, digest, *rest = fields
        seq, total = (int(x) for x in pos.split("/"))
        if meta.setdefault(name, (total, digest)) != (total, digest):
            raise TransportError(f"{name}: 조각 메타데이터가 서로 다르다")
        pieces[name][seq] = rest[0] if rest else ""
    files: dict[str, bytes] = {}
    for name, (total, digest) in meta.items():
        missing = [i for i in range(1, total + 1) if i not in pieces[name]]
        if missing:
            raise TransportError(f"{name}: 조각 {len(missing)}/{total}개 누락(예: {missing[:5]})")
        packed = base64.b64decode("".join(pieces[name][i] for i in range(1, total + 1)))
        if hashlib.sha256(packed).hexdigest() != digest:
            raise TransportError(f"{name}: sha256 불일치")
        files[name] = gzip.decompress(packed)
    return files


def upload(paths: Iterable[Path], *, account_url: str, container: str, run_id: str, token: Callable[[], str],
           client: httpx.Client | None = None) -> list[str]:
    """Blob REST(Put Blob)로 올린다. 관리 ID 토큰을 쓰고 SDK 의존성은 두지 않는다."""
    own = client is None
    http = client or httpx.Client(timeout=60)
    names = []
    try:
        for path in paths:
            name = f"{run_id}/{path.name}"
            r = http.put(
                f"{account_url.rstrip('/')}/{container}/{name}",
                content=path.read_bytes(),
                headers={"Authorization": f"Bearer {token()}", "x-ms-version": BLOB_API_VERSION,
                         "x-ms-blob-type": "BlockBlob", "Content-Type": _content_type(path.name)},
            )
            if r.status_code >= 300:
                raise TransportError(f"Blob 업로드 실패 {name}: HTTP {r.status_code}")
            names.append(name)
    finally:
        if own:
            http.close()
    return names


def _content_type(name: str) -> str:
    if name.endswith(".md"):
        return "text/markdown; charset=utf-8"
    if name.endswith(".json"):
        return "application/json"
    return "application/x-ndjson" if name.endswith(".jsonl") else "application/octet-stream"
