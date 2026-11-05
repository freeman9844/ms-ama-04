// 콘솔 진입점: 공용 IP → Application Gateway WAF v2(Prevention, OWASP DRS 2.1, 운영자 IP만) → 내부 ACA 콘솔(HTTPS).
// 인증서 비밀 ID가 없으면(첫 프로비저닝) 공용 IP만 만듭니다. ops가 그 DNS 이름으로 인증서를 Key Vault에 쓴 뒤 다시 프로비저닝합니다.
param name string
param policyName string
param pipName string
param dnsLabel string
param location string
param tags object
param subnetId string
param identityId string
@description('Key Vault 비밀 ID(버전 없음, 4시간마다 새 버전을 가져감). 비면 WAF 정책·게이트웨이를 만들지 않습니다.')
#disable-next-line secure-secrets-in-params // 비밀 값이 아니라 Key Vault 비밀의 URI입니다.
param certSecretId string
param backendFqdn string
@description('운영자 공인 IPv4. 비면 TEST-NET 주소만 허용해 아무도 들어오지 못합니다(fail-closed).')
param allowedIp string
param lawId string

var deployGateway = !empty(certSecretId)

resource pip 'Microsoft.Network/publicIPAddresses@2024-05-01' = {
  name: pipName
  location: location
  tags: tags
  sku: { name: 'Standard', tier: 'Regional' }
  properties: {
    publicIPAllocationMethod: 'Static'
    publicIPAddressVersion: 'IPv4'
    dnsSettings: { domainNameLabel: dnsLabel }
  }
}

resource policy 'Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies@2024-05-01' = if (deployGateway) {
  name: policyName
  location: location
  tags: tags
  properties: {
    policySettings: {
      state: 'Enabled'
      mode: 'Prevention'
      requestBodyCheck: true
    }
    customRules: [
      {
        // 운영자 IP가 아니면 차단합니다(관리 규칙보다 먼저 평가).
        name: 'allowOperatorOnly'
        priority: 10
        ruleType: 'MatchRule'
        action: 'Block'
        state: 'Enabled'
        matchConditions: [
          {
            matchVariables: [ { variableName: 'RemoteAddr' } ]
            operator: 'IPMatch'
            negationConditon: true // API 철자 그대로(오타가 정식 이름)
            matchValues: [ empty(allowedIp) ? '192.0.2.0/32' : '${allowedIp}/32' ]
          }
        ]
      }
    ]
    managedRules: {
      managedRuleSets: [ { ruleSetType: 'Microsoft_DefaultRuleSet', ruleSetVersion: '2.1' } ]
    }
  }
}

var gatewayType = 'Microsoft.Network/applicationGateways'

resource gateway 'Microsoft.Network/applicationGateways@2024-05-01' = if (deployGateway) {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identityId}': {} }
  }
  properties: {
    sku: { name: 'WAF_v2', tier: 'WAF_v2' }
    autoscaleConfiguration: { minCapacity: 0, maxCapacity: 2 }
    firewallPolicy: { id: resourceId('Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies', policyName) }
    sslPolicy: { policyType: 'Predefined', policyName: 'AppGwSslPolicy20220101' }
    gatewayIPConfigurations: [ { name: 'gateway-ip', properties: { subnet: { id: subnetId } } } ]
    frontendIPConfigurations: [ { name: 'public', properties: { publicIPAddress: { id: pip.id } } } ]
    frontendPorts: [
      { name: 'port-443', properties: { port: 443 } }
      { name: 'port-80', properties: { port: 80 } }
    ]
    sslCertificates: [ { name: 'console-tls', properties: { keyVaultSecretId: certSecretId } } ]
    backendAddressPools: [ { name: 'console', properties: { backendAddresses: [ { fqdn: backendFqdn } ] } } ]
    probes: [
      {
        name: 'console-health'
        properties: {
          protocol: 'Https'
          path: '/healthz'
          interval: 30
          timeout: 30
          unhealthyThreshold: 3
          pickHostNameFromBackendHttpSettings: true
          match: { statusCodes: [ '200-399' ] }
        }
      }
    ]
    backendHttpSettingsCollection: [
      {
        name: 'console-https'
        properties: {
          port: 443
          protocol: 'Https'
          cookieBasedAffinity: 'Disabled'
          pickHostNameFromBackendAddress: true
          requestTimeout: 60
          probe: { id: resourceId('${gatewayType}/probes', name, 'console-health') }
        }
      }
    ]
    httpListeners: [
      {
        name: 'https'
        properties: {
          frontendIPConfiguration: { id: resourceId('${gatewayType}/frontendIPConfigurations', name, 'public') }
          frontendPort: { id: resourceId('${gatewayType}/frontendPorts', name, 'port-443') }
          protocol: 'Https'
          sslCertificate: { id: resourceId('${gatewayType}/sslCertificates', name, 'console-tls') }
        }
      }
      {
        name: 'http'
        properties: {
          frontendIPConfiguration: { id: resourceId('${gatewayType}/frontendIPConfigurations', name, 'public') }
          frontendPort: { id: resourceId('${gatewayType}/frontendPorts', name, 'port-80') }
          protocol: 'Http'
        }
      }
    ]
    redirectConfigurations: [
      {
        name: 'http-to-https'
        properties: {
          redirectType: 'Permanent'
          targetListener: { id: resourceId('${gatewayType}/httpListeners', name, 'https') }
          includePath: true
          includeQueryString: true
        }
      }
    ]
    requestRoutingRules: [
      {
        name: 'https-console'
        properties: {
          ruleType: 'Basic'
          priority: 100
          httpListener: { id: resourceId('${gatewayType}/httpListeners', name, 'https') }
          backendAddressPool: { id: resourceId('${gatewayType}/backendAddressPools', name, 'console') }
          backendHttpSettings: { id: resourceId('${gatewayType}/backendHttpSettingsCollection', name, 'console-https') }
        }
      }
      {
        name: 'http-redirect'
        properties: {
          ruleType: 'Basic'
          priority: 200
          httpListener: { id: resourceId('${gatewayType}/httpListeners', name, 'http') }
          redirectConfiguration: { id: resourceId('${gatewayType}/redirectConfigurations', name, 'http-to-https') }
        }
      }
    ]
  }
  dependsOn: [ policy ]
}

resource toLaw 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = if (deployGateway) {
  name: 'to-law'
  scope: gateway
  properties: {
    workspaceId: lawId
    logAnalyticsDestinationType: 'Dedicated'
    logs: [
      { category: 'ApplicationGatewayAccessLog', enabled: true }
      { category: 'ApplicationGatewayFirewallLog', enabled: true }
    ]
    metrics: [ { category: 'AllMetrics', enabled: true } ]
  }
}
