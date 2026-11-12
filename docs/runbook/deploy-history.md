# 단계별 구현·배포 기록(P2~P6b 원문)

> README에 단계별로 쌓였던 설명·배포 순서·실제 배포 기록·문제 대응·알려진 제약을 **원문 그대로** 옮긴 기록본입니다.
> 당시 기준의 명령·수치·용어이며 고치지 않습니다. 현재 기준 작업 안내는 [setup](setup.md)·[deploy](deploy.md)·[operate](operate.md)·[troubleshooting](troubleshooting.md)을 봅니다.

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
4. **추심 에이전트**(`collections-agent`, gpt-5.4-mini): 2026-11-08부터 경보가 아니라 **상환 원장의 미납 회차**만 처리합니다
   (추심 건 `rc-<재생 ID>-i<회차>`, 최근 경보 요약·신호 종류는 참고). 상환일 3일 전 안내는 LLM 없이 보냅니다.
   승인된 스크립트만 보낼 수 있고(자유 문장 발송 없음), 공정 추심 규칙(KST 08~21시, 7일 7회)을 코드가 막으며,
   상환 곤란이면 재조정 상담 요청(`pending_officer`)을 만듭니다. 재조정 승인·거절은 심사역이 콘솔에서 합니다(`restructure-decisions` 큐).

| 저장 위치 | 내용 |
|---|---|
| `loans`(파티션 `/loan_id`) | 대출·기준선·`monitoring`(재생 ID, 진행 주차, 주차별 지표·트리거·조사 결과) |
| `alerts`(파티션 `/loan_id`) | 경보(`ew-<재생 ID>-wNN`: 근거 신호, 심사역 검토용), 미납 추심 건(`rc-<재생 ID>-iN`: 추심 결과, 재조정 요청), 재생별 연락 기록(`contacts-<재생 ID>`, 상환 안내 포함) |
| 모니터링 동의 문서 | 조기경보·추심 에이전트의 Foundry 대화 ID(`foundry_conversations`) |

- Service Bus가 켜져 있으면 주차 메시지를 `flow.updated` 토픽(세션 = 대출 ID, 구독 `early-warning`)으로, 미납 추심 건을
  `repayment.updated`(구독 `collections`)로 보냅니다. `alert.raised`는 발행만 하고 구독자가 없습니다(2026-11-08). 꺼져 있으면(`ops pause` 상태) 같은 처리를 큐 트리거 안에서 순서대로 직접 합니다(`mode: inline`).
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
- 삭제가 끝난 뒤 늦게 끝난 에이전트 실행의 대화·컨테이너는 한 번만 지우고 `late_cleanup`에 기록합니다. 그 삭제가 실패해도 증명은 완료로
  남고 자동 재시도하지 않습니다. 실행 직후 삭제에 실패한 Code Interpreter 컨테이너가 그 사이 만료되면 삭제가 404라 미확인으로 남습니다.
- P5b 이전 동의는 명령이 올 때까지 수명주기가 없습니다. 이의제기 중복 확인과 저장은 원자적이지 않습니다(같은 ID 재전송만 막음).

## 평가 하네스(P6a)

에이전트 품질·안전·비용을 **실제 Azure 모델로 측정**합니다(스펙 `2026-11-01-p6a-eval-harness-design.md`). 모든 숫자는
**합성 데이터 기준**이며 표본이 작아 경향으로만 해석합니다.

- **방식**: 컴포넌트 평가. 평가 Job(`job-eval`, VNet 안 Container Apps Job)이 에이전트 함수를 직접 부르고 모델 호출만 실제 Azure로 보냅니다.
  Policy·Mock·저장소는 프로세스 안 메모리라 운영 Cosmos·에이전트를 건드리지 않습니다. 평가용 에이전트 이름은 `eval-<이름>-<모델>`이고
  실행 뒤 대화·컨테이너를 지웁니다.
- **suite**: `classify`(Decision-1+mini 폴백 / mini / gpt-5.4, 임계값 곡선), `underwriting`(gpt-5.4 / mini analyst, 근거 인용률·세무 불일치
  탐지·Policy 일치·LLM-judge), `early_warning`(6경로 × 13주 재생, 탐지기 단독 기준선), `safety`(주입 방어율·PII 누출률·반사실 쌍).
- **계측**: httpx 전송 계층에서 호출마다 모델·토큰·지연·재시도·PII 종류를 기록하고(본문은 남기지 않음) 단가표(`src/evals/prices.json`)로
  비용을 계산합니다. 예산 가드 `EVAL_BUDGET_USD`(기본 $5)를 넘으면 남은 케이스를 건너뛰고 `partial`로 표시합니다(케이스 시작 전에 확인하므로 진행 중인 케이스 1건 비용만큼 넘을 수 있음. 예: gpt-5.4 조기경보 1경로 약 $0.16). Decision-1은 공개 단가가 없어
  비용에서 빠지고 호출 수만 보고합니다.
- **결과**: `eval/reports/<실행 이름>/`의 `summary.json`(전체 수치), `report.md`(한국어 리포트, 실패 유형 카탈로그, 사람 검토 표본, 한계),
  `calls.jsonl`(호출 계측). Job은 같은 파일을 Blob `eval-reports/<실행 이름>/`에도 올립니다.

```powershell
python -m evals --fake --out $env:TEMP\eval-fake    # 로컬 회귀(모델 호출 없음, 같은 형식의 리포트)
python -m ops window open --acr                     # 평가 이미지 빌드에만 필요
python -m ops deploy eval                           # az acr build → SERVICE_EVAL_IMAGE_NAME → provision(job-eval 갱신)
python -m ops window close
python -m ops eval --suite all                      # Job 시작 → 종료 대기 → Log Analytics에서 리포트 복원 → eval/reports/
python -m ops eval --collect <실행 이름>            # 회수만 다시(로그 수집 지연으로 복원 실패했을 때)
```

실제 실행 기록(P6a, 2026-11-02, koreacentral):

| 단계 | 실측 |
|---|---|
| `ops window open --acr` | 3.6~4.0분 |
| `ops deploy eval` | 4.3~5.3분(ACR 빌드 40초, 이미지 압축 레이어 합 약 68.9 MiB) |
| `ops window close` | 3.4~3.8분 |
| `ops eval --suite all` | 17.9분(Job 실행 17.1분: classify 약 1.2분, underwriting·early_warning 각 약 7분, safety 약 35초) |
| 실행 이름 / 비용 | `job-eval-9imbutm` / $1.93(단가 확인분, 예산 $5, `partial=False`), 모델 호출 341회, HTTP 오류·재시도 0, 정리 129건 삭제·실패 0 |

주요 실측값(합성 데이터 기준, `eval/reports/job-eval-9imbutm/`):

| 영역 | 결과 |
|---|---|
| 분류(n=규칙 미해결 고유 적요 80그룹, 3구성) | 규칙만으로 80.0% 분류. 세 구성 모두 macro F1 1.00(천장 효과) → 비용·지연으로 비교: Decision-1+mini 폴백 25.0%(호출 60회 기준, 고유 상태 40개 기준 임계값 곡선 0.7에서 22.5%)·$0.0047(+Decision-1 60회, p50 349ms), mini $0.0123, gpt-5.4 $0.0410 |
| 심사(n=8, 검토자 mini 고정) | 분석가 gpt-5.4: 1라운드 근거 통과 100%, 최종 87.5%, 검토 통과 87.5%, 건당 $0.063 / mini: 62.5%·62.5%·62.5%, 건당 $0.020. 두 모델 모두 Policy 일치·세무 불일치 탐지 100%, judge G/R/C 4.12/5.00/4.75(gpt-5.4)·4.12/4.88/5.00(mini) |
| 조기경보(n=6경로 × 13주) | mini precision/recall 1.0/1.0, 평균 리드 71.7일, $0.19 / gpt-5.4 1.0/1.0(5경로, 1경로 agent_error), 70.5일, $0.80 / 탐지기 단독 precision 0.5 |
| 안전성 | 주입 저항률 100%(판별 가능 2건 × 2모델), 반사실 쌍 일관성 100%(2쌍), 모델 입출력 PII 0/341회 |
| 사람 검토(표본 3건, mini 설명) | 정확성·근거 충분성·이해 용이성 모두 5/5(프로젝트 담당자, 채점 양식에 기본값 5가 제시된 상태로 응답, 자동 신호 확인 뒤 변경 없음). 표본 1·2는 자동 근거 검사 실패·검토 미통과이고 judge도 근거 자료에 없는 계절지수 수치를 지적해, 사람 점수가 자동 신호보다 후하다. 1명·3건이라 참고값 |

**변동:** 위 값은 실행 1회 결과입니다. PII 수정 전 같은 데이터·코드로 돌린 직전 실행(`job-eval-n8bqwwv`, 리포트 미보관)에서는 조기경보 mini recall 0.333·평균 리드 18일, 심사 최종 근거 통과 gpt-5.4 100%·mini 75%였습니다. 모델 간 차이와 단일 수치는 LLM 응답 변동 범위 안에 있을 수 있어 경향으로만 봅니다.

첫 실행에서 찾아 고친 결함: azd 출력 누락(`EVAL_JOB_NAME`), 비루트 사용자의 리포트 폴더 쓰기 권한, Foundry 응답 ID(`resp_<16진수>`)의
숫자열을 계좌번호로 본 PII 오탐(50건 → 0건).

- `ops eval --suite <이름>`은 Job 정의(이미지·env·관리 ID)를 그대로 복사하고 `EVAL_SUITE`만 바꿔 ARM `start`로 시작합니다.
- 리포트 회수: Job은 결과 파일을 gzip+base64 6,000자 조각(`EVALCHUNK …` 줄)으로 stdout에 남기고, `ops eval`이
  `ContainerAppConsoleLogs_CL`에서 실행 이름으로 모아 sha256을 확인합니다. 데이터 스토리지(stdat) 네트워크는 열지 않습니다.
- 관리 ID `id-eval`: Foundry(Azure AI User, Cognitive Services OpenAI User), ACR(AcrPull), stdat `eval-reports` 컨테이너만
  (Storage Blob Data Contributor). Cosmos·Key Vault·큐 권한은 없습니다.

### 문제 대응(P6a)

- `ops eval`이 `평가 실패: … 상태 Failed`: 출력된 Log Analytics 쿼리로 Job 로그를 봅니다(`[eval]` 줄, 예외). 이미지가 자리표시면 `ops deploy eval`부터.
- `리포트 복원 실패: 조각 누락`: 로그 수집이 늦었거나 Job이 리포트를 쓰기 전에 끝났습니다. 몇 분 뒤 `python -m ops eval --collect <실행 이름>`으로
  Job을 다시 돌리지 않고 회수만 다시 합니다(일시적 조회 오류·잘린 조각 줄도 재조회 대상). Blob `eval-reports/<실행 이름>/` 사본은 데이터 스토리지가
  공용 접근 차단·사설 엔드포인트 전용이라 운영자 PC에서는 바로 읽을 수 없습니다(VNet 안에서만, 운영자 Blob 역할 없음).
- 리포트에 `agent_error`가 많음: `calls.jsonl`의 `status`·`retries`·`error`를 봅니다(429 → 쿼터, 401/403 → `id-eval` 역할).
- 로컬에서 실제 모델로 돌릴 때(`AzureCliCredential`)는 `AI_PROJECT_ENDPOINT`·`AI_ACCOUNT_ENDPOINT`를 설정하고 운영자 IP 창을 엽니다.
  토큰은 만료 5분 전까지 재사용합니다(호출마다 `az`를 띄우면 느리고 간헐적으로 실패).

