# P6b 운영 지표(관측성·SLO·비용·공정성·장애 주입) — 설계 Spec

- 날짜: 2026-11-02
- 상위 Spec: `2026-10-31-agentic-cashflow-lending-design.md` §2.4(장애 도메인), §4.3(공정성), §4.4(관측성·SLO·비용·복원력), §8(루브릭 A5)
- 범위: P6 3분할 중 두 번째 단계. P6a(평가 하네스)는 완료·병합. P6c(문서·ADR·위협 모델·데모 대본)는 별도 Spec입니다.

---

## 1. 목적과 성공 기준

### 1.1 목적

루브릭 A5(운영 우수성: 측정된 비용·지연·SLO)와 B7(근거 없는 주장 상한)에 대응해, 배포된 여정을 **실제 Azure에서 돌려 측정한 숫자**로 SLO·건당 비용·공정성 격차를 남기고, 장애 주입으로 복원력 동작을 증거와 함께 보여 줍니다. 모든 숫자는 "합성 데이터 기준"으로 표기합니다.

### 1.2 사용자 결정 사항

| 항목 | 결정 |
|---|---|
| 측정 트래픽 | `ops` 명령으로 합성 신청 24건을 실제 여정에 흘려 측정. 수동 신청은 집계 대상이 아님 |
| 배치 구성 | 균형 격자: 원형 4종 × 업종·지역·업력 세그먼트를 독립 배정한 `load-*` 신청자 24명(교란 제거) |
| 승인율 기준 | 에이전트 추천(`assessment.recommendation == approve`) 비율과 정책 판정(`policy.decision == eligible`) 비율. 심사역 결정이 있으면 별도 열로 표시 |
| OTel 범위 | Functions + Policy Engine에 `azure-monitor-opentelemetry`. console·mockapi는 제외 |
| 대시보드 위치 | 공정성 = console `/ops` 페이지(Cosmos 집계 + 임계값 초과 배너). SLO·단계 성공률·비용 추이 = Azure Workbook + Azure Monitor 경보 |
| 장애 시나리오 | ① mockapi 원천 일시 장애(503) → 재시도 회복 ② Policy Engine 다운 → 서킷브레이커 open → fail-closed로 심사역 수동 심사 |
| 비용 모델 | 에이전트 호출별 토큰을 신청 문서에 기록 → 실측 AI 비용 + Cost Management 월 고정비 ÷ 가정 물량. 수작업 원가는 출처 명시 가정치 |
| 접근안 | A: Cosmos `steps[]` 기록이 측정 원천, OTel은 트레이스·Workbook·경보용(§2.1) |

### 1.3 성공 기준

1. `ops load --count 24`가 실제 Azure 여정에 24건을 넣고, 전원이 `awaiting_officer`에 도달(또는 실패로 종료)한 것을 확인합니다.
2. `ops metrics --run <run_id>`가 `ops/reports/<run_id>/report.md`와 `metrics.json`을 만들고, 다음 숫자가 모두 들어 있습니다(또는 "측정 불가" 사유).
   - SLO1: 접수 → 심사역 대기열 p95(분), 목표 ≤ 10분 대비 판정
   - SLO2: 에이전트 단계 성공률(%), 목표 ≥ 99% 대비 판정
   - 건당 비용: 실측 AI 비용/건, 고정비/건(물량 3가정), 합계, 수작업 원가 가정 대비 비율
   - 공정성: 세그먼트별 추천 승인율·eligible율, 최대 격차(pp), 원형 통제 격차, 임계값 판정
3. console `/ops`가 같은 계산 함수로 공정성 표·격차 배너·SLO/비용 요약을 보여 줍니다.
4. App Insights에 Functions 활동 스팬과 Policy 요청이 `application_id` 속성과 함께 들어오고, 활동 → Policy 구간이 한 `operation_Id`로 이어집니다.
5. Workbook 1개와 경보 규칙 2개(에이전트 단계 실패율, Policy 서킷 open)가 bicep으로 배포됩니다.
6. 장애 시나리오 ①②를 실제 Azure에서 각각 실행하고, 신청 ID·최종 상태·플래그·의존성 호출 수(KQL 결과)를 보고서에 남긴 뒤, 장애 토글이 꺼졌음을 확인합니다.
7. README에 P6b 실측값·소요 시간·비용과 알려진 한계를 기록합니다.
8. 실측 비용 합계가 상한 $5 안에 들어옵니다.

---

## 2. 아키텍처

### 2.1 접근안 선택

