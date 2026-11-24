"""Completion as observed across the separate consent and deletion-proof records."""


def proof_is_current(consent: dict | None, proof: dict | None) -> bool:
    return bool(consent and proof and proof.get("complete")
                and not consent.get("raw_write_count") and not consent.get("raw_repair_pending")
                and not consent.get("assessment_runs") and not consent.get("foundry_repair_pending")
                and proof.get("foundry_generation", 0) == consent.get("foundry_generation", 0)
                and proof.get("raw_repair_generation", 0) == consent.get("raw_repair_generation", 0))
