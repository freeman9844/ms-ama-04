"""데모 마켓플레이스 셀러 카탈로그. 콘솔 라벨과 mock 데이터 생성이 함께 쓴다."""

from dataclasses import dataclass


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
)
SELLER_BY_ID = {s.applicant_id: s for s in DEMO_SELLERS}


def seller_label(applicant_id: str) -> str:
    seller = SELLER_BY_ID.get(applicant_id)
    return seller.label if seller else applicant_id