### 알려진 제약(P6a)

- 컴포넌트 평가라 큐·Durable을 포함한 전 구간 지연은 재지 않습니다(P6b App Insights).
- 반사실·주입은 케이스당 1회 실행이라 LLM 변동과 구분되지 않습니다. 반사실 불일치 0건은 "이 표본에서 관찰되지 않음"입니다.
- 분류 표본은 어려운 적요 뱅크를 넣어도 구성 간 차이가 작을 수 있습니다(천장 효과면 리포트 한계 절에 표시).
- judge 점수는 LLM 평가라 참고값입니다. `report.md`의 사람 검토 표본 3건(자동 신호 함께 표시)을 채워 함께 봅니다.
- PII 검사는 응답 봉투의 식별자 키(`id`, `name`, `type`, `status` 등)와 `resp_<16진수>` 같은 서비스 식별자 문자열 전체를 건너뜁니다.
  이 키 아래에 들어간 값이나 문장 속 서비스 ID 옆 숫자는 놓치거나 오탐할 수 있어, "PII 0건"은 이 검사 기준입니다.
- Foundry 포털 평가 업로드는 범위 밖입니다. 운영 데이터 기준 공정성 대시보드·SLO는 P6b 절을 봅니다.

## 운영 지표·복원력(P6b)

배포된 신청 여정에 **합성 신청 24건과 장애 시나리오 2개를 실제 Azure에서 흘려** SLO·건당 비용·공정성 격차·복원력 동작을
측정합니다(스펙 `2026-11-02-p6b-ops-metrics-design.md`). 모든 숫자는 **합성 데이터 기준**입니다.

- **측정 트래픽**: `job-ops`(VNet 안 Container Apps Job, 평가 이미지 재사용, 관리 ID `id-ops`)가 균형 격자 신청자
  `load-<원형>-NN` 24명을 `applications-start` 큐로 제출하고, 심사역 대기 도달까지 기다린 뒤 신청 투영을 로그 조각으로 남깁니다.
  로컬 `ops`는 Job 시작·로그 회수·Cost Management·KQL·보고서만 합니다(Cosmos·큐는 사설 엔드포인트 전용).
- **계측**: 활동이 `_meter`(시각·모델별 토큰·판정)를 결과에 붙이고 오케스트레이터가 신청 문서 `steps[]`로 옮깁니다.
  Functions·Policy는 `azure-monitor-opentelemetry`로 App Insights에 요청·의존성·서킷 이벤트를 보냅니다(역할 이름 `orchestrator`·`policy`).
- **지표**: SLO1 접수 → 심사역 대기 p95 ≤ 10분, SLO2 에이전트 단계 성공률 ≥ 99%, 건당 AI 비용(실측 토큰 × `src/evals/prices.json`)
  + 고정비(Cost Management 최근 완료 7일 일평균 × 30, Foundry Models 제외) ÷ 가정 물량, 세그먼트(업종·지역·업력)별 추천 approve율 격차
  (임계값 기본 20%p — `FAIRNESS_GAP_PP`로 변경, 잘못된 값이면 경고 후 20%p — 표본 5건 미만 제외, 업력은 규칙 R104로 설명되는 축).
- **화면**: console `/ops`(공정성 표·경보 배너·SLO 요약), Azure Workbook "대출 여정 운영 지표(합성 데이터 기준)"(SLO·단계 성공률·비용 추이),
  경보 2개(`alert-agent-step-failure`, `alert-policy-circuit-open`). Foundry 프로젝트는 App Insights 연결로 에이전트 트레이스를 남깁니다.
- **장애 주입**: `source-flaky`(mockapi 첫 요청 503 → 데이터 조립 단계 원천별 재시도(에이전트 도구 재시도 + 안전망, 최대 3회)로 회복),
  `policy-down`(Policy 503 → 서킷 open → fail-closed로 심사역 수동 심사). `ops fault`는 `FAULT_MODE` 환경 변수를 켜고 끄며, 실패해도 `finally`에서 원복합니다.

```powershell
python -m ops load --count 24                                   # 측정 배치 → ops/reports/load-<시각>/
python -m ops fault run source-flaky                            # 장애 ① → ops/reports/fault-<시각>/
python -m ops fault run policy-down                             # 장애 ②
python -m ops fault status                                      # 장애 토글 상태
python -m ops metrics --run load-<시각> --fault-runs fault-<…> fault-<…>   # report.md·metrics.json
```

실제 실행 기록(P6b, 2026-11-02, koreacentral):

| 단계 | 실측 |
|---|---|
| `ops window open --acr` | 6.6분(job-ops·경보·Workbook 첫 생성 포함) |
| `ops deploy all` | 이미지 4개(`policy`·`mockapi`·`console`·`eval`) ACR 빌드 + provision 3.8분 + Functions zip 배포 |
| `ops smoke` | 성공(`job-smoke-ntk2hjf`), App Insights 역할 `orchestrator`·`policy` 확인 |
| `ops load --count 24` | `load-20261102110233`, Job `job-ops-84okde8` 11.1분, 제출 24·도달 24·미도달 0, 비용 가드(표본 3건) 예상 $1.65 < 한도 $4 |
| `ops fault run source-flaky` | `fault-20261102111447`, Job 1.2분, 도달 2/2, 원복 확인 |
| `ops fault run policy-down` | `fault-20261102111750`, Job 3.9분, 도달 3/3, 원복 확인(`fault status`: source off·policy off) |
| `ops window close` | 5.4분 |

주요 실측값(합성 데이터 기준, `ops/reports/load-20261102110233/report.md`):

| 영역 | 결과 |
|---|---|
| SLO1 접수 → 심사역 대기 | p95 1.2분(p50 0.6, 최대 5.7, n=24, nearest-rank) — 목표 ≤ 10분 충족 |
| SLO2 에이전트 단계 성공률 | 100.0%(ok 72/72: 자료 조립·신용 평가·거래 분류 각 24, degraded 0, failed 0) — 목표 ≥ 99% 충족 |
| 건당 AI 비용 | $0.0423(gpt-5.4 70회 $0.885, gpt-5.4-mini 92회 $0.131, 합 $1.016 / 24건, 캐시 할인 미반영 상한). Decision-1 48회는 토큰 0·단가 미확인으로 제외 |
| 고정비·건당 총비용 | 월 $304.73(Cost Management 2026-10-25~10-31 중 데이터가 있는 10-31 하루 × 30, USD). 월 100건 $3.09/건, 1,000건 $0.35/건, 10,000건 $0.07/건 — 수작업 $3,500/건 대비 0.1% 이하 |
| 공정성(추천 approve율 격차) | 업종 12.5%p(임계값 이내), **지역 37.5%p(경보: 부산 50% vs 경기 12.5%)**, 업력 50%p(R104로 설명, 경보 대상 아님). 값당 8건이라 원형 통제 격차가 50~100%p로 잡음이 큽니다 |
| 장애 ① source-flaky | mockapi 호출 5회 중 2회·9회 중 4회 실패 → 데이터 조립 단계 원천별 재시도(에이전트 도구 재시도 + 안전망, 최대 3회)로 회복해 모든 단계 ok, 2건 모두 `awaiting_officer` |
| 장애 ② policy-down | Policy 호출 실패 → 서킷 open, 3건 모두 `policy:failed`·`policy_unavailable`·`assessment_unavailable` 플래그로 `awaiting_officer`(fail-closed). `alert-policy-circuit-open` 발화(11:24Z). 에이전트 단계는 실패가 없어 `alert-agent-step-failure`는 발화하지 않음(정상). 03번 신청은 orchestrator → Policy 호출이 0회입니다(서킷 open이 호출을 막음). 서킷이 Flex 인스턴스마다 따로라 신청별 시험(half-open) 호출 수가 다릅니다 |

console `/ops`의 SLO·비용·공정성 표와 경보 배너가 `report.md`와 같은 값을 보여 줍니다. 지역 경보는 합성 균형 격자 24건에서 나온 것이라
모델·정책 편향의 증거가 아니라 "표본을 늘려 원인을 조사할 대상"을 보여 주는 예시입니다.

### 문제 대응(P6b)

- `ops load`가 `측정 실패: … 상태 Failed`: 출력된 쿼리로 `[opsjob]` 줄을 봅니다. 이미지가 자리표시면 `ops deploy eval`부터.
- `summary.json`의 `unreached`가 많음: Functions 로그(`AppTraces`)에서 해당 `application_id`를 찾고, 큐 메시지가 쌓였는지 봅니다.
- `고정비 측정 불가(…)`: Cost Management 429·권한·지연(약 1일)입니다. 다시 실행하거나 `--fixed-monthly-usd`로 수동 입력합니다.
  청구 통화가 원화면 `--fx-krw-per-usd`를 줍니다.
- 장애 토글이 남음(`ops fault status`에 `flaky`·`unavailable`): `python -m ops fault source off` / `policy off`, 또는 `azd provision`.
- 운영자 PC 공인 IP가 연결마다 바뀌어 배포·콘솔이 403: `ops window status`의 IP와 다른 IP를 Functions 접근 제한·console ingress에
  `operator-alt` 규칙으로 잠시 추가하고, 작업 뒤 지웁니다(`ops window close`는 Functions 규칙을 Bicep 상태로 되돌립니다).

### 알려진 제약(P6b)

- 24건은 SLO 표본 수준이며 용량 시험이 아닙니다. p95·99%는 표본 수와 함께 봅니다.
- 서킷브레이커는 Flex 인스턴스마다 따로입니다(분산 서킷은 운영 전환 과제).
- `agent_assessment_mismatch` 플래그가 측정 신청 24/24건(장애 신청 4/5건)에 붙었습니다. 데이터 조립 에이전트가 보고한 상태가 결정론 판정과 다르다는 뜻입니다. SLO2는 파이프라인 단계 완료를 재며 에이전트 자기 평가 정확도는 재지 않습니다. 원인 분석은 후속 과제입니다.
- Python Durable은 오케스트레이터 → 활동 트레이스를 잇지 않아 신청 단위 연결은 `application_id` KQL 조인입니다.
- 건당 AI 비용은 캐시 할인 미반영(상한 추정)이고, 실패한 에이전트 호출은 토큰을 몰라 호출 수만 셉니다.
- 원형 통제 격차는 원형·값당 2건이라 잡음이 큽니다. 경보 판정에는 전체 격차만 씁니다.
- 고정비는 리소스 그룹 비용 데이터가 있는 날만 평균합니다. 이번 측정은 하루치라 일시 정지·배포 창 여닫기에 따라 크게 달라질 수 있습니다.
- `source-flaky`가 켜진 동안에는 mockapi가 첫 요청마다 503을 주므로 console 차주 시뮬레이터(`/mydata/flow`) 재생이 실패합니다. 측정 중에는 쓰지 않습니다.
- Foundry 트레이스(`genAIContent`)에는 프롬프트·응답이 남습니다. 에이전트 입력이 가명화된 값뿐이라는 원칙에 기댑니다.

## 콘솔 UI·온라인 셀러 소액 대출(P7·P8)

