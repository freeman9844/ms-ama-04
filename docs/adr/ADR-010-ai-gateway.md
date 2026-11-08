# ADR-010: LLM 호출을 APIM AI 게이트웨이로

## 상태
채택(2026-11-05) · as-built

## 맥락
에이전트 8종과 거래 분류(Decision-1)가 Foundry 모델을 직접 호출해, 에이전트별 토큰 한도·콘텐츠 안전·호출 지표를 한곳에서 걸 수 없었다. Agent Service는 프롬프트 에이전트의 모델 호출을 BYOM 연결(ApiManagement 범주)로 APIM에 보낼 수 있다.

## 결정
- APIM Developer(내부 VNet 모드, `snet-apim`)를 게이트웨이로 두고, 에이전트마다 제품·구독·Foundry 연결(`gw-*`)을 하나씩 만든다.
- 정책 순서: 콘텐츠 안전(Prompt Shields) → 제품별 `llm-token-limit` → MI 인증 → `llm-emit-token-metric`(agent·model 차원).
- 앱은 `AI_GATEWAY=apim`이면 모델 이름을 `gw-<agent>/<model>`로 바꿔 호출한다(`GatewayRunner`). 채점자(quality-judge)는 직접 호출한다.
- 429·403은 `GatewayThrottled`·`GatewayBlocked`로 구분해 사용량 `gateway` 태그(`rate_limited`·`content_blocked`)로 남기고, 콘솔 상세 배지는 콘텐츠 차단(403)만 표시한다. 429는 태그와 게이트웨이 로그로만 본다.
- 예외: 채점자(quality-judge)는 직접 호출(운영 TPM을 쓰지 않음), Decision-1 decision API에는 콘텐츠 안전을 걸지 않는다(입력이 거래 적요뿐이고 Prompt Shields는 채팅 형식 대상).
- 롤백은 `AI_GATEWAY_ROUTING=false` 재프로비저닝 한 번이다.

## 검토한 대안
| 대안 | 장점 | 기각 이유 |
|------|------|-----------|
| 직접 호출 유지 | 구성 단순, 비용 0 | 에이전트별 한도·차단·지표를 앱 코드마다 구현해야 함 |
| APIM Standard v2 / Premium | SLA, `ApiManagement` 범주 공식 지원 | 월 비용이 데모 예산을 넘음. 운영 전환 시 채택 |
| 앱 안 레이트 리미터 | 추가 리소스 없음 | 인스턴스별 상태라 전역 한도 아님, 콘텐츠 안전 없음 |
| APIM을 Private Endpoint로 노출(foundry-samples 템플릿 16) | 서브넷·NSG 불필요 | Developer 티어는 인바운드 PE 미지원, 내부 모드가 기존 VNet 설계(ADR-004)와 맞음 |
| Decision-1과 지표만 APIM 경유 | BYOM 프리뷰 의존 없음 | 에이전트별 한도·콘텐츠 안전이 빠져 목적을 못 이룸(관문 실패 시 대안) |

## 결과
실측은 모두 합성 데이터 기준이다. 도입 구현은 커밋 `834c07f`~`d451d34`(브랜치 `ai-gateway-apim`)다.

**관문(2026-11-05, 라우팅 꺼진 상태에서 먼저 확인)**
- APIM Developer(내부 모드) 프로비저닝 27분 19초. `gw-*` 연결 9개가 `ApiManagement` 범주로 그대로 만들어져 `ModelGateway` 범주 대체 경로는 쓰지 않았다.
- 관문 1(호출 경로): `gw-reviewer/gpt-5.4-mini`로 function tool + code_interpreter + json_schema 응답이 `completed`로 끝났다. Agent Service는 `POST /openai/deployments/<모델>/chat/completions`(chat completions, `/responses` 아님)로 APIM을 부른다.
- 관문 2(429): HTTP 429, `retry-after` 53초, `error.code=rate_limit_exceeded`, 메시지 "Token limit is exceeded". Agent Service가 429를 내부에서 1회 재시도해 APIM에는 2건이 찍혔다.
- 관문 3(차단): 콘텐츠 안전 차단은 클라이언트에 **403이 아니라 HTTP 424**(`model_error`, `CustomerManagedDownstreamError … 'innerStatusCode': 403`)로 온다. APIM 쪽은 81ms에 백엔드 전 차단이었다. 그래서 `BLOCK_MARKERS`에 `'innerstatuscode': 403`을 더했다(429 마커는 관문 2에서 본 문구 `token limit is exceeded`·`rate limit exceeded`로 좁혔다, `e57f346`).
- 처음에는 `ApiManagementGatewayLogs`가 비어 있어 Log Analytics 진단 설정을 더했고(`d61cd5e`), 이후 채워진다. 요청·응답 본문과 헤더는 기록하지 않는다.

