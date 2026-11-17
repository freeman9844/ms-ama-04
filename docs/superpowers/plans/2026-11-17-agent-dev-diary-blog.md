# 에이전트 개발 데일리 블로그 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 10/26~11/17 작업 기록(세션 대화·요약·git·배포 이력)을 날짜별 자료로 묶고, 이를 바탕으로 "에이전트와 처음 개발해 본 이야기" 약 15~18편을 영문·한글 짝으로 써서 `blog/`(Jekyll)에서 GitHub Pages로 공개한다.

**Architecture:** `blog/tools/`의 Python 도구 셋이 일을 나눈다. `digest.py`는 원자료를 KST 날짜별 자료 묶음(커밋 밖 세션 폴더)으로, `scan_sensitive.py`는 글의 민감 정보를, `check_site.py`는 글 구조(언어 짝·앞머리·Liquid 충돌)를 검사한다. 편 목록은 `blog/_data/episodes.yml` 하나가 정하고 목록 페이지와 검사기가 함께 읽는다. 배포는 `.github/workflows/blog-pages.yml`이 검사 → `actions/jekyll-build-pages` → `deploy-pages` 순서로 한다.

**Tech Stack:** Python 3.12(표준 라이브러리 + PyYAML, pytest), Jekyll(GitHub Pages 기본 빌드, 테마 minima), GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-17-agent-dev-diary-blog-design.md`

## Global Constraints

- 블로그의 모든 것은 `blog/` 안에 둔다. 예외는 `.github/workflows/blog-pages.yml` 하나. `blog/` 파일은 저장소의 다른 경로를 참조하지 않는다. 코드·커밋 링크는 `https://github.com/freeman9844/ms-ama-04/...` 절대 URL로만.
- 사이트 주소 `https://freeman9844.github.io/ms-ama-04/`, `_config.yml`의 `baseurl: /ms-ama-04`, `timezone: Asia/Seoul`, `future: true`, `theme: minima`.
- 원자료와 날짜별 자료 묶음은 커밋하지 않는다. 출력 위치는 세션 폴더 `%USERPROFILE%\.copilot\session-state\05f1ca1b-6073-417d-bfb1-6b546f82cb58\files\blog-digest\`. 실제 값 차단 목록(`denylist.txt`)도 같은 폴더에만 둔다.
- 익명: 실명·사용자명·회사 이름·회사 계정 이름·과정 이름을 쓰지 않는다. 과정은 "an internal architect program's capstone" / "사내 아키텍트 과정의 캡스톤 과제"로만. Azure·Microsoft 제품명(예: Microsoft Foundry)은 쓸 수 있다.
- 다른 시도는 "Attempt 1~7" / "시도 1~7"로만 부르고 폴더 이름·저장소 링크를 쓰지 않는다. 질의응답 세션(26b23252…)은 저자 발언과 에이전트 답만 쓴다.
- 이미 공개된 식별자(리소스 해시 접미사, Job ID 등)도 글에서는 `func-<hash>`, `<job-id>`, `<subscription-id>`, `<my-ip>` 같은 자리표시로 쓴다.
- 채점 기준(rubric) 원문을 인용하지 않는다. 과제 내용은 요약만.
- 영문 먼저, 그다음 한글판. 한글판 문체: 짧은 인사로 시작, 상황 먼저, "먼저/다음으로/그럼/이제" 연결, 설명 뒤 관찰 결과, 회고로 마무리, 영어 용어는 그대로. 영문판은 같은 흐름의 영어 블로그 말투.
- 한 편 구성 순서: 하려던 일 → 에이전트에게 맡긴 방식(실제 요청 짧게 인용) → 어긋난 지점 → 넘어간 방법 → 교훈 한 줄. 교훈 제목은 영문 `## Takeaway`, 한글 `## 오늘의 교훈`. 영문 약 800~1,500단어.
- 없던 일을 지어내지 않는다. 기록으로 확인되지 않는 동기·이유는 "I think / 아마" 로 쓰거나 저자 확인 목록에 올린다.
- 저자 승인 관문: ① 목차(Task 4) ② 첫 편 문체(Task 5) ③ 공개 전 최종 검토·푸시·Pages 활성화(Task 7). 승인 없이 푸시하지 않는다.
- 블로그 도구 테스트는 `blog/tools/tests/`에 두고 `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider blog/tools/tests`로 돌린다(루트 `testpaths=["tests"]`는 그대로). 기존 `tests/` 전체도 계속 통과해야 한다.
- 커밋 메시지는 한국어, 세션 트레일러 두 줄(`Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`, `Copilot-Session: 05f1ca1b-6073-417d-bfb1-6b546f82cb58`).

