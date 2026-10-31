// 프로젝트 capability host. 같은 이름으로 다시 PUT하면 실패할 수 있으므로 최초 배포에서만 만듭니다(deployCapabilityHost).
param accountName string
param projectName string
param name string
param cosmosConnection string
param storageConnection string
param searchConnection string

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: accountName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' existing = {
  parent: account
  name: projectName
}

resource caphost 'Microsoft.CognitiveServices/accounts/projects/capabilityHosts@2025-04-01-preview' = {
  parent: project
  name: name
  properties: {
    // Bicep 형식 정의에는 없지만 ARM API는 이 값을 요구합니다(샘플 15 주석 참고).
    #disable-next-line BCP037
    capabilityHostKind: 'Agents'
    threadStorageConnections: [ cosmosConnection ]
    storageConnections: [ storageConnection ]
    vectorStoreConnections: [ searchConnection ]
  }
}

output name string = caphost.name
