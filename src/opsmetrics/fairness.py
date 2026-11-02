"""세그먼트별 추천 approve율·eligible율 격차. 업력 축은 규칙 R104로 설명되므로 경보 대상이 아니다."""

from typing import Any

from opsmetrics.projection import archetype_of

AXES = ("industry", "region", "business_age_band")
POLICY_EXPLAINED = frozenset({"business_age_band"})
RECOMMENDATIONS = ("approve", "conditional", "refer", "decline")


def _rates(group: list[dict[str, Any]]) -> dict[str, Any]:
    assessed = [p for p in group if p.get("assessment")]
    policed = [p for p in group if p.get("policy")]
    decided = [p for p in group if p.get("officer_decision")]
    recs = [p["assessment"].get("recommendation") for p in assessed]
    return {
        "n": len(group),
        "assessed": len(assessed),
        "approve_rate": recs.count("approve") / len(assessed) if assessed else None,
        "eligible_rate": sum(p["policy"].get("decision") == "eligible" for p in policed) / len(policed)
        if policed else None,
        "recommendations": {r: recs.count(r) for r in RECOMMENDATIONS},
        "officer_approve_rate": sum(p["officer_decision"] != "decline" for p in decided) / len(decided)
        if decided else None,
    }


def _gap(values: dict[str, dict[str, Any]], key: str = "approve_rate") -> float:
    rates = [v[key] for v in values.values() if not v.get("insufficient") and v.get(key) is not None]
    return round((max(rates) - min(rates)) * 100, 1) if len(rates) >= 2 else 0.0


def _by(docs: list[dict[str, Any]], axis: str) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for p in docs:
        value = (p.get("segment") or {}).get(axis)
        if value is not None:
            groups.setdefault(value, []).append(p)
    return groups


def fairness(docs: list[dict[str, Any]], gap_pp: float = 20.0, min_samples: int = 5) -> dict[str, Any]:
    axes, alerts = {}, []
    for axis in AXES:
        values = {}
        for value, group in sorted(_by(docs, axis).items()):
            r = _rates(group)
            r["insufficient"] = r["assessed"] < min_samples
            values[value] = r
        controlled = {}
        for arch in sorted({a for p in docs if (a := archetype_of(p.get("applicant_id")))}):
            sub = {v: {**_rates(g), "insufficient": len([p for p in g if p.get("assessment")]) < 2}
                   for v, g in _by([p for p in docs if archetype_of(p.get("applicant_id")) == arch], axis).items()}
            controlled[arch] = _gap(sub)
        gap = _gap(values)
        explained = axis in POLICY_EXPLAINED
        alert = (not explained) and gap > gap_pp
        if alert:
            alerts.append(axis)
        axes[axis] = {
            "values": values,
            "gap_pp": gap,
            "eligible_gap_pp": _gap(values, "eligible_rate"),
            "controlled": {"by_archetype": controlled, "max_pp": max(controlled.values(), default=0.0)},
            "policy_explained": explained,
            "alert": alert,
        }
    return {"threshold_pp": gap_pp, "min_samples": min_samples, "axes": axes, "alerts": alerts}
