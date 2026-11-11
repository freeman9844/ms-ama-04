import json

import pytest
from pydantic import ValidationError

from journey.holds import HOLD_ORDER, OFFICER_HOLDS, held_status, pending_holds
from journey.models import (
    OFFICER_MESSAGE,
    CollectionsDecision,
    OfferExpire,
    PlanChoice,
    PlanDecision,
    RestructureDecision,
)


def test_pending_holds_order_and_status():
    loan = {"repayment": {"offer": {"status": "open"}, "plan_change": {"status": "pending_officer"}}}
    case = {"proposal": {"status": "pending_officer"}, "restructure": {"status": "pending_officer"}}
    assert pending_holds(loan, case) == list(HOLD_ORDER)
    assert held_status(["customer_response"]) == "awaiting_customer"
    assert held_status(["customer_response", "plan_change"]) == "paused"
    assert held_status([]) == "awaiting_resume"
    assert pending_holds({}, None) == []


def test_pending_holds_ignore_decided_and_old_documents():
    loan = {"repayment": {"offer": {"status": "expired"}, "plan_change": {"status": "approved"}}}
    case = {"proposal": {"status": "approved"}, "restructure": {"status": "declined"}}
    assert pending_holds(loan, case) == []
    # 옛 문서: proposal·offer·plan_change 없음, repayment·restructure가 None
    assert pending_holds({"repayment": None}, {"restructure": None, "proposal": None}) == []
    assert pending_holds({}, {"restructure": {"status": "pending_officer"}}) == ["restructure"]
    assert all(held_status([h]) == "paused" for h in OFFICER_HOLDS)


@pytest.mark.parametrize(
    ("body", "model"),
    [
        ({"loan_id": "app-1", "alert_id": "rc-1", "decision": "approve", "officer_id": "o-1"}, RestructureDecision),
        ({"kind": "restructure", "loan_id": "app-1", "alert_id": "rc-1", "decision": "decline", "officer_id": "o-1"},
         RestructureDecision),
        ({"kind": "collections", "loan_id": "app-1", "case_id": "rc-1", "decision": "modify", "officer_id": "o-1",
          "text": "안내드립니다.", "include_hardship": False}, CollectionsDecision),
        ({"kind": "plan_choice", "loan_id": "app-1", "offer_id": "of-1", "option": "split"}, PlanChoice),
        ({"kind": "plan_change", "loan_id": "app-1", "change_id": "pc-1", "decision": "approve", "officer_id": "o-1"},
         PlanDecision),
        ({"kind": "offer_expire", "loan_id": "app-1", "offer_id": "of-1", "officer_id": "o-1"}, OfferExpire),
    ],
)
def test_officer_message_dispatches_on_kind(body, model):
    msg = OFFICER_MESSAGE.validate_json(json.dumps(body))
    assert type(msg) is model
    assert msg.kind == body.get("kind", "restructure")


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "bogus", "loan_id": "app-1", "alert_id": "rc-1", "decision": "approve", "officer_id": "o-1"},
        {"kind": "collections", "loan_id": "app-1", "case_id": "rc-1", "decision": "approve", "officer_id": "o-1",
         "extra": 1},
        {"kind": "collections", "loan_id": "app-1", "case_id": "rc-1", "decision": "approve", "officer_id": "o-1",
         "text": "가" * 301},
        {"kind": "collections", "loan_id": "app-1", "case_id": "rc-1", "decision": "approve", "officer_id": "o-1",
         "reason": "가" * 501},
        {"kind": "plan_choice", "loan_id": "app-1", "offer_id": "of-1", "option": "later"},
        {"kind": "plan_change", "loan_id": "app-1", "change_id": "pc-1", "decision": "modify", "officer_id": "o-1"},
        {"loan_id": "app-1", "case_id": "rc-1", "decision": "approve", "officer_id": "o-1"},  # kind 없으면 재조정
    ],
)
def test_officer_message_rejects_bad_bodies(body):
    with pytest.raises(ValidationError):
        OFFICER_MESSAGE.validate_json(json.dumps(body))
