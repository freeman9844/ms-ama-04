---
layout: post
title: "\"That's Not in the Data\""
date: 2026-11-09 09:00:00 +0900
lang: en
ref: day-13
days: "2026-11-09 – 2026-11-10"
permalink: /en/day-13/
summary: "My demo's review assistant said a rate band's criteria were \"not in the data\"; asking what it would need exposed a wiring gap, and the same shape turned up twice more in two days."
---
Last time I moved my "help me understand" questions into a separate session. On the 9th, the one that couldn't answer was my own demo: the loan officer's chat assistant told me that what I'd asked was "not in the data."

## What I was trying to do

By now the project was a running app getting small improvements. Just after midnight on the 9th, the agent reported a new chat panel live in the console's decision tab. The feature is called the review assistant: an LLM that answers a loan officer's questions about one application and cites its sources. Four test questions were answered, and an address question got "not in the data."

Until the evening of the 10th, several of my messages show me clicking through screens and asking about what I saw: rate bands, alert levels, the ratios on the settlement screen.

## How I handed it to the agent

At 10:06 I pasted the assistant's answer back to the agent:

> "I asked the review assistant about 'rate band A' and got: 'The rate band is shown as A (policy decision). However, the specific rate or criteria for band A are not in the data.' What more information would it need to answer properly?"

I didn't ask it to fix the chatbot. I asked what was missing. The reply split the problem in two, and that split was the most useful thing I took from these two days:

> "The answer wasn't wrong. It mixes information the system doesn't have with information the system has but doesn't pass to the chat."

- **Exists, but not passed:** the band table. Policy v3 splits bands only by DSCR: A at 2.0 or more, B at 1.5, C at 1.2, D at 1.0. The chat got the band letter and the application's DSCR, but not the table, so it couldn't say *why* A.
- **Doesn't exist at all:** a real interest rate per band. No code defined "A = x% a year." Monthly repayments used one assumed annual rate of 8% for every band.

So the assistant had been honest. The gap was in what we had handed it.

A minute later I followed up: "How do we make the review assistant able to answer questions about these policy decisions?" The agent sorted such questions into two kinds: what the rule says, and how it was applied to this case. It proposed passing both: the rule text for the version used, and a deterministic trace of the calculation. I typed "yes."

Then came a choice before code. The policy engine was a separate service the console didn't import, so there were three routes: (A) copy the rule files and pure engine code into the console image, (B) store an explanation at decision time, or (C) call a new policy API. The agent recommended A and named its cost: it weakens the separation between console and policy package. B would mean redeploying three components and couldn't explain past decisions. I picked A. The spec came at 10:12, I typed "approve" at 10:13, and a four-task plan followed, run directly this time because the tasks were few and in order.

One rule in the spec stood out. If recomputing a decision disagrees with the stored one, the chat drops the trace and says why, because that beats showing wrong reasoning. Per-band rates stayed out of scope, so for those the chat would keep saying "not in the data."

## Where it went sideways

**A rule file had changed under the same name.** Before the deploy, the log shows an English brief I never typed, sent to a "final whole-branch reviewer." It found one issue: on 11/04 a ruleset had been edited in place, changing the product's amount limits but keeping its version label. An old decision would have been "explained" with limits never applied to it. The 10:32 fix stops explaining those old decisions. Live, all four questions got answers. Band B was explained from a DSCR of 1.76 and the v3 table. The real rate per band was still "not in the data", and now that answer was the whole truth.

**The alert said "high"; the card said "no contact."** In the afternoon I pointed at one loan: the alert was high, "but neither a hardship offer nor a regular missed-payment notice went out?" The agent checked. A hardship offer *had* gone out at 16:13, after the third instalment was missed, but it was recorded on the collections card further down. The early-warning card, by design, always said "for officer review · no contact with borrower."

Each card was correct on its own, and together they told the reader something false. I wrote back:

> "If a hardship offer was actually sent, it looks like it should be recorded on the alert card that raised the alert. The Early-Warning Agent runs as L4 full self-driving."

**The settlement ledger had more in it than we used.** On the evening of the 10th I asked for more explanation on the settlement reconciliation card, and for ideas: "Beyond cash flow, marketplace settlement looks like another important point." The agent's reply included a list titled "already in the ledger but unused": each channel's payout cycle, ad-cost offsets recorded apart from fees, and deposits from advance-payment companies marked "advance." It was the same shape as the rate band. The data was there; nothing was reading it.

## How I got past it

**For the alert card, a real choice.** The agent took my "L4" at face value and turned it into options. A, display only: keep the behaviour, link the contact to the alert behind it, and fix the "no contact" wording. B, L4 autonomous action: the early-warning agent contacts borrowers itself, even with no missed payment, with more write permissions and revised specs. I picked A. The records don't say why; I can only note that I had just claimed L4 autonomy and then chose not to build it. At 16:51 the high alert card showed the chain: missed instalment → collections agent → hardship offer sent → rescheduling request waiting for the officer.

**For settlement, I narrowed and let the agent decide the small things.** I wrote "Do 1–3 first." The agent listed decisions it had already made, for me to override: three new metrics in the deterministic ledger code, and policy kept at v3, since changing it would shift every existing evaluation's expected values. It asked only where the agent's recommendations should live. I answered "B". From the spec that followed, I think that meant a field of up to three officer checks, each with evidence.

Before showing me the spec, the agent had a separate reviewer read it. The brief is one I want to reuse:

> "You will not be able to talk to the person who wrote it. … Report only the questions you'd still need answered before you could plan this well."

The reviewer came back with three questions. The agent answered them in the spec and listed its answers for me to check. I said "yes."

The plan had nine tasks, and this time the agent recommended subagents, because the tasks leaned on each other's interfaces. Each task went to an implementer, then a task reviewer. Three tasks needed one fix round. The Task 3 review found a Critical: a new required field broke two orchestrator tests outside the diff. The Task 4 brief then carried it forward as an "Important regression lesson from Task 3." The final whole-branch review said "with fixes": one metric counted settlements where the spec said amounts, the docs pointed to the wrong tab, and one analyst instruction was missing. After a fix wave and a re-review, 2,896 tests passed, plus one known, unrelated docs failure. At 23:47 the agent asked before deploying and pushing, and I approved.

**Two smaller moments on the 10th also put understanding before building.** For a "no NSG" security warning, the agent found the cause before adding anything: four subnets had no NSG in our Bicep, so a tenant policy kept attaching empty ones. It added our own NSGs with default rules only and deleted the orphans. And when I asked about moving JSON processing to a Fabric medallion pipeline, it advised against putting Fabric on the real-time path and suggested a read-only analytics layer instead. Nothing was built.

Looking back, "not in the data" was the best answer the assistant could have given. It didn't make up a rate; it marked the edge of what it had been given. My part was to ask what lay past that edge, and the agent's part was to say which pieces were missing and which were just unconnected.

## Takeaway

When an AI feature says "that's not in the data," ask what it would need, and have the agent separate what doesn't exist from what exists but was never passed along.
