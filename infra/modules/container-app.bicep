// Container Apps 앱. 모두 internal 환경(VNet 전용)에서 돌고, 콘솔은 App Gateway WAF 뒤에서 Entra 기본 인증으로 막습니다.
// image가 비면 MCR Python 자리표시 이미지로 만들고, ops deploy가 ACR 이미지로 갱신합니다
// (azd 환경 SERVICE_<NAME>_IMAGE_NAME으로 재프로비저닝에도 유지).
param name string
param location string
param tags object
param serviceName string
param environmentId string
param identityId string
param identityClientId string
param registryServer string
param image string = ''
@description('[{ name, value }] 일반 환경 변수')
param envVars array = []
@secure()
@description('{ 환경 변수 이름: 비밀 값 }. 값이 빈 항목은 만들지 않습니다.')
param secretEnv object = {}
@description('Entra 앱(클라이언트) ID. 있으면 Easy Auth로 로그인을 강제합니다(ID 토큰, 비밀 없음).')
param authClientId string = ''
@description('false면 authConfigs를 남긴 채 Easy Auth를 끕니다(데모 모드). 증분 배포는 리소스를 지우지 않으므로 끄기로 표현합니다.')
param authEnabled bool = true

var placeholder = empty(image)
var secretItems = filter(items(secretEnv), s => !empty(s.value))

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: name
  location: location
  tags: union(tags, { 'azd-service-name': serviceName })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identityId}': {} }
  }
  properties: {
    environmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      registries: placeholder ? [] : [ { server: registryServer, identity: identityId } ]
      // 값은 @secure() secretEnv에서 오지만 filter() 때문에 린터가 추적하지 못합니다.
      #disable-next-line use-secure-value-for-secure-inputs
      secrets: [for s in secretItems: { name: toLower(replace(s.key, '_', '-')), value: s.value }]
    }
    template: {
      containers: [
        {
          name: serviceName
          image: placeholder ? 'mcr.microsoft.com/azurelinux/base/python:3.12' : image
          command: placeholder ? [ 'python3', '-m', 'http.server', '8000' ] : null
          env: concat(
            [ { name: 'AZURE_CLIENT_ID', value: identityClientId } ],
            envVars,
            map(secretItems, s => { name: s.key, secretRef: toLower(replace(s.key, '_', '-')) })
          )
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
}

// 로그인하지 않은 요청은 Entra 로그인으로 보냅니다. 앱은 X-MS-CLIENT-PRINCIPAL 헤더의 역할만 믿습니다.
resource auth 'Microsoft.App/containerApps/authConfigs@2024-03-01' = if (!empty(authClientId)) {
  parent: app
  name: 'current'
  properties: {
    platform: { enabled: authEnabled }
    globalValidation: {
      unauthenticatedClientAction: 'RedirectToLoginPage'
      redirectToProvider: 'azureactivedirectory'
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: authClientId
          openIdIssuer: '${environment().authentication.loginEndpoint}${tenant().tenantId}/v2.0'
        }
        validation: { allowedAudiences: [ authClientId ] }
      }
    }
    // App Gateway 뒤: 원래 호스트·스킴 헤더로 로그인 리디렉션 URL을 만듭니다.
    httpSettings: {
      forwardProxy: {
        convention: 'Custom'
        customHostHeaderName: 'X-Original-Host'
        customProtoHeaderName: 'X-Forwarded-Proto'
      }
    }
    login: { preserveUrlFragmentsForLogins: false }
  }
}

output name string = app.name
