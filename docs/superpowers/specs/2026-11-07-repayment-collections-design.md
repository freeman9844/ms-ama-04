# 상환 상태 기반 추심 설계

- 날짜: 2026-11-07
- 근거: `ko-origin.md` 구현 보완 §3("상환일·미납·고객 지원 요청이 발생함 → Collections", "정산이 지연되면 조기 경보가 작동할 수 있지만, 그 사실만으로 추심이 시작되지는 않는다")
- 결정(사용자 선택): 상환 스케줄 A(월 상환 유지, 실행일 기준 30·60·90일차), 이벤트 경로 A(새 Service Bus 토픽), 조기경보 역할 A(기록·심사역 검토만)

## 1. 문제

지금은 조기경보(EW) 경보가 뜨면 `alert.raised` → `run_collections`로 추심 에이전트가 곧바로 차주에게 연락하고 재조정을 요청한다(`src/orchestrator/journey/monitoring.py`). 상환 원장이 없어서 추심이 실제 상환 상태와 무관하다. 보완 §3과 어긋난다.

## 2. 목표와 비목표

**목표**
- 추심(Collections)은 상환 사건(상환일 임박, 미납)으로만 시작한다.
- EW 경보는 기록·콘솔 표시·심사역 검토 대상으로만 남고 차주 연락을 만들지 않는다.
- 대출마다 상환 원장(회차·상환일·금액·상태)을 남기고 콘솔에서 본다.
- 이벤트 경로는 `repayment.updated` 토픽 → `collections` 구독으로 아키텍처에 드러낸다.

**비목표**
- 차주의 상환 지원 요청 화면(보완 §1, 별도 항목 4)
- 재조정 결과(유예·분할)를 이후 재생에 반영
- 실제 펌뱅킹/CMS 연동. 납부 여부는 동의된 계좌 흐름의 출금 내역으로 대조한다(데모 한계로 문서화)

## 3. 설계

### 3.1 상환 스케줄(공유 규칙)

`src/shared/repayment.py`(신규, 순수 함수):
- `LENDER_DEBIT = "에이엠에이렌딩 대출상환"`(Mock 출금 적요와 원장 대조에 같이 쓴다)
- `REMINDER_DAYS = 3`, `CYCLE_DAYS = 30`
- `due_dates(start, end) -> list[date]`: `start + 30k`(k=1,2,…)를 다음 영업일로 미루되 `end`(재생 마지막 날) 이후면 직전 영업일로 당긴다. 91일 재생에서 3회.

Mock과 오케스트레이터가 같은 함수를 써서 상환일이 어긋나지 않는다.

### 3.2 Mock 사후 흐름

`src/mockapis/postloan.py`:
- `_installments`가 `due_dates`를 쓴다(매월 15일 → 실행일 기준 30일 주기).
- 자동이체 실패 규칙: 상환일 출금 직전 잔고가 상환액보다 적으면 출금하지 않는다(모든 시나리오).
- STACKING(주인공 시나리오)은 마지막 회차(약 86~90일차)를 잔고 부족 실패로 정한다. 부도(첫 음수 잔고, 70~88일차) 직후 정산 입금으로 잔고가 잠시 회복되는 경우가 있어 잔고 규칙만으로는 미납이 76%만 재현됐다(2026-11-07 시험). 대본화된 시나리오임을 문서에 밝힌다.
- `PostLoanFlow.missed_installments: tuple[date, ...]`(평가·테스트 전용, API 비노출).
- STABLE 시나리오에서 미납 0건(시험 1,440회차 0건)을 테스트로 고정한다.

### 3.3 상환 원장

대출 문서(`loans` 컨테이너)의 `repayment` 필드. 새 컨테이너·인프라는 없다.

```json
{"installment_krw": 287000,
 "schedule": [{"no": 1, "due_on": "2026-12-02", "status": "paid", "reminded_on": "2026-11-29", "txn_id": "…-P00123", "case_id": null}]}
```

- `status`: `scheduled` → `paid` | `missed`. 안내 발송은 `reminded_on`으로 따로 남긴다.
- 금액은 대출 조건(`monthly_installment(amount, tenor)`)이다. Mock 출금액(고정 287,000원)과 다를 수 있다. 납부 판정은 상환일 당일 `LENDER_DEBIT` 출금 존재 여부로만 한다.
- 원장은 `start_replay`가 흐름을 받은 뒤 만든다(`flow_start` ~ `flow_start + 90일`).

`src/agents/repayment.py`(신규, 순수): `build_schedule(...)`, `week_events(schedule, txns, lo, hi) -> RepaymentWeek(reminders, paid, missed)`.

### 3.4 주차 처리

`process_week`(EW 처리 뒤):
1. `week_events`로 이번 주의 안내 대상(상환일-3일이 이번 주), 납부, 미납을 구한다.
2. **안내:** LLM 없이 승인 스크립트 `payment_reminder`를 `send` 규칙(공정 추심: 7일 7회·08~21시 KST)으로 보낸다. 연락 이력 문서(`contacts-{replay_id}`)에 `by="reminder"`로 남는다.
3. **납부:** 원장 상태 `paid`, `txn_id`.
4. **미납:** 원장 `missed`, 추심 건 문서 `rc-{replay_id}-i{no}`를 `alerts` 컨테이너에 만든다(필드: `kind="repayment_case"`, `case_id`, `loan_id`, `replay_id`, `week`, `as_of`=상환일, `installment`, `recent_alert`(이번 재생 최근 EW 경보 요약, 없으면 null), `status="open"`, `collections=null`, `restructure=null`, `event_error=null`).
5. Service Bus면 `repayment.updated`(세션=loan_id, 메시지 ID `repayment.updated:{case_id}`)를 발행하고, 인라인이면 바로 `run_collections`.

