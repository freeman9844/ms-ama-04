// Key Vault(RBAC 모드). 항상 비공개(Private Endpoint 전용)입니다.
param name string
param location string
param tags object
param peSubnetId string
param zoneId string
@description('[{ principalId, roleId, principalType }]')
param roleAssignments array = []
@secure()
@description('{ 비밀 이름: 값 }. 값이 빈 항목은 만들지 않습니다.')
param secrets object = {}


resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    tenantId: tenant().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      defaultAction: 'Deny'
      // ARM 템플릿 배포로 비밀을 기록할 때 신뢰 서비스 경로가 필요합니다(T9 pseudonym-key).
      bypass: 'AzureServices'
      ipRules: []
    }
  }
}

resource secretResources 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = [for s in filter(items(secrets), s => !empty(s.value)): {
  parent: kv
  name: s.key
  properties: { value: s.value }
}]

module pe 'pe.bicep' = {
  name: 'pe-${name}'
  params: {
    name: 'pe-${name}-vault'
    location: location
    tags: tags
    subnetId: peSubnetId
    targetId: kv.id
    groupId: 'vault'
    zoneIds: [ zoneId ]
  }
}

resource ra 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for a in roleAssignments: {
  scope: kv
  name: guid(kv.id, a.principalId, a.roleId)
  properties: {
    principalId: a.principalId
    principalType: a.principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', a.roleId)
  }
}]

output name string = kv.name
output uri string = kv.properties.vaultUri
