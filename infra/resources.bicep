// 리소스 그룹 범위의 전체 인프라. main.bicep이 호출합니다.
// 섹션은 P2 계획의 태스크 순서대로 추가됩니다(Bicep 선언 순서는 배포 순서와 무관).

param environmentName string
param location string
param tags object
param operatorIp string
param acrPublic bool
param operatorPrincipalId string
param serviceBusEnabled bool
param deployCapabilityHost bool
param modelDeployments array
@secure()
param mockConsentSecret string
@secure()
param pseudonymKey string
param policyImage string
param mockapiImage string
param consoleImage string
@description('평가 Job 이미지. 비면 자리표시 이미지(ops deploy eval 전).')
param evalImage string
@description('콘솔에 접속할 운영자 공인 IPv4. 비면 TEST-NET 주소만 허용해 사실상 막습니다(fail-closed).')
param consoleIp string
@description('false면 콘솔 Easy Auth를 끄고 앱이 고정 데모 주체로 동작합니다(IP 허용 목록만 남음, 데모 전용).')
param consoleAuthEnabled bool
@description('데모 모드의 고정 사용자 역할(CreditOfficer·CreditManager·Auditor).')
param consoleDemoRole string
@description('운영 경보 수신 이메일. 비면 Action Group 없이 포털 경보만 남깁니다.')
param opsAlertEmail string = ''
@description('true면 운영 LLM 호출을 APIM AI 게이트웨이로 보냅니다(AI_GATEWAY=apim). false면 직접 호출(롤백).')
param aiGatewayRouting bool

var sfx = uniqueString(subscription().id, resourceGroup().id, environmentName)

var names = {
  vnet: 'vnet-${environmentName}'
  law: 'log-${environmentName}'
  appi: 'appi-${environmentName}'
  kv: 'kv${sfx}'
  stAgents: 'stagt${sfx}'
  stData: 'stdat${sfx}'
  stFunc: 'stfn${sfx}'
  cosmos: 'cosmos-${sfx}'
  search: 'srch-${sfx}'
  ais: 'ais-${sfx}'
  project: 'proj-lending'
  projectCapHost: 'caphost-lending'
  sb: 'sb-${sfx}'
  acr: 'acr${sfx}'
  caeInt: 'cae-int-${environmentName}'
  caeWeb: 'cae-web-${environmentName}'
  plan: 'plan-${environmentName}'
  func: 'func-${sfx}'
  idOrchestrator: 'id-orch-${environmentName}'
  idApps: 'id-apps-${environmentName}'
  idSmoke: 'id-smoke-${environmentName}'
  idConsole: 'id-console-${environmentName}'
  idEval: 'id-eval-${environmentName}'
  idOps: 'id-ops-${environmentName}'
  smokeJob: 'job-smoke'
  evalJob: 'job-eval'
  opsJob: 'job-ops'
  caPolicy: 'ca-policy'
  caMockapi: 'ca-mockapi'
  caConsole: 'ca-console'
  consoleApp: 'amalend-console-${environmentName}-${sfx}'
  apim: 'apim-${sfx}'
}

var storageSuffix = environment().suffixes.storage
var blobZone = 'privatelink.blob.${storageSuffix}'
var queueZone = 'privatelink.queue.${storageSuffix}'
var tableZone = 'privatelink.table.${storageSuffix}'
var dnsZoneNames = [
  'privatelink.services.ai.azure.com'
  'privatelink.openai.azure.com'
  'privatelink.cognitiveservices.azure.com'
  'privatelink.search.windows.net'
  blobZone
  queueZone
  tableZone
  'privatelink.documents.azure.com'
  'privatelink.vaultcore.azure.net'
  'privatelink.servicebus.windows.net'
  'privatelink.azurecr.io'
  'privatelink.azurewebsites.net'
  'azure-api.net'
]

// 기본 제공 역할 정의 GUID (az role definition list로 확인, 2026-10-31)
// kvValueReader = Key Vault Secrets User, kvValueAdmin = Key Vault Secrets Officer
var role = {
  blobOwner: 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b'
  blobContributor: 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
  blobReader: '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
  queueContributor: '974c5e8b-45b9-4653-ba55-5f855dd0fb88'
  tableContributor: '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'
  kvValueReader: '4633458b-17de-408a-b874-0445c86b69e6'
  kvValueAdmin: 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'
  foundryUser: '53ca6127-db72-4b80-b1b0-d745d6d5456d'
  openAiUser: '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
  sbSender: '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39'
  sbReceiver: '4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0'
  acrPull: '7f951dda-4ed3-4680-a7ca-43fe172d538d'
  acrPush: '8311e382-0749-4cb8-b61a-304f252e45ec'
  metricsPublisher: '3913510d-42f4-4e42-8a64-420c390055eb'
  queueSender: 'c6a89b2d-59bc-44d0-9896-0f6e12d7b80a'
}
var sp = 'ServicePrincipal'
var hasOperator = !empty(operatorPrincipalId)
var evalReportsContainer = 'eval-reports'

