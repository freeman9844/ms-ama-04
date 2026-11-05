# AI Gateway(API Management) 도입 설계

상태: 승인(2026-11-05). 계획 작성 중 보완: 5.1·5.2·6·11절, 이름 `apim-<접미사>`, 공인 IP 제거, 프로브 구독 `gw-probe`, 롤백 스위치 `AI_GATEWAY_ROUTING`. 앞 단계: P9 고객 접수·심사역 UX(배포 완료, `85dc7b9`).

## 1. 목표

모든 운영 LLM 호출이 Azure API Management(AI Gateway)를 거치게 한다. 게이트웨이는 다음 네 가지를 맡는다.

- **단일 경로:** 에이전트 8종과 Decision-1 분류 호출이 모두 APIM을 지난다.
- **토큰 한도:** 에이전트마다 TPM 한도를 두고, 초과하면 429로 막는다(시연 가능해야 한다).
- **토큰 지표:** 에이전트·모델 차원으로 토큰 사용량을 Azure Monitor에 남긴다.
- **콘텐츠 안전:** 프롬프트 주입과 유해 범주를 게이트웨이에서 막고, 막힌 호출은 기존 폴백 경로로 넘긴다.

근거: 루브릭 A5(운영·비용 통제)와 A6(보안·책임 있는 AI). 지금은 Foundry 배포 쿼터 말고는 에이전트별 사용량 통제·관측 지점이 없다.

범위 밖: 평가 채점 모델(judge) 호출, 운영 환경용 Standard v2 이상 티어, APIM 개발자 포털, 다중 지역 백엔드 부하 분산, 의미 캐시.

## 2. 결정

| 항목 | 결정 |
|---|---|
| 티어 | **Developer**(Korea Central 시간당 $0.07, 월 약 $51). SLA가 없고 단일 인스턴스다. 운영 환경 권장안은 Standard v2(월 약 $701)이며 ADR에 적는다. |
| 이름 | `apim-<접미사>`. APIM 이름은 전역 고유라서 다른 리소스와 같은 접미사 규칙을 쓴다. |
| 네트워크 | VNet **내부 모드**. 새 서브넷 `snet-apim`(192.168.6.0/27, 위임 없음)과 NSG. 2024년 5월 이후 내부 모드 VNet 주입은 공인 IP가 필요 없다. 게이트웨이는 사설 IP로만 받는다. |
| 연결 방식 | 접근법 A. 에이전트마다 Foundry 프로젝트 연결(범주 `ApiManagement`) `gw-<약칭>`과 전용 APIM 구독 키 1개. 에이전트 모델 값은 `gw-<약칭>/<배포>`. |
| 키 관리 | 구독 키는 Bicep `listSecrets()`로 연결에 바로 주입한다. 저장소·Key Vault·배포 출력에 남기지 않는다. |
| Decision-1 | 오케스트레이터·eval 관리 ID의 Entra 토큰을 APIM `validate-azure-ad-token`으로 검증한다. 비밀 없음. |
| 콘텐츠 안전 동작 | **차단 후 폴백**. 403이면 해당 에이전트 단계는 기존 실패 폴백을 타고, 단계 기록에 `content_blocked`가 남는다. |
| 롤백 | azd 환경값 `AI_GATEWAY_ROUTING`(기본 `true`)을 `false`로 두고 provision하면 APIM은 남기고 앱 환경변수 `AI_GATEWAY`를 `off`로 바꾼다. 값이 `apim`이 아니면 에이전트와 Decision-1 모두 지금처럼 Foundry를 직접 호출한다. 로컬 테스트 기본값도 직접 호출이다. |
| 진단 | 기존 App Insights·Log Analytics로 보낸다(게이트웨이 로그 `allLogs`, 사용자 지정 지표 `ama-llm`). 프롬프트·응답 본문(LLM 메시지 로그)은 수집하지 않는다. 안정 API 진단 스키마에 해당 설정이 없고, 개인정보 최소화 원칙과도 맞다. |

