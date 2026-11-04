# 운영 지표 보고서 — load-20261104041554

> 합성 데이터 기준. 신청 48건, 생성 2026-11-04T04:42:53.369010+00:00.

## 1. SLO

| 지표 | 목표 | 측정 | 판정 |
|---|---|---|---|
| 접수 → 심사역 대기열 p95 | ≤ 10분 | 1.2분 (p50 0.7, 최대 1.3, n=48) | 충족 |
| 에이전트 단계 성공률 | ≥ 99.0% | 100.0% (ok 144/144, degraded 0, failed 0) | 충족 |

미도달 0건, 접수 시각 대체 0건. p95는 nearest-rank입니다. 표본이 작아 신뢰 구간이 넓고, 용량 시험이 아닙니다. degraded(안전망 경로)는 성공으로 세지 않습니다.

| 단계 | ok | degraded | failed |
|---|---|---|---|
| 데이터 수집 | 48 | 0 | 0 |
| 신용 평가 | 48 | 0 | 0 |
| 거래 분류 | 48 | 0 | 0 |

## 2. 비용

| 모델 | 호출 | 입력 토큰 | 출력 토큰 | 비용 |
|---|---|---|---|---|
| decision-1 | 96 | 0 | 0 | — |
| gpt-5.4 | 158 | 436,014 | 63,797 | $2.0470 |
| gpt-5.4-mini | 202 | 144,743 | 37,365 | $0.2767 |

AI 비용 합계 $2.3237, 신청당 $0.0484 (캐시 할인 미반영 상한, 단가 미확인 호출 96건).
고정비(월): $283.6600 — Cost Management 실측 2026-10-27~2026-11-02 일평균(3일) × 30, Foundry Models 제외.

| 월 물량 | AI/건 | 고정비/건 | 합계/건 | 수작업 대비 | 대출 1건(USD 6,000) 대비 |
|---|---|---|---|---|---|
| 100 | $0.0484 | $2.8366 | $2.8850 | 0.1% | 0.0% |
| 1,000 | $0.0484 | $0.2837 | $0.3321 | 0.0% | 0.0% |
| 10,000 | $0.0484 | $0.0284 | $0.0768 | 0.0% | 0.0% |

수작업 처리 원가 $3,500/건 가정: Mills & McCarthy, HBS Working Paper 15-004 (소상공인 대출 1건 처리 원가 $3,000~4,000의 중간값).

## 3. 공정성(추천 approve율)

임계값 20pp, 값별 평가 표본 < 5이면 격차 계산에서 제외합니다.

### 업종

| 값 | 신청 | 평가 | approve율 | eligible율 | 비고 |
|---|---|---|---|---|---|
| 디지털 | 8 | 8 | 37.5% | 37.5% |  |
| 리빙 | 8 | 8 | 12.5% | 25.0% |  |
| 반려동물 | 8 | 8 | 37.5% | 50.0% |  |
| 뷰티 | 8 | 8 | 37.5% | 37.5% |  |
| 식품 | 8 | 8 | 25.0% | 25.0% |  |
| 패션의류 | 8 | 8 | 25.0% | 25.0% |  |

격차 25pp(eligible 25pp), 원형 통제 최대 100pp. **경보: 임계값 초과**.

### 지역

| 값 | 신청 | 평가 | approve율 | eligible율 | 비고 |
|---|---|---|---|---|---|
| 경기 | 16 | 16 | 31.2% | 37.5% |  |
| 부산 | 16 | 16 | 37.5% | 37.5% |  |
| 서울 | 16 | 16 | 18.8% | 25.0% |  |

격차 18.8pp(eligible 12.5pp), 원형 통제 최대 50pp. 임계값 이내.

### 업력

| 값 | 신청 | 평가 | approve율 | eligible율 | 비고 |
|---|---|---|---|---|---|
| 1~3년 | 16 | 16 | 37.5% | 50.0% |  |
| 3년+ | 16 | 16 | 50.0% | 50.0% |  |
| <1년 | 16 | 16 | 0.0% | 0.0% |  |

격차 50pp(eligible 50pp), 원형 통제 최대 100pp. 규칙 R104(업력)로 설명되는 차이 — 경보 대상 아님.

## 4. 장애 시나리오

### source-flaky (run fault-20261104043236, 원복 확인)

| 신청 | 상태 | 플래그 | 단계 |
|---|---|---|---|
| fault-20261104043236-01 | awaiting_officer | agent_assessment_mismatch | consent:ok, assemble:ok, underwrite:ok, classify:ok, policy:ok, assess:ok |
| fault-20261104043236-02 | awaiting_officer | agent_assessment_mismatch | consent:ok, assemble:ok, underwrite:ok, classify:ok, policy:ok, assess:ok |

