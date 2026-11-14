# 콘솔 화면 번역

화면 문구는 한국어 원문을 키로 쓰고, 영문은 `en/*.json`(원문 → 영문)에 둔다. 파일은 화면 영역별로 나눈다.
같은 원문이 여러 파일에 있으면 번역이 같아야 한다(다르면 앱이 시작하지 않는다).

- 템플릿: `{{ _("원문") }}`, 값은 `{{ _("%(n)s건", n=count) }}`. 템플릿의 `_()` 원문에서 리터럴 `%`는 `%%`로 쓴다.
- Python: `from console.i18n import _` 후 `_("원문")`, 값은 `_("%(n)s건", n=count)`. 값을 넘길 때만 `%%`가 필요하다.
- 상태 코드 → 표시명 사전(`*_LABELS`)은 한국어로 두고, 보여줄 때 `i18n.label(사전, 코드)` 또는 필터로 번역한다.
- 같은 원문이 문맥마다 다른 영어가 되면 `pgettext("문맥", "원문")`을 쓰고 키는 `"문맥|원문"`이다. 예: 결정 버튼 `pgettext("button", "승인")` = Approve, 상태 `_("승인")` = Approved.
- LLM 출력, 고객에게 보내는 승인 문구(`SCRIPTS`), 저장된 데이터(셀러 상호 등)는 번역하지 않는다.
- 언어는 `?lang=en|ko` → `lang` 쿠키 → `Accept-Language` 순으로 정한다.

## 용어집

| 한국어 | English |
|---|---|
| 심사 콘솔 | Underwriting Console |
| 심사역 | credit officer (짧게 officer) |
| 신청 | application |
| 사후 모니터링 | post-loan monitoring |
| 이의제기 | grievance |
| 차주 | borrower |
| 셀러 | seller |
| 운전자금 대출 | working-capital loan |
| 상환 / 상환일 / 회차 | repayment / due date / installment |
| 미납 / 연체 | missed payment / delinquency |
| 추심 | collections |
| 조기경보 / 경보 | early warning / alert |
| 재조정 | restructuring |
| 분할납부 / 상환일 이동 | installment split / due-date move |
| 정산 | settlement (payout) |
| 정책 엔진 / 정책 한도 | policy engine / policy limit |
| 승인 / 거절 / 반려 | approve / decline / reject |
| 예외 승인 | override approval |
| 동의 / 철회 / 만료 | consent / revoke(d) / expired |
| 가명 / 가명처리 / 복원 | pseudonym / pseudonymization / restore |
| 재심사 / 원결정 유지 | re-review / uphold original decision |
| 심사 의견 | underwriting opinion |
| 근거 | evidence |
| 상환능력 | repayment capacity |
| 현금흐름 | cash flow |
| 매출 / 운영비 / 대표자 인출 | revenue / operating costs / owner draw |
| 재생(사후 시나리오) | replay |
| 제안 / 발송 / 보류 / 건너뜀 | proposal / sent / deferred / skipped |
| 업종 / 지역 / 업력 | industry / region / business age |
| 공정성 | fairness |
| 금액 `N원` | `₩N` (`krw` 필터가 처리) |
| 금액 `N만 원` | 원 단위로 풀어 `₩N0,000`처럼 쓴다 |
