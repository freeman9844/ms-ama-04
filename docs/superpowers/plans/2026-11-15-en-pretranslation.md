# 영문 화면 사전 번역 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 데이터 문구를 생성 시점에 가명 처리한 채 번역해 Cosmos `translations`에 저장하고, 콘솔 영문 화면이 처음부터 저장 번역으로 렌더되게 한다. 이미 배포한 즉시 번역 경로도 가명 문구만 보내도록 고친다.

**Architecture:** 공유 모듈(`src/agents/pretranslate.py`)이 번역 대상 추출(허용 목록)·사건별 가명처리·키·토큰 검사를 정한다. 오케스트레이터 Functions의 Cosmos 변경 피드 트리거 4개가 새 문구만 번역해 `translations`(pk `/owner_id`)에 upsert한다. 콘솔은 렌더마다 `PageTexts`(사건 가명처리기 + 저장 번역)를 컨텍스트에 두고 `|mt`가 화면 문구를 다시 마스킹해 저장 번역을 찾는다. 못 찾으면 마스킹 문구를 `data-mt`에 싣고, 브라우저가 페이지의 토큰 지도로 복원한다.

**Tech Stack:** Python 3.12, FastAPI/Jinja2, Azure Functions v2(Flex Consumption, Cosmos DB 트리거 확장 번들 4.x), azure-cosmos, Bicep, pytest.

**Spec:** `docs/superpowers/specs/2026-11-15-en-pretranslation-design.md`

## Global Constraints

- 번역 모델에는 가명 처리한 문구만 보낸다. 원천을 못 읽은 오류(예외)일 때는 그 owner를 번역하지 않는다(패턴 마스킹으로 대신하지 않는다). 원천이 없을 때(None, 동의 삭제)만 패턴 마스킹을 쓴다.
- `translations` 문서: `id`=마스킹 문구 sha256 hex, `owner_id`, `source`(컨테이너 이름), `en`, `model`, `translated_at`(ISO, KST). 원문은 저장하지 않는다.
- 컨테이너: `translations`(pk `/owner_id`), `leases`(pk `/id`).
- owner: applications·grievances는 `application_id`, loans·alerts는 `loan_id`(= 신청 ID).
- 배치: 25개 또는 8,000자마다 끊는다(`BATCH_ITEMS`·`BATCH_CHARS`). 트리거 시작 시점은 모듈 로드 시각의 7일 전(UTC, 리스가 처음 만들어질 때만 적용). 리스 접두사 `<container>-`.
- 동시성: 컨테이너마다 리스 하나(서버리스 소규모 컨테이너)라 소스별로 직렬 처리된다. 한 호출 안에서도 배치를 순서대로 보낸다(병렬 호출 금지).
- 번역 모델 `TRANSLATE_MODEL`=`gpt-6-luna`, 에이전트 `console-translator`, 게이트웨이 연결 `gw-translator`(50K TPM, 콘솔과 공유).
- 콘솔은 Cosmos 쓰기를 하지 않는다. 원 문서는 트리거가 쓰지 않는다.
- 로그에는 owner·개수·글자 수·토큰 수·결과만 남기고 문구 본문은 남기지 않는다.
- 영어 결과의 토큰 집합이 마스킹 문구의 토큰 집합과 다르면 그 번역을 버린다(저장·캐시·표시 모두).
- 영문 표시 규칙: 한국어 화면 문구에서 평문으로 보이던 값의 토큰만 복원한다(상호는 카탈로그 영문이 있으면 영문). 한국어 화면에서 토큰이던 값(감사자 화면)은 토큰 그대로 둔다.
- 사용자 대면 문구·문서는 한국어. 커밋에는 세션 트레일러 두 줄을 붙인다.

### 설계 대비 결정(빌더 재량, 사용자 확인 대상)

- 설계 5.0은 즉시 번역을 "요청에 owner를 보내고 서버가 복원"이라 했다. 이 계획은 **페이지에 토큰 지도(`<script type="application/json" class="mt-tokens">`)를 싣고 브라우저가 복원**한다. 지도에는 그 사용자가 같은 페이지에서 이미 평문으로 본 값만 들어가므로 노출이 늘지 않고, 서버가 요청마다 Blob 두 개를 다시 읽지 않아도 되며, 사람이 쓴 문구 속 전화번호처럼 패턴 토큰도 복원된다.
- 조회 키는 **화면 문구 재마스킹**으로 만든다(가명처리기는 결정적이고 기존 토큰은 건드리지 않는다).
- 이의자 화면(`/borrower/...`)은 여러 신청이 섞이므로 그 신청들의 엔티티를 합친 가명처리기를 쓴다.

