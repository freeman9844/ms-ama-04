# ADR-004: 완전 사설망 + 배포 창

## 상태
채택(P2~P5, 2026-10-31~11-02) · as-built

## 맥락
금융 데이터 평면은 외부 노출을 최소화해야 한다. 상위 Spec §2.2(네트워크 토폴로지)는 모든 PaaS 리소스를 Private Endpoint로만 접근하고 평상시 public network access를 끄도록 한다(`docs/superpowers/specs/2026-10-31-agentic-cashflow-lending-design.md` §2.2). 그런데 운영자 PC에서 코드 배포·이미지 빌드·시드 적재 같은 데이터 평면 작업은 직접 접근이 필요하다. 상시 공개나 점프박스는 비용·운영 부담이 크다.

## 결정
평상시 Foundry·Cosmos·Storage·AI Search·Key Vault·Service Bus·ACR·Functions의 public network access는 모두 꺼져 있고 Private Endpoint·Private DNS로만 접근한다. 운영자 데이터 평면 작업은 **배포 창** 동안에만 연다: `operatorIp` 파라미터가 있을 때만 Functions의 `publicNetworkAccess`를 `Enabled`로 바꾸고 기본 거부 + 운영자 IP 허용 규칙을 넣는다(`infra/modules/functions.bicep:19`·`:43`·`:47-49`, `windowOpen = !empty(operatorIp)`). ACR는 `az acr build` 에이전트가 IP 규칙 밖이라 빌드 창 동안 `acrPublic`으로 전체 공개한다. 창 조작은 `src/ops/window.py`(`python -m ops window open [--acr] | status | close`)가 `azd provision`으로 수행한다. **콘솔은 예외**다: 상시 열려 있고 App Gateway WAF의 운영자 IP 규칙(`CONSOLE_IP`, 창을 닫아도 유지, [ADR-011](ADR-011-appgw-waf-entry.md))으로만 막으며 배포 창 토글과 무관하다.

## 검토한 대안
| 대안 | 장점 | 기각 이유 |
|---|---|---|
| 상시 공개 + IP 제한 | 창 조작 불필요, 배포 간단 | 데이터 평면이 늘 공개라 공격 표면이 큼. 사설 우선 원칙 위반 |
| Bastion·점프박스 | 안정적 운영자 접근 | VM·게이트웨이 상시 비용과 운영 부담. 데모 규모에 과함 |
| Self-hosted runner(VNet 내부) | 창 없이 사설 배포 | 러너 VM·유지보수 비용, 데모 복잡도 증가 |

## 결과
- 장점: 평상시 데이터 평면이 완전 사설이고, 작업 시에만 운영자 IP 하나로 좁게 연다. 창 조작이 프로비저닝 1회로 끝난다.
- 대가: 창 열기·닫기에 프로비저닝 시간이 든다. 실측: 열기 3.7분·닫기 3.7분(P5b), 열기 6.6분·닫기 5.4분(P6b, job-ops·경보·Workbook 첫 생성 포함) — [../runbook/deploy-history.md](../runbook/deploy-history.md). 운영자 PC 공인 IP가 연결마다 바뀌면 WAF 운영자 IP 규칙과 어긋나 403이 나며, `window open --ip`로 `CONSOLE_IP`를 갱신하거나 WAF 규칙에 임시 IP를 더해 대응한다([../runbook/deploy.md](../runbook/deploy.md) "운영자 IP가 바뀔 때"). Key Vault·Cosmos·Storage는 테넌트 정책상 창을 열어도 운영자 PC에서 못 쓰므로 데이터 작업은 VNet 안 Job으로 한다(ADR-008).

## 재검토 조건
- 운영 전환 시 internal 환경 앞 Application Gateway(WAF v2) + Entra 단일 진입으로 외부 경로를 통합할 때(상위 Spec §2.2).
- 배포 빈도가 높아져 창 조작 비용이 과할 때(VNet 내부 CI 러너 도입 검토).
- 운영자 IP 고정이 가능해져 임시 IP 보완이 불필요할 때.

## 근거
- 구현: `infra/modules/functions.bicep:43`(`publicNetworkAccess`), `src/ops/window.py`, `infra/modules/appgw.bicep`(콘솔 운영자 IP 규칙).
- 실측 소요: [../runbook/deploy-history.md](../runbook/deploy-history.md), 운영 안내 [../runbook/deploy.md](../runbook/deploy.md).
- 상위 Spec: `docs/superpowers/specs/2026-10-31-agentic-cashflow-lending-design.md` §2.2.
