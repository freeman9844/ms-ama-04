---
layout: post
title: "Checking a settlement amount is not checking its availability"
date: 2026-11-19 09:00:00 +0900
lang: en
ref: day-18
days: "2026-11-19"
permalink: /en/day-18/
summary: "An expected settlement can match the ledger exactly without having reached the bank account."
---
An expected settlement can match the ledger exactly without having reached the bank account. A numeric check could therefore pass even when the repayment conclusion was wrong. We asked the analyst to compare bank and marketplace evidence and gave the reviewer a separate verdict on the opinion as a whole.

## Changing the assignment for two agents

I asked for an opinion rather than a summary, and specifically for the analyst to use marketplace performance alongside bank cash flow. One synthetic seller illustrates why. We had four months of bank observations and a marketplace account open for 24 months. The longer history gave context to the short bank record. It didn't tell us what would happen if the seller's only sales channel stopped settling.

We changed both the writer and its independent reviewer. The reviewer now sees selected cash-flow and marketplace evidence even if the writer leaves it out. It can send the whole opinion back for revision when the individual claims pass but the conclusion doesn't follow. That's a rejection of a paragraph, not a rejection of a loan. The lending rules and the officer's decision stayed where they were.

## Reading the result, not just the score

The obvious fix was another line in the analyst prompt: “use more marketplace data.” But the analyst already had ratings, review counts, and refund rates. There was already a check on numbers in cited claims. What was missing came *after* that check: can two correctly quoted figures support the sentence connecting them? A reviewer given only the analyst's selection can miss exactly the counterevidence the analyst missed.

We changed both sides of the review contract. On input, the reviewer received available marketplace aggregates independently of the claims, alongside the debt-service coverage ratio (DSCR), cash-buffer days, and the settlement-to-bank reconciliation ratio. On output, `summary_supported` became a verdict on the opinion as a whole. A negative verdict sends the reason into the next revision even when every individual claim passes. The existing limit of three rounds still applies; an opinion can finish with an explicit review failure.

One regression test deliberately makes the reviewer accept every claim but reject the summary for treating an expected settlement as cash. It checks that the analyst actually receives that reason on its next turn. Another holds the draft constant and changes only an *uncited* reconciliation ratio from 0.9 to 1.0. If the reviewer's input stays the same, no model can distinguish those two situations. Missing marketplace data is a different case again: the opinion should state the limit of its evidence, not turn missing data or lack of consent into poor sales performance.

In the deployed evaluation, one opinion treated roughly 2.19 million won in expected settlements as a possible inflow, not cash already available. It asked the officer to match the settlement to an actual bank deposit before the next repayment. That last instruction was what I had wanted out of the change: a document to check, a time to check it, and a reason.

After deployment and a smoke check, we ran the paid underwriting evaluation once. With the production analyst and reviewer, all eleven synthetic applications passed final evidence and independent review checks. The comparison analyst produced eleven opinions too, but only nine passed review. The report listed three failure flags: two opinions failed review, and one of those also failed the evidence check. Three flags did not mean three failed applications.

## What the two configurations actually measured

We held the reviewer fixed at `gpt-5.6-luna` and changed the analyst. “Evidence check” means a deterministic code check. “Review passed” means the independent reviewer accepted the opinion. Neither is a loan approval.

| Analyst | First-round evidence check | Final review passed | Mean rounds | Estimated cost per opinion |
|---|---:|---:|---:|---:|
| `gpt-5.6-sol` (deployed pairing) | 10/11 | 11/11 | 1.27 | $0.1759 |
| `gpt-5.6-luna` (comparison) | 2/11 | 9/11 | 2.00 | $0.0164 |

The comparison analyst was substantially cheaper per opinion even after more revisions. The deployed analyst needed fewer corrections on its first drafts, and none of its opinions exhausted review without passing on this sample. Collapsing those facts into “the better model” loses the information needed to choose. The whole evaluation was estimated at about $2.27, including calls to a separate judging model. The table isn't a bill, and it doesn't price an officer's time spent examining an unresolved issue.

## A reachable console isn't evidence of a new opinion

The console didn't need a release for this change. We deployed the Functions code that generates the opinions and the image containing the evaluation harness. With the deployment window closed, the smoke check passed and the console returned HTTP 200. We then ran the underwriting evaluation with the new image. The HTTP response showed that the access path worked; the opinions and round results from new synthetic applications showed that the model could use the revised review contract. Previously stored opinions were not supposed to change.

Later, reviewing the write-up, we found one more distinction worth spelling out. No opinion after a retry stops the application. An opinion that exists but still hasn't passed review can reach the officer with the outstanding issues marked. A single arrow labelled “assessment available” had hidden the difference. Passing the demo checks didn't prove that officers made better lending decisions; for that we'd have to observe what they actually did with the opinion.

## Takeaway

Ask the agent to show one case where every number is correct but the conclusion is wrong. Then read the revised output: does it tell a person what to check next?
