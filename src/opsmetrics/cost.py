"""실측 토큰 × 단가 = AI 비용, 고정비 ÷ 가정 물량. 캐시 할인 미반영(상한 추정)."""

from typing import Any

MANUAL_USD = 3500.0  # Mills & McCarthy(HBS WP 15-004): 소상공인 대출 1건 처리 원가 $3,000~4,000의 중간값
TICKET_USD = 6000.0
VOLUMES = (100, 1000, 10000)
UNKNOWN_MODEL = "(unknown)"  # 모델명 없는 usage는 단가 미상으로 집계


def ai_cost(docs: list[dict[str, Any]], prices: dict[str, dict[str, Any]]) -> dict[str, Any]:
    by_model: dict[str, dict[str, Any]] = {}
    by_step: dict[str, dict[str, Any]] = {}
    for p in docs:
        for s in p.get("steps") or []:
            for u in s.get("usage") or []:
                model = u.get("model") or UNKNOWN_MODEL
                m = by_model.setdefault(model, {"input_tokens": 0, "output_tokens": 0, "calls": 0})
                st = by_step.setdefault(s.get("step") or UNKNOWN_MODEL,
                                        {"input_tokens": 0, "output_tokens": 0, "calls": 0, "cost_usd": 0.0,
                                         "unpriced_calls": 0})
                tin, tout, calls = (int(u.get(k) or 0) for k in ("input_tokens", "output_tokens", "calls"))
                for target in (m, st):
                    target["input_tokens"] += tin
                    target["output_tokens"] += tout
                    target["calls"] += calls
                price = prices.get(model) or {}
                pin, pout = price.get("input_per_1m"), price.get("output_per_1m")
                if pin is None or pout is None:
                    st["unpriced_calls"] += calls
                else:
                    st["cost_usd"] += tin / 1e6 * pin + tout / 1e6 * pout
    total, unpriced = 0.0, 0
    for model, m in by_model.items():
        price = prices.get(model) or {}
        pin, pout = price.get("input_per_1m"), price.get("output_per_1m")
        if pin is None or pout is None:
            m["cost_usd"], unpriced = None, unpriced + m["calls"]
            continue
        m["cost_usd"] = m["input_tokens"] / 1e6 * pin + m["output_tokens"] / 1e6 * pout
        total += m["cost_usd"]
    n = len(docs)
    return {
        "by_model": by_model,
        "by_step": by_step,
        "total_usd": total,
        "applications": n,
        "per_application_usd": total / n if n else None,
        "unpriced_calls": unpriced,
    }


def unit_economics(
    ai_per_app: float | None,
    fixed_monthly_usd: float | None,
    volumes: tuple[int, ...] = VOLUMES,
    manual_usd: float = MANUAL_USD,
    ticket_usd: float = TICKET_USD,
) -> list[dict[str, Any]]:
    rows = []
    for v in volumes:
        fixed = fixed_monthly_usd / v if fixed_monthly_usd is not None else None
        total = None if ai_per_app is None or fixed is None else round(ai_per_app + fixed, 6)
        rows.append({
            "volume": v,
            "ai_usd": ai_per_app,
            "fixed_usd": fixed,
            "total_usd": total,
            "vs_manual": total / manual_usd if total is not None else None,
            "vs_ticket": total / ticket_usd if total is not None else None,
        })
    return rows
