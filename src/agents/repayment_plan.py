"""셀프 상환 플랜(결정적, LLM 없음): 주 플랫폼 지급일 추정, 이동·분할·유지 옵션, 일정 교체, 흐름 덧씌우기.

- 이동: 예정 회차를 주 플랫폼 다음 지급일 다음 영업일로 옮긴다(상환일+10일 상한). 지급 이력이 없으면 내지 않는다.
- 분할: 다음 예정 회차를 원래 날짜(절반)와 15일 뒤 다음 영업일(나머지)로 나눈다. 합계는 그대로다.
- 유지: 항상 마지막에 둔다.
- 흐름 덧씌우기(apply_plan): 승인된 변경마다 applied_from 이후 원래 상환 출금을 빼고 새 날짜에 넣은 뒤 잔고를 다시 센다.
  applied_from 이전(공개된 주차) 거래는 건드리지 않는다. 새 날짜 잔고가 모자라면 출금을 넣지 않는다(미납).
"""

import statistics
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from agents.ledger_monitor import LedgerBaseline
from agents.repayment import item_amount, item_key
from shared.contracts import PgFlow, Transaction
from shared.krcalendar import add_business_days, next_business_day, previous_business_day
from shared.repayment import LENDER_DEBIT

PAYOUT_LOOKBACK = 9  # 지급 주기를 잴 최근 지급일 수
MOVE_CAP_DAYS = 10
SPLIT_GAP_DAYS = 15
_DEFAULT_DEBIT_CODE = "AUTO_DEBIT"


def main_platform(lb: LedgerBaseline | None) -> str | None:
    """대출 전 기준선에서 주평균 실지급액이 가장 큰 플랫폼(같으면 이름 순 앞)."""
    if lb is None or not lb.platform_weekly_net:
        return None
    return max(sorted(lb.platform_weekly_net), key=lambda p: lb.platform_weekly_net[p])


def payout_days(pg: PgFlow | None, platform: str | None, upto: date) -> list[date]:
    if pg is None or platform is None:
        return []
    return sorted({s.settled_on for s in pg.settlements
                   if s.platform == platform and s.net_krw > 0 and s.settled_on <= upto})


def next_payout(days: Sequence[date], after: date) -> date | None:
    """after 뒤 첫 지급일. 이력에 있으면 그날, 없으면 최근 지급 간격 중앙값을 주기로 마지막 지급일에서 늘려 잡는다."""
    known = sorted(set(days))
    if len(known) < 2:
        return None
    if later := next((d for d in known if d > after), None):
        return later
    recent = known[-PAYOUT_LOOKBACK:]
    period = max(1, round(statistics.median((b - a).days for a, b in zip(recent, recent[1:]))))
    d = known[-1] + timedelta(days=period)
    while d <= after:
        d += timedelta(days=period)
    return d


def _new_item(item: Mapping[str, Any], part: int | None, due: date, amount: int) -> dict[str, Any]:
    return {"no": item["no"], "part": part, "due_on": due.isoformat(), "amount_krw": amount,
            "from_due_on": item["due_on"]}


def plan_options(
    schedule: Sequence[Mapping[str, Any]],
    installment_krw: int,
    *,
    after: date,
    flow_end: date,
    payouts: Sequence[date],
) -> list[dict[str, Any]]:
    targets = sorted(
        (s for s in schedule if s["status"] == "scheduled" and date.fromisoformat(s["due_on"]) > after),
        key=lambda s: s["due_on"],
    )
    options: list[dict[str, Any]] = []
    moved: list[dict[str, Any]] = []
    replaces: list[str] = []
    for item in targets:
        due = date.fromisoformat(item["due_on"])
        payout = next_payout(payouts, due)
        if payout is None:
            break
        new = add_business_days(payout, 1)
        cap = due + timedelta(days=MOVE_CAP_DAYS)
        if new > cap:
            new = previous_business_day(cap)
        if new <= due or new > flow_end:
            continue
        moved.append(_new_item(item, item.get("part"), new, item_amount(item, installment_krw)))
        replaces.append(item_key(item))
    if moved:
        options.append({"option": "move", "items": moved, "replaces": replaces})
    # 이미 나뉜 회차는 다시 나누지 않는다(키가 겹치지 않게).
    if targets and not targets[0].get("part"):
        item = targets[0]
        due = date.fromisoformat(item["due_on"])
        second = next_business_day(due + timedelta(days=SPLIT_GAP_DAYS))
        if second <= flow_end:
            amount = item_amount(item, installment_krw)
            first = amount // 2
            options.append({
                "option": "split",
                "items": [_new_item(item, 1, due, first), _new_item(item, 2, second, amount - first)],
                "replaces": [item_key(item)],
            })
    options.append({"option": "keep", "items": [], "replaces": []})
    return options


