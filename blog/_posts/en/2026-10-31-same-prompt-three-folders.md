---
layout: post
title: "Finding the unit of model work in the specification"
date: 2026-10-31 09:00:00 +0900
lang: en
ref: day-05
days: "2026-10-31"
permalink: /en/day-05/
summary: "Three workspaces received the same starting request and produced different design questions."
---
Three workspaces received the same starting request and produced different design questions. To evaluate the specification I asked how many model calls one application would generate. Reasoning about that unit of work exposed a design choice while it was still cheap to change.

## Objective

The goal hadn't changed: understand the assignment and get a working demo on real Azure. At 10:03 a.m. a new folder got its first commit and a brainstorming request. I'll call it Attempt 3. Attempt 4 started the same way at 10:18–10:19 a.m., and a third folder at 1:06–1:07 p.m. From here on, this blog calls that third folder the main attempt.

The records don't say why I opened three folders, or why the third one became the project, so I won't guess. But the same words went into all three, which makes them easy to compare.

By midnight the three folders held 86 commits between them: 26 in Attempt 3, 4 in Attempt 4 and 56 in the main attempt. The log also shows 37 tool failures across the day.

## Scope and procedure

The request, translated:

> "/superpowers:brainstorming [assignment files] This is a capstone project assignment. I need to build a demo of these requirements on real Azure. Please understand the assignment and summarize the core requirements."

Attempt 4's copy asked for a "detailed" summary; otherwise the text was the same. The replies were not.

**Attempt 3** handed the reading of the assignment files to a research subagent and saved the result as a report. I attached a separate demo design document and asked it to fold that in. Then I told it: "This is for building a demo, so I'll be adding some assumptions. You don't need to worry too much about the legal side." After a long run of approval forms, it stopped at 11:47 a.m. with my answer "document review only for now."

**Attempt 4** asked four multiple-choice questions in about a minute: how deep the demo should go, which country to assume, which agent stack, and how much time I had. Then it walked me through a four-part design. My replies were "yes," six times. The spec was committed at 10:29 a.m., and a 19-task implementation plan at 11:17 a.m. At 11:37 a.m. I asked one real question: the plan pinned everything to Korea Central with an "Allowed locations" policy, so would a Global Standard model deployment be allowed? The agent said that policy alone wouldn't block it, called it a residency violation, and added a custom policy that denies non-regional model SKUs.

**The main attempt** asked six questions, including what the repository was for, how polished the credit officer's screen should be, and what cost and security level to aim for. After I picked an orchestration approach, it presented the design in five sections, asking for approval after each. I added feedback twice. On section 1: "Please strengthen the LLM's role in Underwriting and Early-Warning." On section 3: "With only regional deployments in Korea, models are hard to use, so we need to use Global Standard."

So the same day holds two opposite answers on the same topic. Attempt 4's plan blocked Global Standard at 11:43 a.m.; at 1:33 p.m. I told the main attempt it needed Global Standard. The records don't show how I got from one to the other.

## Observed problems

**Sending the request a second time.** At 1:03 p.m. I pasted the brainstorming request into Attempt 4 again. The agent didn't start over: "This assignment is already done through a design spec and an implementation plan. Rather than brainstorming again from scratch, I'll just re-summarize the core requirements you asked for, based on the original text." Four minutes later the same words went into the new folder. Attempt 4's records for the day stop after that reply. The agent reads the folder's history, so a "fresh start" in an old folder isn't fresh.

**A spec I didn't approve.** At 1:58 p.m. the main attempt committed a Korean spec and asked for approval. I didn't approve it. When it asked what to change, I quoted section 3.2 back to it. It said free-text bank transaction descriptions would be sorted into categories like sales, tax and payroll, with "bulk processing handled by a mini-class model." My question:

> "Then doesn't that mean there has to be one LLM call for every bank transaction?"

**The first real Azure bill.** The infrastructure plan ended with task T10, the real deployment. The agent flagged that Service Bus Premium alone was about $677 a month, and I chose "docs only." Then I asked, "Are there next steps after the real deployment?" A minute later I wrote, "Go ahead with the deployment." The records don't say what changed my mind in that minute. The deploy then hit three problems: a reserved word in a model deployment name, a private endpoint created too early, and a smoke check using the old Assistants API, which failed on the new Foundry project. A tenant policy also forced public access off for Key Vault, Cosmos DB and Storage.

**Attempt 3's reviews kept finding things.** In the evening Attempt 3 ran nine tasks, each sent to a fresh implementer subagent and then to an independent reviewer. Seven of the nine came back "NEEDS_FIXES" on the first review. One example: a cash buffer of exactly 30 days was judged *below* the 30-day threshold, because a rounded decimal division left it at 29.999….

## Response and results

**For the spec, one question was enough.** The agent proposed a tiered pipeline: rules first, then removing duplicates, then a batched LLM call, then a cache. I added: "For this kind of thing, a model like Microsoft-Decision-1 seems like a good option (cheap and fast)." The updated spec reads rules → deduplication → Microsoft-Decision-1 → mini LLM fallback → cache. It also gained metrics such as call count and cache hit rate, and a risk note that Korean classification quality was not yet verified. I approved at 2:07 p.m. I think that one question was among the most useful things I typed that day.

**For research, the agent fanned out.** After P1, the local domain core, was merged, I said "please proceed." The agent split its infrastructure research into five parallel threads (Foundry private networking, model availability in Korea Central, Container Apps, Durable Functions, and Content Safety plus pricing), then added a sixth on Data Zones. Meanwhile it asked me to make two decisions. The research turned up an Asia-Pacific Data Zone that had reached general availability on July 9, 2026, which contradicted a line in our own spec. The agent proposed switching the default to it; I answered "global." Later it found that my chosen networking option would cut the orchestrator off from the internal services, and proposed two Container Apps environments. I agreed.

**For cost, approvals stayed separate.** I approved the infrastructure plan with "stop before T10," picked "docs only" when T10 came up, and only then said to deploy. The agent fixed the three deploy problems. It wrote the tenant policy into the README instead of working around it. Provisioning took 7.6 minutes and all 21 in-network smoke checks passed. Then it asked me what to do about Service Bus Premium, now running at about $22 a day. I chose to pause it.

**For Attempt 3, the loop did its job.** Each fix went to a reviewer who checked only that fix, with up to five rounds allowed. Three times an implementer stopped with "NEEDS_CONTEXT" rather than guess. One of those became a question for me, about what happens when a customer consents again after consent expires. I picked the recommended option. At 11:56 p.m. the last task was on its second fix round. The main attempt worked differently: the agent recommended "native" execution for P1 (doing the work itself, then one whole-branch review). The plan called for 95 tests. A "with fixes" verdict and two fixes later, 100 passed, and it merged by 2:56 p.m.

The folders weren't fully separate, either. At 4:33 p.m. I asked Attempt 3 to update its spec using the main attempt's spec, adding, "I especially like the agent design part in section 3." The file wasn't in Attempt 3's folder, so the agent asked me for the full path instead of guessing.

By 9:20 p.m. the main attempt's real Azure smoke test passed 24 of 24, and one application went from submission to "approved" in 52 seconds.

Looking back, the same prompt didn't produce the same project three times. The questions differed, my answers differed, and the folder's history mattered. I think what helped the main attempt most wasn't the prompt. It was reading what the agent wrote and asking a plain question about it.

## Takeaway

Read the spec before you approve it. One plain question, like "how many model calls does this make?", is cheaper than a redesign later.
