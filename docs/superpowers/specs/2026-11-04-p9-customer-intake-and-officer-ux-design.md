# P9 고객 신청 채널·재생 일시정지·심사 화면 개선 설계

상태: 승인(2026-11-04). 앞 단계: P8 소액 대출·온라인 셀러(병합 완료), 심사역 정책 예외 승인(`6fa885c`).

## 1. 목표

데모를 "고객이 직접 신청하고, 심사역이 그래프로 판단하는" 흐름으로 다듬는다.

1. 고객용 신청 페이지를 만든다. 고객은 데이터 동의 3종을 고르고 금액·기간을 넣어 신청한다. 신청은 실제 심사 프로세스를 탄다.
2. 사후 13주 재생 중 재조정 상담 요청이 생기면 재생을 멈추고, 심사역 결정 뒤 이어서 재생할지 묻는다.
3. 신청 상세의 에이전트 평가를 그래프 중심으로 바꿔 심사역이 한눈에 판단하게 한다.
4. 심사역 결정에서 조건부 승인을 뺀다.
5. 운영 지표 화면을 넓힌다.
6. 대출 기간 상한을 12개월로 낮춘다.

범위 밖: 실제 고객 인증(Entra External ID 등)과 인터넷 공개, 재조정 승인에 따른 상환 조건 변경·차주 통보, 에이전트 권고 값(`conditional`) 제거, 부하·평가 재측정(아래 7장 근거), 과거 기록 문서 수정.

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

## 3. 고객 신청 페이지

### 3.1 화면

- `GET /apply`: 신청 폼. `apply_base.html` 레이아웃(고객용 머리말, 심사 메뉴 없음).
  - 데모 사업자 선택: `DEMO_APPLICANTS` 5명. 라벨에 업종을 함께 보인다(예: `demo-healthy-001 · 패션의류`).
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

`detail.html`의 "에이전트 평가" 영역을 아래 패널로 바꾼다. 모두 서버 SVG(`_charts.html` 매크로, `src/console/charts.py`)로 그리고 계산은 `dashboard.py`의 순수 함수(`assessment_panels(view)`)에 둔다. 에이전트·모델 호출은 바뀌지 않는다.

| 패널 | 데이터 | 표시 |
|---|---|---|
| 한눈 요약 | `policy.decision`, `assessment.recommendation`, `assessment.review`, 신청액·`policy.max_amount_krw` | KPI 3개 + 게이지(한도 ÷ 신청액). 권고 `conditional`은 "승인 권고(조건 제안)" |
| 지표 신호등 | `features` 6개(DSCR, 현금 완충일수, 매출 변동성, 세금·계좌 일치율, 업력, 거래 이력) | 기준선 막대 + 초록(통과)·주황(refer 기준 위반)·빨강(knockout 위반) + 쉬운 설명 한 줄 |
| 상환 여력 | `underwriting.monthly`, `signals.new_installment_krw` | 월별 (매출 − 운영비) 막대와 (그달 기존 상환 + 이번 대출 월 상환액) 선 |
| 한도 산정 | 신청액, `avg_monthly_inflow_krw × 1.5`, 상품 한도 | 가로 막대 3개, 가장 작은 값(= 정책 한도, 10만 원 내림) 강조 |
| 지출 구성 | `underwriting.category_totals`(신규) | 도넛: 거래처, 임대료, 인건비, 기타 운영비, 기존 상환, 대표자 인출, 남는 돈 |
| 주장·근거 | `v.claims` | 근거가 모두 있으면 ✓, 하나라도 없으면 ⚠ 아이콘 |
| 강점·위험 | 지표 신호등 결과, `policy.reason_codes`, `flags` | 두 열 목록(강점: 기준을 넉넉히 넘는 지표, 위험: 위반 지표·사유 코드·주의 플래그) |

- 신호등 기준값은 콘솔에 상수 표로 둔다. 테스트가 `load_rules()`의 knockout·refer 임계값과 같은지 확인한다. "넉넉히 넘는" 기준(강점)은 DSCR ≥ 1.5, 완충일수 ≥ 30일, 일치율 ≥ 0.9, 변동성 ≤ 0.3, 업력 ≥ 24개월, 이력 ≥ 12개월이다.
- 쉬운 설명 예: "DSCR 1.8 — 벌어들이는 돈이 매달 갚을 돈의 1.8배입니다(기준 1.0 이상)."
- `category_totals`: `agents.features._compute`가 카테고리별 월평균 금액(원, 출금은 양수)을 `Underwriting.category_totals`로 내고 오케스트레이터가 `underwriting` 문서에 저장한다. 이 필드가 없는 과거 문서는 `monthly`의 3구분(운영비·상환·대표자 인출)과 남는 돈으로 그린다.
- 기존 월별 현금흐름·정책 마커·반사실 차트는 유지한다. 원래 텍스트(요약·제안 구조·검토 결과)는 패널 아래 "상세" 접기 안에 둔다.

