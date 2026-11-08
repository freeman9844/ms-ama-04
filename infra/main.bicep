// 구독 범위 진입점. 리소스 그룹을 만들고 resources.bicep을 호출합니다.
// azd는 이 파일의 출력을 .azure/<env>/.env 환경 변수로 저장합니다.
targetScope = 'subscription'

@minLength(3)
@maxLength(12)
@description('azd 환경 이름. 리소스 그룹 이름과 리소스 이름 접미사에 쓰입니다.')
param environmentName string

@description('배포 리전. Foundry Agents + Private VNet을 지원해야 합니다.')
param location string = 'koreacentral'

@description('배포 창의 운영자 공인 IPv4. 비어 있으면 모든 PaaS의 public access를 끕니다.')
param operatorIp string = ''

@description('true면 az acr build를 위해 ACR public access를 전체 허용합니다(빌드 창).')
param acrPublic bool = false

@description('운영자(배포자) Entra 객체 ID. 비어 있으면 운영자 데이터 평면 역할을 만들지 않습니다.')
param operatorPrincipalId string = ''

@description('false면 Service Bus를 배포하지 않습니다(pause 상태).')
param serviceBusEnabled bool = true

@description('false면 Foundry 프로젝트 capability host 배포를 건너뜁니다(최초 배포 이후).')
param deployCapabilityHost bool = true

@allowed([ 'GlobalStandard', 'DataZoneStandard' ])
@description('OpenAI 채팅 모델(gpt-5.4 계열·gpt-6.1-sol·gpt-5.6-sol) 배포 SKU (ADR-002).')
param chatModelSku string = 'GlobalStandard'

@allowed([ 'GlobalStandard', 'DataZoneStandard' ])
@description('Microsoft-Decision-1 배포 SKU. APAC Global Standard 지원이 문서와 CLI에서 엇갈리므로 실패 시 DataZoneStandard로 전환합니다.')
param decisionModelSku string = 'GlobalStandard'

@secure()
@description('Mock API 동의 토큰 서명 비밀(32자 이상). ops가 azd 환경에 생성합니다.')
param mockConsentSecret string = ''

@secure()
@description('가명처리 HMAC 키(32자 이상). Key Vault 비밀 pseudonym-key로 저장합니다.')
param pseudonymKey string = ''

@description('정책 엔진 이미지. 비면 자리표시 이미지(ops deploy 전).')
param policyImage string = ''

@description('Mock API 이미지. 비면 자리표시 이미지(ops deploy 전).')
param mockapiImage string = ''

@description('심사역 콘솔 이미지. 비면 자리표시 이미지(ops deploy 전).')
param consoleImage string = ''

@description('평가 Job 이미지. 비면 자리표시 이미지(ops deploy eval 전).')
param evalImage string = ''

@description('심사역 콘솔 접속 허용 IPv4(App Gateway WAF 규칙, ops window open이 설정, close 후에도 유지). 비면 콘솔 진입을 막습니다.')
param consoleIp string = ''

@description('false면 콘솔 Easy Auth를 끄는 데모 모드(azd env CONSOLE_AUTH_ENABLED=false). WAF 운영자 IP 규칙은 그대로입니다.')
param consoleAuthEnabled bool = true

@description('데모 모드의 고정 사용자 역할(azd env CONSOLE_DEMO_ROLE). Auditor면 가명 읽기 전용 데모입니다.')
@allowed([ 'CreditOfficer', 'CreditManager', 'Auditor' ])
param consoleDemoRole string = 'CreditOfficer'

@description('운영 경보 수신 이메일. 비면 Action Group 없이 포털 경보만 남깁니다.')
param opsAlertEmail string = ''

@description('true면 운영 LLM 호출을 APIM AI 게이트웨이로 보냅니다. 롤백은 false로 다시 프로비저닝.')
param aiGatewayRouting bool = true
@description('콘솔 TLS 인증서의 Key Vault 비밀 ID(azd env APPGW_CERT_SECRET_ID, ops window open이 설정). 비면 App Gateway 공용 IP만 만듭니다.')
#disable-next-line secure-secrets-in-params // 비밀 값이 아니라 Key Vault 비밀의 URI입니다.
param appGwCertSecretId string = ''

