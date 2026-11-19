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

The implementation explained why this was easy to miss. On submission the console sent a start request to a queue, then returned an HTTP 303 redirect to the status page. A 303 tells the browser to fetch a different page; it says nothing about whether the background worker has finished. It was perfectly normal for the first GET to find no application document. The menu, however, took its business context exclusively from that document. The missing name was a timing dependency, not evidence that a write had failed.

We didn't make submission wait for the worker. We carried the selected business through the redirect and preserved it in the next refresh URL. The status route then had three cases:

| What the status route finds | Business shown in the menu | Reason |
|---|---|---|
| An application document exists | The business recorded in the document | A stale link must not override stored information. |
| No document yet, but the URL names a listed demo business | The business from the URL | The seller can wait without losing their navigation context. |
| No document and no recognized business in the URL | No selection | An unknown value should not be presented as a fact about the applicant. |

The URL is a **navigation hint**, not proof of ownership. That distinction also identifies the useful regression test. Rendering a completed application never exercises the original problem; the test needs the first GET *and the next refresh* before the document exists. The [route and test changes](https://github.com/freeman9844/ms-ama-04/commit/08ef72f) show precisely where the context used to disappear.

The new overview put the requested amount and term together and showed progress in four stages. That gave me a concrete way to review the design with the agent: follow one seller, submit once, and see whether the next screen still tells the same story. A logo and business cards helped it look like a product. Neither could compensate for a menu that lost track of the seller.

## Why a stylesheet alone wasn't enough

A new palette was the lowest-risk choice, but repainting a table wouldn't put the amount and the next step where a seller would read them first. Rebuilding every detail view had the opposite problem: it risked breaking working tabs and queries to change the emphasis of a number. We changed shared styles and limited template changes to places where the reading order actually needed to change, including the business picker and the application overview.

The progress display retained text for each stage: submission, data review, officer review, and outcome. The current stage received `aria-current="step"` rather than relying on color alone. Cards collapsed to a single column at 720px and below. We kept the existing content security policy instead of allowing inline styles or external fonts. The final visual fixes were about contrast on links and section headings and a visible keyboard focus indicator, not another decorative element.

## Three checks after deployment

Deploying the console image took about ten minutes. The first live request then timed out. Rebuilding the image would have shipped the same code again; it wouldn't have tested whether the request could reach it. The Application Gateway was stopped. Starting it took roughly another nine minutes.

We separated **reachability, version, and behavior**. With the temporary deployment window closed, the customer and officer pages returned HTTP 200. We checked the served stylesheet for the new design, then looked for business cards on the picker and an amount-and-progress card on the selected business's overview. A 200 from yesterday's console would also have been a 200. And a correct overview still wouldn't have tested the timing boundary on the waiting page. Those checks answer different questions.

## Takeaway

When asking an agent to make a demo feel real, give it one journey to preserve. “Pick a business, apply, and come back to the same business” is a better acceptance check than “make it look finished.”
