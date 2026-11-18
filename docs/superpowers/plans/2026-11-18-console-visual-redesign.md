# 콘솔 비주얼 리디자인(B안) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 고객·심사 콘솔을 B안(핀테크 앱형)으로 바꿔 실제 서비스처럼 보이게 한다.

**Architecture:** 고객 화면에 진행 단계(`CustomerStatus.step`)와 히어로 카드·사업자 카드를 더하고(Task 1), `console.css`를 B안 토큰·컴포넌트로 다시 쓰고 두 레이아웃에 SVG 로고를 넣는다(Task 2). 기존 클래스 이름은 지우지 않고 더하기만 한다.

**Tech Stack:** FastAPI + Jinja2, 단일 CSS(`src/console/static/console.css`), pytest, 로컬 미리보기 `python -m consolepreview`.

**Spec:** `docs/superpowers/specs/2026-11-18-console-visual-redesign-design.md`

## Global Constraints

- CSP 그대로: `style-src 'self'`, `img-src 'self' data:`. 템플릿에 `style=` 속성·`<style>` 금지(`test_templates_have_no_inline_style_or_script`가 지킴). 외부 폰트·CDN 금지.
- 기존 클래스 이름(`sidebar`, `brand`, `n-*`, `active`, `apply-status s-*`, `status s-*`, `pill rec-*`, `badge-*`, `kpi kpi-*`, `card` 등)은 유지한다.
- 토큰: 배경 `#f6f8fc`, 카드 `#fff`, 본문 `#191f28`, 보조 `#8b95a1`, 강조 `#3182f6`→`#7c5cff`, 상태 파랑 `#3182f6`·초록 `#22c55e`·주황 `#f59e0b`·빨강 `#f04452`·보라 `#7c5cff`. 카드 반경 14px(히어로 18px), 그림자 `0 4px 14px rgba(0,0,0,.05)`, 버튼 반경 10px.
- 배지·본문 글자 대비 WCAG AA(4.5:1) 이상. 색만으로 상태를 구분하지 않는다.
- 단계 이름: `접수`·`데이터 심사`·`심사역 검토`·`결과`.
- 새 한국어 문구는 모두 `src/console/locales/en/customer.json`에 영어를 넣는다(다른 카탈로그와 같은 키·다른 번역 금지).
- 테스트: `$env:PYTHONIOENCODING="utf-8"; .venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/console`.

## Review Focus

1. 문서가 아직 없는 접수 대기 화면(`/apply/<id>` 문서 없음): 히어로에 금액·기간 없이 단계 0(`접수`)만 보여야 한다 → Task 1 `test_progress_steps_follow_status[None-...]`.
2. 승인 금액이 신청 금액과 다를 때: 히어로 큰 숫자는 승인 금액 → Task 1 `test_hero_shows_officer_amount_when_approved`.
3. 신청이 없는 사업자의 신청 현황: 히어로 없이 "신청 기록이 없습니다." → Task 1 `test_status_page_without_applications_has_no_hero`.
4. 영어 화면: 단계 이름·새 문구가 모두 영어 → 기존 `test_every_msgid_has_english` + Task 1 `test_progress_steps_in_english`.
5. 좁은 화면(≤720px): 사이드바가 위로 쌓이고 본문이 가로 스크롤 없이 보인다 → Task 2 스크린샷 확인(390px 폭).

---

### Task 1: 고객 화면 구조 — 진행 단계·히어로 카드·사업자 카드

**Files:**
- Modify: `src/console/customer.py` (`CustomerStatus`, `customer_status`, 상수 `PROGRESS_STEPS`)
- Modify: `src/console/app.py` (`apply_status` 라우트 약 575–586행, `borrower_page` 약 931–936행, Jinja 필터 등록 약 258행)
- Create: `src/console/templates/_progress.html` (매크로 `hero`)
- Modify: `src/console/templates/apply_status.html`, `borrower.html`, `customer_select.html`
- Modify: `src/console/locales/en/customer.json`
- Test: `tests/console/test_apply.py`, `tests/console/test_privacy.py`

