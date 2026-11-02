# 운영 지표 보고서 — load-20261102110233

> 합성 데이터 기준. 신청 24건, 생성 2026-11-02T11:23:13.721392+00:00.

## 1. SLO

| 지표 | 목표 | 측정 | 판정 |
|---|---|---|---|
| 접수 → 심사역 대기열 p95 | ≤ 10분 | 1.2분 (p50 0.6, 최대 5.7, n=24) | 충족 |
| 에이전트 단계 성공률 | ≥ 99.0% | 100.0% (ok 72/72, degraded 0, failed 0) | 충족 |

미도달 0건, 접수 시각 대체 0건. p95는 nearest-rank입니다. 표본이 작아 신뢰 구간이 넓고, 용량 시험이 아닙니다. degraded(안전망 경로)는 성공으로 세지 않습니다.

| 단계 | ok | degraded | failed |
|---|---|---|---|
| 데이터 수집 | 24 | 0 | 0 |
| 신용 평가 | 24 | 0 | 0 |
| 거래 분류 | 24 | 0 | 0 |

## 2. 비용

| 모델 | 호출 | 입력 토큰 | 출력 토큰 | 비용 |
|---|---|---|---|---|
| decision-1 | 48 | 0 | 0 | — |
| gpt-5.4 | 70 | 187,149 | 27,803 | $0.8849 |
| gpt-5.4-mini | 92 | 68,940 | 17,562 | $0.1307 |

AI 비용 합계 $1.0157, 신청당 $0.0423 (캐시 할인 미반영 상한, 단가 미확인 호출 48건).
고정비(월): $304.7300 — Cost Management 실측 2026-10-25~2026-10-31 일평균(1일) × 30, Foundry Models 제외.

| 월 물량 | AI/건 | 고정비/건 | 합계/건 | 수작업 대비 | 대출 1건(USD 6,000) 대비 |
|---|---|---|---|---|---|
| 100 | $0.0423 | $3.0473 | $3.0896 | 0.1% | 0.1% |
| 1,000 | $0.0423 | $0.3047 | $0.3470 | 0.0% | 0.0% |
| 10,000 | $0.0423 | $0.0305 | $0.0728 | 0.0% | 0.0% |

수작업 처리 원가 $3,500/건 가정: Mills & McCarthy, HBS Working Paper 15-004 (소상공인 대출 1건 처리 원가 $3,000~4,000의 중간값).

## 3. 공정성(추천 approve율)

임계값 20pp, 값별 평가 표본 < 5이면 격차 계산에서 제외합니다.

### 업종

| 값 | 신청 | 평가 | approve율 | eligible율 | 비고 |
|---|---|---|---|---|---|
| 제과점 | 8 | 8 | 37.5% | 50.0% |  |
| 카페 | 8 | 8 | 25.0% | 25.0% |  |
| 한식 | 8 | 8 | 25.0% | 25.0% |  |

격차 12.5pp(eligible 25pp), 원형 통제 최대 50pp. 임계값 이내.

### 지역

| 값 | 신청 | 평가 | approve율 | eligible율 | 비고 |
|---|---|---|---|---|---|
| 경기 | 8 | 8 | 12.5% | 25.0% |  |
| 부산 | 8 | 8 | 50.0% | 50.0% |  |
| 서울 | 8 | 8 | 25.0% | 25.0% |  |

격차 37.5pp(eligible 25pp), 원형 통제 최대 100pp. **경보: 임계값 초과**.

### 업력

| 값 | 신청 | 평가 | approve율 | eligible율 | 비고 |
|---|---|---|---|---|---|
| 1~3년 | 8 | 8 | 50.0% | 50.0% |  |
| 3년+ | 8 | 8 | 37.5% | 50.0% |  |
| <1년 | 8 | 8 | 0.0% | 0.0% |  |

격차 50pp(eligible 50pp), 원형 통제 최대 100pp. 규칙 R104(업력)로 설명되는 차이 — 경보 대상 아님.

## 4. 장애 시나리오

### source-flaky (run fault-20261102111447, 원복 확인)

| 신청 | 상태 | 플래그 | 단계 |
|---|---|---|---|
| fault-20261102111447-01 | awaiting_officer | agent_assessment_mismatch | consent:ok, assemble:ok, underwrite:ok, classify:ok, policy:ok, assess:ok |
| fault-20261102111447-02 | awaiting_officer | — | consent:ok, assemble:ok, underwrite:ok, classify:ok, policy:ok, assess:ok |

