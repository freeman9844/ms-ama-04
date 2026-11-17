# 고객 콘솔 사업자 기준 메뉴 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 고객 콘솔에 들어오면 사업자를 먼저 고르고, 이후 대출 신청을 포함한 모든 메뉴가 그 사업자 기준으로 동작하게 한다.

**Architecture:** 사업자는 지금처럼 주소(`/borrower/<사업자>/…`, `/apply?applicant_id=`)에 담는다. `/apply`는 사업자가 없으면 `/borrower`로 보내고, 신청서는 드롭다운 대신 읽기 전용 사업자 이름 + hidden 필드를 쓴다.

**Tech Stack:** FastAPI, Jinja2, pytest(TestClient)

**Spec:** `docs/superpowers/specs/2026-11-17-customer-console-seller-context-design.md`

## Global Constraints

- 모든 화면 문구는 한국어 `_()` msgid이고, 새 msgid마다 `src/console/locales/en/customer.json`(또는 해당 카탈로그)에 영문을 넣는다(`test_every_msgid_has_english`).
- CSP `style-src 'self'`: 인라인 style 금지.
- 고객 페이지는 `customer()`(로그인만), POST는 CSRF 유지.
- 데모 사업자 목록은 `DEMO_APPLICANTS`.

## Review Focus

- `/apply?applicant_id=nope` 같은 모르는 사업자 → 신청서가 아니라 `/borrower`로 303(Task 1 테스트).
- 위조 POST의 모르는 `applicant_id` → 큐 전송 없이 `/borrower`로 303(Task 1 테스트).
- 422 재표시(동의·금액 오류)에서 같은 사업자가 유지되고 메뉴가 그 사업자를 가리킴(Task 1 테스트).
- 사업자 없는 화면(`/borrower`)에서 `대출 신청` 메뉴가 보이지 않음(Task 1 테스트).
- 영문 화면에 새 한국어 UI 문구가 남지 않음(`test_i18n` EN_PAGES가 `/apply?applicant_id=…`를 렌더).

---

### Task 1: 사업자 먼저 선택 흐름

**Files:**
- Modify: `src/console/app.py`(`apply_page`, `apply_submit`)
- Modify: `src/console/templates/apply.html`, `customer_base.html`, `customer_select.html`, `base.html`
- Modify: `src/console/locales/en/customer.json`
- Test: `tests/console/test_apply.py`, `test_privacy.py`, `test_ui.py`, `test_i18n.py`

**Interfaces:**
- Produces: `GET /apply`(사업자 없음·모름 → 303 `/borrower`), `POST /apply`(모르는 사업자 → 303 `/borrower`), 진입 링크 `/borrower`.

- [ ] **Step 1: 테스트 고치기/추가**

`test_apply.py`:
- `token()`은 `/apply?applicant_id=seller-dailylook-01`에서 CSRF를 읽는다.
- `test_apply_page_needs_login_but_no_console_role`: 401은 그대로, `GET /apply?applicant_id=seller-dailylook-01`가 200이고 동의 3종·`성수데일리룩 · 스마트스토어·쿠팡·자사몰 · 패션의류`·`name="applicant_id" value="seller-dailylook-01"`(hidden)·`<select name="applicant_id"`가 **없음**을 확인.
- `test_apply_page_preselects_known_applicant_and_opens_its_menu` → `test_apply_page_is_fixed_to_chosen_applicant`: `seller-candle-05` 이름과 hidden 값, `n-apply active`, `/borrower/seller-candle-05/grievances` 링크.
- 새 `test_apply_without_known_applicant_goes_to_customer_select`: `/apply`, `/apply?applicant_id=nope` 모두 303, `location == "/borrower"`.
- `test_out_of_range_input_is_rejected_and_form_kept`에서 `applicant_invalid` 케이스를 빼고, 남은 케이스에 `'value="seller-dailylook-01"' in response.text`와 `href="/borrower/seller-dailylook-01"` 확인을 더한다.
- 새 `test_unknown_applicant_post_goes_to_customer_select`: `apply(client, applicant_id="seller-unknown-99")` → 303, `/borrower`, `queue.sent == []`.