P7(콘솔 화면 개편: 차트·단계 표시)과 P8(500만 원 이하 소액 대출, 온라인 셀러 10개 업종 합성 데이터, 측정 격자 48명)을
한 번에 배포했습니다(스펙 `2026-11-03-p8-small-loan-sellers-design.md`). Service Bus는 일시 정지 상태 그대로라 오케스트레이터와
사후 재생은 인라인 모드로 돌았습니다.

실제 실행 기록(P7·P8, 2026-11-04, koreacentral):

| 단계 | 실측 |
|---|---|
| `ops window open --ip <운영자 IP> --acr` | 5.5분 |
| `ops deploy all` | 13.4분. 이미지 4개(`policy`·`mockapi`·`console`·`eval`) 태그 `20261104011410` + Functions zip 배포. `job-ops` `replicaTimeout` 5400초 확인 |
| `ops window close` | 4.2분 |
| `ops smoke` | 성공(`job-smoke-eyag27u`, 3.1분, 신청인 `demo-healthy-001`), 콘솔 관문: 데모 모드(운영자 IP 허용 목록만) |
| 사전 건 다시 만들기 | A `app-20261104-5eb963`(승인), B `app-20261104-f58e82`(인라인 재생 약 140초, 13줄, 경보 2건 — 2주차 높음·8주차 중간), C 동의 `8c77903da5c64deba47e26d8447c90ab`(삭제 증명 완료, 검증 8항목 확인). 상세는 [fallback](../demo/fallback.md) |

- 사후 흐름에 고금리 대출 누적 시나리오가 있는 신청인은 `demo-healthy-001`뿐입니다(`src/mockapis/demo.py`의 `STACKING_APPLICANTS`).
  `demo-stress-003`으로 만든 대출 `app-20261104-4798ea`는 13주 재생을 마쳤지만 경보가 0건이라 데모에 쓰지 않습니다.

재측정(P8, 2026-11-04):

| 단계 | 실측 |
|---|---|
| `ops load`(기본 48건) | `load-20261104041554`, Job `job-ops-fdv8rww` 14.5분, 제출 48·도달 48·미도달 0 |
| `ops fault run source-flaky` | `fault-20261104043236`, 3.7분, 도달 2/2, 원복 확인 |
| `ops fault run policy-down` | `fault-20261104043615`, 6.1분, 도달 3/3, 원복 확인(`fault status`: source off·policy off) |
| `ops metrics` | `ops/reports/load-20261104041554/report.md`. 콘솔 `/ops`가 이 실행을 기본으로 보여 줌 |
| `ops eval --suite all` | `job-eval-ltu3cek`, 31.6분, $1.93(예산 $5, `partial=False`), 실패 32건(분류 오답 28건) |
| 분류 기준 수정 뒤 `ops eval --suite classify` | `job-eval-f3muw8r`, $0.06, 실패 2건 |

주요 실측값(합성 데이터 기준):

| 영역 | 결과 |
|---|---|
| SLO1 접수 → 심사역 대기 | p95 1.2분(p50 0.7, 최대 1.3, n=48) — 충족 |
| SLO2 에이전트 단계 성공률 | 100.0%(ok 144/144) — 충족 |
| 건당 AI 비용 | $0.0484(gpt-5.4 158회 $2.047, gpt-5.4-mini 202회 $0.277, 합 $2.324 / 48건, 캐시 할인 미반영 상한). 고정비 월 $283.66(Cost Management 일평균 3일 × 30) |
| 공정성 | **업종 25.0%p 경보**(디지털·반려동물·뷰티 37.5% vs 리빙 12.5%), 지역 18.8%p(이내), 업력 50%p(R104, 경보 대상 아님). 업종 격차는 격자에서 업력 8개월 칸이 업종마다 다르게 배치된 탓으로 볼 수 있습니다(8개월 칸이 가장 많은 업종은 패션의류·리빙 4/8로, 두 업종이 25.0%·12.5%로 하위권, `docs/limitations.md` P8 항목) |
| 장애 ①·② | P6b와 같은 동작: source-flaky는 원천별 재시도로 회복, policy-down은 서킷 open → fail-closed로 3건 모두 `awaiting_officer` |
| 거래 분류(수정 전) | 세 구성 모두 정확도 87.5~90%, supplier recall 0.667. 오답은 P8 페르소나의 택배사 거래처(`CJ대한통운 택배` 등, 정답 supplier) → 모든 모델이 other_operating으로 분류 |
| 거래 분류(수정 후) | gpt-5.4·gpt-5.4-mini 100%, decision-1+gpt-5.4-mini 97.5%(남은 오답은 일부러 모호한 `대표 개인 신용카드대금` 1종) |
| 심사·조기경보·안전성 | 심사 최종 근거 통과 gpt-5.4 100%·mini 87.5%, 조기경보 precision·recall 둘 다 1.000(평균 리드 mini 74일·gpt-5.4 67일), 주입 저항 100%, 반사실 일관성 100%, PII 0회 |

- **분류 기준 수정**: `src/agents/txn_classify.py`의 `CRITERIA`에서 supplier를 "원재료·상품·식자재·포장재 등 거래처 대금, 택배·물류 배송비"로,
  rent를 "점포·사무실·창고 등 사업장 임대료"로 넓혔습니다(온라인 셀러는 점포가 없고 배송비가 주요 거래처 비용). 실제 심사 분류에도 같은 영향이 있어
  Functions와 평가 이미지(`eval:20261104053042`)를 다시 배포했습니다. 심사·조기경보는 규칙 분류 카테고리만 써서 다시 측정하지 않았습니다.
- **Functions zip 배포가 `ConnectionResetError(10054)`로 두 번 실패**(모바일 회선, 공인 IP <모바일 대역>): `az functionapp deployment source config-zip`가
  업로드 도중 끊겼고 서버에는 배포 기록이 없었습니다. 같은 zip(`ops.deploy.package_function`으로 생성, 29MB)을 `curl`로 Kudu 게시 API에 올리자 11초에 끝났습니다.

```powershell
$tok = az account get-access-token --query accessToken -o tsv
curl.exe -X POST --data-binary "@orchestrator.zip" -H "Authorization: Bearer $tok" -H "Content-Type: application/zip" `
  "https://<function-app>.scm.azurewebsites.net/api/publish?RemoteBuild=false&Deployer=az_cli"   # 202 → /api/deployments에서 status 4 확인
