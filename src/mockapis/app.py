"""Mock MyData·국세청·PG API. 데이터 접근마다 동의 토큰을 검증한다."""

from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from typing import Annotated, Literal

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from mockapis.consent import AuthorityUnavailable, ConsentError, ConsentRegistry, GrantAuthority, issue_token, verify_token
from mockapis.demo import FlowStore
from mockapis.postloan import LoanTerms, PostLoanFlow
from mockapis.synth import SyntheticCase, UsdCase
from shared.contracts import USD_V1, UsdConsentGrant, UsdProduct, UsdTransaction
from shared.usd_source import UsdMarketFlow, UsdMarketplace, UsdPgFlow, UsdProfile, UsdSettlement, UsdTaxFiling
from shared.contracts import (
    ApplicantProfile,
    ConsentScope,
    MarketplaceStore,
    PgFlow,
    PgSettlement,
    TaxFiling,
    Transaction,
)

AuthHeader = Annotated[str | None, Header()]


class ConsentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    applicant_id: str = Field(min_length=1)
    scopes: list[ConsentScope] = Field(min_length=1)
    purpose: str = Field(min_length=1)
    ttl_days: int = Field(default=90, ge=1, le=365)


class ConsentResponse(BaseModel):
    consent_id: str
    token: str
    expires_at: datetime


class UsdConsentRequest(ConsentRequest, UsdProduct):
    pass


class UsdConsentResponse(ConsentResponse, UsdProduct):
    pass


FAULT_MODES = ("none", "unavailable", "flaky")


class FaultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["none", "unavailable", "flaky"]


def _error(status: int, code: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code})


