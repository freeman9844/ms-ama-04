"""--fake 모드용 Foundry·Decision-1 HTTP 에뮬레이터. 운영 클라이언트(FoundryAgents, DecisionClient)와
계측 transport를 그대로 거치게 해 파이프라인 전체를 비용 없이 검증한다. 점수는 의미가 없다(배선 확인용).
"""

import json
import re
import threading
from typing import Any

import httpx

from agents.txn_classify import ABSTAIN, SYSTEMONE_PATH

_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("월세", "rent"), ("임차", "rent"), ("차임", "rent"),
    ("급여", "payroll"), ("월급", "payroll"), ("인건비", "payroll"), ("상여", "payroll"), ("수당", "payroll"),
    ("대표", "owner_transfer"), ("사장", "owner_transfer"),
    ("머니", "high_cost_lender"), ("캐피탈", "high_cost_lender"), ("리드코프", "high_cost_lender"), ("일수", "high_cost_lender"),
    ("은행", "loan_repayment"), ("정책자금", "loan_repayment"),
    ("세", "tax_or_insurance"), ("보험", "tax_or_insurance"), ("연금", "tax_or_insurance"),
    ("요금", "other_operating"), ("방역", "other_operating"),
    ("식", "supplier"), ("대금", "supplier"), ("물품", "supplier"), ("구입", "supplier"),
)
_DRAFT = {"eligible": "approve", "refer": "conditional", "ineligible": "decline"}
_EW_SIGNAL_KIND = {
    "sales_slump": "inflow_drop",
    "settlement_hold": "settlement_hold",
    "return_spike": "return_spike",
    "cost_surge": "cost_surge",
    "high_cost_borrowing": "loan_stacking",
}


def guess(description: str) -> str:
    return next((label for kw, label in _KEYWORDS if kw in description), ABSTAIN)


