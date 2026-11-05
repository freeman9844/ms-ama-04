// AI 게이트웨이: VNet 내부 모드 API Management(Developer). 공인 IP·인터넷 인바운드 없음.
// LLM 요청·응답 본문은 로그에 남기지 않습니다(진단 body bytes 0).
param name string
param location string
param tags object
param subnetId string
param aiAccountName string
param appiName string
@secure()
param appiConnectionString string
param lawId string
param dnsZoneName string = 'azure-api.net'
@description('decision API를 부를 수 있는 관리 ID의 클라이언트 ID(appid 클레임).')
param allowedAppIds array
@description('에이전트별 제품·구독. [{ name, tpm }]')
param gateways array

var cognitiveUser = 'a97b65f3-24c7-4388-baec-2e87135dc908'
var metricsPublisher = '3913510d-42f4-4e42-8a64-420c390055eb'

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: aiAccountName
}

resource appi 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appiName
}

resource zone 'Microsoft.Network/privateDnsZones@2024-06-01' existing = {
  name: dnsZoneName
}

resource apim 'Microsoft.ApiManagement/service@2024-05-01' = {
  name: name
  location: location
  tags: tags
  sku: { name: 'Developer', capacity: 1 }
  identity: { type: 'SystemAssigned' }
  properties: {
    publisherEmail: 'noreply@example.com'
    publisherName: 'AMA Lending Demo'
    virtualNetworkType: 'Internal'
    virtualNetworkConfiguration: { subnetResourceId: subnetId }
  }
}

// 백엔드 호출은 APIM 관리 ID(Entra)로만 합니다. 계정 키는 쓰지 않습니다.
resource aiRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, apim.id, cognitiveUser)
  scope: account
  properties: {
    principalId: apim.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveUser)
  }
}

resource appiRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(appi.id, apim.id, metricsPublisher)
  scope: appi
  properties: {
    principalId: apim.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', metricsPublisher)
  }
}

resource logger 'Microsoft.ApiManagement/service/loggers@2024-05-01' = {
  parent: apim
  name: 'appi'
  properties: {
    loggerType: 'applicationInsights'
    resourceId: appi.id
    credentials: {
      connectionString: appiConnectionString
      identityClientId: 'SystemAssigned'
    }
  }
  dependsOn: [ appiRole ]
}

// llm-emit-token-metric·emit-metric은 metrics=true여야 Application Insights로 갑니다.
resource diagnostic 'Microsoft.ApiManagement/service/diagnostics@2024-05-01' = {
  parent: apim
  name: 'applicationinsights'
  properties: {
    loggerId: logger.id
    alwaysLog: 'allErrors'
    metrics: true
    sampling: { samplingType: 'fixed', percentage: 100 }
    frontend: {
      request: { body: { bytes: 0 } }
      response: { body: { bytes: 0 } }
    }
    backend: {
      request: { body: { bytes: 0 } }
      response: { body: { bytes: 0 } }
    }
  }
}

resource toLaw 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'to-law'
  scope: apim
  properties: {
    workspaceId: lawId
    logAnalyticsDestinationType: 'Dedicated'
    logs: [ { categoryGroup: 'allLogs', enabled: true } ]
  }
}

// 내부 모드는 DNS를 만들어 주지 않습니다. 게이트웨이 호스트만 사설 IP로 등록합니다.
resource gatewayRecord 'Microsoft.Network/privateDnsZones/A@2024-06-01' = {
  parent: zone
  name: name
  properties: {
    ttl: 300
    aRecords: [ { ipv4Address: apim.properties.privateIPAddresses[0] } ]
  }
}

output name string = apim.name
output gatewayUrl string = 'https://${apim.name}.azure-api.net'
output principalId string = apim.identity.principalId
