---
layout: post
title: "Separating read-only review from write access"
date: 2026-11-17 09:00:00 +0900
lang: en
ref: day-16
days: "2026-11-17"
permalink: /en/day-16/
summary: "For the whole-system review, four reviewers had read-only assignments and the subsequent fixes had explicit file boundaries."
---
For the whole-system review, four reviewers had read-only assignments and the subsequent fixes had explicit file boundaries. The test of that arrangement was a cross-cutting change: pseudonymizing officer input touched both the console and the orchestrator.

## Objective

The day started with housekeeping. At 9:00 a.m. I typed "commit please" (with a typo), and the agent committed the architecture diagrams, 29 files in Korean, English and agent-emphasis versions. I said yes to pushing.

A minute later came the real request. The demo was deployed and working; I think, with the slides done, I wanted one last pass for problems a feature-by-feature build tends to miss.

## Scope and procedure

> "Please review and optimize the source as a whole."

The agent didn't start on that big, vague sentence right away. First it reported that the push had failed on an expired GitHub token and asked me to log in again. Then the size: about 44,000 lines of Python and about 3,300 lines of Bicep. It asked me to choose a focus. I ticked all four options (bugs and logic errors; security, meaning pseudonymization, auth and network; performance and latency; code cleanup), with everything in scope and "fix" as the mode.

What it did next is the part I'd copy. At 9:10 a.m. it split the code into four areas and sent out four reviewers at once:

- the orchestrator and the agents,
- the policy engine, mock APIs, evaluations and ops tooling,
- the officer console,
- the Bicep infrastructure.

Each brief said the same few hard things: read only, touch no file or branch, start no more agents, report only findings you're confident about after reading the code (file and line, a triggering scenario, a suggested fix), and end with what you declined to judge. Meanwhile the agent ran the full test suite for a baseline.

The four reports came in between 9:16 and 9:19 a.m., and none of them had anything Critical:

- **Console:** 2 Important and 8 Minor issues, and no XSS, CSRF or open-redirect problems.
- **Ops and policy:** nothing blocking in the policy engine, but a failed `window close` could still show as "closed", and a generated secret could end up printed in an error message.
- **Infrastructure:** the per-agent token limit could be bypassed through extra API Management subscriptions, and Key Vault secret access was broader than it needed to be.
- **Orchestrator:** 3 Important and 5 Minor issues. The most serious: an officer's free-text reason could reach a model without being masked.

The agent didn't wait for the slowest reviewer. At 9:18 a.m. it sent out three fixers; at 9:19 a.m., the fourth. The fixer briefs were stricter than the reviewer ones. Edit only your own area's folders, because other agents are editing the rest. Don't commit, stage, push or deploy. For every behavior change, write a failing test first. And: "if a finding is wrong, skip it and say why." One fix, masking the officer chat, touched both the orchestrator and console areas, so the agent kept that piece for itself. I don't know whether it remembered the parallel agents overwriting each other in the last episode, but this time every area had exactly one owner.

## Observed problems

**The fences leaked a little.** The orchestrator fixer saw one evaluation test fail and left it alone, because another agent was editing that file. The ops fixer's change broke a test outside its own area, as an intended effect: revoking a consent after a mock restart now records the revocation instead of returning 404. Neither reached over the fence. They reported it, and the main agent updated the one expectation that had really changed.

**Deploy day, one more time.** At 11:52 a.m. the agent summarized (4 commits, 5,253 tests passing, a final review with no problems) and asked what next. I picked push, deploy and smoke test, plus deleting the old Key Vault role assignments. Then the familiar list:

- The first deploy run failed when the Azure CLI token connection dropped mid-build. A rerun worked.
- The Functions deploy got a 403, because this PC also goes out through a second address (`<my-ip>`). A temporary rule got it through and was removed afterwards.
- The first smoke test timed out at the console check: the Application Gateway had been stopped again. The agent started it, deleted the old roles and reran the smoke test, which passed on the narrower Key Vault Reader role.

**And the language.** In that session, replies were supposed to come in Korean. The 12:43 p.m. report came in English, and I wrote "Please answer in Korean." Ten minutes later the first brainstorming answer for this blog also came in English, and I wrote it again, more firmly: "You must answer in Korean." I don't know why it slipped; the records only show that it did.

In between, as I often did, I asked it to check my IP and make the console reachable.

## Response and results

**One more reviewer, over everything.** Before telling me it was done, the agent committed the fixes as 4 commits grouped by area, and sent one more independent reviewer over the whole diff with five pointed questions, such as whether the new API Management check could break the live gateway connections, or whether anything still needed the Key Vault role it had just lost. At 10:09 a.m. the answer was "No significant issues found." The full suite showed 5,253 passed and 140 skipped.

**Then the blog.** At 12:52 p.m., with the deploy done, I opened `/superpowers:brainstorming` and wrote, in Korean: "I'd like it to be less a simple tech blog and more a trial-and-error story of someone developing with an agent for the first time. How should I do it?"

The agent checked the material before promising anything: by its count, 436 commits since 10/31, 35 deploy-history records, session logs back to 10/26, and 182 saved summaries. It filled in what it could (voice, post structure, merging quiet days, privacy) and said "correct me if I'm wrong." It offered three ways to host the blog and recommended a dedicated repository; I chose the third, a folder in this repository: "It may change later, but go with option 3 for now." When it found sessions in other folders, it asked whether to include them, and I said: "Please include the other attempts too."

Before showing me the design, it sent a reviewer to read it as a builder who couldn't talk to me, listing only the questions where a wrong guess would matter. Two came back to me, anonymity and the branch. The rest it decided and told me about: no links to the other attempts' repositories, mask even already-public IDs, show the first post for a style check, and use only my words and the agent's answers from the Q&A session. The plan had seven tasks and three approval gates for me: the outline, the style of the first post, and a final review before anything is pushed. I answered "Proceed with the subagent method."

The same pattern from the morning showed up again: a fresh implementer for each task, then a reviewer. The scanner that checks these posts for leaked IDs and paths missed three cases on its first review. The original implementer was already gone ("Agent not found"), so the agent started a fresh one with the findings; it wrote failing tests first, and a short re-review checked each fix. The site skeleton was built without a local Jekyll run, because Ruby isn't installed here; the first real build was left to GitHub Actions.

The rest happened after the day's records end. The outline of 16 episodes was approved without changes. After the style gate, the writer rules got one more line: don't put thoughts or actions in my head that the records don't show; write "I think" instead. Where the records never say why an attempt stopped, these posts only say where its records stop.

Looking back: the series began on 10/26 by asking the agent to check where a login error stood. Then came asking "why?" before "go", the same prompt in three folders, five folders at once, narrowing down to one, asking the price first, a separate session for questions, and parallel agents that needed one owner for each piece they touched. Some things never changed: I often asked to open the console for my IP, and decisions came back to me as forms. The last day's review looked like a summary of all of it: split by area, reviewers who only read, fixers with fences, and the approval-gated steps left to me.

## Takeaway

Split a big review by area, keep the reviewers read-only, give each fixer its own fence, and keep the push, the deploy and the final say for yourself.
