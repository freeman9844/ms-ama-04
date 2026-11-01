import io

import pytest

from ops.azcli import OpsError
from ops.cli import main
from ops.console_gate import check_console_gate, urllib_fetch

ENV = {"CONSOLE_URL": "https://ca-console.example.koreacentral.azurecontainerapps.io",
       "CONSOLE_CLIENT_ID": "22222222-2222-2222-2222-222222222222"}
LOGIN = ("https://login.microsoftonline.com/tenant-1/oauth2/v2.0/authorize?response_type=id_token"
         "&client_id=22222222-2222-2222-2222-222222222222&redirect_uri=x")


def fake_fetch(responses):
    calls = []

    def fetch(url):
        calls.append(url)
        return responses[url]
    return fetch, calls


def test_gate_follows_easy_auth_redirect_to_entra_login():
    base = ENV["CONSOLE_URL"]
    fetch, calls = fake_fetch({
        f"{base}/": (302, f"{base}/.auth/login/aad?post_login_redirect_uri=/"),
        f"{base}/.auth/login/aad?post_login_redirect_uri=/": (302, LOGIN),
    })
    assert "login.microsoftonline.com" in check_console_gate(ENV, fetch)
    assert calls == [f"{base}/", f"{base}/.auth/login/aad?post_login_redirect_uri=/"]  # Entra는 호출하지 않습니다.


def test_gate_accepts_relative_redirect_and_direct_login():
    base = ENV["CONSOLE_URL"]
    fetch, _ = fake_fetch({f"{base}/": (302, LOGIN)})
    check_console_gate(ENV, fetch)
    fetch, _ = fake_fetch({f"{base}/": (302, "/.auth/login/aad"), f"{base}/.auth/login/aad": (302, LOGIN)})
    check_console_gate(ENV, fetch)


@pytest.mark.parametrize("status, location, message", [
    (200, None, "로그인 없이"),
    (403, None, "window open"),
    (302, "https://login.microsoftonline.com/t/oauth2/v2.0/authorize?client_id=other", "client_id"),
    (302, "https://evil.example/login?client_id=22222222-2222-2222-2222-222222222222", "evil.example"),
    (404, None, "HTTP 404"),
])
def test_gate_failures(status, location, message):
    fetch, _ = fake_fetch({f"{ENV['CONSOLE_URL']}/": (status, location)})
    with pytest.raises(OpsError, match=message):
        check_console_gate(ENV, fetch)


def test_gate_stops_redirect_loops():
    base = ENV["CONSOLE_URL"]
    fetch, calls = fake_fetch({f"{base}/": (302, f"{base}/")})
    with pytest.raises(OpsError, match="리디렉션"):
        check_console_gate(ENV, fetch)
    assert len(calls) <= 5


def test_urllib_fetch_asks_like_a_browser_and_does_not_follow_redirects():
    # Easy Auth는 브라우저 User-Agent(Mozilla/…)만 로그인 페이지로 보내고, 나머지는 401을 돌려줍니다(실측).
    import http.server
    import threading

    class EasyAuthLike(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.headers.get("User-Agent", "").startswith("Mozilla/"):
                self.send_response(302)
                self.send_header("Location", LOGIN)
            else:
                self.send_response(401)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), EasyAuthLike)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert urllib_fetch(f"http://127.0.0.1:{server.server_port}/") == (302, LOGIN)
    finally:
        server.shutdown()
        server.server_close()


def test_gate_requires_console_outputs():
    with pytest.raises(OpsError, match="azd provision"):
        check_console_gate({}, lambda url: (302, LOGIN))


def test_cli_smoke_checks_console_gate_after_job(fake_runner, monkeypatch):
    order = []
    monkeypatch.setattr("ops.cli.run_smoke", lambda runner: order.append("job") or "job-smoke-1")
    monkeypatch.setattr("ops.cli.check_console_gate",
                        lambda env: order.append(("gate", env["CONSOLE_URL"])) or "Entra 로그인으로 리디렉션")
    out = io.StringIO()
    assert main(["smoke"], runner=fake_runner(ENV), out=out) == 0
    assert order == ["job", ("gate", ENV["CONSOLE_URL"])]
    assert "콘솔" in out.getvalue()
