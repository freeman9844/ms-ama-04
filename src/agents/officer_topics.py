"""심사 도우미 제안 칩. 칩마다 서버가 가명 스냅샷에서 정해진 근거만 골라 바로 답한다(모델 호출 없음).

칩은 근거가 있을 때만 보인다. 문장은 스냅샷 값으로 서버가 만들며 금액 대안이나 결정을 제시하지 않는다.
"""

from collections.abc import Callable, Mapping
from typing import Any

from agents.officer_facts import evidence_menu
from shared.money import format_usd
from shared.product_context import product_of

TOPICS = ("policy", "limit", "capacity", "opinion", "checks", "market", "cash_only")

_DECISIONS = {"eligible": ("적격", "Eligible"), "refer": ("회부", "Refer"), "ineligible": ("부적격", "Ineligible")}
_MARKET = (
    ("mkt_tenure_months", "마켓 입점 기간(개월)", "Marketplace tenure (months)", "n"),
    ("mkt_rating", "마켓 평점", "Marketplace rating", "n"),
    ("mkt_review_count", "리뷰 수", "Review count", "n"),
    ("mkt_return_rate_90d", "최근 90일 반품률", "90-day return rate", "pct"),
    ("mkt_cancel_rate_90d", "최근 90일 취소율", "90-day cancellation rate", "pct"),
    ("mkt_days_since_suspension", "마지막 판매 정지 후 경과일", "Days since last suspension", "n"),
    ("mkt_pending_settlement", "정산 예정금", "Pending settlement", "money"),
)
_SETTLEMENT = (
    ("pg_reconciliation_ratio", "정산 대사율", "Settlement reconciliation rate", "pct"),
    ("pg_top_platform_share", "최대 플랫폼 비중", "Top platform share", "pct"),
    ("pg_take_rate_trend_pp", "수수료율 추세(%p)", "Take-rate trend (pp)", "n"),
    ("pg_cash_conversion_days", "정산 현금화 일수", "Cash conversion days", "n"),
    ("pg_offset_rate_trend_pp", "상계율 추세(%p)", "Offset-rate trend (pp)", "n"),
    ("pg_advance_share", "선정산 비중", "Advance-settlement share", "pct"),
    ("pg_advance_suspected_count", "선정산 의심 건수", "Suspected advance settlements", "n"),
    ("unmatched_count", "미대사 정산 건수", "Unmatched settlements", "n"),
)