## Review Focus

1. **글에 붙여 넣은 저자 메시지 속 경로·IP·GUID** — 코드 블록이나 인라인 코드 안이어도 검사기가 잡아야 한다(Task 2 테스트 `test_flags_inside_code_fence_and_inline_code`).
2. **KST 자정 경계** — UTC 15:00 이후 이벤트는 다음 KST 날짜로 묶여야 한다(Task 1 테스트 `test_utc_evening_event_goes_to_next_kst_day`).
3. **Liquid 충돌** — 글에 인용한 Jinja/Bicep 템플릿의 `{{ }}`·`{% %}`가 `{% raw %}` 밖에 있으면 Jekyll 빌드가 깨지거나 사라진다(Task 3 테스트 `test_liquid_outside_raw_is_reported`).
4. **언어 짝 누락·전환 링크** — 영문만 있고 한글이 없는 편, 또는 `permalink`가 `/<lang>/<ref>/`가 아닌 편은 검사 실패해야 한다(Task 3 테스트 `test_missing_ko_pair_is_reported`, `test_permalink_must_match_lang_and_ref`).
5. **문서용 IP·noreply 주소 오탐** — `192.0.2.x`·`198.51.100.x`·`203.0.113.x`, `noreply@github.com`, 버전 번호 `1.37.2`는 통과해야 한다(Task 2 테스트 `test_allows_documentation_ranges_and_versions`).

---

### Task 1: 날짜별 자료 묶음 도구

**Files:**
- Create: `blog/tools/digest.py`, `blog/tools/__init__.py`, `blog/tools/tests/__init__.py`
- Test: `blog/tools/tests/test_digest.py`

**Interfaces:**
- Produces:
  - `kst_date(ts: str) -> str` — ISO 타임스탬프(UTC `Z` 또는 오프셋 포함)를 KST `YYYY-MM-DD`로.
  - `@dataclass Item(day: str, time: str, source: str, kind: str, text: str)` — `source`는 `session:<label>`·`git:<label>`·`deploy-history`, `kind`는 `user`·`ask`·`answer`·`assistant`·`tool_fail`·`checkpoint`·`commit`·`deploy`.
  - `read_events(path: Path, label: str) -> list[Item]` — `events.jsonl`에서 `user.message`(`data.content`, 빈 값 제외), `human_response.recorded`(`data.response`의 `message`→`ask`, `content`→`answer`), `tool.execution_complete` 중 `data.success`가 거짓(`False`/`"False"`)인 것(`data.error.message`→`tool_fail`), 턴의 마지막 `assistant.message`(`data.content`, 600자로 자름).
  - `read_checkpoints(db: Path, session_id: str, label: str) -> list[Item]` — 세션 저장소 SQLite `checkpoints`(title + overview, `created_at`).
  - `read_git(repo: Path, label: str) -> list[Item]` — `git log --format=%aI%x09%s`.
  - `read_deploy_history(md: Path) -> list[Item]` — `## YYYY-MM-DD 제목` 절 단위, 본문 포함.
  - `write_digests(items: list[Item], out: Path) -> list[Path]` — 날짜마다 `out/YYYY-MM-DD.md` 한 개, 시간순, 출처별 소제목. 맨 위에 개수 요약(사용자 메시지·실패·커밋 수).
  - CLI: `python -m blog.tools.digest --sources sources.json --out <dir>`. `sources.json`은 `[{"label": "...", "session_id": "...", "repo": "..."}]`와 `deploy_history`, `session_db`, `session_state` 경로.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_utc_evening_event_goes_to_next_kst_day():
    assert kst_date("2026-10-31T15:30:00Z") == "2026-11-01"
    assert kst_date("2026-10-31T14:59:59.000Z") == "2026-10-31"
    assert kst_date("2026-10-26T18:45:56.381+09:00") == "2026-10-26"

def test_read_events_extracts_user_ask_and_failures(tmp_path):
    # user.message(content 빈 것 1, 있는 것 1), human_response.recorded 1,
    # tool.execution_complete success "False" 1·True 1, assistant.message 2(같은 turnId)
    items = read_events(write_jsonl(tmp_path, EVENTS), "A1")
    assert [i.kind for i in items] == ["user", "ask", "answer", "tool_fail", "assistant"]
    assert items[-1].text == "second reply"          # 턴의 마지막 답만
    assert all(i.source == "session:A1" for i in items)

