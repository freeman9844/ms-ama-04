# P9 고객 신청 채널·재생 일시정지·심사 화면 개선·셀러 데이터·사후 예측 현실화 설계

상태: 승인(2026-11-04), 개정 1(2026-11-04: ⑦ 셀러 데이터 현실화, ⑧ 신청 상세 탭, ⑨ 사후 모니터링 예측 추가), 개정 2(2026-11-04: 프로토타입 구현 편차 15장). 앞 단계: P8 소액 대출·온라인 셀러(병합 완료), 심사역 정책 예외 승인(`6fa885c`).

## 1. 목표

데모를 "고객이 직접 신청하고, 심사역이 그래프로 판단하는" 흐름으로 다듬는다.

1. 고객용 신청 페이지를 만든다. 고객은 데이터 동의 3종을 고르고 금액·기간을 넣어 신청한다. 신청은 실제 심사 프로세스를 탄다.
2. 사후 13주 재생 중 재조정 상담 요청이 생기면 재생을 멈추고, 심사역 결정 뒤 이어서 재생할지 묻는다.
3. 신청 상세의 에이전트 평가를 그래프 중심으로 바꿔 심사역이 한눈에 판단하게 한다.
4. 심사역 결정에서 조건부 승인을 뺀다.
5. 운영 지표 화면을 넓힌다.
6. 대출 기간 상한을 12개월로 낮춘다.
7. 데모 신청인과 금융 mock 데이터를 실제 마켓플레이스 셀러의 통장·정산·세금 흐름처럼 만든다(10장).
8. 신청 상세 화면을 탭으로 나눠 직관적으로 본다(11장).
9. 사후 모니터링이 정산 달력으로 기대 입금을 예측하고 4주 잔고를 전망한다. 사후 데이터도 같은 셀러 엔진으로 만든다(12장).

범위 밖: 실제 고객 인증(Entra External ID 등)과 인터넷 공개, 재조정 승인에 따른 상환 조건 변경·차주 통보, 에이전트 권고 값(`conditional`) 제거, 거래 분류 규칙(`classify-v1`) 변경, 부하 신청인 ID(`load-…`) 변경, 과거 기록 문서 수정.

## 2. 결정 요약

| 항목 | 결정 |
|---|---|
| 고객 페이지 위치 | 콘솔 앱 안 `/apply` 경로. 심사 메뉴가 없는 고객용 레이아웃. 접근은 콘솔과 같은 IP 허용 목록 |
| 동의 | 은행 거래·세금 신고·PG 매출 3개를 모두 체크해야 제출. 화면(정적 JS)과 서버 둘 다 막는다 |
| 금액 | 신청 50만~1,000만 원. 정책 상품 한도 1,000만 원(규칙 v2). 예외 승인 상한도 1,000만 원 |
| 기간 | 모든 경로에서 1~12개월. 기본 12개월 |
| 재생 일시정지 | 재조정 요청이 생긴 주차에서 멈춤 → 심사역 결정 → "이어서 재생 / 여기서 종료" 선택 |
| Service Bus 모드 | 13주 일괄 발행 대신 주차를 하나씩 이어서 발행(멈출 수 있게) |
| 에이전트 평가 | 패널 7종(한눈 요약, 지표 신호등, 상환 여력, 한도 산정, 지출 구성, 주장·근거, 강점·위험) |
| 조건부 승인 | 심사역 결정에서만 제거. 과거 기록 표시는 유지. 에이전트 권고 `conditional`은 "승인 권고(조건 제안)"로 표시 |
| 운영 지표 | "최근 신청 100건(전체 채널)" 보기 추가, 패널 6종 추가 |
| 셀러 데이터 | 채널·일정 기반 생성기(`src/mockapis/sellerdata/`). `generate_case`·`generate_postloan`의 입출력은 유지 |
| 데모 신청인 ID | `seller-dailylook-01` 등 셀러형 ID. 화면은 "상호 · 입점 채널 · 업종" |
| 판정 불변 | 시나리오별 정책 판정·사유 코드, 조기경보 대상(주인공 1명)은 지금과 같다 |
| 재측정 | 데이터가 바뀌므로 Azure 배포 뒤 평가·부하를 다시 측정한다 |
| 신청 상세 | 제목·타임라인·신청인 카드는 그대로, 나머지는 탭 7개 |
| 사후 예측 | 심사 시점 정산 프로필 × 영업일 달력 × 계절 지수로 주차별 기대 입금. 탐지는 실제÷기대 |
| 잔고 전망 | 4주 전망에서 잔고가 0 아래로 가면 새 트리거 `projected_shortfall`. 근거 ID `F:` |
| 사후 데이터 | 같은 셀러 엔진. STABLE은 오탐 미끼(행사·명절·부가세) 포함, STACKING은 단계적 악화 |