def _num(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def scoped(snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
    """USD 건은 검토를 통과한 현행 의견만 근거로 쓴다(과거 의견 표시는 유지)."""
    if product_of(snapshot.get("application") or {}):
        opinion = snapshot.get("agent_opinion") or {}
        if opinion.get("review_passed") is not True and opinion.get("state") != "legacy":
            return {**snapshot, "agent_opinion": None}
    return snapshot


class _Fmt:
    def __init__(self, snapshot: Mapping[str, Any], language: str) -> None:
        self.usd = bool(product_of(snapshot.get("application") or {}))
        self.unit = "cents" if self.usd else "krw"
        self.en = self.usd or language == "en"

    def t(self, ko: str, en: str) -> str:
        return en if self.en else ko

    def amount(self, value: Any) -> str:
        if not _num(value) or (self.usd and type(value) is not int):
            return self.t("기록 없음", "not recorded")
        if self.usd:
            return format_usd(value)
        return f"KRW {value:,}" if self.en else f"{value:,}원"

    def value(self, kind: str, value: Any) -> str:
        if kind == "money":
            return self.amount(value)
        if kind == "pct":
            return f"{round(value * 100, 1):g}%"
        return f"{value:g}"

    def decision(self, value: Any) -> str:
        ko, en = _DECISIONS.get(value, (str(value), str(value)))
        return self.t(ko, en)

    def reason(self, row: Mapping[str, Any]) -> str:
        text = f"{row.get('code')} {row.get('message') or ''}".strip()
        observed, threshold = row.get("observed"), row.get("threshold")
        if _num(observed) and _num(threshold):
            text += self.t(f" — 실제값 {observed:g}, 기준값 {threshold:g}", f" — observed {observed:g}, threshold {threshold:g}")
        return text


def _policy(s: Mapping[str, Any], f: _Fmt) -> list[str]:
    policy = s.get("policy")
    if not policy:
        return []
    lines = [f"{f.t('정책 판정', 'Policy decision')}: {f.decision(policy.get('decision'))} "
             f"({f.t('규정', 'rules')} {policy.get('rules_version')})"]
    codes = [r for r in policy.get("reason_codes") or () if isinstance(r, Mapping)]
    lines += [f.reason(r) for r in codes] or [f.t("걸린 규칙이 없습니다.", "No policy rule was hit.")]
    return lines


def _limit(s: Mapping[str, Any], f: _Fmt) -> list[str]:
    policy = s.get("policy")
    if not policy:
        return []
    u = f.unit
    lines = [f"{f.t('정책 한도', 'Policy limit')}: {f.amount(policy.get(f'max_amount_{u}'))}",
             f"{f.t('금리 구간', 'Rate band')}: {policy.get('rate_band') or '-'}"]
    trace = policy.get("trace")
    if not isinstance(trace, Mapping):
        if policy.get("trace_note"):
            lines.append(str(policy["trace_note"]))
        return lines
    lim = trace.get("limit") or {}
    lines.append(f"{f.t('신청액', 'Requested amount')}: {f.amount(lim.get(f'requested_amount_{u}'))}")
    if _num(lim.get("inflow_multiplier")):
        lines.append(f"{f.t('입금 기준 상한', 'Inflow cap')}: {f.amount(lim.get(f'avg_monthly_inflow_{u}'))} × "
                     f"{lim['inflow_multiplier']:g} = {f.amount(lim.get(f'inflow_cap_{u}'))}")
    lines.append(f"{f.t('상품 범위', 'Product range')}: {f.amount(lim.get(f'product_min_{u}'))} – "
                 f"{f.amount(lim.get(f'product_max_{u}'))}")
    if _num(lim.get("pending_settlement_ratio")) and _num(lim.get(f"settlement_cap_{u}")) and lim[f"settlement_cap_{u}"] > 0:
        lines.append(f"{f.t('정산 예정금 기준 상한', 'Pending-settlement cap')}: {f.amount(lim[f'settlement_cap_{u}'])} "
                     f"({f.t('비율', 'ratio')} {lim['pending_settlement_ratio']:g})")
    if _num(lim.get(f"substituted_cap_{u}")):
        lines.append(f"{f.t('대체 규칙 적용 상한', 'Substituted cap')}: {f.amount(lim[f'substituted_cap_{u}'])}")
    lines.append(f"{f.t('단위 조정 전', 'Before rounding')}: {f.amount(lim.get(f'before_rounding_{u}'))} "
                 f"({f.t('단위', 'unit')} {f.amount(lim.get(f'unit_{u}'))}) → {f.amount(lim.get(f'limit_{u}'))}")
    band = trace.get("band") or {}
    bands = ", ".join(f"{b.get('band')} ≥ {b.get('min_dscr'):g}" for b in band.get("bands") or ()
                      if isinstance(b, Mapping) and _num(b.get("min_dscr")))
    if _num(band.get("dscr")):
        lines.append(f"DSCR {band['dscr']:g} → {f.t('금리 구간', 'rate band')} {band.get('band') or '-'}"
                     + (f" ({bands})" if bands else ""))
    return lines


def _capacity(s: Mapping[str, Any], f: _Fmt, menu: Mapping[str, str]) -> list[str]:
    cf = s.get("cashflow") or {}
    capacity = cf.get("capacity") or {}
    short = capacity.get("short_months") or []
    if not short:
        return []
    lines = [menu[k] for k in (f"cashflow.capacity.short_months.{i}" for i in range(len(short))) if k in menu]
    rate = capacity.get("assumed_annual_rate")
    lines.append(f"{f.t('신규 월 상환액', 'New monthly installment')}: {f.amount(capacity.get(f'new_installment_{f.unit}'))}"
                 + (f.t(f" (연 가정 금리 {rate * 100:g}%)", f" (assumed annual rate {rate * 100:g}%)") if _num(rate) else ""))
    if _num(capacity.get("months")):
        lines.append(f.t(f"부족한 달: {len(short)}/{capacity['months']}개월", f"Short months: {len(short)} of {capacity['months']}"))
    dscr = (cf.get("features") or {}).get("dscr")
    if _num(dscr):
        lines.append(f"DSCR: {dscr:g}")
    return lines


def _opinion_items(group: str) -> Callable[[Mapping[str, Any], _Fmt, Mapping[str, str]], list[str]]:
    def lines(_s: Mapping[str, Any], _f: _Fmt, menu: Mapping[str, str]) -> list[str]:
        prefix = f"agent_opinion.{group}."
        return [text for key, text in menu.items() if key.startswith(prefix)]
    return lines


def _market(s: Mapping[str, Any], f: _Fmt) -> list[str]:
    cf = s.get("cashflow") or {}
    features = cf.get("features") or {}
    settlement = cf.get("settlement") or {}
    source = settlement if settlement.get("available") is True else features
    lines = []
    for field, ko, en, kind in _MARKET:
        value = features.get(f"{field}_{f.unit}" if kind == "money" else field)
        if _num(value):
            lines.append(f"{f.t(ko, en)}: {f.value(kind, value)}")
    for field, ko, en, kind in _SETTLEMENT:
        if _num(source.get(field)):
            lines.append(f"{f.t(ko, en)}: {f.value(kind, source[field])}")
    if not lines:
        return []
    trace = (s.get("policy") or {}).get("trace") or {}
    for check in trace.get("checks") or ():
        if (isinstance(check, Mapping) and check.get("result") == "hit"
                and str(check.get("field") or "").startswith(("mkt_", "pg_"))):
            lines.append(f"{f.t('걸린 규칙', 'Rule hit')}: {f.reason(check)}")
    return lines


def _cash_only(s: Mapping[str, Any], f: _Fmt) -> list[str]:
    policy = s.get("policy") or {}
    cash = policy.get("cash_only")
    if not isinstance(cash, Mapping):
        return []
    u = f.unit
    without = (f.t("은행·세금 자료만 본 판정", "Bank and tax data only") if cash.get("excludes") == "alt_data"
               else f.t("마켓 자료 없이 본 판정", "Without marketplace data"))
    lines = [f"{f.t('현재 판정', 'Current decision')}: {f.decision(policy.get('decision'))}, "
             f"{f.t('한도', 'limit')} {f.amount(policy.get(f'max_amount_{u}'))}",
             f"{without}: {f.decision(cash.get('decision'))}, {f.t('한도', 'limit')} {f.amount(cash.get(f'max_amount_{u}'))}"]
    same = (cash.get("decision"), cash.get(f"max_amount_{u}")) == (policy.get("decision"), policy.get(f"max_amount_{u}"))
    lines.append(f.t("두 판정의 결과와 한도가 같습니다.", "Both decisions and limits are the same.") if same
                 else f.t("두 판정의 결과 또는 한도가 다릅니다.", "The decisions or limits differ."))
    lines += [f"{without} · {f.reason(r)}" for r in cash.get("reason_codes") or () if isinstance(r, Mapping)]
    return lines


_BUILDERS: dict[str, Callable[..., list[str]]] = {
    "policy": _policy, "limit": _limit, "market": _market, "cash_only": _cash_only,
}
_MENU_BUILDERS: dict[str, Callable[..., list[str]]] = {
    "capacity": _capacity, "opinion": _opinion_items("claims"), "checks": _opinion_items("officer_checks"),
}


def topic_lines(snapshot: Mapping[str, Any], topic: str, language: str) -> list[str]:
    """칩 하나의 근거 줄. 근거가 없으면 빈 목록이다."""
    snapshot = scoped(snapshot)
    f = _Fmt(snapshot, language)
    if topic in _BUILDERS:
        return _BUILDERS[topic](snapshot, f)
    if topic in _MENU_BUILDERS:
        return _MENU_BUILDERS[topic](snapshot, f, evidence_menu(snapshot, language))
    raise ValueError(f"unknown topic: {topic}")


def available_topics(snapshot: Mapping[str, Any]) -> list[str]:
    return [t for t in TOPICS if topic_lines(snapshot, t, "ko")]
