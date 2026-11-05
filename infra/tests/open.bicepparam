// 배포 창 열림: 운영자 IP만 허용
using '../main.bicep'

param environmentName = 'amatest'
param operatorIp = '20.30.40.50'
param acrPublic = false
param operatorPrincipalId = '11111111-1111-1111-1111-111111111111'
param serviceBusEnabled = true
param deployCapabilityHost = true
param consoleIp = '20.30.40.50'
param appGwCertSecretId = 'https://kvamatest.vault.azure.net/secrets/appgw-console'