## 3. 고객 신청 페이지

### 3.1 화면

- `GET /apply`: 신청 폼. `apply_base.html` 레이아웃(고객용 머리말, 심사 메뉴 없음).
  - 데모 사업자 선택: `DEMO_APPLICANTS` 5명. 라벨은 "상호 · 입점 채널 · 업종"(예: `성수데일리룩 · 스마트스토어·쿠팡·자사몰 · 패션의류`). 라벨 목록은 `shared.sellers.DEMO_SELLERS`(10장)에서 읽는다.
  - 동의 3개 체크박스(`consent_account`, `consent_tax`, `consent_pg`). 항목마다 받는 데이터, 목적(`CONSENT_PURPOSE`), 보유기간 90일(`CONSENT_TTL_DAYS`)을 적는다.
  - 신청 금액(원, 500,000~10,000,000), 기간(개월, 1~12, 기본 12).
  - 제출 버튼은 3개가 모두 체크될 때만 켜진다(`static/apply.js`, CSP상 인라인 금지).
- `POST /apply`: 서버 검증 후 `applications-start` 큐에 `StartRequest(source="customer")`를 보내고 `/apply/{application_id}`로 303.
  - 동의가 하나라도 없으면 422, 메시지 "데이터 3종에 모두 동의해야 신청할 수 있습니다."(`consent_required`).
  - 금액·기간 범위 밖이면 422.
- `GET /apply/{application_id}`: 고객 진행 현황. 내부 근거(지표·정책 코드·에이전트 서술)는 보이지 않는다.

| 신청 상태 | 고객 표시 |
|---|---|
| 문서 없음(큐 처리 전) | 접수 대기(10초마다 새로 고침) |
| `received`~`assessed`, `data_incomplete` | 심사 중 |
| `awaiting_officer`, `escalated` | 심사역 검토 중 |
| `approved`, `conditionally_approved` | 승인(승인 금액 표시) |
| `declined` | 거절 |
| `withdrawn` | 동의 철회로 종료 |
| `closed_no_decision`, `failed` | 처리 종료(문의 안내) |

### 3.2 권한

고객 페이지는 콘솔 역할을 요구하지 않는다. 주체는 `deps.demo_principal`(데모 모드) 또는 Easy Auth 헤더의 로그인 사용자이며 CSRF는 기존 방식(사용자 ID 키)을 쓴다. 인터넷 공개 시 고객 인증은 범위 밖이며 `docs/limitations.md`에 적는다.

### 3.3 계약·정책

- `StartRequest.source`에 `"customer"`를 더한다. `requested_amount_krw`에 `le=10_000_000`을 둔다.
- 콘솔 신청 목록(`_rows.html`)은 `source == "customer"`면 "고객 신청" 배지를 붙인다.
- 정책 규칙: `src/policy/rulesets/v2.yaml`을 새로 만든다(v1 복사, `version: "2026.11-v2"`, `max_amount_krw: 10000000`, 머리 주석 "50만~1,000만 원"). `DEFAULT_RULES_PATH`를 v2로 바꾸고 v1은 이력으로 남긴다.
- `OVERRIDE_MAX_AMOUNT_KRW = 10_000_000`. 콘솔 메시지와 `detail.html` 안내문은 "1,000만 원"으로 고친다. 기존 테스트(`load_rules()` 상품 최대와 일치)가 값을 지킨다.

## 4. 재조정 요청 시 재생 일시정지

### 4.1 상태

`loan.monitoring.status`에 값을 더한다.

| 상태 | 뜻 | 콘솔 라벨 |
|---|---|---|
| `replaying` | 재생 중(기존) | 재생 중 |
| `paused` | 재조정 요청이 심사역 결정을 기다림 | 재조정 대기 |
| `awaiting_resume` | 재조정 결정 완료, 재개 여부 선택 대기 | 재개 확인 대기 |
| `stopped` | 심사역이 남은 재생을 종료 | 재생 종료 |
| `completed`, `failed` | 기존 | 기존 |

`monitoring`에 `paused_week`, `pause_alert_id`, `resumed_at`(목록), `stopped_at`을 더한다.

### 4.2 흐름