def create_app(
    cases: Mapping[str, SyntheticCase],
    secret: bytes,
    clock: Callable[[], datetime],
    flows: Mapping[str, PostLoanFlow] | FlowStore | None = None,
    fault: str = "none",
    *, registry: GrantAuthority | None = None,
) -> FastAPI:
    if fault not in FAULT_MODES:
        raise ValueError(f"FAULT_MODE: {fault}")
    app = FastAPI(title="Mock MyData/Tax/PG API")
    registry = registry if registry is not None else ConsentRegistry()

    @app.exception_handler(AuthorityUnavailable)
    async def authority_unavailable(request, exc):
        return JSONResponse({"detail": {"code": "unavailable"}}, status_code=503)
    state = {"fault": fault}
    # flaky: (동의, 경로)마다 첫 요청만 503
    seen: set[tuple[str, str]] = set()

    def authorize(authorization: str | None, scope: ConsentScope, path: str) -> SyntheticCase | UsdCase:
        if state["fault"] == "unavailable":
            raise _error(503, "unavailable")
        if not authorization or not authorization.startswith("Bearer "):
            raise _error(401, "invalid")
        token = authorization.removeprefix("Bearer ")
        try:
            grant = verify_token(token, secret, scope, clock())
            if not registry.active(grant.consent_id):
                raise ConsentError("revoked")
        except ConsentError as exc:
            raise _error(403 if exc.code == "scope" else 401, exc.code) from None
        if state["fault"] == "flaky" and (grant.consent_id, path) not in seen:
            seen.add((grant.consent_id, path))
            raise _error(503, "flaky")
        if isinstance(grant, UsdConsentGrant):
            case = flows.usd_case(grant.applicant_id) if isinstance(flows, FlowStore) else None
            if case is None:
                raise _error(404, "unknown_applicant")
            return case
        return cases[grant.applicant_id]

    def loan_terms(amount_krw: int | None, installment_krw: int | None) -> LoanTerms | None:
        if (amount_krw is None) != (installment_krw is None):
            raise _error(422, "loan_terms")
        if amount_krw is None:
            return None
        if amount_krw <= 0 or installment_krw <= 0 or installment_krw > amount_krw:
            raise _error(422, "loan_terms")
        return LoanTerms(amount_krw, installment_krw)

    def flow_for(applicant_id: str, loan: LoanTerms | None) -> PostLoanFlow | None:
        # Mapping이면(기존 테스트 호환) loan 인자를 무시하고 그대로 쓴다. FlowStore만 loan으로 재생성한다.
        if isinstance(flows, FlowStore):
            return flows.flow(applicant_id, loan)
        return (flows or {}).get(applicant_id)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/consents", status_code=201)
    def create_consent(req: ConsentRequest | UsdConsentRequest) -> ConsentResponse | UsdConsentResponse:
        usd = isinstance(req, UsdConsentRequest)
        known = (isinstance(flows, FlowStore) and flows.usd_case(req.applicant_id) is not None if usd
                 else req.applicant_id in cases)
        if not known:
            raise _error(404, "unknown_applicant")
        grant = registry.grant(req.applicant_id, req.scopes, req.purpose, timedelta(days=req.ttl_days), clock(),
                               USD_V1 if usd else None)
        return (UsdConsentResponse if usd else ConsentResponse)(
            **(USD_V1.model_dump() if usd else {}),
            consent_id=grant.consent_id,
            token=issue_token(grant, secret),
            expires_at=grant.expires_at,
        )

    @app.post("/consents/{consent_id}/revoke", status_code=204)
    def revoke_consent(consent_id: str) -> Response:
        if not registry.revoke(consent_id):
            raise _error(404, "unknown_consent")
        return Response(status_code=204)

    @app.get("/mydata/profile")
    def get_profile(authorization: AuthHeader = None) -> UsdProfile | ApplicantProfile:
        return authorize(authorization, ConsentScope.ACCOUNT, "/mydata/profile").profile

    @app.get("/mydata/transactions")
    def get_transactions(authorization: AuthHeader = None) -> list[Transaction | UsdTransaction]:
        return list(authorize(authorization, ConsentScope.ACCOUNT, "/mydata/transactions").transactions)

    @app.get("/mydata/flow")
    def get_flow(
        authorization: AuthHeader = None,
        start: date | None = None,
        end: date | None = None,
        amount_krw: int | None = None,
        installment_krw: int | None = None,
        amount_cents: int | None = None, installment_cents: int | None = None, loan_id: str | None = None,
        tenor_months: int | None = None,
    ) -> list[Transaction | UsdTransaction]:
        """대출 실행 이후 거래(조기경보 재생용). start·end는 포함 구간이다."""
        case = authorize(authorization, ConsentScope.ACCOUNT, "/mydata/flow")
        flow = product_flow(case, amount_krw, installment_krw, amount_cents, installment_cents, loan_id, tenor_months)
        if flow is None:
            raise _error(404, "no_flow")
        return [
            t
            for t in flow.transactions
            if (start is None or t.booked_on >= start) and (end is None or t.booked_on <= end)
        ]

    @app.get("/pg/flow")
    def get_pg_flow(
        authorization: AuthHeader = None,
        start: date | None = None,
        end: date | None = None,
        amount_krw: int | None = None,
        installment_krw: int | None = None,
        amount_cents: int | None = None, installment_cents: int | None = None, loan_id: str | None = None,
        tenor_months: int | None = None,
    ) -> PgFlow | UsdPgFlow:
        """대출 실행 이후 PG 정산·주문 원장(조기경보 재생용). start·end는 settled_on/order_on 포함 구간이다."""
        case = authorize(authorization, ConsentScope.PG, "/pg/flow")
        flow = product_flow(case, amount_krw, installment_krw, amount_cents, installment_cents, loan_id, tenor_months)
        if flow is None:
            raise _error(404, "no_flow")
        if isinstance(case, UsdCase):
            return UsdPgFlow(**USD_V1.model_dump(),
                             settlements=tuple(s for s in flow.pg_settlements
                                               if (not start or start <= s.settled_on) and (not end or s.settled_on <= end)),
                             orders=tuple(o for o in flow.orders
                                          if (not start or start <= o.order_on) and (not end or o.order_on <= end)),
                             prior_year_orders=flow.prior_year_orders)
        pg = flow.pg_flow()
        return PgFlow(
            settlements=tuple(
                s for s in pg.settlements
                if (start is None or s.settled_on >= start) and (end is None or s.settled_on <= end)
            ),
            orders=tuple(
                o for o in pg.orders
                if (start is None or o.order_on >= start) and (end is None or o.order_on <= end)
            ),
        )

    @app.get("/marketplace/flow")
    def get_marketplace_flow(
        authorization: AuthHeader = None,
        start: date | None = None,
        end: date | None = None,
        amount_krw: int | None = None,
        installment_krw: int | None = None,
        amount_cents: int | None = None, installment_cents: int | None = None, loan_id: str | None = None,
        tenor_months: int | None = None,
    ) -> UsdMarketFlow:
        """대출 실행 이후 주간 마켓 스냅샷(USD 전용, 조기경보 재생용). start·end는 week_end 포함 구간이다."""
        case = authorize(authorization, ConsentScope.MARKETPLACE, "/marketplace/flow")
        if not isinstance(case, UsdCase):
            raise _error(404, "no_flow")
        flow = product_flow(case, amount_krw, installment_krw, amount_cents, installment_cents, loan_id, tenor_months)
        if flow is None:
            raise _error(404, "no_flow")
        return UsdMarketFlow(**USD_V1.model_dump(),
                             weeks=tuple(w for w in flow.market_weeks
                                         if (not start or start <= w.week_end) and (not end or w.week_end <= end)))

    @app.get("/tax/filings")
    def get_tax_filings(authorization: AuthHeader = None) -> list[TaxFiling | UsdTaxFiling]:
        return list(authorize(authorization, ConsentScope.TAX, "/tax/filings").tax_filings)

    @app.get("/pg/settlements")
    def get_pg_settlements(authorization: AuthHeader = None) -> list[PgSettlement | UsdSettlement]:
        return list(authorize(authorization, ConsentScope.PG, "/pg/settlements").pg_settlements)

    @app.get("/marketplace/store")
    def get_marketplace_store(authorization: AuthHeader = None) -> MarketplaceStore | UsdMarketplace:
        case = authorize(authorization, ConsentScope.MARKETPLACE, "/marketplace/store")
        return case.marketplace or MarketplaceStore(as_of=clock().date())

    def product_flow(case, amount_krw, installment_krw, amount_cents, installment_cents, loan_id, tenor_months):
        if isinstance(case, UsdCase):
            if amount_krw is not None or installment_krw is not None:
                raise _error(422, "loan_terms")
            try:
                return flows.usd_flow(case.profile.applicant_id, loan_id, amount_cents, installment_cents, tenor_months)
            except ValueError:
                raise _error(422, "loan_terms") from None
        if any(v is not None for v in (amount_cents, installment_cents, loan_id, tenor_months)):
            raise _error(422, "loan_terms")
        return flow_for(case.profile.applicant_id, loan_terms(amount_krw, installment_krw))

    @app.post("/admin/faults", status_code=204)
    def set_fault(req: FaultRequest) -> Response:
        state["fault"] = req.mode
        seen.clear()
        return Response(status_code=204)

    return app


def demo_app(*, registry: GrantAuthority | None = None):
    import os
    from mockapis.demo import KST, build_demo_cases, build_load_cases, load_secret, resolve_as_of

    as_of = resolve_as_of()
    cases = {**build_demo_cases(as_of), **build_load_cases(as_of)}
    return create_app(cases, load_secret(), lambda: datetime.now(KST), flows=FlowStore(cases, as_of),
                      fault=os.environ.get("FAULT_MODE", "none"), registry=registry)
