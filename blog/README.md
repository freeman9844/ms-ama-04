# Blog source

Jekyll site (minima theme) published to GitHub Pages by `.github/workflows/blog-pages.yml`.
Every entry exists in English and Korean. Keep it anonymous: no real names, employers,
customers, internal IDs, hosts or local paths.

## Checks

Run from the repository root (needs Python 3.12 and `pyyaml`):

```
python -m blog.tools.scan_sensitive blog   # sensitive-data scan, exit 0 = clean
python -m blog.tools.check_site blog       # structure check, exit 0 = clean
python -m pytest -q blog/tools/tests       # tool tests
```

`scan_sensitive` also reads an optional private denylist (`--denylist <file>` or the
`BLOG_DENYLIST` environment variable). Never commit that file.

The workflow runs both checks before the Jekyll build; either failing stops the deploy.

## Layout

```
blog/
  _config.yml            site settings (baseurl /ms-ama-04)
  _data/episodes.yml     episode order: {ref, days, title_en, title_ko}
  _layouts/post.html     post layout (language switch + covered days)
  _includes/lang-switch.html
  _posts/en/             English posts
  _posts/ko/             Korean posts
  en/index.md, ko/index.md   per-language lists, in episodes.yml order
  index.md               landing page
  assets/main.scss
  tools/                 Python checks (excluded from the site)
```

## Post front matter

```yaml
---
layout: post
title: "..."
date: 2026-10-30 09:00:00 +0900
lang: en              # en | ko
ref: day-01           # day-NN, shared by the en/ko pair
days: "2026-10-29 – 2026-10-30"
permalink: /en/day-01/   # always /<lang>/<ref>/
summary: "One line shown in the list."
---
```

- File name: `_posts/<lang>/YYYY-MM-DD-<slug>.md`; the en and ko files of one `ref`
  use the same date and slug.
- Every `ref` must be listed in `_data/episodes.yml`, and every listed `ref` needs both posts.
- Exactly one takeaway heading: `## Takeaway` (en) or `## 오늘의 교훈` (ko).
- Wrap any literal `{{` or `{%` in a post with `{% raw %}` … `{% endraw %}`.
