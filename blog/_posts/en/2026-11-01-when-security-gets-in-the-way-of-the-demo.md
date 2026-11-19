---
layout: post
title: "Scoping an authentication exception for the demo"
date: 2026-11-01 09:00:00 +0900
lang: en
ref: day-06
days: "2026-11-01"
permalink: /en/day-06/
summary: "Login prevented me from demonstrating the officer workflow."
---
Login prevented me from demonstrating the officer workflow. The demonstration concerned what happened after sign-in, not how sign-in worked. That distinction determined which control could be relaxed, which access restrictions still mattered, and what had to be reversible.

## Objective

In the main attempt, the morning's job was the officer console: the screen where a credit officer reviews an application and decides. On paper it was well guarded. Entra ID login with three app roles (CreditOfficer, CreditManager, Auditor), plus an IP allow list.

Before writing the plan, the agent probed and reported that one way of wiring login through a managed identity was still unofficial, and that a documented alternative used ID tokens only, with no client secret. From its approval prompt I picked a minimal scope and the ID-token login. By 10:38 a.m. the plan was committed: seven tasks, 517 passing tests, and eleven review points, among them CSRF, XSS and "IP fail-closed." I chose to run it inline in the same session, with a real deploy.

By 11:32 a.m. the console was live, and the smoke test had passed all 25 checks. One of them confirmed that an anonymous request gets sent to the Entra login screen. What was left was the part only I could do: open it in a browser, log in, submit an application, make a decision.

## Scope and procedure

I didn't describe the problem in words. I pasted a screenshot and asked:

> "When I try to log in, this screen comes up. What should I do?"

The screen was an admin approval page. The agent granted the login permissions (openid, profile, email) directly to my own account. It noted that the grant covered one account: mine. Then it gave me a six-item checklist, which I answered through the approval form. Login: success. The page that shows my roles: `{"detail":"Not Found"}`. Submitting a new application: failed, with the browser saying I didn't have permission to view the page. The last three items: unchecked.

## Observed problems

**The login was fixed, and the button still didn't work.** At 11:40 a.m. the agent explained the first wall. The tenant blocks users from consenting to apps on their own, and my account only had a read-only directory role, so I couldn't grant admin consent either. Its workaround, a manual grant through the Graph API, was flagged plainly: not in Bicep yet, to be added before redeploying and recorded in its ledger. Login and the list page now worked. The role was confirmed too, because the new-application screen opened, and that screen needs CreditOfficer.

But submitting still returned 403. The POST never showed up in the app's logs, so something in front of the app blocked it. When the app itself returns 403, there's an error body; what I saw was the browser's bare default page. It was about to ask me for my browser's request headers to dig further.

I cancelled that prompt and typed this instead:

> "This is for a demo, so please remove Easy Auth for now and make it testable."

The records don't say why. I think I had decided that proving the login worked wasn't what this demo was for.

**Meanwhile, Attempt 3 kept every control and stayed offline.** That afternoon it had 16 of 21 tasks done, but the infrastructure step was on hold: model availability, access method and cost were all undecided. Through a series of approval prompts I chose to compare Global model deployments and to cost a new VM plus Bastion for private access. The agent was clear that Global deployment in Korea Central does not guarantee that inference stays in the country. The access pieces alone came to about $0.40 an hour. The known subtotal came to about $2.97 an hour, or $71.36 for a hypothetical 24 hours. It was explicitly *not* a full estimate. I said "Go."

At 6:26 p.m. the agent reported that the plan had put even *writing* the Bicep behind the paid-creation approval, so the work was stuck. It suggested splitting local implementation from cost approval, and I agreed. The implementing subagent then stopped with NEEDS_CONTEXT, because it needed an outbound-traffic decision first. I picked a NAT Gateway with its own public IP and deny-by-default NSGs. Two review-and-fix rounds later the infrastructure code was approved, and its status line read `local_complete=true; azure_verified=false`. The next task stopped again at 9:15 p.m.: the API gateway's public access can only be disabled once its private endpoint exists, so it needed a decision about a temporary public bootstrap. Nothing was created on Azure that day.

**And Attempt 5 skipped the real thing.** It started from scratch around noon. After a design session, I asked at 2:02 p.m.:

> "Build a mock demo from this. Implement the whole infrastructure architecture but use mock data, and write the web page first instead of collecting all the back data. It's for a demo, so I'd like it flashier, with lots of graphics. …"

The agent built a dark "Mission Control" dashboard on small fixed mock data. It was merged at 4:31 p.m. with 23 tests and 11 Chrome demo runs passing, and grew to eight sample companies by evening. In its design prompts, the agent had said the screen would separate "UI simulation" from "real Azure run." Still, at 7:02 p.m. I asked, "Has the LLM been applied?" The answer: "No. Right now it's a UI mock demo that doesn't call an LLM." I think the screen looked convincing enough that I wanted to hear it said directly.

## Response and results

Back in the main attempt, what the agent built was not "delete the login." It was a switch, and a narrow one:

- An azd environment variable, `CONSOLE_AUTH_ENABLED=false`, turns Easy Auth off.
- In that mode, every request is treated as one fixed demo officer (CreditOfficer), and a "demo mode" banner sits at the top of every page.
- With login gone, the operator IP allow list (`<my-ip>`) stays as the only gate.

The code was committed at 11:48 a.m. with 532 tests passing. Partway through the deploy, the agent told me a message I'd sent hadn't reached it and asked me to resend. I typed, "What's the status? Should I just wait for the background job?" At 12:03 p.m. it reported back: window open 4.0 minutes, console deploy 4.2, window close 4.3, smoke 25 checks in 2.4. It also sent a POST with a deliberately forged CSRF token. This time the request reached the app, and the app rejected it with 403, as it should. At 12:06 p.m. I wrote: "I tried a new application and approved it, and it looks like it's working properly."

Looking back, I think the most useful part came next. The agent handed the branch to a fresh reviewer, with a brief I never typed. The brief singled out demo mode. Does an unset switch still enforce Easy Auth everywhere? Can demo mode be entered by accident? Does switching back actually restore login? Is there any other way this could fail open? The reviewer found nothing Critical or Important. Demo mode starts only on the exact string `false`; `False` or `0` keep login on, and an unknown mode value stops the app from starting. One Minor finding was that the demo role setting never reached the container. The agent raised it to Important, because in an auditor demo it would expose real names, and fixed it test-first before the merge.

When I asked "What's the current state?" at 12:30 p.m., the answer included what I think is the most important line of the day. To go back to Entra login, set `azd env set CONSOLE_AUTH_ENABLED true`, then run `ops window open --acr` and `close`.

Later that evening, during the consent phase's browser check, I pushed back on scope once more: "It looks normal, but the mid-way consent revocation scenario seems unnecessary." The agent moved straight on to its final review, and the day's records don't show what became of that remark.

So the three folders made three different choices. Attempt 3 kept every control and had nothing running on Azure by night. Attempt 5 had a flashy screen and no model behind it. The main attempt switched off exactly one control, kept the IP gate, had a reviewer check that the switch failed closed, and wrote down how to turn it back on. If you're about to try this, you will hit a moment where a security feature blocks your own demo. Turning it off can be fine. Just make the agent turn it off as a named, reversible switch, not as a deletion.

## Takeaway

Decide what the demo has to prove before you touch a control; switch off only what doesn't serve that, and make sure the way back is written down.