## Review Focus

1. 같은 저장 번역을 심사역과 감사자가 볼 때: 심사역은 상호가 카탈로그 영문(없으면 실명)으로, 감사자는 `[[BIZNAME_…]]` 토큰으로 보여야 한다 → Task 3.
2. 원천 Blob이 없는 신청(동의 삭제)과 Blob 읽기 오류: 없으면 양쪽 모두 패턴 마스킹으로 같은 키를 만들고, 오류면 트리거가 그 owner를 건너뛴다(평문 전송 없음) → Task 1, Task 2.
3. 번역 모델이 토큰을 빼거나 바꾼 결과: 저장하지 않고, 즉시 번역 응답에서는 그 항목을 빈 문자열로 돌려 원문을 유지하고, 이미 저장된 잘못된 번역도 무시한다 → Task 2, Task 3, Task 5.
4. 백필 중 게이트웨이 429·차단: 그 배치만 건너뛰고 나머지 배치·owner는 계속하며 트리거 함수는 예외를 던지지 않는다 → Task 2.
5. 사람이 쓴 문구(이의 내용·심사역 사유)에 전화번호·사업자번호: 모델 입력은 토큰이고, 영문 화면에는 한국어 화면과 같은 번호가 복원돼 보인다 → Task 2, Task 3.

---

## 파일 구조

| 파일 | 책임 |
|---|---|
| `src/shared/pseudonymize.py` (수정) | `tokens(text) -> set[str]` 추가 |
| `src/agents/data_assembly.py` (수정) | `case_entities(profile, txns)` 분리, `build_pseudonymizer`가 사용 |
| `src/agents/pretranslate.py` (신규) | 허용 목록·추출·owner·원천 로드·키·토큰 검사·배치·저장 문서 |
| `src/agents/translator.py` (수정) | 지침의 토큰 예시를 `[[KIND_…]]`로 고침 |
| `src/orchestrator/journey/pretranslate.py` (신규) | 변경 피드 처리(`handle_changes`) |
| `src/orchestrator/journey/activities.py`·`adapters.py`·`function_app.py` (수정) | `Deps.translator`, 빌드, 트리거 등록 |
| `src/console/translate.py` (수정) | `PageTexts`, 페이지 컨텍스트, `_mark` 조회 순서, 토큰 검사 |
| `src/console/app.py`·`adapters.py`·`src/consolepreview/fixtures.py` (수정) | 렌더에 `PageTexts`, 저장 번역 읽기, 즉시 번역 마스킹 |
| `src/console/static/mt.js` (수정) | 토큰 지도 복원, 툴팁은 화면 원문 |
| `infra/resources.bicep` (수정) | 컨테이너 2개, Functions 설정 |

---

### Task 1: 공유 번역 단위 모듈

**Files:**
- Modify: `src/shared/pseudonymize.py`, `src/agents/data_assembly.py:212-224`, `src/agents/translator.py` (INSTRUCTIONS), `src/console/translate.py` (`_batches` 제거·`BATCH_*` 재수출)
- Create: `src/agents/pretranslate.py`
- Test: `tests/agents/test_pretranslate.py`, `tests/agents/test_translator.py`

