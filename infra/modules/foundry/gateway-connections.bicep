// Foundry 프로젝트 → APIM 게이트웨이 연결(에이전트마다 하나). 키는 APIM 구독 키를 배포 시점에 읽어 연결에만 넣습니다.
// 에이전트 정의의 모델을 '<연결>/<배포>'로 쓰면 Agent Service가 이 연결로 APIM을 부릅니다.
param accountName string
param projectName string
@description('APIM 이름. 모듈 출력으로 넘겨 APIM 구독 생성 뒤에 실행되게 합니다.')
param apimName string
param connections array
@description('ApiManagement(기본). Task 4 관문 1이 실패하면 ModelGateway로 바꿉니다.')
param category string = 'ApiManagement'
param modelDeployments array

var models = string(map(filter(modelDeployments, d => d.format == 'OpenAI'), d => {
  name: d.name
  properties: { model: { name: d.name, version: d.version, format: 'OpenAI' } }
}))

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: accountName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' existing = {
  parent: account
  name: projectName
}

// 같은 프로젝트에 연결 PUT을 동시에 보내면 409가 나므로 하나씩 만듭니다.
@batchSize(1)
resource connection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = [for c in connections: {
  parent: project
  name: c
  properties: {
    category: category
    target: 'https://${apimName}.azure-api.net/openai'
    authType: 'ApiKey'
    isSharedToAll: true
    credentials: {
      key: listSecrets(resourceId('Microsoft.ApiManagement/service/subscriptions', apimName, c), '2024-05-01').primaryKey
    }
    metadata: {
      deploymentInPath: 'true'
      inferenceAPIVersion: '2025-04-01-preview'
      models: models
    }
  }
}]
