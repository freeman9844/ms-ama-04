import subprocess

import pytest

from ops import azcli
from ops.azdenv import APP_SECRETS, provision


@pytest.mark.parametrize("stream", [False, True])
def test_env_set_failure_does_not_print_secret_value(monkeypatch, stream):
    monkeypatch.setattr(azcli.shutil, "which", lambda name: name)
    monkeypatch.setattr(azcli.subprocess, "run",
                        lambda *a, **kw: subprocess.CompletedProcess(a[0], 1, stdout="", stderr="env error"))
    with pytest.raises(azcli.OpsError) as err:
        azcli.subprocess_runner(["azd", "env", "set", "MOCK_CONSENT_SECRET", "s3cr3t"], stream=stream)
    assert "s3cr3t" not in str(err.value)
    assert "MOCK_CONSENT_SECRET" in str(err.value)


def test_provision_generates_missing_app_secrets_once(fake_runner):
    runner = fake_runner({"MOCK_CONSENT_SECRET": "too-short"})
    provision(runner)
    first = {k: runner.env[k] for k in APP_SECRETS}
    assert set(APP_SECRETS) == {"MOCK_CONSENT_SECRET", "PSEUDONYM_KEY"}
    assert all(len(v) >= 32 for v in first.values())
    assert first["MOCK_CONSENT_SECRET"] != first["PSEUDONYM_KEY"]
    assert runner.index("azd", "env", "set") < runner.index("azd", "provision")

    provision(runner)
    assert {k: runner.env[k] for k in APP_SECRETS} == first
    assert len(runner.commands("azd", "env", "set")) == 2
