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
