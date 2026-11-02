# P6b 운영 지표 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 배포된 신청 여정에 합성 신청 24건과 장애 시나리오 2개를 실제 Azure에서 흘려, SLO·건당 비용·공정성 격차·복원력 동작을 측정값(합성 데이터 기준)으로 남긴다.

**Architecture:** 활동이 결과에 `_meter`(시각·모델별 토큰·판정)를 붙이고 오케스트레이터가 신청 문서 `steps[]`로 옮긴다. 순수 계산 모듈 `opsmetrics`가 SLO·비용·공정성을 계산하고, 로컬 `ops`(보고서)·VNet 안 `job-ops`(제출·투영)·console `/ops`(공정성 화면)가 같은 함수를 쓴다. OTel(Functions·Policy)은 분산 트레이스·Workbook·경보용이다.

**Tech Stack:** Python 3.12, Azure Functions Durable(Flex), FastAPI, `azure-monitor-opentelemetry==1.8.11`, `opentelemetry-instrumentation-httpx==0.66b1`, Container Apps Job, Cosmos DB, Storage Queue, Bicep(`Microsoft.Insights/workbooks`, `scheduledQueryRules`, Foundry 프로젝트 연결), Cost Management Query API, Log Analytics KQL.

**Spec:** `docs/superpowers/specs/2026-11-02-p6b-ops-metrics-design.md`

**범위 밖:** console·mockapi OTel 계측, 분산 서킷브레이커, Durable 재개 장애 시나리오, 심사역 결정 자동 입력, 부하 시험(동시 수백 건), 문서·ADR·위협 모델·데모 대본(P6c).

---

## 사용자 결정

| 항목 | 결정 |
|---|---|
| 측정 트래픽 | `ops load --count 24`가 `job-ops`로 합성 신청 24건 제출. 수동 신청은 집계 대상이 아님 |
| 배치 구성 | 원형 4종 × 6명 균형 격자 `load-<원형>-NN`, 업종·지역·업력을 원형마다 값별 2건씩 배정(전체 값별 8건) |
| 승인율 기준 | 에이전트 추천 approve율, 정책 eligible율. 심사역 결정은 있으면 별도 열 |
| OTel 범위 | Functions(워커) + Policy. console·mockapi 제외 |
| 대시보드 | 공정성 = console `/ops`. SLO·단계 성공률·비용 추이 = Workbook + 경보 2개 |
| 장애 시나리오 | ① mockapi flaky(첫 요청 503) → 재시도 회복 ② Policy 503 → 서킷 open → fail-closed |
| 비용 | 실측 토큰 × `src/evals/prices.json` + Cost Management 최근 완료일 평균 × 30(Foundry Models 제외) ÷ 가정 물량. 수작업 원가 $3,500(HBS WP 15-004 범위 $3,000~4,000 중간값) |
| 로컬 ↔ Azure | Cosmos·큐는 사설 엔드포인트 전용이라 `job-ops`(평가 이미지 재사용, `id-ops`)가 제출·읽기, 로컬은 Job 시작·로그 회수·Cost·KQL·보고서 |
| 예산 | 상한 $5. 첫 3건 실측 평균 × 건수 > $4면 Job이 제출 중단 |

## Global Constraints

- 모든 숫자에 "합성 데이터 기준"을 붙인다(보고서 머리말, console `/ops` 머리말, README).
- 텔레메트리 속성·로그·투영에 원문 PII(상호·대표자명·사업자번호·적요)를 넣지 않는다. 허용: `application_id`, `applicant_id`(합성 ID), 세그먼트, 단계·판정·토큰 수.
- 활동 결과 객체를 고치지 않는다. `_meter`를 붙일 때도, 뗄 때도 새 dict를 만든다.
- 활동 입력의 `_tags`(source·run_id)는 값이 있을 때만 넣는다(기존 입력 동등 비교 테스트 유지). `run_metered`가 활동 함수에 넘기기 전에 뗀다.
- 오케스트레이터는 결정적이어야 한다. `steps[]` 시각은 `_meter`(활동이 기록, 재생 시 같은 값) 또는 `ctx.current_utc_datetime`만 쓴다. 오케스트레이터 안에서 스팬·로그를 만들지 않는다.
- 측정·텔레메트리 실패가 여정을 막지 않는다: `_meter` 누락·형식 오류는 결과로 판정하고 usage는 빈 목록, OTel 초기화 실패는 경고만.
- 로컬 venv에는 `azure.cosmos`·`azure.storage`·`azure.monitor`가 없다. 해당 SDK는 지연 import하고, 테스트는 가짜 객체로 한다.
- `opsjob`은 `ops`·`journey`(오케스트레이터)를 import하지 않는다(평가 이미지에 없음). `opsmetrics`·`shared`·`evals`만 쓴다.
- 의존성은 `==`로 고정한다(고정 버전 테스트).
- 테스트 명령: `$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python -m pytest -q -p no:cacheprovider` (기준선 717 passed / 94 skipped, `RUN_INFRA_TESTS=1`이면 811).
- 커밋 트레일러:
  ```
  Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
  Copilot-Session: 05f1ca1b-6073-417d-bfb1-6b546f82cb58
  ```

### Spec 대비 변경(계획 단계 확정, Spec에도 반영)

| Spec | 변경 | 이유 |
|---|---|---|
| §3.2 | 세그먼트는 오케스트레이터가 `underwriting["features"]`(industry·region·business_age_months)에서 계산 | 활동이 profile을 따로 돌려줄 필요 없음. 원천 값과 같음 |
| §3.3 | `attempts` 필드 삭제 | Durable 재시도 횟수는 활동 안에서 알 수 없음. 원천별 시도는 `assembly.sources[*].attempts`에 이미 있음 |
| §3.3 | `assemble`은 `status != complete` **또는** `agent_fallback` 플래그면 `degraded` | 에이전트 실패 후 결정론 조립으로 완료한 경우도 안전망 경유 |
| §3.3 | `classify` 하위 단계 시각 = `underwrite` 활동 시각 | 분류는 underwrite 활동 안에서 일어남 |
| §7.2 | 투영의 정책 필드는 `policy.decision`·`policy.reason_codes` | `PolicyDecision`에 `eligible`·`rule_hits` 필드가 없음 |
| §5.3 | Foundry 프로젝트 ID의 Metrics Publisher 역할은 `ops-insights.bicep`에 둠 | 연결과 역할을 한 모듈에 |
| §8 | Cost Management 429는 고정 백오프(20초 × 5회) | `az rest`가 retry-after 헤더를 돌려주지 않음 |
| §6.3 | KQL은 `az monitor log-analytics query`로 워크스페이스 테이블(`AppDependencies`·`AppRequests`·`AppTraces`) 조회 | P6a와 같은 경로 |
| §6.2 | `policy-down` 시나리오 제출 간격 90초(`source-flaky`는 5초) | 서킷 cooldown(60초)보다 길게 둬 각 신청이 half-open 시험 → 재open을 거치게 함 |
| §7.2 | 측정 결과 `ops/reports/<run_id>/`를 커밋 | `eval/reports`와 같은 관례. 투영 허용 필드만 있어 원문 PII 없음 |
| §1.2 | console `/ops`는 고정비를 계산하지 않음(건당 총비용 "-", 보고서 안내) | console 관리 ID에 Cost Management 권한을 주지 않음 |

## Review Focus

| # | 위험 지점 | 기대 동작 | 고정 테스트 |
|---|---|---|---|
| 1 | `_meter` 누락·형식 오류 | 결과로 판정, usage `[]`, 여정 계속 | `test_journey_steps.py::test_missing_or_bad_meter_falls_back_to_result` |
| 2 | 활동 결과 변형 | 원본 dict 불변, 문서에 `_meter` 없음 | `test_metering.py::test_run_metered_does_not_mutate_result`, `test_journey_steps.py::test_meter_is_not_left_in_document` |
| 3 | `_tags`는 값이 있을 때만 | 기존 `assess_credit` 입력 동등 비교 유지 | 기존 `test_assessment_runs_after_policy_without_retry` + `test_meta_and_tags_are_passed_only_when_present` |
| 4 | 서킷 half-open 단일 시험 | 시험 1건만 통과, 성공→closed, 실패→open 재시작 | `test_circuit.py::test_half_open_allows_single_trial` |
| 5 | 4xx는 서킷 실패가 아님 | 422는 PolicyUnavailable이지만 실패 카운트 안 함 | `test_circuit.py::test_client_error_does_not_trip_breaker` |
| 6 | 장애 원복 `finally` | 중간 실패에도 원복 시도, 원복 실패는 원래 오류를 원인으로 OpsError | `test_faults.py::test_scenario_reverts_on_failure`, `test_revert_failure_is_reported` |
| 7 | 기존 데모 신청자 불변 | persona 인자 추가 후 5명 digest 동일 | `test_demo.py::test_demo_cases_unchanged` |
| 8 | 세그먼트 표본 < 5 | 격차 계산에서 제외, "표본 부족" | `test_opsmetrics.py::test_small_groups_are_excluded_from_gap` |
| 9 | Job 시간 초과·미도달 | 미도달 ID·상태를 summary에 남기고 도달분만 계산 | `test_opsjob.py::test_wait_reports_unreached` |
| 10 | Cost Management 실패 | 고정비 "측정 불가(사유)", 나머지 지표 계속, `--fixed-monthly-usd` 수동 입력 | `test_opsrun.py::test_metrics_without_cost_access_still_reports` |
| 11 | `id-ops` 최소 권한 | 큐 Sender(stFunc), Cosmos Data Reader(database), AcrPull만 | `test_ops_job.py::test_ops_identity_is_least_privilege` |
| 12 | `jobs.py` 분리 후 평가 실행 유지 | 기존 `test_eval_runner.py` 전부 통과 | 기존 테스트 |
| 13 | 예산 가드 | 첫 3건 평균 × 건수 > $4면 나머지 미제출, summary `aborted` | `test_opsjob.py::test_budget_guard_stops_submission` |

## 사전 검증(probe) 결과

Spec 끝 "Probe 결과 반영" 표(P1~P7)를 따른다. 요약:

- P1·P2: 워커 `configure_azure_monitor(credential=ManagedIdentityCredential)` 동작, httpx `traceparent`로 활동 → Policy가 한 `operation_Id`. Policy는 healthz·send/receive 스팬 제외 설정 필요.
- P3: Foundry 응답 `usage.input_tokens`·`output_tokens` 존재.
- P4: Foundry 프로젝트 App Insights 연결(`ProjectManagedIdentity`, `metadata.ApplicationInsightsConnectionString` 필수) + 프로젝트 시스템 ID Metrics Publisher → 에이전트 서비스 트레이스. **probe 때 수동 생성한 역할 할당(principal `6af24207-91fb-4394-a08c-0ad350542b00`, appi 범위)은 bicep 배포 전에 지워야 RoleAssignmentExists를 피한다.** 연결은 같은 이름이면 PUT으로 덮어써진다.
- P5: `az containerapp update --set-env-vars` 약 22초, 새 리비전 Healthy까지 약 39초, 설정 유지.
- P6: Cost Management RG 범위 Query 가능, 429 잦음, 약 1일 지연, `Custom` timePeriod 필요.
- P7: 로컬에서 Cosmos·큐 접근 불가 → `job-ops`.

## 알려진 한계

- Python Durable은 오케스트레이터 → 활동 트레이스를 잇지 않는다. 활동 스팬은 각각 루트이고, 신청 단위 연결은 `application_id` KQL 조인으로 한다.
- 서킷브레이커는 Flex 인스턴스(프로세스)마다 따로다. 분산 서킷은 운영 전환 과제.
- AgentError로 끝난 에이전트 호출은 토큰을 알 수 없어 `calls`만 센다. 성공 호출은 캐시 할인 미반영(상한 추정).
- 24건은 SLO 표본 수준이며 용량 시험이 아니다. p95·99%는 표본 수와 함께 보고한다.
- 원형 통제 격차는 원형·값당 2건으로 계산하므로 잡음이 크다. 해석용이며 경보 판정에는 전체 격차만 쓴다.
- `genAIContent`(Foundry 트레이스)에 프롬프트·응답이 남는다. 에이전트 입력이 가명화된 값뿐이라는 기존 원칙에 기댄다.

## 배포 위험

| 위험 | 대응 |
|---|---|
| probe 수동 역할 할당과 bicep 역할 충돌(RoleAssignmentExists) | T14 시작 시 `az role assignment delete`로 먼저 지움 |
| SCM 403(회사 이그레스 IP 2개) | `ops window open` 뒤 다른 IP를 `operator-alt`(우선순위 110) 임시 규칙으로 추가, 작업 후 삭제 |
| mockapi 재시작으로 인메모리 동의 레지스트리 초기화 | 배치 → 장애 순서로 실행. 시나리오 ①은 mockapi 리비전 전환 후 새 신청만 사용 |
| `requirements-eval.txt`에 SDK 추가로 평가 이미지 변경 | 평가 Job 기존 동작은 영향 없음(추가만). `ops deploy eval`로 한 번에 갱신 |
| Workbook·경보 bicep 스키마 경고 | `az bicep build` 경고 0이 목표, 경고가 남으면 사유를 체크박스에 기록 |
| 장애 토글 원복 실패 | `finally` 원복 + `ops fault status` + 다음 `azd provision`이 bicep 값(FAULT_MODE 없음)으로 되돌림 |

## 파일 구조

| 파일 | 역할 |
|---|---|
| `src/agents/runtime.py` (수정) | `AgentRun.input_tokens`·`output_tokens`·`model` |
| `src/shared/segments.py` (신규) | 업력 구간·지역 묶음·`segment_of`, 균형 격자 `LOAD_GRID` |
| `src/shared/otel.py` (신규) | `configure_monitor(logger_name, env, instrument_httpx)` — 지연 import, 실패 시 False |
| `src/orchestrator/journey/metering.py` (신규) | `MeteredRunner`, `outcome_of`, `run_metered`, `step_records`, `failed_record` |
| `src/orchestrator/journey/telemetry.py` (신규) | `setup`, `span`, `record_step`, `circuit_changed` |
| `src/orchestrator/journey/circuit.py` (신규) | `CircuitBreaker` |
| `src/orchestrator/journey/models.py` (수정) | `StartRequest.submitted_at`·`source`·`run_id` |
| `src/orchestrator/journey/orchestration.py` (수정) | `steps[]`·`segment`·태그 기록 |
| `src/orchestrator/journey/adapters.py` (수정) | `HttpPolicyApi(breaker=)`, `build_deps`가 브레이커 연결 |
| `src/orchestrator/function_app.py` (수정) | 계량 활동 5개를 `run_metered`로 감쌈, 시작 시 `setup()` |
| `src/policy/app.py`, `main.py` (수정) | `FAULT_MODE` 미들웨어, OTel 초기화 |
| `src/mockapis/app.py`, `synth.py`, `demo.py`, `main.py` (수정) | `flaky` 모드, `FAULT_MODE`, 격자 신청자 |
| `src/opsmetrics/` (신규) | `projection`, `slo`, `cost`, `fairness`, `summary`, `prices` |
| `src/opsjob/` (신규) | `run.py`(load·submit·collect), `azure.py`(Cosmos·Queue 어댑터), `__main__.py` |
| `src/ops/jobs.py` (신규) | Job 시작(env 덮어쓰기)·대기·로그 회수 공통 |
| `src/ops/eval_runner.py` (수정) | `jobs.py` 사용 |
| `src/ops/opsrun.py`, `faults.py`, `costs.py`, `kql.py`, `opsreport.py` (신규) | load·fault·metrics 흐름 |
| `src/ops/cli.py` (수정) | `load`, `metrics`, `fault` 하위 명령 |
| `src/console/app.py`, `adapters.py`, `templates/ops.html`, `templates/base.html`, `Dockerfile` (수정·신규) | `/ops` |
| `infra/modules/ops-insights.bicep` (신규) | Workbook, 경보 2개, Action Group(선택), Foundry 연결, 프로젝트 ID 역할 |
| `infra/modules/eval-job.bicep`, `identities.bicep`, `monitoring.bicep`, `resources.bicep`, `main.bicep` (수정) | `job-ops`, `id-ops`, OTel 설정 |
| `requirements-api.txt`, `src/orchestrator/requirements.txt`, `requirements-eval.txt`, `src/evals/Dockerfile` (수정) | 의존성·이미지 |
| `README.md` (수정) | P6b 실측 절 |

---

## Task 1: AgentRun 입력·출력 토큰과 모델

**Files:**
- Modify: `src/agents/runtime.py` (AgentRun ~65, `_tokens` ~91, `_loop` ~203–252)
- Test: `tests/agents/test_runtime.py`

- [ ] **Step 1: 실패 테스트 작성** — `tests/agents/test_runtime.py` 끝에 추가

```python
def usage_round(*calls: dict, inp: int, out: int) -> httpx.Response:
    usage = {"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out}
    return httpx.Response(200, json={"status": "completed", "output": list(calls), "usage": usage})


def usage_done(obj, inp: int, out: int) -> httpx.Response:
    message = {"type": "message", "content": [{"type": "output_text", "text": json.dumps(obj)}]}
    usage = {"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out}
    return httpx.Response(200, json={"status": "completed", "output": [message], "usage": usage})


def test_run_accumulates_input_output_tokens_and_model():
    fake = FakeFoundry(
        usage_round(fc("c1", "fetch_source", {"source": "profile"}), inp=40, out=10),
        usage_done({"status": "complete"}, inp=70, out=30),
    )
    run = agents(fake).run(SPEC, "신청", {"fetch_source": lambda a: {"ok": 1}})
    assert (run.input_tokens, run.output_tokens, run.total_tokens) == (110, 40, 150)
    assert run.model == "gpt-5.4-mini"
    assert "input_tokens" not in run.record()  # 감사 기록 형식은 그대로


def test_missing_usage_split_counts_zero():
    run = agents(FakeFoundry(done({"status": "complete"}, tokens=100))).run(SPEC, "신청", {})
    assert (run.input_tokens, run.output_tokens, run.total_tokens) == (0, 0, 100)
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python -m pytest -q -p no:cacheprovider tests/agents/test_runtime.py -k "tokens"`
Expected: FAIL (`AttributeError: 'AgentRun' object has no attribute 'input_tokens'`)

- [ ] **Step 3: 구현** — `src/agents/runtime.py`

`AgentRun`에 필드 추가(`containers` 다음, 기본값 있음 → 기존 생성 코드 호환):

```python
    containers: tuple[str, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
```

`_tokens` 아래에 추가(`_tokens`는 그대로 둔다):

```python
def _usage(response: dict[str, Any]) -> tuple[int, int, int]:
    usage = response.get("usage") or {}
    return (
        int(usage.get("input_tokens") or 0),
        int(usage.get("output_tokens") or 0),
        int(usage.get("total_tokens") or 0),
    )
```

`_loop`에서 누적을 바꾼다:

```python
        response = respond(input_text)
        inp, out, tokens = _usage(response)
        tool_calls: list[ToolCall] = []
        rounds = 0
        while pending := [...]:            # 기존 그대로
            ...
            response = respond(outputs)
            i, o, t = _usage(response)
            inp, out, tokens = inp + i, out + o, tokens + t
        ...
        return AgentRun(
            spec.name, version, conversation_id, output, tool_calls, tokens, rounds, tuple(containers),
            input_tokens=inp, output_tokens=out, model=spec.model,
        )
```

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python -m pytest -q -p no:cacheprovider tests/agents`
Expected: PASS (기존 런타임·에이전트 테스트 포함)

- [ ] **Step 5: 커밋**

```powershell
git add src/agents/runtime.py tests/agents/test_runtime.py
git commit -m "feat(agents): record input/output tokens and model on AgentRun" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com`nCopilot-Session: 05f1ca1b-6073-417d-bfb1-6b546f82cb58"
```

---

## Task 2: 세그먼트와 활동 계량(`_meter`)

**Files:**
- Create: `src/shared/segments.py`, `src/orchestrator/journey/metering.py`, `src/orchestrator/journey/telemetry.py`(이 태스크에서는 no-op 골격, T5에서 채움)
- Test: `tests/shared/test_segments.py`, `tests/orchestrator/test_metering.py`

- [ ] **Step 1: 세그먼트 실패 테스트** — `tests/shared/test_segments.py`

```python
from collections import Counter

import pytest

from shared.segments import AGE_BANDS, LOAD_GRID, LOAD_PER_ARCHETYPE, age_band, region_of, segment_of


@pytest.mark.parametrize(("months", "band"), [(0, "<1년"), (11, "<1년"), (12, "1~3년"), (35, "1~3년"), (36, "3년+")])
def test_age_band(months, band):
    assert age_band(months) == band and band in AGE_BANDS


def test_region_is_first_word():
    assert region_of("서울 성동구") == "서울" and region_of("부산") == "부산"


def test_segment_of_features():
    f = {"industry": "카페", "region": "경기 성남시", "business_age_months": 24, "dscr": 1.2}
    assert segment_of(f) == {"industry": "카페", "region": "경기", "business_age_band": "1~3년"}


@pytest.mark.parametrize("bad", [None, {}, {"industry": "카페"}, {"industry": "카페", "region": "", "business_age_months": "x"}])
def test_segment_of_bad_features_is_none(bad):
    assert segment_of(bad) is None


def test_load_grid_is_balanced_and_interleaved():
    assert len(LOAD_GRID) == 24 and len({a.applicant_id for a in LOAD_GRID}) == 24
    assert [a.archetype for a in LOAD_GRID[:4]] == ["healthy", "mismatch", "stress", "thin"]
    for arch in ("healthy", "mismatch", "stress", "thin"):
        rows = [a for a in LOAD_GRID if a.archetype == arch]
        assert len(rows) == LOAD_PER_ARCHETYPE
        for axis in ("industry", "region"):
            assert set(Counter(getattr(a, axis) for a in rows).values()) == {2}
        assert set(Counter(age_band(a.business_age_months) for a in rows).values()) == {2}
    assert all(a.applicant_id.startswith(f"load-{a.archetype}-") for a in LOAD_GRID)
    assert len({a.seed for a in LOAD_GRID}) == 24
```

- [ ] **Step 2: 실패 확인** — `pytest tests/shared/test_segments.py` → FAIL(`ModuleNotFoundError`)

- [ ] **Step 3: 구현** — `src/shared/segments.py`

```python
"""공정성 세그먼트(업종·지역·업력 구간)와 운영 지표용 합성 신청자 균형 격자."""

from dataclasses import dataclass
from typing import Any

AGE_BANDS = ("<1년", "1~3년", "3년+")


def age_band(months: int) -> str:
    if months < 12:
        return AGE_BANDS[0]
    return AGE_BANDS[1] if months < 36 else AGE_BANDS[2]


def region_of(region: str) -> str:
    return region.split()[0]


def segment_of(features: Any) -> dict[str, str] | None:
    try:
        return {
            "industry": str(features["industry"]),
            "region": region_of(features["region"]),
            "business_age_band": age_band(int(features["business_age_months"])),
        }
    except (KeyError, TypeError, ValueError, IndexError, AttributeError):
        return None


LOAD_ARCHETYPES = ("healthy", "mismatch", "stress", "thin")
LOAD_INDUSTRIES = ("카페", "한식", "제과점")
LOAD_REGIONS = ("서울 성동구", "부산 해운대구", "경기 성남시")
LOAD_AGES = (8, 24, 38)
LOAD_PER_ARCHETYPE = 6
LOAD_SEED_BASE = 5000


@dataclass(frozen=True)
class LoadApplicant:
    applicant_id: str
    archetype: str
    seed: int
    industry: str
    region: str
    business_age_months: int


def _grid() -> tuple[LoadApplicant, ...]:
    rows = []
    for j in range(LOAD_PER_ARCHETYPE):  # j 우선: 원형이 번갈아 나오게(부분 제출도 균형)
        for a, arch in enumerate(LOAD_ARCHETYPES):
            rows.append(
                LoadApplicant(
                    applicant_id=f"load-{arch}-{j + 1:02d}",
                    archetype=arch,
                    seed=LOAD_SEED_BASE + a * 100 + j,
                    industry=LOAD_INDUSTRIES[(j + a) % 3],
                    region=LOAD_REGIONS[(j + j // 3 + a) % 3],
                    business_age_months=LOAD_AGES[(j + 2 * (j // 3) + a) % 3],
                )
            )
    return tuple(rows)


LOAD_GRID = _grid()
```

`segment_of`의 `region`이 빈 문자열이면 `"".split()[0]`이 `IndexError` → None(테스트 4번째 경우는 `business_age_months="x"`가 먼저 ValueError를 내지 않도록 dict 순서상 region에서 먼저 IndexError가 나도 None).

- [ ] **Step 4: 통과 확인** — `pytest tests/shared/test_segments.py` → PASS

- [ ] **Step 5: 계량 실패 테스트** — `tests/orchestrator/test_metering.py`

```python
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from journey import metering
from journey.metering import METER_KEY, MeteredRunner, failed_record, outcome_of, run_metered, step_records

T0 = datetime(2026, 11, 2, 1, 0, tzinfo=UTC)


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        self.now += timedelta(seconds=2)
        return self.now


@dataclass(frozen=True)
class FakeDeps:
    clock: Clock
    agents: object = None


class Runner:
    def __init__(self, fail=False):
        self.fail = fail

    def run(self, spec, text, handlers):
        if self.fail:
            raise RuntimeError("agent down")
        return SimpleNamespace(rounds=1, input_tokens=100, output_tokens=20)

    def delete_conversation(self, cid):
        return "deleted"


SPEC = SimpleNamespace(model="gpt-5.4-mini")


def test_metered_runner_records_usage_and_delegates():
    r = MeteredRunner(Runner())
    r.run(SPEC, "x", {})
    with pytest.raises(RuntimeError):
        MeteredRunner(Runner(fail=True)).run(SPEC, "x", {})
    assert r.entries() == [{"model": "gpt-5.4-mini", "input_tokens": 100, "output_tokens": 20, "calls": 2}]
    assert r.delete_conversation("c") == "deleted"


def test_failed_agent_call_counts_call_only():
    r = MeteredRunner(Runner(fail=True))
    with pytest.raises(RuntimeError):
        r.run(SPEC, "x", {})
    assert r.entries() == [{"model": "gpt-5.4-mini", "input_tokens": 0, "output_tokens": 0, "calls": 1}]


@pytest.mark.parametrize(
    ("step", "result", "outcome"),
    [
        ("assemble", {"status": "complete", "flags": []}, "ok"),
        ("assemble", {"status": "complete", "flags": ["agent_fallback"]}, "degraded"),
        ("assemble", {"status": "partial", "flags": []}, "degraded"),
        ("assess", {"assessment": {"recommendation": "approve"}}, "ok"),
        ("assess", {"assessment": None, "record": {"error": "agents_disabled"}}, "failed"),
        ("policy", {"decision": "eligible"}, "ok"),
    ],
)
def test_outcome_of(step, result, outcome):
    assert outcome_of(step, result)[0] == outcome


def test_run_metered_strips_tags_wraps_agents_and_meters():
    seen = {}

    def activity(deps, payload):
        seen["payload"] = payload
        deps.agents.run(SPEC, "x", {})
        return {"status": "complete", "flags": []}

    deps = FakeDeps(Clock(), Runner())
    out = run_metered("assemble_data", activity, deps, {"application_id": "a-1", "_tags": {"source": "load"}})
    assert seen["payload"] == {"application_id": "a-1"}
    meter = out[METER_KEY]
    assert meter["outcome"] == "ok" and meter["usage"][0]["calls"] == 2
    assert meter["started_at"] < meter["ended_at"]
    assert out["status"] == "complete"


def test_run_metered_does_not_mutate_result():
    original = {"status": "complete", "flags": []}
    out = run_metered("assemble_data", lambda d, p: original, FakeDeps(Clock(), None), {"application_id": "a"})
    assert METER_KEY not in original and out is not original


def test_run_metered_records_classify_substep():
    uw = {"features": {}, "classification": {"metrics": {"decision_calls": 3}}}
    out = run_metered("underwrite", lambda d, p: uw, FakeDeps(Clock(), Runner()), {"application_id": "a"})
    (sub,) = out[METER_KEY]["substeps"]
    assert sub["step"] == "classify" and sub["outcome"] == "ok"
    assert {"model": "decision-1", "input_tokens": 0, "output_tokens": 0, "calls": 3} in sub["usage"]
    assert out[METER_KEY]["usage"] == []


def test_classify_error_is_failed_and_missing_is_absent():
    err = run_metered("underwrite", lambda d, p: {"classification": {"error": "X: y"}}, FakeDeps(Clock()), {})
    none = run_metered("underwrite", lambda d, p: {"classification": None}, FakeDeps(Clock()), {})
    assert err[METER_KEY]["substeps"][0]["outcome"] == "failed"
    assert none[METER_KEY]["substeps"] == []


def test_run_metered_logs_and_reraises(monkeypatch):
    logged = []
    monkeypatch.setattr(metering.telemetry, "record_step", lambda rec, **kw: logged.append(rec))
    with pytest.raises(ValueError):
        run_metered("underwrite", lambda d, p: (_ for _ in ()).throw(ValueError("bad")), FakeDeps(Clock()), {})
    assert logged[0]["outcome"] == "failed" and logged[0]["detail"].startswith("ValueError")


def test_step_records_shape_and_fallback():
    t1, t2 = T0, T0 + timedelta(seconds=3)
    meter = {"started_at": t1.isoformat(), "ended_at": t2.isoformat(), "outcome": "ok", "usage": [], "substeps": []}
    (rec,) = step_records("policy", meter, {"decision": "eligible"}, T0, T0)
    assert rec == {"step": "policy", "outcome": "ok", "started_at": t1.isoformat(), "ended_at": t2.isoformat(),
                   "duration_ms": 3000, "agent": False, "usage": []}
    (bad,) = step_records("assemble", "garbage", {"status": "partial"}, t1, t2)
    assert (bad["outcome"], bad["usage"], bad["duration_ms"], bad["agent"]) == ("degraded", [], 3000, True)


def test_failed_record_truncates_detail():
    rec = failed_record("underwrite", T0, T0 + timedelta(seconds=1), RuntimeError("x" * 500))
    assert rec["outcome"] == "failed" and len(rec["detail"]) == metering.DETAIL_MAX
```

