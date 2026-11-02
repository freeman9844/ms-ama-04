# ADR-002: 모델 배포 유형 — Global Standard + 가명화

## 상태
채택(P2, 2026-10-31~11-02) · as-built

## 맥락
과제는 "추론 처리가 국내에 머무를 것(processing stays in-country)"을 요구하지만, Korea Central regional 배포를 쓰기 어렵고 Global Standard는 추론 요청이 어느 Azure 리전에서든 처리될 수 있다. 상위 Spec §4.2(ADR-002)는 이 트레이드오프를 다룬다(`docs/superpowers/specs/2026-10-31-agentic-cashflow-lending-design.md` §4.2).

상위 Spec §4.2 ADR-002의 요지:

> **결정:** Global Standard 배포를 쓴다. **충돌:** 과제의 국내 처리 제약과 충돌한다. **완화책:** ① 모델 전송 전 가명화(상호·대표자명·계좌·사업자번호 토큰화 후 복원), ② 최소 전송(파생 지표·집계값), ③ 저장 데이터는 국내(Korea Central), ④ 가명화 누락률·PII 스캔 측정. **운영 전환 조건:** Korea Central regional 배포나 PTU로 전환, 중간 단계로 Data Zone Standard(APAC).

## 결정
모델 배포 기본 SKU는 **Global Standard**이고, Bicep 파라미터로 바로 바꿀 수 있다: `chatModelSku`/`decisionModelSku`(기본값 `GlobalStandard`, 허용값 `GlobalStandard`·`DataZoneStandard`)는 `infra/main.bicep`에 선언되고, 실제 배포 리소스는 `infra/modules/foundry/account.bicep`의 `Microsoft.CognitiveServices/accounts/deployments`가 받는다. 완화책의 핵심인 **가명화**는 모델로 가는 모든 문자열에 적용한다: `build_pseudonymizer`(`src/agents/data_assembly.py:211`)가 만든 마스커를 심사·분류·조기경보 경로가 공유하고(`src/orchestrator/journey/activities.py`, `src/console/views.py`), 토큰화·복원·PII 탐지 구현은 `src/shared/pseudonymize.py`(`Pseudonymizer`, `find_pii`)에 있다.

## 검토한 대안
| 대안 | 장점 | 기각 이유 |
|---|---|---|
| Data Zone Standard(APAC) | 처리 범위를 APAC Data Zone으로 축소 | 여전히 국내 처리가 아니라 제약을 해소 못 함. gpt-5.4 쿼터가 더 작음(100K vs 250K TPM, 상위 Spec §4.2) |
| Korea Central regional 배포 | 국내 처리 보장 | 데모 시점에 해당 모델 regional 배포를 쓰기 어려움(상위 Spec §4.2) |
| PTU(프로비저닝 처리량) | 국내 처리·예측 가능 지연·단가 | 데모 규모에 비용 과다. 운영 전환 과제로 미룸 |

## 결과
- 장점: 데모에서 가장 넓은 모델·쿼터를 쓰면서, SKU를 파라미터로 둬 운영 전환 시 한 줄로 바꾼다. 가명화·최소 전송으로 모델 전송 PII를 줄인다(실측: 검사한 모델 호출 341회 중 PII 잔존 0회, `eval/reports/job-eval-9imbutm/report.md` 5장).
- 대가: 추론 처리 위치가 국내로 보장되지 않는다(상위 Spec의 국내 처리 제약 미충족, as-built 잔여 위험). abuse monitoring 데이터 처리 조건 확인이 남는다.
- as-built 차이: `Microsoft-Decision-1`은 APAC Global Standard 지원이 문서·CLI에서 엇갈려, 실패 시 `DataZoneStandard`로 전환하도록 `decisionModelSku`를 별도로 둔다(`infra/main.bicep`, [../runbook/troubleshooting.md](../runbook/troubleshooting.md)).

## 재검토 조건
- 실데이터·실고객이 들어오면 즉시 Korea Central regional 또는 PTU로 전환(그 전 단계로 Data Zone Standard APAC).
- gpt-5.4 TPM 쿼터가 상한에 닿아 처리량 보장이 필요할 때.
- 규제 검토에서 국내 처리가 필수로 확정될 때.

## 근거
- SKU 파라미터: `infra/main.bicep`(`chatModelSku`·`decisionModelSku`), 배포 리소스 `infra/modules/foundry/account.bicep`.
- 가명화: `src/agents/data_assembly.py:211`, `src/shared/pseudonymize.py`.
- PII 스캔 실측: `eval/reports/job-eval-9imbutm/report.md` 5장.
- 상위 Spec: `docs/superpowers/specs/2026-10-31-agentic-cashflow-lending-design.md` §4.2.
- 운영 전환 표: [../architecture.md](../architecture.md).