1. 주차 처리 후 경보가 생기면 추심(`run_collections`)을 돌린다. 추심이 재조정 요청을 만들면 `status=paused`, `paused_week=주차`, `pause_alert_id=경보`로 두고 다음 주차로 가지 않는다. 재조정 요청이 없으면 다음 주차로 간다.
2. 심사역이 재조정을 결정하면(`handle_restructure_decision`) 기존 기록에 더해, 대출의 `pause_alert_id`가 이 경보이고 상태가 `paused`면 `awaiting_resume`으로 바꾼다.
3. 대출 화면에 카드가 뜬다: "재조정 결정(승인/거절)이 기록되었습니다. 남은 {13 − paused_week}주 재생을 이어갈까요?" 버튼 [이어서 재생] [여기서 종료]. 결정 직후 안내(`loan_sent.html`)에도 "결정이 반영되면 대출 화면에서 재생을 이어갈지 선택하세요"를 적는다.
4. 버튼은 `POST /loans/{id}/replay/resume`, `POST /loans/{id}/replay/stop`(심사역 권한, CSRF). 콘솔은 `monitoring-replay` 큐에 `ReplayRequest(action="resume"|"stop", replay_id=…)`를 보낸다.
5. 오케스트레이터는 `status == awaiting_resume`이고 `replay_id`가 같을 때만 처리한다.
   - resume: `status=replaying`, `started_at=now`(30분 가드 재시작), `resumed_at`에 추가, `paused_week+1`주부터 이어서 처리. 다시 재조정 요청이 나오면 또 멈춘다.
   - stop: `status=stopped`, `stopped_at=now`.
6. `paused`·`awaiting_resume` 동안 "사후 13주 재생(다시)" 버튼은 꺼진다(새 재생이 진행 중 재생을 덮지 않게). `replay_in_progress`는 그대로 `replaying`만 본다. 재조정 결정 폼은 `paused`에서 열린다(지금도 `replaying`일 때만 막음).

### 4.3 계약

`ReplayRequest`에 `action: Literal["start", "resume", "stop"] = "start"`와 `replay_id: str | None`을 더한다. resume·stop은 `replay_id`가 필수(모델 검증).

### 4.4 Service Bus 모드

- `handle_replay`는 1주차만 발행한다.
- `process_week`가 끝나면 경보가 없을 때 다음 주차를 발행한다. 경보가 있으면 `alert.raised`만 발행한다.
- `run_collections`(alert.raised)가 끝나면 재조정 요청이 없을 때 다음 주차를 발행하고, 있으면 멈춘다.
- resume은 `paused_week+1`주를 발행한다.
- 인라인 모드와 공용 함수 `_after_week(deps, loan_id, replay_id, week, alert_id) -> "next" | "paused" | "done"`으로 같은 판단을 쓴다. 메시지 ID 형식은 기존(`flow.updated:{loan}:{replay}:{week}`)을 유지한다.

## 5. 에이전트 평가 화면

`detail.html`의 "에이전트 평가" 영역을 아래 패널로 바꾼다. 패널은 11장의 탭(요약·현금흐름·에이전트 의견)에 나눠 놓는다. 모두 서버 SVG(`_charts.html` 매크로, `src/console/charts.py`)로 그리고 계산은 `dashboard.py`의 순수 함수(`assessment_panels(view)`)에 둔다. 에이전트·모델 호출은 바뀌지 않는다.

| 패널 | 데이터 | 표시 |
|---|---|---|
| 한눈 요약 | `policy.decision`, `assessment.recommendation`, `assessment.review`, 신청액·`policy.max_amount_krw` | KPI 3개 + 게이지(한도 ÷ 신청액). 권고 `conditional`은 "승인 권고(조건 제안)" |
| 지표 신호등 | `features` 6개(DSCR, 현금 완충일수, 매출 변동성, 세금·계좌 일치율, 업력, 거래 이력) | 기준선 막대 + 초록(통과)·주황(refer 기준 위반)·빨강(knockout 위반) + 쉬운 설명 한 줄 |
| 상환 여력 | `underwriting.monthly`, `signals.new_installment_krw` | 월별 (매출 − 운영비) 막대와 (그달 기존 상환 + 이번 대출 월 상환액) 선 |
| 한도 산정 | 신청액, `avg_monthly_inflow_krw × 1.5`, 상품 한도 | 가로 막대 3개, 가장 작은 값(= 정책 한도, 10만 원 내림) 강조 |
| 지출 구성 | `underwriting.expense_breakdown`(신규) | 도넛: 거래처·물류, 임대료, 인건비, 세금·4대보험, 기타 운영비, 기존 상환, 대표자 인출, 미분류, 남는 돈 |
| 주장·근거 | `v.claims` | 근거가 모두 있으면 ✓, 하나라도 없으면 ⚠ 아이콘 |
| 강점·위험 | 지표 신호등 결과, `policy.reason_codes`, `flags` | 두 열 목록(강점: 기준을 넉넉히 넘는 지표, 위험: 위반 지표·사유 코드·주의 플래그) |

