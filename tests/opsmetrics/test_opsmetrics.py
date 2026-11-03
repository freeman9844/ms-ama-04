import json
from datetime import UTC, datetime, timedelta

import pytest

from opsmetrics.cost import ai_cost, unit_economics
from opsmetrics.fairness import fairness, gap_pp_from_env
from opsmetrics.prices import load_prices
from opsmetrics.projection import archetype_of, project
from opsmetrics.slo import agent_success, latency, nearest_rank
from opsmetrics.summary import summarize

T0 = datetime(2026, 11, 2, 1, 0, tzinfo=UTC)
PRICES = {"m": {"input_per_1m": 1.0, "output_per_1m": 10.0}, "decision-1": {"input_per_1m": None, "output_per_1m": None}}


def doc(i=1, *, minutes=5, reached=True, submitted=True, arch="healthy", seg=None, rec="approve",
        decision="eligible", steps=None, officer=None, status="awaiting_officer"):
    sub = T0 + timedelta(minutes=i)
    timeline = [{"status": "received", "at": (sub + timedelta(seconds=30)).isoformat()}]
    if reached:
        timeline.append({"status": "awaiting_officer", "at": (sub + timedelta(minutes=minutes)).isoformat()})
    return {
        "id": f"r-{i:02d}", "application_id": f"r-{i:02d}", "applicant_id": f"load-{arch}-{i:02d}",
        "status": status, "submitted_at": sub.isoformat() if submitted else None,
        "received_at": (sub + timedelta(seconds=30)).isoformat(), "source": "load", "run_id": "r",
        "segment": seg or {"industry": "카페", "region": "서울", "business_age_band": "3년+"},
        "steps": steps if steps is not None else [], "timeline": timeline, "flags": [],
        "policy": {"decision": decision, "reason_codes": ["R104"], "max_amount_krw": 1} if decision else None,
        "assessment": {"recommendation": rec, "narrative": "원문"} if rec else None,
        "officer_decision": {"decision": officer} if officer else None,
        "assembly": {"secret": "x"},
    }


def step(name, outcome="ok", usage=None, agent=True):
    return {"step": name, "outcome": outcome, "agent": agent, "usage": usage or [], "duration_ms": 1}


def test_project_keeps_only_allowed_fields():
    p = project(doc(officer="approve"))
    assert set(p) == {"application_id", "applicant_id", "status", "submitted_at", "received_at", "source", "run_id",
                      "segment", "steps", "timeline", "flags", "policy", "assessment", "officer_decision"}
    assert p["policy"] == {"decision": "eligible", "reason_codes": ["R104"]}
    assert p["assessment"] == {"recommendation": "approve"} and p["officer_decision"] == "approve"
    assert "원문" not in json.dumps(p, ensure_ascii=False)


def test_archetype_of():
    assert archetype_of("load-stress-03") == "stress"
    assert archetype_of("demo-healthy-001") is None


