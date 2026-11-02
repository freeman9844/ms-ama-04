# P6a 평가 하네스 — 설계 Spec

- 날짜: 2026-11-01
- 상위 Spec: `2026-10-31-agentic-cashflow-lending-design.md` §5(평가 하네스), §4.3(공정성), §8(루브릭 A6)
- 범위: P6 3분할 중 첫 단계. P6b(운영 지표·공정성 대시보드·SLO·장애 주입), P6c(문서·ADR·데모 대본)는 별도 Spec입니다.

---

## 1. 목적과 성공 기준

### 1.1 목적

루브릭 A6(평가)와 B7(근거 없는 주장 상한)에 대응해, 에이전트 품질·안전·비용을 **실제 Azure 모델로 측정한 숫자**로 남깁니다. 모든 숫자는 "합성 데이터 기준"으로 표기합니다.

### 1.2 사용자 결정 사항

| 항목 | 결정 |
|---|---|
| 실행 위치 | VNet 안 Container Apps Job(`job-eval`). 로컬 CLI 실행은 하지 않음 |
| 평가 방식 | 컴포넌트 평가: 에이전트 코드를 직접 호출하고 모델 호출만 실제 Azure. 배포된 여정 E2E 평가는 제외 |
| 평가기 | 자체 지표 + 자체 LLM-judge(gpt-5.4, groundedness·relevance·coherence 1~5점). `azure-ai-evaluation`은 설치 실패로 쓰지 않음(§9). 포털 업로드는 하지 않음 |
| 규모 | 소규모: 분류 신청 8건(규칙 미분류 그룹 + 어려운 적요 뱅크), 심사 8건 × 2모델, 조기경보 6경로 × 13주 × 2모델. 약 20분, 약 $3 |
| 모델 비교 | 분류: Decision-1 / gpt-5.4-mini / gpt-5.4. 심사 analyst·조기경보 investigator: gpt-5.4 / mini |
| 리포트 회수 | Log Analytics 재조립 + Blob 원본 보관. 데이터 스토리지(stdat) 네트워크는 열지 않음 |
| 공정성 분담 | P6a는 반사실 쌍 테스트. 운영 데이터 기준 세그먼트별 승인율 격차 대시보드는 P6b |

### 1.3 성공 기준

1. `ops eval --suite all`이 실제 Job을 한 번 끝까지 돌리고 `eval/reports/<run-id>/`에 `summary.json`, `report.md`, `calls.jsonl`을 남깁니다.
2. 상위 Spec §5 표의 지표가 모두 리포트에 숫자(또는 "측정 불가" 사유)로 나옵니다.
3. 리포트에 실패 유형 카탈로그(유형, 사례, 대응)가 있습니다.
4. 실측 비용이 예산 가드(`EVAL_BUDGET_USD`, 기본 $5) 안에 들어옵니다.
5. `python -m evals --fake`가 로컬에서 같은 형식의 리포트를 만들고, 이 경로가 pytest 회귀 테스트로 돕니다.
6. README에 실측값, 소요 시간, 비용을 "합성 데이터 기준"과 함께 기록합니다.

---

## 2. 아키텍처

### 2.1 접근안 선택

| 안 | 내용 | 판단 |
|---|---|---|
| 1. 컴포넌트 평가 Job | 평가 이미지가 에이전트 함수를 직접 호출. Policy·Mock·저장소는 프로세스 안 메모리 | **채택.** 모델 교체가 설정값이라 비교가 쉽고, 운영 Cosmos를 오염시키지 않음 |
| 2. 배포 여정 E2E | 큐에 합성 신청 투입, Cosmos에서 결과 수집 | 모델 교체마다 재배포, 느림, 운영 데이터 오염 |
| 3. 로컬 + 정기 Job | 로컬 개발 실행, Job은 회귀용 | 사용자 결정(VNet Job)과 다름. `--fake` 로컬 실행만 테스트용으로 유지 |

한계: 컴포넌트 평가는 전 구간 지연(큐·Durable 오버헤드)을 재지 않습니다. 전 구간 SLO는 P6b에서 App Insights로 측정합니다.

### 2.2 위치 결정

