---
layout: post
title: "One won is a payment, not a paid installment"
date: 2026-11-20 09:00:00 +0900
lang: en
ref: day-19
days: "2026-11-20"
permalink: /en/day-19/
summary: "A one-won debit exposed a ledger that could recognize a payment without knowing how much was still owed."
---
Suppose an installment of KRW 500,000 falls due. The account shows a debit of exactly one won for that loan. What should the officer see? Not “missed,” because money moved. Not “paid,” because KRW 499,999 is still owed. That single won turned out to be a better test than another full payment.

## Follow the money, not the event

The old behavior effectively answered “did a debit happen?” and used that answer to close the installment. I asked for a different rule: take a *confirmed debit for this loan*, allocate it to the oldest amount already due, and carry the remaining balance forward. A debit for a different loan is not evidence just because the amount and date look right.

For the one-won case the regression test now has three things to distinguish: the installment is partial, one won has been paid, and only the balance can go to collections review. With two overdue installments the order matters too. A late payment must reduce the older debt before it can count toward the newer one.

## What if someone is already preparing the notice?

Getting the arithmetic right wasn't enough. An officer could open a collection proposal, the borrower could finish paying, and the officer could then act on the stale proposal. Checking the balance when the page opens leaves that gap intact.

We made the write conditional in Cosmos DB: the records for the same partition are committed together only if the version still matches. If payment wins the race, an old proposal cannot produce a new contact on those terms. If contact was already recorded, a later payment does not rewrite history. For older synthetic loans whose debit records couldn't be attributed safely, we kept the old records rather than guessing.

## Takeaway

The useful test wasn't “can we detect a repayment?” It was “after this particular won moves, what may we still ask the borrower to pay?” That's a question the ledger has to answer before the assistant drafts anything.
