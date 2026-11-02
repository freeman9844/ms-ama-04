import json

from azure.core.credentials import AccessToken

from evals import datasets, report
from evals.__main__ import load_prices, main, selected
from evals.backend import CachedToken, Models, fake_backend
from evals.metering import CallRecord, Meter
from evals.runner import MAX_NAME, EvalRunner, eval_agent_name
from evals.suites import underwriting
from evals.suites.safety import injection
from evals.suites.underwriting import build_context, judge_context
from evals.transport import reassemble


def _run(tmp_path, *argv: str) -> tuple[dict, list[str]]:
    lines: list[str] = []
    assert main(["--fake", "--out", str(tmp_path), *argv], env={}, log=lines.append) == 0
    return json.loads((tmp_path / "summary.json").read_text(encoding="utf-8")), lines


def test_token_cache_reuses_token_until_near_expiry():
    issued = []

    class Credential:
        def get_token(self, scope):
            issued.append(scope)
            return AccessToken(f"t{len(issued)}", now[0] + 3600)

    now = [1000.0]
    token = CachedToken(Credential(), "scope-a", clock=lambda: now[0])
    assert [token(), token()] == ["t1", "t1"]
    now[0] += 3600 - 299  # 만료 5분 전부터는 새로 받는다
    assert token() == "t2"
    assert issued == ["scope-a", "scope-a"]


def test_judge_sees_same_evidence_as_analyst():
    case = datasets.underwriting_cases()[0]
    ctx = build_context(case, b"k" * 32)
    evidence = json.loads(judge_context(ctx))
    assert set(evidence) == {"features", "signals", "flags", "monthly", "policy"}
    assert evidence["monthly"] and evidence["policy"]["decision"]
    assert case.case.profile.representative_name not in judge_context(ctx)


def test_eval_agent_names_never_collide_with_production_names():
    assert eval_agent_name("underwriting-analyst", "gpt-5.4-mini") == "eval-underwriting-analyst-gpt-5-4-mini"
    assert len(eval_agent_name("x" * 80, "gpt-5.4")) <= MAX_NAME


def test_datasets_are_deterministic():
    assert datasets.summary() == datasets.summary()
    first, second = datasets.classify_apps(), datasets.classify_apps()
    assert [a.truth for a in first] == [a.truth for a in second]


def test_safety_suite_also_runs_underwriting():
    assert selected("safety") == ["underwriting", "safety"]
    assert selected("all") == ["classify", "underwriting", "early_warning", "safety"]


def test_injection_followed_only_counts_when_policy_is_not_eligible():
    rows = [
        {"status": "ok", "config": "m", "case": "a-inj", "archetype": "a", "injected": True, "pair_of": None, "policy": "refer",
         "recommendation": "approve", "injection_informative": True, "injection_followed": True},
        {"status": "ok", "config": "m", "case": "b-inj", "archetype": "b", "injected": True, "pair_of": None, "policy": "eligible",
         "recommendation": "approve", "injection_informative": False, "injection_followed": False},
        {"status": "ok", "config": "m", "case": "b", "archetype": "b", "injected": False, "pair_of": None, "policy": "eligible",
         "recommendation": "approve"},
    ]
    out = injection(rows)["configs"]["m"]
    assert (out["cases"], out["informative"], out["followed"], out["resistance_rate"]) == (2, 1, 1, 0.0)
    assert out["twin_consistency_rate"] == 1.0


def test_fake_run_writes_all_suites_and_cleans_up(tmp_path):
    summary, _ = _run(tmp_path)
    assert {p.name for p in tmp_path.iterdir()} == {"summary.json", "report.md", "calls.jsonl"}
    assert set(summary["suites"]) == {"classify", "underwriting", "early_warning", "safety"}
    assert not summary["partial"] and summary["errors"] == []
    assert summary["suites"]["safety"]["pii"]["calls_with_pii"] == 0
    assert summary["cleanup"]["deleted"] > 0 and summary["cleanup"]["failed"] == 0
    decision = summary["suites"]["classify"]["configs"]["decision-1+gpt-5.4-mini"]
    assert decision["unpriced_calls"] > 0 and decision["threshold_curve"]
    ew = summary["suites"]["early_warning"]
    assert ew["detector_only"]["fp"] > 0  # 계절 변동 경로에도 탐지기는 울린다(에이전트 비교 기준)
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    model_calls = [c for c in calls if c["kind"] in ("agent", "decision", "judge")]
    assert len(model_calls) == sum(m["calls"] for m in summary["by_model"].values())
    assert set(calls[0]) == set(CallRecord.__dataclass_fields__)  # 요청·응답 본문은 남기지 않는다
    assert "합성 데이터 기준" in (tmp_path / "report.md").read_text(encoding="utf-8")


def test_zero_budget_gives_partial_report_without_model_calls(tmp_path):
    summary, _ = _run(tmp_path, "--budget", "0")
    assert summary["partial"]
    assert summary["by_model"] == {}
    assert summary["suites"]["underwriting"]["skipped"] > 0
    assert (tmp_path / "report.md").exists()


def test_report_flags_classify_ceiling_effect(tmp_path):
    # 실측(probe)에서 세 구성 모두 macro F1 1.0 → 표본이 구성 차이를 가르지 못한다는 점을 한계에 적는다.
    summary, _ = _run(tmp_path, "--suite", "classify")
    configs = summary["suites"]["classify"]["configs"]
    for c in configs.values():
        c["macro_f1"] = 1.0
    assert "천장 효과" in report.render_markdown(summary)
    next(iter(configs.values()))["macro_f1"] = 0.8
    assert "천장 효과" not in report.render_markdown(summary)


def test_emitted_log_chunks_rebuild_the_report(tmp_path):
    summary, lines = _run(tmp_path, "--suite", "early_warning", "--emit")
    files = reassemble(lines, summary["run_id"])
    assert files["report.md"] == (tmp_path / "report.md").read_bytes()
    assert set(summary["suites"]) == {"early_warning"}

def test_case_error_and_judge_failure_do_not_stop_the_suite(monkeypatch):
    # 케이스 하나의 예외는 그 케이스만 error로 남기고, judge 실패는 점수만 비운다(자체 지표는 유지).
    meter = Meter(load_prices(), 5.0)
    models = Models.from_env({})
    backend = fake_backend(meter, models)
    cases = [c for c in datasets.underwriting_cases() if not c.pair_of][:2]
    real = underwriting.assess

    def flaky(ctx, **kw):
        if ctx.application_id == build_context(cases[0], b"k" * 32).application_id:
            raise RuntimeError("model timeout")
        return real(ctx, **kw)

    class BrokenJudge:
        def score(self, context, response):
            raise ValueError("judge schema")

    monkeypatch.setattr(underwriting, "assess", flaky)
    out = underwriting.run(cases, [models.mini], reviewer_model=models.reviewer, meter=meter,
                           runner=EvalRunner(backend.agents, meter), pseudonym_key=b"k" * 32, judge=BrokenJudge())
    backend.close()
    rows = {r["case"]: r for r in out["cases"]}
    assert rows[cases[0].case_id]["status"] == "error"
    assert rows[cases[1].case_id]["status"] == "ok"
    assert rows[cases[1].case_id]["judge"] == {"error": "ValueError"}
    assert [f["type"] for f in out["failures"] if f["case"] == cases[0].case_id] == ["agent_error"]
    config = out["configs"][models.mini]
    assert (config["cases"], config["assessed"], config["judge_mean"]) == (2, 1, None)
    assert config["final_grounding_rate"] is not None
