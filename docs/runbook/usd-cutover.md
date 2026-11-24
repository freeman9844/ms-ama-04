# USD cutover — approval required, not a deployment record

`USD_ORIGINATION_ENABLED` is a console **producer** setting. Only `true` opens
`POST /apply` and `POST /applications`; missing, `false` or an unrecognized
value is closed. A valid closed POST returns 503 and sends nothing. A stale
KRW form is invalid, not an alternative product. Authentication and CSRF still
apply. The value is loaded at startup. Setting an azd variable alone does not
change a running replica.

The flag is deliberately not a queue/Durable kill switch. Already accepted
starts, redeliveries, officer decisions, borrower actions, servicing, consent
withdrawal and deletion must keep working in either state. The legacy batch
load tool is not a USD producer or a cutover verifier. A direct queue sender
bypasses the console: account for **all** publishers and disable unapproved
jobs/scripts/direct access before calling the gate closed.

## Before any live write

1. Obtain release owner, operations and privacy approval for a named environment,
   immutable SHA/images/Function package, maintenance interval, synthetic-only
   participants, isolated notice destination and rollback owner. No real contact,
   money movement, RBAC expansion or data migration is part of this rehearsal.
   Record baseline KRW counts, status/KPI denominators and native sums, direct
   application/loan links, original approved amounts and replay outcome. Record
   active Durable IDs (including human waits), queue/poison/DLQ backlog, leases,
   incomplete proofs and grant IDs in the restricted release log, not raw PII.
2. **Fence old writers before starting new workers.** Block only new origin
   POSTs at ingress while replacing old consoles (including stale revision
   URLs); pause batch/smoke/scheduled jobs and direct publishers. Quiesce
   trigger intake on every old Function host/slot sharing storage, Service
   Bus or the `lending` hub. Let active invocations settle; stop/deactivate
   the old hosts, spare slots and jobs and prevent restart/scale-out. Record
   instance IDs, package versions and last invocation times. Verify no running
   old instance, no remaining in-flight writer or renewed old lease/lock, and
   no old-version writes after shutdown across the applicable lock/visibility
   intervals. Zero HTTP traffic, an empty queue or a timeout alone is not a
   fence. **Do not purge** queues, Durable history or waiting instances.
   Stage commands during the maintenance interval; keep privacy intake and
   track its backlog/deadlines. If an old worker could resume, do not proceed:
   it can create containers on upload, overwrite CAS deletion proofs, or send
   contacts without the borrower-wide reservation, even with origination off.
3. While fenced, inventory both underwriting and monitoring grants against
   the **same** Blob account/container namespace. For an active legacy grant
   missing its namespace, distinguish confirmed not-found from denied/unavailable
   and from deletion in progress. Reissue via the authorized consent lifecycle,
   or provision the exact namespace only after confirming the grant is still
   valid, with writers **and lifecycle transitions quiesced**; recheck status
   before resuming. Never recreate ended, expired, withdrawn, ambiguous or
   deleting namespaces. Missing expected raw evidence is not fixed by an empty
   container; reconcile or hold that case. No bulk create/backfill, suppressing
   missing proof items or assuming a stale write intent is dead.
4. Review the [component deployment commands](deploy.md) and IaC diff; set
   `azd env set USD_ORIGINATION_ENABLED false` **before** provisioning or
   opening a maintenance window. Never use `ops deploy all` for this switch.
   Stage pinned `mockapi` (both source contracts, stable consent secret and
   native FlowStore), `policy` (legacy and USD rules), `eval`/reporting job
   (legacy scope), fenced `orchestrator` package, then `console` closed.
   Use the existing approved window; close it and verify its status. If the
   deployment temporarily restarts any host, ensure it cannot claim work until
   the fence/versions have been verified. Do not move old instances to a new
   hub or start a second journey to clear a wait.