## 3. 구성 요소와 네트워크

```
[에이전트 런타임(Foundry Agent Service, snet-agents)]
        │ 연결 gw-<약칭> (구독 키)
        ▼
[APIM apim-<접미사> (snet-apim, 내부 VIP)] ──관리 ID──▶ [Foundry 계정 Private Endpoint (snet-pe)]
        ▲
        │ Entra 토큰 (Decision-1)
[오케스트레이터 / eval job (snet-apps-int)]
```

- **사설 DNS:** `azure-api.net` 영역에 게이트웨이 A 레코드를 두고 VNet에 연결한다.
- **APIM → Foundry:** APIM 시스템 관리 ID에 Foundry 계정 `Cognitive Services User` 역할을 준다. 호출은 기존 Private Endpoint로 간다. Foundry 계정은 지금처럼 공용 접근 Disabled다(배포 창 동안만 열림).
- **APIM API 2개:**
  - `openai`: Chat Completions(에이전트 연결용). 백엔드는 Foundry 계정의 OpenAI 엔드포인트.
  - `decision`: Decision-1 경로(`/providers/microsoft/v1/systemone`) 통과.
- **Content Safety 백엔드:** Foundry(AIServices) 계정의 Content Safety 엔드포인트. 인증은 APIM 관리 ID.

### 3.1 에이전트·연결·구독 대응

| 에이전트 이름 | 연결·구독 | 모델 배포 | TPM 초기값 |
|---|---|---|---|
| `underwriting-analyst` | `gw-analyst` | gpt-5.4 | 20K |
| `early-warning-investigator` | `gw-early-warning` | gpt-5.4 | 20K |
| `underwriting-reviewer` | `gw-reviewer` | gpt-5.4-mini | 15K |
| `data-assembly` | `gw-data-assembly` | gpt-5.4-mini | 15K |
| `txn-classifier` | `gw-txn-classifier` | gpt-5.4-mini | 10K |
| `collections-agent` | `gw-collections` | gpt-5.4-mini | 10K |
| `consent-notice-agent` | `gw-consent-notice` | gpt-5.4-mini | 5K |
| `grievance-reply-agent` | `gw-grievance` | gpt-5.4-mini | 5K |
| `gateway-probe`(관문·시연 프로브) | `gw-probe` | gpt-5.4-mini | 1K |

- TPM 합계는 as-built 배포 용량 안이다. gpt-5.4는 GlobalStandard 200K(`gw-analyst` 150K + `gw-early-warning` 50K), gpt-5.4-mini는 100K다. gpt-5.4가 DataZoneStandard면 쿼터가 100K라 배포 한도(100K)가 APIM TPM보다 먼저 걸린다.
- 초기값은 계획 단계 부하 테스트(`ops load`)로 다시 맞춘다. 기준은 측정된 분당 최대 사용량의 2배 이상이며, 부하 테스트에서 429가 나면 안 된다.
- `gw-probe`는 프로브 전용 연결·구독이다. 사전 검증과 429·차단 시연에만 쓰고 업무 에이전트는 쓰지 않는다.

## 4. APIM 정책

### 4.1 `openai` API (구독 키 인증)

`inbound` 순서:

1. 구독 키 확인(APIM 기본). 구독 이름이 에이전트 차원 값이 된다.
2. `llm-content-safety`: 혐오·성·자해·폭력 4개 범주, 심각도 4 이상 차단. `shield-prompt="true"`로 프롬프트 주입 탐지. 탐지되면 403을 돌려준다. 런타임은 오류 본문의 콘텐츠 안전 표식으로 차단을 분류한다(실제 형태는 관문 5에서 확인).
3. `llm-token-limit`: 카운터 키는 구독 ID. 초과하면 429와 `Retry-After`. 남은 토큰은 응답 헤더로 알린다.
4. `authentication-managed-identity`(리소스 `https://cognitiveservices.azure.com`)로 백엔드 호출.
5. `llm-emit-token-metric`: 네임스페이스 `ama-llm`, 차원 `agent`(구독 이름)·`model`(배포 이름)·`api`.

