# P7 심사 콘솔 UI 고도화 — 설계 Spec

- 날짜: 2026-11-03
- 상위 Spec: `2026-10-31-agentic-cashflow-lending-design.md` §6.1(2분 데모), §8(루브릭 B3·B6)
- 선행: P1~P6c 완료·병합. 콘솔 코드는 `src/console/`(FastAPI + Jinja + htmx)
- 범위: 콘솔 **표시 계층만** 바꿉니다. 오케스트레이터·에이전트·데이터 계약·Cosmos 문서·API 경로는 바꾸지 않습니다. Azure 재배포는 이번 범위 밖입니다(로컬 미리보기로 확인).

---

## 1. 목적과 성공 기준

### 1.1 목적

지금 콘솔은 표와 목록 위주라 패널 데모(B3)와 문서 품질(B6)에서 "실제 금융사 심사 시스템"으로 보이지 않습니다. 같은 데이터를
대시보드·진행 단계·차트로 보여 주어 회사용 업무 시스템처럼 보이게 합니다.

### 1.2 사용자 결정 사항

| 항목 | 결정 |
|---|---|
| 디자인 방향 | A안 — 엔터프라이즈(Azure Portal 계열): 네이비 사이드바, 밝은 회색 바탕, 흰 카드, Fluent 계열 색 |
| 우선 화면 | 홈(신청 목록 → 대시보드), 신청 상세, 사후 모니터링(목록·상세), 운영 지표 |
| 그 밖의 화면 | 공통 레이아웃과 CSS만 적용(차주 시뮬레이터, 이의제기, 동의 증명, 신규 신청, 안내 화면) |
| 차트 방식 | 서버에서 SVG 생성 + CSS 애니메이션. JavaScript 차트 라이브러리 없음 |
| 확인 방법 | 로컬 미리보기(가짜 데이터)로 확인. Azure 배포는 다음에 |

### 1.3 원칙

1. **데모 대본 호환**: `docs/demo/demo-script.md`·`fallback.md`가 가리키는 화면 문구를 그대로 둡니다 — 내비게이션 `신청`, 버튼 `새 신청`·`결정 보내기`·`사후 13주 재생`·`지금 만료(데모)`, 섹션 제목 `타임라인`·`정책 판정`·`에이전트 평가`·`주장과 근거`·`제안 구조`·`개선 조건(반사실)`·`근거 거래`·`심사역 결정`·`주간 흐름과 탐지기`·`경보 <N>주차`, 상태 이름(`STATUS_LABELS`). 데모에서 클릭하는 표(`주간 흐름과 탐지기`)는 접지 않습니다.
2. **CSP 유지**: `CSP` 상수(`src/console/app.py`)를 바꾸지 않습니다. 템플릿에 `style=` 속성과 인라인 `<script>`를 쓰지 않고, 색·크기는 CSS 클래스와 SVG 기하 속성(`x`, `y`, `width`, `d`, `points`, `stroke-dasharray` 등)으로만 표현합니다.
3. **데이터에 없는 판단을 만들지 않음**: 차트는 저장된 값만 그립니다. 예) 정책 사유 코드에는 기준의 방향(이상/이하)이 없으므로 게이지에 통과·실패 색을 칠하지 않습니다. 모니터링 차트의 빨간 막대는 "그 주에 트리거가 걸림"이지 콘솔이 새로 계산한 임계값이 아닙니다.
4. **집계 범위를 밝힘**: 홈 KPI는 목록 쿼리 결과(최근 `LIST_LIMIT`=50건)로만 계산하고 화면에 "최근 50건 기준"이라고 적습니다.
5. **기존 정보 보존**: 지금 화면의 표·값은 지우지 않습니다. 차트는 위에 더하고, 데모에 쓰지 않는 상세 표만 `<details>`로 접을 수 있습니다.

### 1.4 성공 기준

