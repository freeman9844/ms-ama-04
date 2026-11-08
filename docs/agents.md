# 에이전트

8개 Foundry 프롬프트 에이전트가 신청·사후 단계 안에서 자율 판단한다. 흐름 자체는 Durable 오케스트레이터가 결정론적으로 관리하고(아래 [아키텍처](architecture.md) 참고), 에이전트는 설명·구조 제안·분류·안내문만 만든다. 신용 규칙과 최종 결정은 LLM 밖에 있다.

> **합성 데이터 기준.** 모델 선택 근거 수치는 `eval/reports/job-eval-9imbutm/report.md`에서 인용한다. 같은 시드에서도 LLM 응답 변동이 있다.

> 상위 Spec 대비: 설계 Spec §3은 에이전트를 5종으로 묶었으나, 구현은 `src/agents/`에 8개 `AgentSpec`으로 분리했다(분류·검토를 별도 에이전트로, 동의 안내와 이의 회신을 각각). 코드가 기준이다.

| 에이전트 | 단계 | 자율성 | 모델(기본) | 도구(handler) | 쓰기 권한 | 실패 시 동작 | 코드 |
|---|---|---|---|---|---|---|---|
| `data-assembly` | 데이터 수집 | L3 | `gpt-5.4-mini` | `fetch_source` | 없음(읽기) | 안전망 수집 + `agent_fallback` 플래그 | `src/agents/data_assembly.py` |
| `txn-classifier` | 거래 분류 | L2 | `gpt-5.4-mini`(폴백), `decision-1`(주) | 없음(구조화 출력) | 없음 | 규칙 분류만으로 심사 계속 | `src/agents/txn_classify.py`, `src/agents/classify.py` |
| `underwriting-analyst` | 신용 평가 | L2 | `gpt-5.6-sol` | `get_metrics`, `get_monthly_series`, `query_transactions`, `code_interpreter` | 없음 | 1라운드 실패 시 평가 없음 → 1회 재시도 후 신청 `failed` | `src/agents/underwriting.py` |
| `underwriting-reviewer` | 신용 평가 | L2 | `gpt-5.4-mini` | 없음 | 없음 | 3라운드 초과 시 "검토 미통과" | `src/agents/underwriting.py` |
| `early-warning-investigator` | 사후 조기경보 | L4 | `gpt-5.4` | `get_weekly_series`, `get_baseline`, `get_peer_cohort`, `query_transactions`, `raise_alert` | 경보(`raise_alert`) | 탐지기 단독 medium 경보(fail-safe) | `src/agents/early_warning.py` |
| `collections-agent` | 사후 추심 | L3 | `gpt-5.4-mini` | `get_case`, `get_contact_history`, `list_scripts`, `send_script`, `route_hardship` | 스크립트 발송·재조정 요청 | 최근 경보 high면 `hardship_offer`, 아니면 `missed_payment`. 재조정 없음 | `src/agents/collections.py` |
| `consent-notice-agent` | 동의 안내 | L4 | `gpt-5.4-mini` | 없음 | 없음 | 템플릿 문안 대체 | `src/agents/notices.py` |
| `grievance-reply-agent` | 이의 회신 | L3 | `gpt-5.4-mini` | 없음 | 없음 | 템플릿 문안 대체 | `src/agents/notices.py` |

자율성 등급은 상위 Spec §3.1 기준이다. 모델 이름은 `infra/resources.bicep`의 오케스트레이터 설정(`DATA_ASSEMBLY_MODEL` 등)과 `DECISION_DEPLOYMENT`에서 왔다.

## 데이터 수집

