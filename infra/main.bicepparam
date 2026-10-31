// azd 환경 변수를 파라미터로 옮깁니다. azd env set 은 빈 값을 다루기 어려우므로
// 배포 창이 닫힌 상태는 OPERATOR_IP=closed 로 표현합니다.
using 'main.bicep'

var operatorIpRaw = readEnvironmentVariable('OPERATOR_IP', 'closed')

param environmentName = readEnvironmentVariable('AZURE_ENV_NAME', 'amalend')
param location = readEnvironmentVariable('AZURE_LOCATION', 'koreacentral')
param operatorIp = operatorIpRaw == 'closed' ? '' : operatorIpRaw
param acrPublic = readEnvironmentVariable('ACR_PUBLIC', 'false') == 'true'
param operatorPrincipalId = readEnvironmentVariable('AZURE_PRINCIPAL_ID', '')
param serviceBusEnabled = readEnvironmentVariable('SERVICEBUS_ENABLED', 'true') == 'true'
param deployCapabilityHost = readEnvironmentVariable('DEPLOY_CAPHOST', 'true') == 'true'
param chatModelSku = readEnvironmentVariable('CHAT_MODEL_SKU', 'GlobalStandard')
param decisionModelSku = readEnvironmentVariable('DECISION_MODEL_SKU', 'GlobalStandard')
param mockConsentSecret = readEnvironmentVariable('MOCK_CONSENT_SECRET', '')
param pseudonymKey = readEnvironmentVariable('PSEUDONYM_KEY', '')
param policyImage = readEnvironmentVariable('SERVICE_POLICY_IMAGE_NAME', '')
param mockapiImage = readEnvironmentVariable('SERVICE_MOCKAPI_IMAGE_NAME', '')
