# ADR-003: 결정적 Policy Engine + fail-closed

## 상태
채택(P1, 2026-10-31~11-02) · as-built

## 맥락
자격·한도·금리 구간 같은 신용 규칙은 감사·재현이 가능해야 하고 규제 대상이다. LLM에 판정을 맡기면 근거 추적·버전 관리·재현이 어렵다. 과제 요구는 "결정론적 정책 엔진 + 심사역 승인"이다(상위 Spec §2.3·§4.1, `docs/superpowers/specs/2026-10-31-agentic-cashflow-lending-design.md`). 판정 서비스가 일시적으로 불능일 때 자동 승인이 새는 것을 막아야 한다.

## 결정
신용 규칙은 LLM 밖의 **결정적 Policy Engine**이 판정한다: 버전드 YAML 규칙(`src/policy/rulesets/v1.yaml`)을 `src/policy/rules.py`가 로드하고(`load_rules`, `RuleSet.version`), `src/policy/engine.py`가 순수 함수로 자격·한도·금리·reason code를 판정하며 응답에 `rules_version`을 담는다(`:66`, `:74`). 서비스 진입점은 `src/policy/app.py`다. LLM(분석가·검토자)은 **추천만** 만들고 판정 권한이 없다. Policy 호출이 실패하면 **fail-closed**로 진행하지 않는다: 오케스트레이터가 `policy_unavailable`+`assessment_unavailable` 플래그를 남기고 `awaiting_officer`로 심사역에게 넘기며(`src/orchestrator/journey/orchestration.py:198-201`, `:245`), 심사역 결정 검증은 정책 결과가 없거나 ineligible이면 **거절만 허용**한다(`src/orchestrator/journey/validation.py:21-24`, `decline_only`). 반복 실패를 막는 서킷 브레이커는 `src/orchestrator/journey/circuit.py`(프로세스 단위)이고, `src/orchestrator/journey/adapters.py`의 `HttpPolicyApi`가 open 상태에서 `PolicyUnavailable("circuit_open")`을 던진다.

## 검토한 대안
| 대안 | 장점 | 기각 이유 |
|---|---|---|
| LLM이 자격·한도까지 판정 | 코드가 적고 유연 | 감사·재현·버전 관리 불가, 규제·과제 요구 위반, 프롬프트 주입에 취약 |
| 코드 하드코딩 규칙 | 가장 빠름 | 규칙 변경이 재배포를 요구하고 버전 추적·근거 노출이 어려움 |
| Policy 실패 시 fail-open(기본 승인 진행) | 가용성 높음 | 판정 없이 승인이 새어 신용 리스크·규제 위반. 거부(fail-closed)가 안전 |

## 결과
- 장점: 모든 판정에 규칙 버전·reason code가 붙어 설명 가능하고, 서비스 장애가 자동 승인으로 이어지지 않는다. 실측 policy-down에서 3건 모두 `policy:failed`로 멈추고 `awaiting_officer`로 복귀, `alert-policy-circuit-open` 발화(`ops/reports/load-20261102110233/report.md` 4장, 원시 `ops/reports/fault-20261102111750/`).
- 대가: Policy 장애 시 심사역 수동 심사로 부하가 쏠리고 자동 처리량이 떨어진다. 서킷 상태가 인스턴스별이라(ADR-001) 신청별 시험 호출 수가 다르다.

## 재검토 조건
- 규칙셋이 커져 규칙 엔진 외부화(예: 전용 룰 서비스·DMN)가 필요할 때.
- Flex 다중 인스턴스에서 전역 서킷이 필요할 때(분산 서킷, ADR-001).
- 실데이터 규제 검토에서 금리·한도 규칙 거버넌스 요건이 강화될 때.

## 근거
- 구현: `src/policy/rulesets/v1.yaml`, `src/policy/rules.py`, `src/policy/engine.py:66`, `src/policy/app.py`.
- fail-closed: `src/orchestrator/journey/orchestration.py:198-201`, `src/orchestrator/journey/validation.py:21-24`.
- 서킷: `src/orchestrator/journey/circuit.py`, `src/orchestrator/journey/adapters.py`.
- 실측: `ops/reports/load-20261102110233/report.md` 4장.
- 상위 Spec: `docs/superpowers/specs/2026-10-31-agentic-cashflow-lending-design.md` §4.1.
