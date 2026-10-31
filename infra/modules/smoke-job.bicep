// VNet 안(internal Container Apps 환경)에서 smoke 점검을 실행하는 수동 트리거 Job.
// 이미지 빌드 없이 MCR Python 이미지에서 src/ops/smoke_checks.py를 인라인 실행합니다.
param name string
param location string
param tags object
param environmentId string
param identityId string
param identityClientId string
param config object

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
      replicaTimeout: 1800
      replicaRetryLimit: 0
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
    }
    template: {
      containers: [
        {
          name: 'smoke'
          image: 'mcr.microsoft.com/azurelinux/base/python:3.12'
          command: [ 'python3', '-c', loadTextContent('../../src/ops/smoke_checks.py') ]
          env: [
            { name: 'AZURE_CLIENT_ID', value: identityClientId }
            { name: 'SMOKE_CONFIG', value: string(config) }
            { name: 'PYTHONUNBUFFERED', value: '1' }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
    }
  }
}

output name string = job.name
