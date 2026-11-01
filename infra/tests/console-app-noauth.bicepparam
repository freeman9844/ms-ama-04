// tests/infra/test_console.py 전용: Easy Auth를 끈 데모 모드의 콘솔 container-app 모듈.
using '../modules/container-app.bicep'

param name = 'ca-console'
param location = 'koreacentral'
param tags = {}
param serviceName = 'console'
param environmentId = '/subscriptions/ffffffff-ffff-ffff-ffff-ffffffffffff/resourceGroups/rg-amatest/providers/Microsoft.App/managedEnvironments/cae-web-amatest'
param identityId = '/subscriptions/ffffffff-ffff-ffff-ffff-ffffffffffff/resourceGroups/rg-amatest/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-console-amatest'
param identityClientId = 'ffffffff-ffff-ffff-ffff-ffffffffffff'
param registryServer = 'acramatest.azurecr.io'
param ipAllowList = ['20.30.40.50/32']
param authClientId = '22222222-2222-2222-2222-222222222222'
param authEnabled = false
