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
| `python -m ops deploy [policy\|mockapi\|orchestrator\|all]` | 앱 배포(P3): 정책·Mock 이미지 ACR Build → 프로비저닝, Function zip. `window open --acr` 필요 |
| `python -m ops smoke` | VNet 안 smoke Job 실행 |
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
   에이전트(`txn-classifier`)가 보완합니다. 결과는 Cosmos `lending/classifications`에 캐시됩니다(토큰이 든 적요 제외).
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

### 문제 대응(P4a)

- `decision`이 401/403: Function·smoke 관리 ID에 Foundry 계정 `Cognitive Services User` 역할이 필요할 수 있습니다
  (운영자 계정은 `Azure AI User`로 동작 확인). 역할을 추가하고 5분 뒤 다시 실행합니다.
- `journey`가 "심사 에이전트 결과 없음": 결과의 `flags`에 `assessment_unavailable`이 있으면 App Insights `traces`에서
  `assess_credit` 오류를 찾습니다. 근거 검사·검토를 3라운드 안에 통과하지 못하면 `assessment`는 남지만
  `review.passed=false`이고 `review_not_passed` 플래그가 붙습니다(smoke는 통과, 출력에 "not passed").
- 에이전트 정의를 바꾸면 Foundry에 새 버전이 생깁니다(같은 정의는 같은 버전).

### 알려진 제약(P4a)

- `assessment` 문장은 가명 토큰(`[[PERSON_…]]`)을 그대로 담습니다. 복원 표시는 심사역 콘솔(P4b)이 합니다.
- 데모 데이터에서 규칙 미분류 적요는 공급업체 송금뿐이라 의미 분류가 특성을 바꾸지 않습니다. 보수적 반영은 단위 테스트로 검증합니다.
- 수치 근거 검사는 숫자 일치만 봅니다. 해석의 타당성은 검토 에이전트 판정에 의존합니다.
- `demo-stress-003`은 고금리 대출이 감지되어도 정책상 eligible입니다(P3 규칙). 분석가는 이를 claim·counterfactual로 드러냅니다.
