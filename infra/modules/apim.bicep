// AI 게이트웨이: VNet 내부 모드 API Management(Developer). 공인 IP·인터넷 인바운드 없음.
// LLM 요청·응답 본문은 로그에 남기지 않습니다(진단 body bytes 0).
param name string
param location string
param tags object
param subnetId string
param aiAccountName string
param appiName string
@secure()
param appiConnectionString string
param lawId string
param dnsZoneName string = 'azure-api.net'
@description('decision API를 부를 수 있는 관리 ID의 클라이언트 ID(appid 클레임).')
param allowedAppIds array
@description('에이전트별 제품·구독. [{ name, tpm }]')
param gateways array

var cognitiveUser = 'a97b65f3-24c7-4388-baec-2e87135dc908'
var metricsPublisher = '3913510d-42f4-4e42-8a64-420c390055eb'

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: aiAccountName
}

resource appi 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appiName
}

resource zone 'Microsoft.Network/privateDnsZones@2024-06-01' existing = {
  name: dnsZoneName
}

resource apim 'Microsoft.ApiManagement/service@2024-05-01' = {
  name: name
  location: location
  tags: tags
  sku: { name: 'Developer', capacity: 1 }
  identity: { type: 'SystemAssigned' }
  properties: {
    publisherEmail: 'noreply@example.com'
    publisherName: 'AMA Lending Demo'
    virtualNetworkType: 'Internal'
    virtualNetworkConfiguration: { subnetResourceId: subnetId }
  }
}

// 백엔드 호출은 APIM 관리 ID(Entra)로만 합니다. 계정 키는 쓰지 않습니다.
resource aiRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, apim.id, cognitiveUser)
  scope: account
  properties: {
    principalId: apim.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveUser)
  }
}

resource appiRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(appi.id, apim.id, metricsPublisher)
  scope: appi
  properties: {
    principalId: apim.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', metricsPublisher)
  }
}

resource logger 'Microsoft.ApiManagement/service/loggers@2024-05-01' = {
  parent: apim
  name: 'appi'
  properties: {
    loggerType: 'applicationInsights'
    resourceId: appi.id
    credentials: {
      connectionString: appiConnectionString
      identityClientId: 'SystemAssigned'
    }
  }
  dependsOn: [ appiRole ]
}

// llm-emit-token-metric·emit-metric은 metrics=true여야 Application Insights로 갑니다.
resource diagnostic 'Microsoft.ApiManagement/service/diagnostics@2024-05-01' = {
  parent: apim
  name: 'applicationinsights'
  properties: {
    loggerId: logger.id
    alwaysLog: 'allErrors'
    metrics: true
    sampling: { samplingType: 'fixed', percentage: 100 }
    frontend: {
      request: { body: { bytes: 0 } }
      response: { body: { bytes: 0 } }
    }
    backend: {
      request: { body: { bytes: 0 } }
      response: { body: { bytes: 0 } }
    }
  }
}

// ApiManagementGatewayLogs는 azuremonitor 진단이 있어야 채워집니다. 헤더·본문은 남기지 않습니다.
resource azmonLogger 'Microsoft.ApiManagement/service/loggers@2024-05-01' = {
  parent: apim
  name: 'azuremonitor'
  properties: {
    loggerType: 'azureMonitor'
    isBuffered: true
  }
}

resource azmonDiagnostic 'Microsoft.ApiManagement/service/diagnostics@2024-05-01' = {
  parent: apim
  name: 'azuremonitor'
  properties: {
    loggerId: azmonLogger.id
    logClientIp: false
    verbosity: 'information'
    sampling: { samplingType: 'fixed', percentage: 100 }
    frontend: {
      request: { headers: [], body: { bytes: 0 } }
      response: { headers: [], body: { bytes: 0 } }
    }
    backend: {
      request: { headers: [], body: { bytes: 0 } }
      response: { headers: [], body: { bytes: 0 } }
    }
  }
  dependsOn: [ toLaw ]
}

resource toLaw 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'to-law'
  scope: apim
  properties: {
    workspaceId: lawId
    logAnalyticsDestinationType: 'Dedicated'
    logs: [ { categoryGroup: 'allLogs', enabled: true } ]
  }
}

