import io

import pytest

from ops.azcli import OpsError
from ops.cli import main


@pytest.fixture(autouse=True)
def _cert_already_current(monkeypatch):
    # 인증서 확인은 test_appgw_cert.py가 다룹니다. 여기서는 "바뀐 것 없음"으로 둡니다.
    monkeypatch.setattr("ops.window.ensure_console_cert", lambda runner: False)

ENV = {"AZURE_RESOURCE_GROUP": "rg-amalend", "AI_ACCOUNT_NAME": "ais-x", "OPERATOR_IP": "closed"}


def test_window_open_uses_detected_public_ip(fake_runner):
    runner = fake_runner(ENV)
    out = io.StringIO()
    assert main(["window", "open"], runner=runner, public_ip=lambda: "20.30.40.50", out=out) == 0
    assert runner.env["OPERATOR_IP"] == "20.30.40.50"
    assert "window close" in out.getvalue()


def test_window_open_with_explicit_ip_and_acr(fake_runner):
    runner = fake_runner(ENV)
    main(["window", "open", "--ip", "20.30.40.60", "--acr"], runner=runner,
         public_ip=lambda: pytest.fail("감지하지 않아야 합니다"), out=io.StringIO())
    assert runner.env["OPERATOR_IP"] == "20.30.40.60"
    assert runner.env["ACR_PUBLIC"] == "true"


def test_window_status_returns_2_when_ip_changed(fake_runner):
    runner = fake_runner({**ENV, "OPERATOR_IP": "20.30.40.50"})
    out = io.StringIO()
    assert main(["window", "status"], runner=runner, public_ip=lambda: "20.30.40.99", out=out) == 2
    assert "20.30.40.99" in out.getvalue()


def test_window_status_survives_ip_lookup_failure(fake_runner):
    def offline():
        raise OSError("network down")
    out = io.StringIO()
    assert main(["window", "status"], runner=fake_runner(ENV), public_ip=offline, out=out) == 0
    assert "닫힘" in out.getvalue()


def test_teardown_requires_confirm_flag(fake_runner):
    with pytest.raises(SystemExit) as err:
        main(["teardown"], runner=fake_runner(ENV), out=io.StringIO())
    assert err.value.code == 2


def test_ops_error_returns_1(fake_runner, capsys):
    def broken(args, *, stream=False):
        raise OpsError("azd 실패")
    assert main(["resume"], runner=broken, out=io.StringIO()) == 1
    assert "azd 실패" in capsys.readouterr().err


def test_fault_and_metrics_dispatch(monkeypatch, fake_runner, tmp_path):
    import ops.cli as cli

    seen = []
    monkeypatch.setattr(cli.faults, "set_fault", lambda r, target, on: seen.append(("set", target, on)))
    monkeypatch.setattr(cli.faults, "status", lambda r: {"source": "off", "policy": "unavailable"})
    monkeypatch.setattr(cli.opsrun, "metrics", lambda r, run, **kw: seen.append(("metrics", run, kw)) or tmp_path)
    out = io.StringIO()
    assert cli.main(["fault", "source", "flaky"], runner=fake_runner(), out=out) == 0
    assert cli.main(["fault", "policy", "off"], runner=fake_runner(), out=out) == 0
    assert cli.main(["fault", "status"], runner=fake_runner(), out=out) == 0
    assert cli.main(["metrics", "--run", "load-1", "--fault-runs", "f-1", "f-2", "--fixed-monthly-usd", "50"],
                    runner=fake_runner(), out=out) == 0
    assert seen[:2] == [("set", "source", True), ("set", "policy", False)]
    assert seen[2][2]["fault_runs"] == ["f-1", "f-2"] and seen[2][2]["fixed_monthly_usd"] == 50.0
    assert "policy: unavailable" in out.getvalue()
