# 배포 단축 + WAF IP 규칙 토글 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**목표:** 변경 한 번 반영하는 시간을 약 30분에서 약 10분으로 줄입니다. 테스트 기간에는 콘솔 WAF의 운영자 IP 규칙을 끄고 데모 비밀번호로 막습니다.

**설계:** 전체 `azd provision`이 하던 일 가운데 실제로 바뀌는 리소스만 az CLI로 바꿉니다. Bicep 입력(azd 환경 값)은 그대로 기록해, 나중에 전체 프로비저닝해도 같은 상태로 수렴합니다. 처음 만드는 경우처럼 전제가 없으면 예전처럼 전체 프로비저닝합니다.

**기술:** Python 3.12 ops CLI, az CLI 2.93, Bicep, FastAPI 콘솔.

**Spec:** 별도 spec 없음. 사용자 선택(2026-11-27 질문 폼)이 기준입니다.
- 배포 단축 4개 항목 모두: 이미지만 교체, 창 열기·닫기 대상 리소스만, 패키지 재사용, Functions 403 자동 처리
- WAF: `ip_off_password`(IP 규칙만 끄고 데모 비밀번호, 나중에 명령으로 다시 켬)

## Global Constraints

- 측정값: 프로비저닝 1회 4~5분(`resources-amalend` 3m08s), ACR 빌드 약 45초, Functions 패키징 250초(28.9 MB).
- 창이 바꾸는 리소스는 4개뿐입니다: ACR 공개 여부, Functions(`publicNetworkAccess` + `operator` 규칙 priority 100), Foundry 계정(`publicNetworkAccess` + `ipRules`), WAF `consoleIp`.
- 빠른 경로가 만드는 상태는 Bicep 선언과 같아야 합니다(`infra/modules/functions.bicep`, `foundry/account.bicep`, `acr.bicep`).
- 관리 규칙(OWASP DRS 2.1, Prevention)은 항상 켭니다. 끄는 것은 사용자 지정 규칙 `allowOperatorOnly`뿐입니다.
- IP 규칙을 끄기 전에 콘솔에 데모 비밀번호가 먼저 적용돼 있어야 합니다(순서 보장, fail-closed).
- 데모 비밀번호는 azd 환경 `CONSOLE_DEMO_PASSWORD`(12자 이상, 없으면 생성)이고 Container App 비밀로 전달합니다. 비밀번호가 설정되지 않은 콘솔은 예전처럼 아무 비밀번호나 받습니다(테스트·하위 호환).
- `azd env set` 값은 오류 메시지에서 가려집니다(`ops.azcli._display`).

## Review Focus

1. 처음 만드는 환경(앱 이름·인증서가 env에 없음)에서는 빠른 경로가 아니라 전체 프로비저닝으로 가야 합니다.
2. 자리표시 이미지(MCR)로 떠 있는 Container App은 레지스트리 설정이 없으므로 이미지만 교체하면 실패합니다. 이때는 프로비저닝합니다.
3. 패키지 캐시가 중간에 실패하면 반쯤 채워진 캐시를 다음에 재사용하면 안 됩니다(완료 표시 파일).
4. SCM 403 자동 처리의 임시 규칙은 배포가 실패해도 지워야 합니다.
5. 창 닫기가 중간에 실패하면 `WINDOW_STATE=closing`으로 남아야 합니다(기존 동작 유지).

---

### Task 1: 이미지만 교체하는 빠른 배포

**파일:** `src/ops/deploy.py`, `src/ops/cli.py`, `tests/ops/test_deploy.py`

- `ContainerService`에 `targets: tuple[tuple[str, str], ...]`를 추가합니다(`("app", "POLICY_APP_NAME")` 또는 `("job", "EVAL_JOB_NAME")`). eval은 `EVAL_JOB_NAME`, `OPS_JOB_NAME` 두 Job입니다.
- `deploy_images(runner, services, root, tag, *, full_provision=False)`: 빌드와 `set_env` 뒤에, 모든 대상이 env에 있고 현재 이미지가 `<ACR 로그인 서버>/`로 시작하면 `az containerapp [job] update -g RG -n NAME --image IMAGE -o none`을 실행합니다. 하나라도 아니면 마지막에 한 번 `provision`을 실행합니다.
- `deploy(..., full_provision=False)`, CLI `ops deploy <대상> --provision`.
- 테스트: 빠른 경로에서 provision을 하지 않고 update를 실행함, 자리표시 이미지면 provision, `--provision`이면 provision, eval은 두 Job을 갱신함.

### Task 2: Functions 패키지 캐시 + SCM 403 자동 처리

**파일:** `src/ops/deploy.py`, `tests/ops/test_deploy.py`

- 캐시 키: `sha256(requirements.txt 바이트 + PIP_PLATFORMS + "3.12")` 앞 16자. 위치: `AMALEND_OPS_CACHE` 환경 변수, 없으면 `~/.cache/amalend/site-packages/<키>`. 완료 표시 `.complete`가 있을 때만 재사용합니다. 없으면 `<키>.tmp`에 설치한 뒤 이름을 바꿉니다.
- `scm_forbidden_ip(name) -> str | None`: `https://<name>.scm.azurewebsites.net/`에 GET을 보내 403이면 `x-ms-forbidden-ip` 헤더를 돌려줍니다. 네트워크 오류면 None입니다.
- 막힌 IP가 있으면 `operator-egress`(priority 110) 규칙을 넣고, 풀릴 때까지 5초 간격으로 최대 12번 확인한 뒤 zip을 배포합니다. `finally`에서 규칙을 지웁니다.
- 테스트: 같은 requirements면 pip는 1번만, 바뀌면 다시 실행, 403이면 규칙 추가·제거(배포 실패에서도 제거), 403이 아니면 규칙을 만들지 않음.