상위 Spec §6.2는 `eval/`에 하네스·데이터셋·리포트를 둔다고 했습니다. 패키징(`pyproject.toml`의 `packages`)과 `pythonpath = src` 관례를 따르기 위해 다음처럼 나눕니다.

- 코드: `src/evals/` (패키지명 `evals`. 내장 함수 `eval`과 이름이 겹치지 않게 합니다.)
- 단가표: `src/evals/prices.json`(이미지에 함께 들어가야 하므로 패키지 안에 둡니다).
- 결과: `eval/reports/<run-id>/`.
- 데이터셋은 시드로 결정적으로 생성하므로 파일로 커밋하지 않고, 리포트에 시드와 분포 요약을 남깁니다.

### 2.3 구성 요소

| 모듈 | 역할 |
|---|---|
| `evals/datasets.py` | `mockapis.synth`로 라벨 평가셋 생성(분류 거래, 심사 케이스, 조기경보 경로, 반사실 쌍). 시드 고정 |
| `evals/metering.py` | httpx 전송 계층에서 호출 단위 기록(suite, case, 모델, 토큰, 지연, 재시도, 상태, PII 적중). 429·5xx 재시도, 단가표로 비용 계산, 예산 가드 |
| `evals/runner.py` | 평가용 에이전트 이름(`eval-<이름>-<모델>`)으로 바꿔 운영 에이전트 버전을 건드리지 않음. 실행 후 대화·컨테이너 정리 |
| `evals/backend.py` | 실제 Azure(관리 ID) 또는 가짜 HTTP 에뮬레이터(`evals/fake.py`). 둘 다 같은 계측 transport를 거침 |
| `evals/metrics.py` | 순수 함수: macro F1, 정확도, precision/recall, 리드 일수 분포, p50/p95 |
| `evals/suites/classify.py` | `agents.txn_classify.classify_unresolved`를 3구성(Decision-1+mini 폴백 / mini만 / gpt-5.4만)으로 실행 |
| `evals/suites/underwriting.py` | `agents.underwriting.assess`(analyst+reviewer)를 모델별 실행. Policy는 `policy.engine.evaluate` 직접 호출 |
| `evals/suites/early_warning.py` | 운영 `process_week`와 같은 주차 처리(규칙 분류 → `agents.monitoring.detect` → 새 트리거만 `investigate`)를 경로별 13주 재생. 탐지기 단독 기준선도 함께 계산 |
| `evals/suites/safety.py` | 위 suite 입출력 재사용: 주입 방어율, PII 누출률, 반사실 공정성 |
| `evals/judge.py` | 자체 LLM-judge(`quality-judge` 에이전트, gpt-5.4). 심사 의견서를 근거 컨텍스트에 비춰 groundedness·relevance·coherence를 1~5점으로 채점 |
| `evals/report.py` | `summary.json`, `report.md`(실패 유형 카탈로그, 사람 표본 검토 섹션, "합성 데이터 기준" 표기), `calls.jsonl` 생성 |
| `evals/transport.py` | 리포트 파일을 gzip+base64 청크로 stdout 출력, Blob REST(Put Blob) 업로드. 회수 측 재조립·sha256 검증 |
| `evals/__main__.py` | `python -m evals [--suite all\|classify\|underwriting\|early_warning\|safety] [--fake] [--out DIR] [--budget USD] [--emit]` |
| `ops` 추가 | `ops deploy eval`(이미지 빌드·Job 갱신), `ops eval [--suite ...]`(Job 시작·대기·리포트 회수, `ops/eval_runner.py`) |

- 에이전트 코드는 고치지 않습니다. 기존 주입 지점(runner, decider, fallback, model 인자)만 씁니다.
- `src/orchestrator`는 이미지에 넣지 않습니다. 필요한 순수 함수가 orchestrator에만 있으면 scratch 단계에서 `shared`나 `agents`로 옮길지 판단하고, 옮길 때는 기존 테스트로 동작 보존을 확인합니다.

---

## 3. 지표와 데이터

### 3.1 데이터셋

