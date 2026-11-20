"""Classify persisted opinions before projecting any narrative or counting recommendations."""

from collections.abc import Mapping
from typing import Any

from shared.amount_advice import has_proposal

CURRENT = ("approve", "refer", "decline")


def opinion_state(assessment: Any) -> str:
    if not isinstance(assessment, Mapping) or not assessment:
        return "none"
    version = assessment.get("schema_version", "1")
    if version == "1":
        return "legacy"
    review = assessment.get("review")
    rec = assessment.get("recommendation")
    if (version == "2" and isinstance(review, Mapping) and review.get("passed") is True
            and rec in CURRENT and "proposed_structure" not in assessment):
        return rec
    return "none"


def archived_reply(doc: Mapping[str, Any]) -> bool:
    """Old reply writers could copy unreviewed counterfactuals into customer correspondence."""
    assessment = (doc.get("record") or {}).get("assessment")
    return bool(doc.get("reply") and not doc["reply"].get("full_request_review")
                and isinstance(assessment, Mapping) and assessment.get("counterfactuals")
                and opinion_state(assessment) not in CURRENT)


def published_opinion(assessment: Any, requested: int | None = None) -> dict[str, Any] | None:
    """Keep the reviewed vote, not audit notes or explicit counteroffers embedded in prose."""
    if opinion_state(assessment) not in CURRENT:
        return None
    result = {k: assessment[k] for k in ("schema_version", "recommendation", "models") if k in assessment}
    result["review"] = {k: assessment["review"][k] for k in ("passed", "rounds") if k in assessment["review"]}
    summary = assessment.get("summary")
    result["summary"] = summary if isinstance(summary, str) and not has_proposal(summary, requested) else ""
    for key, field in (("claims", "text"), ("officer_checks", "text"), ("counterfactuals", "explanation")):
        if key in assessment:
            result[key] = [row for row in assessment[key] or () if isinstance(row, Mapping)
                           and isinstance(row.get(field), str) and not has_proposal(row[field], requested)
                           and not (key == "counterfactuals" and row.get("metric_id") in
                                    ("M:requested_amount_krw", "P:max_amount_krw", "M:requested_tenor_months"))]
    return result