// 내부 모드는 DNS를 만들어 주지 않습니다. 게이트웨이 호스트만 사설 IP로 등록합니다.
resource gatewayRecord 'Microsoft.Network/privateDnsZones/A@2024-06-01' = {
  parent: zone
  name: name
  properties: {
    ttl: 300
    aRecords: [ { ipv4Address: apim.properties.privateIPAddresses[0] } ]
  }
}

// ── 백엔드 ──────────────────────────────────────────────────────────────
resource openaiBackend 'Microsoft.ApiManagement/service/backends@2024-05-01' = {
  parent: apim
  name: 'foundry-openai'
  properties: {
    protocol: 'http'
    url: 'https://${aiAccountName}.openai.azure.com/openai'
  }
}

resource decisionBackend 'Microsoft.ApiManagement/service/backends@2024-05-01' = {
  parent: apim
  name: 'foundry-decision'
  properties: {
    protocol: 'http'
    url: 'https://${aiAccountName}.services.ai.azure.com'
  }
}

// llm-content-safety가 부르는 Content Safety. 백엔드 자격 증명의 managedIdentity는 타입 정의에 아직 없습니다.
resource safetyBackend 'Microsoft.ApiManagement/service/backends@2024-05-01' = {
  parent: apim
  name: 'content-safety'
  properties: {
    protocol: 'http'
    url: 'https://${aiAccountName}.cognitiveservices.azure.com'
    credentials: {
      #disable-next-line BCP037
      managedIdentity: { resource: 'https://cognitiveservices.azure.com' }
    }
  }
}

// ── 정책 ────────────────────────────────────────────────────────────────
// 순서: 콘텐츠 안전(프롬프트 차단) → 제품 정책(<base/>: 에이전트별 TPM) → MI 인증 → 토큰 지표.
// 연결이 보내는 구독 키(api-key)는 백엔드로 넘기지 않습니다.
var openaiPolicy = '''
<policies>
  <inbound>
    <llm-content-safety backend-id="content-safety" shield-prompt="true">
      <categories output-type="EightSeverityLevels">
        <category name="Hate" threshold="4" />
        <category name="Sexual" threshold="4" />
        <category name="SelfHarm" threshold="4" />
        <category name="Violence" threshold="4" />
      </categories>
    </llm-content-safety>
    <base />
    <set-header name="api-key" exists-action="delete" />
    <authentication-managed-identity resource="https://cognitiveservices.azure.com" />
    <set-backend-service backend-id="foundry-openai" />
    <llm-emit-token-metric namespace="ama-llm">
      <dimension name="agent" value="@(context.Subscription.Name)" />
      <dimension name="model" value='@(context.Request.MatchedParameters["deployment-id"])' />
      <dimension name="api" value="@(context.Api.Name)" />
    </llm-emit-token-metric>
  </inbound>
  <backend>
    <base />
  </backend>
  <outbound>
    <base />
  </outbound>
  <on-error>
    <base />
  </on-error>
</policies>
'''

// Decision-1: 구독 키 대신 호출자 관리 ID 토큰을 검증하고 호출 수를 제한합니다.
var decisionPolicy = '''
<policies>
  <inbound>
    <base />
    <validate-azure-ad-token tenant-id="__TENANT__" output-token-variable-name="jwt">
      <audiences>
        <audience>https://cognitiveservices.azure.com</audience>
      </audiences>
      <required-claims>
        <claim name="appid" match="any">__APPIDS__</claim>
      </required-claims>
    </validate-azure-ad-token>
    <rate-limit-by-key calls="120" renewal-period="60" counter-key='@(((Jwt)context.Variables["jwt"]).Claims.GetValueOrDefault("appid", "unknown"))' />
    <emit-metric name="decision-calls" namespace="ama-llm" value="1">
      <dimension name="caller" value='@(((Jwt)context.Variables["jwt"]).Claims.GetValueOrDefault("appid", "unknown"))' />
      <dimension name="api" value="@(context.Api.Name)" />
    </emit-metric>
    <authentication-managed-identity resource="https://cognitiveservices.azure.com" />
    <set-backend-service backend-id="foundry-decision" />
  </inbound>
  <backend>
    <base />
  </backend>
  <outbound>
    <base />
  </outbound>
  <on-error>
    <base />
  </on-error>
</policies>
'''

