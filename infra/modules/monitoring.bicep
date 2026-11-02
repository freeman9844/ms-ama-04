// Log Analytics + 작업 영역 기반 App Insights. 로컬 인증을 끄므로 쓰는 쪽은 Monitoring Metrics Publisher 역할이 필요합니다.
param lawName string
param appiName string
param location string
param tags object
@description('[{ principalId, roleId, principalType }]')
param roleAssignments array = []

resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: lawName
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appi 'Microsoft.Insights/components@2020-02-02' = {
  name: appiName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: law.id
    DisableLocalAuth: true
  }
}

resource ra 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for a in roleAssignments: {
  scope: appi
  name: guid(appi.id, a.principalId, a.roleId)
  properties: {
    principalId: a.principalId
    principalType: a.principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', a.roleId)
  }
}]

output lawId string = law.id
output lawName string = law.name
output lawCustomerId string = law.properties.customerId
output appiConnectionString string = appi.properties.ConnectionString
output appiId string = appi.id
output appiName string = appi.name
