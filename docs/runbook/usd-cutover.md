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

## Independent ingress fence (required for staging and rollback)

`infra/modules/appgw.bicep` owns the WAF policy's entire `customRules` list.
`azd provision`, `ops window open/close` and image deployments can replace a
hand-added rule **before** the console revision changes. Do not use such a
rule, a DNS change, a traffic weight or the console flag itself as the fence.
This procedure requires an independently managed ingress control; this
repository does **not** provision one.

1. Before authorizing the pilot, have the network owner identify the existing
   enforcement point (for example, a separately managed reverse proxy), its
   policy ID/version, owner and rollback procedure. Inventory public listeners,
   gateway IP/hostnames, revision/label URLs and private callers. Verify every
   ordinary caller must traverse the control: direct access to the gateway and
   backend must already be disallowed except via approved paths. A proxy that
   clients can bypass by using the gateway hostname is **not** a fence. Keep
   the existing WAF operator IP restriction, backend network restrictions and
   app authentication unchanged; do not add an Allow rule or broaden an IP range.
2. On that independent control, activate a **deny** for method `POST` and
   either exact normalized path `/apply` or `/applications` on every console
   host. Ignore the query string; cover trailing-slash/normalization aliases
   that dispatch or redirect to those endpoints. Do not deny a path prefix:
   status GETs, `/applications/{id}/decision`, servicing and privacy POSTs
   must still pass their normal controls. Save the policy version and an
   explicit release-owner-only removal condition (no automatic expiry).
3. From each ordinary ingress path use credential-free, invalid test forms
   with redirects disabled; confirm the **edge rule ID** denied both POSTs
   before forwarding. Check authorized GETs and an existing-case workflow
   on a disposable copy still reach the app. Record time, path and rule hits.
   Check reachability and recheck the fence after each provisioning/revision
   change, not just at the end. An ingress denial proves only the fence.
4. Reserve a separate, access-controlled private probe runner with an existing
   authorized session and connectivity to a **specific** console revision.
   Only the release operator may use it; it must not open an ordinary ingress
   bypass. Confirm independent queue-write auditing is available for the real
   `applications-start` queue (including promptly consumed messages).
   **No independent fence, private probe, or usable write audit: no staging,
   enablement or rollback provisioning.** Keep any active deny in force,
   escalate to the incident/release owner, and do not claim writes are closed.

### Prove the application is closed while the edge deny remains in place

After convergence on `false`, inventory the active revisions, traffic and
labels. Retire or isolate obsolete/enabled revisions; record revision names,
image digests, effective flag and which targets remain reachable. From the
private runner pin each reachable revision by its revision-specific FQDN
(not the load-balanced app URL); verify the target in backend request logs.
Using an authorized synthetic account, GET `/apply?applicant_id=seller-bakery-20`
and, as an officer, GET `/applications/new`. Keep the authenticated session
and fresh CSRF value for each form. POST valid form data to its actual action:
`applicant_id=seller-bakery-20`, `requested_amount_usd=5000.00`,
`requested_tenor_months=6`, `csrf`, plus all required borrower consent boxes
for `/apply`. Disable automatic redirects and retries.

Require **both** app responses to be **503 with the closed-origination form**,
correlated with those POSTs on that revision. A WAF 403, authentication
challenge, validation error, generic gateway 503 or inspection of the env
value does not qualify. Keep other start producers quiesced; record the probe
interval and verify zero successful `PutMessage` operations for that queue in
complete data-plane write logs through the end of the interval (allow for
ingestion delay). An unchanged queue depth is not proof. If a probe is
accepted, stop, record its ID and reconcile it without resubmission. Missing
evidence or either failed probe: **keep the deny**, preserve accepted work
and escalate; never unmask the public routes to run this test.

## Before any live write

1. Obtain release owner, operations and privacy approval for a named environment,
   immutable SHA/images/Function package, maintenance interval, synthetic-only
   participants, isolated notice destination and rollback owner. No real contact,
   money movement, RBAC expansion or data migration is part of this rehearsal.
   Record baseline KRW counts, status/KPI denominators and native sums, direct
   application/loan links, original approved amounts and replay outcome. Record
   active Durable IDs (including human waits), queue/poison/DLQ backlog, leases,
   incomplete proofs and grant IDs in the restricted release log, not raw PII.
