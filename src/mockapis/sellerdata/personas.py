"""셀러 페르소나: 상호·대표자·직원·거래처·채널 비중·매출 규모, 사업자등록번호."""

import random
from dataclasses import dataclass

from shared.segments import SELLER_INDUSTRIES
from shared.sellers import LOAD_BUSINESS_NAMES, LOAD_NAMES_PER_INDUSTRY, SELLER_BY_ID

BRN_WEIGHTS = (1, 3, 7, 1, 3, 7, 1, 3, 5)


@dataclass(frozen=True)
class Persona:
    business_name: str
    representative: str
    employees: tuple[str, str]
    suppliers: tuple[str, str]  # (상품 매입처, 포장재)
    courier: str
    industry: str
    region: str
    channels: tuple[tuple[str, float], ...]  # (채널, 매출 비중)
    monthly_sales_krw: int  # 계절 지수 1.0일 때 월 주문액


def brn_check_digit(first9: str) -> int:
    total = sum(int(c) * w for c, w in zip(first9, BRN_WEIGHTS))
    total += int(first9[8]) * 5 // 10
    return (10 - total % 10) % 10


def business_reg_no(rng: random.Random) -> str:
    """개인 과세사업자 형식(가운데 01~79) 사업자등록번호, 검증 숫자 포함."""
    first9 = f"{rng.randint(101, 899)}{rng.randint(1, 79):02d}{rng.randint(0, 9999):04d}"
    digits = first9 + str(brn_check_digit(first9))
    return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"


def is_valid_brn(value: str) -> bool:
    digits = value.replace("-", "")
    return len(digits) == 10 and digits.isdigit() and brn_check_digit(digits[:9]) == int(digits[9])


def _demo(applicant_id: str, representative: str, employees: tuple[str, str], suppliers: tuple[str, str],
          courier: str, region: str, weights: tuple[float, ...], monthly_sales_krw: int) -> Persona:
    s = SELLER_BY_ID[applicant_id]
    return Persona(s.business_name, representative, employees, suppliers, courier, s.industry, region,
                   tuple(zip(s.channels, weights)), monthly_sales_krw)


DEMO_PERSONAS: dict[str, Persona] = {
    "seller-dailylook-01": _demo("seller-dailylook-01", "김민수", ("이서연", "박지훈"),
                                 ("(주)동대문패브릭", "(주)박스왕포장"), "CJ대한통운", "서울 성동구",
                                 (0.45, 0.35, 0.2), 31_000_000),
    "seller-mangwon-02": _demo("seller-mangwon-02", "최영희", ("강수진", "오민재"),
                               ("(주)한끼식품 OEM", "(주)신선포장"), "롯데글로벌로지스", "서울 마포구",
                               (0.45, 0.4, 0.15), 27_000_000),
    "seller-glowlab-03": _demo("seller-glowlab-03", "정대호", ("한미래", "송유나"),
                               ("(주)코스랩 화장품제조", "(주)패키지원"), "한진택배", "부산 해운대구",
                               (0.5, 0.3, 0.2), 29_000_000),
    "seller-knitroom-04": _demo("seller-knitroom-04", "윤지아", ("임하늘", "조은별"),
                                ("(주)실과바늘상회", "(주)박스왕포장"), "우체국택배", "경기 성남시",
                                (0.7, 0.3), 12_000_000),
    "seller-candle-05": _demo("seller-candle-05", "서지우", ("문채원", "안준호"),
                              ("(주)소이왁스코리아", "(주)유리병상사"), "CJ대한통운", "서울 마포구",
                              (0.65, 0.35), 18_000_000),
    "seller-gearstore-06": _demo("seller-gearstore-06", "황재석", ("배수빈", "노경민"),
        ("(주)한빛전자유통", "(주)에어캡포장"), "CJ대한통운", "서울 용산구", (0.5, 0.35, 0.15), 42_000_000),
    "seller-petfood-07": _demo("seller-petfood-07", "신혜원", ("권태호", "유승아"),
        ("(주)네추럴펫푸드 OEM", "(주)신선포장"), "롯데글로벌로지스", "경기 수원시", (0.45, 0.4, 0.15), 22_000_000),
    "seller-ceramic-08": _demo("seller-ceramic-08", "장인호", ("허다솜", "남기현"),
        ("(주)여주백토상사", "(주)박스왕포장"), "우체국택배", "경기 이천시", (0.6, 0.4), 9_000_000),
    "seller-kimchi-09": _demo("seller-kimchi-09", "전순자", ("고은지", "양동훈"),
        ("(주)호남농산", "(주)신선포장"), "한진택배", "전북 전주시", (0.45, 0.4, 0.15), 25_000_000),
    "seller-linen-10": _demo("seller-linen-10", "류상철", ("백지수", "홍보람"),
        ("(주)대구원단상사", "(주)박스왕포장"), "CJ대한통운", "서울 중구", (0.5, 0.3, 0.2), 26_000_000),
    "seller-skinlab-11": _demo("seller-skinlab-11", "손예지", ("차민우", "구슬기"),
        ("(주)달빛화장품제조", "(주)패키지원"), "한진택배", "대구 수성구", (0.55, 0.3, 0.15), 24_000_000),
    "seller-kitchen-12": _demo("seller-kitchen-12", "오창수", ("엄가영", "하준혁"),
        ("(주)스텐리빙 제조", "(주)에어캡포장"), "롯데글로벌로지스", "인천 연수구", (0.5, 0.35, 0.15), 20_000_000),
    "seller-cable-13": _demo("seller-cable-13", "이강산", ("표나래", "지성훈"),
        ("(주)테크라인 유통", "(주)에어캡포장"), "CJ대한통운", "서울 구로구", (0.55, 0.25, 0.2), 33_000_000),
    "seller-leather-14": _demo("seller-leather-14", "문태식", ("석다연", "도은비"),
        ("(주)성수피혁", "(주)박스왕포장"), "우체국택배", "서울 성동구", (0.55, 0.45), 8_000_000),
    "seller-tangerine-15": _demo("seller-tangerine-15", "강미숙", ("고한별", "현동진"),
        ("(주)서귀포감귤유통", "(주)신선포장"), "우체국택배", "제주 서귀포시", (0.45, 0.4, 0.15), 19_000_000),
    "seller-petcare-16": _demo("seller-petcare-16", "김소희", ("방준기", "진세아"),
        ("(주)펫앤라이프 유통", "(주)박스왕포장"), "CJ대한통운", "경기 성남시", (0.55, 0.45), 17_000_000),
    "seller-tint-17": _demo("seller-tint-17", "박하나", ("육지원", "탁민지"),
        ("(주)코스랩 화장품제조", "(주)패키지원"), "한진택배", "서울 마포구", (0.7, 0.3), 11_000_000),
    "seller-sofa-18": _demo("seller-sofa-18", "나정훈", ("기유리", "마성재"),
        ("(주)호남가구목재", "(주)에어캡포장"), "경동택배", "광주 북구", (0.45, 0.35, 0.2), 38_000_000),
    "seller-streetfit-19": _demo("seller-streetfit-19", "최원빈", ("국수정", "봉재원"),
        ("(주)동대문패브릭", "(주)박스왕포장"), "CJ대한통운", "서울 중구", (0.6, 0.4), 14_000_000),
    "seller-bakery-20": _demo("seller-bakery-20", "윤복순", ("염태희", "길동욱"),
        ("(주)금강제분", "(주)신선포장"), "한진택배", "대전 유성구", (0.45, 0.4, 0.15), 18_000_000),
}

