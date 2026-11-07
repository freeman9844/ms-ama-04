# 비용 모델

신청 1건을 처리하는 데 드는 비용을 건당 AI 비용과 월 고정비로 나눠 모델링하고, 월 물량에 따른 건당 총비용과 수작업 처리 원가·대출 규모 대비 비율을 본다. 계산 코드는 `src/opsmetrics/cost.py`, 모델 단가는 `src/evals/prices.json`에 있다.

> **합성 데이터 기준.** 모든 수치는 합성 신청 24건을 실제 Azure에 흘린 실측 보고서 `ops/reports/load-20261102110233/report.md`(2장 비용)와 평가 보고서 `eval/reports/job-eval-9imbutm/report.md`, 그리고 배포·운영 실행 기록 [runbook/deploy-history.md](runbook/deploy-history.md)에서만 인용한다. 캐시 입력 할인은 반영하지 않아 상한 추정이다.

## 계산식

```
건당 총비용 = 건당 AI 비용 + (월 고정비 ÷ 월 물량)
```

- **건당 AI 비용**: 실측 토큰 × 모델 단가의 합을 신청 수로 나눈 값. 토큰 집계와 단가 적용은 `src/opsmetrics/cost.py`의 `ai_cost`가, 물량별 건당 환산은 `unit_economics`가 계산한다. 단가는 `src/evals/prices.json`(Azure OpenAI Global Standard, USD/1M 토큰)에서 읽는다.
- **월 고정비**: 모델 사용량과 무관하게 매달 드는 인프라 비용. 물량이 늘수록 건당 분담액이 줄어 규모의 경제가 작동한다.
- 단가를 확인하지 못한 호출(`decision-1`)은 토큰이 0으로 집계되어 금액에 들어가지 않고 "단가 미확인 호출 수"로만 남는다(`src/evals/prices.json`에서 `decision-1`의 단가는 `null`).

## 건당 AI 비용

실측 24건(`ops/reports/load-20261102110233/report.md` 2장)의 모델별 호출·비용:

| 모델 | 호출 | 비용(USD) | 비고 |
|---|---|---|---|
| gpt-5.4 | 70 | $0.885 | 입력 187,149 · 출력 27,803 토큰 |
| gpt-5.4-mini | 92 | $0.131 | 입력 68,940 · 출력 17,562 토큰 |
| decision-1 | 48 | — | 토큰 0 · 단가 미확인(집계 제외) |

- AI 비용 합계 $1.016, 신청당 $1.016 ÷ 24건 = **$0.0423**(`ops/reports/load-20261102110233/report.md` 2장).
- 이 값은 **캐시 할인 미반영 상한**이고 단가 미확인 호출 48건을 제외한 수치다(`docs/runbook/deploy-history.md:577`). 실패한 에이전트 호출은 토큰을 몰라 호출 수만 센다(`docs/runbook/deploy-history.md:602`).

## 게이트웨이 경로 건당 AI 비용

모든 LLM 호출을 APIM AI 게이트웨이로 보내면([ADR-010](adr/ADR-010-ai-gateway.md)) 같은 48건 부하(`ops load`)의 건당 AI 비용이 올랐다. 위 24건 실측과 다른 격자(48건)라 직접 호출 기준선도 P9 재측정값으로 비교한다.

| 경로 | 부하 실행 | p95(SLO1) | SLO2 | 건당 AI 비용 |
|---|---|---|---|---|
| 직접 호출(P9) | `load-20261104143855` | 1.4분 | 100%(144/144) | $0.0589 |
| 게이트웨이(최종 TPM) | `load-20261105120255` | 1.8분 | 100%(144/144) | $0.0939 |
| 게이트웨이, 분석가 `gpt-5.6-sol`(n=10) | `load-20261107122036` | 0.9분(표본 부족) | 100%(30/30) | $0.0738 |

