"""데모 마켓플레이스 셀러 카탈로그. 콘솔 라벨과 mock 데이터 생성이 함께 쓴다."""

from dataclasses import dataclass

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
