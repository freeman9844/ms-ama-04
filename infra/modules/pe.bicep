// 대상 리소스 하나의 하위 서비스(groupId) 하나에 대한 Private Endpoint와 DNS zone group.
param name string
param location string
param tags object
param subnetId string
param targetId string
param groupId string
@description('이 PE의 A 레코드를 등록할 Private DNS zone ID 목록')
param zoneIds array

resource pe 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    subnet: { id: subnetId }
    privateLinkServiceConnections: [
      {
        name: name
        properties: {
          privateLinkServiceId: targetId
          groupIds: [ groupId ]
        }
      }
    ]
  }

  resource zoneGroup 'privateDnsZoneGroups@2024-05-01' = {
    name: 'default'
    properties: {
      privateDnsZoneConfigs: [for (z, i) in zoneIds: {
        name: 'zone${i}'
        properties: { privateDnsZoneId: z }
      }]
    }
  }
}

output name string = pe.name
