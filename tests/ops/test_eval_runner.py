import io
import json
from pathlib import Path

import pytest

from evals.transport import chunks
from ops.azcli import OpsError
from ops.cli import main
from ops.eval_runner import run_eval, run_gateway_probe

ENV = {"AZURE_SUBSCRIPTION_ID": "sub-azd", "AZURE_RESOURCE_GROUP": "rg-amalend", "EVAL_JOB_NAME": "job-eval",
       "LOG_ANALYTICS_CUSTOMER_ID": "law-guid"}
START = ("az", "containerapp", "job", "start")
SHOW = ("az", "containerapp", "job", "execution", "show")
QUERY = ("az", "monitor", "log-analytics", "query")
EXEC = "job-eval-abc12"
SUMMARY = {"partial": False, "spent_usd": 2.5, "failure_counts": {"classification_wrong": 3}}


def _rows(files: dict[str, bytes], run_id: str = EXEC, drop: int | None = None) -> str:
    lines = [line for name, data in files.items() for line in chunks(run_id, name, data, piece=40)]
    if drop is not None:
        del lines[drop]
    # 콘솔 로그는 앞에 타임스탬프가 붙고 순서가 섞여 올 수 있다.
    return json.dumps([{"Log_s": f"2026-11-02T01:00:00Z stdout F {line}"} for line in reversed(lines)])


FILES = {"summary.json": json.dumps(SUMMARY).encode(), "report.md": "# 평가 리포트\n결과".encode(),
         "calls.jsonl": b'{"agent": "x"}\n' * 30}


def _runner(fake_runner, *queries, status=("Running\n", "Succeeded\n")):
    return (fake_runner(ENV)
            .on(*START, results=[EXEC + "\n"])
            .on(*SHOW, results=list(status))
            .on(*QUERY, results=list(queries)))


def test_run_eval_waits_for_logs_then_saves_reassembled_report(fake_runner, tmp_path):
    runner = _runner(fake_runner, "[]", _rows(FILES, drop=3), _rows(FILES))
    sleeps = []
    result = run_eval(runner, out_root=tmp_path, sleep=sleeps.append)
    assert result.execution == EXEC
    assert result.summary == SUMMARY
    for name, data in FILES.items():
        assert (tmp_path / EXEC / name).read_bytes() == data
    # 로그가 비었거나 조각이 빠지면 기다렸다 다시 조회한다.
    assert len(runner.commands(*QUERY)) == 3
    query = runner.commands(*QUERY)[0]
    assert query[query.index("-w") + 1] == "law-guid"
    kql = query[query.index("--analytics-query") + 1]
    assert f"startswith '{EXEC}'" in kql and "EVALCHUNK" in kql
    assert "--env-vars" not in runner.commands(*START)[0]
    for call in runner.commands("az"):
        assert call[call.index("--subscription") + 1] == "sub-azd"
    assert runner.commands("az", "rest") == []


TEMPLATE = {"containers": [{"name": "eval", "image": "acr.io/eval:t2", "resources": {"cpu": 1.0},
                            "env": [{"name": "AZURE_CLIENT_ID", "value": "cid"},
                                    {"name": "EVAL_SUITE", "value": "all"}]}]}


def test_run_eval_suite_override_starts_with_full_container_copy(fake_runner, tmp_path):
    # start 템플릿 덮어쓰기는 컨테이너를 통째로 바꾸므로 기존 정의(이미지·env·리소스)를 복사하고 EVAL_SUITE만 바꾼다.
    runner = (_runner(fake_runner, _rows(FILES))
              .on("az", "containerapp", "job", "show", results=[json.dumps(TEMPLATE)])
              .on("az", "rest", results=[json.dumps({"name": EXEC})]))
    bodies = []

    def spy(args, *, stream=False):
        if args[:2] == ["az", "rest"]:
            path = Path(args[args.index("--body") + 1].removeprefix("@"))
            bodies.append(json.loads(path.read_text(encoding="utf-8")))
        return runner(args, stream=stream)

    result = run_eval(spy, suite="safety", out_root=tmp_path, sleep=lambda s: None)
    assert result.execution == EXEC
    assert runner.commands(*START) == []
    rest = runner.commands("az", "rest")[0]
    assert rest[rest.index("--method") + 1] == "post"
    assert rest[rest.index("--url") + 1] == (
        "https://management.azure.com/subscriptions/sub-azd/resourceGroups/rg-amalend/providers/"
        "Microsoft.App/jobs/job-eval/start?api-version=2024-03-01")
    container = bodies[0]["containers"][0]
    assert {k: container[k] for k in ("name", "image", "resources")} == {
        "name": "eval", "image": "acr.io/eval:t2", "resources": {"cpu": 1.0}}
    assert container["env"] == [{"name": "AZURE_CLIENT_ID", "value": "cid"},
                                {"name": "EVAL_SUITE", "value": "safety"}]
    # 본문 임시 파일은 남기지 않는다.
    assert not Path(rest[rest.index("--body") + 1].removeprefix("@")).exists()


def test_run_eval_accepts_local_run_id_when_execution_name_not_injected(fake_runner, tmp_path):
    runner = _runner(fake_runner, _rows(FILES, run_id="local-20261102T010000Z"))
    result = run_eval(runner, out_root=tmp_path, sleep=lambda s: None)
    assert (tmp_path / EXEC / "report.md").read_bytes() == FILES["report.md"]
    assert result.execution == EXEC


def test_run_eval_gives_up_on_logs_after_limit(fake_runner, tmp_path):
    runner = _runner(fake_runner, _rows(FILES, drop=0))
    with pytest.raises(OpsError, match="누락") as err:
        run_eval(runner, out_root=tmp_path, sleep=lambda s: None, log_limit=4)
    assert "ContainerAppConsoleLogs_CL" in str(err.value)
    assert len(runner.commands(*QUERY)) == 4


