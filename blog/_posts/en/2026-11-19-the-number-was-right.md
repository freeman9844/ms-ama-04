---
layout: post
title: "The Number Was Right. Was the Money There?"
date: 2026-11-19 09:00:00 +0900
lang: en
ref: day-18
days: "2026-11-19"
permalink: /en/day-18/
summary: "An expected settlement and a bank deposit can carry the same number. I asked the agent to help an officer tell which one could fund the next payment."
---
Hello again. Yesterday's screen made it easier to find an application. Today's question was what an officer would learn by opening it. If the AI repeated the figures already on screen, the officer still had to do the underwriting work from scratch.

## Changing the assignment for two agents

I asked for an opinion rather than a summary, and specifically for the analyst to use marketplace performance alongside bank cash flow. One synthetic seller illustrates why. We had four months of bank observations and a marketplace account open for 24 months. The longer history gave context to the short bank record. It didn't tell us what would happen if the seller's only sales channel stopped settling.

We changed both the writer and its independent reviewer. The reviewer now sees selected cash-flow and marketplace evidence even if the writer leaves it out. It can send the whole opinion back for revision when the individual claims pass but the conclusion doesn't follow. That's a rejection of a paragraph, not a rejection of a loan. The lending rules and the officer's decision stayed where they were.

## Reading the result, not just the score

In the deployed evaluation, one opinion treated roughly 2.19 million won in expected settlements as a possible inflow, not cash already available. It asked the officer to match the settlement to an actual bank deposit before the next repayment. That last instruction was what I had wanted out of the change: a document to check, a time to check it, and a reason.

After deployment and a smoke check, we ran the paid underwriting evaluation once. With the production analyst and reviewer, all eleven synthetic applications passed final evidence and independent review checks. The comparison analyst produced eleven opinions too, but only nine passed review. The report listed three failure flags: two opinions failed review, and one of those also failed the evidence check. Three flags did not mean three failed applications.

Later, reviewing the write-up, we found one more distinction worth spelling out. No opinion after a retry stops the application. An opinion that exists but still hasn't passed review can reach the officer with the outstanding issues marked. A single arrow labelled “assessment available” had hidden the difference. Passing the demo checks didn't prove that officers made better lending decisions; for that we'd have to observe what they actually did with the opinion.

## Takeaway

Ask the agent to show one case where every number is correct but the conclusion is wrong. Then read the revised output: does it tell a person what to check next?