```

- 같은 회선에서 `ops smoke`의 콘솔 관문 확인이 한 번 403, `ops eval`이 az CLI 응답 JSON 오류로 한 번 실패했고, 둘 다 바로 다시 하면 통과했습니다(허용 IP는 일치).

## 2026-11-04 심사역 정책 예외 승인 배포 (`6fa885c`)

- **변경**: 정책 거절·정책 결과 없음 건도 심사역(CreditOfficer·CreditManager)이 승인할 수 있습니다. 브라우저 확인 팝업
  "정책 결과상 거절이지만 승인하겠습니까?"를 거쳐 `policy_override`와 사유가 함께 있어야 하고, 상한은 상품 최대 500만 원입니다.
  적격·조건부 건은 여전히 정책 한도를 넘을 수 없습니다(`amount_exceeds_policy`). 승인 금액 기본값은 신청 금액입니다. 근거: ADR-003.
- **배포**: 콘솔 `console:20261104074252`, Functions(오케스트레이터 재검증) 게시 `81adc9c1`(status 4). `ops smoke` 성공 `job-smoke-5b7q2s9`.
- **회사망에서 Functions 403**: 공인 IP <회사망 IP>로 Container Apps 콘솔은 200이었지만, Functions 기본·SCM 사이트는 같은 IP 허용 규칙에서도
  403을 돌려줬습니다. App Service로 가는 트래픽은 다른 출구 IP를 쓰는 것으로 보입니다. 임시로 `<회사망 /16 대역>`을 허용(`corp-temp`)해 위의 `curl` 게시로
  배포하고 바로 규칙을 지운 뒤 배포 창을 닫았습니다. 창을 닫은 뒤 콘솔 허용 목록은 `allow-0` = <회사망 IP>/32 하나입니다.

## 고객 신청·재생 정지/재개·심사역 화면 개편(P9, 2026-11-04, `373b8c3`)

고객 신청 페이지 `/apply`(동의 3종·최대 1,000만 원·12개월), 재조정 요청 때 사후 재생 정지 → 결정 → 이어서 재생, 신청 상세 탭·평가 차트,
조건부 승인 제거, 마켓플레이스 셀러 5명의 정산 기반 합성 데이터, 사후 예측(기대 입금·4주 잔고 전망)을 배포했습니다
(스펙 `2026-11-04-p9-customer-intake-and-officer-ux-design.md`, 최종 리뷰 반영은 §16). Service Bus는 일시 정지 상태라 인라인 모드입니다.

| 단계 | 실측 |
|---|---|
| `ops window open --ip <운영자 IP> --acr` | 4.2분 |
| `ops deploy orchestrator` | 6.0분(Kudu `f760489c`, status 4). 콘솔이 `source="customer"`를 보내므로 **콘솔보다 먼저** 배포 |
| `ops deploy all` | 이미지 4개 태그 `20261104132225` + 프로비저닝 4.1분. 마지막 Functions zip은 서버에서 12초 만에 끝났지만(`c07daa8c`, status 4) az CLI가 응답을 기다리며 멈춰 15분 뒤 클라이언트 프로세스를 끝냄(명령은 실패로 표시) |
| `ops window close` | 4.7분 |
| `ops smoke` | 성공(`job-smoke-q054g8y`, 3.2분, 신청인 `seller-dailylook-01`) |
| 사전 건(모두 `/apply`로 신청) | A `app-20261104-29eab1`(승인), B `app-20261104-20e255`(2주차 `재조정 대기`, 경보 2건), B′ `app-20261104-107253`(2·8·12주차 정지 → 결정 → 재개, 13주 완료), C 동의 `fae73c19d5f34028896a8ec82ef1d049`(삭제 증명 완료, 검증 8항목 확인). 신청 → 심사역 대기 약 2분 안 |

재측정(P9):

| 단계 | 실측 |
|---|---|
| `ops eval --suite all` | `job-eval-zjmcwjv`, 27.0분, $1.95(예산 $5, `partial=False`), 실패 13건(분류 미해결 4·근거 4·검토 4·에이전트 오류 1) |
| `ops load`(48건) | `load-20261104143855`, Job `job-ops-ta0i7fu` 14.7분, 제출 48·도달 48·미도달 0 |

| 영역 | 결과 |
|---|---|
| SLO1 접수 → 심사역 대기 | p95 1.4분(p50 0.8, 최대 1.5, n=48) — 충족 |
| SLO2 에이전트 단계 성공률 | 100.0%(ok 144/144) — 충족 |
| 건당 AI 비용 | $0.0589(gpt-5.4 $2.527, gpt-5.4-mini $0.302, 합 $2.829 / 48건, 캐시 할인 미반영 상한) |
| 공정성 | 업종 12.5%p(P8의 25.0%p 경보 해소), 지역 12.5%p, 업력 50%p(R104, 경보 대상 아님) |
| 조기경보 | precision·recall 둘 다 1.000(gpt-5.4-mini 6경로, 평균 리드 42.7일 / gpt-5.4 5경로(1경로 호출 오류), 73.0일). 유입 감소 기준을 3주 합 0.75로 넓혀 mini의 첫 경보가 늦어짐(P8 74일) |
| 분류·안전성 | 분류 정확도 97.8~100%, 주입 저항 100%, 반사실 일관성 100%, PII 0회 |

- **IP 변경**: 모바일 회선 공인 IP가 작업 중 대역 A → 대역 B로 바뀌어 콘솔이 403. 콘솔 ingress에 `operator-alt`를 잠시 넣어 `/ops` 확인 뒤 지웠습니다.
  창을 닫은 뒤 콘솔 허용 목록은 `allow-0` = <운영자 IP>/32 하나이므로, 데모 때는 [deploy](deploy.md#운영자-ip가-바뀔-때) 절차로 현재 IP를 허용합니다.

미룬 항목(P9 최종 리뷰의 사소한 지적, 데모 영향 없음):

- 재개 메시지 발행이 실패하면 재개 요청이 사라집니다(30분 가드가 지나면 다시 요청 가능). 재개·종료 메시지에 `pause_alert_id`가 없습니다.
- 고객 신청(`/apply`)에 제출자 인증 주체가 없고 `/apply/{id}`에 ID 형식 검사가 없습니다(고객 인증은 데모 범위 밖, `docs/limitations.md`).
- 콘솔 `_digits`가 `_int`와 겹치고 잘못된 위치의 쉼표도 받습니다. `PRODUCT_MAX_KRW`가 이전(v1) 문서에도 적용됩니다.
- 사후 예측을 모든 주차 행에서 계산합니다(표시는 최근 행만). 배포 도중 진행 중이던 건은 기간 12개월 초과가 남을 수 있습니다.

수정 배포(P9 이후, `cd1936c`):

- **증상**: `ops load`로 들어온 부하 신청(`load-*`)을 심사역이 승인한 뒤 사후 13주 재생을 누르면 `SourceError: {"detail":{"code":"no_flow"}}`로 재생 실패. mock API가 사후 흐름을 데모 셀러 5명에게만 만들었기 때문입니다.
- **수정**: mock API가 부하 격자 48명에게도 정상(STABLE) 사후 흐름을 만듭니다. 부하 신청 금액은 고정 170만 원에서 상품 한도 안 50만~1,000만 원 12단계로 바꿨습니다(업종마다 신청액 합이 같아 공정성 비교가 금액에 흔들리지 않음). 판정·사유 코드는 원형 그대로이고 금리 구간만 금액에 따라 B·C로 내려갈 수 있습니다. 심사역 신규 신청 화면에 최대 1,000만 원을 표시합니다(서버 검증은 기존 `StartRequest le=10_000_000`).
- **배포**: `mockapi:20261104153614`, `console:20261104154047`, `eval:20261104154506`. 실패했던 `load-20261104143855-45`를 다시 재생해 13/13주 완료(경보 0)를 확인했습니다. 다음 `ops load`부터 금액이 다양해지며, 위 P9 재측정 수치는 170만 원 고정 기준입니다.

수정 배포(데모 편의, `e002b65`·`0a2e1ca`·`a862622`):

- **즉시 반영**: 승인·재생·재개·종료처럼 큐로 처리되는 작업 뒤 1초 안에 상세 화면으로 넘어가고, 상태가 바뀔 때까지 2초 간격(최대 30회)으로 새로 고칩니다. 목록·진행 중 화면은 3초 간격입니다.
- **업체명 표시**: 신청·사후 모니터링·이의제기 목록의 신청인을 업체명으로 표시합니다(심사역·관리자는 작은 ID 병기, 감사자는 ID만).
- **데모 셀러 20곳**: 15곳을 추가했습니다(정상 8·불일치 4·스트레스 4·이력 부족 4, 다중 대출 징후 4곳 포함).
- **보안**: GitGuardian이 Key Vault 기본 역할 GUID 변수명(`kvSecrets*`)을 비밀로 오탐해, 이름을 `kvValueReader`·`kvValueAdmin`으로 바꾸고 GitHub 이력을 재작성했습니다(역할 ID와 배포 결과는 같음).
- **배포**: `mockapi:20261104170552`, `console:20261104171030`, `eval:20261104171517`. smoke 성공, `/apply`에 새 셀러 15곳 표시와 목록 업체명 표시를 확인했습니다. 기존 데모 케이스는 건드리지 않았습니다.

## AI 게이트웨이(APIM) 도입 (2026-11-05, `d451d34`)

에이전트 8종과 거래 분류(Decision-1)의 모든 LLM 호출을 APIM Developer(내부 VNet 모드)로 보내는 AI 게이트웨이를 도입했습니다(결정 [ADR-010](../adr/ADR-010-ai-gateway.md), 설계 `docs/superpowers/specs/2026-11-05-ai-gateway-apim-design.md`). 브랜치 `ai-gateway-apim`, 커밋 범위 `3ae8cef..d451d34`:

| 구분 | 커밋 |
|---|---|
| 인프라 | `834c07f`(APIM 내부 VNet), `ac8c7ec`(API·정책·에이전트별 제품), `8f3f907`(Foundry 연결·`AI_GATEWAY` 스위치), `d61cd5e`(게이트웨이 로그 → Log Analytics) |
| 앱·평가 | `4fcc74d`·`2e6167f`·`e57f346`(연결 라우팅, 429·403 분류), `dbc4e4e`(오케스트레이터·Decision-1 경로), `08778bf`(평가 라우팅·프로브 과제), `e177d66`·`869aa6f`(`ops gateway probe`), `febe3f1`(콘솔 콘텐츠 차단 배지) |
| 실측 중 수정 | `f728948`(TPM), `f9eaeb1`(프로브 429 재시도), `f28057c`(Prompt Shields 1만 자), `f43de5d`(도구 라운드), `c05b1b5`·`d451d34`(`gpt-5.4` 용량·분석가 TPM) |

### 관문(Task 4, 라우팅 꺼진 상태)

| 항목 | 실측 |
|---|---|
| APIM 생성 | Developer 내부 모드 프로비저닝 27분 19초 |
| 연결 | `gw-*` 9개가 `ApiManagement` 범주로 생성 성공. `ModelGateway` 범주 대체 사다리는 쓰지 않음 |
| 관문 1(호출 경로) | `gw-reviewer/gpt-5.4-mini`, function + code_interpreter + json_schema → `completed`. Agent Service는 `POST /openai/deployments/<모델>/chat/completions`로 호출(`/responses` 아님) |
| 관문 2(429) | HTTP 429, `retry-after` 53초, `rate_limit_exceeded`("Token limit is exceeded"). Agent Service가 내부에서 1회 재시도해 APIM에 429 2건 |
| 관문 3(차단) | 403이 아니라 HTTP 424(`model_error`, `CustomerManagedDownstreamError … 'innerStatusCode': 403`). APIM 403은 81ms. 그래서 `BLOCK_MARKERS`에 `'innerstatuscode': 403` 추가 |
| 지표·로그 | AppMetrics에 `agent=gw-probe/gw-reviewer`, `model=gpt-5.4-mini` 토큰 합계 기록. `ApiManagementGatewayLogs`는 비어 있어 진단 설정을 더함(`d61cd5e`), 이후 채워짐(본문·헤더 미기록) |
| 스모크 | 창을 닫은 뒤 성공 |

### 라우팅 켜기와 실측(Task 11)

| 단계 | 실측 |
|---|---|
| `AI_GATEWAY_ROUTING=true` 프로비저닝 | 4분 6초(배포 태그 `20261105045427`). `ops window open --acr` 9분 17초 |
| 부하 `load-20261105070836`(3건, 15초 간격) | 도달 3/3, 접수 → 대기열 p95 1.1분, 에이전트 단계 ok 8/9(신용 평가 1건 `gw-analyst` 429), 신청당 $0.0323. 게이트웨이 로그 60분: openai 200 56건, 429 90건(`gw-probe` 74·`gw-analyst` 16), decision 200 15건 |
| 1분 피크 토큰(3시간) | `gw-analyst` 27,505, `gw-data-assembly` 8,388, `gw-reviewer` 2,327, `gw-txn-classifier` 1,762, `gw-consent-notice` 651, `gw-probe` 1,863 → 분석가가 한도 20K 초과. 분석가 20K → 50K, 데이터 수집 15K → 17K(`f728948`) |
| 프로브 unsafe | `job-eval-aw9qy70`: APIM 403 재현(75ms) |
| 프로브 unsafe(`f28057c` 뒤 재확인) | `job-eval-3hh7xn5`(2026-11-05): `content_blocked`, `reproduced=true`. 1만 자 이하 조건을 더한 뒤에도 Prompt Shields가 계속 차단함을 확인 |
| 프로브 throttle(1차) | `job-eval-zimhc4h`: 재현 실패(8회 모두 ok). 시도당 APIM 429 약 11회가 65초에 걸쳐 찍힌 뒤 200 — 클라이언트 429 재시도(MeteredTransport 3회 + FoundryAgents 1회)가 창이 풀릴 때까지 기다림 |
| 프로브 throttle(수정 뒤) | `f9eaeb1`: 프로브 모드에서 `retry_throttle=False`. 재배포 뒤 `job-eval-5zu5jnw`에서 `ok → rate_limited` 재현. 운영 재시도는 그대로 |
| 평가 `job-eval-ba66ts9` | $1.83, 실패 13건, 게이트웨이 "콘텐츠 차단 7건, 토큰 한도 0건". 조기경보 양성 경로 전부 에이전트 오류. 원인: ① Prompt Shields가 1만 자 초과 프롬프트를 거부 → APIM 403 `ContentSafetyPolicyViolated`(오차단 7건), ② `llm-token-limit` 429(`gw-early-warning` 173·`gw-txn-classifier` 16), ③ Decision API 백엔드 429 14건(모델 용량, 평가 재시도가 흡수 — 그대로 둠) |
| 수정 `f28057c` | 정책이 메시지 길이 합 `promptChars`를 계산해 1만 자 이하일 때만 `shield-prompt` 적용(넘으면 카테고리 검사 + 모델 배포 필터 `Microsoft.DefaultV2`만). TPM: `gw-early-warning` 50K, `gw-txn-classifier` 20K(평가 피크 9,982의 2배). 창을 열고 닫으며 프로비저닝 성공(APIM이 새 정책 수락) |
| 평가 `job-eval-aqisk13` | $2.11, 실패 18건, "콘텐츠 차단 0건, 토큰 한도 0건". 조기경보 에이전트 오류 6건 — 22주차 모두 도구 라운드 상한(7호출)에 걸림(직접 경로 평균 4.59). 게이트웨이 경로는 호출당 입력 증가가 150–650 토큰으로 직접 경로(600–2,100)보다 작음 = 턴당 도구 호출이 적음 |
| 수정 `f43de5d` | 라우팅 에이전트(`GatewayRunner`·`EvalRunner`)에만 `GATEWAY_MAX_ROUNDS = 12`, 직접 경로는 `max_rounds=6` 유지 |
| 평가 `job-eval-adcwjhc`(early_warning) | $1.84, 실패 1건(gpt-5.4 `ev-etax_k`가 탐지기 단독 경보로 폴백), precision·recall 1.0/1.0(mini 6경로·gpt-5.4 5경로), "콘텐츠 차단 0건, 토큰 한도 0건". 직접 호출 기준선 `job-eval-zjmcwjv`도 1.0/1.0 |
| 스모크 | `job-smoke-7gvt209`(라우팅 켠 직후), `job-smoke-tu6sfos`(`f43de5d` 배포 뒤), `job-smoke-4des4o7`(최종 TPM 뒤) 모두 성공 |

### 분석가 TPM 조정과 부하 비교(48건)

| 부하 실행 | 분석가 TPM | 결과 |
|---|---|---|
| `load-20261105111618` | 50K | SLO2 92.4%(신용 평가 11건 실패), `gw-analyst` 429 236건, 피크 59.7K |
| `load-20261105114119` | 100K(`c05b1b5`, `gpt-5.4` 용량 50 → 150 포함) | SLO2 98.6%, 429 56건, 피크 110.6K |
| `load-20261105120255` | 150K(`d451d34`, `gpt-5.4` 용량 150 → 200 포함) | p95 1.8분, SLO2 100%(144/144), 건당 AI $0.0939 |
| `load-20261104143855`(P9, 직접 호출) | — | p95 1.4분, SLO2 100%(144/144), 건당 AI $0.0589 |

- 비용은 직접 호출 대비 약 +59%($0.0589 → $0.0939)입니다. 게이트웨이 경로가 라운드마다 전체 맥락을 다시 보내고 라운드가 늘어 `gpt-5.4` 입력이 1.16M 토큰이었습니다(캐시 할인 미반영 상한).
- 최종 1분 피크(`load-20261105120255`): 분석가 147.7K, 검토자 15.4K, 데이터 수집 13.2K, 분류 3.8K. 재시도가 흡수한 429는 분석가 36건·검토자 14건이며 `gw-reviewer`는 15K를 유지했습니다(SLO 영향 없음).
- `gpt-5.4`는 GlobalStandard 배포 용량을 50 → 150(`c05b1b5`) → 200(`d451d34`, 1K TPM 단위, 쿼터 250)으로 올렸습니다. 종량제라 고정비는 늘지 않습니다.

최종 에이전트별 TPM(`infra/resources.bicep`의 `gateways` 표): `gw-analyst` 150,000 · `gw-early-warning` 50,000 · `gw-reviewer` 15,000 · `gw-data-assembly` 17,000 · `gw-txn-classifier` 20,000 · `gw-collections` 10,000 · `gw-consent-notice` 5,000 · `gw-grievance` 5,000 · `gw-probe` 1,000. `gw-early-warning`은 평가 1분 피크 53.7K가 한도 50K를 넘었지만 Agent Service 재시도가 흡수했고(429 50건, 클라이언트 호출은 모두 200) 운영 조사는 대출당 주 1회라 그대로 뒀습니다. `gw-collections`·`gw-grievance`는 이 경로에서 측정하지 못했습니다.

### 운영자 네트워크: 모바일 회선의 App Service 출구 IP

- **증상**: 모바일 회선에서 `ops deploy orchestrator`가 403 두 번. 창이 열려 있었고 `api.ipify`가 알려 준 IP의 /16을 허용해도 마찬가지였습니다(콘솔 Container Apps 접속은 별개).
- **원인**: App Service(Functions 주 사이트와 SCM 사이트)로 가는 트래픽이 통신사 NAT 출구 IP로 나가 `api.ipify` 값과 달랐습니다. 서로 다른 /24 대역의 IP 3개가 회전했습니다.
- **해결**: 창이 열린 동안 Functions 앱에 임시 진단 설정(`ipsec-probe-temp`)으로 `AppServiceIPSecAuditLogs`를 작업 영역에 보내고, SCM 사이트에 몇 번 요청한 뒤 약 5분 기다려 `AppServiceIPSecAuditLogs | extend ip=tostring(split(CIp, ':')[0]) | distinct ip`로 IP를 조회했습니다. 그 IP들을 `/32` `operator-alt2`~`operator-alt4`(priority 111부터)로 임시 허용하고, 위 `curl` Kudu `/api/publish`로 게시(`66d1aad6`, status 4)한 뒤 규칙과 진단 설정을 지우고 `ops window close`로 닫았습니다. 절차는 [deploy](deploy.md#app-service-출구-ip가-다를-때)에 있습니다.
- `publicNetworkAccess`는 창이 닫힌 동안 `Disabled`라 접근 제한 테스트는 창이 열렸을 때만 의미가 있습니다. 같은 날 콘솔 403도 한 번 있었는데 모바일 IP가 다시 바뀐 탓이라(배포 문제 아님) `window open`·`close`로 `CONSOLE_IP`를 갱신했습니다.

### 롤백

```powershell
azd env set AI_GATEWAY_ROUTING false
azd provision --no-prompt
python -m ops smoke
```

모든 LLM 호출(Decision-1 포함)이 직접 호출로 돌아가고, APIM 리소스와 월 약 $51은 남습니다. APIM을 지우려면 롤백 뒤 리소스를 삭제합니다(재생성 30–60분, 실측 27분). 자세한 절차는 [deploy](deploy.md#ai-게이트웨이-켜기끄기)입니다.

## 2026-11-05 심사역 결정 반영 지연 단축

- **증상**: 콘솔에서 승인한 뒤 화면 반영까지 약 11초(app-20261105-a25bc0: 클릭 13:33:12 → 오케스트레이션 이벤트 수신 13:33:23 UTC).
- **원인**: Flex Consumption은 트리거 그룹별로 0에서 확장해 officer_decision 큐 트리거가 콜드 스타트했고, 한가한 인스턴스는 큐를 최대 60초(Durable 큐 30초) 간격으로 확인합니다.
- **조치**(`a68a9a0`): `host.json`의 `queues.maxPollingInterval`과 `durableTask.storageProvider.maxQueuePollingInterval`을 2초로, `durable`·`function:officer_decision`·`function:start_application` 그룹에 always ready 1개씩(월 약 $62). `ops window open` → `ops deploy orchestrator` → `ops window close`.
- **측정**(app-20261105-bc8e70, 콘솔 경유): 승인 POST 후 상태 반영 **2.6초**, 사후 모니터링 링크 5.2초.

## 2026-11-06 App Gateway WAF v2 진입점

- **바꾼 것**: `ca-console`을 지우고(24초) `cae-web-amalend` 환경을 지웠습니다(1218초). 콘솔은 internal `cae-int`로 옮겼고, 앞단에 `agw-amalend`(WAF_v2, 자동 확장 최소 0)·`waf-amalend`(Prevention, DRS 2.1, `allowOperatorOnly` 사용자 지정 규칙)·`pip-agw-amalend`·`nsg-appgw`를 새로 만들었습니다. 콘솔 자체 서명 인증서는 `ops window open`이 Key Vault에 넣습니다.
- **1차 `window open`(632초, 실패)**: 첫 프로비저닝·인증서 저장은 성공했지만 두 번째 프로비저닝이 `ApplicationGatewayNetworkIsolationRequiresSubnetDelegation`으로 실패했습니다. 이 구독은 App Gateway 네트워크 격리 기능이 등록돼 있어 게이트웨이 서브넷을 `Microsoft.Network/applicationGateways`로 위임해야 합니다. `fad9181`에서 `snet-appgw`를 위임했습니다.
- **2차 `window open`(1468초, 프로비저닝 24분 16초)**: 게이트웨이 `Running`, 백엔드 상태 `Healthy`("Received 200 status code"). 버전 없는 비밀 ID의 PFX와 ACA 내부 기본 인증서 신뢰 모두 문제없었습니다.
- **인터넷에서 80/443 시간 초과**: 테넌트 정책이 NSG 없는 서브넷에 `vnet-amalend-snet-appgw-nsg-koreacentral`(GatewayManager만 허용)을 붙여 기본 규칙이 인터넷 인바운드를 막았습니다. 임시 허용 규칙으로 원인을 확인한 뒤(`/healthz` 200, HTTP→HTTPS 301) `ad1a49b`에서 `nsg-appgw`(GatewayManager·AzureLoadBalancer·Internet 80/443)를 직접 소유하게 바꿨습니다. 3차 `window open` 479초(프로비저닝 7분 43초). 임시 규칙은 지웠고 정책이 만든 NSG는 서브넷에서 떨어진 채 남아 있습니다.
- **smoke**: 성공(콘솔 관문: 데모 모드, 운영자 IP에서 App Gateway로 열림). 콘솔 코드는 바뀌지 않아 `ops deploy console`은 건너뛰었습니다.
- **403 확인**: 모바일 회선 출구 IP가 `window open` 뒤 같은 /16 안에서 바뀌어 콘솔이 403을 돌려줬습니다. `AGWFirewallLogs`에 `allowOperatorOnly` Blocked 5건이 남아 비운영자 차단이 403(시간 초과 아님)으로 동작함을 확인했습니다. `azd env set CONSOLE_IP <새 IP>`와 WAF 정책 `matchValues` 갱신으로 바로 200이 됐습니다(다음 프로비저닝도 같은 값을 씁니다).
- **WAF 로그**: 이 기간 DRS 2.1 관리 규칙 탐지 0건(오탐 없음), 사용자 지정 규칙 차단 5건. `AGWAccessLogs`는 200 4건·301 2건·403 5건.
- **`window close`**: 333초. 닫은 뒤에도 콘솔 `GET /` 200.
- **참고**: `/applications`는 POST 전용이라 GET은 405가 정상입니다. 콘솔 첫 화면은 `https://<APPGW_FQDN>/`입니다.
## 2026-11-07 에이전트 평가 없이 심사역 대기로 넘어간 신청