| 안 | 내용 | 판단 |
|---|---|---|
| **A (선택)** | 신청 문서에 단계 기록(`steps[]`)·세그먼트·실행 태그를 남기고, 순수 계산 모듈 하나로 SLO·비용·공정성을 계산. OTel은 분산 트레이스·Workbook·경보용 | 측정치가 재현 가능하고 텔레메트리 지연·샘플링과 무관. console과 보고서가 같은 함수를 씀 |
| B | App Insights customEvents를 단일 원천으로 KQL 계산, console이 Log Analytics 조회 | console에 LA 권한 필요, 수집 지연이 숫자에 영향 |
| C | Cosmos timeline만으로 계산, OTel 없음 | §4.4 분산 트레이스 요구 미충족 |

### 2.2 구성 요소

| 구성 요소 | 위치 | 역할 |
|---|---|---|
| 단계 기록 | `src/orchestrator/journey/orchestration.py`, `activities.py` | 활동 결과의 `_meter`(시각·사용량)를 `steps[]`로 옮기고, 예외는 `failed`로 기록 |
| 토큰 분리 기록 | `src/agents/runtime.py` 외 에이전트 모듈 | `AgentRun`에 `input_tokens`·`output_tokens`·`model` 추가, 활동 결과로 전파 |
| 계산 모듈 | `src/opsmetrics/` (신규, 순수 함수) | SLO1·SLO2·비용·공정성 계산. I/O 없음 |
| console `/ops` | `src/console/app.py`, `views.py`, `adapters.py` | run 선택, 공정성 표, 격차 배너, SLO/비용 요약 |
| ops 명령 | `src/ops/` | `load`, `metrics`, `fault` 하위 명령 |
| 운영 Job | `infra/modules/ops-job.bicep`, `src/opsjob/`(신규, 평가 이미지에 포함) | VNet 내부에서 큐 제출·Cosmos 읽기, 투영을 로그로 반환(`load`·`submit`·`collect` 모드) |
| 균형 격자 신청자 | `src/mockapis/demo.py`, `synth.py` | `load-*` 24명 생성(세그먼트 독립 배정) |
| 장애 주입 | `src/mockapis/app.py`(flaky 모드), `src/policy/app.py`(FAULT_MODE 미들웨어) | 토글로 켜고 끔 |
| 서킷브레이커 | `src/orchestrator/journey/adapters.py` `HttpPolicyApi` | 프로세스 내 브레이커 |
| 텔레메트리 | `function_app.py`, `src/policy/main.py`, requirements | `configure_azure_monitor(credential=관리 ID)`, httpx·FastAPI 계측 |
| 인프라 | `infra/modules/monitoring.bicep` 또는 신규 `ops-insights.bicep`, `container-app.bicep`, `functions.bicep`, Foundry 모듈 | Workbook, 경보 2개, Policy 앱 App Insights 설정, `OTEL_SERVICE_NAME`, Foundry 프로젝트 App Insights 연결·역할, `id-ops`와 역할 |

### 2.3 데이터 흐름

```
ops load ──(job-ops 시작)──▶ job-ops ──(queue applications-start, submitted_at·source·run_id)──▶ Functions 여정
   활동(consent·assemble·underwrite·policy·assess) ──_meter──▶ 오케스트레이터 ──▶ Cosmos applications.steps[]
   활동 스팬(OTel) ──▶ App Insights ◀── Policy FastAPI 스팬(traceparent 전파)
ops metrics ──(job-ops collect: Cosmos 투영 ⇢ 로그) + Cost Management + KQL + prices.json──▶ opsmetrics ──▶ ops/reports/<run_id>/
console /ops ──(Cosmos 읽기)──▶ opsmetrics ──▶ 공정성 표·배너
Workbook·경보 ──(KQL)──▶ App Insights
```

---

## 3. 데이터 모델

### 3.1 StartRequest 확장

`StartRequest`에 선택 필드를 추가합니다(`extra="forbid"` 유지, 기존 메시지 호환).

| 필드 | 타입 | 의미 |
|---|---|---|
| `submitted_at` | datetime \| None | 제출 시각(큐에 넣는 쪽이 기록). SLO1 시작점 |
| `source` | `console` \| `load` \| `fault` \| None | 트래픽 출처 |
| `run_id` | str \| None (≤ 64) | 배치·장애 실행 묶음 ID |

console 신청 경로도 `submitted_at`·`source=console`을 채웁니다. 신청 문서에 세 필드를 그대로 복사합니다.

### 3.2 세그먼트

`underwrite` 이후 신청 문서에 `segment = {industry, region, business_age_band}`를 남깁니다. 값은 원천 profile(Blob)에서 읽어 활동 결과로 돌려줍니다. 업력 구간은 `<1년`(< 12개월), `1~3년`(12 ~ 35개월), `3년+`(≥ 36개월)입니다. 지역은 시·도 단위(첫 어절)로 묶습니다.

### 3.3 단계 기록 `steps[]`

