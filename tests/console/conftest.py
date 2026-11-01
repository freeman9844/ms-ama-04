from datetime import date

import pytest

from agents.data_assembly import build_pseudonymizer
from shared.contracts import ApplicantProfile, Transaction

KEY = b"console-test-pseudonym-key-0123456789"
PROFILE = ApplicantProfile(
    applicant_id="demo-cafe-001", business_name="빈스카페", business_reg_no="123-45-67890",
    representative_name="김대표", industry="cafe", region="서울", business_age_months=48,
)
TXNS = [
    Transaction(txn_id="t1", booked_on=date(2026, 9, 1), amount_krw=-2_500_000, balance_after_krw=10_000_000,
                description="급여 이직원", txn_type_code="TRANSFER_OUT"),
    Transaction(txn_id="t2", booked_on=date(2026, 9, 2), amount_krw=-1_000_000, balance_after_krw=9_000_000,
                description="김대표 개인이체", txn_type_code="TRANSFER_OUT"),
    Transaction(txn_id="t3", booked_on=date(2026, 9, 3), amount_krw=5_000_000, balance_after_krw=14_000_000,
                description="카드매출 정산", txn_type_code="CARD_SETTLE"),
]


def mask(text: str) -> str:
    return build_pseudonymizer(KEY, PROFILE, TXNS).mask(text)


class FakeRaw:
    def __init__(self, blobs: dict[tuple[str, str], object] | None = None):
        self.blobs = blobs if blobs is not None else {
            ("consent-c-1", "profile.json"): PROFILE.model_dump(mode="json"),
            ("consent-c-1", "transactions.json"): [t.model_dump(mode="json") for t in TXNS],
        }
        self.reads: list[tuple[str, str]] = []

    def get_json(self, container: str, name: str):
        self.reads.append((container, name))
        return self.blobs.get((container, name))


def make_doc(status: str = "awaiting_officer", **overrides) -> dict:
    doc = {
        "id": "app-1",
        "application_id": "app-1",
        "applicant_id": "demo-cafe-001",
        "requested_amount_krw": 30_000_000,
        "requested_tenor_months": 12,
        "status": status,
        "received_at": "2026-10-31T09:00:00+09:00",
        "updated_at": "2026-10-31T09:02:00+09:00",
        "timeline": [
            {"status": "received", "at": "2026-10-31T09:00:00+09:00"},
            {"status": "awaiting_officer", "at": "2026-10-31T09:02:00+09:00"},
        ],
        "consent": {"consent_id": "c-1", "scopes": ["account", "tax", "pg"], "expires_at": "2027-01-29T09:00:00+09:00"},
        "underwriting": {
            "features": {"dscr": 1.8, "avg_monthly_inflow_krw": 42_000_000, "industry": "cafe"},
            "signals": {"owner_draw_ratio": 0.1},
            "flags": [],
            "monthly": [{"month": "2026-09", "revenue": 40_000_000, "operating_out": 30_000_000,
                         "debt_service": 1_000_000, "owner_draw": 1_000_000}],
            "classification": None,
            "category_overrides": {},
        },
        "policy": {
            "application_id": "app-1", "decision": "eligible", "max_amount_krw": 20_000_000, "rate_band": "B",
            "reason_codes": [{"code": "R101", "message": "DSCR 충족", "field": "dscr", "observed": 1.8,
                              "threshold": 1.25}],
            "rules_version": "2026.11-v1",
        },
        "assessment": {
            "application_id": "app-1",
            "recommendation": "approve",
            "summary": mask("빈스카페(대표 김대표)는 계절성이 있다"),
            "claims": [
                {"text": mask("김대표 개인이체가 월 100만원이다"), "metric_ids": ["M:dscr", "M:unknown"],
                 "txn_ids": ["t2"]},
                {"text": "DSCR 1.8로 정책 기준을 넘는다", "metric_ids": ["S:2026-09:index", "P:R101:threshold"],
                 "txn_ids": []},
            ],
            "proposed_structure": {"amount_krw": 20_000_000, "tenor_months": 12, "schedule": "level",
                                   "reduced_payment_months": [], "rationale": "한도 내"},
            "counterfactuals": [],
            "review": {"passed": True, "rounds": 1, "issues": []},
            "models": {"analyst": "gpt-5.4", "reviewer": "gpt-5.4-mini"},
        },
        "flags": [],
        "sla": {"hours": 24, "deadline": "2026-11-01T09:00:00+09:00", "escalated": False},
        "decision_attempts": [],
        "officer_decision": None,
        "error": None,
    }
    doc.update(overrides)
    return doc


@pytest.fixture
def raw():
    return FakeRaw()


@pytest.fixture
def key():
    return KEY


@pytest.fixture(name="make_doc")
def make_doc_fixture():
    return make_doc


@pytest.fixture(name="fake_raw")
def fake_raw_fixture():
    return FakeRaw