var tags = {
  'azd-env-name': environmentName
  project: 'ama-cashflow-lending'
  Control: 'Yes'
}

// capacity 단위는 1K TPM. 구독 쿼터(GlobalStandard): gpt-5.4 250, gpt-5.6-luna 250, gpt-6.1-sol 250, gpt-5.6-sol 250, gpt-6-luna 250.
// gpt-5.6-luna 240 = 소형 에이전트 전부(데이터 수집·검토·분류 폴백·추심·안내·이의 회신 합 72K) + 조기경보 조사(gw-early-warning 150K).
// 구독의 다른 리소스가 쿼터 10을 쓰고 있어 이 배포가 가질 수 있는 최대치가 240입니다(2026-11-08 확인).
// 종량제라 용량을 늘려도 고정비는 없습니다.
// gpt-5.6-sol 150 = 분석 에이전트(gw-analyst 150K). 평가 Job의 full 구성(EVAL_FULL_MODEL)도 같은 배포를 나눠 씁니다.
// 구독의 다른 리소스가 gpt-5.6-sol 쿼터 100을 쓰고 있어 이 배포가 가질 수 있는 최대치가 150입니다.
// gpt-5.4 200 = 평가 판정(EVAL_JUDGE_MODEL)·이전 분석 용량(쿼터 보유만).
// gpt-6.1-sol 50 = 보유만. 게이트웨이 연결(chat completions)에서 함수 도구 + 추론을 거부해(400) 분석가로 쓰지 않습니다.
// gpt-6-luna 100 = 콘솔 심사역 채팅(gw-officer-chat 100K) 전용. 다른 에이전트와 용량을 나누지 않습니다(2026-11-08 쿼터 0/250 확인).
// DataZoneStandard 쿼터는 100이라 100으로 낮추며, 이때는 APIM TPM보다 배포 한도(100K)가 먼저 걸립니다.
// ARM은 리소스 이름에 예약어 MICROSOFT를 허용하지 않으므로 Decision-1은 배포 이름을 따로 둡니다.
var modelDeployments = [
  { name: 'gpt-5.4', format: 'OpenAI', version: '2026-03-05', sku: chatModelSku, capacity: chatModelSku == 'GlobalStandard' ? 200 : 100 }
  { name: 'gpt-5.6-luna', format: 'OpenAI', version: '2026-07-09',   sku: chatModelSku, capacity: chatModelSku == 'GlobalStandard' ? 240 : 100 }
  { name: 'gpt-6.1-sol', format: 'OpenAI', version: '2026-09-29', sku: chatModelSku, capacity: 50 }
  { name: 'gpt-5.6-sol', format: 'OpenAI', version: '2026-07-09', sku: chatModelSku, capacity: chatModelSku == 'GlobalStandard' ? 150 : 100 }
  { name: 'gpt-6-luna', format: 'OpenAI', version: '2026-09-22', sku: chatModelSku, capacity: 100 }
  { name: 'Microsoft-Decision-1', deploymentName: 'decision-1', format: 'Microsoft', version: '1', sku: decisionModelSku, capacity: 50 }
]

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-${environmentName}'
  location: location
  tags: tags
}

module resources 'resources.bicep' = {
  name: 'resources-${environmentName}'
  scope: rg
  params: {
    environmentName: environmentName
    location: location
    tags: tags
    operatorIp: operatorIp
    acrPublic: acrPublic
    operatorPrincipalId: operatorPrincipalId
    serviceBusEnabled: serviceBusEnabled
    deployCapabilityHost: deployCapabilityHost
    modelDeployments: modelDeployments
    mockConsentSecret: mockConsentSecret
    pseudonymKey: pseudonymKey
    policyImage: policyImage
    mockapiImage: mockapiImage
    consoleImage: consoleImage
    evalImage: evalImage
    consoleIp: consoleIp
    consoleAuthEnabled: consoleAuthEnabled
    consoleDemoRole: consoleDemoRole
    opsAlertEmail: opsAlertEmail
    aiGatewayRouting: aiGatewayRouting
    appGwCertSecretId: appGwCertSecretId
  }
}

