# ADR-008: 평가·운영 측정을 VNet 안 Container Apps Job으로

## 상태
채택(P4b·P6a, 2026-10-31~11-02) · as-built

## 맥락
평가(분류·심사·조기경보)와 운영 측정(SLO·비용·공정성)은 Cosmos·Storage 큐·Foundry 같은 데이터 평면에 접근해야 한다. 이 리소스는 Private Endpoint 전용이고(ADR-004), 특히 Key Vault·Cosmos·Storage는 테넌트 정책상 배포 창을 열어도 운영자 PC에서 접근할 수 없다([../runbook/deploy.md](../runbook/deploy.md), [../runbook/deploy-history.md](../runbook/deploy-history.md) "알려진 제약"). 따라서 평가·측정을 운영자 PC에서 직접 돌릴 수 없다.

## 결정
평가·운영 측정을 **VNet 안 Container Apps Job**으로 돌린다. `eval-job`과 `ops-job`(`job-ops`)은 같은 모듈 `infra/modules/eval-job.bicep`을 쓰고 internal 환경 `cae-int`에 배치된다(`infra/resources.bicep:686` eval, `:718` ops). 각 Job은 데이터 평면 접근에 필요한 관리 ID를 갖는다: `id-eval`은 eval-reports 컨테이너에 Blob Contributor로 리포트를 쓰고(`infra/resources.bicep:681`) Foundry·OpenAI 사용자 역할로 평가 모델을 호출한다(`:322-323`), `id-ops`는 Cosmos 읽기 + Queue Sender다(`:277`·`:250`), smoke `id-smoke`는 전 경로를 점검하므로 가장 넓다 — Queue Sender(`:248`)·Service Bus 송신(`:404`)에 더해 Cosmos Data Reader(계정 범위, `:275`)·Blob Reader(`:219`)·Key Vault Secrets User(`:175`)·Foundry/OpenAI 사용자(`:320-321`)를 갖는다. 결과는 **콘솔 로그 조각(EVALCHUNK 태그)**으로 회수한다: Job이 로그로 출력하면 `src/ops/jobs.py`의 `collect_files`가 Log Analytics 쿼리로 조각을 모아 재조립한다(`reassemble`). eval은 `EVAL_BLOB_ACCOUNT_URL`이 있으면 같은 리포트의 **보관 사본을 eval-reports Blob에도 업로드**한다(`src/evals/__main__.py:117-129`) — 이것이 `id-eval`의 Blob Contributor 권한 용도다.

## 검토한 대안
| 대안 | 장점 | 기각 이유 |
|---|---|---|
| 로컬 실행 + 임시 공개 | 반복이 빠름 | Cosmos·Storage·KV는 창을 열어도 테넌트 정책상 로컬 접근 불가. 데이터 평면 상시 노출 위험 |
| 결과를 Blob에 업로드 후 다운로드 | 큰 산출물에 적합 | 운영자 PC가 Blob에 닿으려면 공개가 필요. 사설 전용 원칙과 충돌 |
| VM/점프박스에서 실행 | 안정적 VNet 접근 | 상시 VM 비용·운영 부담(ADR-004와 같은 이유로 기각) |

## 결과
- 장점: 평가·측정이 VNet 안에서 사설 IP로 데이터에 접근하고, 1차 회수는 로그로만 나와 추가 공개 경로가 없다. 각 Job 관리 ID는 데이터 평면 역할로 범위를 나눴다. 다만 `id-smoke`는 전 경로 스모크라 Cosmos·Blob·Key Vault·Foundry·큐·Service Bus까지 넓게 가지므로 엄밀한 의미의 최소 권한은 아니다.
- 대가: Log Analytics 수집이 수 분 늦을 수 있어 조각이 모두 모일 때까지 재조회한다(`collect_with_retry`, `src/ops/jobs.py`). 로그 조각 1차 회수는 산출물 크기에 제약되지만, eval은 Blob 보관 사본으로 전체 리포트를 남긴다(`src/evals/__main__.py:117-129`). 실측 리포트가 이 경로로 생성됐다(`eval/reports/job-eval-9imbutm/report.md`, `ops/reports/load-20261102110233/report.md`).

## 재검토 조건
- 산출물이 로그 조각으로 회수하기엔 커질 때(사설 Blob + VNet 내부 수집기로 전환).
- 평가·측정 주기가 잦아져 전용 VNet 내부 러너·스케줄러가 필요할 때.
- 테넌트 정책이 바뀌어 사설 데이터에 대한 안전한 로컬 접근이 가능해질 때.

## 근거
- 구현: `infra/modules/eval-job.bicep`, `infra/resources.bicep:681`·`:322-323`·`:277`·`:275`·`:250`·`:248`·`:219`·`:175`, `src/ops/jobs.py`, `src/evals/__main__.py:117-129`.
- 산출물: `eval/reports/job-eval-9imbutm/report.md`, `ops/reports/load-20261102110233/report.md`.
- 제약 배경: [../runbook/deploy-history.md](../runbook/deploy-history.md), [../runbook/deploy.md](../runbook/deploy.md).