- **목적:** 동의 토큰 범위 안의 원천(프로필·거래·세무·PG)을 모으고 완결성·이상 징후를 보고한다(L3).
- **도구 allowlist:** `fetch_source`(`src/agents/data_assembly.py`). 핸들러는 `AssemblySession.fetch_source`로, 동의 범위 밖 원천은 `scope_not_consented`를 돌려주고, `unavailable`은 최대 `MAX_ATTEMPTS=3`회 재시도한다.
- **가드레일:** 원본은 세션에 남고 LLM에는 가명처리한 요약만 간다(`build_pseudonymizer`로 상호·대표자·사업자번호·적요 속 이름을 토큰화). 적요 안 "지시처럼 보이는 문장"은 따르지 않고 anomalies에 기록. 승인·한도·금리는 판단·언급 금지.
- **완결성은 코드가 계산:** `status`(complete/incomplete)와 `missing_sources`는 결정적으로 산출한다. 에이전트 판단은 `agent_assessment`로만 남는다.
- **알려진 한계:** 에이전트가 빠뜨린 원천은 안전망(`safety_net`)이 동의 범위 안에서 마저 모은다. 에이전트의 `status`가 코드 산출과 다르면 `agent_assessment_mismatch` 플래그를 남긴다(결정에는 영향 없음, 사람이 본다, 콘솔 라벨 "데이터 수집 에이전트 보고와 코드 검증 불일치"). 러너가 없거나 실패하면 `agent_fallback`.
- **불일치 원인(2026-11-07 분석):** 예전 입력은 동의 범위 이름(`account, pg, tax`)과 원천 이름(`profile, transactions, tax, pg`)을 따로 줬다. 모델은 profile·transactions가 동의 범위 밖이라고 보고 건너뛴 뒤 `incomplete`로 보고했고, 안전망이 마저 모아 코드 판정은 `complete`가 되어 플래그가 거의 모든 신청에 붙었다(Foundry 트레이스 2일치: 161건 중 `complete` 보고 17건). 이제 입력은 원천마다 속한 동의를 `profile(account 동의)` 형식으로 적고 모두 수집하라고 지시한다(`_agent_input`).
- **모델 근거:** 데이터 수집은 평가 suite 대상이 아니며 저비용 `gpt-5.4-mini`를 쓴다.

## 거래 분류

- **목적:** 규칙으로 못 나눈 비정형 적요를 고정 카테고리로 분류한다. 거래마다 LLM을 호출하지 않는다(심사 지표 산출 단계의 하위 단계).
- **Decision-1 경로(`src/agents/classify.py` → `src/agents/txn_classify.py`):**
  1. 결정적 규칙 `classify-v1`(`classify` 함수, 거래유형 코드 + 적요 키워드)로 먼저 분류하고 미분류(`unclassified_in/out`)만 대상으로 한다.
  2. 가명처리 → 정규화 → (업종, 방향, 거래유형, 적요) 단위 중복 제거.
  3. 캐시 재사용(가명 토큰·이체 적요 `TRANSFER_IN/OUT`은 캐시에 넣지도 찾지도 않음).
  4. 고유 적요마다 Microsoft-Decision-1(`DECISION_DEPLOYMENT=decision-1`, REST `/providers/microsoft/v1/systemone`)에 고정 선택지(+`cannot_tell`)로 병렬 질의.
  5. 기권·저신뢰(`DEFAULT_THRESHOLD=0.7`)·오류만 mini LLM 폴백 에이전트 `txn-classifier`에 한 번에 묻고, 그래도 모르면 미분류.
- **가드레일:** 모델로 가는 적요는 가명처리. 적요는 데이터일 뿐 지시가 아니라고 지시문에 명시. 특성 반영은 보수적(출금의 부채상환·고금리 상환만 override).
- **모델 근거:** `eval/reports/job-eval-9imbutm/report.md` 2장 — 규칙만으로 80.0% 분류, 캐시 적중 25.0%. `decision-1+gpt-5.4-mini` 구성은 범용 LLM 폴백 비율 25.0%(mini 단독 100.0%)로 범용 LLM 호출을 줄인다. `decision-1+gpt-5.4-mini` 구성의 분류 호출은 신청당 8.375회(Decision-1 7.5회 + mini 폴백)지만 Decision-1 단가 미확인이라 건당 총비용 효과는 미검증이다(보고서의 mini 단독 $0.0123·1.000회 vs `decision-1+mini` $0.0047은 Decision-1 단가를 제외한 값). 세 구성 모두 macro F1 1.000(천장 효과)이라 품질이 아니라 폴백 비율·지연으로 가린다.

## 심사(분석가·검토자)

