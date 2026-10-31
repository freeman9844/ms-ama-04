# Agentic Cash-Flow Lending 데모 — 설계 Spec

- 작성일: 2026-10-31
- 상태: 설계 승인 완료, 사용자 spec 검토 대기
- 원천 과제: Azure Master Architect Program Capstone — Case Study 18 "Agentic Cash-Flow Lending for an Asian SME Lender"
- 평가 기준: Capstone Assessment Guide (A~D 섹션, 20개 기준, 100점)

---

## 1. 목적과 성공 기준

### 1.1 목적

동의 기반 오픈뱅킹·세무·결제 데이터로 SME 현금흐름 대출을 심사·모니터링·추심하는 **5개 에이전트 시스템**을 Azure(Korea Central)에 실제로 구현합니다. 이 저장소는 두 가지 용도로 함께 쓰입니다.

1. **패널 라이브 데모** — 루브릭 A4(구현 완성도), B3(데모와 executive hook)의 근거
2. **Section D Thought Leadership Artifact** — "Reference implementation or accelerator" 형식. README에 목적, 아키텍처, 설치 방법, 알려진 한계를 포함해야 합니다.

### 1.2 사용자 결정 사항

| 항목 | 결정 |
|---|---|
| 용도 | 패널 데모와 Section D Reference Implementation 겸용 |
| 대상 국가와 리전 | 한국, Korea Central. 법률 세부 사항이 아니라 원칙 수준으로 반영 |
| 구현 기간 | 4~6주 |
| 언어와 에이전트 런타임 | Python, Azure AI Foundry Agent Service |
| UI | 심사역(Credit Officer) 콘솔 웹 UI |
| 환경 수준 | 엔터프라이즈: 전체 VNet 격리와 Private Endpoint |
| 모델 배포 | **Global Standard** (Korea Central regional 배포를 쓰기 어려움) |
| 오케스트레이션 | 접근안 A: Durable Functions 오케스트레이터 + Foundry 에이전트 + 이벤트 기반 L4 에이전트 |

### 1.3 가정

- 실제 마이데이터·홈택스·PG API에는 접근할 수 없으므로 **합성 데이터와 mock API**로 대체합니다.
- 신용 규칙, 한도, 금리 구간은 LLM 밖의 **결정론적 Policy Engine**이 판정합니다.
- 조기경보 데모는 **시간 축을 압축**해 재생합니다(90일 흐름을 수 분 안에 재생).

### 1.4 성공 기준

1. 2분 데모에서 "2주 걸리던 심사가 몇 분 만에 끝나고, 심사역이 근거를 보고 승인하는" 결과를 보여줍니다.
2. 신청 1건당 비용, 처리 시간, 평가 점수를 **실제로 측정한 숫자**로 제시합니다.
3. README와 IaC만으로 제3자가 환경을 재현할 수 있습니다.
4. 과제의 5개 에이전트가 모두 E2E로 동작하고, 미구현 범위는 문서에 먼저 밝혀 둡니다.

### 1.5 비즈니스 KPI와 데모 측정값 연결

| 과제 KPI | 데모에서 측정하는 값 |
|---|---|
| 소액 대출 취급 비용 60% 절감 | 신청 1건당 AI 비용과 인프라 비용(비용 모델, 4.4절) |
| 심사 기간 2주에서 48시간으로 | 신청 접수부터 심사역 대기열 진입까지 걸린 시간(p50/p95) |
| 부도 60~90일 전 조기경보 | 합성 부도 경로 기준 사전 감지 일수 분포 |
| 신용비용 100bp 이상 절감 | 정량 측정 범위 밖. 조기경보 재현율로 간접 근거만 제시 |

---

## 2. 아키텍처

### 2.1 접근안 선택 (ADR-001)

