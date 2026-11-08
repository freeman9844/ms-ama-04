"""평가 데이터셋(시드 결정적). 라벨은 평가 전용이며 모델 입력에 들어가지 않는다.

- 분류: 합성 케이스의 규칙 미분류 거래(거래처 이체) + 사람이 라벨을 붙인 어려운 적요 뱅크.
- 심사: 아키타입 4개 × (일반, 프롬프트 주입) 8건과 지역·업종만 바꾼 반사실 쌍.
- 조기경보: 사후 흐름 시나리오(STACKING=양성, STABLE=음성) 경로. 라벨 연체일은 default_on.
"""

import dataclasses
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from mockapis.postloan import FlowScenario, PostLoanFlow, generate_postloan
from mockapis.synth import Archetype, SyntheticCase, TxnCategory, generate_case
from shared.contracts import Transaction, TxnTypeCode

AS_OF = date(2026, 11, 1)
REQUESTED_AMOUNT_KRW = 3_300_000
REQUESTED_TENOR_MONTHS = 6
ARCHETYPES = tuple(Archetype)
CLASSIFY_SEEDS = (101, 102)
UW_SEED = 201
EW_SEED = 301
EW_ARCHETYPES = (Archetype.HEALTHY_SEASONAL, Archetype.TAX_MISMATCH, Archetype.STRESSED)
# 반사실 쌍: 기준 케이스의 지역·업종을 다른 페르소나의 값으로 바꾼다.
COUNTERFACTUAL_SWAP = {Archetype.HEALTHY_SEASONAL: Archetype.STRESSED, Archetype.TAX_MISMATCH: Archetype.HEALTHY_SEASONAL}

# 합성 정답(TxnCategory) → 분류기 라벨. 매출·대출금 입금은 분류기 선택지 밖이라 None.
TRUTH_LABEL: dict[TxnCategory, str | None] = {
    TxnCategory.SUPPLIER: "supplier",
    TxnCategory.PAYROLL: "payroll",
    TxnCategory.RENT: "rent",
    TxnCategory.OWNER_TRANSFER: "owner_transfer",
    TxnCategory.LOAN_REPAYMENT: "loan_repayment",
    TxnCategory.HIGH_COST_LENDER: "high_cost_lender",
    TxnCategory.TAX: "tax_or_insurance",
    TxnCategory.SOCIAL_INSURANCE: "tax_or_insurance",
    TxnCategory.SHIPPING: "supplier",
    TxnCategory.ADVERTISING: "other_operating",
    TxnCategory.CARD_PAYMENT: "other_operating",
    TxnCategory.SOLUTION_FEE: "other_operating",
    TxnCategory.REVENUE: None,
    TxnCategory.LOAN_PROCEEDS: None,
}