**Interfaces:**
- Produces: `PROGRESS_STEPS: tuple[str, ...] = ("접수", "데이터 심사", "심사역 검토", "결과")`(한국어 원문, 템플릿에서 `_()`), `CustomerStatus.step: int`(0~3, 마지막 필드, 기본값 없음 → 모든 생성 지점에 명시).
- Produces(마크업, Task 2 CSS가 소비): `<section class="card hero-card">`, `<p class="hero-meta">`, `<p class="hero-amount">`, `<ol class="steps">` 안의 `<li class="step done">`/`<li class="step now" aria-current="step">`/`<li class="step">`, 결과 단계 `now`에는 `tone-good`/`tone-bad` 추가, `<ul class="seller-grid">` 안의 `<li><a class="seller-card" href=...><span class="avatar av-N" aria-hidden="true">X</span> 라벨</a></li>`(N = 1~6 순환).
- Produces: Jinja 필터 `seller_initial(applicant_id) -> str` = `(seller_name(id) or id)[:1]`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/console/test_apply.py`:

```python
@pytest.mark.parametrize(("status", "current", "done"), [
    (None, "접수", 0),
    ("received", "데이터 심사", 1),
    ("assessed", "데이터 심사", 1),
    ("awaiting_officer", "심사역 검토", 2),
    ("approved", "결과", 3),
    ("declined", "결과", 3),
    ("withdrawn", "결과", 3),
])
def test_progress_steps_follow_status(env, status, current, done):
    client, store, _ = env
    path = "/apply/app-20261104-c0ffee"
    if status:
        store.docs["app-cust"]["status"] = status
        path = "/apply/app-cust"
    html = client.get(path, headers=CUSTOMER).text
    assert f'aria-current="step">{current}</li>' in html
    assert html.count('class="step done"') == done
    assert html.count('aria-current="step"') == 1


def test_hero_shows_requested_amount_and_tenor(env):
    client, store, _ = env
    store.docs["app-cust"]["status"] = "received"
    html = client.get("/apply/app-cust", headers=CUSTOMER).text
    assert 'class="card hero-card"' in html
    assert '<p class="hero-amount">30,000,000원</p>' in html and "12개월" in html


def test_hero_shows_officer_amount_when_approved(env):
    client, store, _ = env
    store.docs["app-cust"].update(status="approved", officer_decision={"decision": "approve", "amount_krw": 6_000_000})
    html = client.get("/apply/app-cust", headers=CUSTOMER).text
    assert '<p class="hero-amount">6,000,000원</p>' in html and "step now tone-good" in html


def test_waiting_hero_has_no_amount(env):
    client, _, _ = env
    html = client.get("/apply/app-20261104-c0ffee", headers=CUSTOMER).text
    assert 'class="hero-amount"' not in html


def test_progress_steps_in_english(env):
    client, store, _ = env
    store.docs["app-cust"]["status"] = "awaiting_officer"
    html = client.get("/apply/app-cust", headers={**CUSTOMER, "Cookie": "lang=en"}).text
    for korean in ("데이터 심사", "심사역 검토", "접수"):
        assert f">{korean}</li>" not in html
```

기존 `test_status_mapping_hides_internal_evidence`의 `f">{label}</p>"` 단언은 그대로 통과해야 한다(상태 배지는 `<p class="apply-status s-..">라벨</p>` 유지).

`tests/console/test_privacy.py`:

```python
def test_status_page_shows_latest_application_as_hero(env):
    client, _, _ = env
    page = client.get(HOME, headers=CUSTOMER).text
    hero = page.split('class="card hero-card"', 1)[1].split("</section>", 1)[0]
    first_row = re.search(r'<tbody>.*?<tr><td>(?:<a [^>]*>)?([\w-]+)', page, re.S).group(1)
    assert first_row in hero and 'aria-current="step"' in hero