- 신호등 기준값은 콘솔에 상수 표로 둔다. 테스트가 `load_rules()`의 knockout·refer 임계값과 같은지 확인한다. "넉넉히 넘는" 기준(강점)은 DSCR ≥ 1.5, 완충일수 ≥ 30일, 일치율 ≥ 0.9, 변동성 ≤ 0.3, 업력 ≥ 24개월, 이력 ≥ 12개월이다.
- 쉬운 설명 예: "DSCR 1.8 — 벌어들이는 돈이 매달 갚을 돈의 1.8배입니다(기준 1.0 이상)."
- `expense_breakdown`: 완결 월 기준 월평균 출금(원, 양수)을 위 8구분으로 나눈 값이다. 규칙 분류(세금·건강보험·임대료·급여·상환·대표자 이체)를 먼저 쓰고, 규칙 미분류 출금은 의미 분류 라벨(`supplier`→거래처·물류, `other_operating`→기타 운영비, `tax_or_insurance`→세금·4대보험, `payroll`·`rent`·`owner_transfer`·상환 라벨→해당 구분)로 나눈다. 라벨이 없거나 `cannot_tell`이면 미분류다. 이를 위해 `SemanticClassification`에 `labels: dict[txn_id, label]`(출금만)을 더하고 `compute_underwriting(..., semantic_labels=...)`가 계산해 `Underwriting.expense_breakdown`으로 낸다. 오케스트레이터는 `underwriting` 문서에 저장한다. DSCR·완충일수 계산은 바뀌지 않는다. 이 필드가 없는 과거 문서는 `monthly`의 3구분(운영비·상환·대표자 인출)과 남는 돈으로 그린다.
- 기존 월별 현금흐름·정책 마커·반사실 차트는 유지한다. 원래 텍스트(요약·제안 구조·검토 결과)는 '에이전트 의견' 탭에 둔다.

## 6. 조건부 승인 제거

- 결정 폼에서 "조건부 승인" 라디오와 조건 입력란을 뺀다. 이의 재심 폼도 같다.
- `validate_officer_decision`은 새 `conditional` 결정을 `conditional_retired`로 거부한다(콘솔 메시지 "조건부 승인은 더 이상 쓰지 않습니다. 승인 또는 거절을 고르세요."). 오케스트레이터도 같은 함수로 재검증한다.
- `OfficerDecision.decision`의 `Literal`과 `Status.CONDITIONALLY_APPROVED`, 상태 라벨은 과거 문서를 읽기 위해 남긴다.
- 에이전트 권고 `conditional`은 그대로 둔다. 화면 라벨만 "승인 권고(조건 제안)".

## 7. 대출 기간 12개월

- `StartRequest.requested_tenor_months`, `CashFlowFeatures.requested_tenor_months`, `ProposedStructure.tenor_months`의 상한을 60 → 12로 바꾼다.
- `agents/underwriting.py`의 구조 검사(`1 <= tenor <= 60`)를 12로 바꾸고 분석가 지시문에 "기간은 12개월 이하"를 넣는다.
- 콘솔 새 신청 폼과 고객 폼의 기간 범위를 1~12로 둔다. 미리보기 픽스처의 24개월은 12개월로 바꾼다.
- 부하(`opsjob TENOR_MONTHS=12`), 평가(`REQUESTED_TENOR_MONTHS=12`), 스모크(`E2E_TENOR_MONTHS=12`)는 이미 12개월이다. 상품 한도 상향도 500만 원 이하 신청의 한도 계산(`min(신청액, 입금×1.5, 상품 한도)`)을 바꾸지 않는다. 재측정은 기간·한도 때문이 아니라 10장의 데이터 변경 때문에 한다.

## 8. 운영 지표 확장

- 보기 범위: 측정 실행 목록 맨 앞에 "최근 신청 100건(전체 채널)"(`run=recent`)을 더한다. `CosmosOps.recent(limit)`가 `received_at` 내림차순으로 읽어 `project()`로 투영한다.
- `project()`에 `policy_override`, `requested_amount_krw`, `sla_escalated`를 더한다(PII 아님).
- 새 계산은 `src/opsmetrics/insights.py`의 순수 함수로 두고 `summarize()`에 넣는다.

| 패널 | 내용 | 차트 |
|---|---|---|
| 결정 퍼널 | 정책 판정 분포 → 에이전트 권고 분포 → 심사역 결정 분포. 권고·결정 일치율, 정책 예외 승인 건수·비율 | 누적 막대 3줄 + KPI |
| 단계별 처리 시간 | `steps[].duration_ms`의 단계별 p50·p95 | 가로 막대 |
| AI 비용 | 모델별·단계별 비용과 토큰(`cost.py` 결과 분해) | 도넛 + 가로 막대 |
| 사유 코드 빈도 | `policy.reason_codes[].code` | 가로 막대 |
| 플래그 빈도 | `flags`(폴백·서킷·검토 미통과 등) | 가로 막대 |
| 시간대별 처리량 | 10분 단위 접수 수와 심사역 대기 도달 수 | 세로 막대 |
| SLA 현황 | 심사역 대기 중 건수, SLA 초과 건수 | KPI |

