"""Detect monetary counteroffers without treating the exact full request as a counteroffer."""

import re
from decimal import Decimal

# Catch explicit offers even when the figure happens to match a cited metric.
# The independent reviewer also checks implicit proposals in context.
_NUMBER = r"\d[\d,]*(?:\.\d+)?"
_REQUEST_APPROVAL = re.compile(
    rf"(?:신청|요청)\s*(?:(?:금액|액)(?:\s*전액)?|전액)(?:인)?\s*[:：]?\s*"
    rf"(?P<number>{_NUMBER})\s*(?P<unit>억|천만|백만|만|천)?\s*원(?=\s*(?:을|를)?\s*승인)"
)
_KRW_UNITS = {None: 1, "천": 1000, "만": 10000, "백만": 1000000, "천만": 10000000, "억": 100000000}
_USD_REQUEST = re.compile(rf"\bapprove\s+(?:the\s+)?(?:full\s+)?requested\s+(?:amount\s+(?:of\s+)?)?"
                          rf"(?:USD\s*|\$)\s*(?P<number>{_NUMBER})", re.I)
_MONEY = rf"(?:(?:[₩$]|KRW|USD)\s*{_NUMBER}|{_NUMBER}\s*(?:억|천만|백만|만|천|million|thousand|[mk])?\s*(?:원|won|krw|dollars?))"
_USD_VALUE = rf"-?(?:(?:\$|USD)\s*{_NUMBER}|{_NUMBER}\s*(?:dollars?|USD))"
_FACT_LABEL = r"(?:deposit|balance|settlement|transaction|revenue|sales|payment|policy (?:cap|limit)|remaining amount)"
_REFERENCED_VALUE = r"(?:this|that|the (?:same|preceding|above|previous|cited|verified)) (?:amount|sum|figure|value)"
_USD_FACT = re.compile(
    rf"(?:(?:verify|check|confirm)\s+(?:the\s+)?{_USD_VALUE}(?:\s+{_FACT_LABEL})?"
    rf"|(?:the\s+|your\s+)?(?:outstanding\s+|pending\s+)?{_FACT_LABEL}\s*(?::|=|is|was|of)?\s*{_USD_VALUE}"
    rf"|(?:which|what)\s+(?:month|week|day|period)\s+(?:had|included|contained)\s+"
    rf"(?:(?:the\s+)?{_USD_VALUE}\s+{_FACT_LABEL}|{_REFERENCED_VALUE})"
    rf"|{_USD_REQUEST.pattern}(?:\s+in full)?)", re.I)
_EVIDENCE_SUBJECT = rf"(?:(?:the |its )?(?:{_FACT_LABEL}|loan file|record|evidence)(?: timing| date)?|{_REFERENCED_VALUE})"
_EVIDENCE_STATE = r"(?:uncertain|unknown|unverified|verified|pending|missing|incomplete|complete|unconfirmed|confirmed|recorded)"
_EVIDENCE_CLAUSE = re.compile(
    rf"(?:{_EVIDENCE_SUBJECT} (?:is|was|remains) {_EVIDENCE_STATE}"
    rf"|(?:is|was) {_EVIDENCE_SUBJECT} {_EVIDENCE_STATE}"
    rf"|(?:verify|check|confirm) {_EVIDENCE_SUBJECT})", re.I)
_PROPOSAL = re.compile(
    rf"{_MONEY}\s*(?:으로|로|만|까지|을|를|의)?\s*(?:감액|승인|대출\s*(?:제안|권고)|제안|권고|추천)"
    rf"|(?:제안|추천|권고|대안|승인)\s*(?:금액|액)?(?:은|는)?\s*[:：]?\s*{_MONEY}"
    rf"|(?:대출|신청|승인)\s*(?:금액|액|금)(?:을|를)?\s*{_MONEY}\s*(?:으로|로)\s*"
    r"(?:낮출|줄일|감액할|조정할|축소할)\s*(?:것|방안)(?:을|를)?\s*(?:권고|추천|제안)"
    r"|(?:균등|계절|분할|거치)\s*상환(?:을|으로)?\s*(?:제안|추천|권고)"
    rf"|{_MONEY}\s+(?:(?:loan\s+)?instead|would (?:work|be (?:safer|suitable)))\b"
    rf"|\b(?:smaller|reduced|alternative|lower)\s+(?:loan|facility|principal|amount)\b"
    rf"|\b(?:recommend|suggest|propose|offer|lend|approve|cap|limit|reduce|counteroffer)\w*\s+"
    rf"(?:(?:lending|a|an|loan|amount|of|at|to|up|the|only|instead|for)\s+)*(?:{_MONEY}|policy\s+(?:cap|limit))"
    rf"|\b(?:suggested|proposed|recommended)\s+(?:loan\s+)?amount\s*(?:is|of|:)?\s*{_MONEY}"
    r"|\b(?:recommend|suggest|propose|offer)\w*\s+(?:a\s+)?(?:[\w-]+\s+){0,3}repayment\s+(?:term|schedule|plan)"
    r"|(?:정책\s*)?한도(?:만큼|까지)\s*(?:승인|대출|제안|권고)",
    re.IGNORECASE,
)


def has_proposal(text: str, requested: int | None, *, usd: bool = False) -> bool:
    # Treat a labelled, exact request as "the full request" only in an approval clause.
    # Other offers in the same sentence must still be inspected.
    def full_request(match: re.Match[str]) -> str:
        amount = Decimal(match["number"].replace(",", "")) * _KRW_UNITS[match["unit"]]
        return "신청 전액" if amount == requested else match[0]

    if usd:
        if re.search(r"\bcounter[- ]?offer\w*\b", text, re.I):
            return True
        for match in _USD_REQUEST.finditer(text):
            if Decimal(match["number"].replace(",", "")) * 100 != requested:
                return True
        # A citation (including an anaphoric one) cannot license an arbitrary instruction
        # in the next clause. Accept only an evidence role or the exact request above.
        if "$" in text or re.search(_USD_VALUE, text, re.I) or re.search(rf"\b{_REFERENCED_VALUE}\b", text, re.I):
            for part in re.split(r"[;!?]|\.(?!\d)", text):
                clause = part.strip()
                if clause and not (_USD_FACT.fullmatch(clause) or _EVIDENCE_CLAUSE.fullmatch(clause)):
                    return True
        text = _USD_REQUEST.sub("approve the full request", text)
        text = re.sub(r"\bpolicy (?:limit|cap)(?=\s*[:=]?\s*(?:USD|\$))", "policy fact", text, flags=re.I)
    return bool(_PROPOSAL.search(_REQUEST_APPROVAL.sub(full_request, text)))
