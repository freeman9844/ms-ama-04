from ops.azdenv import APP_SECRETS, provision


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
