# P6c 문서·ADR·위협 모델·데모 대본 — 설계 Spec

- 날짜: 2026-11-02
- 상위 Spec: `2026-10-31-agentic-cashflow-lending-design.md` §2(아키텍처·ADR-001), §4.2(STRIDE·ADR-002), §4.4(비용), §6.1(데모 대본), §6.2(저장소 구조), §7(위험), §8(루브릭 매핑)
- 범위: P6 3분할의 마지막 단계. P6a(평가 하네스)·P6b(운영 지표)는 완료·병합. Section D(백서), Q&A 노트, 발표 슬라이드는 범위 밖입니다.

---

## 1. 목적과 성공 기준

### 1.1 목적

루브릭 B6(문서가 완결되고 찾아가기 쉬움)과 A1~A3·B3·B7(근거 있는 설계 주장)에 대응합니다. 지금 README는 단계(P2~P6b)별 작업 기록 625줄이라
처음 보는 평가자가 아키텍처·결정 이유·보안 통제·실측 증거를 찾기 어렵습니다. P6c는 **현재 구현(as-built)** 기준 문서 허브를 만들고,
2분 데모 대본을 실제 Azure 리허설 1회로 검증합니다.

### 1.2 사용자 결정 사항

| 항목 | 결정 |
|---|---|
| 범위 | 핵심 문서(아키텍처·ADR·위협 모델·비용 모델·한계), 데모(대본·대비책·리허설), README 재구성. Q&A·슬라이드·백서 제외 |
| 언어 | 한국어 |
| 데모 | 문서 + 실제 Azure 리허설 1회(장면별 소요 시간 기록). 새 데모 명령은 만들지 않음 |
| 구조 | 문서 허브: README(입구, 약 150줄) + `docs/` 주제별 문서 |
| 데모 장면 | 동의 **철회** 장면 없음. 1:45 장면은 동의 **만료** 삭제 증명 |
| 부수 정리 | P6b 보류 Minor M5·M6·M7을 작은 코드 수정으로 처리 |

### 1.3 원칙

1. **as-built**: 문서는 지금 배포된 코드·인프라를 설명합니다. 상위 Spec과 달라진 점(예: 콘솔 데모 모드에서 Entra/Easy Auth 끔)은 ADR과 한계 문서에 명시합니다.
2. **수치 인용만**: 모든 숫자는 기존 실측 보고서·README 실행 기록에서 인용하고 출처 경로를 붙입니다. 새 수치를 만들지 않습니다(리허설 시간만 새로 측정).
3. **삭제 없는 이동**: README에서 옮기는 문제 대응·실제 배포 기록·알려진 제약은 문구를 지우지 않고 `docs/`로 옮깁니다.
4. **증거 연결**: 통제·주장마다 코드 경로, 테스트 파일, 보고서 경로 중 하나 이상을 링크합니다. 증거가 없으면 "미검증"으로 표시합니다.

### 1.4 성공 기준

1. 2장의 문서가 모두 존재하고 README 문서 지도에서 링크됩니다.
2. `tests/docs/test_docs.py`가 통과합니다(상대 링크 대상 존재, 언급된 코드·보고서 경로 존재, 문서 지도 완전성).
3. 리허설 1회를 실제 Azure에서 수행하고 `docs/demo/rehearsal.md`에 장면별 시간·신청 ID·문제점을 기록합니다.
4. M5·M6·M7 수정이 테스트와 함께 들어가고, 기존 테스트(878 passed, 105 skipped)가 모두 유지됩니다.
5. 리허설 배포로 P6b I1(Policy ASGI 스팬 제외)이 실제 Azure에 반영됩니다.

---

## 2. 문서 구성

```text
README.md                         입구(약 150줄)
docs/
  architecture.md                 아키텍처(그림·계약·장애 도메인·확장·운영 아키텍처)
  agents.md                       에이전트 설계(구현된 전부)
  adr/
    README.md                     ADR 색인
    ADR-001-orchestration.md … ADR-009-deletion-vs-explainability.md
  security/
    threat-model.md               STRIDE + AI 위험 + 데이터 보호
  cost-model.md                   건당 비용·고정비·절감 레버
  evidence.md                     루브릭 기준 → 증거 색인
  limitations.md                  알려진 한계 통합 + 운영 전환 체크리스트
  runbook/
    setup.md                      로컬 개발·Azure 준비·최초 배포
    deploy.md                     배포 창·구성요소별 배포 순서
    operate.md                    pause·resume·smoke·eval·load·fault·metrics
    troubleshooting.md            단계별 "문제 대응" 통합(증상 → 원인 → 조치)
    deploy-history.md             단계별 "실제 배포 기록" 원문 보존
  demo/
    demo-script.md                2분 대본
    fallback.md                   대비책
    rehearsal.md                  리허설 절차·기록
```