**Interfaces:**
- Produces:
  - `shared.pseudonymize.tokens(text: str) -> set[str]`: `_TOKEN_RE`로 찾은 완전한 토큰 집합.
  - `agents.data_assembly.case_entities(profile: ApplicantProfile | None, transactions: Sequence[Transaction]) -> dict[str, str]`. `build_pseudonymizer(key, profile, txns) == Pseudonymizer(key, case_entities(profile, txns))`.
  - `agents.pretranslate`:
    - `SOURCES = ("applications", "loans", "alerts", "grievances")`, `BATCH_ITEMS = 25`, `BATCH_CHARS = 8000`
    - `ALLOWLIST: dict[str, tuple[str, ...]]`: 경로 문법은 점 구분 키, `[]`는 리스트의 모든 항목(예: `"assessment.claims[].text"`).
    - `extract(source: str, doc: Mapping[str, Any]) -> list[str]`: 허용 경로의 한국어 문자열, 처음 나온 순서로 중복 제거.
    - `owner_of(source: str, doc: Mapping[str, Any]) -> str | None`
    - `raw_container(consent_id: str) -> str` (`f"consent-{consent_id}"`)
    - `load_case_raw(source, doc, raw: RawStore, read: Callable[[str, str], dict | None]) -> tuple[ApplicantProfile, list[Transaction]] | None`: applications는 `consent.consent_id`의 `profile.json`+`transactions.json`, grievances는 `read("applications", application_id)` 후 같은 규칙, loans는 `underwriting_consent_id`의 `profile.json`+`monitoring.consent_id`의 `flow.json`, alerts는 `read("loans", loan_id)` 후 같은 규칙. 파일·문서가 없으면 None, 저장소 예외는 그대로 올린다.
    - `translation_id(masked: str) -> str` (sha256 hex)
    - `tokens_kept(masked: str, en: str) -> bool`
    - `batches(texts: Sequence[str]) -> Iterator[list[str]]` (기존 `console.translate._batches` 이동)
    - `translation_doc(owner: str, source: str, masked: str, en: str, model: str, at: datetime) -> dict[str, Any]`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_extract_follows_allowlist_and_skips_non_korean():
    doc = {"application_id": "a1", "assessment": {"summary": "매출 안정", "claims": [{"text": "정산 지연"}, {"text": "OK"}],
           "secret": "비허용 경로"}}
    assert extract("applications", doc) == ["매출 안정", "정산 지연"]

def test_owner_by_source():
    assert owner_of("alerts", {"loan_id": "L"}) == "L" and owner_of("grievances", {"application_id": "A"}) == "A"

def test_tokens_kept_detects_dropped_or_changed_token():
    m = "[[BIZNAME_0123456789]] 매출"
    assert tokens_kept(m, "[[BIZNAME_0123456789]] sales")
    assert not tokens_kept(m, "Sales") and not tokens_kept(m, "[[BIZNAME_0123456780]] sales")

def test_load_case_raw_alert_reads_loan_then_flow(): ...       # 가짜 RawStore·read로 alerts → loans → profile+flow 경로 확인
def test_load_case_raw_returns_none_when_blob_missing(): ...    # profile.json 없음 → None
def test_load_case_raw_propagates_store_errors(): ...           # get_json이 OSError → 예외 전파
def test_raw_container_matches_console():
    from console.views import raw_container as console_rc
    assert raw_container("c1") == console_rc("c1")
