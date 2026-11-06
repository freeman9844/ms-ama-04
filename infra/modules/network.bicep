// Spoke VNet 하나와 서브넷 6개. Korea Central에서는 Foundry 샘플 안내대로 class B/C 대역을 씁니다.
param name string
param location string
param tags object

var subnets = [
  { name: 'snet-agents', prefix: '192.168.0.0/24', delegation: 'Microsoft.App/environments', nsg: false }    // Foundry Agent Service 전용(위임, 단독)
  { name: 'snet-pe', prefix: '192.168.1.0/24', delegation: '', nsg: false }       // Private Endpoint
  { name: 'snet-functions', prefix: '192.168.2.0/26', delegation: 'Microsoft.App/environments', nsg: false } // Flex Consumption VNet 통합
  { name: 'snet-apps-int', prefix: '192.168.4.0/24', delegation: 'Microsoft.App/environments', nsg: false }  // internal Container Apps 환경(Policy, Mock API, 콘솔, smoke)
  { name: 'snet-appgw', prefix: '192.168.7.0/24', delegation: 'Microsoft.Network/applicationGateways', nsg: false }    // Application Gateway WAF v2(콘솔 진입점, 전용). 위임은 App Gateway 네트워크 격리가 요구
  { name: 'snet-apim', prefix: '192.168.6.0/27', delegation: '', nsg: true }      // API Management 내부 모드(AI 게이트웨이)
]

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

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: [ '192.168.0.0/16' ] }
    subnets: [for s in subnets: {
      name: s.name
      properties: union({
        addressPrefix: s.prefix
        privateEndpointNetworkPolicies: 'Disabled'
        delegations: empty(s.delegation) ? [] : [
          {
            name: replace(s.delegation, '/', '.')
            properties: { serviceName: s.delegation }
          }
        ]
      }, s.nsg ? { networkSecurityGroup: { id: nsg.id } } : {})
    }]
  }
}

output vnetId string = vnet.id
output vnetName string = vnet.name
output subnetIds object = toObject(subnets, s => s.name, s => resourceId('Microsoft.Network/virtualNetworks/subnets', name, s.name))