- [ ] **Step 6: 실패 확인** — `pytest tests/orchestrator/test_metering.py` → FAIL(`ModuleNotFoundError: journey.metering`)

- [ ] **Step 7: 텔레메트리 골격** — `src/orchestrator/journey/telemetry.py` (T5에서 내용 채움)

```python
"""활동 스팬·단계 로그. OTel이 꺼져 있으면 아무것도 하지 않는다(T5에서 구현)."""

from contextlib import nullcontext
from typing import Any


def span(name: str, attributes: dict[str, Any]):
    return nullcontext()


def set_attrs(span: Any, attributes: dict[str, Any]) -> None:
    return None


def record_step(record: dict[str, Any], *, application_id: str | None, tags: dict[str, Any]) -> None:
    return None
```

- [ ] **Step 8: 계량 구현** — `src/orchestrator/journey/metering.py`

```python
"""활동 계량. 활동 결과에 `_meter`(시각·모델별 토큰·판정)를 붙여 오케스트레이터가 steps[]로 옮기게 한다.

결과 객체는 고치지 않는다(새 dict). 계량 실패는 여정을 막지 않는다.
"""

import dataclasses
from collections.abc import Callable
from datetime import datetime
from typing import Any

from agents.data_assembly import FLAG_AGENT_FALLBACK
from journey import telemetry

METER_KEY = "_meter"
TAGS_KEY = "_tags"
DETAIL_MAX = 200
DECISION_MODEL = "decision-1"
AGENT_STEPS = frozenset({"assemble", "classify", "assess"})
STEPS = {
    "request_consent": "consent",
    "assemble_data": "assemble",
    "underwrite": "underwrite",
    "evaluate_policy": "policy",
    "assess_credit": "assess",
}


class MeteredRunner:
    """에이전트 러너를 감싸 호출마다 모델·토큰을 모은다. 그 밖의 메서드(대화·컨테이너 삭제)는 위임한다."""

    def __init__(self, inner: Any):
        self._inner = inner
        self._usage: list[dict[str, Any]] = []

    def run(self, spec: Any, input_text: str, handlers: Any, **kwargs: Any) -> Any:
        name = getattr(spec, "name", "")
        attrs = {"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": name, "gen_ai.request.model": spec.model}
        with telemetry.span(f"invoke_agent {name}", attrs) as sp:
            try:
                run = self._inner.run(spec, input_text, handlers, **kwargs)
            except Exception:
                self._usage.append({"model": spec.model, "input_tokens": 0, "output_tokens": 0, "calls": 1})
                raise
            entry = {
                "model": spec.model,
                "input_tokens": int(getattr(run, "input_tokens", 0) or 0),
                "output_tokens": int(getattr(run, "output_tokens", 0) or 0),
                "calls": int(getattr(run, "rounds", 0) or 0) + 1,
            }
            telemetry.set_attrs(sp, {"gen_ai.usage.input_tokens": entry["input_tokens"],
                                     "gen_ai.usage.output_tokens": entry["output_tokens"]})
        self._usage.append(entry)
        return run

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def entries(self) -> list[dict[str, Any]]:
        return list(self._usage)


def outcome_of(step: str, result: Any) -> tuple[str, str | None]:
    if not isinstance(result, dict):
        return "ok", None
    if step == "assemble":
        if result.get("status") != "complete":
            return "degraded", f"status={result.get('status')}"
        if FLAG_AGENT_FALLBACK in (result.get("flags") or []):
            return "degraded", FLAG_AGENT_FALLBACK
    if step == "assess" and result.get("assessment") is None:
        error = (result.get("record") or {}).get("error")
        return "failed", str(error)[:DETAIL_MAX] if error else None
    return "ok", None


def _classify_substep(result: Any, usage: list[dict[str, Any]], started: str, ended: str) -> list[dict[str, Any]]:
    classification = result.get("classification") if isinstance(result, dict) else None
    if not isinstance(classification, dict):
        return []
    if "error" in classification:
        outcome, detail = "failed", str(classification["error"])[:DETAIL_MAX]
    else:
        outcome, detail = "ok", None
    calls = int(((classification.get("metrics") or {}).get("decision_calls")) or 0)
    sub_usage = list(usage)
    if calls:
        sub_usage.append({"model": DECISION_MODEL, "input_tokens": 0, "output_tokens": 0, "calls": calls})
    sub = {"step": "classify", "started_at": started, "ended_at": ended, "outcome": outcome, "usage": sub_usage}
    if detail:
        sub["detail"] = detail
    return [sub]


def _record(step: str, outcome: str, started: datetime, ended: datetime, usage: list, detail: str | None):
    rec = {
        "step": step,
        "outcome": outcome,
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
        "duration_ms": max(0, int((ended - started).total_seconds() * 1000)),
        "agent": step in AGENT_STEPS,
        "usage": usage,
    }
    if detail:
        rec["detail"] = detail[:DETAIL_MAX]
    return rec


def failed_record(step: str, started: datetime, ended: datetime, error: BaseException) -> dict[str, Any]:
    return _record(step, "failed", started, ended, [], f"{type(error).__name__}: {error}")


def _parse(value: Any) -> datetime:
    return datetime.fromisoformat(value)


def step_records(step: str, meter: Any, result: Any, started: datetime, ended: datetime) -> list[dict[str, Any]]:
    """`_meter`를 단계 기록으로 바꾼다. 없거나 형식이 틀리면 결과로 판정하고 시각은 오케스트레이터 값을 쓴다."""
    try:
        s, e = _parse(meter["started_at"]), _parse(meter["ended_at"])
        records = [_record(step, str(meter["outcome"]), s, e, list(meter["usage"]), meter.get("detail"))]
        for sub in meter.get("substeps") or []:
            records.append(
                _record(sub["step"], str(sub["outcome"]), _parse(sub["started_at"]), _parse(sub["ended_at"]),
                        list(sub["usage"]), sub.get("detail"))
            )
        return records
    except (KeyError, TypeError, ValueError):
        outcome, detail = outcome_of(step, result)
        return [_record(step, outcome, started, ended, [], detail)]


def run_metered(activity: str, fn: Callable[[Any, dict], Any], deps: Any, payload: Any) -> Any:
    step = STEPS[activity]
    tags = {}
    if isinstance(payload, dict) and TAGS_KEY in payload:
        payload = dict(payload)
        tags = payload.pop(TAGS_KEY) or {}
    app_id = payload.get("application_id") if isinstance(payload, dict) else None
    runner = MeteredRunner(deps.agents) if getattr(deps, "agents", None) is not None else None
    inner = dataclasses.replace(deps, agents=runner) if runner is not None else deps
    started = deps.clock()
    with telemetry.span(f"journey.{activity}", {"application_id": app_id, "step": step, **tags}):
        try:
            result = fn(inner, payload)
        except Exception as e:
            telemetry.record_step(failed_record(step, started, deps.clock(), e), application_id=app_id, tags=tags)
            raise
    ended = deps.clock()
    usage = runner.entries() if runner is not None else []
    s, e = started.isoformat(), ended.isoformat()
    if step == "underwrite":
        substeps, usage = _classify_substep(result, usage, s, e), []
    else:
        substeps = []
    outcome, detail = outcome_of(step, result)
    meter = {"started_at": s, "ended_at": e, "outcome": outcome, "usage": usage, "substeps": substeps}
    if detail:
        meter["detail"] = detail
    for rec in step_records(step, meter, result, started, ended):
        telemetry.record_step(rec, application_id=app_id, tags=tags)
    if not isinstance(result, dict):
        return result
    return {**result, METER_KEY: meter}
```

주의: `test_run_metered_logs_and_reraises`의 FakeDeps는 `agents=None`이므로 `dataclasses.replace`를 건너뛴다. `test_classify_error_is_failed_and_missing_is_absent`의 `FakeDeps(Clock())`도 같다.

- [ ] **Step 9: 통과 확인** — `pytest tests/orchestrator/test_metering.py tests/shared/test_segments.py` → PASS

- [ ] **Step 10: 커밋** — `git add src/shared/segments.py src/orchestrator/journey/metering.py src/orchestrator/journey/telemetry.py tests/shared/test_segments.py tests/orchestrator/test_metering.py` → `feat(journey): meter activities with timing, token usage and outcome` (트레일러 포함)

---

## Task 3: 신청 메타(제출 시각·출처·run_id)와 문서 `steps[]`·`segment`

**Files:**
- Modify: `src/orchestrator/journey/models.py` (`StartRequest`), `src/orchestrator/journey/orchestration.py`, `src/console/app.py` (submit ~237–250)
- Test: `tests/orchestrator/test_journey_steps.py`(신규), `tests/console/test_app.py`

- [ ] **Step 1: 실패 테스트** — `tests/orchestrator/test_journey_steps.py`

```python
import json

import pytest
from pydantic import ValidationError

from journey.metering import METER_KEY
from journey.models import StartRequest
from test_journey import APPROVE, ASSESSED, COMPLETE, CONSENT, ELIGIBLE, START, UNDERWRITING, acts, boom

T = "2026-11-01T15:00:0{}+00:00"


def metered(result, step_outcome="ok", usage=None, substeps=None, s=1, e=2):
    meter = {"started_at": T.format(s), "ended_at": T.format(e), "outcome": step_outcome,
             "usage": usage or [], "substeps": substeps or []}
    return lambda _: {**result, METER_KEY: meter}


USAGE = [{"model": "gpt-5.4-mini", "input_tokens": 10, "output_tokens": 5, "calls": 2}]
FEATURES = {**UNDERWRITING["features"], "industry": "카페", "region": "서울 성동구", "business_age_months": 40}


def test_start_request_accepts_optional_meta():
    req = StartRequest.model_validate({**START, "submitted_at": "2026-11-01T14:59:00+00:00", "source": "load",
                                       "run_id": "load-20261102010000"})
    assert req.source == "load" and req.submitted_at.tzinfo is not None
    with pytest.raises(ValidationError):
        StartRequest.model_validate({**START, "run_id": "bad id!"})
    with pytest.raises(ValidationError):
        StartRequest.model_validate({**START, "source": "other"})


def test_steps_are_recorded_from_meter(run_journey):
    classify = {"step": "classify", "started_at": T.format(3), "ended_at": T.format(4), "outcome": "ok",
                "usage": [{"model": "decision-1", "input_tokens": 0, "output_tokens": 0, "calls": 2}]}
    a = acts(
        request_consent=metered(CONSENT),
        assemble_data=metered(COMPLETE, usage=USAGE),
        underwrite=metered({**UNDERWRITING, "features": FEATURES}, substeps=[classify], s=3, e=4),
        evaluate_policy=metered(ELIGIBLE),
        assess_credit=metered(ASSESSED, usage=USAGE),
    )
    _, ctx = run_journey(START, a, [("event", APPROVE)])
    doc = ctx.saved[-1]
    assert [s["step"] for s in doc["steps"]] == ["consent", "assemble", "underwrite", "classify", "policy", "assess"]
    assert doc["steps"][1]["usage"] == USAGE and doc["steps"][1]["agent"] is True
    assert doc["steps"][3]["duration_ms"] == 1000
    assert doc["segment"] == {"industry": "카페", "region": "서울", "business_age_band": "3년+"}


def test_meter_is_not_left_in_document(run_journey):
    a = acts(assemble_data=metered(COMPLETE), underwrite=metered(UNDERWRITING), evaluate_policy=metered(ELIGIBLE),
             assess_credit=metered(ASSESSED))
    _, ctx = run_journey(START, a, [("event", APPROVE)])
    assert all(METER_KEY not in json.dumps(d, ensure_ascii=False) for d in ctx.saved)
    assert ctx.saved[-1]["policy"] == ELIGIBLE


def test_missing_or_bad_meter_falls_back_to_result(run_journey):
    a = acts(assemble_data=lambda _: {**COMPLETE, METER_KEY: "garbage"})
    _, ctx = run_journey(START, a, [("event", APPROVE)])
    steps = {s["step"]: s for s in ctx.saved[-1]["steps"]}
    assert steps["assemble"]["outcome"] == "ok" and steps["assemble"]["usage"] == []
    assert steps["policy"]["outcome"] == "ok"  # 계량 없는 스텁도 결과로 기록
    assert ctx.saved[-1]["status"] == "approved"


def test_failed_activity_is_recorded_as_failed_step(run_journey):
    _, ctx = run_journey(START, acts(evaluate_policy=boom), [("event", APPROVE)])
    policy = [s for s in ctx.saved[-1]["steps"] if s["step"] == "policy"][0]
    assert policy["outcome"] == "failed" and policy["detail"] == "RuntimeError: down"


def test_meta_and_tags_are_passed_only_when_present(run_journey):
    _, plain = run_journey(START, acts(), [("event", APPROVE)])
    assert all("_tags" not in c.input for c in plain.calls if c.name != "save_document")
    doc = plain.saved[-1]
    assert (doc["submitted_at"], doc["source"], doc["run_id"], doc["segment"]) == (None, None, None, None)

    start = {**START, "source": "load", "run_id": "load-1", "submitted_at": "2026-11-01T14:59:00+00:00"}
    _, tagged = run_journey(start, acts(), [("event", APPROVE)])
    for name in ("request_consent", "assemble_data", "underwrite", "evaluate_policy", "assess_credit"):
        assert tagged.called(name)[0].input["_tags"] == {"source": "load", "run_id": "load-1"}
    assert "_tags" not in tagged.called("register_monitoring")[0].input
    doc = tagged.saved[-1]
    assert (doc["source"], doc["run_id"], doc["submitted_at"]) == ("load", "load-1", "2026-11-01T14:59:00+00:00")
```

`tests/orchestrator`는 `rootdir` 기준 모듈 import가 되는지 먼저 확인한다. `from test_journey import ...`가 안 되면(패키지 구조) 상수를 이 파일에 복사한다.

- [ ] **Step 2: 실패 확인** — `pytest tests/orchestrator/test_journey_steps.py` → FAIL

- [ ] **Step 3: StartRequest** — `src/orchestrator/journey/models.py`의 `StartRequest`에 추가(`from typing import Literal`, `from pydantic import Field` 확인)

```python
    submitted_at: datetime | None = None
    source: Literal["console", "load", "fault"] | None = None
    run_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._-]{1,64}$")
```

`submitted_at`이 tz 없는 값이면 UTC로 본다: 기존 모델에 tz 검증기가 있으면 같은 방식을 따르고, 없으면 `field_validator("submitted_at")`에서 `v.replace(tzinfo=UTC) if v and v.tzinfo is None else v`.

- [ ] **Step 4: 오케스트레이션** — `src/orchestrator/journey/orchestration.py`

import 추가:

```python
from journey.metering import METER_KEY, TAGS_KEY, failed_record, step_records
from shared.segments import segment_of
```

`_new_document`에 필드 추가(`"received_at"` 다음):

```python
        "submitted_at": req.submitted_at.isoformat() if req.submitted_at else None,
        "source": req.source,
        "run_id": req.run_id,
        "segment": None,
        "steps": [],
```

헬퍼 추가(`_new_document` 위):

```python
def _tags(req: StartRequest) -> dict[str, str]:
    return {k: v for k, v in (("source", req.source), ("run_id", req.run_id)) if v}


def _tagged(payload: dict[str, Any], tags: dict[str, str]) -> dict[str, Any]:
    return {**payload, TAGS_KEY: tags} if tags else payload


def _metered(doc: dict[str, Any], step: str, result: Any, started: datetime, ended: datetime) -> Any:
    """`_meter`를 떼어 steps[]에 옮긴다. 결과 객체는 고치지 않는다."""
    meter = result.get(METER_KEY) if isinstance(result, dict) else None
    clean = {k: v for k, v in result.items() if k != METER_KEY} if isinstance(result, dict) else result
    doc["steps"].extend(step_records(step, meter, clean, started, ended))
    return clean
```

`journey`의 각 계량 활동 호출을 이 패턴으로 바꾼다(`request_consent` 예):

```python
    tags = _tags(req)
    started = _now(ctx)
    try:
        consent = yield ctx.call_activity_with_retry(
            "request_consent", retry,
            _tagged({"application_id": req.application_id, "applicant_id": req.applicant_id}, tags),
        )
    except Exception as e:  # noqa: BLE001
        doc["steps"].append(failed_record("consent", started, _now(ctx), e))
        return (yield from _fail(ctx, retry, doc, "request_consent", e))
    consent = _metered(doc, "consent", consent, started, _now(ctx))
```

같은 방식:
- `assemble_data` → 단계 `"assemble"`, `assembly = _metered(...)`.
- `underwrite` → `"underwrite"`, 그 뒤 `doc["segment"] = segment_of(underwriting.get("features"))`.
- `evaluate_policy` → 입력 `_tagged(underwriting["features"], tags)`, 실패 시 `failed_record("policy", ...)` 후 기존 fail-closed 플래그, 성공 시 `doc["policy"] = _metered(doc, "policy", result, ...)`.
- `_assess(ctx, retry, doc, consent_id, underwriting, tags)` → `"assess"`, 실패 시 `failed_record`.
- `register_monitoring`·`save_document`는 계량하지 않는다.

- [ ] **Step 5: 기존 여정 테스트 포함 통과 확인** — `pytest tests/orchestrator` → PASS (`test_assessment_runs_after_policy_without_retry`의 입력 동등 비교가 태그 없이 유지되는지 확인)

- [ ] **Step 6: console 제출 메타 실패 테스트** — `tests/console/test_app.py`의 `test_new_application_is_enqueued_and_redirects`를 고친다

```python
    (queue_name, sent), = queue.sent
    submitted_at = sent.pop("submitted_at")
    assert datetime.fromisoformat(submitted_at).tzinfo is not None
    assert (queue_name, sent) == ("applications-start", {
        "application_id": "app-20261101-abc123", "applicant_id": "demo-cafe-001",
        "requested_amount_krw": 30_000_000, "requested_tenor_months": 12, "sla_hours": 24.0,
        "source": "console"})
```

(`from datetime import datetime` import 확인.) 실행 → FAIL.

- [ ] **Step 7: console 구현** — `src/console/app.py` submit

```python
            req = StartRequest(
                application_id=deps.new_application_id(),
                applicant_id=form.get("applicant_id", "").strip(),
                requested_amount_krw=_int(form.get("requested_amount_krw", "")),
                requested_tenor_months=_int(form.get("requested_tenor_months", "")),
                submitted_at=deps.clock(),
                source="console",
            )
        ...
        deps.queue.send(START_QUEUE, req.model_dump(mode="json", exclude_none=True))
```

`deps.clock`이 ConsoleDeps에 없으면 `datetime.now(UTC)`를 기본으로 하는 `clock: Callable[[], datetime]` 필드를 추가한다(기존 필드 확인 후).

- [ ] **Step 8: 통과 확인** — `pytest tests/console tests/orchestrator` → PASS

- [ ] **Step 9: 커밋** — `feat(journey): record submit meta, segment and per-step outcomes` (트레일러 포함)

---

## Task 4: Policy 호출 서킷브레이커

**Files:**
- Create: `src/orchestrator/journey/circuit.py`
- Modify: `src/orchestrator/journey/adapters.py` (`HttpPolicyApi` ~78–90, `build_deps` ~249)
- Test: `tests/orchestrator/test_circuit.py`

- [ ] **Step 1: 실패 테스트** — `tests/orchestrator/test_circuit.py`

```python
import httpx
import pytest

from journey.adapters import HttpPolicyApi, PolicyUnavailable
from journey.circuit import CircuitBreaker

DECISION = {"application_id": "a", "decision": "eligible", "max_amount_krw": 1, "rate_band": "A",
            "reason_codes": [], "rules_version": "v1"}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def breaker(clock, changes=None):
    return CircuitBreaker(threshold=3, cooldown_s=60, clock=clock,
                          on_change=changes.append if changes is not None else None)


def test_opens_after_threshold_and_blocks():
    clock, changes = Clock(), []
    b = breaker(clock, changes)
    for _ in range(3):
        assert b.allow()
        b.failure()
    assert b.state == "open" and not b.allow()
    assert changes == ["open"]


def test_success_resets_failure_count():
    b = breaker(Clock())
    b.failure(); b.failure(); b.success(); b.failure(); b.failure()
    assert b.state == "closed"


def test_half_open_allows_single_trial():
    clock, changes = Clock(), []
    b = breaker(clock, changes)
    for _ in range(3):
        b.failure()
    clock.t = 61
    assert b.allow() and b.state == "half_open"
    assert not b.allow()  # 시험 진행 중엔 차단
    b.failure()
    assert b.state == "open" and not b.allow()
    clock.t = 122
    assert b.allow()
    b.success()
    assert b.state == "closed" and changes == ["open", "half_open", "open", "half_open", "closed"]


def api(handler, b):
    client = httpx.Client(base_url="http://policy", transport=httpx.MockTransport(handler))
    return HttpPolicyApi(client=client, breaker=b)


def test_server_errors_trip_breaker_and_short_circuit():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503, json={"code": "unavailable"})

    b = breaker(Clock())
    p = api(handler, b)
    for _ in range(3):
        with pytest.raises(PolicyUnavailable):
            p.decide({})
    with pytest.raises(PolicyUnavailable, match="circuit_open"):
        p.decide({})
    assert len(calls) == 3


def test_transport_error_counts_as_failure():
    def handler(request):
        raise httpx.ConnectError("down")

    b = breaker(Clock())
    for _ in range(3):
        with pytest.raises(PolicyUnavailable):
            api(handler, b).decide({})
    assert b.state == "open"


def test_client_error_does_not_trip_breaker():
    b = breaker(Clock())
    p = api(lambda r: httpx.Response(422, json={"detail": "bad"}), b)
    for _ in range(5):
        with pytest.raises(PolicyUnavailable):
            p.decide({})
    assert b.state == "closed"


def test_success_passes_through():
    b = breaker(Clock())
    assert api(lambda r: httpx.Response(200, json=DECISION), b).decide({})["decision"] == "eligible"
```

`DECISION`은 `shared.contracts.PolicyDecision` 필수 필드에 맞춘다(구현 전 `PolicyDecision.model_fields` 확인, 다르면 테스트 상수만 맞춤).

- [ ] **Step 2: 실패 확인** — `pytest tests/orchestrator/test_circuit.py` → FAIL

- [ ] **Step 3: 구현** — `src/orchestrator/journey/circuit.py`

```python
"""프로세스 단위 서킷브레이커. 연속 실패 threshold번이면 open, cooldown 뒤 시험 1건(half_open)."""

import threading
import time
from collections.abc import Callable


class CircuitBreaker:
    def __init__(
        self,
        threshold: int = 3,
        cooldown_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        on_change: Callable[[str], None] | None = None,
    ) -> None:
        self.threshold, self.cooldown_s, self._clock, self._on_change = threshold, cooldown_s, clock, on_change
        self._lock = threading.Lock()
        self.state = "closed"
        self._failures = 0
        self._opened_at = 0.0
        self._trial = False

    def _set(self, state: str) -> None:
        if state == self.state:
            return
        self.state = state
        if self._on_change is not None:
            try:
                self._on_change(state)
            except Exception:  # noqa: BLE001 - 알림 실패가 호출을 막지 않는다
                pass

    def allow(self) -> bool:
        with self._lock:
            if self.state == "closed":
                return True
            if self.state == "open":
                if self._clock() - self._opened_at < self.cooldown_s:
                    return False
                self._set("half_open")
                self._trial = True
                return True
            if self._trial:
                return False
            self._trial = True
            return True

    def success(self) -> None:
        with self._lock:
            self._failures, self._trial = 0, False
            self._set("closed")

    def failure(self) -> None:
        with self._lock:
            self._trial = False
            self._failures += 1
            if self.state == "half_open" or self._failures >= self.threshold:
                self._opened_at = self._clock()
                self._set("open")
```

`src/orchestrator/journey/adapters.py`의 `HttpPolicyApi`:

```python
class HttpPolicyApi:
    def __init__(
        self,
        base_url: str = "",
        *,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self._client = _http(base_url, client, timeout)
        self._breaker = breaker

    def decide(self, features: dict[str, Any]) -> dict[str, Any]:
        b = self._breaker
        if b is not None and not b.allow():
            raise PolicyUnavailable("circuit_open")
        try:
            r = self._client.post("/v1/decisions", json=features)
        except httpx.HTTPError as e:
            if b is not None:
                b.failure()
            raise PolicyUnavailable(type(e).__name__) from e
        if b is not None:
            b.failure() if r.status_code >= 500 else b.success()
        if r.status_code != 200:
            raise PolicyUnavailable(f"HTTP {r.status_code} {r.text[:200]}")
        return PolicyDecision.model_validate(r.json()).model_dump(mode="json")
```

import 추가: `from journey.circuit import CircuitBreaker`. `build_deps`(~249)는 T5에서 `telemetry.circuit_changed`를 연결한다. 이 태스크에서는 `policy=HttpPolicyApi(env["POLICY_URL"], breaker=CircuitBreaker())`.

- [ ] **Step 4: 통과 확인** — `pytest tests/orchestrator` → PASS (기존 HttpPolicyApi 테스트 포함)

- [ ] **Step 5: 커밋** — `feat(journey): circuit breaker on policy calls` (트레일러 포함)

---