def test_status_page_without_applications_has_no_hero(env):
    client, _, _ = env
    page = client.get("/borrower/seller-unknown-99", headers=CUSTOMER).text
    assert "hero-card" not in page and "신청 기록이 없습니다." in page


def test_borrower_index_shows_seller_cards_with_initial_avatar(env):
    client, _, _ = env
    page = client.get("/borrower", headers=CUSTOMER).text
    assert '<ul class="seller-grid">' in page and 'class="seller-card" href="/borrower/seller-dailylook-01"' in page
    assert re.search(r'<span class="avatar av-\d" aria-hidden="true">성</span>', page)
```

`seller-unknown-99`가 동의·신청이 없는 신청인으로 200을 돌려주지 않으면, 동의가 없는 데모 신청인 중 하나로 바꾼다(Ruling 기록).

- [ ] **Step 2: 실패 확인**

Run: `$env:PYTHONIOENCODING="utf-8"; .venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/console/test_apply.py tests/console/test_privacy.py`
Expected: 새 테스트 FAIL(`hero-card`·`aria-current`·`seller-grid` 없음), 기존 테스트 PASS.

- [ ] **Step 3: 구현**

- `customer.py`: `PROGRESS_STEPS` 추가, `CustomerStatus`에 `step: int` 추가. 매핑: 문서 없음 0, `_REVIEWING`·`None` 1, `_OFFICER` 2, 승인·거절·철회·종료·기타 3.
- `app.py`:
  - `apply_status`: `requested_krw=doc.get("requested_amount_krw") if doc else None`, `tenor=doc.get("requested_tenor_months") if doc else None`를 넘긴다.
  - `borrower_page` rows에 `"tenor": doc.get("requested_tenor_months")` 추가.
  - `env.filters["seller_initial"]` 등록(`shared.sellers.seller_name` 사용).
- `_progress.html` 매크로 `hero(s, application_id, seller, amount, tenor)`: 메타(사업자·신청번호) → 큰 금액(`s.amount_krw or amount`, 둘 다 없으면 생략) → 기간 → `<ol class="steps">` → `<p class="apply-status s-{{ s.tone }}">{{ s.label }}</p>` + `s.message`.
- `apply_status.html`: 기존 카드 대신 `hero(...)`, 자동 새로 고침 안내는 카드 밖 `note`로 유지.
- `borrower.html`: `{% if rows %}` 안에서 `rows[0]`으로 `hero(...)`를 그린 뒤 기존 표.
- `customer_select.html`: `<ul class="seller-grid">` + `seller-card`, `loop.index0 % 6 + 1`로 `av-N`.
- `customer.json`: 새 msgid 영어(`접수`→"Received", `데이터 심사`→"Data review", `심사역 검토`→"Officer review", `결과`→"Result", 기간 문구 등). 충돌은 `load_catalog`로 확인.

- [ ] **Step 4: 통과 확인**

Run: `$env:PYTHONIOENCODING="utf-8"; .venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/console`
Expected: 전부 PASS(`test_every_msgid_has_english` 포함).

- [ ] **Step 5: 커밋**

```powershell
git add src/console/customer.py src/console/app.py src/console/templates/_progress.html src/console/templates/apply_status.html src/console/templates/borrower.html src/console/templates/customer_select.html src/console/locales/en/customer.json tests/console/test_apply.py tests/console/test_privacy.py
git commit -q -F $env:TEMP\cmsg.txt   # "feat(console): 고객 진행 단계·히어로 카드·사업자 카드"
```

---

### Task 2: B안 스타일 — CSS 재작성·로고·문서

**Files:**
- Modify: `src/console/static/console.css` (머리 주석·토큰·컴포넌트 전반)
- Modify: `src/console/templates/base.html`, `src/console/templates/customer_base.html` (`.brand` 안 로고)
- Modify: 화면 모양을 설명하는 리빙 문서(`grep -rn "Azure Portal 풍\|Fluent" docs` 결과 중 현재 상태 서술만; 과거 스펙·배포 이력은 그대로)
- Test: `tests/console/test_ui.py`

**Interfaces:**
- Consumes: Task 1 마크업 클래스(`hero-card`, `hero-meta`, `hero-amount`, `steps`, `step done|now`, `tone-good|tone-bad`, `seller-grid`, `seller-card`, `avatar av-1..6`).
- Produces: 로고 마크업 `<svg class="logo-mark" aria-hidden="true" ...>` (gradient `<linearGradient>` + `<text>A</text>`; 속성만, `style` 없음). `.brand` 링크·텍스트 `AMA Lending <small>…</small>`는 유지.

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/console/test_ui.py`)