| 접근안 | 요약 | 결정 |
|---|---|---|
| **A. Durable 오케스트레이터 + Foundry 에이전트 + 이벤트 기반 L4** | 신청 건별 진행 흐름은 Durable Functions 상태 그래프로 관리합니다. 각 단계 안에서는 Foundry 에이전트가 도구를 써서 자율적으로 판단합니다. L4 에이전트는 이벤트에 반응합니다. | **채택** |
| B. Foundry 네이티브 Workflows | 선언형 멀티 에이전트. 코드가 가장 적습니다. | 제외: 며칠 단위 HITL 대기, 결정론적 규칙 엔진 연동, 테스트·평가 제어가 약하고, private 네트워크에서 일부 기능이 preview일 위험이 있습니다. |
| C. Microsoft Agent Framework 워크플로 on Container Apps | 코드로 그래프와 handoff를 표현합니다. | 제외: 체크포인트 저장, 장기 대기, 만료 타이머를 직접 구현해야 하고, 프레임워크 성숙도 위험이 있습니다. |

**채택 근거:** 신용 대출은 감사 가능성과 재개 가능성이 핵심입니다. 그래서 흐름은 결정론적으로 두고, 자율성은 에이전트 단계 안으로 제한합니다. 이 원칙은 과제의 "deterministic policy engine + credit officers sanction" 요구와 그대로 맞습니다. Durable Functions의 `waitForExternalEvent`(HITL 대기)와 durable timer(동의 만료)는 요구사항에 바로 대응합니다.

### 2.2 네트워크 토폴로지

- Korea Central에 Spoke VNet 하나를 둡니다. 서브넷은 다음과 같습니다.
  - `snet-agents`: Foundry Agent Service 전용(위임 서브넷, standard agent setup BYO VNet)
  - `snet-functions`: Durable Functions VNet 통합
  - `snet-apps`: Container Apps 환경(콘솔, Policy Engine, Mock API)
  - `snet-pe`: Private Endpoint
  - `snet-appgw`: Application Gateway
  - `AzureBastionSubnet`, `snet-ops`: 운영용 점프 VM 또는 self-hosted runner
- 외부에서 들어오는 경로는 **Application Gateway(WAF v2)와 Entra ID 인증**뿐입니다.
- Foundry, Cosmos DB, Storage, AI Search, Key Vault, Service Bus, ACR은 모두 Private Endpoint와 Private DNS Zone으로만 접근하고, public network access는 끕니다.
- Foundry 리소스와 VNet은 같은 리전(Korea Central)에 둡니다. 공식 문서에서 Korea Central의 Agents와 Private VNet 지원을 확인했습니다(learn.microsoft.com, Foundry Agent Service limits/quotas/regions, 2026-10-29 기준).

### 2.3 컴포넌트

| # | 컴포넌트 | 호스팅 | 책임 | 의존 |
|---|---|---|---|---|
| 1 | Officer Console | Container Apps (FastAPI + HTMX) | 신청 목록, 판단 근거 열람, 승인/조건부/거절, 조기경보 대시보드, 공정성 대시보드, 이의제기 대기열 | Orchestrator API, Cosmos(읽기) |
| 2 | Journey Orchestrator | Azure Functions (Python, Durable Functions) | 신청 건별 상태 그래프, HITL 대기, 재시도와 보상 처리, 단계 계약 검증 | Foundry 에이전트, Policy Engine, Service Bus |
| 3 | Policy Engine | Container Apps (Python) | 버전 관리되는 YAML 규칙으로 자격, 한도, 금리 구간을 판정하고 reason code를 반환 | 없음(순수 함수 서비스) |
| 4 | 에이전트 5종 | Foundry Agent Service (standard setup) | 단계 안의 자율 판단(3장) | 도구 API(Mock API, 데이터 조회, Code Interpreter) |
| 5 | Mock MyData·세무·PG API | Container Apps (FastAPI) | 합성 데이터 제공, 범위 제한·만료 동의 토큰 검증, 거래 흐름 이벤트 재생 | Blob(합성 데이터) |
| 6 | Event Bus | Service Bus (Premium, Private Endpoint) | `consent.*`, `flow.updated`, `alert.raised`, `grievance.*` 이벤트 | 없음 |
| 7 | 데이터 저장소 | Cosmos DB (NoSQL), Blob Storage | 신청, 동의 레지스트리, 판단 기록, 원천 데이터(동의 ID별 컨테이너) | 없음 |
| 8 | 플랫폼 | Key Vault, Managed Identity, App Insights, Log Analytics, AI Content Safety | 비밀 관리, 무키(keyless) 인증, 관측성, Prompt Shields | 없음 |

