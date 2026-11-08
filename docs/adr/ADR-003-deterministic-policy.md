# ADR-003: 결정적 Policy Engine + fail-closed

## 상태
채택(P1, 2026-10-31~11-02) · as-built

## 맥락
자격·한도·금리 구간 같은 신용 규칙은 감사·재현이 가능해야 하고 규제 대상이다. LLM에 판정을 맡기면 근거 추적·버전 관리·재현이 어렵다. 과제 요구는 "결정론적 정책 엔진 + 심사역 승인"이다(상위 Spec §2.3·§4.1, `docs/superpowers/specs/2026-10-31-agentic-cashflow-lending-design.md`). 판정 서비스가 일시적으로 불능일 때 자동 승인이 새는 것을 막아야 한다.

## 결정
신용 규칙은 LLM 밖의 **결정적 Policy Engine**이 판정한다: 버전드 YAML 규칙(`src/policy/rulesets/v3.yaml`, `2026.11-v3`, 기본 규칙은 `DEFAULT_RULES_PATH`; 2026-11-08 정책 v3로 개정([아래 절](#2026-11-08-개정-정책-v3)). P9에서 상품 한도 50만~1,000만 원·기간 1~12개월로 개정(v2), 2026-11-08 기간을 3~6개월로 축소(규칙 YAML은 기간을 보지 않고 신청 입구에서 강제), `v1.yaml`·`v2.yaml`은 이력으로 보존)을 `src/policy/rules.py`가 로드하고(`load_rules`, `RuleSet.version`), `src/policy/engine.py`가 순수 함수로 자격·한도·금리·reason code를 판정하며 응답에 `rules_version`을 담는다(`:66`, `:74`). 서비스 진입점은 `src/policy/app.py`다. LLM(분석가·검토자)은 **추천만** 만들고 판정 권한이 없다. Policy 호출이 실패하면 **fail-closed**로 진행하지 않는다: 오케스트레이터가 `policy_unavailable`+`assessment_unavailable` 플래그를 남기고 `awaiting_officer`로 심사역에게 넘기며(`src/orchestrator/journey/orchestration.py:198-201`, `:245`), 심사역 결정 검증은 정책 결과가 없거나 ineligible이면 기본적으로 승인을 거부한다(`override_required`). 2026-11-04 개정으로 심사역(CreditOfficer·CreditManager)이 **정책 예외 승인**을 할 수 있다. 콘솔이 "정책 결과상 거절이지만 승인하겠습니까?" 확인을 받고, 결정에 `policy_override=true`와 사유를 붙여야 한다. 금액은 상품 최대 한도(1,000만 원, `OVERRIDE_MAX_AMOUNT_KRW`) 이하이며, 콘솔과 오케스트레이터가 같은 검증을 다시 한다(`src/orchestrator/journey/validation.py`, `src/console/static/decision.js`). 자동 경로에서는 여전히 승인이 새지 않는다. 정책 판정이 eligible·refer인 건은 지금처럼 정책 한도를 넘는 승인을 막는다(`amount_exceeds_policy`). P9부터 심사역 결정은 승인/거절 두 가지이며 조건부 승인은 폐지했다(이전 `conditionally_approved` 문서는 읽기 전용 이력으로 표시). 반복 실패를 막는 서킷 브레이커는 `src/orchestrator/journey/circuit.py`(프로세스 단위)이고, `src/orchestrator/journey/adapters.py`의 `HttpPolicyApi`가 open 상태에서 `PolicyUnavailable("circuit_open")`을 던진다.

## 2026-11-08 개정: 정책 v3

- **R101 삭제:** 신고매출·계좌매출 불일치(`tax_bank_match_ratio < 0.7`)는 간이·면세 사업자와 신고 주기 차이 때문에 정상 셀러에게도 오탐이 났다. 지표는 계산·표시·근거 ID(`M:tax_bank_match_ratio`)로 남기고, 콘솔은 참고("watch") 지표로만 보여 준다. 옛 신청의 R101 사유는 읽기 전용 이력이다.
- **추가(모두 검토 `refer` 규칙):** R105 고비용 대부·P2P 상환 발견(`high_cost_lender_count > 0`), R106 최근 60일 매출 급감(`revenue_trend_ratio < 0.80`, 기준일 직전 60일 일평균 ÷ 그 앞 최대 180일(최소 60일) 일평균. 기준일은 신청 기준일과 마지막 계좌 거래일 다음 영업일 중 이른 날이라 목 데이터가 오래돼도 추세가 떨어지지 않는다), R107 판매 채널 편중(`pg_top_platform_share > 0.80`, 단일 플랫폼 셀러도 걸린다), R108 정산–입금 대사 불일치(`pg_reconciliation_ratio < 0.90`). 정산 지표는 `pg.json`을 계좌 입금과 대조하는 결정적 코드(`src/agents/pg_ledger.py`)가 만들고, LLM은 인용만 한다([에이전트](../agents.md#정산-원장-대사)).
- **값 없음 규칙 미적용:** 엔진은 관측값이 `None`이면 그 규칙을 적용하지 않고 사유도 남기지 않는다. 정산 자료가 없으면 R107·R108이, 이력이 짧아 추세를 못 재면 R106이 빠진다. 값이 없다고 거절하거나 검토로 돌리지 않는 이유는 자료 부재가 곧 위험 신호는 아니기 때문이다. 대신 콘솔이 "정산 자료 없음"으로 표시하고, `pg.json`이 손상되면 `pg_ledger_unavailable` 플래그를 남긴다.
- **임계값 근거(데모 케이스·평가 케이스 분포, 기준일 2026-10-01·11-02·11-09, 기간 6개월):** 정상 셀러의 대사율 최솟값은 1.0, ASSIGNED(정산 양도) 셀러의 최댓값은 0.776이라 R108 기준 0.90이 둘 사이에 들어간다. STRESSED 셀러의 매출 추세 최댓값은 0.799로 R106 기준 0.80 바로 아래다. **R106은 마진이 0.001뿐이라** 합성 데이터의 작은 변화나 다른 기준일에도 판정이 뒤집힐 수 있다. 합성 분포에 맞춘 값이지 실데이터로 보정한 값이 아니다.
- **데모 판정 변화:** 세금 불일치 셀러 4명(mangwon-02·kimchi-09·kitchen-12·petcare-16)은 다른 사유가 없어 적격이 된다. 스트레스 셀러 4명(glowlab-03·skinlab-11·cable-13·sofa-18)은 `refer`(R105·R106). 단일 플랫폼 셀러(candle-05·ceramic-08)는 R107로 `refer`이고, 이력 부족 셀러도 단일 플랫폼이면 R107이 더해진다. 정산 양도 셀러 mugshop-21은 `ineligible`(R002·R106·R108·R901)이다. 양도로 지급이 다른 곳에 들어가 매출이 줄고 "선정산 선지급" 입금(`LOAN_PROCEEDS`로 분류)이 매출에서 빠져 DSCR이 1.0 아래로 크게 내려가므로(신청 금액·기간에 따라 달라지며, 결정 불변성 테스트 입력인 330만 원·12개월·기준일 2026-11-01에서 0.393이고 기본 신청 조건 500만 원·6개월에서는 더 낮다), R108만의 검토가 아니라 R002 탈락에 R108·R106이 *왜 매출이 줄었는지*를 설명하는 사유로 붙는다.
- **배포 순서:** 새 특성 필드는 기본값 `None`이라 정책 서비스를 먼저 올려도 옛 특성을 받는다. 정책 → mockapi → 오케스트레이터 → 콘솔 순서로 배포한다.

## 검토한 대안
| 대안 | 장점 | 기각 이유 |
|---|---|---|
| LLM이 자격·한도까지 판정 | 코드가 적고 유연 | 감사·재현·버전 관리 불가, 규제·과제 요구 위반, 프롬프트 주입에 취약 |
| 코드 하드코딩 규칙 | 가장 빠름 | 규칙 변경이 재배포를 요구하고 버전 추적·근거 노출이 어려움 |
| Policy 실패 시 fail-open(기본 승인 진행) | 가용성 높음 | 판정 없이 승인이 새어 신용 리스크·규제 위반. 거부(fail-closed)가 안전 |
| 정책 거절 건은 사람도 승인 불가(개정 전 방식) | 통제가 가장 단순 | 정책은 심사역 판단을 돕는 도구이고 최종 책임은 사람에게 있다. 소명 자료로 예외를 인정할 길이 없으면 HITL이 형식이 된다. 확인·사유·감사 표시를 조건으로 예외 승인을 허용 |

## 결과
- 장점: 모든 판정에 규칙 버전·reason code가 붙어 설명 가능하고, 서비스 장애가 자동 승인으로 이어지지 않는다. 실측 policy-down에서 3건 모두 `policy:failed`로 멈추고 `awaiting_officer`로 복귀, `alert-policy-circuit-open` 발화(`docs/runbook/deploy-history.md:581`; 원시 `ops/reports/fault-20261102111750/`).
- 대가: Policy 장애 시 심사역 수동 심사로 부하가 쏠리고 자동 처리량이 떨어진다. 서킷 상태가 인스턴스별이라(ADR-001) 신청별 시험(half-open) 호출 수가 다르다(`docs/runbook/deploy-history.md:581`). 예외 승인은 정책 밖 신용 위험을 심사역 책임으로 옮기므로, 운영 전환 시 예외 승인률 모니터링과 2인 승인(관리자 결재) 검토가 필요하다.

## 재검토 조건
- 규칙셋이 커져 규칙 엔진 외부화(예: 전용 룰 서비스·DMN)가 필요할 때.
- Flex 다중 인스턴스에서 전역 서킷이 필요할 때(분산 서킷, ADR-001).
- 실데이터 규제 검토에서 금리·한도 규칙 거버넌스 요건이 강화될 때.

## 근거
- 구현: `src/policy/rulesets/v3.yaml`(이력 `v2.yaml`·`v1.yaml`), 정산 지표 `src/agents/pg_ledger.py`, `src/policy/rules.py`, `src/policy/engine.py:66`, `src/policy/app.py`.
- fail-closed: `src/orchestrator/journey/orchestration.py:198-201`, `src/orchestrator/journey/validation.py`(예외 승인 `_validate_override`).
- 서킷: `src/orchestrator/journey/circuit.py`, `src/orchestrator/journey/adapters.py`.
- 실측: `ops/reports/load-20261102110233/report.md` 4장(정책 호출 수·`circuit_open`), 서킷 half-open·경보는 `docs/runbook/deploy-history.md:581`.
- 상위 Spec: `docs/superpowers/specs/2026-10-31-agentic-cashflow-lending-design.md` §4.1.