공정성·SLO·단계별 결과 패널은 그대로 둔다. "최근 신청" 보기에서 공정성은 표본 부족이면 기존 규칙대로 회색 처리한다.

## 10. 셀러 데이터 현실화

### 10.1 신청인

공용 카탈로그 `src/shared/sellers.py`의 `DEMO_SELLERS`(applicant_id, 상호, 입점 채널, 업종)를 콘솔 라벨과 mock 데이터가 함께 쓴다. 시드(101~105)와 시나리오는 그대로다.

| 새 ID | 이전 ID | 상호 · 채널 · 업종 | 시나리오 |
|---|---|---|---|
| `seller-dailylook-01` | demo-healthy-001 | 성수데일리룩 · 스마트스토어·쿠팡·자사몰 · 패션의류 | 정상, 사후 대출 쌓기 주인공 |
| `seller-mangwon-02` | demo-mismatch-002 | 망원식탁마켓 · 쿠팡·스마트스토어·11번가 · 식품 | 세금 불일치 |
| `seller-glowlab-03` | demo-stress-003 | 해운대뷰티랩 · 스마트스토어·쿠팡·자사몰 · 뷰티 | 스트레스 |
| `seller-knitroom-04` | demo-thin-004 | 판교손뜨개공방 · 스마트스토어·자사몰 · 핸드메이드 | 짧은 이력 |
| `seller-candle-05` | demo-injection-005 | 연남캔들웍스 · 스마트스토어·자사몰 · 리빙 | 프롬프트 주입(전용 페르소나) |

- 부하 신청인 ID(`load-{arch}-NN`)는 유지한다. `load_persona(industry, region, index)`가 업종별 이름 풀에서 상호·대표자·직원 이름을 겹치지 않게 고른다.
- 사업자등록번호는 개인사업자 형식(가운데 두 자리 01~79)이고 검증 숫자가 맞다(가중치 1,3,7,1,3,7,1,3,5, 아홉째 자리×5의 십의 자리를 더함).
- 이전 ID는 현재 문서(`docs/demo/demo-script.md`, `fallback.md`, `limitations.md`)만 고친다. 과거 계획·스펙·배포 기록은 그대로 둔다.

### 10.2 생성기 `src/mockapis/sellerdata/`

| 모듈 | 내용 |
|---|---|
| `calendar.py` | 영업일(주말·공휴일 제외). 고정 공휴일 + 2024~2028 설·추석·부처님오신날·대체공휴일 표(구현 때 공식 달력으로 확인). 표 밖 연도는 고정 공휴일만 |
| `channels.py` | 채널별 수수료·반품률·정산 일정(아래 표) |
| `seasonality.py` | 업종별 월 지수(패션 봄·가을, 식품 설·추석, 뷰티 5·11월), 전 업종 11월 행사(×약 1.3), 요일 효과 |
| `costs.py` | 업종 원가율 매입, 택배비, 광고비, 카드대금, 솔루션 수수료, 임대료, 급여, 원천세, 4대보험, 부가세·종소세, 원리금, 대표자 인출 |
| `engine.py` | 일별 주문 → 채널 정산 → 비용 일정 → 잔고. 시나리오 레버 적용 |

| 채널 | 적요 | 코드 | 수수료(VAT 포함, 근사) | 정산 |
|---|---|---|---|---|
| 스마트스토어 | 네이버파이낸셜 | PG_SETTLE | 약 6.6% | 구매확정(5~9일) + 1영업일 |
| 쿠팡 | 쿠팡(주) | PG_SETTLE | 카테고리별 약 8~12% | 주정산: 일요일 마감 후 15영업일에 70%, 익익월 첫 영업일에 30% |
| 11번가 | 11번가 | PG_SETTLE | 약 12% | 월 2회(1~15일분 → 25일, 16일~말일분 → 다음 달 10일) |
| 자사몰 | 토스페이먼츠 | CARD_SETTLE | 약 3.4% | D+2영업일 |

