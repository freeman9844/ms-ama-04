"""심사역 결정 검증(fail-closed, 스펙 §4.1).

- 정책 결과가 없거나 ineligible이면 심사역이 예외 승인을 확인(`policy_override`)하고 사유를 적어야만
  승인할 수 있다. 예외 승인 금액은 상품 최대 한도 이하다.
- 승인 금액은 정책 한도 이하만 허용한다. 조건부 승인은 조건이, refer 건 승인과 거절은 사유가 필요하다.
"""

from typing import Any

from pydantic import ValidationError

from journey.models import OfficerDecision

# 상품 최대 한도(`src/policy/rulesets/v1.yaml`의 product.max_amount_krw). 테스트가 값 일치를 확인한다.
OVERRIDE_MAX_AMOUNT_KRW = 5_000_000


def validate_officer_decision(
    raw: Any, policy: dict[str, Any] | None
) -> tuple[OfficerDecision | None, str | None]:
    try:
        d = OfficerDecision.model_validate(raw)
    except ValidationError:
        return None, "invalid_payload"
    if d.decision == "decline":
        d = d.model_copy(update={"policy_override": False})
        return (d, None) if d.reason.strip() else (None, "reason_required")
    if policy is None or policy.get("decision") not in ("eligible", "refer"):
        return _validate_override(d)
    d = d.model_copy(update={"policy_override": False})
    if d.amount_krw is None:
        return None, "amount_required"
    if d.amount_krw > int(policy.get("max_amount_krw") or 0):
        return None, "amount_exceeds_policy"
    if d.decision == "conditional" and not [c for c in d.conditions if c.strip()]:
        return None, "conditions_required"
    if policy["decision"] == "refer" and not d.reason.strip():
        return None, "reason_required"
    return d, None


def _validate_override(d: OfficerDecision) -> tuple[OfficerDecision | None, str | None]:
    if not d.policy_override:
        return None, "override_required"
    if not d.reason.strip():
        return None, "reason_required"
    if d.amount_krw is None:
        return None, "amount_required"
    if d.amount_krw > OVERRIDE_MAX_AMOUNT_KRW:
        return None, "amount_exceeds_product"
    if d.decision == "conditional" and not [c for c in d.conditions if c.strip()]:
        return None, "conditions_required"
    return d, None