1. 네 화면(홈, 신청 상세, 사후 모니터링 목록·상세, 운영 지표)이 2~4장의 구성대로 렌더링됩니다.
2. 모든 템플릿에 `style=` 속성과 인라인 스크립트가 없습니다(테스트로 고정).
3. 차트 기하 계산과 대시보드 집계·스테퍼 판정에 단위 테스트가 있고, 경계 사례(빈 데이터, 0, 값 1개, 음수, 결측)를 다룹니다.
4. 기존 테스트(1016 passed, 105 skipped)가 모두 유지됩니다. 문구가 바뀌어 깨지는 기존 테스트가 있으면 1.3의 원칙 1을 어긴 것이므로 템플릿을 고칩니다.
5. `python -m consolepreview`로 로컬 미리보기를 띄워 네 화면을 가짜 데이터로 볼 수 있습니다.

---

## 2. 공통 레이아웃과 디자인 체계

### 2.1 레이아웃(`base.html`)

- 왼쪽 고정 사이드바(네이비): 브랜드 `AMA Lending` + 부제 `심사 콘솔`, 메뉴 `신청`·`사후 모니터링`·`이의제기`·`차주 시뮬레이터`·`운영 지표`(지금 내비게이션과 같은 이름·경로), 아래쪽에 사용자 이름·역할, 데모 모드가 아니면 `로그아웃`.
- 현재 화면의 메뉴 강조: 템플릿이 `{% set nav = "home" %}` 같은 블록 변수로 알려 줍니다(서버 코드 변경 없음).
- 본문 위에 데모 배너(문구 그대로)와 페이지 머리(빵부스러기 + 제목 + 오른쪽 동작 버튼).
- `<title>` 기본값 `AMA Lending 심사 콘솔`과 htmx 스크립트 태그는 그대로입니다.
- 폭 900px 미만에서는 사이드바가 위쪽 가로 메뉴로 바뀝니다(CSS 미디어 쿼리).

### 2.2 디자인 토큰(`static/console.css` 재작성)

- 색: 사이드바 `#1b2a4a`, 바탕 `#f3f2f1`, 카드 `#ffffff`, 글자 `#323130`, 보조 글자 `#605e5c`, 선 `#edebe9`, 강조 `#0078d4`, 성공 `#107c10`, 경고 `#ffb900`(글자는 `#8a6d00`), 위험 `#d13438`(글자는 `#a4262c`), 보라 `#8764b8`, 중립 `#8a8886`.
- 글꼴: `"Segoe UI", "Malgun Gothic", system-ui, sans-serif`, 숫자는 `font-variant-numeric: tabular-nums`.
- 컴포넌트 클래스: `.card`, `.kpi`(+ 위쪽 색 띠 `.kpi-blue|amber|purple|green|red`), `.grid-2|3|4`, `.badge`, `.stepper`, `.chart`, `.legend`, `.page-head`, `.crumb`.
- 기존 상태 클래스(`.status`, `.s-*`, `.m-*`, `.c-*`, `.g-*`, `.sev-*`, `.chip`, `.ops-alert`, `.demo-banner`, `.error`, `.ok`, `.pii`, `.note`, `.quote`, `.notice`)는 이름을 유지하고 새 색으로 다시 칠합니다. 테스트와 템플릿이 이 이름을 씁니다.
- 애니메이션: 막대는 아래에서 자라기(`transform: scaleY`), 도넛·게이지·선은 그려지기(`stroke-dashoffset`), 길이 600ms 이하. `@media (prefers-reduced-motion: reduce)`에서는 모두 끕니다. 신청 상세는 진행 중 10초마다 새로 고쳐지므로 애니메이션이 짧아야 합니다.

### 2.3 차트 공통 규칙