### 4.2 `decision` API (Entra 토큰)

- `validate-azure-ad-token`: 테넌트와 대상(`https://cognitiveservices.azure.com`)을 확인하고, 클레임 `appid`가 오케스트레이터 또는 eval job 관리 ID일 때만 허용한다.
- Decision-1은 OpenAI 형식이 아니라서 토큰 정책을 쓸 수 없다. 대신 `rate-limit-by-key`(키 = `appid`)로 분당 호출 수를 제한하고, `emit-metric`으로 호출 수를 남긴다.
- 콘텐츠 안전은 적용하지 않는다. 입력이 가명 처리된 짧은 적요뿐이라는 근거와 함께 ADR에 예외로 적는다.
- 백엔드 인증은 APIM 관리 ID로 바꿔 보낸다(호출자 토큰을 그대로 넘기지 않는다).

## 5. 코드 변경

### 5.1 게이트웨이 모델 이름 (새 모듈 `src/agents/gateway.py`)

- `CONNECTIONS`: 3.1 표의 에이전트 이름 9개(프로브 포함) → 연결 이름 대응.
- `gateway_enabled(env) -> bool`: `env.get("AI_GATEWAY") == "apim"`.
- `gateway_model(agent: str, deployment: str) -> str`: `f"{CONNECTIONS[agent]}/{deployment}"`. 모르는 에이전트 이름이면 `KeyError`(조용히 직접 호출로 새지 않게).
- `GatewayRunner(inner)`: 에이전트 실행기 감싸개. `run(spec, ...)`에서 `spec.model`만 `gateway_model(spec.name, spec.model)`로 바꿔 `inner.run`에 넘기고, 나머지 속성은 `inner`에 위임한다.
- 적용 위치:
  - 오케스트레이터: `adapters.agent_settings`에서 게이트웨이가 켜져 있으면 `FoundryAgents`를 `GatewayRunner`로 감싼다. 모델 설정 8곳은 바꾸지 않는다.
  - eval: `EvalRunner.run`이 에이전트 이름을 `eval-<이름>-<모델>`로 바꾸기 전에, 원래 이름으로 `gateway_model`을 적용한다. judge(`quality-judge`)는 제외한다.

### 5.2 비용·계측

- 오케스트레이터의 `MeteredRunner`는 `GatewayRunner` 바깥을 감싼다. 그래서 사용량 항목, 스팬 속성 `gen_ai.request.model`, 단가 조회가 모두 원래 배포 이름을 그대로 본다.
- eval 미터 범위도 원래 `spec.model`을 쓴다.
- 따라서 `metering.py`, `src/opsmetrics/prices.py`, `src/evals/prices.json`, 비용 대시보드는 바꾸지 않는다.

### 5.3 오류 분류 (`src/agents/runtime.py`)

- `GatewayBlocked(AgentError)`: HTTP 403이고 본문에 콘텐츠 안전 표식이 있을 때. `safe_detail` 결과가 `content_blocked`가 되도록 메시지를 이 토큰으로 시작한다(원문은 남기지 않는다).
- `GatewayThrottled(AgentError)`: HTTP 429. `Retry-After`(최대 10초)를 기다려 한 번만 재시도한다. 또 실패하면 예외를 올린다.
- 그 밖의 4xx·5xx는 지금처럼 `AgentError`.
- `FoundryAgents.run`이 예외를 다시 감쌀 때 `AgentError`로 바꿔 하위 클래스가 사라지는 문제를 함께 고친다(`type(e)(...)`).
- 403·429가 Agent Service 실행 안에서 어떤 형태(HTTP 상태 또는 응답 `status=failed`의 `error.code`)로 돌아오는지는 사전 검증 관문에서 실측하고, 분류 규칙을 그 형태에 맞춘다.

### 5.4 폴백과 콘솔 표시

