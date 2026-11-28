# 에이전트

10개 Foundry 프롬프트 에이전트가 신청·사후 단계와 심사역 콘솔에서 자율 판단한다. 흐름 자체는 Durable 오케스트레이터가 결정론적으로 관리하고(아래 [아키텍처](architecture.md) 참고), 에이전트는 심사 의견·설명·분류·안내문을 만든다. 신용 규칙과 최종 결정은 LLM 밖에 있다.

> **합성 데이터 기준.** 모델 선택 근거 수치는 `eval/reports/job-eval-9imbutm/report.md`에서 인용한다. 같은 시드에서도 LLM 응답 변동이 있다.

> 상위 Spec 대비: 설계 Spec §3은 에이전트를 5종으로 묶었으나, 구현은 `src/agents/`에 10개 `AgentSpec`으로 분리했다(분류·검토를 별도 에이전트로, 동의 안내와 이의 회신을 각각, 콘솔 심사역 채팅과 영문 화면 번역을 추가). 코드가 기준이다.

| 에이전트 | 단계 | 자율성 | 모델(기본) | 도구(handler) | 쓰기 권한 | 실패 시 동작 | 코드 |
|---|---|---|---|---|---|---|---|
| `data-assembly` | 데이터 수집 | L3 | `gpt-5.6-luna` | `fetch_source` | 없음(읽기) | 안전망 수집 + `agent_fallback` 플래그 | `src/agents/data_assembly.py` |
| `txn-classifier` | 거래 분류 | L2 | `gpt-5.6-luna`(폴백), `decision-1-1`(주) | 없음(구조화 출력) | 없음 | 규칙 분류만으로 심사 계속 | `src/agents/txn_classify.py`, `src/agents/classify.py` |
| `underwriting-analyst` | 신용 평가 | L2 | `gpt-5.6-sol` | `get_metrics`, `get_monthly_series`, `get_settlement_summary`, `query_transactions`, `code_interpreter` | 없음 | 1라운드 실패 시 평가 없음 → 1회 재시도 후 신청 `failed` | `src/agents/underwriting.py` |
| `underwriting-reviewer` | 신용 평가 | L2 | `gpt-5.6-luna` | 없음 | 없음 | 검토 미통과 시 의견 없음 | `src/agents/underwriting.py` |
| `early-warning-investigator` | 사후 조기경보 | L4 | `gpt-5.6-luna` | `get_weekly_series`, `get_baseline`, `get_peer_cohort`, `get_settlement_ledger`, `query_transactions`, `raise_alert` | 경보(`raise_alert`). 높음 경보의 단계별 자동 안내는 LLM 밖 결정적 정책(`src/agents/escalation.py`) | 탐지기 단독 medium 경보(fail-safe) | `src/agents/early_warning.py` |
| `collections-agent` | 사후 추심 | L3(제안만, 발송은 심사역 승인) | `gpt-5.6-luna` | `get_case`, `get_contact_history`, `list_scripts`, `propose` | 제안만(`propose`). 발송·재조정 요청은 심사역 승인 뒤 | 최근 경보 high면 `hardship_offer`, 아니면 `missed_payment` 폴백 제안(재조정 상담 없음). 폴백 제안도 심사역 승인 필요 | `src/agents/collections.py` |
| `consent-notice-agent` | 동의 안내 | L4 | `gpt-5.6-luna` | 없음 | 없음 | 템플릿 문안 대체 | `src/agents/notices.py` |
| `grievance-reply-agent` | 이의 회신 | L3 | `gpt-5.6-luna` | 없음 | 없음 | 템플릿 문안 대체 | `src/agents/notices.py` |
| `officer-assistant` | 심사역 결정 채팅(콘솔) | L1 | `gpt-6-luna` | 없음(가명 스냅샷만) | 없음 | 안내 문구 반환(429·차단·실패 구분), 결정에는 영향 없음 | `src/agents/officer_chat.py`, `src/console/officer_chat.py` |
| `console-translator` | 영문 화면 데이터 번역(콘솔 즉시 번역, Functions 사전 번역) | L1 | `gpt-6-luna` | 없음(가명 문구만) | 없음(번역 저장은 변경 피드 트리거가 `translations`에) | 원문(한국어) 그대로 표시 | `src/agents/translator.py`, `src/console/translate.py`, `src/orchestrator/journey/pretranslate.py` |

자율성 등급은 상위 Spec §3.1 기준이다. 모델 이름은 `infra/resources.bicep`의 오케스트레이터 설정(`DATA_ASSEMBLY_MODEL` 등)과 `DECISION_DEPLOYMENT`에서 왔다.

> **모델 변경(2026-11-08):** 소형 에이전트(데이터 수집·분류 폴백·검토자·추심·동의 안내·이의 회신)와 평가 mini 구성을 `gpt-5.4-mini`에서 `gpt-5.6-luna`(GlobalStandard 100K TPM)로 바꿨다. 단가는 OpenAI API 공개가 입력 $0.20·출력 $1.20/1M 토큰(mini $0.75·$4.50)이며 Azure 단가는 확인하지 못했다. 이 문서의 평가 수치는 변경 전 `gpt-5.4-mini`로 잰 것이고 luna 품질은 아직 재측정하지 않았다.

## 데이터 수집

- **목적:** 동의 토큰 범위 안의 원천(프로필·거래·세무·PG·마켓 판매 성과)을 모으고 완결성·이상 징후를 보고한다(L3).
- **도구 allowlist:** `fetch_source`(`src/agents/data_assembly.py`). 핸들러는 `AssemblySession.fetch_source`로, 동의 범위 밖 원천은 `scope_not_consented`를 돌려주고, `unavailable`은 최대 `MAX_ATTEMPTS=3`회 재시도한다.
- **가드레일:** 원본은 세션에 남고 LLM에는 가명처리한 요약만 간다(`build_pseudonymizer`로 상호·대표자·사업자번호·적요 속 이름을 토큰화). 적요 안 "지시처럼 보이는 문장"은 따르지 않고 anomalies에 기록. 승인·한도·금리는 판단·언급 금지.
- **완결성은 코드가 계산:** `status`(complete/incomplete)와 `missing_sources`는 결정적으로 산출한다. 에이전트 판단은 `agent_assessment`로만 남는다.
- **알려진 한계:** 에이전트가 빠뜨린 원천은 안전망(`safety_net`)이 동의 범위 안에서 마저 모은다. 에이전트의 `status`가 코드 산출과 다르면 `agent_assessment_mismatch` 플래그를 남긴다(결정에는 영향 없음, 사람이 본다, 콘솔 라벨 "데이터 수집 에이전트 보고와 코드 검증 불일치"). 러너가 없거나 실패하면 `agent_fallback`.
- **불일치 원인(2026-11-07 분석):** 예전 입력은 동의 범위 이름(`account, pg, tax`)과 원천 이름(`profile, transactions, tax, pg`)을 따로 줬다. 모델은 profile·transactions가 동의 범위 밖이라고 보고 건너뛴 뒤 `incomplete`로 보고했고, 안전망이 마저 모아 코드 판정은 `complete`가 되어 플래그가 거의 모든 신청에 붙었다(Foundry 트레이스 2일치: 161건 중 `complete` 보고 17건). 이제 입력은 원천마다 속한 동의를 `profile(account 동의)` 형식으로 적고 모두 수집하라고 지시한다(`_agent_input`).
- **모델 근거:** 데이터 수집은 평가 suite 대상이 아니며 저비용 `gpt-5.6-luna`를 쓴다.

## 거래 분류

- **목적:** 규칙으로 못 나눈 비정형 적요를 고정 카테고리로 분류한다. 거래마다 LLM을 호출하지 않는다(심사 지표 산출 단계의 하위 단계).
- **Decision 경로(`src/agents/classify.py` → `src/agents/txn_classify.py`):**
  1. 결정적 규칙 `classify-v1`(`classify` 함수, 거래유형 코드 + 적요 키워드)로 먼저 분류하고 미분류(`unclassified_in/out`)만 대상으로 한다.
  2. 가명처리 → 정규화 → (업종, 방향, 거래유형, 적요) 단위 중복 제거.
  3. 캐시 재사용(가명 토큰·이체 적요 `TRANSFER_IN/OUT`은 캐시에 넣지도 찾지도 않음).
  4. 고유 적요마다 Microsoft-Decision-1.1(`DECISION_DEPLOYMENT=decision-1-1`, 2026-11-28 Decision-1에서 전환, REST `/providers/microsoft/v1/systemone`)에 고정 선택지(+`cannot_tell`)로 병렬 질의.
  5. 기권·저신뢰(`DEFAULT_THRESHOLD=0.7`)·오류만 mini LLM 폴백 에이전트 `txn-classifier`에 한 번에 묻고, 그래도 모르면 미분류.