- **증상**: 분석가를 `gpt-6.1-sol`로 바꾼 뒤 들어온 app-20261107-9d2785(04:57Z)·app-20261107-a49a8a(05:02Z)가 심사역 대기인데 콘솔에 "에이전트 평가가 아직 없습니다". `assess_credit`이 2~4초 만에 끝났습니다.
- **원인**: `AppExceptions`에 HTTP 400 "Function tools with reasoning_effort are not supported for gpt-6.1-sol in /v1/chat/completions". 에이전트가 게이트웨이 연결(`gw-analyst`, chat completions)로 모델을 부르는데 이 경로에서 `gpt-6.1-sol`은 함수 도구 + 추론을 거부합니다. 실측: `reasoning.effort=none`은 `gpt-6.1-sol`·`gpt-5.6-sol`·`gpt-5.4` 모두 미지원, 기본 추론으로 `gpt-5.6-sol`·`gpt-5.4`는 성공, 게이트웨이 없이 `/responses`로 직접 부르면 `gpt-6.1-sol`도 성공. 당시 설계는 평가가 없어도 `assessment_unavailable` 플래그만 남기고 심사역에게 넘겼습니다.
- **조치**(`4359b75`): 분석가를 `gpt-5.6-sol`로(`ANALYST_MODEL`). 평가가 없으면 한 번 더 시도하고 그래도 없으면 신청을 `failed`(오류 단계 `assess_credit`, 마지막 에이전트 오류 포함)로 끝내 심사역 대기로 넘기지 않습니다. 데이터 부족·정책 장애 경로는 그대로 심사역에게 갑니다. 기존 2건은 그대로 두었습니다.
- **용량**: 첫 `window open`(240초)이 `InsufficientQuota`로 실패했습니다. 구독의 다른 리소스가 `gpt-5.6-sol` GlobalStandard 쿼터 250 중 100을 써서 이 배포는 최대 150입니다. 배포를 200 → 150(분석가 150K, 평가 Job 공유)으로 고쳐 두 번째 `window open` 250초(프로비저닝 4분)에 성공했습니다. `gpt-6.1-sol` 배포는 50으로 줄여 보유만 합니다.
- **배포·확인**: `ops deploy orchestrator` 366초. `ops smoke` 성공(159초, `assess_credit` 28.5초로 실제 평가 생성), 이후 20분간 `AppExceptions` 0건. 조사용 Foundry 에이전트 `probe-effort`는 지웠습니다.
## 2026-11-07 에이전트 의견 탭 보강

