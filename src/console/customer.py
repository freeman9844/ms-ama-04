"""고객 신청 페이지의 동의 문구와 진행 상태 표시. 내부 근거(지표·정책 코드·에이전트 서술)는 내보내지 않는다."""

from dataclasses import dataclass
from typing import Any

from console.i18n import _
from shared.contracts import MAX_TENOR_MONTHS, MIN_TENOR_MONTHS

MIN_AMOUNT_KRW = 500_000
MAX_AMOUNT_KRW = 10_000_000
CONSENT_FIELDS = ("consent_account", "consent_tax", "consent_pg")
# 항목명·설명과 APPLY_ERRORS는 한국어 원문이다. 화면(apply.html)에서 _()로 번역한다.
CONSENT_ITEMS = (
    ("consent_account", "은행 계좌 거래내역", "입출금 내역과 잔액(최근 12개월)"),
    ("consent_tax", "홈택스 세금 신고 자료", "부가가치세·종합소득세 신고 내역"),
    ("consent_pg", "PG·마켓플레이스 정산 내역", "스마트스토어·쿠팡 등 채널별 매출과 정산"),
)
APPLY_ERRORS = {
    "consent_required": "데이터 3종에 모두 동의해야 신청할 수 있습니다.",
    "applicant_invalid": "신청 사업자를 목록에서 골라야 합니다.",
    "amount_invalid": "신청 금액은 50만~1,000만 원 사이로 입력해야 합니다.",
    "tenor_invalid": f"대출 기간은 {MIN_TENOR_MONTHS}~{MAX_TENOR_MONTHS}개월로 입력해야 합니다.",
}

_REVIEWING = frozenset(
    {"received", "consent_granted", "data_assembled", "data_incomplete", "underwritten", "assessed"}
)
_OFFICER = frozenset({"awaiting_officer", "escalated"})
_ENDED = frozenset({"closed_no_decision", "failed"})


@dataclass(frozen=True)
class CustomerStatus:
    label: str
    tone: str
    message: str
    refresh: bool
    amount_krw: int | None = None


def customer_status(doc: dict[str, Any] | None) -> CustomerStatus:
    if doc is None:
        return CustomerStatus(_("접수 대기"), "pending", _("신청을 접수하고 있습니다. 잠시 후 자동으로 새로 고칩니다."), True)
    status = doc.get("status")
    if status in _OFFICER:
        return CustomerStatus(_("심사역 검토 중"), "pending", _("심사역이 신청 내용을 검토하고 있습니다."), True)
    if status in {"approved", "conditionally_approved"}:
        decision = doc.get("officer_decision") or {}
        amount = decision.get("amount_krw") or doc.get("requested_amount_krw")
        return CustomerStatus(_("승인"), "good", _("대출이 승인되었습니다."), False, amount)
    if status == "declined":
        return CustomerStatus(_("거절"), "bad", _("이번 신청은 승인되지 않았습니다. 이의제기는 고객센터로 문의해 주세요."), False)
    if status == "withdrawn":
        return CustomerStatus(_("동의 철회로 종료"), "bad", _("데이터 이용 동의가 철회되어 심사를 마쳤습니다."), False)
    if status in _ENDED:
        return CustomerStatus(_("처리 종료"), "bad", _("신청 처리가 끝났습니다. 자세한 내용은 고객센터로 문의해 주세요."), False)
    if status in _REVIEWING or status is None:
        return CustomerStatus(_("심사 중"), "pending", _("제출하신 데이터로 심사를 진행하고 있습니다."), True)
    return CustomerStatus(_("처리 종료"), "bad", _("신청 처리가 끝났습니다. 자세한 내용은 고객센터로 문의해 주세요."), False)


def validate_apply(form: dict[str, str], applicants: tuple[str, ...]) -> str | None:
    """오류 코드를 돌려준다. 동의를 가장 먼저 본다."""
    if not all(form.get(f) == "on" for f in CONSENT_FIELDS):
        return "consent_required"
    if form.get("applicant_id", "").strip() not in applicants:
        return "applicant_invalid"
    amount = _digits(form.get("requested_amount_krw", ""))
    if amount is None or not MIN_AMOUNT_KRW <= amount <= MAX_AMOUNT_KRW:
        return "amount_invalid"
    tenor = _digits(form.get("requested_tenor_months", ""))
    if tenor is None or not MIN_TENOR_MONTHS <= tenor <= MAX_TENOR_MONTHS:
        return "tenor_invalid"
    return None


def _digits(text: str) -> int | None:
    cleaned = text.replace(",", "").strip()
    return int(cleaned) if cleaned.isascii() and cleaned.isdigit() else None
