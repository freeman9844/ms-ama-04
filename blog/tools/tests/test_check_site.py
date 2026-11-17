from pathlib import Path

import yaml

from blog.tools.check_site import check_site, main

EP1 = {"ref": "day-01", "days": "2026-10-29 – 2026-10-30", "title_en": "Day one", "title_ko": "첫날"}

TAKEAWAY = {"en": "## Takeaway", "ko": "## 오늘의 교훈"}


def post(lang, ref, permalink=None, body="Some notes.", takeaway=True, date="2026-10-30", slug="first-day"):
    front = {
        "layout": "post",
        "title": f"{ref} {lang}",
        "date": f"{date} 09:00:00 +0900",
        "lang": lang,
        "ref": ref,
        "days": "2026-10-29 – 2026-10-30",
        "permalink": permalink if permalink is not None else f"/{lang}/{ref}/",
        "summary": "One line.",
    }
    text = "---\n" + yaml.safe_dump(front, allow_unicode=True, sort_keys=False) + "---\n\n" + body + "\n"
    if takeaway:
        text += f"\n{TAKEAWAY[lang]}\n\nLesson.\n"
    return lang, f"{date}-{slug}.md", text


def make_site(root, episodes, posts):
    root = Path(root)
    (root / "_data").mkdir(parents=True)
    (root / "_data" / "episodes.yml").write_text(
        yaml.safe_dump(episodes, allow_unicode=True), encoding="utf-8"
    )
    for lang, name, text in posts:
        folder = root / "_posts" / lang
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(text, encoding="utf-8")
    return root


def test_valid_pair_passes(tmp_path):
    site = make_site(tmp_path, [EP1], [post("en", "day-01"), post("ko", "day-01")])
    assert check_site(site) == []


def test_empty_site_passes(tmp_path):
    assert check_site(make_site(tmp_path, [], [])) == []


def test_missing_ko_pair_is_reported(tmp_path):
    site = make_site(tmp_path, episodes=[EP1], posts=[post("en", "day-01")])
    assert any("day-01" in p and "ko" in p for p in check_site(site))


def test_permalink_must_match_lang_and_ref(tmp_path):
    site = make_site(tmp_path, episodes=[EP1],
                     posts=[post("en", "day-01", permalink="/day-01/"), post("ko", "day-01")])
    assert any("permalink" in p for p in check_site(site))


def test_liquid_outside_raw_is_reported(tmp_path):
    body_ok = "{% raw %}{{ name }}{% endraw %}"
    body_bad = "template {{ name }}"
    assert check_site(make_site(tmp_path / "a", [EP1], [post("en","day-01",body=body_ok), post("ko","day-01")])) == []
    assert any("liquid" in p for p in check_site(make_site(tmp_path / "b", [EP1], [post("en","day-01",body=body_bad), post("ko","day-01")])))


def test_takeaway_heading_required_once(tmp_path):
    site = make_site(tmp_path, [EP1], [post("en","day-01", takeaway=False), post("ko","day-01")])
    assert any("Takeaway" in p for p in check_site(site))
    twice = post("ko", "day-01", body="## 오늘의 교훈\n\nfirst")
    site2 = make_site(tmp_path / "b", [EP1], [post("en", "day-01"), twice])
    assert any("오늘의 교훈" in p for p in check_site(site2))


def test_missing_front_matter_key_and_bad_lang(tmp_path):
    lang, name, text = post("en", "day-01")
    text = text.replace("summary: One line.\n", "")
    site = make_site(tmp_path, [EP1], [(lang, name, text), post("ko", "day-01")])
    assert any("summary" in p for p in check_site(site))
    lang, name, text = post("ko", "day-01")
    site2 = make_site(tmp_path / "b", [EP1], [post("en", "day-01"), (lang, name, text.replace("lang: ko", "lang: jp"))])
    assert any("lang" in p for p in check_site(site2))


def test_episodes_and_posts_must_agree(tmp_path):
    no_posts = make_site(tmp_path / "a", [EP1], [])
    assert any("day-01" in p and "en" in p for p in check_site(no_posts))
    stray = make_site(tmp_path / "b", [], [post("en", "day-01"), post("ko", "day-01")])
    assert any("day-01" in p and "episodes.yml" in p for p in check_site(stray))


def test_pair_must_share_date_and_slug(tmp_path):
    site = make_site(tmp_path, [EP1], [post("en", "day-01"), post("ko", "day-01", slug="other")])
    assert any("day-01" in p and "file name" in p for p in check_site(site))


def test_cli_exit_code(tmp_path):
    assert main([str(make_site(tmp_path / "a", [EP1], [post("en", "day-01"), post("ko", "day-01")]))]) == 0
    assert main([str(make_site(tmp_path / "b", [EP1], [post("en", "day-01")]))]) == 1
