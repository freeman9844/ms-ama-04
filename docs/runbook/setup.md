# 환경 준비 (setup)

처음 저장소를 받아 로컬 개발 환경과 Azure 환경을 갖추는 사람을 위한 안내입니다. 로컬 개발·준비·최초 배포·데모 모드 절은 README 원문을 그대로 옮겼습니다.

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

심사 콘솔 화면만 Azure 없이 확인할 때(합성 데이터, 결정·재생 버튼은 메모리에 기록만 함):

```powershell
.\.venv\Scripts\python -m consolepreview          # http://127.0.0.1:8765/
```

컨테이너 이미지는 로컬 Docker 없이 ACR Build로 만든다(P3).

## Azure 준비

- Azure CLI 2.91+, Azure Developer CLI 1.37+, PowerShell 7, Python 3.12 가상환경(`.venv`)
- `az login` 후 `azd config set auth.useAzCliAuth true`
- `az extension add -n containerapp`
- `az provider register -n Microsoft.App --wait`, `az provider register -n Microsoft.CognitiveServices --wait`
- 오프라인 IaC 테스트: `Install-Module PSRule.Rules.Azure -Scope CurrentUser` 후
  `$env:RUN_INFRA_TESTS = '1'; .\.venv\Scripts\python -m pytest tests/infra -q`

## 최초 배포

```powershell
azd env new amalend --location koreacentral --subscription <구독 ID>   # 운영 도구는 이 구독(AZURE_SUBSCRIPTION_ID)으로만 az를 호출
azd env set AZURE_PRINCIPAL_ID (az ad signed-in-user show --query id -o tsv)
azd env set OPERATOR_IP closed
azd provision --preview
azd provision --no-prompt          # 실측: 성공 실행 7.6분(최초 실패 2회 포함 누적 약 28분)
azd env set DEPLOY_CAPHOST false   # 이후 재프로비저닝에서 capability host 재생성 방지
python -m ops window open          # 최초에는 게이트웨이 인증서 생성 + 게이트웨이 프로비저닝(아래 "배포 뒤 확인")
python -m ops smoke                # Private Endpoint 경로 검증(실측 약 1.2분, 21개 점검, P2) + 콘솔 관문
```

최초 `azd provision`에는 App Gateway가 없습니다(인증서 비밀 ID `APPGW_CERT_SECRET_ID`가 없으면 게이트웨이를 만들지 않음).
smoke의 콘솔 관문은 게이트웨이가 있어야 하므로 smoke는 반드시 첫 `window open` 뒤에 실행합니다. 그 전에 실행하면 `window open`을 먼저 하라는 오류가 납니다.

이후 재프로비저닝(capability host 제외)은 약 3.5분, smoke Job 스크립트(`src/ops/smoke_checks.py`)는
Bicep `loadTextContent`로 Job에 들어가므로 수정 후에는 `azd provision`이 필요합니다.

문제 대응:

- Decision-1 배포(`decision-1`, 모델 `Microsoft-Decision-1`)가 SKU/지역 오류로 실패하면 `azd env set DECISION_MODEL_SKU DataZoneStandard` 후 다시 프로비저닝합니다([ADR-002](../adr/ADR-002-model-deployment.md) 전환 조건). 배포 이름에는 예약어 `Microsoft`를 쓸 수 없어 모델 이름과 다르게 둡니다.
- 재시도 전에 capability host가 이미 만들어졌는지 확인합니다. `Succeeded`면 `azd env set DEPLOY_CAPHOST false`:
  ```powershell
  $sub = azd env get-value AZURE_SUBSCRIPTION_ID; $acct = azd env get-value AI_ACCOUNT_NAME
  az rest --method get --url "https://management.azure.com/subscriptions/$sub/resourceGroups/rg-amalend/providers/Microsoft.CognitiveServices/accounts/$acct/projects/proj-lending/capabilityHosts/caphost-lending?api-version=2025-04-01-preview" --query properties.provisioningState -o tsv
  ```
- 쿼터 오류: `az cognitiveservices usage list -l koreacentral -o table`로 확인하고 `infra/main.bicep`의 `capacity`를 낮춥니다.

