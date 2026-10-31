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
@description('gpt-5.4 계열 배포 SKU (ADR-002).')
param chatModelSku string = 'GlobalStandard'

@allowed([ 'GlobalStandard', 'DataZoneStandard' ])
@description('Microsoft-Decision-1 배포 SKU. APAC Global Standard 지원이 문서와 CLI에서 엇갈리므로 실패 시 DataZoneStandard로 전환합니다.')
param decisionModelSku string = 'GlobalStandard'

var tags = {
  'azd-env-name': environmentName
  project: 'ama-cashflow-lending'
  Control: 'Yes'
}

// capacity 단위는 1K TPM. 구독 쿼터(GlobalStandard): gpt-5.4 250, gpt-5.4-mini 1000.
var modelDeployments = [
  { name: 'gpt-5.4', format: 'OpenAI', version: '2026-03-05', sku: chatModelSku, capacity: 50 }
  { name: 'gpt-5.4-mini', format: 'OpenAI', version: '2026-03-17', sku: chatModelSku, capacity: 100 }
  { name: 'Microsoft-Decision-1', format: 'Microsoft', version: '1', sku: decisionModelSku, capacity: 50 }
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
