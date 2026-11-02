// 평가 하네스 수동 트리거 Job(내부 ACA 환경, VNet 안에서 Foundry Private Endpoint로 호출).
// image가 비면 MCR Python 자리표시 이미지로 만들고, ops deploy eval이 ACR 이미지로 갱신합니다.
param name string
param location string
param tags object
param environmentId string
param identityId string
param identityClientId string
param registryServer string
param image string = ''
@description('[{ name, value }] 일반 환경 변수')
param envVars array = []

var placeholder = empty(image)

resource job 'Microsoft.App/jobs@2024-03-01' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identityId}': {} }
  }
  properties: {
    environmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 3600
      replicaRetryLimit: 0
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: placeholder ? [] : [ { server: registryServer, identity: identityId } ]
    }
    template: {
      containers: [
        {
          name: 'eval'
          image: placeholder ? 'mcr.microsoft.com/azurelinux/base/python:3.12' : image
          command: placeholder ? [ 'python3', '-c', 'print("eval image not deployed: python -m ops deploy eval")' ] : null
          env: concat([
            { name: 'AZURE_CLIENT_ID', value: identityClientId }
            { name: 'PYTHONUNBUFFERED', value: '1' }
          ], envVars)
          resources: {
            cpu: json('1')
            memory: '2Gi'
          }
        }
      ]
    }
  }
}

output name string = job.name
