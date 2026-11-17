---
layout: post
title: "Narrowing Down to One"
date: 2026-11-05 09:00:00 +0900
lang: en
ref: day-10
days: "2026-11-05"
permalink: /en/day-10/
summary: "Three side attempts left their last records, one of them only after I asked it to delete its cloud resources and pause Fabric; the main attempt got through a false secret alert and a long AI-gateway build."
---
In the last entry I asked Attempt 7 whether I was changing too much; on November 5 the narrowing happened one level up. By the end of the day, the main attempt was the only one of my parallel attempts still leaving records.

## What I was trying to do

Four sessions had activity that day, and they did not look alike.

Just after midnight I asked the main attempt a short question: "What's left?" The answer was that implementation and deployment were done and only demo preparation remained: a rehearsal, a backup recording, and day-of checks. It also corrected itself. It had earlier said some small deferred issues were written into the deployment history, but they were missing, so it had just added and committed them. It offered that correction without being asked.

The other three were at different points:

- **Attempt 3** was in the middle of a nine-task plan. At 2:23 a.m. it reported Task 7 done and Task 8 in progress, with no Azure deployment and no paid calls. Its records stop there.
- **Attempt 6** finished a whole phase that night: design, plan, nine tasks through implementer and reviewer agents, a final review with three fixes, and a squash merge at 1:58 a.m. with 711 tests passing. It did not push. Its records stop there too.
- **Attempt 7** got a request at 12:11 a.m.: "Please deploy it to Azure."

The records don't say why Attempts 3 and 6 stopped, so I won't guess. They simply end on this day.

## How I handed it to the agent

Attempt 7 shows the step-by-step approval style. The agent found two blockers (authentication tied to my PC's Azure CLI login, and a local-HTTP-only setting) and asked before fixing them. It noticed that the existing Fabric F2 capacity had been paused at 12:37 a.m., and that the SQL database behind it was therefore disabled. Resuming it would restart billing, so it asked. I chose to resume. Then it asked again before generating files, and a third time before creating anything: a small hosting setup (Container Apps, a registry, Log Analytics and a managed identity) at about $39.21 a month, with two compatibility warnings spelled out. I approved all three items in one answer.

The main attempt, meanwhile, got a plain request: "Please sync the local repo." The agent asked where to. When I gave it the public GitHub repository, it came back with a warning about sensitive values before pushing anything. I chose to mask the sensitive values and overwrite the remote.

At 7:57 a.m. I brought a new idea to the main attempt:

> "I think API Management should sit in front when we call the LLM API. What do you think?"

That started a brainstorming round in approval prompts: which approach, which features had to show in the demo (I picked token metrics, a single path, token limits and content safety), which tier, and what to do when content safety fires. On the tier, the agent recommended Developer, since the demo needed no SLA and Developer cost one fourteenth of Standard v2. Then came a four-part design, and I answered each part with "yes."

## Where it went sideways

**A secret alert on a public repository.** At 1:27 a.m. I pasted an alert from a secret-scanning service into the main session: two "Generic High Entropy Secret" incidents, each pointing at two commits in the public repository. My own words above the paste were short: "Please fix the security issue at the bottom," plus a note that it didn't need to go through `.gitignore`. I think I was relying on the agent to judge the alert; I don't see any attempt of mine to judge it in the records.

**A plan that took longer than the code.** The spec for the API gateway was committed at 8:22 a.m. The plan was not shown to me until 11:04 a.m. In between, the session's checkpoint titles alternate between "spec amended" and "spec finalized" twice, while the agent researched and drafted.

**Live tuning kept stopping.** Just before noon, Azure login tokens expired and the APIM deployment couldn't start; I had cancelled the re-login prompt, so the session sat still until I logged in and typed "Logged in. Continue." In the afternoon, three times, I typed a single word: "retry." The records show the cause only for the third: an implementer agent failed because its model request could not resolve DNS, after five retries. Just before that, a different kind of failure had appeared. An ordinary, non-injection test case was blocked by the gateway's content filter.

**Attempt 7 ended with a bill still running.** At 7:55 a.m. I wrote to Attempt 7: "Please delete the deployed Azure environment." By 8:26 a.m. it had deleted the four hosting resources and two permissions it had added. Its report ended with a line in bold: Fabric F2 was still running, so billing continued.

## How I got past it

**For the alert, the agent explained it and then asked.** At 1:56 a.m. it reported that nothing secret had leaked. The flagged values were built-in Azure role IDs for Key Vault, public constants, and the scanner had reacted to the word "Secrets" in the variable names. It renamed the variables, ran 105 infrastructure tests, and then asked how to treat the two old commits that still held the flagged values. I chose to rewrite the history. The agent rewrote all 228 commits, masking sensitive values along the way, scanned the full history again with zero hits, confirmed the final files were unchanged, kept a backup bundle in its session folder, and force-pushed. It also reminded me that closing the remaining alerts was my job.

**For the gateway, I let the plan carry the work.** The plan had Tasks 0–13 and stated the APIM cost (about $51 a month) and a rollback switch. I approved it and chose subagent execution. From then on, most tasks got a fresh implementer agent and then a reviewer agent; the controller ran the two live-Azure tasks (4 and 11) itself. At least three reviews sent work back: on Task 6 the token-limit and rate-limit markers were too broad, on Task 9 an empty result would have counted as "reproduced," and on Task 12 the docs review asked for changes, including a network diagram that had lost its closing fence. Each went through a fix round and a narrower re-review. The live check also turned up something the plan hadn't expected: a blocked request came back as a 424 with the 403 inside it, so the classifier got one more marker.

**For the false block, the fix stayed narrow.** The agent skipped prompt injection checks only for prompts over 10,000 characters, and later confirmed that a genuinely unsafe probe was still blocked (`<job-id>` → `content_blocked`).

**For the crashed agent, it checked before redoing.** After my third "retry," the controller found that the implementer had already committed before it crashed. It ran the 120 infrastructure tests itself and sent the commit to review, telling the reviewer the report might be partial.

At 9:50 p.m. the final whole-branch review said "Ready to merge: With fixes," with no Critical findings and three Important ones. After one fix wave and a re-review, it was merged and pushed by 9:59 p.m. The agent didn't hide the cost: per application, $0.0589 became $0.0939 (+59%), and p95 went from 1.4 to 1.8 minutes. I still asked, "So is it fully applied now?" It checked live Azure before saying yes.

**For Attempt 7, I closed the last open item.** Five minutes after the deletion report I wrote "Please stop Fabric." Three minutes later F2 was paused, with a note that storage would still cost a little and SQL would be unavailable until resumed.

Looking back, nothing on this day decided that the main attempt would be the one. The others simply have no records after it. What I can see is the difference between stopping and closing. Attempt 6's last record is a local merge it didn't push. Attempt 7 had live resources, and after the deletion the agent itself pointed out that F2 was still billing. Closing it took two short requests from me, and I think the second one came straight from the agent's bold warning.

## Takeaway

When you stop working on an attempt, close it: ask the agent to delete what it deployed, then ask what is still billing.
