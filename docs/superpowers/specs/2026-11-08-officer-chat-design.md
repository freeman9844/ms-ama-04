# 심사역 결정 채팅 설계

## 목적

심사역이 결정 탭에서 자연어로 질문하고, 현금흐름·정산 대사·정책 판정·에이전트 의견을 근거로 한 답을 받아 승인·거절 판단을 돕는다. 채팅은 결정을 대신하지 않는다. 최종 결정은 기존 결정 폼으로 심사역이 보낸다.

## 결정 사항

| 항목 | 결정 |
|---|---|
| 모델 | `gpt-6-luna`(2026-09-22, GlobalStandard) 새 배포, 용량 100(=100K TPM). 구독 쿼터 0/250이라 여유가 있고 조기경보의 `gpt-5.6-luna`(240)와 분리된다. |
| 호출 경로 | 콘솔 → 기존 `FoundryAgents` 런타임 → APIM 연결 `gw-officer-chat`(TPM 100K) → `gpt-6-luna` |
| 근거 방식 | 스냅샷. 서버가 신청 1건의 가명화 요약을 매 질문에 넣는다. 도구 호출은 없다. |
| 대화 기록 | 브라우저 메모리에만 둔다. 서버·Cosmos에 저장하지 않고, 새로 고침하면 사라진다. |
| 위치 | `detail.html` 결정 탭(`tab-decision`) 안, 결정 폼 오른쪽 |
| 응답 | 동기 JSON(스트리밍 없음). 기다리는 동안 입력을 막고 "답변 작성 중…"을 표시한다. |

## 구성 요소

### 인프라

- `infra/main.bicep` 모델 배포 목록에 `{ name: 'gpt-6-luna', format: 'OpenAI', version: '2026-09-22', sku: chatModelSku, capacity: 100 }`를 더하고 쿼터 주석을 갱신한다.
- `infra/resources.bicep` 게이트웨이 표에 `{ name: 'gw-officer-chat', tpm: 100000 }`를 더한다. 모델 목록이 연결에 노출되는 방식은 기존 `gw-*`와 같다.
- 콘솔 관리 ID(`identities.outputs.consolePrincipalId`)에 Foundry 계정 범위 `role.foundryUser`, `role.openAiUser`를 준다(오케스트레이터와 같은 위치, `resources.bicep:331` 부근).
- 콘솔 컨테이너 앱 `envVars`에 `AI_PROJECT_ENDPOINT`, `AI_GATEWAY`, `AI_GATEWAY_URL`, `CHAT_MODEL=gpt-6-luna`를 더한다. 관리 ID는 이미 있는 `AZURE_CLIENT_ID`(`adapters.py:224`)를 그대로 쓴다.
- `src/agents/gateway.py` `CONNECTIONS`에 `"officer-assistant": "gw-officer-chat"`을 더한다. 게이트웨이 표와 `CONNECTIONS`를 맞추는 기존 교차 테스트가 둘을 함께 검사한다.

### 에이전트 `src/agents/officer_chat.py`

- `officer_spec(model)`은 `AgentSpec(name="officer-assistant", tools=(), throttle_retries=5, ...)`을 돌려준다.
- 지시문 원칙:
  1. 입력의 `snapshot` JSON에 있는 사실만 근거로 답한다.
  2. 수치를 인용할 때는 출처를 괄호로 밝힌다(예: "(현금흐름)", "(정산 대사)", "(정책 R106)", "(에이전트 의견)").
  3. 스냅샷에 없는 내용은 "자료에 없음"이라고 답하고 추측하지 않는다.
  4. 승인·거절을 결정하거나 지시하지 않는다. 판단에 필요한 사실과 쟁점을 정리하고 "최종 결정은 심사역이 합니다"를 전제로 한다.
  5. 한국어, 5문장 안팎, 필요하면 짧은 목록.
- `ask(runner, snapshot, history, question) -> ChatAnswer`는 런타임을 한 번 호출하고, 끝나면 Foundry 대화를 지운다.

### 콘솔 `src/console/officer_chat.py`

- `build_snapshot(view) -> dict`는 `detail_view(..., restore=False)` 결과에서 정해진 필드만 고른다.
  - 신청 요약: 신청 ID, 상태, 신청 금액·기간, 업종
  - 현금흐름: 월별 현금흐름(`underwriting.monthly`), 수치 특성치·신호, 상환 여력(이번 대출 월 상환액과 부족한 달), 정산 대사 요약(`pg_ledger`) (신청 상세에는 4주 전망이 없다. 4주 전망은 사후 모니터링 기능이다)
  - 정책: 판정(`decision`), 규칙 코드·설명·실제값·기준, 최대 한도
  - 에이전트 의견: 권고, 요약, 검토 통과 여부, 제안 구조, 주장(`claims`) 텍스트와 근거 지표 값, 반사실
  - 심사역 결정: 결정 종류와 금액(심사역 ID·결정 사유는 넣지 않는다)
