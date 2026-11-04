"""데모 신청자 구성과 환경설정 로더."""

import os
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone

from mockapis.postloan import FlowScenario, PostLoanFlow, generate_postloan
from mockapis.sellerdata.personas import DEMO_PERSONAS
from mockapis.synth import Archetype, SyntheticCase, generate_case, load_persona
from shared.segments import LOAD_GRID

KST = timezone(timedelta(hours=9), "KST")

# (applicant_id, archetype, seed, inject_prompt). ID 순서는 shared.sellers.DEMO_SELLERS와 같다.
DEMO_APPLICANTS: tuple[tuple[str, Archetype, int, bool], ...] = (
    ("seller-dailylook-01", Archetype.HEALTHY_SEASONAL, 101, False),
    ("seller-mangwon-02", Archetype.TAX_MISMATCH, 102, False),
    ("seller-glowlab-03", Archetype.STRESSED, 103, False),
    ("seller-knitroom-04", Archetype.THIN_FILE, 104, False),
    ("seller-candle-05", Archetype.HEALTHY_SEASONAL, 105, True),
    ("seller-gearstore-06", Archetype.HEALTHY_SEASONAL, 106, False),
    ("seller-petfood-07", Archetype.HEALTHY_SEASONAL, 107, False),
    ("seller-ceramic-08", Archetype.HEALTHY_SEASONAL, 108, False),
    ("seller-kimchi-09", Archetype.TAX_MISMATCH, 109, False),
    ("seller-linen-10", Archetype.HEALTHY_SEASONAL, 110, False),
    ("seller-skinlab-11", Archetype.STRESSED, 111, False),
    ("seller-kitchen-12", Archetype.TAX_MISMATCH, 112, False),
    ("seller-cable-13", Archetype.STRESSED, 113, False),
    ("seller-leather-14", Archetype.THIN_FILE, 114, False),
    ("seller-tangerine-15", Archetype.HEALTHY_SEASONAL, 115, False),
    ("seller-petcare-16", Archetype.TAX_MISMATCH, 116, False),
    ("seller-tint-17", Archetype.THIN_FILE, 117, False),
    ("seller-sofa-18", Archetype.STRESSED, 118, False),
    ("seller-streetfit-19", Archetype.THIN_FILE, 119, False),
    ("seller-bakery-20", Archetype.HEALTHY_SEASONAL, 120, False),
)


def build_demo_cases(as_of: date) -> dict[str, SyntheticCase]:
    return {
        applicant_id: generate_case(
            applicant_id, archetype, as_of, seed, inject_prompt=inject, persona=DEMO_PERSONAS[applicant_id]
        )
        for applicant_id, archetype, seed, inject in DEMO_APPLICANTS
    }


LOAD_ARCHETYPE = {
    "healthy": Archetype.HEALTHY_SEASONAL,
    "mismatch": Archetype.TAX_MISMATCH,
    "stress": Archetype.STRESSED,
    "thin": Archetype.THIN_FILE,
}


def build_load_cases(as_of: date) -> dict[str, SyntheticCase]:
    """운영 지표 배치용 합성 신청자(원형 × 업종·지역·업력 균형 격자). 같은 업종 안 순번으로 이름이 겹치지 않는다."""
    seen: dict[str, int] = {}
    cases = {}
    for a in LOAD_GRID:
        index = seen[a.industry] = seen.get(a.industry, -1) + 1
        cases[a.applicant_id] = generate_case(
            a.applicant_id,
            LOAD_ARCHETYPE[a.archetype],
            as_of,
            a.seed,
            persona=load_persona(a.industry, a.region, index),
            business_age_months=a.business_age_months,
        )
    return cases

# 대출 실행 이후 흐름: 심사 때 건강했던 셀러가 시즌 재고 선매입 뒤 고금리 대출을 쌓는다(조기경보 데모).
# 주인공은 패션의류 seller-dailylook-01이고, 업종이 다른 건강 셀러 3곳도 같은 흐름을 탄다. 나머지는 정상 상환.
STACKING_APPLICANTS = frozenset({"seller-dailylook-01", "seller-gearstore-06", "seller-linen-10", "seller-tangerine-15"})
POSTLOAN_SEED_OFFSET = 1000


def build_postloan_flows(cases: Mapping[str, SyntheticCase], as_of: date) -> dict[str, PostLoanFlow]:
    seeds = {applicant_id: seed for applicant_id, _, seed, _ in DEMO_APPLICANTS}
    seeds.update({a.applicant_id: a.seed for a in LOAD_GRID})
    return {
        applicant_id: generate_postloan(
            case,
            FlowScenario.STACKING if applicant_id in STACKING_APPLICANTS else FlowScenario.STABLE,
            as_of,
            seeds.get(applicant_id, 0) + POSTLOAN_SEED_OFFSET,
        )
        for applicant_id, case in cases.items()
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
