# 문제 대응 (troubleshooting)

증상별로 찾아보는 문제 대응 모음입니다. 항목은 README의 단계별 `문제 대응` 절(P2~P6b)을 **원문 그대로** 주제별로 묶은 것이고, 항목 끝의 `(P5b)` 같은 표시는 원문이 있던 단계입니다. 당시 기준의 명령·수치이므로 단계별 맥락은 [deploy-history](deploy-history.md)를 함께 봅니다.

## 배포·네트워크

- Decision-1 배포(`decision-1`, 모델 `Microsoft-Decision-1`)가 SKU/지역 오류로 실패하면 `azd env set DECISION_MODEL_SKU DataZoneStandard` 후 다시 프로비저닝합니다([ADR-002](../adr/ADR-002-model-deployment.md) 전환 조건). 배포 이름에는 예약어 `Microsoft`를 쓸 수 없어 모델 이름과 다르게 둡니다. (P2)
- 재시도 전에 capability host가 이미 만들어졌는지 확인합니다. `Succeeded`면 `azd env set DEPLOY_CAPHOST false`: (P2)
  ```powershell
  $sub = azd env get-value AZURE_SUBSCRIPTION_ID; $acct = azd env get-value AI_ACCOUNT_NAME
  az rest --method get --url "https://management.azure.com/subscriptions/$sub/resourceGroups/rg-amalend/providers/Microsoft.CognitiveServices/accounts/$acct/projects/proj-lending/capabilityHosts/caphost-lending?api-version=2025-04-01-preview" --query properties.provisioningState -o tsv
  ```
- 쿼터 오류: `az cognitiveservices usage list -l koreacentral -o table`로 확인하고 `infra/main.bicep`의 `capacity`를 낮춥니다. (P2)
- `ops deploy`가 "ACR 빌드 창" 오류: `python -m ops window open --acr` 후 다시 실행합니다. (P3)
- `health:*`가 404: 앱이 아직 자리표시 이미지입니다. `azd env get-value SERVICE_POLICY_IMAGE_NAME`을 확인하고
  `python -m ops deploy policy`(또는 `mockapi`)를 다시 실행합니다. (P3)
