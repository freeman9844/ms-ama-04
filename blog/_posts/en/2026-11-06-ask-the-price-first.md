---
layout: post
title: "Separating the WAF's fixed cost from AI cost per application"
date: 2026-11-06 09:00:00 +0900
lang: en
ref: day-11
days: "2026-11-06 – 2026-11-07"
permalink: /en/day-11/
summary: "Before adding a WAF at the public entry point, I asked what it would cost to leave running."
---
Before adding a WAF at the public entry point, I asked what it would cost to leave running. A monthly infrastructure estimate and the model cost of processing one application answer different questions. One informed the deployment choice; the other needed measurement on the actual request path.

## Objective

The loan officer's console was running in an external Container Apps environment, open only to my IP. The next step I had in mind was to put an Application Gateway with WAF v2 at the very front of the architecture.

But before asking for it, I asked a different question first.

## Scope and procedure

> "If I put App Gateway v2 and a WAF at the very front, how much will the cost go up? And if I leave the test environment as it is instead of deleting it, roughly how much will it cost each month?"

Two minutes later the answer came back: about **$370 a month** more for the WAF gateway, and about **$410 a month** if I left the test environment as it was. The agent said both numbers came from the Korea Central list prices and from the actual bill in Cost Management for 10/31–11/05. Most of it was the WAF v2 fixed charge ($0.486 an hour, about $355 a month), which runs even with no traffic; Standard v2 without the WAF would be about $210.

A minute later, I made the request:

> "Please add App Gateway WAF v2 at the very front of the architecture."

The agent didn't start building. It moved into brainstorming and asked me four multiple-choice questions. Who should get in? Login was off in the demo, so opening it to the internet would let anyone press the approve button; I picked my IP only. Then TLS (self-signed) and where the console should sit (internal). The last form covered WAF mode and, again, cost: "about $370 a month, fixed, while it's on." I chose Prevention mode, said I didn't need to demo SQL injection or XSS blocking, and chose to keep it on all the time.

I think that second appearance of the price is the useful part. The number wasn't a surprise on the bill later. It was one of the design choices, sitting next to the security ones.

The spec was committed at 1:13 a.m. While writing the plan, the agent flagged a conflict with its own spec: a tenant policy forces Key Vault public access off, so the certificate step as designed wouldn't work. It offered an alternative that keeps the private key off disk, and I picked it.

The plan was finished at 7:53 a.m. with seven tasks, and I chose to run it with subagents: each task went to a fresh implementer agent, then to a fresh reviewer. Between 7:54 and 9:02 a.m., tasks 1–6 went through that loop. The reviewers found one Important issue in total: the docs' quick-start ran the smoke check before the gateway existed. It was fixed in one round, and 1,949 tests were passing.

## Observed problems

Task 7 was the live rollout, which started by deleting the old console and its environment, and before it the agent asked again. The console would be down for about 30–60 minutes, and the gateway "also adds about $370/month in fixed cost." I approved.

**The plan had a wrong assumption.** The first deployment run failed after 632 seconds with `ApplicationGatewayNetworkIsolationRequiresSubnetDelegation`. This subscription had App Gateway network isolation turned on, so the gateway's subnet had to be delegated. The agent said it plainly: "The plan wrongly assumed the subnet needs no delegation." It logged a plan defect and handed a small fix to a new subagent.

**Then the internet couldn't get in.** The second run took 1,468 seconds. The gateway was running and its backend was healthy, yet ports 80 and 443 timed out from outside. A tenant policy had attached its own NSG to the subnet, because that subnet had none, and the NSG let in only the gateway management traffic. A temporary allow rule confirmed the cause, and the agent didn't leave it there: "without IaC the policy NSG would revert the fix." A second fix subagent made the subnet's NSG part of the Bicep. The third run took 479 seconds.

My own IP then changed, so the WAF blocked me too until the allowed IP was updated. At 12:47 p.m. the next day, it happened again:

> "I went into the console to test, and it doesn't work. Please check the IP and make the console accessible."

There were two causes: my IP had changed again, and the gateway had been stopped at 12:52 a.m. by an identity outside our resource group. The agent guessed it was the tenant's cost-saving automation; I can't confirm that from my records. The gateway I had chosen to keep on all the time had been switched off overnight anyway.

**The cheaper model didn't work.** The same day I asked: "Please change the analysis agent to gpt-6.1 sol." The agent found that the GlobalStandard quota was already used up by other resources in the subscription, and listed the unit prices per million tokens: $2 in and $10 out for gpt-6.1-sol, $4 and $20 for gpt-5.6-sol. Then I noticed something odd in the console:

> "The two most recent ones are at the waiting-for-officer stage, but it says 'There is no agent assessment yet.' Please find the cause. The agent assessment has to exist before the officer reviews, so the review can be based on it."

The cause: through the API Management gateway's chat completions path, gpt-6.1-sol rejected function tools together with reasoning, with a 400 error. And the old design passed the application on to the officer with only a flag. My first pick for a fix wasn't supported by the model, and the next question arrived in English. I answered with "Explain in Korean." With the measured results in front of me in Korean, I chose gpt-5.6-sol, the one with twice the unit price.

## Response and results

**For the rollout, the live fixes became code.** Both fixes came with tests. A final whole-branch review approved it with no Critical or Important findings and two optional Minor ones; 1,950 tests passed, and the agent asked before pushing. Then I asked for two clean-ups: "Please delete the NSGs that aren't attached to a subnet" (five were deleted; three in use remain) and "Please update the spec with whatever is needed from what we've done so far." The spec now records what changed from the plan.

**For the missing assessment, a gate.** If there's no assessment, the workflow tries once more; if there's still none, the application fails instead of reaching the officer. The smoke test produced a real assessment in 28.5 seconds.

**For the rest, I asked it to check against the assignment.** At 4 p.m., I asked the agent to review the current system against the assignment's original text and grading criteria, and chose two items. The first was a mismatch flag that was showing on nearly every application (24 of 24 in one measurement). In the traces from two days, 161 runs, the data-collection agent reported "complete" only 17 times. Its input listed the consent scopes and the source names separately, so the model treated two sources as out of scope and skipped them. After the input listed consent per source, the nine new applications had no flag; the agent noted that was a small sample. The second was honesty: a business case document and softer wording where the docs claimed too much.

That evening I asked, "What's the next update that could make a difference with little change?" and picked a re-measurement. Then one more request:

> "I think the load test count is too high. It pushes the existing data out so it doesn't show on the screen. Could you cut the load test down to about 10?"

The result put a different kind of price on the table. Compared with the earlier gpt-5.4 baseline, gpt-5.6-sol has a higher unit price, yet AI cost per application went from $0.0939 to $0.0738, because the analyst made 4.3 calls per application instead of 8.4. The agent called ten loads and one eight-case run a trend, not proof.

Looking back, the price kept coming up: first in my own question, then inside the design forms, again before the deletions, and finally in a measurement. Asking it first meant the number arrived as a choice I could make, not as a bill. And per month, per token and per application turned out to be three different prices; only measuring showed which one mattered here.

## Takeaway

Before "add it," ask "how much a month if I leave it on?", and once it runs, measure the cost per job, not just the unit price.
