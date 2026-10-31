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
