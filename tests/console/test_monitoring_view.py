"""경보 원인 칩(주원인·부원인)과 주간 정산(PG 원장) 표(P8). tests/console/test_monitoring.py와 같은 principal·
FakeMonitoring 패턴을 쓰되, 원장 필드·primary_cause가 있는 재생/경보와 옛(원장 없음) 재생/경보를 따로 둔다.
"""

import base64
import json
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from console.app import ConsoleDeps, create_app
from console.auth import CREDIT_OFFICER
from console.monitoring import loan_view
from journey.models import KST

OID = "http://schemas.microsoft.com/identity/claims/objectidentifier"
NOW = datetime(2026, 11, 2, 9, 0, tzinfo=KST)
KEY = b"console-test-pseudonym-key-0123456789"

BASELINE = {
    "avg_weekly_revenue": 9_000_000, "revenue_volatility": 0.12, "cash_buffer_days": 30.0,
    "operating_daily_krw": 900_000, "tax_bank_match_ratio": 0.97,
    "seasonal_index": {f"{m:02d}": 1.0 for m in range(1, 13)},
}
LEDGER_BASELINE = {
    "weekly_orders": 5_000_000, "return_rate": 0.04, "deduction_rate": 0.08, "weekly_settle_net": 4_000_000,
    "platform_weekly_net": {"naver": 4_000_000}, "platform_weekly_orders": {"naver": 5_000_000},
    "prior_advance": False, "month_index": {f"{m:02d}": 1.0 for m in range(1, 13)},
}


def principal(*roles, oid):
    claims = [{"typ": OID, "val": oid}, {"typ": "name", "val": oid}] + [{"typ": "roles", "val": r} for r in roles]
    body = {"auth_typ": "aad", "name_typ": "name", "role_typ": "roles", "claims": claims}
    return {"X-MS-CLIENT-PRINCIPAL": base64.b64encode(json.dumps(body).encode()).decode()}


OFFICER = principal(CREDIT_OFFICER, oid="oid-officer")


def _row(week: int, **ledger) -> dict:
    start = date(2026, 9, 1) + timedelta(weeks=week - 1)
    row = {
        "week": week, "start": start.isoformat(), "end": (start + timedelta(days=6)).isoformat(),
        "revenue": 5_000_000, "revenue_ratio": 0.556,
        "operating_out": 2_500_000, "debt_service": 1_000_000, "high_cost_lender": 0, "high_cost_count": 0,
        "loan_proceeds": 0, "unclassified_in": 0, "end_balance": 14_000_000, "buffer_days": 15.6,
    }
    row.update(ledger)
    return row


LEDGER_ROWS = [
    _row(1, order_gross=5_000_000, returns=200_000, held=0, deductions=400_000, settle_net=4_400_000,
         return_rate=0.04, deduction_rate=0.08, orders_vs_expected=1.0),
    _row(2, order_gross=4_800_000, returns=250_000, held=0, deductions=420_000, settle_net=4_130_000,
         return_rate=0.05, deduction_rate=0.09, orders_vs_expected=0.95),
    _row(3, order_gross=4_500_000, returns=300_000, held=1_500_000, deductions=450_000, settle_net=2_250_000,
         return_rate=0.07, deduction_rate=0.10, orders_vs_expected=0.85),
]


def _weeks(rows: list[dict]) -> list[dict]:
    return [{"week": r["week"], "row": r, "triggers": [], "new_triggers": [], "classification": None,
             "investigation": None, "alert_id": None} for r in rows]


def make_loan_with_ledger() -> dict:
    monitoring = {
        "replay_id": "rp-l1", "consent_id": "m-l1", "flow_start": "2026-09-01", "started_at": NOW.isoformat(),
        "mode": "inline", "status": "replaying", "processed_week": 3, "seen_triggers": ["settlement_hold"],
        "overrides": {}, "weeks": _weeks(LEDGER_ROWS), "alert_ids": ["ew-rp-l1-w03"], "case_ids": [], "error": None,
        "pg": "ok", "ledger_baseline": LEDGER_BASELINE,
    }
    return {
        "id": "app-ledger", "loan_id": "app-ledger", "application_id": "app-ledger",
        "applicant_id": "seller-dailylook-01", "industry": "카페", "underwriting_consent_id": "c-1",
        "decision": "approve", "amount_krw": 20_000_000, "tenor_months": 12,
        "approved_at": "2026-10-31T09:00:00+09:00", "baseline": BASELINE, "monitoring": monitoring,
        "repayment": None, "updated_at": NOW.isoformat(),
    }


def make_alert_with_cause() -> dict:
    return {
        "id": "ew-rp-l1-w03", "schema_version": "1", "alert_id": "ew-rp-l1-w03", "loan_id": "app-ledger", "week": 3,
        "as_of": "2026-09-21", "severity": "high", "summary": "3주차 정산 보류가 길어지고 고금리 상환이 겹쳤다",
        "signals": [{"kind": "settlement_hold", "text": "3주차 보류액이 기준의 30%를 넘는다",
                     "metric_ids": ["W:3:held"], "txn_ids": []}],
        "recommended_action": "hardship_review", "triggers": ["settlement_hold", "new_debt"],
        "models": {"investigator": "gpt-5.4"}, "replay_id": "rp-l1", "status": "open",
        "created_at": NOW.isoformat(), "event_error": None, "collections": None, "restructure": None,
        "primary_cause": "settlement_hold", "secondary_causes": ["high_cost_borrowing"],
    }


