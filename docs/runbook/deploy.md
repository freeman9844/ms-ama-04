# 배포 (deploy)

코드나 인프라 변경을 Azure에 올리는 사람을 위한 안내입니다. 명령과 옵션은 `src/ops/cli.py`·`src/ops/deploy.py`·`src/ops/window.py` 기준입니다.
대상 환경 값(리소스 그룹, 앱 이름 등)은 `azd env get-values`에서 읽습니다.

## 배포 창

Azure 서비스는 기본적으로 사설 엔드포인트 전용입니다. 이미지 빌드나 Function 코드 배포처럼 운영자 PC에서 직접 접근해야 하는 작업은 **배포 창** 동안에만 운영자 공인 IP 하나(`<운영자 공인 IP>/32`)에 접근을 허용하고, 끝나면 닫습니다. [ADR-004](../adr/ADR-004-private-network-deploy-window.md)에 설계 근거가 있고, 상위 설계는 `docs/superpowers/specs/2026-10-31-agentic-cashflow-lending-design.md` §2.2입니다.

| 명령 | 하는 일 |
|------|---------|
| `python -m ops window open [--ip <IP>] [--acr]` | `OPERATOR_IP`·`CONSOLE_IP`를 현재 공인 IP(또는 `--ip`)로 정하고 `azd provision`을 실행해 Foundry 계정·Functions 등에 운영자 IP 규칙을 넣습니다. `--acr`이면 `ACR_PUBLIC=true`로 ACR도 열어 `az acr build`가 가능해집니다. |
| `python -m ops window status` | 창 상태와 현재 공인 IP를 보여 줍니다. IP가 허용값과 다르면 경고하고 종료 코드 2로 끝납니다. |
| `python -m ops window close` | `OPERATOR_IP=closed`, `ACR_PUBLIC=false`로 다시 프로비저닝합니다. `CONSOLE_IP`는 그대로 둡니다. |

- 창은 프로비저닝을 동반하므로 몇 분이 걸립니다. 실측: 열기 3.7분·닫기 3.7분(P5b), 열기 6.6분·닫기 5.4분(P6b, job-ops·경보·Workbook 첫 생성 포함) — `docs/runbook/deploy-history.md`의 "실제 배포 기록(P5b…)"과 "실제 실행 기록(P6b…)" 표.
- 일부 서비스(Key Vault, Cosmos DB, Storage)는 테넌트 정책 때문에 창을 열어도 운영자 PC에서 접근할 수 없습니다. 그 서비스의 데이터 작업은 VNet 안 Job으로 합니다([deploy-history](deploy-history.md)의 "알려진 제약" 절).

## 구성요소별 배포

`python -m ops deploy <대상>`의 대상은 `src/ops/deploy.py`의 `TARGETS`입니다. 대상을 생략하면 `all`입니다.

| 대상 | 배포하는 것 | 이미지 환경 키 | 필요한 창 |
|------|-------------|----------------|-----------|
| `policy` | 정책 엔진 이미지(`src/policy/Dockerfile`) | `SERVICE_POLICY_IMAGE_NAME` | `window open --acr` |
| `mockapi` | Mock 마이데이터·국세청·PG 이미지(`src/mockapis/Dockerfile`) | `SERVICE_MOCKAPI_IMAGE_NAME` | `window open --acr` |
| `console` | 심사역 콘솔 이미지(`src/console/Dockerfile`) | `SERVICE_CONSOLE_IMAGE_NAME` | `window open --acr` |
| `eval` | 평가·운영 측정 Job 이미지(`src/evals/Dockerfile`) | `SERVICE_EVAL_IMAGE_NAME` | `window open --acr` |
| `orchestrator` | Functions 코드 zip(`src/orchestrator`+`src/agents`+`src/shared`, Linux wheel 동봉) | — | `window open` |
| `all` | 위 이미지 4개 + 프로비저닝 1회 + Functions zip | — | `window open --acr` |

