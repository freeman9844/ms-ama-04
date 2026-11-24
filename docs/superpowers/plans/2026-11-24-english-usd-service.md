# 영어·USD 대출 서비스 구현 계획

> **실행 담당자:** `superpowers:executing-plans` 또는 `superpowers:subagent-driven-development`로 아래 체크리스트를 실행한다. 이 문서의 작성은 배포 승인이 아니다.

**목표:** 영어·USD로 원천 데이터부터 상환까지 작동하는 신규 상품을 운영 기본값으로 제공하고 기존 KRW 대출은 별도 기록으로 보존한다.

**구조:** 한 서비스가 버전이 명시된 두 계약을 읽는다. 새 데이터는 센트와 영어 원문으로 생성한다. 소비자를 먼저 배포하고 마지막에 신규 신청 경로를 연다.

**기술:** Python, Pydantic, pytest, FastAPI/Jinja, Azure Durable Functions, Cosmos DB, Blob, 큐, Foundry, Container Apps.

**설계:** [승인된 영어·USD 설계](../specs/2026-11-24-english-usd-service-design.md)

## 공통 제약

- USD 대표 $5,000·6개월, 최소 $100, 최대 $10,000, 기간 3~6개월. 정책 한도만 $100 단위 내림. 거래·상환은 센트 단위.
- 신규 생성 언어 `en`, 시장은 한국, 시간대 KST. 명시적 한국어 UI 선택은 통화나 생성 언어를 바꾸지 않는다. 신규 USD에 번역 서비스 의존 금지.
- 기존 문서·링크·감사 기록 보존. 통화별 조회·집계, 동일 차주 연락 보호는 통화 간 합산. 예외 승인이나 자동 최종 결정 추가 금지.

## 집중 검토할 실패 조건

| 조건 | 기대 결과 | 담당 |
|---|---|---|
| 예전 큐 메시지가 전환 후 재전달됨 | 원래 KRW 계약으로 멱등 처리 | 1, 4 |
| 같은 셀러·날짜에 USD와 KRW 거래 존재 | 한쪽 입금이 다른 쪽 정산·상환에 배정되지 않음 | 2, 4 |
| 영어 문구에 센트 금액·가명·감액 제안 혼재 | 근거 금액만 사용하고 금지 제안·식별자는 노출하지 않음 | 3 |
| 최근 원화 기록 수가 기본 목록 제한보다 많음 | USD 목록이 비어 보이지 않고 KPI도 USD만 집계 | 5 |
| 기존 한국어 쿠키 또는 모델 장애 | 첫 방문 영어, 이후 명시적 언어 선택 유지, 신규 실패 안내도 영어 | 3, 5 |

아래 실행 명령의 `pytest`는 저장소 루트에서 `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider`를 뜻한다. 각 단계는 테스트 추가 → 실패 확인 → 구현 → 같은 명령 통과 → 해당 변경만 커밋 순서다.

### Task 1: 금액 계약과 입력 경계

**파일:** 신규 `src/shared/money.py`, `tests/shared/test_money.py`; 수정 `src/shared/contracts.py`, `src/orchestrator/journey/models.py`, `src/orchestrator/journey/validation.py`, `src/console/customer.py`.

- [ ] `test_usd_input_bounds`에서 `parse_usd_cents("5,000.01") == 500001`, `100.00`·`10000.00` 허용, `99.99`·`10000.01`·`1.001`·음수 거부를 검증한다. `test_product_version_drives_currency`에서 기존 메시지와 통화 누락 신규 메시지의 결과가 다른지 확인한다.
- [ ] `pytest tests/shared/test_money.py`를 실행해 신규 계약 부재로 실패하는지 확인한다.
- [ ] `parse_usd_cents(text: str) -> int`와 `format_usd(cents: int) -> str`를 구현한다. 도메인 경계에서 `product_version="usd-v1"`, `currency="USD"`, `language="en"`과 `*_cents`를 검증한다. 기존 `*_krw` 입력은 구버전 판독기로만 처리한다. 두 표현을 함께 받지 않는다.
- [ ] 위 테스트와 기존 큐 계약 검증을 실행한다. 통과 후 `feat: introduce versioned USD money contracts`로 커밋한다.