**경계 원칙**
- 컴포넌트 사이에는 `src/shared/contracts`에 정의한 **버전이 붙은 Pydantic 계약**만 오갑니다.
- 에이전트는 다른 에이전트를 직접 호출하지 않습니다. 단계 간 handoff는 오케스트레이터나 이벤트를 통해서만 일어납니다.
- Policy Engine은 상태를 갖지 않는 순수 함수 서비스입니다. 규칙 버전을 응답에 포함합니다.

### 2.4 확장 경로와 장애 도메인

| 장애 도메인 | 영향 | 격리와 대응 |
|---|---|---|
| Foundry 에이전트·모델 | 심사 단계 지연 또는 실패 | 재시도(지수 백오프), 서킷 브레이커, "수동 심사 전환" 보상 경로 |
| Mock API | 데이터 수집 실패 | 동의 범위 안에서 재시도, 실패 시 "데이터 불완전"으로 심사역에게 넘김 |
| Policy Engine | 판정 불가 | 판정 없이 진행하지 않음(fail-closed). 오케스트레이터가 대기 후 재시도 |
| Service Bus | L4 이벤트 지연 | at-least-once 전달, 소비자 멱등 처리, dead-letter 모니터링 |

**확장:** Functions와 Container Apps는 수평 확장합니다. Foundry 모델은 TPM 할당량이 상한이므로, 대량 거래 분류는 mini급 모델로 분리해 처리량을 확보합니다. 다중 리전 DR은 설계 문서에만 기술합니다.

---

## 3. 에이전트 설계

### 3.1 에이전트 요약

| Agent | 자율성 | LLM 역할 | 결정론적 부분 | 허용 도구 |
|---|---|---|---|---|
| Data Assembly | L3 | 누락·이상 데이터 판단, 보완 요청 결정 | 동의 토큰 범위 검증, 스키마 검증 | Mock API 조회(읽기, 토큰 범위 한정), 가명화 |
| Cash-Flow Underwriting | L2 | 거래 의미 분류, 자율 분석, 대출 구조 제안, 근거 서술, counterfactual 설명 | 한도, 자격, 금리 구간(Policy Engine) | 데이터 조회(읽기), Code Interpreter. **쓰기 도구 없음** |
| Early-Warning | L4 | 의미 기반 스트레스 탐지, 자율 조사, 알림 여부·심각도·권고 조치 결정 | 통계 탐지기(트리거) | 흐름·업종 집단·세무 조회(읽기), `raise_alert` |
| Collections | L3 | 사전 승인 스크립트 선택, hardship 분류 | 공정 추심 규칙(연락 빈도, 시간대) | `send_script(script_id)`, `route_hardship`. 자유 문장 발송 불가 |
| Consent Lifecycle | L4 | 쉬운 말로 된 고지·갱신 안내문 작성 | 범위, 만료, 갱신, 삭제, 삭제 증명 | 동의 레지스트리, 삭제 실행 |

### 3.2 Cash-Flow Underwriting — 분석가와 검토자 반성 패턴

1. **거래 의미 분류:** 은행 거래 적요나 가맹점명처럼 비정형인 한국어 텍스트를 매출, 대표자 개인 이체, 타 금융사 상환, 세금, 급여 등으로 분류합니다. 대량 처리는 mini급 모델이 맡습니다.
2. **자율 분석:** 분석가 에이전트가 도구를 스스로 선택해 DSCR, 현금 버퍼 일수, 매출 변동성, 계절성을 계산합니다. 또 세무 신고 매출과 실제 입금액을 대사해 불일치를 탐지합니다.
3. **대출 구조 제안:** 계절성에 맞춘 상환 스케줄, 금액, 기간을 제안합니다.
4. **검토자 검증:** 검토자 에이전트가 분석가의 모든 주장이 계산된 지표나 거래 근거로 뒷받침되는지(grounding) 검사합니다. 근거 없는 주장은 반려하고, 반성 루프는 최대 3회 돕니다. 3회를 넘기면 "검토 미통과" 표시를 단 채 심사역에게 넘깁니다.
5. **Counterfactual 설명:** "X가 개선되면 승인 가능" 같은 설명을 만들어 심사역용과 이의제기 대응용으로 씁니다.
6. **출력:** `CreditAssessment v1`(추천, 근거 목록[주장, 근거 지표 ID, 근거 거래 ID], 제안 구조, counterfactual). 이어서 Policy Engine이 `PolicyDecision v1`을 판정합니다.

