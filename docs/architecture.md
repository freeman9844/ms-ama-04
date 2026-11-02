# 아키텍처

동의 기반 합성 데이터로 SME 현금흐름 대출을 심사·모니터링·추심하는 에이전트 시스템의 as-built 아키텍처다. 신청 건별 흐름은 Durable Functions 오케스트레이터가 결정론적 상태 그래프로 관리하고, 각 단계 안에서 Foundry 프롬프트 에이전트가 도구를 써서 자율 판단한다. 신용 규칙은 LLM 밖의 결정론적 Policy Engine이 판정하고, 최종 결정은 심사역이 콘솔에서 내린다. 데이터·제어 평면은 VNet 격리 + Private Endpoint로 닫혀 있고 무키(managed identity) 인증을 쓰며, 외부로 열린 진입점은 운영자 IP 허용목록과 Entra 인증을 건 콘솔뿐이다.

> **합성 데이터 기준.** 실제 마이데이터·홈택스·PG API 대신 합성 데이터와 Mock API를 쓴다. 실측 수치는 `ops/reports/load-20261102110233/report.md`, `eval/reports/job-eval-9imbutm/report.md`에서 인용하며, 배포·운영 실행 기록은 [runbook/deploy-history.md](runbook/deploy-history.md)에 있다.

> 상위 Spec 대비: 설계 Spec(`docs/superpowers/specs/2026-10-31-agentic-cashflow-lending-design.md`)은 에이전트를 5종으로 묶었으나, 구현은 역할을 8개 `AgentSpec`으로 분리했다(아래 [에이전트](agents.md) 참고). 코드가 기준이다.

## 시스템 컨텍스트

```mermaid
flowchart LR
  borrower["차주 시뮬레이터"]
  officer["심사역 / 심사 관리자 / 감사자"]
  operator["운영자"]
  subgraph azure["Azure 경계 (Korea Central, VNet 격리)"]
    console["Officer Console"]
    orch["Journey Orchestrator"]
    foundry["Foundry 에이전트 + 모델"]
    policy["Policy Engine"]
    mock["Mock MyData/세무/PG API"]
  end
  borrower -->|신청 제출| orch
  officer -->|로그인 / 승인·조건부·거절| console
  operator -->|상시: IP 허용 + Entra| console
  operator -.->|배포 창: 데이터 평면 작업| orch
  console --> orch
  orch <--> foundry
  orch <--> policy
  orch <--> mock
```

