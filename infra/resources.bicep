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
  smokeJob: 'job-smoke'
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
]

// 기본 제공 역할 정의 GUID (az role definition list로 확인, 2026-10-31)
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
}
var sp = 'ServicePrincipal'
var hasOperator = !empty(operatorPrincipalId)

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
    operatorIp: operatorIp
    peSubnetId: subnetId['snet-pe']
    zoneId: zoneId['privatelink.vaultcore.azure.net']
    roleAssignments: concat([
      { principalId: identities.outputs.orchestratorPrincipalId, roleId: role.kvValueReader, principalType: sp }
      { principalId: identities.outputs.appsPrincipalId, roleId: role.kvValueReader, principalType: sp }
      { principalId: identities.outputs.smokePrincipalId, roleId: role.kvValueReader, principalType: sp }
    ], hasOperator ? [
      { principalId: operatorPrincipalId, roleId: role.kvValueAdmin, principalType: 'User' }
    ] : [])
  }
}

output KEYVAULT_NAME string = keyVault.outputs.name
output KEYVAULT_URI string = keyVault.outputs.uri
output LOG_ANALYTICS_CUSTOMER_ID string = monitoring.outputs.lawCustomerId
output APPLICATIONINSIGHTS_CONNECTION_STRING string = monitoring.outputs.appiConnectionString
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
    operatorIp: ''  // Foundry BYO Storage는 항상 비공개
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
    operatorIp: operatorIp
    peSubnetId: subnetId['snet-pe']
    endpoints: [ { groupId: 'blob', zoneId: zoneId[blobZone] } ]
    softDeleteDays: 0
    roleAssignments: concat([
      { principalId: identities.outputs.orchestratorPrincipalId, roleId: role.blobContributor, principalType: sp }
      { principalId: identities.outputs.appsPrincipalId, roleId: role.blobContributor, principalType: sp }
      { principalId: identities.outputs.smokePrincipalId, roleId: role.blobReader, principalType: sp }
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
    operatorIp: operatorIp
    peSubnetId: subnetId['snet-pe']
    endpoints: [
      { groupId: 'blob', zoneId: zoneId[blobZone] }
      { groupId: 'queue', zoneId: zoneId[queueZone] }
      { groupId: 'table', zoneId: zoneId[tableZone] }
    ]
    containers: [ 'deploymentpackage' ]
    roleAssignments: [
      { principalId: identities.outputs.orchestratorPrincipalId, roleId: role.blobOwner, principalType: sp }
      { principalId: identities.outputs.orchestratorPrincipalId, roleId: role.queueContributor, principalType: sp }
      { principalId: identities.outputs.orchestratorPrincipalId, roleId: role.tableContributor, principalType: sp }
    ]
  }
}

module cosmos 'modules/cosmos.bicep' = {
  name: 'cosmos'
  params: {
    name: names.cosmos
    location: location
    tags: tags
    operatorIp: operatorIp
    peSubnetId: subnetId['snet-pe']
    zoneId: zoneId['privatelink.documents.azure.com']
    sqlRoleAssignments: concat([
      { principalId: identities.outputs.orchestratorPrincipalId, roleDefinitionGuid: '00000000-0000-0000-0000-000000000002', scope: 'database' }
      { principalId: identities.outputs.appsPrincipalId, roleDefinitionGuid: '00000000-0000-0000-0000-000000000002', scope: 'database' }
      { principalId: identities.outputs.smokePrincipalId, roleDefinitionGuid: '00000000-0000-0000-0000-000000000001', scope: 'account' }
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
