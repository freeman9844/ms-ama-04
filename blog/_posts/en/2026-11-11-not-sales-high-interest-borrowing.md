---
layout: post
title: "Not Sales — High-Interest Borrowing"
date: 2026-11-11 09:00:00 +0900
lang: en
ref: day-14
days: "2026-11-11"
permalink: /en/day-14/
summary: "I asked for more realistic post-loan data and got back a list of four gaps; one sentence from me about what was actually wrong set the direction for the rest of the day."
---
The settlement-reconciliation feature from the last episode, the one that grew out of the assistant saying "that's not in the data," still had to reach the live environment when this day began. So the morning started with a deploy, and the real work only began at 10:26 a.m.

## What I was trying to do

My first two messages were "again" at 8:07 a.m. and "keep going again" at 8:35 a.m. The deploy history says the first attempt failed on a temporary local DNS problem and was run again; I think my "again" was that rerun. By 9:09 a.m. the agent reported the feature deployed, checked on the live screens and pushed, and the test run reported 2,897 passing. At 10:08 a.m. I asked the agent to check my IP and whether the console was reachable. It found that my current IP wasn't on the WAF allow list, so the console was answering 403. It added `<my-ip>` to the list and confirmed both pages now returned 200.

Then the actual job. After a loan is approved, AMALend replays 13 weeks of the seller's cash flow week by week, so the early-warning agent has something to watch. I wanted that replay to look more like a real marketplace seller.

## How I handed it to the agent

The request was one line:

> "Please make the post-loan 13-week replay data more realistic, closer to real marketplace sellers."

The agent first checked how the data was generated, and a minute later it came back with four ways it fell short:

1. Settlement deposits arrived exactly as the ledger said. Underwriting data had timing gaps, offsets and advances; the post-loan data had none of them.
2. Repayments ignored the approved terms. A fixed 287,000 won left the account every time, and there was no deposit for the loan itself.
3. The distress scenario was mechanical: 1.5 million won borrowed every two weeks, 70,000 won repaid every Friday, default around day 70–80, the same for every seller.
4. There were no marketplace events at all: no settlement holds, return spikes or fee increases.

All four were fair. But I think a list like that has a quiet risk. If I'd answered "go," the agent would have weighed the four points however it saw fit. A minute later I answered with one sentence instead:

> "It's not sales changes — the results get worse from high-interest borrowing alone."

That sentence is where this post's title comes from. It was a complaint about what the data already did. In every seller that went bad, the only thing that went wrong was borrowing. No sales slump, no held payout, no wave of returns. Just high-interest loans stacking up until default. Real sellers, I think, usually end up borrowing at high rates because something else went wrong first.

## Where it went sideways

The direction itself held all day. At 10:29 a.m. the agent turned my sentence into a choice:

- **Option A:** every distressed seller follows the same path. Sales fall 30–50% below expectations in weeks 1–4, then high-interest borrowing starts around week 5.
- **Option B (recommended):** each seller gets its own marketplace cause: unsold seasonal stock, a platform holding settlements, a return spike, rising ad spend and fees. Borrowing appears only afterwards, and only in some sellers; one seller gets worse without borrowing at all.

I picked B. The next question was scope: change only the data, or the data and the agent. The second option added a post-loan settlement ledger and had the early-warning agent show a cause on the alert card, for about twice the work. I picked that too.

From then on I could see my sentence in places I never typed it. The brief for a read-only code-mapping subagent asked for "high-cost borrowing only as a later consequence." The Task 3 brief opened with "Today the 13-week post-loan replay only worsens through high-cost loan stacking." The early-warning agent's new instructions had to include, word for word, "차입은 대개 결과다" ("borrowing is usually the consequence"). The final reviewer's brief summed up the goal as "user's complaint: replays must not worsen only via high-cost borrowing."

What went sideways was everything around that plan.

**The plan changed one thing, and said so.** The spec was approved at 10:45 a.m. and the plan, ten tasks, arrived at 11:02 a.m. It flagged one difference from the spec: cost increases would be detected only through the settlement deduction rate, because ad and shipping costs leave once a month and can't be compared week by week. I approved the plan and chose subagent execution.

**The network dropped twice under subagents.** At 3:46 p.m. the Task 6 implementer failed after 518 seconds with zero turns: a DNS error while reaching the model API. At 5:06 p.m. the Task 8 implementer failed the same way after 1,092 seconds.

**The final review found a seam.** By 6:27 p.m. all ten tasks had passed their own reviews, Tasks 3 and 5 only after a fix round for three Important findings each. The whole-branch review still came back "Ready to merge: With fixes": 0 Critical, 1 Important, 5 Minor. The Important one: the cause the early-warning agent picked never reached the collections agent. Each task had passed its own review, but the hand-off between two of them was missing.

## How I got past it

**For the DNS failures, I said "retry," and the agent checked before retrying.** I typed "retry" at 3:57 p.m. and again at 4:00 p.m. The records don't show why I sent it twice. Before re-dispatching, the agent confirmed the working tree was clean and nothing had changed, then sent Task 6 out again. It finished at 4:21 p.m. with 4,571 tests passing; the count had jumped from the morning's 2,897 mainly because Task 3 widened one parametrized test matrix to five scenarios, adding 1,656 tests. Task 8 was a different case. That implementer had committed its console change before the connection dropped, but never wrote its report. After my "keep going again" at 5:33 p.m., the agent re-ran the full suite itself (4,577 passed), prepared a substitute report and sent the commit to review. Both times my message was just "retry" or "keep going," and both times the agent checked the state before doing anything.

**For the seam, one fix agent and a narrow re-review.** The cause now reaches the collections agent, a stale "loan stacking" sentence in the docs was fixed, and a possible false alarm when expected orders were zero was closed off. Two minor items were left as comments and docs instead, because fixing them would mean changing all four scenarios or existing metric definitions. The scoped re-review came back clean, with 4,589 tests passing.

**Then the live check.** I approved deploy and push at 6:55 p.m. At 8:07 p.m. the agent reported fresh replays in the live environment. The first alert's primary cause was sales slump, settlement hold, return spike and cost increase for the four sellers. High-interest borrowing showed up as the secondary cause for three of them and not at all for the return-spike seller. The stable seller had zero alerts in 13 weeks. Borrowing was still in the story, just second.

The evening flipped the roles. At 8:08 p.m. I wrote a long message defining the two post-loan agents: early warning finds signs of trouble before they grow, and collections supports customers who are struggling. A missed payment should produce only a proposal, which an officer approves, modifies or rejects. This time the agent restated my request first. It opened with "Here's a summary of what you asked for. If anything is wrong, please correct me," then laid out the summary and listed how it differed from the current structure. It was the same move I'd made that morning, from the other side. It also caught a demo trap at 8:38 p.m.: fair contact hours (8 a.m. to 9 p.m.) were checked against the real clock, so a demo after 9 p.m. would hold every automatic notice. I chose the simulated clock. The spec was approved at 8:41 p.m., an 11-task plan followed at 8:58 p.m., and it went to subagents again. By 11:48 p.m., Task 10 had passed review and Task 11 was running.

Looking back, my first request was the vague one, and the one-line reply after it did the steering. A list of four true problems was useful, but it didn't say which one I cared about. Only I could say that, and once I did, the agent carried that sentence into subagent briefs, the final reviewer's brief and, finally, the early-warning agent's own instructions.

## Takeaway

When the agent hands you a list of problems, answer with the one sentence that names what's actually wrong, then look for that sentence in every brief that follows.
