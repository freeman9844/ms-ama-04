# 신청액 전액 권고 구현 계획

> 작업자는 subagent-driven-development 또는 executing-plans 절차로 단계를 진행한다.

**목표:** AI가 대체 금액을 제안하지 않고 신청액 전액에 대한 세 단계 의견을 근거와 함께 낸다.

**구조:** 현행 의견은 버전으로 식별한다. 정책 한도·신청액 검증은 결정적 코드가 담당하고, 분석가와 검토자는 근거와 불확실성을 설명한다. 화면과 채팅 모두 과거 자료를 현행 권고로 해석하지 않는다.

**기술:** Python, Pydantic, FastAPI, Jinja, pytest. **설계:** `docs/superpowers/specs/2026-11-20-full-request-review-design.md`

## 공통 제약

신규 권고는 `approve`, `refer`, `decline`만 사용한다. 신청액, 정책 한도, 실제 심사역 승인액은 유지한다. 최종 결정·대출 실행·상환·과거 감사 원본은 변경하지 않는다.

## 검토 초점

정책상 전액 불가와 자료 부족이 겹치는 경우, 한도와 신청액이 같은 경우, 미검토 초안, 과거 조건부 승인, 한영 자유서술·채팅에서의 대체 금액 재노출을 확인한다.

### 1. 의견 생산 계약과 정책 제약

- [ ] `tests/agents/test_underwriting.py`와 공유 계약 검사에 적격·동액 한도, 신청액 초과, 회부, 부적격, 근거 부족, 검토 실패 사례를 먼저 작성한다. `pytest tests/agents/test_underwriting.py`에서 구 계약 실패를 확인한다.
- [ ] `src/shared/contracts.py`, `src/agents/underwriting.py`의 신규 의견 계약에서 `proposed_structure`를 제거하고 `schema_version="2"`를 사용한다. 부적격 또는 한도 초과 시 `decline`, 그 밖의 정책 회부 시 `refer`만 허용한다. 적격이라도 근거로 판단을 보류할 수 있다. 새 금액 권고가 들어간 자유서술은 검토에서 거른다.
- [ ] `src/evals/fake.py`와 관련 평가·오케스트레이터 테스트 픽스처를 새 계약에 맞춘다. 정상 사례·거절·재시도·의견 없음 경로가 통과하는지 확인한다.

### 2. 심사 콘솔과 도우미

- [ ] `tests/console/test_opinion.py`, `test_officer_chat.py`, `test_ui.py`에서 신규 권고의 세 문구 및 구버전 금액 미노출을 검증한다. 예: 과거 조건부 제안의 본문에 있는 ‘300만 원 제안’이 현행 의견과 채팅 문맥에 없어야 한다.
- [ ] `src/console/opinion.py`, `src/console/officer_chat.py`, `src/agents/officer_chat.py`, 한영 템플릿·카탈로그 및 목록·요약에 같은 의미를 적용한다. 신규 화면에는 `전액 승인 권고`, `판단 보류`, `전액 승인 비권고`와 근거·확인 사항을 제공한다. 구버전은 별도 표기하며 감사 원본을 변경하지 않는다.
- [ ] `src/consolepreview/fixtures.py`, `src/agents/pretranslate.py` 등 직접 소비 경로를 확인한다. 평가·콘솔 테스트와 전체 테스트를 실행하고, 기존 문서 링크 실패와 새 회귀를 구분한다.
