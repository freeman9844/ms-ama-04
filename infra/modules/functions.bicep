// Flex Consumption Function App(Linux, Python 3.12). 아웃바운드는 snet-functions VNet 통합,
// 인바운드는 Private Endpoint. 코드 배포(azd deploy)는 배포 창에서 운영자 IP로만 합니다.
param planName string
param name string
param location string
param tags object
param operatorIp string
param identityId string
param identityClientId string
param subnetId string
param peSubnetId string
param zoneId string
param storageName string
param storageBlobEndpoint string
param appInsightsConnectionString string
@description('{ 이름: 값 } 앱 런타임 설정(P3)')
param extraSettings object = {}

var windowOpen = !empty(operatorIp)

resource plan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: planName
  location: location
  tags: tags
  kind: 'functionapp'
  sku: { name: 'FC1', tier: 'FlexConsumption' }
  properties: { reserved: true }
}

resource site 'Microsoft.Web/sites@2024-04-01' = {
  name: name
  location: location
  tags: union(tags, { 'azd-service-name': 'orchestrator' })
  kind: 'functionapp,linux'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identityId}': {} }
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    virtualNetworkSubnetId: subnetId
    publicNetworkAccess: windowOpen ? 'Enabled' : 'Disabled'
    siteConfig: {
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      ipSecurityRestrictionsDefaultAction: 'Deny'
      ipSecurityRestrictions: windowOpen ? [
        { name: 'operator', ipAddress: '${operatorIp}/32', action: 'Allow', priority: 100 }
      ] : []
      scmIpSecurityRestrictionsUseMain: true
      appSettings: concat([
        { name: 'AzureWebJobsStorage__accountName', value: storageName }
        { name: 'AzureWebJobsStorage__credential', value: 'managedidentity' }
        { name: 'AzureWebJobsStorage__clientId', value: identityClientId }
        { name: 'AZURE_CLIENT_ID', value: identityClientId }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
        { name: 'APPLICATIONINSIGHTS_AUTHENTICATION_STRING', value: 'ClientId=${identityClientId};Authorization=AAD' }
      ], map(items(extraSettings), s => { name: s.key, value: s.value }))
    }
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${storageBlobEndpoint}deploymentpackage'
          authentication: {
            type: 'UserAssignedIdentity'
            userAssignedIdentityResourceId: identityId
          }
        }
      }
      scaleAndConcurrency: {
        maximumInstanceCount: 40
        instanceMemoryMB: 2048
      }
      runtime: { name: 'python', version: '3.12' }
    }
  }
}

module pe 'pe.bicep' = {
  name: 'pe-${name}'
  params: {
    name: 'pe-${name}-sites'
    location: location
    tags: tags
    subnetId: peSubnetId
    targetId: site.id
    groupId: 'sites'
    zoneIds: [ zoneId ]
  }
}

output name string = site.name
