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
// 실제 mockapi 이미지에는 동의 서명 비밀이 필요합니다(require-mock-consent-secret).
param mockConsentSecret = 'test-mock-consent-secret-0123456789abcdef'
param consoleImage = 'acramatest.azurecr.io/console:v1'
param evalImage = 'acramatest.azurecr.io/eval:v1'
param aiGatewayRouting = false
