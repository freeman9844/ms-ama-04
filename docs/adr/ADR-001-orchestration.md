# ADR-001: 오케스트레이션 접근안 — Durable Functions + Foundry 에이전트

## 상태
채택(P1~P2, 2026-10-31~11-02) · as-built

## 맥락
신용 대출 심사는 **감사 가능성**(모든 단계·근거가 남아야 함)과 **재개 가능성**(며칠 단위 심사역 대기 중에도 흐름이 보존되어야 함)이 핵심이다. 과제 요구는 "결정론적 정책 엔진 + 심사역 승인"이므로, 자율 판단은 쓰되 흐름 자체는 결정론적이어야 한다. 상위 Spec §2.1(접근안 선택)은 세 접근안을 비교해 **A안**을 채택했다(`docs/superpowers/specs/2026-10-31-agentic-cashflow-lending-design.md` §2.1).

상위 Spec §2.1 원문의 결정:

> **A. Durable 오케스트레이터 + Foundry 에이전트 + 이벤트 기반 L4 — 채택.** 신청 건별 진행 흐름은 Durable Functions 상태 그래프로 관리하고, 각 단계 안에서 Foundry 에이전트가 도구를 써서 자율 판단하며, L4 에이전트는 이벤트에 반응한다.

## 결정
신청 1건의 흐름은 Durable Functions 오케스트레이터(`application_journey`)가 결정론적 상태 그래프로 관리하고, 단계 안에서만 Foundry 에이전트가 자율 판단한다. 호스팅은 Azure Functions **Flex Consumption**(`sku: { name: 'FC1', tier: 'FlexConsumption' }`, `maximumInstanceCount: 40`)이고, Durable Task 상태 백엔드는 기본 **Azure Storage** 공급자다(`src/orchestrator/host.json`의 `durableTask.hubName: "lending"`, 사용자 지정 `storageProvider` 없음 → Storage 기본). 심사역 대기는 `wait_for_external_event`(`src/orchestrator/journey/orchestration.py:249`)와 SLA durable timer(`create_timer`, `:252`)로, 동의 만료는 `consent_lifecycle` 오케스트레이션의 만료 타이머(`src/orchestrator/journey/consent.py`)로 구현했다. 구현 위치: `src/orchestrator/function_app.py`, `src/orchestrator/journey/orchestration.py`, `infra/modules/functions.bicep`.

## 검토한 대안
| 대안 | 장점 | 기각 이유 |
|---|---|---|
| B. Foundry 네이티브 Workflows | 선언형 멀티 에이전트, 코드가 가장 적음 | 며칠 단위 HITL 대기·결정론적 규칙 엔진 연동·테스트/평가 제어가 약하고, 사설 네트워크에서 일부 기능이 preview 위험(상위 Spec §2.1) |
| C. Microsoft Agent Framework on Container Apps | 코드로 그래프·handoff 표현 | 체크포인트 저장·장기 대기·만료 타이머를 직접 구현해야 하고 프레임워크 성숙도 위험(상위 Spec §2.1) |
| Logic Apps 기반 워크플로 | 관리형 커넥터·시각적 흐름 | 에이전트 도구 호출·반성 루프·계약 검증 같은 코드 중심 로직과 테스트 제어가 맞지 않음 |

## 결과
- 장점: 흐름이 결정론적이라 감사·재개가 보장되고, `wait_for_external_event`·durable timer가 HITL 대기·동의 만료 요구에 바로 대응한다. 자율성은 에이전트 단계 안으로 격리된다.
- 대가: 서킷 브레이커 상태가 Flex 인스턴스(프로세스) 단위라 여러 인스턴스로 확장되면 전역 open이 보장되지 않는다(`src/orchestrator/journey/circuit.py`, 실측 `ops/reports/load-20261102110233/report.md` 4장 policy-down: 신청별 half-open 호출 수 상이). Durable 실행 기록이 남는다(동의 ID·상태만, 개인정보 없음).

## 재검토 조건
- Flex가 다중 인스턴스로 상시 확장되어 분산 서킷(공유 상태)이 필요할 때.
- Durable 상태 백엔드를 Netherite/MSSQL 등 고처리량 공급자로 바꿔야 할 처리량에 도달할 때.
- Foundry Workflows가 장기 대기·사설 네트워크를 GA로 지원해 흐름 제어를 위임할 수 있을 때.

## 근거
- 구현: `src/orchestrator/journey/orchestration.py:249`(`wait_for_external_event`)·`:252`(`create_timer`), `src/orchestrator/journey/consent.py`, `src/orchestrator/host.json`, `infra/modules/functions.bicep`.
- 상위 Spec: `docs/superpowers/specs/2026-10-31-agentic-cashflow-lending-design.md` §2.1.
- 아키텍처 배경·장애 도메인: [../architecture.md](../architecture.md).
- 서킷 인스턴스별 실측: `ops/reports/load-20261102110233/report.md` 4장, [../runbook/deploy-history.md](../runbook/deploy-history.md).
