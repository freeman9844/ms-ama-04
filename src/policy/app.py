"""Policy Engine HTTP API. stateless이며 입력 검증 실패 시 422로 fail-closed 한다."""

from fastapi import FastAPI

from policy.engine import evaluate
from policy.rules import RuleSet
from shared.contracts import CashFlowFeatures, PolicyDecision


def create_app(rules: RuleSet) -> FastAPI:
    app = FastAPI(title="Policy Engine")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "rules_version": rules.version}

    @app.post("/v1/decisions")
    def decide(features: CashFlowFeatures) -> PolicyDecision:
        return evaluate(features, rules)

    return app
