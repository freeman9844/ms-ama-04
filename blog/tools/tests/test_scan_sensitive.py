from pathlib import Path

from blog.tools.scan_sensitive import load_denylist, main, scan_text, scan_tree


def test_flags_inside_code_fence_and_inline_code():
    text = "```\nC:\\Users\\someone\\x\n```\nip `20.196.1.2` id 9f4014cf-b673-466c-9f7e-9ae4c3db7387"
    assert {f.rule for f in scan_text(text)} == {"user_path", "ipv4", "guid"}


def test_allows_documentation_ranges_and_versions():
    text = "203.0.113.7 and 192.0.2.10, azd 1.37.2, mail noreply@github.com, 123+x@users.noreply.github.com"
    assert scan_text(text) == []


def test_denylist_literal_and_regex(tmp_path):
    p = tmp_path / "d.txt"; p.write_text("# c\nAcmeCorp\nre:(?i)acme\\s+employee\n", encoding="utf-8")
    deny = load_denylist(p)
    rules = [f.rule for f in scan_text("I work at acmecorp as an ACME employee", deny=deny)]
    assert rules == ["deny", "deny"]


def test_cli_exit_code(tmp_path, monkeypatch):
    monkeypatch.delenv("BLOG_DENYLIST", raising=False)
    (tmp_path / "a.md").write_text("ok", encoding="utf-8")
    assert main([str(tmp_path)]) == 0
    (tmp_path / "b.md").write_text("ghp_" + "a" * 30, encoding="utf-8")
    assert main([str(tmp_path)]) == 1


def test_ipv4_octet_range_and_allowed_hosts():
    assert scan_text("0.0.0.0 127.0.0.1 198.51.100.9 256.1.1.1 1.2.3.4.5") == []
    assert [f.rule for f in scan_text("host 10.0.0.255")] == ["ipv4"]


def test_secrets_and_emails():
    text = "\n".join([
        "AccountKey=abc",
        "SharedAccessSignature sr=x",
        "https://x.blob.core.windows.net/c?sv=1&sig=abc",
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
        "token eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.x",
        "mail someone@contoso.com, demo@example.com",
        "/home/dev/x and /Users/dev/y",
    ])
    rules = [(f.line, f.rule) for f in scan_text(text)]
    assert rules == [
        (1, "secret"), (2, "secret"), (3, "secret"), (4, "secret"), (5, "secret"),
        (6, "email"), (7, "user_path"), (7, "user_path"),
    ]


def test_windows_user_path_any_case():
    rules = [f.rule for f in scan_text("c:\\users\\someone\\x\nC:\\USERS\\someone")]
    assert rules == ["user_path", "user_path"]


def test_fine_grained_github_pat():
    assert [f.rule for f in scan_text("github_pat_11ABCDEFG0123456789_abcdefghij")] == ["secret"]


def test_guid_joined_to_word_character():
    assert [f.rule for f in scan_text("rg_9f4014cf-b673-466c-9f7e-9ae4c3db7387")] == ["guid"]


def test_excerpt_does_not_leak_value(tmp_path):
    [f] = scan_text("x\nid 9f4014cf-b673-466c-9f7e-9ae4c3db7387", path="p.md")
    assert (f.path, f.line, f.excerpt) == ("p.md", 2, "9f40…")
    p = tmp_path / "d.txt"; p.write_text("Kim\n", encoding="utf-8")
    [d] = scan_text("by Kim", deny=load_denylist(p))
    assert "Kim" not in d.excerpt and d.excerpt.endswith("…")


def test_scan_tree_filters_extensions_and_skips_tools(tmp_path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "rules.md").write_text("10.1.2.3", encoding="utf-8")
    (tmp_path / "posts").mkdir()
    (tmp_path / "posts" / "a.md").write_text("ok\n10.1.2.3", encoding="utf-8")
    (tmp_path / "posts" / "b.html").write_text("10.1.2.4", encoding="utf-8")
    (tmp_path / "c.yml").write_text("10.1.2.5", encoding="utf-8")
    (tmp_path / "d.txt").write_text("10.1.2.6", encoding="utf-8")
    found = sorted((Path(f.path).name, f.line) for f in scan_tree(tmp_path, []))
    assert found == [("a.md", 2), ("b.html", 1), ("c.yml", 1)]


def test_cli_denylist_env_and_missing_file(tmp_path, monkeypatch, capsys):
    deny = tmp_path / "deny.txt"
    deny.write_text("acmecorp\n", encoding="utf-8")
    site = tmp_path / "site"; site.mkdir()
    (site / "a.md").write_text("hello AcmeCorp", encoding="utf-8")
    monkeypatch.setenv("BLOG_DENYLIST", str(deny))
    assert main([str(site)]) == 1
    out = capsys.readouterr().out
    assert "a.md:1 deny" in out and "AcmeCorp" not in out
    monkeypatch.delenv("BLOG_DENYLIST")
    assert main([str(site)]) == 0
    assert main([str(site), "--denylist", str(tmp_path / "nope.txt")]) == 2
