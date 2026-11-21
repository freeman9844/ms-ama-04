---
layout: default
lang: en
---
<p class="eyebrow">Azure / Engineering log</p>
<h1>Agent Dev Diary</h1>
<p>Building a cash-flow lending demo with GitHub Copilot CLI. Each entry follows one engineering decision from the question we asked to the evidence we could actually verify.</p>

<p lang="ko">에이전트와 함께 Azure 현금흐름 대출 데모를 만들며 남긴 개발 일지입니다. 무엇을 바꿨는지보다 왜 바꿨고 어디까지 확인했는지를 기록합니다.</p>

<ul>
  <li><a href="{{ "/en/" | relative_url }}">Read in English</a></li>
  <li><a href="{{ "/ko/" | relative_url }}" lang="ko">한국어로 읽기</a></li>
</ul>

<h2>Field notes / 개발 기록</h2>
<ol class="timeline">
{%- for ep in site.data.episodes reversed -%}
  {%- assign en = nil -%}
  {%- assign ko = nil -%}
  {%- for p in site.posts -%}
    {%- if p.ref == ep.ref and p.lang == "en" -%}{%- assign en = p -%}{%- endif -%}
    {%- if p.ref == ep.ref and p.lang == "ko" -%}{%- assign ko = p -%}{%- endif -%}
  {%- endfor -%}
  {%- if en or ko %}
<li><span class="stamp">{{ ep.days | escape }}</span><div>{% if en %}<a href="{{ en.url | relative_url }}">{{ en.title | escape }}</a>{% endif %}{% if ko %}<br><a lang="ko" href="{{ ko.url | relative_url }}">{{ ko.title | escape }}</a>{% endif %}</div></li>
  {%- endif -%}
{%- endfor %}
</ol>
