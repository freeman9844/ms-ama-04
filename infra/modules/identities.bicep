// 워크로드별 사용자 할당 관리 ID. 오케스트레이터(Functions), 앱(Container Apps), smoke Job.
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

output orchestratorId string = orchestrator.id
output orchestratorPrincipalId string = orchestrator.properties.principalId
output orchestratorClientId string = orchestrator.properties.clientId
output appsId string = apps.id
output appsPrincipalId string = apps.properties.principalId
output appsClientId string = apps.properties.clientId
output smokeId string = smoke.id
output smokePrincipalId string = smoke.properties.principalId
output smokeClientId string = smoke.properties.clientId
