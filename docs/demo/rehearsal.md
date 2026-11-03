# 데모 리허설 (rehearsal)

실제 Azure 환경에서 [demo-script](demo-script.md)를 처음부터 끝까지 한 번 돌려 장면별 시간을 재고, 사전 건 ID를 [fallback](fallback.md)에 채우는 절차와 기록표입니다. 리허설 전에는 기록표가 비어 있습니다.

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

- 장면마다 시작·끝 시각(KST)을 기록하고, 라이브 신청 ID와 동의 ID를 적습니다.
- 2분을 넘긴 장면은 원인을 비고에 적습니다.
- 대본의 전환 지점(40초·10초·15초 등)에 걸린 장면이 있으면 그 상황도 비고에 적습니다.
- 1:45 장면 뒤에 사전 대출 B와 사전 건 A가 그대로 열리는지(라이브 건의 동의 만료가 다른 건에 영향을 주지 않는지) 확인합니다.

### 5. 정리

```powershell
# operator-alt 규칙을 추가했다면 console ingress에서 제거(deploy 문서의 절차 4)
python -m ops window close
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

값은 리허설 뒤에 채웁니다. "리허설 전"은 아직 측정하지 않았다는 표시이며, 계획 시간은 설계의 계획값입니다.

| 장면 | 계획 | 실측 시작 | 실측 끝 | 소요 | ID | 비고 |
|---|---|---|---|---|---|---|
| 0:00 문제 제시 | 0:00~0:15 | 리허설 전 | 리허설 전 | 리허설 전 | — | 리허설 전 |
| 0:15 신청과 실시간 진행 | 0:15~0:50 | 리허설 전 | 리허설 전 | 리허설 전 | 신청 ID: 리허설 전 | 리허설 전 |
| 0:50 심사 근거와 승인 | 0:50~1:20 | 리허설 전 | 리허설 전 | 리허설 전 | 리허설 전 | 리허설 전 |
| 1:20 조기경보 | 1:20~1:45 | 리허설 전 | 리허설 전 | 리허설 전 | 대출 ID: 리허설 전 | 리허설 전 |
| 1:45 동의 만료 | 1:45~2:00 | 리허설 전 | 리허설 전 | 리허설 전 | 동의 ID: 리허설 전 | 리허설 전 |
| 전체 | 0:00~2:00 | 리허설 전 | 리허설 전 | 리허설 전 | — | 리허설 전 |

## 발견한 문제와 대본 수정

(리허설 전)