- 모든 차트는 `<svg class="chart …" viewBox="…" role="img" aria-label="…">`이며 너비는 CSS로 100%입니다.
- 막대·점·호마다 `<title>`(브라우저 기본 툴팁)에 라벨과 원래 값(형식화된 금액·퍼센트)을 넣습니다.
- 색은 SVG 요소의 `class`(예: `fill-blue`, `stroke-amber`)로 지정합니다.
- 데이터가 없으면 차트 대신 `.note` 문구("표시할 데이터가 없습니다")를 냅니다.

---

## 3. 구성 요소

### 3.1 `src/console/charts.py` — 차트 기하(순수 함수)

SVG 문자열을 만들지 않고 좌표·길이가 담긴 dict/list만 돌려줍니다. 템플릿 매크로가 이 값을 속성에 넣으므로 Jinja 자동 이스케이프가 그대로 적용됩니다. 모든 좌표는 소수점 둘째 자리로 반올림합니다.

| 함수 | 입력 | 출력 | 규칙 |
|---|---|---|---|
| `donut(segments, r)` | `[(label, value, color)]` | 호마다 `dasharray`·`dashoffset`, 합계 | 음수·0 값은 건너뜀. 합계 0이면 빈 목록 |
| `hbars(items, width)` | `[(label, value, color)]` | 막대 `x, y, w, h`, 라벨 위치 | 최댓값 기준 비율. 최댓값 0이면 너비 0 |
| `columns(series, width, height)` | 그룹별 값 목록 | 막대 사각형, 축 눈금 | 음수는 0으로 그려 막대를 만들지 않고 툴팁에 원래 값 표시 |
| `stacked(rows, width)` | 행마다 `[(part, value, color)]` | 100% 누적 막대 조각 | 행 합계 0이면 회색 빈 막대 |
| `line(values, width, height, vmax)` | 숫자 목록 | `points` 문자열, 점 좌표 | `None`은 선을 끊음(구간 분리) |
| `gauge(value, vmax)` | 값, 최댓값 | 반원 배경·채움 `d` 경로 | 0 미만은 0, 최댓값 초과는 가득 참 |
| `marker_bar(observed, threshold)` | 관측값, 기준 | 채움 비율, 기준선 위치 | 둘 중 하나라도 `None`이거나 음수면 `None`(게이지 생략) |

### 3.2 `src/console/dashboard.py` — 화면 집계(순수 함수)

- `home_summary(rows)` → KPI와 분포:
  - `total` = 행 수
  - `awaiting` = 상태 `awaiting_officer`·`escalated` 수, `escalated` = 그중 `escalated` 수
  - `in_progress` = 종결(`TERMINAL`)도 대기도 아닌 수
  - `approval_rate` = (`approved` + `conditionally_approved`) ÷ (그 둘 + `declined`). 분모 0이면 `None`("-" 표시)
  - 상태 분포 5그룹: 승인·조건부 / 심사역 대기 / 거절 / 기타 종료(`withdrawn`·`closed_no_decision`·`failed`) / 진행 중
  - 권고 분포: `approve`·`conditional`·`refer`·`decline`·없음
- `journey_steps(doc)` → 7단계 스테퍼: `접수`, `동의 완료`, `데이터 수집`, `심사 지표 산출`, `에이전트 평가`, `심사역 대기`, `결정`.
  - 각 단계 상태: `done` / `current` / `todo` / `failed` / `warn`, 그 단계에 처음 도달한 시각(`timeline`의 `at`), 직전 단계로부터 걸린 초.
  - `escalated`는 `심사역 대기` 단계의 `warn`("SLA 초과"). `data_incomplete`는 `데이터 수집` 단계의 `warn`.
  - `approved`·`conditionally_approved`·`declined`는 `결정` 단계 `done`, 표시 라벨은 상태 이름(`승인`·`조건부 승인`·`거절`).
  - `withdrawn`·`failed`·`closed_no_decision`은 마지막으로 도달한 단계 다음 단계를 `failed`로 하고 라벨을 상태 이름으로 바꿉니다. 그 뒤 단계는 `todo`.
  - 타임라인에 없는 상태 코드는 무시합니다.