## Task 5: OTel 설정과 활동 계량 연결(Functions)

**Files:**
- Create: `src/shared/otel.py`
- Modify: `src/orchestrator/journey/telemetry.py`(T2 골격 채움), `src/orchestrator/function_app.py`, `src/orchestrator/journey/adapters.py`(`build_deps`), `src/orchestrator/requirements.txt`, `requirements-api.txt`
- Test: `tests/shared/test_otel.py`, `tests/orchestrator/test_telemetry.py`, `tests/orchestrator/test_function_app.py`

- [ ] **Step 1: 실패 테스트** — `tests/shared/test_otel.py`

```python
import sys
import types

from shared.otel import configure_monitor


def test_without_connection_string_is_noop():
    assert configure_monitor("x.otel", env={}) is False


def test_configures_with_managed_identity(monkeypatch):
    seen = {}
    mon = types.ModuleType("azure.monitor.opentelemetry")
    mon.configure_azure_monitor = lambda **kw: seen.update(kw)
    ident = types.ModuleType("azure.identity")
    ident.ManagedIdentityCredential = lambda client_id=None: ("mi", client_id)
    monkeypatch.setitem(sys.modules, "azure.monitor.opentelemetry", mon)
    monkeypatch.setitem(sys.modules, "azure.identity", ident)
    env = {"APPLICATIONINSIGHTS_CONNECTION_STRING": "InstrumentationKey=k", "AZURE_CLIENT_ID": "cid"}
    assert configure_monitor("journey.otel", env=env) is True
    assert seen["connection_string"] == "InstrumentationKey=k" and seen["credential"] == ("mi", "cid")
    assert seen["logger_name"] == "journey.otel" and seen["enable_live_metrics"] is False


def test_failure_is_warning_not_error(monkeypatch, caplog):
    mon = types.ModuleType("azure.monitor.opentelemetry")

    def boom(**kw):
        raise RuntimeError("no")

    mon.configure_azure_monitor = boom
    monkeypatch.setitem(sys.modules, "azure.monitor.opentelemetry", mon)
    assert configure_monitor("x", env={"APPLICATIONINSIGHTS_CONNECTION_STRING": "k"}) is False
    assert "OTel" in caplog.text
```

`tests/orchestrator/test_telemetry.py`

```python
import logging

from journey import telemetry


def test_record_step_logs_without_pii(caplog):
    caplog.set_level(logging.INFO, logger="journey.otel")
    rec = {"step": "assemble", "outcome": "ok", "duration_ms": 10, "agent": True,
           "usage": [{"model": "m", "input_tokens": 3, "output_tokens": 2, "calls": 1},
                     {"model": "n", "input_tokens": 1, "output_tokens": 1, "calls": 1}]}
    telemetry.record_step(rec, application_id="a-1", tags={"source": "load", "run_id": "r"})
    (r,) = [x for x in caplog.records if x.getMessage() == "journey.step"]
    assert (r.application_id, r.step, r.outcome, r.agent, r.input_tokens, r.output_tokens) == (
        "a-1", "assemble", "ok", True, 4, 3)
    assert (r.source, r.run_id) == ("load", "r")


def test_circuit_changed_logs_warning(caplog):
    caplog.set_level(logging.INFO, logger="journey.otel")
    telemetry.circuit_changed("open")
    (r,) = [x for x in caplog.records if x.getMessage() == "policy.circuit"]
    assert r.levelno == logging.WARNING and r.state == "open"


def test_span_without_sdk_is_context_manager():
    with telemetry.span("journey.x", {"application_id": "a", "skip": None}):
        pass
```

`tests/orchestrator/test_function_app.py`에 추가:

```python
def test_metered_activities_are_wrapped(monkeypatch):
    import function_app

    seen = []
    monkeypatch.setattr(function_app, "run_metered", lambda name, fn, deps, payload: seen.append(name) or {"ok": 1})
    monkeypatch.setattr(function_app, "deps", lambda: object())
    for name in ("request_consent", "assemble_data", "underwrite", "evaluate_policy", "assess_credit"):
        assert getattr(function_app, name)._function._func({"application_id": "a"}) == {"ok": 1}  # 데코레이터 내부 원함수
    assert seen == ["request_consent", "assemble_data", "underwrite", "evaluate_policy", "assess_credit"]
```

원함수 접근 경로(`_function._func`)는 `azure.functions` 버전마다 다르다. 구현 전 `function_app.save_document` 객체 속성을 출력해 실제 경로를 확인하고 테스트를 맞춘다. 접근이 불안정하면 활동 본문을 `_metered_activity(name, payload)` 모듈 함수로 빼고 그것을 테스트한다(권장).

- [ ] **Step 2: 실패 확인** — `pytest tests/shared/test_otel.py tests/orchestrator/test_telemetry.py tests/orchestrator/test_function_app.py` → FAIL

- [ ] **Step 3: 구현** — `src/shared/otel.py`

```python
"""Azure Monitor OpenTelemetry 초기화. SDK는 지연 import하고, 실패해도 서비스는 계속 돈다."""

import logging
import os
from collections.abc import Mapping

log = logging.getLogger(__name__)


def configure_monitor(logger_name: str, env: Mapping[str, str] = os.environ, instrument_httpx: bool = False) -> bool:
    conn = env.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not conn:
        return False
    try:
        from azure.identity import ManagedIdentityCredential
        from azure.monitor.opentelemetry import configure_azure_monitor

        configure_azure_monitor(
            connection_string=conn,
            credential=ManagedIdentityCredential(client_id=env.get("AZURE_CLIENT_ID")),
            logger_name=logger_name,
            enable_live_metrics=False,
        )
        if instrument_httpx:
            from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

            HTTPXClientInstrumentor().instrument()
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("OTel 초기화 실패: %s", type(e).__name__)
        return False
```

`src/orchestrator/journey/telemetry.py` (골격 교체)

```python
"""활동 스팬·단계 로그(`journey.otel` 로거 → AppTraces). 속성에 원문 PII를 넣지 않는다."""

import logging
from contextlib import nullcontext
from typing import Any

from shared.otel import configure_monitor

LOGGER = "journey.otel"
log = logging.getLogger(LOGGER)


def setup() -> bool:
    log.setLevel(logging.INFO)
    return configure_monitor(LOGGER, instrument_httpx=True)


def span(name: str, attributes: dict[str, Any]):
    try:
        from opentelemetry import trace
    except ImportError:
        return nullcontext()
    attrs = {k: v for k, v in attributes.items() if isinstance(v, (str, int, float, bool))}
    return trace.get_tracer("journey").start_as_current_span(name, attributes=attrs)


def set_attrs(span: Any, attributes: dict[str, Any]) -> None:
    if span is not None and hasattr(span, "set_attribute"):
        for k, v in attributes.items():
            span.set_attribute(k, v)


def record_step(record: dict[str, Any], *, application_id: str | None, tags: dict[str, Any]) -> None:
    usage = record.get("usage") or []
    log.info(
        "journey.step",
        extra={
            "application_id": application_id,
            "step": record.get("step"),
            "outcome": record.get("outcome"),
            "duration_ms": record.get("duration_ms"),
            "agent": bool(record.get("agent")),
            "source": tags.get("source"),
            "run_id": tags.get("run_id"),
            "input_tokens": sum(int(u.get("input_tokens") or 0) for u in usage),
            "output_tokens": sum(int(u.get("output_tokens") or 0) for u in usage),
        },
    )


def circuit_changed(state: str) -> None:
    log.warning("policy.circuit", extra={"state": state})
    try:
        from opentelemetry import trace

        trace.get_current_span().add_event("policy.circuit", {"state": state})
    except Exception:  # noqa: BLE001
        pass
```

`src/orchestrator/function_app.py`:

```python
from journey import telemetry
from journey.metering import run_metered

telemetry.setup()
...
def _metered_activity(name: str, payload: dict) -> dict:
    return run_metered(name, ACTIVITIES[name], deps(), payload)


@app.activity_trigger(input_name="payload")
def request_consent(payload: dict) -> dict:
    return _metered_activity("request_consent", payload)
```

`assemble_data`·`underwrite`·`evaluate_policy`·`assess_credit`도 같은 방식. `save_document`·`register_monitoring`·동의 활동은 그대로. 테스트는 `_metered_activity`를 대상으로 한다:

```python
def test_metered_activities_are_wrapped(monkeypatch):
    import function_app

    seen = []
    monkeypatch.setattr(function_app, "run_metered", lambda name, fn, d, p: seen.append((name, fn)) or {"ok": 1})
    monkeypatch.setattr(function_app, "deps", lambda: "deps")
    assert function_app._metered_activity("underwrite", {"application_id": "a"}) == {"ok": 1}
    assert seen == [("underwrite", ACTIVITIES["underwrite"])]
    src = (ROOT / "function_app.py").read_text(encoding="utf-8")
    for name in ("request_consent", "assemble_data", "underwrite", "evaluate_policy", "assess_credit"):
        assert f'_metered_activity("{name}", payload)' in src
```

`build_deps`: `policy=HttpPolicyApi(env["POLICY_URL"], breaker=CircuitBreaker(on_change=telemetry.circuit_changed))` (import `from journey import telemetry`).

requirements:
- `src/orchestrator/requirements.txt`: `azure-monitor-opentelemetry==1.8.11`, `opentelemetry-instrumentation-httpx==0.66b1` 추가.
- `requirements-api.txt`: `azure-monitor-opentelemetry==1.8.11` 추가.
- 로컬 venv 설치: `.\.venv\Scripts\python -m pip install azure-monitor-opentelemetry==1.8.11 opentelemetry-instrumentation-httpx==0.66b1` — 설치 후 `opentelemetry` 버전 충돌이 없는지 `pip check`. 설치가 기존 테스트를 깨면(버전 충돌) 설치를 되돌리고 테스트는 가짜 모듈로만 돌린다.
- `test_requirements_pin_runtime_sdks`가 이름 집합을 검사하면 새 패키지를 기대 집합에 추가한다.

- [ ] **Step 4: 통과 확인** — `pytest tests/shared tests/orchestrator` → PASS

- [ ] **Step 5: 커밋** — `feat(journey): OTel spans and step logs for metered activities` (트레일러 포함)

---

## Task 6: Policy 장애 모드와 OTel

**Files:**
- Modify: `src/policy/app.py`, `src/policy/main.py`
- Test: `tests/policy/test_app.py`

- [ ] **Step 1: 실패 테스트** — `tests/policy/test_app.py`에 추가

```python
def test_unavailable_fault_returns_503_for_decisions_only(rules, make_features):
    c = TestClient(create_app(rules, fault="unavailable"))
    assert c.get("/healthz").status_code == 200
    r = c.post("/v1/decisions", json=make_features().model_dump(mode="json"))
    assert r.status_code == 503 and r.json() == {"code": "unavailable"}


def test_unknown_fault_mode_is_rejected(rules):
    with pytest.raises(ValueError):
        create_app(rules, fault="weird")


def test_main_reads_fault_mode(monkeypatch, make_features):
    monkeypatch.setenv("FAULT_MODE", "unavailable")
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    sys.modules.pop("policy.main", None)
    module = importlib.import_module("policy.main")
    r = TestClient(module.app).post("/v1/decisions", json=make_features().model_dump(mode="json"))
    assert r.status_code == 503
    monkeypatch.delenv("FAULT_MODE")
    sys.modules.pop("policy.main", None)
```

`make_features` 픽스처의 실제 시그니처(conftest)를 보고 호출 형태를 맞춘다.

- [ ] **Step 2: 실패 확인** → FAIL

- [ ] **Step 3: 구현** — `src/policy/app.py`

```python
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

FAULT_MODES = ("none", "unavailable")


def create_app(rules: RuleSet, fault: str = "none") -> FastAPI:
    if fault not in FAULT_MODES:
        raise ValueError(f"FAULT_MODE: {fault}")
    app = FastAPI(title="Policy Engine")

    if fault == "unavailable":

        @app.middleware("http")
        async def unavailable(request: Request, call_next):
            if request.url.path == "/v1/decisions":
                return JSONResponse({"code": "unavailable"}, status_code=503)
            return await call_next(request)
    ...  # 기존 라우트 그대로
```

`src/policy/main.py` (FastAPI 계측은 앱 생성 전에 설정돼야 하므로 `policy.app` import 전에 초기화)

```python
"""uvicorn 진입점: `uvicorn policy.main:app`"""

import os
from pathlib import Path

from shared.otel import configure_monitor

configure_monitor("policy.otel")

from policy.app import create_app  # noqa: E402 - OTel 설정 뒤에 FastAPI를 만든다
from policy.rules import DEFAULT_RULES_PATH, load_rules  # noqa: E402

app = create_app(
    load_rules(Path(os.environ.get("POLICY_RULES_PATH", DEFAULT_RULES_PATH))),
    fault=os.environ.get("FAULT_MODE", "none"),
)
```

Policy 이미지(`src/policy/Dockerfile`)가 `src/shared`를 복사하는지, `requirements-api.txt`를 설치하는지 확인한다. 없으면 `COPY src/shared /app/shared` 추가.

- [ ] **Step 4: 통과 확인** — `pytest tests/policy` → PASS

- [ ] **Step 5: 커밋** — `feat(policy): FAULT_MODE=unavailable and Azure Monitor OTel` (트레일러 포함)

---

## Task 7: mockapi flaky 모드와 `FAULT_MODE`

**Files:**
- Modify: `src/mockapis/app.py`, `src/mockapis/main.py`
- Test: `tests/mockapis/test_app.py`

- [ ] **Step 1: 실패 테스트** — `tests/mockapis/test_app.py`에 추가

```python
def test_flaky_fails_first_request_per_consent_and_path(clock):
    cases = {"a1": generate_case("a1", Archetype.HEALTHY_SEASONAL, AS_OF, 1)}
    client = TestClient(create_app(cases, SECRET, clock, fault="flaky"))
    auth = _auth(_consent(client)["token"])
    first = client.get("/mydata/profile", headers=auth)
    assert first.status_code == 503 and first.json()["detail"] == {"code": "flaky"}
    assert client.get("/mydata/profile", headers=auth).status_code == 200
    assert client.get("/tax/filings", headers=auth).status_code == 503  # 다른 경로는 다시 첫 요청
    other = _auth(_consent(client)["token"])
    assert client.get("/mydata/profile", headers=other).status_code == 503  # 다른 동의도 첫 요청


def test_flaky_does_not_hide_auth_errors(clock):
    cases = {"a1": generate_case("a1", Archetype.HEALTHY_SEASONAL, AS_OF, 1)}
    client = TestClient(create_app(cases, SECRET, clock, fault="flaky"))
    assert client.get("/mydata/profile").status_code == 401


def test_admin_fault_flaky_and_reset(client):
    auth = _auth(_consent(client)["token"])
    assert client.post("/admin/faults", json={"mode": "flaky"}).status_code == 204
    assert client.get("/mydata/profile", headers=auth).status_code == 503
    assert client.post("/admin/faults", json={"mode": "none"}).status_code == 204
    assert client.get("/mydata/profile", headers=auth).status_code == 200


def test_unknown_fault_mode_rejected(clock):
    with pytest.raises(ValueError):
        create_app({}, SECRET, clock, fault="weird")
```

- [ ] **Step 2: 실패 확인** → FAIL

- [ ] **Step 3: 구현** — `src/mockapis/app.py`

```python
FAULT_MODES = ("none", "unavailable", "flaky")


class FaultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["none", "unavailable", "flaky"]


def create_app(cases, secret, clock, flows=None, fault: str = "none") -> FastAPI:
    if fault not in FAULT_MODES:
        raise ValueError(f"FAULT_MODE: {fault}")
    ...
    state = {"fault": fault}
    seen: set[tuple[str, str]] = set()

    def authorize(authorization: str | None, scope: ConsentScope, path: str) -> SyntheticCase:
        if state["fault"] == "unavailable":
            raise _error(503, "unavailable")
        ...  # 기존 토큰 검증
        if state["fault"] == "flaky" and (grant.consent_id, path) not in seen:
            seen.add((grant.consent_id, path))
            raise _error(503, "flaky")
        return cases[grant.applicant_id]
```

각 라우트는 경로 문자열을 넘긴다: `authorize(authorization, ConsentScope.ACCOUNT, "/mydata/profile")` 등(`/mydata/transactions`, `/mydata/flow`, `/tax/filings`, `/pg/settlements`). `set_fault`는 `state["fault"] = req.mode; seen.clear()`.

`grant`에 `consent_id`가 있는지 `verify_token` 반환형을 확인한다(없으면 토큰 문자열 해시를 키로 쓴다).

`src/mockapis/main.py`의 `create_app(...)` 호출에 `fault=os.environ.get("FAULT_MODE", "none")` 추가(`import os`). T8에서 신청자 구성도 바꾼다.

flaky 503은 조립 세션에서 `{"error":"unavailable","retryable":true}`로 보이고(`HttpMockApi.fetch` → `SourceError(503)`), 에이전트·결정론 경로 모두 같은 원천을 최대 2번 더 시도하므로 두 번째에 성공한다. 이 동작은 `assembly.sources[*].attempts == 2`로 실측에서 확인한다(T14).

- [ ] **Step 4: 통과 확인** — `pytest tests/mockapis` → PASS

- [ ] **Step 5: 커밋** — `feat(mockapis): flaky fault mode and FAULT_MODE env` (트레일러 포함)

---

## Task 8: 균형 격자 합성 신청자 24명

**Files:**
- Modify: `src/mockapis/synth.py` (`generate_case` ~87, profile ~180), `src/mockapis/demo.py`, `src/mockapis/main.py`
- Test: `tests/mockapis/test_demo.py`, `tests/mockapis/test_synth.py`(있으면 그곳, 없으면 test_demo.py)

- [ ] **Step 1: 실패 테스트** — `tests/mockapis/test_demo.py`에 추가

```python
import hashlib
import json
from datetime import date

from mockapis.demo import LOAD_ARCHETYPE, build_demo_cases, build_load_cases
from mockapis.synth import Archetype
from shared.segments import LOAD_GRID

DIGESTS = {
    "demo-cafe-001": "1179518b691d96f7",
    "demo-mismatch-002": "3f86f1fcf3d5ccde",
    "demo-stress-003": "da1eee928c6ccfc9",
    "demo-thin-004": "1dbd0c52c90bcb84",
    "demo-injection-005": "1db0ddf6309729d6",
}


def _digest(case) -> str:
    body = {
        "profile": case.profile.model_dump(mode="json"),
        "txns": [t.model_dump(mode="json") for t in case.transactions],
        "tax": [t.model_dump(mode="json") for t in case.tax_filings],
        "pg": [p.model_dump(mode="json") for p in case.pg_settlements],
    }
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def test_demo_cases_unchanged():
    cases = build_demo_cases(date(2026, 10, 1))
    assert {k: _digest(v) for k, v in cases.items()} == DIGESTS


def test_load_cases_follow_grid():
    cases = build_load_cases(date(2026, 10, 1))
    assert set(cases) == {a.applicant_id for a in LOAD_GRID}
    for a in LOAD_GRID:
        p = cases[a.applicant_id].profile
        assert (p.industry, p.region, p.business_age_months) == (a.industry, a.region, a.business_age_months)
        assert cases[a.applicant_id].archetype is LOAD_ARCHETYPE[a.archetype]
    names = {cases[a.applicant_id].profile.business_name for a in LOAD_GRID}
    assert not names & {c.profile.business_name for c in build_demo_cases(date(2026, 10, 1)).values()}


def test_load_cases_are_deterministic():
    a, b = build_load_cases(date(2026, 10, 1)), build_load_cases(date(2026, 10, 1))
    assert all(_digest(a[k]) == _digest(b[k]) for k in a)
```

`_digest`는 요약의 digest 정의(profile·txns·tax·pg를 `json.dumps(..., ensure_ascii=False, sort_keys=True)`)와 같다. 먼저 Step 2에서 **변경 전** 코드로 `test_demo_cases_unchanged`가 통과하는지 확인해 digest 계산식이 맞는지 검증한다(통과하지 않으면 계산식을 맞춘 뒤 진행).

- [ ] **Step 2: 확인** — `pytest tests/mockapis/test_demo.py -k unchanged` → PASS(변경 전 기준선), 나머지 → FAIL(ImportError)

- [ ] **Step 3: 구현** — `src/mockapis/synth.py`

```python
from dataclasses import dataclass, replace

_LOAD_PERSONAS: dict[str, _Persona] = {
    "카페": _Persona("다온커피", "한도윤", ("서하은", "문지호"), ("(주)원두나라", "푸른목장 유제품"), "카페", ""),
    "한식": _Persona("소담한상", "배정민", ("유가은", "남도현"), ("(주)제철식자재", "새벽청과"), "한식", ""),
    "제과점": _Persona("밀결베이커리", "신아름", ("홍다인", "권우진"), ("(주)곡물상회", "들녘유업"), "제과점", ""),
}


def load_persona(industry: str, region: str) -> _Persona:
    return replace(_LOAD_PERSONAS[industry], region=region)


def generate_case(
    applicant_id: str,
    archetype: Archetype,
    as_of: date,
    seed: int,
    inject_prompt: bool = False,
    persona: _Persona | None = None,
    business_age_months: int | None = None,
) -> SyntheticCase:
    rng = random.Random(seed)
    persona = persona or _PERSONAS[archetype]
    ...
```

profile 생성(~187): `business_age_months=business_age_months if business_age_months is not None else (8 if thin else 38),`.

`src/mockapis/demo.py`:

```python
from shared.segments import LOAD_GRID
from mockapis.synth import Archetype, SyntheticCase, generate_case, load_persona

LOAD_ARCHETYPE = {
    "healthy": Archetype.HEALTHY_SEASONAL,
    "mismatch": Archetype.TAX_MISMATCH,
    "stress": Archetype.STRESSED,
    "thin": Archetype.THIN_FILE,
}


def build_load_cases(as_of: date) -> dict[str, SyntheticCase]:
    """운영 지표 배치용 합성 신청자(원형 × 업종·지역·업력 균형 격자)."""
    return {
        a.applicant_id: generate_case(
            a.applicant_id,
            LOAD_ARCHETYPE[a.archetype],
            as_of,
            a.seed,
            persona=load_persona(a.industry, a.region),
            business_age_months=a.business_age_months,
        )
        for a in LOAD_GRID
    }
```

`src/mockapis/main.py`:

```python
_demo = build_demo_cases(_as_of)
_cases = {**_demo, **build_load_cases(_as_of)}
app = create_app(
    _cases, load_secret(), lambda: datetime.now(KST),
    flows=build_postloan_flows(_demo, _as_of),   # 사후 흐름은 데모 신청자만
    fault=os.environ.get("FAULT_MODE", "none"),
)
```

mockapi 이미지에 `src/shared`가 있는지 Dockerfile을 확인한다(`shared.contracts`를 이미 쓰므로 있음).

`known_entities`(가명화 사전)가 persona에서 만들어지므로 새 persona 이름도 자동으로 들어간다. console 신청 화면은 데모 신청자 목록만 보여 주는지 확인한다(`load-*`가 드롭다운에 섞이면 안 됨 — console이 `DEMO_APPLICANTS`를 쓰면 그대로).

- [ ] **Step 4: 통과 확인** — `pytest tests/mockapis tests/shared` → PASS

- [ ] **Step 5: 커밋** — `feat(mockapis): balanced synthetic load applicants` (트레일러 포함)

---

## Task 9: 순수 지표 계산 `opsmetrics`

**Files:**
- Create: `src/opsmetrics/__init__.py`(빈 파일), `prices.py`, `projection.py`, `slo.py`, `cost.py`, `fairness.py`, `summary.py`
- Test: `tests/opsmetrics/__init__.py`(다른 테스트 폴더 관례에 맞춤), `tests/opsmetrics/test_opsmetrics.py`

- [ ] **Step 1: 실패 테스트** — `tests/opsmetrics/test_opsmetrics.py`

```python
import json
from datetime import UTC, datetime, timedelta

import pytest

from opsmetrics.cost import ai_cost, unit_economics
from opsmetrics.fairness import fairness
from opsmetrics.prices import load_prices
from opsmetrics.projection import archetype_of, project
from opsmetrics.slo import agent_success, latency, nearest_rank
from opsmetrics.summary import summarize

T0 = datetime(2026, 11, 2, 1, 0, tzinfo=UTC)
PRICES = {"m": {"input_per_1m": 1.0, "output_per_1m": 10.0}, "decision-1": {"input_per_1m": None, "output_per_1m": None}}


def doc(i=1, *, minutes=5, reached=True, submitted=True, arch="healthy", seg=None, rec="approve",
        decision="eligible", steps=None, officer=None, status="awaiting_officer"):
    sub = T0 + timedelta(minutes=i)
    timeline = [{"status": "received", "at": (sub + timedelta(seconds=30)).isoformat()}]
    if reached:
        timeline.append({"status": "awaiting_officer", "at": (sub + timedelta(minutes=minutes)).isoformat()})
    return {
        "id": f"r-{i:02d}", "application_id": f"r-{i:02d}", "applicant_id": f"load-{arch}-{i:02d}",
        "status": status, "submitted_at": sub.isoformat() if submitted else None,
        "received_at": (sub + timedelta(seconds=30)).isoformat(), "source": "load", "run_id": "r",
        "segment": seg or {"industry": "카페", "region": "서울", "business_age_band": "3년+"},
        "steps": steps if steps is not None else [], "timeline": timeline, "flags": [],
        "policy": {"decision": decision, "reason_codes": ["R104"], "max_amount_krw": 1} if decision else None,
        "assessment": {"recommendation": rec, "narrative": "원문"} if rec else None,
        "officer_decision": {"decision": officer} if officer else None,
        "assembly": {"secret": "x"},
    }


def step(name, outcome="ok", usage=None, agent=True):
    return {"step": name, "outcome": outcome, "agent": agent, "usage": usage or [], "duration_ms": 1}


def test_project_keeps_only_allowed_fields():
    p = project(doc(officer="approve"))
    assert set(p) == {"application_id", "applicant_id", "status", "submitted_at", "received_at", "source", "run_id",
                      "segment", "steps", "timeline", "flags", "policy", "assessment", "officer_decision"}
    assert p["policy"] == {"decision": "eligible", "reason_codes": ["R104"]}
    assert p["assessment"] == {"recommendation": "approve"} and p["officer_decision"] == "approve"
    assert "원문" not in json.dumps(p, ensure_ascii=False)


def test_archetype_of():
    assert archetype_of("load-stress-03") == "stress"
    assert archetype_of("demo-cafe-001") is None


def test_nearest_rank():
    assert nearest_rank([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95) == 10
    assert nearest_rank([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 50) == 5
    assert nearest_rank([], 95) is None


def test_latency_counts_unreached_and_received_only():
    docs = [doc(i, minutes=i) for i in range(1, 21)] + [doc(30, reached=False), doc(31, submitted=False, minutes=2)]
    r = latency([project(d) for d in docs])
    assert (r["n"], r["unreached"], r["received_only"], r["insufficient"]) == (21, 1, 1, False)
    assert r["p95_min"] == 19.0 and r["max_min"] == 20.0 and r["met"] is False


def test_latency_insufficient_samples():
    r = latency([project(doc(1))])
    assert r["n"] == 1 and r["insufficient"] is True


def test_agent_success_separates_degraded():
    steps = [step("assemble"), step("classify", "degraded"), step("assess", "failed"), step("policy", agent=False)]
    r = agent_success([project(doc(steps=steps))])
    assert (r["total"], r["ok"], r["degraded"], r["failed"]) == (3, 1, 1, 1)
    assert r["rate"] == pytest.approx(1 / 3) and r["degraded_rate"] == pytest.approx(1 / 3) and r["met"] is False
    assert r["by_step"]["assess"] == {"ok": 0, "degraded": 0, "failed": 1}


def test_ai_cost_sums_priced_and_counts_unpriced():
    usage = [{"model": "m", "input_tokens": 1_000_000, "output_tokens": 100_000, "calls": 2},
             {"model": "decision-1", "input_tokens": 0, "output_tokens": 0, "calls": 3}]
    docs = [project(doc(1, steps=[step("assemble", usage=usage)])), project(doc(2, steps=[]))]
    r = ai_cost(docs, PRICES)
    assert r["by_model"]["m"]["cost_usd"] == pytest.approx(2.0)
    assert r["by_model"]["decision-1"]["cost_usd"] is None
    assert r["total_usd"] == pytest.approx(2.0) and r["per_application_usd"] == pytest.approx(1.0)
    assert r["unpriced_calls"] == 3 and r["applications"] == 2


def test_unit_economics():
    rows = unit_economics(0.05, 300.0, volumes=(100, 1000))
    assert rows[0] == {"volume": 100, "ai_usd": 0.05, "fixed_usd": 3.0, "total_usd": 3.05,
                       "vs_manual": pytest.approx(3.05 / 3500), "vs_ticket": pytest.approx(3.05 / 6000)}
    assert unit_economics(0.05, None, volumes=(100,))[0]["fixed_usd"] is None


def _grid(rate_by_industry):
    docs, i = [], 0
    for industry, approve in rate_by_industry.items():
        for k in range(6):
            i += 1
            docs.append(doc(i, arch=("healthy", "stress")[k % 2],
                            seg={"industry": industry, "region": "서울", "business_age_band": "<1년" if k < 3 else "3년+"},
                            rec="approve" if k < approve else "decline"))
    return [project(d) for d in docs]


def test_fairness_gap_and_alert():
    r = fairness(_grid({"카페": 6, "한식": 2}))
    ind = r["axes"]["industry"]
    assert ind["values"]["카페"]["approve_rate"] == 1.0 and ind["values"]["한식"]["approve_rate"] == pytest.approx(2 / 6)
    assert ind["gap_pp"] == pytest.approx(66.7, abs=0.1) and ind["alert"] is True
    assert r["alerts"] == ["industry"]


def test_small_groups_are_excluded_from_gap():
    docs = _grid({"카페": 6}) + [project(doc(99, seg={"industry": "한식", "region": "서울", "business_age_band": "3년+"},
                                             rec="decline"))]
    ind = fairness(docs)["axes"]["industry"]
    assert ind["values"]["한식"]["insufficient"] is True and ind["gap_pp"] == 0.0 and ind["alert"] is False


def test_age_axis_is_policy_explained_and_never_alerts():
    age = fairness(_grid({"카페": 3, "한식": 3}))["axes"]["business_age_band"]
    assert age["policy_explained"] is True and age["alert"] is False


def test_controlled_gap_is_per_archetype():
    ind = fairness(_grid({"카페": 6, "한식": 0}))["axes"]["industry"]
    assert set(ind["controlled"]["by_archetype"]) == {"healthy", "stress"}
    assert ind["controlled"]["max_pp"] == pytest.approx(100.0)


def test_summary_has_basis_and_sections():
    s = summarize([project(doc())], PRICES, fixed_monthly_usd=None, fixed_source="측정 불가(권한)")
    assert s["basis"] == "합성 데이터 기준"
    assert {"latency", "agent_success", "ai_cost", "unit_economics", "fairness", "fixed"} <= set(s)


def test_load_prices_skips_meta():
    prices = load_prices()
    assert "_source" not in prices and "gpt-5.4-mini" in prices
```

