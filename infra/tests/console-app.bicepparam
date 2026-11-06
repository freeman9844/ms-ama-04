// tests/infra/test_console.py 전용: 콘솔용 container-app 모듈만 펼쳐 Easy Auth 설정을 확인합니다.
// (main.bicep을 펼치면 Graph 앱의 appId를 알 수 없어 authConfigs가 빠집니다.)
using '../modules/container-app.bicep'

param name = 'ca-console'
param location = 'koreacentral'
param tags = {}
param serviceName = 'console'
param environmentId = '/subscriptions/ffffffff-ffff-ffff-ffff-ffffffffffff/resourceGroups/rg-amatest/providers/Microsoft.App/managedEnvironments/cae-int-amatest'
param identityId = '/subscriptions/ffffffff-ffff-ffff-ffff-ffffffffffff/resourceGroups/rg-amatest/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-console-amatest'
param identityClientId = 'ffffffff-ffff-ffff-ffff-ffffffffffff'
param registryServer = 'acramatest.azurecr.io'
param authClientId = '22222222-2222-2222-2222-222222222222'
