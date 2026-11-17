---
layout: post
title: "\"Am I Changing Too Much?\""
date: 2026-11-04 09:00:00 +0900
lang: en
ref: day-09
days: "2026-11-04"
permalink: /en/day-09/
summary: "After a morning of saying \"go\", I asked one attempt how big the change had become. The honest answer was 52 files and about 22,000 lines, so we cut back to a minimal flow."
---
In the last entry I had five folders running at once. This one is about the moment one of those folders made me stop and ask how big things had become.

## What I was trying to do

In Attempt 7, the plan was to grow the existing lending MVP into a consent-first, five-agent design, where a Consent Lifecycle step is always called before anything else. The agent was working in an isolated worktree, so the original MVP stayed as it was.

Just after midnight I asked a checking question: "Is the current work an improvement that includes the existing MVP, or a new build that ignores it?" The answer was clear: "a phased improvement built on the existing MVP." The screens, chat, data and deployment would be kept. The new parts were consent, withdrawal and deletion, plus a durable base for running agents. I replied, "Yes, go."

Then came the morning. Between 8:01 a.m. and 12:53 p.m. I sent "go" five times. Each time a short report came back: a commit and what had been added, most of them with a line saying no real Azure changes had been made. There were two approval prompts along the way. First, whether to install x86 emulation in WSL, which changes my system (I approved). Then, when the image build ran into an emulator conflict and Podman limits, whether to allow more system changes. I chose to continue with code only.

The reports covered Fabric provisioning, deployment and publishing CLIs, SQL bootstrap, model budgets, and an image check. Each sounded reasonable on its own. I think that is exactly why I didn't see the total.

## How I handed it to the agent

At 1:30 p.m. I finally asked:

> "What's the current status? Are we changing too much, by any chance?"

The answer opened with "Yes, that's a fair concern." It said the work had shifted toward deployment and operations safeguards, more than connecting the five agents' actual business flow. Then it gave a table and a number:

- The existing MVP was untouched; the new work sat on a separate branch, not merged.
- Built: the Consent Lifecycle, consent-first processing, auth, storage, retries, withdrawal and deletion, and model call budgets.
- Not built: the other four agents of the new design, integration with the existing MVP, and any real deployment.
- Size: **52 files and about 22,000 added lines, counting committed work only.** About 8,300 lines of application code, 8,400 of tests, and 5,300 of dependency lock files. In its words, quite large to call a small MVP improvement.

It named the cause too: the scope grew because deployment automation and verification were built deep first. Its recommendation was to pause further infrastructure work. First connect a minimal flow, "consent check → existing MVP analysis → show the underwriting result", then add the other agents one at a time.

My reply a minute later was that recommendation, repeated almost word for word. The agent turned it into a scoped approval prompt. Changes would be limited to splitting out the consent step and its tests, with no new database, resources or deployment automation. I didn't approve right away. I asked back: "Does this design include orchestration, from an agent point of view?" I think I was worried that cutting back meant losing the "agentic" part. It said yes. The existing workflow service would be the orchestrator, with plain Python code controlling order, conditions and resume, while the analysis agents still use the LLM and tools. Consent decisions and run order would not be left to the LLM. Distributed execution was deferred.

"Yes, please go ahead." Twenty-three minutes later it was committed: four business-code files changed, no new infrastructure, database or screens.

## Where it went sideways

"So can I test it now?" Not quite: the server had to be restarted with the new code first. The 1,334 offline tests had passed, and a live test with Fabric and Foundry needed separate approval because it could cost money. I asked it to prepare and guide me. In the approval prompt I chose a live test on the existing resources and said I would pause the capacity myself. The agent reminded me that this meant the F2 would keep billing until I told it the test was over.

My first test case, SME-001, showed a processing error. I asked why. The agent found a SQL heartbeat failure (`HEARTBEAT_FAILED`). Consent checks, data lookups and the LLM call had all worked; it stopped before the Spark analysis. The detailed SQL error code wasn't in the logs, so it couldn't yet say whether it was a connection problem or a lock.

I think that's when I wobbled. At 2:40 p.m. I asked:

> "Can I throw away what's being developed and roll back to the previous environment?"

The answer was yes, with steps, and with a catch. The same heartbeat code existed in the old MVP too, so a rollback couldn't guarantee the error would go away.

Three minutes later I noticed something else: "The UI looks different too, doesn't it?" It did. The sidebar, dashboard and style improvements had been *uncommitted* changes in the original folder. The new worktree was built from commits, so it never had them. The agent was direct about it: telling me earlier that it would use the existing UI as it was had been inaccurate, and it apologized.

## How I got past it

I didn't roll back. Instead I asked whether the old UI improvements could be used on the current branch. The agent drew a tight scope: move 13 UI files (styles, templates, icons, page scripts) and their tests. The original folder would stay as it was, the consent and analysis logic would not change, and the heartbeat error would *not* be fixed in this step. I approved, and it was committed at 3:22 p.m.

Then, at 4:01 p.m.: "Analyze the processing error and catch the bug." Before reproducing it, the agent asked to create a separate synthetic test run and possibly run SME-001 through the real LLM and Spark, keeping old records. I approved. It injected a real SQL connection error once, confirmed the fix recovered with a new connection, and a fresh SME-001 finished its analysis. The final fix retries transient errors up to two times. The original root cause stayed unconfirmed, again because the logs didn't have it.

By evening I asked how this would look on Azure. The answer: keep the existing Fabric and Foundry and move only the web process to Container Apps. There was "no need to re-expand" the paused five-agent infrastructure.

The same day, the main attempt went in the opposite direction, and I was the one adding scope. In the afternoon I asked for a credit officer to be able to approve a policy-declined application after a confirmation popup, and it was deployed within the hour. Between 5:32 and 5:56 p.m. I sent request after request:

- a customer application page
- pausing the post-loan replay
- more charts for the officer
- dropping conditional approval
- richer ops metrics
- a six-month maximum loan
- realistic seller names and data
- a tabbed detail page
- realistic post-loan data

Before any code, the agent said the requests together would change policy rules and message contracts, so it called them "architectural" and grouped them into one phase, P9, with a spec first. For the six-month limit it asked how far to apply it, warning that monthly payments would roughly double. I kept 12 months. Then came a plan of 15 tasks and one whole-branch review over 117 files. Three Important issues were fixed, and by 11:56 p.m. it was merged and deployed with 1,350 tests passing.

That change was not small either. The difference, I think, was visibility. In the main attempt every addition came back to me as a question and landed in a named phase. In Attempt 7 the scope grew inside a stream of "go" replies, and I saw the total only when I asked.

Other attempts showed the same thing that day. In Attempt 3 I asked, "Is it still in progress?" A test had been stuck on an endless wait, and the agent apologized for noticing late. Less than an hour later I asked again. It admitted it wasn't actually working: its earlier "in progress" had meant the overall plan was unfinished, and it apologized for the impression. In Attempt 6, "Is the whole implementation done?" got a plain "no" and a table of the stages left.

If you're about to try this: "go" is fine for small steps, but every so often, stop and ask for the size of the change in numbers.

## Takeaway

Regularly ask "how big has this change become?" The agent will answer honestly, with numbers, but only if you ask.