### Task 2: 합성 원장과 결정적 정책

**파일:** `src/shared/sellers.py`, `src/mockapis/synth.py`, `src/mockapis/sellerdata/engine.py`, `src/mockapis/postloan.py`, `src/agents/classify.py`, `src/agents/pg_ledger.py`, `src/agents/features.py`, `src/policy/engine.py`, `src/policy/rules.py`; 신규 `src/policy/rulesets/usd-v1.yaml`, `tests/mockapis/test_usd_sellers.py`.

- [ ] `test_usd_seller_paths`에서 네 셀러와 고정 시드로 적격·부적격(R003)·회부(R107)·승인 후 스트레스 경로를 단언한다. 같은 지급/입금은 같은 센트, 1센트 다르면 미대사, 동일 셀러의 KRW 입금은 USD 매칭에서 제외되는 사례를 넣는다.
- [ ] `pytest tests/mockapis/test_usd_sellers.py`로 기존 한국어·원화 생성기에서 실패를 확인한다.
- [ ] 셀러 식별자는 유지하고 신규 `usd-v1` 원천에 영문 명칭·거래 적요·일관된 플랫폼 코드를 저장한다. 원천과 캐시에 상품을 구분한다. 기존 원장 환산 없이 거래를 생성한다. 정책 범위 `10000..1000000`센트, 한도 절삭 `10000`센트, 이력 대체 상한 `500000`센트. 비율·기간은 v4 기준을 유지한다.
- [ ] `pytest tests/mockapis/test_usd_sellers.py tests/mockapis/test_policy_v4_sellers.py tests/agents/test_pg_ledger.py` 통과 후 `feat: generate English USD ledgers and policy decisions`로 커밋한다.

### Task 3: 생성 영어, 개인정보와 근거 검증

**파일:** `src/shared/pseudonymize.py`, `src/shared/amount_advice.py`, `src/agents/underwriting.py`, `src/agents/early_warning.py`, `src/agents/collections.py`, `src/agents/notices.py`, `src/agents/officer_facts.py`, `src/agents/officer_chat.py`, `src/orchestrator/journey/pretranslate.py`; 신규 `tests/agents/test_usd_language.py`.

- [ ] `test_usd_evidence_not_alternative_offer`는 “Verify the $700.01 deposit”은 허용하고 “Offer $300 instead”는 거부한다. `test_english_output_without_translator`는 오류·고정 템플릿·저장 문안·표시 금액 및 가명 경계를 검사한다.
- [ ] `pytest tests/agents/test_usd_language.py` 실패 확인 후, 프롬프트·도구 인자·결과 검사·폴백 템플릿에 상품 문맥을 전달한다. 모델은 통화를 결정하지 않는다. 잘못된 한국어 생성은 기존 검토/실패 경로로 보내고 검증 없는 영문을 새 의견으로 만들지 않는다. USD 신규 원문에는 번역 트리거를 실행하지 않는다. 인간 입력은 감사 원문을 유지한다.
- [ ] 신규 테스트와 기존 심사역 채팅·동의 안내 테스트를 통과시킨 후 `feat: produce grounded English USD case records`로 커밋한다.

### Task 4: 승인 이후 상환과 보호 규칙

**파일:** `src/agents/repayment.py`, `src/agents/repayment_plan.py`, `src/agents/escalation.py`, `src/orchestrator/journey/activities.py`, `src/orchestrator/journey/monitoring.py`, `src/agents/forecast.py`, `tests/agents/test_repayment.py`, `tests/orchestrator/test_monitoring_handlers.py`.

- [ ] `test_usd_fifo_cent_and_remainder`: $500 회차에 1센트 납부 시 잔액 `49999`, 중복 거래 재전달 시 불변, 분할 합계는 원금과 동일. `test_contact_cap_across_currencies`: 동일 차주의 7일 연락 한도를 통화 분리로 우회할 수 없다.
- [ ] `pytest tests/agents/test_repayment.py tests/orchestrator/test_monitoring_handlers.py`에서 신규 실패를 확인한다.
- [ ] 신청의 계약을 일정·출금·전망·알림·추심 문서까지 전파한다. 신규 기록은 센트로 FIFO 충당한다. 잔액 0 판정과 조건부 발송 쓰기는 기존 원자성 규칙을 보존한다. 연락 횟수만 통화 간 연계하고 금융 금액은 합산하지 않는다. 구버전 진행 중 오케스트레이션의 재생 순서와 활동 계약은 변경하지 않는다.
- [ ] 위 테스트와 구버전 승인 후 재전달 테스트 통과 후 `feat: service USD loans without mixing legacy balances`로 커밋한다.

