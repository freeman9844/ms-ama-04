# P6c 문서 허브·ADR·위협 모델·데모 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 평가자가 아키텍처·결정 이유·보안 통제·실측 증거를 바로 찾을 수 있는 한국어 문서 허브(README + `docs/`)를 만들고, 2분 데모 대본을 실제 Azure 리허설 1회로 검증하며, P6b 보류 Minor M5·M6·M7을 고친다.

**Architecture:** README는 약 150줄 입구로 줄이고, 기존 README 단계별 원문은 `docs/runbook/deploy-history.md`에 그대로 보존한다. 주제별 문서(architecture, agents, adr, security, cost-model, limitations, evidence, runbook, demo)는 as-built 코드와 기존 실측 보고서만 인용한다. `tests/docs/test_docs.py`가 링크·경로·문서 지도·ADR 절을 검사한다.

**Tech Stack:** Markdown + Mermaid, Python 3.12 / pytest, 기존 `ops` CLI(Azure CLI·azd), Azure(Korea Central).

**Spec:** `docs/superpowers/specs/2026-11-02-p6c-docs-demo-design.md`(상위: `docs/superpowers/specs/2026-10-31-agentic-cashflow-lending-design.md`)

## Global Constraints

- 문서 언어: 한국어. 파일·디렉터리 이름은 영문 소문자·하이픈(ADR은 `ADR-00N-<slug>.md`).
- as-built: 지금 저장소 코드·`infra/`가 사실의 기준. 상위 Spec과 다르면 코드를 따르고 차이를 ADR 또는 `docs/limitations.md`에 적는다.
- 수치는 기존 실측 산출물(`ops/reports/…`, `eval/reports/…`, `docs/runbook/deploy-history.md`의 실행 기록)에서만 인용하고, 인용한 곳 바로 옆에 출처 경로를 백틱으로 붙인다. 새 수치는 Task 11 리허설 소요 시간만.
- 모든 수치에 "합성 데이터 기준"을 문서별 첫머리에 한 번 명시.
- 문서 안 저장소 경로는 백틱 + 슬래시(`src/agents/underwriting.py`). 역슬래시 금지. 자리표시자는 `<…>`.
- 리소스 호스트명·구독 ID·IP·콘솔 URL을 문서에 쓰지 않는다(“`azd env get-values`의 `CONSOLE_URL`”처럼 출처만).
- README에서 옮기는 문구는 지우지 않는다(원문 보존: `docs/runbook/deploy-history.md`).
- 데모에 동의 **철회** 장면 없음. 1:45 장면은 동의 **만료** 삭제 증명.
- 테스트 명령: `$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python -m pytest -q -p no:cacheprovider` (기준선 878 passed, 105 skipped).
- 커밋: 메시지를 임시 파일로 써서 `git commit -F <파일>`. 트레일러 두 줄 필수:
  `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>` / `Copilot-Session: 05f1ca1b-6073-417d-bfb1-6b546f82cb58`
- 브랜치: `p6c-docs-demo`(master `e4d8099` 이후에서 분기).

## Review Focus

1. 처음 보는 Azure 서비스 호스트(새 접미사, 대문자 호스트)가 보고서에 리소스 이름을 흘리지 않아야 한다 → Task 1 `external-host`·대문자 케이스.
2. 예외 메시지가 비어 있거나 식별자로 시작하지 않아도(한국어 문장 등) 단계 기록에 원문이 남지 않아야 한다 → Task 2 `"error"` 대체 케이스.
3. 문서에 역슬래시 경로를 쓰면 경로 검사가 조용히 건너뛰므로 실패해야 한다 → Task 4 `test_repo_paths_use_forward_slashes`.
4. README 원문 이동 중 줄 누락 → Task 4 Step 3의 원문 동일성 검사.
5. 리허설 중 운영자 공인 IP 변동으로 콘솔·배포 403 → Task 9 fallback 점검 항목, Task 11 절차에 `operator-alt` 규칙.

---

### Task 0: 브랜치와 Spec 보정

**Files:**
- Modify: `docs/superpowers/specs/2026-11-02-p6c-docs-demo-design.md`

- [ ] **Step 1: 브랜치 생성**

```powershell
git switch -c p6c-docs-demo
```

- [ ] **Step 2: Spec 두 곳 보정(계획 확정 사항 반영)**

§4 표 M5 행의 "기존 보고서는 리허설 중 `ops metrics`로 재생성하고 숫자 불변을 확인"을 다음으로 바꾼다:
"기존 `metrics.json`은 `ops.kql.relabel`로 오프라인 변환 후 `report.md`를 다시 렌더링(Cost Management 재조회 없이 숫자 불변 보장)".

§2.9 끝에 한 줄 추가: "`deploy-history.md`는 README P2~P6b 절 전체(설명·배포 순서·실제 배포 기록·문제 대응·알려진 제약)를 원문 그대로 보존하는 기록본이고, setup·deploy·operate·troubleshooting은 현재 작업 기준으로 재구성한 사본이다."

§7 위험 표 "보고서 재생성 시 수치 변화" 행의 대응을 "오프라인 변환은 의존성 표 대상 열만 바꾼다. `summary`가 변환 전후 같음을 확인"으로 바꾼다.

- [ ] **Step 3: 커밋**

```powershell
git add docs/superpowers/specs/2026-11-02-p6c-docs-demo-design.md docs/superpowers/plans/2026-11-02-p6c-docs-demo.md
# 메시지: "docs(p6c): implementation plan and spec adjustments"
```

---

### Task 1: M5 — 의존성 대상 호스트명을 역할 라벨로

**Files:**
- Modify: `src/ops/kql.py`
- Modify: `tests/ops/test_costs.py`(kql 테스트가 이 파일에 있음)
- Regenerate: `ops/reports/load-20261102110233/metrics.json`, `ops/reports/load-20261102110233/report.md`

**Interfaces:**
- Produces: `ops.kql.target_label(target: str) -> str`, `ops.kql.relabel(deps: dict[str, list[dict]]) -> dict[str, list[dict]]`. `dependency_counts`의 반환값은 이제 `relabel`을 거친다(행 형태 `{"target", "role", "total", "failed"}` 유지).

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/ops/test_costs.py`의 import를 `from ops.kql import dependency_counts, relabel, target_label`로 바꾸고, 기존 `test_dependency_counts_single_line_query`의 기대값 `"target": "ca-policy"`를 `"target": "policy"`로 바꾼 뒤 아래를 추가:

```python
@pytest.mark.parametrize(("target", "label"), [
    ("stfnabc.queue.core.windows.net:None", "storage-queue"),
    ("stdatabc.blob.core.windows.net:None", "storage-blob"),
    ("cosmos-abc-koreacentral.documents.azure.com:None", "cosmos"),
    ("ais-abc.services.ai.azure.com", "foundry"),
    ("kv-abc.vault.azure.net", "key-vault"),
    ("ca-mockapi.beach-123.koreacentral.azurecontainerapps.io", "mockapi"),
    ("ca-policy", "policy"),
    ("169.254.255.2:8081", "link-local"),
    ("10.0.0.4", "ip-address"),
    ("api.example.com", "external-host"),
    ("API.Example.COM:443", "external-host"),
    ("ContainerProxy.read_item", "ContainerProxy.read_item"),
    ("invoke_agent data-assembly", "invoke_agent data-assembly"),
    ("unknown", "unknown"),
])
def test_target_label_hides_resource_hostnames(target, label):
    assert target_label(target) == label


