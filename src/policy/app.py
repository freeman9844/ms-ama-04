"""Policy Engine HTTP API. stateless이며 입력 검증 실패 시 422로 fail-closed 한다."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from policy.engine import evaluate
from policy.rules import RuleSet
from shared.contracts import CashFlowFeatures, PolicyDecision

FAULT_MODES = ("none", "unavailable")


def create_app(rules: RuleSet, fault: str = "none") -> FastAPI:
    if fault not in FAULT_MODES:
        raise ValueError(f"FAULT_MODE: {fault}")
    app = FastAPI(title="Policy Engine")

    if fault == "unavailable":

        @app.middleware("http")
        async def unavailable(request: Request, call_next):
            if request.url.path == "/v1/decisions":
                return JSONResponse({"code": "unavailable"}, status_code=503)
            return await call_next(request)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "rules_version": rules.version}

    @app.post("/v1/decisions")
    def decide(features: CashFlowFeatures) -> PolicyDecision:
        return evaluate(features, rules)

    return app