## 배포 뒤 확인

`python -m ops smoke`는 두 가지를 확인합니다(`src/ops/cli.py`의 `smoke` 명령).

1. VNet 안에서 smoke Job을 실행해 Private Endpoint 경로·모델·데이터 서비스·정책/Mock 상태·신청 여정까지 점검합니다. 점검 항목 이름은 [operate](operate.md#smoke)에 있습니다. 점검 개수는 단계마다 늘어 최초 배포(P2)는 21개, 이후 24개(P5b)였습니다.
2. 운영자 PC에서 콘솔 관문을 확인합니다(`src/ops/console_gate.py`). 게이트웨이 인증서가 자체 서명이라 TLS 검증은 건너뜁니다. 로그인 없이 들어가면 Entra 로그인으로 리디렉션되거나(데모 모드에서는 200), 403이면 WAF 운영자 IP 규칙이 막은 것입니다. 인증서 비밀 ID(`APPGW_CERT_SECRET_ID`)가 없으면 `window open`을 먼저 실행하라는 오류가 납니다.

콘솔 접속 주소는 App Gateway 주소입니다. azd 환경 값 `APPGW_FQDN`(또는 `CONSOLE_URL`, `https://<APPGW_FQDN>`)을 씁니다.

```powershell
azd env get-value APPGW_FQDN
azd env get-value CONSOLE_URL
```

- 게이트웨이는 자체 서명 인증서를 쓰므로 브라우저에 인증서 경고가 뜹니다. 데모에서는 "고급"에서 계속 진행합니다(운영 전환 시 공인 CA 인증서로 교체, [ADR-011](../adr/ADR-011-appgw-waf-entry.md)).
- 첫 `python -m ops window open`은 프로비저닝 → 인증서 생성·Key Vault 저장(`src/ops/appgw_cert.py`) → 두 번째 프로비저닝으로 게이트웨이를 만들므로 더 오래 걸립니다. 인증서는 없거나 만료 30일 전이면 `window open`이 새로 씁니다.
- WAF는 azd 환경의 `CONSOLE_IP`(`<운영자 공인 IP>/32`)가 아닌 요청을 403으로 막습니다. `python -m ops window open`이 `CONSOLE_IP`를 현재 공인 IP로 설정하고, `window close`는 이 값을 바꾸지 않습니다(`src/ops/window.py`).
- 공인 IP가 바뀌면 [deploy](deploy.md#운영자-ip가-바뀔-때)를 따릅니다.
- 첫 배포 직후 smoke에서 걸리는 문제는 [troubleshooting](troubleshooting.md)을 봅니다.

## 데모 모드(Entra 로그인 끔)

테넌트 정책 때문에 Entra 로그인을 쓸 수 없을 때(예: 사용자 동의가 막혀 "관리자 승인 필요")의 데모 전용 스위치입니다.
Easy Auth를 끄고, 콘솔은 모든 요청을 고정 데모 사용자(`CONSOLE_DEMO_ROLE`, 기본 `CreditOfficer`)로 처리합니다.
진입 통제는 WAF 운영자 IP 규칙(`CONSOLE_IP`/32)만 남습니다. 화면 상단에 "데모 모드" 배너가 보입니다.

```powershell
azd env set CONSOLE_AUTH_ENABLED false     # 다시 켜려면 true
azd env set CONSOLE_DEMO_ROLE Auditor      # 선택: 감사자(가명 읽기 전용) 데모. 기본 CreditOfficer
python -m ops window open --acr            # 프로비저닝: authConfigs platform.enabled=false, CONSOLE_AUTH_MODE=demo
python -m ops deploy console               # 데모 모드를 아는 콘솔 이미지
python -m ops window close
python -m ops smoke                        # 콘솔 관문: 운영자 IP에서 200(데모 모드)
```

증분 배포는 리소스를 지우지 않으므로 Easy Auth 설정(`authConfigs`)은 남기고 `platform.enabled`만 끕니다. Entra 앱 등록도 그대로입니다.

단계별 원문은 [deploy-history](deploy-history.md)에 있습니다.
