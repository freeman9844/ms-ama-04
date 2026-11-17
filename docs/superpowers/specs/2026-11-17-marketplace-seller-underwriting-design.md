# 마켓 셀러 데이터 기반 차별화 심사 설계 (정책 v4)

- 작성일: 2026-11-17
- 상태: 설계 승인 대기
- 관련: ADR-003(결정적 정책 엔진), ADR-009(원천 삭제·판단 보존), `src/policy/rulesets/v3.yaml`, `src/agents/pg_ledger.py`

## 1. 의도와 이유

지금 심사는 계좌 현금흐름(DSCR·완충일수·변동성·추세)과 PG 정산 대사 두 가지(R107 채널 편중, R108 대사 불일치)만으로 판정한다. 대상 고객이 온라인 마켓 셀러인데, 셀러만이 가진 운영 데이터(입점 기간, 평점·리뷰, 반품·취소율, 판매정지 이력, 앞으로 받을 정산금)는 쓰지 않는다.

사용자 요구: "기존 underwriting에서 현금흐름만 보고 대출 심사를 하는 게 아니라, 마켓플레이스 셀러라는 특징과 실제 마켓플레이스의 정상 데이터와 다양한 데이터를 통해서 차별화된 대출 심사를 하는 것."

성공 장면(데모·심사):
- 계좌 이력이 짧아 현금흐름만 보면 **탈락**인 셀러가, 마켓에서 오래 좋은 평판으로 팔아 온 기록으로 **심사역 검토(refer)** 로 올라온다.
- 현금흐름은 멀쩡한데 최근 **판매정지**를 당한 셀러는 탈락하고, **반품률·평점**이 나쁜 셀러는 검토로 간다(현금흐름에는 아직 안 보이는 위험).
- 판정 근거 화면에서 "현금흐름만 볼 때의 판정 → 셀러 데이터 반영 판정"과 어떤 규칙이 대체·면제됐는지가 그대로 보인다.

## 2. 목표 · 비목표 · 반목표

**목표**
1. 새 동의 범위 `marketplace`와 셀러 성과 Mock API를 추가하고, 기존 합성 셀러와 숫자가 맞는 데이터를 결정적으로 합성한다.
2. 결정적 코드가 셀러 지표를 계산해 `CashFlowFeatures`의 선택 필드로 넘긴다.
3. 정책 v4(`2026.11-v4`): 셀러 탈락·검토 규칙 + **대체(substitution)** 규칙(탈락·검토 규칙을 셀러 근거로 대체) + **정산 예정금 한도 보강**.
4. 엔진이 같은 입력으로 "현금흐름만 판정(cash-only)"을 함께 계산해 비교를 남긴다.
5. 분석 에이전트가 셀러 지표를 근거 ID로 인용하고, 콘솔이 셀러 지표·대체·비교를 보여 준다.

**비목표**
- 실제 마켓 API 연동, 스크래핑.
- ML 점수 모델(셀러 건전성 점수 단일 지표, 방법 2는 기각).
- 금리 구간 규칙 변경(여전히 DSCR 기준).
- 대출 후 모니터링(조기경보·회수)에 셀러 지표를 쓰는 것. 후속 과제.

**반목표(기술적으로 돌아가도 실패인 것)**
- LLM이 판정·대체 여부를 정한다(ADR-003 위반).
- 셀러 데이터에 동의하지 않았거나 자료가 없어서 불리해진다(값 없음 = 규칙 미적용 원칙 유지).
- v3로 난 기존 판정의 설명이 깨진다(v3 파일·근거 표시는 그대로 남는다).
- 대체 규칙으로 사람 확인 없이 자동 적격이 된다(탈락 대체는 반드시 검토로 끝난다, 4.3절).

## 3. 방법 비교 (사용자 선택: 방법 1)

