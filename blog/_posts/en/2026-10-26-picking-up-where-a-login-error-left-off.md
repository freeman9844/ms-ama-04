---
layout: post
title: "Picking Up Where a Login Error Left Off"
date: 2026-10-26 09:00:00 +0900
lang: en
ref: day-01
days: "2026-10-26"
permalink: /en/day-01/
summary: "I came back ready to fight a login error; the agent checked the present state first, and the real blockers turned out to be model capacity and a Spark dependency."
---
This diary follows an internal architect program's capstone: a demo called AMALend, an agentic cash-flow lending app on Azure. I built it with an AI coding agent, GitHub Copilot CLI, and it was my first time working this way. The Azure parts are the backdrop. What I want to write down is how I learned to work with the agent, including the parts that went wrong.

## What I was trying to do

Day one is not really day one. The repository for what I'll call Attempt 1 already had its first commits from the day before: the case study, a Korean translation, and a document explaining the capstone. The commit history for the 26th also shows a full working day before any conversation I have on record: a requirements analysis and a database design in the morning, a short "two-agent MVP" spec and plan in mid-afternoon, then six feature commits by about 6 p.m. Fixed test data and a mock API, workflow storage in Fabric SQL, Foundry and Fabric clients, notebook calculations, two guarded agents, and a presenter wizard.

Then progress stopped. What I remembered was an Azure login error. By the plan, tasks 1–6 were done; tasks 7–9 (infrastructure, Fabric setup, and a real cloud demo) were still left.

So at 6:45 p.m. the goal was simple: get unstuck and finish the MVP on real Azure.

## How I handed it to the agent

My opening move was lazy, and it worked better than I expected. I attached a file from the previous session, a log of syncing to GitHub, and typed nothing else. The agent didn't guess. It asked what I wanted to do with the file and offered choices; I picked "continue the previous work." It then switched this machine to `main` and confirmed the local branch matched the remote. I think the earlier work had happened on a different machine, because syncing this one was the first thing it had to do.

Then I asked the real question:

> "I was in the middle of building the MVP, but an Azure login issue stopped me. Can you check where that stands?"

I didn't paste the error; I don't think I had the error text anymore. The answer came back as a table, and it changed my picture of the day:

- Azure CLI login: fine. Tokens for Graph, Foundry, Fabric, OneLake and SQL: all issued. Real API calls: working.
- The MVP was stopped not at login but before the infrastructure preflight.
- None of the app's environment variables, and no `.env` file, existed yet.
- The saved notes said a model-list query had hung for 120 seconds and was cancelled. The original `az login` error text had never been saved, so the cause "can't be confirmed."

I liked that last line. It would have been easy for the agent to make up a story about the login failure. Instead it told me what the records did and didn't contain. (Ten minutes later, after more checks, it said the earlier stop had been a failure to reach the login server. Either way, it was no longer the blocker.)

My reply was one word: "go."

For the rest of the evening, my side of the conversation was mostly that word. Six of the messages I typed that day were "go," "keep going," or "approve." The real decisions happened somewhere else: in the agent's approval prompts. Before anything that cost money or changed my machine, it stopped and asked a multiple-choice question, and I mostly answered by picking an option.

## Where it went sideways

**The model I wanted had no room.** I wrote: "Use gpt-6.1-sol for the model." The agent checked the quota in Korea Central and came back with a problem: for that model on GlobalStandard, usage was already over the limit and new capacity was zero. It offered two paths: a different deployment type, or stay on GlobalStandard and look into a quota increase. I chose to stay. It wrote the choice into the design, noted that my existing allocation lived in a different region, and said plainly that it had *not* assumed that allocation could be moved. Then it pointed me to the quota increase form. Correct, but I was blocked.

**The first real cloud run hit something nobody had planned for.** After I approved the paid deployment, the Foundry project, the model, a Fabric F2 capacity, a workspace and three lakehouses were created. One notebook was created too, but the agent's local state record broke off because the async response pointed to a regional address. Later, the end-to-end smoke test reached Spark and stopped there: Fabric's runtime preloads a different Pydantic version than the one the app pinned, and the app's own safety check refused to run.

**Plenty of small stumbles, too.** The log for that day shows fourteen tool failures: line ranges out of bounds, patches that didn't match the file, paths that didn't exist, a 504 from a GitHub tool, a documentation page that returned 404. The agent got past almost all of them by itself. I don't think I noticed most of them at the time.

## How I got past it

**For capacity, I asked a different question.** Instead of waiting on a quota request, I asked: "Other GPT-6 models?" The agent queried the region and returned a short table. Three other GPT-6 models had 200,000–250,000 TPM free; the one I wanted had zero. It suggested gpt-6-sol and flagged that price and tool-calling compatibility still needed checking. I said "Make it gpt-6-sol," and two minutes later the design and plan were updated. Nothing had been deployed yet.

**For cost, I let it stage the approvals.** What I did not expect was how carefully the agent separated "write the plan" from "spend the money." In order:

1. A resource list: a dedicated resource group, a Foundry account and project, a gpt-6-sol deployment at 50,000 TPM, a Fabric F2 capacity, and the items to bootstrap inside it. It gave rough prices (about $0.42 an hour for the F2, plus per-million-token model prices) and said this approval was *not* approval to deploy.
2. A detailed plan, with the F2 cost for a four-hour demo (about $1.68) and for leaving it on all month (about $306.60). I approved generating Bicep and validating it locally, nothing more.
3. Bicep compiled, `what-if` showed six new resources and no changes to anything existing, and a Checkov scan showed two passes and one warning: the public development endpoint I had already accepted. It noted that it hadn't hidden the warning.
4. Installing ODBC Driver 18 on my Windows machine got its own question, because it changes a shared system.
5. Only then, the paid deployment. I approved it with one word: "approve."

**For the lost notebook, it asked before recovering.** Rather than create it again, the agent proposed linking the existing notebook's ID (`<notebook-id>`) into its recovery record, after checking the ownership marker and that the code matched. It reminded me the F2 was now active and billing. I approved.

**For Spark, it brought a narrow fix with a price tag.** One dedicated Fabric Environment inside the existing workspace, pinned dependencies published to it, attached to both notebooks, with the notebook code left as it was. I approved, and at 10:39 p.m. the normal path passed end to end: Foundry → OneLake → normalize and underwrite notebooks → SQL.

The day ended with a review. I think the agent handed the whole branch to a fresh reviewer agent: the log shows an English brief I never typed, asking for a read-only review. The reviewer found nothing critical and two important issues, both about restarts. A restart could strand a workflow whose lease hadn't expired yet, and it could take over a submission whose outcome was unknown. The last commit of the day, at 11:41 p.m., is titled "fix: recover stopped workflows without repeating unknown submissions." I think that was the answer to those findings.

Looking back, I came in ready to fight a login error, and that fight never happened. What saved time was letting the agent check the present state before I tried to describe the past from memory. What kept me comfortable was that every decision with a cost came back to me as a clear question. If you're about to try this for the first time: your first message doesn't need to be perfect. It needs to ask for the current state.

## Takeaway

Start with "check where things stand now" instead of retelling the past, and keep every decision that costs money for yourself.