차주는 접수 경로로, 심사역·운영자는 콘솔로 들어온다. 콘솔은 상시 external이지만 운영자 IP 허용목록(`infra/resources.bicep:599`, `infra/modules/container-app.bicep:42-46`)과 Entra 인증으로 막는다. 운영자 데이터 평면 작업(Functions·Foundry·ACR 빌드)만 배포 창에서 잠시 열린다(아래 [네트워크](#네트워크)).

## 컨테이너

```mermaid
flowchart TB
  subgraph web["external 환경 cae-web (snet-apps-web)"]
    console["Officer Console<br/>FastAPI + HTMX"]
  end
  subgraph fn["Azure Functions Flex (snet-functions)"]
    orch["Journey Orchestrator<br/>Durable Functions"]
  end
  subgraph int["internal 환경 cae-int (snet-apps-int)"]
    policy["Policy Engine"]
    mock["Mock API"]
    smoke["smoke / eval / ops Job"]
  end
  foundry["Foundry Agent Service<br/>프롬프트 에이전트 + 모델"]
  search["AI Search"]
  acr["Container Registry"]
  cosmos["Cosmos DB (NoSQL)"]
  blob["Blob Storage"]
  queues["Storage Queues"]
  sb["Service Bus (Premium)"]
  kv["Key Vault"]
  obs["App Insights / Log Analytics"]

  console -->|읽기| cosmos
  console -->|명령| queues
  queues --> orch
  orch --> foundry
  foundry -.->|벡터 저장소| search
  orch --> policy
  orch --> mock
  orch --> cosmos
  orch --> blob
  orch -.->|이벤트, 활성 시| sb
  sb -.-> orch
  orch --> kv
  acr -.->|이미지| console
  acr -.->|이미지| int
  console --> obs
  orch --> obs
  policy --> obs
```

| 구성요소 | 호스팅 | 책임 | 데이터 | 인증 | 코드 경로 |
|---|---|---|---|---|---|
| Officer Console | Container Apps external `cae-web` | 신청 목록·근거 열람, 승인/조건부/거절, 조기경보·공정성·이의제기 화면 | Cosmos(읽기), 큐(명령 전송) | Easy Auth(Entra) + 운영자 IP 허용. `consoleAuthEnabled=false`면 데모 고정 역할 | `src/console/app.py`, `src/console/auth.py` |
| Journey Orchestrator | Azure Functions Flex Consumption `FC1`(`snet-functions`) | 신청 건별 상태 그래프, HITL 대기, 재시도·보상, 단계 계약 검증, 계량 | Cosmos, Blob, 큐, (활성 시) Service Bus | Managed Identity(`id-orch`) | `src/orchestrator/function_app.py`, `src/orchestrator/journey/` |
| Policy Engine | Container Apps internal `cae-int` | 버전드 YAML 규칙으로 자격·한도·금리 구간 판정, reason code 반환(순수 함수) | 없음 | Managed Identity(`id-apps`), 환경 내부 ingress(`internal: true`) | `src/policy/app.py`, `src/policy/engine.py`, `src/policy/rules.py` |
| Mock API | Container Apps internal `cae-int` | 합성 원천 데이터 제공, 범위·만료 동의 토큰 검증, 흐름 재생 | 합성 데이터(프로세스 내 생성) | Managed Identity(`id-apps`), 환경 내부 ingress, `MOCK_CONSENT_SECRET` | `src/mockapis/app.py`, `src/mockapis/consent.py`, `src/mockapis/postloan.py` |
| 에이전트(8종) | Foundry Agent Service(standard setup, capability host) | 단계 안 자율 판단. 사내 도구는 오케스트레이터가 실행(function tool) | 도구 경유 | Managed Identity(`id-orch`) → `Azure AI User`/`Cognitive Services OpenAI User` | `src/agents/` |
| AI Search | Azure AI Search Basic + PE | Foundry 벡터 저장소 연결(지식 인덱스) | 인덱스 | System-assigned MI, `publicNetworkAccess: disabled` | `infra/modules/search.bicep` |
| Container Registry | Azure Container Registry + PE | 콘솔·Policy·Mock·Job 컨테이너 이미지 | 이미지 | MI `AcrPull`, 배포 창에 운영자 push | `infra/modules/acr.bicep` |
| smoke/eval/ops Job | Container Apps Job internal `cae-int` | 배포 후 스모크, 분류·심사 평가, 운영 리포트 생성 | Cosmos·Blob(읽기) | Managed Identity(`id-smoke`/`id-eval`/`id-ops`) | `infra/modules/smoke-job.bicep`, `infra/modules/eval-job.bicep` |
| Service Bus | Service Bus Premium + PE | `consent`·`flow.updated`·`alert.raised`·`grievance` 토픽. 평상시 pause(삭제) | 없음 | Managed Identity | `infra/modules/servicebus.bicep`, `src/ops/lifecycle.py` |
| 데이터 저장소 | Cosmos DB(NoSQL), Blob Storage | 신청·동의·분류·대출·경보·삭제 증명·이의, 원천 데이터·리포트 | — | Cosmos SQL RBAC, Storage Entra RBAC | `infra/modules/cosmos.bicep`, `infra/modules/storage.bicep` |
| 플랫폼 | Key Vault, Managed Identity, App Insights, Log Analytics | 비밀·무키 인증·관측성 | — | RBAC | `infra/modules/keyvault.bicep`, `infra/modules/identities.bicep`, `infra/modules/monitoring.bicep` |

**경계 원칙:** 구성요소 사이에는 `src/shared/contracts.py`의 버전드 Pydantic 계약만 오간다. 에이전트는 서로 직접 호출하지 않고, handoff는 오케스트레이터나 이벤트로만 일어난다. 사내 도구(Mock API·Policy·데이터 조회)는 Foundry가 아니라 VNet 안 오케스트레이터가 실행해 감사 로그에 남긴다.

> 상위 Spec 대비: Spec §2.3은 Service Bus Premium을 상시 가정하지만, 비용 때문에 평상시 pause(네임스페이스 삭제)한다. 이때 `SERVICEBUS_FQDN`이 비어 트리거를 등록하지 않고, `src/orchestrator/journey/monitoring.py`의 `handle_replay`가 `monitoring-replay` 큐로 같은 처리를 인라인 수행한다.

## 네트워크

```mermaid
flowchart TB
  subgraph vnet["Spoke VNet 192.168.0.0/16 (Korea Central)"]
    agents["snet-agents 192.168.0.0/24<br/>Foundry Agent"]
    pe["snet-pe 192.168.1.0/24<br/>Private Endpoint"]
    fn["snet-functions 192.168.2.0/26<br/>Functions Flex"]
    appsint["snet-apps-int 192.168.4.0/24<br/>cae-int (internal)"]
    appsweb["snet-apps-web 192.168.5.0/24<br/>cae-web (external)"]
  end
  operator["운영자 IP"]
  pe -->|Private DNS| data["Foundry·Cosmos·Storage·Search·KV·Service Bus·ACR·Functions"]
  operator -->|상시: IP 허용 + Entra| appsweb
  operator -.->|배포 창: public 허용| fn
  appsweb --> appsint
  fn --> appsint
```

평상시 모든 PaaS 리소스의 public network access는 꺼져 있고 Private Endpoint로만 접근한다. 운영자 데이터 평면 작업(배포·시드)에는 `operatorIp` 파라미터로 Functions·Foundry·ACR(빌드)의 public access를 잠시 켜고 기본 거부 + 운영자 IP 허용을 적용한 뒤 작업이 끝나면 다시 끈다(`infra/modules/functions.bicep`: `publicNetworkAccess`는 `operatorIp`가 있을 때만 `Enabled`). 콘솔은 external 환경이지만 상시 운영자 IP 허용목록(`infra/resources.bicep:599`, `infra/modules/container-app.bicep:42-46`)과 Entra 인증으로 막으며, 배포 창 토글과 무관하게 열려 있다. internal 환경 앱 앞의 Application Gateway(WAF v2)는 운영 아키텍처 문서에만 둔다.

| 서브넷 | 주소 | 위임 | 용도 |
|---|---|---|---|
| `snet-agents` | `192.168.0.0/24` | 예 | Foundry Agent Service 전용 |
| `snet-pe` | `192.168.1.0/24` | 아니오 | Private Endpoint |
| `snet-functions` | `192.168.2.0/26` | 예 | Functions Flex Consumption VNet 통합 |
| `snet-apps-int` | `192.168.4.0/24` | 예 | internal Container Apps 환경(Policy·Mock·Job) |
| `snet-apps-web` | `192.168.5.0/24` | 예 | external Container Apps 환경(콘솔만) |

주소·프리픽스는 `infra/modules/network.bicep`의 템플릿 값이다. Private Endpoint·DNS zone은 `infra/modules/pe.bicep`와 `infra/modules/dns.bicep`로 리소스별(`infra/resources.bicep` 호출부)에 붙는다: Key Vault, Storage(blob/queue/table), Cosmos, AI Search, Foundry 계정, Service Bus, ACR, Functions.

## 신청 1건 흐름

```mermaid
sequenceDiagram
  participant Q as 접수 큐 applications-start
  participant O as Orchestrator
  participant M as Mock API
  participant A as 에이전트
  participant P as Policy Engine
  participant C as Cosmos
  participant U as 심사역
  Q->>O: 신청 접수
  O->>M: 동의 요청·토큰 발급 (동의)
  O->>A: 데이터 수집 (data-assembly, L3)
  A->>M: fetch_source (동의 범위 한정)
  O->>O: 심사 지표 산출 — 거래 분류 포함 (classify-v1 → Decision-1)
  O->>P: 정책 판정 (fail-closed)
  O->>A: 신용 평가 (analyst ↔ reviewer, 최대 3회)
  O->>C: 단계별 스냅샷 저장
  O->>U: 심사역 대기 (wait_for_external_event, SLA 타이머)
  U->>O: 승인 / 조건부 / 거절
  O->>C: 모니터링 등록 (거절 아니면)
```

단계 이름은 `src/opsmetrics/labels.py`의 `STEP_LABELS`를 따른다: 동의(consent) → 데이터 수집(assemble) → 거래 분류(classify) → 심사 지표 산출(underwrite) → 정책 판정(policy) → 신용 평가(assess). 거래 분류는 심사 지표 산출 활동 안의 하위 단계다(`src/orchestrator/journey/metering.py`). 데이터가 불완전하면(`FLAG_DATA_INCOMPLETE`) 심사를 건너뛰고 바로 심사역 대기로 간다(`src/orchestrator/journey/orchestration.py`).

## 사후 흐름

```mermaid
flowchart LR
  replay["monitoring-replay 큐<br/>(시간 압축 재생)"]
  ew["early-warning-investigator (L4)"]
  alert["alert.raised"]
  col["collections-agent (L3)"]
  hardship["restructure-decisions 큐<br/>(재조정은 심사역)"]
  replay --> ew
  ew -->|raise_alert| alert
  alert --> col
  col -->|route_hardship| hardship

  subgraph consent["동의 수명주기 (consent_lifecycle 오케스트레이션)"]
    renew["갱신 안내"] --> expire["만료"] --> delete["원천 삭제"] --> proof["삭제 증명"]
  end
  grv["grievances 큐"] --> reply["grievance-reply-agent"]
```

조기경보는 `flow.updated`(활성 시 Service Bus, 평상시 `monitoring-replay` 큐)로 재생되어 통계 탐지기 트리거 시 조사 에이전트가 돈다. 경보는 `alert.raised`로 Collections에 전달된다. 동의마다 `consent_lifecycle` 오케스트레이션(인스턴스 `consent-<id>`)이 만료까지 기다렸다가 원천 데이터(Blob)·목업 레지스트리·Foundry 대화를 지우고 `deletion_proofs`에 삭제 증명을 남긴다(`src/orchestrator/journey/consent.py`). 이의제기는 `grievances` 큐로 접수되어 회신 초안을 만든다(`src/orchestrator/journey/grievance.py`).

## 구성요소 계약

모든 계약은 `src/shared/contracts.py`에 있고, `Contract` 기반(frozen, `extra="forbid"`)에 `schema_version="1"`을 붙인다.

| 계약 | 용도 |
|---|---|
| `ConsentGrant` v1 | 동의 토큰(범위·만료) |
| `ApplicantProfile` v1 | 사업자 프로필(업종·지역·업력) |
| `Transaction` v1 | 거래(유형 코드·적요·금액) |
| `TaxFiling` v1 | 세무 신고 |
| `PgSettlement` v1 | 카드·PG 정산 |
| `CashFlowFeatures` v1 | 심사 지표(DSCR·버퍼·변동성 등) |
| `PolicyDecision` v1 | 정책 판정(자격·한도·금리·`rules_version`·reason code) |
| `CreditAssessment` v1 | 평가(추천·근거 `Claim`·`ProposedStructure`·`Counterfactual`) |
| `EarlyWarningAlert` v1 | 경보(심각도·`AlertSignal`·권고 조치) |

`ReasonCode`, `Claim`, `ProposedStructure`, `Counterfactual`, `ReviewOutcome`, `AlertSignal`은 위 계약의 하위 모델이다.

## 장애 도메인

| 장애 도메인 | 영향 | 격리·대응(as-built) | 실측 증거 |
|---|---|---|---|
| Foundry 에이전트·모델 | 심사 단계 지연·실패 | 평가 활동은 예외를 올리지 않고 `assessment_unavailable` 플래그만 남긴 채 심사역 단계로 넘김(결정 권한 없음). 안전망·템플릿 폴백 | 에이전트 단계 성공률 100.0%(ok 72/72) `ops/reports/load-20261102110233/report.md` |
| Mock API | 데이터 수집 실패 | 동의 범위 안 재시도 + 안전망 수집. 불완전이면 "데이터 불완전"으로 심사역에게 넘김 | source-flaky 회복: mockapi 호출 일부 실패에도 전 신청 `awaiting_officer` 복귀(`ops/reports/fault-20261102111447/`) — `ops/reports/load-20261102110233/report.md` 4장 |
| Policy Engine | 판정 불가 | fail-closed: 판정 없이 진행 안 함. `policy_unavailable`+`assessment_unavailable` 플래그, 심사역 판단으로 넘김. 호출에 프로세스 단위 서킷(`src/orchestrator/journey/circuit.py`) | policy-down: 3건 모두 `policy:failed`로 멈추고 `awaiting_officer`(`ops/reports/fault-20261102111750/`) — `ops/reports/load-20261102110233/report.md` 4장 |
| Service Bus | L4 이벤트 지연 | 세션 구독(`requiresSession`)·중복 감지(멱등 지원)·만료 dead-letter. 평상시 pause 시 `monitoring-replay` 큐 인라인 처리 | 세션·중복 감지·`deadLetteringOnMessageExpiration` 설정 `infra/modules/servicebus.bicep` |

**한계:** 서킷 브레이커는 프로세스(Flex 인스턴스) 단위다(`src/orchestrator/journey/circuit.py`). Flex가 여러 인스턴스로 확장되면 서킷 상태가 인스턴스별로 따로 쌓여 전역 open이 보장되지 않는다. 분산 서킷은 운영 전환 과제다(ADR-007 범위).

## 확장 경로

| 축 | 현재 | 확장 |
|---|---|---|
| 처리량 | Functions Flex `maximumInstanceCount: 40`, Container Apps 수평 확장 | 인스턴스 상한 상향, 동시성 조정 |
| 데이터 | Cosmos DB 기본 RU | 컨테이너별 RU 상향·자동 확장 |
| 모델 | Global Standard, 배포 `gpt-5.4`/`gpt-5.4-mini`/`decision-1` | TPM 쿼터 상향 |
| 분류 비용 | 규칙(classify-v1) 선분류 + 정규화·중복 제거 + 캐시 + Decision-1, 폴백만 mini LLM | 규칙 커버리지 80.0%·캐시 적중 25.0%. `decision-1+mini` 구성은 범용 LLM 폴백 비율 25.0%(mini 단독 100.0%)로 범용 LLM 호출을 줄인다. Decision-1 호출은 신청당 8.375회지만 단가 미확인이라 총비용 효과는 미검증(`eval/reports/job-eval-9imbutm/report.md` 2장) |
| 지연·단가 | Global Standard | PTU 전환(예측 가능 지연·단가, ADR-002) |
| 가용성 | 단일 리전(Korea Central) | 다중 리전 DR(문서만) |

## 운영 아키텍처

이 절은 데모에 포함하지 않은 운영 전환 과제(미구축)다.

| 항목 | 데모 | 운영 전환 시 |
|---|---|---|
| 외부 진입 | 콘솔만 external, 운영자 IP + Entra | internal 환경 앞 Application Gateway(WAF v2) + Entra 단일 진입(월 약 $365, 상위 Spec §2.2) |
| 심사역 인증 | Easy Auth implicit id_token, `consoleAuthEnabled=false` 시 데모 역할 | Entra 로그인 복원(비밀 또는 FIC 기반 인증 코드 흐름, ADR-007) |
| 서킷 브레이커 | 프로세스(Flex 인스턴스) 단위 | 분산 서킷(공유 상태) |
| 가용성 | 단일 리전 | 다중 리전 DR |
| 모델 배포 | Global Standard | Data Zone / PTU(ADR-002) |
| 입력 방어 | 없음(미구축) | AI Content Safety Prompt Shields 등 프롬프트 입력 방어(상위 Spec §2.3). 현재 `infra/`·`src/`에 Content Safety/Prompt Shields 구현 없음 |

ADR-001·ADR-002·ADR-007 등은 별도 ADR 문서로 정리 예정이다.