| 방법 | 내용 | 장점 | 대가 | 결과 |
|---|---|---|---|---|
| 1. 정책 v4에 셀러 규칙 + 대체·한도 보강 | YAML에 셀러 knockout/refer, `substitutions`, 한도 보강 섹션. 엔진이 추적을 남김 | 판정이 바뀐 이유가 정책 파일·근거 화면에 그대로 드러남 | 엔진·규칙 검증·근거 화면을 모두 손봄 | **선택** |
| 2. 셀러 건전성 점수 하나 | 코드가 0~100점을 만들고 정책은 점수 기준만 | 엔진 변경 최소 | 점수 공식이 코드에 숨어 정책만으로 설명 불가, 대체는 결국 엔진 변경 필요 | 기각 |
| 3. 셀러 전용 정책 파일 분리 | 셀러 자료가 있으면 다른 규칙셋 | 엔진 거의 그대로 | 규칙 이중 관리, 비교 장면이 규칙셋 전환이 됨 | 기각 |

정책 영향 범위 결정(사용자 선택: **(나) 탈락도 대체 가능**): 보류(refer)뿐 아니라 R001(거래 이력 6개월 미만) 같은 탈락 규칙도 셀러 근거로 대체할 수 있다. 위험 통제는 4.3절의 결과 상한·한도 상한으로 한다(제 판단).

## 4. 설계

### 4.1 데이터: 셀러 성과 Mock API

- 동의 범위: `ConsentScope.MARKETPLACE = "marketplace"`(`src/shared/contracts.py`). 오케스트레이터 `CONSENT_SCOPES`에 추가(`src/orchestrator/journey/models.py:22`), 고지 라벨 `"marketplace": "마켓 판매 성과"`(`src/agents/notices.py:25`).
- 엔드포인트: `GET /marketplace/store`(Bearer 동의 토큰, 범위 `marketplace`). 응답은 새 계약 `MarketplaceStore`:

| 필드 | 뜻 |
|---|---|
| `as_of` | 기준일 |
| `platforms[]` | 플랫폼별 성과(아래) |
| `penalties[]` | `{platform, occurred_on, kind: "suspension"\|"warning"}` (자유 텍스트 사유 없음) |

플랫폼별(`StorePlatform`): `platform`, `opened_on`(입점일), `rating`(1.0~5.0, 소수 1자리), `review_count`, `orders_90d`, `gross_90d_krw`, `cancel_90d_krw`, `return_90d_krw`, `seller_grade`(플랫폼 등급 라벨, 표시용), `pending_settlement_krw`(기준일까지 주문분 중 기준일 이후 지급 예정 순액), `assigned_pending_krw`(그중 선정산 업체에 양도된 금액).

- 합성(`src/mockapis/sellerdata/marketplace.py`, 새 파일): 같은 `SyntheticCase`의 페르소나·아키타입·시드에서 **결정적으로** 만든다.
  - `orders_90d`·`gross_90d_krw`·`return_90d_krw`는 셀러 엔진의 `OrderDay` 기록(채널별 일 주문액·반품액, `channels.payouts(orders=...)`)을 기준일 직전 90일 합산해 쓴다. 그래서 계좌 정산 입금과 숫자가 맞는다.
  - `pending_settlement_krw`는 `payouts()`가 만든 지급 중 `paid_on > as_of`인 순액 합. ASSIGNED 아키타입은 양도분을 `assigned_pending_krw`로 표시.
  - 평점·리뷰·취소율·입점일·등급·제재는 아키타입 기본값 + 데모 페르소나별 덮어쓰기(아래 표).
- 아키타입 기본값(제 판단, 정상 셀러는 셀러 규칙에 걸리지 않게):

| 아키타입 | 가장 오래된 입점 | 평점 | 리뷰 수 | 취소율 | 제재 |
|---|---|---|---|---|---|
| HEALTHY_SEASONAL·TAX_MISMATCH | 업력과 같음(최소 24개월) | 4.5~4.9 | 300~3,000 | 1~3% | 없음 |
| STRESSED | 업력과 같음 | 4.2~4.5 | 200~1,500 | 3~6% | 경고 1건(정지 아님) |
| THIN_FILE | 24개월(개인 판매자로 시작해 업력 8개월 전에 이미 입점) | 4.6~4.9 | 150~800 | 1~3% | 없음 |
| ASSIGNED | 업력과 같음 | 4.4~4.7 | 300~1,500 | 2~4% | 없음 |