def test_write_digests_groups_by_day_in_time_order(tmp_path):
    paths = write_digests([Item("2026-11-01","10:00","git:A","commit","b"),
                           Item("2026-10-31","09:00","git:A","commit","a"),
                           Item("2026-11-01","08:00","session:A","user","q")], tmp_path)
    assert [p.name for p in paths] == ["2026-10-31.md", "2026-11-01.md"]
    body = paths[1].read_text(encoding="utf-8")
    assert body.index("q") < body.index("b")
```

`read_deploy_history`·`read_git`·`read_checkpoints`도 각각 작은 입력으로 한 건씩 테스트한다(절 분리, 탭 구분 파싱, SQLite 한 행).

- [ ] **Step 2: 실패 확인** — `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider blog/tools/tests/test_digest.py` → `ModuleNotFoundError`/`ImportError`.
- [ ] **Step 3: `blog/tools/digest.py` 구현** — `data`가 문자열이면 `ast.literal_eval`이 아니라 `json.loads`를 먼저 시도한다(파일은 JSON 줄). 시각은 `datetime.fromisoformat`(Python 3.12는 `Z` 지원) 후 `ZoneInfo("Asia/Seoul")`.
- [ ] **Step 4: 통과 확인** — 같은 명령 → PASS.
- [ ] **Step 5: 커밋** — `git add blog/tools` → `feat(blog): 날짜별 자료 묶음 도구`.

### Task 2: 민감 정보 검사기

**Files:**
- Create: `blog/tools/scan_sensitive.py`
- Test: `blog/tools/tests/test_scan_sensitive.py`

**Interfaces:**
- Produces:
  - `@dataclass Finding(path: str, line: int, rule: str, excerpt: str)`
  - `scan_text(text: str, path: str = "", deny: list[re.Pattern] = ()) -> list[Finding]`
  - `load_denylist(path: Path | None) -> list[re.Pattern]` — 한 줄에 하나, `#` 주석, `re:` 접두사면 정규식, 아니면 대소문자 무시 리터럴.
  - `scan_tree(root: Path, deny) -> list[Finding]` — `root` 아래 `*.md`·`*.html`·`*.yml`(단 `blog/tools/` 제외).
  - CLI: `python -m blog.tools.scan_sensitive blog [--denylist <path>]` — 걸리면 `path:line rule excerpt` 출력 후 종료 코드 1. 환경변수 `BLOG_DENYLIST`도 받는다.
- 일반 규칙(커밋되는 코드에 들어가는 것): `guid`, `ipv4`(문서용 대역 192.0.2.0/24·198.51.100.0/24·203.0.113.0/24와 `0.0.0.0`·`127.0.0.1` 제외, 각 옥텟 0~255), `email`(`noreply@github.com`·`*@users.noreply.github.com`·`@example.com` 제외), `user_path`(`[A-Za-z]:\\Users\\`, `/Users/`, `/home/`), `secret`(`AccountKey=`, `SharedAccessSignature`, `sig=`, `gh[pousr]_[A-Za-z0-9]{20,}`, `eyJ[A-Za-z0-9_-]{20,}\.`(JWT), `Bearer [A-Za-z0-9._-]{20,}`).

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_flags_inside_code_fence_and_inline_code():
    text = "```\nC:\\Users\\someone\\x\n```\nip `20.196.1.2` id 9f4014cf-b673-466c-9f7e-9ae4c3db7387"
    assert {f.rule for f in scan_text(text)} == {"user_path", "ipv4", "guid"}

def test_allows_documentation_ranges_and_versions():
    text = "203.0.113.7 and 192.0.2.10, azd 1.37.2, mail noreply@github.com, 123+x@users.noreply.github.com"
    assert scan_text(text) == []

def test_denylist_literal_and_regex(tmp_path):
    p = tmp_path / "d.txt"; p.write_text("# c\nAcmeCorp\nre:(?i)acme\\s+employee\n", encoding="utf-8")
    deny = load_denylist(p)
    rules = [f.rule for f in scan_text("I work at acmecorp as an ACME employee", deny=deny)]
    assert rules == ["deny", "deny"]