output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_LOCATION string = location
output VNET_NAME string = resources.outputs.VNET_NAME
output KEYVAULT_NAME string = resources.outputs.KEYVAULT_NAME
output KEYVAULT_URI string = resources.outputs.KEYVAULT_URI
output LOG_ANALYTICS_CUSTOMER_ID string = resources.outputs.LOG_ANALYTICS_CUSTOMER_ID
output APPLICATIONINSIGHTS_CONNECTION_STRING string = resources.outputs.APPLICATIONINSIGHTS_CONNECTION_STRING
output ORCHESTRATOR_IDENTITY_ID string = resources.outputs.ORCHESTRATOR_IDENTITY_ID
output ORCHESTRATOR_IDENTITY_CLIENT_ID string = resources.outputs.ORCHESTRATOR_IDENTITY_CLIENT_ID
output APPS_IDENTITY_ID string = resources.outputs.APPS_IDENTITY_ID
output APPS_IDENTITY_CLIENT_ID string = resources.outputs.APPS_IDENTITY_CLIENT_ID
output DATA_STORAGE_NAME string = resources.outputs.DATA_STORAGE_NAME
output FUNC_STORAGE_NAME string = resources.outputs.FUNC_STORAGE_NAME
output COSMOS_ENDPOINT string = resources.outputs.COSMOS_ENDPOINT
output COSMOS_DATABASE string = resources.outputs.COSMOS_DATABASE
output SEARCH_NAME string = resources.outputs.SEARCH_NAME

output AI_ACCOUNT_NAME string = resources.outputs.AI_ACCOUNT_NAME
output AI_PROJECT_NAME string = resources.outputs.AI_PROJECT_NAME
output AI_PROJECT_ENDPOINT string = resources.outputs.AI_PROJECT_ENDPOINT
output AI_OPENAI_ENDPOINT string = resources.outputs.AI_OPENAI_ENDPOINT
output FOUNDRY_PROJECT_CAPHOST string = resources.outputs.FOUNDRY_PROJECT_CAPHOST

output SERVICEBUS_NAME string = resources.outputs.SERVICEBUS_NAME
output SERVICEBUS_FQDN string = resources.outputs.SERVICEBUS_FQDN

output AZURE_CONTAINER_REGISTRY_ENDPOINT string = resources.outputs.AZURE_CONTAINER_REGISTRY_ENDPOINT
output ACR_NAME string = resources.outputs.ACR_NAME
output CONTAINER_APPS_INT_ENVIRONMENT_ID string = resources.outputs.CONTAINER_APPS_INT_ENVIRONMENT_ID
output CONTAINER_APPS_INT_ENVIRONMENT_NAME string = resources.outputs.CONTAINER_APPS_INT_ENVIRONMENT_NAME
output CONTAINER_APPS_INT_DOMAIN string = resources.outputs.CONTAINER_APPS_INT_DOMAIN

output POLICY_APP_NAME string = resources.outputs.POLICY_APP_NAME
output MOCKAPI_APP_NAME string = resources.outputs.MOCKAPI_APP_NAME
output POLICY_URL string = resources.outputs.POLICY_URL
output MOCKAPI_URL string = resources.outputs.MOCKAPI_URL
output FUNCTION_APP_NAME string = resources.outputs.FUNCTION_APP_NAME

output SMOKE_JOB_NAME string = resources.outputs.SMOKE_JOB_NAME
output EVAL_JOB_NAME string = resources.outputs.EVAL_JOB_NAME
output OPS_JOB_NAME string = resources.outputs.OPS_JOB_NAME
output APPLICATIONINSIGHTS_RESOURCE_ID string = resources.outputs.APPLICATIONINSIGHTS_RESOURCE_ID
output EVAL_REPORTS_CONTAINER string = resources.outputs.EVAL_REPORTS_CONTAINER

output CONSOLE_APP_NAME string = resources.outputs.CONSOLE_APP_NAME
output CONSOLE_URL string = resources.outputs.CONSOLE_URL
output CONSOLE_CLIENT_ID string = resources.outputs.CONSOLE_CLIENT_ID
output APPGW_NAME string = resources.outputs.APPGW_NAME
output APPGW_FQDN string = resources.outputs.APPGW_FQDN