- 비용 적요는 `classify-v1` 키워드를 지킨다: "임대료 {지역}", "급여 {이름}", "국민건강보험공단", "기업은행 대출상환", "{대표자} 개인이체". 원천세(10일)·부가세(1·7월 25일 확정, 4·10월 25일 예정고지)·종소세(5월 31일)는 TAX 코드이고 영업일로 미룬다. 국민연금공단·근로복지공단·택배·광고·카드대금·솔루션 수수료는 규칙 미분류로 남아 의미 분류기로 간다.
- 금액은 원 단위까지 불규칙하고, 거래는 한 달 약 70건이다.
- 시나리오 레버: TAX_MISMATCH는 신고 매출 = 관측 × 0.6. STRESSED는 기준일 60일 전부터 매출 × 0.6, 대부·P2P 차입, 급여일 20일. THIN은 이력 4개월·업력 8개월. INJECTION은 토스페이먼츠 정산 1건 적요에 `INJECTION_TEXT`.
- `generate_case`·`generate_postloan`의 입출력은 유지한다. `SyntheticCase`가 페르소나를 들고 다닌다(사후 데이터가 `_PERSONAS[case.archetype]`로 다시 찾던 버그 수정).

### 10.3 판정 불변

시나리오별 정책 판정·사유 코드·금리 구간은 지금과 같아야 한다. 테스트가 표로 고정한다. 규칙 v1 기준: 녹아웃 R001(이력 6개월 미만)·R002(DSCR 1.0 미만), 회부 R101(세금 일치 0.7 미만)·R102(변동성 0.5 초과)·R103(버퍼 10일 미만)·R104(업력 12개월 미만), 금리 A 2.0 이상·B 1.5·C 1.2·D 1.0.

### 10.4 지출 구성 `expense_breakdown`

- `SemanticClassification`에 출금 거래별 의미 라벨 `labels: dict[txn_id, label]`을 더한다.
- `compute_underwriting(..., semantic_labels=...)`가 완결 월 평균으로 8개 그룹을 낸다: 거래처·물류, 임대료, 인건비, 세금·4대보험, 기타 운영비, 기존 상환, 대표자 인출, 미분류. 콘솔은 여기에 "남는 돈"을 더해 보여준다.
- `activities.underwrite` 반환과 `doc["underwriting"]`에 실린다. 필드가 없는 과거 문서는 `monthly`로 폴백한다.
- 평가 데이터셋 `TRUTH_LABEL`에 새 합성 범주를 매핑한다: 택배 → supplier, 광고·카드대금·솔루션 → other_operating, 연금·고용산재 → tax_or_insurance.

## 11. 신청 상세 탭

- 그대로: 제목, 타임라인 카드, 신청인 카드.
- 탭 7개: 요약(판정·강점·위험·한도) / 현금흐름(월별 차트·신호등·상환 여력·지출 구성) / 정책 판정 / 에이전트 의견(주장·제안 구조·반사실·검토 단계) / 근거 거래 / 동의·데이터(동의·PII 안내) / 심사역 결정.
- 심사역이 결정할 수 있거나 오류로 다시 렌더링되면 결정 탭, 아니면 요약 탭이 열린다.
- 정적 `src/console/static/tabs.js`가 ARIA tablist와 `#tab-…` 해시를 맞춘다. JS가 없으면 모든 섹션이 펼쳐진다.
- 비어 있는 탭은 안내 문구를 보인다. 감사자 화면에는 "/decision"이 없어야 한다.

## 12. 사후 모니터링 예측·데이터

### 12.1 정산 프로필과 기대 입금

- 심사 때 입금 거래를 입금처별로 묶어 `settlement_profile`을 만들어 `doc["underwriting"]`에 저장한다. 입금처는 네이버파이낸셜, 쿠팡 주정산, 쿠팡 최종액정산, 11번가, 토스페이먼츠, 기타다. 항목은 채널별 월평균 입금과 정산 주기(매 영업일·주 1회·월 2회·월 1회), 회당 평균이다.
- 사후 주차별 기대 입금 = Σ 채널(그 주 정산 횟수 × 회당 평균) × 계절 지수. 정산 횟수는 10.2의 영업일 달력으로 센다. 그래서 공휴일이 낀 주는 기대가 낮다. 계절 지수는 자기 이력 월 지수를 쓰고, 없으면 업종 코호트 지수를 쓴다.
- 주차 행에 `expected_revenue`, `revenue_vs_expected`를 더한다. `inflow_drop`은 `revenue_vs_expected < 0.85`로 판단한다. 기존 `revenue_ratio`는 표시·근거용으로 남긴다.
- 프로필이 없는 과거 문서는 기존 방식(기준선 주평균)으로 폴백한다.

### 12.2 4주 잔고 전망