def test_relabel_merges_rows_with_same_label_and_role():
    deps = {"a": [{"target": "x.queue.core.windows.net:None", "role": "orchestrator", "total": 1, "failed": 0},
                  {"target": "y.queue.core.windows.net:None", "role": "orchestrator", "total": 2, "failed": 1},
                  {"target": "y.queue.core.windows.net:None", "role": "policy", "total": 1, "failed": 0}],
            "b": []}
    assert relabel(deps) == {"a": [{"target": "storage-queue", "role": "orchestrator", "total": 3, "failed": 1},
                                   {"target": "storage-queue", "role": "policy", "total": 1, "failed": 0}],
                             "b": []}
```

- [ ] **Step 2: 실패 확인**

Run: `$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python -m pytest -q -p no:cacheprovider tests/ops/test_costs.py`
Expected: FAIL — `ImportError: cannot import name 'relabel'`

- [ ] **Step 3: 구현** — `src/ops/kql.py`에 `import re`를 추가하고 `_query` 위에 넣는다:

```python
_SUFFIX_LABELS = (
    (".queue.core.windows.net", "storage-queue"),
    (".blob.core.windows.net", "storage-blob"),
    (".table.core.windows.net", "storage-table"),
    (".documents.azure.com", "cosmos"),
    (".services.ai.azure.com", "foundry"),
    (".openai.azure.com", "foundry"),
    (".cognitiveservices.azure.com", "foundry"),
    (".vault.azure.net", "key-vault"),
    (".search.windows.net", "ai-search"),
    (".servicebus.windows.net", "service-bus"),
)
_IPV4 = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")
_HOSTNAME = re.compile(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+")


def target_label(target: str) -> str:
    """의존성 대상의 리소스 호스트명을 역할 라벨로 바꾼다(보고서에 리소스 이름을 남기지 않음). 호스트가 아니면 그대로."""
    host = target.split(":", 1)[0].lower()
    for suffix, label in _SUFFIX_LABELS:
        if host.endswith(suffix):
            return label
    if _IPV4.fullmatch(host):
        return "link-local" if host.startswith("169.254.") else "ip-address"
    if host.startswith("ca-") and (host.endswith(".azurecontainerapps.io") or "." not in host):
        return host.split(".", 1)[0][3:]
    if _HOSTNAME.fullmatch(host):
        return "external-host"
    return target


def relabel(deps: dict[str, list[dict]]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for app, rows in deps.items():
        merged: dict[tuple[str, str], dict] = {}
        for r in rows:
            key = (target_label(r["target"]), r["role"])
            m = merged.setdefault(key, {"target": key[0], "role": r["role"], "total": 0, "failed": 0})
            m["total"] += r["total"]
            m["failed"] += r["failed"]
        out[app] = list(merged.values())
    return out
```

`dependency_counts`의 마지막 `return out`을 `return relabel(out)`로 바꾼다.

- [ ] **Step 4: 통과 확인**

Run: `$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python -m pytest -q -p no:cacheprovider tests/ops`
Expected: PASS

- [ ] **Step 5: 기존 보고서 오프라인 변환** — 저장소 루트에서:

```powershell
@'
import json, pathlib, sys
sys.path[:0] = ["src"]
from ops.kql import relabel
from ops.opsreport import render
d = pathlib.Path("ops/reports/load-20261102110233")
data = json.loads((d / "metrics.json").read_text(encoding="utf-8"))
before = json.dumps(data["summary"], sort_keys=True)
for f in data.get("faults") or []:
    if f.get("dependencies"):
        f["dependencies"] = relabel(f["dependencies"])
assert json.dumps(data["summary"], sort_keys=True) == before
(d / "metrics.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
(d / "report.md").write_text(render(data), encoding="utf-8")
'@ | .\.venv\Scripts\python -
```

확인:
- `git diff --stat ops/reports`가 두 파일만 바꿨는지.
- 아래 grep이 아무것도 찾지 않는지(호스트명 제거):

```powershell
Select-String -Path ops/reports/load-20261102110233/* -Pattern 'windows\.net|documents\.azure|services\.ai\.azure|azurecontainerapps\.io'
```

- `git diff ops/reports/load-20261102110233/report.md`에서 SLO·비용·공정성 절(1~3장)에 변경이 없는지(바뀐 줄은 4장 의존성 표뿐).

- [ ] **Step 6: 커밋** — `fix(ops): label dependency targets by role instead of resource hostnames (M5)`

---

### Task 2: M6 — 단계 기록 detail에 예외 메시지 원문을 남기지 않음

**Files:**
- Modify: `src/orchestrator/journey/metering.py`
- Modify: `tests/orchestrator/test_metering.py`, `tests/orchestrator/test_journey_steps.py:116`

**Interfaces:**
- Produces: `journey.metering.safe_detail(detail: str) -> str`. 모든 단계 기록(`_record`)의 `detail`이 이 함수를 거친다. 값은 앞머리 식별자 토큰(예외 종류 `RuntimeError`, 코드 `agents_disabled`, `status=partial`)이거나 `"error"`.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/orchestrator/test_metering.py`의 import 줄에 `safe_detail`을 더하고(`from journey.metering import METER_KEY, MeteredRunner, failed_record, outcome_of, run_metered, safe_detail, step_records`), `test_failed_record_truncates_detail`를 지우고 아래로 대체:

```python
def test_failed_record_keeps_only_exception_type():
    rec = failed_record("underwrite", T0, T0 + timedelta(seconds=1), RuntimeError("계좌 110-123 잔액 5,000,000"))
    assert rec["outcome"] == "failed" and rec["detail"] == "RuntimeError"


@pytest.mark.parametrize(("raw", "safe"), [
    ("ValueError: 적요 '홍길동 이체'", "ValueError"),
    ("agents_disabled", "agents_disabled"),
    ("status=partial", "status=partial"),
    ("  KeyError: 'x'", "KeyError"),
    ("동의가 만료되었습니다", "error"),
    ("!! boom", "error"),
])
def test_safe_detail_keeps_leading_code_only(raw, safe):
    assert safe_detail(raw) == safe


def test_safe_detail_truncates_long_code():
    assert len(safe_detail("A" * 500)) == metering.DETAIL_MAX


def test_classify_error_detail_has_no_message():
    out = run_metered("underwrite", lambda d, p: {"classification": {"error": "ValueError: 적요 '홍길동'"}},
                      FakeDeps(Clock()), {})
    assert out[METER_KEY]["substeps"][0]["detail"] == "ValueError"
```

주의: `test_classify_error_detail_has_no_message`는 `_meter` 안 substep(아직 `_record` 전)을 본다. 그래서 `_classify_substep`도 `safe_detail`을 써야 통과한다(Step 3).

`tests/orchestrator/test_journey_steps.py:116`의 `policy["detail"] == "RuntimeError: down"`을 `policy["detail"] == "RuntimeError"`로 바꾼다.

- [ ] **Step 2: 실패 확인**

Run: `$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python -m pytest -q -p no:cacheprovider tests/orchestrator/test_metering.py tests/orchestrator/test_journey_steps.py`
Expected: FAIL — `ImportError: cannot import name 'safe_detail'`

- [ ] **Step 3: 구현** — `metering.py`에 `import re` 추가, `DETAIL_MAX` 아래에:

```python
_DETAIL_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_.=-]*")


def safe_detail(detail: str) -> str:
    """단계 기록에는 예외 종류·표준 코드만 남긴다(예외 메시지에 원천 값이 섞일 수 있음)."""
    m = _DETAIL_TOKEN.match(detail.strip())
    return (m.group(0) if m else "error")[:DETAIL_MAX]
```

바꿀 곳:
- `outcome_of`의 `return "failed", str(error)[:DETAIL_MAX] if error else None` → `return "failed", safe_detail(str(error)) if error else None`
- `_classify_substep`의 `outcome, detail = "failed", str(classification["error"])[:DETAIL_MAX]` → `outcome, detail = "failed", safe_detail(str(classification["error"]))`
- `_record`의 `rec["detail"] = detail[:DETAIL_MAX]` → `rec["detail"] = safe_detail(detail)`

`failed_record`는 그대로 둔다(`_record`가 정리함).

- [ ] **Step 4: 통과 확인**

Run: `$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python -m pytest -q -p no:cacheprovider tests/orchestrator`
Expected: PASS

- [ ] **Step 5: 커밋** — `fix(orchestrator): keep only exception type or code in step detail (M6)`

---

### Task 3: M7 — 단계·세그먼트 라벨을 한 곳에서 정의

**Files:**
- Create: `src/opsmetrics/labels.py`
- Modify: `src/console/ops.py:10-12`, `src/ops/opsreport.py:6-7,42,79`
- Test: `tests/console/test_ops_page.py`

**Interfaces:**
- Produces: `opsmetrics.labels.STEP_LABELS: dict[str, str]`, `opsmetrics.labels.AXIS_LABELS: dict[str, str]`. `console.ops`는 같은 이름으로 다시 내보낸다(`console/app.py`가 `from console.ops import AXIS_LABELS, STEP_LABELS`로 씀). `ops.opsreport`의 `STEP_NAMES`·`AXIS_NAMES`는 없어지고 `STEP_LABELS`·`AXIS_LABELS`를 import해 쓴다.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/console/test_ops_page.py` 끝에:

```python
def test_ops_page_and_report_use_timeline_step_terms():
    from console.app import STATUS_LABELS
    from console.ops import AXIS_LABELS, STEP_LABELS
    from ops.opsreport import AXIS_LABELS as REPORT_AXES, STEP_LABELS as REPORT_STEPS

    assert (STEP_LABELS, AXIS_LABELS) == (REPORT_STEPS, REPORT_AXES)
    assert STEP_LABELS["assemble"] == STATUS_LABELS["data_assembled"]  # "데이터 수집"
    assert STEP_LABELS["underwrite"] == STATUS_LABELS["underwritten"]  # "심사 지표 산출"
```

- [ ] **Step 2: 실패 확인**

Run: `$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python -m pytest -q -p no:cacheprovider tests/console/test_ops_page.py`
Expected: FAIL — `ImportError: cannot import name 'AXIS_LABELS' from 'ops.opsreport'`

- [ ] **Step 3: 구현**

`src/opsmetrics/labels.py`:

```python
"""운영 화면(console /ops)과 보고서(ops metrics)가 함께 쓰는 라벨. 단계 용어는 콘솔 타임라인 상태 라벨과 같다."""

STEP_LABELS = {"consent": "동의", "assemble": "데이터 수집", "classify": "거래 분류", "underwrite": "심사 지표 산출",
               "policy": "정책 판정", "assess": "신용 평가"}
AXIS_LABELS = {"industry": "업종", "region": "지역", "business_age_band": "업력"}
```

`src/console/ops.py`: `AXIS_LABELS = …`, `STEP_LABELS = …` 정의 두 개를 지우고 import 블록에 `from opsmetrics.labels import AXIS_LABELS, STEP_LABELS`를 넣는다(`console/app.py`가 `console.ops`에서 가져가므로 재수출 유지. 저장소에 린터 설정 없음).

`src/ops/opsreport.py`: `STEP_NAMES`·`AXIS_NAMES` 정의를 지우고 `from opsmetrics.labels import AXIS_LABELS, STEP_LABELS`; 본문의 `STEP_NAMES.get` → `STEP_LABELS.get`, `AXIS_NAMES.get` → `AXIS_LABELS.get`.

- [ ] **Step 4: 통과 확인 + 이미지 포함 확인**

Run: `$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python -m pytest -q -p no:cacheprovider tests/console tests/ops tests/opsmetrics`
Expected: PASS

`src/console/Dockerfile`·`src/evals/Dockerfile`은 `COPY src/opsmetrics …`로 디렉터리 전체를 복사하므로 변경 불필요(확인만).

- [ ] **Step 5: 기존 보고서 확인** — `ops/reports/load-20261102110233/report.md`의 단계 표는 이미 "데이터 수집·거래 분류·신용 평가"라 다시 렌더링하지 않는다.

- [ ] **Step 6: 커밋** — `fix(console,ops): share step and axis labels with timeline terms (M7)`

---

### Task 4: 문서 검사 테스트 + runbook 이동

**Files:**
- Create: `tests/docs/test_docs.py`
- Create: `docs/runbook/deploy-history.md`, `docs/runbook/setup.md`, `docs/runbook/deploy.md`, `docs/runbook/operate.md`, `docs/runbook/troubleshooting.md`

**Interfaces:**
- Produces: `tests/docs/test_docs.py`의 `ROOT`, `REQUIRED_DOCS: list[str]`(이후 Task가 항목을 덧붙임), `hub_docs() -> list[Path]`, `links(doc: Path) -> list[str]`(Task 10이 씀). README 원문 보존본 `docs/runbook/deploy-history.md`(이후 문서가 실행 기록 출처로 링크).

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/docs/test_docs.py`:

```python
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
]
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
```

- [ ] **Step 2: 실패 확인**

Run: `$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python -m pytest -q -p no:cacheprovider tests/docs`
Expected: `test_required_doc_exists` 5건 FAIL, README 대상 검사는 PASS(현재 README 경로 13개 모두 존재 확인됨).

- [ ] **Step 3: `deploy-history.md` — README P2~P6b 원문 보존**

README 25행(`## Azure 인프라(P2)`)부터 파일 끝(625행)까지를 **수정 없이** 복사하고 앞에 머리말만 붙인다. 원문 제목 단계는 그대로(`##`, `###`).

```markdown
# 단계별 구현·배포 기록(P2~P6b 원문)

> README에 단계별로 쌓였던 설명·배포 순서·실제 배포 기록·문제 대응·알려진 제약을 **원문 그대로** 옮긴 기록본입니다.
> 당시 기준의 명령·수치·용어이며 고치지 않습니다. 현재 기준 작업 안내는 [setup](setup.md)·[deploy](deploy.md)·[operate](operate.md)·[troubleshooting](troubleshooting.md)을 봅니다.

<README 25~625행 원문>
```

원문 동일성 확인(README를 고치기 전에 실행):

```powershell
@'
import pathlib
src = pathlib.Path("README.md").read_text(encoding="utf-8").splitlines()[24:]
dst = pathlib.Path("docs/runbook/deploy-history.md").read_text(encoding="utf-8").splitlines()
start = dst.index(src[0])
assert dst[start:start + len(src)] == src, "원문 불일치"
print("ok", len(src), "lines")
'@ | .\.venv\Scripts\python -
```

원문에 상대 링크가 없어 링크 검사 영향 없음. 원문 백틱 경로가 깨지면(현재 0건) 원문을 고치지 말고 보고한다.

- [ ] **Step 4: `setup.md`** — 처음 환경을 갖추는 사람용. 절 구성과 출처(README 원문 행):
  1. `## 로컬 개발` ← README 5~24행 원문(가상환경·테스트 명령)
  2. `## Azure 준비` ← README `### 준비`(31~39행)
  3. `## 최초 배포` ← README `### 최초 배포`(40~64행)
  4. `## 배포 뒤 확인` — `python -m ops smoke`와 콘솔 접속(`azd env get-values`의 콘솔 URL 키 이름은 `src/ops/azdenv.py`나 `infra/main.bicep` output에서 확인해 적는다)
  5. `## 데모 모드(Entra 로그인 끔)` ← README `### 데모 모드(Entra 로그인 끔)`(242~258행) 원문
  문서 끝에 "단계별 원문은 [deploy-history](deploy-history.md)" 한 줄을 둔다.

- [ ] **Step 5: `deploy.md`** — 코드 변경을 배포하는 사람용.
  1. `## 배포 창` — `ops window open --acr|close|status`의 의미(상위 Spec §2.2 배포 창, ADR-004 링크는 Task 6 뒤에 추가되므로 이 Task에서는 넣지 않음), 소요 시간 실측(P5b 3.7분, P6b 6.6분 — 출처 deploy-history)
  2. `## 구성요소별 배포` — `ops deploy all|orchestrator|console|policy|mockapi|eval` 표(대상 이름은 `src/ops/deploy.py`의 `TARGETS`에서 확인), 단계별 "배포 순서" 절들의 공통 순서(창 열기 → 배포 → 창 닫기 → smoke)와 "배포 전 실행 중 여정이 없는지 확인" 주의
  3. `## 운영자 IP가 바뀔 때` — README P6b 문제 대응 마지막 항목 원문 + 절차: Functions 접근 제한과 console ingress에 `operator-alt` /32 규칙(priority 110) 추가 → 작업 → 제거. `ops window close`는 Functions 규칙만 Bicep 상태로 되돌리므로 console 규칙은 직접 지운다. 명령은 `az functionapp config access-restriction add|remove`와 `az containerapp ingress access-restriction set|remove`(정확한 옵션은 `az … --help`로 확인해 적는다)

- [ ] **Step 6: `operate.md`** — 평상시 운영.
  1. `## 일시 정지·재개` ← README `### 운영 명령`(65~80행) 원문 + `ops pause`/`ops resume` 설명
  2. `## smoke` — 검사 항목(`src/ops/smoke_checks.py`의 check 함수 이름 목록)
  3. `## 평가(eval)` ← README P6a 절의 실행 명령·보고서 위치(476~531행에서 명령 블록과 산출물 설명만)
  4. `## 운영 지표·장애 주입` ← README P6b 명령 블록(573~579행) + 장애 토글 확인 `ops fault status`
  5. `## 비용 관리` — `ops pause` 후 남는 비용 항목(README `### 비용(월, 대략)` 81~91행 원문)

- [ ] **Step 7: `troubleshooting.md`** — README의 `### 문제 대응` 7개 절(138, 181, 282, 370, 455, 532, 605행) 항목을 **원문 그대로** 모으되, 주제별 절로 묶는다: `## 배포·네트워크`, `## 신청 여정·오케스트레이터`, `## 에이전트·Foundry`, `## 콘솔`, `## 동의·삭제·이의제기`, `## 평가·운영 지표`. 각 항목 끝에 출처 단계 표시 `(P5b)`.

항목 수 확인(빠짐 없음):

```powershell
@'
import re, pathlib
readme = pathlib.Path("README.md").read_text(encoding="utf-8")
src = sum(len(re.findall(r"^- ", sec, re.M)) for sec in re.findall(r"### 문제 대응[^\n]*\n(.*?)(?=\n## |\n### |\Z)", readme, re.S))
dst = len(re.findall(r"^- ", pathlib.Path("docs/runbook/troubleshooting.md").read_text(encoding="utf-8"), re.M))
print(src, dst); assert dst >= src
'@ | .\.venv\Scripts\python -
```

- [ ] **Step 8: 통과 확인**

Run: `$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python -m pytest -q -p no:cacheprovider tests/docs`
Expected: PASS

- [ ] **Step 9: 커밋** — `docs(p6c): runbook (setup, deploy, operate, troubleshooting, history) and doc checks`

---

### Task 5: 아키텍처·에이전트 문서

**Files:**
- Create: `docs/architecture.md`, `docs/agents.md`
- Modify: `tests/docs/test_docs.py`(`REQUIRED_DOCS`에 두 경로 추가)

**Interfaces:**
- Consumes: `docs/runbook/deploy-history.md`(실행 기록 출처 링크)
- Produces: `docs/architecture.md`의 절 앵커 `#장애-도메인`, `#운영-아키텍처`(Task 7·8이 링크)

- [ ] **Step 1: 실패 테스트** — `REQUIRED_DOCS`에 `"docs/architecture.md"`, `"docs/agents.md"` 추가 → `pytest tests/docs` FAIL 2건 확인.

- [ ] **Step 2: 사실 수집(쓰기 전에 코드에서 확인)** — 아래를 `infra/`·`src/`에서 직접 확인해 메모한다. 상위 Spec과 다르면 코드가 기준.
  - 구성요소·호스팅: `infra/resources.bicep`과 `infra/modules/*.bicep`(functions, container-app, aca-env, cosmos, storage, keyvault, search, servicebus, acr, monitoring, ops-insights, eval-job, smoke-job, foundry/*, network, pe, dns, aca-dns, identities, console-auth)
  - 서브넷 이름·주소: `infra/modules/network.bicep`
  - Private Endpoint 대상: `infra/modules/pe.bicep` 호출부(`infra/resources.bicep`)
  - 관리 ID와 역할: `infra/modules/identities.bicep`, 각 모듈의 role assignment
  - Service Bus의 현재 상태(pause 시 삭제·인라인 모드): `infra/modules/servicebus.bicep`, `src/ops/lifecycle.py`
  - 콘솔 인증 모드: `infra/modules/console-auth.bicep`, `src/console/auth.py`
  - 큐 이름·Durable 오케스트레이션 이름: `src/orchestrator/function_app.py`(또는 해당 진입 파일)와 `src/orchestrator/journey/`
  - 에이전트: `src/agents/*.py`의 `AgentSpec(` 정의 8개(`data-assembly`, `txn-classifier`, `underwriting-analyst`, `underwriting-reviewer`, `early-warning-investigator`, `collections-agent`, `consent-notice-agent`, `grievance-reply-agent`) — 이름·모델 설정 변수·도구 목록·템플릿 대체 조건, 분류는 `src/agents/classify.py`의 Decision-1 경로

- [ ] **Step 3: `docs/architecture.md` 작성** — 절 구성:
  1. `# 아키텍처` + 한 문단 요약 + "합성 데이터 기준" 명시
  2. `## 시스템 컨텍스트` — Mermaid `flowchart LR`: 차주(시뮬레이터)·심사역·운영자 → 콘솔, 오케스트레이터 ↔ Foundry·Policy·Mock API, Azure 경계
  3. `## 컨테이너` — Mermaid `flowchart`: Step 2 구성요소 전부 + 호스팅 표(구성요소 / 호스팅 / 책임 / 데이터 / 인증 / 코드 경로)
  4. `## 네트워크` — Mermaid: VNet·서브넷·PE·`cae-int`/`cae-web`·배포 창(운영자 IP, 평상시 public 차단) + 서브넷 표
  5. `## 신청 1건 흐름` — Mermaid `sequenceDiagram`: 접수 큐 → 동의 → 데이터 수집(에이전트) → 심사 지표 산출(분류 포함) → 정책 판정 → 신용 평가(분석가↔검토자) → 심사역 대기 → 결정 → 모니터링 등록. 단계 이름은 `src/opsmetrics/labels.py` 용어
  6. `## 사후 흐름` — Mermaid: 조기경보 재생 → 경보 → 추심·hardship 재조정, 동의 수명주기(갱신 안내 → 만료 → 삭제 → 삭제 증명), 이의제기
  7. `## 구성요소 계약` — 계약 위치 `src/shared/contracts` 등, 버전 붙은 Pydantic 모델 목록(실제 파일에서 클래스명)
  8. `## 장애 도메인` — 상위 Spec §2.4 표를 as-built로 갱신하고, 실측 증거 열: source-flaky 회복, policy-down fail-closed(`ops/reports/load-20261102110233/report.md` 4장), 서킷이 Flex 인스턴스별이라는 한계
  9. `## 확장 경로` — Flex 인스턴스, Cosmos RU, 모델 TPM(Global Standard 쿼터), 분류 호출 절감(규칙·중복 제거·캐시·Decision-1), PTU 전환, 다중 리전(문서만)
  10. `## 운영 아키텍처(미구축)` — 표: 항목 / 데모 / 운영 전환 시. App Gateway WAF v2 + Entra(월 약 $365, 상위 Spec §2.2), Entra 로그인 복원(ADR-007), 분산 서킷, 다중 리전 DR, Data Zone/PTU(ADR-002)
  ADR 링크는 Task 6에서 파일이 생긴 뒤 추가하므로 이 Task에서는 `ADR-00N` 텍스트만 쓰고 링크하지 않는다.

- [ ] **Step 4: `docs/agents.md` 작성** — 첫 표: 에이전트 / 단계 / 자율성(L2~L4, 상위 Spec §3.1) / 모델 / 도구 / 쓰기 권한 / 실패 시 동작 / 코드. 이어서 에이전트별 절(`## 데이터 수집`, `## 거래 분류`, `## 심사(분석가·검토자)`, `## 조기경보`, `## 추심`, `## 동의 안내`, `## 이의 회신`): 목적, 도구 allowlist(코드의 handler 이름), 가드레일(근거 검사 `src/agents/grounding.py`, 템플릿 대체, 가명화), 반성 패턴(검토자 최대 3회), 모델 선택 근거(`eval/reports/job-eval-9imbutm/report.md`에서 해당 수치 인용), 알려진 한계(`agent_assessment_mismatch`는 데이터 수집 절). 마지막 `## 공통 런타임` — `src/agents/runtime.py`(도구 호출 루프, 대화 삭제), 계량(`src/orchestrator/journey/metering.py`).

- [ ] **Step 5: 확인** — `pytest tests/docs` PASS. Mermaid는 VS Code 미리보기 또는 https://mermaid.live 에 붙여 렌더링되는지 눈으로 확인(저장소 밖으로 비밀을 보내지 않음: 그림에는 리소스 이름 대신 역할 이름만).

- [ ] **Step 6: 커밋** — `docs(p6c): architecture and agents`

---

### Task 6: ADR 9건 + 색인

**Files:**
- Create: `docs/adr/README.md`, `docs/adr/ADR-001-orchestration.md`, `docs/adr/ADR-002-model-deployment.md`, `docs/adr/ADR-003-deterministic-policy.md`, `docs/adr/ADR-004-private-network-deploy-window.md`, `docs/adr/ADR-005-classification-model.md`, `docs/adr/ADR-006-two-container-apps-environments.md`, `docs/adr/ADR-007-demo-auth-mode.md`, `docs/adr/ADR-008-vnet-jobs-for-eval-ops.md`, `docs/adr/ADR-009-deletion-vs-explainability.md`
- Modify: `tests/docs/test_docs.py`, `docs/architecture.md`, `docs/runbook/deploy.md`(ADR 링크 추가)

**Interfaces:**
- Produces: ADR 파일 경로(위 목록) — Task 7·8·10이 링크.

- [ ] **Step 1: 실패 테스트** — `REQUIRED_DOCS`에 위 10개 경로 추가하고 아래 테스트 추가:

```python
ADR_SECTIONS = ("## 상태", "## 맥락", "## 결정", "## 검토한 대안", "## 결과", "## 재검토 조건", "## 근거")


@pytest.mark.parametrize("adr", sorted((ROOT / "docs" / "adr").glob("ADR-*.md")), ids=_id)
def test_adr_has_required_sections(adr):
    text = adr.read_text(encoding="utf-8")
    assert [s for s in ADR_SECTIONS if s not in text] == []


def test_adr_index_links_every_adr():
    index = ROOT / "docs" / "adr" / "README.md"
    linked = {(index.parent / t).resolve() for t in links(index)}
    assert {p.resolve() for p in (ROOT / "docs" / "adr").glob("ADR-*.md")} <= linked
```

주의: `glob`이 빈 목록이면 parametrize가 테스트를 건너뛰므로 `test_required_doc_exists`가 존재를 보장한다.
Run → FAIL 확인.

- [ ] **Step 2: ADR 템플릿** — 모든 ADR은 이 형식:

```markdown
# ADR-00N: <제목>

## 상태
채택(<P 단계>, 2026-10-31~11-02) · as-built

## 맥락
<요구사항·제약, 상위 Spec 절 번호>

## 결정
<한 문단 + 구현 위치(백틱 경로)>

## 검토한 대안
| 대안 | 장점 | 기각 이유 |
|---|---|---|

## 결과
- 장점: …
- 대가: …

## 재검토 조건
- <운영 전환·규모·규제 조건>

## 근거
- <코드·테스트·실측 보고서·deploy-history 링크>
```

- [ ] **Step 3: ADR별 핵심 내용**(사실은 코드에서 재확인)
  - 001 오케스트레이션: 상위 Spec §2.1 표 원문 + as-built(Durable Functions Flex Consumption, Storage 백엔드, `waitForExternalEvent`·durable timer 사용 위치). 대안: Foundry Workflows, Agent Framework on Container Apps, Logic Apps.
  - 002 모델 배포: 상위 Spec §4.2 ADR-002 원문 + 실제 배포 SKU 파라미터 위치(`infra/modules/foundry/*.bicep`), 가명화 구현(`src/agents/` 가명화 함수 경로).
  - 003 결정적 정책 엔진 + fail-closed: `src/policy/`(YAML 규칙·규칙 버전), LLM은 추천만, Policy 실패 시 `awaiting_officer`로 거절만 허용(실측: policy-down `ops/reports/load-20261102110233/report.md`), 서킷(`src/orchestrator/journey/` 서킷 파일).
  - 004 완전 사설망 + 배포 창: PE 전부, public 기본 차단, `ops window`로 운영자 IP만 잠시 허용, ACR 빌드 창 동안 공개. 대안: 상시 공개+IP 제한, Bastion·점프박스(비용·운영), Self-hosted runner. 대가: 창 열기·닫기 소요(실측 3.7~6.6분, deploy-history), 운영자 IP 변동 문제.
  - 005 거래 분류 Decision-1 + mini 대체: 상위 Spec §3.2 계층형 파이프라인, 실측 Decision-1 호출 48회 단가 미확인(`ops/reports/load-20261102110233/report.md` 2장), P6a 분류 정확도(`eval/reports/job-eval-9imbutm/report.md`).
  - 006 Container Apps 환경 2개: internal ingress는 같은 환경 앱만 접근 가능 → Functions가 호출 못 함, 그래서 `cae-int`(내부 LB + Private DNS)·`cae-web`(콘솔만). Consumption 환경 기본 요금 없음.
  - 007 데모 인증 모드: 상위 Spec은 Entra + IP 허용. as-built는 `CONSOLE_AUTH_ENABLED=false` → `CONSOLE_AUTH_MODE=demo`, IP 허용 목록만, 데모 역할(`CONSOLE_DEMO_ROLE`). 이유: 데모 참여자 계정·테넌트 동의 부담. 대가: 사용자 식별·역할 분리 없음(위협 모델 잔여 위험). 재검토 조건: 실제 사용자·실데이터가 들어오면 즉시 Entra 복원(복원 방법: `CONSOLE_AUTH_ENABLED=true` + 앱 등록, `infra/modules/console-auth.bicep`).
  - 008 평가·운영을 VNet Container Apps Job으로: Cosmos·큐가 사설 엔드포인트 전용이라 로컬에서 직접 못 씀 → `eval-job.bicep`·job-ops, 결과는 로그 청크로 회수(`src/ops/jobs.py`). 대안: 로컬 실행 + 임시 공개, 결과 Blob 업로드(공개 필요).
  - 009 원천 삭제 + 판단 기록 보존: 상위 Spec §4.1 보존 표, `delete_consent_data` 순서·검증(deploy-history P5b), 삭제 증명 컨테이너, 보존 항목 `application_record`.

- [ ] **Step 4: 색인** — `docs/adr/README.md`: 표(번호·제목·상태·한 줄 결정·관련 문서) 9행, 각 ADR 링크.

- [ ] **Step 5: 역링크** — `docs/architecture.md`의 `ADR-00N` 텍스트를 상대 링크(`adr/ADR-00N-….md`)로, `docs/runbook/deploy.md` 배포 창 절에 ADR-004 링크.

- [ ] **Step 6: 확인** — `pytest tests/docs` PASS.

- [ ] **Step 7: 커밋** — `docs(p6c): ADR-001..009 and index`

---

### Task 7: 위협 모델

**Files:**
- Create: `docs/security/threat-model.md`
- Modify: `tests/docs/test_docs.py`(`REQUIRED_DOCS`에 추가)

**Interfaces:**
- Consumes: ADR-002·003·004·007·009 링크, `docs/architecture.md#네트워크`

- [ ] **Step 1: 실패 테스트** — `REQUIRED_DOCS`에 `"docs/security/threat-model.md"` 추가 → FAIL 확인.

- [ ] **Step 2: 증거 수집** — 통제마다 코드·테스트 경로를 grep으로 찾아 표에 적는다. 최소 확인 대상:
  - 동의 범위·만료 검증: `src/mockapis/`(토큰 검증), `tests/mockapis/test_app.py`(`scope`·`expired`·`revoked` 케이스), 데이터 수집 도구 `scope_not_consented`(`src/agents/data_assembly.py`)
  - 프롬프트 주입: 평가 세트 `demo-injection-005`(`src/console/app.py`의 `DEMO_APPLICANTS`), 평가 보고서 주입 결과(`eval/reports/job-eval-9imbutm/report.md`), 이의 회신 에이전트에 차주 글을 주지 않음(`src/agents/notices.py`)
  - 그라운딩: `src/agents/grounding.py`, 검토자 반성 루프, 템플릿 대체(notices)
  - PII: 가명화(`src/agents/` 가명화 함수), 보고서 PII 검사(P6a·P6b에서 쓴 검사 위치), Foundry 대화 삭제(`src/agents/runtime.py`), M6 `safe_detail`, M5 `target_label`
  - 권한: 콘솔 역할(`src/console/auth.py`), 결정은 큐 명령으로만(`src/console/app.py` decision 경로), 관리 ID·키 없음(`infra/modules/identities.bicep`)
  - 네트워크: PE·public 차단(`infra/modules/pe.bicep`, 각 모듈 `publicNetworkAccess`), IP 제한(`infra/modules/functions.bicep`, `infra/modules/container-app.bicep`)
  - 자율성: 도구 allowlist(`src/agents/runtime.py` `unknown_tool`), 추심은 스크립트 ID만(`src/agents/collections.py`), 정책 엔진 최종 결정(ADR-003)
  - 감사: 결정 기록·`decision_attempts`(`src/orchestrator/journey/orchestration.py`), 삭제 증명
  - CSP·SRI: `src/console/app.py`의 `CSP`·`HTMX_SRI`
  - Prompt Shields: 실제로 켜져 있는지 `src/agents/`·`infra/modules/foundry/`에서 확인. 없으면 "미구현"으로 표시(상위 Spec 대비 차이).

- [ ] **Step 3: 작성** — 절 구성:
  1. `# 위협 모델` — 범위(데모 as-built), 방법(STRIDE + AI 위험), 상태 범례: `검증`(테스트 또는 실측 있음) / `구현`(코드만) / `미구현` / `데모 완화`(ADR-007로 약화)
  2. `## 신뢰 경계` — Mermaid: 인터넷(운영자 IP) → 콘솔(cae-web) → VNet 내부(Functions, cae-int, PE 리소스) → Foundry(Global Standard 추론, ADR-002)
  3. `## 자산` — 표: 원천 거래 데이터, 판단 기록, 동의·삭제 증명, 비밀(없음·관리 ID), 모델 프롬프트·트레이스
  4. `## STRIDE` — 표 열: ID(S1…) / 범주 / 위협 / 대상 / 통제 / 증거 / 상태. 범주마다 2행 이상
  5. `## AI 위험` — 표 같은 열: 프롬프트 주입(직접·간접), 그라운딩 실패, PII 유출(모델 전송·트레이스·보고서), 과도한 자율성, 모델 처리 위치(ADR-002)
  6. `## 데이터 분류와 보존` — 상위 Spec §4.1 보존 표 + as-built 저장소(Cosmos 컨테이너 이름은 deploy-history P5b 표)
  7. `## 개인정보 원칙` — 범위 동의, 만료·철회 삭제와 증명(ADR-009), 설명 요구권(반사실·이의제기), dark pattern 체크리스트(상위 Spec §4.3), "법률 검토를 대신하지 않음"
  8. `## 잔여 위험` — 데모 인증(ADR-007), Foundry 트레이스에 프롬프트 남음(deploy-history P6b 제약), Durable 실행 기록, 서킷 인스턴스별 등 → `docs/limitations.md`는 Task 8에서 생기므로 링크는 Task 8에서 추가

- [ ] **Step 4: 확인** — `pytest tests/docs` PASS. 표에서 `검증` 상태 행은 모두 테스트 또는 보고서 경로가 있는지 눈으로 확인.

- [ ] **Step 5: 커밋** — `docs(p6c): STRIDE and AI threat model with evidence`

---

### Task 8: 비용 모델·한계·증거 색인

**Files:**
- Create: `docs/cost-model.md`, `docs/limitations.md`, `docs/evidence.md`
- Modify: `tests/docs/test_docs.py`, `docs/security/threat-model.md`(잔여 위험 → limitations 링크)

- [ ] **Step 1: 실패 테스트** — `REQUIRED_DOCS`에 세 경로 추가 → FAIL 확인.

- [ ] **Step 2: `cost-model.md`** — 모든 숫자는 `ops/reports/load-20261102110233/report.md` 2장과 deploy-history에서 인용:
  1. `## 계산식` — 건당 총비용 = 건당 AI 비용 + 월 고정비 ÷ 월 물량. 코드 `src/opsmetrics/cost.py`, 단가 `src/evals/prices.json`
  2. `## 건당 AI 비용` — 모델별 표(gpt-5.4 70회 $0.885, gpt-5.4-mini 92회 $0.131, Decision-1 48회 단가 미확인), 합 $1.016 / 24건 = $0.0423, 캐시 할인 미반영 상한
  3. `## 월 고정비` — $304.73(Cost Management, 데이터가 있는 하루 × 30, Foundry Models 제외), 한계(하루치·pause 영향). 초기 추정 표(deploy-history P2 `비용(월, 대략)`)와 나란히
  4. `## 물량별 단가` — 100/1,000/10,000건 → $3.09/$0.35/$0.07, 수작업 $3,500/건(Mills & McCarthy, HBS Working Paper 15-004) 대비, USD 6,000 대출 대비 비율(report.md 표 그대로)
  5. `## 평가 비용` — P6a 평가 1회 $1.93(`eval/reports/job-eval-9imbutm/report.md`)
  6. `## 절감 레버` — pause(남는 비용 항목), 배포 창 운영, 모델 등급(mini·Decision-1), 분류 캐시, PTU 손익분기(정성 설명, 새 수치 만들지 않음), App Gateway 미구축 비용(월 약 $365, 상위 Spec §2.2)

- [ ] **Step 3: `limitations.md`**:
  1. `## 요약` — 5줄(README 한계 요약의 원본)
  2. 주제별 절: `## 인증·접근`, `## 데이터·외부 연동`(모의 API·합성 데이터), `## 에이전트·모델`(`agent_assessment_mismatch` 24/24, Decision-1 단가, ADR-002 처리 위치), `## 동의·삭제·이의제기`, `## 운영·관측`(서킷 인스턴스별, 트레이스 연결 KQL 조인, 고정비 하루치), `## 측정 표본`(24건, 공정성 지역 37.5%p 경보는 표본 잡음 가능성 — 조사 대상), `## 배포 상태`(리허설 전까지 P6b I1 Policy 수정 미배포 — Task 11에서 갱신)
  각 항목은 deploy-history의 "알려진 제약" 원문 문장을 쓰고 끝에 `(P5b)` 같은 출처 표시. 항목 수 확인:

```powershell
@'
import re, pathlib
h = pathlib.Path("docs/runbook/deploy-history.md").read_text(encoding="utf-8")
src = sum(len(re.findall(r"^- ", s, re.M)) for s in re.findall(r"### 알려진 제약[^\n]*\n(.*?)(?=\n## |\n### |\Z)", h, re.S))
dst = len(re.findall(r"^- ", pathlib.Path("docs/limitations.md").read_text(encoding="utf-8"), re.M))
print(src, dst); assert dst >= src
'@ | .\.venv\Scripts\python -
```

  3. `## 상위 Spec 위험(§7)` — 위험별 현재 상태
  4. `## 운영 전환 체크리스트` — 체크박스 목록: Entra 복원(ADR-007), App Gateway WAF, Data Zone/PTU(ADR-002), 분산 서킷, 다중 리전, 실데이터 법률 검토, 공정성 표본 확대, `agent_assessment_mismatch` 원인 분석, Prompt Shields(Task 7 결과에 따라)

- [ ] **Step 4: `evidence.md`** — 루브릭(`C:/Users/jungwoonlee/orca/projects/ama-agents-01/ama-rubric-01.md`의 A1~A7, B3, B6, B7 기준 이름을 그대로)별 표: 기준 / 주장 / 증거(문서·코드·테스트·보고서 링크). 끝에 `## 실측 보고서 색인` 표: 보고서 / 무엇 / 날짜 / 링크 — P6a 평가(`eval/reports/job-eval-9imbutm/report.md`), P6b load(`ops/reports/load-20261102110233/report.md`), fault 2건(`ops/reports/fault-20261102111447/`, `ops/reports/fault-20261102111750/`), 단계별 smoke·배포 기록(deploy-history), 리허설(`docs/demo/rehearsal.md` — Task 9에서 생기므로 이 Task에서는 텍스트만, Task 9 Step 5에서 링크로 바꿈).

- [ ] **Step 5: 역링크** — threat-model 잔여 위험 절과 architecture 운영 아키텍처 절에 limitations 링크.

- [ ] **Step 6: 확인** — `pytest tests/docs` PASS, 숫자 대조: cost-model의 $0.0423·$304.73·$3.09·$0.35·$0.07이 report.md와 같은지 `Select-String`으로 확인.

- [ ] **Step 7: 커밋** — `docs(p6c): cost model, limitations and evidence index`

---

### Task 9: 데모 대본·대비책·리허설 절차

**Files:**
- Create: `docs/demo/demo-script.md`, `docs/demo/fallback.md`, `docs/demo/rehearsal.md`
- Modify: `tests/docs/test_docs.py`, `docs/evidence.md`(리허설 링크)

- [ ] **Step 1: 실패 테스트** — `REQUIRED_DOCS`에 세 경로 추가 → FAIL 확인.

- [ ] **Step 2: 화면 동선 확인** — `src/console/app.py` 경로와 템플릿(`src/console/templates/`)에서 버튼·링크 이름을 그대로 적는다: `/applications/new`(신청 폼), `/applications/<ID>`(타임라인·근거·결정 폼·동의 영역 `지금 만료(데모)`), `/loans/<ID>`(재생 버튼·경보·재조정), `/consents/<동의 ID>/proof`. 데모 신청자 ID는 `DEMO_APPLICANTS`(`demo-cafe-001` 등)에서 고른다.

- [ ] **Step 3: `demo-script.md`** — 상위 Spec §6.1 표를 기반으로 장면별 절:

| 시간 | 장면 | 화면 | 방식 |
|---|---|---|---|
| 0:00 | 문제 제시 | 콘솔 첫 화면(또는 README 그림) | 말 |
| 0:15 | 신청과 실시간 진행 | `/applications/new` → `/applications/<ID>` 타임라인 | 라이브 1건 |
| 0:50 | 심사 근거와 승인 | 신청 상세 근거 링크·반사실 → 승인 | 라이브 건(지연 시 사전 건 A) |
| 1:20 | 조기경보 | `/loans/<ID>` → 재생 → 경보·재조정 | 사전 대출 B |
| 1:45 | 동의 만료 | 라이브 건 `지금 만료(데모)` → `/consents/<ID>/proof` | 라이브 건(지연 시 사전 동의 C) |

장면마다: 말할 문장(1~2문장, 상위 Spec §6.1 "보여줄 결과"를 문장으로), 클릭 순서, 예상 소요(초), 전환 지점("N초 안에 상태가 `심사역 대기`가 아니면 사전 건 A 탭으로"). 동의 철회 장면은 넣지 않는다. 사전 대출 B는 심사 동의를 만료하지 않은 대출이어야 함(만료 뒤 재생 불가 — deploy-history P5b 제약) 명시.

- [ ] **Step 4: `fallback.md`**
  1. `## 사전 준비 목록` — 표: 이름(A 승인 신청, B 재생 대출, C 삭제 증명 완료 동의) / 만드는 방법 / ID(리허설 때 채움) / 확인 방법
  2. `## 시작 전 점검` — 체크박스: `ops resume` 완료, `ops smoke` 통과, 콘솔 접속(운영자 공인 IP가 console ingress 허용 목록과 같은지; 다르면 `operator-alt` 규칙 — [deploy](../runbook/deploy.md) 링크), `ops fault status`가 둘 다 off, 브라우저 탭 순서(1 콘솔 목록, 2 사전 건 A, 3 사전 대출 B, 4 사전 동의 C 증명)
  3. `## 라이브 지연·실패 시` — 장면별 전환 대사
  4. `## 전체 장애 시` — 녹화본 전환 대사와 절차(녹화는 리허설 때 발표자가 직접 촬영, 저장소에 넣지 않음)

- [ ] **Step 5: `rehearsal.md`(절차 + 빈 기록표)** — `## 절차`(Task 11 단계 그대로), `## 기록`(표: 장면 / 계획 / 실측 시작 / 실측 끝 / 소요 / ID / 비고 — 값은 비워 두고 "리허설 전" 표시), `## 발견한 문제와 대본 수정`(비움). `docs/evidence.md`의 리허설 텍스트를 링크로 바꾼다.

- [ ] **Step 6: 확인·커밋** — `pytest tests/docs` PASS → `docs(p6c): demo script, fallback and rehearsal procedure`

---

### Task 10: README 재작성(문서 허브)

**Files:**
- Modify: `README.md`(전체 교체)
- Modify: `tests/docs/test_docs.py`

- [ ] **Step 1: 실패 테스트 추가**

```python
def test_readme_links_every_hub_doc():
    readme = ROOT / "README.md"
    linked = {(ROOT / t).resolve() for t in links(readme)}
    expected = {p.resolve() for p in hub_docs() if p.parent != ROOT and p.parent.name != "adr"}
    expected.add((ROOT / "docs" / "adr" / "README.md").resolve())
    assert sorted(_id(p) for p in expected - linked) == []


def test_readme_is_an_entry_page():
    assert len((ROOT / "README.md").read_text(encoding="utf-8").splitlines()) <= 200
```

Run → FAIL(링크 없음, 625줄) 확인.

- [ ] **Step 2: Task 4 원문 동일성 검사를 한 번 더 실행**(README를 바꾸기 직전, deploy-history가 원문과 같은지).

- [ ] **Step 3: README 작성** — 절 순서(Spec §2.1):
  1. `# Agentic Cash-Flow Lending` + 한 문단(Case Study 18, 소상공인 운전자금 대출 2주 → 몇 분, 에이전트는 추천·정책 엔진과 심사역이 결정)
  2. `## 아키텍처 한눈에` — Mermaid 1개(architecture.md 컨테이너 그림의 축약) + "자세히: [아키텍처](docs/architecture.md)"
  3. `## 실측 결과(합성 데이터 기준)` — 표: 지표 / 값 / 출처 링크. 행: SLO1 p95 1.2분(목표 ≤ 10분), SLO2 100%(72/72), 건당 AI 비용 $0.0423, 월 고정비 $304.73, 건당 총비용(월 1,000건) $0.35, P6a 평가 핵심 지표 1~2개(`eval/reports/job-eval-9imbutm/report.md`에서 인용), 장애 2종 회복·fail-closed
  4. `## 빠른 시작` — 로컬 테스트 명령 2~3줄 + Azure 배포 요약 3~5줄(→ [setup](docs/runbook/setup.md))
  5. `## 문서 지도` — 표: 문서 / 내용, `hub_docs()`의 README 외 모든 문서(ADR은 색인 하나로)
  6. `## 알려진 한계(요약)` — limitations 요약 5줄 + 링크
  7. `## 단계별 이력` — P1~P6c 한 줄씩, 각 Spec·Plan 링크(`docs/superpowers/…`)와 원문 기록 [deploy-history](docs/runbook/deploy-history.md)

- [ ] **Step 4: 확인** — `pytest tests/docs` PASS, 전체 테스트 실행(기준선 878 passed + 새 테스트, 105 skipped).

- [ ] **Step 5: 커밋** — `docs(p6c): README as documentation hub`

---

### Task 11: 실제 Azure 리허설(컨트롤러가 직접 수행)

이 Task는 Azure 자격 증명과 운영자 브라우저가 필요하므로 서브에이전트에 맡기지 않고 컨트롤러가 사용자와 함께 진행한다. 시각은 KST로 기록.

**Files:**
- Modify: `docs/demo/rehearsal.md`, `docs/demo/fallback.md`(ID), `docs/limitations.md`(배포 상태), `docs/evidence.md`, `README.md`(필요 시 리허설 한 줄)

- [ ] **Step 1: 창 열기·배포**

```powershell
python -m ops window status                 # 운영자 IP 확인
python -m ops window open --acr             # 시작·끝 시각 기록
python -m ops deploy all                    # M5·M6·M7 + P6b I1 Policy 반영
python -m ops smoke
```

IP가 바뀌어 403이면 `docs/runbook/deploy.md`의 `operator-alt` 절차.

- [ ] **Step 2: I1 반영 확인** — Log Analytics(`AppDependencies`, 최근 30분, `AppRoleName == "policy"`)에서 이름이 ` http send`·` http receive`로 끝나는 스팬이 0건인지. 쿼리는 Python `subprocess`로 `az monitor log-analytics query -w <LOG_ANALYTICS_CUSTOMER_ID>` 실행(PowerShell 인용 문제 회피).

- [ ] **Step 3: 사전 건 준비** — 콘솔에서: A 신청 1건 제출 → 심사역 대기 → 승인. B 신청 1건 승인 → 대출 상세에서 재생 시작(완료까지 대기, 경보 확인). C 신청 1건 승인 → `지금 만료(데모)` → 삭제 증명 complete. ID를 fallback.md 표에 기록.

- [ ] **Step 4: 대본 리허설** — demo-script.md대로 처음부터 끝까지 1회. 장면별 시작·끝 시각, 라이브 신청 ID·동의 ID 기록. 2분 초과 장면은 원인 기록.

- [ ] **Step 5: 정리**

```powershell
# operator-alt 규칙을 추가했다면 console ingress에서 제거
python -m ops window close
python -m ops fault status                  # 둘 다 off
python -m ops pause
```

- [ ] **Step 6: 기록 반영** — rehearsal.md 기록표·문제·대본 수정 사항, demo-script.md 수정(실측 반영), limitations.md 배포 상태 항목 갱신(I1 반영 완료), evidence.md 리허설 행, `pytest tests/docs` PASS.

- [ ] **Step 7: 커밋** — `docs(p6c): live demo rehearsal record`

---

### Task 12: 최종 확인

- [ ] 전체 테스트 PASS(`$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python -m pytest -q -p no:cacheprovider`).
- [ ] 문서 전체에서 리소스 호스트명·IP·구독 ID가 없는지:

```powershell
Select-String -Path README.md, docs/*.md, docs/*/*.md -Pattern 'windows\.net|documents\.azure|services\.ai\.azure|azurecontainerapps\.io|\b\d{1,3}(\.\d{1,3}){3}\b|<구독 ID 앞 8자리>' | Where-Object { $_.Path -notmatch 'superpowers|deploy-history' }
```

  (deploy-history는 원문 보존이므로 제외하되, 결과가 있으면 사용자에게 보고.)
- [ ] 브랜치 전체 리뷰 → finishing-a-development-branch.