def apply_change(schedule: Sequence[Mapping[str, Any]], change: Mapping[str, Any]) -> list[dict[str, Any]]:
    """change.replaces 키의 회차를 change.items로 바꾼 새 일정(상환일 순). 새 회차는 안내 기록 없이 예정 상태다."""
    gone = set(change.get("replaces") or ())
    out = [dict(s) for s in schedule if item_key(s) not in gone]
    for i in change.get("items") or ():
        out.append({
            "no": i["no"], "part": i.get("part"), "due_on": i["due_on"], "amount_krw": i["amount_krw"],
            "from_due_on": i.get("from_due_on"), "status": "scheduled", "reminded_on": None, "reminder_status": None,
            "early_reminded_on": None, "early_reminder_status": None, "txn_id": None, "case_id": None,
        })
    return sorted(out, key=lambda s: (s["due_on"], s["no"], s.get("part") or 0))


def _is_debit(t: Transaction) -> bool:
    return t.description == LENDER_DEBIT and t.amount_krw < 0


def _apply_one(txns: list[Transaction], change: Mapping[str, Any]) -> list[Transaction]:
    start = date.fromisoformat(change["applied_from"])
    head = [t for t in txns if t.booked_on < start]
    tail = [t for t in txns if t.booked_on >= start]
    if head:
        balance = head[-1].balance_after_krw
    elif txns:
        balance = txns[0].balance_after_krw - txns[0].amount_krw
    else:
        balance = 0
    before_days = {date.fromisoformat(b["due_on"]) for b in change.get("before") or ()}
    removed = [t for t in tail if t.booked_on in before_days and _is_debit(t)]
    removed_ids = {t.txn_id for t in removed}
    kept = [t for t in tail if t.txn_id not in removed_ids]
    code = next((t.txn_type_code for t in removed), None) or next(
        (t.txn_type_code for t in txns if _is_debit(t)), _DEFAULT_DEBIT_CODE
    )
    by_day: dict[date, list[Transaction]] = {}
    for t in kept:
        by_day.setdefault(t.booked_on, []).append(t)
    debits: dict[date, list[Mapping[str, Any]]] = {}
    for a in change.get("after") or ():
        d = date.fromisoformat(a["due_on"])
        if d >= start:
            debits.setdefault(d, []).append(a)
    out = list(head)
    for day in sorted(set(by_day) | set(debits)):
        for t in by_day.get(day, ()):
            balance += t.amount_krw
            out.append(t if t.balance_after_krw == balance else t.model_copy(update={"balance_after_krw": balance}))
        for a in debits.get(day, ()):
            amount = int(a["amount_krw"])
            if balance < amount:
                continue
            balance -= amount
            out.append(Transaction(txn_id=f"{change['change_id']}-{a['key']}", booked_on=day, amount_krw=-amount,
                                   balance_after_krw=balance, description=LENDER_DEBIT, txn_type_code=code))
    return out


def apply_plan(txns: Sequence[Transaction], history: Sequence[Mapping[str, Any]]) -> list[Transaction]:
    """승인된 일정 변경을 차례로 흐름에 덧씌운다. history가 비면 원래 거래 그대로."""
    out = list(txns)
    for change in history:
        out = _apply_one(out, change)
    return out