def test_translation_id_is_sha256_of_masked(): ...
def test_batches_split_by_items_and_chars(): ...               # 26개 → 25+1, 8,001자 → 2배치
```

`tests/agents/test_translator.py`에 `assert "[[BIZNAME_" in INSTRUCTIONS and "[ORG_" not in INSTRUCTIONS` 추가.

- [ ] **Step 2: 실패 확인** — `.\.venv\Scripts\python -m pytest tests/agents/test_pretranslate.py tests/agents/test_translator.py -q` → ImportError/assert 실패.
- [ ] **Step 3: 구현.** `ALLOWLIST` 초기값은 `detail.html`·`loan.html`·`grievance.html`·`borrower.html`의 `|mt`·`|mt_ref` 대상이 원 문서의 어느 경로인지 `views.py`·`monitoring.py`·`opinion`·`assessment` 뷰 모델을 따라가 채운다(완전성은 Task 6에서 검증). 정책 사유 메시지처럼 카탈로그(`locales/en/data.json`)에 있는 문구 경로는 넣지 않는다. 지침의 토큰 줄은 "가명 토큰(`[[BIZNAME_0a1b2c3d4e]]`, `[[PERSON_…]]`, `[[PHONE_…]]` 등)은 글자 하나 바꾸지 말고 그대로 둔다"로 바꾸고, "고유명사(상호)는 로마자로" 문구는 "토큰이 아닌 고유명사만"으로 좁힌다. `console/translate.py`는 `agents.pretranslate.batches`를 쓴다.
- [ ] **Step 4: 통과 확인** — 같은 명령 + `tests/console/test_translate.py tests/agents/test_data_assembly.py` PASS.
- [ ] **Step 5: 커밋** — `feat(agents): 사전 번역 공유 모듈(허용 목록·가명 키·토큰 검사)`

### Task 2: 변경 피드 번역 처리(오케스트레이터)

**Files:**
- Create: `src/orchestrator/journey/pretranslate.py`
- Modify: `src/orchestrator/journey/activities.py:84-103` (`Deps.translator: Any | None = None`, `translate_model: str = TRANSLATE_MODEL`), `src/orchestrator/journey/adapters.py:244-279`, `src/orchestrator/function_app.py`
- Test: `tests/orchestrator/test_pretranslate.py`, `tests/orchestrator/test_function_app.py`

**Interfaces:**
- Consumes: Task 1의 `extract`·`owner_of`·`load_case_raw`·`translation_id`·`tokens_kept`·`batches`·`translation_doc`, `case_entities`, `Deps.store.read/upsert`, `Deps.raw`, `Deps.pseudonym_key`, `Deps.clock`.
- Produces:
  - `journey.pretranslate.TRANSLATIONS = "translations"`
  - `handle_changes(deps: Deps, source: str, docs: Sequence[Mapping[str, Any]]) -> dict[str, int]`: 키 `owners`, `texts`, `translated`, `skipped`(이미 있음), `rejected`(토큰 불일치), `failed`(배치 오류·원천 오류 owner의 문구 수), `input_tokens`, `output_tokens`. 절대 예외를 던지지 않는다.
  - `adapters.agent_settings`가 에이전트가 있으면 `translator=Translator(agents, env.get("TRANSLATE_MODEL", TRANSLATE_MODEL))`, `translate_model`을 채운다. `TRANSLATE_MODEL = "gpt-6-luna"`(`agents/translator.py`).
  - Functions: `CosmosDBConnection__accountEndpoint`가 있을 때만 `pretranslate_<source>` 4개 등록. 바인딩: `connection="CosmosDBConnection"`, `database_name="%COSMOS_DATABASE%"`, `container_name=<source>`, `lease_container_name="leases"`, `lease_container_prefix="<source>-"`, `create_lease_container_if_not_exists=False`, `start_from_time`=로드 시각−7일(UTC, `%Y-%m-%dT%H:%M:%SZ`).

처리 순서(owner별): 문서 묶기 → `load_case_raw`(예외면 그 owner 전부 `failed`, 로그 후 다음 owner) → `Pseudonymizer(key, case_entities(...))`(None이면 `case_entities(None, ())`) → `extract` 후 마스킹·중복 제거 → `store.read(TRANSLATIONS, id, owner)`가 있으면 `skipped` → `batches`마다 `translator.translate`(예외면 그 배치 `failed`) → `en`이 비었거나 `tokens_kept` 거짓이면 `rejected`, 아니면 `upsert`. 마지막에 `log.info("pretranslate source=%s owners=%d texts=%d chars=%d translated=%d skipped=%d rejected=%d failed=%d tokens=%d/%d", ...)`. translator가 None이면 바로 0 보고.

- [ ] **Step 1: 실패하는 테스트 작성** (`InMemoryStore`·가짜 raw·`FakeTranslator`는 기존 `tests/orchestrator/conftest.py` 픽스처를 쓰거나 같은 모양으로 만든다)

```python
def test_translates_masked_text_once_and_stores_by_owner(deps_with_raw):
    doc = app_doc(reason="가나상회 대표 010-1234-5678 확인")       # 프로필 상호 = 가나상회
    report = handle_changes(deps_with_raw, "applications", [doc])
    sent = deps_with_raw.translator.calls[0]
    assert "가나상회" not in sent[0] and "010-1234-5678" not in sent[0] and "[[BIZNAME_" in sent[0] and "[[PHONE_" in sent[0]
    stored = deps_with_raw.store.read("translations", translation_id(sent[0]), doc["application_id"])
    assert stored["en"].startswith("EN(") and stored["source"] == "applications" and "가나상회" not in json.dumps(stored)
    assert handle_changes(deps_with_raw, "applications", [doc])["skipped"] == report["translated"]
    assert len(deps_with_raw.translator.calls) == 1