- 증가 약 **+59%**($0.0589 → $0.0939). 게이트웨이(chat completions) 경로는 라운드마다 전체 맥락을 다시 보내고 도구 라운드도 늘어(`GATEWAY_MAX_ROUNDS = 12`), 이 부하에서 `gpt-5.4` 입력이 1.16M 토큰이었다. 캐시 할인은 반영하지 않은 상한이다.
- 같은 이유로 `gpt-5.4` 배포 용량을 50 → 200(종량제, 쿼터 250)으로 올렸고 분석가 TPM은 150,000이다. 용량은 고정비를 만들지 않는다.
- 2026-11-07부터 분석가는 `gpt-5.6-sol`(배포 150K TPM, `gw-analyst` 150K, 입력 $4·출력 $20/1M 토큰)이다. `gpt-5.4`($2.5·$15)보다 단가가 높지만 재측정(`ops/reports/load-20261107122036/report.md`, 10건)에서 건당 AI 비용은 오히려 $0.0939 → $0.0738로 내려갔다. 분석가 호출이 신청당 8.4회(입력 약 24K 토큰) → 4.3회(약 10K)로 줄었기 때문이다(분석 단계 신청당 $0.087 → $0.067). 데이터 수집 수정(`246d321`)으로 mini 호출은 신청당 5.5회 → 7.0회로 늘었다. 표본이 10건이고 기준선(48건 격자)과 구성이 달라 경향으로만 읽는다. 심사 평가(`eval/reports/job-eval-ngrkucj/report.md`)에서도 게이트웨이 경로 평가서 건당 비용이 `gpt-5.4` $0.0984(`eval/reports/job-eval-aqisk13/report.md`) → `gpt-5.6-sol` $0.0783이다. `gpt-6.1-sol`($2·$10)은 게이트웨이 경유 함수 도구 미지원으로 쓰지 않는다. 평가 full 구성도 `gpt-5.6-sol`이다(`EVAL_BUDGET_USD=5` 상한 그대로, 단가 `src/evals/prices.json`).
- 평가 비용: 조기경보 평가 `job-eval-adcwjhc` $1.84, 전체 평가 `job-eval-aqisk13` $2.11(직접 호출 기준선 `job-eval-zjmcwjv` $1.95).

## 월 고정비

- 측정값: **월 $304.73**(`ops/reports/load-20261102110233/report.md` 2장). Cost Management 실측 2026-10-25\~10-31 중 데이터가 있는 하루(10-31) × 30으로 환산했고, Foundry Models 사용량은 제외했다(`docs/runbook/deploy-history.md:578`).
- 한계: 리소스 그룹 비용 데이터가 있는 하루치만 평균했다. 일시 정지(`ops pause`)·배포 창 여닫기에 따라 크게 달라질 수 있다(`docs/runbook/deploy-history.md:604`).

아래는 초기 설계 단계의 리소스별 월 비용 추정표(`docs/runbook/deploy-history.md:62`)다. 측정 고정비 $304.73은 Cost Management 하루치 실측을 30배로 환산한 값이고, 이 추정표는 설계 시점의 리소스별 대략값이라 **집계 방식·시점이 달라 측정값과 직접 비교 대상은 아니다**. 참고용으로 나란히 둔다.

| 항목 | 월 비용(초기 추정) | 비고 |
|---|---|---|
| Service Bus Premium 1 MU | 약 $677 | 쓰지 않을 때 `ops pause`로 절감 |
| AI Search Basic | 약 $74 | Foundry 벡터 저장소 |
| ACR Premium | 약 $50 | Private Endpoint 필요 |
| API Management Developer | 약 $51 | AI 게이트웨이, SLA 없음([ADR-010](adr/ADR-010-ai-gateway.md)). 위 측정 고정비는 도입 전 값이라 이 금액이 들어 있지 않다 |
| Private Endpoint 약 17개 | 약 $125 | 개당 약 $7.3 |
| Functions Flex always ready 3개 | 약 $62 | `durable`·`officer_decision`·`start_application` 그룹 각 1개(2GB × $0.000004/GB-s 대기 단가). 심사역 결정·신청 접수의 콜드 스타트를 없앤다. 위 측정 고정비에는 들어 있지 않다 |
| 모델·Functions(always ready 외)·Container Apps·Cosmos serverless | 사용량 기반 | 유휴 시 거의 0 |
| Application Gateway WAF v2 | 약 $370 | 콘솔 진입점, 상시(고정 게이트웨이 시간 + 용량 단위). 위 측정 고정비에는 들어 있지 않다([ADR-011](adr/ADR-011-appgw-waf-entry.md)) |

## 물량별 단가

`ops/reports/load-20261102110233/report.md` 2장의 표를 그대로 옮긴다. 수작업 처리 원가는 $3,500/건(Mills & McCarthy, HBS Working Paper 15-004의 소상공인 대출 1건 처리 원가 $3,000\~4,000 중간값), 대출 1건은 USD 6,000으로 가정한다(상수는 `src/opsmetrics/cost.py`의 `MANUAL_USD`·`TICKET_USD`).

| 월 물량 | AI/건 | 고정비/건 | 합계/건 | 수작업 대비 | 대출 1건(USD 6,000) 대비 |
|---|---|---|---|---|---|
| 100 | $0.0423 | $3.0473 | $3.0896 | 0.1% | 0.1% |
| 1,000 | $0.0423 | $0.3047 | $0.3470 | 0.0% | 0.0% |
| 10,000 | $0.0423 | $0.0305 | $0.0728 | 0.0% | 0.0% |