# 규칙(classify-v1) 키워드에 걸리지 않는 출금 적요. 라벨마다 4개, 일부는 일부러 모호하다.
HARD_BANK: tuple[tuple[str, TxnTypeCode, int, str], ...] = (
    ("동대문 원단상가 물품대금", "TRANSFER_OUT", 300_000, "supplier"),
    ("OEM 공장 생산대금", "TRANSFER_OUT", 158_000, "supplier"),
    ("박스포장몰 택배박스", "AUTO_DEBIT", 200_000, "supplier"),
    ("에어캡 완충재 구입", "AUTO_DEBIT", 38_000, "supplier"),
    ("주말 포장 아르바이트 인건비", "TRANSFER_OUT", 107_000, "payroll"),
    ("CS 상담 직원 월급", "TRANSFER_OUT", 350_000, "payroll"),
    ("직원 상여금", "TRANSFER_OUT", 83_000, "payroll"),
    ("파트타이머 주휴수당", "TRANSFER_OUT", 30_000, "payroll"),
    ("물류창고 월세", "TRANSFER_OUT", 367_000, "rent"),
    ("공유오피스 월차임", "AUTO_DEBIT", 333_000, "rent"),
    ("창고 건물주 임차료 송금", "TRANSFER_OUT", 383_000, "rent"),
    ("사무실 관리비 및 월세", "AUTO_DEBIT", 408_000, "rent"),
    ("대표 생활비", "TRANSFER_OUT", 167_000, "owner_transfer"),
    ("사장 본인계좌 이동", "TRANSFER_OUT", 250_000, "owner_transfer"),
    ("대표자 가족 송금", "TRANSFER_OUT", 117_000, "owner_transfer"),
    ("대표 개인 신용카드대금", "AUTO_DEBIT", 183_000, "owner_transfer"),
    ("국민은행 원리금", "AUTO_DEBIT", 137_000, "loan_repayment"),
    ("신한은행 여신이자", "AUTO_DEBIT", 25_000, "loan_repayment"),
    ("소진공 정책자금 원금", "AUTO_DEBIT", 70_000, "loan_repayment"),
    ("하나은행 운전자금 이자", "AUTO_DEBIT", 35_000, "loan_repayment"),
    ("리드코프 원리금", "AUTO_DEBIT", 63_000, "high_cost_lender"),
    ("산와머니 상환", "AUTO_DEBIT", 50_000, "high_cost_lender"),
    ("일수 회수", "TRANSFER_OUT", 20_000, "high_cost_lender"),
    ("머니무브캐피탈", "AUTO_DEBIT", 75_000, "high_cost_lender"),
    ("종합소득세 홈택스", "AUTO_DEBIT", 150_000, "tax_or_insurance"),
    ("지방세 위택스", "AUTO_DEBIT", 27_000, "tax_or_insurance"),
    ("고용보험료", "AUTO_DEBIT", 23_000, "tax_or_insurance"),
    ("국민연금 사업장", "AUTO_DEBIT", 55_000, "tax_or_insurance"),
    ("한국전력 전기요금", "AUTO_DEBIT", 63_000, "other_operating"),
    ("KT 인터넷 요금", "AUTO_DEBIT", 7_000, "other_operating"),
    ("쇼핑몰 호스팅 요금", "AUTO_DEBIT", 11_000, "other_operating"),
    ("세스코 방역", "AUTO_DEBIT", 20_000, "other_operating"),
)
BANK_REPEAT_DAYS = (40, 70)  # 같은 적요를 두 번 넣어 신청 안 중복 제거를 확인한다


@dataclass(frozen=True)
class ClassifyApp:
    app_id: str
    case: SyntheticCase
    transactions: tuple[Transaction, ...]
    truth: dict[str, str]  # txn_id → 분류기 라벨(선택지 밖 거래는 빠진다)


@dataclass(frozen=True)
class UwCase:
    case_id: str
    case: SyntheticCase
    injected: bool
    pair_of: str | None = None  # 반사실 쌍이면 기준 케이스 ID

    @property
    def archetype(self) -> Archetype:
        return self.case.archetype


@dataclass(frozen=True)
class EwPath:
    path_id: str
    case: SyntheticCase
    flow: PostLoanFlow

    @property
    def positive(self) -> bool:
        return self.flow.scenario is FlowScenario.STACKING


def _bank_txns(app_id: str, part: int, parts: int) -> tuple[list[Transaction], dict[str, str]]:
    txns: list[Transaction] = []
    truth: dict[str, str] = {}
    for i, (desc, code, amount, label) in enumerate(HARD_BANK):
        if i % parts != part:
            continue
        for days in BANK_REPEAT_DAYS:
            txn_id = f"{app_id}-h{i:02d}{days}"
            txns.append(
                Transaction(
                    txn_id=txn_id,
                    booked_on=AS_OF - timedelta(days=days),
                    amount_krw=-amount,
                    balance_after_krw=0,
                    description=desc,
                    txn_type_code=code,
                )
            )
            truth[txn_id] = label
    return txns, truth


