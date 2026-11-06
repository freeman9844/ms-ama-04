import base64
import datetime as dt
import json
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from ops.appgw_cert import build_pfx, ensure_console_cert
from ops.azcli import OpsError

SUB = "ffffffff-ffff-ffff-ffff-ffffffffffff"
KV_ID = f"/subscriptions/{SUB}/resourceGroups/rg-amalend/providers/Microsoft.KeyVault/vaults/kvx"
FQDN = "amalend-abc.koreacentral.cloudapp.azure.com"
SECRET_ID = "https://kvx.vault.azure.net/secrets/appgw-console"
ENV = {"AZURE_SUBSCRIPTION_ID": SUB, "APPGW_FQDN": FQDN, "KEYVAULT_NAME": "kvx",
       "KEYVAULT_URI": "https://kvx.vault.azure.net/"}
NOW = dt.datetime(2026, 11, 6, tzinfo=dt.timezone.utc)
NOT_FOUND = OpsError("명령 실패(3): az rest\n(SecretNotFound) Secret appgw-console was not found")


def _secret(days_left: int) -> str:
    return json.dumps({"properties": {"attributes": {"enabled": True,
                                                     "exp": int((NOW + dt.timedelta(days=days_left)).timestamp())}}})


@pytest.fixture
def make_runner(fake_runner):
    class Capturing(fake_runner):
        """az rest PUT의 --body @파일 내용을 읽어 둡니다(파일이 지워지기 전에)."""

        def __init__(self, env):
            super().__init__(env)
            self.bodies: list[dict] = []
            self.body_files: list[Path] = []

        def __call__(self, args, *, stream=False):
            if list(args[:4]) == ["az", "rest", "--method", "put"]:
                path = Path(args[args.index("--body") + 1].removeprefix("@"))
                self.body_files.append(path)
                self.bodies.append(json.loads(path.read_text(encoding="utf-8")))
            return super().__call__(args, stream=stream)

    def make(existing, env=ENV):
        runner = Capturing(env)
        runner.on("az", "keyvault", "show", results=[KV_ID + "\n"])
        runner.on("az", "rest", "--method", "get", results=[existing])
        return runner
    return make


def test_ensure_cert_creates_secret_when_missing(make_runner):
    runner = make_runner(NOT_FOUND)
    assert ensure_console_cert(runner, now=lambda: NOW) is True
    assert runner.env["APPGW_CERT_SECRET_ID"] == SECRET_ID
    [put] = runner.commands("az", "rest", "--method", "put")
    assert put[put.index("--url") + 1] == (
        f"https://management.azure.com{KV_ID}/secrets/appgw-console?api-version=2023-07-01")
    [body] = runner.bodies
    props = body["properties"]
    assert props["contentType"] == "application/x-pkcs12"
    assert props["attributes"] == {"enabled": True,
                                   "nbf": int((NOW - dt.timedelta(minutes=5)).timestamp()),
                                   "exp": int((NOW + dt.timedelta(days=365)).timestamp())}
    key, cert, _ = pkcs12.load_key_and_certificates(base64.b64decode(props["value"]), None)
    assert key is not None
    assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == FQDN
    assert not any(path.exists() for path in runner.body_files)  # PFX가 든 본문 파일은 남지 않습니다.
    [show] = runner.commands("az", "keyvault", "show")
    assert show[-2:] == ["--subscription", SUB]


def test_ensure_cert_keeps_valid_secret(make_runner):
    runner = make_runner(_secret(200), env={**ENV, "APPGW_CERT_SECRET_ID": SECRET_ID})
    assert ensure_console_cert(runner, now=lambda: NOW) is False
    assert runner.commands("az", "rest", "--method", "put") == []
    assert runner.commands("azd", "env", "set") == []


def test_ensure_cert_renews_expiring_secret_without_reprovision(make_runner):
    # 게이트웨이는 버전 없는 비밀 ID를 4시간마다 다시 읽으므로 갱신만으로 반영됩니다.
    runner = make_runner(_secret(10), env={**ENV, "APPGW_CERT_SECRET_ID": SECRET_ID})
    assert ensure_console_cert(runner, now=lambda: NOW) is False
    assert len(runner.bodies) == 1


def test_ensure_cert_sets_env_for_existing_secret(make_runner):
    runner = make_runner(_secret(200))
    assert ensure_console_cert(runner, now=lambda: NOW) is True
    assert runner.env["APPGW_CERT_SECRET_ID"] == SECRET_ID
    assert runner.bodies == []


def test_ensure_cert_propagates_other_errors(make_runner):
    runner = make_runner(OpsError("명령 실패(1): az rest\n(AuthorizationFailed) denied"))
    with pytest.raises(OpsError, match="AuthorizationFailed"):
        ensure_console_cert(runner, now=lambda: NOW)
    assert runner.bodies == []


def test_ensure_cert_requires_provisioned_outputs(make_runner):
    runner = make_runner(NOT_FOUND, env={k: v for k, v in ENV.items() if k != "APPGW_FQDN"})
    with pytest.raises(OpsError, match="azd provision"):
        ensure_console_cert(runner, now=lambda: NOW)


def test_build_pfx_is_passwordless_self_signed_server_cert():
    pfx, not_before, not_after = build_pfx(FQDN, NOW)
    key, cert, extra = pkcs12.load_key_and_certificates(pfx, None)
    assert key is not None and extra == []
    assert cert.subject == cert.issuer
    assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == FQDN
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert san.get_values_for_type(x509.DNSName) == [FQDN]
    assert cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is False
    assert cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value == x509.ExtendedKeyUsage(
        [ExtendedKeyUsageOID.SERVER_AUTH])
    assert (not_before, not_after) == (NOW - dt.timedelta(minutes=5), NOW + dt.timedelta(days=365))
    assert (cert.not_valid_before_utc, cert.not_valid_after_utc) == (not_before, not_after)