반올림 요약: 월 100건 $3.09/건, 1,000건 $0.35/건, 10,000건 $0.07/건 — 수작업 $3,500/건 대비 0.1% 이하다(`docs/runbook/deploy-history.md:578`). 이 비교는 **기술 비용만** 수작업 전체 원가와 견준 것이다. 심사역 검토 인건비·운영 비용은 빠져 있어 취급 비용 절감률(원문 목표 60%)을 뜻하지 않는다([비즈니스 케이스](business-case.md#단위-경제건당-usd-6000-대출)).

게이트웨이 경로로 환산하면(건당 AI $0.0939, 고정비 $304.73 + APIM $51 = 월 $355.73, 두 값은 측정 시점이 달라 계산값이며 App Gateway WAF v2(약 $370)는 들어 있지 않다): 월 100건 약 $3.65/건, 1,000건 약 $0.45/건, 10,000건 약 $0.13/건으로 모두 수작업 대비 0.1% 이하다.

## 평가 비용

- P6a 평가 1회 실행(`job-eval-9imbutm`) 비용은 **$1.93**(단가 확인분, 예산 $5, `partial=False`, 모델 호출 341회)이다(`eval/reports/job-eval-9imbutm/report.md`, `docs/runbook/deploy-history.md:490`).
- 평가 비용은 운영 건당 비용과 별개다. 품질·안전·비용을 측정할 때만 들고, 데모·운영 흐름에는 포함되지 않는다.

## 절감 레버

- **일시 정지(`ops pause`)**: 쓰지 않을 때 Service Bus Premium을 내려 고정비의 가장 큰 항목을 없앤다(초기 추정 약 $677). pause 5.8분·resume 4.7분(`docs/runbook/deploy-history.md:55`). pause 중에도 남는 비용 항목은 AI Search·ACR·Private Endpoint 등 상주 리소스다.
- **게이트웨이 끄기**: 데모를 쉬는 기간에는 `AI_GATEWAY_ROUTING=false`로 프로비저닝한 뒤 APIM을 삭제하면 월 약 $51을 아낀다(재생성 30–60분, 실측 첫 생성 27분). 롤백 절차는 [배포](runbook/deploy.md#ai-게이트웨이-켜기끄기)에 있다.
- **배포 창 운영**: public 접근을 배포 창에만 잠깐 열고 닫는다. 주 목적은 비용 절감이 아니라 공격 표면 관리이며(상주 게이트웨이를 두지 않는 운영 방식), 이 자체의 비용 효과는 측정하지 않았다.
- **모델 등급(정성적 설계 선택)**: 규칙으로 못 푼 적요만 LLM에 보내고, 먼저 선택형 분류 모델(`decision-1`)에 물은 뒤 기권·낮은 확신(0.7 미만)만 `gpt-5.4-mini` 에이전트(`txn-classifier`)가 보완한다([ADR-005](adr/ADR-005-classification-model.md)). 비용을 염두에 둔 등급 선택이지만 mini 단독 대비 신청당 호출 수는 측정했다(8.375회 vs 1.000회, 오히려 더 많음). `decision-1`은 공개 단가를 확인하지 못해 **비용 이점은 검증하지 못했다**. 평가에서 세 구성은 천장 효과로 품질이 같아 비용·지연으로만 비교한다(`eval/reports/job-eval-9imbutm/report.md`).
- **분류 캐시**: 분류 결과를 Cosmos에 캐시해 같은 적요의 재호출을 줄인다(토큰이 든 적요·이체 적요는 캐시 제외).
- **PTU 손익분기**: 물량이 충분히 크면 Global Standard 종량제 대신 PTU(프로비저닝) 전환이 유리해지는 지점이 생긴다. 다만 데모 표본으로는 손익분기를 산출할 근거가 없어 정성적 과제로만 남긴다(새 수치를 만들지 않는다. [ADR-002](adr/ADR-002-model-deployment.md)).
- **App Gateway 끄기**: 데모를 오래 쉬면 게이트웨이를 지워 월 약 $370을 아낄 수 있다(공용 IP는 남겨 DNS 이름 유지, 다시 `window open`으로 생성). 상시 진입점이 요구라 기본값은 상시 운영이다.

---

관련 문서: [아키텍처](architecture.md) · [한계와 운영 전환](limitations.md) · [증거 색인](evidence.md) · [운영 실행 기록](runbook/deploy-history.md)