def test_run_eval_failed_job_includes_log_hint(fake_runner, tmp_path):
    runner = _runner(fake_runner, _rows(FILES), status=["Failed\n"])
    with pytest.raises(OpsError, match="Failed") as err:
        run_eval(runner, out_root=tmp_path, sleep=lambda s: None)
    assert "law-guid" in str(err.value)
    assert runner.commands(*QUERY) == []


def test_cli_eval_prints_summary(fake_runner, tmp_path, monkeypatch):
    monkeypatch.setattr("ops.eval_runner.REPORTS", tmp_path)
    runner = (_runner(fake_runner, _rows(FILES))
              .on("az", "containerapp", "job", "show", results=[json.dumps(TEMPLATE)])
              .on("az", "rest", results=[json.dumps({"name": EXEC})]))
    monkeypatch.setattr("ops.eval_runner.time.sleep", lambda s: None)
    out = io.StringIO()
    assert main(["eval", "--suite", "classify"], runner=runner, out=out) == 0
    text = out.getvalue()
    assert EXEC in text and "$2.5" in text and str(tmp_path / EXEC) in text


def test_run_eval_retries_transient_log_query_failure(fake_runner, tmp_path):
    runner = _runner(fake_runner, OpsError("az monitor: 일시 오류"), _rows(FILES))
    result = run_eval(runner, out_root=tmp_path, sleep=lambda s: None)
    assert (tmp_path / EXEC / "summary.json").exists() and result.execution == EXEC
    assert len(runner.commands(*QUERY)) == 2


def test_run_eval_collect_only_recovers_report_of_finished_execution(fake_runner, tmp_path):
    # 로그 수집이 늦어 복원에 실패한 실행은 Job을 다시 돌리지 않고 회수만 다시 한다.
    runner = _runner(fake_runner, _rows(FILES))
    result = run_eval(runner, collect=EXEC, out_root=tmp_path, sleep=lambda s: None)
    assert result.execution == EXEC and (tmp_path / EXEC / "report.md").read_bytes() == FILES["report.md"]
    assert runner.commands(*START) == [] and runner.commands(*SHOW) == [] and runner.commands("az", "rest") == []


def test_log_give_up_message_points_to_recollect_and_blob_copy(fake_runner, tmp_path):
    runner = _runner(fake_runner, _rows(FILES, drop=0))
    with pytest.raises(OpsError) as err:
        run_eval(runner, out_root=tmp_path, sleep=lambda s: None, log_limit=2)
    assert f"python -m ops eval --collect {EXEC}" in str(err.value)
    assert f"eval-reports/{EXEC}/" in str(err.value)


def test_cli_eval_collect(fake_runner, tmp_path, monkeypatch):
    monkeypatch.setattr("ops.eval_runner.REPORTS", tmp_path)
    runner = _runner(fake_runner, _rows(FILES))
    out = io.StringIO()
    assert main(["eval", "--collect", EXEC], runner=runner, out=out) == 0
    assert str(tmp_path / EXEC) in out.getvalue() and runner.commands(*START) == []


PROBE = {"task": "gateway-probe", "mode": "throttle",
         "checks": {"throttle": {"attempts": ["ok", "rate_limited"], "expected": "rate_limited", "reproduced": True}}}
PROBE_FILES = {"probe.json": json.dumps(PROBE).encode()}


def test_gateway_probe_starts_job_with_task_and_collects_probe(fake_runner, tmp_path):
    runner = (_runner(fake_runner, _rows(PROBE_FILES))
              .on("az", "containerapp", "job", "show", results=[json.dumps(TEMPLATE)])
              .on("az", "rest", results=[json.dumps({"name": EXEC})]))
    bodies = []

    def spy(args, *, stream=False):
        if args[:2] == ["az", "rest"]:
            path = Path(args[args.index("--body") + 1].removeprefix("@"))
            bodies.append(json.loads(path.read_text(encoding="utf-8")))
        return runner(args, stream=stream)

    result = run_gateway_probe(spy, mode="throttle", out_root=tmp_path, sleep=lambda s: None)
    assert result.summary == PROBE
    env = bodies[0]["containers"][0]["env"]
    assert {"name": "EVAL_TASK", "value": "gateway-probe"} in env
    assert {"name": "PROBE_MODE", "value": "throttle"} in env
    assert {"name": "EVAL_SUITE", "value": "all"} in env
    assert (tmp_path / EXEC / "probe.json").read_bytes() == PROBE_FILES["probe.json"]


def test_gateway_probe_give_up_message_points_to_probe_collect(fake_runner, tmp_path):
    runner = _runner(fake_runner, _rows(PROBE_FILES, drop=0))
    with pytest.raises(OpsError) as err:
        run_gateway_probe(runner, mode="unsafe", collect=EXEC, out_root=tmp_path, sleep=lambda s: None, log_limit=2)
    assert f"python -m ops gateway probe --mode unsafe --collect {EXEC}" in str(err.value)


@pytest.mark.parametrize("reproduced, code", [(True, 0), (False, 2)])
def test_cli_gateway_probe_exit_code(fake_runner, tmp_path, monkeypatch, reproduced, code):
    monkeypatch.setattr("ops.eval_runner.REPORTS", tmp_path)
    probe = {**PROBE, "checks": {"throttle": {**PROBE["checks"]["throttle"], "reproduced": reproduced}}}
    runner = _runner(fake_runner, _rows({"probe.json": json.dumps(probe).encode()}))
    out = io.StringIO()
    assert main(["gateway", "probe", "--collect", EXEC], runner=runner, out=out) == code
    assert '"reproduced"' in out.getvalue() and str(tmp_path / EXEC) in out.getvalue()