- 데모 셀러 덮어쓰기(제 판단; 사후 시나리오 셀러 dailylook-01·gearstore-06·linen-10·tangerine-15는 건드리지 않는다):

| 셀러 | 설정 | 의도한 v4 결과 |
|---|---|---|
| knitroom-04, leather-14 (THIN_FILE) | 기본값(입점 24개월, 평점 높음, 반품 낮음) | R001·R104 대체 → **탈락 → 검토**(S001·R107, 한도 상한 500만) |
| tint-17 (THIN_FILE) | 기본값(대체 조건 충족) | S001 조건은 맞지만 R002(DSCR 0.94)로 **탈락 유지** — 평판이 상환 능력을 덮지 않음 |
| streetfit-19 (THIN_FILE) | 평점 3.8 | R110 사유 추가, 대체 미달·R002로 탈락 유지 |
| petfood-07 (HEALTHY) | 기준일 40일 전 판매정지 1건 | 현금흐름은 적격이지만 **R003 탈락** |
| ceramic-08 (HEALTHY, 단일 플랫폼) | 최근 90일 반품률 18% | R107에 **R109** 사유 추가 |

  현재 v3 실측(기준일 2026-11-17, 신청 500만·6개월): knitroom-04 `ineligible`(R001·R104·R107, DSCR 1.50), leather-14 `ineligible`(R001·R104·R107, DSCR 1.29), tint-17 `ineligible`(R001·R002·R901·R104·R107, DSCR 0.94), streetfit-19 `ineligible`(R001·R002·R901·R104, DSCR 0.65), petfood-07 `eligible`, ceramic-08 `refer`(R107).

  기존 아키타입별 합성 계좌 데이터는 바꾸지 않는다. 예외는 8절 1번의 두 셀러다(사후 시나리오·평가 기준선 보호). 셀러 데이터는 별도 파일·별도 난수 스트림에서 만든다.

- 데이터 수집: `src/agents/data_assembly.py` `SOURCES`에 `"marketplace"`, `SOURCE_SCOPE["marketplace"] = "marketplace"`. 필수 아님(`REQUIRED` 그대로). 원천 Blob은 기존처럼 동의 ID별 컨테이너에 `marketplace.json`으로 저장되므로 ADR-009 삭제·증명 흐름에 자동 포함된다(테스트로 확인).

### 4.2 지표 (결정적 코드)

새 모듈 `src/agents/marketplace.py`가 `store_summary(store, as_of) -> dict`와 `features_of(summary) -> dict`를 제공한다(`pg_ledger.py`와 같은 모양). 자료가 없거나 동의가 없으면 모든 값이 `None`이고, 손상되면 `marketplace_unavailable` 플래그.

`CashFlowFeatures`에 추가할 선택 필드(기본 `None`, strict 유지):

| 필드 | 계산 |
|---|---|
| `mkt_tenure_months` | 가장 이른 `opened_on`부터 기준일까지 개월 수(내림) |
| `mkt_rating` | 플랫폼 평점을 `review_count` 가중 평균, 소수 2자리 |
| `mkt_review_count` | 리뷰 수 합 |
| `mkt_return_rate_90d` | `Σreturn_90d / Σgross_90d` (gross 0이면 None) |
| `mkt_cancel_rate_90d` | `Σcancel_90d / Σgross_90d` |
| `mkt_days_since_suspension` | 가장 최근 `suspension`부터 기준일까지 일수, 없으면 None |
| `mkt_pending_settlement_krw` | `Σ(pending_settlement − assigned_pending)`, 0 미만이면 0 |

`policy.rules.FeatureName`에 위 필드를 추가한다. 의미 분류 보정(`_conservative`)은 셀러 필드를 건드리지 않는다.

### 4.3 정책 v4 (`src/policy/rulesets/v4.yaml`, 기본 규칙셋)

