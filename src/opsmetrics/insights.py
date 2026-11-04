"""운영 지표 확장(P9): 결정 퍼널, 단계별 처리 시간, 사유 코드·플래그 빈도, 10분 단위 처리량, SLA 현황.

모두 `project()` 투영을 받는 순수 함수다. 값이 없으면 0건·None으로 두고 예외를 내지 않는다.
"""

from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from opsmetrics.labels import STEP_LABELS
from opsmetrics.slo import nearest_rank

POLICY_ORDER = ("eligible", "refer", "ineligible")
RECOMMENDATION_ORDER = ("approve", "conditional", "refer", "decline")
OFFICER_ORDER = ("approve", "decline")
NONE = "none"
APPROVE_SIDE = frozenset({"approve", "conditional"})
AWAITING = frozenset({"awaiting_officer", "escalated"})
BUCKET_MIN = 10
MAX_BUCKETS = 36  # 최근 6시간. 더 오래된 구간은 `earlier`로만 센다.


def _ordered(values: Iterable[str | None], order: tuple[str, ...]) -> dict[str, int]:
    counts = Counter(v if v else NONE for v in values)
    keys = [k for k in order if k in counts] + sorted(k for k in counts if k not in order and k != NONE)
    out = {k: counts[k] for k in keys}
    if counts[NONE]:
        out[NONE] = counts[NONE]
    return out


def _recommendation(p: dict[str, Any]) -> str | None:
    a = p.get("assessment")
    return a.get("recommendation") if isinstance(a, dict) else None


def decision_funnel(docs: list[dict[str, Any]]) -> dict[str, Any]:
    """정책 판정 → 에이전트 권고 → 심사역 결정 분포와 권고·결정 일치율, 정책 예외 승인 비율.

    일치율은 권고가 승인 쪽(approve·conditional)이나 decline이고 심사역이 결정한 건만 센다(refer는 방향이 없다).
    정책 예외 승인 비율의 분모는 심사역 승인 건수다.
    """
    policy = _ordered(((p.get("policy") or {}).get("decision") for p in docs), POLICY_ORDER)
    recommendation = _ordered((_recommendation(p) for p in docs), RECOMMENDATION_ORDER)
    officer = _ordered((p.get("officer_decision") for p in docs), OFFICER_ORDER)
    compared = agreed = 0
    for p in docs:
        rec, dec = _recommendation(p), p.get("officer_decision")
        if dec not in ("approve", "decline") or rec not in APPROVE_SIDE | {"decline"}:
            continue
        compared += 1
        agreed += (rec in APPROVE_SIDE) == (dec == "approve")
    approved = sum(p.get("officer_decision") == "approve" for p in docs)
    overrides = sum(bool(p.get("policy_override")) and p.get("officer_decision") == "approve" for p in docs)
    return {
        "policy": policy,
        "recommendation": recommendation,
        "officer": officer,
        "agreement": {"compared": compared, "agreed": agreed, "rate": agreed / compared if compared else None},
        "overrides": {"count": overrides, "approved": approved, "rate": overrides / approved if approved else None},
    }


def step_latency(docs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """단계별 `duration_ms`의 p50·p95(최근접 순위법). 순서는 파이프라인 순, 모르는 단계는 뒤에 이름순."""
    values: dict[str, list[float]] = {}
    for p in docs:
        for s in p.get("steps") or []:
            ms = s.get("duration_ms")
            if s.get("step") and isinstance(ms, int | float) and not isinstance(ms, bool) and ms >= 0:
                values.setdefault(s["step"], []).append(float(ms))
    order = [k for k in STEP_LABELS if k in values] + sorted(k for k in values if k not in STEP_LABELS)
    return {k: {"n": len(values[k]), "p50_ms": nearest_rank(values[k], 50), "p95_ms": nearest_rank(values[k], 95)}
            for k in order}


def _top(counter: Counter[str]) -> list[dict[str, Any]]:
    return [{"key": k, "count": n} for k, n in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))]


def reason_code_counts(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """정책 사유 코드별 신청 수(같은 신청 안의 중복은 한 번). 메시지는 처음 본 것을 쓴다."""
    counter: Counter[str] = Counter()
    messages: dict[str, str] = {}
    for p in docs:
        seen = set()
        for r in (p.get("policy") or {}).get("reason_codes") or []:
            code = r.get("code") if isinstance(r, dict) else r
            if not isinstance(code, str) or code in seen:
                continue
            seen.add(code)
            counter[code] += 1
            if isinstance(r, dict) and r.get("message"):
                messages.setdefault(code, r["message"])
    return [{**row, "message": messages.get(row["key"])} for row in _top(counter)]


def flag_counts(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _top(Counter(f for p in docs for f in {f for f in p.get("flags") or [] if isinstance(f, str)}))


def _parse(at: Any) -> datetime | None:
    if not isinstance(at, str):
        return None
    try:
        t = datetime.fromisoformat(at)
    except ValueError:
        return None
    return (t if t.tzinfo else t.replace(tzinfo=UTC)).astimezone(UTC)


def _floor(t: datetime, minutes: int) -> datetime:
    return t.replace(minute=t.minute - t.minute % minutes, second=0, microsecond=0)


def throughput(docs: list[dict[str, Any]], bucket_min: int = BUCKET_MIN,
               max_buckets: int = MAX_BUCKETS) -> dict[str, Any]:
    """10분 단위 접수 수와 심사역 대기 도달 수. 마지막 구간에서 거꾸로 `max_buckets`칸을 빈칸 포함해 연속으로 낸다."""
    received: Counter[datetime] = Counter()
    reached: Counter[datetime] = Counter()
    for p in docs:
        start = _parse(p.get("received_at") or p.get("submitted_at"))
        if start:
            received[_floor(start, bucket_min)] += 1
        end = next((t.get("at") for t in p.get("timeline") or [] if t.get("status") == "awaiting_officer"), None)
        end_t = _parse(end)
        if end_t:
            reached[_floor(end_t, bucket_min)] += 1
    keys = set(received) | set(reached)
    if not keys:
        return {"bucket_min": bucket_min, "buckets": [], "earlier": {"received": 0, "reached": 0}}
    last, step = max(keys), timedelta(minutes=bucket_min)
    first = max(min(keys), last - step * (max_buckets - 1))
    buckets, t = [], first
    while t <= last:
        buckets.append({"start": t.isoformat(), "received": received[t], "reached": reached[t]})
        t += step
    return {
        "bucket_min": bucket_min,
        "buckets": buckets,
        "earlier": {"received": sum(n for k, n in received.items() if k < first),
                    "reached": sum(n for k, n in reached.items() if k < first)},
    }


def sla_status(docs: list[dict[str, Any]]) -> dict[str, int]:
    """심사역 대기 중(awaiting_officer·escalated) 건수와 SLA 초과(escalated 표시) 건수."""
    return {
        "awaiting": sum(p.get("status") in AWAITING for p in docs),
        "escalated": sum(bool(p.get("sla_escalated")) or p.get("status") == "escalated" for p in docs),
    }