// ── 네트워크와 Private DNS (T1) ─────────────────────────────────────────
module network 'modules/network.bicep' = {
  name: 'network'
  params: {
    name: names.vnet
    location: location
    tags: tags
  }
}

module dns 'modules/dns.bicep' = {
  name: 'dns'
  params: {
    zoneNames: dnsZoneNames
    vnetId: network.outputs.vnetId
    tags: tags
  }
}

var zoneId = toObject(dns.outputs.zones, z => z.name, z => z.id)
var subnetId = network.outputs.subnetIds

output VNET_NAME string = network.outputs.vnetName

// ── 모니터링, 관리 ID, Key Vault (T2) ───────────────────────────────────
module identities 'modules/identities.bicep' = {
  name: 'identities'
  params: {
    names: {
      orchestrator: names.idOrchestrator
      apps: names.idApps
      smoke: names.idSmoke
      console: names.idConsole
      eval: names.idEval
      ops: names.idOps
    }
    location: location
    tags: tags
  }
}

module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring'
  params: {
    lawName: names.law
    appiName: names.appi
    location: location
    tags: tags
    roleAssignments: [
      { principalId: identities.outputs.orchestratorPrincipalId, roleId: role.metricsPublisher, principalType: sp }
      { principalId: identities.outputs.appsPrincipalId, roleId: role.metricsPublisher, principalType: sp }
    ]
  }
}

module keyVault 'modules/keyvault.bicep' = {
  name: 'keyvault'
  params: {
    name: names.kv
    location: location
    tags: tags
    peSubnetId: subnetId['snet-pe']
    zoneId: zoneId['privatelink.vaultcore.azure.net']
    secrets: { 'pseudonym-key': pseudonymKey }
    roleAssignments: concat([
      { principalId: identities.outputs.orchestratorPrincipalId, roleId: role.kvValueReader, principalType: sp }
      { principalId: identities.outputs.appsPrincipalId, roleId: role.kvValueReader, principalType: sp }
      { principalId: identities.outputs.smokePrincipalId, roleId: role.kvValueReader, principalType: sp }
      { principalId: identities.outputs.consolePrincipalId, roleId: role.kvValueReader, principalType: sp }
    ], hasOperator ? [
      { principalId: operatorPrincipalId, roleId: role.kvValueAdmin, principalType: 'User' }
    ] : [])
  }
}

output KEYVAULT_NAME string = keyVault.outputs.name
output KEYVAULT_URI string = keyVault.outputs.uri
output LOG_ANALYTICS_CUSTOMER_ID string = monitoring.outputs.lawCustomerId
output APPLICATIONINSIGHTS_CONNECTION_STRING string = monitoring.outputs.appiConnectionString
output APPLICATIONINSIGHTS_RESOURCE_ID string = monitoring.outputs.appiId
output ORCHESTRATOR_IDENTITY_ID string = identities.outputs.orchestratorId
output ORCHESTRATOR_IDENTITY_CLIENT_ID string = identities.outputs.orchestratorClientId
output APPS_IDENTITY_ID string = identities.outputs.appsId
output APPS_IDENTITY_CLIENT_ID string = identities.outputs.appsClientId

// ── 데이터 저장소 (T3) ───────────────────────────────────────────────────
module stAgents 'modules/storage.bicep' = {
  name: 'storage-agents'
  params: {
    name: names.stAgents
    location: location
    tags: tags
    bypass: 'AzureServices'
    peSubnetId: subnetId['snet-pe']
    endpoints: [ { groupId: 'blob', zoneId: zoneId[blobZone] } ]
  }
}

module stData 'modules/storage.bicep' = {
  name: 'storage-data'
  params: {
    name: names.stData
    location: location
    tags: tags
    peSubnetId: subnetId['snet-pe']
    endpoints: [ { groupId: 'blob', zoneId: zoneId[blobZone] } ]
    softDeleteDays: 0
    containers: [ evalReportsContainer ]
    roleAssignments: concat([
      { principalId: identities.outputs.orchestratorPrincipalId, roleId: role.blobContributor, principalType: sp }
      { principalId: identities.outputs.appsPrincipalId, roleId: role.blobContributor, principalType: sp }
      { principalId: identities.outputs.smokePrincipalId, roleId: role.blobReader, principalType: sp }
      { principalId: identities.outputs.consolePrincipalId, roleId: role.blobReader, principalType: sp }
    ], hasOperator ? [
      { principalId: operatorPrincipalId, roleId: role.blobContributor, principalType: 'User' }
    ] : [])
  }
}

