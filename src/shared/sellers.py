"""데모 마켓플레이스 셀러 카탈로그. 콘솔 라벨과 mock 데이터 생성이 함께 쓴다."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from shared.segments import LOAD_GRID


@dataclass(frozen=True)
class DemoSeller:
    applicant_id: str
    business_name: str
    channels: tuple[str, ...]
    industry: str

    @property
    def label(self) -> str:
        return f"{self.business_name} · {'·'.join(self.channels)} · {self.industry}"


DEMO_SELLERS: tuple[DemoSeller, ...] = (
    DemoSeller("seller-dailylook-01", "성수데일리룩", ("스마트스토어", "쿠팡", "자사몰"), "패션의류"),
    DemoSeller("seller-mangwon-02", "망원식탁마켓", ("쿠팡", "스마트스토어", "11번가"), "식품"),
    DemoSeller("seller-glowlab-03", "해운대뷰티랩", ("스마트스토어", "쿠팡", "자사몰"), "뷰티"),
    DemoSeller("seller-knitroom-04", "판교손뜨개공방", ("스마트스토어", "자사몰"), "핸드메이드"),
    DemoSeller("seller-candle-05", "연남캔들웍스", ("스마트스토어", "자사몰"), "리빙"),
    DemoSeller("seller-gearstore-06", "용산기어스토어", ("쿠팡", "스마트스토어", "11번가"), "디지털"),
    DemoSeller("seller-petfood-07", "광교펫푸드랩", ("쿠팡", "스마트스토어", "자사몰"), "반려동물"),
    DemoSeller("seller-ceramic-08", "이천도예공방", ("스마트스토어", "자사몰"), "핸드메이드"),
    DemoSeller("seller-kimchi-09", "전주한상김치", ("쿠팡", "스마트스토어", "자사몰"), "식품"),
    DemoSeller("seller-linen-10", "을지로린넨", ("스마트스토어", "쿠팡", "자사몰"), "패션의류"),
    DemoSeller("seller-skinlab-11", "대구스킨랩", ("스마트스토어", "쿠팡", "11번가"), "뷰티"),
    DemoSeller("seller-kitchen-12", "인천키친웨어", ("쿠팡", "스마트스토어", "11번가"), "리빙"),
    DemoSeller("seller-cable-13", "구로케이블몰", ("쿠팡", "11번가", "스마트스토어"), "디지털"),
    DemoSeller("seller-leather-14", "성수가죽공방", ("스마트스토어", "자사몰"), "핸드메이드"),
    DemoSeller("seller-tangerine-15", "제주귤하루", ("쿠팡", "스마트스토어", "자사몰"), "식품"),
    DemoSeller("seller-petcare-16", "분당펫케어샵", ("쿠팡", "스마트스토어"), "반려동물"),
    DemoSeller("seller-tint-17", "홍대틴트하우스", ("스마트스토어", "자사몰"), "뷰티"),
    DemoSeller("seller-sofa-18", "광주소파공장", ("쿠팡", "스마트스토어", "자사몰"), "리빙"),
    DemoSeller("seller-streetfit-19", "동대문스트릿핏", ("스마트스토어", "쿠팡"), "패션의류"),
    DemoSeller("seller-bakery-20", "대전밀빵공방", ("쿠팡", "스마트스토어", "자사몰"), "식품"),
    DemoSeller("seller-mugshop-21", "연남머그상점", ("자사몰", "쿠팡", "스마트스토어"), "리빙"),
)
SELLER_BY_ID = {s.applicant_id: s for s in DEMO_SELLERS}


@dataclass(frozen=True)
class UsdSeller:
    business_name: str
    representative: str
    region: str
    industry: str
    supplier: str
    weekly_sales_cents: int
    channel_percent: tuple[tuple[str, int], ...]
    history_months: int = 12
    suspension_days_ago: int | None = None
    # KRW 원형(mockapis.synth.Archetype)과 같은 정책 판정이 나도록 USD 장부에 얹는 위험 레버.
    business_age_months: int = 38
    rating: float = 4.8
    return_rate: Decimal | None = None  # None이면 기본 반품률(mockapis.sellerdata.usd.BASE_RETURN_RATE)
    inventory_percent: int = 50  # 영업일마다 주 매출의 이 비율 ÷ 5를 재고로 매입한다
    risk: Literal["stressed", "assigned"] | None = None


USD_CHANNEL_NAMES = {"coupang": "Coupang", "naver_smart_store": "Naver Smart Store",
                     "own_store": "Own Store", "11st": "11st"}
USD_PAYOUT_BUSINESS_DAYS = {"naver_smart_store": 5, "coupang": 20, "own_store": 2, "11st": 12}
_GENERAL = (("coupang", 45), ("naver_smart_store", 40), ("own_store", 15))
_RETAIL = (("coupang", 50), ("naver_smart_store", 35), ("11st", 15))
_STUDIO = (("naver_smart_store", 85), ("own_store", 15))
_TWO_MARKETS = (("coupang", 55), ("naver_smart_store", 45))
USD_SELLERS = {
    "seller-bakery-20": UsdSeller("Daejeon Bakery", "Yun Boksun", "Daejeon", "Food", "Geumgang Flour",
                                  420_000, _GENERAL),
    "seller-petfood-07": UsdSeller("Gwanggyo Pet Food Lab", "Shin Hyewon", "Suwon", "Pet supplies",
                                   "Natural Pet Foods", 510_000, _GENERAL,
                                   suspension_days_ago=40),
    "seller-knitroom-04": UsdSeller("Pangyo Hand-Knit Studio", "Yun Jia", "Seongnam", "Handmade",
                                    "Thread and Needle", 315_000, (("naver_smart_store", 87), ("own_store", 13)), 4,
                                    business_age_months=8),
    "seller-dailylook-01": UsdSeller("Seongsu Daily Look", "Kim Minsu", "Seoul", "Apparel",
                                     "Dongdaemun Fabric", 620_000,
                                     (("naver_smart_store", 45), ("coupang", 35), ("own_store", 20))),
    "seller-mangwon-02": UsdSeller("Mangwon Table Market", "Choi Yeonghui", "Seoul", "Food",
                                   "Hankki Foods", 390_000, _RETAIL),
    "seller-glowlab-03": UsdSeller("Haeundae Beauty Lab", "Jeong Daeho", "Busan", "Beauty",
                                   "Cos Lab", 480_000, _GENERAL, risk="stressed"),
    "seller-candle-05": UsdSeller("Yeonnam Candle Works", "Seo Jiu", "Seoul", "Home and living",
                                  "Soy Wax Korea", 225_000, _STUDIO),
    "seller-gearstore-06": UsdSeller("Yongsan Gear Store", "Hwang Jaeseok", "Seoul", "Electronics",
                                     "Hanbit Electronics", 695_000, _RETAIL),
    "seller-ceramic-08": UsdSeller("Icheon Pottery Studio", "Jang Inho", "Icheon", "Handmade",
                                   "Yeoju Clay", 195_000, _STUDIO, return_rate=Decimal(".18")),
    "seller-kimchi-09": UsdSeller("Jeonju Hansang Kimchi", "Jeon Sunja", "Jeonju", "Food",
                                  "Honam Produce", 385_000, _GENERAL),
    "seller-linen-10": UsdSeller("Euljiro Linen", "Ryu Sangcheol", "Seoul", "Apparel",
                                 "Daegu Textiles", 410_000, _GENERAL),
    "seller-skinlab-11": UsdSeller("Daegu Skin Lab", "Son Yeji", "Daegu", "Beauty",
                                   "Dalbit Cosmetics", 375_000, _RETAIL, risk="stressed"),
    "seller-kitchen-12": UsdSeller("Incheon Kitchenware", "O Changsu", "Incheon", "Home and living",
                                   "Steel Living", 340_000, _RETAIL),
    "seller-cable-13": UsdSeller("Guro Cable Mall", "Lee Gangsan", "Seoul", "Electronics",
                                 "Tech Line", 455_000, _RETAIL, risk="stressed"),
    "seller-leather-14": UsdSeller("Seongsu Leather Studio", "Mun Taesik", "Seoul", "Handmade",
                                   "Seongsu Leather Supply", 170_000, _STUDIO, 4, business_age_months=8),
    "seller-tangerine-15": UsdSeller("Jeju Tangerine Day", "Gang Misuk", "Jeju", "Food",
                                     "Seogwipo Citrus", 270_000, _GENERAL),
    "seller-petcare-16": UsdSeller("Bundang Pet Care Shop", "Kim Sohui", "Seongnam", "Pet supplies",
                                   "Pet and Life", 295_000, _TWO_MARKETS),
    "seller-tint-17": UsdSeller("Hongdae Tint House", "Bak Hana", "Seoul", "Beauty",
                                "Cos Lab", 165_000, _STUDIO, 4, business_age_months=8),
    "seller-sofa-18": UsdSeller("Gwangju Sofa Factory", "Na Jeonghun", "Gwangju", "Home and living",
                                "Honam Timber", 570_000, _GENERAL, risk="stressed"),
    "seller-streetfit-19": UsdSeller("Dongdaemun Street Fit", "Choi Wonbin", "Seoul", "Apparel",
                                     "Dongdaemun Fabric", 275_000, _TWO_MARKETS, 4,
                                     business_age_months=8, rating=3.8, inventory_percent=60),
    "seller-mugshop-21": UsdSeller("Yeonnam Mug Shop", "Byeon Jaemin", "Seoul", "Home and living",
                                   "Hanyang Ceramics", 325_000, _GENERAL, risk="assigned",
                                   inventory_percent=65),
}


def seller_label(applicant_id: str) -> str:
    seller = SELLER_BY_ID.get(applicant_id)
    return seller.label if seller else applicant_id


# 업종별 부하 셀러 상호(업종당 8개: 부하 격자 48건 / 업종 6개).
LOAD_BUSINESS_NAMES: dict[str, tuple[str, ...]] = {
    "패션의류": ("오늘의핏", "모노데일리", "린넨앤코", "스트릿무드", "어반클로젯", "데님하우스", "소프트니트", "위켄드룩"),
    "뷰티": ("글로우랩", "수분공방", "퓨어스킨샵", "데일리코스", "블룸뷰티", "클린앤글로우", "로즈마리랩", "에센셜바이미"),
    "식품": ("산지곳간", "제주감귤농장", "할매반찬", "바다식탁", "곡물상회", "따뜻한도시락", "청정한우몰", "햇살과일가게"),
    "리빙": ("살림공간", "우드앤홈", "모던키친", "포근침구", "정리의기술", "그린플랜트", "라탄하우스", "세라믹데이"),
    "디지털": ("픽셀기어", "테크박스", "모바일플러스", "케이블마켓", "스마트액세서리", "게이밍존", "오디오랩", "충전공방"),
    "반려동물": ("멍냥상점", "펫밀키친", "꼬리살랑", "냥이식탁", "산책하개", "펫케어랩", "도그앤캣", "털복숭이마켓"),
}
LOAD_NAMES_PER_INDUSTRY = 8


def _load_names() -> dict[str, str]:
    """부하 셀러 상호: 격자 순서대로 같은 업종 안 순번(mockapis.demo.build_load_cases와 같은 규칙)."""
    seen: dict[str, int] = {}
    names = {}
    for a in LOAD_GRID:
        index = seen[a.industry] = seen.get(a.industry, -1) + 1
        names[a.applicant_id] = LOAD_BUSINESS_NAMES[a.industry][index % LOAD_NAMES_PER_INDUSTRY]
    return names


_NAME_BY_ID = {s.applicant_id: s.business_name for s in DEMO_SELLERS} | _load_names()


def seller_name(applicant_id: str) -> str | None:
    """데모·부하 셀러 상호. 모르는 신청인이면 None."""
    return _NAME_BY_ID.get(applicant_id)