```python
@pytest.mark.parametrize("path", ["/", "/borrower"])
def test_brand_shows_logo_mark(client, path):
    html = page(client, path)
    brand = html.split('class="brand"', 1)[1].split("</a>", 1)[0]
    assert '<svg class="logo-mark" aria-hidden="true"' in brand and "AMA Lending" in brand
```

- [ ] **Step 2: 실패 확인**

Run: `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/console/test_ui.py -k logo`
Expected: FAIL 2건.

- [ ] **Step 3: 구현**

- 두 레이아웃 `.brand` 맨 앞에 같은 SVG 로고(그라데이션 id는 `logo-g`).
- `console.css`:
  - 토큰을 Global Constraints 값으로 교체(`--c-*` 차트 색 포함). 머리 주석을 "B안(핀테크 앱형)"으로.
  - 카드·버튼·입력·표·탭·배지(`.status`, `.pill`, `.badge-*`, `.apply-status`)를 둥근 알약·새 팔레트로.
  - 심사 사이드바: `linear-gradient(180deg,#14213f,#1e2f57)`, 활성 메뉴 반투명 알약. 고객(`.customer-shell .sidebar`): 흰 배경·오른쪽 경계선, 활성 `#e8f3ff`/`#1b64da`.
  - `.kpi-<tone>::before` 아이콘 칩(22px, 반경 8px, 톤 색 배경, 흰 글리프).
  - Task 1 클래스: 히어로(반경 18px, 파랑 그림자), 단계 표시(원+선, `done` 파랑, `now` 보라 링, `tone-good` 초록, `tone-bad` 빨강), 사업자 카드 격자(2~3열)와 `av-1..6` 색.
  - `@media (max-width: 720px)`: `.shell`을 1열로, 사이드바 `position: static; height: auto`.
- 리빙 문서의 화면 모양 서술을 B안으로 고친다.

- [ ] **Step 4: 통과 확인**

Run: `$env:PYTHONIOENCODING="utf-8"; .venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/console`
Expected: 전부 PASS(인라인 스타일 금지·CSP 불변 테스트 포함).

- [ ] **Step 5: 화면 확인(스크린샷)**

```powershell
$p = Start-Process -PassThru -WindowStyle Hidden .venv\Scripts\python.exe "-m consolepreview --port 8765"
$edge = "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
foreach ($s in @(@("home","/",1400), @("detail","/applications/app-20261103-p01",1400), @("loan","/loans/app-20261103-p03",1400), @("select","/borrower",1400), @("status","/borrower/seller-dailylook-01",1400), @("apply","/apply?applicant_id=seller-dailylook-01",1400), @("mobile","/borrower/seller-dailylook-01",390))) {
  & $edge --headless=new --disable-gpu "--window-size=$($s[2]),1000" "--screenshot=$PWD\.superpowers\shots\$($s[0]).png" "http://127.0.0.1:8765$($s[1])"
}
Stop-Process -Id $p.Id
```

Expected: 7장 모두 B안 톤. 상세·대출 화면 표·평가서가 읽히고 겹침 없음, 390px에서 가로 스크롤 없음. 스크린샷은 커밋하지 않는다.

- [ ] **Step 6: 커밋**

```powershell
git add src/console/static/console.css src/console/templates/base.html src/console/templates/customer_base.html tests/console/test_ui.py <고친 문서>
git commit -q -F $env:TEMP\cmsg.txt   # "feat(console): B안 핀테크 앱형 스타일과 로고"
```