## 6. 조건부 승인 제거

- 결정 폼에서 "조건부 승인" 라디오와 조건 입력란을 뺀다. 이의 재심 폼도 같다.
- `validate_officer_decision`은 새 `conditional` 결정을 `conditional_retired`로 거부한다(콘솔 메시지 "조건부 승인은 더 이상 쓰지 않습니다. 승인 또는 거절을 고르세요."). 오케스트레이터도 같은 함수로 재검증한다.
- `OfficerDecision.decision`의 `Literal`과 `Status.CONDITIONALLY_APPROVED`, 상태 라벨은 과거 문서를 읽기 위해 남긴다.
- 에이전트 권고 `conditional`은 그대로 둔다(평가 재측정이 필요 없다). 화면 라벨만 "승인 권고(조건 제안)".

## 7. 대출 기간 12개월

- `StartRequest.requested_tenor_months`, `CashFlowFeatures.requested_tenor_months`, `ProposedStructure.tenor_months`의 상한을 60 → 12로 바꾼다.
- `agents/underwriting.py`의 구조 검사(`1 <= tenor <= 60`)를 12로 바꾸고 분석가 지시문에 "기간은 12개월 이하"를 넣는다.
- 콘솔 새 신청 폼과 고객 폼의 기간 범위를 1~12로 둔다. 미리보기 픽스처의 24개월은 12개월로 바꾼다.
- 부하(`opsjob TENOR_MONTHS=12`), 평가(`REQUESTED_TENOR_MONTHS=12`), 스모크(`E2E_TENOR_MONTHS=12`)는 이미 12개월이다. 상품 한도 상향도 500만 원 이하 신청의 한도 계산(`min(신청액, 입금×1.5, 상품 한도)`)을 바꾸지 않는다. 그래서 기존 측정치는 그대로 유효하며 재측정하지 않는다(규칙 버전 문자열만 v2로 바뀐다).

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

## 9. 테스트

- 고객 신청: 동의 누락 422, 금액·기간 범위, 큐 메시지(`source="customer"`), 진행 현황 상태 매핑, 콘솔 목록 배지, `apply.js` 훅 존재.
- 정책 v2: 1,000만 원 신청의 한도 계산, `OVERRIDE_MAX_AMOUNT_KRW == load_rules().product.max_amount_krw`.
- 재생: 인라인 모드 pause → 결정 → awaiting_resume → resume(이어서 끝까지)·stop, 두 번째 재조정에서 다시 pause, 상태가 다르거나 `replay_id`가 다르면 무시. Service Bus 모드는 가짜 이벤트 버스로 주차 하나씩 발행·멈춤·재개를 확인한다. 콘솔 버튼·카드 표시와 재생 버튼 비활성화.
- 평가 화면: `assessment_panels` 단위 테스트(신호등 색, 한도 최솟값, 지출 구성 폴백, 권고 라벨), 상세 페이지 렌더링, 신호등 기준값 = 규칙 임계값.
- 조건부 제거: `conditional_retired` 거부(콘솔 422, 오케스트레이터 재검증), 폼에 선택지 없음, 과거 조건부 문서 표시.
- 기간: 13개월 신청 거부, 구조 검사 12개월.
- 운영 지표: `insights.py` 함수별 단위 테스트, `run=recent` 렌더링.

## 10. 배포·문서

- 배포: Policy(규칙 v2), 콘솔, Functions(검증·재생·category_totals), 평가 이미지(규칙 v2 포함). 배포 창을 열고 닫은 뒤 `ops smoke`.
- 라이브 확인: `/apply`로 신청 1건 → 콘솔 목록 배지 → 심사역 대기까지. 재조정이 나는 대출(스트레스 페르소나)로 재생 → 멈춤 → 결정 → 재개.
- 문서: `docs/architecture.md`(고객 채널, 재생 흐름), `docs/agents.md`(조건부 표시, 기간), ADR-003(한도 1,000만 원·조건부 제거), `docs/limitations.md`(고객 인증, 재조정 효과 미반영), `docs/runbook/deploy-history.md`.