v3 규칙은 모두 유지하고 아래를 더한다. 수치는 합성 분포에 맞춘 데모 값이다(ADR-003 v3 절과 같은 단서).

```yaml
version: "2026.11-v4"
knockouts:   # v3의 R001·R002 + 
  - {code: R003, message: "최근 90일 판매정지 이력", field: mkt_days_since_suspension, op: lt, threshold: 90}
refers:      # v3의 R102~R108 +
  - {code: R109, message: "최근 90일 반품률 과다", field: mkt_return_rate_90d, op: gt, threshold: 0.15}
  - {code: R110, message: "마켓 평점 낮음", field: mkt_rating, op: lt, threshold: 4.0}
substitutions:
  - code: S001
    message: "마켓 판매 이력으로 거래 이력 부족 대체"
    replaces: R001
    outcome: refer          # 탈락 대체는 검토까지만
    when:
      - {field: mkt_tenure_months, op: ge, threshold: 18}
      - {field: mkt_rating, op: ge, threshold: 4.5}
      - {field: mkt_review_count, op: ge, threshold: 100}
      - {field: mkt_return_rate_90d, op: le, threshold: 0.08}
  - code: S002
    message: "마켓 판매 이력으로 업력 부족 면제"
    replaces: R104
    outcome: pass
    when:
      - {field: mkt_tenure_months, op: ge, threshold: 18}
      - {field: mkt_rating, op: ge, threshold: 4.5}
limit_boost:
  pending_settlement_ratio: 0.8
  substituted_max_amount_krw: 5000000
```

엔진 동작(`src/policy/engine.py`):
1. 규칙 평가는 v3와 같다(값 없음 → `not_applied`).
2. **대체**: `substitutions`의 각 항목은 아래 조건을 모두 만족할 때 적용된다. 단, 대체할 수 없는 탈락이 하나라도 걸리면 어떤 대체도 적용하지 않는다(8절 3번).
   - `replaces` 규칙이 `hit`이다.
   - `when` 조건이 모두 관측값이 있고 참이다. 조건 중 하나라도 `None`이면 적용하지 않는다.

   적용되면 대상 규칙의 결과를 `substituted`로 바꾸고 사유에서 뺀다.
   - `outcome: refer`면 대체 코드(S001)를 검토 사유로 추가한다.
   - `outcome: pass`면 사유를 남기지 않는다.
3. 대체할 수 없는 규칙: R002(DSCR)·R003(판매정지)·R900·R901. 규칙 로더가 `replaces`에 knockout 코드 R002·R003과 엔진 예약 코드를 쓰면 거부한다(제 판단: 상환 능력과 플랫폼 제재는 셀러 평판으로 덮지 않는다).
4. **한도**: `inflow_cap = avg_monthly_inflow × inflow_multiplier`, `settlement_cap = mkt_pending_settlement × pending_settlement_ratio`(값 없으면 0). `before = min(requested, max(inflow_cap, settlement_cap), product_max)`. knockout이 하나라도 대체됐으면 `substituted_max_amount_krw`로 한 번 더 자른다. 10만 원 단위 절사·R900은 그대로. `LimitTrace`에 `settlement_cap_krw`, `substituted_cap_krw`를 추가한다.
5. **현금흐름만 판정**: 같은 규칙셋에서 `mkt_*` 필드를 모두 `None`으로 바꾸고 대체·한도 보강 없이 다시 평가한 결과를 `cash_only`(판정·한도·사유 코드)로 함께 돌려준다. `PolicyDecision`에 선택 필드 `cash_only: CashOnlyDecision | None = None`을 추가한다(옛 문서 호환).
6. `RuleCheck.kind`에 `"substitution"`, `result`에 `"substituted"`를 추가하고 `Explanation`에 대체 추적을 담는다.

`DEFAULT_RULES_PATH`는 v4로 바꾸고 v3는 이력으로 남긴다(`rules_for_version`이 둘 다 읽음). `RuleSet`의 `substitutions`·`limit_boost`는 선택(기본 빈 값)이라 v1~v3 파일이 그대로 로드된다. 오케스트레이터 `validation.py`의 상품 최대 한도 상수는 바뀌지 않는다.

