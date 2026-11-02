# ADR-009: 원천 데이터 삭제 + 판단 기록 보존

## 상태
채택(P5b, 2026-10-31~11-02) · as-built

## 맥락
신용정보법·마이데이터 원칙상 동의 만료·철회 시 **원천 데이터를 지우고 삭제를 증명**해야 한다. 동시에 자동화된 평가에는 **설명 요구권**(반사실·이의제기)이 있어 판단을 사후에 설명할 수 있어야 한다. 두 요구는 상충한다: 다 지우면 설명이 불가능하고, 다 남기면 삭제 의무 위반이다. 상위 Spec §4.1(저장 위치·보존 정책 표)이 이를 규정한다(`docs/superpowers/specs/2026-10-31-agentic-cashflow-lending-design.md` §4.1).

상위 Spec §4.1 보존 표 요지: 원천 거래·세무·PG 데이터(Blob, 동의 ID별 컨테이너)는 만료·철회 시 삭제, 판단 기록(파생 특징값·reason code·모델/프롬프트/규칙 버전·심사역 결정, Cosmos)과 삭제 증명은 장기 보존.

## 결정
원천 데이터는 지우고, 판단 기록은 원천 없이도 설명 가능한 파생값·근거 ID만 남긴다. 동의마다 `consent_lifecycle` 오케스트레이션이 만료·철회까지 기다렸다가 `delete_consent_data`(결정적·멱등, `src/orchestrator/journey/consent.py`)로 지운다. 순서(deploy-history P5b): 동의를 먼저 `expired`/`revoked`로 표시 → 원천 Blob 컨테이너(삭제 전 파일 목록·sha256 manifest 기록, blob을 하나씩 지운 뒤 컨테이너 삭제, "없음 또는 빈 컨테이너"를 삭제 확인으로 간주) → 목업 동의 레지스트리 revoke → Foundry 대화·컨테이너 삭제. 모든 항목이 검증되어야 `complete`이고, 아니면 증명을 미완료로 남기고 재시도한다(앞 시도에서 확인된 항목은 다시 지우지 않고 원천 컨테이너만 매번 재확인). 증명은 **`deletion_proofs`** 컨테이너(파티션 `/consent_id`, id = 동의 ID)에 저장하고, 보존 항목으로 신청 문서를 남긴다(`retained`의 `application_record` → `applications/<id>`).

## 검토한 대안
| 대안 | 장점 | 기각 이유 |
|---|---|---|
| 원천·판단 기록 모두 삭제 | 가장 강한 삭제 | 설명 요구권·이의제기 대응 불가. 감사 불가 |
| 모두 보존(삭제 안 함) | 설명·감사 쉬움 | 삭제·철회 의무 위반. 개인정보 과보존 |
| 원천 데이터 익명화 후 보존 | 일부 분석 유지 | 재식별 위험과 "삭제" 정의 논란. 파생값+근거 ID 보존이 더 안전·명확 |

## 결과
- 장점: 원천 PII는 삭제·증명되고(멱등·검증), 판단은 파생값·근거 ID로 설명 가능하다. 삭제가 실패하면 증명이 미완료로 남아 재시도된다. 실측: smoke `consent`가 승인 신청의 심사 동의를 `expire_now`로 끝내 삭제 증명 `complete`·동의 `expired`·신청 문서 보존을 확인([../runbook/deploy-history.md](../runbook/deploy-history.md) P5b).
- 대가: 원천이 지워진 뒤에는 사후 재생(모니터링)을 시작할 수 없다(프로필 원천 데이터 소실, deploy-history P5b "알려진 제약"). Durable 실행 기록은 지우지 않는다(동의 ID·상태만, 개인정보 없음). 데모 Mock 동의 레지스트리는 메모리라 재시작 뒤 철회는 `absent`로 기록된다.

## 재검토 조건
- 보존 기간·삭제 범위에 대한 실데이터 규제 요건이 확정될 때(법률 검토는 이 데모가 대신하지 않음).
- 사후 재생이 원천 삭제 이후에도 필요해 파생 기준선 보존 설계를 바꿔야 할 때.
- 삭제 증명을 외부 감사기관에 제출하는 포맷·서명 요건이 생길 때.

## 근거
- 구현: `src/orchestrator/journey/consent.py`(`delete_consent_data`, `deletion_proofs`, `retained`의 `application_record`).
- 순서·검증·제약: [../runbook/deploy-history.md](../runbook/deploy-history.md) "동의 수명주기·삭제 증명·이의제기(P5b)".
- 상위 Spec: `docs/superpowers/specs/2026-10-31-agentic-cashflow-lending-design.md` §4.1.
- 사후·동의 흐름: [../architecture.md](../architecture.md).