### Task 3: 창 열기·닫기 빠른 경로

**파일:** `src/ops/azcli.py`(`rest_patch`), `src/ops/window.py`, `src/ops/cli.py`, `tests/ops/test_window.py`

- 빠른 경로 조건: env에 `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`, `FUNCTION_APP_NAME`, `ACR_NAME`, `AI_ACCOUNT_NAME`, `APPGW_CERT_SECRET_ID`가 있고 `--provision`이 아님.
- `apply_window(az, env, ip | None, acr)`:
  - Functions는 `config/web`과 site를 PATCH합니다. 규칙은 `[{"name":"operator","ipAddress":"<ip>/32","action":"Allow","priority":100}]` 또는 `[]`, 기본 동작 `Deny`, `scmIpSecurityRestrictionsUseMain: true`, 공개 여부 `Enabled|Disabled`입니다.
  - Foundry 계정은 PATCH합니다(`api-version=2025-04-01-preview`). `publicNetworkAccess`와 `networkAcls {defaultAction: Deny, bypass: AzureServices, ipRules, virtualNetworkRules: []}`를 넣습니다.
  - ACR은 `az acr update --public-network-enabled true|false --default-action Allow|Deny`입니다.
  - 열 때는 규칙을 먼저 넣고 공개합니다. 닫을 때는 먼저 비공개로 바꾸고 규칙을 지웁니다.
- 빠른 경로에서도 `ensure_console_cert`를 확인하고, True면 프로비저닝합니다.
- `CONSOLE_IP`는 비어 있을 때만 정합니다. WAF 허용 목록은 Task 4 명령이 관리합니다.
- 테스트: 빠른 열기·닫기에서 provision 0회, PATCH 본문, 순서 확인, 조건 부족이나 `--provision`이면 예전 경로, 닫기 실패 시 closing 유지.

### Task 4: WAF IP 규칙 토글 + 콘솔 데모 비밀번호

**파일:** `infra/main.bicep`, `infra/main.bicepparam`, `infra/resources.bicep`, `infra/modules/appgw.bicep`, `src/console/app.py`, `src/console/adapters.py`, `src/console/templates/login.html`, `src/ops/console_access.py`(신규), `src/ops/cli.py`, 테스트(`tests/infra/test_appgw.py`, `tests/infra/test_console.py`, `tests/console/test_app.py`, `tests/console/test_adapters.py`, `tests/ops/test_console_access.py`)

- Bicep:
  - `consoleIpFilter bool = true`(env `WAF_IP_FILTER` != 'false')를 appgw `ipFilter`로 넘기고, 규칙 `state: ipFilter ? 'Enabled' : 'Disabled'`로 씁니다.
  - `@secure() consoleDemoPassword`(env `CONSOLE_DEMO_PASSWORD`)는 데모 모드일 때 콘솔 `secretEnv.CONSOLE_DEMO_PASSWORD`로 넘깁니다.
- 콘솔: `ConsoleDeps.demo_password: str | None`를 추가합니다. 값이 있으면 `hmac.compare_digest`로 비교하고, 로그인 화면의 미리 채운 `value="demo"`를 뺍니다.
- `python -m ops waf ip-filter off|on [--ip]|status`:
  - `off`: 비밀번호를 확보합니다(없으면 생성해 env에 기록). 콘솔에 `CONSOLE_DEMO_PASSWORD`가 없으면 프로비저닝합니다(이때 규칙은 아직 켜져 있음). 그다음 `az network application-gateway waf-policy custom-rule update --state Disabled`를 실행하고 `WAF_IP_FILTER=false`로 기록합니다. 비밀번호를 출력합니다.
  - `on`: `CONSOLE_IP=--ip 또는 현재 공인 IP`, `WAF_IP_FILTER=true`로 기록한 뒤 프로비저닝합니다(Bicep이 규칙과 허용 IP를 정함).
  - `status`: 규칙 상태와 허용 IP를 보여 줍니다.
  - 정책 이름은 `az network application-gateway show`의 `firewallPolicy.id`에서 읽습니다.
- 테스트: Bicep 규칙 state와 비밀 전달, 로그인 비밀번호 검사, off의 순서(provision → 규칙 끄기 → env), on이 provision함.

### Task 5: 문서와 실배포 검증

- `docs/runbook/deploy.md`의 창, 구성요소별 배포, IP 절차, 프로비저닝하는 명령 절을 고치고 WAF 토글 절을 추가합니다. ADR-011에 메모를 남깁니다.
- 실배포 순서:
  1. 전체 프로비저닝 1회(새 Bicep, 비밀번호)로 콘솔 배포
  2. `ops waf ip-filter off`
  3. 빠른 창 열기 → orchestrator(캐시) → 빠른 닫기
  4. smoke
  5. 각 단계 시간 기록, deploy-history 기록
