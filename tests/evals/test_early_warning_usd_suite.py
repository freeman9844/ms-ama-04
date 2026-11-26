"""USD 단계 판단 suite: 가짜 러너로 채점·폴백·금지 표현·예산을 잰다(네트워크 없음)."""

import re
from dataclasses import replace

from agents import usd_language
from agents.runtime import AgentRun
from agents.usd_early_warning import UsdStageOutput, fallback_stage
from evals.__main__ import SUITES, load_prices
from evals.metering import Meter
from evals.runner import EvalRunner
from evals.suites import early_warning_usd as suite
from evals.usd_scenes import scene_context

KEY = b"k" * 32
BASE = re.compile(r"Base stage: (\w+)")
CLEAN = "Settlement data moved first and bank data followed."


class StageAgents:
    """base 단계를 고르는(또는 pick으로 바꾸는) 가짜 에이전트. medium·high는 raise_alert를 먼저 올린다."""

    def __init__(self, pick=lambda base: base, why_now=CLEAN):
        self.pick, self.why_now = pick, why_now

    def run(self, spec, input_text, handlers):
        base = BASE.search(input_text).group(1)
        stage = self.pick(base)
        if stage in ("medium", "high"):
            baseline = handlers["get_baseline"]({})
            causes = baseline["cause_candidates"]
            cited = [m for s in baseline["signals"] for m in s["metric_ids"]]
            cited += [m for c in causes[:1] for m in c["evidence_ids"]]
            handlers["raise_alert"]({
                "severity": stage,
                "summary": "The weekly evidence changed.",
                "check_action": "Verify the evidence with the borrower.",
                "recommended_action": "officer_review",
                "signals": [{"kind": "other", "text": "The detector flagged a change.",
                             "metric_ids": list(dict.fromkeys(cited)), "txn_ids": []}],
                "primary_cause": causes[0]["cause"] if causes else "",
                "secondary_causes": [],
            })
        out = UsdStageOutput(stage=stage, why_now=self.why_now, outlook="Cash may stay tight for a few weeks.",
                             adjust_reason="", reason="Evidence supports the stage.")
        return AgentRun(spec.name, "1", "conv-1", out, [], 1, 1)

    def delete_container(self, container_id):
        return True


def _run(agents, budget=5.0, models=("m1",)):
    meter = Meter(load_prices(), budget)
    return suite.run(list(models), meter=meter, runner=EvalRunner(agents, meter), pseudonym_key=KEY)


def _rows(out, model="m1"):
    return {r["seller"]: r for r in out["cases"] if r["config"] == model}


def test_base_stage_runner_scores_full_marks():
    out = _run(StageAgents())
    m = out["configs"]["m1"]
    assert (m["valid_rate"], m["stage_match"], m["forbidden_rate"]) == (1.0, 1.0, 0.0)
    assert [(r["seller"], r["week"]) for r in out["cases"]] == [(s, w) for s, w, _ in suite.CASES]
    assert all(r["stage"] == r["base"] and r["agent"] == "agent" for r in out["cases"])
    assert out["failures"] == [] and not out["partial"]


def test_high_on_snackbox_is_rejected_and_falls_back():
    out = _run(StageAgents(pick=lambda base: "high"))
    row = _rows(out)["seller-snackbox-22"]
    assert (row["agent"], row["stage"], row["base"]) == ("fallback", "medium", "medium")
    assert row["match"] is True
    assert out["configs"]["m1"]["valid_rate"] < 1.0
    assert any(f["type"] == "agent_error" and f["case"] == "seller-snackbox-22-w03" for f in out["failures"])


def test_stage_outside_allowed_set_is_a_failure(monkeypatch):
    monkeypatch.setattr(suite, "investigate_usd", lambda ctx, **kw: replace(
        fallback_stage(ctx, "x"), stage="high", agent="agent"))
    out = _run(StageAgents())
    rows = _rows(out)
    assert rows["seller-tint-17"]["match"] is False and rows["seller-dailylook-01"]["match"] is True
    assert {f["case"] for f in out["failures"] if f["type"] == "ew_stage_mismatch"} == {
        f"{s}-w{w:02d}" for s, w, allowed in suite.CASES if "high" not in allowed}
    assert out["configs"]["m1"]["stage_match"] == round(1 / len(suite.CASES), 4)


def test_forbidden_recommendation_is_flagged_even_when_validator_rejects():
    out = _run(StageAgents(why_now="We recommend a rate change and a limit cut."))
    assert all(r["forbidden"] is True for r in out["cases"])
    assert out["configs"]["m1"]["forbidden_rate"] == 1.0
    assert {f["type"] for f in out["failures"]} >= {"ew_forbidden_text"}


def test_forbidden_terms():
    for text in ("Recall the loan now.", "Consider calling the loan.", "Cut the credit limit.",
                 "A limit reduction is advised.", "Declare default this week.", "Raise the interest rate.",
                 "A rate change is needed."):
        assert suite.forbidden(text), text
    for text in (CLEAN, "Marketplace cancel rate and seller rating moved first.", "Check the limit of the account."):
        assert not suite.forbidden(text), text


def test_budget_exhausted_gives_partial_result():
    out = _run(StageAgents(), budget=0.0)
    assert out["partial"] and out["skipped"] > 0 and out["cases"] == []
    assert out["configs"]["m1"]["valid_rate"] is None


def test_fallback_text_is_english_and_number_free_for_every_case():
    for seller, week, _ in suite.CASES:
        ctx = scene_context(seller, week)
        result = fallback_stage(ctx, "agents_disabled")
        for text in (result.why_now, result.outlook):
            assert usd_language.issues(text) == [], (seller, text)
            assert not re.search(r"\d", text), (seller, text)


def test_dailylook_case_is_the_first_base_high_week():
    seller, week, allowed = next(c for c in suite.CASES if c[0] == "seller-dailylook-01")
    assert allowed == {"medium", "high"}
    assert scene_context(seller, week).base_stage == "high"
    assert scene_context(seller, week - 1).base_stage != "high"


def test_suite_registered():
    assert "early_warning_usd" in SUITES
