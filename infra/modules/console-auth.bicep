// 심사역 콘솔 Entra 앱 등록(Microsoft Graph Bicep 확장). Container Apps 기본 인증(Easy Auth)이 씁니다.
// 비밀 없이 ID 토큰만 받는 암시적 흐름입니다(데모 범위). 운영 전환 시 Key Vault 비밀 또는
// 관리 ID 페더레이션(FIC)을 쓰는 인증 코드 흐름으로 바꿉니다(README P4b).
extension microsoftGraphV1

param uniqueName string
param displayName string
@description('Easy Auth 콜백 URI (https://<앱 FQDN>/.auth/login/aad/callback)')
param redirectUri string
@description('CreditOfficer 역할을 줄 운영자 Entra 객체 ID. 비면 할당하지 않습니다.')
param operatorPrincipalId string = ''

var roles = [
  { value: 'CreditOfficer', name: '심사역', description: '신청 조회·실명 복원·승인/조건부/거절 결정·신규 신청' }
  { value: 'CreditManager', name: '심사 관리자', description: '심사역 권한과 같음(에스컬레이션 처리)' }
  { value: 'Auditor', name: '감사자', description: '가명 상태로 조회만' }
]

resource app 'Microsoft.Graph/applications@v1.0' = {
  uniqueName: uniqueName
  displayName: displayName
  signInAudience: 'AzureADMyOrg'
  web: {
    redirectUris: [ redirectUri ]
    implicitGrantSettings: { enableIdTokenIssuance: true, enableAccessTokenIssuance: false }
  }
  appRoles: [for r in roles: {
    id: guid(uniqueName, r.value)
    value: r.value
    displayName: r.name
    description: r.description
    allowedMemberTypes: [ 'User' ]
    isEnabled: true
  }]
}

// 역할이 할당된 사용자만 로그인할 수 있습니다.
resource sp 'Microsoft.Graph/servicePrincipals@v1.0' = {
  appId: app.appId
  appRoleAssignmentRequired: true
}

resource operatorRole 'Microsoft.Graph/appRoleAssignedTo@v1.0' = if (!empty(operatorPrincipalId)) {
  principalId: operatorPrincipalId
  resourceId: sp.id
  appRoleId: guid(uniqueName, 'CreditOfficer')
}

output clientId string = app.appId
