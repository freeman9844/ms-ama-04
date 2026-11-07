# 한계와 운영 전환

이 문서는 데모의 **as-built 한계**를 주제별로 모으고, 상위 Spec §7 위험의 현재 상태와 운영 전환 체크리스트를 정리한다. 원문 한계 문장은 [runbook/deploy-history.md](runbook/deploy-history.md)의 "알려진 제약"에서 그대로 옮기고 끝에 단계(예: `(P5b)`)를 붙였다. 지금 저장소 코드와 `infra/`가 사실의 기준이다.

> **합성 데이터 기준.** 모든 수치는 합성 데이터로 측정했고, 실제 고객·실제 성능을 나타내지 않는다. 수치는 `ops/reports/load-20261102110233/report.md`·`eval/reports/job-eval-9imbutm/report.md`와 배포·운영 실행 기록에서만 인용한다.

## 요약

- 실데이터·실사용자 대신 **모의 API와 합성 데이터**로 전 구간을 돌린 데모다(마이데이터·홈택스·PG는 Mock API).
- 콘솔 인증은 **데모 모드**(Easy Auth 꺼짐, WAF 운영자 IP 규칙만)이고 심사역 ID가 고정 값이라 감사 추적에 실제 신원이 없다([ADR-007](adr/ADR-007-demo-auth-mode.md)).
- 측정 표본이 작아(SLO 24건, 평가 케이스 소수) 수치는 경향으로만 읽고, 공정성 경보 하나는 표본 잡음일 수 있다.
- 동의·삭제·이의제기 수명주기는 구현했으나 갱신·재조정·회신은 기록·시뮬레이션에 그치고, 데모 시나리오에 동의 철회 장면은 없다.
- 운영 전환 과제(Entra 복원·공인 인증서·분산 서킷·다중 리전·모델 처리 위치)는 아래 체크리스트와 [아키텍처#운영 아키텍처](architecture.md#운영-아키텍처)에 모았다.
- 모든 LLM 호출은 SLA 없는 APIM Developer(AI 게이트웨이)를 지나고, 1만 자 초과 프롬프트에는 Prompt Shields가 적용되지 않는다. 직접 호출 대비 건당 AI 비용이 약 59% 늘었다([ADR-010](adr/ADR-010-ai-gateway.md)).

## 인증·접근

- 콘솔은 App Gateway WAF v2(Prevention, OWASP DRS 2.1) 뒤 internal 환경 `cae-int`에 있고 운영자 IP 규칙으로만 열린다([ADR-011](adr/ADR-011-appgw-waf-entry.md)). 남는 한계는 다음과 같다.
  - TLS는 자체 서명 인증서라 브라우저 경고가 뜬다. 공인 CA 인증서·사용자 도메인은 운영 전환 과제다.
  - 운영자 IP 규칙은 단일 `CONSOLE_IP`(`/32`)다. 다른 IP는 `window open --ip` 또는 WAF 규칙 조건 추가로만 허용하고, 다음 프로비저닝이 Bicep 정의로 되돌린다.
  - DRS 2.1 오탐은 조정하지 않았다. 차단되면 방화벽 로그로 규칙을 찾아 해당 규칙만 제외해야 한다.
  - 게이트웨이는 첫 `window open`에서 프로비저닝 → 인증서 → 두 번째 프로비저닝으로 만들어져 더 오래 걸린다.
  - 게이트웨이는 상시 비용이 든다(월 약 $370, [비용 모델](cost-model.md)).
- 배포 창 동안에는 운영자 IP에서 데이터 평면에 접근할 수 있다. `window status`로 확인하고 작업 후 바로 닫는다. (P2)
- 테넌트 정책(MCAPS)의 Modify 정책이 Key Vault·Cosmos·Storage의 `publicNetworkAccess`를 Disabled로, 로컬 인증을 끈다. 배포 창이 실제로 여는 것은 Foundry 계정·ACR·Functions뿐이고, 닫힌 서비스의 데이터 작업은 VNet 안 Job으로 한다(정책 우회 없음). (P2)
- 앱 비밀은 운영자 PC의 azd `.env`에 평문으로 있다. 운영 설계는 Key Vault 참조와 정기 교체다. (P3)
- Easy Auth는 클라이언트 비밀 없이 id_token(암시적 흐름)만 쓴다. 운영 설계는 인증 코드 흐름이다. (P4b)
- 데모 모드(`CONSOLE_AUTH_ENABLED=false`)에서는 WAF 운영자 IP 규칙만 진입을 막고, 결정 기록의 심사역 ID는 고정 값 `demo-operator`다. (P4b)
- 데모 인증(Easy Auth 꺼짐 + WAF 운영자 IP 규칙만)은 모든 결정을 고정 주체 `demo-operator`로 기록해 감사 추적에 실제 신원이 없다. 복원은 환경 변수로 끝나지 않고 `window open` → `deploy console` → `window close` 재배포(실측 약 12분, `docs/runbook/deploy-history.md:250`)와 테넌트 관리자 동의가 선행돼야 한다([ADR-007](adr/ADR-007-demo-auth-mode.md), `src/console/adapters.py:229`).
- AI Search는 키 인증을 유지하고(`disableLocalAuth: false`, `infra/modules/search.bicep:20`), Foundry·Functions는 배포 창 동안만 공용 접근을 연다(`docs/security/threat-model.md:75`).
- 운영 Job의 광범위 읽기는 `id-smoke`에 한정돼 있어 엄격한 최소 권한은 아니다(`docs/security/threat-model.md:80`, [ADR-008](adr/ADR-008-vnet-jobs-for-eval-ops.md)).
- 고객 신청 페이지(`/apply`)는 콘솔 역할 없이 같은 진입 통제(WAF 운영자 IP 규칙)만 받는다. 고객 본인 인증·사업자 실명 확인은 범위 밖이라 데모 셀러 20곳만 고를 수 있고, 진행 현황(`/apply/{id}`)은 신청번호만 알면 누구나 본다(고객 신청 건만, 내부 근거 없이). 운영 설계는 고객용 Entra External ID와 신청-사용자 바인딩이다. (P9)

## 데이터·외부 연동

- 실제 마이데이터·홈택스·PG API 대신 합성 데이터와 Mock API를 쓴다([아키텍처](architecture.md)).
- Mock API 동의 레지스트리는 메모리에 있어 복제본 1개로 운영한다. 재시작하면 발급된 동의가 사라진다. (P3)
- `request_consent` 재시도는 Mock에 동의를 중복 발급할 수 있다(멱등 키 없음). (P3)
- 사후 흐름은 합성 데이터이고, 재생은 실제 시간이 아니라 주차 단위로 압축된다. 연락은 실제로 발송하지 않고 기록만 한다. (P5a)
- 업종 집단 통계는 코드에 고정된 합성 표다. (P5a)
- 데모 데이터는 온라인 마켓플레이스 셀러(10개 업종)의 50만~500만 원 소액 운전자금 대출로 맞췄다. 금액·거래 규모는 이전 합성 데이터를 1/6로 줄인 값이라 판정 비율은 같고, 실제 셀러 정산 주기·수수료 구조는 반영하지 않는다. (P8 — P9에서 아래와 같이 대체)
- P9부터 상품 한도는 50만~1,000만 원·1~12개월이고, 데모 셀러 20곳(`src/shared/sellers.py`, 7개 업종: 원형별 건강 8·세금 불일치 4·스트레스 4·이력 부족 4, 사후 대출 중첩 4곳)은 채널별 정산 지연·수수료, 매입·급여·4대보험·원천세·부가세 일정을 가진 합성 데이터를 쓴다. 정산 일정은 근사치다: 명절·대체공휴일에 따른 정산일 이동은 없고, 종합소득세(5월)는 전망하지 않는다. (P9)
- 사후 13주 재생에서 재조정을 승인해도 유예·분할 상환 효과는 이후 주차 흐름과 4주 잔고 전망에 반영하지 않는다. 재생은 같은 시나리오로 이어진다. (P9)
- 콘솔 미리보기(`src/consolepreview`)의 지출 분류는 합성 데이터의 정답 레이블을 쓴다. 실제 심사에서는 분류 에이전트 결과를 쓴다. (P9)
- `source-flaky`가 켜진 동안에는 mockapi가 첫 요청마다 503을 주므로 console 차주 시뮬레이터(`/mydata/flow`) 재생이 실패한다. 측정 중에는 쓰지 않는다. (P6b)

## 에이전트·모델

- 새 Foundry 프로젝트에서 클래식 Assistants API는 모든 배포에 실패해, 프롬프트 에이전트와 `responses`(`agent_reference`)를 쓴다. (P2)
- `assessment` 문장은 가명 토큰(`[[PERSON_…]]`)을 그대로 담는다. 복원 표시는 심사역 콘솔이 한다. (P4a)
- 가명처리는 사전 기반이라 규칙 미분류 이체 적요에 든 제3자 이름이 모델에 그대로 갈 수 있어, 이체(`TRANSFER_IN/OUT`) 적요는 공유 캐시에 남기지 않는다. (P4a)
- 데모 데이터에서 규칙 미분류 적요는 공급업체 송금뿐이라 의미 분류가 특성을 바꾸지 않는다. (P4a)
- 수치 근거 검사는 숫자 일치만 본다. 해석의 타당성은 검토 에이전트 판정에 의존한다. (P4a)
- `demo-stress-003`은 고금리 대출이 감지되어도 정책상 eligible이고, 분석가는 이를 claim·counterfactual로 드러낸다. (P4a)
- `agent_assessment_mismatch` 플래그가 측정 신청 24/24건(장애 신청 4/5건)에 붙었다. 데이터 조립 에이전트가 보고한 상태가 결정론 판정과 다르다는 뜻이다(`docs/runbook/deploy-history.md:600`). 2026-11-07에 원인(입력이 동의 범위와 원천 이름을 따로 줘 모델이 profile·transactions를 범위 밖으로 오해)을 찾아 고쳤고, 수정 뒤 신청 19건(smoke 1·부하 8·재측정 부하 10)에서는 플래그가 없었다([에이전트](agents.md#데이터-수집)). 표본이 작아 재발 여부는 다음 부하 측정에서 다시 본다. 분류 모델 `decision-1`은 공개 단가를 확인하지 못해 비용에서 호출 수만 남고, 모델 처리 위치(Global Standard)는 [ADR-002](adr/ADR-002-model-deployment.md)에 정리했다. (P6b)
- Foundry 트레이스(`genAIContent`)에는 프롬프트·응답이 남아, 에이전트 입력이 가명화된 값뿐이라는 원칙에 기댄다. (P6b)
- AI 게이트웨이(APIM Developer)는 SLA가 없는 단일 인스턴스라 게이트웨이가 멈추면 모든 LLM 호출이 실패한다. 복구는 `AI_GATEWAY_ROUTING=false` 재프로비저닝(직접 호출)이다. ([ADR-010](adr/ADR-010-ai-gateway.md))
- Foundry 연결 범주 `ApiManagement`는 공식적으로 Standard v2·Premium 대상이고, Developer에서는 동작을 실측으로만 확인했다(2026-11-05). BYOM 연결은 프리뷰라 GA에서 바뀔 수 있다. (ADR-010)
- Prompt Shields는 10,000자를 넘는 프롬프트를 거부하므로, 정책이 메시지 길이 합(`promptChars`)이 10,000자 이하일 때만 `shield-prompt`를 켠다(오차단 수정 `f28057c`). 그보다 긴 프롬프트는 카테고리 검사와 모델 배포 필터 `Microsoft.DefaultV2`만 적용된다. 긴 입력의 탈옥 시도는 APIM에서 막히지 않는다. (ADR-010)
- Decision-1(decision API)에는 콘텐츠 안전을 걸지 않는다(분류 입력은 거래 적요뿐이고 Prompt Shields는 채팅 형식 대상). 평가 채점자(quality-judge)는 게이트웨이를 거치지 않고 직접 호출한다. (ADR-010)
- VNet에 연결한 `azure-api.net` 사설 DNS 영역이 같은 영역의 공용 이름을 가린다(VNet 안에서는 그 영역에 등록한 APIM 이름만 풀린다). (ADR-010)
- 게이트웨이 로그에는 LLM 요청·응답 본문과 헤더를 남기지 않는다(호출 수·상태·지연·토큰 지표만). 원인 분석은 지표와 Foundry 트레이스에 기댄다. (ADR-010)
- 콘솔 상세의 "AI 게이트웨이 · 콘텐츠 차단" 표시는 모델 배포 자체의 콘텐츠 필터 결과(400 `content_filter`·`incomplete_details`)도 `GatewayBlocked`로 분류하므로(`src/agents/runtime.py`), 배포 필터가 막은 건이 게이트웨이 차단으로 보일 수 있다. 막은 계층은 게이트웨이 로그로 따로 확인한다. (ADR-010)
- 평가(eval) 트래픽이 운영과 같은 에이전트별 TPM을 나눠 쓴다. 조기경보 평가 피크(1분 53.7K)는 한도 50K를 넘어 Agent Service 재시도가 흡수했다. 평가 전용 제품 분리는 후속 과제다. (ADR-010)
- `llm-token-limit`은 `estimate-prompt-tokens=false`라 한도를 넘긴 요청 자체는 통과하고 카운터가 넘은 **다음 호출부터** 429가 된다. 429가 나도 Agent Service가 1회, 클라이언트가 재시도한다. (ADR-010)
- 게이트웨이 경로는 같은 일을 하는 데 더 많은 토큰을 쓴다: 건당 AI 비용 $0.0589 → $0.0939(+59%), 분석가 TPM 150K·`gpt-5.4` 용량 200이 필요했다([비용 모델](cost-model.md#게이트웨이-경로-건당-ai-비용)). `collections-agent`·`grievance-reply-agent`의 TPM(10K·5K)은 측정하지 못한 여유값이다. (ADR-010)

## 동의·삭제·이의제기

- 동의 토큰이 활동 입력으로 Durable 기록(사설 Storage)에 남는다. 운영 설계는 토큰 대신 동의 ID로 조회한다. (P3)
- 동의 토큰은 계좌·세무·PG 세 범위를 **한 토큰으로 한꺼번에** 발급하며 범위별 개별 선택 UI는 없다(`docs/security/threat-model.md:120`, `src/orchestrator/journey/activities.py:176-177`).
- 데모 시나리오에는 동의 철회 장면이 없다(만료 처리로 삭제·증명을 보여준다)(`docs/security/threat-model.md:133`).
- 재조정 결정은 기록만 하고 상환 일정을 실제로 바꾸지 않는다. (P5a)
- 갱신은 안내만 하고 실제 연장(재동의)은 하지 않는다. 안내·회신 발송은 시뮬레이션이며 문서에만 남긴다. (P5b)
- 재심사 결과는 이의제기 문서에 기록만 하고 신청 문서나 대출을 바꾸지 않는다. (P5b)
- Mock 동의 레지스트리는 메모리에 있어 재시작 뒤 철회는 `absent`(이미 없음)로 기록된다. (P5b)
- 승인 뒤 심사 동의를 끝내면 그 대출의 사후 재생을 시작할 수 없다(프로필 원천 데이터가 지워짐). (P5b)
- 데이터 조립 중 철회가 오면 여정이 실패하고, 방금 쓴 원천 데이터를 지운 뒤(`late_cleanup`) 멈춘다. 철회 종료(`withdrawn`)는 심사역 대기 단계에서만 처리한다. (P5b)
- Durable 실행 기록은 지우지 않는다(동의 ID와 상태만 있고 개인정보 없음). (P5b)
- 삭제가 끝난 뒤 늦게 끝난 실행의 대화·컨테이너는 한 번만 지우고 `late_cleanup`에 기록한다. 그 삭제가 실패해도 증명은 완료로 남고 자동 재시도하지 않는다. (P5b)
- P5b 이전 동의는 명령이 올 때까지 수명주기가 없다. 이의제기 중복 확인과 저장은 원자적이지 않다(같은 ID 재전송만 막음). (P5b)

## 운영·관측

- App Insights 수집은 public 엔드포인트 + Entra 인증을 쓴다(Azure Monitor Private Link Scope 미사용). (P2)
- 콘솔 인증 흐름(실제 Entra 로그인 → 결정)은 자동 테스트가 없다. smoke는 로그인 없는 접근이 Entra로 리디렉션되는지만 본다. (P4b)
- 목록은 Cosmos 교차 파티션 `ORDER BY updated_at` 최근 50건이다(페이지 없음). (P4b)
- 콘솔이 보낸 결정의 결과는 상세 화면 새로 고침으로 확인한다. 큐 처리 실패는 오케스트레이터 로그에 남는다. (P4b)
- 인라인 모드는 13주를 한 번의 큐 트리거 안에서 처리한다(Flex Consumption 기본 제한 30분). (P5a)
- 서킷 브레이커는 Flex 인스턴스마다 따로다(분산 서킷은 운영 전환 과제). (P6b)
- Python Durable은 오케스트레이터 → 활동 트레이스를 잇지 않아, 신청 단위 연결은 `application_id` KQL 조인으로 한다. (P6b)
- 고정비는 리소스 그룹 비용 데이터가 있는 날만 평균한다. 이번 측정은 하루치라 일시 정지·배포 창 여닫기에 따라 크게 달라질 수 있다. (P6b)

## 측정 표본

- 컴포넌트 평가라 큐·Durable을 포함한 전 구간 지연은 재지 않는다(P6b App Insights에서 본다). (P6a)
- 반사실·주입은 케이스당 1회 실행이라 LLM 변동과 구분되지 않는다. (P6a)
- 분류 표본은 어려운 적요 뱅크를 넣어도 구성 간 차이가 작을 수 있다(천장 효과). (P6a)
- judge 점수는 LLM 평가라 참고값이며, 사람 검토 표본과 함께 본다. (P6a)
- PII 검사는 응답 봉투의 식별자 키와 서비스 식별자 문자열을 건너뛰므로 "PII 0건"은 이 검사 기준이다. (P6a)
- Foundry 포털 평가 업로드는 범위 밖이다. (P6a)
- 24건은 SLO 표본 수준이며 용량 시험이 아니다. p95·99%는 표본 수와 함께 본다. (P6b)
- P8에서 측정 격자를 48건(셀러 업종 6개 × 원형 4개 × 2건)으로 늘렸다. 이 문서의 24건 실측(SLO·비용·공정성)은 P8 이전 격자(오프라인 업종 3개)와 금액 기준이며, 48건 격자는 Azure 재배포 뒤 다시 측정한다. (P8)
- 이 격자에서 업종과 업력(業歷)은 독립이 아니다. 업종 6개 모두 8건씩이지만, R104(업력 12개월 미만)가 걸리는 업력 8개월 칸 비중은 업종별로 고르지 않다(패션의류·리빙 4/8, 식품 3/8, 디지털·뷰티 2/8, 반려동물 1/8). 그래서 업종 공정성 격차는 모델이 아니라 격자 구성에서 비롯될 수 있으니, P8 재측정의 업종 경보는 이를 감안해 읽는다. 실제로 `load-20261104041554`에서 업종 격차 25.0%p 경보가 났고, 8개월 칸이 가장 많은 리빙(12.5%)이 최저, 가장 적은 반려동물(37.5%)이 최고 쪽이었다. (P8)
- 건당 AI 비용은 캐시 할인 미반영(상한 추정)이고, 실패한 에이전트 호출은 토큰을 몰라 호출 수만 센다. (P6b)
- 원형 통제 격차는 원형·값당 2건이라 잡음이 크다. 경보 판정에는 전체 격차만 쓴다. (P6b)
- 지역 추천 approve율 격차 37.5%p 경보는 원형·값당 2건 균형 격자(24건)에서 나온 것이라 표본 잡음일 수 있어 조사 대상이다(`ops/reports/load-20261102110233/report.md` 3장, `docs/runbook/deploy-history.md:583`).
- 운영자·차주가 모델에 직접 지시하는 **직접 프롬프트 주입** 전용 테스트는 없고(간접 주입만 측정), "출력은 추천, 판정은 정책·심사역" 설계로만 완화한다(`docs/security/threat-model.md:99`).

## 배포 상태

- Function 의존성은 운영자 PC에서 Linux wheel로 받아 zip에 넣는다(원격 빌드 미사용). (P3)
- P6b 검토에서 발견한 I1 수정(Policy 앱의 ASGI `http send`/`http receive` 스팬이 App Insights에서 제외되지 않던 문제 — 제외용 환경변수가 동작하지 않던 설정)은 Azure에 배포됐고 반영을 확인했다. 2026-11-03 리허설에서 최근 45분 Policy 요청 2건(라이브 리허설 건 포함) 중 ` http send`/` http receive` 스팬이 0건이었다([리허설 기록](demo/rehearsal.md)). 표본이 2건이라 경향 확인 수준이다.
- Service Bus 모드 사후 재생은 2026-11-03 리허설 전까지 라이브에서 검증되지 않았고(그전 재생 실측은 모두 인라인 모드), 리허설에서 구독 규칙 결함을 발견해 고쳤다. Bicep이 `$Default` 이름으로 CorrelationFilter를 PUT하면 규칙이 남지 않고 자동 `1=1` 규칙도 사라져 두 구독(`flow.updated/early-warning`, `alert.raised/collections`)의 규칙이 0개가 되어 재생 메시지가 모두 버려졌다. `subject-label` 규칙을 추가한 수정(커밋 `8cfe7d1`) 뒤 첫 라이브 재생이 약 3.1분에 성공했다(인라인 모드 참고 실측 132초보다 김). 재개·프로비저닝 뒤 규칙 확인은 [배포](runbook/deploy.md#service-bus-구독-규칙-확인)에 있다.
- 재생 버튼을 두 번 누르면 `replay_in_progress` 확인이 원자적이지 않아 두 실행이 통과해 모니터링 동의가 2개 생기던 경쟁 조건은 `start_replay`의 ETag 조건부 대출 선점으로 고쳤다(커밋 `6c003a5`). 선점에 실패한 요청은 "동시 재생 요청 무시"로 끝난다. 그래도 데모에서는 재생 버튼을 한 번만 누른다.
- 운영자 공인 IP는 회사 NAT처럼 출구 IP가 여러 개로 회전할 수 있어(2026-11-03 하루 4개 관측) 바뀔 때마다 콘솔은 `window open --ip`로 `CONSOLE_IP`를 갱신하거나 WAF 규칙에 `<추가 IP>/32` 조건을 더하고, Functions에는 `/32` 임시 규칙을 하나씩 더해야 하고, 프로비저닝(`window open` 등)은 Functions의 임시 규칙을 지우므로 다시 추가해야 한다([배포](runbook/deploy.md#운영자-ip가-바뀔-때)). 모바일 회선에서는 App Service(Functions 주 사이트·SCM)로 가는 트래픽이 `api.ipify`가 알려 주는 IP와 다른 통신사 NAT 출구 IP(관측 3개, 서로 다른 /24)로 나가 창이 열려 있어도 `ops deploy orchestrator`가 403이었다. 이때는 임시 진단 설정으로 실제 출구 IP를 찾아 허용한다(같은 절 "App Service 출구 IP가 다를 때").
- 2분 데모 리허설은 1회(2026-11-03) 실시했으나 탐색 위주였고 2분 대본 시간 측정은 하지 못했다. 대본은 그 결과로 보완했고 2회차 리허설은 하지 않았다([리허설 기록](demo/rehearsal.md)).

## 상위 Spec 위험(§7)

상위 Spec `docs/superpowers/specs/2026-10-31-agentic-cashflow-lending-design.md` §7의 위험별 현재 상태:

| 위험(§7) | 현재 상태 |
|---|---|
| Global Standard 사용으로 국내 처리 제약 위반 | [ADR-002](adr/ADR-002-model-deployment.md)로 완화, 운영 전환 조건 명시(아래 체크리스트) |
| Korea Central 모델·도구 가용성 차이 | 실제 Azure 배포로 분류·심사·조기경보 경로 동작 확인(Code Interpreter 포함) |
| Decision-1 한국어 적요 분류 품질·배포 가능 여부 | 배포·호출 확인, 단 공개 단가 미확인으로 비용은 호출 수만 집계 |
| 전체 비공개 네트워크 배포 복잡도 | 배포 창 토글로 운영, 실행 기록은 [deploy-history](runbook/deploy-history.md) |
| 배포 창 동안 PaaS public access 일시 허용 | 운영자 IP만 허용하고 작업 후 닫음, Foundry·Functions만 열림(`docs/security/threat-model.md:75`) |
| 콘솔 외부 진입 | App Gateway WAF v2(Prevention, DRS 2.1)와 운영자 IP 규칙으로 구축([ADR-011](adr/ADR-011-appgw-waf-entry.md)). 데모 모드는 Easy Auth 꺼짐([ADR-007](adr/ADR-007-demo-auth-mode.md) 참조; 운영 전환 시 Entra 복원). 자체 서명 인증서·오탐 미조정은 위 "인증·접근"의 한계 |
| Decision-1 APAC 배포 문서·CLI 불일치 | 배포 SKU를 파라미터로 두고 배포 성공 |
| Foundry caphost 삭제 후 서브넷 점유 | teardown 스크립트가 순서·대기 처리 |
| Durable + 비공개 스토리지 조합 | 검증된 Durable 1.x Python + Azure Storage 백엔드로 배포 |
| 합성 데이터의 현실성 한계 | 수치는 "합성 데이터 기준"으로 표기, 외적 타당성 제한을 위 절들에 명시 |
| 4~6주 일정 | E2E 골격 우선 연결 후 단계적으로 깊이 추가(P2\~P6b 단계 진행) |

## 운영 전환 체크리스트

- [ ] Entra 로그인 복원: `CONSOLE_AUTH_ENABLED=true` + 재배포 + 앱 등록 + 테넌트 관리자 동의, 암시적 id_token 대신 인증 코드 흐름([ADR-007](adr/ADR-007-demo-auth-mode.md)).
- [ ] 공인 CA 인증서·사용자 도메인으로 게이트웨이 TLS 교체(자체 서명 경고 제거), WAF 운영자 IP 규칙 대신 Entra 단일 진입, DRS 오탐 조정([ADR-011](adr/ADR-011-appgw-waf-entry.md)).
- [ ] 모델 처리 위치: Global Standard → Data Zone 또는 PTU 전환([ADR-002](adr/ADR-002-model-deployment.md)).
- [ ] 분산 서킷 브레이커(인스턴스별 → 공유 상태).
- [ ] 다중 리전 DR(현재 단일 리전).
- [ ] 실데이터 투입 전 법률·규제 검토(실제 마이데이터·홈택스·PG 연동, 개인신용정보 처리).
- [ ] 공정성 표본 확대: 지역 approve율 37.5%p 경보가 표본 잡음인지 더 큰 표본으로 재측정.
- [x] `agent_assessment_mismatch`(측정 24/24) 원인 분석 — 2026-11-07 입력 문구 수정(`246d321`), 다음 부하 측정에서 재확인.
- [ ] APIM Standard v2 이상으로 전환(SLA, `ApiManagement` 연결 범주 공식 지원, [ADR-010](adr/ADR-010-ai-gateway.md)). 1만 자 초과 프롬프트 방어와 Decision-1 입력 방어도 이때 함께 본다.
- [ ] 평가 전용 APIM 제품 분리(평가 트래픽이 운영 TPM을 잠식하지 않게).

---

관련 문서: [아키텍처](architecture.md) · [비용 모델](cost-model.md) · [증거 색인](evidence.md) · [위협 모델](security/threat-model.md) · [ADR 색인](adr/README.md) · [운영 실행 기록](runbook/deploy-history.md)
