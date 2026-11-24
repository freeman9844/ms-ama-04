"""Validate dollar citations against integer-cent evidence, separately from ratios and counts."""
import re
from collections.abc import Iterable
from dataclasses import replace

from agents.grounding import ungrounded
from agents.runtime import AgentRun, AgentSpec
from shared.amount_advice import has_proposal
from shared.money import parse_usd_cents

_AMOUNT = r"[-+]?\d[\d,]*(?:\.\d+)?"
_DOLLARS = re.compile(rf"(?P<sign>-?)(?:\$|USD\s*)\s*(?P<a>{_AMOUNT})|(?P<b>{_AMOUNT})\s*(?:dollars?|USD)\b", re.I)
_OTHER = re.compile(r"[₩€£¥₹가-힣ㄱ-ㅎㅏ-ㅣ]|\b(?:krw|won|eur|gbp|cad|aud|nzd|sgd|hkd|jpy|cny|inr|"
                    r"yen|yuan|euros?|pounds?)\b|\b(?:ca?|au?|nz|hk|s)\$", re.I)

_TOOLS = {
    "get_metrics": "Read metric IDs and values, policy reasons, classification and watch indicators.",
    "get_monthly_series": "Read complete months of cash flow in cents and monthly seasonal indices.",
    "get_settlement_summary": "Read aggregate settlement reconciliation, concentration and deduction metrics.",
    "query_transactions": "Read up to twenty masked transactions matching the category and period.",
    "get_weekly_series": "Read weekly metrics and forecasts up to the observation date.",
    "get_baseline": "Read baseline metrics, loan terms, triggers and possible causes.",
    "get_peer_cohort": "Read available peer metrics.",
    "get_settlement_ledger": "Read settlement aggregates for the specified inclusive week range.",
    "raise_alert": "Submit an evidence-backed alert. Only the first accepted alert is recorded.",
    "get_case": "Read the missed installment and recent alert.",
    "get_contact_history": "Read recent contacts and remaining contact allowance.",
    "list_scripts": "List approved English borrower scripts.",
    "propose": "Propose an approved script and optional hardship consultation. Nothing is sent.",
}


def native_spec(spec: AgentSpec, instructions: str) -> AgentSpec:
    def schema(node):
        if isinstance(node, dict):
            return {k: schema(v) for k, v in node.items() if k != "description"}
        if isinstance(node, list):
            return [schema(v) for v in node]
        return node

    tools = tuple({**schema(tool), "description": _TOOLS[tool["name"]]} if tool.get("type") == "function"
                  else tool for tool in spec.tools)
    return replace(spec, instructions=instructions, tools=tools, output_schema=schema(spec.output_schema))


def run_record(run: AgentRun, product) -> dict:
    record = run.record()
    if product:
        # Rejected write-tool arguments may contain unverified narrative or identifiers.
        record["tool_calls"] = [{"name": c.name if c.name in _TOOLS else "unknown_tool",
                                 "error": bool(c.output.get("error") or c.output.get("accepted") is False)}
                                for c in run.tool_calls]
    return record


def english(text: str) -> bool:
    return bool(re.search("[A-Za-z]", text)) and not _OTHER.search(text)


def issues(text: str, cents: Iterable[int] = (), numbers: Iterable[float] = (), *, requested: int | None = None) -> list[str]:
    if not english(text) or has_proposal(text, requested, usd=True):
        return ["Use English USD evidence only; do not propose different credit terms."]
    amounts = {v for v in cents if type(v) is int}
    errors = []

    def verify(match: re.Match) -> str:
        value = match["a"] or match["b"]
        try:
            signed = parse_usd_cents(value.lstrip("-")) * (-1 if value.startswith("-") or match["sign"] else 1)
        except ValueError:
            signed = None
        if signed not in amounts or re.match(r"\s*(?:million|billion|thousand|[km])\b", text[match.end():], re.I):
            errors.append("Dollar amount does not match the cited cent evidence.")
        return " "

    remainder = _DOLLARS.sub(verify, text)
    if re.search(r"\$|\b(?:dollars?|cents?)\b", remainder, re.I):
        errors.append("Use a numeric USD amount.")
    return errors + ungrounded(remainder, list(numbers), include_small=True)