### 2.1 README.md(약 150줄)

1. 한 줄 소개와 문제(소상공인 운전자금 대출 2주 → 몇 분), 해결 방식 요약
2. 아키텍처 요약 Mermaid 그림 1개(신청 → 오케스트레이터 → 에이전트·정책 엔진 → 심사역 콘솔, 사후 모니터링·동의 수명주기)
3. 핵심 실측값 표: SLO(p95 1.2분, 단계 성공 72/72), 건당 AI 비용($0.0423), 월 고정비($304.73), 평가 결과(P6a), 각 행에 보고서 링크
4. 빠른 시작: 로컬 테스트 명령, Azure 배포 요약 3~5줄 → `docs/runbook/setup.md`
5. 문서 지도: 2장의 모든 문서와 한 줄 설명
6. 한계 요약 5줄 이내 → `docs/limitations.md`
7. 단계별 이력(P1~P6c) 한 줄씩 → 해당 Spec·Plan 링크

### 2.2 docs/architecture.md

- **그림(Mermaid)**: ① 시스템 컨텍스트(차주·심사역·운영자·외부 데이터 원천·Azure) ② 컨테이너(Functions Flex 오케스트레이터, Policy Engine, Console, Mock API, Foundry 에이전트, Cosmos, Storage, Key Vault, App Insights, Container Apps Job) ③ 네트워크(VNet·서브넷·Private Endpoint·배포 창·IP 허용 목록) ④ 신청 1건 시퀀스(동의 → 조립 → 분류 → 심사 → 정책 → 심사역) ⑤ 사후 흐름(조기경보 재생·추심·동의 만료 삭제)
- **컴포넌트 계약**: 구성요소별 책임·입출력·저장 위치·인증 방식(관리 ID 등) 표
- **장애 도메인**: 상위 Spec §2.4 + P6b 실측(source-flaky 회복, policy-down fail-closed) 링크
- **확장 경로**: 물량 증가 시 병목과 대응(Flex 인스턴스, Cosmos RU, 모델 TPM, PTU 전환)
- **운영 아키텍처(미구축)**: App Gateway WAF, Entra 로그인 복원, 다중 리전 등 데모와의 차이를 표로

### 2.3 docs/agents.md

`src/agents/`에 구현된 에이전트 전부(데이터 조립, 거래 분류, 심사, 조기경보, 추심, 동의 안내, 이의 회신) 각각: 목적, 자율성 수준, 도구 allowlist, 가드레일(근거 검사·템플릿 대체), 반성 패턴, 모델과 선택 근거(P6a 평가 보고서 인용), 실패 시 동작.

### 2.4 docs/adr/

템플릿: `상태 / 맥락 / 결정 / 검토한 대안과 기각 이유 / 결과(장점·대가) / 재검토 조건 / 근거 링크`.

| ADR | 결정 | 주요 대안 |
|---|---|---|
| 001 | Durable Functions(Flex, Storage 백엔드) 오케스트레이션 | Logic Apps, Foundry 다중 에이전트 단독 |
| 002 | 모델 배포 Global Standard | Data Zone Standard, PTU |
| 003 | 결정적 정책 엔진 + fail-closed(LLM은 최종 결정 불가) | LLM 판단 |
| 004 | 완전 사설망 + 배포 창(Bastion 없음) | 공개 엔드포인트, Bastion·점프박스 |
| 005 | 거래 분류 Decision-1 + mini 대체 | gpt-5.4 단일 |
| 006 | Container Apps 환경 2개(콘솔만 외부) | 단일 환경 |
| 007 | 데모 모드: Entra/Easy Auth 제거, IP 허용 목록만 | Entra 로그인 유지 |
| 008 | 평가·운영 작업을 VNet Container Apps Job으로, 보고서는 로그 청크 회수 | 로컬 실행 + 임시 공개 접근 |
| 009 | 원천 데이터 삭제 + 판단 기록 보존 | 전부 삭제, 전부 보존 |

