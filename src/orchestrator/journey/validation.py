"""심사역 결정 검증(fail-closed, 스펙 §4.1).

- 정책 결과가 없거나 ineligible이면 거절만 허용한다.
- 승인 금액은 정책 한도 이하만 허용한다. 조건부 승인은 조건이, refer 건 승인과 거절은 사유가 필요하다.
"""

from typing import Any

from pydantic import ValidationError

from journey.models import OfficerDecision


def validate_officer_decision(
    raw: Any, policy: dict[str, Any] | None
) -> tuple[OfficerDecision | None, str | None]:
    try:
        d = OfficerDecision.model_validate(raw)
    except ValidationError:
        return None, "invalid_payload"
    if d.decision == "decline":
        return (d, None) if d.reason.strip() else (None, "reason_required")
    if policy is None or policy.get("decision") not in ("eligible", "refer"):
        return None, "decline_only"
    if d.amount_krw is None:
        return None, "amount_required"
    if d.amount_krw > int(policy.get("max_amount_krw") or 0):
        return None, "amount_exceeds_policy"
    if d.decision == "conditional" and not [c for c in d.conditions if c.strip()]:
        return None, "conditions_required"
    if policy["decision"] == "refer" and not d.reason.strip():
        return None, "reason_required"
    return d, None
