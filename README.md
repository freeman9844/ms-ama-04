# Agentic Cash-Flow Lending — Reference Implementation

Case Study 18 캡스톤 데모. 설계: `docs/superpowers/specs/2026-10-31-agentic-cashflow-lending-design.md`

## 로컬 개발

사전 요구: Python 3.12 (`winget install --id Python.Python.3.12 -e --source winget`)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m pytest
```

로컬 서비스 실행:

```powershell
$env:MOCK_CONSENT_SECRET = "local-dev-secret-at-least-32-characters!!"
.\.venv\Scripts\python -m uvicorn mockapis.main:app --port 8001
.\.venv\Scripts\python -m uvicorn policy.main:app --port 8002
```

컨테이너 이미지는 로컬 Docker 없이 ACR Build로 만든다(P3).

## Azure 인프라(P2)

Korea Central에 전체 비공개 데모 환경을 Bicep + azd로 배포합니다. 설계 근거는
`docs/superpowers/specs/2026-10-31-agentic-cashflow-lending-design.md` §2.2, §6.2를 참고하세요.
실측값은 첫 실제 배포(MCAPS 테넌트 구독) 기준입니다.

### 준비

- Azure CLI 2.91+, Azure Developer CLI 1.37+, PowerShell 7, Python 3.12 가상환경(`.venv`)
- `az login` 후 `azd config set auth.useAzCliAuth true`
- `az extension add -n containerapp`
- `az provider register -n Microsoft.App --wait`, `az provider register -n Microsoft.CognitiveServices --wait`
- 오프라인 IaC 테스트: `Install-Module PSRule.Rules.Azure -Scope CurrentUser` 후
  `$env:RUN_INFRA_TESTS = '1'; .\.venv\Scripts\python -m pytest tests/infra -q`

### 최초 배포

```powershell
azd env new amalend --location koreacentral --subscription <구독 ID>   # 운영 도구는 이 구독(AZURE_SUBSCRIPTION_ID)으로만 az를 호출
azd env set AZURE_PRINCIPAL_ID (az ad signed-in-user show --query id -o tsv)
azd env set OPERATOR_IP closed
azd provision --preview
azd provision --no-prompt          # 실측: 성공 실행 7.6분(최초 실패 2회 포함 누적 약 28분)
azd env set DEPLOY_CAPHOST false   # 이후 재프로비저닝에서 capability host 재생성 방지
python -m ops smoke                # VNet 안에서 Private Endpoint 경로 검증(실측 약 1.2분, 21개 점검)
```

이후 재프로비저닝(capability host 제외)은 약 3.5분, smoke Job 스크립트(`src/ops/smoke_checks.py`)는
Bicep `loadTextContent`로 Job에 들어가므로 수정 후에는 `azd provision`이 필요합니다.

문제 대응:

- Decision-1 배포(`decision-1`, 모델 `Microsoft-Decision-1`)가 SKU/지역 오류로 실패하면 `azd env set DECISION_MODEL_SKU DataZoneStandard` 후 다시 프로비저닝합니다(ADR-002 전환 조건). 배포 이름에는 예약어 `Microsoft`를 쓸 수 없어 모델 이름과 다르게 둡니다.
- 재시도 전에 capability host가 이미 만들어졌는지 확인합니다. `Succeeded`면 `azd env set DEPLOY_CAPHOST false`:
  ```powershell
  $sub = azd env get-value AZURE_SUBSCRIPTION_ID; $acct = azd env get-value AI_ACCOUNT_NAME
  az rest --method get --url "https://management.azure.com/subscriptions/$sub/resourceGroups/rg-amalend/providers/Microsoft.CognitiveServices/accounts/$acct/projects/proj-lending/capabilityHosts/caphost-lending?api-version=2025-04-01-preview" --query properties.provisioningState -o tsv
  ```
- 쿼터 오류: `az cognitiveservices usage list -l koreacentral -o table`로 확인하고 `infra/main.bicep`의 `capacity`를 낮춥니다.

### 운영 명령

| 명령 | 용도 |
|------|------|
| `python -m ops window open [--ip <IP>] [--acr]` | 배포 창 열기: 운영자 공인 IP만 허용(`--acr`은 ACR 빌드 창). 실측 11.0분(Cosmos 갱신이 약 8분) |
| `python -m ops window status` | 창 상태 확인. 공인 IP가 바뀌었으면 경고(종료 코드 2) |
| `python -m ops window close` | 배포 창 닫기(작업 후 반드시 실행). 실측 10.8분 |
| `python -m ops deploy [policy\|mockapi\|console\|orchestrator\|all]` | 앱 배포(P3·P4b): 정책·Mock·콘솔 이미지 ACR Build → 프로비저닝, Function zip. `window open --acr` 필요 |
| `python -m ops smoke` | VNet 안 smoke Job 실행 + 운영자 PC에서 콘솔 로그인 관문 확인(P4b) |
| `python -m ops pause` / `resume` | Service Bus Premium 삭제/복구(비용 절감). 실측 pause 5.8분, resume 4.7분 |
| `python -m ops teardown --confirm rg-amalend` | capability host → Foundry 계정 purge → 리소스 그룹 삭제 → Key Vault purge |

teardown 뒤 같은 azd 환경으로 다시 만들 때는 **다른 `ops` 명령보다 먼저 `azd provision --no-prompt`를 실행**하세요.
azd 환경에 이전 `AI_ACCOUNT_NAME`이 남아 있어, `ops window/pause/resume`을 먼저 실행하면 `DEPLOY_CAPHOST`가
`false`로 바뀌고 새 환경에 capability host가 만들어지지 않습니다.

### 비용(월, 대략)

| 항목 | 비용 | 비고 |
|------|------|------|
| Service Bus Premium 1 MU | 약 $677 | 쓰지 않을 때 `pause` |
| AI Search Basic | 약 $74 | Foundry 벡터 저장소 |
| ACR Premium | 약 $50 | Private Endpoint 필요 |
| Private Endpoint 약 17개 | 약 $125 | 개당 약 $7.3 |
| 모델, Functions, Container Apps, Cosmos serverless | 사용량 기반 | 유휴 시 거의 0 |
| Application Gateway WAF v2 | (구축 안 함) | 운영 아키텍처 문서에만 기술, 약 $365 |

### 알려진 제약

- 콘솔은 WAF 없이 external Container Apps 환경에 둡니다(운영자 IP 허용 + Entra 인증, P3). 운영 설계는 App Gateway WAF.
- App Insights 수집은 public 엔드포인트 + Entra 인증을 씁니다(Azure Monitor Private Link Scope 미사용).
- 배포 창 동안에는 운영자 IP에서 데이터 평면에 접근할 수 있습니다. `window status`로 확인하고 작업 후 바로 닫으세요.
- **테넌트 정책(MCAPS)**: `MCAPSGovDeployPolicies`의 Modify 정책이 Key Vault, Cosmos DB, Storage의
  `publicNetworkAccess`를 `Disabled`로, Cognitive Services/Cosmos/Service Bus/Storage의 로컬 인증을 끕니다.
  그래서 배포 창을 열어도 이 세 서비스는 IP 규칙만 들어가고 운영자 PC에서 접근할 수 없습니다(403).
  창이 실제로 여는 것은 Foundry 계정, ACR, Functions입니다. 이 세 서비스의 데이터 작업은 VNet 안 Job으로 합니다.
  정책을 우회하지 않습니다.
- **에이전트 API**: 새 Foundry 프로젝트에서 클래식 Assistants API(`/assistants`, `/threads/runs`)는 모든 배포에
  `invalid_engine_error: Failed to resolve model info`로 실패합니다. 프롬프트 에이전트
  (`POST {project}/agents/{name}/versions?api-version=v1`)와 `POST {project}/openai/v1/responses`
  (`agent_reference`)를 사용하세요(P3 이후 동일).

## 신청 여정(P3)

흐름: `applications-start` 큐 → Durable 오케스트레이션(동의 → 데이터 조립 에이전트 → 언더라이팅 → 정책 엔진)
→ `awaiting_officer` → `officer-decisions` 큐 → 최종 상태(`approved`, `conditionally_approved`, `declined`,
SLA 초과 시 `escalated` → `closed_no_decision`). 신청 문서는 단계마다 Cosmos `lending/applications`에 기록됩니다.

| 구성 | 위치 | 배포 방식 |
|------|------|-----------|
| 오케스트레이터 | Functions Flex(`FUNCTION_APP_NAME`): `src/orchestrator` + `src/agents` + `src/shared` | zip, Linux wheel 동봉(원격 빌드 없음) |
| 정책 엔진 | `ca-policy`(internal 환경) | ACR Build → azd 환경 이미지 → 프로비저닝 |
| Mock 마이데이터·국세청·PG | `ca-mockapi`(internal 환경, 복제본 1) | 같음 |

### 배포 순서

```powershell
python -m ops resume               # 선택: Service Bus 이벤트(flow.updated)까지 보려면(실측 4.9분)
python -m ops window open --acr    # ACR·Functions·Foundry에 운영자 IP 허용(실측 4.8분)
python -m ops deploy all           # 이미지 2개 빌드 + 프로비저닝 1회 + Function zip 배포(실측 10.1분)
python -m ops window close         # 작업 후 반드시 닫기(실측 3.8분)
python -m ops smoke                # P2 점검 + health:policy, health:mockapi, journey(e2e)(실측 2.4분, 24개 점검)
```

- 하나만 다시 배포: `python -m ops deploy policy|mockapi|orchestrator`. 이미지 태그는 UTC 시각이고
  azd 환경 `SERVICE_POLICY_IMAGE_NAME`, `SERVICE_MOCKAPI_IMAGE_NAME`에 남아 이후 프로비저닝에서도 유지됩니다.
- 앱 비밀(`MOCK_CONSENT_SECRET`, `PSEUDONYM_KEY`)은 `ops` 명령의 프로비저닝(`window open` 등)이 비어 있으면
  azd 환경에 생성합니다. P3 이후 첫 프로비저닝은 `azd provision` 대신 `ops` 명령으로 하세요.
- `journey` 점검은 실행마다 `smoke-YYYYMMDD-HHMMSS` 신청을 `demo-cafe-001`(eligible)로 넣고, `awaiting_officer`에서
  정책 한도 이하 금액으로 승인해 `approved`까지 확인합니다(최대 약 20분, 실측 52초·6단계). smoke 신청은 Cosmos에 남습니다.
- 신청 상태는 운영자 PC에서 직접 볼 수 없습니다(Cosmos, Storage는 MCAPS 정책으로 공용 접근 차단).
  smoke 출력의 `journey` 결과나 Application Insights `traces`를 보세요.

### 문제 대응

- `ops deploy`가 "ACR 빌드 창" 오류: `python -m ops window open --acr` 후 다시 실행합니다.
- `health:*`가 404: 앱이 아직 자리표시 이미지입니다. `azd env get-value SERVICE_POLICY_IMAGE_NAME`을 확인하고
  `python -m ops deploy policy`(또는 `mockapi`)를 다시 실행합니다.
- `journey`가 `awaiting_officer`에 도달하지 못함(마지막 상태 `None`): Function이 큐를 처리하지 않았습니다.
  `az functionapp function list -g rg-amalend -n (azd env get-value FUNCTION_APP_NAME) -o table`에 큐 트리거 2개,
  오케스트레이터, 활동이 보이는지 확인합니다. 비어 있으면 zip 배포나 함수 인덱싱 실패입니다(App Insights `exceptions`).
- `journey`가 `failed`: 결과의 `error.stage`(`request_consent`, `assemble_data`, `underwrite`)로 구간을 찾습니다.
  `assemble_data`는 Foundry 에이전트 호출, 나머지는 Mock API·정책 엔진 연결 문제입니다.

### 알려진 제약(P3)

- Mock API 동의 레지스트리는 메모리에 있어 복제본 1개로 운영합니다. 재시작하면 발급된 동의가 사라집니다.
  기준일(`as_of`)은 컨테이너 시작일입니다(`MOCK_AS_OF`로 고정 가능).
- 동의 토큰이 활동 입력으로 Durable 기록(사설 Storage)에 남습니다. 운영 설계는 토큰 대신 동의 ID로 조회합니다.
- `request_consent` 재시도는 Mock에 동의를 중복 발급할 수 있습니다(멱등 키 없음).
- 앱 비밀은 운영자 PC의 azd `.env`에 평문으로 있습니다. 운영 설계는 Key Vault 참조와 정기 교체입니다.
- Function 의존성은 운영자 PC에서 Linux wheel로 받아 zip에 넣습니다(원격 빌드 미사용).

## 심사 에이전트(P4a)

정책 엔진 다음에 두 단계가 추가됩니다. 결정 권한은 그대로 정책 엔진과 심사역에 있습니다.

1. **거래 의미 분류**(`underwrite` 활동 안): 규칙(classify-v1)으로 분류되지 않은 거래의 적요를 가명처리·정규화해
   Microsoft-Decision-1(`decision-1`, systemone 선택형 질문)에 묻고, 기권·낮은 확신(0.7 미만)은 `gpt-5.4-mini`
   에이전트(`txn-classifier`)가 보완합니다. 결과는 Cosmos `lending/classifications`에 캐시됩니다(토큰이 든 적요·이체 적요 제외).
   부채 라벨(`loan_repayment`, `high_cost_lender`)만 특성에 반영하며, DSCR·현금 완충일수는 규칙 결과와 비교해
   **더 낮은 값**을 씁니다(LLM이 상환여력을 좋게 만들 수 없음).
2. **분석·검토 에이전트**(`assess_credit` 활동): 분석가(`underwriting-analyst`, gpt-5.4)가 도구(`get_metrics`,
   `get_monthly_series`, `query_transactions`, Code Interpreter)로 근거를 모아 `CreditAssessment` 초안을 내고,
   코드가 수치 근거(2% 이내)·정책 한도를 검사한 뒤 검토자(`underwriting-reviewer`, gpt-5.4-mini)가 claim별로
   판정합니다. 지적이 있으면 최대 3라운드까지 다시 씁니다. 결과는 신청 문서 `assessment`(상태 `assessed`)에 남고,
   실패하면 `assessment_unavailable` 플래그만 남긴 채 심사역 단계로 넘어갑니다.

- 에이전트가 쓴 Foundry 대화·Code Interpreter 컨테이너 ID는 동의 문서 `foundry_conversations`, `foundry_containers`에
  남습니다. 컨테이너는 실행 직후 삭제하고(`deleted` 기록), 대화는 P5 동의 만료 처리에서 지웁니다.
- smoke에 `decision`(Decision-1 왕복) 점검이 추가되고, `journey`는 `assessment`가 있어야 통과합니다(25개 점검).
  심사 에이전트 호출 때문에 여정이 1~2분 길어집니다(로컬 실측: 분류 5~23초, 분석·검토 약 30초).
- P4a 실제 배포 실측: `ops resume` 4.5분 → `window open` 3.6분 → `deploy orchestrator` 4.6분 →
  `window close` 3.4분 → `ops smoke` 2.7분(25개 점검 통과; `decision` 0.8초, `journey` 72초·7단계,
  assessment approve·검토 1라운드 통과) → `ops pause` 5.8분.

### 문제 대응(P4a)

- `decision`이 401/403: Function·smoke 관리 ID에 Foundry 계정 `Cognitive Services User` 역할이 필요할 수 있습니다
  (운영자 계정은 `Azure AI User`로 동작 확인). 역할을 추가하고 5분 뒤 다시 실행합니다.
- `journey`가 "심사 에이전트 결과 없음": 결과의 `flags`에 `assessment_unavailable`이 있으면 App Insights `traces`에서
  `assess_credit` 오류를 찾습니다. 근거 검사·검토를 3라운드 안에 통과하지 못하면 `assessment`는 남지만
  `review.passed=false`이고 `review_not_passed` 플래그가 붙습니다(smoke는 통과, 출력에 "not passed").
- 에이전트 정의를 바꾸면 Foundry에 새 버전이 생깁니다(같은 정의는 같은 버전).

### 알려진 제약(P4a)

- `assessment` 문장은 가명 토큰(`[[PERSON_…]]`)을 그대로 담습니다. 복원 표시는 심사역 콘솔(P4b)이 합니다.
- 가명처리는 사전 기반(상호·대표자·사업자번호·급여/개인이체 적요 속 이름)이라, 규칙 미분류 이체 적요에 든 제3자 이름
  (예: "이영희 생활비")은 Decision-1·보완 에이전트에 그대로 갑니다. 그래서 이체(`TRANSFER_IN/OUT`) 적요는 공유 캐시에
  남기지 않습니다(데모 데이터의 해당 적요는 공급업체 상호뿐). 개인정보 탐지 서비스 연동은 후속 과제입니다.
- 데모 데이터에서 규칙 미분류 적요는 공급업체 송금뿐이라 의미 분류가 특성을 바꾸지 않습니다. 보수적 반영은 단위 테스트로 검증합니다.
- 수치 근거 검사는 숫자 일치만 봅니다. 해석의 타당성은 검토 에이전트 판정에 의존합니다.
- `demo-stress-003`은 고금리 대출이 감지되어도 정책상 eligible입니다(P3 규칙). 분석가는 이를 claim·counterfactual로 드러냅니다.

## 심사역 콘솔(P4b)

심사역이 신청 목록·상세(심사 에이전트 근거)를 보고 승인·조건부 승인·거절을 내리는 웹 콘솔입니다(`src/console`, FastAPI +
서버 렌더링 HTML, 목록만 htmx 폴링). 결정은 P3와 같은 `officer-decisions` 큐로 보내고, 최종 판단은 오케스트레이터가
정책 한도와 함께 다시 검증합니다.

| 구성 | 위치 | 비고 |
|------|------|------|
| 콘솔 | `ca-console`(external 환경 `cae-web`, `snet-apps-web`) | 관리 ID `id-console`: Cosmos `lending` 읽기, 데이터 Blob 읽기, 큐 보내기, Key Vault 비밀 읽기, ACR pull |
| 진입 통제 | ingress IP 허용(`CONSOLE_IP`/32) + Easy Auth(Entra 로그인 강제) | `CONSOLE_IP`가 비면 TEST-NET 주소만 허용(fail-closed) |
| 앱 등록 | Bicep Graph 확장(`infra/modules/console-auth.bicep`) | 앱 역할 `CreditOfficer`·`CreditManager`·`Auditor`, 역할 할당 필수, 운영자에게 `CreditOfficer` 부여 |

| 역할 | 목록·상세 | 실명 복원 | 결정·새 신청 |
|------|-----------|-----------|--------------|
| `CreditOfficer`, `CreditManager` | 가능 | 가능(동의된 원천 데이터로 가명 토큰 복원) | 가능 |
| `Auditor` | 가능 | 불가(가명 토큰 그대로) | 불가(403) |
| 역할 없음 | 불가(Entra가 로그인 거부: 역할 할당 필수) | | |

- 심사역 ID(`officer_id`)는 폼 값이 아니라 Easy Auth 헤더(`X-MS-CLIENT-PRINCIPAL`)의 로그인 사용자에서 가져옵니다.
- 결정·새 신청은 CSRF 토큰(가명 키 HMAC, 복제본 간 동일)과 `application/x-www-form-urlencoded`만 받습니다.
  응답에는 CSP(`script-src 'self'` + htmx SRI), `no-store`, `nosniff`, `no-referrer`를 붙입니다.
- 결정 가능한 상태는 `awaiting_officer`, `escalated`뿐이고, 금액은 정책 한도(`max_amount_krw`) 이하만 보냅니다.

### 배포 순서(P4b)

```powershell
python -m ops resume                       # 선택: Service Bus 이벤트까지 보려면
python -m ops window open --acr            # CONSOLE_IP도 운영자 IP로 설정(창을 닫아도 유지)
python -m ops deploy console               # 콘솔 이미지 + 프로비저닝(Graph 앱 등록·Easy Auth 포함)
python -m ops deploy orchestrator          # P4a 리뷰 수정분 포함 Function 재배포
python -m ops window close
python -m ops smoke                        # 25개 점검 + 콘솔 관문(로그인 없이 접근 → Entra 로그인 리디렉션)
python -m ops pause
```

브라우저로 `azd env get-value CONSOLE_URL`을 열어 로그인합니다(허용 IP에서만). `새 신청`으로 데모 사업자를 넣고
`awaiting_officer`가 되면 상세 화면에서 결정합니다. 공인 IP가 바뀌면 `python -m ops window open` 후 `window close`로
`CONSOLE_IP`를 갱신합니다.

다른 사용자에게 역할을 줄 때는 Entra 관리 센터 → 엔터프라이즈 애플리케이션 → `AMA Lending 심사 콘솔 (<환경>)` →
사용자 및 그룹에서 할당합니다. 운영자 자신을 `Auditor`로 바꿔 보려면 같은 화면에서 역할을 바꾸고 다시 로그인합니다.

### 데모 모드(Entra 로그인 끔)

테넌트 정책 때문에 Entra 로그인을 쓸 수 없을 때(예: 사용자 동의가 막혀 "관리자 승인 필요")의 데모 전용 스위치입니다.
Easy Auth를 끄고, 콘솔은 모든 요청을 고정 데모 사용자(`CONSOLE_DEMO_ROLE`, 기본 `CreditOfficer`)로 처리합니다.
진입 통제는 운영자 IP 허용 목록(`CONSOLE_IP`/32)만 남습니다. 화면 상단에 "데모 모드" 배너가 보입니다.

```powershell
azd env set CONSOLE_AUTH_ENABLED false     # 다시 켜려면 true
azd env set CONSOLE_DEMO_ROLE Auditor      # 선택: 감사자(가명 읽기 전용) 데모. 기본 CreditOfficer
python -m ops window open --acr            # 프로비저닝: authConfigs platform.enabled=false, CONSOLE_AUTH_MODE=demo
python -m ops deploy console               # 데모 모드를 아는 콘솔 이미지
python -m ops window close
python -m ops smoke                        # 콘솔 관문: 운영자 IP에서 200(데모 모드)
```

증분 배포는 리소스를 지우지 않으므로 Easy Auth 설정(`authConfigs`)은 남기고 `platform.enabled`만 끕니다. Entra 앱 등록도 그대로입니다.

### 실제 배포 기록(P4b, 2026-11-01, koreacentral)

| 단계 | 명령 | 시간 | 결과 |
|---|---|---|---|
| Service Bus 재개 | `ops resume` | 4.5분 | 성공 |
| 배포 창 | `ops window open --acr` | 4.1분 | Graph 확장으로 Entra 앱·역할·운영자 CreditOfficer 할당 생성 |
| 콘솔 | `ops deploy console` | 4.1분 | 리비전 Healthy, Key Vault 가명 키 로드 |
| 오케스트레이터 | `ops deploy orchestrator` | 5.0분 | 성공 |
| 창 닫기 | `ops window close` | 3.8분 | 성공 |
| smoke | `ops smoke` | 2.7분 | 25개 점검 + 로그인 없는 접근 → Entra 로그인 리디렉션(client_id 일치) |
| 데모 모드 전환 | `CONSOLE_AUTH_ENABLED=false` 후 window open → deploy console → window close | 4.0 + 4.2 + 4.3분 | 성공 |
| smoke(데모 모드) | `ops smoke` | 2.4분 | 25개 점검 + 운영자 IP에서 200 |
| 정리 | `ops pause` | 6.0분 | Service Bus 삭제(콘솔은 데모 모드로 유지) |

브라우저 확인 결과:

- Easy Auth 모드: 로그인 시 "관리자 승인 필요"(테넌트가 사용자 동의를 막음, 운영자는 Global Reader). 운영자 본인에 대한
  위임 권한 부여(openid·profile·email)를 만든 뒤 로그인·목록·`새 신청` 화면(CreditOfficer 역할 필요)까지 확인했습니다.
  역할 클레임 파싱은 정상입니다. `/.auth/me`는 토큰 저장소를 쓰지 않아 앱의 404가 보입니다. 다만 브라우저의 `새 신청` 제출(POST)이
  앱에 도달하지 않고 빈 403으로 끝나 CLI로 재현하지 못했고, 데모는 데모 모드로 진행했습니다.
- 데모 모드: `새 신청`(demo-cafe-001, 50,000,000원, 12개월) → 접수 대기(404 자동 새로고침) → 상세 → `승인` 결정(202) →
  최종 상태 `approved`와 타임라인의 심사역 결정을 확인했습니다.

### 문제 대응(P4b)

- 콘솔이 403(빈 화면): 현재 공인 IP가 `CONSOLE_IP`와 다릅니다. `python -m ops window open` → `window close`.
- 로그인 시 "관리자 승인 필요": 테넌트가 사용자 동의를 막았습니다. 관리자가 앱에 관리자 동의(openid·profile·email)를 하거나,
  운영자 본인에 대한 위임 권한 부여(`oauth2PermissionGrants`, consentType `Principal`)를 만들거나, 데모 모드를 씁니다.
- 로그인 후 `AADSTS50105`(역할 미할당): 엔터프라이즈 애플리케이션에서 사용자에게 앱 역할을 할당합니다.
- 로그인 후 `AADSTS50011`(redirect URI 불일치): `azd provision`이 Container Apps 기본 도메인으로 redirect URI를 다시 씁니다.
  `ops window open`/`close` 중 하나를 다시 실행하세요.
- 로그인은 되지만 403 "역할 없음": 토큰에 `roles` 클레임이 없습니다. 역할을 할당한 뒤 로그아웃(`/.auth/logout`)하고 다시 로그인합니다.
- 상세 화면의 실명이 "원천 데이터 없음": 동의 컨테이너(`consent-<동의 ID>`)가 없거나 만료 처리되었습니다(가명 그대로 표시).

### 알려진 제약(P4b)

- Easy Auth는 클라이언트 비밀 없이 ID 토큰(암시적 흐름)만 씁니다. 운영 설계는 Key Vault 비밀 또는 페더레이션 자격 증명으로
  권한 부여 코드 흐름을 쓰고, 콘솔은 internal 환경 + Application Gateway WAF 뒤에 둡니다.
- 콘솔 인증 흐름(실제 Entra 로그인 → 결정)은 자동 테스트가 없습니다. smoke는 로그인 없는 접근이 Entra로 리디렉션되는지만 봅니다.
- 데모 모드(`CONSOLE_AUTH_ENABLED=false`)에서는 IP 허용 목록만 진입을 막고, 결정 기록의 심사역 ID는 고정 값(`demo-operator`)입니다.
  데모 후 `azd env set CONSOLE_AUTH_ENABLED true`로 되돌리고 다시 프로비저닝합니다.
- 목록은 Cosmos 교차 파티션 `ORDER BY updated_at` 최근 50건입니다(페이지 없음).
- 콘솔이 보낸 결정의 결과는 상세 화면 새로 고침(진행 중 10초 자동)으로 확인합니다. 큐 처리 실패는 오케스트레이터 로그에 남습니다.

## 사후 모니터링: 조기경보·추심(P5a)

승인된 대출을 사후 13주 동안 지켜보는 두 에이전트입니다(스펙 §3.3). 데모에서는 Mock MyData의 사후 거래 흐름
(`GET /mydata/flow`, 승인 다음 날부터 13주)을 콘솔의 **재생** 버튼으로 한 주씩 흘려보냅니다.

1. **등록**: 최종 결정이 승인·조건부 승인이면 오케스트레이터가 `register_monitoring` 활동으로 Cosmos `loans`에 대출 문서와
   기준선(주 평균 매출·변동성·현금 버퍼·월별 계절 지수)을 만듭니다. 실패해도 결정은 그대로이고 `monitoring_unavailable` 플래그만 남습니다.
2. **재생**(`monitoring-replay` 큐): 계좌 범위의 모니터링 동의를 새로 받아 흐름을 `consent-<동의 ID>/flow.json`에 저장하고,
   주차별로 거래 의미 분류(P4a와 같은 경로) → 주간 지표 → 탐지기(매출 급감·현금 버퍼 감소·변동성 급증·신규 차입)를 돌립니다.
3. **조기경보 에이전트**(`early-warning-investigator`, gpt-5.4): 이번 재생에서 **처음** 걸린 트리거가 있을 때만 조사합니다.
   주간 지표·기준선·업종 집단 계절 지수·거래 조회 도구로 계절성(예: 1월 카페 비수기)을 걸러 내고, 경보를 내면
   모든 문장의 수치가 근거 ID(`W:<주차>:<지표>`, `B:<기준선>`)와 맞아야 합니다(코드 검사). 실패하면 탐지기만으로 만든
   `officer_review` 경보가 남습니다(자동 연락 없음).
4. **추심 에이전트**(`collections-agent`, gpt-5.4-mini): 권고가 `contact_borrower`·`hardship_review`인 경보만 처리합니다.
   승인된 스크립트만 보낼 수 있고(자유 문장 발송 없음), 공정 추심 규칙(KST 08~21시, 7일 7회)을 코드가 막으며,
   상환 곤란이면 재조정 상담 요청(`pending_officer`)을 만듭니다. 재조정 승인·거절은 심사역이 콘솔에서 합니다(`restructure-decisions` 큐).

| 저장 위치 | 내용 |
|---|---|
| `loans`(파티션 `/loan_id`) | 대출·기준선·`monitoring`(재생 ID, 진행 주차, 주차별 지표·트리거·조사 결과) |
| `alerts`(파티션 `/loan_id`) | 경보(`ew-<재생 ID>-wNN`: 근거 신호, 추심 결과, 재조정 요청), 재생별 연락 기록(`contacts-<재생 ID>`) |
| 모니터링 동의 문서 | 조기경보·추심 에이전트의 Foundry 대화 ID(`foundry_conversations`) |

- Service Bus가 켜져 있으면 주차 메시지를 `flow.updated` 토픽(세션 = 대출 ID, 구독 `early-warning`)으로, 경보를 `alert.raised`
  (구독 `collections`)로 보냅니다. 꺼져 있으면(`ops pause` 상태) 같은 처리를 큐 트리거 안에서 순서대로 직접 합니다(`mode: inline`).
- 같은 대출의 재생은 30분 안에 한 번만 시작됩니다(진행 중이면 콘솔 버튼이 꺼지고 큐 메시지도 무시). 멈춘 재생은 30분 뒤 다시 시작할 수 있습니다.
- smoke `journey`는 승인 뒤 `loans` 문서 등록까지 확인합니다(재생은 LLM 비용 때문에 돌리지 않음).

### 배포 순서(P5a)

```powershell
python -m ops window open --acr            # 새 큐(monitoring-replay, restructure-decisions)·컨테이너(loans, alerts)·앱 설정
python -m ops deploy mockapi               # GET /mydata/flow
python -m ops deploy orchestrator          # 모니터링 트리거·활동
python -m ops deploy console               # 사후 모니터링 화면
python -m ops window close
python -m ops smoke                        # journey가 모니터링 등록까지 확인
```

콘솔 상단의 `사후 모니터링` → 승인된 대출 → `사후 13주 재생`을 누르면 인라인 모드에서 수 분 뒤(주차마다 분류, 새 트리거마다 에이전트)
주간 표·경보·추심 결과가 채워집니다(재생 중 10초 자동 새로 고침). 재조정 결정 폼은 재생이 끝난 뒤에 나타납니다(새로 고침이 입력을 지우지 않도록). `demo-cafe-001`은 사후 흐름에서 고금리 대출을 겹쳐 받는
시나리오(1주차 설비 지출로 버퍼 감소, 2주차부터 신규 차입, 4주차부터 매출 급감)입니다.

### 실제 배포 기록(P5a, 2026-11-01, koreacentral)

| 단계 | 명령 | 시간 | 결과 |
|---|---|---|---|
| 배포 창 | `ops window open --acr` | 3.4분 | 큐·loans/alerts 컨테이너·앱 설정 프로비저닝 |
| Mock API | `ops deploy mockapi` | 4.7분 | 성공 |
| 오케스트레이터 | `ops deploy orchestrator` | 4.8분 | 트리거 목록에 `monitoring_replay`·`restructure_decision`·`register_monitoring`, Service Bus 트리거 없음 |
| 콘솔 | `ops deploy console` | 4.4분 | 성공 |
| 창 닫기 | `ops window close` | 3.6분 | 성공 |
| smoke | `ops smoke` | 2.7분 | 23개 점검 통과, journey `… approved 20,000,000 KRW (7 steps); … monitoring registered (카페)` |
| 정리 | `ops pause` | 즉시 | Service Bus 없음(인라인 모드 유지) |

체크리스트 결과(데모 모드, 운영자 IP에서 HTTP로 같은 순서 실행):

1. `demo-cafe-001` 50,000,000원·12개월 신청 → 50초 뒤 `awaiting_officer` → 제안 금액 30,000,000원 `승인` → 20초 안에 `사후 모니터링` 링크.
2. `사후 모니터링` 목록에 `재생 전`으로 표시.
3. `사후 13주 재생` → "사후 13주 재생 요청을 큐에 보냈습니다…" → `재생 중`, 버튼 비활성 문구·자동 새로 고침 확인.
4. 132초 뒤 `재생 완료`. 주간 표 13줄, 경보 2건(2주차 중간 `차주 연락`, 9주차 높음 `상환 곤란 검토`). 1주차 버퍼 감소는 `no_alert`.
   사전 검증(2·4·9주차)과 달리 4주차 경보는 이번 실행에서 나오지 않았습니다(LLM 조사 결과 변동). 첫 경보(2주차 말 2026-11-14)는 연체일 2027-01-20보다 67일 앞섭니다(KPI 60~90일).
5. 경보마다 신호별 근거 칩(2주차 W 5·B 2개, 9주차 W 7·B 6개)과 참조 거래 적요가 보입니다. 추심: 2주차 `hardship_offer` 발송 + 재조정 요청,
   9주차 발송 없이 재조정 요청(`revenue_shock`). 두 요청 모두 `pending_officer`.
6. 2주차 재조정 `승인`(사유 입력) → "재조정 결정(approve)을 큐에 보냈습니다." → 상태 `approved`, 결정자 `demo-operator`·시각·사유 표시, 해당 폼 사라짐.
7. `demo-injection-005`(STABLE 흐름) 신청 → 승인 → 재생 40초 → 경보 0건.

### 문제 대응(P5a)

- 대출 목록이 비어 있음: P5a 배포 전에 승인한 신청은 `loans` 문서가 없습니다. 새로 신청해 승인합니다.
- 재생 상태가 `재생 실패`: 화면의 오류와 App Insights `traces`(`journey.monitoring`)를 봅니다. 버튼으로 바로 다시 시작할 수 있습니다.
- 경보 모델이 `detector_only`: 에이전트 호출이 실패해 탐지기만으로 만든 경보입니다(조사 기록의 `record.error`).
- Service Bus 모드에서 주차가 멈춤: `flow.updated/early-warning` 구독의 dead-letter를 확인합니다. 앞 주차가 안 끝나면 뒤 주차는 재시도됩니다.

### 알려진 제약(P5a)

- 사후 흐름은 합성 데이터이고, 재생은 실제 시간이 아니라 주차 단위로 압축됩니다. 연락은 실제로 발송하지 않고 기록만 합니다.
- 업종 집단 통계는 코드에 고정된 합성 표입니다.
- 인라인 모드는 13주를 한 번의 큐 트리거 안에서 처리합니다(Flex Consumption 기본 제한 30분).
- 재조정 결정은 기록만 하고 상환 일정을 실제로 바꾸지 않습니다. 동의 만료·삭제와 이의 제기는 P5b입니다.

## 동의 수명주기·삭제 증명·이의제기(P5b)

동의(스펙 §3.1 Consent Lifecycle)는 받는 순간부터 끝(만료·철회)까지 추적하고, 끝나면 그 동의로 모은 데이터를 지우고
**삭제 증명**을 남깁니다. 차주는 결정에 **이의**를 제기할 수 있고, 심사역은 판단 기록을 근거로 처리합니다(스펙 §4.1).

1. **수명주기 예약**: 심사 동의(`request_consent`)와 모니터링 동의(`start_replay`)를 저장하면 `consent-lifecycle` 큐로
   Durable 오케스트레이션 `consent_lifecycle`(인스턴스 `consent-<동의 ID>`)을 시작합니다. 큐 전송이 실패하면 동의를 `void`로 남기고
   여정을 실패시킵니다(fail-closed). 타이머는 만료 7일 전 갱신 안내 → 만료 순서이고, 6일 단위로 나눠 겁니다.
2. **명령**(`consent-commands` 큐): `revoke`(차주 철회), `expire_now`·`remind_now`(데모 버튼). 수명주기가 실행 중이면 외부 이벤트로,
   없으면(P5b 이전 동의) 동의 문서의 만료일로 새로 시작하며 그 명령을 바로 처리합니다. 철회이고 신청 여정이 심사역 대기 중이면
   여정에 `consent_withdrawn`을 보내 `withdrawn`(철회 종료)으로 끝냅니다.
3. **삭제**(`delete_consent_data`, 결정적·멱등): 동의를 먼저 `expired`/`revoked`로 표시한 뒤 원천 Blob 컨테이너(삭제 전 파일 목록과
   sha256 manifest 기록, blob을 하나씩 지운 뒤 컨테이너 삭제 — Azure의 컨테이너 삭제는 비동기라 "없음 또는 빈 컨테이너"를 삭제 확인으로
   봅니다) → Mock 동의 레지스트리 철회 → Foundry 대화(삭제 후 조회로 확인) → 코드 실행 컨테이너 순으로 지웁니다.
   모든 항목이 확인되어야 `complete`이고, 아니면 증명을 미완료로 남기고 재시도합니다(앞선 시도에서 확인된 항목은 다시 지우지 않고,
   원천 컨테이너만 매번 다시 확인). 신청 문서(판단 기록)는 보존 항목으로 남깁니다.
4. **안내문 에이전트**(`consent-notice-agent`, gpt-5.4-mini, 도구 없음): 목적·범위 라벨·만료일·철회 방법·삭제 항목 수 같은 사실만 받아
   갱신 안내와 삭제 완료 안내를 씁니다. 필수 사실이 빠지거나 근거 없는 숫자가 있으면 템플릿 문안을 씁니다. 대화는 쓰고 바로 지웁니다.
5. **이의제기**(`grievances` 큐): 결정이 난 신청에만 받습니다. 접수 시점의 판단 기록(정책 사유 코드, 분석 요약·반사실, 심사역 결정)을
   스냅숏으로 남깁니다. 심사역은 `원결정 유지` 또는 `재심사`(정책 한도 안에서 다시 검증)로 처리하고(`grievance-decisions` 큐),
   **회신 에이전트**(`grievance-reply-agent`, gpt-5.4-mini)가 판단 기록만으로 회신 초안을 씁니다(차주가 쓴 글은 주지 않음, 근거 검사 실패 시 템플릿).

| 저장 위치 | 내용 |
|---|---|
| `consents`(파티션 `/consent_id`) | 상태(`granted`·`expired`·`revoked`·`void`), 종료 시각, 안내문 사본(`notices`), 삭제 증명 ID |
| `deletion_proofs`(파티션 `/consent_id`, id = 동의 ID) | 항목별 결과·검증, manifest와 sha256, 보존 항목, 시도 횟수, 이벤트 발행 여부 |
| `grievances`(파티션 `/application_id`) | 이의 내용, 판단 기록 스냅숏, 처리 결과, 회신, 거부된 처리 시도 |

콘솔:

- 상단 `차주 시뮬레이터` → 차주 선택 → 동의 목록(범위·만료·안내문·삭제 증명 링크), `동의 철회`, 결정 난 신청에 이의제기, 회신 확인.
- 신청 상세의 `동의` 영역: 동의별 상태와 `지금 만료(데모)`·`갱신 안내(데모)` 버튼, 삭제 증명 링크(`/consents/<동의 ID>/proof`).
- 상단 `이의제기` → 목록 → 상세(이의 내용, 판단 기록, 처리 폼).
- Service Bus가 켜져 있으면 `consent` 토픽에 `consent.expired`/`consent.revoked`, `grievance` 토픽에 `grievance.submitted`를 보냅니다
  (구독은 없음, 이의 내용은 싣지 않음). 꺼져 있으면 문서에 `servicebus_disabled`만 남습니다.
- smoke `consent`는 `journey`가 승인한 신청의 심사 동의를 `expire_now`로 끝내고, 삭제 증명이 `complete`인지, 동의가 `expired`인지,
  신청 문서가 남았는지 확인합니다.

### 배포 순서(P5b)

```powershell
python -m ops window open --acr            # 새 큐 4개·컨테이너(deletion_proofs, grievances)·함수 설정(QUEUE_ENDPOINT, NOTICE_MODEL, GRIEVANCE_MODEL)
python -m ops deploy orchestrator          # 수명주기 오케스트레이션·명령·이의제기 트리거
python -m ops deploy console               # 차주 시뮬레이터·동의 영역·삭제 증명·이의제기 화면
python -m ops window close
python -m ops smoke                        # journey 승인 뒤 consent가 삭제 증명까지 확인
```

배포 전에 실행 중인 신청 여정이 없는지 확인합니다(여정 코드가 바뀌어 실행 중 인스턴스는 replay가 어긋날 수 있음).

### 실제 배포 기록(P5b, 2026-11-01, koreacentral)

| 단계 | 명령 | 시간 | 결과 |
|---|---|---|---|
| 배포 창 | `ops window open --acr` | 3.7분 | 큐·컨테이너·함수 설정 프로비저닝 |
| 오케스트레이터 | `ops deploy orchestrator` | 4.6분 | 새 함수 7개(`consent_lifecycle`, `delete_consent_data`, `grievance_submitted` 등), Service Bus 트리거 없음 |
| 콘솔 | `ops deploy console` | 4.3분 | 성공 |
| 창 닫기 | `ops window close` | 3.7분 | 성공 |
| smoke(1차) | `ops smoke` | 8.1분 | 23/24 — consent 실패: 삭제 증명의 `raw_container` 검증 실패 |
| 수정 재배포 | `window open` → `deploy orchestrator` → `window close` | 4.9 + 4.7 + 3.4분 | 아래 원인 수정 |
| smoke(2차) | `ops smoke` | 7.6분 | 24/24 통과. journey `… approved 20,000,000 KRW (7 steps) … monitoring registered`, consent `… expired; proof complete (5 items verified, 4 files in manifest); application retained`(삭제 11초) |
| 정리 | `ops pause` | 0.5분 | Service Bus 없음(인라인 모드 유지) |

1차 smoke 실패 원인: Azure의 컨테이너 삭제는 202로 끝나고 실제 제거는 비동기라, 삭제 직후 목록 조회에 blob 4개가 그대로 보여
재시도해도 검증되지 않았습니다(App Insights의 Blob 요청 로그로 확인). 어댑터가 blob을 먼저 하나씩 지운 뒤 컨테이너를 지우고,
검증은 "없음 또는 빈 컨테이너"로 바꿨습니다(인메모리 테스트로는 드러나지 않던 실제 Azure 동작).

체크리스트 결과(데모 모드, 운영자 브라우저): 1~3(동의 영역·갱신 안내·지금 만료 → 삭제 증명), 5~7(이의제기 접수·한도 초과 422 뒤
한도 안 재심사·원결정 유지) 정상. 4(심사역 대기 중 차주 철회 → `withdrawn`)는 데모 시나리오에 불필요하다고 판단해 브라우저 확인을
생략했고 단위 테스트(`test_command_raises_event_and_withdraws_running_journey`, 여정의 철회 종료 테스트)로만 검증했습니다.

### 문제 대응(P5b)

- 동의에 `lifecycle_error: queue_disabled`: 함수 설정 `QUEUE_ENDPOINT`가 없습니다. `ops window open`으로 설정을 다시 넣습니다.
- 삭제 증명이 `미완료`: 증명의 `items`에서 `검증 실패` 항목과 비고를 봅니다(`agents_disabled`면 Foundry 설정 없음). 오케스트레이션이 재시도하며,
  끝내 실패하면 수명주기 결과가 `deletion_failed`입니다. 같은 동의에 아무 명령이나(예: `consent-commands` 큐에 `{"consent_id": "…", "action": "remind_now", "requested_by": "ops"}` JSON 원문) 다시 보내면 끝난 사유(철회·만료) 그대로 수명주기를 다시 시작해 검증되지 않은 항목만 다시 지웁니다.
- 이의제기가 목록에 안 보임: 결정 전 신청이나 다른 차주의 신청이면 큐 처리기가 버립니다(App Insights `journey.grievance`).
- 재심사가 `거부된 처리 시도`로 남음: 접수 시점 정책 한도를 넘었거나 사유가 없습니다.

### 알려진 제약(P5b)

- 갱신은 안내만 하고 실제 연장(재동의)은 하지 않습니다. 안내·회신 발송은 시뮬레이션이며 문서에만 남깁니다.
- 재심사 결과는 이의제기 문서에 기록만 하고 신청 문서(원 판단 기록)나 대출을 바꾸지 않습니다.
- Mock 동의 레지스트리는 메모리에 있어 재시작 뒤 철회는 `absent`(이미 없음)로 기록됩니다.
- 승인 뒤 심사 동의를 끝내면 그 대출의 사후 재생을 시작할 수 없습니다(프로필 원천 데이터가 지워짐). smoke 신청도 같습니다.
- 데이터 조립 중 철회가 오면 여정이 실패합니다. 삭제가 조립 에이전트의 쓰기보다 먼저 끝났으면 `assemble_data`가 쓰기 뒤 동의 상태를 보고
  방금 쓴 원천 데이터를 지운 뒤(동의 문서 `late_cleanup`) `ConsentEnded`로 멈춥니다. 철회 종료(`withdrawn`)는 심사역 대기 단계에서만 처리합니다.
- Durable 실행 기록은 지우지 않습니다(동의 ID와 상태만 있음, 개인정보 없음).
- P5b 이전 동의는 명령이 올 때까지 수명주기가 없습니다. 이의제기 중복 확인과 저장은 원자적이지 않습니다(같은 ID 재전송만 막음).