ADR-001·002는 상위 Spec 본문을 옮기고 as-built 결과를 덧붙입니다. ADR-007은 운영 전환 시 복원 조건을 명시합니다.

### 2.5 docs/security/threat-model.md

- 범위·신뢰 경계 그림(Mermaid), 자산 목록
- **STRIDE 표**: 행 = 위협, 열 = 범주 / 대상 / 통제 / 증거(코드·테스트·실측) / 상태(검증·미검증)
- **AI 위험**: 프롬프트 주입(차주 글을 회신 에이전트에 주지 않음 등), 그라운딩 실패(근거 검사·템플릿 대체), PII 유출(보고서 PII 검사, 대화 삭제), 과도한 자율성(도구 allowlist, 정책 엔진 최종 결정)
- 데이터 분류·보존 기간, 개인정보 원칙(동의·목적 제한·삭제 증명)
- 데모 모드에서 약해진 통제(ADR-007)와 잔여 위험

### 2.6 docs/cost-model.md

- 건당 AI 비용 $0.0423(모델별 호출·비용, Decision-1 단가 미확인 명시) — 출처 `ops/reports/load-20261102110233/report.md`
- 월 고정비 $304.73 항목별 분해, Cost Management 데이터 기간 한계
- 물량별 단가(월 100/1,000/10,000건 → $3.09/$0.35/$0.07), 수작업 기준 $3,500/건(출처: Mills & McCarthy, HBS WP 15-004)
- 절감 레버: pause, 모델 등급 조정, PTU 손익분기, 배포 창 운영

### 2.7 docs/evidence.md

루브릭 기준(A1~A7, B3, B6, B7)별로 "주장 → 증거 문서·보고서·테스트" 표. 실측 보고서 색인(P6a 평가, P6b load·fault, smoke 기록, 리허설).

### 2.8 docs/limitations.md

README 단계별 "알려진 제약" 원문을 주제별(인증·데이터·에이전트·동의·운영·관측)로 묶고, 상위 Spec §7 위험, P6b 후속 과제
(`agent_assessment_mismatch`, 표본 24건 공정성 통계 한계, 지역 격차 경보)를 더합니다. 마지막에 운영 전환 체크리스트.

### 2.9 docs/runbook/

README의 P2~P6b 절을 작업 기준으로 재편합니다. 명령·문제 대응 문구는 원문 유지, 단계 표시는 `(P5b)`처럼 괄호로 남깁니다.
운영자 IP 변동 대응(`operator-alt` 임시 규칙 추가·제거)을 troubleshooting에 추가합니다.
`deploy-history.md`는 README P2~P6b 절 전체(설명·배포 순서·실제 배포 기록·문제 대응·알려진 제약)를 원문 그대로 보존하는 기록본이고, setup·deploy·operate·troubleshooting은 현재 작업 기준으로 재구성한 사본이다.

---

## 3. 데모

### 3.1 2분 대본(docs/demo/demo-script.md)

| 시간 | 장면 | 화면 | 방식 |
|---|---|---|---|
| 0:00 | 문제 제시 | README 아키텍처 그림 또는 콘솔 첫 화면 | 말 |
| 0:15 | 신청과 실시간 진행 | `/applications/new` → `/applications/<ID>` 타임라인 | **라이브 1건** |
| 0:50 | 심사 근거와 승인 | 신청 상세의 근거 링크·반사실 → 승인 | 라이브 건(지연 시 사전 건) |
| 1:20 | 조기경보 | `/loans/<ID>` → 재생 → 경보·hardship(재조정) | **사전 실행 대출** |
| 1:45 | 동의 만료 | 신청 상세 `지금 만료(데모)` → `/consents/<ID>/proof` | 라이브 건(또는 사전 건) |

- 장면마다 말할 문장, 클릭 순서, 예상 소요, 전환 지점(몇 초 넘으면 사전 건으로)을 적습니다.
- 사전 실행 대출은 심사 동의를 만료하지 않은 대출이어야 합니다(만료 뒤에는 재생 불가, P5b 제약).

### 3.2 대비책(docs/demo/fallback.md)

- 사전 준비 목록: 승인 완료 신청 1건, 재생 준비된 대출 1건, 삭제 증명 완료 동의 1건(ID 기록)
- 라이브 지연·실패 시 사전 건으로 넘어가는 대사
- 전체 장애 시 녹화본 전환 대본(녹화는 사용자가 직접 촬영, 저장소에는 절차만)
- 시작 전 점검: 콘솔 접속 IP(허용 목록·`operator-alt`), resume 상태, smoke 결과, 브라우저 탭 순서

