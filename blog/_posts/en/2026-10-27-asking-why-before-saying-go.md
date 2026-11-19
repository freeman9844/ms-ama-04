---
layout: post
title: "Checking whether a second environment was necessary"
date: 2026-10-27 09:00:00 +0900
lang: en
ref: day-02
days: "2026-10-27"
permalink: /en/day-02/
summary: "My approval to proceed became a quote for a second cloud environment a few hours later."
---
My approval to proceed became a quote for a second cloud environment a few hours later. Before approving the spend, I asked which step the running demo could not perform. That was a different question from whether the agent could provision another environment.

## Objective

By midnight the main thing was done. The agent's 12:06 a.m. summary said the three demo scenarios had been checked on real cloud resources, and that decisions survived a restart. It also said it had held off on recreating a separate environment "until further approval," and reminded me that the Fabric F2 capacity was still Active and billing at about $0.42 an hour.

So the plan for the 27th was not "build the MVP" anymore. It was: actually use the demo, then make it better. Attempt 1's history shows 45 commits that day: AI-written reports, tabs and charts, a reset button for test customers, more realistic mock data, and a design for a review chat. But the part I want to write about happened before any of that, at 7:35 a.m.

## Scope and procedure

At 12:57 a.m. I had typed one word: "approve." The agent replied that it understood this as approval to recreate a separate environment, and that it would check costs and a deployment preview first. I can't tell from the records whether that is what I meant. Given its previous message, it was a fair reading.

At 7:35 a.m. the approval form arrived. A new `repro` environment in Korea Central: a dedicated resource group, a Foundry account and project, gpt-6-sol, a second F2, a workspace, three lakehouses, two notebooks, a SQL database and a pinned-dependency environment. The extra F2 was another $0.42 an hour (about $1.68 for four hours); with both capacities active, $0.84 an hour before model and storage costs. The existing demo would not be touched.

In the acknowledgement field, I typed a question instead: "What's the reason for creating the repro environment?" A minute later I asked again in the chat, more directly:

> "Why are we creating a repro environment? The demo environment looks like it deployed fine."

The answer was short and, I think, honest:

- Right. The demo was already deployed and had passed the three scenarios and the restart check on real cloud.
- `repro` was extra verification: could the deployment code alone rebuild a new environment from scratch?
- It was not fixing anything in the demo, and it was not needed to use or present the MVP.
- "I'll correct having presented it as if it were a required next step."
- Nothing had been created yet.

I replied: "No need for extra verification. Please carry on from there." At 7:46 a.m. a commit landed titled "docs: finalize existing demo scope without extra recreation." A whole second environment, with its own capacity bill, didn't get built.

To be fair, reproducibility is worth checking. But it had presented an optional check as the next step, and my one-word "approve" a few hours earlier had already been read as a yes to it. The question cost me a minute.

The same thing happened later, in a different shape. At 9:20 a.m., after it had made the recommendation report "richer," I asked: "Is the recommendation review based on gen AI?" Again it didn't oversell. It said: partly. The model picks verified explanations, but the metrics and policy checks are plain code, and the "richer" report was assembled by the server from fixed phrases and calculated values. A Gen AI report that interprets each borrower's data "has not been implemented yet." So that became my next request.

What followed was a pattern I saw all day. The agent asked one decision at a time, through forms: generate once and store, or on every view (it recommended storing, so reloads don't cost money); then a design document, a six-step plan, and "Native" execution (it implements, one fresh reviewer checks the whole branch) versus "Subagent-driven" (an implementer and reviewer per step, at higher cost). The reviewer found nothing critical and, per the agent's notes, two integration problems, which it reproduced and fixed. Only then did it ask to touch the live demo, with a ceiling: at most three model calls of up to 6,000 completion tokens and 90 seconds each.

## Observed problems

**A paused capacity looked like a broken demo.** At 7:57 a.m. the web app couldn't reach SQL: error 42131, because the F2 was `Paused`. The agent said plainly, "This is not a login problem," and asked me to approve resuming the billing. I did.

**Then a login problem that really was one.** In the afternoon I opened the customer screen and got `{"error_code":"AUTHENTICATION_DENIED","correlation_id":"<correlation-id>"}`. The agent found the Azure login flow had been cancelled and offered a device-code login. I wrote back, "I logged in from the terminal. Please try again." Same error; now Microsoft Graph wanted re-authentication, and it gave me an `az login --scope` command for Graph. "I logged in again." At 2:07 p.m. it reported that the login itself had succeeded, but the Graph request was still rejected by an existing authentication policy. Then: "I'll stop the repeated logins." Its next idea was a full Azure CLI logout to rebuild the token cache.

**The first real run on the new mock data failed.** After the realistic data was published, the first underwriting workflow stopped at the step that saves the recommendation, after three attempts that produced invalid output. My records show an investigation and, by 6:03 p.m., a message that the rollout was done with real Spark and Gen AI processing for two customers. They don't show what exactly was changed in between.

**And the screens got slow.** I typed: "The screens are slow to load." Twenty-five minutes later the agent reported the customer screen going from 34 seconds to 2.3, the admin list from 29 to 2.9, the collection page from 16 to 4, and the review page from 11 to 2.3. It had cut duplicate database reads and unneeded report loading.

## Response and results

**For the login, I changed the question.** Instead of another round of logging in, I typed into the approval form: "Please turn off the login authentication in the application." The agent didn't just switch it off. It came back with a narrower version: an option that disables only the app's Graph presenter check, only for the local demo, with the default left on. Tokens for Azure SQL, Fabric and the model, permission checks, consent checks and CSRF protection would all stay. "So Azure's own authentication doesn't go away." I approved, and at 2:15 p.m. the customer screen opened.

**For the mock data, I asked the agent to brainstorm instead of build.** At 2:54 p.m. I started with a slash command:

> "/superpowers:brainstorming Please make the bank transactions, settlements and tax data that we collect with consent into more realistic mock data. For example, bank transactions are daily data covering six months, and settlements should be the same. And assume tax only has quarterly data."

The agent's first move was a question, not code: what does "six months" mean? It recommended six complete months (April 1 to September 30, 183 days) so daily data would line up with tax quarters. I chose to keep the existing 180-day window (2026-04-29 to 2026-10-25). It accepted that and adjusted: the unfinished fourth quarter would be marked as "not yet due," not missing. Then one question each for granularity, scenarios and tax rules, three implementation options (it recommended a fixed-seed generator writing versioned JSON), and a design in three parts, approved one at a time. Then a design document, an eight-task plan, Native execution, and a review that found no issues. When I typed "approve" at 4:54 p.m., it didn't assume what that covered. It asked one more question to confirm the scope, including the paid Spark and model runs.

The evening request (separate customer and admin modules, six showcase cases, and a review chat with decision buttons) went the same way: a handful of one-at-a-time questions, a design, a written spec, a nine-step plan.

Looking back, the most useful thing I typed all day was not a request. It was a question about a request the agent had made. I think I had been treating "approve" as the normal answer to every form. The 7:35 a.m. form showed me that an approval form can also be the place to ask "why?", and that this time the agent answered it straight.

## Takeaway

When the agent proposes extra work, ask "why do we need this?" before you say "go." Sometimes the honest answer is that you don't.
