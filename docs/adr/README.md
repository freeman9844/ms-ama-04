# 아키텍처 결정 기록(ADR)

이 디렉터리는 "Agentic Cash-Flow Lending" 데모의 as-built 아키텍처 결정을 정리한다. 각 ADR은 맥락·대안·트레이드오프·재검토 조건을 담으며, 수치는 기존 평가/운영 보고서와 [배포 기록](../runbook/deploy-history.md)에서만 인용한다. 코드가 상위 Spec과 다르면 ADR이 그 차이를 명시한다.

| 번호 | 제목 | 상태 | 한 줄 결정 | 관련 문서 |
|---|---|---|---|---|
| [ADR-001](ADR-001-orchestration.md) | 오케스트레이션 접근안 | 채택 · as-built | 흐름은 Durable Functions(Flex, Storage 백엔드)로 결정론적, 자율성은 Foundry 에이전트 단계 안으로 | [architecture](../architecture.md), [agents](../agents.md) |
| [ADR-002](ADR-002-model-deployment.md) | 모델 배포 유형 | 채택 · as-built | Global Standard + 전송 전 가명화, SKU는 Bicep 파라미터로 전환 가능 | [architecture](../architecture.md), [troubleshooting](../runbook/troubleshooting.md) |
| [ADR-003](ADR-003-deterministic-policy.md) | 결정적 Policy Engine | 채택 · as-built | 신용 규칙은 버전드 YAML 순수 함수가 판정, LLM은 추천만, 실패 시 fail-closed | [architecture](../architecture.md) |
| [ADR-004](ADR-004-private-network-deploy-window.md) | 완전 사설망 + 배포 창 | 채택 · as-built | 평상시 PE 전용, 운영자 작업만 배포 창으로 IP 하나를 잠시 허용 | [deploy](../runbook/deploy.md), [deploy-history](../runbook/deploy-history.md) |
| [ADR-005](ADR-005-classification-model.md) | 거래 분류 모델 | 채택 · as-built | 규칙 선분류 + Microsoft-Decision-1 + mini 폴백(단가 미확인) | [agents](../agents.md) |
| [ADR-006](ADR-006-two-container-apps-environments.md) | Container Apps 환경 2개 | 대체됨(ADR-011) | internal `cae-int` 하나로 통합, 콘솔 진입은 App Gateway WAF([ADR-011](ADR-011-appgw-waf-entry.md)) | [architecture](../architecture.md) |
| [ADR-007](ADR-007-demo-auth-mode.md) | 데모 인증 모드 | 채택 · as-built | Easy Auth off + 고정 역할 + IP 허용만, 전환 시 Entra 복원 | [architecture](../architecture.md), [deploy-history](../runbook/deploy-history.md) |
| [ADR-008](ADR-008-vnet-jobs-for-eval-ops.md) | 평가·운영 VNet Job | 채택 · as-built | 사설 데이터 접근은 VNet 안 Container Apps Job, 결과는 로그 조각으로 회수 | [deploy](../runbook/deploy.md) |
| [ADR-009](ADR-009-deletion-vs-explainability.md) | 삭제 vs 설명 가능성 | 채택 · as-built | 원천 데이터 삭제·증명 + 판단 기록(파생값·근거 ID) 보존 | [deploy-history](../runbook/deploy-history.md), [architecture](../architecture.md) |
| [ADR-010](ADR-010-ai-gateway.md) | AI 게이트웨이 | 채택 · as-built | APIM Developer 내부 모드, 에이전트별 연결·TPM·콘텐츠 안전, 플래그로 롤백 | [architecture](../architecture.md), [deploy-history](../runbook/deploy-history.md) |
| [ADR-011](ADR-011-appgw-waf-entry.md) | 콘솔 진입점 App Gateway WAF v2 | 채택 · as-built | 공용 IP → WAF_v2(Prevention, DRS 2.1, 운영자 IP만) → 내부 콘솔, 자체 서명 인증서는 ops가 Key Vault에 | [architecture](../architecture.md), [threat-model](../security/threat-model.md) |