### 4.4 에이전트

- 근거 카탈로그(`src/agents/evidence.py`·`underwriting.py`): `M:mkt_tenure_months`, `M:mkt_rating`, `M:mkt_review_count`, `M:mkt_return_rate_90d`, `M:mkt_cancel_rate_90d`, `M:mkt_days_since_suspension`, `M:mkt_pending_settlement_krw`, 그리고 정책 근거 `P:R003`·`P:R109`·`P:R110`·`P:S001`·`P:S002`(기준값 포함).
- 분석가 지시문: 셀러 지표를 근거로 인용할 수 있고, 대체가 적용된 건이면 심사역 확인 사항(`officer_checks`)에 대체 근거를 1개 이상 단다. 승인·거절을 지시하지 않는 기존 규칙과 "ineligible이면 decline/refer만" 규칙은 그대로(엔진이 대체로 refer를 내면 그 결과를 따른다).
- 검토자·검증 코드: 새 근거 ID를 허용 목록에 추가. 문장에 리뷰 원문이나 판매정지 사유 원문을 쓰지 않는다(수치 근거만).

### 4.5 콘솔

- 상세 `현금흐름` 탭 옆에 **`마켓 셀러`** 탭: 플랫폼별 표(입점일·평점·리뷰 수·90일 주문·반품률·취소율·등급·정산 예정금), 제재 목록, 동의 없음/자료 없음 안내.
- `정책 판정` 탭: (1) "현금흐름만 볼 때 → 셀러 데이터 반영" 비교 한 줄(판정·한도), (2) 규칙 표에 대체된 규칙을 `대체됨(S001)`으로 표시, (3) 한도 산식에 정산 예정금 상한·대체 상한 줄 추가.
- 범위 라벨 `marketplace` → "마켓 판매 성과". 새 한국어 msgid는 모두 EN 로케일 항목을 단다.
- 심사역 챗봇(`officer_chat.py`)의 규칙 원문·추적 스냅샷에 대체·한도 보강이 포함되게 한다.

### 4.6 평가·문서

- `src/evals/suites/underwriting.py`의 아키타입별 기대 판정을 v4 기준으로 갱신하고, THIN_FILE 대체(탈락→검토)와 판매정지 탈락 케이스를 추가한다. 실제 Azure 평가(`ops eval`) 실행은 사용자 승인 후.
- 문서: ADR-003에 "2026-11-17 개정: 정책 v4" 절(대체 원칙·대체 불가 규칙·임계값 근거·데모 판정 변화·배포 순서), `docs/agents.md`(셀러 지표), `docs/limitations.md`(합성 데이터 기반 임계값, 평점·리뷰의 조작 가능성·공정성 한계).
- 배포 순서(옛 특성 호환): 정책 → mockapi → 오케스트레이터 → 콘솔. 배포는 사용자 승인 후.

## 5. 제약

- ADR-003: 판정은 결정적 엔진만. LLM은 인용·권고만.
- ADR-009: 셀러 원천 데이터도 동의 만료·철회 시 삭제·증명. 판단 기록에는 파생 지표와 근거 ID만.
- `CashFlowFeatures`는 strict. 새 필드는 모두 선택·기본 `None`.
- 기존 v1~v3 판정 문서의 표시·근거가 깨지면 안 된다.
- 합성 데이터는 결정적이어야 한다(같은 시드 → 같은 셀러 데이터). 기존 계좌·정산 합성 결과는 바뀌지 않는다.

## 6. 빌더에게 맡기는 것

- `StorePlatform` 등급 라벨 문구, 리뷰 수·평점 난수 분포의 세부, 콘솔 탭 레이아웃 세부.
- 엔진 내부 자료구조(대체 추적 dataclass 이름 등). 단 4.3절의 동작과 필드 이름은 지킨다.
- 테스트 배치(기존 테스트 파일에 추가 우선).

## 7. 제가 내린 판단(검토 부탁)