def test_cli_exit_code(tmp_path):
    (tmp_path / "a.md").write_text("ok", encoding="utf-8")
    assert main([str(tmp_path)]) == 0
    (tmp_path / "b.md").write_text("ghp_" + "a" * 30, encoding="utf-8")
    assert main([str(tmp_path)]) == 1
```

- [ ] **Step 2: 실패 확인** — `... pytest ... blog/tools/tests/test_scan_sensitive.py` → ImportError.
- [ ] **Step 3: `blog/tools/scan_sensitive.py` 구현** — 줄 단위 검사, 줄 번호 1부터. 결과 `excerpt`는 걸린 값의 앞 4자 + `…`만 보여 준다(검사 출력이 다시 값을 퍼뜨리지 않게).
- [ ] **Step 4: 통과 확인** → PASS.
- [ ] **Step 5: 커밋** — `feat(blog): 민감 정보 검사기`.

### Task 3: 사이트 뼈대·구조 검사기·배포 워크플로

**Files:**
- Create: `blog/_config.yml`, `blog/index.md`, `blog/en/index.md`, `blog/ko/index.md`, `blog/_layouts/post.html`, `blog/_includes/lang-switch.html`, `blog/assets/main.scss`, `blog/_data/episodes.yml`(빈 목록 `[]`), `blog/README.md`, `blog/tools/check_site.py`, `.github/workflows/blog-pages.yml`
- Test: `blog/tools/tests/test_check_site.py`

**Interfaces:**
- Consumes: Task 2 `scan_tree`, `load_denylist`.
- Produces:
  - 글 앞머리 규약: `layout: post`, `title`, `date: YYYY-MM-DD 09:00:00 +0900`, `lang: en|ko`, `ref: day-NN`(두 자리), `days`(예: `"2026-10-29 – 2026-10-30"`), `permalink: /<lang>/<ref>/`, `summary`(목록에 보이는 한 줄).
  - 파일 위치: `blog/_posts/en/YYYY-MM-DD-<slug>.md`, `blog/_posts/ko/YYYY-MM-DD-<slug>.md`(같은 `ref`는 같은 날짜·슬러그).
  - `episodes.yml` 항목: `{ref, days, title_en, title_ko}`. 목록 페이지는 이 순서대로 보여 준다.
  - `check_site(root: Path) -> list[str]` — 문제 목록(빈 목록이면 통과). 검사: 앞머리 필수 키, `lang` 값, `permalink == f"/{lang}/{ref}/"`, 같은 `ref`의 en·ko 짝, `episodes.yml`의 모든 `ref`에 짝이 있고 글의 모든 `ref`가 `episodes.yml`에 있음, 교훈 제목(en `## Takeaway`, ko `## 오늘의 교훈`)이 정확히 한 번, `{% raw %}…{% endraw %}` 밖의 `{{`·`{%`(레이아웃·include·목록 페이지는 제외, `_posts`만 검사).
  - CLI: `python -m blog.tools.check_site blog` — 문제 출력 후 종료 코드 1.
- 레이아웃: `post.html`은 minima `default`를 상속하고 본문 위에 `lang-switch.html`(같은 `ref`·다른 `lang` 글을 `site.posts`에서 찾아 `relative_url` 링크, 현재 언어는 굵게)과 `days`를 보여 준다. `<html lang>`은 페이지 `lang`. 목록 페이지(`en/index.md`, `ko/index.md`)는 `site.data.episodes` 순서로 해당 언어 글을 링크한다. 첫 화면 `index.md`는 영문 소개 한 단락 + 한글 소개 한 단락 + 두 목록 링크(소개 문구는 Task 5에서 확정, 여기서는 자리 문구).
- 워크플로: `on: push(branches: [main], paths: ['blog/**', '.github/workflows/blog-pages.yml'])`, `workflow_dispatch`. 잡 `build`: checkout → setup-python 3.12 → `pip install pyyaml` → `python -m blog.tools.scan_sensitive blog` → `python -m blog.tools.check_site blog` → `actions/configure-pages@v5` → `actions/jekyll-build-pages@v1`(`source: ./blog`, `destination: ./_site`) → `actions/upload-pages-artifact@v3`. 잡 `deploy`: `actions/deploy-pages@v4`, `environment: github-pages`. 권한 `contents: read`, `pages: write`, `id-token: write`. `concurrency: pages`.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_missing_ko_pair_is_reported(tmp_path):
    site = make_site(tmp_path, episodes=[EP1], posts=[post("en", "day-01")])
    assert any("day-01" in p and "ko" in p for p in check_site(site))