_SURNAMES = "김이박최정강조윤장임한오서신권황안송류전홍고문양손배백허유남"
_GIVEN = ("도윤", "서하", "지호", "하은", "민준", "아름", "다인", "우진", "세린", "민혁", "소율", "준서", "예린", "태윤",
          "유진", "서진", "도하", "가은", "도현", "정민", "시우", "지안", "건우", "수아", "현우", "채윤", "은우", "지유",
          "선우", "윤서", "주원", "하린", "승현", "예준", "다은", "지훈", "서윤", "민서", "재희", "나연")
_LOAD_CHANNELS: dict[str, tuple[tuple[str, float], ...]] = {
    "패션의류": (("스마트스토어", 0.45), ("쿠팡", 0.35), ("자사몰", 0.2)),
    "뷰티": (("스마트스토어", 0.5), ("쿠팡", 0.3), ("자사몰", 0.2)),
    "식품": (("쿠팡", 0.45), ("스마트스토어", 0.4), ("11번가", 0.15)),
    "리빙": (("스마트스토어", 0.5), ("쿠팡", 0.3), ("11번가", 0.2)),
    "디지털": (("쿠팡", 0.5), ("스마트스토어", 0.35), ("11번가", 0.15)),
    "반려동물": (("쿠팡", 0.5), ("스마트스토어", 0.35), ("자사몰", 0.15)),
}
_LOAD_SUPPLIERS = {"패션의류": "(주)신상패브릭", "뷰티": "(주)코스메디 OEM", "식품": "(주)들녘농산",
                   "리빙": "(주)리빙메이트 제조", "디지털": "(주)테크원 유통", "반려동물": "(주)펫푸드코리아"}
_COURIERS = ("CJ대한통운", "한진택배", "롯데글로벌로지스", "우체국택배")


def person_name(slot: int) -> str:
    """slot마다 다른 이름. 같은 성이면 이름 순번이 1씩 달라져 slot < 성 수 × 이름 수 범위에서 겹치지 않는다."""
    surname, k = slot % len(_SURNAMES), slot // len(_SURNAMES)
    return _SURNAMES[surname] + _GIVEN[(surname + k) % len(_GIVEN)]


def load_persona(industry: str, region: str, index: int) -> Persona:
    """부하 셀러 페르소나. index는 같은 업종 안의 순번(0~7)이라 상호·사람 이름이 겹치지 않는다."""
    slot = SELLER_INDUSTRIES.index(industry) * LOAD_NAMES_PER_INDUSTRY + index
    return Persona(
        business_name=LOAD_BUSINESS_NAMES[industry][index % LOAD_NAMES_PER_INDUSTRY],
        representative=person_name(3 * slot),
        employees=(person_name(3 * slot + 1), person_name(3 * slot + 2)),
        suppliers=(_LOAD_SUPPLIERS[industry], "(주)박스왕포장"),
        courier=_COURIERS[slot % len(_COURIERS)],
        industry=industry,
        region=region,
        channels=_LOAD_CHANNELS[industry],
        monthly_sales_krw=20_000_000 + (slot * 1_370_000) % 12_000_000,
    )