2. **Fence old writers before starting new workers.** Activate and verify the
   independent ingress deny above **before the first provisioning or window
   operation**; pause batch/smoke/scheduled jobs and direct publishers. Quiesce
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
5. With the external deny still active, complete the revision-pinned
   closed-application and zero-write probes above. Verify **effective**
   healthy package/image revisions and config on every
   listener/replica; route 100% to the dual reader. Resume only the new
   workers against the original hub, queues and subscriptions. Confirm a saved
   untagged pending journey replays, its queued KRW decision is honored, and a
   duplicate delivery does not originate a second case. Check mixed records
   through mixed-reader staging with isolated synthetic copies. KRW archive
   totals and old direct links must match baseline; USD lists and KPIs exclude
   them before limit/aggregation. Confirm real roles, auditor masking and
   ownership/CSRF barriers, including after withdrawal; verify revoke and
   deletion proof on disposable **copies only** for each contract.
   The mock API now requires a positive marker for each signed, unexpired,
   in-scope token. IaC binds `MOCK_AUTHORITY_URL` to the primary private
   `stfn…/consent-authority` Blob container. The mock's dedicated `id-mockapi`
   identity has AcrPull on the registry and Blob Data Contributor **only on
   this container**, not account-wide access or Cosmos rights. Verify effective
   assignments, private DNS, read/write and denial outside this scope.
   Before switching endpoints, isolate old revisions from all callers and
   quiesce issuance/revocation while retaining commands for replay. The old
   process-local registry cannot prove a token is still active after a restart.
   Reconcile the old authority, stored grants, expiry and withdrawal history;
   seed only individually verified live grant IDs with `active` bytes, using
   create-only writes. Never overwrite a tombstone, infer active from a signature,
   or import unresolved grants. If continuity cannot be proven, hold the switch
   and arrange authorized re-consent, not a bypass. Preserve the same secret.
   On isolated replicas including a fresh process, verify active access,
   revoked-token denial without a second revoke, and denial on authority outage.
   Do not restore a stale authority snapshot or route back to memory-only code.
6. Require working private identity/network paths, Cosmos indexing and RU
   headroom, conditional writes/batches (contact cap across both currencies,
   ETag proof and grant tracking), queue consumption/poison monitoring,
   `flow.updated` and `repayment.updated` subscriptions/filters and DLQs,
   and a healthy five-minute `repair_consent_deletions` timer. Verify verified
   Blob namespace absence and same-project Foundry cleanup: after a failed
   DELETE only authoritative GET 404 confirms container absence; forbidden,
   transport and server errors are **pending**, not success. Check repair and
   notice reconciliation. For registry revocation, only HTTP 204 acknowledges
   the durable tombstone; retry 404, unexpected 2xx and transport failures.
   Assessment, early-warning and collections workers fence each attempt on
   its consent before calling Foundry. A crash or ambiguous response can leave
   known targets **and** unknown ones. Keep proof and notice pending until
   authorized same-project inventory and target reconciliation; neither age,
   cleanup of known IDs nor successful redelivery clears an earlier fence.
   With Code Interpreter enabled, inspect itemized output for every response
   round: top-level text alone cannot establish the container inventory.
   Retain container IDs from file citations as cleanup targets. A citation
   not accounted for by an interpreter call leaves the inventory pending.
   On the Service Bus path, check the replay and both grants again immediately
   before publishing another week. A stop-write failure must retry; redelivery
   after deletion must not load raw data, stop a replacement replay, or
   overwrite a completed or failed replay's original result.
   Inspect old `late_cleanup` failures before rollout;
   old missing manifests/cleared markers cannot be
   reconstructed by the timer. Escalate unresolved historical gaps. Check
   native English grounded review via the actual model/API and fallback paths
   in the isolated stage. Fail or lack of evidence on any check = keep closed.

## Enable last, verify once

Only after the closed probes and written go/no-go for writes: keep the
independent deny active, restrict pilot ingress to the approved
operators; `azd env set USD_ORIGINATION_ENABLED true`, then
`azd provision --no-prompt` in that environment after reviewing its scope.
Confirm the effective revision value and retire alternate writable revisions.
Only then may the network owner lift the deny for approved pilot traffic.
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
immediately re-engage the independent deny and verify both edge rule hits
**before provisioning**. Quiesce direct start publishers. Set
`azd env set USD_ORIGINATION_ENABLED false` and apply the reviewed config
while the deny stays active. Repeat the private revision-specific probes and
queue-write audit above. Only after both app 503s and zero writes are proven
on all reachable revisions may the owner remove the deny with the flag closed.
Preserve all accepted USD and KRW records, work queues, proof checkpoints and
history. Keep both contracts' consumers, APIs, archive, decisions, 13-week
servicing and repair timer online. If a compromised worker must be isolated,
retain its messages for a compatible replacement; never roll back to KRW-only,
restore old unconditional writers, re-label money or replay notices to recover.
Record affected IDs/deltas and actual revisions; require a new approval to reopen.
