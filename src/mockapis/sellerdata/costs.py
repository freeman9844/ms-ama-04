"""셀러 비용 일정. 적요는 classify-v1 키워드를 지킨다.

규칙이 잡는 적요: "임대료 {지역}", "급여 {이름}", "국민건강보험공단", "기업은행 대출상환", "{대표자} 개인이체", TAX 코드.
규칙 미분류로 남아 의미 분류기로 가는 적요: 매입처·포장재, 택배비, 광고 충전, 카드대금, 솔루션 이용료,
국민연금공단, 근로복지공단.
"""

import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date

from shared.krcalendar import month_add, next_business_day, previous_business_day
from mockapis.sellerdata.categories import TxnCategory
from mockapis.sellerdata.personas import Persona
from mockapis.sellerdata.seasonality import month_index
from shared.contracts import TxnTypeCode


@dataclass(frozen=True)
class Event:
    booked_on: date
    amount_krw: int
    description: str
    code: TxnTypeCode
    category: TxnCategory


@dataclass(frozen=True)
class CostProfile:
    cogs_ratio: float  # 주문액 대비 상품 매입. 채널 수수료·택배·광고·고정비를 빼고 영업이익률이 약 8%가 되게 맞췄다
    order_value_krw: int  # 객단가(택배·포장 건수 계산)
    ad_ratio: float  # 주문액 대비 광고비


INDUSTRY_COSTS: dict[str, CostProfile] = {
    "패션의류": CostProfile(0.42, 38_000, 0.07),
    "뷰티": CostProfile(0.36, 29_000, 0.085),
    "식품": CostProfile(0.4, 32_000, 0.05),
    "리빙": CostProfile(0.41, 41_000, 0.06),
    "디지털": CostProfile(0.47, 64_000, 0.045),
    "반려동물": CostProfile(0.41, 36_000, 0.06),
    "핸드메이드": CostProfile(0.4, 45_000, 0.05),
}
SHIPPING_PER_ORDER_KRW = 3_100
PACKAGING_PER_ORDER_KRW = 420
AD_TRANSFER_SHARE = 0.6  # 나머지는 카드로 결제해 카드대금에 들어간다
AD_TOPUPS_PER_MONTH = 3
CARD_MISC_RATIO = 0.012
RENT_RATIO = 0.035
PAYROLL_NET_RATIOS = (0.07, 0.035)  # 정규직 1명, 단시간 1명
NET_TO_GROSS = 1 / 0.9
HEALTH_RATE, PENSION_RATE, EMPLOYMENT_RATE = 0.0799, 0.095, 0.025
WITHHOLDING_RATE = 0.025
VAT_EFFECTIVE_RATE = 0.028  # (매출세액 − 매입세액) / 주문액 근사
INCOME_TAX_RATE = 0.012
LOCAL_TAX_SHARE = 0.1
BANK_LOAN_RATIO = 0.012
BANK_LOAN_DECAY = 0.004
OWNER_DRAW_RATIO = 0.06
OWNER_DRAW_DAY = 28


def _ten(year: int, month: int) -> int:
    return 10


def _one(d: date) -> float:
    return 1.0


