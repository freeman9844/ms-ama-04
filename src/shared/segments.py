"""공정성 세그먼트(업종·지역·업력 구간)와 운영 지표용 합성 신청자 균형 격자."""

from dataclasses import dataclass
from typing import Any

AGE_BANDS = ("<1년", "1~3년", "3년+")


def age_band(months: int) -> str:
    if months < 12:
        return AGE_BANDS[0]
    return AGE_BANDS[1] if months < 36 else AGE_BANDS[2]


def region_of(region: str) -> str:
    return region.split()[0]


def segment_of(features: Any) -> dict[str, str] | None:
    try:
        return {
            "industry": str(features["industry"]),
            "region": region_of(features["region"]),
            "business_age_band": age_band(int(features["business_age_months"])),
        }
    except (KeyError, TypeError, ValueError, IndexError, AttributeError):
        return None


LOAD_ARCHETYPES = ("healthy", "mismatch", "stress", "thin")
# 온라인 마켓플레이스 셀러 업종. 부하 격자는 앞 6개를 쓴다.
SELLER_INDUSTRIES = ("패션의류", "뷰티", "식품", "리빙", "디지털", "반려동물", "유아동", "스포츠", "핸드메이드", "도서·문구")
LOAD_INDUSTRIES = SELLER_INDUSTRIES[:6]
LOAD_REGIONS = ("서울 성동구", "부산 해운대구", "경기 성남시")
LOAD_AGES = (8, 24, 38)
LOAD_PER_ARCHETYPE = 12
LOAD_SEED_BASE = 5000


@dataclass(frozen=True)
class LoadApplicant:
    applicant_id: str
    archetype: str
    seed: int
    industry: str
    region: str
    business_age_months: int


def _grid() -> tuple[LoadApplicant, ...]:
    rows = []
    for j in range(LOAD_PER_ARCHETYPE):  # j 우선: 원형이 번갈아 나오게(부분 제출도 균형)
        for a, arch in enumerate(LOAD_ARCHETYPES):
            rows.append(
                LoadApplicant(
                    applicant_id=f"load-{arch}-{j + 1:02d}",
                    archetype=arch,
                    seed=LOAD_SEED_BASE + a * 100 + j,
                    industry=LOAD_INDUSTRIES[(j + a) % len(LOAD_INDUSTRIES)],
                    region=LOAD_REGIONS[(j + j // 3 + a) % 3],
                    business_age_months=LOAD_AGES[(j + 2 * (j // 3) + a) % 3],
                )
            )
    return tuple(rows)


LOAD_GRID = _grid()
