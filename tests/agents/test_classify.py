from datetime import date

import pytest

from agents.classify import RULES_VERSION, Category, classify
from mockapis.demo import build_demo_cases
from mockapis.synth import TxnCategory
from shared.contracts import Transaction


def _txn(amount: int, description: str, code: str) -> Transaction:
    return Transaction(
        txn_id="t-1",
        booked_on=date(2026, 10, 1),
        amount_krw=amount,
        balance_after_krw=1_000_000,
        description=description,
        txn_type_code=code,
    )


def test_matches_synthetic_labels_for_every_demo_case():
    # 합성 정답과 같아야 한다. 공급업체 이체는 규칙이 없으므로 unclassified_out이 정답이다.
    for case in build_demo_cases(date(2026, 11, 2)).values():
        for t in case.transactions:
            label = case.labels[t.txn_id]
            want = Category.UNCLASSIFIED_OUT if label is TxnCategory.SUPPLIER else Category(label.value)
            assert classify(t) == want, (t.description, t.txn_type_code)


@pytest.mark.parametrize(
    ("amount", "description", "code", "want"),
    [
        (500_000, "현금 입금", "TRANSFER_IN", Category.UNCLASSIFIED_IN),
        (-90_000, "통신비", "AUTO_DEBIT", Category.UNCLASSIFIED_OUT),
        (-350_000, "펀딩리P2P 상환", "AUTO_DEBIT", Category.HIGH_COST_LENDER),
        (-650_000, "기업은행 대출상환", "AUTO_DEBIT", Category.LOAN_REPAYMENT),
    ],
)
def test_rules_cover_edge_descriptions(amount, description, code, want):
    assert classify(_txn(amount, description, code)) == want


def test_rules_version_is_pinned():
    assert RULES_VERSION == "classify-v1"