- `loans_summary(rows)` → 대출 수, 재생 중, 재생 완료, 경보 합계.
- `loan_summary(view)` → 재생 진행(처리 주 ÷ 13), 경보 수(심각도별), 최저 현금 버퍼와 기준 버퍼.
- `alert_steps(alert)` → `탐지` → `조기경보 조사` → `추심 에이전트` → (`재조정 상담 요청`이 있으면) `재조정 결정`. 추심 결과가 없으면 `추심 에이전트`는 `current`, 재조정 상태가 `pending_officer`면 `재조정 결정`이 `current`.

### 3.3 `src/console/templates/_charts.html` — Jinja 매크로

`donut`, `hbars`, `columns`, `stacked`, `line_overlay`, `gauge`, `marker_bar`, `stepper`, `kpi` 매크로. 각 매크로는 3.1·3.2의 결과만 받아 SVG/HTML을 그리고 계산은 하지 않습니다.

### 3.4 `src/console/app.py` 연결

- 라우트에서 dashboard 함수로 화면 요약을 계산해 컨텍스트 `ui`로 넘깁니다(홈 `home_summary(rows)`, 상세 `detail_summary(view)`, 모니터링 목록 `loans_summary(rows)`, 모니터링 상세 `loan_summary(view)`, 운영 `ops_summary(s, STEP_LABELS)`). 홈·모니터링 목록은 `limit=LIST_LIMIT`도 넘깁니다.
- 라우트 경로·폼 처리·권한·CSP·htmx 부분 갱신(`/applications/rows`)은 그대로입니다.

---

## 4. 화면별 구성

### 4.1 홈 `/` (`list.html`, `_rows.html`)

- 페이지 머리: 빵부스러기 `홈 › 신청`, 제목 `신청 대시보드`, 오른쪽 `새 신청` 버튼(권한 있을 때만, 지금과 같은 조건).
- KPI 4장: 최근 신청 / 심사역 대기(보조 줄 "SLA 초과 N") / 진행 중 / 승인율(종결 건). 보조 줄에 "최근 50건 기준".
- 차트 2개: 상태 분포 도넛(가운데 합계, 범례에 그룹별 수), 에이전트 권고 가로 막대.
- 아래 신청 표: 열 그대로, 상태는 점이 붙은 배지. `hx-get="/applications/rows"` 15초 갱신은 표 본문에만 그대로 적용됩니다. KPI·차트는 페이지 로드 시점 값입니다.

### 4.2 신청 상세 `/applications/<id>` (`detail.html`)

- 페이지 머리: 신청 ID + 상태 배지, 오른쪽에 SLA 마감(초과면 빨간 배지). 지금의 사실 목록(신청인, 금액, 접수, 플래그, 동의 철회, 오류)은 머리 아래 카드로.
- `타임라인` 카드: 7단계 스테퍼(3.2). 단계 아래 시각과 경과 초. 기존 상태 목록은 스테퍼 아래 `<details>`로 접어 둡니다(모든 상태 기록 보존).
- 2열 카드:
  - `월별 현금흐름`: 월마다 매출 막대와 지출 누적 막대(운영비·상환·대표자 인출)를 나란히. 데이터는 `doc.underwriting.monthly`. 없으면 카드 생략.
  - `정책 판정`: 판정 배지, 한도, 금리 구간. 관측값·기준이 모두 숫자인 사유 코드마다 마커 막대(관측값 채움 + 기준선). 막대 색은 중립(파랑). 아래에 기존 표(코드·내용·관측값·기준) 유지.
- `에이전트 평가` 카드: 권고 배지, 요약, 분석가→검토자 2단 스테퍼(`review.passed`·`rounds`), `주장과 근거` 칩, `제안 구조` 카드, `개선 조건(반사실)`(현재→목표가 둘 다 0 이상 숫자면 작은 비교 막대, 아니면 지금처럼 글).
- `현금흐름 지표` 표, `근거 거래` 표, 동의 블록(`_consents.html`)은 내용 그대로, 카드 스타일만.
- `심사역 결정` 카드: 폼·문구·기본값·오류 표시 그대로.
- 진행 중 10초 새로 고침(`meta refresh`)은 그대로입니다.

