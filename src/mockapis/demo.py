"""데모 신청자 구성과 환경설정 로더."""

import os
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone

from mockapis.synth import Archetype, SyntheticCase, generate_case

KST = timezone(timedelta(hours=9), "KST")

# (applicant_id, archetype, seed, inject_prompt)
DEMO_APPLICANTS: tuple[tuple[str, Archetype, int, bool], ...] = (
    ("demo-cafe-001", Archetype.HEALTHY_SEASONAL, 101, False),
    ("demo-mismatch-002", Archetype.TAX_MISMATCH, 102, False),
    ("demo-stress-003", Archetype.STRESSED, 103, False),
    ("demo-thin-004", Archetype.THIN_FILE, 104, False),
    ("demo-injection-005", Archetype.HEALTHY_SEASONAL, 105, True),
)


def build_demo_cases(as_of: date) -> dict[str, SyntheticCase]:
    return {
        applicant_id: generate_case(applicant_id, archetype, as_of, seed, inject_prompt=inject)
        for applicant_id, archetype, seed, inject in DEMO_APPLICANTS
    }


def load_secret(env: Mapping[str, str] = os.environ) -> bytes:
    value = env.get("MOCK_CONSENT_SECRET", "")
    if len(value) < 32:
        raise RuntimeError("MOCK_CONSENT_SECRET는 32자 이상이어야 한다")
    return value.encode()


def resolve_as_of(env: Mapping[str, str] = os.environ, now: datetime | None = None) -> date:
    if raw := env.get("MOCK_AS_OF"):
        return date.fromisoformat(raw)
    return (now or datetime.now(KST)).astimezone(KST).date()
