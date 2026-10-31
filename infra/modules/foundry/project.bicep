// Foundry 프로젝트와 BYO 리소스 연결 3개(스레드: Cosmos, 파일: Storage, 벡터: AI Search). 모두 Entra 인증.
param accountName string
param name string
param location string
param tags object
param cosmosName string
param storageName string
param searchName string

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: accountName
}

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' existing = {
  name: cosmosName
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageName
}

resource search 'Microsoft.Search/searchServices@2024-06-01-preview' existing = {
  name: searchName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' = {
  parent: account
  name: name
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    displayName: name
    description: 'Agentic cash-flow lending demo project'
  }

  resource cosmosConnection 'connections@2025-04-01-preview' = {
    name: cosmosName
    properties: {
      category: 'CosmosDB'
      target: cosmos.properties.documentEndpoint
      authType: 'AAD'
      metadata: { ApiType: 'Azure', ResourceId: cosmos.id, location: location }
    }
  }

  resource storageConnection 'connections@2025-04-01-preview' = {
    name: storageName
    properties: {
      category: 'AzureStorageAccount'
      target: storage.properties.primaryEndpoints.blob
      authType: 'AAD'
      metadata: { ApiType: 'Azure', ResourceId: storage.id, location: location }
    }
  }

  resource searchConnection 'connections@2025-04-01-preview' = {
    name: searchName
    properties: {
      category: 'CognitiveSearch'
      target: 'https://${searchName}.search.windows.net'
      authType: 'AAD'
      metadata: { ApiType: 'Azure', ResourceId: search.id, location: location }
    }
  }
}

// internalId(32자리 16진수)를 GUID 형식으로 바꿉니다. 사후 역할의 ABAC 조건과 Cosmos 역할 이름에 쓰입니다.
// substring 대신 take/skip을 써서, 값이 비어 있는 오프라인 펼치기(PSRule)에서도 실패하지 않게 합니다.
var internalId = project.properties.internalId
var workspaceGuid = '${take(internalId, 8)}-${take(skip(internalId, 8), 4)}-${take(skip(internalId, 12), 4)}-${take(skip(internalId, 16), 4)}-${skip(internalId, 20)}'

output name string = project.name
output principalId string = project.identity.principalId
output workspaceId string = workspaceGuid
output cosmosConnection string = project::cosmosConnection.name
output storageConnection string = project::storageConnection.name
output searchConnection string = project::searchConnection.name
