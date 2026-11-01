from datetime import datetime

import pytest

from agents.notices import (
    CONSENT_NOTICE_AGENT,
    GRIEVANCE_AGENT,
    Draft,
    check_grievance_reply,
    check_notice,
    consent_facts,
    grievance_facts,
    korean_date,
    write_grievance_reply,
    write_notice,
)
from agents.runtime import AgentError, AgentRun
from journey.models import KST

EXPIRES = "2027-01-31T09:00:00+09:00"
CONSENT = {
    "consent_id": "c1",
    "purpose": "운전자금 대출 심사",
    "scopes": ["account", "tax", "pg"],
    "expires_at": EXPIRES,
}
PROOF = {
    "id": "c1",
    "reason": "revoked",
    "deleted_at": "2026-11-03T10:00:00+09:00",
    "items": [{}, {}, {}],
    "manifest": [{"name": "a.json"}, {"name": "b.json"}, {"name": "c.json"}, {"name": "d.json"}],
}


class FakeRunner:
    def __init__(self, draft=None, error=None):
        self.draft, self.error = draft, error
        self.calls, self.deleted = [], []

    def run(self, spec, input_text, handlers):
        self.calls.append((spec, input_text, handlers))
        if self.error:
            raise AgentError(self.error, "conv_err")
        return AgentRun(spec.name, "1", "conv_1", self.draft, [], 42, 0)

    def delete_container(self, container_id):
        return True

    def delete_conversation(self, conversation_id):
        self.deleted.append(conversation_id)
        return "deleted"


def renewal_facts():
    return consent_facts("renewal", CONSENT)


def good_renewal():
    return Draft(
        title="동의 만료 예정 안내",
        body="운전자금 대출 심사를 위한 계좌 거래내역, 세무 신고 내역, 카드·PG 정산 내역 제공 동의가 "
        "2027년 1월 31일에 끝납니다. 원하지 않으시면 차주 화면의 동의 관리에서 동의 철회를 누르세요.",
    )


def test_korean_date_uses_kst():
    assert korean_date("2027-01-30T16:00:00+00:00") == "2027년 1월 31일"
    assert korean_date(datetime(2027, 1, 31, 9, tzinfo=KST)) == "2027년 1월 31일"


def test_renewal_facts_have_labels_and_no_identifiers():
    facts = renewal_facts()
    assert facts["expires_on"] == "2027년 1월 31일"
    assert facts["scopes"] == ["계좌 거래내역", "세무 신고 내역", "카드·PG 정산 내역"]
    assert "applicant_id" not in facts and "consent_id" not in facts


def test_check_notice_requires_date_scopes_and_revoke_path():
    facts = renewal_facts()
    assert check_notice(facts, good_renewal()) == []
    missing = Draft(title="안내", body="곧 동의가 끝납니다. 동의 관리에서 동의 철회를 할 수 있습니다.")
    issues = check_notice(facts, missing)
    assert any("2027년 1월 31일" in i for i in issues) and any("세무 신고 내역" in i for i in issues)
    wrong_number = Draft(title="안내", body=good_renewal().body + " 수수료 30,000원이 부과됩니다.")
    assert any("30,000" in i for i in check_notice(facts, wrong_number))


def test_write_notice_uses_agent_and_deletes_conversation():
    runner = FakeRunner(good_renewal())
    notice = write_notice(renewal_facts(), runner=runner, model="gpt-5.4-mini")
    assert (notice.source, notice.title, notice.issues) == ("agent", "동의 만료 예정 안내", [])
    spec, text, handlers = runner.calls[0]
    assert spec.name == CONSENT_NOTICE_AGENT and spec.tools == () and handlers == {}
    assert "2027년 1월 31일" in text
    assert runner.deleted == ["conv_1"]
    assert notice.record["conversations"] == {"conv_1": "deleted"} and notice.record["model"] == "gpt-5.4-mini"


@pytest.mark.parametrize(
    "runner",
    [None, FakeRunner(error="model down"), FakeRunner(Draft(title="안내", body="동의가 곧 끝납니다."))],
)
def test_write_notice_falls_back_to_template(runner):
    facts = renewal_facts()
    notice = write_notice(facts, runner=runner, model="m")
    assert notice.source == "template"
    assert check_notice(facts, Draft(title=notice.title, body=notice.body)) == []
    if runner is None:
        assert notice.record == {"error": "agents_disabled"}
    elif runner.error:
        assert runner.deleted == ["conv_err"] and "model down" in notice.record["error"]
    else:
        assert notice.issues and runner.deleted == ["conv_1"]


def test_renewal_template_does_not_attach_particle_to_purpose():
    body = write_notice(renewal_facts(), runner=None, model="m").body
    assert f"{CONSENT['purpose']} 목적으로" in body