| 신청 | 대상 | 역할 | 호출 | 실패 |
|---|---|---|---|---|
| fault-20261104043236-01 | unknown | responsesapi | 6 | 0 |
| fault-20261104043236-01 | mockapi | orchestrator | 5 | 2 |
| fault-20261104043236-01 | foundry | orchestrator | 4 | 0 |
| fault-20261104043236-01 | invoke_agent data-assembly | orchestrator | 1 | 0 |
| fault-20261104043236-01 | QueueClient.send_message | orchestrator | 1 | 0 |
| fault-20261104043236-01 | storage-queue | orchestrator | 1 | 0 |
| fault-20261104043236-01 | ContainerProxy.upsert_item | orchestrator | 1 | 0 |
| fault-20261104043236-01 | cosmos | orchestrator | 1 | 0 |
| fault-20261104043236-02 | unknown | responsesapi | 6 | 0 |
| fault-20261104043236-02 | ContainerClient.create_container | orchestrator | 1 | 0 |
| fault-20261104043236-02 | storage-blob | orchestrator | 1 | 0 |
| fault-20261104043236-02 | mockapi | orchestrator | 8 | 3 |
| fault-20261104043236-02 | foundry | orchestrator | 1 | 0 |
| fault-20261104043236-02 | QueueClient.send_message | orchestrator | 1 | 0 |
| fault-20261104043236-02 | storage-queue | orchestrator | 1 | 0 |
| fault-20261104043236-02 | ContainerProxy.upsert_item | orchestrator | 1 | 0 |
| fault-20261104043236-02 | cosmos | orchestrator | 1 | 0 |

### policy-down (run fault-20261104043615, 원복 확인)

| 신청 | 상태 | 플래그 | 단계 |
|---|---|---|---|
| fault-20261104043615-01 | awaiting_officer | agent_assessment_mismatch, policy_unavailable, assessment_unavailable | consent:ok, assemble:ok, underwrite:ok, classify:ok, policy:failed |
| fault-20261104043615-02 | awaiting_officer | agent_assessment_mismatch, high_cost_lender_detected, policy_unavailable, assessment_unavailable | consent:ok, assemble:ok, underwrite:ok, classify:ok, policy:failed |
| fault-20261104043615-03 | awaiting_officer | agent_assessment_mismatch, policy_unavailable, assessment_unavailable | consent:ok, assemble:ok, underwrite:ok, classify:ok, policy:failed |

| 신청 | 대상 | 역할 | 호출 | 실패 |
|---|---|---|---|---|
| fault-20261104043615-01 | policy | orchestrator | 2 | 2 |
| fault-20261104043615-01 | QueueClient.send_message | orchestrator | 1 | 0 |
| fault-20261104043615-01 | storage-queue | orchestrator | 1 | 0 |
| fault-20261104043615-01 | ContainerProxy.upsert_item | orchestrator | 1 | 0 |
| fault-20261104043615-01 | cosmos | orchestrator | 1 | 0 |
| fault-20261104043615-01 | mockapi | orchestrator | 5 | 0 |
| fault-20261104043615-01 | ContainerClient.create_container | orchestrator | 2 | 1 |
| fault-20261104043615-01 | ContainerClient.upload_blob | orchestrator | 1 | 0 |
| fault-20261104043615-01 | storage-blob | orchestrator | 2 | 0 |
| fault-20261104043615-01 | invoke_agent data-assembly | orchestrator | 1 | 0 |
| fault-20261104043615-01 | foundry | orchestrator | 3 | 0 |
| fault-20261104043615-01 | unknown | responsesapi | 4 | 0 |
| fault-20261104043615-02 | policy | orchestrator | 3 | 3 |
| fault-20261104043615-03 | QueueClient.send_message | orchestrator | 1 | 0 |
| fault-20261104043615-03 | storage-queue | orchestrator | 1 | 0 |
| fault-20261104043615-03 | ContainerProxy.upsert_item | orchestrator | 1 | 0 |
| fault-20261104043615-03 | cosmos | orchestrator | 6 | 0 |
| fault-20261104043615-03 | mockapi | orchestrator | 5 | 0 |
| fault-20261104043615-03 | ContainerProxy.read_item | orchestrator | 3 | 0 |
| fault-20261104043615-03 | ContainerProxy.replace_item | orchestrator | 2 | 0 |
| fault-20261104043615-03 | ContainerClient.upload_blob | orchestrator | 4 | 0 |
| fault-20261104043615-03 | storage-blob | orchestrator | 8 | 0 |
| fault-20261104043615-03 | ContainerClient.create_container | orchestrator | 4 | 3 |
| fault-20261104043615-03 | invoke_agent data-assembly | orchestrator | 1 | 0 |
| fault-20261104043615-03 | foundry | orchestrator | 5 | 0 |
| fault-20261104043615-03 | unknown | responsesapi | 6 | 0 |
| fault-20261104043615-03 | invoke_agent txn-classifier | orchestrator | 1 | 0 |
| fault-20261104043615-03 | BlobClient.download_blob | orchestrator | 1 | 0 |