### Task 5: 기본 화면, KRW 기록 조회, 지표

**파일:** `src/console/i18n.py`, `src/console/app.py`, `src/console/dashboard.py`, `src/console/customer.py`, `src/console/monitoring.py`, `src/console/translate.py`, `src/console/templates/`, `src/console/locales/en/`, `src/opsmetrics/projection.py`, `src/opsmetrics/insights.py`; `tests/console/test_i18n.py`, 신규 `tests/console/test_usd_views.py`.

- [ ] `test_first_visit_and_explicit_locale`: 한국 브라우저·구 쿠키는 영어, 전환 후 명시적 한국어 선택은 유지. `test_archive_before_limit`: KRW 100건 뒤 USD 1건이 있어도 목록·합계에는 USD 1건, 아카이브에는 KRW 100건만 표시. 구 링크의 승인액은 원화.
- [ ] `pytest tests/console/test_i18n.py tests/console/test_usd_views.py`에서 실패 확인. 메시지 원문의 언어와 기본 UI 언어를 분리한다(`DEFAULT` 한 줄만 바꾸면 기존 gettext가 영어 카탈로그를 건너뛴다).
- [ ] 처음 접속은 영어, 명시적 선택은 새 쿠키로 기록한다. 고객·심사역 입력, 상태, 통지, 오류, 차트, 빈 상태에 카탈로그와 통화별 포매터를 적용한다. 아카이브 진입점은 `/legacy`, 기존 식별자 링크는 유지한다. 저장소 쿼리에서 통화를 먼저 제한하고 그 뒤 정렬·페이지 제한·집계한다. 동일 셀러의 신규 이의·운영 지표에도 적용한다.
- [ ] 번역기를 끈 USD 화면과 기존 KRW 직접 링크를 포함해 위 테스트 통과 후 `feat: default to English USD views with a KRW archive`로 커밋한다.

### Task 6: 실제 서비스 전환과 판정 기준

**파일:** `infra/resources.bicep`, `docs/runbook/deploy-history.md`; 실행 경계는 신청 접수 플래그 `USD_ORIGINATION_ENABLED`(기본 꺼짐). 이미 저장된 신청의 처리에는 이 플래그를 사용하지 않는다.

- [ ] `test_usd_origination_gate`를 `tests/console/test_usd_views.py`에 추가한다. 게이트가 닫혔을 때 USD 쓰기 불가, 이미 접수한 USD 및 기존 KRW 조회·처리 가능. 실패 확인 후 접수 진입점과 배포 설정에 게이트를 연결한다.
- [ ] 모든 독립 검증 및 `pytest` 전체 결과를 기록한다. 큐·Functions·모의 API·정책·콘솔 모두 새 계약을 읽는 상태에서 격리된 USD 여정을 실행한다. 기존 KRW 심사 대기, 기존 링크, 역할별 원문 노출·동의 철회·삭제는 합성 복제 데이터로 회귀 검사한다.
- [ ] 배포 실행 승인을 받은 뒤 소비자부터 배포하고 마지막에 USD 접수를 연다. 실제 Azure에서 같은 네 셀러에 $5,000·6개월 각 1건만 제출한다. 목록뿐 아니라 영어 원천·근거·실제 결정액·상환 기록을 확인한다. 결정/고객 대기는 완료와 구별하고 중복 실행하지 않는다.
- [ ] 구버전 링크와 전환 전 대시보드 합계의 KRW 보존, 신규 KPI 격리를 확인한다. 실패 시 USD 신규 접수만 차단하고 두 세대를 읽는 소비자는 유지한다. 신청 번호·기대/관측 차이·배포 리비전을 기록하고 `docs: record verified USD cutover`로 마친다.
