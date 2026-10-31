// AI Search Basic. Foundry 벡터 저장소 연결용. 항상 비공개(에이전트는 VNet injection으로 접근).
param name string
param location string
param tags object
param peSubnetId string
param zoneId string

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
    disableLocalAuth: false
    authOptions: { aadOrApiKey: { aadAuthFailureMode: 'http401WithBearerChallenge' } }
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

output name string = search.name