### 3.3 리허설(docs/demo/rehearsal.md)

실제 Azure에서 1회 수행합니다. 새 명령 없이 기존 `ops` 명령만 씁니다.

1. `ops window open --acr` → `ops deploy all`(M5·M6·M7 수정과 P6b I1 Policy 수정 반영) → `ops smoke`
2. 사전 건 준비(신청 승인, 대출 재생 준비)
3. 대본대로 실행하며 장면별 시작·끝 시각, 신청·대출·동의 ID 기록
4. App Insights에서 Policy ASGI 스팬이 더 들어오지 않는지 확인(I1 반영 확인)
5. 임시 규칙 제거 → `ops window close` → `ops pause`
6. rehearsal.md에 소요 시간 표, 대본 대비 차이, 문제점과 대본 수정 사항 기록

---

## 4. 부수 정리(P6b 보류 Minor)

| ID | 문제 | 수정 |
|---|---|---|
| M5 | P6b 보고서 의존성 표에 리소스 호스트명이 그대로 노출(`report.md`, `metrics.json`) | 보고서 생성 시 호스트명을 역할 라벨(`storage-queue`, `cosmos`, `mockapi`, `foundry`, `storage-blob` 등)로 바꿈. 기존 `metrics.json`은 `ops.kql.relabel`로 오프라인 변환 후 `report.md`를 다시 렌더링(Cost Management 재조회 없이 숫자 불변 보장) |
| M6 | 단계 기록 `detail`에 원시 예외 문자열 저장(`metering.py`의 `str(error)`) | 예외 종류·표준 코드만 남김(메시지 본문 제외). 기존 `DETAIL_MAX` 유지 |
| M7 | 단계 라벨 불일치(`src/console/ops.py`의 `assemble` = "자료 조립" vs `src/ops/opsreport.py`·콘솔 타임라인 "데이터 수집") | 한 곳(공유 상수)에서 라벨을 정의하고 콘솔·보고서가 같이 씀 |

각 수정은 실패하는 테스트를 먼저 쓰고(TDD) 고칩니다.

---

## 5. 검증

- `tests/docs/test_docs.py`
  1. README와 `docs/**/*.md`(단 `docs/superpowers/` 제외)의 상대 링크 대상이 존재
  2. 문서 본문 백틱 안의 저장소 경로(`src/…`, `infra/…`, `ops/reports/…`, `eval/reports/…`, `tests/…`)가 존재
  3. README 문서 지도가 2장 문서를 모두 링크
  4. ADR 파일마다 필수 절(맥락·결정·대안·결과·재검토 조건)이 있음
- 전체 테스트: `$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python -m pytest -q -p no:cacheprovider`
- Mermaid 문법은 자동 검사하지 않고, 작성 후 렌더링(VS Code 미리보기 등) 수동 확인을 체크리스트로 둡니다.

---

## 6. 작업 순서(계획 입력)

1. 문서 검사 테스트(실패 상태로 시작)
2. M5·M6·M7 코드 수정(TDD)
3. runbook 이동(README 원문 → docs/runbook, deploy-history)
4. architecture.md, agents.md
5. ADR 9건 + 색인
6. threat-model.md
7. cost-model.md, limitations.md, evidence.md
8. demo-script.md, fallback.md
9. README 재작성(문서 지도) → 문서 테스트 통과
10. 실제 Azure 리허설 → rehearsal.md
11. 최종 리뷰 → 병합

브랜치: `p6c-docs-demo`.

---

## 7. 위험

| 위험 | 대응 |
|---|---|
| 문서와 코드 불일치 | 경로 검사 테스트, 작성 시 코드에서 직접 확인, 리뷰 단계에서 표본 대조 |
| README 이동 중 내용 손실 | 이동 전후 "문제 대응"·"알려진 제약" 항목 수 비교를 계획 단계 검사로 둠 |
| 리허설 중 IP 변동 | `operator-alt` 임시 규칙 절차를 fallback·troubleshooting에 명시 |
| 리허설 비용·시간 | 배포 창 약 7분 + 배포 약 10분 + 리허설 30분 이내, 끝나면 pause |
| 보고서 재생성 시 수치 변화 | 오프라인 변환은 의존성 표 대상 열만 바꾼다. `summary`가 변환 전후 같음을 확인 |