| 데이터셋 | 구성 | 라벨 |
|---|---|---|
| 분류 거래 | 4개 아키타입 × 시드 2개(신청 8건). 규칙(classify-v1)이 못 푸는 거래처 이체에 더해, 규칙 키워드에 걸리지 않는 어려운 적요 뱅크(라벨마다 4개, 일부러 모호한 것 포함)를 아키타입별로 나눠 넣음. 같은 적요를 두 번 넣어 신청 안 중복 제거와 캐시 적중을 확인 | 분류기 라벨(`TxnCategory` 정답을 분류기 선택지로 사상. 매출·대출금 입금은 선택지 밖이라 제외) |
| 심사 케이스 | 8건: 아키타입 4개 × (일반, 프롬프트 주입) | 아키타입, Policy 판정, TAX_MISMATCH 여부, 주입 여부 |
| 조기경보 경로 | 6개: 아키타입 3개(HEALTHY_SEASONAL, TAX_MISMATCH, STRESSED) × 사후 흐름 시나리오 2개(STACKING=양성, STABLE=음성). 경로당 13주 재생 | 양성 여부와 라벨 연체일(`default_on`) |
| 반사실 쌍 | 주입 없는 심사 케이스 2건(HEALTHY_SEASONAL, TAX_MISMATCH)의 재무 데이터는 그대로 두고 지역·업종만 다른 페르소나 값으로 바꾼 쌍. gpt-5.4 analyst로만 실행 | 결론과 등급이 같아야 함 |

### 3.2 지표

| Suite | 지표 |
|---|---|
| 분류 | macro F1, 정확도(고유 적요 그룹 단위 + 거래 가중), 신뢰도 임계값별(0.5~0.9) 정확도와 fallback 비율, 규칙 처리 비율, 캐시 적중률, 신청당 모델 호출 수, 분류 1건당 비용 |
| 심사 | 근거 인용률(`check_grounding` 통과 비율), 세무 대사 불일치 탐지율, Policy 판정과의 결론 일치율, reviewer 라운드 수, LLM-judge 점수(groundedness·relevance·coherence) |
| 조기경보 | precision, recall(경로 단위), 리드 일수 분포(라벨 연체일 − 첫 경보일), 오탐 경로. 비교 기준선으로 "탐지기 단독"(트리거가 하나라도 걸리면 경보) 값을 함께 표기 |
| 안전 | 주입 방어율(주입 거래가 있는 케이스에서 승인 유도에 따르지 않은 비율), PII 누출률(모델 입력·출력에 원본 PII가 남은 건수/전체), 반사실 공정성(쌍 간 결론·등급 불일치 수) |
| 공통(모델 비교) | 모델별 p50/p95 지연, 입력·출력 토큰, 건당 비용 |

- 단가는 `src/evals/prices.json`에 모델별로 두고 출처와 확인 날짜를 적습니다. 공개 단가가 없는 모델(Decision-1)은 "단가 미확인"으로 표시하고 토큰 수와 호출 수만 보고합니다. 비용 합계와 예산 가드에는 단가를 아는 호출만 들어갑니다.
- 사람 표본 검토: `report.md`에 심사 설명 3건과 빈 평가 칸을 넣고, 사용자가 채운 결과를 README에 옮깁니다.

### 3.3 예산

거래 그룹 × 3구성, 심사 8건 × 2모델(+ reviewer), 반사실 2건, 조기경보 조사(새 트리거 주차만) × 2모델, judge 16회. 목표는 약 20분, 약 $3이고, 가드는 $5입니다(Decision-1 호출은 단가 미확인이라 가드에 들어가지 않음).

---

## 4. 인프라와 실행

### 4.1 이미지와 Job

