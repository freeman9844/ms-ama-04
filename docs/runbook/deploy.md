# 배포 (deploy)

코드나 인프라 변경을 Azure에 올리는 사람을 위한 안내입니다. 명령과 옵션은 `src/ops/cli.py`·`src/ops/deploy.py`·`src/ops/window.py` 기준입니다.
대상 환경 값(리소스 그룹, 앱 이름 등)은 `azd env get-values`에서 읽습니다.

## 배포 창

Azure 서비스는 기본적으로 사설 엔드포인트 전용입니다. 이미지 빌드나 Function 코드 배포처럼 운영자 PC에서 직접 접근해야 하는 작업은 **배포 창** 동안에만 운영자 공인 IP 하나(`<운영자 공인 IP>/32`)에 접근을 허용하고, 끝나면 닫습니다. [ADR-004](../adr/ADR-004-private-network-deploy-window.md)에 설계 근거가 있고, 상위 설계는 `docs/superpowers/specs/2026-10-31-agentic-cashflow-lending-design.md` §2.2입니다.

| 명령 | 하는 일 |
|------|---------|
| `python -m ops window open [--ip <IP>] [--acr] [--provision]` | `OPERATOR_IP`를 현재 공인 IP(또는 `--ip`)로 정하고 Foundry 계정·Functions에 운영자 IP 규칙을 넣습니다. `--acr`이면 `ACR_PUBLIC=true`로 ACR도 열어 `az acr build`가 가능해집니다. `CONSOLE_IP`는 비어 있을 때만 같은 IP로 정합니다(바꾸려면 `ops waf ip-filter on --ip`). 운영자 IP를 넣은 뒤 단계가 실패하면 창이 일부 열린 채일 수 있어, 오류 메시지 끝에 `python -m ops window close` 안내가 붙습니다(자동으로 닫지 않음). |
| `python -m ops window status` | 창 상태와 현재 공인 IP를 보여 줍니다. IP가 허용값과 다르거나 이전 `window close`가 끝나지 않았으면(`닫는 중(close 미완료)`) 경고하고 종료 코드 2로 끝납니다. |
| `python -m ops window close [--provision]` | `OPERATOR_IP=closed`, `ACR_PUBLIC=false`로 되돌립니다. `CONSOLE_IP`는 그대로 둡니다. azd 환경의 `WINDOW_STATE`를 변경 전 `closing`, 성공 뒤 `closed`로 기록하므로 변경이 실패하면 `window status`가 닫힘으로 보이지 않습니다. 그때는 `window close`를 다시 실행합니다. |

- **빠른 경로(기본)**: 최초 프로비저닝과 콘솔 인증서가 끝난 환경(`FUNCTION_APP_NAME`·`ACR_NAME`·`AI_ACCOUNT_NAME`·`APPGW_CERT_SECRET_ID` 등이 azd 환경에 있음)에서는 전체 `azd provision` 대신 창이 바꾸는 세 리소스만 Bicep 선언과 같은 값으로 바꿉니다(`src/ops/window.py`의 `apply_window`). Functions는 `config/web`·사이트 `publicNetworkAccess`를 ARM PATCH, Foundry 계정은 `publicNetworkAccess`·`networkAcls`를 PATCH한 뒤 `Succeeded`까지 기다리고, ACR은 `az acr update`를 씁니다. 열 때는 규칙을 넣고 공개하며, 닫을 때는 먼저 비공개로 바꾼 뒤 규칙을 지웁니다. azd 환경 값도 같이 기록하므로 다음 프로비저닝도 같은 상태를 선언합니다.
- `--provision`을 주거나 위 값이 없으면(첫 배포) 예전처럼 `azd provision`을 돌립니다. 실측(전체 프로비저닝): 열기 3.7분·닫기 3.7분(P5b), 열기 6.6분·닫기 5.4분(P6b) — `docs/runbook/deploy-history.md`의 "실제 배포 기록(P5b…)"과 "실제 실행 기록(P6b…)" 표. 빠른 경로 실측은 deploy-history의 "배포 단축" 절에 있습니다.
- 일부 서비스(Key Vault, Cosmos DB, Storage)는 테넌트 정책 때문에 창을 열어도 운영자 PC에서 접근할 수 없습니다. 그 서비스의 데이터 작업은 VNet 안 Job으로 합니다([deploy-history](deploy-history.md)의 "알려진 제약" 절).

