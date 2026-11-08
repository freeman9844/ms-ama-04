# 마켓플레이스 정산 원장 활용과 정책 v3 설계

- 날짜: 2026-11-08
- 근거: 사용자 요청("에이전트 의견에서 마켓플레이스 정산데이터를 좀 더 활용할 수 있는 방안"), 이전 요청("R101 은 제외해주고 R105~R107 추가")
- 결정(사용자 선택): 활용 범위 ①정산–입금 대사 ②채널 믹스·집중도 ③공제율 추이 ⑤에이전트 도구·근거 ID / 정책 v3와 묶어 설계 / 정산 자료가 없으면 정산 규칙 미적용·"정산 자료 없음" 표시 / 모의 원장 분리(시점 차이·상계·양도) / 양도는 새 원형 ASSIGNED / 접근 A(결정적 지표 + 에이전트 도구) / R107은 단일 플랫폼 셀러도 적용

## 1. 문제

PG 정산(`pg.json`)은 데이터 수집 에이전트가 모아 원천 저장소에 넣지만, 심사(`underwrite`)와 에이전트 평가(`assess_credit`)는 읽지 않는다(`_load`가 profile·transactions·tax만 읽음). 화면과 에이전트 입력에는 건수·기간·플랫폼명 요약뿐이다. 정산 프로필(`settlement_profile`)도 정산 원장이 아니라 계좌 입금에서 추정한다.

또 모의 데이터는 같은 지급 건(`Payout`)에서 계좌 입금과 정산 레코드를 함께 만들어 둘이 항상 같다. 수수료율도 채널 상수라 추이가 없다. 지표만 추가하면 데모에서 아무것도 걸리지 않는다.

## 2. 목표와 비목표

**목표**
- 정산 원장과 계좌 입금을 대조해 매출 실재성(대사율)을 잰다.
- 정산 원장 기준 채널 비중·집중도와 실효 공제율 추이를 잰다.
- 지표를 정책 v3 규칙, 분석 에이전트의 근거 ID·도구, 상세 화면에 연결한다.
- 모의 데이터가 정산 원장과 계좌 입금을 분리해 만들어 현실적인 차이가 생기게 한다.

**비목표**
- 반품률(정산 레코드에 반품 필드 없음), 정산 예정금 추정(④, 선택 안 함)
- 매출 부풀리기 시나리오(원장에 없는 '정산' 입금), 정산 보류 시나리오
- 공제율 추이 정책 규칙(표시·의견 전용)
- 심사역 채팅 패널(별도 설계)

## 3. 설계

### 3.1 모의 데이터: 원장 분리

**계약.** `PgSettlement`(`src/shared/contracts.py`)에 `deduction_krw: int = Field(default=0, ge=0)`(광고비·물류비 상계)를 추가하고 검증식을 `net_krw == gross_krw - fee_krw - deduction_krw`로 바꾼다. 기본값이 0이라 기존 JSON은 그대로 유효하므로 `schema_version`은 "1"을 유지한다.

**생성 2단계(`src/mockapis/sellerdata/`).**
1. 플랫폼 정산 원장: 채널별 `payouts`가 지급 예정일·총액·수수료·상계액을 낸다. 이것이 `pg_settlements`다.
2. 계좌 입금 반영기(새 함수, 예: `bank_receipts`): 원장을 계좌 이벤트로 바꾸며 아래 차이를 넣는다.
   - **시점 차이(모든 셀러):** 지급 건의 약 15%를 1~2영업일 늦게 입금한다(셀러 rng로 결정적).
   - **상계(모든 셀러):** 쿠팡·스마트스토어·11번가 지급에 총액의 1~4% 광고비 상계를 넣는다(셀러마다 기본률 고정). 일부 셀러(시드 기준 약 1/4)는 기준일 직전 3개월 상계율이 1.5~2.5%p 오른다. 자사몰(토스페이먼츠)은 상계 없음. 상계는 정산 레코드에 있고 입금액은 실지급액(`net_krw`)이므로 대사율에는 영향이 없고 공제율 추이에만 나타난다.
   - **양도(ASSIGNED 원형만):** 기준일 90일 전부터 정산 총액 비중이 가장 큰 플랫폼의 지급이 계좌로 들어오지 않는다. 대신 그 지급의 약 85%가 "한결선정산 선지급"(`TRANSFER_IN`)으로 지급 예정일 5~7일 전에 들어온다. 정산 원장에는 원래 지급이 그대로 남는다.

**새 원형·데모 셀러.** `Archetype.ASSIGNED = "assigned"`와 `seller-mugshop-21`(리빙, 자사몰+쿠팡+스마트스토어, 시드 121)을 추가한다. 매출·지출·부채 신호는 정상 셀러처럼 만든다.