- `src/evals/Dockerfile`: `shared`, `policy`, `mockapis`, `agents`, `evals`를 복사하고 `requirements-eval.txt`(pydantic, PyYAML, httpx, azure-identity, 버전 고정)를 설치합니다. Blob은 REST로 올리므로 Storage SDK는 넣지 않습니다.
- `ops deploy eval`: 기존 `deploy_images`와 같은 방식으로 `az acr build` 후 `SERVICE_EVAL_IMAGE_NAME` azd env를 설정하고 provision합니다. ACR 창(`window open --acr`)이 필요합니다. `ops deploy all`에도 포함됩니다.
- `infra/modules/eval-job.bicep`:
  - `job-eval`, 내부 ACA 환경(VNet), 수동 트리거, 1 CPU / 2Gi, `replicaTimeout` 3600초, 재시도 0.
  - `SERVICE_EVAL_IMAGE_NAME`이 없으면 자리표시 이미지로 배포합니다(다른 서비스와 같은 패턴).
  - 환경 변수: OpenAI·Foundry 엔드포인트, 모델 배포 이름(운영과 같은 analyst gpt-5.4·mini·reviewer mini·decision-1), judge 모델, `EVAL_BUDGET_USD`=5, `EVAL_EMIT_LOGS`=1, 리포트 Blob 엔드포인트·컨테이너, managed identity client id.
- 신규 managed identity `id-eval`(최소 권한):
  - Cognitive Services OpenAI User, Azure AI User(모델, judge, 평가용 에이전트 버전 생성), AcrPull.
  - Storage Blob Data Contributor는 stdat의 `eval-reports` 컨테이너 범위만(`modules/blob-container-role.bicep`).
  - Cosmos, Key Vault, 큐 권한은 없습니다.

### 4.2 실행 흐름

1. `ops window open --acr` → `ops deploy eval` → `ops window close`. 창은 이미지 빌드에만 필요합니다.
2. `ops eval [--suite ...]`: Job 시작(제어 평면. `all`이 아니면 `job show`의 컨테이너 정의를 복사해 `EVAL_SUITE`만 바꾼 템플릿으로 ARM `start`) → `smoke_runner`와 같은 폴링으로 종료 대기 → 리포트 회수 → `eval/reports/<실행 이름>/` 저장.
3. 리포트를 커밋하고 README에 실측값을 기록합니다.

### 4.3 리포트 회수

- Job은 리포트 원본을 Blob `eval-reports/<run-id>/`에 올립니다(실패해도 로그만 남기고 계속).
- 같은 파일을 stdout에 `EVALCHUNK <run-id> <file> <seq>/<total> <sha256> <base64>` 형식(gzip 후 base64, 조각 6000자, 한 줄 8KB 이하)으로 출력합니다. run-id는 `CONTAINER_APP_JOB_EXECUTION_NAME`, 없으면 `local-<UTC 시각>`입니다.
- `ops eval`은 Log Analytics(`ContainerAppConsoleLogs_CL`)에서 실행 이름으로 청크를 모아 재조립하고 sha256을 확인합니다. 수집 지연이 있으므로 30초 간격으로 최대 10분 다시 조회합니다.
- 이렇게 해서 stdat 네트워크를 열지 않고 리포트를 받습니다.

### 4.4 Live probe 항목 (plan 작성 전에 확인)

1. ~~`azure-ai-evaluation` 평가기~~: 설치 실패로 자체 judge로 대체(§9). 대신 gpt-5.4 judge 에이전트가 관리 ID로 동작하는지.
2. 이미지 크기와 `az acr build` 시간.
3. 분류 suite가 gpt-5.4 배포 이름으로 `LlmFallback`을 직접 부를 수 있는지, Decision-1 응답에 usage 필드가 있는지.
4. 긴 stdout 줄이 Log Analytics에서 잘리지 않는지(6000자 조각 기준), Job에 `CONTAINER_APP_JOB_EXECUTION_NAME`이 주입되는지, 시작 시 suite를 덮어쓸 수 있는지.
5. `eval-` 접두어 에이전트 이름을 Foundry가 받는지.

결과(2026-11-02, 운영자 PC와 `job-smoke`로 확인):