- **가드레일:** 모델로 가는 적요는 가명처리. 적요는 데이터일 뿐 지시가 아니라고 지시문에 명시. 특성 반영은 보수적(출금의 부채상환·고금리 상환만 override).
- **모델 근거:** `eval/reports/job-eval-9imbutm/report.md` 2장 — 규칙만으로 80.0% 분류, 캐시 적중 25.0%. `decision-1+gpt-5.4-mini` 구성은 범용 LLM 폴백 비율 25.0%(mini 단독 100.0%)로 범용 LLM 호출을 줄인다. `decision-1+gpt-5.4-mini` 구성의 분류 호출은 신청당 8.375회(Decision-1 7.5회 + mini 폴백)지만 Decision-1 단가 미확인이라 건당 총비용 효과는 미검증이다(보고서의 mini 단독 $0.0123·1.000회 vs `decision-1+mini` $0.0047은 Decision-1 단가를 제외한 값). 세 구성 모두 macro F1 1.000(천장 효과)이라 품질이 아니라 폴백 비율·지연으로 가린다. Decision-1.1 전환 비교(같은 suite, 2026-11-28)는 정확도 99.3% → 100.0%, LLM 폴백 비율 26.9% → 3.9%였다([ADR-005](adr/ADR-005-classification-model.md#decision-11-전환2026-11-28)).

## 심사(분석가·검토자)

- **목적:** 분석가는 “현재 근거로 신청액 전액을 승인할 수 있는가?”에 답하고 검토자는 그 근거를 검증한다. v2 권고는 `approve`(전액 승인 권고), `refer`(판단 보류), `decline`(전액 승인 비권고) 중 하나다. 대체 금액·기간·상환 구조를 제안하지 않는다. **최종 결정은 심사역이 한다.**
- **분석가 도구 allowlist(`src/agents/underwriting.py`):** `get_metrics`, `get_monthly_series`, `get_settlement_summary`(정산 원장 대사 요약, 집계만), `query_transactions`(가명 거래, 최대 `MAX_TXNS=20`), 그리고 Foundry 내장 `code_interpreter`. 쓰기 도구 없음.
- **검토자:** 도구 없음. 주장별 근거 충족을 예/아니오로 판정한다. 주장이 월별 계절 지수(`S:YYYY-MM:index`)를 하나라도 인용하면 입력에 전체 월별 지수와 기준(`seasonal_index`, 1.0 = 관측 기간 월평균)을 함께 넣어 "최저·낮다" 같은 비교를 판정할 수 있게 한다(2026-11-12, 최근 16건 중 추가 라운드 10건의 9건이 이 비교 주장 반려였다).
- **반성 패턴:** 결정적 정책·수치 검사(`check_grounding`, 숫자 허용 오차 2%) → 독립 검토 → 지적 사항을 다음 초안에 반영하며 최대 3라운드 진행한다. 통과한 초안만 의견서가 된다.
- **요약:** `summary`는 6~8문장 안에서 현금흐름·정산·셀러 신호가 전액 권고를 뒷받침하거나 약화하는 이유를 설명한다. 남은 불확실성과 확인할 자료를 밝힌다. 신청액·정책 한도·입금액은 근거로 인용할 수 있지만 어느 필드에서도 새 대출 금액을 제안하지 않는다.
- **화면·집계:** 독립 검토를 통과한 v2만 신청액 전액 권고로 표시한다. 무버전·v1은 ‘과거 의견’, 미검토·미생성은 ‘의견 없음’이다. 두 경우 모두 현행 권고율·권고/결정 비교에서 제외한다. 과거 본문·제안 구조와 반려 초안은 번역·현재 화면·도우미로 전달하지 않는다. 원본 감사 기록과 실제 심사역 승인액은 유지한다.
- **확인 사항:** `officer_checks`는 정산·[셀러 지표](#마켓-셀러-지표)와 연결된 확인 대상과 목적을 0~3개 적는다. 각 항목은 1~200자 문장과 근거 ID를 포함한다. 어느 자료도 없으면 빈 배열이다. 검토자는 모든 항목의 근거를 확인한다.
- **참고 지표(watch, 2026-11-17):** 정책 판정에 쓰지 않는 지표 중 값이 0보다 크고 하한 미만인 것을 `get_metrics`의 `watch`(`[{metric_id, observed}]`)로 알린다. 지금은 세금·계좌 일치율 하나다(`WATCH_METRICS`, `src/agents/features.py`, 하한 0.7 — 정책 v3에서 R101을 지운 뒤 분석가가 이 지표를 언급하지 않아 평가의 세무 불일치 탐지가 0%로 떨어졌다). 분석가는 watch 항목을 claim 하나에 인용해 참고 위험으로 적고 summary (2)에도 언급하되 정책 결과·권고는 바꾸지 않고 기준값 숫자는 쓰지 않는다. claim이 watch 지표를 인용하면 검토자 입력에도 `watch`가 붙는다. 콘솔 신호등의 watch 기준(`SIGNAL_RULES`)도 같은 상수를 쓴다.
- **가드레일:** 모델 입력은 가명처리하고 Code Interpreter 컨테이너는 실행마다 삭제한다. 부적격 또는 신청액이 정책 한도를 초과하면 `decline`만 가능하다. 그 밖의 정책 회부는 `refer`만 가능하다. 한도 이내(동액 포함) 적격이라도 근거가 부족하면 보류하고 확인된 반대 근거가 있으면 비권고할 수 있다. 저장된 v1의 조건과 금액은 재해석하거나 덮어쓰지 않는다.
- **실패 처리:** 모델 오류·근거 부족 등으로 검토를 통과하지 못하면 실행 기록만 남기고 `assessment`는 비워 둔다. 오케스트레이터는 기존 절차대로 한 번 더 시도하고, 여전히 의견이 없으면 `assessment_unavailable`과 함께 신청을 `failed`로 끝낸다.
- **모델 근거:** `eval/reports/job-eval-9imbutm/report.md` 3장(검토자 `gpt-5.4-mini` 고정) — 분석가 `gpt-5.4`는 1라운드 근거 통과 100.0%·검토 통과 87.5%·건당 $0.0631, `gpt-5.4-mini`는 62.5%·62.5%·$0.0199. 품질 차이 때문에 분석가는 `gpt-5.4`, 검토자는 `gpt-5.4-mini`로 두었다.
- **모델 변경(2026-11-06):** 분석가를 `gpt-6.1-sol`(GlobalStandard 150K TPM, 단가 입력 $2·출력 $10/1M 토큰으로 `gpt-5.4`의 $2.5·$15보다 낮음)로 바꿨다(`ANALYST_MODEL`). 검토자는 그대로 `gpt-5.4-mini`(2026-11-08 `gpt-5.6-luna`로 변경). 평가 Job의 full 구성(`EVAL_FULL_MODEL`)은 `gpt-5.6-sol`이다. 변경 뒤 라이브 점검은 [배포 기록](runbook/deploy-history.md)에 있다.
- **모델 재변경(2026-11-07):** `gpt-6.1-sol`은 게이트웨이 연결(Agent Service → APIM, chat completions)에서 함수 도구와 추론을 함께 쓰면 400("Function tools with reasoning_effort are not supported … in /v1/chat/completions")으로 거부한다. `reasoning.effort=none`은 이 모델이 지원하지 않는다(low·medium·high만). 그 사이 들어온 신청 2건은 평가 없이 심사역 대기로 넘어갔다. 분석가를 게이트웨이에서 함수 도구가 동작하는 `gpt-5.6-sol`(입력 $4·출력 $20/1M 토큰, 배포 150K TPM, 평가 Job과 공유)로 옮겼다. 이제 분석가와 평가 full 구성이 같은 모델이다. `gpt-6.1-sol` 배포(50K)는 보유만 한다.
- **재측정(2026-11-07, 게이트웨이 경로, 심사 8케이스):** `eval/reports/job-eval-ngrkucj/report.md` 3장 — `gpt-5.6-sol`은 1라운드 근거 통과 100%·최종 100%·검토 통과 100%·평균 1.12라운드·평가서 건당 $0.0783이다. 같은 경로의 `gpt-5.4`(`eval/reports/job-eval-aqisk13/report.md`)는 75.0%·100%·87.5%·2.00라운드·$0.0984였다. 정책 일치·세무 불일치 탐지는 둘 다 100%, 판정자 G/R/C는 3.88/4.75/4.62 대 3.75/5.00/4.62. 대신 호출 p95 지연이 11.7초로 길다(mini 4.6초). 단일 실행이라 LLM 변동 범위 안일 수 있다.

## 정산 원장 대사

- **목적:** 마켓플레이스 정산 원장(`pg.json`)과 계좌 입금을 대조해 매출 실재성, 채널 집중, 공제율 추이를 잰다(`src/agents/pg_ledger.py`, 2026-11-08). 지표는 결정적 코드만 만들고 에이전트는 인용만 한다.
- **대상 기간·매칭:** 기준일 직전 6개 완결 월의 정산 건이 대상이고, 기준일 직전 3영업일 안의 지급은 아직 입금 전일 수 있어 뺀다. 여기서 기준일은 신청 기준일과 '마지막 계좌 거래일 다음 영업일' 중 이른 날이다(`features.data_end`). 원천 데이터가 신청일보다 일찍 끝나도(오래 떠 있던 목 API) 입금 없는 날이 대상에 들어가지 않게 하려는 것이며, 최근 매출 추세(R106)도 같은 끝날로 잰다. 정산 1건은 같은 플랫폼·같은 실지급액(`net_krw`)이고 입금일이 지급일부터 3영업일 안인 가장 이른 미사용 계좌 입금과 짝이 된다(한 입금은 한 번만). 계좌 입금의 플랫폼은 입금처 키워드(네이버파이낸셜·쿠팡 주정산/최종액정산·11번가·토스페이먼츠)로 판별한다. 같은 플랫폼·같은 실지급액(`net_krw`)이고 입금일이 지급일부터 3영업일 안인 가장 이른 미사용 계좌 입금과 짝이 된다(한 입금은 한 번만). 계좌 입금의 플랫폼은 입금처 키워드(네이버파이낸셜·쿠팡 주정산/최종액정산·11번가·토스페이먼츠)로 판별한다.
- **지표:** `pg_reconciliation_ratio`(매칭된 실지급액 합 ÷ 대상 실지급액 합), `pg_top_platform_share`(총액 기준 최대 플랫폼 비중), `pg_hhi`, `pg_take_rate_trend_pp`(최근 3개월 평균 공제율 − 이전 3개월 평균, %p. 공제율은 수수료+광고비 상계 ÷ 총액). 광고비 상계는 실지급액에 이미 반영돼 있어 대사율에는 영향이 없고 공제율에만 나타난다. 매칭 못 한 정산 건은 미입금으로 세고 콘솔에 최근 10건만 올린다.
- **새 지표(2026-11-10):** `pg_offset_rate_trend_pp`(최근 3개월 평균 광고비 상계율 − 이전 3개월 평균, %p, `pg_take_rate_trend_pp`와 같은 `_trend_pp`로 소수 둘째 자리), `pg_cash_conversion_days`(플랫폼별 평균 정산 지연일을 플랫폼 총액 비중으로 가중평균, 소수 첫째 자리), `pg_advance_suspected_count`(선정산으로 추정되는 미입금 정산 건수)·`pg_advance_share`(그중 선지급 짝이 있는 미매칭 실지급액 ÷ 전체 미매칭 실지급액, 건수가 아니라 금액 비율이다, 소수 셋째 자리). 비율 지표는 모두 소수 셋째 자리로 반올림한다. 지연일은 `PLATFORM_LAG_DAYS`(달력일) — 스마트스토어 8일·쿠팡 32일·11번가 17일·자사몰 3일 — 이며 `mockapis/sellerdata/channels.py`의 정산 일정에서 유도한 평균값이다.
- **선정산 추정(2026-11-10):** 매칭되지 않은 정산 건을 정산일 오름차순으로 돌며, 플랫폼 정산 입금이 아니고 아직 매칭에 쓰이지 않은 계좌 입금 중 입금일이 `정산일 − 10일`(달력일, `ADVANCE_WINDOW_DAYS`)부터 정산일까지이고 금액이 실지급액(`net_krw`)의 75~95%(`ADVANCE_RATIO`) 범위인 가장 이른 것과 짝짓는다(한 입금은 한 번만 쓴다 — 같은 금액·같은 날의 미매칭 정산 두 건이 후보가 같아도 먼저 짝지어진 건만 쓴다). 짝이 지어지면 `unmatched[]` 항목의 `advance`에 입금일·금액·리드데이(정산일 − 입금일)가 실리고 콘솔은 "선지급 추정(N일 전, 정산액의 N%)"으로 원인을 보인다. 짝이 없으면 `advance`는 `None`이다.
- **자료 없음:** 정산 레코드가 없거나 PG 동의가 없으면 지표가 모두 `None`이고 결과는 `{"available": false}`다. 정책은 R107·R108을 적용하지 않고, 콘솔은 "정산 자료 없음"을 보인다(2026-11-08 이전 신청 문서도 같다). `pg.json`이 손상돼 읽지 못하면 같은 처리에 `pg_ledger_unavailable` 플래그를 남기고 심사는 계속한다.
- **에이전트 인용 규칙:** `get_metrics`의 지표 맵에 `M:pg_reconciliation_ratio`·`M:pg_top_platform_share`·`M:pg_hhi`·`M:pg_take_rate_trend_pp`·`M:pg_offset_rate_trend_pp`·`M:pg_cash_conversion_days`·`M:pg_advance_suspected_count`·`M:pg_advance_share`·`M:pg_unmatched_count`가 들어간다(값이 `None`이면 넣지 않음). `get_settlement_summary`는 인자 없이 기간·건수·지표 ID·플랫폼 비중·월별 공제율·미입금 건수만 돌려주고(집계만, 미입금 개별 금액과 선정산 짝의 날짜·금액은 없음) 자료가 없으면 `{"available": false}`다. 지시문은 정산 자료가 있으면 이 도구로 확인하고 `M:pg_*` ID와 값만 인용하게 하며, 정책 사유 R107·R108이 있으면 `P:R107:threshold`·`P:R108:threshold`를 목표로 한 반사실을 허용한다. 숫자 대조(`check_grounding`)는 기존 방식 그대로다.
- **선지급 입금 분류:** 계좌에 들어온 "선정산" 이체(`TRANSFER_IN`)는 매출이 아니라 대출금(`LOAN_PROCEEDS`)으로 분류한다(`src/agents/classify.py`). 정산 양도 셀러의 선지급이 매출로 잡히지 않게 하려는 규칙이다. 양도 시나리오(`seller-mugshop-21`)는 정책에서 R108이 사유로 붙고, 선지급이 대출금으로 빠지면서 DSCR도 낮아져 R002·R106이 함께 걸린다([ADR-003](adr/ADR-003-deterministic-policy.md#2026-11-08-개정-정책-v3)).
- **평가:** 심사 suite의 `TAX_EVIDENCE`는 `M:tax_bank_match_ratio` 하나로 줄었고(R101 삭제), 정산 양도(ASSIGNED) 케이스에서 주장이 `M:pg_reconciliation_ratio` 또는 `P:R108`을 인용하는 비율을 `settlement_gap_detection_rate`로 재서 리포트 표의 "정산 차이 탐지" 열에 싣는다(`src/evals/suites/underwriting.py`). 같은 케이스에서 확인 사항이 `SETTLEMENT_CHECK_EVIDENCE`(`M:pg_advance_share`·`M:pg_reconciliation_ratio`·`P:R108` 세 가지만, 접두 일치)를 인용하는 비율을 `settlement_check_rate`로 재고, 내지 않은 케이스는 `settlement_check_missed` 실패로 남긴다. 이 지표들의 실측은 아직 없다.
- **콘솔:** 상세 화면 현금흐름 탭의 "정산 대사" 카드가 정산금 계좌 입금 비율·최대 채널 비중·현금화 일수·공제율 변화·선정산 추정 건수 KPI, 플랫폼 비중 막대, 월별 공제율(수수료·광고비 상계 분리) 막대, 미입금 정산 목록(원인 열에 선지급 추정 여부)을 보인다(`settlement_card`, `src/console/dashboard.py`). 옛 문서는 현금화 일수·선정산 추정 두 KPI만 "—"다(둘 다 2026-11-10에 새로 생긴 키라서 값이 없다 — 정산금 계좌 입금·최대 채널 비중·공제율 변화는 2026-11-08부터 있던 지표라 옛 문서에도 값이 있다). 공제율 막대는 단색이다. 세금·계좌 일치율은 정책 규칙이 아니라 참고(watch) 지표로만 표시한다. 현행 의견의 검토된 다음 확인 사항과 인용 근거는 `에이전트 의견` 탭에 표시한다. 정산 자료가 없어도 이 탭에서 확인할 수 있다.

## 마켓 셀러 지표

- **목적:** 계좌 현금흐름에 없는 마켓 셀러 운영 데이터(입점 기간·평점·리뷰·반품·취소·판매정지·정산 예정금)를 결정적 코드가 지표로 만들고(`src/agents/marketplace.py`의 `store_summary`·`features_of`, 2026-11-17, 정책 v4), 에이전트는 근거 ID로 인용만 한다. 판정·대체 여부는 정책 엔진이 정한다([ADR-003](adr/ADR-003-deterministic-policy.md#2026-11-17-개정-정책-v4)).
- **동의 범위·원천:** 새 동의 범위 `ConsentScope.MARKETPLACE`(`marketplace`, 고지 라벨 "마켓 판매 성과")와 Mock API `GET /marketplace/store`(`MarketplaceStore`). 데이터 수집 에이전트의 `SOURCES`에 `marketplace`가 들어가며(필수 원천 아님) 원천 Blob은 동의 ID별 컨테이너의 `marketplace.json`이라 동의 삭제 흐름에 포함된다. 동의가 없거나 자료가 없으면 지표가 모두 `None`이고 정책은 셀러 규칙을 적용하지 않는다(불이익 없음). `marketplace.json`이 손상되면 같은 처리에 `marketplace_unavailable` 플래그를 남기고 심사는 계속한다.
- **지표 7개(`mkt_*`, `CashFlowFeatures`의 선택 필드, 기본 `None`):**

  | 지표 | 정의 |
  |---|---|
  | `mkt_tenure_months` | 가장 이른 입점일부터 기준일까지 개월 수(내림, 입점일이 기준일보다 늦으면 0) |
  | `mkt_rating` | 플랫폼 평점의 리뷰 수 가중 평균, 소수 둘째 자리. 리뷰 수 합이 0이면 단순 평균으로 대신한다 |
  | `mkt_review_count` | 플랫폼 리뷰 수 합 |
  | `mkt_return_rate_90d` | 최근 90일 반품액 합 ÷ 총매출 합(총매출 0이면 `None`) |
  | `mkt_cancel_rate_90d` | 최근 90일 취소액 합 ÷ 총매출 합 |
  | `mkt_days_since_suspension` | 가장 최근 판매정지부터 기준일까지 일수(정지가 없으면 `None`) |
  | `mkt_pending_settlement_krw` | Σ(정산 예정금 − 선정산 양도분), 0 미만이면 0 |

- **근거 ID:** `get_metrics`의 지표 맵에 `M:mkt_tenure_months`·`M:mkt_rating`·`M:mkt_review_count`·`M:mkt_return_rate_90d`·`M:mkt_cancel_rate_90d`·`M:mkt_days_since_suspension`·`M:mkt_pending_settlement_krw`가 들어간다(값이 `None`이면 넣지 않음). 정책 사유가 있으면 `P:R003`·`P:R109`·`P:R110`·`P:S001`의 `observed`·`threshold`(예: `P:S001:observed`)가 근거가 된다. `P:S002`는 사유 코드를 남기지 않는 `outcome: pass` 규칙이라 근거 ID가 생기지 않는다. 숫자 대조(`check_grounding`)는 기존 방식 그대로다.
- **지시문:** 분석가는 셀러 지표를 `M:mkt_*` ID와 값만 인용하고, 정책 사유에 S001이 있으면 확인 사항 하나 이상에 `P:S001` 근거를 단다(검증 실패로 처리하지 않는 지시문 요구). 리뷰 원문이나 판매정지 사유 원문은 쓰지 않는다(자료에 없다). 승인·거절을 지시하지 않는 규칙과 "정책이 ineligible이면 decline/refer만" 규칙은 그대로다. 대체 규칙(S001)으로 정책이 refer를 내면 그 결과를 따른다.
- **확인 사항 근거 확장:** 심사역 확인 사항의 유효 근거 접두사(`OFFICER_CHECK_PREFIXES`, `src/agents/underwriting.py`)가 `M:pg_`·`P:R107:`·`P:R108:`에 더해 `M:mkt_`·`P:R003:`·`P:R109:`·`P:R110:`·`P:S001:`·`P:S002:`로 넓어졌다. 정산 자료가 없어도 마켓 자료가 있으면 확인 사항을 쓸 수 있다. 콘솔 상세의 `마켓 셀러` 탭과 정책 판정 탭이 같은 지표와 "현금흐름만 볼 때 → 셀러 데이터 반영" 비교를 보여 준다.
- **평가:** 심사 suite에 판매정지 케이스 `ev-uhealsusp`(정상 계절형 셀러에 기준일 40일 전 판매정지, `StoreOverride(suspended_days_ago=40)`)를 더했고, 이력 부족(THIN_FILE)과 이 케이스의 행에 `seller_evidence_cited`(분석가 초안의 주장이 `M:mkt_`·`P:S001`·`P:R003`·`P:R109`·`P:R110`을 하나라도 인용했는지)를 싣는다(`SELLER_EVIDENCE`, `src/evals/suites/underwriting.py`). 컨텍스트는 운영과 같이 `compute_underwriting(..., marketplace=...)`로 만든다. 이 지표의 모델 실측은 아직 없다.

## 조기경보

- **목적:** 통계 탐지기 트리거가 있을 때 의미 기반으로 스트레스를 조사하고 경보 여부·심각도·권고를 정한다(L4, 사후). 경보는 "이유 + 확인할 조치"로 쓴다(2026-11-11): `summary`는 위험이 무엇이고 왜인지, `check_action`은 심사역·고객이 확인할 조치 한 문장(200자 이내, 숫자는 `summary`와 같이 근거 값과 맞아야 함)이다. 폴백 경보도 원인 → 첫 트리거 → 기본 순으로 고른 고정 문장(`FALLBACK_TEXT`, 숫자 없음)으로 두 칸을 채운다.
- **신호별 읽는 법(지시문 표, `src/agents/early_warning.py`):**

  | 신호 | 이유 | 확인할 조치 |
  |---|---|---|
  | 판매대금 입금 감소 | 상환재원이 줄 수 있다 | 최근 주문·정산 추이 |
  | 반품·환불 증가 | 예상 정산액이 줄 수 있다 | 반품·환불 사유와 정산 차감 |
  | 광고비·매입비 증가 | 매출이 유지돼도 가용현금이 줄 수 있다 | 광고비·매입비 지출 계획 |
  | 예상 현금잔액 < 예정 상환액 | 다음 상환일에 현금 부족이 예상된다 | 상환일 전 입금 예정과 다른 계좌의 가용자금 |

- **트리거:** `inflow_drop`·`new_debt`·`buffer_drop`에 더해, 결정적 4주 잔고 전망(`src/agents/forecast.py`의 `project`)이 0원 아래로 내려가면 `projected_shortfall`. 전망은 심사 시점 정산 프로필(채널별 정산 지연·수수료)과 최근 3개월 고정 지출 일정으로 만들며 LLM을 쓰지 않는다. 근거 지표는 `W:<주차>:<지표>`(주간 실측)와 `F:<주차>:projected_min_balance`(전망)를 인용한다. 상환 원장이 있으면 오케스트레이터가 다음 상환일(`F:<주차>:next_due_on`)과 그날 출금 직전 예상 잔고(`F:<주차>:due_balance`)를 주간 행에 넣고, 그 잔고가 그날 예정 회차 금액 합보다 적으면 `due_shortfall`(신호 종류 `projected_shortfall`, 콘솔 "상환일 잔고 부족")이 걸린다(2026-11-11, `src/agents/monitoring.py`).
- **원인 후보(`src/agents/ledger_monitor.py`, 2026-11-11):** PG 정산 원장이 있으면 대출 전 8주 원장 기준선(`LedgerBaseline`, 주문·반품률·공제율·플랫폼별 주간 지급) 대비 새 트리거 4개를 본다 — `sales_slump`(3주 주문액이 기준선의 0.75 미만. 기대 주문이 0이면 비교하지 않는다 — `orders_vs_expected`가 `null`이고 트리거도 근거 ID도 없다), `settlement_hold`(주간 보류액이 기대 주간 정산의 30% 이상, 또는 비중 10% 이상 플랫폼 지급이 2주 연속 0이면서 그 플랫폼 주문은 기준의 0.75 이상), `return_spike`(2주 연속 반품률이 기준선의 2배 이고 +5%p 이상), `cost_surge`(2주 연속 공제율이 기준선 +4%p 이상, 광고·물류 출금 배율은 보지 않는다 — 월 1회 출금이라 주 단위 비교가 안 되고 분류(`Category`)에 광고·택배 범주가 없어서다). 네 트리거는 발화한 주차·근거 ID(`W:<주차>:<지표>`)와 함께 `AlertCause` 후보로 바뀌고, `new_debt`(`high_cost_borrowing`)는 다른 후보보다 늦거나 같은 주에 났으면 "결과"(`consequence=true`)로 표시된다. 원장이나 기준선이 없으면(옛 재생, PG 동의·수집 실패) 원장 트리거 없이 계좌 지표만 쓴다.
- **USD 승인 근거와 설명 처리(`src/agents/usd_monitoring.py`의 `observe_usd`, 2026-11-25):** 같은 주차·거래로 은행 계좌 신호(`bank_only`: `inflow_drop`·`projected_shortfall`·`due_shortfall`)를 먼저 계산하고, 원장이 있으면 원장 트리거·승인 근거·지급 예정을 반영한 전망을 더해 `triggers`(경보·조사 입력)를 만든다. `bank_only`는 설명 처리의 입력으로 주간 행에 `bank_only_triggers`로 남을 뿐 콘솔이 둘을 비교해 보이지는 않는다(비교 화면은 2026-11-26에 지웠다). 둘 다 결정적이며 LLM을 부르지 않는다. 원장이 없으면 `triggers` = `bank_only`다.

  | 승인 대체(substitution) | 주간 지표 | 훼손 하한 |
  |---|---|---|
  | S003 빠르고 대사가 맞는 정산(얇은 현금 버퍼 대체) | `recon_ratio`(최근 21일 지급 정산 대사율) | 0.95 |
  | S005 원장이 확인한 매출(세무 신고 과소 대체) | `recon_ratio` | 0.90 |
  | S004 긴 마켓 실적(계절 변동 대체) | `orders_vs_prior_year`(최근 3주 주문 ÷ 작년 같은 주) | 0.75 |

  지표가 하한 아래면 `basis_breach`가 걸리고 원인 후보는 S003·S005면 같은 주 `settlement_hold`가 있을 때 정산 보류, 없으면 정산 양도(`settlement_assigned`), S004면 매출 부진이다. 지표가 없으면(작년 주문 없음 등) 상태는 `unknown`이고 트리거는 없다. 설명 처리는 두 가지다 — `bank_only`의 `inflow_drop`은 3주 정산 대비 입금(`settle_coverage`) ≥ 0.95이고 영업일당 주문(`orders_per_day_vs_expected`, 작년 계절성이 있으면 작년 주문으로 보정한 기대 대비) ≥ 0.75이면 `triggers`에서 빼고 `explained`에 근거 ID와 함께 남긴다(같은 주 `sales_slump`나 `settlement_hold`가 있으면 빼지 않는다). `bank_only`의 `projected_shortfall`은 지급 예정(대사율로 깎고 보류 해제분 제외)을 넣은 전망 최저 잔고가 0 이상이면 설명된다. `due_shortfall`은 설명하지 않고 은행 전망으로 판단해 `bank_only`와 `triggers`에 같게 넣는다.
- **USD 단계형 알람(`src/agents/incident.py`, `src/agents/usd_early_warning.py`, `src/orchestrator/journey/monitoring.py`의 `_watch_usd_week`, 2026-11-26):** USD 대출은 주마다 경보를 올리는 대신 신호를 사건으로 묶어 단계(관찰 `watch` → 중간 `medium` → 높음 `high`, 해소 `resolved`)로 다룬다. 신호만으로 정한 기본 단계를 조사 에이전트가 ±1 단계 안에서 조정한다.
  - **세 데이터원 신호(`SIGNALS`):** 은행(`bank`) — `inflow_drop`(약), `projected_shortfall`·`due_shortfall`(강). 정산(`settlement`) — `orders_dip`·`returns_rising`·`deductions_rising`·`sales_slump`·`return_spike`·`cost_surge`(약), `settlement_hold`·`basis_breach`(강). 마켓(`market`) — `market_score_drop`(약), `market_score_crash`·`account_warning`(강). 마켓 점수(`src/agents/usd_market_score.py`)는 마켓마다 대출 전 주를 100으로 두고 취소율·반품률·평점·등급·판매 경고 악화를 감점한 값을 대출 전 매출 비중으로 가중한 것이며, 가중 점수 또는 비중 20% 이상 마켓 하나의 점수가 85 이하면 drop, 60 이하면 crash다(crash가 drop을 대신한다). 2026-11-27 전 재생 기록의 `cancel_rise`·`cancel_spike`·`rating_drop`·`grade_down`은 탐지에서 빠졌지만 콘솔에서 그대로 읽힌다. 신호마다 근거 ID(`W:<주차>:<지표>`, `F:<주차>:<지표>`)를 단다. 마켓 지표는 `W:<주차>:mkt_*`이고 점수 신호는 `W:<주차>:mkt_score`·`W:<주차>:mkt_worst_score`를 인용한다.
  - **기본 단계(`base_stage`):**

    | 조건 | 기본 단계 |
    |---|---|
    | 강한 은행 신호가 하나라도 있음 | `high` |
    | 약한 신호가 두 데이터원 이상이거나, 은행이 아닌 강한 신호가 있음 | `medium` |
    | 그 밖의 신호 | `watch` |
    | 신호 없음 | 정상(조사 없음) |

  - **조사 호출(`plan_week`):** 열린 사건이 없을 때 신호가 처음 생기면, 열린 사건의 신호 집합이 바뀌면 조사 에이전트를 부른다. 신호 집합이 같으면 부르지 않고 `hold`로 단계를 유지한다(단, 높음 사건은 2단계 안내를 보내기 전까지 매주 다시 부른다). 신호가 없는 주가 연속 2주(`QUIET_TO_RESOLVE`)면 사건을 해소한다. 에이전트가 `resolve`를 고르면 그 사건을 닫고 같은 신호 코드는 신호가 2주 사라질 때까지 다시 사건으로 열지 않는다(`suppressed`).
  - **±1 조정과 결정적 검증(`check_stage`):** 에이전트의 단계(`resolve`<`watch`<`medium`<`high`)는 기본 단계에서 한 단계 넘게 벗어날 수 없다. `high`는 강한 은행 신호가 있어야 하고, 강한 신호가 있으면 `watch`·`resolve`를 고를 수 없으며, 약한 신호가 한 데이터원뿐이고 강한 신호가 없으면 `medium`까지다. 기본 단계와 다르면 `adjust_reason`이 가장 강한 신호의 데이터원과 다른 데이터원의 근거 ID를 하나 이상 인용해야 한다. `why_now`·`outlook`·`adjust_reason`은 200자 이내 영어이고 숫자는 인용한 근거 값(또는 주차)과 맞아야 한다(`usd_language.issues`). `medium`·`high`는 같은 단계의 `raise_alert`가 먼저 받아들여져야 하고 `watch`·`resolve`는 경보를 올리지 않는다. 지시문은 확인만 권고하고 회수·한도·금리·조건 변경은 권하지 않게 한다.
  - **폴백(`fallback_stage`):** 에이전트를 못 쓰거나 출력이 검증을 통과하지 못하면 기본 단계를 그대로 쓰고, 숫자 없는 고정 영어 문장(`WHY_NOW_FALLBACK`·`OUTLOOK_FALLBACK`)을 `why_now`·`outlook`에 넣는다. `medium`·`high`면 탐지기 신호만으로 경보를 만든다(`fallback_alert`). `why_now`가 "먼저 움직인" 데이터원으로 꼽는 것은 열린 사건이 시작된 주의 신호 데이터원이다(신호 목록 순서가 아니다, `first_moved_source`). 그 주에 여러 데이터원이 함께 시작했거나 일지에 그 주가 없으면 "먼저"를 쓰지 않는 중립 문장을 쓴다. 일지와 경보에는 `agent`(`agent`/`fallback`)가 남는다.
  - **단계가 오를 때만 경보:** 사건에서 처음 `medium`·`high`가 되거나 직전 단계보다 오르는 주(`stage_up`)에만 경보 문서를 새로 만든다(`alert_action=created`). `watch`·`resolved`·단계가 같거나 내려간 주는 경보·연락을 만들지 않는다. 같은 사건이 이어지는 동안 다시 조사해 원인 후보가 바뀌면 새 경보 대신 열린 경보의 원인을 갱신한다(`alert_action=updated`).
  - **1·2단계 자동 대응:** 높음 주마다 `escalation.next_stage`를 적용한다. 이 재생에서 처음 `high`가 되는 주 1단계(잔고 부족 알림), 이미 1단계를 쓴 뒤 바로 직전 주도 `high`였던 주 2단계(상환일 이동·분할 안내와 셀프 플랜 링크)다. 안내 내용·발송 규칙은 아래 단계별 자동 대응과 같다. 사건이 `high`인 동안에는 신호가 없는 조용한 주도 높음 주로 세어(`watch_week`의 주별 단계 이력) 2단계 트리거에 들어간다(의도한 동작이다).
  - **평가:** `early_warning_usd` suite가 고정한 여섯 장면에서 에이전트 단계를 잰다(아래 평가).
- **도구 allowlist(`src/agents/early_warning.py`):** `get_weekly_series`, `get_baseline`, `get_peer_cohort`, `get_settlement_ledger`, `query_transactions`, `raise_alert`. `get_settlement_ledger(from_week, to_week)`는 플랫폼별·주별 원장 집계(주문액·반품·보류·공제·지급)를 주고, 원장이 없으면 `available=false`다. `get_baseline`이 원인 후보 목록(`cause_candidates`: 원인·첫 발화 주차·근거 ID)을 함께 준다. `raise_alert`는 결정적 grounding 검사를 통과해야 `accepted=true`이고, 첫 성공만 유효하다.
- **원인 근거 규칙(`check_alert`):** `raise_alert`의 `primary_cause`는 후보에 없는 원인을 고를 수 없고, 후보가 있는데 비울 수도 없다. 주원인의 신호 중 하나는 그 원인의 지표 군(매출 부진: `W:n:order_gross`·`orders_vs_expected`, 정산 보류: `W:n:held`·`advance_in`, 반품 급증: `W:n:returns`·`return_rate`, 비용 증가: `W:n:deductions`·`deduction_rate`, 고금리 차입: `W:n:high_cost_lender`·`high_cost_count`·`loan_proceeds`)를 인용해야 한다. `secondary_causes`는 최대 2개, 주원인과 겹칠 수 없고 후보 밖에서 고를 수 없다. 지시문은 "차입은 대개 결과다. 차입보다 앞선 주문·정산·반품·비용 변화가 있으면 그것을 주원인으로 고른다"고 명시한다. 위반하면 `issues`로 거부되고 같은 실행에서 재시도한다. 경보가 여러 번 나면 매번 그때까지의 전체 데이터로 원인을 다시 고른다.
- **가드레일:** 모델 입력 가명처리. 근거 검사 실패 시 `issues`를 돌려줘 같은 실행에서 고치게 한다.
- **실패 시 동작:** 에이전트를 쓸 수 없으면 탐지기 결과만으로 medium 경보를 올린다(fail-safe: 사람이 본다). `fallback_alert`의 `primary_cause`는 후보 중 가장 먼저 발화한 원인, `secondary_causes`는 그다음 서로 다른 후보 최대 2개다(후보가 없으면 둘 다 비움).
- **콘솔:** 경보 카드 머리에 "주원인: <라벨>"·"부원인: <라벨>, ..." 칩이 보이고(`CAUSE_LABELS`, `src/console/monitoring.py`), 모니터링 탭에 "주간 정산(PG 원장)" 표(주차·주문액·반품·보류·공제·지급)가 함께 있다. 옛 경보 문서(`primary_cause` 없음)는 주원인 칩이 "—"(`NO_CAUSE_LABEL`), 옛 재생 문서(원장·`ledger_baseline` 없음)는 표 대신 "정산 원장 없음"(`NO_LEDGER_LABEL`)이 보인다. 값은 오케스트레이터가 저장한 경보·주간 행을 그대로 그리며 콘솔이 다시 계산하지 않는다.
- **단계별 자동 대응(2026-11-11, `src/agents/escalation.py`, `src/orchestrator/journey/monitoring.py`의 `_escalate`):** 높음 경보는 심사역 검토와 별개로 LLM 없이 사전 승인 템플릿 안내를 차주에게 보낸다(L4 결정적 정책). 각 단계는 재생마다 한 번만 쓰고(발송이 보류(`deferred`)돼도 쓴 것으로 보고 다시 보내지 않는다), 다음 예정 회차가 없으면 보내지 않고 단계도 쓰지 않는다. 안내는 경보 주차 마지막 날 날짜로 연락 기록(`contacts-<재생>`, `by="early_warning"`, ref `<재생>-s<단계>`)에 남고, 콘솔 경보 카드의 "이 경보로 자동 발송된 안내"와 고객 콘솔 `받은 안내` 메뉴(`/borrower/<신청인>/notices`)에 보인다.

  | 단계 | 조건 | 보내는 것 | 그 뒤 |
  |---|---|---|---|
  | 1 | 이 재생에서 처음 난 높음 경보 | 잔고 부족 예상 알림(`balance_warning`: 다음 회차 상환일·금액, `due_balance`가 0원 이상이고 상환액보다 적을 때만 예상 잔고 금액을 넣고 아니면 금액 없는 문구) | 이후 상환일이 1단계 날짜 이후인 회차는 3일 전 기본 안내에 더해 5일 전 추가 안내(`early_reminder`, `EARLY_REMINDER_DAYS=5`)를 받는다 |
  | 2 | 직전 경보와 이번 경보가 모두 높음(1단계를 이미 씀) | 상환일 이동·분할납부 안내와 셀프 상환 플랜 링크(`/borrower/<신청인>/plans`, 고객 콘솔 `상환 플랜` 메뉴)를 한 메시지로(`self_plan_offer`) | 발송되면 셀프 플랜 제안(`of-<재생>`)이 열리고 재생이 그 주에서 `awaiting_customer`(고객 응답 대기)로 멈춘다. 유지 외에 고를 옵션이 없으면 보내지 않고 `skipped`(`no_options`)로 남기며 다시 시도하지 않는다. 보류되면 제안을 열지 않는다 |

  셀프 플랜 옵션(`src/agents/repayment_plan.py`)은 이동(남은 예정 회차마다 상환일 뒤 주 정산 플랫폼 — 원장 기준선의 주간 지급이 가장 큰 플랫폼 — 의 다음 지급일 다음 영업일로, 원래 상환일 + 10일 상한), 분할(다음 회차 하나를 원래 상환일 50% + 15일 뒤 다음 영업일 50%로, 이미 나뉜 회차는 다시 나누지 않음), 유지(늘 있음)다. 지급일은 이번 주까지 공개된 지급 이력에서 찾고 없으면 최근 지급 간격 중앙값으로 늘려 잡으며, 이력이 2건 미만이면 이동 옵션이 없다. 고객이 유지를 고르면 승인 없이 제안만 닫히고, 이동·분할을 고르면 심사역 승인을 기다리는 플랜 변경 요청이 생긴다. 승인하면 아직 공개되지 않은 주차의 원래 출금을 빼고 새 회차 출금을 넣어(잔고가 모자라면 미납) 이후 재생 흐름·전망·납부 판정이 바뀐 일정으로 돈다(`apply_plan`). 심사역이 "고객 응답 없이 계속"을 누르면 제안이 만료되고, 그 뒤 들어온 고객 선택은 버린다.
- **L4 범위:** 자동 대응은 안내(알림·추가 안내·플랜 제안)까지다. 대출 회수·한도 축소·부도 확정·금리 변경은 하지 않고 권하지도 않는다(지시문이 경보 문장에서도 금지). 일정 변경은 고객 선택 + 심사역 승인이 있어야 반영된다.
- **자동 발송 시각:** 1·2단계 안내와 3일 전·5일 전 안내는 실제 시각이 아니라 시뮬레이션 날짜 10:00 KST(`sim_now`)에 보낸 것으로 보고 공정 연락 규칙(연락 시간·7일 7회)을 검사한다. 10시는 연락 시간 안이라 실제로는 7일 7회 한도에서만 보류된다.
- **평가:** 조기경보 suite가 경보 주차 심각도에 운영과 같은 `next_stage`를 적용해 경로별 1·2단계 첫 도달 주차(`stage1_paths`·`stage2_paths`)를 잰다(`src/evals/suites/early_warning.py`). 실측은 아직 없다.
- **USD 단계 평가(`early_warning_usd` suite, `src/evals/suites/early_warning_usd.py`):** 오프라인 장면 재생(`evals.usd_scenes.scene_context`)이 만든 (셀러, 주차) 문맥 여섯 곳에서 `investigate_usd`가 고른 단계가 허용 단계 안인지 잰다 — tint-17 w6 `{watch}`, snackbox-22 w3 `{medium}`(high를 고르면 검증이 거절해 폴백 medium), linen-10 w3 `{medium}`(정산 반품률 상승과 마켓 점수 하락, 두 출처의 약신호. 강한 은행 신호가 없어 high는 거절), gearstore-06 w3 `{medium}`(강한 비은행 신호(계정 경고·마켓 점수 급락·정산 보류)만 있고 강한 은행 신호가 없어 high는 거절), tangerine-15 w3 `{watch}`(비용 신호뿐), dailylook-01 w7 `{medium, high}`(기본 high의 첫 주, 한 단계 낮춤 허용). 모델별 `valid_rate`(폴백 없이 에이전트 출력이 통과한 비율)·`stage_match`(허용 단계 안 비율)·`forbidden_rate`(회수·한도 축소·기한이익 상실 선언·금리 변경을 권하는 문장이 있는 비율, 검증기가 거절한 출력 원문도 본다)와 케이스별 `{seller, week, base, stage, agent, forbidden}`을 낸다. 실패 유형은 `ew_stage_mismatch`·`ew_forbidden_text`·`agent_error`(폴백)다. 가짜 백엔드(`--fake`)에는 이 에이전트 흉내가 없어 모든 장면이 폴백이므로 배선 확인용이다. 라이브 실측은 아직 없다.
- **모델 근거:** `eval/reports/job-eval-9imbutm/report.md` 4장 — `gpt-5.4`/`gpt-5.4-mini` 모두 양성 경로 precision·recall 1.000, 탐지기 단독은 precision 0.500(FP 3). 리드 타임은 합성 경로에서 평균 약 70일(양성 2~3건)로 원문의 60~90일 범위 안이었다. 연체가 나도록 설계한 합성 시나리오라 실제 부도 이력으로 검증한 것은 아니다([비즈니스 케이스](business-case.md)). 기본 `gpt-5.4`(`EW_MODEL`). 원인 분류(2026-11-11)의 정확도 실측은 아직 없다.

## 추심

- **목적:** 상환 원장에서 미납이 확정된 회차(추심 건 `rc-<재생>-i<회차>`)를 받아 승인된 스크립트로 어떻게 연락할지, 상환 곤란 상담(재조정 요청)으로 넘길지 **제안만** 한다(2026-11-11). 에이전트는 아무것도 보내지 않는다. 발송과 재조정 요청은 심사역이 제안을 승인·수정한 뒤에만 생기고, 재조정 결정도 사람만 한다. 원문 설계의 L3(에이전트 직접 발송)보다 보수적이다(이유는 `docs/limitations.md`).
- **언제 도는가(2026-11-20):** 같은 대출의 확인된 출금을 출금일 현재 도래한 회차에 오래된 순서로 충당하고, 미납·부분납 회차마다 잔액 기준으로 제안한다. 지연 납부로 잔액이 달라지면 기존 미발송 제안은 종료하고, 잔액이 남으면 그 금액으로 새 제안을 검토한다(`repayment.updated`, `src/agents/repayment.py`). 상환 안내(`payment_reminder`)는 상환일 3일 전에 LLM 없이 보낸다(`remind`, 공정 추심 규칙에 걸리면 `deferred`). 높음 경보의 단계별 자동 안내(잔고 부족 예상 알림·5일 전 추가 안내·셀프 플랜 제안)는 조기경보 정책이 보내며(위 [조기경보](#조기경보)) 추심 에이전트와 무관하다. 추심 에이전트는 `get_case`로 최근 경보 요약과 신호 종류(`signals`, 예: `loan_stacking`·`inflow_drop`·`settlement_hold`·`return_spike`), 그리고 경보가 고른 원인(`primary_cause`·`secondary_causes`)을 참고만 한다. 조사 폴백 경보는 요약에 근거가 없어 신호 종류로 판단한다(2026-11-08). 지시문의 상환 유예(`hardship_offer`) 대상 신호에 `settlement_hold`·`return_spike`가 2026-11-11에 추가됐다(원인 값은 경보 문서에서 추심 입력 `recent_alert`로 그대로 실려 오며 — 스펙 §7.4 — 참고 정보일 뿐 추심 판단 로직을 바꾸지 않는다. 옛 경보 문서는 원인이 없어 `null`·`[]`로 온다).
- **도구 allowlist(`src/agents/collections.py`):** `get_case`(회차·상환일·금액·누적 미납·최근 경보 요약·신호 종류·원인), `get_contact_history`, `list_scripts`, `propose`(승인 스크립트 enum의 `script_id`, 재조정 상담 `hardship_category`(`revenue_shock`/`illness_or_disaster`/`debt_overload`, 없으면 빈 문자열)·`hardship_note`, `reason`). `propose`는 한 번만 받는다(두 번째는 `already_proposed`). 옛 `send_script`·`route_hardship`는 없앴다. 재조정 상담 근거는 누적 미납 2회 이상 또는 최근 경보 주원인이 매출 부진·정산 보류·반품 급증·고금리 차입인 경우다.
- **제안서(`build_proposal`, LLM 없이 조립):** 심사역 승인 화면(대출 화면 추심 건 카드의 "추심 제안")에 네 가지를 보인다. ① 제안 조치와 이유(스크립트, 재조정 상담 포함 여부·분류·메모, 에이전트 `reason`) ② 판단 근거(회차·상환일·금액·누적 미납, 최근 경보 심각도·요약·확인할 조치, 최근 7일 연락 수, 조기경보 단계 자동 안내 이력) ③ 정책 확인 결과(`approved_script`·`contact_hours`·`weekly_limit`·`banned_words`·`pii_tokens`, 재조정 상담을 제안했으면 `hardship_grounds`) ④ 고객에게 보낼 정확한 문구(승인 스크립트를 회차 값으로 채운 것). 추심 건은 `proposal_pending`이 되고, 재생 중이면 그 주에서 멈춘다(대기 항목 `collections_proposal`).
- **심사역 결정(`settle_proposal`):** 승인은 제안 문구 그대로 보낸다. 수정(콘솔 "수정 후 승인")은 고친 문구로 보내며, 재조정 상담은 에이전트 권고일 뿐이라 승인·수정 모두 기본은 문구만 보내고, 심사역이 `재조정 상담 요청도 만들기`(`include_hardship=True`)를 체크할 때만 요청을 만든다(제안에 없던 상담은 더할 수 없음, 오류 "제안에 재조정 상담이 없어 포함할 수 없습니다"). 반려는 사유가 필수이고 아무것도 보내지 않는다. 승인·수정 문구는 보내기 전에 콘솔과 오케스트레이터가 같은 규칙(`message_issues`: 빈 문구, 300자(`MESSAGE_MAX`) 초과, 금지 표현 `BANNED_PHRASES`(법적 조치·압류·소송·회수·한도 축소·부도·금리 인상 등), 가명 토큰 `[[PERSON_`)으로 다시 검사해 걸리면 결정을 반영하지 않는다. 발송 기록은 `by="officer"`·`officer_id`와 원래 `script_id`로 연락 기록에 남고, 심사역이 재조정 상담을 포함했을 때만 그때 재조정 요청(`rs-<추심 건>`, `pending_officer`)이 생긴다. 결정은 `approved`/`modified`/`rejected`로, 수정이면 보낸 문구(`sent_text`)도 남는다.
- **가드레일:** 공정 추심 규칙을 결정적으로 강제 — 7일 `MAX_CONTACTS_PER_7_DAYS=7`회, `CONTACT_HOURS` 08:00~21:00(KST) 밖이면 발송 보류(`deferred`). 심사역 발송은 시뮬레이션 날짜가 아니라 **실제 시각**(결정 처리 시각)으로 검사한다(자동 안내는 시뮬레이션 10시). 보류돼도 결정은 끝난 것으로 보고 다시 보내지 않는다(추심 건 `deferred`). 자유 문장은 심사역 수정 문구만 허용된다.
- **결정 큐(`restructure-decisions`):** 메시지는 `kind`로 구분한다(`src/orchestrator/journey/models.py`) — `restructure`(재조정 승인·거절, `kind`가 없는 옛 메시지도 이것), `collections`(추심 제안 `approve`/`modify`/`reject`), `plan_choice`(고객 콘솔 `상환 플랜` 메뉴의 플랜 선택 `move`/`split`/`keep`), `plan_change`(플랜 변경 `approve`/`decline`), `offer_expire`(심사역의 "고객 응답 없이 계속"). 이미 결정된 건에 다시 온 메시지(재전달)는 다시 결정하지 않는다.
- **재생 정지·재개(P9, 2026-11-11 갱신):** 재생은 대기 항목이 끝날 때까지 그 주에서 멈춘다. 대기 항목은 추심 승인 대기 → 고객 응답 대기 → 플랜 변경 승인 대기 → 재조정 결정 대기 순으로 보인다(`HOLD_ORDER`, `src/orchestrator/journey/holds.py`). 심사역 항목이 하나라도 있으면 `paused`(콘솔 "심사역 결정 대기"), 고객 응답만 남으면 `awaiting_customer`(콘솔 "고객 응답 대기"), 모두 끝나면 `awaiting_resume`으로 바뀌어 "이어서 재생"/"여기서 종료"를 고른다. 기다리는 사이 심사 동의가 만료·철회되면 재개 요청은 이유를 오류로 남기고 재생을 종료한다(`stopped`). 재조정 결과(유예·분할)는 이후 재생 흐름에 반영하지 않는다(셀프 플랜 변경만 반영, `docs/limitations.md`).
- **실패 시 동작:** 에이전트를 쓸 수 없거나 `propose` 없이 끝나면(`draft_fallback: no_proposal`) 최근 경보가 high면 `hardship_offer`, 아니면 `missed_payment`를 폴백 제안(`by="fallback"`, 재조정 상담 없음)으로 만든다. 폴백 제안도 똑같이 심사역 승인을 거쳐야 보낸다. 에이전트가 실패 전에 `propose`를 했으면 그 제안을 쓴다.
- **모델 근거:** 스크립트 선택·분류 중심이라 `gpt-5.6-luna`(`COLLECTIONS_MODEL`).

## 동의 안내

- **목적:** 동의 만료 예정 갱신 안내와 삭제 완료 안내문을 쉬운 한국어로 쓴다(L4, 동의 수명주기).
- **도구:** 없음. 사실(facts)만 받아 쓴다(`src/agents/notices.py`의 `write_notice`, `consent_facts`).
- **가드레일:** 개인 식별 정보는 넘기지 않음. 결과를 결정적으로 검사(필수 사실 포함 여부, 근거 없는 숫자 금지). 검사 통과 못 하면 템플릿 문안(`source="template"`)을 쓴다. 대화는 쓰고 바로 삭제(`_delete`).
- **모델 근거:** 안내문 작성이라 `gpt-5.6-luna`(`NOTICE_MODEL`).

## 이의 회신

- **목적:** 판단 기록(정책 사유·반사실)과 심사역 결정만으로 이의제기 회신 초안을 쓴다(L3).
- **도구:** 없음. `grievance_facts`로 만든 사실만 입력하며 차주가 쓴 이의 내용은 넣지 않는다.
- **가명 원칙:** 심사역 결정 사유(자유 입력)는 그 신청의 가명처리기(`load_case_raw` + `build_pseudonymizer`)로 마스킹해 넣는다. 원천이 없으면(`None`) 패턴 마스킹만 쓰고, 원천을 읽다 예외가 나면 사유를 모델에 보내지 않는다(템플릿 회신에는 원문 사유를 쓴다). 가명 토큰(`[[BIZNAME_…]]` 등)이 든 반사실 설명은 템플릿에 넣지 않는다.
- **가드레일:** 입력에 없는 숫자·금리·약속 금지, 초안에 가명 토큰이 있으면 실패. 검사 미통과 시 템플릿 대체. 대화 즉시 삭제.
- **모델 근거:** `gpt-5.6-luna`(`GRIEVANCE_MODEL`).

## 심사역 결정 채팅

- **목적:** 결정 탭에서 심사역이 현금흐름·정책 판정·에이전트 의견에 대해 자연어로 묻고 답을 받는다(L1, 결정 보조만).
- **입력:** 콘솔이 신청 문서로 만든 가명 스냅샷(`build_snapshot`: 월별 현금흐름, 수치 특성·신호, 상환 여력과 월 상환액 계산에 쓴 가정 금리(`assumed_annual_rate`, 연 8%), 정산 대사 요약, 정책 사유, 의견의 주장·근거 값·반사실·확인 사항(`officer_checks`), 심사역 결정 종류·금액)과 브라우저가 보낸 최근 6턴. 스냅샷은 원천 Blob 없이 만들며 실명·사업자번호·심사역 ID·결정 사유·거래 원문은 넣지 않는다. 심사역이 쓴 질문·앞선 대화는 그 건의 원천(프로필·거래)으로 만든 가명처리기로 가린 뒤 보낸다(`OfficerChat.ask(mask=...)`). 원천이 없는 KRW 건은 패턴과 신청인 ID로 확인되는 등록 상호·번역 별칭을 가린다. USD 원천이 없거나 원천 읽기가 실패하면 모델을 호출하지 않는다(502). 24,000자를 넘으면 주장 → 반사실 → 확인 사항 → 오래된 월 순으로 줄이고(`_fit`, 각 목록을 끝부터 모두 비운 다음에야 다음 목록으로 넘어간다) `truncated`를 표시한다(정책 부분은 줄이지 않음).
- **전액 기준:** 도우미는 현행 권고·근거·다음 확인 사항을 설명한다. 과거 의견으로 새 권고를 추정하지 않는다. 대체 금액은 부정문으로도 인용하지 않으며, 감액 제안이 감지되면 검토된 권고와 정책 근거로 답한다. 신청액·정책 한도·정산 입금·실제 결정액은 제안액이 아니라 사실 정보다.
- **정책 판정 설명(2026-11-09):** 콘솔 이미지가 `policy`의 순수 모듈(`rules`·`engine`)과 `rulesets/*.yaml`을 함께 담는다. 스냅샷 `policy.rules`는 판정의 `rules_version`에 해당하는 규정 원문(상품 한도·배수·단위, 부적격·회부 규칙, 금리 구간표)이고, `policy.trace`는 저장된 특성으로 `explain()`을 다시 돌린 이 건의 계산 과정(규칙별 실제값·기준값·결과 `hit`/`pass`/`not_applied`, 한도 산식, 구간 결정)이다. 버전을 모르거나 특성이 계약에 맞지 않거나 재계산 결과(판정·한도·구간·사유 코드)가 저장된 판정과 다르면 `trace`는 `null`이고 `trace_note`에 이유를 적는다. v1 규정은 2026-11-04에 버전 이름을 그대로 둔 채 상품 한도가 바뀌었으므로(`AMENDED_IN_PLACE`), 그 전에 접수된 v1 판정은 `rules`·`trace`를 모두 싣지 않는다. 구간별 실제 금리와 기준값을 정한 이유는 정책에 없어 "자료에 없음"으로 답한다. **USD 규정(2026-11-25):** USD 상품은 `usd-v2` 규칙셋이 현행이다(`USD_CURRENT_RULES = {"usd-v1": "usd-v2"}`, 상품 버전 → 현행 규정 매핑). `usd-v2`는 정산·마켓 대체 규칙 S003–S005와 위험 규칙 R111–R113을 더하고, 규칙셋의 `alt_data_features`가 "은행만 볼 때"의 지움 범위를 정한다. 비교 문구는 `usd-v2`가 "Bank and tax records only" / "With settlement and marketplace data", `usd-v1`이 "Cash flow only" / "With seller data"다. 은행 분류(`agents/classify.py`)는 TRANSFER_IN + "payout"을 REVENUE로 본다(선지급 규칙 다음에 적용). 셀러가 "Marketplace payout"으로 기록한 선지급이 은행만 보는 쪽에서 매출처럼 보이게 하려는 것이다. 마켓 행은 `orders_90d`·`seller_grade`·`assigned_pending_cents`를 싣고, 순 정산 예정액은 양도된 금액을 뺀다. USD 전용 장면 셀러 8곳(`USD_ONLY_SELLER_IDS`)은 KRW 사례 없이 mock API·콘솔·사후 흐름을 통과하며, 성장 셀러는 사후 시작일부터 성장 배수를 적용한다.
- **답변 조립:** 모델은 관련 근거의 스냅샷 경로를 최대 6개 고른다. 서버는 저장된 검토 상태로 전액 권고 문장을 만들고, 선택한 필드의 이름·값·통화를 붙인다. 모델의 자유 문장과 알 수 없는 경로는 버리되 같은 답의 맞는 경로는 남긴다. 객체·배열 항목 경로(예: `policy.reason_codes.0`)는 그 아래 값이 8개 이하일 때만 펼친다(`selected_paths`, 2026-11-28). 실제 심사역 승인 기록이 없으면 승인액은 ‘기록 없음’이다. 대화는 답을 받은 뒤(실패해도) 바로 지운다. 대화 기록은 브라우저 메모리에만 있고 서버·Cosmos에 남기지 않는다. 로그는 신청 ID·결과·지연·토큰 수만.
- **제안 칩(2026-11-28, `src/agents/officer_topics.py`):** 칩 7종(정책 판정과 사유, 한도·금리 구간 계산, 상환 여력 부족 달, 에이전트 권고 근거, 결정 전 확인 사항, 마켓·정산 위험 신호, 마켓·정산 자료 없이 본 판정)은 스냅샷에 그 근거가 있을 때만 보인다. 예를 들어 부족한 달이 없거나, 검토를 통과한 주장·확인 사항이 없거나, `cash_only` 비교 판정이 없으면 해당 칩이 숨는다. 칩을 누르면 `topic`만 보내고, 서버가 정해진 근거로 바로 답한다(모델 호출·토큰 없음, 로그 `topic=`). 첫 줄은 자유 질문과 같은 전액 권고 문장이다. 이전 칩 4개는 모델이 경로를 고르게 했는데, 2026-11-28 운영 신청 3건에서 답 12건 중 9건에 근거가 붙지 않았다.
- **확인 사항과 후속 질문:** 현행 검토를 통과한 주장·확인 사항은 지표 또는 거래 ID와 함께 읽는다. 거래 원문은 보내지 않는다. 영문 화면은 카탈로그·저장 번역을 사용하고 없으면 검토된 원문을 유지한다. 대체 대출 제안은 질문과 대화 기록에서 제외하지만 “700만 원 매출은 몇 월인가요?”와 그 후속 질문은 그대로 전달한다. 해당 월의 상환 여력이 부족하면 매출에서 운영비를 뺀 금액과 기존·신규 상환액의 관계를 설명한다.
- **실패 시:** 429(5번 지수 백오프 뒤)·콘텐츠 차단·기타 실패를 안내 문구로 구분해 보여 준다. 결정 폼과 독립이다.
- **모델 근거:** 전용 배포 `gpt-6-luna`(250K TPM, 쿼터 0/250에서 100으로 신설 뒤 2026-11-09 250으로 증설)로 조기경보의 `gpt-5.6-luna`와 용량을 나누지 않는다(`CHAT_MODEL`). 2026-11-14부터 영문 화면 번역과 나눠 쓴다(APIM `gw-officer-chat` 200K + `gw-translator` 50K).

## 영문 화면 번역

- **목적:** 콘솔 영문 모드에서 카탈로그에 없는 데이터 문구(에이전트 의견·요약·신호, 경보 요약, 심사역 사유, 차주에게 보낸 문구, 이의 내용·회신)를 영어로 보여 준다(L1, 표시만).
- **사전 번역(2026-11-15, `src/orchestrator/journey/pretranslate.py`·`src/agents/pretranslate.py`):** 문서가 만들어지면 Cosmos 변경 피드 트리거 `pretranslate_<source>`(`applications`·`loans`·`alerts`·`grievances`)가 번역 대상 문구만 뽑아(`ALLOWLIST`: 허용 경로의 한국어 문자열. 정책 사유는 카탈로그에 있고, 시스템 오류 문자열과 코드가 만든 문구는 뺐다) 가명처리하고 영어로 번역해 `translations` 컨테이너(`/owner_id`)에 저장한다. owner는 `applications`·`grievances`가 `application_id`, `loans`·`alerts`가 `loan_id`(= 신청 ID)다. 문서는 `id`(마스킹 문구 sha256), `owner_id`, `source`, `en`, `model`, `translated_at`이고 원문은 저장하지 않는다. 이미 저장된 문구는 건너뛰므로 같은 문서가 다시 바뀌어도 새 문구만 번역한다. 원 문서는 쓰지 않는다(트리거 루프 없음). 심사·사후 흐름 밖에서 돌아 흐름을 지연시키지 않는다.
- **트리거 운영:** 리스 컨테이너 `leases`(`/id`)에 소스별 접두사 `<source>-g<세대>-`(`LEASE_GENERATION`, 현재 2)로 리스를 두고, 리스가 처음 만들어질 때만 모듈 로드 시각 7일 전부터 읽는다(기존 문서 백필. eval·load·smoke의 합성 신청을 전부 읽지 않으려는 것). 허용 목록 경로를 더했거나 실패한 번역을 다시 시도하려면 `LEASE_GENERATION`을 올린다: 새 리스가 만들어져 최근 7일을 다시 읽고, 저장된 문구는 건너뛰어 빠진 문구만 모델에 간다(데이터 평면이 사설이라 리스를 손으로 지울 수 없다). 옛 세대 리스 문서는 `leases`에 남지만 무해하다. `CosmosDBConnection__accountEndpoint`가 있을 때만 등록한다. 컨테이너마다 리스 하나라 소스별로 직렬 처리되고, 한 호출은 최대 20건(`max_items_per_invocation`)이며 호출 안에서도 25개·8,000자 배치를 순서대로 보낸다. 모델은 `TRANSLATE_MODEL`(`gpt-6-luna`)이고 연결 `gw-translator`(50K TPM)는 콘솔 즉시 번역과 나눠 쓴다.
- **가명 원칙:** 번역 모델에는 가명처리한 문구만 보낸다. 마스킹은 그 건의 가명처리기(알려진 상호·대표자·사업자번호·적요 이름 + 패턴)로 한다. 대출·경보는 심사 동의의 프로필과 모니터링 동의의 사후 흐름, 이의는 그 신청의 원천을 쓴다(`load_case_raw`). 원천 Blob이 없으면(`None`, 예: 동의 삭제 뒤) 패턴 마스킹(전화번호·사업자번호 등)만 쓴다. 원천을 읽다 **예외**가 나면 그 owner는 번역하지 않고 건너뛴다(패턴 마스킹으로 대신하지 않는다. 엔티티 없이 마스킹한 문구가 상호·이름을 그대로 모델에 보낼 수 있다). 영어 결과의 토큰 집합이 마스킹 문구와 다르면(토큰이 빠지거나 바뀌거나 늘어남) 그 번역은 저장·캐시·표시 모두 버리고 원문을 둔다. 실패(429·차단·개수 불일치·저장 오류)도 그 배치·항목만 건너뛰고 트리거 함수는 예외를 올리지 않는다(올리면 같은 묶음이 계속 재시도된다). 로그에는 소스·owner·개수·글자 수·토큰 수·결과, 예외는 종류 이름만 남기고 문구는 남기지 않는다.
- **표시 순서(`src/console/translate.py`, `|mt`·`|mt_ref`):** ① 데이터 카탈로그(`locales/en/data.json`: 셀러 상호·채널·업종·지역·업력 구간·정책 사유·고정 안내 문구)에 원문이 있으면 그 영문, ② `translations`의 저장 번역(`PageTexts`가 화면 문구를 다시 마스킹한 키로 찾는다. 읽기는 `for_owner`로 owner 단위), ③ 콘솔 메모리 캐시(키는 마스킹 문구, 레플리카당 5,000건), ④ 모두 없으면 페이지는 원문에 `data-mt`(마스킹 문구)를 달아 그리고 브라우저(`static/mt.js`)가 `POST /i18n/translate`(CSRF 필수, 요청당 80개·문구당 2,000자·합계 30,000자)로 한 번에 받아 바꾼다. 서버는 25개·8,000자씩 나눠 `console-translator`를 부른다. 콘솔은 Cosmos에 쓰지 않으므로 즉시 번역 결과는 메모리 캐시에만 남는다. 동의 안내문(`consents` 컨테이너)은 사전 번역 소스가 아니라 계속 ④로 번역한다. 콘솔이 조립한 문구(원 문서에 그대로 없는 문장)도 같다.
- **복원·토큰 지도:** 영문 화면은 해당 문구에서 마스킹으로 새로 생긴 토큰만 복원한다(`tokens(마스킹 문구) − tokens(화면 문구)`). 저장 번역은 `PageTexts.show`가 서버에서 복원한다. 즉시 번역은 `mt.js`가 각 요소의 `data-mt-restore`만 사용한다. 마스킹 문구가 같아 번역 응답을 한 번만 받아도, 한 요소에서 보인 이름이 다른 요소의 감사자용 문구에 복원되지 않는다. 고객 콘솔 내 대출 관리(`/borrower/<신청인>/…`)는 여러 신청이 섞여 그 신청들의 엔티티를 합친 가명처리기를 쓴다.
- **표시:** 기계 번역은 점선 밑줄과 "Machine translation · original: …" 툴팁을 단다. 차주에게 보낸 문구와 차주가 쓴 이의 내용은 원문(실제 발송·접수 문구)을 그대로 두고 아래에 `EN ·` 참고 번역을 붙인다. 감사·근거 기준은 언제나 원문이다.
- **즉시 번역 가드레일(`/i18n/translate`):** 받은 문구를 패턴 마스킹으로 한 번 더 가린 뒤 번역한다(정상 클라이언트는 이미 마스킹한 문구를 보내 변화가 없다). 숫자·코드·가명 토큰·영문 약어는 그대로 두고 금액은 `₩`로 쓴다. 개수가 다르면 실패로 보고 원문을 둔다. 토큰이 사라진 항목은 `""`를 돌려 원문이 남는다. 대화는 바로 지운다. 로그는 결과·개수·글자 수·지연·토큰 수만 남긴다.
- **실패 시:** 429·차단·기타 실패면 원문(한국어)이 그대로 남는다. 이의·고객 콘솔 화면에서 원천 읽기가 **예외**로 실패하면 그 화면은 새 번역 대기(`data-mt`)를 내지 않고 저장 번역·카탈로그만 쓰며 나머지는 한국어로 보인다(엔티티 없이 마스킹한 문구를 모델에 보내지 않으려는 것). 원천이 없으면(`None`, 동의 삭제 뒤) 패턴 마스킹만으로 같은 키를 만들어 저장 번역을 찾고 번역 대기도 낸다. `AI_PROJECT_ENDPOINT`·`TRANSLATE_MODEL`(없으면 `CHAT_MODEL`)이 없으면 즉시 번역을 끄고 저장 번역·카탈로그 데이터만 영문으로 바뀐다. 번역기가 없으면 트리거도 번역하지 않는다.

## 공통 런타임

- **도구 호출 루프(`src/agents/runtime.py`):** 프롬프트 에이전트를 REST로 돌린다. 버전 생성(`POST /agents/{name}/versions`) → 대화 생성 → 응답 생성(`agent_reference` + conversation). `function_call`이 있으면 VNet 안에서 핸들러를 실행해 `function_call_output`으로 되돌리며, 최대 `max_rounds=6` 라운드(직접 호출 기본값. AI 게이트웨이를 거치는 에이전트는 아래 `GATEWAY_MAX_ROUNDS = 12`). 도구 예외 내용은 모델에 노출하지 않는다(`{"error": ...}`만 반환). 출력은 `output_model`로 스키마 검증한다.
- **대화·컨테이너 삭제:** `delete_conversation`은 입력·출력 항목을 포함해 대화를 지우고(`deleted`/`absent`/`failed`), `delete_container`는 Code Interpreter 컨테이너(가명 집계 데이터가 올라간 곳)를 즉시 삭제한다. 동의 철회·수명주기 종료 시 남은 Foundry 대화를 정리한다.
- **계량(`src/orchestrator/journey/metering.py`):** `MeteredRunner`가 호출마다 모델·입출력 토큰·라운드를 모으고, `step_records`가 단계 기록(`steps[]`)으로 바꾼다. 거래 분류는 심사 지표 산출의 하위 단계(`classify`)로 분리 기록하고, Decision-1 호출 수를 `decision_calls`로 센다. 단계 기록에는 예외 종류·표준 코드만 남긴다(`safe_detail`).
- **AI 게이트웨이(`src/agents/gateway.py`, [ADR-010](adr/ADR-010-ai-gateway.md)):** `AI_GATEWAY=apim`이고 `AI_GATEWAY_URL`이 있으면 `GatewayRunner`가 모델 이름을 `gw-<agent>/<model>`로 바꿔 Foundry BYOM 연결로 APIM을 거친다(결과의 모델은 원래 배포 이름으로 되돌려 계량·단가에 쓴다). 거래 분류 Decision-1은 `AI_GATEWAY_URL`의 decision API를 직접 부르고(콘텐츠 안전 없음), 채점자(quality-judge)는 직접 호출한다. 429·403은 `GatewayThrottled`·`GatewayBlocked`로 구분한다. 연결표(`CONNECTIONS`)와 최종 TPM(`infra/resources.bicep`의 `gateways`):

  | 에이전트 | 연결 | TPM |
  |---|---|---|
  | `underwriting-analyst` | `gw-analyst` | 250,000 |
  | `early-warning-investigator` | `gw-early-warning` | 150,000 |
  | `underwriting-reviewer` | `gw-reviewer` | 50,000 |
  | `data-assembly` | `gw-data-assembly` | 40,000 |
  | `txn-classifier` | `gw-txn-classifier` | 20,000 |
  | `collections-agent` | `gw-collections` | 10,000 |
  | `consent-notice-agent` | `gw-consent-notice` | 5,000 |
  | `grievance-reply-agent` | `gw-grievance` | 5,000 |
  | `officer-assistant` | `gw-officer-chat` | 200,000 |
  | `console-translator` | `gw-translator` | 50,000 |
  | (프로브 전용) | `gw-probe` | 1,000 |

- **도구 라운드 상한:** 직접 호출은 `max_rounds=6`을 유지한다. 게이트웨이(chat completions) 경로에서는 모델이 턴당 도구 호출을 덜 해서 조기경보 조사가 모든 주차에서 6라운드 상한에 걸렸기 때문에, 라우팅되는 에이전트(`GatewayRunner`, 평가의 `EvalRunner`)에만 `GATEWAY_MAX_ROUNDS = 12`를 적용한다. 대가는 조사 한 번당 최대 2배의 라운드·토큰이다(건당 AI 비용 변화는 [비용 모델](cost-model.md#게이트웨이-경로-건당-ai-비용)).