### 3.3 Early-Warning — 탐지와 LLM 조사

1. **트리거:** 통계 탐지기(입금 급감, 잔고 버퍼 감소, 변동성 급증)가 `flow.updated`를 처리하다가 이상을 잡으면 조사를 시작합니다.
2. **의미 탐지:** LLM이 거래 텍스트에서 신규 대부업체나 P2P 상환(loan stacking), 급여 지연, 핵심 거래처 이탈, 세금 체납 같은 패턴을 찾습니다.
3. **자율 조사:** 과거 흐름, 비슷한 업종 집단, 세무 상태를 조회해 가설을 세우고 검증한 뒤, 알림 여부와 심각도, 권고 조치를 결정합니다. 오탐 억제도 이 단계의 역할입니다.
4. **출력:** `EarlyWarningAlert v1`(심각도, 신호 목록과 근거, 권고 조치). `alert.raised` 이벤트로 발행되어 콘솔과 Collections에 전달됩니다.

### 3.4 모델 구성

- 심층 분석과 조사(Underwriting 분석가, Early-Warning 조사): 추론형 상위 모델과 일반 상위 모델 중에서 평가로 결정합니다.
- 대량 거래 분류와 검토자: mini 또는 nano급 모델.
- 배포 유형: **Global Standard**(4.2절 트레이드오프 참고).
- 실제 모델 이름은 구현 시점에 Foundry 카탈로그에서 확인하고, 평가 결과로 확정합니다.

---

## 4. 데이터 흐름, 보안, 운영

### 4.1 신청 1건의 흐름 (Durable Orchestrator)

```text
신청 접수
  → [Consent] 동의 요청(범위: 계좌·세무·PG, 만료일) → 차주 동의(시뮬레이터)
  → [Data Assembly·L3] 동의 토큰으로 수집 → 완전성 검증 → 가명화
       └ 누락 시 동의 범위 안에서 재요청하거나 "보완 필요"로 표시
  → [Underwriting·L2] 분석가 ↔ 검토자 반성 루프(최대 3회) → CreditAssessment
  → [Policy Engine] 결정론적 판정 → PolicyDecision(reason code, 규칙 버전)
  → [HITL] 심사역 승인/조건부/거절 (waitForExternalEvent, SLA 타이머)
  → 실행(데모에서는 상태 전환만) → 모니터링 등록
```

**이벤트 기반 흐름 (오케스트레이터와 분리)**
- **Consent Lifecycle:** 동의마다 durable timer를 겁니다. 만료가 다가오면 갱신 안내를 보내고, 만료되면 `consent.expired`를 발행합니다. 이어서 원천 데이터(Blob 컨테이너, Foundry thread·파일)를 삭제하고 삭제 증명 기록을 남깁니다. 철회(`consent.revoked`)도 같은 경로로 처리합니다.
- **Early-Warning:** Mock API가 대출 실행 이후의 흐름을 `flow.updated`로 재생하면, 탐지와 조사를 거쳐 필요 시 `alert.raised`를 발행합니다.
- **Collections:** 경보나 연체가 생기면 스크립트를 선택하고, hardship이 감지되면 재조정 경로로 보냅니다. 재조정 확정은 사람이 합니다.
- **이의제기:** 이의제기를 접수하면 판단 기록을 조회해 재심사 요청 이벤트를 만들고, 심사역 대기열에 올립니다.