EW 경보는 저장하고 `alert.raised`도 계속 발행하지만(감사·향후 구독용) 추심을 부르지 않는다. 주차 연쇄는 추심 건이 있을 때만 추심 핸들러가 잇고, 없으면 흐름 핸들러가 바로 잇는다.

### 3.5 추심 에이전트

`src/agents/collections.py`:
- 입력이 `EarlyWarningAlert`에서 `RepaymentCase`(회차·상환일·금액·연체 회차 수·최근 EW 경보 요약)로 바뀐다.
- 도구 `get_alert` → `get_case`. 나머지(`get_contact_history`, `list_scripts`, `send_script`, `route_hardship`)는 유지.
- 스크립트 `missed_payment`(상환금 자동이체 미처리 안내, 개인정보·금액 자리표시자 없음) 추가.
- `AUTO_ACTIONS` 게이트 제거(미납이면 항상 처리).
- 폴백: 최근 EW 경보가 high면 `hardship_offer`, 아니면 `missed_payment`. 재조정 요청은 만들지 않는다(기존과 같음).
- 결정적 안내 함수 `remind(contacts, on, now) -> entry`를 같은 모듈에 둔다(공정 규칙 재사용).

### 3.6 정지·재개

- `_after_week`·`_promote_if_decided`의 키를 경보 ID에서 추심 건 ID로 바꾼다. 필드 이름 `pause_alert_id` → `pause_case_id`(읽을 때 옛 필드도 본다: 이미 멈춘 옛 재생이 막히지 않게).
- 13주차(마지막 주)에 멈추면 결정 뒤 `awaiting_resume`이 아니라 `completed`로 끝낸다(이어갈 주차가 없다).
- `handle_restructure_decision`은 `alerts` 컨테이너 문서 ID로 찾으므로 그대로 쓴다(`RestructureDecision.alert_id`에 추심 건 ID가 들어간다).

### 3.7 Service Bus·Functions

- `infra/resources.bicep`: 토픽에 `repayment.updated` 추가. 구독 `alert.raised/collections` → `repayment.updated/collections`.
- `src/orchestrator/function_app.py`: `alert_raised` 트리거 → `repayment_updated`(`handle_repayment_updated`).
- Service Bus는 평소 꺼져 있다(`ops pause`). Bicep은 다음 `ops resume`/프로비저닝 때 반영된다.

### 3.8 콘솔

- `ALERTS_QUERY`가 `ew-`와 `rc-` 문서를 모두 읽고 `loan_view`가 나눈다.
- 대출 화면:
  - 상환 원장 표(회차·상환일·금액·상태·안내일·출금 거래)
  - 추심 건 카드(미납 정보, 참고 EW 경보, 추심 에이전트 결과·연락 표, 재조정 요청·결정 폼)
  - EW 경보 카드에서 추심 영역 제거, "심사역 검토 대상 · 차주 연락 없음" 표시
  - KPI에 "상환 n/m회 · 미납 k"
- 재조정 결정 폼은 기존 경로(`/loans/{id}/alerts/{doc_id}/restructure`)를 쓰고, 핸들러는 문서 `id`로 찾는다.
- 정지 안내 문구: "n주차 미납 추심 건에서 추심 에이전트가 재조정 상담을 요청했습니다."

## 4. 오류·경계 조건

- 같은 주에 안내일과 상환일이 함께 오면 안내를 먼저 기록하고 납부를 판정한다.
- 재전달된 `repayment.updated`는 `collections`가 이미 있으면 다시 연락하지 않는다(기존 멱등 규칙).
- 발행 실패 시 추심 건에 `event_error`를 남기고 흐름 핸들러가 다음 주차를 잇는다(경보와 같은 규칙).
- 재생을 다시 시작하면 원장도 새 재생 기준으로 다시 만든다.
- 마지막 주차 정지: 결정 후 `completed`.

## 5. 검증

- 단위: 스케줄(영업일·끝 경계), 주차 이벤트, Mock 미납 규칙(STACKING 미납 1회 이상·STABLE 0회, 여러 기준일), 추심 에이전트 도구, 정지·재개(13주차 포함), 인라인·Service Bus 경로, 콘솔 렌더.
- Azure: 주인공 대출 인라인 재생 → 원장 3회(납부 2, 미납 1), 안내 연락, 13주차 추심 에이전트 실행, 재조정 결정 → `completed`. EW 경보에 추심 없음.
- EW 평가(`ops eval --suite early_warning`)는 Mock 흐름이 바뀌므로 다시 돌려 리드 타임 변화를 기록한다(선택, 비용 확인 후).

## 6. 문서

`docs/agents.md`(추심), `docs/architecture.md`(이벤트 흐름·토픽), `docs/limitations.md`(납부 대조 한계, 대본화된 미납), `docs/demo/demo-script.md`(사후 장면), `docs/runbook/deploy-history.md`.
