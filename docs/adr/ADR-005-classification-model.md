# ADR-005: 거래 분류 — Microsoft-Decision-1 + mini 폴백

## 상태
채택(P1·P6a, 2026-10-31~11-02) · as-built

## 맥락
은행 거래 적요·가맹점명 같은 비정형 한국어 텍스트를 고정 카테고리(매출·대표자 이체·타 금융사 상환·세금·급여 등)로 분류해야 한다. SME 거래는 신청당 수백~수천 건이라 거래마다 범용 LLM을 호출하면 비용·지연·TPM 쿼터가 문제가 된다. 상위 Spec §3.2(계층형 분류 파이프라인)는 규칙 선분류 → 정규화·중복 제거 → 의사결정 모델 → 폴백 → 캐시 순서를 규정한다(`docs/superpowers/specs/2026-10-31-agentic-cashflow-lending-design.md` §3.2).

## 결정
계층형 파이프라인을 쓴다(`src/agents/classify.py` → `src/agents/txn_classify.py`): ① 결정적 규칙 `classify-v1`로 선분류, ② 남은 적요 정규화·중복 제거, ③ 고유 적요마다 **Microsoft-Decision-1**(`DECISION_DEPLOYMENT=decision-1`)에 고정 선택지(+`cannot_tell`)로 병렬 질의, ④ 기권·저신뢰(`DEFAULT_THRESHOLD=0.7`)·오류만 mini LLM 폴백 에이전트 `txn-classifier`로, ⑤ 분류 캐시로 신청 간 재사용(가명화 토큰 포함 적요는 캐시 제외). 거래마다 LLM을 호출하지 않는다. 모델로 가는 적요는 가명처리하고, 적요는 데이터일 뿐 지시가 아니라고 지시문에 명시한다.

## 검토한 대안
| 대안 | 장점 | 기각 이유 |
|---|---|---|
| 범용 mini LLM 단독 | 구성이 단순, 신청당 모델 호출은 오히려 더 적음(1.000회 vs 8.375회, `eval/reports/job-eval-9imbutm/report.md:20-21`) | 호출당 지연이 ~7배 크다(p50 2478ms vs Decision-1 349ms, `eval/reports/job-eval-9imbutm/report.md:94`·`:97`). 분류가 범용 LLM TPM 쿼터를 소모해 심사·설명 경로와 경합(근거 기반 추론, 미측정) |
| 규칙만 사용 | 비용 0·결정론적 | 전체 거래의 80.0%만 분류(`eval/reports/job-eval-9imbutm/report.md` 2장), 비정형 적요를 놓침 |
| 범용 상위 모델(gpt-5.4) 분류 | 품질 상한 | 비용이 가장 큼($0.0410/신청, 같은 보고서). 분류엔 근거 서술이 불필요해 과함 |

## 결과
- 장점: 규칙이 80.0%를 거르고 캐시 적중 25.0%로 모델 호출 자체를 줄인다(`eval/reports/job-eval-9imbutm/report.md` 2장). 남은 분류를 경량 **Decision-1**으로 보내 호출당 지연을 낮추고(p50 349ms vs mini 2478ms, `eval/reports/job-eval-9imbutm/report.md:94`·`:97`), 범용 LLM TPM 쿼터를 심사·설명 경로에 남긴다(근거 기반 추론, 미측정). Decision-1이 기권·저신뢰일 때만 mini로 폴백해 품질을 보전한다.
- 대가(명시): **mini 단독 대비 측정된 비용·호출 수 이점은 없다.** 오히려 `decision-1+gpt-5.4-mini`의 신청당 모델 호출은 8.375회로 mini 단독 1.000회보다 많다(`eval/reports/job-eval-9imbutm/report.md:20-21`; Decision-1 60회/8건 = 7.5 + mini 폴백, 같은 보고서 6장). 폴백 비율 25.0% vs 100.0%는 호출 수가 아니라 미해결 적요 그룹 기준 비율이다(`eval/reports/job-eval-9imbutm/report.md:20-21`). 세 구성 모두 macro F1 1.000(천장 효과)이라 품질 우위도 없다. 또 **Decision-1 단가가 공개 확인되지 않아 건당 총비용 효과는 미검증**이다(`$0.0047`은 단가 제외값). 운영 부하 실측에서도 Decision-1 48회는 토큰 0·단가 미확인으로 비용 집계에서 뺐다(`ops/reports/load-20261102110233/report.md` 2장).

## 재검토 조건
- Decision-1의 호출당 지연 우위(349ms vs mini 2478ms)가 사라지거나 mini류 모델의 지연·TPM이 개선되어, 경량 분류 모델을 따로 둘 근거가 없어질 때.
- Microsoft-Decision-1 공개 단가가 확정되어 총비용 비교가 가능해질 때.
- 실제 거래 분포에서 규칙 커버리지·캐시 적중률이 측정치(80.0%·25.0%)와 크게 다를 때.
- 더 어려운 적요 표본에서 구성 간 품질 차이가 드러나 F1 천장 효과가 깨질 때.

## 근거
- 구현: `src/agents/classify.py`, `src/agents/txn_classify.py`.
- 분류 지표: `eval/reports/job-eval-9imbutm/report.md` 2장·6장.
- 운영 부하 Decision-1 호출: `ops/reports/load-20261102110233/report.md` 2장.
- 상위 Spec: `docs/superpowers/specs/2026-10-31-agentic-cashflow-lending-design.md` §3.2.
- 에이전트 설명: [../agents.md](../agents.md).