def test_dropped_token_is_not_stored(...): ...             # 번역기가 토큰을 지움 → rejected=1, 저장 없음
def test_throttled_batch_is_skipped_and_others_continue(...): ...  # 첫 배치 GatewayThrottled, 26개 → failed=25, translated=1, 예외 없음
def test_raw_error_skips_owner_without_calling_model(...): ...     # raw.get_json OSError → failed>0, calls == []
def test_missing_raw_uses_pattern_masking(...): ...       # Blob 없음 → 전화번호만 토큰, 번역·저장됨
def test_alert_uses_monitoring_raw_owner_is_loan(...): ...
def test_logs_have_counts_not_text(caplog, ...): ...      # 원문·마스킹 문구가 로그에 없음
```

`tests/orchestrator/test_function_app.py`: `test_pretranslate_triggers_only_with_cosmos_connection` — 환경 변수가 있으면 4개 함수가 `cosmosDBTrigger`, `containerName`, `leaseContainerName == "leases"`, `leaseContainerPrefix == f"{source}-"`, `connection == "CosmosDBConnection"`, `startFromTime`이 7일 전(±1일)이고, 없으면 등록되지 않음. 기존 `test_function_app_registers_triggers`의 기대 집합은 그대로 통과해야 한다. `test_requirements_pin_runtime_sdks`는 변경 없음.

- [ ] **Step 2: 실패 확인** — `.\.venv\Scripts\python -m pytest tests/orchestrator/test_pretranslate.py tests/orchestrator/test_function_app.py -q` → FAIL.
- [ ] **Step 3: 구현.** 트리거 함수 몸체는 `handle_changes(deps(), source, [d.to_dict() for d in docs])`이며 어떤 예외도 잡아 `log.exception`(본문 없이)으로 남긴다. 루프 안 데코레이터는 소스별 팩토리 함수로 만들어 이름 충돌을 피한다.
- [ ] **Step 4: 통과 확인** — `tests/orchestrator -q` 전체 PASS.
- [ ] **Step 5: 커밋** — `feat(orchestrator): 변경 피드로 데이터 문구 사전 번역`

### Task 3: 콘솔 `PageTexts`와 `|mt` 조회 순서

**Files:**
- Modify: `src/console/translate.py`
- Test: `tests/console/test_translate.py`

**Interfaces:**
- Consumes: `case_entities`, `tokens_kept`, `translation_id`, `shared.pseudonymize.Pseudonymizer`·`tokens`.
- Produces:
  - `class PageTexts(masker: Pseudonymizer, stored: Mapping[str, str] | None = None)`
    - `masked(text: str) -> str`
    - `show(text: str, en: str) -> str`: `tokens(masked) - tokens(text)`인 토큰만 값으로 바꾼다. 값은 `catalog(value) or value`.
    - `stored_en(text: str) -> str | None`: `stored[translation_id(masked)]`이 있고 `tokens_kept`이면 `show` 결과.
    - `pending(text: str) -> str`: 마스킹 문구를 돌려주고 새로 만든 토큰을 토큰 지도에 기록.
    - `token_map() -> dict[str, str]`
    - `@classmethod for_case(key: bytes, profile, transactions, stored=None)`, `@classmethod merged(key: bytes, cases: Iterable[tuple[profile, txns]], stored=None)`(엔티티 합집합)
  - `page_scope(page: PageTexts) -> ContextManager[None]`, `current_page() -> PageTexts | None` (contextvar).
  - `_mark` 조회 순서: 카탈로그 → `page.stored_en` → `translator.lookup(page.masked(text))`(있으면 `page.show`) → 대기(`data-mt=page.pending(text)`, 표시는 원문). 페이지가 없으면 지금처럼 동작하되 `data-mt`는 패턴 마스킹 문구(`Pseudonymizer`는 `mt_filters(translator, key)`로 받은 키 사용).
  - `MachineTranslation.translate`: 토큰이 어긋난 결과는 캐시하지 않고 그 항목에 `""`를 돌려준다(원문 대신). `lookup(masked)`의 키는 마스킹 문구.
  - `mt_filters(translator, key: bytes)` — 시그니처에 `key` 추가.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_stored_translation_restores_only_visible_values(english):
    key = b"k" * 32
    page = PageTexts.for_case(key, profile(business_name="성수데일리룩"), [])   # 카탈로그: Seongsu Daily Look
    masked = page.masked("성수데일리룩 매출 감소")
    page = PageTexts.for_case(key, profile(business_name="성수데일리룩"), [],
                              stored={translation_id(masked): masked.replace("매출 감소", "sales fell")})
    with page_scope(page):
        officer = str(mt_filters(None, key)["mt"]("성수데일리룩 매출 감소"))
        auditor = str(mt_filters(None, key)["mt"](masked))          # 감사자 화면은 원래 토큰
    assert "Seongsu Daily Look sales fell" in officer and 'class="mt mt-done"' in officer
    assert "[[BIZNAME_" in auditor and "Seongsu" not in auditor

def test_stored_translation_with_missing_token_is_ignored(english): ...   # 저장 en에 토큰 없음 → data-mt 대기
def test_pending_carries_masked_text_and_token_map(english):
    # "연락처 010-1234-5678" → data-mt에 [[PHONE_…]], token_map()에 그 토큰 → "010-1234-5678"
def test_machine_translation_rejects_token_loss(): ...    # FakeTranslator가 토큰 삭제 → 결과 "" 이고 캐시 없음
```

