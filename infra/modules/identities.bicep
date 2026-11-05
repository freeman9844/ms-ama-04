// 워크로드별 사용자 할당 관리 ID. 오케스트레이터(Functions), 앱(Container Apps), smoke Job, 심사역 콘솔, 평가 Job, 운영 지표 Job, App Gateway.
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

// 평가 Job은 모델 호출과 eval-reports 컨테이너 쓰기만 합니다(Cosmos·Key Vault·큐 권한 없음).
resource eval 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: names.eval
  location: location
  tags: tags
}

// 운영 지표 Job은 신청 큐 보내기·Cosmos 읽기만 합니다(모델·Key Vault·쓰기 권한 없음).
resource ops 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: names.ops
  location: location
  tags: tags
}

// App Gateway는 Key Vault의 TLS 인증서 비밀만 읽습니다.
resource appgw 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: names.appgw
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
output evalId string = eval.id
output evalPrincipalId string = eval.properties.principalId
output evalClientId string = eval.properties.clientId
output opsId string = ops.id
output opsPrincipalId string = ops.properties.principalId
output opsClientId string = ops.properties.clientId
output appgwId string = appgw.id
output appgwPrincipalId string = appgw.properties.principalId