- **목적:** 정책 판정을 근거로 분석가가 설명·대출 구조·반사실을 만들고, 검토자가 근거(grounding)를 검증한다(신용 평가 단계, L2). **결정 권한 없음** — 최종은 심사역.
- **분석가 도구 allowlist(`src/agents/underwriting.py`):** `get_metrics`, `get_monthly_series`, `query_transactions`(가명 거래, 최대 `MAX_TXNS=20`), 그리고 Foundry 내장 `code_interpreter`. 쓰기 도구 없음.
- **검토자:** 도구 없음. 주장별 근거 충족을 예/아니오로 판정한다.
- **반성 패턴:** 결정적 grounding 검사(`check_grounding` + `src/agents/grounding.py`의 숫자 대조, 허용 오차 2%) → 통과 시 검토자 → 지적 사항을 다음 라운드 입력으로, 최대 `MAX_ROUNDS=3`회. 3회 초과 시 `review_not_passed`로 심사역에게 넘긴다.
- **가드레일:** 모델로 가는 모든 문자열은 가명처리. Code Interpreter 컨테이너는 실행마다 즉시 삭제(`delete_container`). 근거 목록에 없는 숫자(직접 계산 포함) 사용 금지. 정책이 ineligible이면 추천은 decline/refer만. 제안 구조의 기간은 1~12개월(`check_grounding`의 구조 검사, P9). 권고 `conditional`은 남아 있지만 심사역 결정에는 조건부 승인이 없으므로 콘솔은 "승인 권고(조건 제안)"로 표시한다.
- **알려진 한계:** 평가에서 1라운드 근거 통과율이 모델에 민감하다. 평가 활동은 예외를 올리지 않는다(1라운드 실패면 평가 없음, 이후 실패면 직전 초안을 미통과로). 평가가 없으면 오케스트레이터가 한 번 더 돌리고, 그래도 없으면 `assessment_unavailable` 플래그와 함께 신청을 `failed`로 끝낸다. 심사역은 에이전트 평가를 근거로 결정하므로 평가 없는 건을 심사역 대기로 넘기지 않는다(2026-11-07).
- **모델 근거:** `eval/reports/job-eval-9imbutm/report.md` 3장(검토자 `gpt-5.4-mini` 고정) — 분석가 `gpt-5.4`는 1라운드 근거 통과 100.0%·검토 통과 87.5%·건당 $0.0631, `gpt-5.4-mini`는 62.5%·62.5%·$0.0199. 품질 차이 때문에 분석가는 `gpt-5.4`, 검토자는 `gpt-5.4-mini`로 두었다.
- **모델 변경(2026-11-06):** 분석가를 `gpt-6.1-sol`(GlobalStandard 150K TPM, 단가 입력 $2·출력 $10/1M 토큰으로 `gpt-5.4`의 $2.5·$15보다 낮음)로 바꿨다(`ANALYST_MODEL`). 검토자는 그대로 `gpt-5.4-mini`. 평가 Job의 full 구성(`EVAL_FULL_MODEL`)은 `gpt-5.6-sol`이다. 변경 뒤 라이브 점검은 [배포 기록](runbook/deploy-history.md)에 있다.
- **모델 재변경(2026-11-07):** `gpt-6.1-sol`은 게이트웨이 연결(Agent Service → APIM, chat completions)에서 함수 도구와 추론을 함께 쓰면 400("Function tools with reasoning_effort are not supported … in /v1/chat/completions")으로 거부한다. `reasoning.effort=none`은 이 모델이 지원하지 않는다(low·medium·high만). 그 사이 들어온 신청 2건은 평가 없이 심사역 대기로 넘어갔다. 분석가를 게이트웨이에서 함수 도구가 동작하는 `gpt-5.6-sol`(입력 $4·출력 $20/1M 토큰, 배포 150K TPM, 평가 Job과 공유)로 옮겼다. 이제 분석가와 평가 full 구성이 같은 모델이다. `gpt-6.1-sol` 배포(50K)는 보유만 한다.
- **재측정(2026-11-07, 게이트웨이 경로, 심사 8케이스):** `eval/reports/job-eval-ngrkucj/report.md` 3장 — `gpt-5.6-sol`은 1라운드 근거 통과 100%·최종 100%·검토 통과 100%·평균 1.12라운드·평가서 건당 $0.0783이다. 같은 경로의 `gpt-5.4`(`eval/reports/job-eval-aqisk13/report.md`)는 75.0%·100%·87.5%·2.00라운드·$0.0984였다. 정책 일치·세무 불일치 탐지는 둘 다 100%, 판정자 G/R/C는 3.88/4.75/4.62 대 3.75/5.00/4.62. 대신 호출 p95 지연이 11.7초로 길다(mini 4.6초). 단일 실행이라 LLM 변동 범위 안일 수 있다.

## 조기경보