- 차단·제한 예외는 기존 에이전트 실패 폴백을 탄다(심사 → 사람 검토, 거래 분류 → 규칙 기반, 안내문 → 기본 문안). 폴백 로직은 바꾸지 않는다.
- 심사 실패는 `rounds[].analyst_error`에, 분류 폴백 오류는 건수로만 남아 단계 `detail`에 차단 사실이 항상 실리지 않는다. 그래서 `MeteredRunner`가 실패한 호출의 사용량 항목에 `gateway: content_blocked`(또는 `rate_limited`)를 붙인다. 비용 집계는 모델·토큰만 읽으므로 영향이 없다.
- 콘솔 신청 상세는 단계 사용량 항목에 `gateway: content_blocked`가 있는 단계(데이터 수집·거래 분류·신용 평가)를 모아 "콘텐츠 차단 · <단계>" 배지를 보인다.

### 5.5 Decision-1 (`src/agents/txn_classify.py`, `src/evals/backend.py`)

- 게이트웨이가 켜져 있고(`AI_GATEWAY=apim`) `AI_GATEWAY_URL`이 있으면 `{AI_GATEWAY_URL}/decision` + 기존 경로로 호출한다. 토큰 스코프(`https://cognitiveservices.azure.com/.default`)는 같다.
- 그렇지 않으면 지금처럼 `AI_ACCOUNT_ENDPOINT`를 직접 호출한다. 그래서 롤백 스위치가 Decision-1에도 적용된다.

### 5.6 인프라

- `infra/modules/network.bicep`: `snet-apim`과 NSG(APIM 내부 모드 필수 규칙: 관리 3443 인바운드, Azure Load Balancer 6390, Storage·SQL·Key Vault·Monitor 아웃바운드).
- 새 모듈 `infra/modules/apim.bicep`: 서비스, 관리 ID와 역할 할당, 백엔드, API 2개, 정책, 제품·구독 9개, 로거·진단 설정, `azure-api.net` A 레코드.
- `infra/resources.bicep` `dnsZoneNames`: `azure-api.net` 사설 영역 추가(A 레코드는 APIM 모듈이 만든다).
- 새 모듈 `infra/modules/foundry/gateway-connections.bicep`: 프로젝트 연결 9개(`category: 'ApiManagement'`, 인증 `ApiKey`, 키는 `listSecrets()`).
- `infra/resources.bicep`: 오케스트레이터와 eval job 환경변수 `AI_GATEWAY`(`aiGatewayRouting ? 'apim' : 'off'`, 빈 값은 Container Apps가 거부할 수 있어 쓰지 않는다), `AI_GATEWAY_URL=https://apim-<접미사>.azure-api.net`.
- `infra/main.bicep`·`main.bicepparam`: 매개변수 `aiGatewayRouting`(환경값 `AI_GATEWAY_ROUTING`, 기본 `true`).

## 6. 사전 검증 관문 (계획의 첫 작업)

APIM, 연결 1개(`gw-reviewer`), 임시 에이전트 1개로 실제 호출해 아래를 모두 확인한다.

1. `snet-agents`에서 APIM 내부 VIP로의 DNS 해석과 호출 성공.
2. `text.format` `json_schema` 구조화 출력이 그대로 통과.
3. 함수 호출(function tool) 왕복.
4. Code Interpreter 실행.
5. 콘텐츠 차단 403과 토큰 한도 429가 에이전트 실행 결과에서 어떤 형태로 보이는지.
6. `ama-llm` 지표에 토큰 수가 맞게 찍히는지(스트리밍 여부 포함).
7. Developer 티어 APIM에 `ApiManagement` 범주 연결이 받아들여지는지. foundry-samples 가이드는 이 범주를 Standard v2·Premium만 지원한다고 적고, Learn 문서는 티어 제한을 적지 않는다. 거부되면 같은 APIM을 가리키는 `ModelGateway` 범주 연결로 바꿔 1~6을 다시 확인한다(사용자 상의 없이 진행 가능한 유일한 전환).