**저장 위치와 보존 정책**

| 데이터 | 저장소 | 보존 |
|---|---|---|
| 원천 거래·세무·PG 데이터 | Blob (동의 ID별 컨테이너) | 동의 만료나 철회 시 삭제 |
| 판단 기록(파생 특징값, reason code, 모델·프롬프트·규칙 버전, 심사역 결정) | Cosmos DB | 장기 보존(설명 가능성) |
| 삭제 증명 | Cosmos DB | 장기 보존 |
| 신청 진행 상태 | Durable Task 저장소 | 신청 종료 후 정리 |

**설명 가능성과 데이터 삭제의 조화:** 원천 데이터는 삭제하고, 판단 기록에는 원천 데이터 없이도 판단을 설명할 수 있는 파생값과 근거 ID만 남깁니다. 삭제는 멱등하게 동작합니다.

### 4.2 보안 위협 모델 (STRIDE 기반)

| 위협 | 대응 |
|---|---|
| 동의 범위를 넘는 데이터 접근 | 동의 토큰에 범위와 만료를 넣고 Mock API가 검증합니다. Data Assembly 도구는 범위 밖 요청을 거부합니다. |
| 간접 prompt injection (거래 적요 등에 명령문 삽입) | Content Safety Prompt Shields, 데이터 구획화(delimiting). LLM 출력은 추천일 뿐이고 판정은 Policy Engine과 심사역이 합니다. 평가 세트에 injection 사례를 포함합니다. |
| 데이터 유출 | 전체 Private Endpoint, Managed Identity(키 없음), 로그·트레이스 PII 마스킹, 모델 전송 전 가명화 |
| 근거 없는 설명(hallucination) | 검토자 grounding 검사. 근거 인용 비율을 지표로 측정합니다. |
| 권한 오남용 | Entra ID 앱 역할(`CreditOfficer`, `CreditManager`, `Auditor`). 승인은 콘솔에서 오케스트레이터 이벤트로만 할 수 있습니다. |
| 에이전트 자율성 이탈 | 에이전트별 도구 allowlist(3.1절). L2에는 쓰기 도구 없음. Collections는 스크립트 ID만 선택 가능 |

**모델 배포와 데이터 레지던시 트레이드오프 (ADR-002)**
- **결정:** Global Standard 배포를 씁니다. Korea Central regional 배포를 쓰기 어렵기 때문입니다.
- **충돌:** 과제의 "processing stays in-country" 제약과 충돌합니다. Global Standard는 추론 요청이 어느 Azure 리전에서든 처리될 수 있습니다. 아시아에는 Data Zone 배포 옵션이 없습니다.
- **완화책:**
  1. 모델 전송 전 가명화: 상호, 대표자명, 계좌번호, 사업자번호를 토큰으로 바꾸고 응답 후 복원합니다.
  2. 최소 전송: 가능한 단계에서는 파생 지표와 집계값만 보냅니다.
  3. 저장 데이터는 국내에: standard agent setup의 BYO Cosmos, Storage, AI Search를 모두 Korea Central에 둡니다.
  4. 가명화 누락률을 측정합니다. 모델로 보내는 payload에서 PII를 스캔합니다.
- **운영 전환 조건:** Korea Central regional 배포나 PTU로 전환합니다. abuse monitoring 데이터 처리 조건도 확인해야 합니다. README의 한계 항목에 명시합니다.

### 4.3 컴플라이언스와 공정성

- **신용정보법·마이데이터 원칙(데모 수준):**
  - 목적별 범위 동의
  - 만료·철회 시 삭제와 증명
  - 국내 저장(추론 처리는 ADR-002 예외)
  - 자동화된 평가에 대한 설명 요구권(counterfactual 설명, 이의제기 채널)
  - 이 데모는 법률 검토를 대신하지 않습니다.
- **공정성 모니터링:** 업종, 지역, 업력 세그먼트별로 승인율과 추천 점수 분포를 대시보드에 보여주고, 격차가 임계값을 넘으면 알림을 냅니다.
- **Dark pattern 금지 체크리스트:** 기본 체크 해제, 범위별 개별 동의, 철회도 동의만큼 쉽게, 만료일 명시.