- [ ] **Step 2: 실패 확인** — `pytest tests/opsmetrics` → FAIL(ModuleNotFoundError)

- [ ] **Step 3: 구현**

`src/opsmetrics/prices.py`

```python
"""모델 단가(USD/1M 토큰). 저장소·평가 이미지·console 이미지 모두 `<root>/evals/prices.json`에 있다."""

import json
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "evals" / "prices.json"


def load_prices(path: Path | None = None) -> dict[str, dict[str, Any]]:
    data = json.loads((path or DEFAULT_PATH).read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if isinstance(v, dict)}
```

`src/opsmetrics/projection.py`

```python
"""신청 문서 → 지표 계산용 투영. 허용 필드만 남긴다(원문 PII·에이전트 서술 제외)."""

import re
from typing import Any

_LOAD_ID = re.compile(r"^load-(healthy|mismatch|stress|thin)-\d+$")
_STEP_KEYS = ("step", "outcome", "started_at", "ended_at", "duration_ms", "agent", "usage", "detail")


def archetype_of(applicant_id: str | None) -> str | None:
    m = _LOAD_ID.match(applicant_id or "")
    return m.group(1) if m else None


def project(doc: dict[str, Any]) -> dict[str, Any]:
    policy, assessment, officer = doc.get("policy"), doc.get("assessment"), doc.get("officer_decision")
    return {
        "application_id": doc.get("application_id"),
        "applicant_id": doc.get("applicant_id"),
        "status": doc.get("status"),
        "submitted_at": doc.get("submitted_at"),
        "received_at": doc.get("received_at"),
        "source": doc.get("source"),
        "run_id": doc.get("run_id"),
        "segment": doc.get("segment"),
        "steps": [{k: s[k] for k in _STEP_KEYS if k in s} for s in doc.get("steps") or []],
        "timeline": [{"status": t.get("status"), "at": t.get("at")} for t in doc.get("timeline") or []],
        "flags": list(doc.get("flags") or []),
        "policy": {"decision": policy.get("decision"), "reason_codes": list(policy.get("reason_codes") or [])}
        if isinstance(policy, dict) else None,
        "assessment": {"recommendation": assessment.get("recommendation")} if isinstance(assessment, dict) else None,
        "officer_decision": officer.get("decision") if isinstance(officer, dict) else None,
    }
```

`src/opsmetrics/slo.py`

```python
"""SLO1(접수 → 심사역 대기열 지연)과 SLO2(에이전트 단계 성공률)."""

import math
from datetime import datetime
from typing import Any

OUTCOMES = ("ok", "degraded", "failed")


def nearest_rank(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(1, math.ceil(p / 100 * len(ordered))) - 1]


def _reached_at(p: dict[str, Any]) -> str | None:
    return next((t["at"] for t in p.get("timeline") or [] if t.get("status") == "awaiting_officer"), None)


def latency(docs: list[dict[str, Any]], target_min: float = 10.0, min_samples: int = 20) -> dict[str, Any]:
    minutes, unreached, received_only = [], 0, 0
    for p in docs:
        end = _reached_at(p)
        if end is None:
            unreached += 1
            continue
        start = p.get("submitted_at")
        if not start:
            start, received_only = p.get("received_at"), received_only + 1
        minutes.append((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() / 60)
    p95 = nearest_rank(minutes, 95)
    return {
        "n": len(minutes),
        "p50_min": nearest_rank(minutes, 50),
        "p95_min": p95,
        "max_min": max(minutes) if minutes else None,
        "target_min": target_min,
        "met": p95 is not None and p95 <= target_min,
        "insufficient": len(minutes) < min_samples,
        "unreached": unreached,
        "received_only": received_only,
    }


def agent_success(docs: list[dict[str, Any]], target: float = 0.99) -> dict[str, Any]:
    counts = dict.fromkeys(OUTCOMES, 0)
    by_step: dict[str, dict[str, int]] = {}
    for p in docs:
        for s in p.get("steps") or []:
            if not s.get("agent"):
                continue
            outcome = s.get("outcome") if s.get("outcome") in OUTCOMES else "failed"
            counts[outcome] += 1
            by_step.setdefault(s["step"], dict.fromkeys(OUTCOMES, 0))[outcome] += 1
    total = sum(counts.values())
    rate = counts["ok"] / total if total else None
    return {
        "total": total,
        **counts,
        "rate": rate,
        "degraded_rate": counts["degraded"] / total if total else None,
        "target": target,
        "met": rate is not None and rate >= target,
        "by_step": by_step,
    }
```

`src/opsmetrics/cost.py`

```python
"""실측 토큰 × 단가 = AI 비용, 고정비 ÷ 가정 물량. 캐시 할인 미반영(상한 추정)."""

from typing import Any

MANUAL_USD = 3500.0  # Mills & McCarthy(HBS WP 15-004): 소상공인 대출 1건 처리 원가 $3,000~4,000의 중간값
TICKET_USD = 6000.0
VOLUMES = (100, 1000, 10000)


def ai_cost(docs: list[dict[str, Any]], prices: dict[str, dict[str, Any]]) -> dict[str, Any]:
    by_model: dict[str, dict[str, Any]] = {}
    for p in docs:
        for s in p.get("steps") or []:
            for u in s.get("usage") or []:
                m = by_model.setdefault(u["model"], {"input_tokens": 0, "output_tokens": 0, "calls": 0})
                for k in ("input_tokens", "output_tokens", "calls"):
                    m[k] += int(u.get(k) or 0)
    total, unpriced = 0.0, 0
    for model, m in by_model.items():
        price = prices.get(model) or {}
        pin, pout = price.get("input_per_1m"), price.get("output_per_1m")
        if pin is None or pout is None:
            m["cost_usd"], unpriced = None, unpriced + m["calls"]
            continue
        m["cost_usd"] = m["input_tokens"] / 1e6 * pin + m["output_tokens"] / 1e6 * pout
        total += m["cost_usd"]
    n = len(docs)
    return {
        "by_model": by_model,
        "total_usd": total,
        "applications": n,
        "per_application_usd": total / n if n else None,
        "unpriced_calls": unpriced,
    }


def unit_economics(
    ai_per_app: float | None,
    fixed_monthly_usd: float | None,
    volumes: tuple[int, ...] = VOLUMES,
    manual_usd: float = MANUAL_USD,
    ticket_usd: float = TICKET_USD,
) -> list[dict[str, Any]]:
    rows = []
    for v in volumes:
        fixed = fixed_monthly_usd / v if fixed_monthly_usd is not None else None
        total = None if ai_per_app is None or fixed is None else round(ai_per_app + fixed, 6)
        rows.append({
            "volume": v,
            "ai_usd": ai_per_app,
            "fixed_usd": fixed,
            "total_usd": total,
            "vs_manual": total / manual_usd if total is not None else None,
            "vs_ticket": total / ticket_usd if total is not None else None,
        })
    return rows
```

`src/opsmetrics/fairness.py`

```python
"""세그먼트별 추천 approve율·eligible율 격차. 업력 축은 규칙 R104로 설명되므로 경보 대상이 아니다."""

from typing import Any

from opsmetrics.projection import archetype_of

AXES = ("industry", "region", "business_age_band")
POLICY_EXPLAINED = frozenset({"business_age_band"})
RECOMMENDATIONS = ("approve", "conditional", "decline")


def _rates(group: list[dict[str, Any]]) -> dict[str, Any]:
    assessed = [p for p in group if p.get("assessment")]
    policed = [p for p in group if p.get("policy")]
    decided = [p for p in group if p.get("officer_decision")]
    recs = [p["assessment"].get("recommendation") for p in assessed]
    return {
        "n": len(group),
        "assessed": len(assessed),
        "approve_rate": recs.count("approve") / len(assessed) if assessed else None,
        "eligible_rate": sum(p["policy"].get("decision") == "eligible" for p in policed) / len(policed)
        if policed else None,
        "recommendations": {r: recs.count(r) for r in RECOMMENDATIONS},
        "officer_approve_rate": sum(p["officer_decision"] != "decline" for p in decided) / len(decided)
        if decided else None,
    }


def _gap(values: dict[str, dict[str, Any]], key: str = "approve_rate") -> float:
    rates = [v[key] for v in values.values() if not v.get("insufficient") and v.get(key) is not None]
    return round((max(rates) - min(rates)) * 100, 1) if len(rates) >= 2 else 0.0


def _by(docs: list[dict[str, Any]], axis: str) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for p in docs:
        value = (p.get("segment") or {}).get(axis)
        if value is not None:
            groups.setdefault(value, []).append(p)
    return groups


def fairness(docs: list[dict[str, Any]], gap_pp: float = 20.0, min_samples: int = 5) -> dict[str, Any]:
    axes, alerts = {}, []
    for axis in AXES:
        values = {}
        for value, group in sorted(_by(docs, axis).items()):
            r = _rates(group)
            r["insufficient"] = r["assessed"] < min_samples
            values[value] = r
        controlled = {}
        for arch in sorted({a for p in docs if (a := archetype_of(p.get("applicant_id")))}):
            sub = {v: {**_rates(g), "insufficient": len([p for p in g if p.get("assessment")]) < 2}
                   for v, g in _by([p for p in docs if archetype_of(p.get("applicant_id")) == arch], axis).items()}
            controlled[arch] = _gap(sub)
        gap = _gap(values)
        explained = axis in POLICY_EXPLAINED
        alert = (not explained) and gap > gap_pp
        if alert:
            alerts.append(axis)
        axes[axis] = {
            "values": values,
            "gap_pp": gap,
            "eligible_gap_pp": _gap(values, "eligible_rate"),
            "controlled": {"by_archetype": controlled, "max_pp": max(controlled.values(), default=0.0)},
            "policy_explained": explained,
            "alert": alert,
        }
    return {"threshold_pp": gap_pp, "min_samples": min_samples, "axes": axes, "alerts": alerts}
```

`src/opsmetrics/summary.py`

```python
"""보고서·console이 공유하는 요약."""

from typing import Any

from opsmetrics.cost import ai_cost, unit_economics
from opsmetrics.fairness import fairness
from opsmetrics.slo import agent_success, latency

BASIS = "합성 데이터 기준"


def summarize(
    docs: list[dict[str, Any]],
    prices: dict[str, dict[str, Any]],
    fixed_monthly_usd: float | None = None,
    fixed_source: str | None = None,
    gap_pp: float = 20.0,
) -> dict[str, Any]:
    cost = ai_cost(docs, prices)
    return {
        "basis": BASIS,
        "applications": len(docs),
        "latency": latency(docs),
        "agent_success": agent_success(docs),
        "ai_cost": cost,
        "fixed": {"monthly_usd": fixed_monthly_usd, "source": fixed_source},
        "unit_economics": unit_economics(cost["per_application_usd"], fixed_monthly_usd),
        "fairness": fairness(docs, gap_pp=gap_pp),
    }
```

`test_latency_counts_unreached_and_received_only` 검산: doc(i, minutes=i) i=1..20은 submitted→awaiting = i분, doc(31)은 received(=submitted+30초)→awaiting(submitted+2분) = 1.5분. 정렬 21개 중 nearest-rank 95 = ceil(19.95)=20번째 = 19.0, max 20.0. 21 ≥ 20이므로 insufficient False, p95 19 > 10이므로 met False.

`test_controlled_gap_is_per_archetype` 검산: 카페 6건 모두 approve, 한식 0건. 원형 healthy(k 짝수 3건씩)·stress(홀수 3건씩) 각각 카페 1.0 vs 한식 0.0 → 100pp.

- [ ] **Step 4: 통과 확인** — `pytest tests/opsmetrics` → PASS

- [ ] **Step 5: 커밋** — `feat(opsmetrics): SLO, cost and fairness calculations` (트레일러 포함)

---

## Task 10: VNet 안 실행 Job `opsjob`

**Files:**
- Create: `src/opsjob/__init__.py`, `src/opsjob/__main__.py`, `src/opsjob/run.py`, `src/opsjob/azure.py`
- Modify: `requirements-eval.txt`, `src/evals/Dockerfile`
- Test: `tests/opsjob/test_opsjob.py`

환경 변수:

| 변수 | 의미 |
|---|---|
| `OPS_MODE` | `load`(격자 제출+대기+수집) · `submit`(지정 신청자 제출+대기+수집, 장애 시나리오용) · `collect`(run_id 목록 재수집) |
| `OPS_RUN_ID` | 이번 실행 ID(`^[A-Za-z0-9._-]{1,64}$`) |
| `OPS_COUNT` | load 건수(기본 24, 최대 24) |
| `OPS_INTERVAL_S` | 제출 간격 초(기본 15) |
| `OPS_APPLICANTS` | submit 신청자 CSV |
| `OPS_SOURCE` | `load`·`fault` |
| `OPS_RUN_IDS` | collect 대상 CSV |
| `OPS_WAIT_S` | 최대 대기(기본 1500, job-ops replicaTimeout 3600 안에 맞춤) |
| `COSMOS_ENDPOINT`, `COSMOS_DATABASE`, `QUEUE_ENDPOINT`, `AZURE_CLIENT_ID` | 접근 정보 |

- [ ] **Step 1: 실패 테스트** — `tests/opsjob/test_opsjob.py`

```python
import json
from datetime import UTC, datetime, timedelta

import pytest

from opsjob.run import BUDGET_GUARD_USD, Config, done, message, run

T0 = datetime(2026, 11, 2, 1, 0, tzinfo=UTC)


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t


class FakeQueue:
    def __init__(self):
        self.sent = []

    def send(self, body: str):
        self.sent.append(json.loads(body))


class FakeStore:
    """제출된 신청을 즉시(또는 지정 시점부터) awaiting_officer 상태 문서로 돌려준다."""

    def __init__(self, queue, tokens=0, stuck=()):
        self.queue, self.tokens, self.stuck = queue, tokens, set(stuck)

    def by_run(self, run_id):
        out = []
        for m in self.queue.sent:
            if m["run_id"] != run_id:
                continue
            reached = m["applicant_id"] not in self.stuck
            timeline = [{"status": "received", "at": m["submitted_at"]}]
            if reached:
                timeline.append({"status": "awaiting_officer", "at": m["submitted_at"]})
            usage = [{"model": "gpt-5.4", "input_tokens": self.tokens, "output_tokens": 0, "calls": 1}]
            out.append({"application_id": m["application_id"], "applicant_id": m["applicant_id"],
                        "status": "awaiting_officer" if reached else "underwritten", "run_id": run_id,
                        "submitted_at": m["submitted_at"], "timeline": timeline,
                        "steps": [{"step": "assess", "outcome": "ok", "agent": True, "usage": usage}]})
        return out


def cfg(tmp_path, **kw):
    base = dict(mode="load", run_id="load-1", count=4, interval_s=0, applicants=(), source="load",
                run_ids=(), wait_s=60, out_dir=tmp_path)
    return Config(**{**base, **kw})


def run_with(tmp_path, store_kw=None, **kw):
    q, clock = FakeQueue(), Clock()
    store = FakeStore(q, **(store_kw or {}))
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        clock.t += timedelta(seconds=s)

    summary = run(cfg(tmp_path, **kw), queue=q, store=store, clock=clock, sleep=sleep,
                  prices={"gpt-5.4": {"input_per_1m": 1.0, "output_per_1m": 1.0}})
    return summary, q, sleeps


def test_message_shape():
    m = message("load-1", 3, "load-healthy-01", T0, "load")
    assert m == {"application_id": "load-1-03", "applicant_id": "load-healthy-01", "requested_amount_krw": 10_000_000,
                 "requested_tenor_months": 12, "submitted_at": T0.isoformat(), "source": "load", "run_id": "load-1"}


def test_application_id_matches_entry_pattern():
    import re
    assert re.match(r"^[a-z0-9][a-z0-9-]{2,62}$", message("load-20261102010000", 24, "x", T0, "load")["application_id"])


def test_load_submits_grid_in_order_and_writes_outputs(tmp_path):
    summary, q, _ = run_with(tmp_path)
    assert [m["applicant_id"] for m in q.sent] == ["load-healthy-01", "load-mismatch-01", "load-stress-01", "load-thin-01"]
    assert summary["submitted"] == 4 and summary["reached"] == 4 and summary["unreached"] == []
    docs = json.loads((tmp_path / "docs.json").read_text(encoding="utf-8"))
    assert len(docs["runs"]["load-1"]) == 4
    assert json.loads((tmp_path / "summary.json").read_text(encoding="utf-8")) == summary


def test_wait_reports_unreached(tmp_path):
    summary, _, _ = run_with(tmp_path, store_kw={"stuck": {"load-thin-01"}}, wait_s=120)
    assert summary["reached"] == 3
    assert summary["unreached"] == [{"application_id": "load-1-04", "status": "underwritten"}]


def test_budget_guard_stops_submission(tmp_path):
    # 건당 1M 입력 토큰 × $1 = $1/건 → 24건 예상 $24 > $4
    summary, q, _ = run_with(tmp_path, store_kw={"tokens": 1_000_000}, count=24)
    assert len(q.sent) == 3 and summary["aborted"] is True
    assert summary["guard"]["projected_usd"] == pytest.approx(24.0)
    assert summary["guard"]["limit_usd"] == BUDGET_GUARD_USD


def test_submit_mode_uses_given_applicants(tmp_path):
    summary, q, _ = run_with(tmp_path, mode="submit", run_id="fault-1", source="fault",
                             applicants=("demo-cafe-001", "demo-stress-003"))
    assert [m["applicant_id"] for m in q.sent] == ["demo-cafe-001", "demo-stress-003"]
    assert {m["source"] for m in q.sent} == {"fault"}


def test_collect_mode_submits_nothing(tmp_path):
    summary, q, _ = run_with(tmp_path, mode="collect", run_ids=("load-1",))
    assert q.sent == [] and summary["mode"] == "collect"
    assert json.loads((tmp_path / "docs.json").read_text(encoding="utf-8")) == {"runs": {"load-1": []}}


def test_done_rule():
    assert done({"status": "failed", "timeline": []})
    assert done({"status": "assessed", "timeline": [{"status": "awaiting_officer"}]})
    assert not done({"status": "underwritten", "timeline": [{"status": "received"}]})


def test_config_from_env_validates():
    from opsjob.run import config_from_env

    with pytest.raises(ValueError):
        config_from_env({"OPS_MODE": "load", "OPS_RUN_ID": "bad id"})
    with pytest.raises(ValueError):
        config_from_env({"OPS_MODE": "load", "OPS_RUN_ID": "ok", "OPS_COUNT": "25"})
    c = config_from_env({"OPS_MODE": "submit", "OPS_RUN_ID": "f-1", "OPS_APPLICANTS": "a,b", "OPS_SOURCE": "fault"})
    assert c.applicants == ("a", "b") and c.count == 24
```

- [ ] **Step 2: 실패 확인** — `pytest tests/opsjob` → FAIL

- [ ] **Step 3: 구현** — `src/opsjob/run.py`

```python
"""운영 지표 Job 본체. 큐에 신청을 넣고, 심사역 대기열 도달을 기다린 뒤, 투영 문서를 남긴다.

평가 이미지에서 돈다(ops·journey 패키지 없음). 출력은 evals.transport.emit으로 로그에 싣는다.
"""

import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from opsmetrics.cost import ai_cost
from opsmetrics.projection import project
from shared.segments import LOAD_GRID

AMOUNT_KRW = 10_000_000
TENOR_MONTHS = 12
MAX_COUNT = len(LOAD_GRID)
GUARD_SAMPLE = 3
BUDGET_GUARD_USD = 4.0
POLL_S = 15
_RUN_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class Queue(Protocol):
    def send(self, body: str) -> None: ...


class Store(Protocol):
    def by_run(self, run_id: str) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class Config:
    mode: str
    run_id: str
    count: int
    interval_s: float
    applicants: tuple[str, ...]
    source: str
    run_ids: tuple[str, ...]
    wait_s: float
    out_dir: Path


def _csv(value: str | None) -> tuple[str, ...]:
    return tuple(v.strip() for v in (value or "").split(",") if v.strip())


def config_from_env(env: Mapping[str, str], out_dir: Path = Path("/tmp/ops")) -> Config:
    mode = env.get("OPS_MODE", "")
    if mode not in ("load", "submit", "collect"):
        raise ValueError(f"OPS_MODE: {mode}")
    run_id = env.get("OPS_RUN_ID", "")
    if not _RUN_ID.match(run_id):
        raise ValueError("OPS_RUN_ID")
    count = int(env.get("OPS_COUNT", MAX_COUNT))
    if not 1 <= count <= MAX_COUNT:
        raise ValueError(f"OPS_COUNT 1~{MAX_COUNT}")
    run_ids = _csv(env.get("OPS_RUN_IDS"))
    if not all(_RUN_ID.match(r) for r in run_ids):
        raise ValueError("OPS_RUN_IDS")
    return Config(
        mode=mode, run_id=run_id, count=count, interval_s=float(env.get("OPS_INTERVAL_S", 15)),
        applicants=_csv(env.get("OPS_APPLICANTS")), source=env.get("OPS_SOURCE", "load"),
        run_ids=run_ids, wait_s=float(env.get("OPS_WAIT_S", 1800)), out_dir=out_dir,
    )


def message(run_id: str, i: int, applicant_id: str, now: datetime, source: str) -> dict[str, Any]:
    return {
        "application_id": f"{run_id.lower()}-{i:02d}",
        "applicant_id": applicant_id,
        "requested_amount_krw": AMOUNT_KRW,
        "requested_tenor_months": TENOR_MONTHS,
        "submitted_at": now.isoformat(),
        "source": source,
        "run_id": run_id,
    }


def done(doc: dict[str, Any]) -> bool:
    return doc.get("status") == "failed" or any(
        t.get("status") == "awaiting_officer" for t in doc.get("timeline") or []
    )


def _wait(store: Store, run_id: str, ids: set[str], deadline: datetime, clock, sleep) -> list[dict[str, Any]]:
    while True:
        docs = [d for d in store.by_run(run_id) if d.get("application_id") in ids]
        if len(docs) == len(ids) and all(done(d) for d in docs):
            return docs
        if clock() >= deadline:
            return docs
        sleep(POLL_S)


def _guard(docs: list[dict[str, Any]], count: int, prices: dict) -> dict[str, Any]:
    cost = ai_cost([project(d) for d in docs], prices)
    per_app = cost["per_application_usd"] or 0.0
    return {"sample": len(docs), "per_application_usd": per_app, "projected_usd": per_app * count,
            "limit_usd": BUDGET_GUARD_USD}


def _write(cfg: Config, runs: dict[str, list[dict[str, Any]]], summary: dict[str, Any]) -> list[Path]:
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    docs_path, summary_path = cfg.out_dir / "docs.json", cfg.out_dir / "summary.json"
    docs_path.write_text(json.dumps({"runs": runs}, ensure_ascii=False), encoding="utf-8")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    return [docs_path, summary_path]


def run(
    cfg: Config,
    *,
    queue: Queue,
    store: Store,
    prices: dict[str, dict[str, Any]],
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if cfg.mode == "collect":
        runs = {r: [project(d) for d in store.by_run(r)] for r in cfg.run_ids}
        summary = {"run_id": cfg.run_id, "mode": "collect", "runs": {r: len(v) for r, v in runs.items()}}
        _write(cfg, runs, summary)
        return summary

    applicants = (
        [a.applicant_id for a in LOAD_GRID[: cfg.count]] if cfg.mode == "load" else list(cfg.applicants)
    )
    sent: list[dict[str, Any]] = []
    guard, aborted = None, False
    deadline = None
    for i, applicant in enumerate(applicants, start=1):
        if i > 1 and cfg.interval_s:
            sleep(cfg.interval_s)
        m = message(cfg.run_id, i, applicant, clock(), cfg.source)
        queue.send(json.dumps(m, ensure_ascii=False))
        sent.append(m)
        if cfg.mode == "load" and i == GUARD_SAMPLE and len(applicants) > GUARD_SAMPLE:
            ids = {s["application_id"] for s in sent}
            sample = _wait(store, cfg.run_id, ids, clock() + _seconds(cfg.wait_s), clock, sleep)
            guard = _guard(sample, len(applicants), prices)
            if guard["projected_usd"] > BUDGET_GUARD_USD:
                aborted = True
                break
    ids = {s["application_id"] for s in sent}
    deadline = clock() + _seconds(cfg.wait_s)
    docs = _wait(store, cfg.run_id, ids, deadline, clock, sleep)
    by_id = {d["application_id"]: d for d in docs}
    unreached = [
        {"application_id": a, "status": (by_id.get(a) or {}).get("status")}
        for a in sorted(ids)
        if a not in by_id or not done(by_id[a])
    ]
    summary = {
        "run_id": cfg.run_id,
        "mode": cfg.mode,
        "submitted": len(sent),
        "reached": len(ids) - len(unreached),
        "unreached": unreached,
        "aborted": aborted,
        "guard": guard,
    }
    _write(cfg, {cfg.run_id: [project(d) for d in docs]}, summary)
    return summary


def _seconds(s: float):
    from datetime import timedelta

    return timedelta(seconds=s)
```

