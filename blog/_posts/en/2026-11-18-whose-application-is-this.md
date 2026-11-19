---
layout: post
title: "Preserving the selected business across application submission"
date: 2026-11-18 09:00:00 +0900
lang: en
ref: day-17
days: "2026-11-18"
permalink: /en/day-17/
summary: "The console redesign had two distinct pieces of state: the selected business and the application's progress."
---
The console redesign had two distinct pieces of state: the selected business and the application's progress. Submitting an application could leave the decision pending, but it should not clear the business from the waiting screen or its menu. I reviewed the design against that transition.

## What I meant by “flashier”

I asked the agent if it could make the site flashier. That could have been answered with a new color palette. My next sentence was a better brief: I wanted it to look like a real service. We settled on the fintech-app design, option B. But the work that made that design convincing had started with the business selector, not the colors.

We gave the choice of business its own screen. Once a seller chose one, the customer menu stayed in that business's context. A telling detail was the wait just after submission. The application wasn't ready to show a decision, but the menu still needed to remember the business. The fix was to carry the selection through that wait, not to ask the customer to make the same choice again.

## Following one application

The new overview put the requested amount and term together and showed progress in four stages. That gave me a concrete way to review the design with the agent: follow one seller, submit once, and see whether the next screen still tells the same story. A logo and business cards helped it look like a product. Neither could compensate for a menu that lost track of the seller.

Even the visual pass had a functional follow-up. Links, section headings and the current stage needed enough contrast, and keyboard focus had to stay visible. Then came a different kind of mismatch: the deployed console didn't answer the live check. The application gateway was stopped. After it started, the pages responded and we could check the cards and logo on the deployed site. A timeout hadn't told us anything about the design.

## Takeaway

When asking an agent to make a demo feel real, give it one journey to preserve. “Pick a business, apply, and come back to the same business” is a better acceptance check than “make it look finished.”
