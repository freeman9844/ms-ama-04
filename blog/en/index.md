---
layout: page
title: English
lang: en
permalink: /en/
---
{%- assign listed = 0 -%}
<ul class="post-list">
{%- for ep in site.data.episodes reversed -%}
  {%- assign entry = nil -%}
  {%- for p in site.posts -%}
    {%- if p.ref == ep.ref and p.lang == "en" -%}
      {%- assign entry = p -%}
      {%- break -%}
    {%- endif -%}
  {%- endfor -%}
  {%- if entry -%}
    {%- assign listed = listed | plus: 1 -%}
<li><span class="post-meta">{{ ep.days | escape }}</span><h3><a class="post-link" href="{{ entry.url | relative_url }}">{{ entry.title | escape }}</a></h3><p>{{ entry.summary | escape }}</p></li>
  {%- endif -%}
{%- endfor -%}
</ul>
{%- if listed == 0 %}
<p>No entries yet.</p>
{%- endif %}

<p><a href="{{ "/ko/" | relative_url }}" lang="ko">한국어 목록</a></p>
