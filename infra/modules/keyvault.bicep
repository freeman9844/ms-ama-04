// Key Vault(RBAC 모드). 배포 창에서만 운영자 IP를 허용합니다.
param name string
param location string
param tags object
param operatorIp string
param peSubnetId string
param zoneId string
@description('[{ principalId, roleId, principalType }]')
param roleAssignments array = []

var windowOpen = !empty(operatorIp)

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
    publicNetworkAccess: windowOpen ? 'Enabled' : 'Disabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'None'
      ipRules: windowOpen ? [ { value: operatorIp } ] : []
    }
  }
}

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
