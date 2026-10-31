// Spoke VNet 하나와 서브넷 5개. Korea Central에서는 Foundry 샘플 안내대로 class B/C 대역을 씁니다.
param name string
param location string
param tags object

var subnets = [
  { name: 'snet-agents', prefix: '192.168.0.0/24', delegated: true }    // Foundry Agent Service 전용(위임, 단독)
  { name: 'snet-pe', prefix: '192.168.1.0/24', delegated: false }       // Private Endpoint
  { name: 'snet-functions', prefix: '192.168.2.0/26', delegated: true } // Flex Consumption VNet 통합
  { name: 'snet-apps-int', prefix: '192.168.4.0/24', delegated: true }  // internal Container Apps 환경(Policy, Mock API, smoke)
  { name: 'snet-apps-web', prefix: '192.168.5.0/24', delegated: true }  // external Container Apps 환경(콘솔만)
]

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: [ '192.168.0.0/16' ] }
    subnets: [for s in subnets: {
      name: s.name
      properties: {
        addressPrefix: s.prefix
        privateEndpointNetworkPolicies: 'Disabled'
        delegations: s.delegated ? [
          {
            name: 'Microsoft.App.environments'
            properties: { serviceName: 'Microsoft.App/environments' }
          }
        ] : []
      }
    }]
  }
}

output vnetId string = vnet.id
output vnetName string = vnet.name
output subnetIds object = toObject(subnets, s => s.name, s => resourceId('Microsoft.Network/virtualNetworks/subnets', name, s.name))