- 원천 Blob을 읽지 않는다. 거래 원본, 실명·사업자번호·계좌번호, 복원(`restore=True`) 텍스트는 넣지 않는다. 가명 토큰만 남는다.
- 스냅샷 크기 상한은 직렬화 기준 24,000자다. 넘으면 주장(뒤에서부터) → 반사실 → 오래된 월 순으로 줄이고 `truncated: true`를 붙인다.
- `ConsoleDeps`에 선택 필드 `chat: ChatClient | None`을 더한다. `adapters.build_deps()`는 `AI_PROJECT_ENDPOINT`와 `CHAT_MODEL`이 있을 때만 만든다. 없으면 채팅 패널이 "채팅을 쓸 수 없습니다"를 보여 준다.

### 라우트

`POST /applications/{application_id}/chat`

- 요청: `Content-Type: application/json`, `X-CSRF-Token` 헤더(콘솔 CSRF 토큰), 본문 JSON `{"question": str, "history": [{"role": "user"|"assistant", "text": str}, ...]}`
- 검증: 질문은 앞뒤 공백을 빼고 1~500자. 대화 기록은 최근 6턴(12메시지)까지만 쓰고, 각 메시지는 2,000자로 자른다.
- 응답 200: `{"answer": str}`
- 오류(401·403·413·415는 FastAPI 기본 `{"detail"}`, 나머지는 JSON `{"error": 안내 문구}`):

| 상황 | 코드 | 안내 문구 |
|---|---|---|
| 본문이 JSON 객체가 아님 | 400 | 요청 형식이 올바르지 않습니다. |
| 본문 128KB 초과 | 413 | (FastAPI 기본 `{"detail"}`) |
| Content-Type이 JSON이 아님 | 415 | (FastAPI 기본 `{"detail"}`) |
| 신청 없음 | 404 | 신청을 찾을 수 없습니다. |
| 질문 검증 실패 | 422 | 질문은 1~500자로 입력해 주세요. |
| 채팅 비활성(설정 없음) | 503 | 채팅을 쓸 수 없습니다. |
| 5번 재시도 뒤에도 429(`GatewayThrottled`) | 429 | 요청이 많습니다. 잠시 후 다시 질문해 주세요. |
| 게이트웨이 차단(`GatewayBlocked`, 콘텐츠 필터 등) | 422 | 이 질문에는 답할 수 없습니다. |
| 시간 초과(호출당 60초, App Gateway 요청 시간 180초)·기타 `AgentError` | 502 | 답변을 받지 못했습니다. |

- 권한: 결정 탭을 볼 수 있는 모든 콘솔 역할(`viewer`: 심사역·관리자·감사자)이 호출할 수 있다. CSRF 토큰이 맞지 않으면 403이다.
- 로그: 신청 ID, 입력·출력 토큰, 지연(ms), 결과 코드만 표준 출력(로거 `console.chat`)에 남긴다. 질문·답변 본문은 남기지 않는다.

### 화면 `detail.html`

- 결정 탭을 2열로 나눈다. 왼쪽은 기존 결정 폼(변경 없음), 오른쪽은 채팅 패널이다. 좁은 화면에서는 세로로 쌓는다.
- 채팅 패널 구성:
  - 대화 목록
  - 추천 질문 3개 버튼: "상환 여력이 부족한 달과 이유는?", "정책 판정과 회부 사유는?", "에이전트가 이 권고를 낸 근거는?"
  - 질문 입력창(500자)과 보내기 버튼
- 정적 파일 `static/officer_chat.js`(CSP상 인라인 금지)는 `fetch`로 라우트를 `X-CSRF-Token` 헤더와 함께 부르고 대화 기록을 메모리 배열에 둔다. 응답을 기다리는 동안 입력과 버튼을 막는다. 오류 응답의 `error` 문구를 대화 목록에 시스템 메시지로 보여 준다.
- 결정 탭이 보이는 모든 상태에서 채팅을 쓸 수 있다. 결정이 반영된 뒤에도 질문할 수 있다. 결정 폼과 채팅은 서로 독립이다.
- 상세 화면의 자동 새로 고침(`DETAIL_REFRESH_SECONDS`)은 처리 중(`in_progress`)이거나 결정 반영을 기다릴 때(`watch_url`)만 동작한다. 그래서 심사역 결정 대기 상태에서는 대화가 유지된다. 결정을 보낸 직후 반영을 기다리는 새로 고침에서는 대화가 지워진다. 기록을 남기지 않기로 한 결정과 같으므로 그대로 둔다.

