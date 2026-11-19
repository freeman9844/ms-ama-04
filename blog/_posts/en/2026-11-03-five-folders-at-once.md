---
layout: post
title: "Distinguishing parallel work from verified throughput"
date: 2026-11-03 09:00:00 +0900
lang: en
ref: day-08
days: "2026-11-03"
permalink: /en/day-08/
summary: "Agents were active in five workspaces, but their approvals and results still converged on one reviewer."
---
Agents were active in five workspaces, but their approvals and results still converged on one reviewer. Commit volume could not establish whether the Service Bus path delivered a message; that took a rehearsal. The distinction that mattered was work started versus work verified.

## Objective

Each folder had its own goal.

- **The main attempt** had finished its documentation tasks and was ready for a live rehearsal on Azure: deploy, run a smoke test, then click through the two-minute demo script together.
- **Attempt 6** had run its next plan, 17 tasks, overnight through subagents and was moving on to a private-network deployment. Its final review at 1:42 a.m. had found five issues (three Critical) that every test missed, because the tests only used fakes.
- **Attempt 3** was deep in a single task about deleting cloud resources safely.
- **Attempt 5** was a local demo, and I was asking it how it would look on Azure.
- **Attempt 7** began at 1:41 p.m. by syncing a repository that held an earlier two-agent MVP. It used the same gpt-6-sol and Fabric F2 setup as Attempt 1, so I think it was that code.

## Scope and procedure

Mostly with one word. Between 7:05 and 7:06 a.m. I answered four folders: "subagent" as the execution method in the main attempt, "go" in Attempt 3, "go" in Attempt 5, and "merge" for Attempt 6's finished branch. At 9:43 a.m. it happened again: "go" in Attempt 6, "current status?" in Attempt 5, "start now" for the main rehearsal, and "go" in Attempt 3 a minute later.

Attempt 3 shows this rhythm most clearly. I typed "go" there six times that day, and each reply ended with nearly the same line: Task 4 is still in progress, and nothing was deployed to Azure.

The more interesting hand-offs were in the main attempt. When the agent proposed the rehearsal, it listed what it would do, what we'd do together, how it would clean up, an estimate of 40–60 minutes, and the fact that it would cost money. I picked "start now." Later, when I found something broken, my whole instruction was:

> "Fix it right away."

## Observed problems

**The console kept refusing me.** Partway through the deployment the agent stopped to tell me my PC's public IP had changed, and that Azure still returned 403 even after it allowed the new address. I picked "reopen," and by 10:44 a.m. two IPs were on the console's allow list. At 1:12 p.m. I was back:

> "I'm getting a 403... check my IP and add it to the allow list."

The cause, it said, was my network's NAT: the outgoing IP kept changing. It allowed the two new addresses as temporary rules, opened the page five times in a row to check, and wrote the rules into its progress ledger so they'd be removed at cleanup.

**The rehearsal found a real bug, and I found it by clicking.** At 1:23 p.m. I wrote: "I tested it. Look at the most recent data." Nine minutes later the agent came back with a cause I don't think I'd have found on my own:

- Two Service Bus subscriptions had no receive rules, so all 26 replay messages were dropped (27 in, 0 out).
- When Bicep creates a rule named `$Default`, Azure returns success but the rule doesn't stay. Rules with other names did.
- Every earlier replay measurement (132 seconds) had been taken with Service Bus turned off. That's why this showed up only now.
- The replay button had been pressed twice, so two replays ran at once.

The agent had decided earlier to leave these defects out of this branch. Its end-of-branch summary says my "fix it right away" reversed that decision.

**The other folders waited for me.** In Attempt 6, the preflight for a real deployment stopped at 4:17 p.m.: a Microsoft Graph token needed re-authentication, and the agent said I had cancelled the re-login prompt. I wrote "I logged in" two minutes later, but the agent replied that the token was still cached under the old policy and asked me to clear the cache and log in again. My next message to that folder, "clear the cache again and proceed," is timestamped 9:11 p.m., almost five hours later. In Attempt 7, at 4:40 p.m., I chose to keep a chat fix but restart the server "later"; "restart the server" came at 8:04 p.m. In Attempt 3, a reply finished at 10:12 a.m. and my next "go" came at 1:11 p.m.

**One sentence, two folders.** At 3:49 p.m., right after the main attempt merged, I typed: "The UI is too simple overall. Make it flashier, use lots of graphs and steps, so it looks more like something a company would use." At 3:58 p.m. I sent Attempt 7 the exact same sentence, plus a request to clearly separate the customer application screen from the admin screen. The two agents took very different routes.

## Response and results

**For the bug, the agent ran its usual loop, just faster.** A subagent implemented both fixes (1,016 tests passing), a separate reviewer checked them, and the redeploy took 7.6 minutes. At 2:01 p.m. I wrote "I clicked it," and three minutes later the Service Bus replay finished end to end for the first time: 13 weeks processed, three alerts.

**When I didn't know how to test, I asked.** "Tell me how I should test" got a numbered script: open four tabs, start a stopwatch, note when each scene starts. After my "done," the agent read the access logs and reported back. Submit-to-waiting took 62 seconds against the script's 40-second cut-over, and my approval had landed on the wrong pre-made case. It found no code defect, only gaps in the script's timing and in telling the presenter which case was on screen. I chose "fix and close." It paused Azure, a subagent updated the docs, and a final reviewer found two overstatements in them. The fix for those broke two Mermaid labels; a re-review caught that, and at 3:39 p.m. I said "approve" to the merge. The agent added that the two-minute timing still hadn't been measured and recommended one more practice run.

**For the UI request, the main attempt slowed down on purpose.** Instead of editing screens, it asked which screens mattered (I picked four), offered to show mockups in a browser tab (warning that this might use a lot of tokens), and drew three directions. I picked "A", an enterprise look with a navy sidebar. Then it asked how to draw charts under the console's strict content security policy: server-side SVG (recommended), a bundled Chart.js, or CSS only. I chose SVG. The spec came at 4:39 p.m. and the plan at 6:01 p.m., ten tasks whose code it had already run in a separate folder. Then subagents worked task by task with a review each time. At 8:04 p.m. it reported 12 commits and 1,072 tests passing; I picked the first option, and the branch was merged. Attempt 7 took the same sentence, proposed separate customer and admin portals, and finished its redesign by 4:27 p.m.

Attempt 7 also had its own evening. Back at 1:53 p.m. I had asked, "Are 5 agents implemented now?" It answered plainly: no, two, while the documents target five. Around 9:30 p.m. I asked about the current architecture and an agent-centred Azure version, then started a brainstorm on the agents actually needed. By 11:32 p.m. it had a consent-first five-agent design approved section by section, and a plan to build it in an isolated worktree. One odd moment: at 9:48 p.m. its offer to show a diagram came in Chinese. I said yes.

Attempt 6's day ended less neatly. A deployment only partly succeeded because a blob container name was two characters long (the minimum is three, and what-if didn't catch it). An upload was then blocked by a tenant policy, and the last report, at 11:58 p.m., was a jumpbox integration test with 11 passed, 5 failed and 3 errors.

Looking back, the longest pauses I can see in the log weren't the agents working slowly. Those folders were waiting for me: a login, a click, a "go." Running more folders didn't remove that step. It multiplied it. If you try parallel attempts, keep an eye on which folder has been waiting longest for your answer, not which one looks busiest.

## Takeaway

Parallel attempts feel productive, but each one still waits for your "go": you are the bottleneck, so run only as many as you can actually answer.