5. Verify **effective** healthy package/image revisions and config on every
   listener/replica; route 100% to the dual reader. Resume only the new
   workers against the original hub, queues and subscriptions. Confirm a saved
   untagged pending journey replays, its queued KRW decision is honored, and a
   duplicate delivery does not originate a second case. Check mixed records
   through mixed-reader staging with isolated synthetic copies. KRW archive
   totals and old direct links must match baseline; USD lists and KPIs exclude
   them before limit/aggregation. Confirm real roles, auditor masking and
   ownership/CSRF barriers, including after withdrawal; verify revoke and
   deletion proof on disposable **copies only** for each contract. The mock
   token revocation registry is in memory: verify active-token continuity and
   continued rejection of revoked, unexpired tokens across revision changes.
   If revocations cannot be preserved/restored before serving traffic, stop.
6. Require working private identity/network paths, Cosmos indexing and RU
   headroom, conditional writes/batches (contact cap across both currencies,
   ETag proof and grant tracking), queue consumption/poison monitoring,
   `flow.updated` and `repayment.updated` subscriptions/filters and DLQs,
   and a healthy five-minute `repair_consent_deletions` timer. Verify verified
   Blob namespace absence and same-project Foundry cleanup: after a failed
   DELETE only authoritative GET 404 confirms container absence; forbidden,
   transport and server errors are **pending**, not success. Check repair and
   notice reconciliation; old missing manifests/cleared markers cannot be
   reconstructed by the timer. Escalate unresolved historical gaps. Check
   native English grounded review via the actual model/API and fallback paths
   in the isolated stage. Fail or lack of evidence on any check = keep closed.

## Enable last, verify once

Only after written go/no-go for writes: restrict pilot ingress to the approved
operators; `azd env set USD_ORIGINATION_ENABLED true`, then
`azd provision --no-prompt` in that environment after reviewing its scope.
Confirm the effective revision value and retire alternate writable revisions.
Submit **one** $5,000.00, six-month request per seller. Record each returned
application ID immediately; on a timeout inspect queue, Durable and stored
receipt before any retry. No rerun to obtain a preferred AI or human outcome.

| Seller | Expected evidence, not a promised decision |
|---|---|
| `seller-bakery-20` Daejeon Bakery | Eligible, ordinary approval path; scheduled/paid principal in the 13-week window, not six-month payoff. |
| `seller-petfood-07` Gwanggyo Pet Food Lab | Suspension, R003 ineligible; distinguish policy and actual officer disposition. No servicing without approval. |
| `seller-knitroom-04` Pangyo Hand-Knit Studio | Channel concentration R107 and S001, refer. Officer wait is **not completed**. |
| `seller-dailylook-01` Seongsu Daily Look | Eligible, approved stress path: inspect weeks, shortfall, receipts and missed balance. `paused`, `awaiting_customer`, `awaiting_resume` are not completion. Only authorized human actions/resume may move them on. |

For each ID cross-check borrower/officer views, native English source/profile,
transaction and settlement cents, policy reasons and reviewed evidence. Check
`(usd-v1, USD, en)` on start, nested officer decision and downstream messages;
stored actual approval (not requested amount or recommendation) drives the
loan. Check repayment rows and remaining balances including remainder cents;
no invented payments or forced approval. Record expected vs observed, revision,
times, state/holds, week and links; compare both portfolio totals again and
inspect duplicates, poison/DLQ and privacy repair. Record live history only
after these checks, with outstanding waits explicitly identified.

## Stop / rollback

On mismatch, duplicate, stale writer, privacy failure or unexplained backlog,
block the two new POSTs immediately at ingress (do not block subpaths for
existing cases). Set `azd env set USD_ORIGINATION_ENABLED false` and apply the
reviewed configuration; verify every reachable console revision rejects a
valid synthetic origin without a queue entry before lifting the ingress fence.
Preserve all accepted USD and KRW records, work queues, proof checkpoints and
history. Keep both contracts' consumers, APIs, archive, decisions, 13-week
servicing and repair timer online. If a compromised worker must be isolated,
retain its messages for a compatible replacement; never roll back to KRW-only,
restore old unconditional writers, re-label money or replay notices to recover.
Record affected IDs/deltas and actual revisions; require a new approval to reopen.
