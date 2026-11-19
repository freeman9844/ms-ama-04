---
layout: default
lang: en
---
<p>AMALend is an Azure demo for cash-flow lending, built with GitHub Copilot CLI as an internal architecture capstone. These engineering notes trace the work from October 26 through November 19, 2026: how we scoped model calls, kept decisions outside the model, and checked that a successful deployment actually served a request. Each entry records what I asked the agent to do, what the evidence showed, and where that evidence stopped. The series is anonymous and available in English and Korean.</p>

<p lang="ko">AMALend는 사내 아키텍트 과정의 캡스톤으로 GitHub Copilot CLI와 함께 구축한 Azure 현금흐름 대출 데모입니다. 이 기술 일지는 2026년 10월 26일부터 11월 19일까지의 설계와 검증을 기록합니다. 신청 한 건이 모델을 몇 번 호출하는지, 대출 결정은 누가 내리는지, 배포 성공 뒤 실제 요청은 어디까지 도달했는지를 다룹니다. 각 편에서 에이전트에게 맡긴 일과 직접 확인한 결과, 그 결과만으로는 판단할 수 없는 점을 구분했습니다. 모든 글은 익명으로 한국어와 영어로 제공합니다.</p>

<ul>
  <li><a href="{{ "/en/" | relative_url }}">Read in English</a></li>
  <li><a href="{{ "/ko/" | relative_url }}" lang="ko">한국어로 읽기</a></li>
</ul>

<h2>Episodes / 편 목록</h2>
<table>
<thead><tr><th>Days</th><th>English</th><th lang="ko">한국어</th></tr></thead>
<tbody>
{%- for ep in site.data.episodes -%}
  {%- assign en = nil -%}
  {%- assign ko = nil -%}
  {%- for p in site.posts -%}
    {%- if p.ref == ep.ref and p.lang == "en" -%}{%- assign en = p -%}{%- endif -%}
    {%- if p.ref == ep.ref and p.lang == "ko" -%}{%- assign ko = p -%}{%- endif -%}
  {%- endfor -%}
  {%- if en or ko %}
<tr><td>{{ ep.days | escape }}</td><td>{% if en %}<a href="{{ en.url | relative_url }}">{{ en.title | escape }}</a>{% endif %}</td><td lang="ko">{% if ko %}<a href="{{ ko.url | relative_url }}">{{ ko.title | escape }}</a>{% endif %}</td></tr>
  {%- endif -%}
{%- endfor %}
</tbody>
</table>