기존 `test_mt_filter_marks_by_source`·`test_mt_ref_keeps_sent_korean`은 `mt_filters(machine, KEY)`로 바꾸고 기대값은 그대로(한국어에 PII 없음).

- [ ] **Step 2: 실패 확인** — `pytest tests/console/test_translate.py -q` FAIL.
- [ ] **Step 3: 구현.**
- [ ] **Step 4: 통과 확인.**
- [ ] **Step 5: 커밋** — `feat(console): 영문 렌더가 가명 키로 저장 번역을 먼저 쓴다`

### Task 4: 콘솔 렌더가 저장 번역을 읽는다

**Files:**
- Modify: `src/console/app.py` (`ConsoleDeps.translations`, `render`, `detail`, `loan_page`, `grievance`·`invalid`, `borrower_page`, 템플릿 필터 등록 `mt_filters(machine, deps.pseudonym_key)`), `src/console/adapters.py`, `src/consolepreview/fixtures.py`
- Test: `tests/console/test_translate.py`, `tests/console/test_adapters.py`

**Interfaces:**
- Consumes: Task 3의 `PageTexts`·`page_scope`.
- Produces:
  - `ConsoleDeps.translations: TranslationReader | None = None`; `TranslationReader.for_owner(owner: str) -> dict[str, str]` (`id → en`).
  - `adapters.CosmosTranslations(container).for_owner(owner)`: `SELECT c.id, c.en FROM c`, `partition_key=owner`. `build_deps`가 `translations` 컨테이너로 만든다.
  - 픽스처 `InMemoryTranslations(items: dict[str, dict[str, str]])`.
  - `render(name, user, status_code=200, texts: PageTexts | None = None, **ctx)`: 영문일 때 `texts`(없으면 패턴 전용 `PageTexts`)로 `page_scope` 안에서 렌더하고, `token_map()`이 비어 있지 않으면 `<script type="application/json" class="mt-tokens">{json}</script>`를 `</body>` 앞(없으면 끝)에 붙인다. JSON은 `<`·`>`·`&`를 `\u003c` 등으로 이스케이프한다.
  - 라우트별 `PageTexts`: detail = `for_case(load_raw 결과)` + `for_owner(application_id)`; loan = `for_case(load_monitoring_raw 결과)` + `for_owner(loan_id)`; grievance = 그 신청 문서의 `load_raw` + `for_owner(application_id)`; borrower = 신청별 `load_raw`·`load_monitoring_raw`를 `merged`로 + 각 owner의 저장 번역 합집합.
  - 한국어 화면에서는 `for_owner`를 부르지 않는다. `for_owner` 예외는 `log.warning`(owner만) 후 빈 dict.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_english_detail_uses_stored_translation_without_browser_call(mt):
    client, deps = mt
    page = "/applications/app-20261103-p01"
    pending = re.findall(r'data-mt="([^"]*)"', client.get(page, headers={"Cookie": "lang=en"}).text)
    deps.translations = fixtures.InMemoryTranslations({"app-20261103-p01": {translation_id(html.unescape(t)): f"EN({html.unescape(t)})" for t in pending}})
    again = TestClient(create_app(deps)).get(page, headers={"Cookie": "lang=en"}).text
    assert "data-mt=" not in again and deps.translator.calls == []

