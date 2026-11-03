# ADR-007: 데모 인증 모드(Easy Auth off + 고정 역할)

## 상태
채택(P4b, 2026-10-31~11-02) · as-built

## 맥락
상위 Spec §2.2·§4.2는 콘솔에 Entra ID 인증(Easy Auth)과 운영자 IP 허용을 둘 다 적용하고, 앱 역할(`CreditOfficer`·`CreditManager`·`Auditor`)로 권한을 나눈다(`docs/superpowers/specs/2026-10-31-agentic-cashflow-lending-design.md` §4.2). 실제 트리거는 테넌트 정책이 사용자 동의를 막아 로그인 시 "관리자 승인 필요"가 뜬 것이다(`docs/runbook/deploy-history.md:225`). Easy Auth 자체는 구성됐지만, 브라우저의 `새 신청` 제출(POST)이 앱에 도달하지 못하고 빈 403으로 끝나 데모 경로를 검증할 수 없었다(`docs/runbook/deploy-history.md:256-259`). 관리자 동의를 받거나 운영자 본인에게 위임 권한을 부여하기 전에는 평가 데모를 진행할 수 없었다.

## 결정
데모에서는 `CONSOLE_AUTH_ENABLED=false`(`infra/main.bicep` `consoleAuthEnabled`)로 Easy Auth를 끈다. 이때 Container App에 `CONSOLE_AUTH_MODE=demo`와 `CONSOLE_DEMO_ROLE`(기본 `CreditOfficer`) 환경 변수를 주입하고(`infra/resources.bicep:609`), 콘솔은 **고정 주체 `demo-operator`**로 동작한다(`src/console/adapters.py:229`). 외부 접근은 **영구 운영자 IP 허용목록만**으로 막는다(`infra/resources.bicep:599`, `infra/modules/container-app.bicep:46-50`). Entra 앱 등록(`infra/modules/console-auth.bicep`, 역할 정의 포함)은 그대로 둬, 전환 시 `CONSOLE_AUTH_ENABLED=true`로 복원한다. 데모 모드에서 Easy Auth가 꺼져 운영자 IP에서 바로 열려야 함은 `src/ops/console_gate.py`가 검증한다.

## 검토한 대안
| 대안 | 장점 | 기각 이유 |
|---|---|---|
| Entra Easy Auth 상시(Spec 원안) | 사용자 식별·역할 분리 | 테넌트가 사용자 동의를 막아 로그인 시 "관리자 승인 필요"·POST 403으로 데모 경로 검증 불가(`docs/runbook/deploy-history.md:225`·`:256-259`). 관리자 동의/위임 부여 전까지 진행 불가 |
| 인증 완전 제거 | 가장 단순 | IP 허용마저 없으면 외부 노출. 전환 경로(앱 등록)도 사라짐 |
| 공유 비밀번호 폼 인증 | 로그인 흉내 | 역할 클레임·감사 식별이 없고 Easy Auth로 되돌릴 자산이 안 남음 |

## 결과
- 장점: 평가자가 계정 없이 운영자 IP에서 바로 콘솔을 쓴다.
- 대가(보안): 모든 결정이 **고정 심사역 ID `demo-operator`로 기록**되므로(`src/console/adapters.py:229`, `docs/runbook/deploy-history.md:279`) 감사 추적에 실제 신원이 남지 않는다. 고정 `CreditOfficer` 데모 역할은 복원된 실명을 보고 대출을 승인할 수 있으며, 이를 막는 것은 **운영자 IP 허용목록뿐**이다. 실데이터·실사용자에는 쓸 수 없다. `Auditor`로 설정하면 가명 읽기 전용 데모가 된다(`infra/main.bicep` `consoleDemoRole`).
- 대가(전환 비용): 복원은 환경 변수 한 개로 끝나지 않는다. `window open` → `deploy console` → `window close` 재배포(실측 4.0+4.2+4.3분 ≈ 12분, `docs/runbook/deploy-history.md:250`)와 기존 앱 등록(`docs/runbook/deploy-history.md:280`)이 필요하다. 게다가 테넌트가 사용자 동의를 막고 있으면 복원해도 처음과 같은 "관리자 승인 필요" 블록에 걸리므로, 관리자 동의 또는 운영자 위임 부여가 선행돼야 한다.

## 재검토 조건
- **실데이터·실사용자가 들어오면 즉시 Entra를 복원한다**: `CONSOLE_AUTH_ENABLED=true` + 재배포(`window open`→`deploy console`→`window close`) + 앱 등록(`infra/modules/console-auth.bicep`) + 테넌트 관리자 동의. 운영 전환 시 암시적 id_token 대신 비밀 또는 관리 ID 페더레이션(FIC) 기반 인증 코드 흐름으로 바꾼다.
- 다중 심사역·감사 추적이 필요해 고정 `demo-operator` 식별로는 부족할 때.

## 근거
- 구현: `infra/main.bicep`(`consoleAuthEnabled`·`consoleDemoRole`), `infra/resources.bicep:609`·`:599`, `src/console/adapters.py`, `src/ops/console_gate.py`, `infra/modules/console-auth.bicep`, `infra/modules/container-app.bicep:46-50`.
- 상위 Spec: `docs/superpowers/specs/2026-10-31-agentic-cashflow-lending-design.md` §4.2.
- 운영 전환 표: [../architecture.md](../architecture.md), 배경 [../runbook/deploy-history.md](../runbook/deploy-history.md).
