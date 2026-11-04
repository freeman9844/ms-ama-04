"""합성 거래의 정답 범주(평가 전용, API로 노출하지 않는다)."""

from enum import StrEnum


class TxnCategory(StrEnum):
    REVENUE = "revenue"
    OWNER_TRANSFER = "owner_transfer"
    LOAN_REPAYMENT = "loan_repayment"
    TAX = "tax"
    PAYROLL = "payroll"
    SUPPLIER = "supplier"
    RENT = "rent"
    SOCIAL_INSURANCE = "social_insurance"
    HIGH_COST_LENDER = "high_cost_lender"
    LOAN_PROCEEDS = "loan_proceeds"
    SHIPPING = "shipping"
    ADVERTISING = "advertising"
    CARD_PAYMENT = "card_payment"
    SOLUTION_FEE = "solution_fee"
