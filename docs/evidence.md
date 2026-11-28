# 증거 색인

캡스톤 평가 루브릭의 기준별로, 이 저장소가 뒷받침하는 **주장**과 그 **증거**(문서·코드·테스트·실측 보고서)를 모았다. 기준 이름은 루브릭 원문을 그대로 쓴다.

> **합성 데이터 기준.** 인용하는 수치는 모두 합성 데이터 실측이며, `ops/reports/load-20261102110233/report.md`·`eval/reports/job-eval-9imbutm/report.md`와 배포·운영 실행 기록에서만 온다.

## 기준별 증거

| 기준 | 주장 | 증거 |
|---|---|---|
| A1. Architecture, modularity & scalability | 컴포넌트가 계약으로 분리되고 확장 경로·장애 도메인을 문서화했다 | [아키텍처](architecture.md), [에이전트](agents.md), [ADR-001](adr/ADR-001-orchestration.md), [ADR-006](adr/ADR-006-two-container-apps-environments.md) |
| A2. Trade-off reasoning & design patterns | 패턴을 대안·기각 이유와 함께 선택했다(오케스트레이션·모델 배포·결정론 정책 등) | [ADR 색인](adr/README.md), [ADR-001](adr/ADR-001-orchestration.md), [ADR-002](adr/ADR-002-model-deployment.md), [ADR-003](adr/ADR-003-deterministic-policy.md), [ADR-005](adr/ADR-005-classification-model.md) |
| A3. Security, compliance & responsible AI | STRIDE + AI 위험 위협 모델에 맞춰 신원·데이터 보호·격리를 다루고, 잔여 위험과 미구현을 밝혔다 | [위협 모델](security/threat-model.md), [한계](limitations.md), [ADR-007](adr/ADR-007-demo-auth-mode.md), [ADR-009](adr/ADR-009-deletion-vs-explainability.md) |
| A4. Implementation completeness | 접수→심사→결정→사후 모니터링→동의 수명주기까지 전 구간이 실제 Azure에서 동작하고, 알려진 공백을 선제 공개했다 | [운영 실행 기록](runbook/deploy-history.md), [한계](limitations.md), `ops/reports/load-20261102110233/report.md` |
| A5. Operations — observability, reliability, performance & cost | 구조적 로그·지표·트레이스와 SLO(측정·판정), 복원력 패턴, 업무 가치 단위 비용 모델을 갖췄다 | [운영 런북](runbook/operate.md), [비용 모델](cost-model.md), `ops/reports/load-20261102110233/report.md`, `ops/reports/load-20261128132033/report.md`, `ops/reports/fault-20261102111447/summary.json` |
| A6. AI solution design & evaluation | 모델 선택을 비용·지연·품질로 비교하고, 지표·실패 유형을 정의한 평가 하네스로 측정했다 | `eval/reports/job-eval-9imbutm/report.md`, 최신 `eval/reports/job-eval-33u8sb3/report.md`(심사·안전성)·`eval/reports/job-eval-bwyuy87/report.md`(조기경보), [ADR-002](adr/ADR-002-model-deployment.md), [ADR-005](adr/ADR-005-classification-model.md) |
| A7. Agentic autonomy & orchestration | 에이전트가 명확한 가드레일 안에서 자율 판단하고, 핸드오프·반성·상태 그래프 패턴을 의도적으로 썼다 | [에이전트](agents.md), [아키텍처](architecture.md), [ADR-001](adr/ADR-001-orchestration.md), `eval/reports/job-eval-9imbutm/report.md` |
| B3. Demo & executive hook | 2분 데모로 기능이 아니라 결과("so what")를 보이고 장애 시 복구를 보이는 것이 목표다. 2026-11-03에 실제 환경에서 리허설을 1회 했고(탐색 위주 약 6분 20초, 2분 시간 측정은 완료하지 못함) 그 결과로 대본·대비책을 보완했다 | [운영 실행 기록](runbook/deploy-history.md), [데모 대본](demo/demo-script.md), [대비책](demo/fallback.md), [리허설 기록](demo/rehearsal.md) |
| B6. Written communication — deck & capstone documentation | 캡스톤 문서가 완결적이고 탐색 가능하며, 링크·경로가 테스트로 검증된다 | [아키텍처](architecture.md), [한계](limitations.md), [비용 모델](cost-model.md), [발표 덱](deck/capstone.pptx), [발표 스크립트](deck/capstone.script.md), `tests/docs/test_docs.py` |
| B7. Use of evidence & data storytelling | 청중이 행동할 수 있는 숫자로 주장을 뒷받침한다(건당 $0.0423, 고정비 월 $304.73 → 재측정 건당 $0.1919·고정비 월 $671.36, SLO·공정성 격차) | [비용 모델](cost-model.md), `ops/reports/load-20261102110233/report.md`, `ops/reports/load-20261128063941/report.md`, `ops/reports/load-20261128132033/report.md`, `eval/reports/job-eval-9imbutm/report.md` |
| C1. Discovery & business framing | 후원자(CBO·CRO)·제약·성공 지표를 밝히고, 원문 수치로 기대 가치($6,000 대출 단위 경제, 100bp = 자산 3억~5억 달러에서 연 $3M~5M)와 미추진 비용을 산술했다. 데모 측정값과 목표를 대조하되 목표 달성은 주장하지 않는다 | [비즈니스 케이스](business-case.md), [비용 모델](cost-model.md) |

