"""Replay seeded demo seller fixtures locally; emit paired policy outputs, never loan actions."""

import argparse
import json
from collections.abc import Mapping
from datetime import date

from agents.features import compute_underwriting
from evals.datasets import AS_OF, REQUESTED_AMOUNT_KRW, REQUESTED_TENOR_MONTHS
from mockapis.demo import build_demo_cases
from mockapis.synth import SyntheticCase
from policy.engine import evaluate
from policy.rules import RuleSet, rules_for_version
from shared.contracts import MarketplaceStore

RULES_VERSION = "2026.11-v4"
CONSOLE_DATE = date(2026, 11, 20)
CONSOLE_CASES = ("seller-bakery-20", "seller-knitroom-04", "seller-petfood-07", "seller-ceramic-08")


def _arm(case: SyntheticCase, rules: RuleSet, marketplace: MarketplaceStore | None, console_cohort: bool) -> dict:
    uw = compute_underwriting(
        case.profile.applicant_id, case.profile, case.transactions, tax_filings=case.tax_filings if console_cohort else (),
        as_of=CONSOLE_DATE if console_cohort else AS_OF, requested_amount_krw=5_000_000 if console_cohort else REQUESTED_AMOUNT_KRW,
        requested_tenor_months=REQUESTED_TENOR_MONTHS, pg_settlements=case.pg_settlements if console_cohort else (), marketplace=marketplace,
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


def compare(cases: Mapping[str, SyntheticCase], *, console_cohort: bool = False) -> dict:
    rules = rules_for_version(RULES_VERSION)
    if rules is None:
        raise ValueError(f"Missing pilot ruleset: {RULES_VERSION}")
    pairs = []
    for case_id, case in sorted(cases.items()):
        bank = _arm(case, rules, None, console_cohort)
        market = _arm(case, rules, case.marketplace, console_cohort)
        pairs.append({
            "case_id": case_id, "bank": bank, "bank_marketplace": market,
            "decision_changed": market["decision"] != bank["decision"],
            "added_reasons": sorted(set(market["reason_codes"]) - set(bank["reason_codes"])),
            "limit_delta_krw": market["limit_krw"] - bank["limit_krw"],
        })
    return {
        "basis": "synthetic", "rules_version": rules.version, "as_of": (CONSOLE_DATE if console_cohort else AS_OF).isoformat(),
        "requested_amount_krw": 5_000_000 if console_cohort else REQUESTED_AMOUNT_KRW, "requested_tenor_months": REQUESTED_TENOR_MONTHS,
        "common_sources": ["account_profile", "bank_transactions"] + (["tax", "pg"] if console_cohort else []),
        "excluded_sources": [] if console_cohort else ["tax", "pg"],
        "paired_cases": len(pairs),
        "marketplace_observed_pairs": sum(p["bank_marketplace"]["marketplace_available"] for p in pairs),
        "decision_changed_pairs": sum(p["decision_changed"] for p in pairs),
        "reasons_only_pairs": sum(not p["decision_changed"] and not p["limit_delta_krw"]
                                  and set(p["bank"]["reason_codes"]) != set(p["bank_marketplace"]["reason_codes"]) for p in pairs),
        "limit_changed_pairs": sum(p["limit_delta_krw"] != 0 for p in pairs),
        "pairs": pairs,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--console-cohort", action="store_true", help="four 5M KRW console scenarios including tax and PG")
    console = parser.parse_args().console_cohort
    cases = build_demo_cases(CONSOLE_DATE if console else AS_OF)
    print(json.dumps(compare({k: cases[k] for k in CONSOLE_CASES} if console else cases, console_cohort=console), indent=2))