module stFunc 'modules/storage.bicep' = {
  name: 'storage-functions'
  params: {
    name: names.stFunc
    location: location
    tags: tags
    peSubnetId: subnetId['snet-pe']
    endpoints: [
      { groupId: 'blob', zoneId: zoneId[blobZone] }
      { groupId: 'queue', zoneId: zoneId[queueZone] }
      { groupId: 'table', zoneId: zoneId[tableZone] }
    ]
    containers: [ 'deploymentpackage' ]
    queues: [
      'applications-start', 'officer-decisions', 'monitoring-replay', 'restructure-decisions'
      'consent-lifecycle', 'consent-commands', 'grievances', 'grievance-decisions'
    ]
    roleAssignments: [
      { principalId: identities.outputs.orchestratorPrincipalId, roleId: role.blobOwner, principalType: sp }
      { principalId: identities.outputs.orchestratorPrincipalId, roleId: role.queueContributor, principalType: sp }
      { principalId: identities.outputs.orchestratorPrincipalId, roleId: role.tableContributor, principalType: sp }
      { principalId: identities.outputs.smokePrincipalId, roleId: role.queueSender, principalType: sp }
      { principalId: identities.outputs.consolePrincipalId, roleId: role.queueSender, principalType: sp }
      { principalId: identities.outputs.opsPrincipalId, roleId: role.queueSender, principalType: sp }
    ]
  }
}

module cosmos 'modules/cosmos.bicep' = {
  name: 'cosmos'
  params: {
    name: names.cosmos
    location: location
    tags: tags
    peSubnetId: subnetId['snet-pe']
    zoneId: zoneId['privatelink.documents.azure.com']
    containers: [
      { name: 'applications', partitionKey: '/application_id' }
      { name: 'consents', partitionKey: '/consent_id' }
      { name: 'classifications', partitionKey: '/id' }
      { name: 'loans', partitionKey: '/loan_id' }
      { name: 'alerts', partitionKey: '/loan_id' }
      { name: 'deletion_proofs', partitionKey: '/consent_id' }
      { name: 'grievances', partitionKey: '/application_id' }
    ]
    sqlRoleAssignments: concat([
      { principalId: identities.outputs.orchestratorPrincipalId, roleDefinitionGuid: '00000000-0000-0000-0000-000000000002', scope: 'database' }
      { principalId: identities.outputs.appsPrincipalId, roleDefinitionGuid: '00000000-0000-0000-0000-000000000002', scope: 'database' }
      { principalId: identities.outputs.smokePrincipalId, roleDefinitionGuid: '00000000-0000-0000-0000-000000000001', scope: 'account' }
      { principalId: identities.outputs.consolePrincipalId, roleDefinitionGuid: '00000000-0000-0000-0000-000000000001', scope: 'database' }
      { principalId: identities.outputs.opsPrincipalId, roleDefinitionGuid: '00000000-0000-0000-0000-000000000001', scope: 'database' }
    ], hasOperator ? [
      { principalId: operatorPrincipalId, roleDefinitionGuid: '00000000-0000-0000-0000-000000000002', scope: 'account' }
    ] : [])
  }
}

module search 'modules/search.bicep' = {
  name: 'search'
  params: {
    name: names.search
    location: location
    tags: tags
    peSubnetId: subnetId['snet-pe']
    zoneId: zoneId['privatelink.search.windows.net']
  }
}

output DATA_STORAGE_NAME string = stData.outputs.name
output FUNC_STORAGE_NAME string = stFunc.outputs.name
output COSMOS_ENDPOINT string = cosmos.outputs.endpoint
output COSMOS_DATABASE string = cosmos.outputs.databaseName
output SEARCH_NAME string = search.outputs.name