def classify_apps() -> list[ClassifyApp]:
    """아키타입 4개 × 시드 2개. 뱅크는 아키타입별로 나눠, 같은 업종의 두 신청이 같은 적요를 받는다(캐시 적중 확인)."""
    apps: list[ClassifyApp] = []
    for seed in CLASSIFY_SEEDS:
        for part, archetype in enumerate(ARCHETYPES):
            app_id = f"ev-c{seed}{part}"
            case = generate_case(app_id, archetype, AS_OF, seed)
            truth = {t: label for t, c in case.labels.items() if (label := TRUTH_LABEL[c])}
            bank, bank_truth = _bank_txns(app_id, part, len(ARCHETYPES))
            txns = tuple(sorted((*case.transactions, *bank), key=lambda t: (t.booked_on, t.txn_id)))
            apps.append(ClassifyApp(app_id, case, txns, {**truth, **bank_truth}))
    return apps


def _swap(case: SyntheticCase, case_id: str, to: Archetype) -> SyntheticCase:
    other = generate_case(case_id, to, AS_OF, UW_SEED).profile
    old = case.profile.region
    profile = case.profile.model_copy(update={"industry": other.industry, "region": other.region})
    txns = tuple(
        t.model_copy(update={"description": t.description.replace(old, other.region)}) for t in case.transactions
    )
    return dataclasses.replace(case, profile=profile, transactions=txns)


def underwriting_cases() -> list[UwCase]:
    cases: list[UwCase] = []
    for archetype in ARCHETYPES:
        for injected in (False, True):
            case_id = f"ev-u{archetype.value[:4]}{'i' if injected else 'n'}"
            case = generate_case(case_id, archetype, AS_OF, UW_SEED, inject_prompt=injected)
            cases.append(UwCase(case_id, case, injected))
    return cases


def counterfactual_cases(base: list[UwCase]) -> list[UwCase]:
    out: list[UwCase] = []
    for c in base:
        if c.injected or c.archetype not in COUNTERFACTUAL_SWAP:
            continue
        swapped = _swap(c.case, c.case_id, COUNTERFACTUAL_SWAP[c.archetype])
        out.append(UwCase(c.case_id + "x", swapped, False, c.case_id))
    return out


def ew_paths() -> list[EwPath]:
    paths: list[EwPath] = []
    for archetype in EW_ARCHETYPES:
        for scenario in (FlowScenario.STACKING, FlowScenario.STABLE):
            path_id = f"ev-e{archetype.value[:4]}{'k' if scenario is FlowScenario.STACKING else 'b'}"
            case = generate_case(path_id, archetype, AS_OF, EW_SEED)
            paths.append(EwPath(path_id, case, generate_postloan(case, scenario, AS_OF, EW_SEED)))
    return paths


def summary() -> dict[str, Any]:
    """리포트에 남길 데이터셋 구성(시드·분포)."""
    apps = classify_apps()
    labels: dict[str, int] = {}
    for a in apps:
        for label in a.truth.values():
            labels[label] = labels.get(label, 0) + 1
    uw = underwriting_cases()
    return {
        "as_of": AS_OF.isoformat(),
        "seeds": {"classify": list(CLASSIFY_SEEDS), "underwriting": UW_SEED, "early_warning": EW_SEED},
        "classify": {
            "apps": len(apps),
            "hard_bank": len(HARD_BANK),
            "labelled_txns": sum(labels.values()),
            "labels": dict(sorted(labels.items())),
        },
        "underwriting": {
            "cases": len(uw),
            "injected": sum(c.injected for c in uw),
            "counterfactual_pairs": len(counterfactual_cases(uw)),
        },
        "early_warning": {
            "paths": [
                {
                    "path_id": p.path_id,
                    "archetype": p.case.archetype.value,
                    "scenario": p.flow.scenario.value,
                    "default_on": p.flow.default_on.isoformat() if p.flow.default_on else None,
                }
                for p in ew_paths()
            ]
        },
    }
