// 변형: pause(Service Bus 없음) + ACR 빌드 창 + caphost 생략(재배포) + Data Zone SKU + 운영자 없음
using '../main.bicep'

param environmentName = 'amatest'
param operatorIp = ''
param acrPublic = true
param operatorPrincipalId = ''
param serviceBusEnabled = false
param deployCapabilityHost = false
param chatModelSku = 'DataZoneStandard'
param decisionModelSku = 'DataZoneStandard'
param policyImage = 'acramatest.azurecr.io/policy:v1'
param mockapiImage = 'acramatest.azurecr.io/mockapi:v1'
param consoleImage = 'acramatest.azurecr.io/console:v1'