```json
{"step": "assemble", "outcome": "ok|degraded|failed", "started_at": "...", "ended_at": "...",
 "duration_ms": 1234, "agent": true, "attempts": 1,
 "usage": [{"model": "gpt-5.4-mini", "input_tokens": 0, "output_tokens": 0, "calls": 1}],
 "detail": "짧은 사유(선택, ≤ 200자)"}
```

- 활동은 결과에 `_meter = {started_at, ended_at, usage, outcome?, detail?}`를 붙이고, 오케스트레이터가 이를 꺼내 `steps[]`에 넣습니다(활동 안의 시계는 비결정적이어도 됩니다). 오케스트레이터는 `_meter`를 문서 본문 다른 곳에 남기지 않습니다.
- 활동이 예외로 끝나면 오케스트레이터가 `ctx.current_utc_datetime`으로 `failed`를 기록합니다(시작 시각은 호출 직전 오케스트레이션 시각).
- 단계별 판정 규칙

| step | agent | ok | degraded | failed |
|---|---|---|---|---|
| `consent` | 아니오 | 동의 발급 | — | 예외 |
| `assemble` | 예 | `status == complete` | `incomplete`(원천 누락) | 예외 |
| `classify` | 예 | 의미 분류 성공 또는 대상 없음 | — | 의미 분류 오류(규칙 분류로 진행) |
| `underwrite` | 아니오 | 산출 | — | 예외 |
| `policy` | 아니오 | 판정 수신 | — | 예외(서킷 open 포함, fail-closed) |
| `assess` | 예 | `assessment` 존재 | — | 에이전트 오류로 `assessment` 없음 |

- `classify`는 `underwrite` 활동 안에서 일어나므로 `underwrite`의 `_meter`가 하위 단계 기록 `substeps`로 함께 돌려줍니다. 오케스트레이터는 이를 별도 `steps[]` 항목으로 펼칩니다.
- Decision-1 호출은 단가가 없으므로 `usage`에 `model: "decision-1"`, `calls`만 남기고 토큰은 0으로 둡니다.

---

## 4. 지표 정의(`src/opsmetrics/`)

모두 신청 문서 목록과 설정만 받는 순수 함수입니다. 입력 필터는 `run_id`(또는 `source`)입니다.

### 4.1 SLO1 — 접수 → 심사역 대기열 지연

- 표본: `timeline`에 `awaiting_officer`가 있는 신청. 시작점은 `submitted_at`(없으면 `received_at`), 끝점은 첫 `awaiting_officer` 시각.
- p95는 nearest-rank 방식입니다. 표본 수, p50, p95, 최대값을 같이 냅니다. 대기열에 도달하지 못한 신청(실패·진행 중)은 따로 셉니다.
- 판정: p95 ≤ 10분이면 충족. 표본이 20건 미만이면 "표본 부족"을 함께 표시합니다.
- 큐 대기·콜드 스타트가 포함되도록 `submitted_at`를 씁니다. `received_at`만 있는 신청은 표에 표시합니다.

### 4.2 SLO2 — 에이전트 단계 성공률

- 분모: `agent == true`인 단계 기록 수(`assemble`·`classify`·`assess`). 분자: `ok` 수.
- `degraded`는 성공으로 치지 않고 따로 비율을 냅니다(안전망 경유율).
- 판정: 성공률 ≥ 99%면 충족. 24건 × 3단계 = 72표본이므로 실패 1건이면 98.6%로 미달입니다. 이는 그대로 보고합니다.
- 품질 신호(근거 실패·검토 미통과)는 P6a 평가 범위이며 여기서는 세지 않습니다(기술적 성공 기준).

### 4.3 건당 비용

- AI 비용/건 = Σ(steps.usage × `src/evals/prices.json` 단가) ÷ 신청 수. 단가 미확인 호출(Decision-1)은 호출 수만 표시합니다. 캐시 할인을 반영하지 않아 상한 추정입니다.
- 고정비/건 = 월 고정비 ÷ 가정 물량(월 100, 1,000, 10,000건). 월 고정비는 Cost Management에서 `rg-amalend`의 최근 완료일 일별 비용(최대 7일) 평균 × 30으로 환산하고, MeterCategory `Foundry Models`(AI 토큰)는 이중 계산을 막기 위해 뺍니다(Probe 결과 반영 P6 참고). 데모 운영 형태(Service Bus pause, IP 창 닫힘)가 반영된 값임을 표기합니다.
- 수작업 원가 = 소상공인 대출 1건 심사·실행 원가에 대한 공개 출처 가정치. 출처 URL·발행 연도·환산 근거를 보고서와 README에 적습니다(조사는 계획 단계).
- 결과 표: 물량별 (AI + 고정비)/건, 수작업 원가 대비 비율, USD 6,000 티켓 대비 비율.

### 4.4 공정성