`reached`는 "대기열 도달 또는 실패로 끝남"이다(`done`). 실패 수는 지표 계산(SLO1 `unreached`)에서 따로 나온다. 위 `_seconds`는 상단 `from datetime import UTC, datetime, timedelta`로 정리해도 된다(구현 시 상단 import 사용).

`src/opsjob/azure.py`

```python
"""Cosmos·Storage Queue 어댑터. SDK는 지연 import(로컬 venv에 없음)."""

from typing import Any


class QueueSender:
    def __init__(self, endpoint: str, credential: Any, name: str = "applications-start") -> None:
        from azure.storage.queue import QueueClient

        # host.json messageEncoding=none: 원문 JSON 그대로 보낸다(v12 기본은 인코딩 없음).
        self._client = QueueClient(endpoint, name, credential=credential)

    def send(self, body: str) -> None:
        self._client.send_message(body)


class CosmosRuns:
    def __init__(self, endpoint: str, database: str, credential: Any) -> None:
        from azure.cosmos import CosmosClient

        db = CosmosClient(endpoint, credential=credential).get_database_client(database)
        self._container = db.get_container_client("applications")

    def by_run(self, run_id: str) -> list[dict[str, Any]]:
        return list(
            self._container.query_items(
                "SELECT * FROM c WHERE c.run_id = @run",
                parameters=[{"name": "@run", "value": run_id}],
                enable_cross_partition_query=True,
            )
        )
```

`src/opsjob/__main__.py`

```python
"""`python -m opsjob` — Container Apps Job 진입점."""

import os
import sys

from evals.transport import emit
from opsjob.run import config_from_env, run
from opsmetrics.prices import load_prices


def main() -> int:
    from azure.identity import ManagedIdentityCredential

    from opsjob.azure import CosmosRuns, QueueSender

    cfg = config_from_env(os.environ)
    credential = ManagedIdentityCredential(client_id=os.environ.get("AZURE_CLIENT_ID"))
    summary = run(
        cfg,
        queue=QueueSender(os.environ["QUEUE_ENDPOINT"], credential),
        store=CosmosRuns(os.environ["COSMOS_ENDPOINT"], os.environ["COSMOS_DATABASE"], credential),
        prices=load_prices(),
    )
    emit(cfg.run_id, [cfg.out_dir / "docs.json", cfg.out_dir / "summary.json"], print)
    return 0 if summary.get("mode") == "collect" or summary.get("submitted") else 1


if __name__ == "__main__":
    sys.exit(main())
```

`emit` 시그니처(`src/evals/transport.py` line 35)를 확인해 인자 순서를 맞춘다.

`requirements-eval.txt`: `azure-cosmos==4.17.1`, `azure-storage-queue==12.18.1` 추가(버전은 `src/orchestrator/requirements.txt` 고정값과 같게 — 다르면 그쪽 값을 따른다).

`src/evals/Dockerfile`: 기존 `COPY src/... /app/...` 줄 옆에 `COPY src/opsmetrics /app/opsmetrics`, `COPY src/opsjob /app/opsjob`. `prices.json`은 `/app/evals/prices.json`에 이미 있다(`opsmetrics.prices.DEFAULT_PATH` = `/app/evals/prices.json`).

평가 이미지 테스트(`tests/evals` 또는 `tests/infra`에 Dockerfile 검사)가 있으면 새 COPY 줄을 기대값에 추가한다.

- [ ] **Step 4: 통과 확인** — `pytest tests/opsjob tests/evals` → PASS

- [ ] **Step 5: 커밋** — `feat(opsjob): in-VNet load, submit and collect job` (트레일러 포함)

---

## Task 11: 인프라(job-ops, id-ops, OTel 설정, Workbook·경보, Foundry 연결)

**Files:**
- Modify: `infra/modules/identities.bicep`, `infra/modules/eval-job.bicep`, `infra/modules/monitoring.bicep`, `infra/resources.bicep`, `infra/main.bicep`
- Create: `infra/modules/ops-insights.bicep`, `infra/modules/ops-workbook.json`
- Test: `tests/infra/test_ops_job.py`(신규), 기존 `tests/infra/test_eval_job.py` 유지

- [ ] **Step 1: 실패 테스트** — `tests/infra/test_ops_job.py`

```python
import json
import re

import pytest

from tests.infra.test_console import INFRA, _compile, _resources

JOB = "Microsoft.App/jobs"
CA = "Microsoft.App/containerApps"
COSMOS_READER = "00000000-0000-0000-0000-000000000001"
METRICS_PUBLISHER = "3913510d-42f4-4e42-8a64-420c390055eb"


def _ops(exp) -> dict:
    return exp.one(JOB, "job-ops")


def test_ops_job_runs_opsjob_with_ops_identity(variant):
    job = _ops(variant)
    props = job["properties"]
    assert props["configuration"]["triggerType"] == "Manual"
    assert props["configuration"]["replicaTimeout"] == 2700
    assert list(job["identity"]["userAssignedIdentities"])[0].endswith("/id-ops-amatest")
    container = props["template"]["containers"][0]
    assert container["name"] == "ops" and container["image"] == "acramatest.azurecr.io/eval:v1"
    assert container["command"] == ["/opt/venv/bin/python", "-m", "opsjob"]
    env = {e["name"]: e["value"] for e in container["env"]}
    assert env["COSMOS_DATABASE"] and env["COSMOS_ENDPOINT"].startswith("https://")
    assert re.match(r"^https://st[a-z0-9]+\.queue\.core\.windows\.net$", env["QUEUE_ENDPOINT"])


def test_eval_job_unchanged_by_shared_module(variant):
    container = variant.one(JOB, "job-eval")["properties"]["template"]["containers"][0]
    assert container["name"] == "eval" and not container.get("command")
    assert variant.one(JOB, "job-eval")["properties"]["configuration"]["replicaTimeout"] == 3600


def test_ops_identity_is_least_privilege():
    grants: dict[str, set[str]] = {}
    for module in _resources(_compile(INFRA / "resources.bicep")):
        text = json.dumps(module.get("properties", {}).get("parameters", {}))
        found = re.findall(r"opsPrincipalId\.value.{0,80}?(?:role'\)\.(\w+)|roleDefinitionGuid', '([0-9a-f-]+)')", text)
        if found:
            grants[module["name"]] = {a or b for a, b in found}
    # 큐 보내기(신청 제출)·Cosmos 읽기·이미지 pull뿐이다. 모델·Key Vault·쓰기 권한 없음.
    assert grants == {"storage-functions": {"queueSender"}, "cosmos": {COSMOS_READER}, "acr": {"acrPull"}}


def test_policy_exports_otel(variant):
    container = variant.one(CA, "ca-policy")["properties"]["template"]["containers"][0]
    env = {e["name"]: e.get("value") for e in container["env"]}
    assert env["OTEL_SERVICE_NAME"] == "policy"
    assert env["APPLICATIONINSIGHTS_CONNECTION_STRING"]
    assert env["OTEL_PYTHON_FASTAPI_EXCLUDED_URLS"] == "healthz"
    assert env["OTEL_PYTHON_FASTAPI_EXCLUDE_SPANS"] == "receive,send"


def test_functions_service_name(variant):
    site = variant.one("Microsoft.Web/sites", "func-")
    settings = {s["name"]: s["value"] for s in site["properties"]["siteConfig"]["appSettings"]}
    assert settings["OTEL_SERVICE_NAME"] == "orchestrator"


def test_alerts_and_workbook(variant):
    rules = variant.of_type("Microsoft.Insights/scheduledQueryRules")
    queries = {r["name"]: r["properties"]["criteria"]["allOf"][0]["query"] for r in rules}
    assert any("journey.step" in q for q in queries.values())
    assert any("policy.circuit" in q for q in queries.values())
    windows = sorted(r["properties"]["windowSize"] for r in rules)
    assert windows == ["PT15M", "PT1H"]
    wb = variant.one("Microsoft.Insights/workbooks")
    data = json.loads(wb["properties"]["serializedData"])
    assert "journey.step" in json.dumps(data) and wb["properties"]["category"] == "workbook"


def test_foundry_project_traces_to_app_insights(variant):
    conn = variant.one("Microsoft.CognitiveServices/accounts/projects/connections", "appi-")
    props = conn["properties"]
    assert (props["category"], props["authType"]) == ("AppInsights", "ProjectManagedIdentity")
    assert set(props["metadata"]) >= {"ApplicationInsightsConnectionString", "ResourceId", "ApiType"}
    assert METRICS_PUBLISHER in {
        r["properties"]["roleDefinitionId"].rsplit("/", 1)[-1] for r in variant.role_assignments_on("appi-")
    }


@pytest.mark.parametrize("output", ["OPS_JOB_NAME", "APPLICATIONINSIGHTS_RESOURCE_ID"])
def test_ops_outputs_exposed_to_azd(output):
    assert output in _compile(INFRA / "main.bicep")["outputs"]
```

픽스처 `variant`(이미지 지정 파라미터)·`closed`는 `tests/infra/conftest.py`에 있다. 프로젝트 연결 이름 접두(`appi-`)·`role_assignments_on` 동작은 펼친 결과를 한 번 출력해 맞춘다. App Insights 리소스 이름 접두(`appi-`)는 `names.appi`를 따른다.

- [ ] **Step 2: 실패 확인** — `$env:RUN_INFRA_TESTS='1'; pytest tests/infra/test_ops_job.py` → FAIL

- [ ] **Step 3: identities** — `infra/modules/identities.bicep`

```bicep
// 운영 지표 Job은 신청 큐 보내기·Cosmos 읽기만 합니다(모델·Key Vault·쓰기 권한 없음).
resource ops 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: names.ops
  location: location
  tags: tags
}

output opsId string = ops.id
output opsPrincipalId string = ops.properties.principalId
output opsClientId string = ops.properties.clientId
```

`resources.bicep` names: `idOps: 'id-ops-${environmentName}'`, `opsJob: 'job-ops'`. identities 모듈 `names`에 `ops: names.idOps`.

- [ ] **Step 4: eval-job 모듈 일반화** — `infra/modules/eval-job.bicep`

```bicep
param containerName string = 'eval'
@description('비우면 이미지 기본 명령')
param command array = []
param replicaTimeout int = 3600
...
      replicaTimeout: replicaTimeout
...
          name: containerName
          image: placeholder ? 'mcr.microsoft.com/azurelinux/base/python:3.12' : image
          command: placeholder ? [ 'python3', '-c', 'print("eval image not deployed: python -m ops deploy eval")' ] : (empty(command) ? null : command)
```

머리 주석에 "운영 지표 Job(job-ops)도 같은 모듈을 씁니다"를 추가한다.

- [ ] **Step 5: resources.bicep 배선**

역할(기존 목록에 추가):

```bicep
// stFunc roleAssignments
      { principalId: identities.outputs.opsPrincipalId, roleId: role.queueSender, principalType: sp }
// cosmos 역할 목록
      { principalId: identities.outputs.opsPrincipalId, roleDefinitionGuid: '00000000-0000-0000-0000-000000000001', scope: 'database' }
// acr roleAssignments
      { principalId: identities.outputs.opsPrincipalId, roleId: role.acrPull, principalType: sp }
```

Job:

```bicep
module opsJob 'modules/eval-job.bicep' = {
  name: 'ops-job'
  params: {
    name: names.opsJob
    location: location
    tags: tags
    environmentId: acaInt.outputs.id
    identityId: identities.outputs.opsId
    identityClientId: identities.outputs.opsClientId
    registryServer: acr.outputs.loginServer
    image: evalImage
    containerName: 'ops'
    command: [ '/opt/venv/bin/python', '-m', 'opsjob' ]
    replicaTimeout: 2700
    envVars: [
      { name: 'COSMOS_ENDPOINT', value: cosmos.outputs.endpoint }
      { name: 'COSMOS_DATABASE', value: cosmos.outputs.databaseName }
      { name: 'QUEUE_ENDPOINT', value: 'https://${names.stFunc}.queue.${environment().suffixes.storage}' }
    ]
  }
}

output OPS_JOB_NAME string = opsJob.outputs.name
```

`/opt/venv/bin/python`은 `src/evals/Dockerfile`의 venv 경로(평가 Job 기본 `CMD`와 같음)다.

Policy OTel(`caPolicy` params에 추가):

```bicep
    envVars: [
      { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: monitoring.outputs.appiConnectionString }
      { name: 'OTEL_SERVICE_NAME', value: 'policy' }
      { name: 'OTEL_PYTHON_FASTAPI_EXCLUDED_URLS', value: 'healthz' }
      { name: 'OTEL_PYTHON_FASTAPI_EXCLUDE_SPANS', value: 'receive,send' }
    ]
```

Functions `extraSettings` union 첫 객체에 `OTEL_SERVICE_NAME: 'orchestrator'`.

monitoring 출력(`infra/modules/monitoring.bicep`):

```bicep
output appiId string = appi.id
output appiName string = appi.name
```

`resources.bicep`: `output APPLICATIONINSIGHTS_RESOURCE_ID string = monitoring.outputs.appiId`(ops kql·workbook 링크용). `param opsAlertEmail string = ''`.

ops-insights 모듈:

```bicep
module opsInsights 'modules/ops-insights.bicep' = {
  name: 'ops-insights'
  params: {
    location: location
    tags: tags
    lawId: monitoring.outputs.lawId
    appiName: monitoring.outputs.appiName
    appiConnectionString: monitoring.outputs.appiConnectionString
    aiAccountName: aiAccount.outputs.name
    projectName: aiProject.outputs.name
    projectPrincipalId: aiProject.outputs.principalId
    alertEmail: opsAlertEmail
  }
}
```

`infra/main.bicep`: `param opsAlertEmail string = ''`을 resources에 넘기고, 출력 `OPS_JOB_NAME`·`APPLICATIONINSIGHTS_RESOURCE_ID`를 `EVAL_JOB_NAME` 옆에 추가.

- [ ] **Step 6: ops-insights 모듈** — `infra/modules/ops-insights.bicep`

```bicep
// 운영 지표: Workbook, 경보 2개(에이전트 단계 실패율, Policy 서킷 open), Foundry 프로젝트 → App Insights 트레이스 연결.
param location string
param tags object
param lawId string
param appiName string
@secure()
param appiConnectionString string
param aiAccountName string
param projectName string
param projectPrincipalId string
@description('비우면 Action Group 없이 포털 경보만 남깁니다')
param alertEmail string = ''

var metricsPublisher = '3913510d-42f4-4e42-8a64-420c390055eb'
var hasEmail = !empty(alertEmail)

resource appi 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appiName
}

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: aiAccountName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' existing = {
  parent: account
  name: projectName
}

// 에이전트 서비스 트레이스(invoke_agent·chat·execute_tool)를 우리 App Insights로 보낸다(Probe P4).
resource traces 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: project
  name: appiName
  properties: {
    category: 'AppInsights'
    target: appi.id
    authType: 'ProjectManagedIdentity'
    isSharedToAll: true
    metadata: {
      ApiType: 'Azure'
      ResourceId: appi.id
      ApplicationInsightsConnectionString: appiConnectionString
    }
  }
}

resource projectPublisher 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: appi
  name: guid(appi.id, projectPrincipalId, metricsPublisher)
  properties: {
    principalId: projectPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', metricsPublisher)
  }
}

resource group 'Microsoft.Insights/actionGroups@2023-01-01' = if (hasEmail) {
  name: 'ag-ops'
  location: 'global'
  tags: tags
  properties: {
    groupShortName: 'ops'
    enabled: true
    emailReceivers: [ { name: 'operator', emailAddress: alertEmail, useCommonAlertSchema: true } ]
  }
}

var alerts = [
  {
    name: 'alert-agent-step-failure'
    description: '에이전트 단계(assemble·classify·assess) 실패·안전망 비율 > 1% (1시간, 표본 ≥ 10)'
    window: 'PT1H'
    frequency: 'PT15M'
    query: 'AppTraces | where Message == "journey.step" and tostring(Properties.agent) =~ "true" | summarize total = count(), bad = countif(tostring(Properties.outcome) != "ok") | where total >= 10 and bad * 100.0 / total > 1'
  }
  {
    name: 'alert-policy-circuit-open'
    description: 'Policy 서킷 open 이벤트 (15분)'
    window: 'PT15M'
    frequency: 'PT5M'
    query: 'AppTraces | where Message == "policy.circuit" and tostring(Properties.state) == "open"'
  }
]

resource rules 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = [for a in alerts: {
  name: a.name
  location: location
  tags: tags
  properties: {
    description: a.description
    severity: 2
    enabled: true
    scopes: [ lawId ]
    windowSize: a.window
    evaluationFrequency: a.frequency
    criteria: {
      allOf: [
        {
          query: a.query
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: { numberOfEvaluationPeriods: 1, minFailingPeriodsToAlert: 1 }
        }
      ]
    }
    actions: { actionGroups: hasEmail ? [ group.id ] : [] }
  }
}]

resource workbook 'Microsoft.Insights/workbooks@2023-06-01' = {
  name: guid(resourceGroup().id, 'ops-workbook')
  location: location
  tags: tags
  kind: 'shared'
  properties: {
    displayName: '대출 여정 운영 지표(합성 데이터 기준)'
    category: 'workbook'
    sourceId: lawId
    serializedData: loadTextContent('ops-workbook.json')
  }
}

output connectionName string = traces.name
```

`group.id`는 조건부 리소스라 bicep 경고(BCP318)가 날 수 있다. 경고가 나면 `hasEmail ? [ resourceId('Microsoft.Insights/actionGroups', 'ag-ops') ] : []`로 바꾼다.

- [ ] **Step 7: Workbook 내용** — `infra/modules/ops-workbook.json`

```json
{
  "version": "Notebook/1.0",
  "items": [
    {"type": 1, "content": {"json": "## 대출 여정 운영 지표\n합성 데이터 기준. 신청 단위 연결은 `application_id`로 합니다(Durable 오케스트레이터 → 활동 트레이스 미연결)."}},
    {"type": 9, "content": {"version": "KqlParameterItem/1.0", "parameters": [
      {"name": "TimeRange", "type": 4, "value": {"durationMs": 86400000}, "typeSettings": {"allowCustom": true}},
      {"name": "ApplicationId", "type": 1, "value": ""}
    ]}},
    {"type": 3, "content": {"version": "KqlItem/1.0", "title": "단계별 결과·p95 소요(ms)", "queryType": 0, "resourceType": "microsoft.operationalinsights/workspaces", "timeContext": {"durationMs": 0}, "timeContextFromParameter": "TimeRange",
      "query": "AppTraces\n| where Message == \"journey.step\"\n| extend step = tostring(Properties.step), outcome = tostring(Properties.outcome), ms = todouble(Properties.duration_ms)\n| summarize total = count(), ok = countif(outcome == \"ok\"), degraded = countif(outcome == \"degraded\"), failed = countif(outcome == \"failed\"), p95_ms = percentile(ms, 95) by step\n| extend ok_rate = round(100.0 * ok / total, 1)\n| order by step asc", "visualization": "table"}},
    {"type": 3, "content": {"version": "KqlItem/1.0", "title": "의존성 실패(대상별)", "queryType": 0, "resourceType": "microsoft.operationalinsights/workspaces", "timeContextFromParameter": "TimeRange",
      "query": "AppDependencies\n| where AppRoleName in (\"orchestrator\", \"policy\")\n| summarize total = count(), failed = countif(Success == false) by Target, AppRoleName\n| order by failed desc", "visualization": "table"}},
    {"type": 3, "content": {"version": "KqlItem/1.0", "title": "Policy 서킷 상태", "queryType": 0, "resourceType": "microsoft.operationalinsights/workspaces", "timeContextFromParameter": "TimeRange",
      "query": "AppTraces\n| where Message == \"policy.circuit\"\n| project TimeGenerated, state = tostring(Properties.state), AppRoleInstance\n| order by TimeGenerated desc", "visualization": "table"}},
    {"type": 3, "content": {"version": "KqlItem/1.0", "title": "토큰 추이(시간별)", "queryType": 0, "resourceType": "microsoft.operationalinsights/workspaces", "timeContextFromParameter": "TimeRange",
      "query": "AppTraces\n| where Message == \"journey.step\"\n| summarize input_tokens = sum(tolong(Properties.input_tokens)), output_tokens = sum(tolong(Properties.output_tokens)) by bin(TimeGenerated, 1h)", "visualization": "timechart"}},
    {"type": 3, "content": {"version": "KqlItem/1.0", "title": "신청 1건의 스팬", "queryType": 0, "resourceType": "microsoft.operationalinsights/workspaces", "timeContextFromParameter": "TimeRange",
      "query": "let ops = union AppRequests, AppDependencies | where tostring(Properties.application_id) == \"{ApplicationId}\" | distinct OperationId;\nunion AppRequests, AppDependencies\n| where OperationId in (ops)\n| project TimeGenerated, OperationId, Name, Target, DurationMs, Success, AppRoleName\n| order by TimeGenerated asc", "visualization": "table"}}
  ],
  "fallbackResourceIds": [],
  "$schema": "https://github.com/Microsoft/Application-Insights-Workbooks/blob/master/schema/workbook.json"
}
```

- [ ] **Step 8: 빌드·테스트**

Run: `az bicep build --file infra/main.bicep --stdout > $null` → 오류 0, 새 경고 0(기존 경고 수와 비교).
Run: `$env:RUN_INFRA_TESTS='1'; pytest tests/infra -q -p no:cacheprovider` → PASS(기존 eval·console 테스트 포함)

- [ ] **Step 9: 커밋** — `feat(infra): ops job, OTel settings, workbook, alerts and Foundry tracing` (트레일러 포함)

---

## Task 12: 로컬 `ops` — load·fault·metrics

**Files:**
- Create: `src/ops/jobs.py`, `src/ops/opsrun.py`, `src/ops/faults.py`, `src/ops/costs.py`, `src/ops/kql.py`, `src/ops/opsreport.py`
- Modify: `src/ops/azcli.py`(`rest_post`), `src/ops/eval_runner.py`(jobs 사용), `src/ops/cli.py`
- Test: `tests/ops/test_jobs.py`, `tests/ops/test_opsrun.py`, `tests/ops/test_faults.py`, `tests/ops/test_costs.py`(신규), `tests/ops/test_cli.py`(추가), 기존 `tests/ops/test_eval_runner.py`(변경 없이 통과)

결과 위치는 `ops/reports/<run_id>/`(`docs.json`, `summary.json`, `run.json`, `metrics.json`, `report.md`)다. `eval/reports`처럼 커밋한다(합성 데이터, 원문 PII 없음). `ops deploy eval`은 `provision`으로 `job-eval`·`job-ops` 이미지를 함께 갱신하므로 `deploy.py`는 바꾸지 않는다.

- [ ] **Step 1: 실패 테스트 — jobs** `tests/ops/test_jobs.py`

```python
import json
from pathlib import Path

from evals.transport import chunks
from ops.jobs import collect_files, start_job

TEMPLATE = {"containers": [{"name": "ops", "image": "acr.io/eval:t", "command": ["/opt/venv/bin/python", "-m", "opsjob"],
                            "env": [{"name": "COSMOS_DATABASE", "value": "lending"}, {"name": "OPS_MODE", "value": "x"}]}]}
ENV = {"AZURE_SUBSCRIPTION_ID": "sub"}


def _spy(runner, bodies):
    def call(args, *, stream=False):
        if args[:2] == ["az", "rest"]:
            bodies.append(json.loads(Path(args[args.index("--body") + 1][1:]).read_text(encoding="utf-8")))
        return runner(args, stream=stream)
    return call


def test_start_job_overrides_only_named_vars(fake_runner):
    runner = (fake_runner().on("az", "containerapp", "job", "show", results=[json.dumps(TEMPLATE)])
              .on("az", "rest", results=['{"name": "job-ops-1"}']))
    bodies = []
    name = start_job(_spy(runner, bodies), ENV, "rg", "job-ops", {"OPS_MODE": "load", "OPS_RUN_ID": "load-1"})
    assert name == "job-ops-1"
    (container,) = bodies[0]["containers"]
    assert container["command"] == ["/opt/venv/bin/python", "-m", "opsjob"]
    assert {e["name"]: e["value"] for e in container["env"]} == {
        "COSMOS_DATABASE": "lending", "OPS_MODE": "load", "OPS_RUN_ID": "load-1"}
    url = runner.commands("az", "rest")[0]
    assert "/jobs/job-ops/start?api-version=2024-03-01" in url[url.index("--url") + 1]


def test_start_job_without_overrides_uses_cli(fake_runner):
    runner = fake_runner().on("az", "containerapp", "job", "start", results=["job-eval-1\n"])
    assert start_job(runner, ENV, "rg", "job-eval", None) == "job-eval-1"
    assert runner.commands("az", "rest") == []


def test_collect_files_uses_given_run_id(fake_runner):
    lines = chunks("load-1", "summary.json", b"{}", piece=4) + chunks("other", "summary.json", b"[]", piece=4)
    rows = json.dumps([{"Log_s": f"2026-11-02T01:00:00Z stdout F {x}"} for x in lines])
    runner = fake_runner().on("az", "monitor", "log-analytics", "query", results=[rows])
    assert collect_files(runner, "law", "job-ops-1", ("summary.json",), run_id="load-1") == {"summary.json": b"{}"}
```

- [ ] **Step 2: 실패 테스트 — opsrun** `tests/ops/test_opsrun.py`

