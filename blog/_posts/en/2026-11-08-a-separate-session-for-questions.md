---
layout: post
title: "Verifying architectural explanations against the implementation"
date: 2026-11-08 09:00:00 +0900
lang: en
ref: day-12
days: "2026-11-08"
permalink: /en/day-12/
summary: "I separated architecture questions from the implementation session so that a request for an explanation wouldn't interrupt deployment."
---
I separated architecture questions from the implementation session so that a request for an explanation wouldn't interrupt deployment. The separation introduced a boundary: an answer from the second session was not an observation of the running system. I checked it before turning it into a change request.

## Objective

The main attempt had a long day. Just after midnight I approved a spec that started collections from a missed installment instead of an early-warning alert; by 9:31 a.m. seven of its tasks were committed with the full suite's 3,046 tests passing, and the agent then deployed and verified it on Azure. At 1 p.m. a question about using the marketplace settlement data turned into a design and a 14-task plan, run with subagents. The evening brought a model change and, at night, a chat panel for the loan officer.

The problem was that I didn't understand my own system well enough anymore. Around noon I started asking in the main session: how often does the detail page refresh, what does the "declared revenue vs. bank revenue mismatch" rule mean, which policy rules exist, what does gpt-5.4-mini do in the data-assembly agent. The answers were good, but they were mixed into a session that was also provisioning and deploying. At 12:35 p.m. the agent wrote that it was opening the operations window (about 5 minutes), and at 12:37 p.m. my next message was a question about a model's role.

## Scope and procedure

At 1:37 p.m. I opened a separate Q&A session, on purpose. The main attempt was finishing the settlement design; I approved it at 1:42 p.m., and the agent went off to write the plan. My first message to the new session was:

> "Please explain the current architecture."

Then I kept going. Between 1:37 and 4:23 p.m. I typed 27 messages into that session. All but one were questions; the exception asked it to save a note. A few of the answers:

- "Paused" Service Bus means the namespace is actually deleted, because Premium costs about $677 a month even when idle; the flows run through a storage queue instead.
- The mock data isn't random: each seller comes from a persona, an archetype and a seed.
- The early-warning agent and the collections agent never call each other. They meet through an alert document stored in Cosmos, because only the orchestrator is allowed to hand work between agents.
- Collections messages need no officer approval; only a rescheduling decision does.

I didn't plan an order. One answer raised the next question. I think that was the point: I could follow my curiosity for three hours while, from 2:20 p.m., the main attempt ran its 14-task plan, an implementer subagent and then a read-only reviewer for each task.

## Observed problems

**The wall leaked.** At 2:39–2:41 p.m. I typed three of the same questions into the main session: the precise roles of the two agents according to the assignment documents, what the officer should do about an early-warning alert, and what "rescheduling review" really means. I had asked the Q&A session exactly these at 2:14, 2:18 and 2:20 p.m. The main agent was mid-task; just before my question it had written "Three failures, all expected fixture values (tenor 12). Updating them." It answered me anyway, between commits. A few minutes later, two messages further on (one of them comes up below), I asked:

> "Is the deployment all done now?"

The answer was no: of the 14 tasks only T1 and T2 were committed, two small side requests I'd added mid-run were waiting for the final deploy task, and nothing was pushed. Why I repeated the questions in the main session, the records don't say.

**An answer had mistakes in it.** At 2:28 p.m. the Q&A session answered "how can I tell which rescheduling option fits this business?" with a tidy table of metrics. It also said you could compare the options just by changing the arguments of the balance-forecast function. At 2:31 p.m. I asked it to save that as a note, and it did, uncommitted. At 2:42 p.m. I pasted the same answer into the main session and asked:

> "Is there an issue with the content below?"

The main agent said the direction and most facts were right, then listed problems. "Just change the arguments" was an overstatement: the function subtracts one installment amount on every due date, so split repayments and refinancing can't be expressed without code changes. "All of these values already exist in the forecast, detectors and ledger" was wrong; two of them live elsewhere. And with a 28-day forecast the options can barely be compared at all. What I did with the saved note after that, the records don't show.

**The Q&A session didn't know what the builder had done that morning.** At 3:00 p.m. I asked why a replay alert said the investigation agent "couldn't be used," so the alert came from the detector alone (`agent_error`). The Q&A agent dug through the gateway logs and found 16 errors with status 429 on the early-warning connection between 10:18 and 10:23 a.m., all `OpenAITokenLimitExceeded`. The numbers weren't news to the main attempt. At 10:27 a.m., during the deploy check, the main agent had reported the same thing (16 of 78 calls throttled at 50K tokens per minute), and by 11:30 a.m. it had raised the limit to 150K. Two sessions, one system, and I was the only one who saw both.

One thing stayed unconfirmed: in the later morning runs, the deploy log says week 7 fell back with `agent_error` with no HTTP error or 429 at all. The error text was never checked; the log only guesses a tool-round limit or a schema mismatch.

## Response and results

**I used each session for what it was good at.** The Q&A session took the wide, cheap questions; when an answer mattered, I asked the main session, which had the day's context, to check it. That's how the forecast overstatement got caught.

**I sent the builder back to its plan with one line.** After its "no, not deployed yet" answer, I wrote:

> "Please proceed as planned."

A minute later it wrote "Now resuming SDD at T3," and the subagent run continued through T13, the final review and the deploy.

**I turned three questions into one request.** In the afternoon the Q&A session had told me three more things. The investigation used gpt-5.4, and the docs didn't state why that model was the default. Twelve rounds was a cap, not a target; it had been six and was doubled because six wasn't enough on the gateway path. And on the direct path the model packs several tool calls into one response. At 7:30 p.m. I sent the main session this:

> "Change the early-warning investigation agent's model to gpt-5.6-luna, and if a 429 comes back, retry up to 5 times (with exponential backoff). Also, when tools need to be called, batch them into one call where possible."

I think those afternoon answers are where this request came from, but the records don't link them directly. By 8:15 p.m. it was deployed: in a live run all five investigations finished on gpt-5.6-luna with zero 429s and zero fallbacks. The deploy log adds that week 7, the one with no 429, now raised a normal alert. There was one catch: because of quota, the gateway's limit had to drop from 150K to 75K tokens per minute. At 8:19 p.m. I wrote, "I've secured more gpt-5.6-luna quota. Check the quota, then raise it further." By 8:34 p.m. the deployment was at 240 (all 250 of the quota in use) and the gateway was back at 150K.

The day ended, a little ironically, with a Q&A feature: a chat on the officer's decision tab for plain-language questions about cash flow, policy results and the agent's opinion. By the design, anything not in the application snapshot gets the answer "not in the data". By 11:57 p.m. its six tasks had passed review, the final review's two findings were fixed and re-reviewed, and the deploy was next.

Looking back, splitting the questions out did two things. The builder spent most of the day on its plan instead of on my curiosity, and I think it let me ask "dumb" questions in bulk without worrying about what I was interrupting. But the split had a cost. The Q&A session could read the code and the logs, but it didn't know what the other session had done that day, so anything it said about what *happened* had to be checked against the session that did it.

## Takeaway

Ask your "help me understand" questions in a separate session, and bring an answer back to the building session only to have it checked or to turn it into one clear request.
