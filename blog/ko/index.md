---
layout: page
title: 한국어
lang: ko
permalink: /ko/
---
{%- assign listed = 0 -%}
<ul class="post-list">
{%- for ep in site.data.episodes reversed -%}
  {%- assign entry = nil -%}
  {%- for p in site.posts -%}
    {%- if p.ref == ep.ref and p.lang == "ko" -%}
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
<p>아직 글이 없습니다.</p>
{%- endif %}

<p><a href="{{ "/en/" | relative_url }}" lang="en">English list</a></p>
