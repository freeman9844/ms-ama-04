"""상환 스케줄 공유 규칙(스펙 §3.1). Mock 사후 흐름과 오케스트레이터 원장이 같은 상환일을 쓴다."""

from datetime import date, timedelta

from shared.krcalendar import next_business_day, previous_business_day

LENDER_DEBIT = "에이엠에이렌딩 대출상환"
CYCLE_DAYS = 30
REMINDER_DAYS = 3


def due_dates(start: date, days: int) -> list[date]:
    """start부터 days일 범위 안의 상환일. 휴일이면 다음 영업일, 범위를 넘으면 앞 영업일."""
    last = start + timedelta(days=days - 1)
    out: list[date] = []
    k = 1
    while (d := start + timedelta(days=CYCLE_DAYS * k)) <= last:
        due = next_business_day(d)
        out.append(due if due <= last else previous_business_day(d))
        k += 1
    return out