**영향.** 모든 셀러의 계좌 입금액이 상계만큼(1~4%) 줄고 일부 입금일이 바뀐다. 결정 불변성 테스트(`tests/mockapis/test_decision_invariance.py`), 평가 데이터셋 기대값, 데모 스크립트를 새 정책과 함께 다시 맞춘다.

### 3.2 정산 지표(`src/agents/pg_ledger.py`, 결정적)

입력: 정산 레코드, 계좌 거래, 기준일. 대상 기간은 최근 6개 완결 월(`features.py`의 완결 월 정의와 같음).

- **플랫폼 판별:** 계좌 입금은 `settlement.py`의 입금처 키워드(`_CHANNELS` 계열)로 플랫폼에 매핑한다. 정산 레코드는 `platform` 필드를 쓴다.
- **대사 매칭:** 정산 1건과 아직 짝이 없는 계좌 입금 1건이 (같은 플랫폼, 입금일 ∈ [지급일, 지급일+3영업일], 금액 == `net_krw`)이면 짝이다. 지급일 순, 같은 조건에서는 가장 이른 입금을 고른다.
- **`pg_reconciliation_ratio`** = 매칭 실지급액 합 ÷ 대상 실지급액 합(소수 셋째 자리). 기준일 직전 3영업일 이내 지급은 대상에서 뺀다(아직 입금 전일 수 있음).
- **미매칭:** 플랫폼별 미입금액과 미매칭 정산 목록(플랫폼·지급일·실지급액, 지급일 역순).
- **`pg_top_platform_share`:** 정산 총액(`gross_krw`) 기준 최대 플랫폼 비중. **`pg_hhi`:** 비중 제곱합(0~1). 플랫폼별 비중도 함께 낸다.
- **공제율:** 월별 실효 공제율 = (수수료+상계)/총액. **`pg_take_rate_trend_pp`** = 최근 3개월 평균 − 이전 3개월 평균(%p, 소수 둘째 자리).
- **자료 없음:** 정산 레코드가 0건이거나 PG 동의가 없으면 모든 정산 지표가 None이고 결과는 `{"available": false}`다.

### 3.3 특성 계약과 정책 v3

`CashFlowFeatures`에 선택 필드를 추가한다(모두 `None` 허용, strict 유지).
- `pg_reconciliation_ratio: float | None`(0~1), `pg_top_platform_share: float | None`(0~1)
- `high_cost_lender_count: int | None`(≥0, `_compute`의 기존 신호 값)
- `revenue_trend_ratio: float | None`(≥0) = 기준일 직전 60일 매출 일평균 ÷ 그 앞 180일 일평균. 이력이 240일보다 짧으면 있는 만큼(앞 구간 최소 60일)으로 계산하고, 그보다 짧으면 None.

`src/policy/rulesets/v3.yaml`(version `"2026.11-v3"`, v2는 이력 보존)과 `DEFAULT_RULES_PATH` 변경:

| 코드 | 변경 | 규칙 |
|---|---|---|
| R101 | 삭제 | 신고매출 불일치. 지표 `tax_bank_match_ratio`는 계산·표시·근거 ID로 유지 |
| R105 | 추가 | "고비용 대부·P2P 상환 발견": `high_cost_lender_count gt 0` |
| R106 | 추가 | "최근 매출 급감": `revenue_trend_ratio lt 0.80` |
| R107 | 추가 | "판매 채널 편중": `pg_top_platform_share gt 0.80` (단일 플랫폼 셀러 포함) |
| R108 | 추가 | "정산–입금 대사 불일치": `pg_reconciliation_ratio lt 0.90` |

- `FeatureName`에 네 필드를 추가한다.
- 엔진 `_check`는 관측값이 None이면 규칙을 적용하지 않는다(사유 없음). 정산 자료가 없으면 R107·R108이 빠진다.
- **임계값 검증:** 3.1 모의 데이터를 만든 뒤 20+1 데모 셀러와 평가 데이터셋으로 분포를 잰다. R108은 정상 셀러 최솟값 > 0.90 > ASSIGNED 값, R106은 STRESSED 최댓값 < 0.80이 목표다. 어긋나면 구현을 멈추고 조정안을 사용자에게 보고한다.
- **예상 판정 변화:** TAX_MISMATCH 4명 → 다른 사유가 없으면 적격. STRESSED → R105·R106 검토. candle-05·ceramic-08(단일 플랫폼) → R107 검토. mugshop-21 → R108 검토. 계절성으로 일부 정상 셀러가 특정 기준일에 R106에 걸릴 수 있다.

### 3.4 오케스트레이터 데이터 흐름