| 신청 | 대상 | 역할 | 호출 | 실패 |
|---|---|---|---|---|
| fault-20261102111447-01 | QueueClient.send_message | orchestrator | 1 | 0 |
| fault-20261102111447-01 | stfngaz7yh2zic7zc.queue.core.windows.net:None | orchestrator | 1 | 0 |
| fault-20261102111447-01 | ContainerProxy.upsert_item | orchestrator | 1 | 0 |
| fault-20261102111447-01 | cosmos-gaz7yh2zic7zc-koreacentral.documents.azure.com:None | orchestrator | 1 | 0 |
| fault-20261102111447-01 | ca-mockapi.ambitiousbeach-549217a1.koreacentral.azurecontainerapps.io | orchestrator | 5 | 2 |
| fault-20261102111447-01 | unknown | responsesapi | 6 | 0 |
| fault-20261102111447-01 | invoke_agent data-assembly | orchestrator | 1 | 0 |
| fault-20261102111447-01 | ais-gaz7yh2zic7zc.services.ai.azure.com | orchestrator | 4 | 0 |
| fault-20261102111447-01 | POST /v1/decisions http send | policy | 2 | 0 |
| fault-20261102111447-01 | POST /v1/decisions http receive | policy | 1 | 0 |
| fault-20261102111447-02 | QueueClient.send_message | orchestrator | 1 | 0 |
| fault-20261102111447-02 | stfngaz7yh2zic7zc.queue.core.windows.net:None | orchestrator | 1 | 0 |
| fault-20261102111447-02 | ContainerProxy.upsert_item | orchestrator | 1 | 0 |
| fault-20261102111447-02 | cosmos-gaz7yh2zic7zc-koreacentral.documents.azure.com:None | orchestrator | 1 | 0 |
| fault-20261102111447-02 | ca-mockapi.ambitiousbeach-549217a1.koreacentral.azurecontainerapps.io | orchestrator | 9 | 4 |
| fault-20261102111447-02 | unknown | responsesapi | 6 | 0 |
| fault-20261102111447-02 | ContainerClient.upload_blob | orchestrator | 3 | 0 |
| fault-20261102111447-02 | stdatgaz7yh2zic7zc.blob.core.windows.net:None | orchestrator | 6 | 0 |
| fault-20261102111447-02 | ContainerClient.create_container | orchestrator | 3 | 2 |
| fault-20261102111447-02 | invoke_agent data-assembly | orchestrator | 1 | 0 |
| fault-20261102111447-02 | ais-gaz7yh2zic7zc.services.ai.azure.com | orchestrator | 4 | 0 |

### policy-down (run fault-20261102111750, 원복 확인)

| 신청 | 상태 | 플래그 | 단계 |
|---|---|---|---|
| fault-20261102111750-01 | awaiting_officer | agent_assessment_mismatch, policy_unavailable, assessment_unavailable | consent:ok, assemble:ok, underwrite:ok, classify:ok, policy:failed |
| fault-20261102111750-02 | awaiting_officer | agent_assessment_mismatch, high_cost_lender_detected, policy_unavailable, assessment_unavailable | consent:ok, assemble:ok, underwrite:ok, classify:ok, policy:failed |
| fault-20261102111750-03 | awaiting_officer | agent_assessment_mismatch, policy_unavailable, assessment_unavailable | consent:ok, assemble:ok, underwrite:ok, classify:ok, policy:failed |

