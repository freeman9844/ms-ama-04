---
layout: post
title: "Cutting Back What the Agent Added"
date: 2026-11-02 09:00:00 +0900
lang: en
ref: day-07
days: "2026-11-02"
permalink: /en/day-07/
summary: "In 26 minutes I approved a signed-approval deployment design layer by layer; when the five-task plan arrived I asked for changes, and the cut-back plan had three tasks."
---
In the last entry I switched off a security control that was blocking the demo; this day the same move came back in a different shape: taking out things the agent had built up, sometimes less than an hour after I had approved them. Four folders had sessions on the 2nd: Attempt 3, Attempt 5, Attempt 6 (which started that afternoon) and the main attempt. The log counts 136 commits across them. Most of this entry is about one hour in Attempt 3.

## What I was trying to do

Attempt 3 was building a deployment pipeline for the lending demo, and that morning it reached the question of who may say "deploy now." I first picked an existing organisation controller as the approval source. The agent asked for its API documentation and added: "If no controller is available yet, tell me 'none'. I won't implement against an API that doesn't exist." I answered "none," and we agreed to build packaging and CI first and defer approval integration. By 12:34 p.m. that part had passed an independent review, and the agent stopped at the line I had drawn.

So the open item in the early afternoon was the deferred deployment approval.

## How I handed it to the agent

At 1:24 p.m. I typed one word:

> "Go."

The agent reminded me that I had deferred this, and offered the remaining option: design and build an external signed-approval scheme locally, with no real services or paid deployment. I approved. What followed was a chain of small, reasonable questions:

1. **1:42 p.m., signing.** An offline signing tool in a separate admin environment, or a central signing service. It recommended the offline tool for a demo. I took it.
2. **1:51 p.m., the shared execution record.** A dedicated coordinator API in front of its own Blob storage, or the same API on Cosmos DB. It recommended Blob. I took it.
3. **1:53 p.m., the detailed design.** ES256-signed approvals carrying the target, plan/source/release hashes, allowed actions, cost and time caps and expiry. Only the coordinator writes to Blob; lost responses stay "unknown." The next stage opens only on fresh evidence from a separately trusted observer. I said write it up.
4. **1:56 p.m., the design document.** Written in Korean and committed. I approved it and asked for an implementation plan.

That made five approvals in 26 minutes. Every question said clearly that nothing real (no keys, no services, no cloud calls) would be created yet. I think that care is part of why it was so easy to keep saying yes: each step was safe on its own. I don't think I was adding up the steps.

## Where it went sideways

At 2:03 p.m. the plan arrived: five tasks, from a signing tool through a Blob execution record and coordinator API to wiring and handover, each with failing tests and an independent review.

That was where I stopped and answered "the plan needs changes." The records don't show what I was thinking. When the agent asked what to change, my request was one line:

> "The integrity verification seems unnecessary."

The agent didn't just delete things. It pointed out that my sentence covered two different checks:

- ① **artifact integrity checks**, such as comparing ZIP, source and dependency hashes;
- ② **approval forgery and tampering checks**, which compare the approval's signature with the target and release hashes.

Dropping ① would simplify packaging. Dropping ② meant replacing the whole signed-approval structure I had chosen about 35 minutes earlier. I chose to drop both.

The same pattern showed up in other folders that day.

**Attempt 5**, which was building the demo UI, got three cuts from me:

- At 4:10 p.m., about the test-run screen: "all the testing functions here seem unnecessary."
- At 6:19 p.m.: "The 'emergency consent withdrawal' button for the no-network demo doesn't need to be on every screen. Please remove it."
- At 6:22 p.m.: auto-blocking access and deleting the managed copy five minutes after consent "looks like an unnecessary process."

The third one is the interesting one, because the agent slowed down: "This isn't a wording change; it changes the consent, collection and deletion policy." The assignment's case study asks for automatic deletion on expiry, so the design document would have to say this demo doesn't implement it. Not everything I cut was the agent's invention; some came from the assignment, and the agent made that visible.

**The main attempt** had a quieter case. At 1:19 p.m. the agent asked me to score three underwriting explanations from its evaluation run, 1–5 on three criteria, as an underwriter would. I answered 5/5/5 for all three. Twenty-three minutes later, after a whole-branch review, it came back: "I had put 5/5/5 in as the default earlier, which may have influenced your judgment. Please look at the signals below and re-check the scores. You can leave them as they are." Two of the three explanations had failed the automatic evidence check. I can't tell from the records whether I read them closely the first time, and I don't see a new score afterwards; my next answer was "merge." This time the agent had added a default value, not a feature.

## How I got past it

**In Attempt 3, the agent did the cutting properly once I asked.** At 2:20 p.m. it recommended withdrawing the signature-and-hash design and switching to "human check plus a single deployment runner":

- no offline signing tool, no coordinator or Blob admin layer;
- before running, show the target, expected cost and task list, and a human confirms;
- deploy only from one fixed private runner, and stop if the run record is missing or a result is unclear (no automatic resume across runners);
- keep secret-file exclusion, ZIP path safety, version compatibility, the private network and the consent/deletion controls.

It added that the design would state plainly that this approach does not protect approvals or artifacts against tampering. I said yes. At 2:24 p.m. the revised spec said "the earlier, complex plan has been discarded," and at 2:32 p.m. the plan had three tasks instead of five: 29 minutes from "needs changes" to an approved plan.

The smaller plan still got real scrutiny. Reviews found a confirmation window that could exceed its 10 minutes across a daylight-saving change, and a token retry that could send a request after confirmation expired. The final review listed 0 Critical, 3 Important and 1 Minor, all fixed before I chose "merge" at 5:33 p.m. Simpler didn't mean unchecked.

**In Attempt 6, I asked for efficiency up front.** It started at 4:38 p.m. with Attempt 3's design spec and this request:

> "Referring to [Attempt 3's design spec], please guide me on the most efficient way to build the MVP."

The answer opened with: "This spec is very broad (five agents, eight cases, about 35 control tests, the full private-network infrastructure)." It proposed the deterministic core first, then one case end to end, then wider cases and infrastructure. When I asked about efficiency directly, the agent did talk about size. A minute later, though, I asked it to "expand each stage in more detail." The resulting plan had 16 tasks; I chose subagent-driven execution, and by 6:25 p.m. all 127 tests passed and phase 1 was merged. The records don't say why I opened a new folder for this.

**In the main attempt, I changed how I delegated.** For the evaluation phase that morning I had taken the agent's recommendation of Native execution: it works through the plan itself, with one full review at the end. The ops-metrics plan at 5:12 p.m. was 14 tasks and 4,605 lines, and this time I chose "subagent": each task went to an implementer subagent, then to a separate reviewer. The first fix round came at 5:49 p.m., for a tracing span that could make every metered activity fail. By 6:17 p.m., tasks 1–9 were done, two of them after one fix round.

Before the merge at 9:07 p.m., the agent listed eight "decisions I made on your behalf," each with its cost if wrong; one was running subagents on the harness default model, which "may increase token cost." I picked "merge," and 878 tests passed. Looking back, that list is what I had been missing at 1:56 p.m.: every addition in one place, while I could still say no.

What I take from this day is that the agent will help me cut, and I think it sometimes does it more carefully than I would alone. It split my vague "unnecessary" into two concrete checks, and it wrote down what we were giving up. But in that hour of approvals, the cut didn't start until I started it.

## Takeaway

Don't wait for the agent to say a plan is too big: before approving, ask what the demo actually needs, and have the agent write down what you removed.
