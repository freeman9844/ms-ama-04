// 평상시: 배포 창 닫힘
using '../main.bicep'

param environmentName = 'amatest'
param operatorIp = ''
param acrPublic = false
param operatorPrincipalId = '11111111-1111-1111-1111-111111111111'
param serviceBusEnabled = true
param deployCapabilityHost = true