def make_legacy_loan() -> dict:
    """원장 필드·ledger_baseline이 없는 옛 재생 문서(2026-11-11 이전)."""
    row = {
        "week": 1, "start": "2026-08-01", "end": "2026-08-07", "revenue": 4_000_000, "revenue_ratio": 0.444,
        "operating_out": 2_000_000, "debt_service": 1_000_000, "high_cost_lender": 0, "high_cost_count": 0,
        "loan_proceeds": 0, "unclassified_in": 0, "end_balance": 10_000_000, "buffer_days": 20.0,
    }
    monitoring = {
        "replay_id": "rp-legacy", "consent_id": "m-legacy", "flow_start": "2026-08-01", "started_at": NOW.isoformat(),
        "mode": "inline", "status": "completed", "processed_week": 1, "seen_triggers": ["inflow_drop"],
        "overrides": {}, "weeks": [{"week": 1, "row": row, "triggers": ["inflow_drop"],
                                     "new_triggers": ["inflow_drop"], "classification": None,
                                     "investigation": {"decision": "alert", "reason": "r", "record": {}},
                                     "alert_id": "ew-rp-legacy-w01"}],
        "alert_ids": ["ew-rp-legacy-w01"], "case_ids": [], "error": None,
        # 옛 재생 문서에는 pg·ledger_baseline 키가 아예 없었다.
    }
    return {
        "id": "app-legacy", "loan_id": "app-legacy", "application_id": "app-legacy",
        "applicant_id": "seller-dailylook-01", "industry": "카페", "underwriting_consent_id": "c-1",
        "decision": "approve", "amount_krw": 10_000_000, "tenor_months": 12,
        "approved_at": "2026-09-30T09:00:00+09:00", "baseline": BASELINE, "monitoring": monitoring,
        "repayment": None, "updated_at": NOW.isoformat(),
    }


def make_legacy_alert() -> dict:
    """primary_cause·secondary_causes 필드가 아예 없는 옛 경보 문서."""
    return {
        "id": "ew-rp-legacy-w01", "schema_version": "1", "alert_id": "ew-rp-legacy-w01", "loan_id": "app-legacy",
        "week": 1, "as_of": "2026-08-07", "severity": "medium", "summary": "1주차 매출이 기준보다 크게 줄었다",
        "signals": [{"kind": "inflow_drop", "text": "1주차 매출 급감", "metric_ids": ["W:1:revenue"], "txn_ids": []}],
        "recommended_action": "monitor", "triggers": ["inflow_drop"], "models": {"investigator": "gpt-5.4"},
        "replay_id": "rp-legacy", "status": "open", "created_at": NOW.isoformat(), "event_error": None,
        "collections": None, "restructure": None,
    }


@pytest.fixture
def loan_with_ledger():
    return make_loan_with_ledger()


@pytest.fixture
def alert_with_cause():
    return make_alert_with_cause()


@pytest.fixture
def legacy_loan():
    return make_legacy_loan()


@pytest.fixture
def legacy_alert():
    return make_legacy_alert()


class FakeMonitoring:
    def __init__(self, loans, alerts):
        self.loans = {d["id"]: d for d in loans}
        self.alerts = alerts

    def list_loans(self, limit):
        return list(self.loans.values())[:limit]

    def get_loan(self, loan_id):
        return self.loans.get(loan_id)

    def list_alerts(self, loan_id, replay_id):
        return [a for a in self.alerts if a["loan_id"] == loan_id and a["replay_id"] == replay_id]


class FakeRaw:
    def get_json(self, container, name):
        return None


class FakeQueue:
    def send(self, queue, message):
        pass


@pytest.fixture
def client(loan_with_ledger, alert_with_cause, legacy_loan, legacy_alert):
    store = FakeMonitoring([loan_with_ledger, legacy_loan], [alert_with_cause, legacy_alert])
    deps = ConsoleDeps(store=None, raw=FakeRaw(), queue=FakeQueue(), pseudonym_key=KEY, clock=lambda: NOW,
                       monitoring=store)
    return TestClient(create_app(deps), follow_redirects=False)


def test_alert_cause_chip(loan_with_ledger, alert_with_cause):
    v = loan_view(loan_with_ledger, [alert_with_cause], None, KEY, restore=False)
    assert v["alerts"][0]["cause_label"] == "정산 보류" and v["alerts"][0]["secondary_labels"] == ["고금리 차입"]
    assert v["ledger_rows"][2]["held"] > 0 and v["ledger_note"] is None


def test_legacy_alert_and_replay_render(client, legacy_loan, legacy_alert):
    html = client.get(f"/loans/{legacy_loan['id']}", headers=OFFICER).text
    assert "주원인: —" in html and "정산 원장 없음" in html


def test_ledger_table_renders(client, loan_with_ledger):
    html = client.get(f"/loans/{loan_with_ledger['id']}", headers=OFFICER).text
    assert "주간 정산(PG 원장)" in html and "보류" in html