하나라도 실패하면 작업을 멈추고 사용자와 상의한다. 후보 대안:

- **B:** APIM을 내부 모드 대신 Private Endpoint로 노출(foundry-samples 템플릿 16 방식).
- **C:** Decision-1과 지표만 APIM을 거치고 에이전트는 직접 호출.

## 7. 테스트

- **단위(TDD):** `gateway_model` 에이전트 8종과 모르는 에이전트 `KeyError`; `GatewayRunner`가 모델만 바꾸고 미터는 원래 배포 이름을 봄; eval 게이트웨이 매핑(judge 제외); `runtime` 오류 분류(403 차단, 429 재시도 1회 후 성공·실패, 일반 4xx, 하위 클래스 보존); Decision-1 URL 분기; 콘솔 "콘텐츠 차단" 배지.
- **인프라(`RUN_INFRA_TESTS=1`):** APIM Developer·내부 VNet 모드; `snet-apim`·NSG·DNS 영역; 관리 ID 역할; 연결 9개와 이름; 정책 3종 포함; 구독 키가 출력값·Key Vault에 없음; 환경변수 2개.
- **전체 스위트:** 기존 테스트가 모두 통과해야 한다.

## 8. 배포 후 검증

1. `ops smoke` 통과(사설 DNS 점검 대상에 `apim-<접미사>.azure-api.net` 추가).
2. 새 신청 1건 처리 뒤 APIM 로그와 `ama-llm` 지표에 에이전트·모델 차원이 보인다.
3. `ops gateway probe --mode throttle`: `gw-probe` 연결의 프로브 에이전트를 연속 호출해 429를 재현한다(VNet 안 job-eval에서 `EVAL_TASK=gateway-probe`로 실행).
4. `ops gateway probe --mode unsafe`: 같은 경로로 프롬프트 주입 문장을 보내 `content_blocked`를 재현한다.
5. eval 재실행: 게이트웨이에서 막힌 건수를 보고하고, 기존 품질 기준을 유지하는지 확인한다.
6. 데모 케이스(A·B·B′·C, 추가 건, 부하 대출)는 건드리지 않는다.

## 9. 문서

- 새 ADR `docs/adr/ADR-010-ai-gateway.md`: 도입 근거, Developer 티어 선택과 운영 권장(Standard v2), 접근법 A 선택 이유, 예외(judge 직접 호출, Decision-1 콘텐츠 안전 미적용), 롤백 스위치.
- 갱신: `docs/architecture.md`(구성도·호출 경로), `docs/agents.md`(에이전트별 연결·TPM), `docs/cost-model.md`(APIM 고정비), `docs/limitations.md`(Developer SLA 없음, BYOM 미리보기), `docs/demo/demo-script.md`(429·차단 시연), `docs/runbook/deploy-history.md`.

## 10. 비용과 일정

- 월 추가 비용: APIM Developer 약 $51(공인 IP 없음).
- APIM 최초 생성 30~60분, VNet 모드 변경도 비슷하다. 배포 창 시간을 그만큼 잡는다.

## 11. 위험

| 위험 | 대응 |
|---|---|
| BYOM(미리보기)이 VNet 주입 Developer APIM과 맞지 않음 | 사전 검증 관문, 대안 B·C |
| `ApiManagement` 연결 범주가 Developer 티어를 공식 지원하지 않음 | 관문 7, `ModelGateway` 범주로 전환. 문서 한계에 적는다 |
| 구조화 출력·Code Interpreter가 게이트웨이를 지나며 깨짐 | 사전 검증 관문 2·4 |
| 콘텐츠 안전 오탐으로 정상 심사가 폴백으로 빠짐 | eval 재실행으로 차단 건수 확인, 필요하면 심각도 기준 조정 |
| TPM 한도가 부하 테스트를 막음 | 부하 측정 후 2배 이상으로 재조정 |
| Developer 단일 인스턴스 장애 | `azd env set AI_GATEWAY_ROUTING false` 후 provision하면 직접 호출로 롤백 |