// 에이전트(구독)별 분당 토큰 한도. 카운터는 구독 단위입니다.
var productPolicy = '''
<policies>
  <inbound>
    <base />
    <llm-token-limit counter-key="@(context.Subscription.Id)" tokens-per-minute="__TPM__" estimate-prompt-tokens="false" remaining-tokens-header-name="x-ratelimit-remaining-tokens" />
  </inbound>
  <backend>
    <base />
  </backend>
  <outbound>
    <base />
  </outbound>
  <on-error>
    <base />
  </on-error>
</policies>
'''

var appIdValues = join(map(allowedAppIds, id => '<value>${id}</value>'), '')

// ── API ─────────────────────────────────────────────────────────────────
resource openaiApi 'Microsoft.ApiManagement/service/apis@2024-05-01' = {
  parent: apim
  name: 'openai'
  properties: {
    displayName: 'Foundry OpenAI'
    path: 'openai'
    protocols: [ 'https' ]
    subscriptionRequired: true
    subscriptionKeyParameterNames: { header: 'api-key', query: 'subscription-key' }
  }
}

resource chatOperation 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = {
  parent: openaiApi
  name: 'chat-completions'
  properties: {
    displayName: 'Chat Completions'
    method: 'POST'
    urlTemplate: '/deployments/{deployment-id}/chat/completions'
    templateParameters: [ { name: 'deployment-id', type: 'string', required: true } ]
  }
}

resource openaiApiPolicy 'Microsoft.ApiManagement/service/apis/policies@2024-05-01' = {
  parent: openaiApi
  name: 'policy'
  properties: { format: 'rawxml', value: openaiPolicy }
  dependsOn: [ openaiBackend, safetyBackend ]
}

resource decisionApi 'Microsoft.ApiManagement/service/apis@2024-05-01' = {
  parent: apim
  name: 'decision'
  properties: {
    displayName: 'Foundry Decision-1'
    path: 'decision'
    protocols: [ 'https' ]
    subscriptionRequired: false
  }
}

resource systemoneOperation 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = {
  parent: decisionApi
  name: 'systemone'
  properties: {
    displayName: 'systemone'
    method: 'POST'
    urlTemplate: '/providers/microsoft/v1/systemone'
  }
}

resource decisionApiPolicy 'Microsoft.ApiManagement/service/apis/policies@2024-05-01' = {
  parent: decisionApi
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: replace(replace(decisionPolicy, '__TENANT__', tenant().tenantId), '__APPIDS__', appIdValues)
  }
  dependsOn: [ decisionBackend ]
}

// ── 에이전트별 제품·구독 ────────────────────────────────────────────────
resource products 'Microsoft.ApiManagement/service/products@2024-05-01' = [for g in gateways: {
  parent: apim
  name: g.name
  properties: {
    displayName: g.name
    subscriptionRequired: true
    approvalRequired: false
    state: 'published'
  }
}]

resource productApis 'Microsoft.ApiManagement/service/products/apis@2024-05-01' = [for (g, i) in gateways: {
  parent: products[i]
  name: openaiApi.name
}]

resource productPolicies 'Microsoft.ApiManagement/service/products/policies@2024-05-01' = [for (g, i) in gateways: {
  parent: products[i]
  name: 'policy'
  properties: { format: 'rawxml', value: replace(productPolicy, '__TPM__', string(g.tpm)) }
}]

resource subscriptions 'Microsoft.ApiManagement/service/subscriptions@2024-05-01' = [for (g, i) in gateways: {
  parent: apim
  name: g.name
  properties: {
    displayName: g.name
    scope: products[i].id
    state: 'active'
    allowTracing: false
  }
}]

output name string = apim.name
output gatewayUrl string = 'https://${apim.name}.azure-api.net'
output principalId string = apim.identity.principalId