- 세그먼트 축: 업종, 지역, 업력 구간.
- 축별·값별: 표본 수, 추천 approve율(분모: assessment가 있는 신청), eligible율(분모: policy가 있는 신청), 추천 분포(approve/conditional/decline 수), 심사역 결정이 있으면 승인율.
- 격차 = 같은 축 안에서 값별 비율의 최대 − 최소(pp). 표본 5건 미만인 값은 계산에서 빼고 "표본 부족"으로 표시합니다.
- 원형 통제 격차: 같은 원형 안에서 축 값별 비율 격차를 계산하고, 원형별 최대값을 함께 표시합니다. 균형 격자에서는 원형 효과가 상쇄되므로 이 값이 편향 신호입니다.
- 임계값: 기본 20pp(`FAIRNESS_GAP_PP`로 변경). 업종·지역 격차가 임계값을 넘으면 경보(console 배너, 보고서 표시)입니다. 업력 격차는 규칙 R104(업력 12개월 미만 → refer)로 설명되는 부분이 있으므로 "정책 근거 있음"으로 표시하고 경보 대상에서 뺍니다.
- 상위 Spec의 "추천 점수 분포"는 점수 필드가 없으므로 추천 분포(범주 수)로 대체한다고 보고서에 적습니다.

---

## 5. 관측성

### 5.1 Functions

