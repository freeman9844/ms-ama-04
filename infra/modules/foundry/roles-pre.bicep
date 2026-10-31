// capability host 생성 전에 필요한 프로젝트 관리 ID 역할(샘플 15와 동일).
param projectPrincipalId string
param cosmosName string
param storageName string
param searchName string

var cosmosOperator = '230815da-be43-4aae-9cb4-875f7bd000aa'
var blobContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var searchRoles = [
  '8ebe5a00-799e-43f5-93ac-243d3dce84a7' // Search Index Data Contributor
  '7ca78c08-252a-4471-8644-bb5ff32d4ba0' // Search Service Contributor
]

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' existing = {
  name: cosmosName
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageName
}

resource search 'Microsoft.Search/searchServices@2024-06-01-preview' existing = {
  name: searchName
}

resource cosmosRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: cosmos
  name: guid(cosmos.id, projectPrincipalId, cosmosOperator)
  properties: {
    principalId: projectPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cosmosOperator)
  }
}

resource storageRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, projectPrincipalId, blobContributor)
  properties: {
    principalId: projectPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobContributor)
  }
}

resource searchRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for r in searchRoles: {
  scope: search
  name: guid(search.id, projectPrincipalId, r)
  properties: {
    principalId: projectPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', r)
  }
}]