def test_permalink_must_match_lang_and_ref(tmp_path):
    site = make_site(tmp_path, episodes=[EP1],
                     posts=[post("en", "day-01", permalink="/day-01/"), post("ko", "day-01")])
    assert any("permalink" in p for p in check_site(site))

def test_liquid_outside_raw_is_reported(tmp_path):
    body_ok = "{% raw %}{{ name }}{% endraw %}"
    body_bad = "template {{ name }}"
    assert check_site(make_site(tmp_path / "a", [EP1], [post("en","day-01",body=body_ok), post("ko","day-01")])) == []
    assert any("liquid" in p for p in check_site(make_site(tmp_path / "b", [EP1], [post("en","day-01",body=body_bad), post("ko","day-01")])))

def test_takeaway_heading_required_once(tmp_path):
    site = make_site(tmp_path, [EP1], [post("en","day-01", takeaway=False), post("ko","day-01")])
    assert any("Takeaway" in p for p in check_site(site))
```

`make_site`·`post`·`EP1`는 테스트 파일 안 도우미(유효한 앞머리와 교훈 제목을 기본으로 넣음).

- [ ] **Step 2: 실패 확인** → ImportError.
- [ ] **Step 3: `check_site.py`와 사이트 파일·워크플로 작성.** 앞머리 파싱은 `yaml.safe_load`.
- [ ] **Step 4: 통과 확인** — `... pytest ... blog/tools/tests` → PASS. 빈 사이트에 대해 `python -m blog.tools.check_site blog` → 종료 코드 0, `python -m blog.tools.scan_sensitive blog` → 0.
- [ ] **Step 5: 커밋** — `feat(blog): Jekyll 사이트 뼈대와 Pages 워크플로`. (푸시하지 않는다. 실제 Jekyll 빌드는 로컬에 Ruby가 없어 Task 7 첫 워크플로 실행에서 확인한다.)

### Task 4: 자료 묶음 생성과 목차 승인

**Files:**
- Create(커밋 밖): 세션 폴더 `files/blog-digest/sources.json`, `files/blog-digest/denylist.txt`, `files/blog-digest/YYYY-MM-DD.md`, `files/blog-digest/outline.md`
- Modify: `blog/_data/episodes.yml`

**Interfaces:**
- Consumes: Task 1 CLI, Task 2 `load_denylist`.
- Produces: `episodes.yml`(승인된 편 목록), 편마다 근거가 되는 자료 묶음 날짜 범위.

- [ ] **Step 1: `sources.json` 작성** — 스펙 4절 표의 9개 세션(id 전체는 세션 저장소 `sessions` 표에서 cwd로 찾음)과 각 폴더 git 저장소, 이 저장소의 `docs/runbook/deploy-history.md`, `%USERPROFILE%\.copilot\session-store.db`. 라벨: 시작 순서대로 `A1`~`A7`(본선 ama-agent-04는 `main`, 질의응답은 `qna`).
- [ ] **Step 2: `denylist.txt` 작성** — 리소스 해시 접미사, 로컬 사용자명, git 작성자 이름·이메일(각 저장소 `git log --format='%an%n%ae'`에서 수집), 회사 계정 이름, 과정 이름과 그 약어 문구, 회사 소속을 드러내는 표현(`re:` 정규식으로). 이 목록의 실제 값은 계획·커밋 어디에도 적지 않는다. 실명이 기록에서 확인되지 않으면 저자에게 묻는다.
- [ ] **Step 3: 자료 묶음 생성** — `python -m blog.tools.digest --sources <sources.json> --out <files/blog-digest>`. 확인: 10/26~11/17 사이 기록이 있는 날마다 파일이 하나씩 있고, 각 파일 맨 위 개수 요약이 있다.
- [ ] **Step 4: 목차 초안 `outline.md`** — 편마다 `ref`, 날짜 범위, 영문·한글 제목, 그날의 중심 사건 1~2개(자료 묶음 근거 줄 표시), 교훈 후보 한 줄. 기록이 적은 날(스펙 7.2 후보)은 묶는다. 시도 1~7을 접은 이유가 기록에 없으면 "저자 확인" 표시.
- [ ] **Step 5: 저자 승인 관문 ①** — 목차를 한국어로 짧게 보여 주고 승인·수정을 받는다. "저자 확인" 항목도 이때 함께 묻는다.
- [ ] **Step 6: `episodes.yml` 반영·커밋** — `check_site`는 아직 글이 없어 짝 누락을 보고하므로 이 단계에서는 커밋만 하고 검사는 Task 6 끝에서 통과시킨다. 커밋: `docs(blog): 편 목록`.

### Task 5: 첫 편(문체 승인)

**Files:**
- Create: `blog/_posts/en/<첫 편>.md`, `blog/_posts/ko/<첫 편>.md`
- Modify: `blog/index.md`(소개 문구 확정)

**Interfaces:**
- Consumes: Task 3 앞머리 규약, Task 4 `episodes.yml`·자료 묶음.

- [ ] **Step 1: 영문 초안** — 해당 날짜 자료 묶음만 근거로, Global Constraints의 구성·익명·자리표시 규칙대로.
- [ ] **Step 2: 한글판** — 영문을 옮기되 한글 문체 규칙으로 다시 쓴다(직역 아님).
- [ ] **Step 3: 소개 문구** — `index.md`의 영문·한글 소개 단락(익명, 독자에게 하는 말 포함).
- [ ] **Step 4: 검사** — `python -m blog.tools.scan_sensitive blog --denylist <denylist.txt>` → 0. `check_site`는 이 편에 대해 문제가 없어야 한다(다른 편 짝 누락만 남음).
- [ ] **Step 5: 저자 승인 관문 ②** — 두 판의 경로와 첫 몇 문단을 보여 주고 문체·분량·드러내는 정도를 확인받는다. 고칠 점은 Global Constraints에 한 줄씩 더해 이후 편에 적용한다.
- [ ] **Step 6: 커밋** — `docs(blog): 첫 편`.

### Task 6: 나머지 편

**Files:**
- Create: 나머지 `ref`마다 `blog/_posts/en/…md`, `blog/_posts/ko/…md`

**Interfaces:**
- Consumes: Task 5에서 확정한 문체 규칙, Task 4 자료 묶음.

- [ ] **Step 1: 편마다 영문 → 한글** — 편끼리 독립이므로 서브에이전트에 나눠 맡길 수 있다. 각 서브에이전트에는 그 편의 자료 묶음, 목차 항목, 첫 편 두 판(문체 견본), Global Constraints를 준다. 앞뒤 편과 이어지는 한 문장(지난 편 → 이번 편)을 첫 문단에 넣는다.
- [ ] **Step 2: 검사** — `scan_sensitive`(denylist 포함) → 0, `check_site blog` → 0(이제 모든 짝이 있음).
- [ ] **Step 3: 사실 확인** — 편마다 인용한 저자 요청·실패 메시지가 자료 묶음에 실제로 있는지 대조(지어낸 인용 0건).
- [ ] **Step 4: 커밋** — 몇 편씩 묶어 `docs(blog): N~M편`.

### Task 7: 최종 검토·공개

**Files:**
- Modify: 검토 반영분

- [ ] **Step 1: 전체 검사** — `... pytest ... blog/tools/tests` PASS, 기존 `... pytest -q -p no:cacheprovider`(전체, `initial_wait` 900) 0 failed, `scan_sensitive`(denylist 포함)·`check_site` 0.
- [ ] **Step 2: 리뷰어 서브에이전트** — 익명·민감 정보·사실성·문체 관점으로 전체 글 검토. 문제는 고친다.
- [ ] **Step 3: 저자 승인 관문 ③** — 편 목록과 검토 결과를 보여 주고 공개·푸시·Pages 활성화 승인을 받는다.
- [ ] **Step 4: 푸시와 Pages 활성화** — 기존 방식으로 `master:main` 푸시. `gh api -X POST repos/freeman9844/ms-ama-04/pages -f build_type=workflow`(이미 있으면 `PUT`으로 `build_type=workflow`). 워크플로 실행이 실패하면 로그를 보고 고쳐 다시 푸시(Liquid·테마 오류 등).
- [ ] **Step 5: 라이브 확인** — `https://freeman9844.github.io/ms-ama-04/`, `/en/`, `/ko/`, 각 편 URL이 200이고, 전환 링크가 짝 글로 가며, 첫 화면에 두 목록이 보인다(`curl -s -o NUL -w "%{http_code}"`).
- [ ] **Step 6: 배포 기록** — `blog/README.md`에 갱신 방법(자료 묶음 → 글 → 검사 → 푸시)과 공개 주소를 적고 커밋·푸시.
