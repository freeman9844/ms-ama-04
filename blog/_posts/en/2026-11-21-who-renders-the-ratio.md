---
layout: post
title: "Who puts 80.2% in the answer?"
date: 2026-11-21 09:00:00 +0900
lang: en
ref: day-20
days: "2026-11-21"
permalink: /en/day-20/
summary: "A paired settlement experiment changed the policy reason without changing the loan limit. The chat still left out the number."
---
I asked the officer assistant for the full-request opinion, the settlement reconciliation rate, and the next check. It told me to defer judgment. In one case it even told me to compare unmatched settlements with bank transactions. It did not tell me the rate I'd just asked for.

## Same request, different deposits

We ran two synthetic cases through the policy service and real models in Azure. Both used the same seller, a six-month request for KRW 5 million, and the same marketplace and settlement records. In the second case we reduced selected bank deposits by a total of KRW 1,822,888.

| | Original deposits | Reduced deposits |
| --- | --- | --- |
| Settlement amount matched to deposits | 100% | 80.2% |
| Policy outcome | Eligible | Refer for review (R108) |
| Policy limit | KRW 5 million | KRW 5 million |
| Reviewed AI opinion on the full request | Defer | Defer |

The limit didn't move, but the question for the officer did. The unmatched portion is a reason to check the statement, timing and destination account; it is not proof of a loss. And an eligible request doesn't automatically get an approval recommendation. We deliberately didn't ask the assistant to invent a smaller loan.

## What exactly is the denominator?

The unit behind that percentage is **won, not rows**. For each eligible settlement we look for an unused deposit from the same platform, for the same net payout, booked between the payout date and three business days later. We divide the sum of the matched net payouts by the sum of the eligible net payouts. A deposit can only be used once. Recent payouts still inside the waiting window are excluded rather than counted as missing.

Here's a small example, separate from the Azure run. Suppose ten payouts have all had time to arrive: nine for KRW 10,000 each and one for KRW 910,000. Match the nine small ones and the **count** says 9/10, or 90%. But only 90,000 of 1,000,000 won has been reconciled: the **amount** says 9%. That distinction matters to an officer looking for cash available for repayment. Neither figure proves what happened to the large payout.

One trap in our paired run was to read the unchanged KRW 5 million limit as “the marketplace data made no difference.” It did: policy returned a reason to investigate in one case and not the other. The limit answers “what ceiling did the policy calculate?” The review answers “what do we still need to know about this request?” We can get the same verdict twice for different reasons.

## A model can select evidence without owning the number

The chat model selects evidence by field name. The server rejects invalid selections rather than displaying unverified prose. That was useful, but it also meant an empty selection could hide a perfectly good number already on the detail page.

We kept that boundary. When the question explicitly asks for the reconciliation rate, the server formats the recorded ledger ratio itself: `0.802` becomes `80.2%`. Zero is `0%`; a missing rate is “not recorded,” not zero. Other evidence can still come from valid model selections.

## The smallest test doesn't need a convincing model

For the local regression I wanted a *bad* model answer: an unknown field followed by `90%`, while the stored ratio was `0.802`. The expected answer contains `80.2%` exactly once. Neither `90%` nor the unknown field may make it to the screen. Another case has the model select the rate itself: that must not print it a second time. These tests exercise the boundary even if tomorrow's model happens to choose all the right fields.

Zero and missing needed separate cases. A recorded zero is a measurement: eligible payouts existed, but none of their value matched. With no usable denominator, there is no rate to report. Turning both into `0%` would quietly convert “we don't know” into adverse evidence. We changed the rendering of evidence, not the policy rule or the officer's decision.

## Takeaway

We checked two different things. The paired Azure experiment produced the 80.2% ledger value and exposed the omission. After the display fix, local tests covered 80.2%, zero and missing data; on the deployed console we verified that a real chat answer matched a **100%** detail page. That's not a claim that we repeated the 80.2% case on the final screen. Knowing which check proves which claim is part of the fix.