- 현재 잔고 + 다음 4주 기대 입금 − 예정 출금(임대료·급여·4대보험·부가세 일정·원리금·평균 운영비)을 일 단위로 계산한다. 예정 출금 일정은 심사 시점 거래의 반복 패턴(날짜·금액)에서 뽑는다.
- 전망 최저 잔고가 0 아래면 새 트리거 `projected_shortfall`을 낸다.
- 근거 ID `F:{주차}:projected_min_balance`, `F:{주차}:shortfall_date`를 근거 카탈로그에 더한다. 조기경보 에이전트 지시문·근거 검증·도구 설명에 `F:` 접두사를 더한다.

### 12.3 사후 데이터

- 같은 셀러 엔진으로 대출 시작일부터 13주를 만든다. 부하 셀러도 자기 페르소나를 쓴다.
- STABLE: 정상 영업. 오탐 미끼(11월 행사, 연말, 1월 부가세 확정 납부, 명절 정산 공백)를 넣는다. 경보가 나지 않아야 한다.
- STACKING(주인공): 시즌 재고 대량 선매입 → 판매 부진으로 정산 감소 → 광고비·카드대금 증가 → 대부·P2P 신규 차입(2주 간격) → 급여 지연·4대보험 미납(출금 없음) → 잔고 마이너스.

### 12.4 화면

대출 화면(`loan.html`)에 차트 2개를 더한다: 주차별 기대 입금 대비 실제(막대·선), 4주 잔고 전망(0원 기준선).

### 12.5 지키는 것

주인공은 지금처럼 경보 → 추심 → 재조정 요청(4장 일시정지 시연)으로 이어진다. 조기경보 평가(사전 감지 일수·오탐률)는 배포 뒤 다시 잰다.

## 13. 테스트

- 고객 신청: 동의 누락 422, 금액·기간 범위, 큐 메시지(`source="customer"`), 진행 현황 상태 매핑, 콘솔 목록 배지, `apply.js` 훅 존재.
- 정책 v2: 1,000만 원 신청의 한도 계산, `OVERRIDE_MAX_AMOUNT_KRW == load_rules().product.max_amount_krw`.
- 재생: 인라인 모드 pause → 결정 → awaiting_resume → resume(이어서 끝까지)·stop, 두 번째 재조정에서 다시 pause, 상태가 다르거나 `replay_id`가 다르면 무시. Service Bus 모드는 가짜 이벤트 버스로 주차 하나씩 발행·멈춤·재개를 확인한다. 콘솔 버튼·카드 표시와 재생 버튼 비활성화.
- 평가 화면: `assessment_panels` 단위 테스트(신호등 색, 한도 최솟값, 지출 구성 폴백, 권고 라벨), 상세 페이지 렌더링, 신호등 기준값 = 규칙 임계값.
- 조건부 제거: `conditional_retired` 거부(콘솔 422, 오케스트레이터 재검증), 폼에 선택지 없음, 과거 조건부 문서 표시.
- 기간: 13개월 신청 거부, 구조 검사 12개월.
- 운영 지표: `insights.py` 함수별 단위 테스트, `run=recent` 렌더링.
- 셀러 데이터: 영업일·공휴일, 채널별 정산일, 사업자번호 검증 숫자, 시드 결정성, 월 거래 수, 부하 셀러 이름 중복 없음, 시나리오별 판정 불변 표, 사후 페르소나 버그 수정, `TRUTH_LABEL`이 모든 합성 범주를 덮음, `expense_breakdown` 그룹 합계.
- 탭: 탭 7개 렌더링, 기본 탭 선택, 빈 탭 안내, 감사자 화면에 "/decision" 없음, `tabs.js` 존재.
- 사후 예측: 정산 프로필 추출, 공휴일 주의 기대 입금 감소, STABLE 13주 무경보(미끼 포함), STACKING 경보 주차, `projected_shortfall` 판단, `F:` 근거 검증, 과거 문서 폴백.

## 14. 배포·문서

- 배포: Policy(규칙 v2), Mock API(셀러 엔진), 콘솔, Functions(검증·재생·`expense_breakdown`·사후 예측), 평가 이미지(규칙 v2·셀러 엔진). 배포 창을 열고 닫은 뒤 `ops smoke`.
- 재측정: 평가(분류·조기경보 포함)와 부하를 다시 돌려 결과 문서를 갱신한다.
- 라이브 확인: `/apply`로 신청 1건 → 콘솔 목록 배지 → 심사역 대기까지. 주인공 `seller-dailylook-01` 대출로 재생 → 멈춤 → 결정 → 재개. 대출 화면의 기대 입금·잔고 전망 차트.
- 문서: `docs/architecture.md`(고객 채널, 재생 흐름, 셀러 엔진, 사후 예측), `docs/agents.md`(조건부 표시, 기간, `F:` 근거), ADR-003(한도 1,000만 원·조건부 제거), `docs/demo/demo-script.md`·`fallback.md`(새 신청인 ID), `docs/limitations.md`(고객 인증, 재조정 효과 미반영, 합성 정산 일정은 근사), `docs/runbook/deploy-history.md`.

