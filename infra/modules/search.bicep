// AI Search Basic. Foundry 벡터 저장소 연결용. 항상 비공개(에이전트는 VNet injection으로 접근).
param name string
param location string
param tags object
param peSubnetId string
param zoneId string
@description('감사 로그를 보낼 Log Analytics 작업 영역 ID')
param lawId string

resource search 'Microsoft.Search/searchServices@2024-06-01-preview' = {
  name: name
  location: location
  tags: tags
  sku: { name: 'basic' }
  identity: { type: 'SystemAssigned' }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    publicNetworkAccess: 'disabled'
    networkRuleSet: { bypass: 'None', ipRules: [] }
    // 키 인증을 끕니다. Foundry 연결(authType AAD)과 역할 할당만으로 접근합니다.
    disableLocalAuth: true
    semanticSearch: 'disabled'
  }
}

module pe 'pe.bicep' = {
  name: 'pe-${name}'
  params: {
    name: 'pe-${name}-search'
    location: location
    tags: tags
    subnetId: peSubnetId
    targetId: search.id
    groupId: 'searchService'
    zoneIds: [ zoneId ]
  }
}

// 감사 로그(쓰기·삭제·접근)만 보냅니다. 대량 읽기 로그는 비용 때문에 뺍니다.
resource auditToLaw 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'audit-to-law'
  scope: search
  properties: {
    workspaceId: lawId
    logAnalyticsDestinationType: 'Dedicated'
    logs: [
      { category: 'OperationLogs', enabled: true }
    ]
  }
}

output name string = search.name
