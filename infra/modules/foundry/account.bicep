// Foundry(AI Services) 계정. Agent Service는 snet-agents에 network injection 됩니다.
// 계정 capability host(<계정>@aml_aiagentservice)는 플랫폼이 자동 생성합니다.
param name string
param location string
param tags object
param operatorIp string
param agentSubnetId string
param peSubnetId string
@description('services.ai, openai, cognitiveservices 순서의 Private DNS zone ID')
param zoneIds array
@description('[{ name, deploymentName?, format, version, sku, capacity }] — deploymentName이 없으면 name을 씁니다.')
param modelDeployments array
@description('[{ principalId, roleId, principalType }]')
param roleAssignments array = []

var windowOpen = !empty(operatorIp)

#disable-next-line BCP036
resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' = {
  name: name
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    allowProjectManagement: true
    customSubDomainName: name
    disableLocalAuth: true
    publicNetworkAccess: windowOpen ? 'Enabled' : 'Disabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'AzureServices'
      ipRules: windowOpen ? [ { value: operatorIp } ] : []
      virtualNetworkRules: []
    }
    networkInjections: [
      {
        scenario: 'agent'
        subnetArmId: agentSubnetId
        useMicrosoftManagedNetwork: false
      }
    ]
  }
}

// 같은 계정의 배포를 동시에 만들면 충돌하므로 하나씩 배포합니다.
@batchSize(1)
resource deployments 'Microsoft.CognitiveServices/accounts/deployments@2025-04-01-preview' = [for m in modelDeployments: {
  parent: account
  name: m.?deploymentName ?? m.name
  sku: { name: m.sku, capacity: m.capacity }
  properties: {
    model: { name: m.name, format: m.format, version: m.version }
  }
}]

// 모델 배포 중에는 계정이 Accepted 상태라 PE를 동시에 만들면 실패하므로 배포 뒤에 만듭니다.
module pe '../pe.bicep' = {
  name: 'pe-${name}'
  dependsOn: [ deployments ]
  params: {
    name: 'pe-${name}-account'
    location: location
    tags: tags
    subnetId: peSubnetId
    targetId: account.id
    groupId: 'account'
    zoneIds: zoneIds
  }
}

resource ra 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for a in roleAssignments: {
  scope: account
  name: guid(account.id, a.principalId, a.roleId)
  properties: {
    principalId: a.principalId
    principalType: a.principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', a.roleId)
  }
}]

output name string = account.name
