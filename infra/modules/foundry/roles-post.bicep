// capability host 생성 후 역할(샘플 15와 동일).
// - Foundry BYO Storage: 이 프로젝트의 에이전트 컨테이너(<workspaceId>*-azureml-agent)에만 Blob Data Owner(ABAC 조건)
// - Cosmos: caphost가 만든 enterprise_memory DB에 SQL 기본 제공 Data Contributor
param projectPrincipalId string
param workspaceId string
param cosmosName string
param storageName string

var blobOwner = 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b'
var cosmosContributor = '00000000-0000-0000-0000-000000000002'
var condition = '((!(ActionMatches{\'Microsoft.Storage/storageAccounts/blobServices/containers/blobs/tags/read\'})  AND  !(ActionMatches{\'Microsoft.Storage/storageAccounts/blobServices/containers/blobs/filter/action\'}) AND  !(ActionMatches{\'Microsoft.Storage/storageAccounts/blobServices/containers/blobs/tags/write\'}) ) OR (@Resource[Microsoft.Storage/storageAccounts/blobServices/containers:name] StringStartsWithIgnoreCase \'${workspaceId}\' AND @Resource[Microsoft.Storage/storageAccounts/blobServices/containers:name] StringLikeIgnoreCase \'*-azureml-agent\'))'

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageName
}

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' existing = {
  name: cosmosName
}

resource blobOwnerRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, projectPrincipalId, blobOwner, workspaceId)
  properties: {
    principalId: projectPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobOwner)
    conditionVersion: '2.0'
    condition: condition
  }
}

resource threadStoreRole 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = {
  parent: cosmos
  name: guid(workspaceId, cosmosName, cosmosContributor, projectPrincipalId)
  properties: {
    principalId: projectPrincipalId
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/${cosmosContributor}'
    scope: '${cosmos.id}/dbs/enterprise_memory'
  }
}