```python
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from evals.transport import chunks
from ops.azcli import OpsError
from ops.opsrun import load, metrics, new_run_id

T0 = datetime(2026, 11, 2, 1, 0, tzinfo=UTC)
ENV = {"AZURE_SUBSCRIPTION_ID": "sub", "AZURE_RESOURCE_GROUP": "rg", "OPS_JOB_NAME": "job-ops",
       "LOG_ANALYTICS_CUSTOMER_ID": "law"}
TEMPLATE = {"containers": [{"name": "ops", "env": []}]}


def doc(run_id, i, *, arch="healthy", rec="approve", minutes=4):
    return {
        "application_id": f"{run_id}-{i:02d}", "applicant_id": f"load-{arch}-{i}", "status": "awaiting_officer",
        "submitted_at": "2026-11-02T01:00:00+00:00", "received_at": "2026-11-02T01:00:05+00:00",
        "source": "load", "run_id": run_id, "segment": {"industry": "카페", "region": "서울", "business_age_band": "1~3년"},
        "steps": [{"step": "assemble", "outcome": "ok", "agent": True,
                   "usage": [{"model": "gpt-5.4-mini", "input_tokens": 1000, "output_tokens": 100, "calls": 1}]}],
        "timeline": [{"status": "awaiting_officer", "at": f"2026-11-02T01:{minutes:02d}:00+00:00"}],
        "flags": [], "policy": {"decision": "eligible", "reason_codes": []},
        "assessment": {"recommendation": rec}, "officer_decision": None,
    }


def _save(root: Path, run_id: str, docs: list, summary: dict | None = None):
    (root / run_id).mkdir(parents=True)
    (root / run_id / "docs.json").write_text(json.dumps({"runs": {run_id: docs}}), encoding="utf-8")
    (root / run_id / "summary.json").write_text(json.dumps(summary or {"unreached": []}), encoding="utf-8")


def _rows(run_id: str, files: dict[str, bytes]) -> str:
    lines = [x for name, data in files.items() for x in chunks(run_id, name, data, piece=50)]
    return json.dumps([{"Log_s": f"t stdout F {x}"} for x in lines])


def _job_runner(fake_runner, run_id, files):
    return (fake_runner(ENV)
            .on("az", "containerapp", "job", "show", results=[json.dumps(TEMPLATE)])
            .on("az", "rest", results=['{"name": "job-ops-x1"}'])
            .on("az", "containerapp", "job", "execution", "show", results=["Running\n", "Succeeded\n"])
            .on("az", "monitor", "log-analytics", "query", results=[_rows(run_id, files)]))


def _spy(runner, bodies):
    def call(args, *, stream=False):
        if args[:2] == ["az", "rest"]:
            bodies.append(json.loads(Path(args[args.index("--body") + 1][1:]).read_text(encoding="utf-8")))
        return runner(args, stream=stream)
    return call


def test_new_run_id():
    assert new_run_id("load", T0) == "load-20261102010000"


def test_load_starts_job_and_saves_outputs(fake_runner, tmp_path):
    run_id = "load-20261102010000"
    summary = {"run_id": run_id, "submitted": 24, "reached": 24, "unreached": [], "aborted": False}
    runner = _job_runner(fake_runner, run_id, {"docs.json": b'{"runs": {}}', "summary.json": json.dumps(summary).encode()})
    bodies = []
    result = load(_spy(runner, bodies), count=24, interval_s=15, now=T0, out_root=tmp_path, sleep=lambda s: None)
    env = {e["name"]: e["value"] for e in bodies[0]["containers"][0]["env"]}
    assert env == {"OPS_RUN_ID": run_id, "OPS_MODE": "load", "OPS_COUNT": "24", "OPS_INTERVAL_S": "15",
                   "OPS_SOURCE": "load"}
    assert result.summary == summary and result.execution == "job-ops-x1"
    assert json.loads((tmp_path / run_id / "run.json").read_text(encoding="utf-8"))["execution"] == "job-ops-x1"


def test_load_rejects_count_over_grid(fake_runner, tmp_path):
    runner = fake_runner(ENV)
    with pytest.raises(OpsError, match="1~24"):
        load(runner, count=25, out_root=tmp_path)
    assert runner.commands("az") == []


def test_metrics_without_cost_access_still_reports(fake_runner, tmp_path):
    _save(tmp_path, "load-1", [doc("load-1", i) for i in range(1, 6)])

    def no_access(*a, **k):
        raise OpsError("명령 실패(1): az rest\nAuthorizationFailed")

    out = metrics(fake_runner(ENV), "load-1", out_root=tmp_path, now=T0, cost_fn=no_access)
    data = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    assert data["summary"]["fixed"] == {"monthly_usd": None, "source": "측정 불가(명령 실패(1): az rest)"}
    assert data["summary"]["latency"]["n"] == 5 and data["summary"]["ai_cost"]["applications"] == 5
    report = (out / "report.md").read_text(encoding="utf-8")
    assert "합성 데이터 기준" in report and "측정 불가" in report


def test_metrics_manual_fixed_cost_skips_query(fake_runner, tmp_path):
    _save(tmp_path, "load-1", [doc("load-1", 1)])

    def never(*a, **k):
        raise AssertionError("Cost Management를 부르면 안 됨")

    out = metrics(fake_runner(ENV), "load-1", out_root=tmp_path, now=T0, fixed_monthly_usd=100.0, cost_fn=never)
    summary = json.loads((out / "metrics.json").read_text(encoding="utf-8"))["summary"]
    assert summary["fixed"] == {"monthly_usd": 100.0, "source": "수동 입력"}
    assert summary["unit_economics"][0]["fixed_usd"] == 1.0


def test_metrics_recollects_missing_runs_and_adds_fault_section(fake_runner, tmp_path):
    _save(tmp_path, "load-1", [doc("load-1", 1)])
    fault_docs = [{**doc("fault-1", 1), "source": "fault", "flags": ["policy_unavailable"]}]
    collected = {"runs": {"load-1": [doc("load-1", 1), doc("load-1", 2)], "fault-1": fault_docs}}
    run_id = "collect-20261102010000"
    runner = _job_runner(fake_runner, run_id, {"docs.json": json.dumps(collected).encode(),
                                                "summary.json": b'{"mode": "collect"}'})
    (tmp_path / "fault-1").mkdir()
    (tmp_path / "fault-1" / "run.json").write_text(
        json.dumps({"run_id": "fault-1", "fault": {"scenario": "policy-down", "reverted": True}}), encoding="utf-8")
    seen = {}

    def deps(az, workspace, ids, **kw):
        seen["ids"] = ids
        return {ids[0]: [{"target": "ca-policy", "role": "orchestrator", "total": 0, "failed": 0}]}

    bodies = []
    out = metrics(_spy(runner, bodies), "load-1", fault_runs=["fault-1"], out_root=tmp_path, now=T0,
                  fixed_monthly_usd=0.0, kql_fn=deps, sleep=lambda s: None)
    env = {e["name"]: e["value"] for e in bodies[0]["containers"][0]["env"]}
    assert (env["OPS_MODE"], env["OPS_RUN_IDS"]) == ("collect", "load-1,fault-1")
    data = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    assert data["summary"]["applications"] == 2
    (fault,) = data["faults"]
    assert fault["scenario"] == "policy-down" and fault["reverted"] is True and seen["ids"] == ["fault-1-01"]
    assert fault["applications"][0]["flags"] == ["policy_unavailable"]
    assert "policy-down" in (out / "report.md").read_text(encoding="utf-8")
```

- [ ] **Step 3: 실패 테스트 — faults** `tests/ops/test_faults.py`

```python
import json

import pytest

from ops.azcli import OpsError
from ops.faults import SCENARIOS, run_scenario, set_fault, status

ENV = {"AZURE_SUBSCRIPTION_ID": "sub", "AZURE_RESOURCE_GROUP": "rg", "MOCKAPI_APP_NAME": "ca-mockapi",
       "POLICY_APP_NAME": "ca-policy"}
SHOW = ("az", "containerapp", "show")
UPDATE = ("az", "containerapp", "update")


def props(mode=None, ready=True):
    env = [{"name": "MOCK_CONSENT_SECRET", "secretRef": "s"}] + ([{"name": "FAULT_MODE", "value": mode}] if mode else [])
    return json.dumps({"latestRevisionName": "r2", "latestReadyRevisionName": "r2" if ready else "r1",
                       "template": {"containers": [{"env": env}]}})


class Done:
    def __init__(self, tmp_path):
        self.out_dir = tmp_path
        self.run_id = "fault-1"
        self.summary = {"submitted": 2, "reached": 2}


def test_set_fault_waits_for_ready_revision(fake_runner):
    runner = fake_runner(ENV).on(*SHOW, results=[props(), props("flaky", ready=False), props("flaky")])
    sleeps = []
    set_fault(runner, "source", True, sleep=sleeps.append, poll_wait=10)
    (update,) = runner.commands(*UPDATE)
    assert update[update.index("-n") + 1] == "ca-mockapi" and "FAULT_MODE=flaky" in update
    assert sleeps == [10]


def test_set_fault_off_is_noop_when_already_off(fake_runner):
    runner = fake_runner(ENV).on(*SHOW, results=[props()])
    set_fault(runner, "policy", False, sleep=lambda s: None)
    assert runner.commands(*UPDATE) == []


def test_status(fake_runner):
    runner = fake_runner(ENV).on(*SHOW, results=[props("flaky"), props()])
    assert status(runner) == {"source": "flaky", "policy": "off"}


def test_scenario_reverts_and_records_meta(fake_runner, tmp_path):
    runner = fake_runner(ENV).on(*SHOW, results=[props(), props("unavailable"), props("unavailable"), props()])
    calls = []

    def submit(r, applicants, **kw):
        calls.append((applicants, kw["interval_s"]))
        return Done(tmp_path)

    result = run_scenario(runner, "policy-down", submit_fn=submit, sleep=lambda s: None)
    assert calls == [(SCENARIOS["policy-down"].applicants, 90)]
    set_call, revert = runner.commands(*UPDATE)
    assert "FAULT_MODE=unavailable" in set_call and revert[revert.index("--remove-env-vars") + 1] == "FAULT_MODE"
    assert set_call[set_call.index("-n") + 1] == "ca-policy"
    meta = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert meta["fault"] == {"scenario": "policy-down", "app": "ca-policy", "mode": "unavailable", "reverted": True}
    assert result.run_id == "fault-1"


def test_scenario_reverts_on_failure(fake_runner):
    runner = fake_runner(ENV).on(*SHOW, results=[props(), props("flaky"), props("flaky"), props()])

    def submit(r, applicants, **kw):
        raise OpsError("job boom")

    with pytest.raises(OpsError, match="job boom"):
        run_scenario(runner, "source-flaky", submit_fn=submit, sleep=lambda s: None)
    assert any("--remove-env-vars" in c for c in runner.commands(*UPDATE))


def test_revert_failure_is_reported(fake_runner):
    runner = (fake_runner(ENV).on(*SHOW, results=[props(), props("flaky"), props("flaky")])
              .on(*UPDATE, results=["", OpsError("revert boom")]))
    original = OpsError("job boom")

    def submit(r, applicants, **kw):
        raise original

    with pytest.raises(OpsError, match="장애 원복 실패") as info:
        run_scenario(runner, "source-flaky", submit_fn=submit, sleep=lambda s: None)
    assert info.value.__cause__ is original
    assert "python -m ops fault source off" in str(info.value)
```

`FakeRunner`의 `on` 접두어는 `show`가 앱을 구분하지 않는다. `status`는 source → policy 순서로 조회한다.

- [ ] **Step 4: 실패 테스트 — costs·kql** `tests/ops/test_costs.py`

```python
import json
from datetime import date
from pathlib import Path

import pytest

from ops.azcli import OpsError
from ops.costs import monthly_fixed_usd, period
from ops.kql import dependency_counts

ENV = {"AZURE_SUBSCRIPTION_ID": "sub", "AZURE_RESOURCE_GROUP": "rg"}


def result(rows, currency="USD"):
    return json.dumps({"properties": {
        "columns": [{"name": "totalCost"}, {"name": "UsageDate"}, {"name": "MeterCategory"}, {"name": "Currency"}],
        "rows": [[cost, day, cat, currency] for cost, day, cat in rows]}})


ROWS = [(10.0, 20261025, "Azure Cosmos DB"), (4.0, 20261025, "Foundry Models"),
        (12.0, 20261026, "Azure Cosmos DB"), (2.0, 20261026, "Container Apps")]


def test_period_is_last_seven_completed_days():
    assert period(date(2026, 11, 2)) == (date(2026, 10, 25), date(2026, 10, 31))


def test_cost_excludes_foundry_models_and_retries_429(fake_runner):
    runner = fake_runner(ENV).on("az", "rest", results=[OpsError("명령 실패(1)\n(429) Too Many Requests"), result(ROWS)])
    bodies, sleeps = [], []

    def spy(args, *, stream=False):
        if args[:2] == ["az", "rest"]:
            bodies.append(json.loads(Path(args[args.index("--body") + 1][1:]).read_text(encoding="utf-8")))
        return runner(args, stream=stream)

    usd, source = monthly_fixed_usd(spy, ENV, date(2026, 11, 2), sleep=sleeps.append)
    assert usd == 360.0  # (10 + 12 + 2) / 2일 × 30
    assert sleeps == [20.0]
    assert bodies[0]["timePeriod"] == {"from": "2026-10-25T00:00:00Z", "to": "2026-10-31T23:59:59Z"}
    assert "Foundry Models 제외" in source and "2일" in source


def test_non_usd_requires_fx(fake_runner):
    runner = fake_runner(ENV).on("az", "rest", results=[result([(14000.0, 20261025, "Azure Cosmos DB")], "KRW")])
    with pytest.raises(OpsError, match="--fx-krw-per-usd"):
        monthly_fixed_usd(runner, ENV, date(2026, 11, 2), sleep=lambda s: None)
    usd, source = monthly_fixed_usd(runner, ENV, date(2026, 11, 2), fx_krw_per_usd=1400.0, sleep=lambda s: None)
    assert usd == 300.0 and "1400" in source


def test_non_429_error_is_not_retried(fake_runner):
    runner = fake_runner(ENV).on("az", "rest", results=[OpsError("AuthorizationFailed")])
    with pytest.raises(OpsError, match="AuthorizationFailed"):
        monthly_fixed_usd(runner, ENV, date(2026, 11, 2), sleep=lambda s: pytest.fail("재시도 금지"))


def test_dependency_counts_single_line_query(fake_runner):
    rows = json.dumps([{"app": "fault-1-01", "Target": "ca-policy", "AppRoleName": "orchestrator",
                        "total": "3", "failed": "3"}])
    runner = fake_runner().on("az", "monitor", "log-analytics", "query", results=[rows])
    assert dependency_counts(runner, "law", ["fault-1-01"], sleep=lambda s: None) == {
        "fault-1-01": [{"target": "ca-policy", "role": "orchestrator", "total": 3, "failed": 3}]}
    call = runner.commands("az", "monitor")[0]
    kql = call[call.index("--analytics-query") + 1]
    # az.cmd는 큰따옴표·줄바꿈이 든 인자를 깨뜨린다.
    assert "\n" not in kql and '"' not in kql and "'fault-1-01'" in kql
```

- [ ] **Step 5: 실패 테스트 — CLI** `tests/ops/test_cli.py`에 추가

```python
def test_fault_and_metrics_dispatch(monkeypatch, fake_runner, tmp_path):
    import ops.cli as cli

    seen = []
    monkeypatch.setattr(cli.faults, "set_fault", lambda r, target, on: seen.append(("set", target, on)))
    monkeypatch.setattr(cli.faults, "status", lambda r: {"source": "off", "policy": "unavailable"})
    monkeypatch.setattr(cli.opsrun, "metrics", lambda r, run, **kw: seen.append(("metrics", run, kw)) or tmp_path)
    out = io.StringIO()
    assert cli.main(["fault", "source", "flaky"], runner=fake_runner(), out=out) == 0
    assert cli.main(["fault", "policy", "off"], runner=fake_runner(), out=out) == 0
    assert cli.main(["fault", "status"], runner=fake_runner(), out=out) == 0
    assert cli.main(["metrics", "--run", "load-1", "--fault-runs", "f-1", "f-2", "--fixed-monthly-usd", "50"],
                    runner=fake_runner(), out=out) == 0
    assert seen[:2] == [("set", "source", True), ("set", "policy", False)]
    assert seen[2][2]["fault_runs"] == ["f-1", "f-2"] and seen[2][2]["fixed_monthly_usd"] == 50.0
    assert "policy: unavailable" in out.getvalue()
```

`test_cli.py` 상단에 `io` import가 없으면 추가한다.

- [ ] **Step 6: 실패 확인** — `pytest tests/ops` → 새 테스트 FAIL(ImportError), 기존 PASS

- [ ] **Step 7: 구현 — azcli·jobs·eval_runner**

`src/ops/azcli.py` 끝에 추가:

```python
def rest_post(az: Runner, url: str, body: dict) -> dict:
    """az rest POST. az.cmd는 따옴표가 든 인자를 깨뜨리므로 본문은 임시 파일로 넘긴다."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(body, f)
    try:
        return json.loads(az(["az", "rest", "--method", "post", "--url", url, "--body", f"@{f.name}"]) or "{}")
    finally:
        os.unlink(f.name)
```

(`json`, `os`, `tempfile` import 추가.)

`src/ops/jobs.py`

```python
"""Container Apps Job 공통: 시작(컨테이너 환경 변수 덮어쓰기)·종료 대기·콘솔 로그 조각(EVALCHUNK) 회수."""
from __future__ import annotations

import json
from collections.abc import Callable, Iterable

from evals.transport import TAG, TransportError, reassemble
from ops.azcli import OpsError, Runner, rest_post
from ops.smoke_runner import TERMINAL, log_hint

START_API = "2024-03-01"


def start_job(az: Runner, env: dict[str, str], group: str, job: str,
              overrides: dict[str, str] | None = None) -> str:
    if not overrides:
        return az(["az", "containerapp", "job", "start", "-g", group, "-n", job,
                   "--query", "name", "-o", "tsv"]).strip()
    # start 템플릿 덮어쓰기는 컨테이너 정의를 통째로 바꾼다. 기존 정의를 복사하고 지정한 변수만 바꿔 ARM으로 시작한다.
    template = json.loads(az(["az", "containerapp", "job", "show", "-g", group, "-n", job,
                              "--query", "properties.template", "-o", "json"]))
    containers = template["containers"]
    containers[0]["env"] = ([e for e in containers[0].get("env", []) if e.get("name") not in overrides]
                            + [{"name": k, "value": v} for k, v in overrides.items()])
    body = {"containers": containers}
    if template.get("initContainers"):
        body["initContainers"] = template["initContainers"]
    url = (f"https://management.azure.com/subscriptions/{env['AZURE_SUBSCRIPTION_ID']}/resourceGroups/{group}"
           f"/providers/Microsoft.App/jobs/{job}/start?api-version={START_API}")
    return rest_post(az, url, body)["name"]


def wait_job(az: Runner, env: dict[str, str], group: str, job: str, execution: str, *,
             sleep: Callable[[float], None], poll_wait: float, poll_limit: int, label: str) -> None:
    for _ in range(poll_limit):
        status = az(["az", "containerapp", "job", "execution", "show", "-g", group, "-n", job,
                     "--job-execution-name", execution, "--query", "properties.status", "-o", "tsv"]).strip()
        if status in TERMINAL:
            break
        sleep(poll_wait)
    else:
        raise OpsError(f"{label} 대기 시간 초과: {execution}\n{log_hint(env, execution)}")
    if status != "Succeeded":
        raise OpsError(f"{label} 실패: {execution} 상태 {status}\n{log_hint(env, execution)}")


def _query(execution: str) -> str:
    return (f"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '{execution}' "
            f"| where Log_s has '{TAG}' | project Log_s")


def _run_id(lines: list[str], execution: str) -> str:
    # Job에 실행 이름이 주입되지 않으면 local-<시각>을 쓴다. 로그는 이미 실행 이름으로 걸렀으므로 그 하나를 고른다.
    ids = {line[line.find(TAG):].split(" ")[1] for line in lines if TAG + " " in line}
    if execution in ids or len(ids) != 1:
        return execution
    return ids.pop()


def collect_files(az: Runner, workspace: str, execution: str, required: Iterable[str],
                  run_id: str | None = None) -> dict[str, bytes]:
    rows = json.loads(az(["az", "monitor", "log-analytics", "query", "-w", workspace,
                          "--analytics-query", _query(execution), "-o", "json"]) or "[]")
    lines = [row.get("Log_s", "") for row in rows]
    files = reassemble(lines, run_id or _run_id(lines, execution))
    missing = [name for name in required if name not in files]
    if missing:
        raise TransportError(f"파일 누락: {', '.join(missing)}")
    return files


def collect_with_retry(az: Runner, workspace: str, execution: str, required: Iterable[str], *,
                       sleep: Callable[[float], None], wait: float, limit: int,
                       run_id: str | None = None) -> dict[str, bytes]:
    """Log Analytics 수집은 수 분 늦을 수 있어 조각이 모두 모일 때까지 다시 조회한다. 마지막 오류는 그대로 올린다."""
    for attempt in range(1, limit + 1):
        try:
            return collect_files(az, workspace, execution, required, run_id)
        except (TransportError, OpsError):
            if attempt == limit:
                raise
            sleep(wait)
    raise OpsError("limit은 1 이상이어야 합니다")
```

`src/ops/eval_runner.py`: `_query`, `_run_id`, `collect_report`, `_start`, `_wait`, `START_API`와 `json`/`os`/`tempfile` import를 지우고 `from ops.jobs import collect_with_retry, start_job, wait_job`. `run_eval` 본문:

```python
    if collect:
        execution = collect
    else:
        execution = start_job(az, env, group, job, None if suite == "all" else {"EVAL_SUITE": suite})
        wait_job(az, env, group, job, execution, sleep=sleep, poll_wait=poll_wait, poll_limit=poll_limit,
                 label="평가")

    workspace = env.get("LOG_ANALYTICS_CUSTOMER_ID", "")
    try:
        files = collect_with_retry(az, workspace, execution, REQUIRED, sleep=sleep, wait=log_wait, limit=log_limit)
    except (TransportError, OpsError) as err:  # 조각 누락·수집 지연·일시적 조회 오류
        container = env.get("EVAL_REPORTS_CONTAINER", "eval-reports")
        raise OpsError(
            f"리포트 복원 실패: {err}\n잠시 뒤 회수만 다시: python -m ops eval --collect {execution}\n"
            f"원본 사본: 데이터 스토리지 Blob {container}/{execution}/ (사설 엔드포인트 전용)\n"
            f"{log_hint(env, execution)}") from err
```

(`json`은 `EvalResult` summary 파싱에 계속 쓴다.) 메시지 문구가 기존과 같으므로 `test_eval_runner.py`는 수정 없이 통과해야 한다(Review Focus 12).

- [ ] **Step 8: 구현 — costs·kql**

`src/ops/costs.py`

```python
"""고정비: Cost Management 실측(리소스 그룹, 최근 완료 7일) 일평균 × 30. 모델 사용료는 실측 토큰으로 따로 계산하므로 뺀다."""
from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

from ops.azcli import OpsError, Runner, rest_post

API = "2023-11-01"
EXCLUDED = frozenset({"Foundry Models"})
DAYS = 7
RETRIES = 5
RETRY_WAIT = 20.0  # Probe P6: retry-after 2~20초. az rest는 헤더를 주지 않으므로 상한으로 고정


def period(today: date) -> tuple[date, date]:
    # 데이터가 약 1일 늦으므로 어제도 미완료로 본다.
    end = today - timedelta(days=2)
    return end - timedelta(days=DAYS - 1), end


def _body(start: date, end: date) -> dict:
    return {
        "type": "ActualCost",
        "timeframe": "Custom",
        "timePeriod": {"from": f"{start}T00:00:00Z", "to": f"{end}T23:59:59Z"},
        "dataset": {
            "granularity": "Daily",
            "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
            "grouping": [{"type": "Dimension", "name": "MeterCategory"}],
        },
    }


def _query(az: Runner, url: str, body: dict, sleep: Callable[[float], None]) -> dict:
    for attempt in range(1, RETRIES + 1):
        try:
            return rest_post(az, url, body)
        except OpsError as err:
            text = str(err)
            if attempt == RETRIES or ("429" not in text and "Too Many Requests" not in text):
                raise
            sleep(RETRY_WAIT)
    raise OpsError("unreachable")


def monthly_fixed_usd(az: Runner, env: dict[str, str], today: date, *, fx_krw_per_usd: float | None = None,
                      sleep: Callable[[float], None]) -> tuple[float, str]:
    start, end = period(today)
    url = (f"https://management.azure.com/subscriptions/{env['AZURE_SUBSCRIPTION_ID']}/resourceGroups/"
           f"{env['AZURE_RESOURCE_GROUP']}/providers/Microsoft.CostManagement/query?api-version={API}")
    props = _query(az, url, _body(start, end), sleep)["properties"]
    names = [c["name"] for c in props["columns"]]
    rows = [dict(zip(names, r)) for r in props["rows"]]
    days = {r["UsageDate"] for r in rows}
    if not days:
        raise OpsError(f"비용 데이터 없음({start}~{end})")
    kept = [r for r in rows if r.get("MeterCategory") not in EXCLUDED]
    total = sum(float(r["totalCost"]) for r in kept)
    currencies = {r.get("Currency") for r in rows} - {None}
    note = ""
    if currencies - {"USD"}:
        if currencies != {"KRW"} or not fx_krw_per_usd:
            raise OpsError(f"비용 통화 {sorted(currencies)}: --fx-krw-per-usd로 원/달러 환율을 주세요")
        total /= fx_krw_per_usd
        note = f", 환율 {fx_krw_per_usd:g}원/USD"
    monthly = round(total / len(days) * 30, 2)
    return monthly, (f"Cost Management 실측 {start}~{end} 일평균({len(days)}일) × 30, "
                     f"Foundry Models 제외{note}")
```

검산: `ROWS`에서 Foundry 제외 합 24, 날짜 2개 → 12/일 × 30 = 360. KRW 14000/1400 = 10, 1일 → 300.

`src/ops/kql.py`

```python
"""장애 시나리오 증거: 신청별 의존성 호출 수(App Insights → Log Analytics)."""
from __future__ import annotations

import json
from collections.abc import Callable

from ops.azcli import OpsError, Runner

LOOKBACK = "3d"


def _query(application_ids: list[str]) -> str:
    # az.cmd는 큰따옴표·줄바꿈이 든 인자를 깨뜨리므로 작은따옴표 한 줄로 만든다. ID는 opsjob 형식([a-z0-9.-_])만 온다.
    ids = ", ".join(f"'{a}'" for a in application_ids)
    return (f"let ids = dynamic([{ids}]); "
            f"let ops = union AppRequests, AppDependencies | where TimeGenerated > ago({LOOKBACK}) "
            "| extend app = tostring(Properties.application_id) | where app in (ids) | distinct OperationId, app; "
            f"AppDependencies | where TimeGenerated > ago({LOOKBACK}) and not(Name startswith 'journey.') "
            "| join kind=inner ops on OperationId "
            "| summarize total = count(), failed = countif(Success == false) by app, Target, AppRoleName")


def dependency_counts(az: Runner, workspace: str, application_ids: list[str], *,
                      sleep: Callable[[float], None], retries: int = 3, wait: float = 30.0,
                      ) -> dict[str, list[dict]]:
    for attempt in range(1, retries + 1):
        try:
            rows = json.loads(az(["az", "monitor", "log-analytics", "query", "-w", workspace,
                                  "--analytics-query", _query(application_ids), "-o", "json"]) or "[]")
            break
        except OpsError:
            if attempt == retries:
                raise
            sleep(wait)
    out: dict[str, list[dict]] = {a: [] for a in application_ids}
    for r in rows:
        out.setdefault(r["app"], []).append({"target": r["Target"], "role": r["AppRoleName"],
                                             "total": int(r["total"]), "failed": int(r["failed"])})
    return out
```

- [ ] **Step 9: 구현 — opsrun·opsreport**

`src/ops/opsrun.py`

