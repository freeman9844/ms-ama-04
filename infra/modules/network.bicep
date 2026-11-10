// Spoke VNet 하나와 서브넷 6개. Korea Central에서는 Foundry 샘플 안내대로 class B/C 대역을 씁니다.
param name string
param location string
param tags object

var subnets = [
  { name: 'snet-agents', prefix: '192.168.0.0/24', delegation: 'Microsoft.App/environments', nsg: 'nsg-agents' }    // Foundry Agent Service 전용(위임, 단독)
  { name: 'snet-pe', prefix: '192.168.1.0/24', delegation: '', nsg: 'nsg-pe' }       // Private Endpoint
  { name: 'snet-functions', prefix: '192.168.2.0/26', delegation: 'Microsoft.App/environments', nsg: 'nsg-functions' } // Flex Consumption VNet 통합
  { name: 'snet-apps-int', prefix: '192.168.4.0/24', delegation: 'Microsoft.App/environments', nsg: 'nsg-apps-int' }  // internal Container Apps 환경(Policy, Mock API, 콘솔, smoke)
  { name: 'snet-appgw', prefix: '192.168.7.0/24', delegation: 'Microsoft.Network/applicationGateways', nsg: 'nsg-appgw' }    // Application Gateway WAF v2(콘솔 진입점, 전용). 위임은 App Gateway 네트워크 격리가 요구
  { name: 'snet-apim', prefix: '192.168.6.0/27', delegation: '', nsg: 'nsg-apim' }      // API Management 내부 모드(AI 게이트웨이)
]

// 내부 서브넷 NSG. 테넌트 정책이 NSG 없는 서브넷에 빈 NSG를 붙이지만 Bicep이 소유하지 않으면 프로비저닝마다 연결이 풀려
// "NSG 없는 서브넷" 경고가 뜹니다. 그래서 직접 둡니다. 규칙은 Azure 기본 규칙(VNet·AzureLoadBalancer 인바운드 허용,
// 인터넷 인바운드 거부, 아웃바운드 허용)만 써서 정책 NSG와 동작이 같습니다. 인터넷 인바운드는 App Gateway 서브넷만 받습니다.
var internalNsgs = [ 'nsg-agents', 'nsg-pe', 'nsg-functions', 'nsg-apps-int' ]

resource nsgInternal 'Microsoft.Network/networkSecurityGroups@2024-05-01' = [for n in internalNsgs: {
  name: n
  location: location
  tags: tags
  properties: { securityRules: [] }
}]

// APIM VNet 주입 필수 규칙만 둡니다. 인터넷 인바운드는 열지 않습니다(내부 모드).
resource nsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: 'nsg-apim'
  location: location
  tags: tags
  properties: {
    securityRules: [
      {
        name: 'allow-apim-management'
        properties: { priority: 100, direction: 'Inbound', access: 'Allow', protocol: 'Tcp', sourceAddressPrefix: 'ApiManagement', sourcePortRange: '*', destinationAddressPrefix: 'VirtualNetwork', destinationPortRange: '3443' }
      }
      {
        name: 'allow-lb-6390'
        properties: { priority: 110, direction: 'Inbound', access: 'Allow', protocol: 'Tcp', sourceAddressPrefix: 'AzureLoadBalancer', sourcePortRange: '*', destinationAddressPrefix: 'VirtualNetwork', destinationPortRange: '6390' }
      }
      {
        name: 'allow-vnet-https'
        properties: { priority: 120, direction: 'Inbound', access: 'Allow', protocol: 'Tcp', sourceAddressPrefix: 'VirtualNetwork', sourcePortRange: '*', destinationAddressPrefix: 'VirtualNetwork', destinationPortRange: '443' }
      }
      {
        name: 'allow-storage-out'
        properties: { priority: 100, direction: 'Outbound', access: 'Allow', protocol: 'Tcp', sourceAddressPrefix: 'VirtualNetwork', sourcePortRange: '*', destinationAddressPrefix: 'Storage', destinationPortRange: '443' }
      }
      {
        name: 'allow-sql-out'
        properties: { priority: 110, direction: 'Outbound', access: 'Allow', protocol: 'Tcp', sourceAddressPrefix: 'VirtualNetwork', sourcePortRange: '*', destinationAddressPrefix: 'Sql', destinationPortRange: '1433' }
      }
      {
        name: 'allow-keyvault-out'
        properties: { priority: 120, direction: 'Outbound', access: 'Allow', protocol: 'Tcp', sourceAddressPrefix: 'VirtualNetwork', sourcePortRange: '*', destinationAddressPrefix: 'AzureKeyVault', destinationPortRange: '443' }
      }
      {
        name: 'allow-monitor-out'
        properties: { priority: 130, direction: 'Outbound', access: 'Allow', protocol: 'Tcp', sourceAddressPrefix: 'VirtualNetwork', sourcePortRange: '*', destinationAddressPrefix: 'AzureMonitor', destinationPortRanges: [ '1886', '443' ] }
      }
    ]
  }
}

// 테넌트 정책이 NSG 없는 서브넷에 기본 거부 NSG를 붙이므로 직접 소유합니다. IP 허용 목록은 NSG가 아니라 WAF 사용자 지정 규칙이 적용합니다.
resource nsgAppGw 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: 'nsg-appgw'
  location: location
  tags: tags
  properties: {
    securityRules: [
      {
        name: 'allow-gateway-manager'
        properties: { priority: 100, direction: 'Inbound', access: 'Allow', protocol: 'Tcp', sourceAddressPrefix: 'GatewayManager', sourcePortRange: '*', destinationAddressPrefix: '*', destinationPortRange: '65200-65535' }
      }
      {
        name: 'allow-azure-lb'
        properties: { priority: 110, direction: 'Inbound', access: 'Allow', protocol: '*', sourceAddressPrefix: 'AzureLoadBalancer', sourcePortRange: '*', destinationAddressPrefix: '*', destinationPortRange: '*' }
      }
      {
        name: 'allow-internet-web'
        properties: { priority: 120, direction: 'Inbound', access: 'Allow', protocol: 'Tcp', sourceAddressPrefix: 'Internet', sourcePortRange: '*', destinationAddressPrefix: '*', destinationPortRanges: [ '80', '443' ] }
      }
    ]
  }
}

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: name
  location: location
  tags: tags
  dependsOn: [ nsg, nsgAppGw, nsgInternal ]
  properties: {
    addressSpace: { addressPrefixes: [ '192.168.0.0/16' ] }
    subnets: [for s in subnets: {
      name: s.name
      properties: {
        addressPrefix: s.prefix
        privateEndpointNetworkPolicies: 'Disabled'
        delegations: empty(s.delegation) ? [] : [
          {
            name: replace(s.delegation, '/', '.')
            properties: { serviceName: s.delegation }
          }
        ]
        networkSecurityGroup: { id: resourceId('Microsoft.Network/networkSecurityGroups', s.nsg) }
      }
    }]
  }
}

output vnetId string = vnet.id
output vnetName string = vnet.name
output subnetIds object = toObject(subnets, s => s.name, s => resourceId('Microsoft.Network/virtualNetworks/subnets', name, s.name))