- judge 에이전트(gpt-5.4)와 `eval-` 접두어 에이전트 버전 생성·대화·컨테이너 삭제가 정상입니다.
- Decision-1 응답에 usage(입력 토큰, 출력 1)가 있고, gpt-5.4 배포 이름으로 `LlmFallback`을 직접 부를 수 있습니다.
- `CONTAINER_APP_JOB_EXECUTION_NAME`은 주입됩니다. start 템플릿 덮어쓰기는 컨테이너 정의를 통째로 바꾸므로, 기존 컨테이너를 복사해 env만 바꾸면 이미지·env·관리 ID가 유지됩니다(§4.2).
- Log Analytics는 16,384바이트를 넘는 줄을 나눠 저장합니다(40K 줄 → 3조각). 6K·12K 줄은 그대로라 6000자 조각은 안전합니다. 수집 지연은 약 5초입니다.
- 사전 실행 비용: 분류 $0.06, 심사 $0.55, 조기경보 $0.99(합계 약 $1.6, 가드 $5 안). 이미지 크기와 빌드 시간은 실제 배포에서 기록합니다.

---

## 5. 오류 처리

| 상황 | 처리 |
|---|---|
| 케이스 하나의 모델 오류·시간 초과·스키마 위반 | 케이스를 `status=error`로 기록하고 계속 진행. 오류율을 별도로 보고하고 지표 분모에서 제외. 실패 유형 카탈로그에 포함 |
| 429, 5xx | 지수 백오프로 최대 3회 재시도(기존 runtime 패턴 재사용). 총 재시도 수 보고 |
| 예산 초과 | 남은 케이스를 건너뛰고 "예산 초과로 부분 결과" 표시 |
| judge 실패 | judge 점수만 비우고 자체 지표는 유지 |
| Job 종료 코드 | 리포트를 만들었으면 0. 리포트 생성 자체가 실패하면 0이 아닌 값을 반환하고, `ops eval`이 로그 조회 힌트를 출력 |
| 회수 실패(청크 누락, hash 불일치) | 10분까지 재조회 후 실패 처리, Blob 원본 경로를 안내 |

---

## 6. 테스트

로컬 TDD로 진행합니다. 실제 모델은 부르지 않습니다.

| 대상 | 검증 |
|---|---|
| 데이터셋 | 같은 시드면 같은 데이터. 라벨 분포(4개 아키타입, 주입 거래, PII, 반사실 쌍)가 고정 |
| 지표 함수 | macro F1, precision/recall, 리드 일수, p50/p95, 비용을 손으로 계산한 기대값과 비교 |
| Suite | 가짜 runner·decider로 케이스 격리, 예산 가드, 모델 전환, 오류 집계 |
| 리포트 | `summary.json` 스키마, `report.md`의 "합성 데이터 기준" 표기와 실패 유형 카탈로그 |
| 회수 | 청크 출력 ↔ 재조립 왕복, 순서 섞임, 누락, hash 불일치 |
| `ops eval`, `ops deploy eval` | 가짜 `az`로 시작·폴링·회수·저장 경로 |
| 인프라 | `job-eval`, `id-eval`, 역할 범위 Bicep 테스트(`RUN_INFRA_TESTS=1`) |
| 종단 | `python -m evals --fake`가 완전한 리포트를 만듦 |

배포 후에는 `ops eval --suite all`을 한 번 실제로 돌려 실측값, 소요 시간, 비용을 README에 기록합니다.

---

## 7. 범위에서 뺀 것

- Foundry 포털 평가 업로드와 대시보드.
- 운영 데이터 기준 세그먼트별 승인율 격차, SLO, 장애 주입, 비용 모델 문서(P6b).
- Prompt Shields 같은 외부 안전 서비스 평가. 이번 주입 방어율은 자체 grounding·Policy 방어선 기준입니다.
- 대규모 평가와 통계적 유의성 검정. 소규모 표본이라 숫자는 경향으로만 해석한다고 리포트에 적습니다.

## 8. 위험

| 위험 | 대응 |
|---|---|
| `azure-ai-evaluation` 의존성이 무겁거나 private endpoint에서 동작하지 않음 | 실제로 설치 실패. 같은 기준의 자체 LLM-judge로 대체함(§9) |
| Decision-1 공개 단가 없음 | 토큰 수만 보고하고 "단가 미확인" 표시 |
| 소규모 표본 | 지표마다 표본 수(n)를 함께 표기 |
| Log Analytics 수집 지연·줄 길이 제한 | probe로 청크 크기 확정, 재조회, Blob 원본 보관 |

---

## 9. Scratch 결과 반영 (2026-11-02 보정)