- **목적:** 통계 탐지기 트리거가 있을 때 의미 기반으로 스트레스를 조사하고 경보 여부·심각도·권고를 정한다(L4, 사후).
- **트리거:** `inflow_drop`·`new_debt`·`buffer_drop`에 더해, 결정적 4주 잔고 전망(`src/agents/forecast.py`의 `project`)이 0원 아래로 내려가면 `projected_shortfall`. 전망은 심사 시점 정산 프로필(채널별 정산 지연·수수료)과 최근 3개월 고정 지출 일정으로 만들며 LLM을 쓰지 않는다. 근거 지표는 `W:<주차>:<지표>`(주간 실측)와 `F:<주차>:projected_min_balance`(전망)를 인용한다.
- **도구 allowlist(`src/agents/early_warning.py`):** `get_weekly_series`, `get_baseline`, `get_peer_cohort`, `query_transactions`, `raise_alert`. `raise_alert`는 결정적 grounding 검사를 통과해야 `accepted=true`이고, 첫 성공만 유효하다.
- **가드레일:** 모델 입력 가명처리. 근거 검사 실패 시 `issues`를 돌려줘 같은 실행에서 고치게 한다.
- **실패 시 동작:** 에이전트를 쓸 수 없으면 탐지기 결과만으로 medium 경보를 올린다(fail-safe: 사람이 본다).
- **모델 근거:** `eval/reports/job-eval-9imbutm/report.md` 4장 — `gpt-5.4`/`gpt-5.4-mini` 모두 양성 경로 precision·recall 1.000, 탐지기 단독은 precision 0.500(FP 3). 리드 타임은 합성 경로에서 평균 약 70일(양성 2~3건)로 원문의 60~90일 범위 안이었다. 연체가 나도록 설계한 합성 시나리오라 실제 부도 이력으로 검증한 것은 아니다([비즈니스 케이스](business-case.md)). 기본 `gpt-5.4`(`EW_MODEL`).

## 추심

- **목적:** 상환 원장에서 미납이 확정된 회차(추심 건 `rc-<재생>-i<회차>`)를 받아 승인된 스크립트로만 연락하고, 상환 곤란이면 재조정 요청을 심사역에게 넘긴다(L3). 재조정 결정은 사람만 한다.
- **언제 도는가(2026-11-07):** 상환일 당일 `에이엠에이렌딩 대출상환` 출금이 없을 때(미납)만 돈다(`repayment.updated`, `src/agents/repayment.py`). 상환 안내(`payment_reminder`)는 상환일 3일 전에 LLM 없이 보낸다(`remind`, 공정 추심 규칙에 걸리면 `deferred`). 조기경보만으로는 차주에게 연락하지 않는다. 경보는 심사역 검토용이고, 추심 에이전트는 `get_case`로 최근 경보 요약과 신호 종류(`signals`, 예: `loan_stacking`·`inflow_drop`)를 참고만 한다. 조사 폴백 경보는 요약에 근거가 없어 신호 종류로 판단한다(2026-11-08).
- **재생 정지·재개(P9):** 13주 재생 중 재조정 요청이 생기면 재생이 그 주에서 멈추고(대출 `paused`, 콘솔 "재조정 대기"), 심사역이 승인/거절하면 `awaiting_resume`으로 바뀌어 "이어서 재생"/"여기서 종료"를 고른다. 기다리는 사이 심사 동의가 만료·철회되면 재개 요청은 이유를 오류로 남기고 재생을 종료한다(`stopped`). 재조정 결과(유예·분할)는 이후 재생 흐름에 반영하지 않는다(`docs/limitations.md`).
- **도구 allowlist(`src/agents/collections.py`):** `get_case`(회차·상환일·금액·누적 미납·최근 경보 요약·신호 종류), `get_contact_history`, `list_scripts`, `send_script`(승인 스크립트 enum만), `route_hardship`(`revenue_shock`/`illness_or_disaster`/`debt_overload`).
- **가드레일:** 공정 추심 규칙을 도구 안에서 결정적으로 강제 — 7일 `MAX_CONTACTS_PER_7_DAYS=7`회, `CONTACT_HOURS` 08:00~21:00(KST) 밖이면 발송 보류(`deferred`). 자유 문장 발송 불가, 승인 스크립트만. `send_script`는 한 번만 성공.
- **실패 시 동작:** 에이전트를 쓸 수 없으면 최근 경보가 high면 `hardship_offer`, 아니면 `missed_payment`를 보내고 재조정 요청은 만들지 않는다.
- **모델 근거:** 스크립트 선택·분류 중심이라 `gpt-5.4-mini`(`COLLECTIONS_MODEL`).

## 동의 안내

- **목적:** 동의 만료 예정 갱신 안내와 삭제 완료 안내문을 쉬운 한국어로 쓴다(L4, 동의 수명주기).
- **도구:** 없음. 사실(facts)만 받아 쓴다(`src/agents/notices.py`의 `write_notice`, `consent_facts`).
- **가드레일:** 개인 식별 정보는 넘기지 않음. 결과를 결정적으로 검사(필수 사실 포함 여부, 근거 없는 숫자 금지). 검사 통과 못 하면 템플릿 문안(`source="template"`)을 쓴다. 대화는 쓰고 바로 삭제(`_delete`).
- **모델 근거:** 안내문 작성이라 `gpt-5.4-mini`(`NOTICE_MODEL`).