### 4.4 관측성, SLO, 비용

- **관측성:** OpenTelemetry로 App Insights와 Log Analytics에 수집합니다. `application_id`를 상관관계 ID로 써서 오케스트레이터 → 에이전트 → 도구 → Policy Engine을 분산 트레이스 하나로 이어 봅니다. Foundry 에이전트 트레이싱도 연결하고, 구조화 로그와 비즈니스 메트릭(처리 시간, 단계별 소요, 수동 전환율, 경보 수)을 남깁니다.
- **SLO:**
  - 신청 접수부터 심사역 대기열 진입까지 p95 10분 이하
  - 에이전트 단계 성공률 99% 이상
- **비용 모델:** 신청 1건당 AI 토큰 비용과 인프라 고정비를 건수로 나눈 값을 합쳐 **건당 처리 비용**을 계산합니다. 이를 USD 6,000 티켓 기준 기존 수작업 원가 가정과 비교합니다. 수작업 원가 가정과 그 출처는 문서에 명시합니다.
- **복원력:** 재시도, 서킷 브레이커, durable 재개, 수동 심사 전환. 데모용 장애 주입 시나리오를 1개 이상 둡니다.

---

## 5. 평가 하네스 (`eval/`)

| 대상 | 지표 | 데이터 |
|---|---|---|
| 거래 분류 | macro F1 | 라벨링된 합성 거래 |
| Underwriting | 근거 인용 비율, 세무 대사 불일치 탐지율, Policy 판정과의 일치율, 설명 품질(LLM-judge와 사람 표본 검토) | 합성 신청 케이스 |
| Early-Warning | 정밀도, 재현율, 사전 감지 일수 분포 | 합성 부도·정상 경로 |
| 안전 | injection 방어율, PII 누출률(가명화 누락), 세그먼트별 승인율 격차 | 공격·민감 케이스 세트 |
| 모델 비교 | 품질, p50/p95 지연, 건당 토큰 비용 | 위 데이터셋 전체 |

- Foundry Evaluation과 자체 지표를 함께 씁니다.
- 결과는 `eval/reports/`에 남기고, **실패 유형 카탈로그**(유형, 사례, 대응)를 문서화합니다.
- 평가 하네스는 프롬프트와 모델을 바꿀 때 회귀 테스트로도 실행합니다.

---

## 6. 데모, 저장소, 일정, 테스트

### 6.1 2분 데모 대본

| 시간 | 장면 | 보여줄 결과 |
|---|---|---|
| 0:00 | 문제 제시 | "매출 3억 원 카페의 6,000달러 대출에 2주가 걸립니다." |
| 0:15 | 신청과 실시간 진행 | 동의, 수집, 분석이 콘솔 타임라인에 나타나고 몇 분 안에 끝남 |
| 0:50 | 심사 근거와 승인 | 근거 링크가 붙은 설명(세무·입금 대사, 계절성 상환), 심사역 승인 |
| 1:20 | 조기경보 | 90일 흐름 빨리 재생, loan stacking 감지, 부도 수십 일 전 경보, hardship 경로 |
| 1:45 | 동의 만료 | 원천 데이터 삭제 증명. 판단 근거 기록은 유지 |

- **대비책:** 시간이 오래 걸리는 부분은 미리 실행해 둔 케이스로 보여주고, 라이브 실행은 1건만 합니다. 장애 시 녹화본으로 전환하는 대본을 준비합니다.

### 6.2 저장소 구조

```text
infra/            Bicep + azd (Foundry private network standard agent setup 샘플 기반)
src/orchestrator/ Durable Functions (Python)
src/agents/       에이전트 정의, 프롬프트, 도구 (버전 관리)
src/policy/       결정론적 규칙 엔진 + rules/*.yaml
src/console/      FastAPI + HTMX
src/mockapis/     MyData·세무·PG mock + 합성 데이터 생성기
src/shared/       계약(Pydantic), 가명화, 텔레메트리
eval/             평가 하네스, 데이터셋, 결과 리포트
docs/             아키텍처, ADR, 위협 모델, 비용 모델, 한계
```