### 4.3 사후 모니터링 목록 `/loans` (`loans.html`)

- KPI 4장: 모니터링 대출 / 진행 중 재생 / 13주 완주(보조 줄 "미실행 N건") / 누적 경보. 상태 배지 문구(`재생 중`·`재생 완료`·`재생 전`)와 겹치지 않게 KPI 라벨을 달리해, 표의 배지를 확인하는 기존 테스트가 KPI 라벨로 우연히 통과하지 않게 합니다. 아래 표는 열 그대로, 모니터링 상태는 배지.

### 4.4 사후 모니터링 상세 `/loans/<id>` (`loan.html`)

- 페이지 머리: 대출 ID + 모니터링 상태 배지, 오른쪽 `사후 13주 재생` 버튼(문구·비활성 조건 그대로).
- KPI 4장: 대출(금액·기간·결정) / 재생 진행(N/13주 + 진행 막대) / 경보(심각도별) / 최저 현금 버퍼(기준선 버퍼 함께).
- 차트 `주간 매출과 현금 버퍼`: 주별 매출 막대(트리거가 걸린 주는 빨강), 기준 주매출(`baseline.avg_weekly_revenue`) 점선, 현금 버퍼 선(오른쪽 축), 새 트리거 주에 ▲. 툴팁에 주차·기간·매출·버퍼.
- `주간 흐름과 탐지기` 표는 접지 않고 그대로(데모가 이 표의 칩과 `경보` 링크를 씁니다).
- 경보 카드: 심각도 색 왼쪽 띠, 경보 처리 흐름 스테퍼(3.2 `alert_steps`), 그 아래 요약·신호·근거 칩·추심 연락 표·재조정 폼 그대로. `id="<alert_id>"` 앵커 유지.

### 4.5 운영 지표 `/ops` (`ops.html`)

- 측정 실행 선택 폼과 공정성 경보(`class="ops-alert"`) 그대로.
- 위쪽 4장: p95 지연 반원 게이지(최댓값 = 목표의 2배, 충족이면 초록, 미충족이면 빨강 — `latency.met` 값 그대로 사용) / 에이전트 단계 성공률 게이지(목표 대비, `rate ≥ target`이면 초록) / AI 비용(건당) KPI / 측정 표본 KPI(p50·최대 보조 줄).
- `단계별 결과`: 단계마다 ok·degraded·failed 100% 누적 막대.
- `공정성(세그먼트별)`: 축마다 세그먼트별 추천 approve율 세로 막대, 표본 부족 세그먼트는 회색, 표본 충분한 세그먼트의 최댓값·최솟값 높이에 점선. 축 제목의 격차 문구와 `policy_explained` 설명은 그대로. 기존 세그먼트 상세 표는 차트 아래 `<details>`로 접습니다.
- 기존 SLO 표는 게이지 아래 `<details>`로 접어 보존합니다.

---

## 5. 로컬 미리보기 — `src/consolepreview/`

- `python -m consolepreview [--port 8765]`: `127.0.0.1`에서 uvicorn으로 `create_app(ConsoleDeps(...))`을 띄웁니다. 데모 주체(`demo_principal`, CreditOfficer)로 인증 없이 열립니다.
- 메모리 가짜 저장소(`fixtures.py`):
  - 신청 8건 이상 — 상태가 접수·데이터 수집·에이전트 평가·심사역 대기·SLA 초과·승인·조건부 승인·거절·철회 종료를 덮습니다. 그중 1건은 `underwriting.features`·`monthly`(6개월)·`policy`(숫자 관측값·기준이 있는 사유 코드 포함)·`assessment`(주장·제안 구조·반사실·검토 결과)를 모두 갖습니다.
  - 대출 3건 — 재생 완료 1건(13주, 경보 2건: 높음·중간, 추심 결과와 재조정 대기 포함), 재생 중 1건, 재생 전 1건.
  - 운영 지표 측정 실행 1건 — 공정성 경보가 걸린 축 1개, 표본 부족 세그먼트 포함.
  - 원천 데이터(Blob)는 없음(`pii`는 "복원 불가" 표시, `근거 거래` 비어 있음). 큐 전송은 메모리에 쌓기만 합니다.
