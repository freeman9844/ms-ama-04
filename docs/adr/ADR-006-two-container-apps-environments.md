# ADR-006: Container Apps 환경 2개 분리(internal + external)

## 상태
채택(P2, 2026-10-31~11-02) · as-built

## 맥락
Policy Engine·Mock API·배포 Job은 VNet 안에서만 접근해야 하고, 심사역 콘솔만 외부로 노출한다. Container Apps에서 internal ingress(`external: false`)는 **같은 환경 안의 앱**에서만 접근할 수 있다. 그러면 Azure Functions 오케스트레이터(별도 호스팅)가 internal 앱을 호출할 수 없다. 상위 Spec §2.2는 환경을 둘로 나누고 internal 환경은 Private DNS로 VNet 안에서만, external 환경엔 콘솔만 두도록 한다(`docs/superpowers/specs/2026-10-31-agentic-cashflow-lending-design.md` §2.2).

## 결정
Container Apps 환경을 둘로 나눈다. **`cae-int`**(internal, 내부 부하 분산기)에 Policy Engine·Mock API·smoke/eval/ops Job을 두고(`infra/resources.bicep:441` `internal: true`), 환경 기본 도메인의 Private DNS zone(와일드카드 A 레코드, `infra/modules/aca-dns.bicep`)으로 VNet 안에서만 호출한다. **`cae-web`**(external)에는 콘솔 앱만 둔다(`infra/resources.bicep:463` `internal: false`). Functions 오케스트레이터는 internal 앱을 사설 IP로 해석해 호출한다. 환경 정의는 `infra/modules/aca-env.bicep`가 `internal` 파라미터로 공유한다.

## 검토한 대안
| 대안 | 장점 | 기각 이유 |
|---|---|---|
| 단일 external 환경에 전부 | 환경 하나로 단순 | Policy·Mock·Job까지 외부 노출되거나 앱별 접근 제어가 복잡. 최소 노출 원칙 위반 |
| 단일 internal 환경에 전부 | 모두 사설 | 콘솔도 internal이 되어 운영자 브라우저가 직접 접근 못 함(별도 게이트웨이 필요) |
| internal 앞 Application Gateway(WAF v2)로 통합 진입 | 운영 수준 경계 | 월 약 $365 비용(상위 Spec §2.2). 데모 범위 밖 — 운영 전환 과제 |

## 결과
- 장점: internal 앱은 VNet 밖에서 접근 불가, 콘솔만 좁게 노출된다. Consumption 전용 workload profile 환경은 기본 요금이 없어 환경을 나눠도 비용이 거의 늘지 않는다(상위 Spec §2.2).
- 대가: 환경·Private DNS zone을 둘 운영하고, internal↔Functions 경로가 Private DNS 해석에 의존한다. 콘솔 외부 진입은 운영 전환 시 Application Gateway로 바꿔야 한다([../architecture.md](../architecture.md) 운영 아키텍처).

## 재검토 조건
- 운영 전환으로 internal 환경 앞 Application Gateway(WAF v2) + Entra 단일 진입을 도입할 때.
- 콘솔을 internal로 옮기고 전용 게이트웨이로만 노출하도록 바꿀 때.
- Consumption 외 전용 workload profile이 필요한 처리량·격리 요건이 생길 때.

## 근거
- 구현: `infra/resources.bicep:441`(cae-int internal)·`:463`(cae-web external), `infra/modules/aca-env.bicep`, `infra/modules/aca-dns.bicep`.
- 상위 Spec: `docs/superpowers/specs/2026-10-31-agentic-cashflow-lending-design.md` §2.2.
- 네트워크·컨테이너 배치: [../architecture.md](../architecture.md).