## 이의 회신

- **목적:** 판단 기록(정책 사유·반사실)과 심사역 결정만으로 이의제기 회신 초안을 쓴다(L3).
- **도구:** 없음. `grievance_facts`로 만든 사실만 입력하며 차주가 쓴 이의 내용은 넣지 않는다.
- **가드레일:** 입력에 없는 숫자·금리·약속 금지. 검사 미통과 시 템플릿 대체. 대화 즉시 삭제.
- **모델 근거:** `gpt-5.4-mini`(`GRIEVANCE_MODEL`).

## 공통 런타임

- **도구 호출 루프(`src/agents/runtime.py`):** 프롬프트 에이전트를 REST로 돌린다. 버전 생성(`POST /agents/{name}/versions`) → 대화 생성 → 응답 생성(`agent_reference` + conversation). `function_call`이 있으면 VNet 안에서 핸들러를 실행해 `function_call_output`으로 되돌리며, 최대 `max_rounds=6` 라운드(직접 호출 기본값. AI 게이트웨이를 거치는 에이전트는 아래 `GATEWAY_MAX_ROUNDS = 12`). 도구 예외 내용은 모델에 노출하지 않는다(`{"error": ...}`만 반환). 출력은 `output_model`로 스키마 검증한다.
- **대화·컨테이너 삭제:** `delete_conversation`은 입력·출력 항목을 포함해 대화를 지우고(`deleted`/`absent`/`failed`), `delete_container`는 Code Interpreter 컨테이너(가명 집계 데이터가 올라간 곳)를 즉시 삭제한다. 동의 철회·수명주기 종료 시 남은 Foundry 대화를 정리한다.
- **계량(`src/orchestrator/journey/metering.py`):** `MeteredRunner`가 호출마다 모델·입출력 토큰·라운드를 모으고, `step_records`가 단계 기록(`steps[]`)으로 바꾼다. 거래 분류는 심사 지표 산출의 하위 단계(`classify`)로 분리 기록하고, Decision-1 호출 수를 `decision_calls`로 센다. 단계 기록에는 예외 종류·표준 코드만 남긴다(`safe_detail`).
- **AI 게이트웨이(`src/agents/gateway.py`, [ADR-010](adr/ADR-010-ai-gateway.md)):** `AI_GATEWAY=apim`이고 `AI_GATEWAY_URL`이 있으면 `GatewayRunner`가 모델 이름을 `gw-<agent>/<model>`로 바꿔 Foundry BYOM 연결로 APIM을 거친다(결과의 모델은 원래 배포 이름으로 되돌려 계량·단가에 쓴다). 거래 분류 Decision-1은 `AI_GATEWAY_URL`의 decision API를 직접 부르고(콘텐츠 안전 없음), 채점자(quality-judge)는 직접 호출한다. 429·403은 `GatewayThrottled`·`GatewayBlocked`로 구분한다. 연결표(`CONNECTIONS`)와 최종 TPM(`infra/resources.bicep`의 `gateways`):

  | 에이전트 | 연결 | TPM |
  |---|---|---|
  | `underwriting-analyst` | `gw-analyst` | 150,000 |
  | `early-warning-investigator` | `gw-early-warning` | 100,000 |
  | `underwriting-reviewer` | `gw-reviewer` | 15,000 |
  | `data-assembly` | `gw-data-assembly` | 17,000 |
  | `txn-classifier` | `gw-txn-classifier` | 20,000 |
  | `collections-agent` | `gw-collections` | 10,000 |
  | `consent-notice-agent` | `gw-consent-notice` | 5,000 |
  | `grievance-reply-agent` | `gw-grievance` | 5,000 |
  | (프로브 전용) | `gw-probe` | 1,000 |

- **도구 라운드 상한:** 직접 호출은 `max_rounds=6`을 유지한다. 게이트웨이(chat completions) 경로에서는 모델이 턴당 도구 호출을 덜 해서 조기경보 조사가 모든 주차에서 6라운드 상한에 걸렸기 때문에, 라우팅되는 에이전트(`GatewayRunner`, 평가의 `EvalRunner`)에만 `GATEWAY_MAX_ROUNDS = 12`를 적용한다. 대가는 조사 한 번당 최대 2배의 라운드·토큰이다(건당 AI 비용 변화는 [비용 모델](cost-model.md#게이트웨이-경로-건당-ai-비용)).
