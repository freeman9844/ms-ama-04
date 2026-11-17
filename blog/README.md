# Blog source

Jekyll site (minima theme) published to GitHub Pages at
https://freeman9844.github.io/ms-ama-04/ by the workflow `.github/workflows/blog-pages.yml`
(at the repository root, not inside `blog/`).
Every entry exists in English and Korean. Keep it anonymous: no real names, employers,
customers, internal IDs, hosts or local paths.

## Checks

Run from the repository root (needs Python 3.12 and `pyyaml`):

```
python -m blog.tools.scan_sensitive --denylist <private-denylist> blog   # sensitive-data scan, exit 0 = clean
python -m blog.tools.check_site blog                                     # structure check, exit 0 = clean
python -m pytest -q -p no:cacheprovider blog/tools/tests                 # tool tests
```

`scan_sensitive` also reads an optional private denylist (`--denylist <file>` or the
`BLOG_DENYLIST` environment variable). Keep that file outside the repository and never commit it.

The workflow runs both checks (without the private denylist) before the Jekyll build;
either failing stops the deploy.

## Update workflow

1. Build the private digest bundle for the new days (outside the repository; never commit it).
2. Write the posts from the digests: the English post first, then the Korean one, and add the
   `ref` to `_data/episodes.yml`.
3. Run the checks above: `scan_sensitive` with `--denylist`, `check_site`, then `pytest`.
4. Push to `main`. Any change under `blog/` (or to the workflow file) triggers
   `.github/workflows/blog-pages.yml`, which checks, builds and deploys the site to
   https://freeman9844.github.io/ms-ama-04/.

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
title: "Picking Up Where a Login Error Left Off"
date: 2026-10-26 09:00:00 +0900   # first day of the range, 09:00 KST
lang: en              # en | ko
ref: day-01           # day-NN, shared by the en/ko pair
days: "2026-10-26"    # verbatim from _data/episodes.yml
permalink: /en/day-01/   # always /<lang>/<ref>/
summary: "One line shown in the list."
---
```

- File name: `_posts/<lang>/YYYY-MM-DD-<slug>.md`; the en and ko files of one `ref`
  use the same date and slug.
- Every `ref` must be listed in `_data/episodes.yml`, and every listed `ref` needs both posts.
- Exactly one takeaway heading: `## Takeaway` (en) or `## 오늘의 교훈` (ko).
- Wrap any literal `{{` or `{%` in a post with `{% raw %}` … `{% endraw %}`.
