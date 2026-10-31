// Private DNS zone과 VNet 링크. 출력은 [{ name, id }] 배열입니다.
param zoneNames array
param vnetId string
param tags object

resource zones 'Microsoft.Network/privateDnsZones@2024-06-01' = [for z in zoneNames: {
  name: z
  location: 'global'
  tags: tags
}]

resource links 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = [for (z, i) in zoneNames: {
  parent: zones[i]
  name: 'link-vnet'
  location: 'global'
  tags: tags
  properties: {
    registrationEnabled: false
    virtualNetwork: { id: vnetId }
  }
}]

output zones array = [for (z, i) in zoneNames: { name: z, id: zones[i].id }]
