"""콘솔 App Gateway의 자체 서명 TLS 인증서(스펙 §4).

MCAPS 정책이 Key Vault 공용 접근을 막으므로 데이터 평면(az keyvault certificate) 대신 제어면 ARM PUT으로
비밀 appgw-console을 씁니다. PFX는 메모리에서만 만들고, 요청 본문 임시 파일은 바로 지웁니다.
App Gateway는 버전 없는 비밀 ID를 4시간마다 다시 읽으므로 갱신은 다시 프로비저닝하지 않아도 반영됩니다.
"""
from __future__ import annotations

import base64
import datetime as dt
import json
from typing import Callable

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from ops.azcli import OpsError, Runner, is_not_found, rest_put, subscription_runner
from ops.azdenv import get_env, set_env

SECRET_NAME = "appgw-console"
API_VERSION = "2023-07-01"
VALID_DAYS = 365
RENEW_BEFORE_DAYS = 30
ENV_KEY = "APPGW_CERT_SECRET_ID"


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def build_pfx(fqdn: str, now: dt.datetime) -> tuple[bytes, dt.datetime, dt.datetime]:
    """CN·SAN이 fqdn인 자체 서명 서버 인증서와 키를 암호 없는 PFX로 만듭니다."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, fqdn)])
    not_before = now - dt.timedelta(minutes=5)
    not_after = now + dt.timedelta(days=VALID_DAYS)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(fqdn)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )
    pfx = pkcs12.serialize_key_and_certificates(fqdn.encode(), key, cert, None, serialization.NoEncryption())
    return pfx, not_before, not_after


def _needs_new_cert(az: Runner, url: str, now: dt.datetime) -> bool:
    try:
        secret = json.loads(az(["az", "rest", "--method", "get", "--url", url]) or "{}")
    except OpsError as err:
        if is_not_found(err):
            return True
        raise
    exp = secret.get("properties", {}).get("attributes", {}).get("exp")
    if exp is None:
        return True
    return dt.datetime.fromtimestamp(exp, dt.timezone.utc) - now < dt.timedelta(days=RENEW_BEFORE_DAYS)


def ensure_console_cert(runner: Runner, now: Callable[[], dt.datetime] = _utcnow) -> bool:
    """인증서 비밀이 없거나 만료 30일 전이면 새로 쓰고, azd 환경의 APPGW_CERT_SECRET_ID를 맞춥니다.

    환경 값이 바뀌었으면(게이트웨이를 만들려면 다시 프로비저닝해야 함) True를 돌려줍니다.
    """
    env = get_env(runner)
    fqdn, kv_name, kv_uri = env.get("APPGW_FQDN"), env.get("KEYVAULT_NAME"), env.get("KEYVAULT_URI")
    if not (fqdn and kv_name and kv_uri):
        raise OpsError("azd 환경에 APPGW_FQDN·KEYVAULT_NAME·KEYVAULT_URI가 없습니다. 먼저 `azd provision`을 실행하세요.")
    az = subscription_runner(runner, env)
    kv_id = az(["az", "keyvault", "show", "--name", kv_name, "--query", "id", "-o", "tsv"]).strip()
    url = f"https://management.azure.com{kv_id}/secrets/{SECRET_NAME}?api-version={API_VERSION}"
    current = now()
    if _needs_new_cert(az, url, current):
        pfx, not_before, not_after = build_pfx(fqdn, current)
        rest_put(az, url, {"properties": {
            "value": base64.b64encode(pfx).decode("ascii"),
            "contentType": "application/x-pkcs12",
            "attributes": {"enabled": True, "nbf": int(not_before.timestamp()), "exp": int(not_after.timestamp())},
        }})
    secret_id = f"{kv_uri.rstrip('/')}/secrets/{SECRET_NAME}"
    if env.get(ENV_KEY) == secret_id:
        return False
    set_env(runner, ENV_KEY, secret_id)
    return True
