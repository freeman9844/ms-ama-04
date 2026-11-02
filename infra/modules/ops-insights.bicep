// 운영 지표: Workbook, 경보 2개(에이전트 단계 실패율, Policy 서킷 open), Foundry 프로젝트 → App Insights 트레이스 연결.
param location string
param tags object
param lawId string
param appiName string
@secure()
param appiConnectionString string
param aiAccountName string
param projectName string
param projectPrincipalId string
@description('비우면 Action Group 없이 포털 경보만 남깁니다')
param alertEmail string = ''

var metricsPublisher = '3913510d-42f4-4e42-8a64-420c390055eb'
var hasEmail = !empty(alertEmail)

resource appi 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appiName
}

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: aiAccountName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' existing = {
  parent: account
  name: projectName
}

// 에이전트 서비스 트레이스(invoke_agent·chat·execute_tool)를 우리 App Insights로 보낸다(Probe P4).
resource traces 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: project
  name: appiName
  properties: {
    category: 'AppInsights'
    target: appi.id
    // Probe P4에서 실제 API가 받아들인 값이지만 Bicep 형식 정의에는 아직 없습니다.
    #disable-next-line BCP036
    authType: 'ProjectManagedIdentity'
    isSharedToAll: true
    metadata: {
      ApiType: 'Azure'
      ResourceId: appi.id
      ApplicationInsightsConnectionString: appiConnectionString
    }
  }
}

resource projectPublisher 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: appi
  name: guid(appi.id, projectPrincipalId, metricsPublisher)
  properties: {
    principalId: projectPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', metricsPublisher)
  }
}

resource group 'Microsoft.Insights/actionGroups@2023-01-01' = if (hasEmail) {
  name: 'ag-ops'
  location: 'global'
  tags: tags
  properties: {
    groupShortName: 'ops'
    enabled: true
    emailReceivers: [ { name: 'operator', emailAddress: alertEmail, useCommonAlertSchema: true } ]
  }
}

var alerts = [
  {
    name: 'alert-agent-step-failure'
    description: '에이전트 단계(assemble·classify·assess) 실패·안전망 비율 > 1% (1시간, 표본 ≥ 10)'
    window: 'PT1H'
    frequency: 'PT15M'
    query: 'AppTraces | where Message == "journey.step" and tostring(Properties.agent) =~ "true" | summarize total = count(), bad = countif(tostring(Properties.outcome) != "ok") | where total >= 10 and bad * 100.0 / total > 1'
  }
  {
    name: 'alert-policy-circuit-open'
    description: 'Policy 서킷 open 이벤트 (15분)'
    window: 'PT15M'
    frequency: 'PT5M'
    query: 'AppTraces | where Message == "policy.circuit" and tostring(Properties.state) == "open"'
  }
]

resource rules 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = [for a in alerts: {
  name: a.name
  location: location
  tags: tags
  properties: {
    description: a.description
    severity: 2
    enabled: true
    scopes: [ lawId ]
    windowSize: a.window
    evaluationFrequency: a.frequency
    criteria: {
      allOf: [
        {
          query: a.query
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: { numberOfEvaluationPeriods: 1, minFailingPeriodsToAlert: 1 }
        }
      ]
    }
    actions: { actionGroups: hasEmail ? [ group.id ] : [] }
  }
}]

resource workbook 'Microsoft.Insights/workbooks@2023-06-01' = {
  name: guid(resourceGroup().id, 'ops-workbook')
  location: location
  tags: tags
  kind: 'shared'
  properties: {
    displayName: '대출 여정 운영 지표(합성 데이터 기준)'
    category: 'workbook'
    sourceId: lawId
    serializedData: loadTextContent('ops-workbook.json')
  }
}

output connectionName string = traces.name
