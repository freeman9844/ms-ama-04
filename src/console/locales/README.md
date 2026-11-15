# 콘솔 화면 번역

화면 문구는 한국어 원문을 키로 쓰고, 영문은 `en/*.json`(원문 → 영문)에 둔다. 파일은 화면 영역별로 나눈다.
같은 원문이 여러 파일에 있으면 번역이 같아야 한다(다르면 앱이 시작하지 않는다).

- 템플릿: `{{ _("원문") }}`, 값은 `{{ _("%(n)s건", n=count) }}`. 템플릿의 `_()` 원문에서 리터럴 `%`는 `%%`로 쓴다.
- Python: `from console.i18n import _` 후 `_("원문")`, 값은 `_("%(n)s건", n=count)`. 값을 넘길 때만 `%%`가 필요하다.
- 상태 코드 → 표시명 사전(`*_LABELS`)은 한국어로 두고, 보여줄 때 `i18n.label(사전, 코드)` 또는 필터로 번역한다.
- 같은 원문이 문맥마다 다른 영어가 되면 `pgettext("문맥", "원문")`을 쓰고 키는 `"문맥|원문"`이다. 예: 결정 버튼 `pgettext("button", "승인")` = Approve, 상태 `_("승인")` = Approved.
- 데이터 문구는 `console/translate.py`가 옮긴다. `data.json`은 원문 데이터 → 영문(셀러 상호·채널·업종·지역·업력 구간·정책 사유·고정 안내 문구)이고 같은 키가 화면 문구와 겹치면 영문이 같아야 한다.
- 템플릿에서 데이터는 `|dt`(카탈로그만, 차트·표 라벨), `|dl`(`상호 · 채널·채널 · 업종` 라벨), `|mt`(조회 순서 4단계: 카탈로그 → 저장 번역 → 메모리 캐시 → 대기, 점선 밑줄), `|mt_ref`(차주에게 보낸·차주가 쓴 원문을 두고 영문 참고를 덧붙임, 같은 4단계)를 쓴다. `|mt`는 HTML을 돌려주므로 속성 값 안에서는 쓰지 않는다.
  - 저장 번역은 오케스트레이터가 미리 만든 `translations`(owner 단위)다. 화면 문구를 그 건의 가명처리기로 다시 마스킹한 문구의 sha256이 키라서, 조회·메모리 캐시·`data-mt`가 모두 마스킹 문구를 쓴다. 토큰이 달라진 저장 번역은 무시한다.
  - 대기(`data-mt`)는 마스킹 문구를 싣고 브라우저(`static/mt.js`)가 번역을 받아, 페이지의 `script.mt-tokens`(토큰 → 화면 값) 지도로 토큰을 되돌린다. 저장 번역은 서버가 복원하고 감사자 화면의 토큰은 그대로 둔다.
  - 어떤 문구가 사전 번역 대상인지는 `src/agents/pretranslate.py`의 `ALLOWLIST`가 정한다. 새 `|mt`·`|mt_ref` 문구를 템플릿에 더하면 그 허용 경로도 맞춰야 한다(`tests/console/test_pretranslate_coverage.py`가 빠진 곳을 알려 준다).
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