// ── Foundry (T4) ────────────────────────────────────────────────────────
module aiAccount 'modules/foundry/account.bicep' = {
  name: 'foundry-account'
  params: {
    name: names.ais
    location: location
    tags: tags
    operatorIp: operatorIp
    agentSubnetId: subnetId['snet-agents']
    peSubnetId: subnetId['snet-pe']
    zoneIds: [
      zoneId['privatelink.services.ai.azure.com']
      zoneId['privatelink.openai.azure.com']
      zoneId['privatelink.cognitiveservices.azure.com']
    ]
    modelDeployments: modelDeployments
    roleAssignments: concat([
      { principalId: identities.outputs.orchestratorPrincipalId, roleId: role.foundryUser, principalType: sp }
      { principalId: identities.outputs.orchestratorPrincipalId, roleId: role.openAiUser, principalType: sp }
      { principalId: identities.outputs.smokePrincipalId, roleId: role.foundryUser, principalType: sp }
      { principalId: identities.outputs.smokePrincipalId, roleId: role.openAiUser, principalType: sp }
      { principalId: identities.outputs.evalPrincipalId, roleId: role.foundryUser, principalType: sp }
      { principalId: identities.outputs.evalPrincipalId, roleId: role.openAiUser, principalType: sp }
    ], hasOperator ? [
      { principalId: operatorPrincipalId, roleId: role.foundryUser, principalType: 'User' }
      { principalId: operatorPrincipalId, roleId: role.openAiUser, principalType: 'User' }
    ] : [])
  }
}

// cosmos/stAgents/search 출력을 쓰므로 각 모듈(PE 포함)이 끝난 뒤 프로젝트가 만들어집니다.
module aiProject 'modules/foundry/project.bicep' = {
  name: 'foundry-project'
  params: {
    accountName: aiAccount.outputs.name
    name: names.project
    location: location
    tags: tags
    cosmosName: cosmos.outputs.name
    storageName: stAgents.outputs.name
    searchName: search.outputs.name
  }
}

module aiRolesPre 'modules/foundry/roles-pre.bicep' = {
  name: 'foundry-roles-pre'
  params: {
    projectPrincipalId: aiProject.outputs.principalId
    cosmosName: cosmos.outputs.name
    storageName: stAgents.outputs.name
    searchName: search.outputs.name
  }
}

module aiCapHost 'modules/foundry/caphost.bicep' = if (deployCapabilityHost) {
  name: 'foundry-caphost'
  params: {
    accountName: aiAccount.outputs.name
    projectName: aiProject.outputs.name
    name: names.projectCapHost
    cosmosConnection: aiProject.outputs.cosmosConnection
    storageConnection: aiProject.outputs.storageConnection
    searchConnection: aiProject.outputs.searchConnection
  }
  dependsOn: [ aiRolesPre ]
}

module aiRolesPost 'modules/foundry/roles-post.bicep' = if (deployCapabilityHost) {
  name: 'foundry-roles-post'
  params: {
    projectPrincipalId: aiProject.outputs.principalId
    workspaceId: aiProject.outputs.workspaceId
    cosmosName: cosmos.outputs.name
    storageName: stAgents.outputs.name
  }
  dependsOn: [ aiCapHost ]
}

output AI_ACCOUNT_NAME string = aiAccount.outputs.name
output AI_PROJECT_NAME string = aiProject.outputs.name
output AI_PROJECT_ENDPOINT string = 'https://${names.ais}.services.ai.azure.com/api/projects/${names.project}'
output AI_OPENAI_ENDPOINT string = 'https://${names.ais}.openai.azure.com/'
output FOUNDRY_PROJECT_CAPHOST string = names.projectCapHost

// ── Service Bus (T5) ────────────────────────────────────────────────────
module serviceBus 'modules/servicebus.bicep' = if (serviceBusEnabled) {
  name: 'servicebus'
  params: {
    name: names.sb
    location: location
    tags: tags
    peSubnetId: subnetId['snet-pe']
    zoneId: zoneId['privatelink.servicebus.windows.net']
    topics: [ 'consent', 'flow.updated', 'alert.raised', 'grievance' ]
    // 대출별 세션으로 주차 순서를 지키고, subject(label) 필터로 스모크 메시지를 거릅니다.
    subscriptions: [
      { topic: 'flow.updated', name: 'early-warning' }
      { topic: 'alert.raised', name: 'collections' }
    ]
    roleAssignments: [
      { principalId: identities.outputs.orchestratorPrincipalId, roleId: role.sbSender, principalType: sp }
      { principalId: identities.outputs.orchestratorPrincipalId, roleId: role.sbReceiver, principalType: sp }
      { principalId: identities.outputs.appsPrincipalId, roleId: role.sbSender, principalType: sp }
      { principalId: identities.outputs.smokePrincipalId, roleId: role.sbSender, principalType: sp }
    ]
  }
}

// 조건부 모듈의 출력을 참조하지 않도록 이름으로 계산합니다.
output SERVICEBUS_NAME string = names.sb
output SERVICEBUS_FQDN string = serviceBusEnabled ? '${names.sb}.servicebus.windows.net' : ''