```python
"""운영 지표 흐름: job-ops 실행(load·submit·collect), 결과 저장, 지표·보고서 작성."""
from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.transport import TransportError
from ops import costs, kql
from ops.azcli import OpsError, Runner, subscription_runner
from ops.azdenv import get_env
from ops.jobs import collect_with_retry, start_job, wait_job
from ops.opsreport import render
from ops.smoke_runner import log_hint
from opsmetrics.prices import load_prices
from opsmetrics.summary import summarize

REPORTS = Path(__file__).resolve().parents[2] / "ops" / "reports"
REQUIRED = ("docs.json", "summary.json")
MAX_COUNT = 24


@dataclass(frozen=True)
class JobRun:
    run_id: str
    execution: str
    out_dir: Path
    summary: dict[str, Any]


def new_run_id(kind: str, now: datetime) -> str:
    return f"{kind}-{now:%Y%m%d%H%M%S}"


def _now() -> datetime:
    return datetime.now(UTC)


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def update_meta(out_dir: Path, extra: dict[str, Any]) -> None:
    path = out_dir / "run.json"
    _write(path, {**(_read(path) if path.exists() else {}), **extra})


def run_job(runner: Runner, run_id: str, overrides: dict[str, str], *, out_root: Path | None = None,
            sleep: Callable[[float], None] | None = None, poll_wait: float = 15, poll_limit: int = 200,
            log_wait: float = 30, log_limit: int = 20, progress: Callable[[str], None] | None = None) -> JobRun:
    sleep = sleep or time.sleep
    env = get_env(runner)
    group, job = env["AZURE_RESOURCE_GROUP"], env.get("OPS_JOB_NAME")
    if not job:
        raise OpsError("azd 환경에 OPS_JOB_NAME이 없습니다. 인프라 갱신(azd provision) 후 다시 실행하세요.")
    az = subscription_runner(runner, env)
    execution = start_job(az, env, group, job, {"OPS_RUN_ID": run_id, **overrides})
    if progress:
        progress(f"job-ops 시작: {execution} (run {run_id}). 끝날 때까지 기다립니다.")
    wait_job(az, env, group, job, execution, sleep=sleep, poll_wait=poll_wait, poll_limit=poll_limit,
             label="운영 Job")
    try:
        files = collect_with_retry(az, env.get("LOG_ANALYTICS_CUSTOMER_ID", ""), execution, REQUIRED,
                                   run_id=run_id, sleep=sleep, wait=log_wait, limit=log_limit)
    except (TransportError, OpsError) as err:
        raise OpsError(f"결과 복원 실패: {err}\n{log_hint(env, execution)}") from err
    out_dir = (out_root or REPORTS) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (out_dir / name).write_bytes(data)
    update_meta(out_dir, {"run_id": run_id, "execution": execution, "mode": overrides.get("OPS_MODE")})
    return JobRun(run_id, execution, out_dir, json.loads(files["summary.json"]))


def load(runner: Runner, *, count: int = MAX_COUNT, interval_s: float = 15, now: datetime | None = None,
         **job_kw: Any) -> JobRun:
    if not 1 <= count <= MAX_COUNT:
        raise OpsError(f"--count는 1~{MAX_COUNT}입니다(균형 격자 크기)")
    return run_job(runner, new_run_id("load", now or _now()),
                   {"OPS_MODE": "load", "OPS_COUNT": str(count), "OPS_INTERVAL_S": f"{interval_s:g}",
                    "OPS_SOURCE": "load"}, **job_kw)


def submit(runner: Runner, applicants: Sequence[str], *, interval_s: float, now: datetime | None = None,
           **job_kw: Any) -> JobRun:
    return run_job(runner, new_run_id("fault", now or _now()),
                   {"OPS_MODE": "submit", "OPS_APPLICANTS": ",".join(applicants),
                    "OPS_INTERVAL_S": f"{interval_s:g}", "OPS_SOURCE": "fault"}, **job_kw)


def _fault_section(az: Runner, env: dict[str, str], run_id: str, docs: list[dict[str, Any]], root: Path,
                   kql_fn: Callable[..., dict], sleep: Callable[[float], None]) -> dict[str, Any]:
    meta = (_read(root / run_id / "run.json") if (root / run_id / "run.json").exists() else {}).get("fault") or {}
    ids = [d["application_id"] for d in docs]
    deps, error = None, None
    try:
        deps = kql_fn(az, env.get("LOG_ANALYTICS_CUSTOMER_ID", ""), ids, sleep=sleep) if ids else {}
    except OpsError as err:
        error = str(err).splitlines()[0]
    return {
        "run_id": run_id,
        "scenario": meta.get("scenario"),
        "reverted": meta.get("reverted"),
        "applications": [{"application_id": d["application_id"], "applicant_id": d.get("applicant_id"),
                          "status": d.get("status"), "flags": d.get("flags") or [],
                          "steps": [{"step": s.get("step"), "outcome": s.get("outcome")} for s in d.get("steps") or []]}
                         for d in docs],
        "dependencies": deps,
        "dependencies_error": error,
    }


def metrics(runner: Runner, run_id: str, *, fault_runs: Sequence[str] = (), fixed_monthly_usd: float | None = None,
            fx_krw_per_usd: float | None = None, recollect: bool = False, out_root: Path | None = None,
            now: datetime | None = None, sleep: Callable[[float], None] | None = None,
            cost_fn: Callable[..., tuple[float, str]] = costs.monthly_fixed_usd,
            kql_fn: Callable[..., dict] = kql.dependency_counts, **job_kw: Any) -> Path:
    root, now, sleep = out_root or REPORTS, now or _now(), sleep or time.sleep
    runs = [run_id, *fault_runs]
    if recollect or any(not (root / r / "docs.json").exists() for r in runs):
        job = run_job(runner, new_run_id("collect", now), {"OPS_MODE": "collect", "OPS_RUN_IDS": ",".join(runs)},
                      out_root=root, sleep=sleep, **job_kw)
        collected = _read(job.out_dir / "docs.json")["runs"]
        for r in runs:
            _write(root / r / "docs.json", {"runs": {r: collected.get(r, [])}})
    docs = {r: _read(root / r / "docs.json")["runs"].get(r, []) for r in runs}

    env = get_env(runner)
    az = subscription_runner(runner, env)
    if fixed_monthly_usd is not None:
        fixed, source = fixed_monthly_usd, "수동 입력"
    else:
        try:
            fixed, source = cost_fn(az, env, now.date(), fx_krw_per_usd=fx_krw_per_usd, sleep=sleep)
        except OpsError as err:
            fixed, source = None, f"측정 불가({str(err).splitlines()[0]})"

    out = root / run_id
    load_summary = out / "summary.json"
    data = {
        "run_id": run_id,
        "generated_at": now.isoformat(),
        "load": _read(load_summary) if load_summary.exists() else None,
        "summary": summarize(docs[run_id], load_prices(), fixed, source),
        "faults": [_fault_section(az, env, r, docs[r], root, kql_fn, sleep) for r in fault_runs],
    }
    _write(out / "metrics.json", data)
    (out / "report.md").write_text(render(data), encoding="utf-8")
    return out
```

`test_metrics_without_cost_access_still_reports` 검산: `str(err).splitlines()[0]` = `"명령 실패(1): az rest"` → source `"측정 불가(명령 실패(1): az rest)"`. 문서 5건 모두 timeline 도달 → latency n 5.

`test_metrics_manual_fixed_cost_skips_query` 검산: 고정비 100 ÷ 물량 100 = 1.0.

`src/ops/opsreport.py`

```python
"""metrics.json → 한국어 보고서(markdown). 모든 숫자는 합성 데이터 기준이다."""
from __future__ import annotations

from typing import Any

STEP_NAMES = {"assemble": "데이터 수집", "classify": "거래 분류", "assess": "신용 평가"}
AXIS_NAMES = {"industry": "업종", "region": "지역", "business_age_band": "업력"}


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def _num(x: float | None, nd: int = 1) -> str:
    return "—" if x is None else f"{x:.{nd}f}"


def _usd(x: float | None) -> str:
    return "—" if x is None else f"${x:,.4f}"


def _verdict(met: bool, insufficient: bool) -> str:
    return "표본 부족" if insufficient else ("충족" if met else "미달")


def _slo(s: dict[str, Any]) -> list[str]:
    lat, ag = s["latency"], s["agent_success"]
    lines = [
        "## 1. SLO", "",
        "| 지표 | 목표 | 측정 | 판정 |", "|---|---|---|---|",
        f"| 접수 → 심사역 대기열 p95 | ≤ {_num(lat['target_min'], 0)}분 | {_num(lat['p95_min'])}분 "
        f"(p50 {_num(lat['p50_min'])}, 최대 {_num(lat['max_min'])}, n={lat['n']}) | "
        f"{_verdict(lat['met'], lat['insufficient'])} |",
        f"| 에이전트 단계 성공률 | ≥ {_pct(ag['target'])} | {_pct(ag['rate'])} (ok {ag['ok']}/{ag['total']}, "
        f"degraded {ag['degraded']}, failed {ag['failed']}) | {_verdict(ag['met'], ag['total'] == 0)} |",
        "",
        f"미도달 {lat['unreached']}건, 접수 시각 대체 {lat['received_only']}건. p95는 nearest-rank입니다. "
        "표본이 작아 신뢰 구간이 넓고, 용량 시험이 아닙니다. degraded(안전망 경로)는 성공으로 세지 않습니다.",
        "", "| 단계 | ok | degraded | failed |", "|---|---|---|---|",
    ]
    for step, c in sorted(ag["by_step"].items()):
        lines.append(f"| {STEP_NAMES.get(step, step)} | {c['ok']} | {c['degraded']} | {c['failed']} |")
    return lines


def _cost(s: dict[str, Any]) -> list[str]:
    cost, fixed = s["ai_cost"], s["fixed"]
    lines = ["", "## 2. 비용", "", "| 모델 | 호출 | 입력 토큰 | 출력 토큰 | 비용 |", "|---|---|---|---|---|"]
    for model, m in sorted(cost["by_model"].items()):
        lines.append(f"| {model} | {m['calls']} | {m['input_tokens']:,} | {m['output_tokens']:,} | {_usd(m['cost_usd'])} |")
    lines += [
        "",
        f"AI 비용 합계 {_usd(cost['total_usd'])}, 신청당 {_usd(cost['per_application_usd'])} "
        f"(캐시 할인 미반영 상한, 단가 미확인 호출 {cost['unpriced_calls']}건).",
        f"고정비(월): {_usd(fixed['monthly_usd'])} — {fixed['source']}.",
        "", "| 월 물량 | AI/건 | 고정비/건 | 합계/건 | 수작업 대비 | 대출 1건(USD 6,000) 대비 |",
        "|---|---|---|---|---|---|",
    ]
    for r in s["unit_economics"]:
        lines.append(f"| {r['volume']:,} | {_usd(r['ai_usd'])} | {_usd(r['fixed_usd'])} | {_usd(r['total_usd'])} | "
                     f"{_pct(r['vs_manual'])} | {_pct(r['vs_ticket'])} |")
    lines.append("")
    lines.append("수작업 처리 원가 $3,500/건 가정: Mills & McCarthy, HBS Working Paper 15-004 "
                 "(소상공인 대출 1건 처리 원가 $3,000~4,000의 중간값).")
    return lines


def _fairness(s: dict[str, Any]) -> list[str]:
    f = s["fairness"]
    lines = ["", "## 3. 공정성(추천 approve율)", "",
             f"임계값 {f['threshold_pp']:g}pp, 값별 평가 표본 < {f['min_samples']}이면 격차 계산에서 제외합니다."]
    for axis, a in f["axes"].items():
        lines += ["", f"### {AXIS_NAMES.get(axis, axis)}", "",
                  "| 값 | 신청 | 평가 | approve율 | eligible율 | 비고 |", "|---|---|---|---|---|---|"]
        for value, v in a["values"].items():
            lines.append(f"| {value} | {v['n']} | {v['assessed']} | {_pct(v['approve_rate'])} | "
                         f"{_pct(v['eligible_rate'])} | {'표본 부족' if v['insufficient'] else ''} |")
        note = "규칙 R104(업력)로 설명되는 차이 — 경보 대상 아님" if a["policy_explained"] else (
            "**경보: 임계값 초과**" if a["alert"] else "임계값 이내")
        lines += ["", f"격차 {a['gap_pp']:g}pp(eligible {a['eligible_gap_pp']:g}pp), "
                      f"원형 통제 최대 {a['controlled']['max_pp']:g}pp. {note}."]
    return lines


def _faults(faults: list[dict[str, Any]]) -> list[str]:
    if not faults:
        return []
    lines = ["", "## 4. 장애 시나리오"]
    for f in faults:
        reverted = {True: "확인", False: "실패"}.get(f["reverted"], "기록 없음")
        lines += ["", f"### {f['scenario'] or f['run_id']} (run {f['run_id']}, 원복 {reverted})", "",
                  "| 신청 | 상태 | 플래그 | 단계 |", "|---|---|---|---|"]
        for a in f["applications"]:
            steps = ", ".join(f"{s['step']}:{s['outcome']}" for s in a["steps"])
            lines.append(f"| {a['application_id']} | {a['status']} | {', '.join(a['flags']) or '—'} | {steps} |")
        if f["dependencies_error"]:
            lines += ["", f"의존성 조회 실패: {f['dependencies_error']}"]
        elif f["dependencies"] is not None:
            lines += ["", "| 신청 | 대상 | 역할 | 호출 | 실패 |", "|---|---|---|---|---|"]
            for app, rows in f["dependencies"].items():
                for r in rows or [{"target": "—", "role": "—", "total": 0, "failed": 0}]:
                    lines.append(f"| {app} | {r['target']} | {r['role']} | {r['total']} | {r['failed']} |")
    return lines


def render(data: dict[str, Any]) -> str:
    s = data["summary"]
    lines = [f"# 운영 지표 보고서 — {data['run_id']}", "",
             f"> {s['basis']}. 신청 {s['applications']}건, 생성 {data['generated_at']}.", ""]
    load = data.get("load") or {}
    if load.get("unreached"):
        ids = ", ".join(f"{u['application_id']}({u['status']})" for u in load["unreached"])
        lines += [f"미도달 {len(load['unreached'])}건: {ids}", ""]
    if load.get("aborted"):
        lines += [f"예산 가드로 제출 중단: 예상 {_usd((load.get('guard') or {}).get('projected_usd'))}", ""]
    lines += _slo(s) + _cost(s) + _fairness(s) + _faults(data.get("faults") or [])
    return "\n".join(lines) + "\n"
```

- [ ] **Step 10: 구현 — faults**

`src/ops/faults.py`

```python
"""장애 주입: FAULT_MODE 환경 변수로 리비전을 갱신하고, 시나리오는 제출 뒤 finally로 원복한다(Spec §6)."""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ops import opsrun
from ops.azcli import OpsError, Runner, subscription_runner
from ops.azdenv import get_env

FAULT_VAR = "FAULT_MODE"
TARGETS = {"source": ("MOCKAPI_APP_NAME", "flaky"), "policy": ("POLICY_APP_NAME", "unavailable")}


@dataclass(frozen=True)
class Scenario:
    name: str
    target: str
    applicants: tuple[str, ...]
    interval_s: float


SCENARIOS = {
    "source-flaky": Scenario("source-flaky", "source", ("demo-cafe-001", "demo-mismatch-002"), 5),
    # 첫 신청이 Durable 재시도(5초 간격 3회)로 서킷을 연 뒤 다음 신청이 Policy 단계에 닿도록 간격을 둔다.
    "policy-down": Scenario("policy-down", "policy", ("demo-cafe-001", "demo-stress-003", "demo-thin-004"), 90),
}


def _props(az: Runner, group: str, app: str) -> dict[str, Any]:
    return json.loads(az(["az", "containerapp", "show", "-g", group, "-n", app, "--query", "properties", "-o", "json"]))


def _mode(props: dict[str, Any]) -> str | None:
    env = props["template"]["containers"][0].get("env") or []
    return next((e.get("value") for e in env if e.get("name") == FAULT_VAR), None)


def _set(az: Runner, group: str, app: str, mode: str | None, *, sleep: Callable[[float], None],
         poll_wait: float, poll_limit: int) -> None:
    if _mode(_props(az, group, app)) == mode:
        return
    change = ["--set-env-vars", f"{FAULT_VAR}={mode}"] if mode else ["--remove-env-vars", FAULT_VAR]
    az(["az", "containerapp", "update", "-g", group, "-n", app, *change, "-o", "none"])
    # Probe P5: 새 리비전 Healthy·트래픽 전환까지 약 40초.
    for _ in range(poll_limit):
        props = _props(az, group, app)
        if props.get("latestRevisionName") == props.get("latestReadyRevisionName") and _mode(props) == mode:
            return
        sleep(poll_wait)
    raise OpsError(f"{app}: 새 리비전이 준비되지 않았습니다({FAULT_VAR}={mode or '없음'})")


def _context(runner: Runner) -> tuple[Runner, str, dict[str, str]]:
    env = get_env(runner)
    return subscription_runner(runner, env), env["AZURE_RESOURCE_GROUP"], env


def set_fault(runner: Runner, target: str, on: bool, *, sleep: Callable[[float], None] = time.sleep,
              poll_wait: float = 10, poll_limit: int = 30) -> None:
    az, group, env = _context(runner)
    key, mode = TARGETS[target]
    _set(az, group, env[key], mode if on else None, sleep=sleep, poll_wait=poll_wait, poll_limit=poll_limit)


def status(runner: Runner) -> dict[str, str]:
    az, group, env = _context(runner)
    return {target: _mode(_props(az, group, env[key])) or "off" for target, (key, _) in TARGETS.items()}


def run_scenario(runner: Runner, name: str, *, submit_fn: Callable[..., Any] = opsrun.submit,
                 sleep: Callable[[float], None] = time.sleep, poll_wait: float = 10, poll_limit: int = 30,
                 **job_kw: Any) -> Any:
    scenario = SCENARIOS[name]
    az, group, env = _context(runner)
    key, mode = TARGETS[scenario.target]
    app = env[key]
    toggle = {"sleep": sleep, "poll_wait": poll_wait, "poll_limit": poll_limit}
    _set(az, group, app, mode, **toggle)
    failure: BaseException | None = None
    try:
        result = submit_fn(runner, scenario.applicants, interval_s=scenario.interval_s, sleep=sleep, **job_kw)
    except BaseException as err:
        failure = err
        raise
    finally:
        try:
            _set(az, group, app, None, **toggle)
        except OpsError as err:
            raise OpsError(f"장애 원복 실패: {app}({FAULT_VAR}={mode}). "
                           f"수동 원복: python -m ops fault {scenario.target} off\n{err}") from (failure or err)
    opsrun.update_meta(result.out_dir, {"fault": {"scenario": name, "app": app, "mode": mode, "reverted": True}})
    return result
```

검산(`test_revert_failure_is_reported`): show 결과 `[off, flaky, flaky…]`, update 결과 `["", 오류]` → 켜기 성공 → 제출 실패 → 원복 show `flaky` → update 오류 → `OpsError("장애 원복 실패…") from 제출 오류`.

- [ ] **Step 11: 구현 — CLI** `src/ops/cli.py`

import: `from ops import faults, opsrun`.

`build_parser`의 `deploy` 앞에:

```python
    loader = commands.add_parser("load", help="job-ops로 균형 격자 신청을 제출하고 결과를 ops/reports/<run>/에 저장")
    loader.add_argument("--count", type=int, default=opsrun.MAX_COUNT, help="제출 건수(1~24)")
    loader.add_argument("--interval", type=float, default=15.0, help="제출 간격 초")
    meter = commands.add_parser("metrics", help="SLO·비용·공정성 보고서(ops/reports/<run>/report.md)")
    meter.add_argument("--run", required=True, help="ops load가 출력한 run ID")
    meter.add_argument("--fault-runs", nargs="*", default=[], help="ops fault run이 출력한 run ID")
    meter.add_argument("--fixed-monthly-usd", type=float, help="고정비(월, USD) 수동 입력")
    meter.add_argument("--fx-krw-per-usd", type=float, help="비용 통화가 KRW일 때 환율")
    meter.add_argument("--recollect", action="store_true", help="job-ops로 신청 상태를 다시 수집")
    fault = commands.add_parser("fault", help="장애 주입(리비전 갱신)")
    fault_actions = fault.add_subparsers(dest="action", required=True)
    fault_actions.add_parser("source", help="Mock 데이터 원천 일시 오류").add_argument("mode", choices=("flaky", "off"))
    fault_actions.add_parser("policy", help="Policy 서비스 503").add_argument("mode", choices=("on", "off"))
    fault_actions.add_parser("status", help="현재 장애 모드")
    fault_actions.add_parser("run", help="토글 → 제출 → 대기 → 원복").add_argument(
        "scenario", choices=tuple(faults.SCENARIOS))
```

`main` 분기(`eval` 뒤):

```python
        elif args.command == "load":
            result = opsrun.load(runner, count=args.count, interval_s=args.interval,
                                 progress=lambda m: print(m, file=out))
            s = result.summary
            print(f"제출 {s['submitted']}건, 도달 {s['reached']}건, 미도달 {len(s['unreached'])}건 "
                  f"(run {result.run_id})", file=out)
            if s.get("aborted"):
                print(f"예산 가드로 제출 중단: 예상 ${s['guard']['projected_usd']:.2f}", file=out)
            print(f"다음: python -m ops metrics --run {result.run_id}", file=out)
        elif args.command == "metrics":
            path = opsrun.metrics(runner, args.run, fault_runs=args.fault_runs,
                                  fixed_monthly_usd=args.fixed_monthly_usd, fx_krw_per_usd=args.fx_krw_per_usd,
                                  recollect=args.recollect)
            print(f"보고서: {path / 'report.md'}", file=out)
        elif args.command == "fault" and args.action == "status":
            for target, mode in faults.status(runner).items():
                print(f"{target}: {mode}", file=out)
        elif args.command == "fault" and args.action == "run":
            result = faults.run_scenario(runner, args.scenario, progress=lambda m: print(m, file=out))
            print(f"{args.scenario} 완료, 원복 확인: run {result.run_id} "
                  f"(도달 {result.summary['reached']}/{result.summary['submitted']})", file=out)
        elif args.command == "fault":
            faults.set_fault(runner, args.action, args.mode in ("flaky", "on"))
            print(f"{args.action} 장애 모드: {args.mode}", file=out)
```

`run_scenario`의 `**job_kw`로 `progress`가 `submit` → `run_job`까지 전달된다.

- [ ] **Step 12: 통과 확인** — `pytest tests/ops -q -p no:cacheprovider` → PASS(기존 `test_eval_runner.py` 포함). 전체: `pytest -q -p no:cacheprovider` → 실패 0.

- [ ] **Step 13: 커밋** — `feat(ops): load, fault and metrics commands with shared job runner` (트레일러 포함)

---

## Task 13: console `/ops` 공정성 화면

**Files:**
- Create: `src/console/ops.py`, `src/console/templates/ops.html`, `tests/console/test_ops_page.py`
- Modify: `src/console/app.py`(`ConsoleDeps.ops`, 필터 `pct`·`usd`·`num`, `GET /ops`), `src/console/adapters.py`(`CosmosOps`, `build_deps`), `src/console/templates/base.html`(메뉴), `src/console/static/console.css`(경보 배너), `src/console/Dockerfile`(opsmetrics·prices.json 복사), `tests/console/test_adapters.py`, `tests/console/test_dockerfile.py`

console은 Cosmos `applications`를 이미 읽을 수 있다(Built-in Data Reader, 데이터베이스 범위). 새 권한·인프라 변경은 없다. 고정비는 Cost Management 권한이 필요해 console에서 계산하지 않는다(`fixed_monthly_usd=None` → 건당 총비용 "-", 보고서 링크 안내).

- [ ] **Step 1: 실패 테스트** — `tests/console/test_ops_page.py`

```python
import base64
import json
import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from console.app import ConsoleDeps, create_app
from console.auth import AUDITOR
from console.ops import default_run, sorted_runs

OID = "http://schemas.microsoft.com/identity/claims/objectidentifier"
T0 = datetime(2026, 11, 2, 1, 0, tzinfo=UTC)


def principal(*roles):
    claims = [{"typ": OID, "val": "oid-a"}, {"typ": "name", "val": "감사"}] + [{"typ": "roles", "val": r} for r in roles]
    body = {"auth_typ": "aad", "name_typ": "name", "role_typ": "roles", "claims": claims}
    return {"X-MS-CLIENT-PRINCIPAL": base64.b64encode(json.dumps(body).encode()).decode()}


AUDITOR_H = principal(AUDITOR)


def projected(i, industry, rec, *, age="3년+", arch="healthy"):
    sub = T0 + timedelta(minutes=i)
    return {
        "application_id": f"r-{i:02d}", "applicant_id": f"load-{arch}-{i:02d}", "status": "awaiting_officer",
        "submitted_at": sub.isoformat(), "received_at": sub.isoformat(), "source": "load", "run_id": "load-1",
        "segment": {"industry": industry, "region": "서울", "business_age_band": age},
        "steps": [{"step": "underwrite", "outcome": "ok", "agent": True,
                   "usage": [{"model": "gpt-5.4-mini", "input_tokens": 1000, "output_tokens": 100, "calls": 1}]}],
        "timeline": [{"status": "awaiting_officer", "at": (sub + timedelta(minutes=3)).isoformat()}],
        "flags": [], "policy": {"decision": "eligible", "reason_codes": []},
        "assessment": {"recommendation": rec}, "officer_decision": None,
    }


class FakeOps:
    def __init__(self, runs):
        self.data = runs
        self.asked = []

    def runs(self):
        return list(self.data)

    def by_run(self, run_id):
        self.asked.append(run_id)
        return self.data[run_id]


def skewed():
    # 카페 5건 전부 approve, 한식 5건 전부 decline → 업종 격차 100%p(경보). 업력 격차도 100%p지만 R104 설명 축.
    return ([projected(i, "카페", "approve", age="3년+") for i in range(5)]
            + [projected(i + 5, "한식", "decline", age="<1년") for i in range(5)])


def client(ops, key=b"k" * 32):
    deps = ConsoleDeps(store=None, raw=None, queue=None, pseudonym_key=key, ops=ops)
    return TestClient(create_app(deps), follow_redirects=False)


def test_sorted_runs_newest_first_and_default_is_latest_load():
    runs = ["load-20261102010000", "fault-20261102030000", "load-20261102020000", "collect-20261102040000"]
    assert sorted_runs(runs)[0] == "collect-20261102040000"
    assert default_run(runs) == "load-20261102020000"
    assert default_run(["fault-20261102030000"]) == "fault-20261102030000"
    assert default_run([]) is None


def test_ops_page_shows_fairness_table_and_alert_banner():
    ops = FakeOps({"load-20261102010000": skewed()})
    page = client(ops).get("/ops", headers=AUDITOR_H)
    assert page.status_code == 200
    html = page.text
    assert "합성 데이터 기준" in html and "load-20261102010000" in html
    assert 'class="ops-alert"' in html and "업종" in html and "100.0%p" in html
    assert "규칙 R104로 설명" in html  # 업력 축은 경보 대신 설명 표시
    assert "카페" in html and "한식" in html and "100.0%" in html and "0.0%" in html
    assert ops.asked == ["load-20261102010000"]


def test_ops_page_without_alert_has_no_banner():
    docs = [projected(i, "카페" if i % 2 else "한식", "approve") for i in range(10)]
    html = client(FakeOps({"load-20261102010000": docs})).get("/ops", headers=AUDITOR_H).text
    assert 'class="ops-alert"' not in html and "0.0%p" in html


def test_small_groups_marked_insufficient():
    docs = [projected(i, "카페", "approve") for i in range(5)] + [projected(9, "한식", "decline")]
    html = client(FakeOps({"load-20261102010000": docs})).get("/ops", headers=AUDITOR_H).text
    assert re.search(r"<td>한식</td>.*?<td>표본 부족</td>", html, re.S)
    assert not re.search(r"<td>카페</td>(?:(?!</tr>).)*표본 부족", html, re.S)
    assert 'class="ops-alert"' not in html


def test_ops_page_selects_run_and_rejects_unknown():
    ops = FakeOps({"load-20261102010000": skewed(), "fault-20261102030000": skewed()[:2]})
    c = client(ops)
    assert c.get("/ops?run=fault-20261102030000", headers=AUDITOR_H).status_code == 200
    assert ops.asked == ["fault-20261102030000"]
    assert c.get("/ops?run=nope", headers=AUDITOR_H).status_code == 404


def test_ops_page_without_runs_explains_how_to_load():
    html = client(FakeOps({})).get("/ops", headers=AUDITOR_H).text
    assert "측정 실행이 없습니다" in html and "python -m ops load" in html


def test_ops_page_requires_role_and_configuration():
    assert client(FakeOps({})).get("/ops").status_code == 401
    assert client(None).get("/ops", headers=AUDITOR_H).status_code == 404


def test_nav_links_to_ops():
    html = client(FakeOps({})).get("/ops", headers=AUDITOR_H).text
    assert '<a href="/ops">운영 지표</a>' in html
```