class FakeAzure:
    """httpx.MockTransport 핸들러. 대화별 상태로 도구 호출 흐름을 흉내 낸다."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._n = 0
        self.agents: dict[str, dict[str, Any]] = {}
        self.conversations: dict[str, dict[str, Any]] = {}
        self.deleted: list[str] = []

    def _id(self, prefix: str) -> str:
        with self._lock:
            self._n += 1
            return f"{prefix}_{self._n:06d}"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        body = json.loads(request.content) if request.content else {}
        if path == SYSTEMONE_PATH:
            label = guess(body["state"]["description"])
            return httpx.Response(200, json={"answers": {"category": {"choice": label, "confidence": 0.9 if label != ABSTAIN else 0.3}}})
        if m := re.fullmatch(r"/agents/([^/]+)/versions", path):
            self.agents[m.group(1)] = body["definition"]
            return httpx.Response(200, json={"name": m.group(1), "version": "1"})
        if path == "/openai/v1/conversations" and method == "POST":
            cid = self._id("conv")
            self.conversations[cid] = {}
            return httpx.Response(200, json={"id": cid})
        if m := re.fullmatch(r"/openai/v1/(conversations|containers)/([^/]+)", path):
            if method == "DELETE":
                if self.conversations.pop(m.group(2), None) is None and m.group(1) == "conversations":
                    return httpx.Response(404, json={"error": "not_found"})
                self.deleted.append(m.group(2))
                return httpx.Response(200, json={"id": m.group(2), "deleted": True})
            return httpx.Response(200 if m.group(2) in self.conversations else 404, json={})
        if path == "/openai/v1/responses":
            return self._respond(body)
        return httpx.Response(404, json={"error": f"fake: {method} {path}"})

    def _respond(self, body: dict[str, Any]) -> httpx.Response:
        name = body["agent_reference"]["name"]
        definition = self.agents[name]
        output_name = definition["text"]["format"]["name"]
        state = self.conversations.setdefault(body["conversation"], {})
        payload = body["input"]
        results = {o["call_id"]: json.loads(o["output"]) for o in payload} if isinstance(payload, list) else {}
        step = getattr(self, f"_{output_name}", None)
        if step is None:
            return httpx.Response(400, json={"error": f"fake: 모르는 출력 {output_name}"})
        out = step(state, payload if isinstance(payload, str) else "", list(results.values()))
        if isinstance(out, tuple):
            tool, args = out
            items = [{"type": "function_call", "call_id": self._id("call"), "name": tool, "arguments": json.dumps(args, ensure_ascii=False)}]
            text = ""
        else:
            text = json.dumps(out, ensure_ascii=False)
            items = [{"type": "message", "content": [{"type": "output_text", "text": text}]}]
        inp, outp = len(json.dumps(payload, ensure_ascii=False)) // 4 + 200, len(text) // 4 + 20
        return httpx.Response(200, json={
            "id": self._id("resp"), "status": "completed", "output": items,
            "usage": {"input_tokens": inp, "output_tokens": outp, "total_tokens": inp + outp},
        })

    # 출력 이름별 흐름. 도구 호출이면 (이름, 인자), 끝이면 출력 dict.
    def _txn_labels(self, state: dict[str, Any], text: str, results: list[Any]) -> Any:
        items = json.loads(text)["items"]
        return {"items": [{"key": i["key"], "label": guess(i["description"])} for i in items]}

    def _analyst_draft(self, state: dict[str, Any], text: str, results: list[Any]) -> Any:
        if not results:
            return ("get_metrics", {})
        metrics = results[0]["metrics"]
        decision = results[0]["policy"]["decision"]
        cited = [next(k for k in metrics if k.startswith("M:"))]
        claims = [{"text": "핵심 현금흐름 지표를 확인했다.", "metric_ids": cited, "txn_ids": []}]
        if "M:pg_reconciliation_ratio" in metrics and any(
                r["code"] == "R108" for r in results[0]["policy"]["reason_codes"]):
            claims.append({"text": "정산금 계좌 입금 비율이 기준에 못 미친다.", "metric_ids": ["M:pg_reconciliation_ratio"], "txn_ids": []})
        rec = _DRAFT[decision]
        lend = rec in ("approve", "conditional")
        officer_checks = [{"text": "정산금 계좌 미입금 원인과 선정산 계약 여부를 확인한다.",
                           "metric_ids": ["M:pg_reconciliation_ratio"]}] if "M:pg_reconciliation_ratio" in metrics else []
        return {
            "recommendation": rec,
            "summary": "정책 판정과 현금흐름 지표를 근거로 작성한 초안이다.",
            "claims": claims,
            "proposed_structure": {"amount_krw": 1_700_000 if lend else 0, "tenor_months": 6, "schedule": "level",
                                   "reduced_payment_months": [], "rationale": "가짜 모드 기본 구조"},
            "counterfactuals": [],
            "officer_checks": officer_checks,
        }

    def _review(self, state: dict[str, Any], text: str, results: list[Any]) -> Any:
        payload = json.loads(text[text.index("{"):])
        claims = payload["claims"]
        checks = payload.get("officer_checks", [])
        return {
            "verdicts": [{"claim_index": c["claim_index"], "supported": True, "reason": "근거와 일치"} for c in claims],
            "feedback": "",
            "check_verdicts": [
                {"check_index": c["check_index"], "supported": True, "reason": "근거와 일치"} for c in checks
            ],
        }

    def ew_cause(self, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
        """주원인으로 고를 후보. 테스트에서 덮어써 '원인 오답' 흐름을 만든다. None이면 경보하지 않는다."""
        return candidates[0] if candidates else None

    def _ew_decision(self, state: dict[str, Any], text: str, results: list[Any]) -> Any:
        if not results:
            return ("get_baseline", {})
        r = results[0]
        if "cause_candidates" in r:
            candidates = r["cause_candidates"]
            chosen = self.ew_cause(candidates)
            if chosen is None:
                return {"decision": "no_alert", "reason": "계절 변동 범위로 본다."}
            cause = chosen["cause"]
            others = [c["cause"] for c in candidates if c["cause"] != cause][:2]
            return ("raise_alert", {
                "severity": "medium", "summary": "탐지기가 지목한 원인 후보를 근거로 경보를 올린다.",
                "check_action": "원인 후보의 정산·지출 추이 확인이 필요합니다.",
                "recommended_action": "contact_borrower",
                "signals": [{"kind": _EW_SIGNAL_KIND[cause], "text": "원인 후보의 주차 지표가 기준선에서 벗어났다.",
                             "metric_ids": list(chosen["evidence_ids"]), "txn_ids": []}],
                "primary_cause": cause,
                "secondary_causes": others,
            })
        return {"decision": "alert" if r.get("accepted") else "no_alert", "reason": "탐지 신호를 확인했다."}

    def _judge_scores(self, state: dict[str, Any], text: str, results: list[Any]) -> Any:
        return {"groundedness": 4, "relevance": 4, "coherence": 4, "reason": "가짜 판정"}

    def _probe_reply(self, state: dict[str, Any], text: str, results: list[Any]) -> Any:
        return {"reply": "ok"}