// ── ACR, Container Apps 환경 (T6) ──────────────────────────────────────
module acr 'modules/acr.bicep' = {
  name: 'acr'
  params: {
    name: names.acr
    location: location
    tags: tags
    publicAccess: acrPublic
    peSubnetId: subnetId['snet-pe']
    zoneId: zoneId['privatelink.azurecr.io']
    roleAssignments: concat([
      { principalId: identities.outputs.appsPrincipalId, roleId: role.acrPull, principalType: sp }
      { principalId: identities.outputs.consolePrincipalId, roleId: role.acrPull, principalType: sp }
      { principalId: identities.outputs.evalPrincipalId, roleId: role.acrPull, principalType: sp }
      { principalId: identities.outputs.opsPrincipalId, roleId: role.acrPull, principalType: sp }
    ], hasOperator ? [
      { principalId: operatorPrincipalId, roleId: role.acrPush, principalType: 'User' }
    ] : [])
  }
}

module acaInt 'modules/aca-env.bicep' = {
  name: 'aca-int'
  params: {
    name: names.caeInt
    location: location
    tags: tags
    subnetId: subnetId['snet-apps-int']
    internal: true
    lawName: monitoring.outputs.lawName
  }
}

module acaIntDns 'modules/aca-dns.bicep' = {
  name: 'aca-int-dns'
  params: {
    domain: acaInt.outputs.defaultDomain
    staticIp: acaInt.outputs.staticIp
    vnetId: network.outputs.vnetId
    tags: tags
  }
}

module acaWeb 'modules/aca-env.bicep' = {
  name: 'aca-web'
  params: {
    name: names.caeWeb
    location: location
    tags: tags
    subnetId: subnetId['snet-apps-web']
    internal: false
    lawName: monitoring.outputs.lawName
  }
}

output AZURE_CONTAINER_REGISTRY_ENDPOINT string = acr.outputs.loginServer
output ACR_NAME string = acr.outputs.name
output CONTAINER_APPS_INT_ENVIRONMENT_ID string = acaInt.outputs.id
output CONTAINER_APPS_INT_ENVIRONMENT_NAME string = acaInt.outputs.name
output CONTAINER_APPS_INT_DOMAIN string = acaInt.outputs.defaultDomain
output CONTAINER_APPS_WEB_ENVIRONMENT_ID string = acaWeb.outputs.id
output CONTAINER_APPS_WEB_ENVIRONMENT_NAME string = acaWeb.outputs.name

// ── 앱 런타임: 정책 엔진·Mock API (P3) ──────────────────────────────────
var acaIntDomain = acaInt.outputs.defaultDomain
var policyUrl = 'https://${names.caPolicy}.${acaIntDomain}'
var mockapiUrl = 'https://${names.caMockapi}.${acaIntDomain}'

module caPolicy 'modules/container-app.bicep' = {
  name: 'ca-policy'
  params: {
    name: names.caPolicy
    location: location
    tags: tags
    serviceName: 'policy'
    environmentId: acaInt.outputs.id
    identityId: identities.outputs.appsId
    identityClientId: identities.outputs.appsClientId
    registryServer: acr.outputs.loginServer
    image: policyImage
    envVars: [
      { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: monitoring.outputs.appiConnectionString }
      { name: 'OTEL_SERVICE_NAME', value: 'policy' }
      { name: 'OTEL_PYTHON_FASTAPI_EXCLUDED_URLS', value: 'healthz' }
    ]
  }
}

module caMockapi 'modules/container-app.bicep' = {
  name: 'ca-mockapi'
  params: {
    name: names.caMockapi
    location: location
    tags: tags
    serviceName: 'mockapi'
    environmentId: acaInt.outputs.id
    identityId: identities.outputs.appsId
    identityClientId: identities.outputs.appsClientId
    registryServer: acr.outputs.loginServer
    image: mockapiImage
    secretEnv: { MOCK_CONSENT_SECRET: mockConsentSecret }
  }
}

output POLICY_APP_NAME string = caPolicy.outputs.name
output MOCKAPI_APP_NAME string = caMockapi.outputs.name
output POLICY_URL string = policyUrl
output MOCKAPI_URL string = mockapiUrl