- `azure-monitor-opentelemetry==1.8.11`·`opentelemetry-instrumentation-httpx==0.66b1`을 의존성에 추가하고, 시작 시 `configure_azure_monitor(credential=ManagedIdentityCredential(client_id), logger_name="journey.otel", enable_live_metrics=False)`를 호출합니다(App Insights 로컬 인증이 꺼져 있음). httpx 계측을 켜 Policy·mockapi·Foundry 호출을 의존성으로 남깁니다.
- host.json `telemetryMode`와 `PYTHON_APPLICATIONINSIGHTS_ENABLE_TELEMETRY`는 바꾸지 않습니다. 호스트는 기존 App Insights 연동(Entra 인증 문자열)을 그대로 쓰고, 워커는 배포판으로 직접 내보냅니다(Probe 결과 반영 P1).
- `OTEL_SERVICE_NAME`을 앱 설정으로 지정합니다(Functions `orchestrator`, Policy `policy`). 지정하지 않으면 `unknown_service:python`으로 기록됩니다.
- 활동마다 `journey.<step>` 스팬을 만들고 `application_id`, `step`, `outcome`, `source`, `run_id`를 속성으로 붙입니다. 에이전트 호출 스팬은 GenAI 의미 규약 속성(`gen_ai.agent.name`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`)을 붙입니다.
- 오케스트레이터 함수에서는 스팬을 만들지 않습니다(재생 시 중복).
- 로그·스팬 속성에 원문 PII(상호·대표자명·사업자번호·적요)를 넣지 않습니다. `application_id`·`applicant_id`(합성 ID)·세그먼트만 허용합니다.

### 5.2 Policy Engine

- 같은 배포판(FastAPI 계측 포함, 별도 패키지 불필요). `/healthz` 요청과 ASGI 내부 send/receive 스팬은 `OTEL_PYTHON_FASTAPI_EXCLUDED_URLS=healthz`, `OTEL_PYTHON_FASTAPI_EXCLUDE_SPANS=receive,send`로 뺍니다. Policy 앱은 이미 `apps` 사용자 할당 ID를 쓰고, 이 ID에 Monitoring Metrics Publisher 역할이 있습니다(`infra/resources.bicep` monitoring 역할 목록). `container-app.bicep`에 선택 파라미터로 `APPLICATIONINSIGHTS_CONNECTION_STRING`을 넘기고, 코드는 `AZURE_CLIENT_ID`로 자격 증명을 만듭니다.
- 활동 → Policy 호출은 httpx 계측이 `traceparent`를 전파해 같은 `operation_Id`로 이어집니다.

### 5.3 Workbook과 경보

- Workbook(bicep `Microsoft.Insights/workbooks`): 단계별 성공률·p95 소요시간, 의존성 실패(대상별), 서킷 상태 이벤트, 모델별 토큰 추이, `application_id`로 한 신청의 스팬 목록 보기.
- 경보(`Microsoft.Insights/scheduledQueryRules`):
  1. 에이전트 단계 실패율 > 1%(1시간 창, 표본 ≥ 10)
  2. Policy 서킷 open 이벤트 ≥ 1(15분 창)
- Action Group 수신 이메일은 선택 파라미터입니다. 비어 있으면 Action Group 없이 포털 경보로만 남깁니다.

### 5.4 알려진 한계

- Python Durable은 오케스트레이터 → 활동 트레이스를 자동으로 잇지 않습니다. 활동 스팬은 각각 새 트레이스의 루트가 되고, 신청 단위 연결은 `application_id` KQL 조인으로 대신합니다.
- Foundry 에이전트 서비스 트레이스(`invoke_agent`, `chat <model>`, `execute_tool`, `genAIContent`)는 프로젝트 App Insights 연결로 채택합니다. httpx가 `traceparent`를 전파해 우리 `journey.assess_credit` 스팬과 같은 `operation_Id`로 이어집니다(Probe 결과 반영 P4). `genAIContent`에는 프롬프트·응답이 남으므로 에이전트 입력이 가명화된 값뿐이라는 기존 원칙(P5)에 기대며, 이를 README에 적습니다.

---

## 6. 복원력과 장애 주입

### 6.1 시나리오 ① 원천 일시 장애 → 재시도 회복

- mockapi `FaultRequest.mode`에 `flaky`를 추가합니다. `flaky`일 때 데이터 엔드포인트는 (동의 ID, 경로)별 첫 요청에만 503을 주고 이후 정상 응답합니다(인메모리 카운터, `none`으로 돌리면 초기화).
- mockapi와 Policy는 내부 ACA 환경에 있어 로컬에서 `/admin/faults`를 부를 수 없습니다. 그래서 시작 시 `FAULT_MODE` 환경변수로 초기 장애 모드를 정하고, 토글은 리비전 갱신으로 합니다(§6.2와 같은 방식). mockapi 재시작은 인메모리 동의 레지스트리를 비우므로, 장애 시나리오는 배치가 끝난 뒤에 돌립니다.
- 기존 data_assembly 재시도 예산(`MAX_ATTEMPTS = 3`)으로 회복해야 합니다. 기대 결과: `assemble` 단계 `ok`, 원천별 시도 2회, App Insights에 같은 원천의 503 → 200 의존성 기록.
- 일시 장애 모드 `unavailable`(기존)은 그대로 둡니다.

### 6.2 시나리오 ② Policy 다운 → 서킷브레이커 → 수동 심사

- Policy 앱에 `FAULT_MODE` 환경변수 미들웨어를 둡니다. `unavailable`이면 `/v1/decisions`에 503, `/healthz`는 정상(재시작 루프 방지).
- 토글은 `ops fault policy on|off`가 `az containerapp update --set-env-vars/--remove-env-vars`로 새 리비전을 만드는 방식입니다. ingress 비활성화는 다시 켤 때 ingress 설정을 재현해야 해 bicep과 어긋날 위험이 있어 쓰지 않습니다. 리비전 전환 시간과 다른 설정 유지는 probe로 확인합니다. 다음 `azd` 배포가 환경변수를 bicep 값으로 되돌리므로 원복이 이중으로 보장됩니다.
- `HttpPolicyApi`에 프로세스 내 서킷브레이커: 연속 실패 3회 → open(60초) 동안 HTTP 호출 없이 `PolicyUnavailable("circuit_open")`. 이후 half-open에서 1회 시험, 성공하면 closed. 상태 전이마다 스팬 이벤트·구조화 로그(`policy.circuit`, `state`)를 남깁니다. 시계·임계값은 주입 가능(테스트용).
- 기대 흐름: 첫 신청은 Durable 재시도 3회 실패 → 서킷 open → `policy_unavailable`·`assessment_unavailable` 플래그 → 심사역 대기열. 이어지는 신청은 HTTP 호출 없이 같은 경로로 빠르게 대기열에 도착.
- 한계: Flex 인스턴스마다 서킷 상태가 따로입니다. 분산 서킷(Durable Entity 등)은 운영 전환 과제로 README에 적습니다.

### 6.3 운영 명령과 증거

- `ops fault source flaky|off`, `ops fault policy on|off`, `ops fault status`.
- `ops fault run <scenario>`는 토글 → 신청 2~3건 제출(`source=fault`, `run_id`) → 대기열 도달 대기 → 토글 원복 → 원복 확인까지 한 번에 합니다. 중간 실패 시에도 `finally`로 원복을 시도하고, 원복 실패는 명확한 오류로 알립니다.
- 보고서 장애 절: 시나리오별 신청 ID, 최종 상태, 플래그, 단계 기록, KQL로 센 의존성 호출 수(시나리오 ②는 서킷 open 뒤 Policy 호출 0건), 원복 확인.
- Durable 재개(호스트 재시작 후 이어서 진행)는 기존 Durable 기능에 맡기고 이번 시나리오에 넣지 않습니다.

---

## 7. 배치와 실행

### 7.1 균형 격자 신청자

- mockapi에 `load-<archetype>-<n>` 신청자 24명을 추가합니다(원형 4종 × 6명, seed 결정론적).
- 세그먼트 값: 업종 3(카페, 한식, 제과점), 지역 3(서울, 부산, 경기), 업력 구간 3(<1년, 1~3년, 3년+). 원형마다 각 값이 2번씩 나오도록 라틴 방격으로 배치해, 업종·지역·업력이 원형과 독립이 되게 합니다. 값별 표본은 8건입니다.
- 원형은 거래 패턴만 정하고, 상호·업종·지역·업력은 세그먼트 값에서 정합니다(합성 상호·인물명은 업종별 목록). `generate_case`에 persona 덮어쓰기 인자를 추가하되 기존 데모 신청자 결과는 바뀌지 않아야 합니다.
- 신규(thin file) 원형은 거래 이력이 4개월이라 R001로 knockout됩니다. 업력 값과 무관하게 정책 결과가 같다는 점을 보고서에 적습니다.

### 7.2 실행 흐름

Cosmos와 Storage Queue는 IP 창으로도 로컬에서 열리지 않습니다(Probe 결과 반영 P7). 그래서 큐 제출과 Cosmos 읽기는 내부 VNet의 Container Apps Job `job-ops`가 하고, 로컬 `ops`는 Job 시작·결과 수집·Cost Management·KQL 조회·보고서 작성을 맡습니다. `job-ops`는 P6a 평가 이미지를 재사용하고(`opsmetrics`·적재 모듈 추가), 전용 사용자 할당 ID(`id-ops`)에 `stfn` Queue Data Message Sender와 Cosmos 기본 제공 Data Reader만 줍니다. 결과는 P6a 로그 전송(EVALCHUNK 재조립)으로 돌려받습니다.

1. `ops window open` (ACR 갱신이 필요하면 `--acr`).
2. `ops load --count 24 --interval 15`: 로컬이 `run_id`를 만들고 `job-ops`를 `load` 모드로 시작합니다. Job은 15초 간격으로 큐에 제출(`submitted_at`, `source=load`)하고 Cosmos를 폴링해 전원이 `awaiting_officer` 또는 종료 상태가 될 때까지 기다린 뒤(최대 30분, Job 제한 시간 45분), 지표 입력용 투영(아래)을 로그로 내보냅니다. 로컬은 Job 상태를 폴링하며 진행 상황을 출력합니다.
3. `ops fault run source-flaky`, `ops fault run policy-down`: 로컬이 토글(리비전 갱신)한 뒤 `job-ops`를 `submit` 모드(`source=fault`)로 시작하고, 끝나면 `finally`로 원복합니다.
4. `ops metrics --run <load run_id> --fault-runs <id...>`: 로컬이 `job-ops`를 `collect` 모드로 시작해 run별 투영을 받고, Cost Management·KQL을 더해 `ops/reports/<run_id>/report.md`·`metrics.json`을 만듭니다. `load`가 받은 투영은 `ops/reports/<run_id>/docs.json`에 저장해 재계산에 씁니다.
5. `ops window close`, `ops pause`.

- 투영 필드: `application_id`, `applicant_id`, `status`, `submitted_at`, `received_at`, `source`, `run_id`, `segment`, `steps`, `timeline`, `flags`, `policy.eligible`·`policy.decision`·`policy.rule_hits`, `assessment.recommendation`, 심사역 결정. 원문 PII는 넣지 않습니다.

- 배치 신청은 심사역 대기열에 남습니다. 승인율 기준이 추천이므로 결정은 넣지 않습니다. SLA 경과 후 escalated → closed_no_decision으로 자연 종료됩니다.

### 7.3 예산

배치 약 $2, 장애 시나리오 약 $0.5, probe 약 $0.5. 상한 $5. 배치 시작 전 단건 비용을 확인하고(첫 3건 완료 후 실측 평균 × 24가 $4를 넘으면 중단), 보고서에 실측 합계를 남깁니다.

---

## 8. 오류 처리

- `_meter`가 없거나 형식이 틀린 활동 결과: 단계는 `outcome`을 결과로 판정하고 `usage`를 빈 목록으로 기록합니다(측정 실패가 여정을 막지 않음).
- 텔레메트리 초기화 실패: 경고 로그만 남기고 업무 처리는 계속합니다.
- `ops load` 타임아웃: 미도달 신청 ID와 현재 상태를 출력하고, 지표는 도달한 것만으로 계산하되 미도달 수를 보고서에 표시합니다.
- Cost Management 조회 실패(권한·데이터 지연): 고정비를 "측정 불가(사유)"로 두고 나머지 지표는 계속 냅니다. `--fixed-monthly-usd`로 수동 입력할 수 있게 하고, 입력값이면 출처를 "수동 입력"으로 표시합니다.
- KQL 조회 일시 오류: P6a와 같은 재시도 정책을 재사용합니다.
- 장애 원복 실패: 명령이 0이 아닌 코드로 끝나고 원복 명령을 안내합니다.

---

## 9. Live probe 항목(plan 작성 전에 확인)

1. Flex Consumption Python에서 `azure-monitor-opentelemetry` + 관리 ID 자격 증명으로 스팬이 `appi-amalend`에 들어오는지, host.json `telemetryMode` 설정 필요 여부, 기존 호스트 로그와 중복 여부.
2. 활동 → Policy 호출이 같은 `operation_Id`로 이어지는지(httpx 계측 + FastAPI 계측).
3. Foundry Responses 응답의 `usage.input_tokens`·`output_tokens` 필드 존재(P6a 메터링에서 확인한 값 재확인).
4. Foundry 프로젝트 App Insights 연결로 에이전트 서비스 트레이스가 보이는지와 비용.
5. `az containerapp update --set-env-vars` 리비전 전환 시간(Policy·mockapi), 기존 설정(이미지·ID·ingress) 유지 여부.
6. Cost Management 쿼리 권한과 `rg-amalend` 서비스별 월 비용 조회 형태.
7. 로컬(IP 창 열림)에서 Cosmos `applications` 쿼리와 `applications-start` 큐 전송 권한.

결과는 이 Spec 끝에 "Probe 결과 반영" 절로 추가합니다.

---

## 10. 테스트

- 단위: `opsmetrics` 계산(p95 nearest-rank, 표본 부족, degraded 분리, 격차·원형 통제·R104 표시, 비용 합산·단가 미확인), 서킷브레이커 상태 전이(주입 시계), mockapi `flaky` 카운터, Policy `FAULT_MODE` 미들웨어, 격자 배정 균형(값별 8건·원형 독립), 기존 데모 신청자 결과 불변.
- 오케스트레이션: 가짜 컨텍스트로 `steps[]` 기록(성공·예외·`_meter` 누락), `submitted_at`·`source`·`run_id`·`segment` 복사, 서킷 open 시 fail-closed 경로.
- 런타임: `AgentRun`의 입력·출력 토큰 합산.
- console: `/ops` 렌더링(표, 배너 표시/미표시, 표본 부족).
- ops: `load`·`metrics`·`fault` 명령을 가짜 Azure 어댑터로 검증(원복 `finally` 포함).
- 인프라: bicep 빌드 테스트(`RUN_INFRA_TESTS=1`)에 Workbook·경보·Policy App Insights 설정 확인 추가.
- 실제 Azure: 성공 기준 1·2·4·6은 실제 실행으로 확인합니다.

---

## 11. 범위에서 뺀 것

- console·mockapi OTel 계측.
- 분산 서킷브레이커, Durable 재개 장애 시나리오.
- 심사역 결정 자동 입력, 최종 승인율 기준 공정성.
- 실시간 스트리밍 대시보드, Grafana.
- 부하 시험(동시 수백 건) — 24건 측정은 SLO 표본 수준이며 용량 시험이 아님을 README에 명시.
- 문서·ADR·위협 모델·데모 대본(P6c).

---

## 12. 위험

| 위험 | 대응 |
|---|---|
| Flex Python OTel 설정이 문서와 다르게 동작 | probe 1로 먼저 확인, 실패 시 워커 측 배포판만 사용하고 호스트 로그는 기존 유지 |
| 24건 표본으로 p95·99%가 불안정 | 표본 수·nearest-rank 방식·신뢰 한계를 보고서에 명시, 과장 금지(B7) |
| LLM 변동으로 원형 통제 격차가 우연히 임계값 초과 | 표본 수와 함께 보고하고, 초과 시 해당 사례를 보고서에 나열해 해석 |
| 장애 토글 원복 실패로 데모 환경 오염 | `finally` 원복 + `ops fault status` + 보고서 원복 확인 항목 |
| 고정비 조회 데이터 지연(당일 비용 미반영)·429 제한 | 최근 완료일 평균 환산, 429는 retry-after 존중 재시도, 실패 시 수동 입력 |
| 배치 비용 초과 | 첫 3건 실측 후 중단 조건(§7.3) |

---

## Probe 결과 반영(2026-11-02)

| # | 항목 | 결과 | 설계 반영 |
|---|---|---|---|
| P1 | Flex Python 워커 OTel + 관리 ID | **통과.** `configure_azure_monitor(credential=ManagedIdentityCredential)`만으로 워커 스팬이 `appi-amalend`(로컬 인증 꺼짐)에 들어옴. host.json·`PYTHON_APPLICATIONINSIGHTS_ENABLE_TELEMETRY` 변경 불필요. `logger_name`을 전용 이름으로 두어 호스트 로그와 중복 없음. 기본 `cloud_RoleName`은 `unknown_service:python` | §5.1: 호스트 설정 유지, `OTEL_SERVICE_NAME` 지정 |
| P2 | 활동 → Policy 상관관계 | **통과.** `journey.evaluate_policy`(루트) → httpx `POST /v1/decisions`(부모) → Policy `request`가 같은 `operation_Id`. Policy 쪽에 `/healthz`와 ASGI send/receive 스팬이 함께 기록됨 | §5.2: healthz·send/receive 제외 |
| P3 | Foundry 사용량 필드 | **통과.** P6a `calls.jsonl`과 `src/evals/metering.py`가 `usage.input_tokens`·`output_tokens`를 사용 | §3.3 그대로 |
| P4 | Foundry 트레이스 | **통과.** 프로젝트 연결 `appi-amalend`(category `AppInsights`, authType `ProjectManagedIdentity`, `metadata.ApplicationInsightsConnectionString` 필수 — 없으면 400) + 프로젝트 시스템 ID에 appi Monitoring Metrics Publisher 역할. `invoke_agent`·`chat gpt-5.4`·`execute_tool`·`genAIContent`가 우리 스팬과 같은 `operation_Id`로 기록됨. 연결·역할은 probe 중 수동 생성했으므로 bicep에 넣음 | §5.4 채택, 인프라 작업 추가 |
| P5 | 리비전 토글 | **통과.** `--set-env-vars` 약 22초 반환, 새 리비전 Healthy·트래픽 100%까지 약 39초. `--remove-env-vars` 약 19초. 이미지·ID·ingress·단일 리비전 모드 유지 | §6.2 그대로, 토글 후 리비전 Healthy 확인을 대기 조건으로 |
| P6 | Cost Management | **조건부 통과.** RG 범위 `query`(api 2023-11-01) 가능. 429가 잦아 retry-after(2~20초) 재시도 필요. `TheLastMonth`는 지원 안 함 → `Custom` + `timePeriod`. 데이터 약 1일 지연. 배포가 월중 시작이라 완료월 없음 | §4.3: 최근 완료일 평균 × 30, MeterCategory `Foundry Models` 제외 |
| P7 | 로컬 Cosmos·큐 접근 | **불가.** `operatorIp`는 Foundry 계정과 Functions에만 적용, Cosmos·Storage Queue는 사설 엔드포인트만 | §7.2: 사용자 선택에 따라 `job-ops`(VNet 내부 Job)로 변경 |

## 계획 단계 확정 변경(2026-11-02)

구현 계획(`docs/superpowers/plans/2026-11-02-p6b-ops-metrics.md`) 작성 중 확정한 변경입니다. 본문과 다르면 이 표가 우선합니다.

| Spec | 변경 | 이유 |
|---|---|---|
| §3.2 | 세그먼트는 오케스트레이터가 `underwriting["features"]`(industry·region·business_age_months)에서 계산 | 활동이 profile을 따로 돌려줄 필요 없음. 원천 값과 같음 |
| §3.3 | `attempts` 필드 삭제 | Durable 재시도 횟수는 활동 안에서 알 수 없음. 원천별 시도는 `assembly.sources[*].attempts`에 이미 있음 |
| §3.3 | `assemble`은 `status != complete` **또는** `agent_fallback` 플래그면 `degraded` | 에이전트 실패 후 결정론 조립으로 완료한 경우도 안전망 경유 |
| §3.3 | `classify` 하위 단계 시각 = `underwrite` 활동 시각 | 분류는 underwrite 활동 안에서 일어남 |
| §7.2 | 투영의 정책 필드는 `policy.decision`·`policy.reason_codes` | `PolicyDecision`에 `eligible`·`rule_hits` 필드가 없음 |
| §5.3 | Foundry 프로젝트 ID의 Metrics Publisher 역할은 `ops-insights.bicep`에 둠 | 연결과 역할을 한 모듈에 |
| §8 | Cost Management 429는 고정 백오프(20초 × 5회) | `az rest`가 retry-after 헤더를 돌려주지 않음 |
| §6.3 | KQL은 `az monitor log-analytics query`로 워크스페이스 테이블(`AppDependencies`·`AppRequests`·`AppTraces`) 조회 | P6a와 같은 경로 |
| §6.2 | `policy-down` 시나리오 제출 간격 90초(`source-flaky`는 5초) | 서킷 cooldown(60초)보다 길게 둬 각 신청이 half-open 시험 → 재open을 거치게 함 |
| §7.2 | 측정 결과 `ops/reports/<run_id>/`를 커밋 | `eval/reports`와 같은 관례. 투영 허용 필드만 있어 원문 PII 없음 |
| §1.2 | console `/ops`는 고정비를 계산하지 않음(건당 총비용 "-", 보고서 안내) | console 관리 ID에 Cost Management 권한을 주지 않음 |
