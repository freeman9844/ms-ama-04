import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from evals.transport import chunks
from ops.azcli import OpsError
from ops.opsrun import load, metrics, new_run_id

T0 = datetime(2026, 11, 2, 1, 0, tzinfo=UTC)
ENV = {"AZURE_SUBSCRIPTION_ID": "sub", "AZURE_RESOURCE_GROUP": "rg", "OPS_JOB_NAME": "job-ops",
       "LOG_ANALYTICS_CUSTOMER_ID": "law"}
TEMPLATE = {"containers": [{"name": "ops", "env": []}]}


def doc(run_id, i, *, arch="healthy", rec="approve", minutes=4):
    return {
        "application_id": f"{run_id}-{i:02d}", "applicant_id": f"load-{arch}-{i}", "status": "awaiting_officer",
        "submitted_at": "2026-11-02T01:00:00+00:00", "received_at": "2026-11-02T01:00:05+00:00",
        "source": "load", "run_id": run_id, "segment": {"industry": "카페", "region": "서울", "business_age_band": "1~3년"},
        "steps": [{"step": "assemble", "outcome": "ok", "agent": True,
                   "usage": [{"model": "gpt-5.4-mini", "input_tokens": 1000, "output_tokens": 100, "calls": 1}]}],
        "timeline": [{"status": "awaiting_officer", "at": f"2026-11-02T01:{minutes:02d}:00+00:00"}],
        "flags": [], "policy": {"decision": "eligible", "reason_codes": []},
        "assessment": {"recommendation": rec}, "officer_decision": None,
    }


def _save(root: Path, run_id: str, docs: list, summary: dict | None = None):
    (root / run_id).mkdir(parents=True)
    (root / run_id / "docs.json").write_text(json.dumps({"runs": {run_id: docs}}), encoding="utf-8")
    (root / run_id / "summary.json").write_text(json.dumps(summary or {"unreached": []}), encoding="utf-8")


def _rows(run_id: str, files: dict[str, bytes]) -> str:
    lines = [x for name, data in files.items() for x in chunks(run_id, name, data, piece=50)]
    return json.dumps([{"Log_s": f"t stdout F {x}"} for x in lines])


def _job_runner(fake_runner, run_id, files):
    return (fake_runner(ENV)
            .on("az", "containerapp", "job", "show", results=[json.dumps(TEMPLATE)])
            .on("az", "rest", results=['{"name": "job-ops-x1"}'])
            .on("az", "containerapp", "job", "execution", "show", results=["Running\n", "Succeeded\n"])
            .on("az", "monitor", "log-analytics", "query", results=[_rows(run_id, files)]))


def _spy(runner, bodies):
    def call(args, *, stream=False):
        if args[:2] == ["az", "rest"]:
            bodies.append(json.loads(Path(args[args.index("--body") + 1][1:]).read_text(encoding="utf-8")))
        return runner(args, stream=stream)
    return call


def test_new_run_id():
    assert new_run_id("load", T0) == "load-20261102010000"


def test_load_starts_job_and_saves_outputs(fake_runner, tmp_path):
    run_id = "load-20261102010000"
    summary = {"run_id": run_id, "submitted": 24, "reached": 24, "unreached": [], "aborted": False}
    runner = _job_runner(fake_runner, run_id, {"docs.json": b'{"runs": {}}', "summary.json": json.dumps(summary).encode()})
    bodies = []
    result = load(_spy(runner, bodies), count=24, interval_s=15, now=T0, out_root=tmp_path, sleep=lambda s: None)
    env = {e["name"]: e["value"] for e in bodies[0]["containers"][0]["env"]}
    assert env == {"OPS_RUN_ID": run_id, "OPS_MODE": "load", "OPS_COUNT": "24", "OPS_INTERVAL_S": "15",
                   "OPS_SOURCE": "load"}
    assert result.summary == summary and result.execution == "job-ops-x1"
    assert json.loads((tmp_path / run_id / "run.json").read_text(encoding="utf-8"))["execution"] == "job-ops-x1"


def test_load_rejects_count_over_grid(fake_runner, tmp_path):
    runner = fake_runner(ENV)
    with pytest.raises(OpsError, match="1~24"):
        load(runner, count=25, out_root=tmp_path)
    assert runner.commands("az") == []


def test_metrics_without_cost_access_still_reports(fake_runner, tmp_path):
    _save(tmp_path, "load-1", [doc("load-1", i) for i in range(1, 6)])

    def no_access(*a, **k):
        raise OpsError("명령 실패(1): az rest\nAuthorizationFailed")

    out = metrics(fake_runner(ENV), "load-1", out_root=tmp_path, now=T0, cost_fn=no_access)
    data = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    assert data["summary"]["fixed"] == {"monthly_usd": None, "source": "측정 불가(명령 실패(1): az rest)"}
    assert data["summary"]["latency"]["n"] == 5 and data["summary"]["ai_cost"]["applications"] == 5
    report = (out / "report.md").read_text(encoding="utf-8")
    assert "합성 데이터 기준" in report and "측정 불가" in report


def test_metrics_manual_fixed_cost_skips_query(fake_runner, tmp_path):
    _save(tmp_path, "load-1", [doc("load-1", 1)])

    def never(*a, **k):
        raise AssertionError("Cost Management를 부르면 안 됨")

    out = metrics(fake_runner(ENV), "load-1", out_root=tmp_path, now=T0, fixed_monthly_usd=100.0, cost_fn=never)
    summary = json.loads((out / "metrics.json").read_text(encoding="utf-8"))["summary"]
    assert summary["fixed"] == {"monthly_usd": 100.0, "source": "수동 입력"}
    assert summary["unit_economics"][0]["fixed_usd"] == 1.0


def test_metrics_recollects_missing_runs_and_adds_fault_section(fake_runner, tmp_path):
    _save(tmp_path, "load-1", [doc("load-1", 1)])
    fault_docs = [{**doc("fault-1", 1), "source": "fault", "flags": ["policy_unavailable"]}]
    collected = {"runs": {"load-1": [doc("load-1", 1), doc("load-1", 2)], "fault-1": fault_docs}}
    run_id = "collect-20261102010000"
    runner = _job_runner(fake_runner, run_id, {"docs.json": json.dumps(collected).encode(),
                                                "summary.json": b'{"mode": "collect"}'})
    (tmp_path / "fault-1").mkdir()
    (tmp_path / "fault-1" / "run.json").write_text(
        json.dumps({"run_id": "fault-1", "fault": {"scenario": "policy-down", "reverted": True}}), encoding="utf-8")
    seen = {}

    def deps(az, workspace, ids, **kw):
        seen["ids"] = ids
        return {ids[0]: [{"target": "ca-policy", "role": "orchestrator", "total": 0, "failed": 0}]}

    bodies = []
    out = metrics(_spy(runner, bodies), "load-1", fault_runs=["fault-1"], out_root=tmp_path, now=T0,
                  fixed_monthly_usd=0.0, kql_fn=deps, sleep=lambda s: None)
    env = {e["name"]: e["value"] for e in bodies[0]["containers"][0]["env"]}
    assert (env["OPS_MODE"], env["OPS_RUN_IDS"]) == ("collect", "load-1,fault-1")
    data = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    assert data["summary"]["applications"] == 2
    (fault,) = data["faults"]
    assert fault["scenario"] == "policy-down" and fault["reverted"] is True and seen["ids"] == ["fault-1-01"]
    assert fault["applications"][0]["flags"] == ["policy_unavailable"]
    assert "policy-down" in (out / "report.md").read_text(encoding="utf-8")
