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
_MONEY = rf"(?:(?:[₩$]|KRW|USD)\s*{_NUMBER}|{_NUMBER}\s*(?:억|천만|백만|만|천|million|thousand|[mk])?\s*(?:원|won|krw|dollars?))"
_PROPOSAL = re.compile(
    rf"{_MONEY}\s*(?:으로|로|만|까지|을|를|의)?\s*(?:감액|승인|대출\s*(?:제안|권고)|제안|권고|추천)"
    rf"|(?:제안|추천|권고|대안|승인)\s*(?:금액|액)?(?:은|는)?\s*[:：]?\s*{_MONEY}"
    rf"|(?:대출|신청|승인)\s*(?:금액|액|금)(?:을|를)?\s*{_MONEY}\s*(?:으로|로)\s*"
    r"(?:낮출|줄일|감액할|조정할|축소할)\s*(?:것|방안)(?:을|를)?\s*(?:권고|추천|제안)"
    r"|(?:균등|계절|분할|거치)\s*상환(?:을|으로)?\s*(?:제안|추천|권고)"
    rf"|\b(?:recommend|suggest|propose|offer|lend|approve|cap|limit|reduce)\w*\s+"
    rf"(?:(?:lending|a|an|loan|amount|of|at|to|up|the|only|instead|for)\s+)*(?:{_MONEY}|policy\s+(?:cap|limit))"
    rf"|\b(?:suggested|proposed|recommended)\s+(?:loan\s+)?amount\s*(?:is|of|:)?\s*{_MONEY}"
    r"|\b(?:recommend|suggest|propose|offer)\w*\s+(?:a\s+)?(?:[\w-]+\s+){0,3}repayment\s+(?:term|schedule|plan)"
    r"|(?:정책\s*)?한도(?:만큼|까지)\s*(?:승인|대출|제안|권고)",
    re.IGNORECASE,
)


def has_proposal(text: str, requested: int | None) -> bool:
    # Treat a labelled, exact request as "the full request" only in an approval clause.
    # Other offers in the same sentence must still be inspected.
    def full_request(match: re.Match[str]) -> str:
        amount = Decimal(match["number"].replace(",", "")) * _KRW_UNITS[match["unit"]]
        return "신청 전액" if amount == requested else match[0]

    return bool(_PROPOSAL.search(_REQUEST_APPROVAL.sub(full_request, text)))
