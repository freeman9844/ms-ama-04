---
layout: post
title: "Back to the Assignment Text"
date: 2026-10-28 09:00:00 +0900
lang: en
ref: day-03
days: "2026-10-28"
permalink: /en/day-03/
summary: "While Attempt 1 kept shipping features, I opened Attempt 2 in an empty repository, put the assignment text in a file, and asked basic questions until the answers became a design."
---
In the last entry I learned to ask "why?" before saying "go"; on the 28th the "why" went one step further back, to what the assignment itself was asking for. The day had two tracks. Attempt 1, the cloud MVP from the first two entries, kept running through the day. At 1:56 p.m. a new, empty repository got its "Initial commit", and three minutes later I opened Attempt 2 in it. The records don't say why I started a second attempt that day, so I won't guess.

## What I was trying to do

Attempt 1 picked up where the night had left it. Before a rollout, the agent's preflight found the Fabric F2 capacity paused and SQL blocked (error 42131). It stopped and asked: resuming would restart billing, go ahead? I said yes. By 8:04 a.m. the new screens were live locally and merged into GitHub `main`.

Through the day, I also kept asking about what I had already built. At 2:14 p.m.:

> "Right now we push JSON into Fabric, refine it the medallion-architecture way, and the AI uses it. Is that a generally recommended architecture? Is there a better alternative?"

The agent said yes, Bronze → Silver → Gold is a pattern Microsoft recommends for Fabric, but refining data and processing applications quickly are different jobs, and it suggested a hybrid. I think I was checking the foundation after the house was already up.

Attempt 2 went the other way round: questions first, code later.

## How I handed it to the agent

At 1:59 p.m. I pasted an image of the case study and asked for the English original as a markdown file, then a Korean translation in a second file. Then:

> "Please analyze the @ko-origin.md file."

From there my afternoon was a string of short questions. When an answer was useful, I asked the agent to save it to a numbered analysis file. The questions were basic. Can a mock open-banking feed show card and PG settlement details? (No, make a separate mock.) Reconciling bank data against settlements looks like too much work; any alternative? (Treat the bank account as the only cash-flow basis and never add settlements to it.) Then I pushed back: "Isn't PG settlement an important point in the evaluation, since it's money still to come?" The agent's reply began: "You're right. My earlier suggestion focused only on cash flow that had already happened."

Then the question that started it. At 3:22 p.m.:

> "The capstone says I have to build five agents. Do they all have to be LLM agents?"

Not necessarily, the agent said, but there was no assignment spec in the conversation, so it couldn't confirm what would count. I told it: "Collaboration of five agents is the requirement." I then asked the same kind of question against the English original, and against the assessment criteria, which I had it turn into markdown and translate. By the original, the agent said, the Data Assembly Agent didn't need an LLM; the criteria didn't require every agent to be one; and the number five came from the assignment itself, not the criteria.

## Where it went sideways

Three times that afternoon, the agent's answer drifted away from what I meant.

**It shrank the project.** At 3:31 p.m., after I said five collaborating agents were required, it proposed five agents "fitted to the current MVP": data review, cash-flow analysis, settlement and liquidity forecast, risk review and a report writer. None of them were the case study's five roles. Fifteen minutes later, at 3:46 p.m., it answered a follow-up from the same reduced picture, noting that it wasn't keeping the original's collections and consent roles.

**It answered for the wrong screen.** At 4:52 p.m. I asked how the "client screen" should be organized. It described an officer's work console. I had meant the business owner's portal.

**It explained too much at once.** After an afternoon of repositories, APIs, Durable Functions and orchestrators, at 5:56 p.m. I wrote: "I don't really understand the current architecture. What would be a good way?"

Attempt 1 hit a different problem that evening. At 9:05 p.m. I reported an application that had failed at normalization with a bare `REMOTE_FAILURE`. The agent diagnosed it read-only: collection and the Bronze upload had succeeded, and between 9:02 and 9:03 p.m. a statement inside the Fabric notebook had failed with `System_Cancelled_Session_Statements_Failed`. But the status API and the Spark logs held no actual Python exception, so it asked me to open the run snapshot and send the failing cell's traceback. My answer:

> "I can't check it. Please check it directly in Azure."

## How I got past it

**For the drift, I pointed back at the source.** At 3:49 p.m.:

> "@ko-origin.md is the project I have to do, and it has short descriptions of the five agents too. Analyze it again."

The reply opened: "You're right. I shrank the original project too far into an 'underwriting assistant MVP' and changed the five specified agents into different roles." It then laid out the case study's whole journey: consent, data assembly, credit assessment, human approval, ongoing monitoring, repayment support, and consent renewal, expiry and deletion. The five roles stayed; only how deep each one goes was open.

The screen mix-up took one sentence: "I meant the business-owner portal where the business applies." The agent said it had read the question as being about the officer screen, and redid the answer.

For the overload, the agent took the blame: "I made it complicated by explaining repositories, APIs, Durable Functions and LLMs all at once." It dropped the technology names and drew four boxes: screens, a business server acting as the code orchestrator, five business programs, and storage. It added a one-line picture: the agents are staff, the orchestrator is the progress manager, and the database is the shared work record. I had it add that to the translated case study.

**For the build, I let brainstorming turn the notes into a spec.** At 6:33 p.m. I switched from asking to building:

> "/superpowers:brainstorming Based on what we've discussed, can I build a simple demo locally and test it?"

A series of approval prompts followed: all five roles in scope, real model inference, and, after I asked whether GitHub Copilot SDK would work locally, the SDK for the three judgment roles. It asked for only the provider and model name, explicitly not API keys or tokens. Then a behavior spec with real numbers: 3 million won in cash, a 5 million won settlement on D+3, 6 million won payroll on D+5, a 1 million won repayment on D+10. Push the settlement to D+8 and the D+5 shortfall should raise an alert. I think numbers like those made it easy to say yes.

Two things I said here stuck. First: "When you make plans and specs with the superpowers skill, please write them in Korean," applied to all future projects. Second, when the agent said the spec was written but still waiting for review: "It looks like it's already approved, so shouldn't it move on to the next step?" Eight minutes later the Korean spec and a 10-task plan were committed. I approved Native execution in a separate worktree and picked `gpt-6.1-sol` from the SDK's model list. Ten feature commits landed between 7:08 and 8:50 p.m.

At 8:51 p.m. a fresh reviewer got the whole branch; I think the agent sent it, since the log shows an English brief I never typed. Its first important finding: retrying an old failed job after an officer's approval would push the application from `APPROVED` back to `AWAITING_REVIEW`. At 9:18 p.m. the agent reported six important issues fixed and its tests passing, including a full journey on the real model. After two last bumps (the server wasn't running, then no model was selected), by 9:24 p.m. the demo was waiting for an officer.

**For Attempt 1, the agent went and looked.** A 403 permission error had come at 9:07 p.m., before my request, and another came now, but at 9:49 p.m. it had the cause: an empty SQL cell holding only the default placeholder comment sat ahead of the real code. It raised `PARSE_SYNTAX_ERROR` (SQLSTATE 42601), and that cell wasn't in the Git version. It hadn't changed anything yet. I said "fix and rerun," and by 10:11 p.m. the run finished with an `APPROVE` recommendation waiting for review. I asked one more question at 10:41 p.m., and in the records Attempt 1 stops there.

Looking back, the most useful work I did that day wasn't code. It was asking basic questions against the source documents, one at a time. When the agent drifted, I think "analyze it again from this file" fixed it faster than arguing would have.

## Takeaway

Put the original assignment in a file the agent can read, ask your basic questions against it, and when an answer drifts, say "analyze it again from this file."
