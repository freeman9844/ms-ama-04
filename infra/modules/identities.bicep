// 워크로드별 사용자 할당 관리 ID. 오케스트레이터(Functions), 앱(Container Apps), smoke Job, 심사역 콘솔.
param names object
param location string
param tags object

resource orchestrator 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: names.orchestrator
  location: location
  tags: tags
}

resource apps 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: names.apps
  location: location
  tags: tags
}

resource smoke 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: names.smoke
  location: location
  tags: tags
}

// 콘솔은 인터넷에 노출되므로 앱 ID와 분리해 읽기·큐 보내기 권한만 줍니다.
resource console 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: names.console
  location: location
  tags: tags
}

output orchestratorId string = orchestrator.id
output orchestratorPrincipalId string = orchestrator.properties.principalId
output orchestratorClientId string = orchestrator.properties.clientId
output appsId string = apps.id
output appsPrincipalId string = apps.properties.principalId
output appsClientId string = apps.properties.clientId
output smokeId string = smoke.id
output smokePrincipalId string = smoke.properties.principalId
output smokeClientId string = smoke.properties.clientId
output consoleId string = console.id
output consolePrincipalId string = console.properties.principalId
output consoleClientId string = console.properties.clientId