Scratch TDD에서 확인해 위 본문에 반영한 변경입니다.

| 항목 | 원래 | 변경과 이유 |
|---|---|---|
| judge | `azure-ai-evaluation` 평가기 | 패키지 설치가 실패해 §8 대응대로 자체 LLM-judge(`evals/judge.py`)로 대체. 기준(groundedness·relevance·coherence, 1~5점)과 judge 모델(gpt-5.4)은 같음 |
| 에이전트 이름 | 운영 이름 그대로 | 운영 코드는 같은 이름의 최신 버전을 부르므로, 평가가 다른 모델로 같은 이름의 버전을 만들면 운영 동작이 바뀜. `eval-<이름>-<모델>`로 분리하고 실행 후 대화·컨테이너를 정리 |
| 계량 위치 | suite가 직접 기록 | httpx 전송 계층(`MeteredTransport`)에서 기록. 재시도(429·502·503·504, 지수 백오프 최대 3회)도 같은 곳에서 처리해 에이전트 코드를 고치지 않음 |
| PII 검사 | 모델 입출력 문자열 | 도구 인자·출력 안에 다시 들어 있는 JSON 문자열까지 풀어서 검사. 식별자·메타데이터 키(id, created_at 등)는 숫자열이 계좌번호 패턴에 걸리므로 제외 |
| 분류 데이터 | 거래 약 300건 | 합성 케이스의 규칙 미분류 거래만으로는 모델 비교 대상이 적어, 규칙 키워드에 걸리지 않는 어려운 적요 뱅크를 추가 |
| 조기경보 데이터 | STRESSED onset 기반 6경로 | P5a의 사후 흐름 시나리오(STACKING/STABLE)를 그대로 써 운영 모니터링 경로와 같은 입력을 재생. "탐지기 단독" 기준선을 함께 보고해 에이전트 조사의 기여를 보임 |
| 반사실 쌍 | "일부" | 주입 없는 2건, gpt-5.4 analyst만. 소규모라 불일치 0건이어도 공정성 보장이 아니라 "이 표본에서 관찰되지 않음"으로 표기 |
| Decision-1 비용 | 단가 미확인 표시 | 비용 합계·예산 가드에서도 제외하고 호출 수를 따로 보고 |
| Blob 업로드 | SDK | REST(Put Blob) + 관리 ID 토큰. 이미지 의존성을 줄임 |
| 컨테이너 범위 역할 | 미정 | `modules/blob-container-role.bicep`으로 `eval-reports` 컨테이너에만 부여 |
| 단가표 위치 | `eval/prices.json` | `src/evals/prices.json`(이미지·wheel에 포함) |
| suite 이름 | `ew` | `early_warning`, `safety` 추가. `safety`만 고르면 입력이 되는 `underwriting`도 함께 실행 |
| suite 덮어쓰기 | `job start --env-vars` | start 템플릿이 컨테이너를 통째로 바꾸고, Windows `az.cmd`가 따옴표 든 인자를 깨뜨림. `job show` 컨테이너를 복사해 `EVAL_SUITE`만 바꾼 본문을 `az rest`(ARM `jobs/<이름>/start`, 본문은 임시 파일)로 보냄 |
| 토큰 | 자격 증명 객체 그대로 | 로컬 `AzureCliCredential`은 호출마다 az 프로세스를 띄워 분류 suite가 18분, 75% 토큰 실패. `CachedToken`(만료 5분 전까지 재사용)으로 91초, 실패 0 |
| judge 컨텍스트 | 지표·정책 | 분석가가 인용한 월별 지수·신호가 없어 groundedness가 2.1~2.5로 깎임. 분석가와 같은 근거(지표·신호·플래그·월별·정책, 가명)를 주자 4.25 |
| 분류 천장 효과 | — | 사전 실행에서 세 구성 모두 macro F1 1.0. 모든 구성이 0.95 이상이면 리포트 한계 절에 "천장 효과"를 적음 |
| 로컬 실행 의존성 | — | dev extra에 `azure-identity==1.27.0`. win-arm64에서는 `--only-binary=cryptography,cffi`로 설치 |