`tests/console/test_adapters.py`에 추가:

```python
def test_cosmos_ops_lists_runs_and_projects_docs():
    from console.adapters import CosmosOps

    class Container:
        def __init__(self):
            self.queries = []

        def query_items(self, query, parameters=None, enable_cross_partition_query=False):
            self.queries.append((query, parameters, enable_cross_partition_query))
            if "DISTINCT" in query:
                return iter(["load-20261102010000", "fault-20261102030000"])
            return iter([{"id": "a", "application_id": "a", "run_id": "load-20261102010000",
                          "assembly": {"secret": "x"}, "policy": {"decision": "eligible", "max_amount_krw": 1}}])

    c = Container()
    ops = CosmosOps(c)
    assert ops.runs() == ["load-20261102010000", "fault-20261102030000"]
    docs = ops.by_run("load-20261102010000")
    assert docs[0]["application_id"] == "a" and "assembly" not in docs[0]
    assert docs[0]["policy"] == {"decision": "eligible", "reason_codes": []}
    assert c.queries[1][1] == [{"name": "@run", "value": "load-20261102010000"}]
    assert all(q[2] for q in c.queries)
```

`test_build_deps_wires_env`: monkeypatch 대상 이름 목록에 `"CosmosOps"`를 추가하고 끝에 `assert made["CosmosOps"][0] == (("applications", "https://cos", "lending"),)`를 추가한다.

`tests/console/test_dockerfile.py::test_dockerfile_copies_everything_the_console_imports`: import 문을 `"import console.app, console.adapters, console.views, console.ops; "`로 바꾸고, 끝에 다음을 추가한다.

```python
    assert (app / "evals" / "prices.json").exists()
    assert (app / "opsmetrics" / "summary.py").exists()
    assert not (app / "evals" / "runner.py").exists()
```

- [ ] **Step 2: 실패 확인** — `pytest tests/console -q` → `ModuleNotFoundError: console.ops`, `TypeError: ConsoleDeps.__init__() got an unexpected keyword argument 'ops'`, `ImportError: CosmosOps` 등으로 FAIL

- [ ] **Step 3: 구현**

`src/console/ops.py`

```python
"""운영 지표 화면(/ops): 측정 실행(run_id)별 신청 투영 → opsmetrics 요약. 고정비는 계산하지 않는다."""

from collections.abc import Iterable
from typing import Any, Protocol

from opsmetrics.prices import load_prices
from opsmetrics.summary import summarize

AXIS_LABELS = {"industry": "업종", "region": "지역", "business_age_band": "업력"}
STEP_LABELS = {"consent": "동의", "assemble": "자료 조립", "classify": "거래 분류", "underwrite": "심사 분석",
               "policy": "정책 판정", "assess": "신용 평가"}


class OpsStore(Protocol):
    def runs(self) -> list[str]: ...
    def by_run(self, run_id: str) -> list[dict[str, Any]]: ...


def _stamp(run_id: str) -> str:
    return run_id.rsplit("-", 1)[-1]


def sorted_runs(runs: Iterable[str]) -> list[str]:
    return sorted(set(runs), key=lambda r: (_stamp(r), r), reverse=True)


def default_run(runs: Iterable[str]) -> str | None:
    ordered = sorted_runs(runs)
    return next((r for r in ordered if r.startswith("load-")), ordered[0] if ordered else None)


def ops_view(docs: list[dict[str, Any]]) -> dict[str, Any]:
    return summarize(docs, load_prices())
```

`STEP_LABELS`의 키는 Task 2 `metering.STEPS` 값과 `classify` 하위 단계다(`by_step`에는 에이전트 단계 `assemble`·`classify`·`assess`만 나온다). 모르는 키는 원래 이름으로 표시한다(템플릿 `STEP_LABELS.get(name, name)`).

`src/console/app.py`
- import: `from console.ops import AXIS_LABELS, STEP_LABELS, OpsStore, default_run, ops_view, sorted_runs`
- `ConsoleDeps` 끝에 `ops: OpsStore | None = None`
- `_templates()`에 필터 추가:

```python
    env.filters["pct"] = lambda v: f"{v * 100:.1f}%" if isinstance(v, int | float) else "-"
    env.filters["usd"] = lambda v: f"${v:,.4f}" if isinstance(v, int | float) else "-"
    env.filters["num"] = lambda v, d=1: f"{v:.{d}f}" if isinstance(v, int | float) else "-"
```

- `create_app` 안, `/grievances` 라우트 뒤:

```python
    def ops_store() -> OpsStore:
        if deps.ops is None:
            raise HTTPException(404, "ops metrics not configured")
        return deps.ops

    @app.get("/ops", response_class=HTMLResponse)
    def ops_page(request: Request, run: str | None = None) -> HTMLResponse:
        user = viewer(request)
        store = ops_store()
        runs = sorted_runs(store.runs())
        if run is not None and run not in runs:
            raise HTTPException(404, "run not found")
        selected = run or default_run(runs)
        summary = ops_view(store.by_run(selected)) if selected else None
        return render("ops.html", user, runs=runs, selected=selected, s=summary,
                      AXIS_LABELS=AXIS_LABELS, STEP_LABELS=STEP_LABELS)
```

`viewer(request)`가 먼저 인증(401·403)을 처리하고, 그 다음 `ops_store()`가 미구성 404를 낸다(기존 `/loans` 순서와 같다).

`src/console/templates/ops.html`

```html
{% extends "base.html" %}
{% block title %}운영 지표 · AMA Lending 심사 콘솔{% endblock %}
{% block main %}
<h1>운영 지표 <small>합성 데이터 기준</small></h1>
{% if not selected %}
<p>측정 실행이 없습니다. 운영자 PC에서 <code>python -m ops load --count 24</code>로 합성 신청을 제출하면 여기에 표시됩니다.</p>
{% else %}
<form method="get" action="/ops">
  <label>측정 실행
    <select name="run" onchange="this.form.submit()">
    {% for r in runs %}<option value="{{ r }}"{% if r == selected %} selected{% endif %}>{{ r }}</option>{% endfor %}
    </select>
  </label>
  <noscript><button type="submit">보기</button></noscript>
</form>
<p>실행 <code>{{ selected }}</code> · 신청 {{ s.applications }}건 · {{ s.basis }}. 고정비·건당 총비용은 <code>ops/reports/{{ selected }}/report.md</code>를 봅니다.</p>

{% for axis in s.fairness.alerts %}
<p class="ops-alert">공정성 경보: {{ AXIS_LABELS.get(axis, axis) }} 추천 approve율 격차 {{ s.fairness.axes[axis].gap_pp|num }}%p &gt; 임계값 {{ s.fairness.threshold_pp|num }}%p. 원형 통제 최대 격차 {{ s.fairness.axes[axis].controlled.max_pp|num }}%p를 함께 확인하세요.</p>
{% endfor %}

<h2>SLO</h2>
<table class="kv">
  <tr><th>접수 → 심사역 대기 p50 / p95 / 최대(분)</th><td>{{ s.latency.p50_min|num }} / {{ s.latency.p95_min|num }} / {{ s.latency.max_min|num }} (목표 p95 ≤ {{ s.latency.target_min|num(0) }}분, {{ "충족" if s.latency.met else "미충족" }})</td></tr>
  <tr><th>측정 표본</th><td>{{ s.latency.n }}건{% if s.latency.insufficient %} · 표본 부족(20건 미만){% endif %}{% if s.latency.unreached %} · 미도달 {{ s.latency.unreached }}건{% endif %}{% if s.latency.received_only %} · 접수 시각 기준 {{ s.latency.received_only }}건{% endif %}</td></tr>
  <tr><th>에이전트 단계 성공률</th><td>{{ s.agent_success.rate|pct }} (목표 {{ s.agent_success.target|pct }}, ok {{ s.agent_success.ok }} · degraded {{ s.agent_success.degraded }} · failed {{ s.agent_success.failed }})</td></tr>
  <tr><th>AI 비용(건당, 캐시 할인 미반영)</th><td>{{ s.ai_cost.per_application_usd|usd }}{% if s.ai_cost.unpriced_calls %} · 단가 미확인 호출 {{ s.ai_cost.unpriced_calls }}회 제외{% endif %}</td></tr>
</table>
{% if s.agent_success.by_step %}
<table>
  <thead><tr><th>단계</th><th>ok</th><th>degraded</th><th>failed</th></tr></thead>
  <tbody>
  {% for name, c in s.agent_success.by_step|dictsort %}
  <tr><td>{{ STEP_LABELS.get(name, name) }}</td><td>{{ c.ok }}</td><td>{{ c.degraded }}</td><td>{{ c.failed }}</td></tr>
  {% endfor %}
  </tbody>
</table>
{% endif %}

<h2>공정성(세그먼트별)</h2>
<p>격차는 표본 {{ s.fairness.min_samples }}건 이상 세그먼트의 추천 approve율 최대 − 최소입니다. 임계값 {{ s.fairness.threshold_pp|num }}%p.</p>
{% for axis, a in s.fairness.axes.items() %}
<h3>{{ AXIS_LABELS.get(axis, axis) }} — 격차 {{ a.gap_pp|num }}%p · eligible 격차 {{ a.eligible_gap_pp|num }}%p · 원형 통제 최대 {{ a.controlled.max_pp|num }}%p
{% if a.policy_explained %}<small>(규칙 R104로 설명되는 축, 경보 대상 아님)</small>{% endif %}</h3>
<table>
  <thead><tr><th>값</th><th>신청</th><th>추천 완료</th><th>추천 approve율</th><th>정책 eligible율</th><th>approve / conditional / decline</th><th>심사역 승인율</th><th></th></tr></thead>
  <tbody>
  {% for value, r in a["values"].items() %}
  <tr>
    <td>{{ value }}</td><td>{{ r.n }}</td><td>{{ r.assessed }}</td><td>{{ r.approve_rate|pct }}</td><td>{{ r.eligible_rate|pct }}</td>
    <td>{{ r.recommendations.approve }} / {{ r.recommendations.conditional }} / {{ r.recommendations.decline }}</td>
    <td>{{ r.officer_approve_rate|pct }}</td><td>{% if r.insufficient %}표본 부족{% endif %}</td>
  </tr>
  {% endfor %}
  </tbody>
</table>
{% endfor %}
{% endif %}
{% endblock %}
```

주의: Jinja에서 `a.values`는 dict 메서드 `values`로 해석되므로 반드시 `a["values"]`로 쓴다.

`src/console/templates/base.html` 메뉴 줄 끝(`차주 시뮬레이터` 뒤)에 ` · <a href="/ops">운영 지표</a>`를 추가한다.

`src/console/static/console.css` 끝에:

```css
.ops-alert { margin: .5rem 0; padding: .5rem 1rem; background: #fde7e9; color: #a4262c; border: 1px solid #f1707b; }
```

`src/console/adapters.py`
- 모듈 docstring의 Cosmos 목록 설명은 그대로(applications 읽기 전용).
- import: `from opsmetrics.projection import project`
- 상수·클래스(`CosmosPrivacy` 뒤):

```python
RUNS_QUERY = "SELECT DISTINCT VALUE c.run_id FROM c WHERE IS_STRING(c.run_id)"
RUN_DOCS_QUERY = "SELECT * FROM c WHERE c.run_id = @run"


class CosmosOps:
    """측정 실행별 신청 투영(applications 읽기 전용). 원문 필드는 project()가 버린다."""

    def __init__(self, applications: Any) -> None:
        self._applications = applications

    def runs(self) -> list[str]:
        return list(self._applications.query_items(query=RUNS_QUERY, enable_cross_partition_query=True))

    def by_run(self, run_id: str) -> list[dict[str, Any]]:
        items = self._applications.query_items(
            query=RUN_DOCS_QUERY, parameters=[{"name": "@run", "value": run_id}], enable_cross_partition_query=True
        )
        return [project(d) for d in items]
```

- `build_deps`의 `ConsoleDeps(...)`에 `ops=CosmosOps(db.get_container_client(APPLICATIONS)),` 추가.

`src/console/Dockerfile`: `COPY src/agents /app/agents` 줄 뒤에 다음 두 줄을 추가한다(평가 하네스 나머지는 넣지 않는다).

```dockerfile
COPY src/opsmetrics /app/opsmetrics
COPY src/evals/prices.json /app/evals/
```

`opsmetrics.prices.DEFAULT_PATH`는 `/app/opsmetrics/../evals/prices.json` = `/app/evals/prices.json`이다. `/app/evals`에는 `__init__.py`가 없어도 된다(파일로만 읽음). 대상은 디렉터리(`/app/evals/`)로 쓴다: Dockerfile 테스트의 `_copy`는 단일 파일을 `dest / f.name`으로 복사하므로 파일 경로 대상이면 `prices.json/prices.json`이 된다. `.dockerignore`는 `src/opsmetrics`·`src/evals`를 제외하지 않으므로 바꾸지 않는다.

- [ ] **Step 4: 통과 확인** — `pytest tests/console -q` → PASS. 전체 `pytest -q`도 기준선 + 신규 테스트로 PASS(실패 0).

- [ ] **Step 5: 커밋** — `feat(console): /ops fairness and SLO page` (트레일러 포함)

---

## Task 14: 실제 Azure 측정 실행과 README

**Files:**
- Create: `ops/reports/<load run>/…`, `ops/reports/<fault run ×2>/…`(실행 결과, 커밋)
- Modify: `README.md`(P6b 절), 필요 시 Spec·계획의 체크박스와 실측 메모

이 Task는 실제 Azure 리소스를 바꾼다. 각 단계의 실측(소요 시간, run_id, 오류)을 이 체크박스 아래에 메모하고 README 실행 기록 표에 옮긴다. 오류가 나면 원인을 고치는 코드 변경은 해당 Task의 테스트를 먼저 추가하고(TDD) 별도 커밋으로 남긴다.

- [ ] **Step 1: 사전 확인**

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python -m pytest -q -p no:cacheprovider          # 실패 0
$env:RUN_INFRA_TESTS='1'; .\.venv\Scripts\python -m pytest -q -p no:cacheprovider tests/infra; Remove-Item Env:RUN_INFRA_TESTS
az bicep build --file infra/main.bicep --stdout > $null              # 경고 0 목표(남으면 사유 메모)
```

- [ ] **Step 2: probe 잔여물 정리** — 수동 생성한 Metrics Publisher 역할 할당을 지운다(bicep의 같은 역할과 RoleAssignmentExists 충돌 방지).

```powershell
$appi = (azd env get-value APPLICATIONINSIGHTS_RESOURCE_ID 2>$null)  # 첫 provision 전이면 비어 있음
if (-not $appi) { $appi = az resource list -g rg-amalend --resource-type Microsoft.Insights/components --query "[0].id" -o tsv }
az role assignment list --scope $appi --assignee 6af24207-91fb-4394-a08c-0ad350542b00 --query "[].id" -o tsv |
  ForEach-Object { az role assignment delete --ids $_ }
```

리소스 그룹 이름은 `azd env get-value AZURE_RESOURCE_GROUP`로 확인한다. Foundry 연결 `appi-amalend`는 같은 이름 PUT으로 덮어써지므로 지우지 않는다.

- [ ] **Step 3: 배포 창 + SCM 우회**

```powershell
python -m ops window open --acr
# SCM 403 회피: 회사 이그레스 IP 중 window가 넣지 않은 다른 하나를 Functions에 임시 허용
$fn = azd env get-value FUNCTION_APP_NAME; $rg = azd env get-value AZURE_RESOURCE_GROUP
az functionapp config access-restriction add -g $rg -n $fn --rule-name operator-alt --priority 110 --ip-address <다른 IP>/32 --action Allow
az functionapp config access-restriction add -g $rg -n $fn --rule-name operator-alt --priority 110 --ip-address <다른 IP>/32 --action Allow --scm-site true
```

`<다른 IP>`는 `<회사망 IP>`·`<회사망 IP>` 중 `ops window status`에 표시되지 않은 쪽이다.

- [ ] **Step 4: 배포와 smoke**

```powershell
python -m ops deploy all     # 이미지 4종(policy·mockapi·console·eval) ACR 빌드 → provision → Function 코드
python -m ops smoke
```

확인: `azd env get-values`에 `OPS_JOB_NAME`·`APPLICATIONINSIGHTS_RESOURCE_ID`가 생겼는지, console `/ops`가 "측정 실행이 없습니다"를 보이는지, Policy·Functions App Insights에 `cloud_RoleName`(`policy`·`orchestrator`, T11 `OTEL_SERVICE_NAME`)로 요청이 들어오는지.

```powershell
$ws = azd env get-value LOG_ANALYTICS_CUSTOMER_ID
az monitor log-analytics query -w $ws --analytics-query "AppRequests | where TimeGenerated > ago(30m) | summarize count() by AppRoleName" -o table
```

- [ ] **Step 5: 측정 배치**

```powershell
python -m ops load --count 24
```

기대: `ops/reports/load-<시각>/`에 `summary.json`(submitted 24, reached·unreached, `aborted` False), `docs.json`. 실패하면 출력된 Log Analytics 쿼리로 `[opsjob]` 줄을 보고, 예산 가드로 중단되었으면 `summary.json`의 첫 3건 비용을 메모한다.

- [ ] **Step 6: 장애 시나리오 2개**(배치 다음에 실행. mockapi 재시작이 동의 레지스트리를 초기화하므로)

```powershell
python -m ops fault run source-flaky
python -m ops fault run policy-down
python -m ops fault status        # 둘 다 정상(FAULT_MODE 없음)인지
```

기대: source-flaky 신청은 `assembly.sources[*].attempts` ≥ 2로 완료(재시도 회복), policy-down 신청은 서킷 open 뒤 `flags`에 fail-closed 표시와 심사역 수동 심사 상태. 각 run_id를 메모한다.

- [ ] **Step 7: 보고서**

```powershell
python -m ops metrics --run load-<시각> --fault-runs fault-<시각1> fault-<시각2>
```

Cost Management가 실패하면(`측정 불가(...)`) 몇 분 뒤 다시 실행하고, 계속 실패하면 포털 Cost analysis 값을 `--fixed-monthly-usd`로 넣고 사유를 메모한다. KRW 청구면 `--fx-krw-per-usd`(당일 환율, 출처 메모)를 준다.

`ops/reports/load-<시각>/report.md`를 열어 확인: 머리말 "합성 데이터 기준", SLO p95·표본 수, 단계 성공률, 건당 AI 비용·물량별 총비용·수작업 대비, 공정성 표·경보, 장애 절(KQL 의존성 호출 수·서킷 이벤트·결과 상태).

- [ ] **Step 8: 포털 확인**(스크린샷은 P6c에서 데모 대본과 함께)
  - Workbook "대출 여정 운영 지표(합성 데이터 기준)": SLO·단계 성공률·비용 추이 타일에 데이터가 보이는지
  - 경보 규칙 `alert-agent-step-failure`·`alert-policy-circuit-open`이 `Enabled`이고 평가 오류가 없는지. `policy-down` 시나리오 뒤 서킷 경보가 발화했는지(메일 수신은 `opsAlertEmail`을 설정한 경우만)
  - Foundry 포털 프로젝트 → Tracing에 에이전트 실행 트레이스가 보이는지
  - console `/ops`에 load run이 기본 선택되고 표가 보고서 공정성 수치와 같은지

- [ ] **Step 9: 정리**

```powershell
az functionapp config access-restriction remove -g $rg -n $fn --rule-name operator-alt
az functionapp config access-restriction remove -g $rg -n $fn --rule-name operator-alt --scm-site true
python -m ops window close
python -m ops pause
```

- [ ] **Step 10: README P6b 절** — `## 평가 하네스(P6a)` 절 끝(`### 알려진 제약(P6a)` 뒤)에 추가. 실측값은 Step 4~7 메모로 채운다(자리표시 없이).

```markdown
## 운영 지표·복원력(P6b)

배포된 신청 여정에 **합성 신청 24건과 장애 시나리오 2개를 실제 Azure에서 흘려** SLO·건당 비용·공정성 격차·복원력 동작을
측정합니다(스펙 `2026-11-02-p6b-ops-metrics-design.md`). 모든 숫자는 **합성 데이터 기준**입니다.

- **측정 트래픽**: `job-ops`(VNet 안 Container Apps Job, 평가 이미지 재사용, 관리 ID `id-ops`)가 균형 격자 신청자
  `load-<원형>-NN` 24명을 `applications-start` 큐로 제출하고, 심사역 대기 도달까지 기다린 뒤 신청 투영을 로그 조각으로 남깁니다.
  로컬 `ops`는 Job 시작·로그 회수·Cost Management·KQL·보고서만 합니다(Cosmos·큐는 사설 엔드포인트 전용).
- **계측**: 활동이 `_meter`(시각·모델별 토큰·판정)를 결과에 붙이고 오케스트레이터가 신청 문서 `steps[]`로 옮깁니다.
  Functions·Policy는 `azure-monitor-opentelemetry`로 App Insights에 요청·의존성·서킷 이벤트를 보냅니다.
- **지표**: SLO1 접수 → 심사역 대기 p95 ≤ 10분, SLO2 에이전트 단계 성공률 ≥ 99%, 건당 AI 비용(실측 토큰 × `src/evals/prices.json`)
  + 고정비(Cost Management 최근 완료일 × 30, Foundry Models 제외) ÷ 가정 물량, 세그먼트(업종·지역·업력)별 추천 approve율 격차
  (임계값 20%p, 표본 5건 미만 제외, 업력은 규칙 R104로 설명되는 축).
- **화면**: console `/ops`(공정성 표·경보 배너·SLO 요약), Azure Workbook(SLO·단계 성공률·비용 추이), 경보 2개(에이전트 단계 실패, Policy 서킷 open).
  Foundry 프로젝트는 App Insights 연결로 에이전트 트레이스를 남깁니다.
- **장애 주입**: `source-flaky`(mockapi 첫 요청 503 → Durable 재시도 회복), `policy-down`(Policy 503 → 서킷 open → fail-closed로
  심사역 수동 심사). `ops fault`는 `FAULT_MODE` 환경 변수를 켜고 끄며, 실패해도 `finally`에서 원복합니다.

```powershell
python -m ops load --count 24                                   # 측정 배치 → ops/reports/load-<시각>/
python -m ops fault run source-flaky                            # 장애 ① → ops/reports/fault-<시각>/
python -m ops fault run policy-down                             # 장애 ②
python -m ops fault status                                      # 장애 토글 상태
python -m ops metrics --run load-<시각> --fault-runs fault-<…> fault-<…>   # report.md·metrics.json
```

실제 실행 기록(P6b, <날짜>, koreacentral): Step 4~7 메모의 단계별 소요 시간·run_id·비용 표.

주요 실측값(합성 데이터 기준, `ops/reports/<load run>/report.md`): SLO1 p95·표본 수, SLO2 성공률(단계별), 건당 AI 비용,
물량별 총비용·수작업($3,500) 대비, 공정성 축별 격차·경보, 장애 ①·② 결과(재시도 횟수, 서킷 이벤트, 최종 상태).

### 문제 대응(P6b)

- `ops load`가 `측정 실패: … 상태 Failed`: 출력된 쿼리로 `[opsjob]` 줄을 봅니다. 이미지가 자리표시면 `ops deploy eval`부터.
- `summary.json`의 `unreached`가 많음: Functions 로그(`AppTraces`)에서 해당 `application_id`를 찾고, 큐 메시지가 쌓였는지 봅니다.
- `고정비 측정 불가(…)`: Cost Management 429·권한·지연(약 1일)입니다. 다시 실행하거나 `--fixed-monthly-usd`로 수동 입력합니다.
- 장애 토글이 남음(`ops fault status`에 `flaky`·`unavailable`): `python -m ops fault source off` / `policy off`, 또는 `azd provision`.

### 알려진 제약(P6b)

- 24건은 SLO 표본 수준이며 용량 시험이 아닙니다. p95·99%는 표본 수와 함께 봅니다.
- 서킷브레이커는 Flex 인스턴스마다 따로입니다(분산 서킷은 운영 전환 과제).
- Python Durable은 오케스트레이터 → 활동 트레이스를 잇지 않아 신청 단위 연결은 `application_id` KQL 조인입니다.
- 건당 AI 비용은 캐시 할인 미반영(상한 추정)이고, 실패한 에이전트 호출은 토큰을 몰라 호출 수만 셉니다.
- 원형 통제 격차는 원형·값당 2건이라 잡음이 큽니다. 경보 판정에는 전체 격차만 씁니다.
- Foundry 트레이스(`genAIContent`)에는 프롬프트·응답이 남습니다. 에이전트 입력이 가명화된 값뿐이라는 원칙에 기댑니다.
```

README 안 코드 블록이 마크다운 코드 블록 안에 중첩되므로, 실제 README에는 바깥 펜스 없이 내용만 넣는다. "실제 실행 기록"과 "주요 실측값"은 P6a 절처럼 표로 쓰고, 측정하지 못한 항목은 "측정 불가(사유)"로 적는다.

또 README `### 알려진 제약(P6a)` 마지막 줄의 "운영 데이터 기준 공정성 대시보드, SLO는 범위 밖입니다(P6b)."를 "운영 데이터 기준 공정성 대시보드·SLO는 P6b 절을 봅니다."로 바꾼다.

- [ ] **Step 11: 최종 확인과 커밋**

```powershell
$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python -m pytest -q -p no:cacheprovider   # 실패 0
git add ops/reports README.md
git commit -m "docs(p6b): live ops metrics run and README"   # 트레일러 포함
```

`ops/reports/**`에 원문 PII가 없는지 커밋 전에 확인한다: `docs.json`은 `project()` 허용 필드만, `report.md`에는 합성 ID·세그먼트·수치만.

```powershell
Select-String -Path ops\reports\*\docs.json -Pattern '"assembly"|"narrative"|"business_name"|"representative"' | Measure-Object | % Count   # 0
```

---

## 계획 자체 검토

- Spec 대응: §1 목적·결정(헤더 사용자 결정, T14), §3 데이터 모델(T1~T3), §4 지표(T9), §5 관측성(T5·T6·T11·T13), §6 복원력·장애 주입(T4·T7·T12), §7 배치·실행·예산(T8·T10·T12·T14), §8 오류 처리(T10·T12), §9 probe(헤더 "사전 검증" 절), §10 테스트(각 Task Step 1), §12 위험(헤더 "배포 위험"). 빠진 절 없음.
- 이름 일관성: `run_metered`·`step_records`·`project`·`summarize`·`start_job`·`wait_job`·`run_scenario`·`CosmosOps`가 정의 Task와 사용 Task에서 같은 이름·인자.
- Review Focus 테스트 이름이 각 Task 테스트에 존재: `test_missing_or_bad_meter_falls_back_to_result`(T3), `test_run_metered_does_not_mutate_result`(T2), `test_half_open_allows_single_trial`·`test_client_error_does_not_trip_breaker`(T4), `test_scenario_reverts_on_failure`·`test_revert_failure_is_reported`(T12), `test_demo_cases_unchanged`(T8), `test_small_groups_are_excluded_from_gap`(T9), `test_wait_reports_unreached`·`test_budget_guard_stops_submission`(T10), `test_metrics_without_cost_access_still_reports`(T12), `test_ops_identity_is_least_privilege`(T11).
- 자리표시: T14 README의 실측값은 실행 후 채우는 값이며, 채우는 규칙(측정 못 하면 "측정 불가(사유)")을 명시했다.