1. 탈락 대체(S001)는 **검토(refer)까지만** — 사람 확인 없이 자동 적격이 되지 않게.
2. 대체된 건 한도 상한 500만 원.
3. R002(DSCR)·R003(판매정지)는 대체 불가.
4. 정산 예정금 한도 보강 비율 0.8, 선정산 양도분 제외.
5. 데모 셀러 결과 배치(4.1절 표): knitroom-04·leather-14 대체, tint-17·streetfit-19 탈락 유지(DSCR), petfood-07 판매정지, ceramic-08 반품 과다.
6. 셀러 데이터 동의 없음/자료 없음은 불이익 없음(규칙 미적용).
7. 8절의 리뷰 반영 결정 전부.

## 8. 리뷰 반영 결정 (제 판단)

1. **숫자 일치가 우선.** 셀러 성과 데이터는 계좌·정산과 숫자가 맞아야 한다. 그래서 덮어쓰기가 필요한 데모 셀러 두 곳만 계좌 합성에 셀러 훅을 건다(셀러 ID 기준, `demo.py`). 나머지 셀러와 아키타입 페르소나(평가 케이스)는 그대로다.
   - ceramic-08: 기존 반품률 훅(`return_rate`)으로 기준일 직전 90일 반품률을 약 18%로 올린다. 정산 입금도 그만큼 줄어든다.
   - petfood-07: 판매정지 기간(기준일 40일 전부터 7일간) 동안 해당 플랫폼 주문액을 0으로 만든다.
   - 두 셀러의 v3 지표는 조금 바뀔 수 있다. 바뀐 v3·v4 판정은 ADR-003 v4 절에 실측으로 적는다.
2. **확인 사항 근거.** `M:mkt_*`, `P:R003`, `P:R109`, `P:R110`, `P:S001`, `P:S002`를 심사역 확인 사항의 유효 근거로 허용한다(정산 근거와 같은 급).
   - 정산 자료가 없어도 셀러 근거만 있으면 확인 사항을 쓸 수 있다.
   - "대체 건이면 S 근거 1개 이상"은 지시문 요구로만 두고, 검증 실패로 처리하지 않는다(재검토 반복 방지).
3. **대체할 수 없는 탈락이 있으면 대체를 적용하지 않는다.** R002·R003·R900·R901 중 하나라도 걸리면 대체 결과를 판정에 반영하지 않는다.
   - 사유 코드는 원래대로(R001·R104 포함) 남긴다.
   - 추적에는 대체 항목을 `not_applied`로, 막은 규칙과 함께 남긴다. 콘솔은 "조건 충족, R002로 미적용"을 표시한다.
   - 한도 계산 순서: 대체 → 한도(대체 상한 포함) → R900.
4. **삭제 후 보존 범위(ADR-009).** 신청 문서에는 `pg_ledger`처럼 `marketplace` 요약을 저장한다.
   - 보존하는 것: 플랫폼별 숫자 행과 제재의 날짜·종류·플랫폼.
   - 제재에는 자유 텍스트 사유가 없다(`kind`만 쓰며 `reason` 필드는 두지 않는다). 원천 `marketplace.json`만 삭제 대상이다.
5. **배포 순서는 정책 → mockapi → 오케스트레이터 → 콘솔로 유지한다.** 대신 호환 규칙을 둔다.
   - 특성에 `mkt_*` 값이 하나도 없으면 정책 응답에 `cash_only`를 넣지 않는다(키 생략). 대체도 일어나지 않으므로 응답이 v3와 같은 모양이다(버전 문자열만 v4).
   - `ReasonCode.code` 패턴은 `^[RS]\d{3}$`로 넓힌다.
   - 콘솔과 오케스트레이터는 `cash_only` 키 없음과 S 코드를 모두 받아들여야 한다.
6. **S002로 인한 적격은 의도한 것이다.** 모든 신청은 판정과 관계없이 `awaiting_officer`에서 심사역이 결정한다(자동 승인 없음). 그래서 S002 적격은 "검토 사유 없음"일 뿐이다. 대체 상한은 탈락을 대체한 경우에만 적용한다.
