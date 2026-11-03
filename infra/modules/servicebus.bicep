// Service Bus Premium(Private Endpoint는 Premium만 지원). 월 약 $677이므로 쓰지 않을 때는 pause(삭제)합니다.
param name string
param location string
param tags object
param peSubnetId string
param zoneId string
param topics array
@description('[{ topic, name }] 세션 구독. subject(label)가 토픽 이름과 같은 메시지만 받습니다.')
param subscriptions array = []
@description('[{ principalId, roleId, principalType }]')
param roleAssignments array = []

resource sb 'Microsoft.ServiceBus/namespaces@2024-01-01' = {
  name: name
  location: location
  tags: tags
  sku: { name: 'Premium', tier: 'Premium', capacity: 1 }
  properties: {
    disableLocalAuth: true
    publicNetworkAccess: 'Disabled'
    minimumTlsVersion: '1.2'
  }
}

resource networkRules 'Microsoft.ServiceBus/namespaces/networkRuleSets@2024-01-01' = {
  parent: sb
  name: 'default'
  properties: {
    publicNetworkAccess: 'Disabled'
    defaultAction: 'Deny'
    trustedServiceAccessEnabled: false
    ipRules: []
    virtualNetworkRules: []
  }
}

// 중복 감지: 같은 MessageId 재발행(재시도)을 10분 동안 무시해 이벤트 소비를 멱등하게 돕습니다.
resource topicResources 'Microsoft.ServiceBus/namespaces/topics@2024-01-01' = [for t in topics: {
  parent: sb
  name: t
  properties: {
    defaultMessageTimeToLive: 'P14D'
    requiresDuplicateDetection: true
    duplicateDetectionHistoryTimeWindow: 'PT10M'
  }
}]

resource subscriptionResources 'Microsoft.ServiceBus/namespaces/topics/subscriptions@2024-01-01' = [for s in subscriptions: {
  name: '${name}/${s.topic}/${s.name}'
  properties: {
    requiresSession: true
    lockDuration: 'PT5M'
    maxDeliveryCount: 5
    deadLetteringOnMessageExpiration: true
  }
  dependsOn: [ topicResources ]
}]

// $Default PUT은 구독 생성 시 자동 생기는 1=1(전체 수신) 규칙을 없애는 용도입니다(실측: 200이지만 규칙은 남지 않음).
// 실제 필터는 이름이 다른 subject-label 규칙이며, 그것만 남아 subject(label) 없는 메시지(스모크)는 받지 않습니다.
resource defaultRules 'Microsoft.ServiceBus/namespaces/topics/subscriptions/rules@2024-01-01' = [for (s, i) in subscriptions: {
  parent: subscriptionResources[i]
  name: '$Default'
  properties: {
    filterType: 'CorrelationFilter'
    correlationFilter: { label: s.topic }
  }
}]

resource subscriptionRules 'Microsoft.ServiceBus/namespaces/topics/subscriptions/rules@2024-01-01' = [for (s, i) in subscriptions: {
  parent: subscriptionResources[i]
  name: 'subject-label'
  properties: {
    filterType: 'CorrelationFilter'
    correlationFilter: { label: s.topic }
  }
  dependsOn: [ defaultRules ]
}]

module pe 'pe.bicep' = {
  name: 'pe-${name}'
  params: {
    name: 'pe-${name}-namespace'
    location: location
    tags: tags
    subnetId: peSubnetId
    targetId: sb.id
    groupId: 'namespace'
    zoneIds: [ zoneId ]
  }
}

resource ra 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for a in roleAssignments: {
  scope: sb
  name: guid(sb.id, a.principalId, a.roleId)
  properties: {
    principalId: a.principalId
    principalType: a.principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', a.roleId)
  }
}]
