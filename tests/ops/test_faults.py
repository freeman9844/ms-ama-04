import json

import pytest

from ops.azcli import OpsError
from ops.faults import SCENARIOS, run_scenario, set_fault, status

ENV = {"AZURE_SUBSCRIPTION_ID": "sub", "AZURE_RESOURCE_GROUP": "rg", "MOCKAPI_APP_NAME": "ca-mockapi",
       "POLICY_APP_NAME": "ca-policy"}
SHOW = ("az", "containerapp", "show")
UPDATE = ("az", "containerapp", "update")


def props(mode=None, ready=True):
    env = [{"name": "MOCK_CONSENT_SECRET", "secretRef": "s"}] + ([{"name": "FAULT_MODE", "value": mode}] if mode else [])
    return json.dumps({"latestRevisionName": "r2", "latestReadyRevisionName": "r2" if ready else "r1",
                       "template": {"containers": [{"env": env}]}})


class Done:
    def __init__(self, tmp_path):
        self.out_dir = tmp_path
        self.run_id = "fault-1"
        self.summary = {"submitted": 2, "reached": 2}


def test_set_fault_waits_for_ready_revision(fake_runner):
    runner = fake_runner(ENV).on(*SHOW, results=[props(), props("flaky", ready=False), props("flaky")])
    sleeps = []
    set_fault(runner, "source", True, sleep=sleeps.append, poll_wait=10)
    (update,) = runner.commands(*UPDATE)
    assert update[update.index("-n") + 1] == "ca-mockapi" and "FAULT_MODE=flaky" in update
    assert sleeps == [10]


def test_set_fault_off_is_noop_when_already_off(fake_runner):
    runner = fake_runner(ENV).on(*SHOW, results=[props()])
    set_fault(runner, "policy", False, sleep=lambda s: None)
    assert runner.commands(*UPDATE) == []


def test_status(fake_runner):
    runner = fake_runner(ENV).on(*SHOW, results=[props("flaky"), props()])
    assert status(runner) == {"source": "flaky", "policy": "off"}


def test_scenario_reverts_and_records_meta(fake_runner, tmp_path):
    runner = fake_runner(ENV).on(*SHOW, results=[props(), props("unavailable"), props("unavailable"), props()])
    calls = []

    def submit(r, applicants, **kw):
        calls.append((applicants, kw["interval_s"]))
        return Done(tmp_path)

    result = run_scenario(runner, "policy-down", submit_fn=submit, sleep=lambda s: None)
    assert calls == [(SCENARIOS["policy-down"].applicants, 90)]
    set_call, revert = runner.commands(*UPDATE)
    assert "FAULT_MODE=unavailable" in set_call and revert[revert.index("--remove-env-vars") + 1] == "FAULT_MODE"
    assert set_call[set_call.index("-n") + 1] == "ca-policy"
    meta = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert meta["fault"] == {"scenario": "policy-down", "app": "ca-policy", "mode": "unavailable", "reverted": True}
    assert result.run_id == "fault-1"


def test_scenario_reverts_on_failure(fake_runner):
    runner = fake_runner(ENV).on(*SHOW, results=[props(), props("flaky"), props("flaky"), props()])

    def submit(r, applicants, **kw):
        raise OpsError("job boom")

    with pytest.raises(OpsError, match="job boom"):
        run_scenario(runner, "source-flaky", submit_fn=submit, sleep=lambda s: None)
    assert any("--remove-env-vars" in c for c in runner.commands(*UPDATE))


def test_revert_failure_is_reported(fake_runner):
    runner = (fake_runner(ENV).on(*SHOW, results=[props(), props("flaky"), props("flaky")])
              .on(*UPDATE, results=["", OpsError("revert boom")]))
    original = OpsError("job boom")

    def submit(r, applicants, **kw):
        raise original

    with pytest.raises(OpsError, match="장애 원복 실패") as info:
        run_scenario(runner, "source-flaky", submit_fn=submit, sleep=lambda s: None)
    assert info.value.__cause__ is original
    assert "python -m ops fault source off" in str(info.value)