- **배포와 운영 경로:** 네트워크가 전부 비공개이므로 Bastion과 VNet 안의 점프 VM, 또는 VNet 안의 self-hosted runner를 통해 배포합니다. 이 경로를 README에 적습니다.

### 6.3 일정 (6주)

| 주차 | 산출물 |
|---|---|
| 1 | 네트워크와 Foundry private 구성, Mock API, 합성 데이터 생성기, 계약 정의 |
| 2 | Orchestrator, Consent, Data Assembly, Policy Engine의 E2E 골격 |
| 3 | Underwriting(분석가와 검토자), 콘솔 승인 흐름 |
| 4 | Early-Warning, Collections, 만료 삭제, 이의제기, **README 초안**(Section D는 패널 2주 전 제출) |
| 5 | 평가 하네스, 대시보드, 비용 모델, 공정성 모니터링 |
| 6 | 강화 작업, 장애 주입, 데모 리허설, 문서 마무리 |

### 6.4 테스트

- **단위(pytest):** Policy 규칙, 계약 검증, 가명화, 삭제 멱등성, 동의 토큰 범위 검증
- **통합:** 오케스트레이터와 Mock API, Policy Engine 연동 흐름
- **AI 회귀:** 평가 하네스(5장)
- **IaC:** Bicep `what-if`, 배포 후 smoke test(Private Endpoint 이름 해석, 신청 1건 E2E)

### 6.5 범위에서 뺀 것

- 실제 마이데이터·홈택스·PG 연동
- 차주용 UI (시뮬레이터로 대체)
- 다중 리전 DR (설계 문서에만 기술)
- 실제 대출 실행과 계정계 연동
- 신용비용(bp) 절감의 정량 검증

---

## 7. 위험과 알려진 한계

| 위험 또는 한계 | 영향 | 대응 |
|---|---|---|
| Global Standard 사용으로 국내 처리 제약 위반 | 과제 요구와 충돌 | ADR-002 완화책. 운영 전환 조건을 명시하고 Q&A에서 먼저 밝힘 |
| Korea Central에서 모델과 도구의 가용성 차이 | 일부 도구(Code Interpreter 등) 사용 제한 가능 | 1주차에 리전·모델·도구 매트릭스 확인. 막히면 도구를 함수 도구로 대체 |
| 전체 비공개 네트워크의 배포 복잡도 | 일정 지연 | Foundry 공식 private network 샘플을 바탕으로 1주차에 먼저 검증 |
| 합성 데이터의 현실성 한계 | 평가 수치의 외적 타당성 제한 | 생성 규칙과 한계를 문서화하고, 수치는 "합성 데이터 기준"으로 표기 |
| 4~6주 일정 | 기능 축소 위험 | 2주차에 E2E 골격을 먼저 연결하고, 이후 깊이를 더하는 순서 유지 |

---

## 8. 루브릭 매핑

| 기준 | 이 설계의 근거 |
|---|---|
| A1 | 컴포넌트 경계, 버전이 붙은 계약, 장애 도메인 표(2.3, 2.4절) |
| A2 | ADR-001(오케스트레이션), ADR-002(모델 배포) — 검토하고 제외한 대안과 이유 |
| A3 | STRIDE 위협 모델, 가명화, Prompt Shields, 도구 allowlist(4.2절) |
| A4 | 5개 에이전트 E2E, 한계 사전 공개(6.5절, 7장) |
| A5 | OTel 트레이스, SLO, 건당 비용 모델, 복원력 패턴(4.4절) |
| A6 | 모델 비교와 평가 하네스, 실패 유형 카탈로그(3.4절, 5장) |
| A7 | 자율성 수준별 가드레일, 분석가·검토자 반성 패턴, 상태 그래프, 이벤트 기반 L4(3장) |
| B3, B7 | 2분 데모 대본, 측정값 기반 KPI 연결(1.5절, 6.1절) |
| D | README 포함 Reference Implementation(6.2절) |
