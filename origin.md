# Case Study 18

## Agentic Cash-Flow Lending for an Asian SME Lender

| | | | |
|---|---|---|---|
| **Industry** | Financial Services (SME Lending / Non-Bank) | **Customer Profile** | Digital-first SME lender |
| **Operating Region** | Asia-Pacific | **Data Residency & Sovereignty** | Borrower financial data pulled only under explicit, scoped, expiring consent via open-banking rails; data deleted on consent expiry; processing stays in-country. |

### Business Challenge

A lender serves small businesses that lack formal financials but have rich digital footprints — bank statements via open-banking rails, tax filings, payment-platform flows. Underwriting still routes through manual financial-statement analysis built for larger borrowers, so small-ticket loans are unprofitable to originate, turnaround takes weeks, and early-warning on stressed accounts arrives at default. The fastest-growing credit segment in Asia is the one its cost structure can't touch.

### Transformation Objective

Build lending agents that assemble consented open-banking and tax data per applicant, construct cash-flow-based credit assessments with explainable reasoning for credit officers, monitor live consented flows for early-warning signals, and run fair collections with hardship pathways — with credit officers approving all sanctioning decisions.

### Agentic Architecture Brief

| Agent | Owns end-to-end | Autonomy level |
|---|---|---|
| Data Assembly Agent | Pulls consented flow data; verifies completeness | L3 – acts within consent scope |
| Cash-Flow Underwriting Agent | Builds flow-based assessment with explainable factors | L2 – recommend to credit officer |
| Early-Warning Agent | Monitors consented flows for stress signatures | L4 – autonomous alerts |
| Collections Agent | Compliant reminders, restructuring, hardship routing | L3 – acts within fair-practice rules |
| Consent Lifecycle Agent | Manages consent scope, renewal, deletion | L4 – autonomous |

**Orchestration:** Journey orchestrator per application; deterministic policy engine holds credit rules; credit officers sanction; collections scripts pre-approved; consent expiry drives deletion.

**Governance, safety and evaluation:** Explainability retained per decision; fair-treatment monitoring across segments; no dark patterns in consent; grievance mechanism.

### Expected Outcomes

Small-ticket origination cost down 60%, turnaround from two weeks to 48 hours, early-warning 60–90 days ahead of default, and profitable expansion into a segment competitors can't reach.

### Why Now (2026–2028)

Open-banking and consented-data rails reached critical mass across Asia's major markets in 2025–2026 — flow-based underwriting is now an infrastructure question answered, leaving execution speed as the only moat.

### Business Case

***Asia's SME credit gap is measured in trillions of dollars; the lender whose unit economics work at USD 6,000 tickets owns the segment banks can't profitably serve.***

Value at stake: cutting origination cost 60% turns small-ticket lending profitable, unlocking a book expansion of USD 300–500 million over the window at spreads unavailable in competitive large-ticket segments; earlier warning cuts credit cost 100+ basis points. Cost of inaction: fintech rivals underwrite on flows today, open-banking coverage compounds monthly, and manual underwriting keeps the lender out of the market's growth engine. Executive sponsor: Chief Business Officer with the CRO. Success measures: cost per origination, turnaround, warning lead time, credit cost, book growth.

---

Asia AMA FY27 · Agentic Case Study Compendium · Page 22
