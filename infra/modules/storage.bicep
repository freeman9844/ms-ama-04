// 범용 Storage 계정. 공유 키를 끄고 항상 비공개(Private Endpoint 전용)입니다.
param name string
param location string
param tags object
@description('신뢰할 수 있는 Azure 서비스 우회. Foundry BYO Storage는 샘플대로 AzureServices.')
param bypass string = 'None'
param peSubnetId string
@description('[{ groupId: blob|queue|table, zoneId }]')
param endpoints array
param containers array = []
@description('Storage 큐 이름 목록(Functions 큐 트리거)')
param queues array = []
@description('blob soft delete 보존 일수. 0이면 끕니다(원천 데이터 삭제 증명용).')
param softDeleteDays int = 7
@description('[{ principalId, roleId, principalType }]')
param roleAssignments array = []


resource st 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: name
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    defaultToOAuthAuthentication: true
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: bypass
      ipRules: []
      virtualNetworkRules: []
    }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: st
  name: 'default'
  properties: {
    deleteRetentionPolicy: softDeleteDays > 0 ? { enabled: true, days: softDeleteDays } : { enabled: false }
  }
}

resource blobContainers 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = [for c in containers: {
  parent: blobService
  name: c
  properties: { publicAccess: 'None' }
}]

resource queueService 'Microsoft.Storage/storageAccounts/queueServices@2023-05-01' = if (!empty(queues)) {
  parent: st
  name: 'default'
}

resource queueResources 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-05-01' = [for q in queues: {
  parent: queueService
  name: q
}]

module pes 'pe.bicep' = [for e in endpoints: {
  name: 'pe-${name}-${e.groupId}'
  params: {
    name: 'pe-${name}-${e.groupId}'
    location: location
    tags: tags
    subnetId: peSubnetId
    targetId: st.id
    groupId: e.groupId
    zoneIds: [ e.zoneId ]
  }
}]

resource ra 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for a in roleAssignments: {
  scope: st
  name: guid(st.id, a.principalId, a.roleId)
  properties: {
    principalId: a.principalId
    principalType: a.principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', a.roleId)
  }
}]

output name string = st.name
output blobEndpoint string = st.properties.primaryEndpoints.blob