// ── Functions (T7) ─────────────────────────────────────────────────────
module functions 'modules/functions.bicep' = {
  name: 'functions'
  params: {
    planName: names.plan
    name: names.func
    location: location
    tags: tags
    operatorIp: operatorIp
    identityId: identities.outputs.orchestratorId
    identityClientId: identities.outputs.orchestratorClientId
    subnetId: subnetId['snet-functions']
    peSubnetId: subnetId['snet-pe']
    zoneId: zoneId['privatelink.azurewebsites.net']
    storageName: stFunc.outputs.name
    storageBlobEndpoint: stFunc.outputs.blobEndpoint
    appInsightsConnectionString: monitoring.outputs.appiConnectionString
    extraSettings: union({
      OTEL_SERVICE_NAME: 'orchestrator'
      COSMOS_ENDPOINT: cosmos.outputs.endpoint
      COSMOS_DATABASE: cosmos.outputs.databaseName
      DATA_BLOB_ENDPOINT: stData.outputs.blobEndpoint
      AI_PROJECT_ENDPOINT: 'https://${names.ais}.services.ai.azure.com/api/projects/${names.project}'
      DATA_ASSEMBLY_MODEL: 'gpt-5.4-mini'
      AI_ACCOUNT_ENDPOINT: 'https://${names.ais}.services.ai.azure.com'
      DECISION_DEPLOYMENT: 'decision-1'
      ANALYST_MODEL: 'gpt-5.4'
      REVIEWER_MODEL: 'gpt-5.4-mini'
      CLASSIFIER_MODEL: 'gpt-5.4-mini'
      EW_MODEL: 'gpt-5.4'
      COLLECTIONS_MODEL: 'gpt-5.4-mini'
      NOTICE_MODEL: 'gpt-5.4-mini'
      GRIEVANCE_MODEL: 'gpt-5.4-mini'
      // 동의 수명주기 예약(consent-lifecycle 큐). 오케스트레이터 ID가 함수 스토리지 큐 기여자입니다.
      QUEUE_ENDPOINT: 'https://${names.stFunc}.queue.${storageSuffix}/'
      POLICY_URL: policyUrl
      MOCKAPI_URL: mockapiUrl
      SERVICEBUS_FQDN: serviceBusEnabled ? '${names.sb}.servicebus.windows.net' : ''
      KEYVAULT_URI: keyVault.outputs.uri
      AI_GATEWAY: gatewaySettings.AI_GATEWAY
      AI_GATEWAY_URL: gatewaySettings.AI_GATEWAY_URL
    }, serviceBusEnabled ? {
      // Service Bus 트리거(관리 ID). pause 상태에서는 트리거를 등록하지 않으므로 두지 않습니다.
      ServiceBusConnection__fullyQualifiedNamespace: '${names.sb}.servicebus.windows.net'
      ServiceBusConnection__credential: 'managedidentity'
      ServiceBusConnection__clientId: identities.outputs.orchestratorClientId
    } : {})
  }
}

output FUNCTION_APP_NAME string = functions.outputs.name

// ── 심사역 콘솔 (P4b) ──────────────────────────────────────────────────
// 외부 Container Apps 환경(snet-apps-web, 같은 VNet이라 Private Endpoint DNS가 풀립니다).
// 진입은 운영자 IP 허용 목록 + Entra 로그인(앱 역할)으로 막습니다.
var consoleFqdn = '${names.caConsole}.${acaWeb.outputs.defaultDomain}'

module consoleAuth 'modules/console-auth.bicep' = {
  name: 'console-auth'
  params: {
    uniqueName: names.consoleApp
    displayName: 'AMA Lending 심사 콘솔 (${environmentName})'
    redirectUri: 'https://${consoleFqdn}/.auth/login/aad/callback'
    operatorPrincipalId: operatorPrincipalId
  }
}

module caConsole 'modules/container-app.bicep' = {
  name: 'ca-console'
  params: {
    name: names.caConsole
    location: location
    tags: tags
    serviceName: 'console'
    environmentId: acaWeb.outputs.id
    identityId: identities.outputs.consoleId
    identityClientId: identities.outputs.consoleClientId
    registryServer: acr.outputs.loginServer
    image: consoleImage
    ipAllowList: [ empty(consoleIp) ? '192.0.2.0/32' : '${consoleIp}/32' ]
    authClientId: consoleAuth.outputs.clientId
    authEnabled: consoleAuthEnabled
    envVars: concat([
      { name: 'COSMOS_ENDPOINT', value: cosmos.outputs.endpoint }
      { name: 'COSMOS_DATABASE', value: cosmos.outputs.databaseName }
      { name: 'DATA_BLOB_ENDPOINT', value: stData.outputs.blobEndpoint }
      { name: 'QUEUE_ENDPOINT', value: 'https://${names.stFunc}.queue.${storageSuffix}/' }
      { name: 'KEYVAULT_URI', value: keyVault.outputs.uri }
    ], consoleAuthEnabled ? [] : [
      { name: 'CONSOLE_AUTH_MODE', value: 'demo' }
      { name: 'CONSOLE_DEMO_ROLE', value: consoleDemoRole }
    ])
  }
}