def test_korean_page_does_not_read_translations(mt): ...      # for_owner 호출 0회
def test_auditor_sees_tokens_in_stored_translation(mt): ...   # demo_principal 역할 Auditor → "[[BIZNAME_" 보임, 실명 없음
def test_translation_read_failure_falls_back_to_pending(mt): ...  # for_owner가 예외 → 200, data-mt 있음
def test_token_map_script_lists_only_pending_tokens(mt): ...  # mt-tokens JSON 키가 data-mt 안 토큰의 부분집합
def test_cosmos_translations_queries_single_partition(): ...  # tests/console/test_adapters.py, 가짜 컨테이너로 partition_key 확인
```

- [ ] **Step 2: 실패 확인.**
- [ ] **Step 3: 구현.**
- [ ] **Step 4: 통과 확인** — `pytest tests/console -q` PASS(기존 `test_english_pages_leave_only_pending_machine_translation` 포함).
- [ ] **Step 5: 커밋** — `feat(console): 영문 상세·대출·이의 화면이 저장 번역을 읽는다`

### Task 5: 즉시 번역 경로를 가명 문구로

**Files:**
- Modify: `src/console/app.py` (`/i18n/translate`), `src/console/static/mt.js`
- Test: `tests/console/test_translate.py`

**Interfaces:**
- Consumes: Task 3의 `MachineTranslation`(토큰 검사, 실패 항목 `""`).
- Produces:
  - `/i18n/translate`: 받은 `texts`를 `Pseudonymizer(deps.pseudonym_key).mask`(패턴만)로 한 번 더 마스킹해 번역한다. 정상 클라이언트에서는 변화가 없다. 응답 `translations[i]`는 마스킹 문구의 영어(토큰 포함) 또는 `""`.
  - `mt.js`: 문서의 모든 `script.mt-tokens`(교체된 조각 포함)를 합친 지도로 응답의 토큰을 바꾼 뒤 `textContent`로 넣는다. `""`이면 원문을 둔다. 인라인 툴팁의 원문은 바꾸기 전 노드의 `textContent`(화면 원문)다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_translate_endpoint_masks_pii_before_model(mt):
    client, deps = mt
    r = _post(client, deps, {"texts": ["연락처 010-1234-5678 확인"]})
    assert r.status_code == 200 and "010-1234-5678" not in json.dumps(deps.translator.calls)
    assert "[[PHONE_" in r.json()["translations"][0]

def test_translate_endpoint_blanks_token_loss(mt): ...   # 번역기가 토큰 삭제 → translations == [""]
```

기존 `test_translate_endpoint_fills_cache_for_next_render`는 `data-mt`(이제 마스킹 문구)를 그대로 보내도 통과해야 한다.

- [ ] **Step 2: 실패 확인.**
- [ ] **Step 3: 구현.**
- [ ] **Step 4: 통과 확인** — `pytest tests/console/test_translate.py -q` PASS. `mt.js`는 Task 9 라이브 확인에서 브라우저로 본다(JS 테스트 도구 없음).
- [ ] **Step 5: 커밋** — `fix(console): 즉시 번역도 가명 문구만 보낸다`

### Task 6: 허용 목록 커버리지 시험

**Files:**
- Create: `tests/console/test_pretranslate_coverage.py`
- Modify: `src/agents/pretranslate.py` (`ALLOWLIST` 보완)

**Interfaces:**
- Consumes: 미리보기 픽스처의 문서·raw 저장소, Task 1의 추출·로드, Task 2의 `handle_changes`(픽스처 문서를 `InMemoryStore` 모양으로 감싸 호출하거나, 같은 단계를 `agents.pretranslate`만으로 재현), Task 4의 `InMemoryTranslations`.

- [ ] **Step 1: 시험 작성** — 픽스처의 모든 applications·loans·alerts(연락 기록 포함)·grievances 문서를 사전 번역(`FakeTranslator`: `EN(<masked>)`)해 `InMemoryTranslations`를 채운 뒤, `EN_PAGES`와 `/borrower/seller-dailylook-01`을 영문으로 렌더해 `data-mt=` 수가 0인지 본다. 콘솔이 조립해 원 문서에 그대로 없는 문구가 있으면 `COMPOSED: dict[str, str]`(문구 → 이유)에 적고 시험은 남은 대기 문구 집합이 `COMPOSED`의 마스킹 결과와 같은지 본다.
- [ ] **Step 2: 실행해 빠진 경로 확인** — `pytest tests/console/test_pretranslate_coverage.py -q` → 남은 `data-mt` 목록이 출력되게 assert 메시지를 쓴다.
- [ ] **Step 3: `ALLOWLIST` 보완** — 남은 문구마다 원 문서 경로를 찾아 추가한다. `COMPOSED`는 최소화하고 항목마다 이유를 쓴다.
- [ ] **Step 4: 통과 확인** — 위 시험과 `tests/agents/test_pretranslate.py` PASS.
- [ ] **Step 5: 커밋** — `test(console): 사전 번역 허용 목록 커버리지`

### Task 7: 인프라

