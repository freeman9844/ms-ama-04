import dataclasses
import json

from agents.runtime import AgentRun, GatewayBlocked, GatewayThrottled
from evals import gateway_probe
from evals.__main__ import load_prices, main
from evals.backend import Models, fake_backend
from evals.metering import Meter
from evals.runner import EvalRunner


class Agents:
    def __init__(self, errors=()):
        self.specs = []
        self.errors = list(errors)

    def run(self, spec, text, handlers):
        self.specs.append(spec)
        error = self.errors.pop(0) if self.errors else None
        if error:
            raise error
        return AgentRun(spec.name, "1", f"conv-{len(self.specs)}", gateway_probe.ProbeReply(reply="ok"), [], 3, 0,
                        model=spec.model)


def _meter():
    return Meter(load_prices(), 5.0)


def test_eval_runner_routes_via_gateway_and_counts_errors():
    agents = Agents([None, GatewayThrottled("rate_limited: x")])
    meter = _meter()
    runner = EvalRunner(agents, meter, gateway=True)
    spec = gateway_probe.probe_spec("gpt-5.4-mini")
    with meter.scope(suite="s", config="c", case="k"):
        run = runner.run(spec, "t", {})
        try:
            runner.run(spec, "t", {})
        except GatewayThrottled:
            pass
    assert agents.specs[0].model == "gw-probe/gpt-5.4-mini"
    assert agents.specs[0].name == "eval-gateway-probe-gpt-5-4-mini"
    assert run.model == "gpt-5.4-mini" and runner.runs[0]["model"] == "gpt-5.4-mini"
    assert runner.gateway_summary() == {"enabled": True, "content_blocked": 0, "rate_limited": 1}


def test_eval_runner_judge_and_gateway_off_call_directly():
    agents = Agents()
    meter = _meter()
    spec = gateway_probe.probe_spec("gpt-5.4")
    with meter.scope(suite="s", config="c", case="k"):
        EvalRunner(agents, meter, gateway=True).run(dataclasses.replace(spec, name="quality-judge"), "t", {})
        EvalRunner(agents, meter).run(spec, "t", {})
    assert [s.model for s in agents.specs] == ["gpt-5.4", "gpt-5.4"]


def test_run_probe_reproduces_block_and_throttle():
    agents = Agents([GatewayBlocked("content_blocked: x"), None, GatewayThrottled("rate_limited: x")])
    meter = _meter()
    result = gateway_probe.run_probe(EvalRunner(agents, meter, gateway=True), meter, model="gpt-5.4-mini", mode="all")
    assert result["checks"]["unsafe"] == {"attempts": ["content_blocked"], "expected": "content_blocked",
                                          "reproduced": True}
    assert result["checks"]["throttle"] == {"attempts": ["ok", "rate_limited"], "expected": "rate_limited",
                                            "reproduced": True}
    assert gateway_probe.INJECTION not in json.dumps(result, ensure_ascii=False)


def test_run_probe_fake_backend_is_not_reproduced():
    meter = _meter()
    backend = fake_backend(meter, Models.from_env({}))
    try:
        result = gateway_probe.run_probe(EvalRunner(backend.agents, meter), meter, model="gpt-5.4-mini",
                                         mode="throttle")
    finally:
        backend.close()
    throttle = result["checks"]["throttle"]
    assert throttle["attempts"] == ["ok"] * gateway_probe.MAX_THROTTLE_CALLS
    assert throttle["reproduced"] is False and "unsafe" not in result["checks"]


def test_main_gateway_probe_task_writes_probe_json(tmp_path):
    env = {"EVAL_TASK": gateway_probe.TASK, "PROBE_MODE": "unsafe"}
    assert main(["--fake", "--out", str(tmp_path)], env=env, log=lambda m: None) == 0
    probe = json.loads((tmp_path / gateway_probe.OUTPUT).read_text(encoding="utf-8"))
    assert probe["mode"] == "unsafe" and probe["checks"]["unsafe"]["attempts"] == ["ok"]
    assert probe["gateway"]["enabled"] is False and probe["cleanup"]["deleted"] == 1
    assert not (tmp_path / "summary.json").exists()
