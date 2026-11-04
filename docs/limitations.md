# 한계와 운영 전환

이 문서는 데모의 **as-built 한계**를 주제별로 모으고, 상위 Spec §7 위험의 현재 상태와 운영 전환 체크리스트를 정리한다. 원문 한계 문장은 [runbook/deploy-history.md](runbook/deploy-history.md)의 "알려진 제약"에서 그대로 옮기고 끝에 단계(예: `(P5b)`)를 붙였다. 지금 저장소 코드와 `infra/`가 사실의 기준이다.

> **합성 데이터 기준.** 모든 수치는 합성 데이터로 측정했고, 실제 고객·실제 성능을 나타내지 않는다. 수치는 `ops/reports/load-20261102110233/report.md`·`eval/reports/job-eval-9imbutm/report.md`와 배포·운영 실행 기록에서만 인용한다.

## 요약

- 실데이터·실사용자 대신 **모의 API와 합성 데이터**로 전 구간을 돌린 데모다(마이데이터·홈택스·PG는 Mock API).
- 콘솔 인증은 **데모 모드**(Easy Auth 꺼짐, 운영자 IP 허용목록만)이고 심사역 ID가 고정 값이라 감사 추적에 실제 신원이 없다([ADR-007](adr/ADR-007-demo-auth-mode.md)).
- 측정 표본이 작아(SLO 24건, 평가 케이스 소수) 수치는 경향으로만 읽고, 공정성 경보 하나는 표본 잡음일 수 있다.
- 동의·삭제·이의제기 수명주기는 구현했으나 갱신·재조정·회신은 기록·시뮬레이션에 그치고, 데모 시나리오에 동의 철회 장면은 없다.
- 운영 전환 과제(Entra 복원·App Gateway WAF·분산 서킷·다중 리전·모델 처리 위치)는 아래 체크리스트와 [아키텍처#운영 아키텍처](architecture.md#운영-아키텍처)에 모았다.

## 인증·접근

- 콘솔은 WAF 없이 external Container Apps 환경에 둔다(운영자 IP 허용 + Entra 인증). 운영 설계는 App Gateway WAF다. (P2)
- 배포 창 동안에는 운영자 IP에서 데이터 평면에 접근할 수 있다. `window status`로 확인하고 작업 후 바로 닫는다. (P2)
- 테넌트 정책(MCAPS)의 Modify 정책이 Key Vault·Cosmos·Storage의 `publicNetworkAccess`를 Disabled로, 로컬 인증을 끈다. 배포 창이 실제로 여는 것은 Foundry 계정·ACR·Functions뿐이고, 닫힌 서비스의 데이터 작업은 VNet 안 Job으로 한다(정책 우회 없음). (P2)
- 앱 비밀은 운영자 PC의 azd `.env`에 평문으로 있다. 운영 설계는 Key Vault 참조와 정기 교체다. (P3)
- Easy Auth는 클라이언트 비밀 없이 id_token(암시적 흐름)만 쓴다. 운영 설계는 인증 코드 흐름과 internal 환경 + Application Gateway WAF다. (P4b)
- 데모 모드(`CONSOLE_AUTH_ENABLED=false`)에서는 IP 허용 목록만 진입을 막고, 결정 기록의 심사역 ID는 고정 값 `demo-operator`다. (P4b)
- 데모 인증(Easy Auth 꺼짐 + IP 허용목록만)은 모든 결정을 고정 주체 `demo-operator`로 기록해 감사 추적에 실제 신원이 없다. 복원은 환경 변수로 끝나지 않고 `window open` → `deploy console` → `window close` 재배포(실측 약 12분, `docs/runbook/deploy-history.md:250`)와 테넌트 관리자 동의가 선행돼야 한다([ADR-007](adr/ADR-007-demo-auth-mode.md), `src/console/adapters.py:229`).
- AI Search는 키 인증을 유지하고(`disableLocalAuth: false`, `infra/modules/search.bicep:20`), Foundry·Functions는 배포 창 동안만 공용 접근을 연다(`docs/security/threat-model.md:74`).
- 운영 Job의 광범위 읽기는 `id-smoke`에 한정돼 있어 엄격한 최소 권한은 아니다(`docs/security/threat-model.md:79`, [ADR-008](adr/ADR-008-vnet-jobs-for-eval-ops.md)).
- 고객 신청 페이지(`/apply`)는 콘솔 역할 없이 같은 진입 통제(데모 모드면 IP 허용 목록)만 받는다. 고객 본인 인증·사업자 실명 확인은 범위 밖이라 데모 셀러 5곳만 고를 수 있고, 진행 현황(`/apply/{id}`)은 신청번호만 알면 누구나 본다(고객 신청 건만, 내부 근거 없이). 운영 설계는 고객용 Entra External ID와 신청-사용자 바인딩이다. (P9)

## 데이터·외부 연동

- 실제 마이데이터·홈택스·PG API 대신 합성 데이터와 Mock API를 쓴다([아키텍처](architecture.md)).
- Mock API 동의 레지스트리는 메모리에 있어 복제본 1개로 운영한다. 재시작하면 발급된 동의가 사라진다. (P3)
- `request_consent` 재시도는 Mock에 동의를 중복 발급할 수 있다(멱등 키 없음). (P3)
- 사후 흐름은 합성 데이터이고, 재생은 실제 시간이 아니라 주차 단위로 압축된다. 연락은 실제로 발송하지 않고 기록만 한다. (P5a)
- 업종 집단 통계는 코드에 고정된 합성 표다. (P5a)
- 데모 데이터는 온라인 마켓플레이스 셀러(10개 업종)의 50만~500만 원 소액 운전자금 대출로 맞췄다. 금액·거래 규모는 이전 합성 데이터를 1/6로 줄인 값이라 판정 비율은 같고, 실제 셀러 정산 주기·수수료 구조는 반영하지 않는다. (P8)
- `source-flaky`가 켜진 동안에는 mockapi가 첫 요청마다 503을 주므로 console 차주 시뮬레이터(`/mydata/flow`) 재생이 실패한다. 측정 중에는 쓰지 않는다. (P6b)

## 에이전트·모델

- 새 Foundry 프로젝트에서 클래식 Assistants API는 모든 배포에 실패해, 프롬프트 에이전트와 `responses`(`agent_reference`)를 쓴다. (P2)
- `assessment` 문장은 가명 토큰(`[[PERSON_…]]`)을 그대로 담는다. 복원 표시는 심사역 콘솔이 한다. (P4a)
- 가명처리는 사전 기반이라 규칙 미분류 이체 적요에 든 제3자 이름이 모델에 그대로 갈 수 있어, 이체(`TRANSFER_IN/OUT`) 적요는 공유 캐시에 남기지 않는다. (P4a)
- 데모 데이터에서 규칙 미분류 적요는 공급업체 송금뿐이라 의미 분류가 특성을 바꾸지 않는다. (P4a)
- 수치 근거 검사는 숫자 일치만 본다. 해석의 타당성은 검토 에이전트 판정에 의존한다. (P4a)
- `demo-stress-003`은 고금리 대출이 감지되어도 정책상 eligible이고, 분석가는 이를 claim·counterfactual로 드러낸다. (P4a)
- `agent_assessment_mismatch` 플래그가 측정 신청 24/24건(장애 신청 4/5건)에 붙었다. 데이터 조립 에이전트가 보고한 상태가 결정론 판정과 다르다는 뜻으로, 원인 분석은 후속 과제다(`docs/runbook/deploy-history.md:600`). 분류 모델 `decision-1`은 공개 단가를 확인하지 못해 비용에서 호출 수만 남고, 모델 처리 위치(Global Standard)는 [ADR-002](adr/ADR-002-model-deployment.md)에 정리했다. (P6b)
- Foundry 트레이스(`genAIContent`)에는 프롬프트·응답이 남아, 에이전트 입력이 가명화된 값뿐이라는 원칙에 기댄다. (P6b)

## 동의·삭제·이의제기

- 동의 토큰이 활동 입력으로 Durable 기록(사설 Storage)에 남는다. 운영 설계는 토큰 대신 동의 ID로 조회한다. (P3)
- 동의 토큰은 계좌·세무·PG 세 범위를 **한 토큰으로 한꺼번에** 발급하며 범위별 개별 선택 UI는 없다(`docs/security/threat-model.md:119`, `src/orchestrator/journey/activities.py:176-177`).
- 데모 시나리오에는 동의 철회 장면이 없다(만료 처리로 삭제·증명을 보여준다)(`docs/security/threat-model.md:132`).
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
- 운영자·차주가 모델에 직접 지시하는 **직접 프롬프트 주입** 전용 테스트는 없고(간접 주입만 측정), "출력은 추천, 판정은 정책·심사역" 설계로만 완화한다(`docs/security/threat-model.md:98`).

## 배포 상태

- Function 의존성은 운영자 PC에서 Linux wheel로 받아 zip에 넣는다(원격 빌드 미사용). (P3)
- P6b 검토에서 발견한 I1 수정(Policy 앱의 ASGI `http send`/`http receive` 스팬이 App Insights에서 제외되지 않던 문제 — 제외용 환경변수가 동작하지 않던 설정)은 Azure에 배포됐고 반영을 확인했다. 2026-11-03 리허설에서 최근 45분 Policy 요청 2건(라이브 리허설 건 포함) 중 ` http send`/` http receive` 스팬이 0건이었다([리허설 기록](demo/rehearsal.md)). 표본이 2건이라 경향 확인 수준이다.
- Service Bus 모드 사후 재생은 2026-11-03 리허설 전까지 라이브에서 검증되지 않았고(그전 재생 실측은 모두 인라인 모드), 리허설에서 구독 규칙 결함을 발견해 고쳤다. Bicep이 `$Default` 이름으로 CorrelationFilter를 PUT하면 규칙이 남지 않고 자동 `1=1` 규칙도 사라져 두 구독(`flow.updated/early-warning`, `alert.raised/collections`)의 규칙이 0개가 되어 재생 메시지가 모두 버려졌다. `subject-label` 규칙을 추가한 수정(커밋 `8cfe7d1`) 뒤 첫 라이브 재생이 약 3.1분에 성공했다(인라인 모드 참고 실측 132초보다 김). 재개·프로비저닝 뒤 규칙 확인은 [배포](runbook/deploy.md#service-bus-구독-규칙-확인)에 있다.
- 재생 버튼을 두 번 누르면 `replay_in_progress` 확인이 원자적이지 않아 두 실행이 통과해 모니터링 동의가 2개 생기던 경쟁 조건은 `start_replay`의 ETag 조건부 대출 선점으로 고쳤다(커밋 `6c003a5`). 선점에 실패한 요청은 "동시 재생 요청 무시"로 끝난다. 그래도 데모에서는 재생 버튼을 한 번만 누른다.
- 운영자 공인 IP는 회사 NAT처럼 출구 IP가 여러 개로 회전할 수 있어(2026-11-03 하루 4개 관측) 바뀔 때마다 console ingress와 Functions에 `/32` 임시 규칙을 하나씩 더해야 하고, 프로비저닝(`window open` 등)은 Functions의 임시 규칙을 지우므로 다시 추가해야 한다([배포](runbook/deploy.md#운영자-ip가-바뀔-때)).
- 2분 데모 리허설은 1회(2026-11-03) 실시했으나 탐색 위주였고 2분 대본 시간 측정은 하지 못했다. 대본은 그 결과로 보완했고 2회차 리허설은 하지 않았다([리허설 기록](demo/rehearsal.md)).

## 상위 Spec 위험(§7)

상위 Spec `docs/superpowers/specs/2026-10-31-agentic-cashflow-lending-design.md` §7의 위험별 현재 상태:

| 위험(§7) | 현재 상태 |
|---|---|
| Global Standard 사용으로 국내 처리 제약 위반 | [ADR-002](adr/ADR-002-model-deployment.md)로 완화, 운영 전환 조건 명시(아래 체크리스트) |
| Korea Central 모델·도구 가용성 차이 | 실제 Azure 배포로 분류·심사·조기경보 경로 동작 확인(Code Interpreter 포함) |
| Decision-1 한국어 적요 분류 품질·배포 가능 여부 | 배포·호출 확인, 단 공개 단가 미확인으로 비용은 호출 수만 집계 |
| 전체 비공개 네트워크 배포 복잡도 | 배포 창 토글로 운영, 실행 기록은 [deploy-history](runbook/deploy-history.md) |
| 배포 창 동안 PaaS public access 일시 허용 | 운영자 IP만 허용하고 작업 후 닫음, Foundry·Functions만 열림(`docs/security/threat-model.md:74`) |
| 콘솔 외부 ingress(App Gateway 없음) | 운영자 IP 허용목록으로 완화(데모 모드는 Easy Auth 꺼짐, [ADR-007](adr/ADR-007-demo-auth-mode.md) 참조; 운영 전환 시 Entra 복원), App Gateway WAF는 문서로만 기술(운영 전환 과제) |
| Decision-1 APAC 배포 문서·CLI 불일치 | 배포 SKU를 파라미터로 두고 배포 성공 |
| Foundry caphost 삭제 후 서브넷 점유 | teardown 스크립트가 순서·대기 처리 |
| Durable + 비공개 스토리지 조합 | 검증된 Durable 1.x Python + Azure Storage 백엔드로 배포 |
| 합성 데이터의 현실성 한계 | 수치는 "합성 데이터 기준"으로 표기, 외적 타당성 제한을 위 절들에 명시 |
| 4~6주 일정 | E2E 골격 우선 연결 후 단계적으로 깊이 추가(P2\~P6b 단계 진행) |

## 운영 전환 체크리스트

- [ ] Entra 로그인 복원: `CONSOLE_AUTH_ENABLED=true` + 재배포 + 앱 등록 + 테넌트 관리자 동의, 암시적 id_token 대신 인증 코드 흐름([ADR-007](adr/ADR-007-demo-auth-mode.md)).
- [ ] internal 환경 앞 Application Gateway(WAF v2) + Entra 단일 진입(월 약 $365, 상위 Spec §2.2).
- [ ] 모델 처리 위치: Global Standard → Data Zone 또는 PTU 전환([ADR-002](adr/ADR-002-model-deployment.md)).
- [ ] 분산 서킷 브레이커(인스턴스별 → 공유 상태).
- [ ] 다중 리전 DR(현재 단일 리전).
- [ ] 실데이터 투입 전 법률·규제 검토(실제 마이데이터·홈택스·PG 연동, 개인신용정보 처리).
- [ ] 공정성 표본 확대: 지역 approve율 37.5%p 경보가 표본 잡음인지 더 큰 표본으로 재측정.
- [ ] `agent_assessment_mismatch`(측정 24/24) 원인 분석.
- [ ] Content Safety Prompt Shields 등 입력 방어 도입(as-built 미구현, `docs/security/threat-model.md:95`).

---

관련 문서: [아키텍처](architecture.md) · [비용 모델](cost-model.md) · [증거 색인](evidence.md) · [위협 모델](security/threat-model.md) · [ADR 색인](adr/README.md) · [운영 실행 기록](runbook/deploy-history.md)