## 실측 보고서 색인

| 보고서 | 무엇 | 날짜 | 링크 |
|---|---|---|---|
| P6a 평가(`job-eval-9imbutm`) | 에이전트 품질·안전·비용(분류·심사·조기경보·주입·반사실·PII) | 2026-11-02 | [report.md](../eval/reports/job-eval-9imbutm/report.md) |
| P6b load(`load-20261102110233`) | 신청 24건 SLO·건당 비용·공정성 격차 | 2026-11-02 | [report.md](../ops/reports/load-20261102110233/report.md) |
| P6b fault — source-flaky(`fault-20261102111447`) | 원천 API 불안정 주입·원복 확인 | 2026-11-02 | [summary.json](../ops/reports/fault-20261102111447/summary.json) |
| P6b fault — policy-down(`fault-20261102111750`) | 정책 엔진 다운 주입·원복 확인 | 2026-11-02 | [summary.json](../ops/reports/fault-20261102111750/summary.json) |
| 단계별 smoke·실제 배포 기록 | P2\~P6b 배포 순서·실측 소요·문제 대응·알려진 제약 | 2026-10\~11 | [deploy-history.md](runbook/deploy-history.md) |
| 부하 재측정(`load-20261128052837`) | TPM 상향 전 44건: p95 3.7분, 단계 성공 83.2%(SLO 미달), 건당 AI $0.2003 | 2026-11-28 | [report.md](../ops/reports/load-20261128052837/report.md) |
| 부하 재측정(`load-20261128063941`) | TPM 상향 후 36건: p95 1.8분, 단계 성공 100%(108/108), 건당 AI $0.1919, 고정비 월 $671.36 | 2026-11-28 | [report.md](../ops/reports/load-20261128063941/report.md) |
| 전체 평가(`job-eval-059ekf3`) | 분류·심사·조기경보·주입·PII. 예산 $5 도달로 부분 결과($5.45) | 2026-11-28 | [report.md](../eval/reports/job-eval-059ekf3/report.md) |
| 심사·안전성 재평가(`job-eval-33u8sb3`) | suite 분리 재실행. 주입 문구 3종·판별 가능 9건 × 모델 2개 지시 따름 0건, 반사실 6쌍(유효 5쌍 일관 100%), PII 0/402, 분석가 `gpt-5.6-sol` 최종 근거 100%·검토 통과 94.1%. $5.05 | 2026-11-28 | [report.md](../eval/reports/job-eval-33u8sb3/report.md) |
| 균형 부하 재측정(`load-20261128132033`) | 48건 전체 격자: p95 2.2분, 단계 성공 100%(144/144), 건당 AI $0.2007. 공정성 지역 18.8%p(이내)·업종 25.0%p 경보(eligible 격차 0%p) | 2026-11-28 | [report.md](../ops/reports/load-20261128132033/report.md) |
| 조기경보 재평가(`job-eval-bwyuy87`) | 원인 경로 10개 전체. 운영 모델 `gpt-5.6-luna` precision·recall 1.000·원인 정확도 100%·평균 리드 60.0일(연체 경로 2건: 46·74일). $4.39 | 2026-11-28 | [report.md](../eval/reports/job-eval-bwyuy87/report.md) |
| 리허설 | 2분 데모 리허설 1회(탐색 위주 약 6분 20초, 2분 시간 측정은 아님)·운영 시각·발견한 문제 | 2026-11-03 | [rehearsal.md](demo/rehearsal.md) |

---

관련 문서: [아키텍처](architecture.md) · [비용 모델](cost-model.md) · [한계와 운영 전환](limitations.md) · [위협 모델](security/threat-model.md) · [ADR 색인](adr/README.md)
