---
layout: post
title: "Keeping business assumptions separate from implementation results"
date: 2026-10-29 09:00:00 +0900
lang: en
ref: day-04
days: "2026-10-29 – 2026-10-30"
permalink: /en/day-04/
summary: "A working local demo established feasibility, not a business case."
---
A working local demo established feasibility, not a business case. For two days I used the agent to investigate the latter. An answer in the conversation and a file I could inspect at the next design review turned out to be different deliverables.

## Objective

By midday on October 29, Attempt 2 had a local demo. The agent's own checkpoint summary describes it: React, TypeScript and SQLite, a separate HTTP mock data source, and real calls through the GitHub Copilot SDK, with a final review already done. The demo worked. What I didn't have was an answer to a plainer question: is this idea worth anything to a real lender?

So for most of these two days I didn't ask for code. The five commits on the 29th are an initial commit, documents and merges, and the 30th has no commits at all. I spent the time asking questions about the business and having the agent write the answers into files.

## Scope and procedure

The first request, at 12:17 p.m., was short and had a typo in it:

> "Please do some more analysis from a business point of view."

The answer reframed the project in one sentence: the core is not "AI approves loans" but "underwrite small loans profitably and act before repayment problems grow." It also drew a line: a demo that runs technically and a business that is proven are two separate things. Then came a table of stakeholders, from the business and risk heads to the underwriter, the collections team and the borrowing business, each with a current problem and the value on offer. The direct buyer, it said, is the lender; the small business is the end beneficiary.

Thirty minutes later I asked it to save that to a notes file, and it did.

The next day I went further. I pointed it at one sentence in the source document, the one describing the lender's target customers, and asked:

> "Is there anyone in Asia actually running this kind of business? I'd like to find references doing something similar."

From there the session was a chain of short questions, each one built on the last answer:

1. What technical architecture does each company use?
2. Does this have business value? Analyze it a bit more.
3. Do a gap analysis of the existing businesses. Is there a way to raise the business value, or to score credit better with digital data?
4. Is there a way to make the business stand out?
5. How do you actually get the platform's data?
6. What's wrong with the existing players?
7. What if we could get data on similar sellers from a Korean platform? Wouldn't that make a seller's loan request easier to judge?

Every so often I typed "update the file" and the agent folded the recent discussion into a second notes file.

## Observed problems

**The research tools stumbled first.** Within six minutes of the first question on the 30th, three tool calls failed: a 504 from the GitHub tool, a web fetch that couldn't connect, and a timeout. The day before, a GitHub documentation page had returned 404. I don't think I noticed any of this at the time. The answer arrived at 11:36 a.m. anyway, and the saved file has source links. I can't tell from the records exactly which sources it finally reached.

**The agent kept narrowing my claims.** I think part of me hoped for "yes, this exists, go build it." Instead each answer came with a fence around it:

- The references were real: a Korean e-commerce platform lending with a capital company, a Chinese online bank that started with marketplace sellers' transaction data, and two Indian lenders. But it separated "they evaluate small firms with digital transaction records" from "they use open banking, tax and payment data the same way we do."
- Its architecture diagrams were "a logical structure reconstructed from public material," not the companies' own designs. The comparison table had a column for what public information *doesn't* show.
- On business value: yes, but as a tool sold to existing lenders for underwriting prep and post-loan follow-up, not as a new AI lender. There was a case for a limited paid pilot, but profitability was not proven. One line from that answer: competitor cases are evidence of a market, not of our product's strength.
- On the gap analysis, it said it would not treat a feature as missing just because a company hadn't published it.
- On my own idea, comparing a seller with similar sellers, it agreed it adds context, then split it in two: judging a business's current state more clearly is one thing, predicting repayment risk more accurately is another. The second needs cash, existing debt and actual repayment outcomes.

None of this was a failure. But it was not cheerleading.

**One save may not have happened.** At 12:34 p.m. I asked it to update the file. A minute later, before any reply shows up in the records, I asked the next question about business value. The next save, at 1:18 p.m., reports adding the business-value analysis; the one after that, at 2:19 p.m., lists the gap analysis, differentiation, platform data access and peer comparison. Neither summary mentions the architecture comparison from 12:27 p.m. From the records alone I can't tell whether it ever made it into the file.

## Response and results

**For the narrowing, I kept asking.** I didn't argue with the caveats. I asked the next question inside them. When the agent said head-on competition on "our AI scores credit better" would need real default data, I asked where the gap was instead. When it pointed to settlement-flow-based underwriting for online sellers, I asked how we would get the platform data, and the answer separated "the seller agreed" from "we have the right to use platform data for credit decisions." That chain of eight questions is, I think, the most useful thing these two days produced.

**For memory, I used files.** Five times across the two days I told the agent to save or update. Every reply that shows up in the records came with a short summary of what was added, which is also how I can see the gap above. Reading those summaries against what I asked for is the check I'd add.

**A side quest showed the same pattern.** On the 29th I wanted to push Attempt 2 to a different GitHub account from the one Copilot was signed in with. I asked whether I could keep the current Copilot login and use the other account only for syncing. The first answer was a generic setup: a dedicated SSH key, set only for this repository. It added two notes worth knowing: changing the remote URL doesn't change who authenticates, and `user.name` and `user.email` are author details, not login.

Then I gave it one fact: "That account is already logged in too, in gh." It checked, confirmed both accounts were authenticated, and changed the plan: no new login, no SSH key, no global `gh auth switch`. It also pointed out that no remote was registered yet, and that it hadn't changed any settings. Before pushing, it asked once more. The target was public, `main` had only a README, and I had 13 uncommitted analysis documents. How should it sync? I picked `main`. Three minutes later the code and documents were there, the existing history was kept, and my active account was unchanged.

That afternoon I also used the agent to explain my own design. I asked it to brainstorm and expand an orchestration document. Before touching it, it pointed out that the document assumed a separate LLM orchestrator, while the actual demo wired five roles together in code and only three of them called a model. It asked what the document's main purpose was, and I chose `conceptual`. Its proposal put it plainly: five agents are five business roles, not necessarily five LLMs or five servers.

Looking back, these two days were light on keystrokes and heavy on questions. The agent was a good research partner not because it agreed with me, but because it kept marking where the evidence stopped. The file it wrote is the part I can reuse.

## Takeaway

On days when you aren't building, use the agent to research, and have it save each answer to a file. Then check that each save actually landed.
