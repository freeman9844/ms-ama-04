---
layout: post
title: "From Features to Deliverables"
date: 2026-11-12 09:00:00 +0900
lang: en
ref: day-15
days: "2026-11-12 – 2026-11-16"
permalink: /en/day-15/
summary: "Five days in which the work turned from finishing features to producing deliverables: a slow review loop that wasn't the model's fault, a whitepaper and deck, and four parallel agents overwriting each other's words."
---
Last time, the early-warning and collections approval feature went from spec to working code in a day; on the morning of 11/12 it went live, and over the next five days the work slowly turned from building features to producing the things I would actually hand in. These five days are busy, so I'm picking three moments that taught me something about working with the agent.

## What I was trying to do

Two things, in order. First, close out the features. On 11/13, for example, I asked it to make collections approval send "only the message, without the counselling request," and chose to change the whole system rather than one case.

Second, step back and ask what the capstone still needed. A working demo is only part of it. There is also a presentation and a written piece. The deck and a recording didn't exist yet, and the written piece was only a Korean draft.

## How I handed it to the agent

**A slow application.** On 11/12 I filed a new loan application on the live site and waited. Then I asked:

> "I applied for case `<application-id>`, and computing the underwriting metrics is taking a long time. Is there a particular reason?"

The answer split the time apart. The metrics themselves had finished in 4 seconds. The slow part was the next step, the agent evaluation: 5 minutes 4 seconds, because the analyst agent wrote its draft three times before it passed. It added that its reasons for each round were an estimate from matching the code flow to the logs, and that it hadn't checked the detailed reasons yet.

My next question was the obvious one: "To make this faster, which model would be better to switch to?"

**The gap analysis.** On 11/14 I pointed it at the original assignment text and the evaluation criteria and asked it to "analyze what's built so far and suggest, by priority, what else to build or do." Its answer: most of the original requirements were implemented and running, and the remaining risk sat in the presentation and thought-leadership parts, not in the technology. The slide deck and other presentation materials weren't in the repository yet.

So I asked: "Please create the thought-leadership deliverable and the slide deck."

**Bilingual screens.** That evening I asked what the best way would be to switch the site between Korean and English. It laid out a design and I said "Yes"; it then restated what it thought I wanted, and I said "Go".

## Where it went sideways

**My question pointed at the model, but the cause was elsewhere, and the agent's first guess missed too.** The agent didn't just pick a faster model. Its answer opened by saying it was hard to promise that changing only the model would make things faster; this time the five minutes came more from the repeated rounds than from model speed. It went through the candidates: a lighter model might need more rounds, another's capacity was reserved for officer chat, and a third failed on the gateway path when tools and reasoning were combined. Then it named a different likely cause: a recent change that had made the summary longer. I chose to look at the rounds.

Three minutes later: "I found the cause. Earlier I guessed the summary-number check was the main cause, but measurement shows it wasn't (correcting myself)." Of the 16 most recent applications, 6 passed in round one. Of the 10 that needed more rounds, 9 had been rejected for the same reason: a seasonal-index comparison such as "February is the lowest of the 12 months." The reviewer agent only received the index for the month the claim cited, so it had no way to confirm "lowest" and rejected it. The agent also said slow model responses at that hour were a big part of why this one case took 304 seconds.

**I changed my mind mid-run.** While the deck tool's runtime installed (it failed at first on a PDF library that needed a build on ARM64 Windows), the agent handed the whitepaper, in English, to a background agent. Three minutes later I wrote: "Do the whitepaper in Korean first."

**Four agents, two translation files, the same words.** For the language switch, the agent built the shared plumbing itself and then split the screens across four parallel subagents: monitoring, application detail, home and ops, and customer and privacy. Each brief listed the files that area owned plus a new English translation file for it, and said not to touch anything outside the area. As far as I can see in the briefs, though, nothing said who owned a phrase that appeared on more than one screen.

So two of them collided. The home and detail agents both had entries for common words like "정책 판정" (policy decision) and "검토 에이전트" (review agent). Each changed its values to match the other's at the same moment, so they swapped places instead of agreeing. The home agent's log says: "The detail agent switched to my original values at the same moment, so I'm restoring them." The detail agent then messaged the home agent directly: "we just flip-flopped on shared keys."

## How I got past it

**Rounds, not the model.** The proposed fix left the model alone. When a claim cites a monthly seasonal index, the reviewer now gets all twelve months plus the definition (1.0 = the monthly average over the period), and the analyst is told to cite every month it compares. I picked "go" in the form. After the deploy, a re-test with 8 new applications showed round-one passes going from 6/16 to 5/8 and seasonal-index rejections dropping to 0. A new application from the seller whose case had taken 303.7 seconds passed in one round, in 54.6 seconds.

**Course corrections reach the background.** I didn't have to stop anything. Within three minutes the agent sent the background writer a "CHANGE OF SCOPE" message: don't create the English file, delete it if it already exists, remove any links to it, and upgrade the Korean draft in place instead. Seven minutes later the Korean whitepaper was done, the English file was gone, and no link to it remained.

One answer I'm glad I gave earlier. In the question form before writing started, I answered the "cases" item with "spell out in detail which cases should go in." The whitepaper had to rest on my own experience, so instead of inventing stories the agent left nine marked author-case boxes, each describing what to write and what to leave out. That evening the whitepaper and a 45-slide deck with a presenter script on every slide were committed locally. The agent estimated about 49 minutes without the optional slides, against my 45-minute slot, and listed a timed rehearsal as remaining work. The nine boxes were mine to fill.

**One owner per shared phrase.** The flip-flop ended when one side gave up ownership. The home agent removed the contested keys from its own file: "detail.json is now the sole owner, so whatever value you keep there wins and can't conflict." The reply in the log is "That works," followed by cleanup and a final test run. The switch was committed, deployed and pushed that night with 5,091 tests passing. I don't see myself anywhere in that exchange; I think I only saw the result.

The next two days repeated the pattern on a smaller scale. On 11/15 the English screens still showed Korean for up to about 30 seconds on first load, so I asked: "Is there a more effective way to handle the English version?" That turned into a pre-translation pipeline: eight implementation tasks, each done by a subagent and each reviewed, then a deploy. Before planning, a fresh agent read the design only to list the questions it still had. That step surfaced that the on-demand translation already deployed was sending text to the model with real names restored, against the project's own rule (on synthetic data), so the fix went into the design. On 11/16 I asked for the architecture on one slide, then again in English, then: "Redraw the architecture with a bit more emphasis on the agents." Each time, the agent called its accuracy check a model reading back its own image, not an independent review.

Looking back, these five days were less about writing code and more about deciding what the code was for. The most useful thing I did was ask "why is it slow?" before "which model?", and let the agent measure instead of guess. The most useful thing the agents did was end their collision with a simple rule: one owner per shared item.

## Takeaway

When you split work across parallel agents, give every shared item, not just every file, exactly one owner.