- 동의에 `lifecycle_error: queue_disabled`: 함수 설정 `QUEUE_ENDPOINT`가 없습니다. `ops window open`으로 설정을 다시 넣습니다. (P5b)
- 운영자 PC 공인 IP가 연결마다 바뀌어 배포가 403: `ops window status`의 IP와 다른 IP를 Functions 접근 제한에
  `operator-alt` 규칙으로 잠시 추가하고, 작업 뒤 지웁니다(`ops window close`는 Functions 규칙을 Bicep 상태로 되돌립니다). 콘솔이 403이면 App Gateway WAF가 막은 것이라 `window open --ip <현재 IP>`로 `CONSOLE_IP`를 갱신합니다([deploy](deploy.md#운영자-ip가-바뀔-때)). (P6b)

## 신청 여정·오케스트레이터

- `journey`가 `awaiting_officer`에 도달하지 못함(마지막 상태 `None`): Function이 큐를 처리하지 않았습니다.
  `az functionapp function list -g rg-amalend -n (azd env get-value FUNCTION_APP_NAME) -o table`에 큐 트리거 2개,
  오케스트레이터, 활동이 보이는지 확인합니다. 비어 있으면 zip 배포나 함수 인덱싱 실패입니다(App Insights `exceptions`). (P3)
- `journey`가 `failed`: 결과의 `error.stage`(`request_consent`, `assemble_data`, `underwrite`)로 구간을 찾습니다.
  `assemble_data`는 Foundry 에이전트 호출, 나머지는 Mock API·정책 엔진 연결 문제입니다. (P3)
- 대출 목록이 비어 있음: P5a 배포 전에 승인한 신청은 `loans` 문서가 없습니다. 새로 신청해 승인합니다. (P5a)
- 재생 상태가 `재생 실패`: 화면의 오류와 App Insights `traces`(`journey.monitoring`)를 봅니다. 버튼으로 바로 다시 시작할 수 있습니다. (P5a)
- Service Bus 모드에서 주차가 멈춤: `flow.updated/early-warning` 구독의 dead-letter를 확인합니다. 앞 주차가 안 끝나면 뒤 주차는 재시도됩니다. (P5a)
- 재생이 `심사역 결정 대기`·`고객 응답 대기`·`재개 확인 대기`에서 멈춤: 정상입니다. 주차 처리 뒤 대기 항목이 남으면 재생이 멈추고, 대출 화면 위 배너가 남은 항목을 이 순서로 보여 줍니다(`HOLD_ORDER`, `src/orchestrator/journey/holds.py`). 심사역 항목이 하나라도 있으면 `심사역 결정 대기`, 고객 응답만 남으면 `고객 응답 대기`입니다. 항목별로 이렇게 풉니다. (P9, 2026-11-11)
  - `추심 승인 대기`(`collections_proposal`): 미납 추심 건의 `추심 제안` 카드에서 `추심 제안 결정`(승인·수정 후 승인·반려)을 보냅니다. 미납 주차는 늘 이 항목으로 멈춥니다. 발송 결과가 `보류`(연락 시간 밖·7일 7회 초과)여도 결정은 끝난 것이고 다시 보내지 않습니다.
  - `고객 응답 대기`(`customer_response`): 높음 경보 2단계로 연 `상환 플랜 제안`입니다. 차주 시뮬레이터에서 고객이 옵션을 고르거나(`지금 일정 유지`면 바로 끝남), 심사역이 대출 화면의 `고객 응답 없이 계속`으로 제안을 만료시킵니다.
  - `플랜 변경 승인 대기`(`plan_change`): 고객이 상환일 이동·분할을 골랐습니다. `플랜 변경 요청` 카드의 `플랜 변경 결정`(승인·반려, 반려는 사유 필수)을 보냅니다. 승인하면 아직 공개되지 않은 주차의 상환 일정이 바뀝니다.
  - `재조정 결정 대기`(`restructure`): 승인·수정 후 승인한 추심 제안에 재조정 상담이 들어 있어 만든 요청입니다. `재조정 상담 요청`의 `재조정 결정`(승인·거절)을 보냅니다.
  - 항목이 모두 끝나면 `재개 확인 대기`가 되고 [이어서 재생]·[여기서 종료]를 고릅니다. 결정 폼은 재생 중에는 잠기고 멈춘 뒤에 열립니다. 결정을 보냈는데 항목이 그대로면 `restructure-decisions` 큐의 poison 큐와 App Insights `traces`(`journey.monitoring`)를 봅니다.
  - 주차는 앞 주차(미납이 있으면 추심 제안까지)가 끝나야 다음 주차가 발행됩니다. 추심은 미납 주차에만 돌며, 미납 주차에서 멈춰 있는데 추심 제안이 안 보이면 `repayment.updated/collections` 구독의 dead-letter도 봅니다.

## 에이전트·Foundry

- `decision`이 401/403: Function·smoke 관리 ID에 Foundry 계정 `Cognitive Services User` 역할이 필요할 수 있습니다
  (운영자 계정은 `Azure AI User`로 동작 확인). 역할을 추가하고 5분 뒤 다시 실행합니다. (P4a)
- 신청이 `failed`(오류 `assess_credit: 에이전트 평가 없음(2회 시도): …`): 평가가 두 번 모두 비었다. 오류 문구에 마지막 에이전트 오류가 남는다. `HTTP 400 … Function tools with reasoning_effort are not supported`면 그 모델은 게이트웨이(chat completions)에서 함수 도구를 못 쓴다 — `ANALYST_MODEL`을 게이트웨이에서 동작하는 모델로 바꾼다(2026-11-07 `gpt-6.1-sol` 사례).
- `journey`가 "심사 에이전트 결과 없음": 결과의 `flags`에 `assessment_unavailable`이 있으면 App Insights `traces`에서
  `assess_credit` 오류를 찾습니다. 근거 검사·검토를 3라운드 안에 통과하지 못하면 `assessment`는 남지만
  `review.passed=false`이고 `review_not_passed` 플래그가 붙습니다(smoke는 통과, 출력에 "not passed"). (P4a)
- 에이전트 정의를 바꾸면 Foundry에 새 버전이 생깁니다(같은 정의는 같은 버전). (P4a)
- 경보 모델이 `detector_only`: 에이전트 호출이 실패해 탐지기만으로 만든 경보입니다(조사 기록의 `record.error`). (P5a)

## 콘솔

- 콘솔이 403(빈 화면): 현재 공인 IP가 `CONSOLE_IP`와 다릅니다. `python -m ops window open` → `window close`. (P4b)
- 로그인 시 "관리자 승인 필요": 테넌트가 사용자 동의를 막았습니다. 관리자가 앱에 관리자 동의(openid·profile·email)를 하거나,
  운영자 본인에 대한 위임 권한 부여(`oauth2PermissionGrants`, consentType `Principal`)를 만들거나, 데모 모드를 씁니다. (P4b)
- 로그인 후 `AADSTS50105`(역할 미할당): 엔터프라이즈 애플리케이션에서 사용자에게 앱 역할을 할당합니다. (P4b)
- 로그인 후 `AADSTS50011`(redirect URI 불일치): `azd provision`이 Container Apps 기본 도메인으로 redirect URI를 다시 씁니다.
  `ops window open`/`close` 중 하나를 다시 실행하세요. (P4b)
- 로그인은 되지만 403 "역할 없음": 토큰에 `roles` 클레임이 없습니다. 역할을 할당한 뒤 로그아웃(`/.auth/logout`)하고 다시 로그인합니다. (P4b)
- 상세 화면의 실명이 "원천 데이터 없음": 동의 컨테이너(`consent-<동의 ID>`)가 없거나 만료 처리되었습니다(가명 그대로 표시). (P4b)

## 동의·삭제·이의제기

- 삭제 증명이 `미완료`: 증명의 `items`에서 `검증 실패` 항목과 비고를 봅니다(`agents_disabled`면 Foundry 설정 없음). 오케스트레이션이 재시도하며,
  끝내 실패하면 수명주기 결과가 `deletion_failed`입니다. 같은 동의에 아무 명령이나(예: `consent-commands` 큐에 `{"consent_id": "…", "action": "remind_now", "requested_by": "ops"}` JSON 원문) 다시 보내면 끝난 사유(철회·만료) 그대로 수명주기를 다시 시작해 검증되지 않은 항목만 다시 지웁니다. (P5b)
- 이의제기가 목록에 안 보임: 결정 전 신청이나 다른 차주의 신청이면 큐 처리기가 버립니다(App Insights `journey.grievance`). (P5b)
- 재심사가 `거부된 처리 시도`로 남음: 접수 시점 정책 한도를 넘었거나 사유가 없습니다. (P5b)

## 평가·운영 지표

- `ops eval`이 `평가 실패: … 상태 Failed`: 출력된 Log Analytics 쿼리로 Job 로그를 봅니다(`[eval]` 줄, 예외). 이미지가 자리표시면 `ops deploy eval`부터. (P6a)
- `리포트 복원 실패: 조각 누락`: 로그 수집이 늦었거나 Job이 리포트를 쓰기 전에 끝났습니다. 몇 분 뒤 `python -m ops eval --collect <실행 이름>`으로
  Job을 다시 돌리지 않고 회수만 다시 합니다(일시적 조회 오류·잘린 조각 줄도 재조회 대상). Blob `eval-reports/<실행 이름>/` 사본은 데이터 스토리지가
  공용 접근 차단·사설 엔드포인트 전용이라 운영자 PC에서는 바로 읽을 수 없습니다(VNet 안에서만, 운영자 Blob 역할 없음). (P6a)
- 리포트에 `agent_error`가 많음: `calls.jsonl`의 `status`·`retries`·`error`를 봅니다(429 → 쿼터, 401/403 → `id-eval` 역할). (P6a)
- 로컬에서 실제 모델로 돌릴 때(`AzureCliCredential`)는 `AI_PROJECT_ENDPOINT`·`AI_ACCOUNT_ENDPOINT`를 설정하고 운영자 IP 창을 엽니다.
  토큰은 만료 5분 전까지 재사용합니다(호출마다 `az`를 띄우면 느리고 간헐적으로 실패). (P6a)
- `ops load`가 `측정 실패: … 상태 Failed`: 출력된 쿼리로 `[opsjob]` 줄을 봅니다. 이미지가 자리표시면 `ops deploy eval`부터. (P6b)
- `summary.json`의 `unreached`가 많음: Functions 로그(`AppTraces`)에서 해당 `application_id`를 찾고, 큐 메시지가 쌓였는지 봅니다. (P6b)
- `고정비 측정 불가(…)`: Cost Management 429·권한·지연(약 1일)입니다. 다시 실행하거나 `--fixed-monthly-usd`로 수동 입력합니다.
  청구 통화가 원화면 `--fx-krw-per-usd`를 줍니다. (P6b)
- 장애 토글이 남음(`ops fault status`에 `flaky`·`unavailable`): `python -m ops fault source off` / `policy off`, 또는 `azd provision`. (P6b)