- 패키지는 `src/console/` 밖이라 콘솔 Docker 이미지(`src/console/Dockerfile`은 `src/console`만 복사)에 들어가지 않습니다.
- 차주 시뮬레이터·이의제기 화면은 미리보기에서 빈 데이터로 열리면 충분합니다(가짜 privacy 저장소는 빈 목록).

---

## 6. 테스트

| 파일 | 내용 |
|---|---|
| `tests/console/test_charts.py` | 3.1 함수별 정상·경계 사례(빈 입력, 합계 0, 값 1개, 음수, `None`, 최댓값 초과), 좌표 반올림 |
| `tests/console/test_dashboard.py` | `home_summary` 집계·승인율 분모 0, `journey_steps`의 정상·SLA 초과·데이터 부족·철회·실패·미결·거절 경로와 경과 초, `alert_steps` 분기, `loan_summary` 최저 버퍼 |
| `tests/console/test_ui.py` | 네 화면 렌더링(TestClient): SVG 차트·스테퍼·KPI 존재, 1.3 원칙 1의 문구 존재, 모든 템플릿 파일에 `style=`·인라인 `<script>`(src 없는 것) 없음, CSP 헤더 불변 |
| `tests/console/test_preview.py` | 미리보기 앱을 만들어 네 화면이 200으로 열림 |
| 기존 | `tests/console/*` 그대로 통과 |

명령: `$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python -m pytest -q -p no:cacheprovider`.

---

## 7. 문서

- `docs/demo/demo-script.md`: 0:15 장면의 화면 설명에 "`타임라인`은 7단계 진행 단계로 표시"를 한 줄 덧붙입니다. 섹션·버튼 이름은 그대로라 다른 수정은 없습니다.
- `docs/runbook/setup.md`의 "로컬 서비스 실행"에 미리보기 명령 한 줄. `docs/demo/demo-script.md`의 사이드바·상단 버튼 위치 표현과 `STATUS_LABELS` 위치(`src/console/statuses.py`)를 고칩니다.
- 배포(다음 단계): `python -m ops window open --acr` → `python -m ops deploy console` → 확인 → `python -m ops window close`. 이번 범위에서는 실행하지 않습니다.

---

## 8. 범위 밖

- 오케스트레이터·에이전트·정책·데이터 계약·Cosmos 쿼리 변경
- 새 집계 API, 실시간 차트 갱신(htmx 표 갱신 외)
- JavaScript 차트 라이브러리, 다크 테마
- 차주 시뮬레이터·이의제기·동의 증명 화면의 차트화(공통 레이아웃·CSS만 적용)
- Azure 배포와 라이브 확인

## 9. 위험

| 위험 | 대응 |
|---|---|
| 문구 변경으로 데모 대본과 화면이 어긋남 | 1.3 원칙 1의 문구 목록을 `test_ui.py`로 고정 |
| CSP 위반(인라인 스타일)으로 운영에서 차트가 깨짐 | 템플릿 `style=` 금지 테스트, CSP 상수 불변 테스트 |
| 진행 중 10초 새로 고침마다 애니메이션이 반복되어 산만함 | 애니메이션 600ms 이하, 감소된 동작 설정 존중 |
| 홈 KPI가 전체 통계로 오해됨 | "최근 50건 기준" 표기 |
| 정책 게이지가 통과·실패로 오해됨 | 중립색, 기존 표 유지(원칙 3) |