## 구성요소별 배포

영어·USD 전환에는 아래 공통 `all` 순서를 쓰지 않습니다. [USD 전환 승인·소비자 배포·롤백](usd-cutover.md)을 먼저 확인합니다.

`python -m ops deploy <대상>`의 대상은 `src/ops/deploy.py`의 `TARGETS`입니다. 대상을 생략하면 `all`입니다.

| 대상 | 배포하는 것 | 이미지 환경 키 | 필요한 창 |
|------|-------------|----------------|-----------|
| `policy` | 정책 엔진 이미지(`src/policy/Dockerfile`) | `SERVICE_POLICY_IMAGE_NAME` | `window open --acr` |
| `mockapi` | Mock 마이데이터·국세청·PG 이미지(`src/mockapis/Dockerfile`) | `SERVICE_MOCKAPI_IMAGE_NAME` | `window open --acr` |
| `console` | 심사역 콘솔 이미지(`src/console/Dockerfile`) | `SERVICE_CONSOLE_IMAGE_NAME` | `window open --acr` |
| `eval` | 평가·운영 측정 Job 이미지(`src/evals/Dockerfile`) | `SERVICE_EVAL_IMAGE_NAME` | `window open --acr` |
| `orchestrator` | Functions 코드 zip(`src/orchestrator`+`src/agents`+`src/shared`, Linux wheel 동봉) | — | `window open` |
| `all` | 위 이미지 4개 + Functions zip | — | `window open --acr` |

- 이미지 대상은 `az acr build`로 이미지를 만든 뒤 azd 환경에 이미지 이름을 기록하고, 대상 앱·Job이 이미 ACR 이미지로 돌고 있으면 `az containerapp [job] update --image`로 이미지만 바꿉니다(수십 초, `eval`은 `job-eval`·`job-ops` 둘). 자리표시 이미지(첫 배포)이거나 `--provision`을 주면 `azd provision`을 한 번 돌립니다. Bicep도 바꿨다면 `--provision`을 씁니다. ACR 창이 닫혀 있으면 오류로 멈춥니다.
- `orchestrator`는 운영자 IP 창이 닫혀 있으면 멈춥니다. 원격 빌드는 쓰지 않습니다.
  - Linux wheel은 `requirements.txt` 내용·플랫폼·Python 버전 해시별로 `~/.cache/amalend/site-packages`(환경 변수 `AMALEND_OPS_CACHE`로 변경)에 캐시하고, 요구사항이 같으면 `pip install`을 건너뜁니다(실측 패키징 250초 → 캐시 적중 시 수 초).
  - zip 배포 전에 SCM 사이트를 한 번 찔러 보고, 403이면 응답 헤더 `x-ms-forbidden-ip`의 출구 IP를 `operator-egress`(priority 110) 규칙으로 잠시 허용한 뒤 배포가 끝나면(실패해도) 지웁니다. 아래 "App Service 출구 IP가 다를 때"의 수동 절차는 이 자동 처리가 실패할 때만 씁니다.

### 공통 순서

```powershell
python -m ops window open --acr      # 이미지를 빌드하지 않는 배포(orchestrator만)는 --acr 없이
python -m ops deploy <대상>
python -m ops window close           # 작업 뒤 반드시 닫기
python -m ops smoke                  # 배포 뒤 확인
```

Service Bus 이벤트까지 보려면 앞에 `python -m ops resume`을, 비용을 줄이려면 끝에 `python -m ops pause`를 붙입니다([operate](operate.md)).

주의:

- 오케스트레이터(`orchestrator`)를 배포하기 전에 실행 중인 신청 여정이 없는지 확인합니다. 원문(P5b): 배포 전에 실행 중인 신청 여정이 없는지 확인합니다(여정 코드가 바뀌어 실행 중 인스턴스는 replay가 어긋날 수 있음).
- 배포 직후 `ops smoke`는 `journey`·`consent` 점검으로 새 코드가 실제 신청 여정을 끝까지 처리하는지 확인합니다. 점검 항목은 [operate](operate.md#smoke)에 있습니다.
- 콘솔이 새 메시지 필드를 보내는 변경(P9의 고객 신청 `source="customer"`처럼)은 `ops deploy orchestrator`를 먼저 하고 `ops deploy all`을 합니다. `all`은 이미지(콘솔 교체)를 먼저 하고 Functions를 마지막에 올리므로, 그 사이 새 콘솔의 메시지를 이전 오케스트레이터가 거절할 수 있습니다.
- `usd-v2` 정책 규칙을 올릴 때는 오케스트레이터 → 콘솔 → 모의 API → 정책을 연이어 배포하고, 넷이 모두 끝나기 전에는 USD 신청을 넣지 않으며 `ops smoke`도 그 뒤에 합니다. 새 오케스트레이터는 정책이 모르는 필드를 보내고 옛 오케스트레이터는 `usd-v2` 판정을 거절하므로, 어긋난 동안 들어온 신청은 `policy_unavailable`로 실패합니다.
- 롤백은 오케스트레이터와 정책을 한 쌍으로 함께 되돌립니다.

## AI 게이트웨이 켜기/끄기

모든 LLM 호출을 APIM AI 게이트웨이로 보낼지는 azd 환경 값 `AI_GATEWAY_ROUTING`이 정합니다([ADR-010](../adr/ADR-010-ai-gateway.md)). 에이전트별 연결(`gw-*`)·TPM·Foundry 연결은 Bicep이 선언하고, 앱 설정 `AI_GATEWAY`·`AI_GATEWAY_URL`이 라우팅을 켭니다.

```powershell
azd env set AI_GATEWAY_ROUTING true     # 끄려면 false
azd provision --no-prompt
python -m ops smoke                      # 배포 뒤 확인
```

- APIM(Developer, 내부 VNet 모드)은 처음 만들 때 30–60분이 걸립니다(실측 27분 19초, `docs/runbook/deploy-history.md`의 "AI 게이트웨이(APIM) 도입" 절). 이미 있는 상태에서 라우팅만 켜는 프로비저닝은 약 4분이었습니다.
- 끄면(`false`) Decision-1을 포함한 모든 LLM 호출이 직접 호출로 돌아갑니다(롤백). 롤백 뒤에도 APIM 리소스와 월 약 $51 고정비는 남습니다. 지우려면 [비용 모델](../cost-model.md#절감-레버)을 봅니다.
- APIM 자체가 비정상이면 `AI_GATEWAY_ROUTING=false` 프로비저닝이 실패할 수 있습니다. 연결 모듈이 여전히 APIM 키(`listSecrets`)를 읽기 때문입니다. 이때는 앱 설정을 직접 바꾸는 빠른 경로를 씁니다(앱은 `AI_GATEWAY == "apim"`일 때만 게이트웨이를 씁니다, `src/agents/gateway.py`).

  ```powershell
  az functionapp config appsettings set -g <resource-group> -n <function-app> --settings AI_GATEWAY=off
  az containerapp job update -g <resource-group> -n <eval-job> --set-env-vars AI_GATEWAY=off
  ```

  APIM이 복구되면 `AI_GATEWAY_ROUTING=false`로 다시 프로비저닝합니다. 다음 프로비저닝이 Bicep 값으로 설정을 되돌립니다.
- 429·콘텐츠 차단 동작 확인은 [운영](operate.md#평가eval)의 `ops gateway probe`를 씁니다.

## 운영자 IP가 바뀔 때

운영자 PC의 공인 IP가 연결마다 바뀌면(관측: 서로 다른 /24 대역 두 개) `ops window open`으로 허용한 IP와 달라져 배포(Functions)가 403이 되고, 콘솔은 App Gateway WAF가 403으로 막습니다. 콘솔에는 ingress 규칙이 없고 WAF 사용자 지정 규칙 `allowOperatorOnly`가 접근을 정합니다([ADR-011](../adr/ADR-011-appgw-waf-entry.md)). Functions는 아래 `operator-alt` 절차를, 콘솔은 WAF 규칙을 씁니다. 값은 `azd env get-values`의 `AZURE_RESOURCE_GROUP`, `FUNCTION_APP_NAME`에서 읽습니다.

### 콘솔: WAF 운영자 IP 규칙

- 평상시: `python -m ops waf ip-filter on --ip <새 IP>`가 `CONSOLE_IP`를 새 IP로 갱신하고 프로비저닝으로 WAF 규칙에 반영합니다(`--ip`를 빼면 현재 공인 IP). `window open`은 `CONSOLE_IP`가 비어 있을 때만 정합니다.
- 임시로 IP를 하나 더 허용할 때: WAF 규칙은 "운영자 IP가 아니면 차단"이라 부정 조건(`--negate true`)으로 구성돼 있고 조건은 AND로 묶입니다. 부정 조건을 하나 더 붙이면 "둘 다 아니면 차단", 곧 허용 목록이 됩니다.

```powershell
az network application-gateway waf-policy custom-rule match-condition add `
  -g rg-amalend --policy-name waf-amalend --name allowOperatorOnly `
  --match-variables RemoteAddr --operator IPMatch --values <추가 IP>/32 --negate true
```

- 다음 프로비저닝(`window open|close --provision`, `deploy ... --provision`, `waf ip-filter on` 등)이 Bicep 정의로 되돌려 임시 조건이 사라집니다. 사라지면 다시 추가하거나 `waf ip-filter on --ip`로 `CONSOLE_IP`를 바꿉니다.
- 403이 WAF 때문인지는 Log Analytics의 `AGWFirewallLogs`로 확인합니다.

### 콘솔: 테스트 기간에 IP 규칙 끄기

IP가 자주 바뀌는 테스트 기간에는 IP 규칙 대신 데모 로그인 비밀번호로 콘솔 진입을 막을 수 있습니다([ADR-011](../adr/ADR-011-appgw-waf-entry.md)의 "테스트 기간 예외"). 관리 규칙(OWASP DRS 2.1, Prevention)은 그대로입니다. 콘솔이 데모 모드(`CONSOLE_AUTH_ENABLED=false`)일 때만 씁니다.

| 명령 | 하는 일 |
|------|---------|
| `python -m ops waf ip-filter off` | `CONSOLE_DEMO_PASSWORD`가 없으면 20자 비밀번호를 만들고, 콘솔에 아직 없으면 규칙이 켜진 채로 프로비저닝해 컨테이너 비밀로 넣습니다. 그다음 `allowOperatorOnly`를 `Disabled`로 바꾸고 `WAF_IP_FILTER=false`를 기록합니다. 로그인 아이디와 비밀번호를 출력합니다. |
| `python -m ops waf ip-filter on [--ip <IP>]` | `CONSOLE_IP`를 `--ip`(기본: 현재 공인 IP)로, `WAF_IP_FILTER=true`로 정하고 프로비저닝합니다. 비밀번호는 그대로 남아 이중으로 막습니다. |
| `python -m ops waf ip-filter status` | 규칙 상태(`Enabled`/`Disabled`)·허용 IP·비밀번호 설정 여부를 보여 줍니다. |

- 비밀번호 다시 보기: `azd env get-value CONSOLE_DEMO_PASSWORD`. 바꾸려면 `azd env set CONSOLE_DEMO_PASSWORD <새 값>` 뒤 `ops deploy console --provision`처럼 프로비저닝합니다.
- 비밀번호가 설정되면 로그인 화면에 비밀번호를 미리 채우지 않고, 값이 정확히 같아야 들어옵니다(`src/console/app.py`의 `/login`).
- `WAF_IP_FILTER=false`는 azd 환경에 남으므로 이후 프로비저닝도 규칙을 끈 채로 둡니다. 구축이 끝나면 반드시 `waf ip-filter on`으로 되돌립니다.

### 프로비저닝하는 명령

`src/ops`에서 `azd provision`을 실행하는 명령은 다음뿐입니다(`provision()` 호출 위치).

- `ops window open`, `ops window close` — `--provision`을 주거나 첫 배포일 때만. 빠른 경로는 Functions·Foundry·ACR만 바꾸며, 콘솔 인증서가 새로 생기면 한 번 프로비저닝합니다(`src/ops/window.py`).
- `ops pause`, `ops resume` (`src/ops/lifecycle.py`)
- `ops deploy policy|mockapi|console|eval|all` — `--provision`이거나 대상이 아직 자리표시 이미지일 때만(`src/ops/deploy.py`의 `deploy_images`).
- `ops waf ip-filter on`, 그리고 콘솔에 비밀번호를 처음 넣는 `ops waf ip-filter off` (`src/ops/console_access.py`)

`ops deploy orchestrator`(Functions zip만), `ops smoke`, `ops fault`는 프로비저닝하지 않습니다.

Bicep이 Functions 접근 제한(`infra/modules/functions.bicep`)과 WAF 정책(`infra/modules/appgw.bicep`)을 선언하므로, 프로비저닝 뒤에는 임시 규칙이 사라질 수 있습니다. 빠른 경로의 `window open|close`도 Functions `config/web`의 규칙 목록을 통째로 바꾸므로 임시 규칙을 지웁니다. Functions의 임시 `operator-alt*` 규칙은 `window open`(프로비저닝)이 지운다는 것을 2026-11-03 리허설에서 실측으로 확인했습니다. 그래서 **창을 연 뒤, 프로비저닝하는 마지막 명령 뒤에** 임시 규칙을 넣습니다.

### 절차(Functions)

`ops deploy orchestrator`는 SCM 403을 자동으로 처리합니다(위 "구성요소별 배포"). 아래 수동 절차는 자동 처리가 실패할 때(예: 출구 IP가 요청마다 바뀜)만 씁니다.

1. 프로비저닝하는 명령(`window open --acr`, `deploy console|all` 등)을 모두 먼저 실행합니다. `ops window open --ip <IP>`로 현재 IP를 직접 지정하면 이 단계의 403을 피할 수 있습니다.
2. 마지막 프로비저닝이 끝나면 현재 공인 IP를 임시 허용합니다(Functions: priority 110).
3. 프로비저닝하지 않는 작업(`ops deploy orchestrator`)을 수행합니다. 중간에 다시 프로비저닝했다면 2)를 반복합니다.
4. 임시 규칙을 지웁니다.
5. `ops window close`로 창을 닫습니다. 이 명령도 Functions 규칙 목록을 Bicep 상태(빈 목록)로 되돌립니다.

```powershell
# 2) 현재 공인 IP를 임시 허용
az functionapp config access-restriction add -g <resource-group> -n <function-app> `
  --rule-name operator-alt --action Allow --ip-address <IP>/32 --priority 110

# 3) 프로비저닝하지 않는 작업 수행

# 4) 작업 뒤 임시 규칙 제거(이미 없으면 not found 오류는 무시)
az functionapp config access-restriction remove -g <resource-group> -n <function-app> --rule-name operator-alt
```

- Functions 규칙 이름 `operator`(priority 100)는 `window open`이 만드는 규칙입니다(`infra/modules/functions.bicep`). 임시 규칙은 이름을 `operator-alt`로 달리 하고 priority 110을 씁니다. SCM 사이트는 주 사이트 규칙을 따르므로(`scmIpSecurityRestrictionsUseMain`) 같은 규칙으로 zip 배포가 됩니다.
- 프로비저닝 뒤 접근이 다시 403이면 2)를 반복합니다.

### 출구 IP가 여러 개로 바뀔 때

회사 NAT처럼 출구 IP가 하루에 여러 번 바뀌면(2026-11-03 리허설에서 4개 관측) 임시 규칙이 하나로는 부족합니다.

- IP가 바뀔 때마다 규칙 이름을 `operator-alt2`, `operator-alt3`처럼 늘려 `/32` 규칙을 하나씩 더합니다. Functions 우선순위는 110부터 하나씩 올립니다(111, 112 …). 콘솔은 위 WAF 규칙에 `<추가 IP>/32` 조건을 하나씩 더합니다.
- `window open`(프로비저닝)은 Functions의 임시 규칙을 지웁니다(2026-11-03 실측). `window close`·`ops deploy`(이미지 대상) 같은 다른 프로비저닝도 Bicep 선언상 같게 동작할 것으로 예상하지만 실측하지 않았습니다. 프로비저닝을 다시 했다면 현재 IP의 규칙을 다시 추가합니다.
- 정리 때는 쓴 이름을 전부 지웁니다. 이미 없으면 not found 오류는 무시합니다.

```powershell
az functionapp config access-restriction remove -g <resource-group> -n <function-app> --rule-name <이름>

# 남은 규칙 확인
az functionapp config access-restriction show -g <resource-group> -n <function-app> --query ipSecurityRestrictions -o table
```

### App Service 출구 IP가 다를 때

모바일 회선에서는 App Service(Functions 주 사이트와 SCM 사이트)로 가는 트래픽이 통신사 NAT 출구 IP로 나가, `api.ipify`가 알려 주는 IP(`ops window status`의 IP)와 다릅니다. 관측: 서로 다른 /24 대역의 출구 IP 3개가 회전했고, 그래서 창이 열려 있고 보고된 IP의 /16을 허용해도 `ops deploy orchestrator`가 403이었습니다(2026-11-05). 콘솔(WAF)은 `CONSOLE_IP`로 따로 정해지므로 Functions만 다른지 먼저 봅니다. 접근 제한 테스트는 `publicNetworkAccess`가 창이 닫힌 동안 `Disabled`이므로 **창이 열려 있을 때만** 의미가 있습니다.

실제 출구 IP를 찾아 허용하는 절차(창이 열린 상태에서 시작):

1. Functions 앱의 `AppServiceIPSecAuditLogs`를 Log Analytics 작업 영역으로 보내는 임시 진단 설정을 만듭니다.
2. SCM 사이트에 요청을 몇 번 보내 403 기록을 만들고 약 5분 기다립니다.
3. 작업 영역에서 출구 IP를 조회합니다.
4. 나온 IP를 `/32`로 `operator-alt2`, `operator-alt3`… 이름의 Functions 규칙(priority 111부터)에 넣습니다. SCM은 주 사이트 규칙을 따르므로(`scmIpSecurityRestrictionsUseMain=true`) `--scm-site`를 붙이지 않고 주 사이트에 넣습니다.
5. 압축 파일을 Kudu 게시 API에 `curl`로 올립니다([deploy-history](deploy-history.md)의 `curl.exe -X POST --data-binary … /api/publish` 예시와 같은 명령이며, `/api/deployments`에서 status 4를 확인합니다).
6. 임시 규칙과 진단 설정을 지우고 `ops window close`로 창을 닫습니다.

```powershell
# 1) 임시 진단 설정(로그 범주만 켠다)
az monitor diagnostic-settings create --name ipsec-probe-temp `
  --resource <function-app-resource-id> --workspace <log-analytics-workspace-id> `
  --logs '[{\"category\":\"AppServiceIPSecAuditLogs\",\"enabled\":true}]'

# 3) 작업 영역 쿼리(KQL) — 출구 IP 목록
#    AppServiceIPSecAuditLogs | extend ip=tostring(split(CIp, ':')[0]) | distinct ip
az monitor log-analytics query -w <workspace-customer-id> `
  --analytics-query "AppServiceIPSecAuditLogs | extend ip=tostring(split(CIp, ':')[0]) | distinct ip" -o table

# 4) 찾은 IP마다 임시 허용(priority 111, 112, …)
az functionapp config access-restriction add -g <resource-group> -n <function-app> `
  --rule-name operator-alt2 --action Allow --ip-address <IP>/32 --priority 111

# 6) 정리
az functionapp config access-restriction remove -g <resource-group> -n <function-app> --rule-name operator-alt2
az monitor diagnostic-settings delete --name ipsec-probe-temp --resource <function-app-resource-id>
python -m ops window close
```

- 출구 IP는 계속 회전할 수 있으니 4)에서 모은 IP를 모두 넣습니다. 게시가 끝나면 규칙을 모두 지웁니다.
- 진단 설정은 로그 수집이 끝나면 바로 지웁니다(비용·노출 최소화).

## Service Bus 구독 규칙 확인

`ops resume`이나 프로비저닝(`window close`·`window open` 등) 뒤에 Service Bus 모드 사후 재생을 쓸 거라면 두 구독에 `subject-label` 규칙만 남아 있는지 확인합니다. 2026-11-03 리허설에서 Bicep이 `$Default` 이름으로 PUT한 CorrelationFilter가 남지 않고 자동 `1=1` 규칙도 사라져 구독 규칙이 0개가 되었고, 그 결과 재생 메시지가 모두 버려졌습니다. 이를 고치려고 `subject-label`(label = 토픽 이름) CorrelationFilter 규칙을 추가했습니다(커밋 `8cfe7d1`). 이름은 `azd env get-values`의 `AZURE_RESOURCE_GROUP`과 Service Bus 네임스페이스에서 읽습니다.

```powershell
az servicebus topic subscription rule list -g <resource-group> --namespace-name <servicebus-namespace> `
  --topic-name flow.updated --subscription-name early-warning --query "[].name" -o tsv
az servicebus topic subscription rule list -g <resource-group> --namespace-name <servicebus-namespace> `
  --topic-name repayment.updated --subscription-name collections --query "[].name" -o tsv
```

기대값은 두 명령 모두 `subject-label` 한 줄입니다. 규칙이 비어 있으면 재생이 시작돼도 메시지가 전달되지 않으니 `ops deploy`나 프로비저닝을 다시 확인합니다.