`test_privacy.py`:
- `test_officer_sidebar_links_customer_console_and_grievances`: `'class="customer-link" href="/borrower"'`; `'href="/borrower"'` 부재 단언은 `'<li class="n-borrower'` 부재로 바꾼다.
- `test_borrower_index_lists_demo_applicants`: `'<li class="n-status active"><a href="/borrower">고객 선택</a>'`, 그리고 `'class="n-apply'`가 **없음**.

`test_ui.py`: `("/apply", …)` → `("/apply?applicant_id=seller-dailylook-01", '<li class="n-apply active">')`.
`test_i18n.py`: `EN_PAGES`의 `"/apply"` → `"/apply?applicant_id=seller-dailylook-01"`.

- [ ] **Step 2: 실패 확인**

Run: `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/console/test_apply.py tests/console/test_privacy.py tests/console/test_ui.py`
Expected: 새/바뀐 단언 FAIL.

- [ ] **Step 3: 구현**

- `apply_page`: `applicant_id not in DEMO_APPLICANTS`면 `RedirectResponse("/borrower", 303)`.
- `apply_submit`: 사업자 검사를 `validate_apply`보다 먼저 해서 모르면 303 `/borrower`(큐 전송 없음). 422 재표시는 `applicant_id=chosen`.
- `apply.html`: 드롭다운을 `<p class="seller-fixed"><strong>{{ applicant_id|seller_label }}</strong> <a href="/borrower">{{ _("고객 변경") }}</a></p>` + `<input type="hidden" name="applicant_id" value="{{ applicant_id }}">`로 바꾼다(제목 `신청 사업자` 유지, `사업자(데모)` 라벨 삭제).
- `customer_base.html`: 브랜드 링크 `/borrower`. `n-apply` li를 `{% if applicant_id %}` 안으로 옮기고, 사업자 없을 때의 항목 문구를 `고객 선택`으로.
- `customer_select.html`: 제목·h1 `고객 선택`, 안내문은 "신청 사업자(셀러)를 고르면 그 사업자 기준으로 대출 신청·신청 현황·상환 플랜·받은 안내·동의 관리·이의제기 메뉴가 열립니다. 데모라 로그인한 사용자가 사업자를 직접 고릅니다."
- `base.html`: `customer-link` href `/borrower`.
- `customer.json`: 새 msgid(`고객 선택`, 새 안내문) 영문 추가, 쓰지 않게 된 키(`사업자(데모)`, 옛 안내문, 사이드바에서만 쓰던 `내 대출 관리`가 더 안 쓰이면) 삭제.

- [ ] **Step 4: 통과 확인**

Run: `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/console`
Expected: 모두 PASS.

- [ ] **Step 5: 커밋** — `feat(console): 고객 콘솔을 사업자 선택 뒤 그 사업자 기준으로 고정`

### Task 2: 문서

**Files:** `docs/architecture.md:39,92`, `docs/limitations.md:34,80`, `docs/demo/demo-script.md:11-12,20,38`, `docs/demo/fallback.md:9,29,40`

- [ ] **Step 1:** 고객 진입을 "`고객 콘솔 열기`/`/borrower`에서 사업자 선택 → `대출 신청`(`/apply?applicant_id=<사업자>`)"으로 고친다. demo-script 38행은 "`고객 선택`에서 `성수데일리룩 · …`을 고른 뒤 `대출 신청` 메뉴"로, 브라우저 탭 1은 `/apply?applicant_id=seller-dailylook-01`.
- [ ] **Step 2:** 전체 테스트 `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests` 통과 확인.
- [ ] **Step 3: 커밋** — `docs: 고객 콘솔 사업자 선택 흐름 반영`
