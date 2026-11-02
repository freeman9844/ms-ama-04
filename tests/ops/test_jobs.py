import json
from pathlib import Path

from evals.transport import chunks
from ops.jobs import collect_files, start_job

TEMPLATE = {"containers": [{"name": "ops", "image": "acr.io/eval:t", "command": ["/opt/venv/bin/python", "-m", "opsjob"],
                            "env": [{"name": "COSMOS_DATABASE", "value": "lending"}, {"name": "OPS_MODE", "value": "x"}]}]}
ENV = {"AZURE_SUBSCRIPTION_ID": "sub"}


def _spy(runner, bodies):
    def call(args, *, stream=False):
        if args[:2] == ["az", "rest"]:
            bodies.append(json.loads(Path(args[args.index("--body") + 1][1:]).read_text(encoding="utf-8")))
        return runner(args, stream=stream)
    return call


def test_start_job_overrides_only_named_vars(fake_runner):
    runner = (fake_runner().on("az", "containerapp", "job", "show", results=[json.dumps(TEMPLATE)])
              .on("az", "rest", results=['{"name": "job-ops-1"}']))
    bodies = []
    name = start_job(_spy(runner, bodies), ENV, "rg", "job-ops", {"OPS_MODE": "load", "OPS_RUN_ID": "load-1"})
    assert name == "job-ops-1"
    (container,) = bodies[0]["containers"]
    assert container["command"] == ["/opt/venv/bin/python", "-m", "opsjob"]
    assert {e["name"]: e["value"] for e in container["env"]} == {
        "COSMOS_DATABASE": "lending", "OPS_MODE": "load", "OPS_RUN_ID": "load-1"}
    url = runner.commands("az", "rest")[0]
    assert "/jobs/job-ops/start?api-version=2024-03-01" in url[url.index("--url") + 1]


def test_start_job_without_overrides_uses_cli(fake_runner):
    runner = fake_runner().on("az", "containerapp", "job", "start", results=["job-eval-1\n"])
    assert start_job(runner, ENV, "rg", "job-eval", None) == "job-eval-1"
    assert runner.commands("az", "rest") == []


def test_collect_files_uses_given_run_id(fake_runner):
    lines = chunks("load-1", "summary.json", b"{}", piece=4) + chunks("other", "summary.json", b"[]", piece=4)
    rows = json.dumps([{"Log_s": f"2026-11-02T01:00:00Z stdout F {x}"} for x in lines])
    runner = fake_runner().on("az", "monitor", "log-analytics", "query", results=[rows])
    assert collect_files(runner, "law", "job-ops-1", ("summary.json",), run_id="load-1") == {"summary.json": b"{}"}
