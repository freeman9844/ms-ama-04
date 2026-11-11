"""조기경보 단계별 자동 대응(스펙 §5, L4 결정적 정책). LLM 없이 단계 판정·사전 승인 템플릿·시뮬레이션 시각 연락만 한다.

- 1단계: 이 재생에서 처음 높음 경보 → 잔고 부족 예상 알림(+ 이후 회차 5일 전 추가 안내).
- 2단계: 직전 경보와 이번 경보가 모두 높음 → 상환일 이동·분할납부 안내와 셀프 상환 플랜 링크.
- 각 단계는 재생마다 한 번만 쓴다(deferred여도 쓴 것으로 본다).
- 자동 발송은 시뮬레이션 날짜 10:00 KST에 보낸 것으로 보고 공정 연락 규칙(collections.fair_check)을 적용한다.
"""

from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from typing import Any

from agents.collections import KST, remind

SIM_HOUR = 10

# 사전 승인 템플릿. 자리표시자는 {due_on}, {amount}, {balance}, {link}만 쓴다(개인 식별 정보 없음).
TEMPLATES: dict[str, str] = {
    "balance_warning": "고객님, 에이엠에이렌딩입니다. {due_on} 상환일의 예상 계좌 잔고({balance})가 상환 금액({amount})보다 "
    "적을 것으로 보여 미리 안내드립니다. 상환일 전에 계좌 잔고를 확인해 미리 입금해 주시고, "
    "납부가 어려우시면 회신 주시면 함께 방법을 찾겠습니다.",
    "balance_warning_plain": "고객님, 에이엠에이렌딩입니다. {due_on} 상환일({amount})에 계좌 잔고가 부족할 것으로 보여 "
    "미리 안내드립니다. 상환일 전에 계좌 잔고를 확인해 미리 입금해 주시고, "
    "납부가 어려우시면 회신 주시면 함께 방법을 찾겠습니다.",
    "early_reminder": "고객님, 에이엠에이렌딩입니다. {due_on} 상환일에 {amount}이 자동이체될 예정이라 미리 안내드립니다. "
    "상환일 전에 계좌 잔고를 확인해 주시고, 납부가 어려우시면 미리 말씀해 주세요.",
    "self_plan_offer": "고객님, 에이엠에이렌딩입니다. 최근 자금 흐름을 고려해 {due_on} 상환일의 납부 방법을 바꾸실 수 있도록 "
    "안내드립니다. 상환일을 정산 지급일 뒤로 옮기거나, 다음 회차({amount})를 두 번에 나눠 납부하실 수 있고, "
    "지금 일정을 유지하셔도 됩니다. 아래 링크에서 원하시는 방법을 골라 주세요. {link}",
}


def sim_now(on: date) -> datetime:
    """자동 발송 시각: 시뮬레이션 날짜 10:00 KST."""
    return datetime.combine(on, time(SIM_HOUR), tzinfo=KST)


def next_stage(severities: Sequence[str], escalation: Mapping[str, Any]) -> int | None:
    """이번 경보로 보낼 단계. severities는 이 재생 경보의 심각도(이번 것 포함, 순서대로)."""
    if not severities or severities[-1] != "high":
        return None
    if not isinstance(escalation.get("stage1"), dict):
        return 1
    if not isinstance(escalation.get("stage2"), dict) and len(severities) >= 2 and severities[-2] == "high":
        return 2
    return None


def _krw(amount: int) -> str:
    return f"{amount:,}원"


def stage_message(stage: int, *, due_on: date, amount: int, balance: int | None, link: str) -> tuple[str, str]:
    """단계 안내 (script_id, 문구). 1단계는 예상 잔고가 있을 때만 금액을 넣는다."""
    values = {"due_on": due_on.isoformat(), "amount": _krw(amount), "link": link}
    if stage == 1:
        if balance is None:
            return "balance_warning", TEMPLATES["balance_warning_plain"].format(**values)
        return "balance_warning", TEMPLATES["balance_warning"].format(**values, balance=_krw(balance))
    if stage == 2:
        return "self_plan_offer", TEMPLATES["self_plan_offer"].format(**values)
    raise ValueError(f"unknown stage: {stage}")


def plan_link(applicant_id: str) -> str:
    return f"/borrower/{applicant_id}#self-plan"


def auto_contact(ref: str, script_id: str, text: str, contacts: Sequence[Mapping[str, Any]], on: date) -> dict[str, Any]:
    """조기경보 자동 발송 기록. 시간대는 시뮬레이션 10:00, 횟수는 7일 7회로 검사한다(걸리면 deferred, 재시도 없음)."""
    return remind(ref, contacts, on, sim_now(on), script_id=script_id, by="early_warning", text=text)
