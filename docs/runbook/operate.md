# 운영 (operate)

배포된 데모 환경을 평소에 다루는 사람을 위한 안내입니다. 명령은 `src/ops/cli.py` 기준이며, 아래 표·명령 블록 일부는 README 원문을 그대로 옮겼습니다.

## 일시 정지·재개

| 명령 | 용도 |
|------|------|
| `python -m ops window open [--ip <IP>] [--acr]` | 배포 창 열기: 운영자 공인 IP만 허용(`--acr`은 ACR 빌드 창). 실측 11.0분(P2, Cosmos 갱신이 약 8분; 최근 실측은 [deploy](deploy.md#배포-창)) |
| `python -m ops window status` | 창 상태 확인. 공인 IP가 바뀌었으면 경고(종료 코드 2) |
| `python -m ops window close` | 배포 창 닫기(작업 후 반드시 실행). 실측 10.8분(P2) |
| `python -m ops deploy [policy\|mockapi\|console\|eval\|orchestrator\|all]` | 앱 배포(P3·P4b): 정책·Mock·콘솔·평가(`eval`) 이미지 ACR Build → 프로비저닝, Function zip. `window open --acr` 필요 |
| `python -m ops smoke` | VNet 안 smoke Job 실행 + 운영자 PC에서 콘솔 로그인 관문 확인(P4b) |
| `python -m ops pause` / `resume` | Service Bus Premium 삭제/복구(비용 절감). 실측 pause 5.8분, resume 4.7분(P2) |
| `python -m ops teardown --confirm rg-amalend` | capability host → Foundry 계정 purge → 리소스 그룹 삭제 → Key Vault purge |

teardown 뒤 같은 azd 환경으로 다시 만들 때는 **다른 `ops` 명령보다 먼저 `azd provision --no-prompt`를 실행**하세요.
azd 환경에 이전 `AI_ACCOUNT_NAME`이 남아 있어, `ops window/pause/resume`을 먼저 실행하면 `DEPLOY_CAPHOST`가
`false`로 바뀌고 새 환경에 capability host가 만들어지지 않습니다.

- `python -m ops pause`: azd 환경의 `SERVICEBUS_ENABLED`를 `false`로 바꾸고 Service Bus 사설 엔드포인트와 네임스페이스를 지운 뒤 프로비저닝으로 출력값을 갱신합니다(`src/ops/lifecycle.py`). 이미 없으면 "이미 없습니다"라고만 알립니다.
- `python -m ops resume`: `SERVICEBUS_ENABLED`를 `true`로 바꾸고 프로비저닝해 Service Bus를 다시 만듭니다. 재개 뒤에는 구독 규칙이 `subject-label`인지 확인합니다([deploy](deploy.md#service-bus-구독-규칙-확인)).
- Service Bus가 없는 동안 오케스트레이터는 인라인 모드로 동작합니다(이벤트 발행 생략, `servicebus_disabled` 기록).
- 실측: pause 5.8분·resume 4.7분(P2), pause 6.0분·resume 4.5분(P4b) — `docs/runbook/deploy-history.md`의 "운영 명령" 표와 "실제 배포 기록(P4b…)" 표.

## smoke

`python -m ops smoke`는 VNet 안 smoke Job을 실행하고(`src/ops/smoke_checks.py`의 `build_checks`), 이어서 운영자 PC에서 콘솔 관문을 확인합니다. Job의 점검 항목은 다음 순서입니다.

| 점검 이름 | 함수 | 확인하는 것 |
|-----------|------|-------------|
| `dns:<호스트>` | `check_private_dns` | 서비스 호스트가 사설 주소로 풀리는지 |
| `chat:<모델>` | `check_chat` | 배포된 채팅 모델에 짧은 요청 |
| `blob` | `check_blob` | 데이터 Storage의 Blob 컨테이너 목록 조회 |
| `cosmos` | `check_cosmos` | Cosmos DB 데이터베이스 목록 조회 |
| `keyvault` | `check_keyvault` | Key Vault 비밀 목록 조회 |
| `health:policy` | `check_health` | 정책 엔진 상태 |
| `health:mockapi` | `check_health` | Mock API 상태 |
| `servicebus` | `check_servicebus` | Service Bus 토픽에 메시지 전송(Service Bus가 켜져 있을 때만) |
| `agent` | `check_agent` | Foundry 프롬프트 에이전트 생성·호출·삭제(capability host 경로 포함) |
| `decision` | `check_decision` | Decision-1(`systemone`) 질문 1건 |
| `journey` | `check_journey` | 신청 → 심사역 대기 → 승인 → 모니터링 등록까지 신청 여정 |
| `consent` | `check_consent_deletion` | 동의 만료 → 삭제 증명 완료, 신청 문서 보존 |

- 점검 개수는 모델·호스트·Service Bus 상태에 따라 달라집니다. 실측: 24/24 통과(P5b, `docs/runbook/deploy-history.md`의 "실제 배포 기록(P5b…)" 표).
- `journey`는 실행마다 `smoke-<UTC 날짜>-<시각>` 신청을 만들고 Cosmos에 남깁니다.
- 콘솔 관문은 `src/ops/console_gate.py`가 확인합니다(운영자 IP가 `CONSOLE_IP`와 다르면 403).
- 실패 원인별 대응은 [troubleshooting](troubleshooting.md)을 봅니다.

## 평가(eval)

- **결과**: `eval/reports/<실행 이름>/`의 `summary.json`(전체 수치), `report.md`(한국어 리포트, 실패 유형 카탈로그, 사람 검토 표본, 한계),
  `calls.jsonl`(호출 계측). Job은 같은 파일을 Blob `eval-reports/<실행 이름>/`에도 올립니다.

```powershell
python -m evals --fake --out $env:TEMP\eval-fake    # 로컬 회귀(모델 호출 없음, 같은 형식의 리포트)
python -m ops window open --acr                     # 평가 이미지 빌드에만 필요
python -m ops deploy eval                           # az acr build → SERVICE_EVAL_IMAGE_NAME → provision(job-eval 갱신)
python -m ops window close
python -m ops eval --suite all                      # Job 시작 → 종료 대기 → Log Analytics에서 리포트 복원 → eval/reports/
python -m ops eval --collect <실행 이름>            # 회수만 다시(로그 수집 지연으로 복원 실패했을 때)
python -m ops gateway probe --mode throttle         # AI 게이트웨이 토큰 한도(429) 재현 → eval/reports/<실행>/probe.json
python -m ops gateway probe --mode unsafe           # 콘텐츠 차단(403) 재현. 재현 안 되면 종료 코드 2
```

- 산출물은 `eval/reports/<실행 이름>/`에 저장됩니다. 실측 실행 하나는 `eval/reports/job-eval-9imbutm/report.md`입니다.
- `ops eval --suite`의 값은 `all`, `classify`, `underwriting`, `early_warning`, `safety`입니다(`src/ops/cli.py`의 `EVAL_SUITES`).
- 결과 해석과 한계는 리포트의 한계 절과 [deploy-history](deploy-history.md)의 P6a 절을 봅니다.
- `ops gateway probe`는 eval Job으로 프로브를 돌려 AI 게이트웨이 동작을 확인합니다(`--mode all|throttle|unsafe`, 기본 `all`; `--collect <실행>`으로 끝난 결과만 회수). 프로브 모드에서는 클라이언트 429 재시도를 꺼서 APIM의 429가 그대로 보입니다. `AI_GATEWAY_ROUTING`이 켜져 있어야 합니다([deploy](deploy.md#ai-게이트웨이-켜기끄기)). 평가 리포트의 게이트웨이 줄은 "콘텐츠 차단 N건, 토큰 한도 N건"으로 요약합니다.
- 게이트웨이 로그(`ApiManagementGatewayLogs`)와 에이전트별 토큰 지표(AppMetrics `Total Tokens`, `Properties.agent`)는 Log Analytics에서 봅니다. TPM을 바꿀 때는 지표의 1분 피크를 기준으로 `infra/resources.bicep`의 `gateways` 표를 고칩니다.

## 운영 지표·장애 주입

```powershell
python -m ops load                                              # 측정 배치(기본 48건=균형 격자 전체) → ops/reports/load-<시각>/
python -m ops fault run source-flaky                            # 장애 ① → ops/reports/fault-<시각>/
python -m ops fault run policy-down                             # 장애 ②
python -m ops fault status                                      # 장애 토글 상태
python -m ops metrics --run load-<시각> --fault-runs fault-<…> fault-<…>   # report.md·metrics.json
```

- `ops load`의 `--count`는 1~48(기본값은 균형 격자 크기 `len(LOAD_GRID)`), `--interval`은 제출 간격(초, 기본 15)입니다. 결과는 `ops/reports/load-<시각>/`에 저장되며, 측정 실행 하나는 `ops/reports/load-20261102110233/report.md`입니다.
- `ops fault run <시나리오>`는 장애를 켜고 신청을 제출한 뒤 `finally`에서 원복합니다. 시나리오는 `source-flaky`, `policy-down`입니다.
- 장애를 수동으로 켜고 끄는 명령(`src/ops/cli.py`): `python -m ops fault source flaky|off`, `python -m ops fault policy on|off`.
- **장애 토글 확인**: `python -m ops fault status`는 `source: off|flaky`, `policy: off|unavailable`을 한 줄씩 출력합니다. 시연이나 측정 전에 둘 다 `off`인지 확인합니다. 켜진 채 남았으면 `python -m ops fault source off` / `policy off`(또는 `azd provision`)로 원복합니다.
- `source-flaky`가 켜져 있는 동안에는 콘솔 차주 시뮬레이터 재생이 실패합니다.

## 비용 관리

`ops pause`로 Service Bus Premium을 지워도 아래 항목은 남습니다. 표는 README 원문(첫 실제 배포 기준 대략값)입니다.

| 항목 | 비용 | 비고 |
|------|------|------|
| Service Bus Premium 1 MU | 약 $677 | 쓰지 않을 때 `pause` |
| AI Search Basic | 약 $74 | Foundry 벡터 저장소 |
| ACR Premium | 약 $50 | Private Endpoint 필요 |
| Private Endpoint 약 17개 | 약 $125 | 개당 약 $7.3 |
| 모델, Functions, Container Apps, Cosmos serverless | 사용량 기반 | 유휴 시 거의 0 |
| Application Gateway WAF v2 | (구축 안 함) | 운영 아키텍처 문서에만 기술, 약 $365 |

월 고정비의 실측 추정은 `ops/reports/load-20261102110233/report.md`(Cost Management 일평균 × 30)를 봅니다.
