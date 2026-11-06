# 데모 리허설 (rehearsal)

실제 Azure 환경에서 [demo-script](demo-script.md)를 처음부터 끝까지 한 번 돌려 장면별 시간을 재고, 사전 건 ID를 [fallback](fallback.md)에 채우는 절차와 기록표입니다. 리허설은 2026-11-03에 1회 실시했고 기록표를 채웠습니다. 발표자가 스톱워치를 쓰지 않아 시각은 콘솔 접속 로그에서 복원했습니다. 대본만 보완했고 2회차 리허설은 하지 않았습니다.

> **합성 데이터 기준.** 리허설에서 재는 시간은 합성 데이터와 데모 환경에서의 값입니다. 이 문서에 채우는 시간은 새 실측이고, 그 밖의 수치는 이미 있는 산출물에서 출처 경로와 함께 인용합니다. 시각은 모두 KST로 기록합니다.

## 준비

- Azure 자격 증명과 운영자 PC의 브라우저가 필요합니다. 이 절차는 발표자(또는 운영자)가 직접 수행합니다.
- 명령은 [operate](../runbook/operate.md)·[deploy](../runbook/deploy.md)의 형태 그대로입니다. 환경 값(콘솔 주소, 워크스페이스 ID 등)은 `azd env get-values`에서 읽습니다.
- 녹화본은 이 리허설 때 발표자가 대본대로 화면을 녹화합니다([fallback](fallback.md#전체-장애-시)). 녹화본은 저장소에 넣지 않습니다.

## 절차

### 1. 배포 창 열기와 배포

시작·끝 시각을 기록합니다.

```powershell
python -m ops window status                 # 운영자 공인 IP 확인
python -m ops window open --acr
python -m ops deploy all                    # 이미지 4개 + 프로비저닝 + Functions zip(Policy 계측 수정 포함)
python -m ops smoke
```

- IP가 바뀌어 403이 나면 [deploy](../runbook/deploy.md#운영자-ip가-바뀔-때)의 `operator-alt` 절차를 따릅니다.
- Service Bus를 지워 둔 상태라면 먼저 `python -m ops resume`을 실행합니다(필수는 아니며, 없으면 인라인 모드로 동작합니다).

### 2. Policy 계측 수정이 반영됐는지 확인

Log Analytics의 `AppDependencies`에서 최근 30분 동안 `AppRoleName`이 `policy`인 호출 중 이름이 ` http send` 또는 ` http receive`로 끝나는 스팬이 **0건**이어야 합니다(원인과 현재 상태는 [limitations](../limitations.md)의 배포 상태 항목). PowerShell에서는 쿼리 인용이 깨지므로 Python `subprocess`로 실행합니다. 워크스페이스 ID는 `azd env get-values`의 `LOG_ANALYTICS_CUSTOMER_ID`입니다.

```python
import shutil, subprocess
query = (
    "AppDependencies | where TimeGenerated > ago(30m) and AppRoleName == 'policy' "
    "| where Name endswith ' http send' or Name endswith ' http receive' | count"
)
print(subprocess.run(
    [shutil.which("az"), "monitor", "log-analytics", "query", "-w", "<LOG_ANALYTICS_CUSTOMER_ID>",
     "--analytics-query", query, "-o", "json"],
    capture_output=True, text=True, check=True,
).stdout)
```

- 먼저 Policy를 거치는 신청을 한 건 이상 처리한 뒤(3단계 이후에 다시 확인해도 됩니다) 실행해야 의미가 있습니다. 로그 수집 지연이 있으니 비어 있으면 몇 분 뒤 다시 봅니다.
- 결과(건수)를 아래 기록의 비고에 적습니다.

### 3. 사전 건 준비

콘솔에서 [fallback](fallback.md#사전-준비-목록)의 표대로 만듭니다.

1. **A:** 신청 1건 제출 → `심사역 대기` → `승인`.
2. **B:** 신청 1건 승인 → 대출 상세에서 `사후 13주 재생` → 상태가 `재생 완료`가 될 때까지 기다리고 경보를 확인합니다. 이 대출의 심사 동의는 만료시키지 않습니다.
3. **C:** 신청 1건 승인 → 신청 상세에서 `지금 만료(데모)` → 삭제 증명이 `완료`인지 확인합니다.

신청 ID·대출 ID·동의 ID를 [fallback](fallback.md#사전-준비-목록) 표의 "ID" 열에 적습니다.

### 4. 대본 리허설

[demo-script](demo-script.md)대로 처음부터 끝까지 한 번 진행합니다.

- 장면마다 시작·끝 시각(KST)을 기록하고, 라이브 신청 ID와 동의 ID를 적습니다. 제출 직후 라이브 신청 ID(끝 6자리)를 메모합니다.
- 2분을 넘긴 장면은 원인을 비고에 적습니다.
- 대본의 전환 지점(0:50 판정·10초·15초 등)에 걸린 장면이 있으면 그 상황도 비고에 적습니다.
- 1:45 장면 뒤에 사전 대출 B와 사전 건 A가 그대로 열리는지(라이브 건의 동의 만료가 다른 건에 영향을 주지 않는지) 확인합니다.

### 5. 정리

```powershell
# operator-alt* 규칙을 추가했다면 Functions에서 제거(deploy 문서의 절차 4). 콘솔에 WAF 규칙 조건을 더했다면 다음 프로비저닝이 되돌린다
python -m ops window close
# close 뒤 Service Bus 구독 규칙이 subject-label인지 확인(선택, deploy 문서의 "Service Bus 구독 규칙 확인")
python -m ops fault status                  # source: off, policy: off
python -m ops pause
```

### 6. 기록 반영

1. 아래 기록표·"발견한 문제와 대본 수정"을 채웁니다.
2. 실측을 반영해 [demo-script](demo-script.md)의 계획 시간과 전환 지점을 고칩니다.
3. [limitations](../limitations.md)의 배포 상태 항목(Policy 계측 수정 반영 여부)을 갱신합니다.
4. [evidence](../evidence.md)의 리허설 행을 갱신합니다.
5. `$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python -m pytest -q -p no:cacheprovider tests/docs`가 통과하는지 확인합니다.

## 기록

리허설은 2026-11-03(KST)에 1회 실시했습니다. 발표자가 스톱워치를 기록하지 않아 시작·끝 시각은 콘솔 접속 로그에서 복원한 값이고, 로그에 없는 값은 "기록 없음"으로 둡니다. 계획 시간은 설계의 계획값입니다. 이 리허설은 탐색 위주였고 2분 대본 시간을 재지는 않았습니다.

| 장면 | 계획 | 실측 시작 | 실측 끝 | 소요 | ID | 비고 |
|---|---|---|---|---|---|---|
| 0:00 문제 제시 | 0:00~0:15 | 기록 없음(14:07경 탭 준비) | 기록 없음 | 기록 없음 | — | 14:07:00경 탭 준비·목록 확인. 정확한 0:00 시작 시각은 기록이 없음 |
| 0:15 신청과 실시간 진행 | 0:15~0:50 | 14:08:34 `제출` | 14:09:36 `심사역 대기` | 62초 | 신청 ID: `app-20261103-c452a1` | 제출 직후 "신청 문서가 아직 없습니다" 화면(설계대로, 5초 새로 고침) → 14:08:55 신청 문서 생성(접수, 제출 후 21초) → 14:09:36 `심사역 대기`. 계획 35초와 전환 지점 40초보다 길었음. 발표자는 대기 화면에서 목록으로 나갔다가 14:09:25에 돌아옴 |
| 0:50 심사 근거와 승인 | 0:50~1:20 | 14:10:31(승인 POST) | 기록 없음 | 기록 없음 | 승인이 들어간 건: `app-20261103-93e63b`(사전 건 C의 신청) | 승인이 라이브 건이 아니라 당시 `심사역 대기`였던 사전 건 C의 신청에 들어감. 목록에 `심사역 대기` 건이 둘이라 라이브 ID를 놓침. 라이브 건은 승인되지 않음. 반사실 설명은 없었고(이 건은 반사실 없음) `제안 구조`는 표시됨 |
| 1:20 조기경보 | 1:20~1:45 | 14:11:19 | 14:12:20 | 약 61초 | 대출 ID: `app-20261103-9c3d9e` | `/loans` 목록만 보고 사전 대출 B 상세는 열지 않음 |
| 1:45 동의 만료 | 1:45~2:00 | 14:13:05 철회 | 14:13:17 삭제 증명 `완료` | 약 12초 | 동의 ID: `970a05a5fc434ca7a5b4e02fdcfa1ebe` | `지금 만료(데모)` 대신 차주 화면(`/borrower/demo-cafe-001`)의 `동의 철회`로 진행 → 라이브 건이 `철회 종료`로 끝남. 삭제 증명 3건(Blob·동의 레지스트리·Foundry 대화) 모두 `확인`. 사전 대출 B의 동의는 영향 없음 |
| 전체 | 0:00~2:00 | 14:07:00경 | 14:13:20 | 약 6분 20초 | — | 탐색 위주. 2분 대본 시간 측정은 아님 |

기타 측정:

- 사전 건 3건의 `접수`→`심사역 대기`: 57초·37초·25초(사전 준비 때).
- 사전 대출 B(`app-20261103-9c3d9e`): Service Bus 모드 재생 14:00:45 시작 → 14:03:50 `재생 완료`(약 3.1분), `주간 흐름과 탐지기` 13줄, 경보 3건(1·2·8주차).
- 사전 건 C(신청 `app-20261103-93e63b`, 동의 `ceb450e1e3344249a2a4ae983d2f5f73`): 삭제 증명 `완료`.
- 사전 건 A(`app-20261103-a30edc`): 상태 `승인`. 이 건의 사후 재생은 Service Bus 구독 규칙 결함(규칙이 0개라 흐름 메시지가 버려져 재생이 진행되지 않고 멈춤) 때문에 멈춘 상태여서 쓰지 않고, 신청 상세만 씁니다. 재생 버튼 두 번 누름 경쟁은 부차적으로 중복 재생 실행과 모니터링 동의를 만들었을 뿐입니다(아래 결함 2건).
- Policy 계측 수정(I1) 확인: 최근 45분 Policy 요청 2건(라이브 리허설 건 포함)에서 ` http send`/` http receive` 스팬이 **0건**이었습니다.

## 운영 기록

리허설 날(2026-11-03, KST)의 운영 명령 시각입니다.

| 작업 | 시각 | 소요·결과 |
|---|---|---|
| `ops resume` | 09:43–09:48 | 5.0분 |
| `window open --acr` | 09:48–09:53 | 4.5분 |
| `deploy all` | 09:53–10:07 | 컨테이너 성공, orchestrator zip 실패(배포 중 운영자 공인 IP가 바뀜) |
| 창 다시 열기 | 10:27–10:31 | — |
| `deploy orchestrator` | 10:34–10:40 | 성공 |
| `smoke` | 10:40–10:43 | 성공 |
| 결함 수정 뒤 `deploy orchestrator` | 13:50–13:58 | 7.6분 |
| 임시 IP 규칙 제거 | 14:21–14:22 | — |
| `window close` | 14:22:40–14:27:39 | 5.0분(프로비저닝 4분 53초) |
| `fault status` | `window close` 뒤 | `source: off`, `policy: off` |
| `pause` | 14:28:16–14:35:04 | 6.8분(프로비저닝 4분 2초 + Service Bus 삭제) |

- 회사 NAT 출구 IP가 하루에 여러 번 바뀌었습니다(4개 관측). 바뀔 때마다 console ingress와 Functions 양쪽에 `/32` 임시 규칙(`operator-alt`, `operator-alt2`, `operator-alt3` …)을 추가했고 정리 때 모두 지웠습니다. Functions 규칙 우선순위는 110부터 하나씩 올렸습니다.
- `window open`(프로비저닝)은 Functions의 임시 `operator-alt*` 규칙을 지웁니다(실측 확인). 창을 다시 열었으면 규칙을 다시 추가했습니다.
- I1(Policy 계측): 최근 45분 Policy 요청 2건 중 ` http send`/` http receive` 스팬 0건 → 반영 확인.

## 발견한 문제와 대본 수정

1. **제출→`심사역 대기`가 62초로 전환 지점(40초)보다 길었습니다.** 0:50 장면의 판정 기준을 "0:50에 라이브 건이 `심사역 대기`가 아니면 사전 건 A로"로 바꿨습니다. 2분 일정표는 유지하므로 사전 건 A 경로가 사실상 기본입니다.
2. **라이브 ID를 놓쳐 다른 건을 승인했습니다.** 목록에 `심사역 대기` 건이 둘이었고 사전 건 C의 신청이 승인됐습니다. 제출 직후 신청 ID를 메모하고 목록의 다른 `심사역 대기` 건은 승인하지 않도록 대본·대비책에 적었습니다.
3. **1:45 장면에서 차주 화면의 `동의 철회`를 썼습니다.** 신청이 `철회 종료`로 끝났습니다. `지금 만료(데모)`를 라이브 건에 승인 여부와 관계없이 누르도록 고쳤고(만료는 신청 상태를 바꾸지 않음), 차주 화면 철회는 쓰지 않도록 적었습니다.
4. **사전 대출 B 상세를 열지 않았습니다.** `/loans` 목록만 봤습니다. 탭 3을 B 상세에 고정해 목록으로 가지 않도록 적었습니다.
5. **리허설 준비 중 결함 2건을 발견·수정했습니다**(Task 11a, 커밋 `8cfe7d1`, `6c003a5`). 대본 자체의 수정은 아닙니다.
   - Service Bus 구독 규칙: Bicep이 `$Default` 이름으로 CorrelationFilter를 PUT하면 ARM은 200을 돌려주지만 규칙이 남지 않고 자동 `1=1` 규칙도 사라져, 두 구독(`flow.updated/early-warning`, `alert.raised/collections`)의 규칙이 0개가 되어 Service Bus 모드 사후 재생 메시지가 모두 버려졌습니다(그전 재생 실측은 모두 인라인 모드). `$Default` PUT은 유지하고 `subject-label` CorrelationFilter 규칙을 추가했으며, 수정 배포 뒤 `window close` 재프로비저닝 후에도 `subject-label`만 남는 것을 확인했습니다.
   - 재생 버튼 두 번 누름 경쟁: `replay_in_progress` 확인이 원자적이지 않아 두 실행이 모두 통과하고 모니터링 동의가 2개 생겼습니다. `start_replay`가 ETag 조건부 교체로 대출을 먼저 선점하고, 실패하면 "동시 재생 요청 무시"로 끝나게 했습니다.
   - 수정 뒤 Service Bus 모드 첫 라이브 재생이 성공했습니다(사전 대출 B, 약 3.1분. 인라인 모드 참고 실측 132초보다 김).
6. **회사 NAT 출구 IP가 회전했습니다.** 임시 규칙이 여러 개 필요했고(`operator-alt`, `operator-alt2` …), 프로비저닝이 Functions 임시 규칙을 지웠습니다. [deploy](../runbook/deploy.md#운영자-ip가-바뀔-때)에 반영했습니다.
