# ADR-007: 데모 인증 모드(Easy Auth off + 고정 역할)

## 상태
채택(P4b, 2026-10-31~11-02) · as-built

## 맥락
상위 Spec §2.2·§4.2는 콘솔에 Entra ID 인증(Easy Auth)과 운영자 IP 허용을 둘 다 적용하고, 앱 역할(`CreditOfficer`·`CreditManager`·`Auditor`)로 권한을 나눈다(`docs/superpowers/specs/2026-10-31-agentic-cashflow-lending-design.md` §4.2). 그러나 데모에서는 참여자 Entra 계정 준비·테넌트 관리자 동의·역할 할당 부담이 크고, 평가자가 로그인 없이 화면을 봐야 한다.

## 결정
데모에서는 `CONSOLE_AUTH_ENABLED=false`(`infra/main.bicep` `consoleAuthEnabled`)로 Easy Auth를 끈다. 이때 Container App에 `CONSOLE_AUTH_MODE=demo`와 `CONSOLE_DEMO_ROLE`(기본 `CreditOfficer`) 환경 변수를 주입하고(`infra/resources.bicep:609`), 콘솔은 이 고정 역할의 주체로 동작한다(`src/console/adapters.py`). 외부 접근은 **영구 운영자 IP 허용목록만**으로 막는다(`infra/resources.bicep:599`, `infra/modules/container-app.bicep:42-46`). Entra 앱 등록(`infra/modules/console-auth.bicep`, 역할 정의 포함)은 그대로 둬, 전환 시 `CONSOLE_AUTH_ENABLED=true`로 복원한다. 데모 모드에서 Easy Auth가 꺼져 운영자 IP에서 바로 열려야 함은 `src/ops/console_gate.py`가 검증한다.

## 검토한 대안
| 대안 | 장점 | 기각 이유 |
|---|---|---|
| Entra Easy Auth 상시(Spec 원안) | 사용자 식별·역할 분리 | 데모 참여자 계정·테넌트 동의·역할 할당 부담. 평가자 로그인 마찰 |
| 인증 완전 제거 | 가장 단순 | IP 허용마저 없으면 외부 노출. 전환 경로(앱 등록)도 사라짐 |
| 공유 비밀번호 폼 인증 | 로그인 흉내 | 역할 클레임·감사 식별이 없고 Easy Auth로 되돌릴 자산이 안 남음 |

## 결과
- 장점: 평가자가 계정 없이 운영자 IP에서 바로 콘솔을 쓴다. 전환은 환경 변수 한 개(`CONSOLE_AUTH_ENABLED=true`)와 기존 앱 등록으로 끝난다.
- 대가: 데모 모드에는 사용자 식별·역할 분리가 없다(모두 고정 역할). 위협 모델 잔여 위험으로, 실데이터·실사용자에는 쓸 수 없다. `Auditor`로 설정하면 가명 읽기 전용 데모가 된다(`infra/main.bicep` `consoleDemoRole`).

## 재검토 조건
- **실데이터·실사용자가 들어오면 즉시 Entra를 복원한다**: `CONSOLE_AUTH_ENABLED=true` + 앱 등록(`infra/modules/console-auth.bicep`). 운영 전환 시 암시적 id_token 대신 비밀 또는 관리 ID 페더레이션(FIC) 기반 인증 코드 흐름으로 바꾼다.
- 다중 심사역·감사 추적이 필요해 역할 분리가 필수일 때.

## 근거
- 구현: `infra/main.bicep`(`consoleAuthEnabled`·`consoleDemoRole`), `infra/resources.bicep:609`·`:599`, `src/console/adapters.py`, `src/ops/console_gate.py`, `infra/modules/console-auth.bicep`, `infra/modules/container-app.bicep:42-46`.
- 상위 Spec: `docs/superpowers/specs/2026-10-31-agentic-cashflow-lending-design.md` §4.2.
- 운영 전환 표: [../architecture.md](../architecture.md), 배경 [../runbook/deploy-history.md](../runbook/deploy-history.md).