- 이미지 대상은 `az acr build`로 이미지를 만든 뒤 azd 환경에 이미지 이름을 기록하고 `azd provision`으로 Container App·Job을 갱신합니다. ACR 창이 닫혀 있으면 오류로 멈춥니다.
- `orchestrator`는 운영자 IP 창이 닫혀 있으면 멈춥니다. 원격 빌드는 쓰지 않습니다.

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
- 콘솔이 새 메시지 필드를 보내는 변경(P9의 고객 신청 `source="customer"`처럼)은 `ops deploy orchestrator`를 먼저 하고 `ops deploy all`을 합니다. `all`은 이미지·프로비저닝(콘솔 교체)을 먼저 하고 Functions를 마지막에 올리므로, 그 사이 새 콘솔의 메시지를 이전 오케스트레이터가 거절할 수 있습니다.

## 운영자 IP가 바뀔 때

운영자 PC의 공인 IP가 연결마다 바뀌면(관측: 서로 다른 /24 대역 두 개) `ops window open`으로 허용한 IP와 달라져 배포·콘솔이 403이 됩니다. README P6b 문제 대응 원문(P6b):

- 운영자 PC 공인 IP가 연결마다 바뀌어 배포·콘솔이 403: `ops window status`의 IP와 다른 IP를 Functions 접근 제한·console ingress에
  `operator-alt` 규칙으로 잠시 추가하고, 작업 뒤 지웁니다(`ops window close`는 Functions 규칙을 Bicep 상태로 되돌립니다). (P6b)

원문은 프로비저닝과의 순서를 말하지 않으므로 아래 절차가 보완합니다. 값은 `azd env get-values`의 `AZURE_RESOURCE_GROUP`, `FUNCTION_APP_NAME`, `CONSOLE_APP_NAME`에서 읽습니다.

### 프로비저닝하는 명령

`src/ops`에서 `azd provision`을 실행하는 명령은 다음뿐입니다(`provision()` 호출 위치).

- `ops window open`, `ops window close` (`src/ops/window.py`)
- `ops pause`, `ops resume` (`src/ops/lifecycle.py`)
- `ops deploy policy|mockapi|console|eval|all` — 이미지 대상은 빌드 뒤 프로비저닝합니다(`src/ops/deploy.py`의 `deploy_images`).

`ops deploy orchestrator`(Functions zip만), `ops smoke`, `ops fault`는 프로비저닝하지 않습니다.

Bicep이 Functions 접근 제한(`infra/modules/functions.bicep`)과 콘솔 ingress 규칙(`infra/modules/container-app.bicep`)을 선언하므로, 프로비저닝 뒤에는 임시 규칙이 사라질 수 있습니다. Functions의 임시 `operator-alt*` 규칙은 `window open`(프로비저닝)이 지운다는 것을 2026-11-03 리허설에서 실측으로 확인했습니다(콘솔 ingress 쪽은 같은 방식으로 확인하지 않았으니 403이면 다시 추가합니다). 그래서 **프로비저닝하는 마지막 명령 뒤에** 임시 규칙을 넣습니다.

### 절차

1. 프로비저닝하는 명령(`window open --acr`, `deploy console|all` 등)을 모두 먼저 실행합니다. `ops window open --ip <IP>`로 현재 IP를 직접 지정하면 이 단계의 403을 피할 수 있습니다.
2. 마지막 프로비저닝이 끝나면 현재 공인 IP를 임시 허용합니다(Functions: priority 110, 콘솔 ingress).
3. 프로비저닝하지 않는 작업(`ops deploy orchestrator`, 콘솔 접속, 브라우저 확인)을 수행합니다. 중간에 다시 프로비저닝했다면 2)를 반복합니다.
4. 임시 규칙을 지웁니다.
5. `ops window close`로 창을 닫습니다. 이 명령도 프로비저닝이므로 Functions 규칙을 Bicep 상태로 되돌립니다. 콘솔 ingress의 `operator-alt`는 이 시점에 이미 지웠어야 하며, 남아 있으면 4)의 `remove`로 직접 지웁니다.

```powershell
# 2) 현재 공인 IP를 임시 허용
az functionapp config access-restriction add -g <resource-group> -n <function-app> `
  --rule-name operator-alt --action Allow --ip-address <IP>/32 --priority 110