output CONSOLE_APP_NAME string = caConsole.outputs.name
output CONSOLE_URL string = 'https://${consoleFqdn}'
output CONSOLE_CLIENT_ID string = consoleAuth.outputs.clientId

// ── VNet 안 smoke Job (T8) ─────────────────────────────────────────────
// Private Endpoint가 있는 모든 호스트. VNet 안에서 사설 IP로 해석되어야 합니다.
var smokePrivateHosts = concat([
  '${names.ais}.openai.azure.com'
  '${names.ais}.services.ai.azure.com'
  '${names.ais}.cognitiveservices.azure.com'
  '${names.search}.search.windows.net'
  '${names.stAgents}.blob.${storageSuffix}'
  '${names.stData}.blob.${storageSuffix}'
  '${names.stFunc}.blob.${storageSuffix}'
  '${names.stFunc}.queue.${storageSuffix}'
  '${names.stFunc}.table.${storageSuffix}'
  '${names.cosmos}.documents.azure.com'
  '${names.kv}${environment().suffixes.keyvaultDns}'
  '${names.acr}${environment().suffixes.acrLoginServer}'
  '${names.func}.azurewebsites.net'
  '${names.apim}.azure-api.net'
], serviceBusEnabled ? [ '${names.sb}.servicebus.windows.net' ] : [])

module smokeJob 'modules/smoke-job.bicep' = {
  name: 'smoke-job'
  params: {
    name: names.smokeJob
    location: location
    tags: tags
    environmentId: acaInt.outputs.id
    identityId: identities.outputs.smokeId
    identityClientId: identities.outputs.smokeClientId
    config: {
      openai_host: '${names.ais}.openai.azure.com'
      project_endpoint: 'https://${names.ais}.services.ai.azure.com/api/projects/${names.project}'
      blob_host: '${names.stData}.blob.${storageSuffix}'
      cosmos_host: '${names.cosmos}.documents.azure.com'
      keyvault_host: '${names.kv}${environment().suffixes.keyvaultDns}'
      servicebus_host: serviceBusEnabled ? '${names.sb}.servicebus.windows.net' : ''
      servicebus_topic: 'flow.updated'
      chat_models: map(filter(modelDeployments, m => m.format == 'OpenAI'), m => m.name)
      agent_model: 'gpt-5.4-mini'
      private_hosts: smokePrivateHosts
      queue_host: '${names.stFunc}.queue.${storageSuffix}'
      start_queue: 'applications-start'
      decision_queue: 'officer-decisions'
      consent_command_queue: 'consent-commands'
      cosmos_database: cosmos.outputs.databaseName
      policy_url: policyUrl
      mockapi_url: mockapiUrl
      e2e_applicant: 'seller-dailylook-01'
      ai_account_endpoint: 'https://${names.ais}.services.ai.azure.com'
      decision_deployment: 'decision-1'
    }
  }
}

output SMOKE_JOB_NAME string = smokeJob.outputs.name

// ── 평가 하네스 Job (P6a) ──────────────────────────────────────────────
// 모델 배포 이름은 오케스트레이터 설정과 같게 둡니다(평가가 운영 구성을 잰다).
module evalReportsRole 'modules/blob-container-role.bicep' = {
  name: 'eval-reports-role'
  params: {
    storageName: stData.outputs.name
    containerName: evalReportsContainer
    roleAssignments: [
      { principalId: identities.outputs.evalPrincipalId, roleId: role.blobContributor, principalType: sp }
    ]
  }
}

module evalJob 'modules/eval-job.bicep' = {
  name: 'eval-job'
  params: {
    name: names.evalJob
    location: location
    tags: tags
    environmentId: acaInt.outputs.id
    identityId: identities.outputs.evalId
    identityClientId: identities.outputs.evalClientId
    registryServer: acr.outputs.loginServer
    image: evalImage
    envVars: [
      { name: 'AI_PROJECT_ENDPOINT', value: 'https://${names.ais}.services.ai.azure.com/api/projects/${names.project}' }
      { name: 'AI_ACCOUNT_ENDPOINT', value: 'https://${names.ais}.services.ai.azure.com' }
      { name: 'DECISION_DEPLOYMENT', value: 'decision-1' }
      { name: 'EVAL_FULL_MODEL', value: 'gpt-5.4' }
      { name: 'EVAL_MINI_MODEL', value: 'gpt-5.4-mini' }
      { name: 'REVIEWER_MODEL', value: 'gpt-5.4-mini' }
      { name: 'EVAL_JUDGE_MODEL', value: 'gpt-5.4' }
      { name: 'EVAL_BUDGET_USD', value: '5' }
      { name: 'EVAL_EMIT_LOGS', value: '1' }
      { name: 'EVAL_BLOB_ACCOUNT_URL', value: stData.outputs.blobEndpoint }
      { name: 'EVAL_BLOB_CONTAINER', value: evalReportsContainer }
      { name: 'AI_GATEWAY', value: gatewaySettings.AI_GATEWAY }
      { name: 'AI_GATEWAY_URL', value: gatewaySettings.AI_GATEWAY_URL }
    ]
  }
}

