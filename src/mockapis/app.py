"""Mock MyData·국세청·PG API. 데이터 접근마다 동의 토큰을 검증한다."""

from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from typing import Annotated, Literal

from fastapi import FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from mockapis.consent import ConsentError, ConsentRegistry, issue_token, verify_token
from mockapis.postloan import PostLoanFlow
from mockapis.synth import SyntheticCase
from shared.contracts import ApplicantProfile, ConsentScope, PgSettlement, TaxFiling, Transaction

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
    flows: Mapping[str, PostLoanFlow] | None = None,
    fault: str = "none",
) -> FastAPI:
    if fault not in FAULT_MODES:
        raise ValueError(f"FAULT_MODE: {fault}")
    app = FastAPI(title="Mock MyData/Tax/PG API")
    registry = ConsentRegistry()
    state = {"fault": fault}
    # flaky: (동의, 경로)마다 첫 요청만 503
    seen: set[tuple[str, str]] = set()

    def authorize(authorization: str | None, scope: ConsentScope, path: str) -> SyntheticCase:
        if state["fault"] == "unavailable":
            raise _error(503, "unavailable")
        if not authorization or not authorization.startswith("Bearer "):
            raise _error(401, "invalid")
        token = authorization.removeprefix("Bearer ")
        try:
            grant = verify_token(token, secret, scope, clock(), registry.revoked_ids)
        except ConsentError as exc:
            raise _error(403 if exc.code == "scope" else 401, exc.code) from None
        if state["fault"] == "flaky" and (grant.consent_id, path) not in seen:
            seen.add((grant.consent_id, path))
            raise _error(503, "flaky")
        return cases[grant.applicant_id]

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/consents", status_code=201)
    def create_consent(req: ConsentRequest) -> ConsentResponse:
        if req.applicant_id not in cases:
            raise _error(404, "unknown_applicant")
        grant = registry.grant(req.applicant_id, req.scopes, req.purpose, timedelta(days=req.ttl_days), clock())
        return ConsentResponse(
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
    def get_profile(authorization: AuthHeader = None) -> ApplicantProfile:
        return authorize(authorization, ConsentScope.ACCOUNT, "/mydata/profile").profile

    @app.get("/mydata/transactions")
    def get_transactions(authorization: AuthHeader = None) -> list[Transaction]:
        return list(authorize(authorization, ConsentScope.ACCOUNT, "/mydata/transactions").transactions)

    @app.get("/mydata/flow")
    def get_flow(
        authorization: AuthHeader = None, start: date | None = None, end: date | None = None
    ) -> list[Transaction]:
        """대출 실행 이후 거래(조기경보 재생용). start·end는 포함 구간이다."""
        case = authorize(authorization, ConsentScope.ACCOUNT, "/mydata/flow")
        flow = (flows or {}).get(case.profile.applicant_id)
        if flow is None:
            raise _error(404, "no_flow")
        return [
            t
            for t in flow.transactions
            if (start is None or t.booked_on >= start) and (end is None or t.booked_on <= end)
        ]

    @app.get("/tax/filings")
    def get_tax_filings(authorization: AuthHeader = None) -> list[TaxFiling]:
        return list(authorize(authorization, ConsentScope.TAX, "/tax/filings").tax_filings)

    @app.get("/pg/settlements")
    def get_pg_settlements(authorization: AuthHeader = None) -> list[PgSettlement]:
        return list(authorize(authorization, ConsentScope.PG, "/pg/settlements").pg_settlements)

    @app.post("/admin/faults", status_code=204)
    def set_fault(req: FaultRequest) -> Response:
        state["fault"] = req.mode
        seen.clear()
        return Response(status_code=204)

    return app