az containerapp ingress access-restriction set -g <resource-group> -n <console-app> `
  --rule-name operator-alt --action Allow --ip-address <IP>/32

# 3) 프로비저닝하지 않는 작업 수행

# 4) 작업 뒤 임시 규칙 제거(이미 없으면 not found 오류는 무시)
az functionapp config access-restriction remove -g <resource-group> -n <function-app> --rule-name operator-alt
az containerapp ingress access-restriction remove -g <resource-group> -n <console-app> --rule-name operator-alt
```

- Functions 규칙 이름 `operator`(priority 100)는 `window open`이 만드는 규칙입니다(`infra/modules/functions.bicep`). 임시 규칙은 이름을 `operator-alt`로 달리 하고 priority 110을 씁니다. SCM 사이트는 주 사이트 규칙을 따르므로(`scmIpSecurityRestrictionsUseMain`) 같은 규칙으로 zip 배포가 됩니다.
- Container Apps ingress 규칙에는 priority 옵션이 없습니다(`az containerapp ingress access-restriction set --help`). 모든 규칙이 같은 `--action`이어야 합니다.
- 프로비저닝 뒤 접근이 다시 403이면 2)를 반복합니다.

### 출구 IP가 여러 개로 바뀔 때

회사 NAT처럼 출구 IP가 하루에 여러 번 바뀌면(2026-11-03 리허설에서 4개 관측) 임시 규칙이 하나로는 부족합니다.

- IP가 바뀔 때마다 규칙 이름을 `operator-alt2`, `operator-alt3`처럼 늘려 `/32` 규칙을 하나씩 더합니다. console ingress와 Functions 양쪽에 모두 넣고, Functions 우선순위는 110부터 하나씩 올립니다(111, 112 …).
- `window open`(프로비저닝)은 Functions의 임시 규칙을 지웁니다(2026-11-03 실측). `window close`·`ops deploy`(이미지 대상) 같은 다른 프로비저닝도 Bicep 선언상 같게 동작할 것으로 예상하지만 실측하지 않았습니다. 프로비저닝을 다시 했다면 현재 IP의 규칙을 다시 추가합니다.
- 정리 때는 쓴 이름을 전부 지웁니다. 이미 없으면 not found 오류는 무시합니다.

```powershell
az containerapp ingress access-restriction remove -g <resource-group> -n <console-app> --rule-name <이름>
az functionapp config access-restriction remove -g <resource-group> -n <function-app> --rule-name <이름>

# 남은 규칙 확인
az containerapp ingress access-restriction list -g <resource-group> -n <console-app> -o table
az functionapp config access-restriction show -g <resource-group> -n <function-app> --query ipSecurityRestrictions -o table
```

## Service Bus 구독 규칙 확인

`ops resume`이나 프로비저닝(`window close`·`window open` 등) 뒤에 Service Bus 모드 사후 재생을 쓸 거라면 두 구독에 `subject-label` 규칙만 남아 있는지 확인합니다. 2026-11-03 리허설에서 Bicep이 `$Default` 이름으로 PUT한 CorrelationFilter가 남지 않고 자동 `1=1` 규칙도 사라져 구독 규칙이 0개가 되었고, 그 결과 재생 메시지가 모두 버려졌습니다. 이를 고치려고 `subject-label`(label = 토픽 이름) CorrelationFilter 규칙을 추가했습니다(커밋 `8cfe7d1`). 이름은 `azd env get-values`의 `AZURE_RESOURCE_GROUP`과 Service Bus 네임스페이스에서 읽습니다.

```powershell
az servicebus topic subscription rule list -g <resource-group> --namespace-name <servicebus-namespace> `
  --topic-name flow.updated --subscription-name early-warning --query "[].name" -o tsv
az servicebus topic subscription rule list -g <resource-group> --namespace-name <servicebus-namespace> `
  --topic-name alert.raised --subscription-name collections --query "[].name" -o tsv
```

기대값은 두 명령 모두 `subject-label` 한 줄입니다. 규칙이 비어 있으면 재생이 시작돼도 메시지가 전달되지 않으니 `ops deploy`나 프로비저닝을 다시 확인합니다.
