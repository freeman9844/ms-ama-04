// Cosmos DB for NoSQL(serverless). Foundry 스레드 저장소(enterprise_memory, caphost가 생성)와 앱 DB(lending)를 함께 둡니다.
param name string
param location string
param tags object
param operatorIp string
param peSubnetId string
param zoneId string
param appDatabaseName string = 'lending'
@description('[{ principalId, roleDefinitionGuid, scope: account|database }] — Cosmos 데이터 평면 SQL 역할')
param sqlRoleAssignments array = []

var windowOpen = !empty(operatorIp)

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = {
  name: name
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    consistencyPolicy: { defaultConsistencyLevel: 'Session' }
    locations: [ { locationName: location, failoverPriority: 0, isZoneRedundant: false } ]
    capabilities: [ { name: 'EnableServerless' } ]
    disableLocalAuth: true
    disableKeyBasedMetadataWriteAccess: true
    publicNetworkAccess: windowOpen ? 'Enabled' : 'Disabled'
    ipRules: windowOpen ? [ { ipAddressOrRange: operatorIp } ] : []
    networkAclBypass: 'None'
  }
}

resource appDb 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-11-15' = {
  parent: cosmos
  name: appDatabaseName
  properties: { resource: { id: appDatabaseName } }
}

module pe 'pe.bicep' = {
  name: 'pe-${name}'
  params: {
    name: 'pe-${name}-sql'
    location: location
    tags: tags
    subnetId: peSubnetId
    targetId: cosmos.id
    groupId: 'Sql'
    zoneIds: [ zoneId ]
  }
}

// Cosmos는 역할 할당마다 계정 단위 배타 잠금을 잡으므로 하나씩, PE 승인이 끝난 뒤에 씁니다.
@batchSize(1)
resource sqlRa 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = [for a in sqlRoleAssignments: {
  parent: cosmos
  name: guid(cosmos.id, a.principalId, a.roleDefinitionGuid, a.scope)
  properties: {
    principalId: a.principalId
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/${a.roleDefinitionGuid}'
    scope: a.scope == 'database' ? '${cosmos.id}/dbs/${appDatabaseName}' : cosmos.id
  }
  dependsOn: [ appDb, pe ]
}]

output name string = cosmos.name
output endpoint string = cosmos.properties.documentEndpoint
output databaseName string = appDb.name