**프로브 재현(`ops gateway probe`)**
- 콘텐츠 차단: `job-eval-aw9qy70`에서 APIM 403(75ms)이 재현됐다. 조건부 Prompt Shields 수정(`f28057c`) 뒤에 unsafe 프로브를 다시 돌린 `job-eval-3hh7xn5`(2026-11-05)도 `content_blocked`, `reproduced=true`라서, 1만 자 이하 조건을 더한 뒤에도 Prompt Shields가 계속 차단함을 확인했다.
- 토큰 한도: 첫 시도(`job-eval-zimhc4h`)는 재현하지 못했다. 클라이언트 429 재시도(MeteredTransport 최대 3회 + FoundryAgents 1회)가 슬라이딩 창이 풀릴 때까지 기다려 시도당 APIM 429가 약 11회(65초) 찍힌 뒤 200이 돌아왔다. 프로브 모드에서만 클라이언트 429 재시도를 끄도록 고쳐(`f9eaeb1`, `retry_throttle=False`) `job-eval-5zu5jnw`에서 `ok → rate_limited`가 재현됐다. 운영 재시도는 그대로다.

**Prompt Shields 10,000자 한도(`f28057c`)**
- Prompt Shields는 10,000자를 넘는 사용자 프롬프트를 거부한다. 평가 `job-eval-ba66ts9`에서 긴 프롬프트가 APIM 403 `ContentSafetyPolicyViolated`로 막혔다(콘텐츠 차단 7건, 정상 요청의 오차단).
- 수정: openai 정책이 메시지 본문 길이 합 `promptChars`를 계산해 `promptChars <= 10000`일 때만 `shield-prompt`를 켠다. 넘으면 카테고리 검사만 돌고, 모델 배포의 콘텐츠 필터 `Microsoft.DefaultV2`가 계속 걸러 준다. 즉 **긴 프롬프트의 탈옥 시도는 APIM이 아니라 모델 필터에서만 막힌다**. 수정 뒤 전체 평가 `job-eval-aqisk13`의 게이트웨이 줄은 "콘텐츠 차단 0건, 토큰 한도 0건"이다.

**도구 라운드(`f43de5d`)**
- 게이트웨이(chat completions) 경로에서는 모델이 턴당 도구 호출을 덜 해서, 조기경보 조사 에이전트가 모든 주차에서 6라운드 상한에 걸렸다(`job-eval-aqisk13`: 양성 경로 에이전트 오류 6건, 직접 경로 평균 4.59회). 라우팅하는 에이전트(`GatewayRunner`, `EvalRunner`)에만 `GATEWAY_MAX_ROUNDS = 12`를 적용하고 직접 경로는 `max_rounds=6`을 유지한다. 대가는 게이트웨이 경로에서 조사 한 번에 최대 2배의 도구 라운드·토큰이다.

**에이전트별 TPM과 모델 용량(최종값, `infra/resources.bicep` `gateways` 표·`infra/main.bicep`)**
| 연결 | TPM | 근거 |
|---|---|---|
| `gw-analyst` | 150,000 | 48건 부하 피크 147.7K. 20K → 50K(`f728948`) → 100K(`c05b1b5`) → 150K(`d451d34`) |
| `gw-early-warning` | 100,000 | 평가 1분 피크 53.7K는 50K에서 Agent Service 재시도가 흡수했다. 상환 기반 추심(2026-11-08) 뒤 인라인 재생이 경보에서 멈추지 않아 13주 조사가 몇 분 안에 몰려 50K에서 429 16건(78회 중)과 조사 폴백 1건이 나서 100K로 올렸다(`gpt-5.4` 배포 200K 안) |
| `gw-reviewer` | 15,000 | 부하 피크 15.4K, 흡수된 429 14건, SLO 영향 없음 |
| `gw-data-assembly` | 17,000 | 피크 13.2K(15K → 17K) |
| `gw-txn-classifier` | 20,000 | 평가 피크 약 10.0K의 2배, 부하 피크 3.8K |
| `gw-collections` | 10,000 | 미측정(경로 미발생), 기본 여유값 |
| `gw-consent-notice` | 5,000 | 피크 0.7K |
| `gw-grievance` | 5,000 | 미측정(경로 미발생), 기본 여유값 |
| `gw-probe` | 1,000 | 프로브 전용 |