output EVAL_JOB_NAME string = evalJob.outputs.name
output EVAL_REPORTS_CONTAINER string = evalReportsContainer

// ── 운영 지표 (P6b) ────────────────────────────────────────────────────
// 같은 eval 이미지로 VNet 안에서 합성 신청을 제출하고 Cosmos 결과를 모읍니다.
module opsJob 'modules/eval-job.bicep' = {
  name: 'ops-job'
  params: {
    name: names.opsJob
    location: location
    tags: tags
    environmentId: acaInt.outputs.id
    identityId: identities.outputs.opsId
    identityClientId: identities.outputs.opsClientId
    registryServer: acr.outputs.loginServer
    image: evalImage
    containerName: 'ops'
    command: [ '/opt/venv/bin/python', '-m', 'opsjob' ]
    replicaTimeout: 5400
    envVars: [
      { name: 'COSMOS_ENDPOINT', value: cosmos.outputs.endpoint }
      { name: 'COSMOS_DATABASE', value: cosmos.outputs.databaseName }
      { name: 'QUEUE_ENDPOINT', value: 'https://${names.stFunc}.queue.${environment().suffixes.storage}' }
    ]
  }
}

// ── AI 게이트웨이(APIM) ────────────────────────────────────────────────
// 에이전트마다 제품·구독·Foundry 연결 하나. TPM은 Task 11 실측(운영 부하+평가) 피크 기준입니다.
// gpt-5.4를 주로 쓰는 gw-analyst + gw-early-warning 합(150K)은 gpt-5.4 배포 용량(main.bicep)과 같습니다.
// src/agents/gateway.py CONNECTIONS와 이름이 같아야 합니다(테스트가 교차 확인).
var gateways = [
  { name: 'gw-analyst', tpm: 100000 }
  { name: 'gw-early-warning', tpm: 50000 }
  { name: 'gw-reviewer', tpm: 15000 }
  { name: 'gw-data-assembly', tpm: 17000 }
  { name: 'gw-txn-classifier', tpm: 20000 }
  { name: 'gw-collections', tpm: 10000 }
  { name: 'gw-consent-notice', tpm: 5000 }
  { name: 'gw-grievance', tpm: 5000 }
  { name: 'gw-probe', tpm: 1000 }
]

module apim 'modules/apim.bicep' = {
  name: 'apim'
  params: {
    name: names.apim
    location: location
    tags: tags
    subnetId: subnetId['snet-apim']
    aiAccountName: aiAccount.outputs.name
    appiName: monitoring.outputs.appiName
    appiConnectionString: monitoring.outputs.appiConnectionString
    lawId: monitoring.outputs.lawId
    allowedAppIds: [ identities.outputs.orchestratorClientId, identities.outputs.evalClientId ]
    gateways: gateways
  }
  dependsOn: [ dns ]
}

module gatewayConnections 'modules/foundry/gateway-connections.bicep' = {
  name: 'gateway-connections'
  params: {
    accountName: aiAccount.outputs.name
    projectName: aiProject.outputs.name
    apimName: apim.outputs.name
    connections: map(gateways, g => g.name)
    modelDeployments: modelDeployments
  }
  // caphost 프로비저닝 중이거나 다른 연결 PUT과 겹치면 409가 나므로 뒤에 둡니다.
  dependsOn: [ aiCapHost, opsInsights ]
}

var gatewaySettings = {
  AI_GATEWAY: aiGatewayRouting ? 'apim' : 'off'
  AI_GATEWAY_URL: 'https://${names.apim}.azure-api.net'
}

module opsInsights 'modules/ops-insights.bicep' = {
  name: 'ops-insights'
  params: {
    location: location
    tags: tags
    lawId: monitoring.outputs.lawId
    appiName: monitoring.outputs.appiName
    appiConnectionString: monitoring.outputs.appiConnectionString
    aiAccountName: aiAccount.outputs.name
    projectName: aiProject.outputs.name
    projectPrincipalId: aiProject.outputs.principalId
    alertEmail: opsAlertEmail
  }
  // caphost 프로비저닝 중 프로젝트 연결 PUT은 409가 나므로 뒤에 둡니다.
  dependsOn: [ aiCapHost ]
}

output OPS_JOB_NAME string = opsJob.outputs.name
