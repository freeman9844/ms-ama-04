"""USD 사후 감시 장면을 오케스트레이터와 LLM 없이 재생한다(스펙 §7).

장면 테스트와 평가 묶음이 쓴다. 대출은 usd-v2 승인으로 잡고(seed 1001, 500,000센트·6개월), 13주 동안 매주
`observe_usd` → `watch_week`를 돈다. 조사 문맥(`EwContext`)은 오케스트레이터 USD 주간 처리와 같은 필드로 만든다.
"""

from collections.abc import Callable
from datetime import date, timedelta
from functools import lru_cache
from typing import Any, NamedTuple

from agents import features
from agents.early_warning import EwContext, EwResult
from agents.escalation import next_stage
from agents.incident import new_watch, watch_week
from agents.usd_early_warning import fallback_stage
from agents.usd_monitoring import approval_basis, observe_usd, usd_baseline
from mockapis import demo, postloan
from policy.engine import explain
from policy.rules import rules_for_version
from shared.contracts import USD_V1
from shared.usd_source import UsdMarketFlow, UsdPgFlow

AS_OF = date(2026, 11, 24)
WEEKS = 13
SEED = 1001
LOAN_ID = "loan-usd"
AMOUNT_CENTS = 500_000
INSTALLMENT_CENTS = 84_000
TENOR_MONTHS = 6
JOURNAL_LINES = 6
_PG_WEEK_FIELDS = ("week", "order_gross_cents", "returns_cents", "held_cents", "deductions_cents", "settle_net_cents")

Decide = Callable[[EwContext], EwResult]


@lru_cache(maxsize=None)
def _cases(as_of: date):
    return demo.build_usd_cases(as_of)


def case_at(seller: str, as_of: date = AS_OF):
    return _cases(as_of)[seller]


def underwrite_at(seller: str, as_of: date = AS_OF, principal: int = AMOUNT_CENTS):
    case = case_at(seller, as_of)
    return features.compute_usd_underwriting(
        "app-demo", case.profile, case.transactions, case.tax_filings, as_of=as_of,
        requested_amount_cents=principal, requested_tenor_months=TENOR_MONTHS,
        pg_settlements=case.pg_settlements, marketplace=case.marketplace,
    )


class Scene(NamedTuple):
    loan: dict[str, Any]
    flow: postloan.UsdPostLoanFlow
    pg: UsdPgFlow
    market: UsdMarketFlow | None
    industry: str

    def visible(self, week: int) -> list:
        hi = self.flow.start + timedelta(days=7 * week - 1)
        return [t for t in self.flow.transactions if t.booked_on <= hi]


def prepare(seller: str, *, as_of: date = AS_OF, basis: list[dict] | None = None, prior_year: bool = True,
            market: bool = True) -> Scene:
    """usd-v2 승인으로 대출을 잡고 사후 흐름을 만든다. `loan["monitoring"]["weeks"]`는 비어 있다."""
    uw = underwrite_at(seller, as_of)
    stored = {"features": uw.features.model_dump(mode="json"), "monthly": uw.monthly}
    decision = explain(uw.features, rules_for_version("usd-v2")).decision
    case = case_at(seller, as_of)
    flow = postloan.generate_usd_postloan(case, as_of, SEED, loan_id=LOAN_ID, amount_cents=AMOUNT_CENTS,
                                          installment_cents=INSTALLMENT_CENTS)
    pg = UsdPgFlow(**USD_V1.model_dump(), settlements=flow.pg_settlements, orders=flow.orders,
                   prior_year_orders=flow.prior_year_orders if prior_year else ())
    mkt = UsdMarketFlow(**USD_V1.model_dump(), weeks=flow.market_weeks) if market else None
    loan = {"monitoring": {"flow_start": flow.start.isoformat(), "weeks": []}, "baseline": usd_baseline(stored),
            "approval_basis": approval_basis(decision.applied_substitutions, stored["features"])
            if basis is None else basis}
    return Scene(loan, flow, pg, mkt, case.profile.industry)


def _fallback(ctx: EwContext) -> EwResult:
    return fallback_stage(ctx, "agents_disabled")


def _replay(seller: str, decide: Decide, market: bool, as_of: date, last_week: int = WEEKS) -> list[dict]:
    scene = prepare(seller, as_of=as_of, market=market)
    loan, start = scene.loan, scene.flow.start
    mon = loan["monitoring"]
    replay_id = f"scene-{seller}"
    terms = {**USD_V1.model_dump(), "amount_cents": AMOUNT_CENTS, "tenor_months": TENOR_MONTHS}
    watch: dict[str, Any] = new_watch()
    escalation: dict[str, Any] = {}
    lines: list[dict] = []
    for week in range(1, last_week + 1):
        visible = scene.visible(week)
        obs = observe_usd(loan, visible, scene.pg, week, {}, scene.market)
        mon["weeks"].append({"week": week, "row": obs.rows[-1], "triggers": obs.triggers,
                             "bank_only_triggers": obs.bank_only, "explained": obs.explained})

        def investigate(base: str | None, week=week, visible=visible, obs=obs) -> EwResult:
            incident = watch["open"]
            return decide(EwContext(
                alert_id=f"ew-{replay_id}-w{week:02d}", loan_id=LOAN_ID, week=week, start=start,
                industry=scene.industry, triggers=obs.triggers, rows=obs.rows, baseline=loan["baseline"],
                transactions=visible, categories=obs.cats, mask=lambda text: text, terms=terms,
                candidates=obs.candidates,
                pg_weeks=[{k: r[k] for k in _PG_WEEK_FIELDS if k in r} for r in obs.rows],
                signals=obs.signals, base_stage=base, journal=lines[-JOURNAL_LINES:],
                # 새 사건이면 incident.py가 이 주에 여는 사건의 ID
                incident_id=incident["id"] if incident else f"in-{replay_id}-w{week:02d}",
            ))

        line, result = watch_week(
            watch, list(obs.signals), week=week, replay_id=replay_id,
            high_recheck=not isinstance(escalation.get("stage2"), dict), explained=bool(obs.explained),
            market=scene.market is not None, investigate=investigate,
        )
        if line["stage"] == "high" and (stage := next_stage(watch["stages"], escalation)):
            escalation[f"stage{stage}"] = {"week": week}
        lines.append({**line, "week": week, "called": result is not None})
    return lines


def scene_replay(seller: str, *, decide: Decide | None = None, market: bool = True,
                 as_of: date = AS_OF) -> list[dict]:
    """13주 일지 줄. 줄마다 `week`와 그 주 조사 호출 여부 `called`를 더한다. 기본 판단은 에이전트 없는 폴백이다."""
    return _replay(seller, decide or _fallback, market, as_of)


def scene_context(seller: str, week: int, *, as_of: date = AS_OF) -> EwContext:
    """그 주 조사 문맥. 앞 주들은 폴백으로 재생한다. 그 주에 조사를 부르지 않으면 ValueError."""
    seen: list[EwContext] = []

    def decide(ctx: EwContext) -> EwResult:
        if ctx.week == week:
            seen.append(ctx)
        return _fallback(ctx)

    _replay(seller, decide, True, as_of, last_week=week)
    if not seen:
        raise ValueError(f"{seller} week {week} does not call the investigator")
    return seen[0]
