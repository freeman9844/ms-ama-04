from datetime import UTC, datetime, timedelta

import pytest

from opsmetrics.insights import (
    decision_funnel,
    flag_counts,
    reason_code_counts,
    sla_status,
    step_latency,
    throughput,
)

T0 = datetime(2026, 11, 4, 1, 0, tzinfo=UTC)


def p(*, policy="eligible", rec="approve", officer=None, override=False, codes=(), flags=(), steps=(),
      received=None, reached=None, status="awaiting_officer", escalated=False):
    timeline = [{"status": "awaiting_officer", "at": reached.isoformat()}] if reached else []
    return {
        "status": status, "received_at": received.isoformat() if received else None, "submitted_at": None,
        "policy": {"decision": policy, "reason_codes": list(codes)} if policy else None,
        "assessment": {"recommendation": rec} if rec else None,
        "officer_decision": officer, "policy_override": override, "sla_escalated": escalated,
        "flags": list(flags), "steps": list(steps), "timeline": timeline,
    }


def test_funnel_distributions_keep_pipeline_order_and_count_missing():
    f = decision_funnel([p(policy="refer", rec="refer"), p(), p(policy=None, rec=None), p(officer="decline")])
    assert list(f["policy"].items()) == [("eligible", 2), ("refer", 1), ("none", 1)]
    assert list(f["recommendation"].items()) == [("approve", 2), ("refer", 1), ("none", 1)]
    assert f["officer"] == {"decline": 1, "none": 3}


def test_funnel_agreement_skips_refer_and_undecided():
    docs = [p(rec="approve", officer="approve"), p(rec="conditional", officer="approve"),
            p(rec="approve", officer="decline"), p(rec="decline", officer="decline"),
            p(rec="refer", officer="approve"), p(rec="approve")]
    a = decision_funnel(docs)["agreement"]
    assert (a["compared"], a["agreed"]) == (4, 3) and a["rate"] == pytest.approx(0.75)
    assert decision_funnel([p(rec="refer", officer="approve")])["agreement"]["rate"] is None


def test_funnel_override_rate_is_share_of_officer_approvals():
    docs = [p(policy="ineligible", officer="approve", override=True), p(officer="approve"),
            p(officer="decline", override=True)]
    o = decision_funnel(docs)["overrides"]
    assert o == {"count": 1, "approved": 2, "rate": 0.5}
    assert decision_funnel([])["overrides"]["rate"] is None


def test_step_latency_nearest_rank_in_pipeline_order_and_skips_bad_values():
    steps = lambda ms: [{"step": "assess", "duration_ms": ms}, {"step": "assemble", "duration_ms": ms / 10},  # noqa: E731
                        {"step": "zeta", "duration_ms": None}, {"step": "policy", "duration_ms": True}]
    r = step_latency([p(steps=steps(ms)) for ms in range(1000, 11000, 1000)])
    assert list(r) == ["assemble", "assess"]
    assert r["assess"] == {"n": 10, "p50_ms": 5000.0, "p95_ms": 10000.0}
    assert step_latency([]) == {}


def test_reason_codes_count_each_application_once_and_keep_message():
    docs = [p(codes=[{"code": "R101", "message": "대사율"}, {"code": "R101", "message": "대사율"}, "R104"]),
            p(codes=["R101"]), p(policy=None)]
    assert reason_code_counts(docs) == [{"key": "R101", "count": 2, "message": "대사율"},
                                        {"key": "R104", "count": 1, "message": None}]


def test_flag_counts_sorted_by_count_then_name():
    docs = [p(flags=["agent_fallback", "data_incomplete"]), p(flags=["data_incomplete", "data_incomplete"]), p()]
    assert flag_counts(docs) == [{"key": "data_incomplete", "count": 2}, {"key": "agent_fallback", "count": 1}]


def test_throughput_fills_empty_buckets_and_counts_reached():
    docs = [p(received=T0 + timedelta(minutes=3), reached=T0 + timedelta(minutes=12)),
            p(received=T0 + timedelta(minutes=31)), p(received=None)]
    t = throughput(docs)
    assert [(b["start"][11:16], b["received"], b["reached"]) for b in t["buckets"]] == [
        ("01:00", 1, 0), ("01:10", 0, 1), ("01:20", 0, 0), ("01:30", 1, 0)]
    assert t["earlier"] == {"received": 0, "reached": 0}


def test_throughput_keeps_last_buckets_and_reports_earlier():
    docs = [p(received=T0 - timedelta(days=2)), p(received=T0, reached=T0 + timedelta(minutes=5))]
    t = throughput(docs, max_buckets=3)
    assert [(b["received"], b["reached"]) for b in t["buckets"]] == [(0, 0), (0, 0), (1, 1)] and t["earlier"] == {"received": 1, "reached": 0}
    assert throughput([p()])["buckets"] == []


def test_throughput_ignores_unparseable_times():
    bad = p()
    bad["received_at"] = "not-a-time"
    assert throughput([bad])["buckets"] == []


def test_sla_status_counts_awaiting_and_escalated():
    docs = [p(status="awaiting_officer"), p(status="escalated"), p(status="approved", escalated=True),
            p(status="declined")]
    assert sla_status(docs) == {"awaiting": 2, "escalated": 2}
