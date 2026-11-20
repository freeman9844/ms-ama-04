"""Replay seeded demo seller fixtures locally; emit paired policy outputs, never loan actions."""

import json
from collections.abc import Mapping

from agents.features import compute_underwriting
from evals.datasets import AS_OF, REQUESTED_AMOUNT_KRW, REQUESTED_TENOR_MONTHS
from mockapis.demo import build_demo_cases
from mockapis.synth import SyntheticCase
from policy.engine import evaluate
from policy.rules import RuleSet, rules_for_version
from shared.contracts import MarketplaceStore

RULES_VERSION = "2026.11-v4"


def _arm(case: SyntheticCase, rules: RuleSet, marketplace: MarketplaceStore | None) -> dict:
    uw = compute_underwriting(
        case.profile.applicant_id, case.profile, case.transactions, tax_filings=(),
        as_of=AS_OF, requested_amount_krw=REQUESTED_AMOUNT_KRW,
        requested_tenor_months=REQUESTED_TENOR_MONTHS, pg_settlements=(), marketplace=marketplace,
    )
    policy = evaluate(uw.features, rules)
    return {
        "marketplace_available": uw.marketplace["available"],
        "decision": policy.decision.value,
        "limit_krw": policy.max_amount_krw,
        "reason_codes": [reason.code for reason in policy.reason_codes],
        "human_override": None,
        "hands_on_minutes": None,
    }


def compare(cases: Mapping[str, SyntheticCase]) -> dict:
    rules = rules_for_version(RULES_VERSION)
    if rules is None:
        raise ValueError(f"Missing pilot ruleset: {RULES_VERSION}")
    pairs = []
    for case_id, case in sorted(cases.items()):
        bank = _arm(case, rules, None)
        market = _arm(case, rules, case.marketplace)
        pairs.append({
            "case_id": case_id, "bank": bank, "bank_marketplace": market,
            "decision_changed": market["decision"] != bank["decision"],
            "limit_delta_krw": market["limit_krw"] - bank["limit_krw"],
        })
    return {
        "basis": "synthetic", "rules_version": rules.version, "as_of": AS_OF.isoformat(),
        "requested_amount_krw": REQUESTED_AMOUNT_KRW, "requested_tenor_months": REQUESTED_TENOR_MONTHS,
        "common_sources": ["account_profile", "bank_transactions"], "excluded_sources": ["tax", "pg"],
        "paired_cases": len(pairs),
        "marketplace_observed_pairs": sum(p["bank_marketplace"]["marketplace_available"] for p in pairs),
        "decision_changed_pairs": sum(p["decision_changed"] for p in pairs),
        "limit_changed_pairs": sum(p["limit_delta_krw"] != 0 for p in pairs),
        "pairs": pairs,
    }


if __name__ == "__main__":
    print(json.dumps(compare(build_demo_cases(AS_OF)), indent=2))
