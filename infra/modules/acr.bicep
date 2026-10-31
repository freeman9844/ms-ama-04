// ACR Premium(Private Endpoint 지원). 이미지는 az acr build로 만들므로 빌드 창(publicAccess)에서만 공개합니다.
param name string
param location string
param tags object
param publicAccess bool
param peSubnetId string
param zoneId string
@description('[{ principalId, roleId, principalType }]')
param roleAssignments array = []

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: name
  location: location
  tags: tags
  sku: { name: 'Premium' }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: publicAccess ? 'Enabled' : 'Disabled'
    networkRuleSet: {
      defaultAction: publicAccess ? 'Allow' : 'Deny'
      ipRules: []
    }
    networkRuleBypassOptions: 'AzureServices'
    zoneRedundancy: 'Disabled'
  }
}

module pe 'pe.bicep' = {
  name: 'pe-${name}'
  params: {
    name: 'pe-${name}-registry'
    location: location
    tags: tags
    subnetId: peSubnetId
    targetId: acr.id
    groupId: 'registry'
    zoneIds: [ zoneId ]
  }
}

resource ra 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for a in roleAssignments: {
  scope: acr
  name: guid(acr.id, a.principalId, a.roleId)
  properties: {
    principalId: a.principalId
    principalType: a.principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', a.roleId)
  }
}]

output name string = acr.name
output loginServer string = acr.properties.loginServer