## 15. 구현 편차(개정 2)

프로토타입을 끝까지 만들어 보며 위 설계와 달라진 점이다. 계획서는 이 내용을 따른다.

- 위치: 영업일·공휴일 달력은 `src/mockapis/sellerdata/calendar.py`가 아니라 `src/shared/krcalendar.py`에 둔다(에이전트 `settlement.py`도 같이 쓴다). `sellerdata`에는 `categories.py`(업종별 거래 범주)와 `personas.py`(시나리오별 페르소나)를 더 둔다. 평가 패널 함수 `assessment_panels`는 `dashboard.py`가 아니라 새 파일 `src/console/assessment.py`에 둔다.
- 데모 셀러는 5명이다(`src/shared/sellers.py` 카탈로그).
- 사후 예측(§12): 기대 입금은 지난 4주 달성률로 줄이거나 늘린다. 대표자 인출·원천세는 예정 출금, 변동 지출은 입금 대비 비율, 한도대출은 주 단위 상환으로 본다. 종합소득세는 예측에 넣지 않는다. 정산 일정에 명절 이동(예: 추석 월 변경)은 반영하지 않는다. 전망 필드에 `projected_min_date`를 더하고, 신호 종류는 `SignalKind.PROJECTED_SHORTFALL`이다. 상환일은 매달 15일(휴일이면 다음 영업일). 전망은 가장 최근 주차 행에만 넣는다.
- 지수 혼합: 월 지수는 업종 지수와 셀러 자체 지수를 50:50으로 섞는다. `revenue_vs_expected`는 최근 2주 합으로 잰다.
- 차트: 기대 대비 실제 입금 막대는 85% 미만이면 주황색이다. 미리보기의 사후 전망은 합성 값이다.
- 재생(§4): 재개 시점에 심사 동의가 이미 만료·철회됐으면 재생을 `stopped`로 끝내고 사유(`error`)를 남긴다. 시작 때와 같은 검사(`_underwriting_consent_ended`)를 쓴다. 재개가 Service Bus 모드에서 `replaying`에 멈춰 있지 않게 하기 위해서다.
- 미리보기(`consolepreview`)는 `mockapis`·`policy`·`agents`를 직접 불러 실제 셀러 엔진 데이터로 화면을 만든다. 거래 의미 라벨은 정답 라벨에서 옮긴다. 과거 조건부 문서 예시는 p04로 남긴다.
- 문서 범위 추가: `infra/modules/console-auth.bicep`의 역할 설명(승인/거절 결정)과 `docs/security/threat-model.md`(한도 1,000만 원·조건부 제거)도 고친다.

## 16. 최종 리뷰 반영(개정 3)

구현 뒤 전체 브랜치 리뷰에서 나온 세 가지를 고쳤다. 위 내용과 다르면 이 절을 따른다.

- 유입 감소 기준(§12.1·§15 대체): `revenue_vs_expected`는 최근 **3주** 합으로 재고 `inflow_drop`은 **0.75** 미만에서 낸다. 정산 프로필이 있으면 1주차(창이 다 차지 않음)에는 `inflow_drop`을 보지 않고, 변동성(`volatility_spike`)도 창이 다 찬 주차만으로 잰다. 0.85·2주는 기준일에 따라 정상(STABLE) 셀러에게 명절 주변 오탐을 냈다(§12.3 "정상 셀러는 경보 없음"이 우선). 32개 기준일 × 5명 측정에서 정상 경보 0/160, 주인공(STACKING)은 `buffer_drop` 1주·`new_debt` 2주 그대로이고 `inflow_drop` 첫 감지는 6~9주다. 화면의 주 단위 주황색(85%)은 주의 표시로 남긴다.
- 멈춤 경합(§4): 주차 처리가 '대기 중' 재조정 요청을 읽은 뒤 멈춤을 쓰기 전에 심사역 결정이 끝나면 대출이 `paused`에 남았다. 멈춤은 etag 조건부 교체로 쓰고, 결정 처리와 주차 처리 모두 자기 쓰기 뒤 `_promote_if_decided`로 "이 요청 때문에 멈췄고 결정이 끝났으면 `awaiting_resume`"을 확인한다.
- 재개 검사(§15 재생 항목 확장): 재개 때 심사 동의뿐 아니라 사후(모니터링) 동의도 본다. 만료·철회됐으면 `flow.json`이 지워졌으므로 `stopped`로 끝내고 사유를 남긴다.