**Files:**
- Modify: `infra/resources.bicep:277-283`(컨테이너), `:542-569`(Functions 설정)
- Test: `tests/infra/test_data.py`, `tests/infra/test_functions.py`

**Interfaces:**
- Produces: 컨테이너 `{ name: 'translations', partitionKey: '/owner_id' }`, `{ name: 'leases', partitionKey: '/id' }`; Functions 설정 `TRANSLATE_MODEL: 'gpt-6-luna'`, `CosmosDBConnection__accountEndpoint: cosmos.outputs.endpoint`, `CosmosDBConnection__credential: 'managedidentity'`, `CosmosDBConnection__clientId: identities.outputs.orchestratorClientId`. 역할은 추가하지 않는다(오케스트레이터 Data Contributor·콘솔 Data Reader가 데이터베이스 범위).

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_cosmos_has_translation_and_lease_containers(closed):
    pks = {c["name"].split("/")[-1]: c["properties"]["resource"]["partitionKey"]["paths"]
           for c in closed.of_type(f"{COSMOS}/sqlDatabases/containers")}
    assert pks["translations"] == ["/owner_id"] and pks["leases"] == ["/id"]

def test_function_app_has_cosmos_trigger_identity_connection(closed):
    s = _settings(closed.one(SITE, "func-"))
    assert s["CosmosDBConnection__credential"] == "managedidentity" and s["TRANSLATE_MODEL"] == "gpt-6-luna"
    assert s["CosmosDBConnection__accountEndpoint"].startswith("https://") and s["CosmosDBConnection__clientId"]
```

- [ ] **Step 2: 실패 확인** — `$env:RUN_INFRA_TESTS=1; pytest tests/infra/test_data.py tests/infra/test_functions.py -q` FAIL.
- [ ] **Step 3: 구현.**
- [ ] **Step 4: 통과 확인** — `RUN_INFRA_TESTS=1`로 `tests/infra -q` 전체 PASS.
- [ ] **Step 5: 커밋** — `feat(infra): translations·leases 컨테이너와 변경 피드 연결`

### Task 8: 문서

**Files:** `docs/agents.md`("## 영문 화면 번역": 사전 번역·가명 원칙·토큰 지도), `docs/architecture.md:39`(변경 피드 → translations 흐름 한 줄), `docs/limitations.md`(동의 삭제 후 패턴 마스킹만, 콘솔 조립 문구는 즉시 번역, 저장 지연 수십 초), `src/console/locales/README.md`(조회 순서 4단계), `docs/security/threat-model.md`(번역 모델 입력은 가명 문구, 토큰 지도 범위).

- [ ] **Step 1: 갱신** — 표 셀 안의 `|`는 `\|`로 쓴다.
- [ ] **Step 2: 전체 시험** — `.\.venv\Scripts\python -m pytest tests -q`(약 4.5분) PASS, `RUN_INFRA_TESTS=1`로 `tests/infra -q` PASS.
- [ ] **Step 3: 커밋** — `docs: 영문 화면 사전 번역`

### Task 9: 배포·라이브 확인(사용자 승인 후)

- [ ] **Step 1: 승인 요청** — 배포와 푸시(`a04b526` 포함)를 각각 묻는다.
- [ ] **Step 2: 배포** — `python -m ops window open --acr`(프로비저닝으로 컨테이너·Functions 설정 반영; 반영 안 되면 `docs/runbook`의 프로비저닝 절차를 따른다) → `ops deploy orchestrator` → `ops deploy console` → `ops smoke`. `agw-amalend`가 Stopped면 시작한다.
- [ ] **Step 3: 백필 확인** — Functions 로그(`traces | where message startswith "pretranslate"`)에서 4개 소스의 `translated`·`failed` 합계를 본다. `failed`가 크면 429 비율을 확인하고 남은 것은 콘솔 즉시 번역에 맡긴다.
- [ ] **Step 4: 화면 확인** — `/applications/app-20261114-170e2a?lang=en`, `/loans/app-20261113-59e115?lang=en`의 HTML에 `data-mt=`가 0인지 curl로 확인하고, 브라우저에서 심사역 화면의 상호가 영문/실명으로 보이는지 본다. 새 신청 1건을 넣어 1분 안에 영문으로 렌더되는지 본다.
- [ ] **Step 5: 마무리** — `window close`, `docs/runbook/deploy-history.md`에 항목 추가·커밋, 승인 받은 경우 푸시.
