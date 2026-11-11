from datetime import date

import pytest

from agents.collections import message_issues
from agents.escalation import TEMPLATES, auto_contact, next_stage, plan_link, sim_now, stage_message


@pytest.mark.parametrize("sev, esc, expected", [
    (["high"], {}, 1),
    (["medium", "high"], {}, 1),
    (["high", "high"], {"stage1": {"status": "sent"}}, 2),
    (["high", "medium", "high"], {"stage1": {"status": "sent"}}, None),
    (["high", "high", "high"], {"stage1": {}, "stage2": {"status": "deferred"}}, None),
    (["high", "high"], {"stage1": {"status": "deferred"}}, 2),
    (["medium"], {}, None),
    (["high"], {"stage1": None, "stage2": None}, 1),
])
def test_next_stage(sev, esc, expected):
    assert next_stage(sev, esc) == expected


def test_templates_pass_message_policy():
    for stage, bal in ((1, 900_000), (1, None), (2, None)):
        _, text = stage_message(stage, due_on=date(2026, 12, 2), amount=1_170_000, balance=bal, link=plan_link("seller-x"))
        assert message_issues(text) == []
        assert "2026-12-02" in text


def test_every_template_passes_policy_and_uses_only_allowed_placeholders():
    values = {"due_on": "2026-12-02", "amount": "1,170,000원", "balance": "900,000원", "link": plan_link("seller-x")}
    assert set(TEMPLATES) == {"balance_warning", "balance_warning_plain", "early_reminder", "self_plan_offer"}
    for text in TEMPLATES.values():
        assert message_issues(text.format(**values)) == []
    assert "{balance}" not in TEMPLATES["early_reminder"] and "{link}" not in TEMPLATES["early_reminder"]


def test_stage_message_script_ids_and_balance():
    sid, text = stage_message(1, due_on=date(2026, 12, 2), amount=1_170_000, balance=900_000, link="x")
    assert sid == "balance_warning" and "900,000원" in text and "1,170,000원" in text
    sid, text = stage_message(1, due_on=date(2026, 12, 2), amount=1_170_000, balance=None, link="x")
    assert sid == "balance_warning" and text == TEMPLATES["balance_warning_plain"].format(
        due_on="2026-12-02", amount="1,170,000원")
    assert stage_message(2, due_on=date(2026, 12, 2), amount=1, balance=None, link="x")[0] == "self_plan_offer"


def test_self_plan_offer_has_link_and_options():
    _, text = stage_message(2, due_on=date(2026, 12, 2), amount=1, balance=None, link="/borrower/s#self-plan")
    assert "/borrower/s#self-plan" in text and "나눠" in text and "정산" in text


def test_plan_link():
    assert plan_link("seller-x") == "/borrower/seller-x#self-plan"


def test_sim_now_is_10am_kst():
    assert sim_now(date(2026, 12, 1)).isoformat() == "2026-12-01T10:00:00+09:00"


def test_auto_contact_uses_simulated_10am_even_at_night():
    c = auto_contact("rp-1-s1", "balance_warning", "t", [], date(2026, 12, 1))
    assert c["status"] == "sent" and c["at"].startswith("2026-12-01T10:00") and c["by"] == "early_warning"
    assert (c["ref"], c["on"], c["script_id"], c["text"], c["reason"]) == ("rp-1-s1", "2026-12-01", "balance_warning", "t", None)


def test_auto_contact_respects_weekly_limit():
    full = [{"on": "2026-12-01", "status": "sent"}] * 7
    c = auto_contact("r", "balance_warning", "t", full, date(2026, 12, 1))
    assert (c["status"], c["reason"]) == ("deferred", "weekly_limit")
