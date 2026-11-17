"""심사역 결정 검증(fail-closed, 스펙 §4.1).

- 정책 결과가 없거나 ineligible이면 심사역이 예외 승인을 확인(`policy_override`)하고 사유를 적어야만
  승인할 수 있다. 예외 승인 금액은 상품 최대 한도 이하다.
- 승인 금액은 정책 한도 이하만 허용한다. refer 건 승인과 거절은 사유가 필요하다.
- 조건부 승인은 P9에서 폐지했다(`conditional_retired`). 과거 문서를 읽기 위해 모델의 값은 남긴다.
- 이의 재심사는 정책 밖 예외 승인을 지원하지 않는다(`rereview_outside_policy`). 예외 승인은 원 심사 결정에서만 한다.
"""

from typing import Any

from pydantic import ValidationError

from journey.models import OfficerDecision

# 상품 최대 한도(`src/policy/rulesets/v4.yaml`의 product.max_amount_krw). 테스트가 값 일치를 확인한다.
OVERRIDE_MAX_AMOUNT_KRW = 10_000_000
REREVIEW_ERRORS = {
    "rereview_outside_policy": "정책 결과가 거절이거나 없는 건은 이의 재심사로 승인할 수 없습니다. 예외 승인은 원 심사 결정에서만 할 수 있습니다.",
}


def validate_officer_decision(
    raw: Any, policy: dict[str, Any] | None
) -> tuple[OfficerDecision | None, str | None]:
    try:
        d = OfficerDecision.model_validate(raw)
    except ValidationError:
        return None, "invalid_payload"
    if d.decision == "conditional":
        return None, "conditional_retired"
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
    if policy["decision"] == "refer" and not d.reason.strip():
        return None, "reason_required"
    return d, None


def validate_rereview_decision(
    raw: Any, policy: dict[str, Any] | None
) -> tuple[OfficerDecision | None, str | None]:
    """이의 재심사 결정 검증. 재심사 메시지에는 예외 승인 확인이 없어 정책 밖 승인은 별도 코드로 거부한다."""
    d, error = validate_officer_decision(raw, policy)
    if error == "override_required":
        return None, "rereview_outside_policy"
    return d, error


def _validate_override(d: OfficerDecision) -> tuple[OfficerDecision | None, str | None]:
    if not d.policy_override:
        return None, "override_required"
    if not d.reason.strip():
        return None, "reason_required"
    if d.amount_krw is None:
        return None, "amount_required"
    if d.amount_krw > OVERRIDE_MAX_AMOUNT_KRW:
        return None, "amount_exceeds_product"
    return d, None