@dataclass
class CostSchedule:
    persona: Persona
    start: date
    end: date
    sales: Mapping[date, int]
    rng: random.Random
    payroll_day: Callable[[int, int], int] = _ten
    owner_draw_factor: Callable[[date], float] = _one

    def __post_init__(self) -> None:
        self.profile = INDUSTRY_COSTS.get(self.persona.industry, INDUSTRY_COSTS["리빙"])
        self.events: list[Event] = []

    def add(self, d: date, amount: float, desc: str, code: TxnTypeCode, cat: TxnCategory) -> None:
        if self.start <= d < self.end and int(amount):
            self.events.append(Event(d, -abs(int(amount)), desc, code, cat))

    def month_sales(self, year: int, month: int) -> float:
        first = date(year, month, 1)
        nxt = date(*month_add(year, month, 1), 1)
        days = [d for d in self.sales if first <= d < nxt]
        if len(days) == (nxt - first).days:
            return float(sum(self.sales[d] for d in days))
        return self.persona.monthly_sales_krw * month_index(self.persona.industry, year, month)

    def half_vat(self, year: int, half: int) -> float:
        months = range(1, 7) if half == 1 else range(7, 13)
        return VAT_EFFECTIVE_RATE * sum(self.month_sales(year, m) for m in months)

    def noise(self, spread: float = 0.03) -> float:
        return self.rng.uniform(1 - spread, 1 + spread)

    def build(self) -> list[Event]:
        y, m = self.start.year, self.start.month
        while date(y, m, 1) < self.end:
            self._monthly(y, m)
            y, m = month_add(y, m, 1)
        self._weekly_purchases()
        return self.events

    def _weekly_purchases(self) -> None:
        p = self.persona
        d = self.start
        while d < self.end:
            if d.weekday() == 2:
                week = sum(self.sales.get(date.fromordinal(d.toordinal() - k), 0) for k in range(1, 8))
                self.add(next_business_day(d), week * self.profile.cogs_ratio * self.noise(0.08), p.suppliers[0],
                         "TRANSFER_OUT", TxnCategory.SUPPLIER)
            d = date.fromordinal(d.toordinal() + 1)

    def _monthly(self, y: int, m: int) -> None:
        p, prof, s = self.persona, self.profile, self.persona.monthly_sales_krw
        py, pm = month_add(y, m, -1)
        prev_sales = self.month_sales(py, pm)
        orders = prev_sales / prof.order_value_krw

        self.add(next_business_day(date(y, m, 1)), 77_000, "사방넷 이용료", "AUTO_DEBIT", TxnCategory.SOLUTION_FEE)
        if any(c == "자사몰" for c, _ in p.channels):
            self.add(next_business_day(date(y, m, 2)), 55_000, "카페24 이용료", "AUTO_DEBIT", TxnCategory.SOLUTION_FEE)
        self.add(next_business_day(date(y, m, 3)), orders * PACKAGING_PER_ORDER_KRW * self.noise(0.06),
                 p.suppliers[1], "TRANSFER_OUT", TxnCategory.SUPPLIER)
        self.add(next_business_day(date(y, m, 5)), round(s * RENT_RATIO, -4), f"임대료 {p.region}", "AUTO_DEBIT",
                 TxnCategory.RENT)

        payday = previous_business_day(date(y, m, self.payroll_day(y, m)))
        gross_payroll = 0.0
        for name, ratio in zip(p.employees, PAYROLL_NET_RATIOS):
            net = s * ratio * self.noise(0.015)
            gross_payroll += net * NET_TO_GROSS
            self.add(payday, net, f"급여 {name}", "TRANSFER_OUT", TxnCategory.PAYROLL)
        tenth = next_business_day(date(y, m, 10))
        self.add(tenth, gross_payroll * WITHHOLDING_RATE, "국세 원천세", "TAX", TxnCategory.TAX)
        self.add(tenth, gross_payroll * HEALTH_RATE, "국민건강보험공단", "AUTO_DEBIT", TxnCategory.SOCIAL_INSURANCE)
        self.add(tenth, gross_payroll * PENSION_RATE, "국민연금공단", "AUTO_DEBIT", TxnCategory.SOCIAL_INSURANCE)
        self.add(tenth, gross_payroll * EMPLOYMENT_RATE, "근로복지공단 고용산재", "AUTO_DEBIT",
                 TxnCategory.SOCIAL_INSURANCE)
        self.add(tenth, orders * SHIPPING_PER_ORDER_KRW * self.noise(0.04), f"{p.courier} 택배비", "AUTO_DEBIT",
                 TxnCategory.SHIPPING)

        topup = round(prev_sales * prof.ad_ratio * AD_TRANSFER_SHARE / AD_TOPUPS_PER_MONTH / 1.1, -4) * 1.1
        for day in sorted(self.rng.sample(range(1, 28), AD_TOPUPS_PER_MONTH)):
            self.add(next_business_day(date(y, m, day)), topup, "네이버 검색광고 충전", "TRANSFER_OUT",
                     TxnCategory.ADVERTISING)
        card = prev_sales * (prof.ad_ratio * (1 - AD_TRANSFER_SHARE) + CARD_MISC_RATIO) * self.noise(0.1)
        self.add(next_business_day(date(y, m, 14)), card, "신한카드 결제대금", "AUTO_DEBIT", TxnCategory.CARD_PAYMENT)

        elapsed = (y - self.start.year) * 12 + m - self.start.month
        bank = s * BANK_LOAN_RATIO * (1 - BANK_LOAN_DECAY * elapsed) + self.rng.randint(0, 999)
        self.add(next_business_day(date(y, m, 25)), bank, "기업은행 대출상환", "AUTO_DEBIT", TxnCategory.LOAN_REPAYMENT)

        if m in (1, 4, 7, 10):
            self.add(next_business_day(date(y, m, 25)), self._vat(y, m), "부가가치세 국세청", "TAX", TxnCategory.TAX)
        if m == 5:
            due = next_business_day(date(y, 5, 31))
            prior = sum(self.month_sales(y - 1, k) for k in range(1, 13))
            self.add(due, prior * INCOME_TAX_RATE, "종합소득세 국세청", "TAX", TxnCategory.TAX)
            self.add(due, prior * INCOME_TAX_RATE * LOCAL_TAX_SHARE, "지방소득세 위택스", "TAX", TxnCategory.TAX)

        draw_day = date(y, m, OWNER_DRAW_DAY)
        draw = round(s * OWNER_DRAW_RATIO * self.owner_draw_factor(draw_day), -5)
        self.add(draw_day, draw, f"{p.representative} 개인이체", "TRANSFER_OUT", TxnCategory.OWNER_TRANSFER)

    def _vat(self, y: int, m: int) -> float:
        """1·7월 확정(반기 세액 − 예정고지), 4·10월 예정고지(직전 반기 세액의 1/2)."""
        if m == 4:
            return self.half_vat(y - 1, 2) / 2
        if m == 10:
            return self.half_vat(y, 1) / 2
        if m == 7:
            return self.half_vat(y, 1) - self.half_vat(y - 1, 2) / 2
        return self.half_vat(y - 1, 2) - self.half_vat(y - 1, 1) / 2