- **변경**(`998a062`): `에이전트 의견` 탭에 권고 배지·KPI(근거 확인 주장·검토 결과·토큰·단계 시간), 권고의 의미와 정책 판정 일치 여부, 주장별 근거 값 표(✓/⚠)와 근거 출처 도넛, 제안 구조 표와 월별 매출 지수·감액 월 차트, 반사실 표·막대, 반성 루프 라운드 표·도구 호출·지적 수 차트, 모델별 토큰 사용 표를 넣었습니다. 계산은 `src/console/opinion.py`의 순수 함수입니다. 같은 배포로 결정·요청 반영 대기 화면의 자동 새로고침이 2초에서 5초로 바뀌었습니다(`979e1f7`).
- **배포·확인**: `ops deploy console`(`console:20261107062523`) 뒤 `ops smoke` 성공. App Gateway로 smoke 건(분석 `gpt-5.6-sol`, 주장 7/7 확인, 1라운드 통과)과 이전 거절 건(`gpt-5.4`, 주장 10/10, 반사실 3건)의 탭을 확인했습니다.
## 2026-11-07 `agent_assessment_mismatch` 원인 수정

- **증상**: 거의 모든 신청에 `agent_assessment_mismatch`가 붙었고(P6b 측정 24/24), 콘솔 라벨 "에이전트 권고와 정책 판정 불일치"는 실제 의미(데이터 수집 에이전트의 자기 보고 ≠ 코드 판정)와 달라 에이전트 의견 탭의 "같은 방향"과 모순돼 보였습니다.
- **원인**: Foundry 트레이스(`invoke_agent data-assembly`, 이틀치 161건)에서 에이전트가 `complete`로 보고한 것은 17건뿐이었습니다. 입력이 "동의 범위: account, pg, tax. 수집 가능한 원천: profile, transactions, tax, pg."처럼 범위와 원천 이름을 따로 줘서 모델이 profile·transactions를 범위 밖으로 보고 건너뛰었습니다. 안전망이 마저 모아 코드 판정은 `complete`라 플래그가 붙었습니다. 결정에는 영향이 없었습니다.
- **조치**(`246d321`): 입력을 원천별 동의로 바꿨습니다(`profile(account 동의), transactions(account 동의), …`, 모두 수집 지시). 콘솔 라벨은 "데이터 수집 에이전트 보고와 코드 검증 불일치".
- **배포·확인**: `ops deploy orchestrator`·`ops deploy console`(`console:20261107072608`) → `ops smoke` 성공 → `ops load --count 8`(`load-20261107073503`, 8/8 도달). 배포 뒤 에이전트 최종 보고 7/7 `complete`(로그 수집 시점), 콘솔에서 smoke 1건·부하 8건 모두 플래그 없음. 이전 신청은 플래그가 그대로 남습니다(새 라벨로 표시).
## 2026-11-07 분석가 `gpt-5.6-sol` 재측정

부하 건수는 콘솔 목록에서 기존 건이 밀려나지 않도록 10건으로 줄였습니다.

| 단계 | 결과 |
|---|---|
| `window open --acr` → `ops deploy eval` | `eval:20261107121520`(이전 이미지 `eval:20261105094318`은 `gpt-5.6-sol` 설정 전) |
| `ops load --count 10` | `load-20261107122036`, Job `job-ops-fjlgjae`, 제출 10·도달 10. p95 0.9분(표본 부족), SLO2 100%(30/30), 건당 AI $0.0738(`gpt-5.6-sol` 43호출 $0.671, mini 70호출 $0.067). 콘솔에서 10건 모두 `agent_assessment_mismatch` 없음 |
| `ops eval --suite underwriting` | `job-eval-ngrkucj`, $0.86, 실패 2건(모두 비교 구성 `gpt-5.4-mini`: 근거 1·검토 1). `gpt-5.6-sol` 1라운드 근거 100%·최종 100%·검토 100%·1.12라운드·평가서 건당 $0.0783 |

- **비교**(게이트웨이 경로 `gpt-5.4`): 부하 `load-20261105120255` 건당 $0.0939(분석가 신청당 8.4호출), 평가 `job-eval-aqisk13` 1라운드 75.0%·검토 87.5%·2.00라운드·건당 $0.0984. 단가는 높지만 도구 라운드가 줄어 건당 비용이 내려갔습니다. 표본이 작고(부하 10건, 평가 8케이스 1회) 부하 격자가 달라 경향으로만 봅니다.
- **`window close`** 완료.
## 2026-11-08 상환 기반 미납 추심 배포

- **변경**: 추심을 조기경보가 아니라 상환 원장의 미납 회차로 시작합니다(`029374d`~`39e7c25`, 계획 `docs/superpowers/plans/2026-11-07-repayment-collections.md`). 이미지 `mockapi:20261108004332`, `console:20261108005535`, Functions `func-gaz7yh2zic7zc`. Service Bus는 일시 정지 상태라 `repayment.updated` 토픽은 다음 `ops resume` 때 생깁니다.
- **smoke 콘솔 관문 시간 초과**: `agw-amalend`가 `Stopped`였습니다(외부 자동화로 추정, 2026-11-06과 같은 증상). `az network application-gateway start` 약 6분 뒤 `ops smoke` 성공.
- **1차 검증** `app-20261108-ef4d8f`(`seller-dailylook-01`, 500만 원·6개월): 심사역 대기 42초, 재생 371초. 원장 1·2회차 납부(`P00083`·`P00169`), 3회차 미납 → `rc-rp-caec686c32-i3`, 안내 3건, 경보 카드 "차주 연락 없음", `재생 완료`. 그러나 추심 에이전트가 `contacted`(missed_payment)로 끝나 재조정 요청이 없었습니다.
- **원인**: 재생이 경보에서 멈추지 않아 13주 조사가 몇 분 안에 몰려 `gw-early-warning`(50K)에서 429 16건(78회 중)이 났고 12주차 조사가 `detector_only`로 폴백했습니다. 폴백 경보 요약에는 근거가 없어 추심 에이전트가 고금리 중첩·매출 급감을 보지 못했습니다.
- **조치**: `63cb146` 추심 건 `recent_alert`에 신호 종류(`signals`)를 넣고 지시문이 이를 근거로 씁니다. TPM 50K → 100K. 2차 검증 `app-20261108-4922b2`(재생 240초)에서 429는 1건으로 줄고 추심은 `routed`(hardship_offer, `debt_overload`)였지만 1분 피크 85.9K로 429 1건이 남아 `1d15822`에서 150K로 올렸습니다(`gpt-5.4` 배포 200K 안).
- **3차 검증** `app-20261108-d0b4b0`(재생 234초): EW 429 0건, 1분 피크 103.8K. 원장 납부 2·미납 1, 마지막 추심 건 `routed` → `재조정 상담 요청`(`pending_officer`)과 `재조정 결정` 폼. 결정하지 않고 데모 B로 둡니다.
- **남은 문제**: 2·3차 모두 7주차(트리거 `inflow_drop`·`buffer_drop`·`loan_stacking`) 조사가 HTTP 오류·429 없이 `agent_error`로 폴백했습니다. 오류 문구는 비공개 Cosmos의 `monitoring.weeks[].investigation.record.error`에만 있어 확인하지 못했습니다(도구 라운드 한도 또는 출력 스키마 불일치로 추정). 폴백 경보도 심사역 검토 대상으로 남고 추심 판단은 신호 종류로 동작합니다.
- **`window close`** 완료.
## 2026-11-08 정산 원장·정책 v3 배포

