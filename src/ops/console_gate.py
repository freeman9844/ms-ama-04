"""운영자 PC에서 콘솔 진입 관문을 확인합니다: 로그인 없이 들어가면 Entra 로그인으로 보내야 합니다.

콘솔은 App Gateway WAF(운영자 IP만 허용) 뒤 내부 환경이라, VNet 안 smoke Job이 아니라 운영자 IP(CONSOLE_IP)에서
게이트웨이 주소로 확인합니다. 게이트웨이 인증서는 자체 서명이라 TLS 검증은 하지 않습니다(자격 증명을 보내지 않음).
Entra 로그인 화면 자체는 호출하지 않고, 리디렉션 대상(호스트·client_id)만 봅니다.
"""
from __future__ import annotations

import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from ops.azcli import OpsError

LOGIN_HOST = "login.microsoftonline.com"
MAX_HOPS = 4
Fetch = Callable[[str], "tuple[int, str | None]"]
_IP_BLOCKED = ("App Gateway WAF가 현재 IP를 막았습니다(403). WAF 허용 IP는 CONSOLE_IP입니다: "
               "`python -m ops window open`으로 갱신한 뒤 `window close` 하세요.")


def _check_demo_console(url: str, fetch: Fetch) -> str:
    """데모 모드(CONSOLE_AUTH_ENABLED=false): Easy Auth가 꺼져 운영자 IP에서 바로 열려야 합니다."""
    status, _ = fetch(url)
    if status == 403:
        raise OpsError(_IP_BLOCKED)
    if status in (301, 302, 303, 307, 308):
        raise OpsError(f"{url}: 데모 모드인데 리디렉션합니다(HTTP {status}). Easy Auth가 아직 켜져 있습니다: "
                       "`python -m ops window open` 으로 다시 프로비저닝하세요.")
    if status != 200:
        raise OpsError(f"{url}: 예상하지 못한 응답 HTTP {status}")
    return "데모 모드: Entra 로그인 꺼짐, 운영자 IP에서 App Gateway(WAF)로 콘솔이 열립니다(자체 서명 인증서라 TLS 검증 생략)"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def urllib_fetch(url: str) -> tuple[int, str | None]:
    """리디렉션을 따라가지 않고 (상태 코드, Location)을 돌려줍니다.

    Easy Auth는 브라우저 User-Agent(Mozilla/…) 요청만 로그인 페이지로 보내고 그 밖의 요청에는 401을 돌려주므로
    (실측) 브라우저처럼 묻습니다.
    """
    # App Gateway 인증서는 자체 서명입니다. 상태 코드와 Location만 보고 자격 증명은 보내지 않으므로 검증을 끕니다.
    insecure = ssl.create_default_context()
    insecure.check_hostname = False
    insecure.verify_mode = ssl.CERT_NONE
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=insecure))
    request = urllib.request.Request(url, method="GET", headers={
        "User-Agent": "Mozilla/5.0 (compatible; ama-ops-console-gate)", "Accept": "text/html"})
    try:
        with opener.open(request, timeout=30) as resp:
            return resp.status, resp.headers.get("Location")
    except urllib.error.HTTPError as err:
        return err.code, err.headers.get("Location")


def check_console_gate(env: dict[str, str], fetch: Fetch = urllib_fetch) -> str:
    base, client_id = env.get("CONSOLE_URL"), env.get("CONSOLE_CLIENT_ID")
    if not base or not client_id:
        raise OpsError("CONSOLE_URL/CONSOLE_CLIENT_ID가 azd 환경에 없습니다. `azd provision` 후 다시 실행하세요.")
    if not env.get("APPGW_CERT_SECRET_ID"):
        raise OpsError("App Gateway가 아직 없습니다(인증서 대기). "
                       "`python -m ops window open`으로 인증서를 만들고 다시 프로비저닝하세요.")
    if env.get("CONSOLE_AUTH_ENABLED") == "false":
        return _check_demo_console(base.rstrip("/") + "/", fetch)
    console_host = urllib.parse.urlsplit(base).hostname
    url = base.rstrip("/") + "/"
    for _ in range(MAX_HOPS):
        status, location = fetch(url)
        if status == 403:
            raise OpsError(_IP_BLOCKED)
        if status == 200:
            raise OpsError(f"{url}: 로그인 없이 200을 돌려줍니다. Easy Auth(authConfigs)를 확인하세요.")
        if status not in (301, 302, 303, 307, 308) or not location:
            raise OpsError(f"{url}: 예상하지 못한 응답 HTTP {status}")
        target = urllib.parse.urljoin(url, location)
        parts = urllib.parse.urlsplit(target)
        if parts.hostname == LOGIN_HOST:
            if urllib.parse.parse_qs(parts.query).get("client_id") != [client_id]:
                raise OpsError(f"Entra 로그인 리디렉션의 client_id가 콘솔 앱({client_id})이 아닙니다: {target}")
            return f"로그인 없는 요청은 Entra 로그인으로 리디렉션({LOGIN_HOST}, client_id {client_id})"
        if parts.hostname != console_host:
            raise OpsError(f"콘솔이 예상하지 못한 호스트로 리디렉션합니다: {parts.hostname}")
        url = target
    raise OpsError(f"콘솔 리디렉션이 {MAX_HOPS}번 안에 Entra 로그인에 도달하지 않습니다: {url}")