def test_deletion_check_ignores_digits_inside_proof_id():
    proof = {**PROOF, "id": "489ba880b39a4849a0b05c9f2557dfae"}
    facts = consent_facts("deletion", CONSENT, proof=proof)
    body = write_notice(facts, runner=None, model="m").body
    assert check_notice(facts, Draft(title="삭제 완료", body=body)) == []
    padded = Draft(title="삭제 완료", body=body + " 처리 수수료 15,000원은 없습니다.")
    assert any("15,000" in i for i in check_notice(facts, padded))


def test_deletion_notice_template_mentions_proof_reason_and_retained_record():
    facts = consent_facts("deletion", CONSENT, proof=PROOF)
    assert facts["reason"] == "동의 철회" and facts["deleted_items"] == 3 and facts["proof_id"] == "c1"
    assert facts["deleted_files"] == 4
    notice = write_notice(facts, runner=None, model="m")
    assert "c1" in notice.body and "동의 철회" in notice.body and "판단 기록" in notice.body
    assert "파일 4개" in notice.body and "3개 항목" in notice.body
    assert check_notice(facts, Draft(title="삭제 완료", body=notice.body)) == []
    assert check_notice(facts, Draft(title="삭제 완료", body="삭제했습니다.")) != []


APPLICATION = {
    "application_id": "app-1",
    "requested_amount_krw": 20_000_000,
    "policy": {
        "decision": "refer",
        "reason_codes": [
            {"code": "R201", "message": "부채상환비율 높음", "field": "dscr", "observed": 1.1, "threshold": 1.25}
        ],
    },
    "assessment": {
        "summary": "매출은 안정적이나 상환 여력이 기준에 못 미친다.",
        "counterfactuals": [
            {"metric_id": "M:dscr", "current": 1.1, "target": 1.3, "explanation": "DSCR이 1.3 이상이면 승인 가능"}
        ],
    },
    "officer_decision": {"decision": "decline", "officer_id": "o1", "amount_krw": None, "reason": "상환 여력 부족"},
}


def uphold():
    return {"outcome": "uphold", "reason": "제출 자료로 판단이 바뀌지 않음", "new_decision": None, "amount_krw": None}


def rereview():
    return {"outcome": "rereview", "reason": "추가 매출 확인", "new_decision": "conditional", "amount_krw": 15_000_000}


def test_grievance_facts_come_from_record_only():
    facts = grievance_facts(APPLICATION, uphold())
    assert facts["original_decision"] == "거절" and facts["outcome"] == "원결정 유지"
    assert facts["reasons"] == [{"message": "부채상환비율 높음", "observed": 1.1, "threshold": 1.25}]
    assert facts["improvements"] == [{"explanation": "DSCR이 1.3 이상이면 승인 가능", "current": 1.1, "target": 1.3}]
    assert "applicant_id" not in facts
    re = grievance_facts(APPLICATION, rereview())
    assert (re["outcome"], re["new_decision"], re["amount_krw"]) == ("재심사", "조건부 승인", 15_000_000)


def test_check_grievance_reply_numbers_and_keywords():
    facts = grievance_facts(APPLICATION, uphold())
    ok = Draft(title="이의제기 검토 결과", body="검토 결과 원결정(거절)을 유지합니다. DSCR 1.1이 기준 1.25보다 낮습니다.")
    assert check_grievance_reply(facts, ok) == []
    bad = Draft(title="이의제기 검토 결과", body="원결정을 유지합니다. DSCR 0.9가 기준 1.25보다 낮습니다.")
    assert any("0.9" in i for i in check_grievance_reply(facts, bad))
    no_keyword = Draft(title="결과", body="검토했습니다.")
    assert any("유지" in i for i in check_grievance_reply(facts, no_keyword))
    re_facts = grievance_facts(APPLICATION, rereview())
    assert any("조건부 승인" in i for i in check_grievance_reply(re_facts, Draft(title="결과", body="재심사했습니다.")))


def test_write_grievance_reply_agent_and_template():
    facts = grievance_facts(APPLICATION, rereview())
    good = Draft(title="재심사 결과", body="재심사 결과 조건부 승인으로 판단했습니다. 한도는 15,000,000원입니다.")
    runner = FakeRunner(good)
    reply = write_grievance_reply(facts, runner=runner, model="m")
    assert reply.source == "agent" and runner.calls[0][0].name == GRIEVANCE_AGENT and runner.deleted == ["conv_1"]
    fallback = write_grievance_reply(facts, runner=None, model="m")
    assert fallback.source == "template" and check_grievance_reply(facts, Draft(title=fallback.title, body=fallback.body)) == []
    up = grievance_facts(APPLICATION, uphold())
    up_reply = write_grievance_reply(up, runner=None, model="m")
    assert "유지" in up_reply.body and "DSCR이 1.3 이상이면 승인 가능" in up_reply.body