- **변경**: 마켓플레이스 정산 원장 대사와 정책 `2026.11-v3`(`016b357`~`16c193a`, 계획 `docs/superpowers/plans/2026-11-08-settlement-ledger-policy-v3.md`). 신청 기간 3~6개월(기본 6)과 결정 보내기 버튼 잠금도 함께 배포했습니다. 이미지 `policy:20261108075226`, `mockapi:20261108084226`, `console:20261108084823`, Functions `func-gaz7yh2zic7zc`. 순서는 policy → orchestrator → mockapi → console입니다(mockapi의 새 필드를 `extra="forbid"`인 오케스트레이터가 먼저 받아야 함).
- **Functions 403**: `ops deploy orchestrator`가 두 번 403이었습니다. `operator` 규칙에 이미 `api.ipify` IP가 있었으므로 App Service 출구 IP가 달랐습니다. [deploy](deploy.md#app-service-출구-ip가-다를-때) 절차로 `AppServiceIPSecAuditLogs`를 조회해 출구 IP 2개(회전)를 `operator-alt2`·`operator-alt3`으로 허용했고, 세 번째 시도에 성공했습니다(첫 IP만 허용했을 때는 업로드 중 연결 재설정).
- **smoke**: 첫 `ops smoke`는 Job은 성공했지만 콘솔 관문이 403이었습니다(공인 IP가 `CONSOLE_IP`와 달라짐). `window open --acr`로 갱신한 뒤 `ops smoke` 성공(`job-smoke-jijy4vi`).
- **라이브 확인**(500만 원·6개월):
  - `app-20261108-04b021`(`seller-mugshop-21`): 부적격 [R002, R106, R108, R901], 정산 대사 카드의 정산금 계좌 입금 77%(기준 90% 이상), 에이전트 의견이 `pg_reconciliation_ratio`를 인용합니다.
  - `app-20261108-7da0f6`(`seller-dailylook-01`): 적격, 정산금 계좌 입금 100%, 에이전트 권고는 승인입니다.
- 임시 규칙은 그 뒤의 프로비저닝이 이미 지웠고, 진단 설정 `ipsec-probe-temp`는 지웠습니다. **`window close`** 완료.
- **최종 리뷰 수정 재배포**(`8a8c717`): 매출 추세·정산 대사 창의 끝을 마지막 거래 다음 영업일로 맞춰(데이터가 오래돼도 R106이 번지지 않음) 고비용 상환 단위를 "곳"에서 "건"으로 고쳤습니다. `console:20261108094644`·오케스트레이터 재배포(이번에는 Functions 403 없음), `ops smoke` 성공(`job-smoke-x5vdop3`). `app-20261108-e260af`(`seller-glowlab-03`): 회부 [R105, R106], "고비용 대출 상환 25건 … (기준 0건)". **`window close`** 완료.
## 2026-11-08 조기경보 조사 gpt-5.6-luna·429 백오프 배포

- **변경**(`fb21928`): 조기경보 조사 모델 `gpt-5.4` → `gpt-5.6-luna`. 429는 지수 백오프(max(retry-after, 1·2^n)초, 상한 30초)로 최대 5번 다시 보냅니다(다른 에이전트는 1번 그대로). 지시문이 독립 도구 호출(`get_weekly_series`·`get_baseline`·`get_peer_cohort`, 여러 `query_transactions`)을 한 응답에 묶도록 안내합니다. 용량: `gpt-5.6-luna` 배포 100 → 150(쿼터 250 중 다른 리소스 100), `gw-early-warning` TPM 150K → 75K(luna 게이트웨이 합 147K).
- `window open --acr`(프로비저닝 4분 13초) → `ops deploy orchestrator`(Functions 403 없음) → `ops smoke` 성공(`job-smoke-elvz75p`). Function 설정 `EW_MODEL=gpt-5.6-luna`, 배포 용량 150 확인.
- **라이브 확인** `app-20261108-29c02d`(`seller-dailylook-01`, 500만 원·6개월, 재생 207초): 조사 5건(1·2·7·11·12주차) 모두 `investigator=gpt-5.6-luna`로 끝났고 폴백 0건입니다. 이전에 `agent_error`로 폴백하던 7주차도 정상 경보를 냈습니다. `gw-early-warning` 호출 43건·429 0건(같은 경로의 이전 `gpt-5.4` 재생은 56건).
- **`window close`** 완료.
- **luna 쿼터 증설 반영**: 구독의 다른 리소스가 쓰던 `gpt-5.6-luna` 쿼터가 100 → 10으로 줄어 이 배포를 150 → 240(쿼터 250/250)으로 올리고, `gw-early-warning` TPM을 75K → 150K(이전 `gpt-5.4`에서 429 0건을 확인한 값)로 되돌렸습니다. luna 게이트웨이 합 222K ≤ 240K. `window open` 프로비저닝 → 배포 용량 240·APIM 정책 `tokens-per-minute="150000"` 확인 → `ops smoke` 성공(`job-smoke-olmrsib`) → **`window close`** 완료.
## 2026-11-08 사후 모니터링 새로 고침 7초

- **변경**(`6686059`): 사후 모니터링 화면(대출 목록·대출 상세)의 자동 새로 고침을 3·5초에서 7초(`MONITORING_REFRESH_SECONDS`)로 맞췄습니다. `window open --acr` → `ops deploy console`(`console:20261108115721`) → **`window close`** 완료.
- **콘솔 403**: 모바일 회선 출구 IP가 바뀌어 콘솔이 403이었습니다. 창을 닫은 뒤 `AGWFirewallLogs`의 브라우저 IP와 새 PC IP를 `allowOperatorOnly` 조건으로 더하고 `azd env set CONSOLE_IP`로 갱신해 `/`·`/loans` 200을 확인했습니다. 더한 조건은 다음 프로비저닝이 지웁니다.
## 2026-11-08 심사역 결정 채팅 배포

- **변경**(`8c0a256`~`b950e9f`, 계획 `docs/superpowers/plans/2026-11-08-officer-chat.md`): 심사역 결정 탭에 현금흐름·정책 판정·에이전트 의견만 근거로 답하는 채팅을 더했습니다. 에이전트 `officer-assistant`(연결 `gw-officer-chat`, TPM 100K), 모델 `gpt-6-luna`(2026-09-22, GlobalStandard 100), 콘솔 ID에 Foundry User·OpenAI User 역할, 콘솔 환경 변수 `AI_PROJECT_ENDPOINT`·`AI_GATEWAY`·`AI_GATEWAY_URL`·`CHAT_MODEL`. App Gateway `console-https` 요청 시간 제한 60 → 180초(429 재시도 예산보다 길게).
- `window open --acr` → `ops deploy console`(`console:20261108150547`) → `ops smoke` 성공(`job-smoke-u4iibc0`, 채팅 점검 포함) → **`window close`** 완료. 프로비저닝이 `allowOperatorOnly`를 PC IP 하나로 되돌려 `AGWFirewallLogs`의 브라우저 IP를 다시 더했습니다.
- **WAF 오탐**: 앞선 답변을 담은 세 번째 질문부터 403이었습니다. 답변의 괄호 출처 표기로 `history.text`마다 942430(SQL 특수문자 12개 이상)이 매칭돼 이상 점수 6 ≥ 5(949110)였습니다. `question`·`history.*` 인자에서 942430만 빼는 제외를 bicep에 넣고, 라이브 정책에는 같은 제외를 `az network application-gateway waf-policy managed-rule exclusion rule-set add`로 바로 적용했습니다.
- **라이브 확인** `app-20261108-c92b51`(심사역 대기): 질문 4개(부족한 달, 정책 판정·회부 사유, 권고 근거, 대표자 주소)가 모두 200, 1.8~8.4초. 답변마다 괄호로 출처(현금흐름·정책·에이전트 의견)를 달았고 주소 질문은 "자료에 없음"이었습니다. 승인·거절 지시와 실명은 없었습니다. `ca-console` 로그에 `officer_chat ... result=ok` 줄만 있고 질문 원문은 없으며, APIM 429는 0건, `/`·`/loans` 200.
- **gpt-6-luna 증설(2026-11-09)**: 채팅 배포 `gpt-6-luna` 100 → 250(쿼터 250/250), `gw-officer-chat` TPM 100K → 250K(DataZoneStandard 변형은 100 그대로). `window open` 프로비저닝 4분 40초 → 배포 용량 250·APIM 정책 `tokens-per-minute="250000"` 확인. `agw-amalend`가 다시 `Stopped`라 smoke 콘솔 관문이 시간 초과였고, `az network application-gateway start` 뒤 `ops smoke` 성공(`job-smoke-ielem0w`). 채팅 질문 4개 모두 200(2.0~9.9초). **`window close`** 완료. WAF 942430 제외는 bicep에 있어 유지됐습니다.

## 2026-11-09 심사 도우미 정책 설명 배포

- **변경**(`a554d5a`~`5f00580`, 계획 `docs/superpowers/plans/2026-11-09-officer-chat-policy-explain.md`): 정책 엔진 `explain()`이 규칙별 점검·한도 계산·금리 구간 추적을 돌려주고, 채팅 스냅샷에 판정 버전의 규정(`policy.rules`)과 계산 추적(`policy.trace`)을 넣었습니다. 답변 출처 표기는 "(정책 규정 <버전>)"·"(정책 계산)"입니다. v1 규정은 제자리 개정(2026-11-04) 전에 접수한 건이면 규정·추적을 빼고 `trace_note`로 알립니다. 콘솔 이미지에 `policy` 모듈·`rulesets`와 `PyYAML==6.0.3`을 더했습니다.
- `window open --acr` → `ops deploy console`(`console:20261109013727`) → `ops smoke` 성공(`job-smoke-7775qf2`) → **`window close`** 완료.
- **라이브 확인** `app-20261108-c92b51`(v3, 심사역 대기): 질문 4개 모두 200(2.5~21.5초). 금리 구간 기준은 DSCR 1.76 → B와 v3 구간(A 2.0·B 1.5·C 1.2·D 1.0), 한도는 월 유입 × 1.5와 상품 최대 10,000,000원·신청액 5,000,000원·100,000원 단위, R102는 매출 변동성 0.06 ≤ 기준 0.5로 답했고, 구간별 실제 금리는 "자료에 없음"이었습니다. `ca-console` 로그에는 `officer_chat ... result=ok` 줄만 있고 질문 원문은 없으며, `/`·`/loans` 200.

## 2026-11-09 에이전트 요약 확장 배포

- **변경**(`f607c99`): 신용 평가 분석가의 `summary`를 세 문장 이내에서 5~7문장 해설(상환 여력 → 위험 요인 → 정책 판정과의 관계 → 제안 구조 → 결과를 바꿀 조건)로 바꾸고, `check_grounding`이 요약 속 숫자도 근거 카탈로그·제안 금액·인용 거래 금액과 대조합니다. 에이전트 버전은 실행 시 코드의 지시로 만들어지므로 오케스트레이터만 배포했습니다. 기존 신청의 요약은 그대로이고 새로 평가하는 신청부터 적용됩니다.
- `window open --acr` → `ops deploy orchestrator`가 두 번 403이었습니다. App Service가 본 출구 IP가 `api.ipify` IP와 같은 /16의 다른 주소였습니다([deploy](deploy.md#app-service-출구-ip가-다를-때) 절차). SCM은 주 사이트 규칙을 따르므로(`scmIpSecurityRestrictionsUseMain=true`) `operator-alt2`를 **주 사이트**에 넣어야 했습니다(SCM 규칙에 넣으면 여전히 403). 세 번째 시도에 성공했고, 임시 규칙과 진단 설정 `ipsec-probe-temp`는 지웠습니다. **`window close`** 완료. `ops smoke`는 첫 시도에 az CLI JSON 오류였고 다시 돌려 성공(`job-smoke-2uzou1w`), `/`·`/loans` 200.
- **라이브 확인** `app-20261109-045335`(`seller-dailylook-01`, 5,000,000원·6개월): 109초 만에 평가가 났고, 요약이 5문장으로 영업 DSCR 2.2·월 유입·완충일수 → 인출 반영 DSCR 0.638·2월 계절 지수·플랫폼 비중 → 정책 eligible·한도·금리 구간 A → 5,000,000원·6개월 계절형 상환 → 인출 반영 DSCR 1.0 이상이면 재검토 순서였습니다.

## 2026-11-09 콘솔 UX 3건 배포(탭 유지·요약 심사역 결정·경보 카드 연락 연결)

- **탭 유지**(`9d16158`): 신청 상세에서 고른 탭을 `sessionStorage`(`tab:<경로>`)에 기억해 자동 새로고침·결정 뒤 이동에도 유지합니다. 결정 오류 재표시(`data-tab-reset`)만 서버 기본 탭을 씁니다. `window open --acr` → `ops deploy console`(`console:20261109070106`).
- **요약 탭 심사역 결정 카드**(`f833894`): 승인·거절일 때만 요약 탭 맨 위에 신청 금액·기간, 승인 금액(승인일 때), 결정 시각, 정책 예외 승인 여부를 보입니다. 같은 창에서 `ops deploy console`(`console:20261109070732`) → `ops smoke` 성공(`job-smoke-kg1k4cd`) → 라이브 `app-20261109-3a9b70` 요약 탭에 "심사역 결정 승인 · 신청 5,000,000원 / 6개월 · 승인 5,000,000원" 확인 → **`window close`**, `/`·`/loans` 200.
- **경보 카드 연락 연결**(`263d581`): 동작은 그대로(경보만으로는 연락하지 않음)이고, 미납 추심 건의 `recent_alert`로 경보와 연락을 이어 경보 카드에 "이 경보를 근거로 한 차주 연락"(회차·스크립트·상태·재조정 요청, 추심 카드 링크)과 스테퍼 `미납 추심 연락`·`재조정 결정` 단계를 보입니다. 연결이 없으면 "이 경보로 나간 차주 연락 없음". `window open --acr` → `ops deploy console`(`console:20261109073518`) → `ops smoke` 성공(`job-smoke-ref72kg`) → 라이브 `app-20261109-3a9b70` 12주차 높음 경보 카드에 "3회차 미납 추심 · routed · hardship_offer sent · 재조정 상담 요청 pending_officer", 나머지 경보 3장은 "연락 없음" 확인 → **`window close`**, `/`·`/loans` 200.

## 2026-11-10 서브넷 NSG 직접 소유

- **배경:** `vnet-amalend`에 "NSG 없는 서브넷" 보안 경고. `snet-agents`·`snet-pe`·`snet-functions`·`snet-apps-int`는 Bicep에 NSG가 없어 테넌트 정책이 붙인 빈 NSG(`vnet-amalend-snet-*-nsg-koreacentral`)에 기대고 있었고, 프로비저닝(`window open/close`)마다 연결이 풀렸다가 정책이 다시 붙였습니다.
- **변경:** `infra/modules/network.bicep`이 `nsg-agents`·`nsg-pe`·`nsg-functions`·`nsg-apps-int`(사용자 규칙 없음, Azure 기본 규칙만 → 정책 NSG와 동작 동일)를 만들고 6개 서브넷 모두에 NSG를 연결합니다. 테스트 `tests/infra/test_network.py`의 `test_every_subnet_has_owned_nsg`.
- **라이브 적용:** 전체 프로비저닝 대신 같은 내용으로 `az network nsg create`(태그 `Control`·`azd-env-name`·`project`) → `az network vnet subnet update --network-security-group`를 서브넷별로 실행했습니다. 콘솔 `/`·`/loans`·`/loans/app-20261109-3a9b70` 200, `ops smoke` 성공(`job-smoke-i320hwq`). 연결이 없어진 정책 NSG 4개는 지웠습니다. 다음 프로비저닝은 이 상태와 일치합니다.
- **콘솔 IP:** 운영자 IP가 바뀌어 WAF `allowOperatorOnly`에 새 IP 예외를 더하고 `CONSOLE_IP`를 갱신했습니다. `agw-amalend`가 `Stopped`라 시작했습니다.

## 2026-11-11 정산 인사이트·심사역 확인 사항 배포

- **변경**(`feat/settlement-checks`, 스펙 `docs/superpowers/specs/2026-11-10-settlement-insights-officer-checks-design.md`): 정산 원장 지표 3종(`pg_cash_conversion_days`·`pg_offset_rate_trend_pp`·`pg_advance_share`/`pg_advance_suspected_count`, 선정산 비중은 미입금액 가중), 심사 분석가 `officer_checks`(최대 3개, 근거 검증) + 검토자 `check_verdicts`, 정산 대사 카드 KPI 5개·지표 읽는 법·에이전트 확인 사항, 심사 도우미 스냅샷에 확인 사항, 평가 지표 `settlement_check_rate`.
- **배포:** `window open --acr` → `ops deploy orchestrator`(`func-gaz7yh2zic7zc`) → `ops deploy console`(`console:20261110233530`) → `ops smoke` 성공(`job-smoke-ccrx506`). 첫 시도는 로컬 DNS 일시 장애(패키지 피드·`management.azure.com` 조회 실패)로 실패해 재실행했습니다. 운영자 공인 IP가 통신사 대역 안에서 계속 바뀌어 창을 한 번 다시 열었습니다.
- **라이브 확인:** `agw-amalend`가 `Stopped`라 시작했습니다. 새 신청 `app-20261111-5cbd5e`(mugshop-21): 선정산 추정 14건·미입금액의 100%, 미입금 표 "선지급 추정(…)", 에이전트 확인 사항 3개·검토자 검증 3/3(R108 0.774 근거, 거래 상대방 이름 없음). `app-20261111-3cb194`(dailylook-01): 선정산 추정 0건, 현금화 일수 18.7일, 확인 사항 3/3. 이전 신청 `app-20261110-a475cc`: 현금화 일수·선정산 추정 "—"와 "2026-11-10 이전 심사분은 확인 사항이 없습니다." 안내. 심사 도우미 "정산에서 확인할 점은?" → 확인 사항·R108 근거 답변. → **`window close`**.
- **문서 테스트:** 위 NSG 항목의 `파일::테스트` 표기를 문서 경로 검사(`tests/docs`)가 읽을 수 있게 고쳤습니다.

## 2026-11-11 사후 13주 재생 현실화·조기경보 원인 분류 배포

- **변경**(`f788d4e`~`6875ca0`, 스펙 `docs/superpowers/specs/2026-11-11-postloan-replay-realism-design.md`): 재생이 고금리 차입만으로 나빠지지 않도록 셀러 4곳에 운영 원인 시나리오(dailylook-01 매출 부진·gearstore-06 정산 보류·linen-10 반품 급증·tangerine-15 비용 증가)를 두고, 계좌 흐름과 함께 PG 정산 원장·주문을 만듭니다(`/pg/flow`, 상환액 인자). 원장 모니터(`agents/ledger_monitor.py`)가 새 트리거 4개와 원인 후보를 계산하고, 조기경보 에이전트는 `get_settlement_ledger` 도구와 주원인·부원인(근거 지표 인용 필수)을 냅니다. 추심 입력(`recent_alert`)에 원인을 넘기고, 콘솔은 경보 카드에 주원인·부원인과 "주간 정산(PG 원장)" 표를 보입니다. 평가는 원인 경로 10개·`cause_accuracy`·`ew_wrong_cause`를 더했습니다.
- **배포:** `window open --acr` → `ops deploy mockapi`(`mockapi:20261111100027`) → `ops deploy orchestrator`가 두 번 연결 끊김(10054)이었습니다. 운영자 출구 IP가 바뀌어 주 사이트 규칙(SCM이 따름)에 막힌 것이라 창을 다시 열고 성공(`func-gaz7yh2zic7zc`) → `ops deploy console`(`console:20261111103408`) → `ops smoke` 성공(`job-smoke-yb3sged`) → **`window close`**. 창을 열면서 WAF `allowOperatorOnly`가 창 IP 하나로 바뀌어, 오늘 확인된 운영자 IP 3개로 다시 맞췄습니다(`/loans` 200).
- **라이브 확인**(새 재생): `app-20261108-29c02d`(dailylook-01) 경보 5건, 첫 경보 주원인 **매출 부진**·부원인 고금리 차입, 재생 완료. `app-20261109-3a9b70`(gearstore-06) 첫 경보 **정산 보류**·부원인 고금리 차입, 5주차에 추심 에이전트의 재조정 요청으로 `재조정 대기`. 새 신청 `app-20261111-5e64e9`(linen-10, 기존 건은 재조정 대기로 409) 경보 2건 **반품 급증**, 재생 완료. `app-20261110-a475cc`(tangerine-15) 경보 2건 **비용 증가**·부원인 고금리 차입, 재생 완료. 네 건 모두 "주간 정산(PG 원장)" 표가 보입니다. STABLE `app-20261111-33e2f1`(tint-17) 13/13주 경보 0건.

## 2026-11-12 조기경보 단계 안내·셀프 상환 플랜·추심 승인 배포

- **변경**(`7527e7b`~`e8e1398`, 스펙 `docs/superpowers/specs/2026-11-11-postloan-escalation-approval-design.md`): 높음 경보 1회면 잔고 부족 예상 알림(1단계)과 다음 회차 5일 전 안내, 높음 2회 연속이면 상환일 이동·분할납부 안내와 셀프 상환 플랜 링크(2단계)를 자동 발송합니다(L4). 고객이 고른 플랜은 심사역 승인 뒤에만 일정을 바꾸고 재생이 바뀐 일정으로 납부를 판정합니다. 추심 에이전트는 제안(조치·이유·근거·정책 확인·고객 문구)만 만들고 심사역이 승인·수정·반려해야 보냅니다. 콘솔 대출 화면의 제안·플랜 카드와 결정 폼, 차주 시뮬레이터 셀프 플랜, 평가 `escalation_weeks`·`stage1_paths`/`stage2_paths`. 전체 테스트 4761 통과·136 건너뜀.
- **배포:** `window open --acr` → `ops deploy orchestrator`(`func-gaz7yh2zic7zc`) → `ops deploy console`(`console:20261111225408`) → `ops smoke` 첫 시도는 콘솔 게이트 시간 초과(10060)였습니다. `agw-amalend`가 `Stopped`라 시작한 뒤(약 5분) 재실행 성공(`job-smoke-eavtmrf`) → **`window close`**. 운영자 IP가 통신사 NAT로 두 주소를 오가 WAF `allowOperatorOnly`에 /32 예외 두 개를 더했습니다(`/loans` 200).
- **라이브 실측**(원인 셀러 4곳 새 재생, 스펙 §9 "어느 셀러가 2단계에 닿는지 보고"):
  - `app-20261108-29c02d`(dailylook-01, `rp-06e2f90ae6`): 경보 5건(높음 3·중간 2). 1단계 잔고 부족 예상 알림 2026-12-29 발송, 2회차 5일 전 안내 2027-01-06. 2단계(10주차 2027-01-19)는 다음 회차 2027-02-05가 재생 끝(2027-02-09)에 가까워 이동(지급일+1영업일, 상환일+10일 이내)·분할(상환일+15일) 모두 재생 안에 들지 않아 `skipped`(`no_options`). 납부 2·3회차 미납, 추심 제안 `상환 곤란 상담 안내`(재조정 상담 포함)가 심사역 승인 대기인 채로 재생 완료 → 데모 사전 대출 B.
  - `app-20261112-39fc1b`(gearstore-06, 새 신청 5,000,000원·6개월, 기존 건은 재조정 대기로 409): 중간 경보 2건, 5주차 1회차 미납으로 추심 승인 대기에서 정지.
  - `app-20261111-5e64e9`(linen-10)·`app-20261110-a475cc`(tangerine-15): 중간 경보 2건씩, 단계 없이 재생 완료.
  - **결과:** 2단계 옵션까지 닿은 셀러는 없습니다. 스펙대로 데이터는 바꾸지 않았고, 데모의 셀프 플랜 장면(사전 대출 D)은 보류합니다.

## 2026-11-12 검토자 계절지수 전체 시계열 제공 배포

- **배경:** `app-20261112-0f7edc`(sofa-18) 에이전트 단계가 303.7초(3라운드)였습니다. 최근 신청 16건 중 1라운드 통과는 6건이고, 추가 라운드가 생긴 10건 중 9건이 계절지수 비교 주장("2월이 12개월 중 최저", "지수가 낮다")을 검토자가 비교 근거 없음으로 반려한 경우였습니다. 검토자 입력에는 인용된 달의 지수만 있고 지수 정의(월 매출 ÷ 기간 평균, 1.0 = 평균)가 없었습니다.
- **변경**(`4bfc3f3`): `_reviewer_input`이 `:index` 지표를 인용한 주장이 있으면 `seasonal_index`(정의 + 전체 월 지수)를 넘기고, 분석가 지침은 비교 시 비교한 모든 달의 `S:YYYY-MM:index`를 인용하게, 검토자 지침은 그 시계열로 판단하게 했습니다. 모델은 그대로입니다.
- **배포:** `window open --acr` → `ops deploy orchestrator`가 SCM 403("Web App - Unavailable")이었습니다. App Service가 보는 출구 IP가 `api.ipify`와 달라(같은 /16의 다른 주소) [출구 IP가 다를 때](deploy.md#app-service-출구-ip가-다를-때) 절차로 `AppServiceIPSecAuditLogs`에서 실제 IP를 찾아 임시 규칙 `operator-alt2`(priority 111)를 넣고 재실행해 성공(`func-gaz7yh2zic7zc`) → 임시 규칙·진단 설정 삭제 → `ops smoke` 성공(`job-smoke-rqujogr`) → **`window close`**. 창이 WAF `allowOperatorOnly`를 창 IP 하나로 바꿔 운영자 IP를 다시 더했습니다(`/loans` 200).
- **라이브 재측정**(새 신청 8건, 5,000,000원·6개월): 1라운드 통과 **5/8**(이전 6/16), 계절지수 반려 **0건**. 1라운드 통과 건은 43.7~68.5초, 추가 라운드 3건은 108.0~142.8초입니다. 남은 지적은 정책 판정·금리 구간 주장에 정책 근거 ID가 없는 경우 2건(bakery-20, linen-10, 3라운드에서 `P:decision`·`P:rate_band` 같은 없는 ID를 인용)과 현금화 일수만으로 상환일 정렬을 단정한 확인 사항 1건(ceramic-08)입니다. sofa-18 새 신청은 1라운드 통과(54.6초)입니다.