- `gpt-5.4` GlobalStandard 배포 용량을 50 → 200(1K TPM 단위, 쿼터 250)으로 올렸다. APIM 한도가 모델 용량보다 크면 의미가 없기 때문이다. 종량제라 고정비 변화는 없다.
- 부하 48건 SLO2(에이전트 단계 성공률)는 분석가 한도에 따라 50K에서 92.4%(429 236건, 피크 59.7K), 100K에서 98.6%(429 56건, 피크 110.6K), 150K에서 100%(144/144)로 올랐다.
- Decision API의 백엔드 429(평가 중 14건)는 게이트웨이 정책이 아니라 모델 배포 용량 문제라 그대로 두었고 평가 재시도가 흡수한다.

**평가·SLO·비용 비교(직접 호출 기준선 P9와 게이트웨이 최종)**
| 항목 | P9 직접 호출 | 게이트웨이(최종) |
|---|---|---|
| 부하 실행 | `load-20261104143855` | `load-20261105120255` |
| SLO1 p95 | 1.4분 | 1.8분 |
| SLO2 | 100%(144/144) | 100%(144/144) |
| 건당 AI 비용 | $0.0589 | $0.0939(약 +59%) |
| 조기경보 평가 precision/recall | 1.0/1.0 (`job-eval-zjmcwjv`) | 1.0/1.0 (`job-eval-adcwjhc`, mini·gpt-5.4 모두), 실패 1건(gpt-5.4 `ev-etax_k`가 탐지기 단독 경보로 폴백), $1.84 |

- 비용이 오른 이유: 게이트웨이 경로는 라운드마다 전체 맥락을 다시 보내고 라운드 수도 늘어, 부하 한 번에 `gpt-5.4` 입력이 1.16M 토큰이었다. 전체 평가 `job-eval-aqisk13`는 $2.11(콘텐츠 차단·토큰 한도 0건)이다.
- 지표: AppMetrics `Total Tokens`(`Properties.agent`, `model`)로 에이전트별 1분 피크를 본다. 최종 부하 피크는 분석가 147.7K, 검토자 15.4K, 데이터 수집 13.2K, 분류 3.8K다.

**기타**
- 고정비 약 $51/월 추가. 공인 IP 없음.
- LLM 요청·응답 본문은 게이트웨이 로그에 남기지 않는다.
- 한계: Developer 티어는 SLA가 없고 `ApiManagement` 범주는 공식적으로 Standard v2·Premium 대상이다. 자세한 항목은 [한계와 운영 전환](../limitations.md#에이전트모델)에 있다.

## 재검토 조건
- 운영 전환(SLA 필요) → Standard v2 또는 Premium.
- BYOM 연결이 GA에서 바뀌거나 `ApiManagement` 범주가 Developer에서 막힐 때.
- 평가 트래픽이 운영 TPM을 눈에 띄게 잠식할 때(평가 전용 제품 분리).

## 근거
- 설계: `docs/superpowers/specs/2026-11-05-ai-gateway-apim-design.md`
- 구현: `infra/modules/apim.bicep`, `infra/modules/foundry/gateway-connections.bicep`, `src/agents/gateway.py`
- 기록: [../runbook/deploy-history.md](../runbook/deploy-history.md)의 "AI 게이트웨이(APIM) 도입" 절
- 산출물: `eval/reports/job-eval-aw9qy70`, `eval/reports/job-eval-5zu5jnw`, `eval/reports/job-eval-aqisk13`, `eval/reports/job-eval-adcwjhc`, `ops/reports/load-20261105120255`