| 신청 | 대상 | 역할 | 호출 | 실패 |
|---|---|---|---|---|
| fault-20261102111750-01 | QueueClient.send_message | orchestrator | 1 | 0 |
| fault-20261102111750-01 | stfngaz7yh2zic7zc.queue.core.windows.net:None | orchestrator | 1 | 0 |
| fault-20261102111750-01 | ContainerProxy.upsert_item | orchestrator | 1 | 0 |
| fault-20261102111750-01 | cosmos-gaz7yh2zic7zc-koreacentral.documents.azure.com:None | orchestrator | 1 | 0 |
| fault-20261102111750-01 | ca-mockapi.ambitiousbeach-549217a1.koreacentral.azurecontainerapps.io | orchestrator | 5 | 0 |
| fault-20261102111750-01 | POST /v1/decisions http send | policy | 6 | 3 |
| fault-20261102111750-01 | ca-policy.ambitiousbeach-549217a1.koreacentral.azurecontainerapps.io | orchestrator | 2 | 2 |
| fault-20261102111750-01 | ContainerClient.upload_blob | orchestrator | 1 | 0 |
| fault-20261102111750-01 | stdatgaz7yh2zic7zc.blob.core.windows.net:None | orchestrator | 2 | 0 |
| fault-20261102111750-01 | ContainerClient.create_container | orchestrator | 1 | 0 |
| fault-20261102111750-01 | unknown | responsesapi | 4 | 0 |
| fault-20261102111750-01 | invoke_agent data-assembly | orchestrator | 1 | 0 |
| fault-20261102111750-01 | ais-gaz7yh2zic7zc.services.ai.azure.com | orchestrator | 3 | 0 |
| fault-20261102111750-02 | POST /v1/decisions http send | policy | 4 | 2 |
| fault-20261102111750-02 | ca-policy.ambitiousbeach-549217a1.koreacentral.azurecontainerapps.io | orchestrator | 2 | 2 |
| fault-20261102111750-02 | QueueClient.send_message | orchestrator | 1 | 0 |
| fault-20261102111750-02 | stfngaz7yh2zic7zc.queue.core.windows.net:None | orchestrator | 1 | 0 |
| fault-20261102111750-02 | 169.254.255.2:8081 | orchestrator | 3 | 0 |
| fault-20261102111750-02 | ContainerProxy.upsert_item | orchestrator | 1 | 0 |
| fault-20261102111750-02 | cosmos-gaz7yh2zic7zc-koreacentral.documents.azure.com:None | orchestrator | 9 | 0 |
| fault-20261102111750-02 | ca-mockapi.ambitiousbeach-549217a1.koreacentral.azurecontainerapps.io | orchestrator | 5 | 0 |
| fault-20261102111750-02 | ContainerProxy.read_item | orchestrator | 3 | 0 |
| fault-20261102111750-02 | ContainerProxy.replace_item | orchestrator | 2 | 0 |
| fault-20261102111750-02 | ContainerClient.upload_blob | orchestrator | 4 | 0 |
| fault-20261102111750-02 | stdatgaz7yh2zic7zc.blob.core.windows.net:None | orchestrator | 8 | 0 |
| fault-20261102111750-02 | ContainerClient.create_container | orchestrator | 4 | 3 |
| fault-20261102111750-02 | invoke_agent data-assembly | orchestrator | 1 | 0 |
| fault-20261102111750-02 | ais-gaz7yh2zic7zc.services.ai.azure.com | orchestrator | 7 | 0 |
| fault-20261102111750-02 | unknown | responsesapi | 6 | 0 |
| fault-20261102111750-02 | invoke_agent txn-classifier | orchestrator | 1 | 0 |
| fault-20261102111750-03 | unknown | responsesapi | 4 | 0 |
| fault-20261102111750-03 | ais-gaz7yh2zic7zc.services.ai.azure.com | orchestrator | 3 | 0 |
| fault-20261102111750-03 | ContainerClient.create_container | orchestrator | 2 | 1 |
| fault-20261102111750-03 | stdatgaz7yh2zic7zc.blob.core.windows.net:None | orchestrator | 3 | 0 |
| fault-20261102111750-03 | ContainerClient.upload_blob | orchestrator | 1 | 0 |
| fault-20261102111750-03 | ca-mockapi.ambitiousbeach-549217a1.koreacentral.azurecontainerapps.io | orchestrator | 5 | 0 |
| fault-20261102111750-03 | invoke_agent data-assembly | orchestrator | 1 | 0 |
| fault-20261102111750-03 | QueueClient.send_message | orchestrator | 1 | 0 |
| fault-20261102111750-03 | stfngaz7yh2zic7zc.queue.core.windows.net:None | orchestrator | 1 | 0 |
| fault-20261102111750-03 | ContainerProxy.upsert_item | orchestrator | 1 | 0 |
| fault-20261102111750-03 | cosmos-gaz7yh2zic7zc-koreacentral.documents.azure.com:None | orchestrator | 1 | 0 |