## 데이터 흐름

1. 심사역이 질문을 입력한다.
2. 브라우저가 질문과 최근 6턴을 `POST /applications/{id}/chat`으로 보낸다.
3. 서버가 신청 문서를 읽고 `detail_view(restore=False)`로 가명화 뷰를 만든 뒤 `build_snapshot`으로 줄인다.
4. `officer_chat.ask`가 `{snapshot, history, question}`을 입력으로 런타임을 호출한다. 런타임은 `gw-officer-chat/gpt-6-luna`로 가고 429면 지수 백오프로 5번까지 다시 보낸다.
5. 서버가 Foundry 대화를 지우고 `{"answer"}`를 돌려준다.
6. 브라우저가 답을 대화 목록과 메모리 기록에 붙인다.

## 테스트

단위 테스트는 가짜 런타임을 쓴다.

- `build_snapshot`
  - 실명, 사업자번호, 계좌번호, 거래 원본 설명이 들어가지 않는다.
  - 정해진 키만 담긴다.
  - 크기 상한을 넘으면 줄인다.
- 라우트
  - 정상 응답 200
  - 질문 길이 검증 422
  - 대화 기록은 6턴, 메시지는 2,000자로 자른다.
  - 없는 신청 404, 채팅 비활성 503
  - `GatewayThrottled` 429, `GatewayBlocked` 422, 기타 `AgentError` 502와 각 안내 문구
  - 로그에 질문 본문이 없다.
- 에이전트 스펙: `throttle_retries=5`, 도구 없음, 지시문에 "결정을 내리지 않는다"와 출처 표기 원칙이 있다.
- 화면: 결정 탭에 채팅 패널, 추천 질문 3개, 입력창이 렌더링된다. 채팅 비활성일 때는 안내 문구만 렌더링된다.
- 인프라: 다음 항목이 서로 맞는지 교차 검사한다.
  - 게이트웨이 표의 `gw-officer-chat`과 `CONNECTIONS`
  - `gpt-6-luna` 배포
  - 콘솔 앱 설정 `CHAT_MODEL`
  - 콘솔 ID의 Foundry 역할

## 배포와 검증

1. `ops window open --acr`. 프로비저닝으로 `gpt-6-luna` 배포, `gw-officer-chat`, 콘솔 역할과 설정이 생긴다.
2. `ops deploy console`
3. `ops smoke`
4. 라이브 확인: 결정 대기 신청 한 건에서 추천 질문 3개를 보낸다. 다음을 확인한다.
   - 답에 출처 표기가 있다.
   - 스냅샷에 없는 질문(예: "대표자 주소는?")에 "자료에 없음"이라고 답한다.
   - 결정을 지시하지 않는다.
   - 실명이 나오지 않는다.
   - `gw-officer-chat`에서 429가 없다.
5. `ops window close`. 이어서 필요하면 WAF 임시 예외를 다시 추가한다.
6. `docs/agents.md`(새 에이전트), `docs/architecture.md`(콘솔 → 게이트웨이 경로), `docs/runbook/deploy-history.md`를 고친다.

## 위험

- **게이트웨이 호환**: 게이트웨이 연결(chat completions)에서 `gpt-6.1-sol`은 함수 도구와 추론을 함께 쓰면 400이었다. 이 에이전트는 도구가 없으므로 영향이 없을 것으로 보지만, 배포 직후 라이브 확인으로 검증한다. 실패하면 `CHAT_MODEL`을 이미 검증된 모델로 바꾸는 것이 롤백이다.
- **사적 네트워크**: 콘솔은 `cae-int`(VNet 안)에서 돌고 Foundry와 APIM은 사설 경로로 닿는다. 오케스트레이터와 같은 경로라 새 네트워크 규칙은 필요 없다. 배포 후 콘솔에서 첫 호출로 확인한다.
- **비용과 용량**: 100K TPM 전용 배포라 다른 에이전트와 경합하지 않는다.

## 범위 밖

- 대화 저장과 감사 기록
- 스트리밍 응답
- 도구 호출(거래 조회 등)
- 재조정 what-if 시뮬레이션