def test_nearest_rank():
    assert nearest_rank([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95) == 10
    assert nearest_rank([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 50) == 5
    assert nearest_rank([], 95) is None


def test_latency_counts_unreached_and_received_only():
    docs = [doc(i, minutes=i) for i in range(1, 21)] + [doc(30, reached=False), doc(31, submitted=False, minutes=2)]
    r = latency([project(d) for d in docs])
    assert (r["n"], r["unreached"], r["received_only"], r["insufficient"]) == (21, 1, 1, False)
    assert r["p95_min"] == 19.0 and r["max_min"] == 20.0 and r["met"] is False


def test_latency_skips_items_without_any_start_time():
    p = project(doc(1, submitted=False))
    p["received_at"] = None
    r = latency([p, project(doc(2, minutes=4))])
    assert r["n"] == 1 and r["p50_min"] == 4.0


def test_latency_insufficient_samples():
    r = latency([project(doc(1))])
    assert r["n"] == 1 and r["insufficient"] is True


def test_agent_success_separates_degraded():
    steps = [step("assemble"), step("classify", "degraded"), step("assess", "failed"), step("policy", agent=False)]
    r = agent_success([project(doc(steps=steps))])
    assert (r["total"], r["ok"], r["degraded"], r["failed"]) == (3, 1, 1, 1)
    assert r["rate"] == pytest.approx(1 / 3) and r["degraded_rate"] == pytest.approx(1 / 3) and r["met"] is False
    assert r["by_step"]["assess"] == {"ok": 0, "degraded": 0, "failed": 1}


def test_ai_cost_sums_priced_and_counts_unpriced():
    usage = [{"model": "m", "input_tokens": 1_000_000, "output_tokens": 100_000, "calls": 2},
             {"model": "decision-1", "input_tokens": 0, "output_tokens": 0, "calls": 3}]
    docs = [project(doc(1, steps=[step("assemble", usage=usage)])), project(doc(2, steps=[]))]
    r = ai_cost(docs, PRICES)
    assert r["by_model"]["m"]["cost_usd"] == pytest.approx(2.0)
    assert r["by_model"]["decision-1"]["cost_usd"] is None
    assert r["total_usd"] == pytest.approx(2.0) and r["per_application_usd"] == pytest.approx(1.0)
    assert r["unpriced_calls"] == 3 and r["applications"] == 2


def test_ai_cost_treats_usage_without_model_as_unpriced():
    usage = [{"model": "m", "input_tokens": 1_000_000, "output_tokens": 0, "calls": 1},
             {"input_tokens": 5, "output_tokens": 5, "calls": 4}]
    r = ai_cost([project(doc(1, steps=[step("assemble", usage=usage)]))], PRICES)
    assert r["total_usd"] == pytest.approx(1.0) and r["unpriced_calls"] == 4


def test_unit_economics():
    rows = unit_economics(0.05, 300.0, volumes=(100, 1000))
    assert rows[0] == {"volume": 100, "ai_usd": 0.05, "fixed_usd": 3.0, "total_usd": 3.05,
                       "vs_manual": pytest.approx(3.05 / 3500), "vs_ticket": pytest.approx(3.05 / 6000)}
    assert unit_economics(0.05, None, volumes=(100,))[0]["fixed_usd"] is None


def _grid(rate_by_industry):
    docs, i = [], 0
    for industry, approve in rate_by_industry.items():
        for k in range(6):
            i += 1
            docs.append(doc(i, arch=("healthy", "stress")[k % 2],
                            seg={"industry": industry, "region": "서울", "business_age_band": "<1년" if k < 3 else "3년+"},
                            rec="approve" if k < approve else "decline"))
    return [project(d) for d in docs]


def test_fairness_gap_and_alert():
    r = fairness(_grid({"카페": 6, "한식": 2}))
    ind = r["axes"]["industry"]
    assert ind["values"]["카페"]["approve_rate"] == 1.0 and ind["values"]["한식"]["approve_rate"] == pytest.approx(2 / 6)
    assert ind["gap_pp"] == pytest.approx(66.7, abs=0.1) and ind["alert"] is True
    assert r["alerts"] == ["industry"]


def test_recommendation_breakdown_includes_refer():
    recs = ["approve", "conditional", "refer", "refer", "decline"]
    values = fairness([project(doc(i, rec=r)) for i, r in enumerate(recs, 1)])["axes"]["industry"]["values"]
    cafe = values["카페"]
    assert cafe["recommendations"] == {"approve": 1, "conditional": 1, "refer": 2, "decline": 1}
    assert sum(cafe["recommendations"].values()) == cafe["assessed"]


def test_small_groups_are_excluded_from_gap():
    docs = _grid({"카페": 6}) + [project(doc(99, seg={"industry": "한식", "region": "서울", "business_age_band": "3년+"},
                                             rec="decline"))]
    ind = fairness(docs)["axes"]["industry"]
    assert ind["values"]["한식"]["insufficient"] is True and ind["gap_pp"] == 0.0 and ind["alert"] is False


def test_age_axis_is_policy_explained_and_never_alerts():
    age = fairness(_grid({"카페": 3, "한식": 3}))["axes"]["business_age_band"]
    assert age["policy_explained"] is True and age["alert"] is False


def test_controlled_gap_is_per_archetype():
    ind = fairness(_grid({"카페": 6, "한식": 0}))["axes"]["industry"]
    assert set(ind["controlled"]["by_archetype"]) == {"healthy", "stress"}
    assert ind["controlled"]["max_pp"] == pytest.approx(100.0)


def test_summary_has_basis_and_sections():
    s = summarize([project(doc())], PRICES, fixed_monthly_usd=None, fixed_source="측정 불가(권한)")
    assert s["basis"] == "합성 데이터 기준"
    assert {"latency", "agent_success", "ai_cost", "unit_economics", "fairness", "fixed"} <= set(s)


def test_load_prices_skips_meta():
    prices = load_prices()
    assert "_source" not in prices and "gpt-5.4-mini" in prices


@pytest.mark.parametrize(("raw", "expected"), [
    (None, 20.0), ("", 20.0), ("35", 35.0), (" 12.5 ", 12.5),
    ("abc", 20.0), ("-1", 20.0), ("nan", 20.0), ("inf", 20.0),
])
def test_gap_pp_from_env_falls_back_on_invalid(raw, expected):
    env = {} if raw is None else {"FAIRNESS_GAP_PP": raw}
    assert gap_pp_from_env(env) == expected