- `_load`가 `pg.json`을 선택으로 읽는다(없으면 빈 목록).
- `underwrite`가 `pg_ledger` 결과를 특성에 넣고, 상세(플랫폼별 비중·미입금액, 월별 공제율, 미매칭 상위 10건)를 반환값 `"pg_ledger"`로 돌려 `doc.underwriting`에 저장한다.
- `assess_credit`는 `pg_ledger`를 `UnderwritingContext`로 넘긴다.

### 3.5 에이전트

- **근거 ID:** `get_metrics`의 지표 맵에 `M:pg_reconciliation_ratio`, `M:pg_top_platform_share`, `M:pg_hhi`, `M:pg_take_rate_trend_pp`를 추가한다(None이면 넣지 않음). `check_grounding`·숫자 대조는 그대로 동작한다. 정책 사유 `P:R105~R108`도 기존 방식대로 들어간다.
- **새 도구 `get_settlement_summary`:** 인자 없음. `pg_ledger` 상세를 돌려준다(자료 없음이면 `{"available": false}`). `ANALYST_TOOLS`에 추가.
- **지시문:** 분석가에게 "정산 자료가 있으면 대사율·채널 집중·공제율 추이를 확인하고 해당 M: ID로 인용"하는 단계를 추가한다.
- **선정산 입금 분류:** `classify.py` 규칙에 (`TRANSFER_IN`, "선정산", `LOAN_PROCEEDS`)를 추가해 선지급이 매출로 잡히지 않게 한다.
- 검토자 구조·모델은 그대로다.

### 3.6 콘솔

상세 화면 현금흐름 탭에 "정산 대사" 카드를 추가한다(`src/console/dashboard.py`·`detail.html`, 기존 차트 매크로 재사용, 인라인 스크립트·스타일 없음).
- 대사율(0.90 미만이면 경고색), 플랫폼 비중 가로 막대, 월별 공제율 선, 미입금 정산 상위 5건 표.
- `pg_ledger`가 없거나 `available=false`면 "정산 자료 없음".
- 정책 사유 라벨에 R105~R108을 추가하고 R101 라벨은 옛 신청 표시용으로 남긴다.

### 3.7 평가

- 심사 suite(`src/evals/suites/underwriting.py`): `TAX_EVIDENCE`를 `("M:tax_bank_match_ratio",)`로 줄인다. ASSIGNED 사례를 데이터셋에 넣고 `settlement_gap_detection_rate`(주장이 `M:pg_reconciliation_ratio` 또는 `P:R108`을 인용)를 추가한다.
- 평가 실행(`ops eval`)은 비용이 들어 별도 승인 후 실행한다.

## 4. 오류 처리

- `pg.json` 파싱 실패: 정산 자료 없음으로 처리하고 `pg_ledger_unavailable` 플래그를 남긴다(심사는 계속).
- 플랫폼 매핑이 안 되는 정산 레코드: 미매칭으로 센다(대사율을 낮춘다. 데이터 이상이므로 사람이 본다).
- 정책 서비스와 오케스트레이터 배포 순서: 새 선택 필드는 기본값 None이라 정책 서비스를 먼저 배포해도 옛 특성을 받는다. 오케스트레이터를 먼저 배포하면 옛 정책 서비스가 모르는 필드를 거부할 수 있으므로 **정책 → mockapi → 오케스트레이터 → 콘솔** 순서로 배포한다.

## 5. 테스트

- `tests/agents/test_pg_ledger.py`(신규): 완전 일치 1.0, 시점 차이(+2영업일) 매칭, +4영업일 미매칭, 금액 불일치, 기준일 직전 제외, 단일 플랫폼 비중 1.0, HHI, 공제율 추이, 자료 없음 None.
- `tests/shared`: `PgSettlement` 상계 검증식.
- `tests/mockapis`: 원장 분리 후 정상 셀러 대사율 = 1.0(시점 차이 흡수), 상계 반영, ASSIGNED 대사율 < 0.90과 선지급 입금 존재, 결정 불변성 기대값 갱신.
- `tests/policy`: v3 규칙, None 미적용, R101 부재.
- `tests/agents/test_underwriting.py`: 새 도구 핸들러, 근거 ID 포함/제외.
- `tests/orchestrator/test_activities.py`: `underwrite`가 `pg.json`을 읽고 `pg_ledger`를 돌려줌, 없을 때 None.
- `tests/console`: 정산 카드 표시·자료 없음 표시.
- 문서: `docs/agents.md`(도구·지시), `docs/architecture.md`(정책 v3), 정책 문서·ADR-003 메모, 데모 스크립트.

## 6. 배포·검증

정책 → mockapi → 오케스트레이터 → 콘솔 순서로 배포하고 `ops smoke`. 이어 `seller-mugshop-21`과 정상 셀러 1명을 실제로 신청해 R108·정산 카드·에이전트 의견의 정산 인용을 확인한다.
