"""거래 분류(결정적 규칙). 거래 유형 코드와 적요 키워드만 쓴다."""

from enum import StrEnum

from shared.contracts import Transaction

RULES_VERSION = "classify-v1"


class Category(StrEnum):
    REVENUE = "revenue"
    TAX = "tax"
    SOCIAL_INSURANCE = "social_insurance"
    RENT = "rent"
    HIGH_COST_LENDER = "high_cost_lender"
    LOAN_REPAYMENT = "loan_repayment"
    PAYROLL = "payroll"
    OWNER_TRANSFER = "owner_transfer"
    LOAN_PROCEEDS = "loan_proceeds"
    UNCLASSIFIED_IN = "unclassified_in"
    UNCLASSIFIED_OUT = "unclassified_out"


# (유형 코드, 적요 키워드 또는 None, 분류). 위에서부터 처음 맞는 규칙을 쓴다.
# '대부'·'P2P'를 '대출상환'보다 먼저 본다(고금리 대부 상환이 일반 상환으로 섞이지 않게).
_RULES: tuple[tuple[str, str | None, Category], ...] = (
    ("CARD_SETTLE", None, Category.REVENUE),
    ("PG_SETTLE", None, Category.REVENUE),
    ("TAX", None, Category.TAX),
    ("AUTO_DEBIT", "국민건강보험", Category.SOCIAL_INSURANCE),
    ("AUTO_DEBIT", "임대료", Category.RENT),
    ("AUTO_DEBIT", "대부", Category.HIGH_COST_LENDER),
    ("AUTO_DEBIT", "P2P", Category.HIGH_COST_LENDER),
    ("AUTO_DEBIT", "대출상환", Category.LOAN_REPAYMENT),
    ("TRANSFER_OUT", "급여", Category.PAYROLL),
    ("TRANSFER_OUT", "개인이체", Category.OWNER_TRANSFER),
    ("TRANSFER_IN", "대출금", Category.LOAN_PROCEEDS),
    ("TRANSFER_IN", "대출실행", Category.LOAN_PROCEEDS),  # 자체 대출금 입금(사후 재생 1일차, 2026-11-11)
    ("TRANSFER_IN", "선정산", Category.LOAN_PROCEEDS),
)


def classify(txn: Transaction) -> Category:
    for code, keyword, category in _RULES:
        if txn.txn_type_code == code and (keyword is None or keyword in txn.description):
            return category
    return Category.UNCLASSIFIED_IN if txn.amount_krw > 0 else Category.UNCLASSIFIED_OUT
