import json

import pytest
from pydantic import ValidationError

from journey.metering import METER_KEY
from journey.models import StartRequest

# test_journey.py와 같은 상수(importlib 모드라 테스트 모듈끼리 import할 수 없다).
START = {
    "application_id": "app-1",
    "applicant_id": "demo-healthy-001",
    "requested_amount_krw": 20_000_000,
    "requested_tenor_months": 12,
}
CONSENT = {
    "consent_id": "c-1",
    "token": "secret-token",
    "scopes": ["account", "tax", "pg"],
    "expires_at": "2027-01-30T15:00:00+00:00",
}
COMPLETE = {"status": "complete", "missing_sources": [], "flags": [], "conversation_id": "conv_1"}
UNDERWRITING = {
    "features": {"application_id": "app-1", "dscr": 3.8},
    "monthly": [{"month": "2026-10", "revenue": 1}],
    "signals": {},
    "flags": ["high_cost_lender_detected"],
}
ELIGIBLE = {"application_id": "app-1", "decision": "eligible", "max_amount_krw": 20_000_000, "rate_band": "A"}
APPROVE = {"decision": "approve", "officer_id": "officer-1", "amount_krw": 20_000_000}
DECLINE = {"decision": "decline", "officer_id": "officer-1", "reason": "정책 부적격"}
ASSESSMENT = {"application_id": "app-1", "recommendation": "approve", "review": {"passed": True, "rounds": 1}}
ASSESSED = {"assessment": ASSESSMENT, "flags": [], "record": {"rounds": [{"round": 1}]}}


def acts(**overrides):
    base = {
        "request_consent": lambda _: CONSENT,
        "assemble_data": lambda _: COMPLETE,
        "underwrite": lambda _: UNDERWRITING,
        "evaluate_policy": lambda _: ELIGIBLE,
        "assess_credit": lambda _: ASSESSED,
        "register_monitoring": lambda p: {"loan_id": p["application_id"]},
    }
    return base | overrides


def boom(_):
    raise RuntimeError("down")


T = "2026-11-01T15:00:0{}+00:00"


def metered(result, step_outcome="ok", usage=None, substeps=None, s=1, e=2):
    meter = {"started_at": T.format(s), "ended_at": T.format(e), "outcome": step_outcome,
             "usage": usage or [], "substeps": substeps or []}
    return lambda _: {**result, METER_KEY: meter}


USAGE = [{"model": "gpt-5.4-mini", "input_tokens": 10, "output_tokens": 5, "calls": 2}]
FEATURES = {**UNDERWRITING["features"], "industry": "카페", "region": "서울 성동구", "business_age_months": 40}


def test_start_request_accepts_optional_meta():
    req = StartRequest.model_validate({**START, "submitted_at": "2026-11-01T14:59:00+00:00", "source": "load",
                                       "run_id": "load-20261102010000"})
    assert req.source == "load" and req.submitted_at.tzinfo is not None
    naive = StartRequest.model_validate({**START, "submitted_at": "2026-11-01T14:59:00"})
    assert naive.submitted_at.isoformat() == "2026-11-01T14:59:00+00:00"
    with pytest.raises(ValidationError):
        StartRequest.model_validate({**START, "run_id": "bad id!"})
    with pytest.raises(ValidationError):
        StartRequest.model_validate({**START, "source": "other"})


def test_steps_are_recorded_from_meter(run_journey):
    classify = {"step": "classify", "started_at": T.format(3), "ended_at": T.format(4), "outcome": "ok",
                "usage": [{"model": "decision-1", "input_tokens": 0, "output_tokens": 0, "calls": 2}]}
    a = acts(
        request_consent=metered(CONSENT),
        assemble_data=metered(COMPLETE, usage=USAGE),
        underwrite=metered({**UNDERWRITING, "features": FEATURES}, substeps=[classify], s=3, e=4),
        evaluate_policy=metered(ELIGIBLE),
        assess_credit=metered(ASSESSED, usage=USAGE),
    )
    _, ctx = run_journey(START, a, [("event", APPROVE)])
    doc = ctx.saved[-1]
    assert [s["step"] for s in doc["steps"]] == ["consent", "assemble", "underwrite", "classify", "policy", "assess"]
    assert doc["steps"][1]["usage"] == USAGE and doc["steps"][1]["agent"] is True
    assert doc["steps"][3]["duration_ms"] == 1000
    assert doc["segment"] == {"industry": "카페", "region": "서울", "business_age_band": "3년+"}


def test_meter_is_not_left_in_document(run_journey):
    a = acts(assemble_data=metered(COMPLETE), underwrite=metered(UNDERWRITING), evaluate_policy=metered(ELIGIBLE),
             assess_credit=metered(ASSESSED))
    _, ctx = run_journey(START, a, [("event", APPROVE)])
    assert all(METER_KEY not in json.dumps(d, ensure_ascii=False) for d in ctx.saved)
    assert ctx.saved[-1]["policy"] == ELIGIBLE


def test_missing_or_bad_meter_falls_back_to_result(run_journey):
    a = acts(assemble_data=lambda _: {**COMPLETE, METER_KEY: "garbage"})
    _, ctx = run_journey(START, a, [("event", APPROVE)])
    steps = {s["step"]: s for s in ctx.saved[-1]["steps"]}
    assert steps["assemble"]["outcome"] == "ok" and steps["assemble"]["usage"] == []
    assert steps["policy"]["outcome"] == "ok"  # 계량 없는 스텁도 결과로 기록
    assert ctx.saved[-1]["status"] == "approved"


def test_failed_activity_is_recorded_as_failed_step(run_journey):
    # 정책 실패는 거절만 허용(fail-closed)하므로 승인 시도 뒤 거절로 끝낸다.
    _, ctx = run_journey(START, acts(evaluate_policy=boom), [("event", APPROVE), ("event", DECLINE)])
    policy = [s for s in ctx.saved[-1]["steps"] if s["step"] == "policy"][0]
    assert policy["outcome"] == "failed" and policy["detail"] == "RuntimeError"


def test_meta_and_tags_are_passed_only_when_present(run_journey):
    _, plain = run_journey(START, acts(), [("event", APPROVE)])
    assert all("_tags" not in c.input for c in plain.calls if c.name != "save_document")
    doc = plain.saved[-1]
    assert (doc["submitted_at"], doc["source"], doc["run_id"], doc["segment"]) == (None, None, None, None)

    start = {**START, "source": "load", "run_id": "load-1", "submitted_at": "2026-11-01T14:59:00+00:00"}
    _, tagged = run_journey(start, acts(), [("event", APPROVE)])
    for name in ("request_consent", "assemble_data", "underwrite", "evaluate_policy", "assess_credit"):
        assert tagged.called(name)[0].input["_tags"] == {"source": "load", "run_id": "load-1"}
    assert "_tags" not in tagged.called("register_monitoring")[0].input
    doc = tagged.saved[-1]
    assert (doc["source"], doc["run_id"], doc["submitted_at"]) == ("load", "load-1", "2026-11-01T14:59:00+00:00")
